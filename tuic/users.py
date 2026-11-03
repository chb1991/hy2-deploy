#!/usr/bin/env python3
"""Isolated TUIC v5 account manager; never modifies HY2 files or restarts HY2."""
import fcntl
import grp
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

CONFIG = Path("/etc/tuic-singbox/config.json")
STATE = Path("/var/lib/hy2-telegram/tuic")
CLIENTS = STATE / "clients"
LOCK = STATE / ".users.lock"
INFO = Path("/etc/tuic-singbox/info.json")
BIN = "/opt/tuic-singbox/sing-box"
SERVICE = "tuic-singbox.service"
DEFAULTS = ("WIN-PC", "MACBOOK", "IPHONE", "FAMILY")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class ManagementError(Exception):
    pass


def check_name(value):
    name = value.upper()
    if not NAME_RE.fullmatch(name):
        raise ManagementError("名称仅允许1-32位字母、数字、下划线及连字符。")
    return name


def load():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def users(doc):
    return doc["inbounds"][0]["users"]


def find(doc, name):
    return next((u for u in users(doc) if u["name"].upper() == name), None)


def info():
    return json.loads(INFO.read_text(encoding="utf-8"))


def emit(value):
    print(json.dumps({"ok": True, **value}, ensure_ascii=False))


def write_atomic(doc):
    fd, path = tempfile.mkstemp(prefix=".tuic-", suffix=".json", dir=str(CONFIG.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(doc, file, ensure_ascii=False, indent=2)
            file.write("\n")
        shutil.chown(path, user="root", group="tuic")
        os.chmod(path, 0o640)
        os.replace(path, CONFIG)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def restart_checked(new_doc):
    old = CONFIG.read_bytes()
    STATE.joinpath("backups").mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = STATE / "backups" / ("config-" + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    backup.write_bytes(old)
    os.chmod(backup, 0o600)
    try:
        write_atomic(new_doc)
        checked = subprocess.run([BIN, "check", "-c", str(CONFIG)], capture_output=True, text=True, timeout=20)
        if checked.returncode:
            raise ManagementError("新 TUIC 配置未通过检查：" + checked.stderr[-400:])
        subprocess.run(["systemctl", "restart", SERVICE], timeout=25, check=True)
        active = subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], timeout=8)
        if active.returncode:
            raise ManagementError("TUIC 重启后未保持运行。")
    except Exception:
        CONFIG.write_bytes(old)
        shutil.chown(CONFIG, user="root", group="tuic")
        os.chmod(CONFIG, 0o640)
        subprocess.run(["systemctl", "restart", SERVICE], timeout=25, check=False)
        raise


def artifact(user):
    data = info()
    name = user["name"]
    host = data["domain"]
    port = int(data["port"])
    params = "sni=" + quote(host) + "&alpn=h3&congestion_control=bbr&udp_relay_mode=native"
    uri = (
        "tuic://" + user["uuid"] + ":" + quote(user["password"], safe="")
        + "@" + host + ":" + str(port) + "?" + params + "#TUIC-" + quote(name)
    )
    folder = CLIENTS / name
    folder.mkdir(parents=True, exist_ok=True)
    os.chown(folder, 0, grp.getgrnam("hy2bot").gr_gid)
    folder.chmod(0o750)
    node_path = folder / "node.txt"
    qr_path = folder / "node-qr.png"
    node_path.write_text(uri + "\n", encoding="utf-8")
    os.chown(node_path, 0, grp.getgrnam("hy2bot").gr_gid)
    node_path.chmod(0o640)
    subprocess.run(["qrencode", "-o", str(qr_path), "-s", "7", "-m", "2", uri], check=True, timeout=10)
    os.chown(qr_path, 0, grp.getgrnam("hy2bot").gr_gid)
    qr_path.chmod(0o640)
    return {"name": name, "uri": uri, "qr_path": str(qr_path)}


def main():
    if os.geteuid():
        raise ManagementError("需要 root 权限。")
    if not CONFIG.is_file() or not INFO.is_file():
        raise ManagementError("TUIC 尚未安装。")
    STATE.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(STATE, 0, grp.getgrnam("hy2bot").gr_gid)
    STATE.chmod(0o750)
    CLIENTS.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(CLIENTS, 0, grp.getgrnam("hy2bot").gr_gid)
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        args = sys.argv[1:]
        doc = load()
        if args == ["list"]:
            emit({"clients": [{"name": u["name"]} for u in users(doc)]})
        elif args == ["get"] or args == ["add"] or args == ["delete"]:
            raise ManagementError("必须提供设备名称。")
        elif len(args) == 2 and args[0] == "get":
            name = check_name(args[1])
            u = find(doc, name)
            if not u:
                raise ManagementError("没有该 TUIC 用户：" + name)
            emit(artifact(u))
        elif len(args) == 2 and args[0] == "add":
            name = check_name(args[1])
            if find(doc, name):
                raise ManagementError("设备已存在：" + name)
            item = {"name": name, "uuid": str(uuid.uuid4()), "password": secrets.token_urlsafe(24)}
            users(doc).append(item)
            restart_checked(doc)
            emit(artifact(item))
        elif args == ["add-defaults"]:
            missing = [name for name in DEFAULTS if not find(doc, name)]
            if missing:
                for name in missing:
                    users(doc).append({"name": name, "uuid": str(uuid.uuid4()), "password": secrets.token_urlsafe(24)})
                restart_checked(doc)
            emit({"created": missing, "clients": [artifact(find(doc, name)) for name in DEFAULTS]})
        elif len(args) == 2 and args[0] == "delete":
            name = check_name(args[1])
            item = find(doc, name)
            if not item:
                raise ManagementError("没有该 TUIC 用户：" + name)
            if len(users(doc)) < 2:
                raise ManagementError("不能删除最后一个用户。")
            users(doc).remove(item)
            restart_checked(doc)
            shutil.rmtree(CLIENTS / name, ignore_errors=True)
            emit({"deleted": name})
        elif args == ["restart"]:
            subprocess.run(["systemctl", "restart", SERVICE], timeout=25, check=True)
            emit({"service": "active"})
        else:
            raise ManagementError("用法: list | get NAME | add NAME | add-defaults | delete NAME | restart")


if __name__ == "__main__":
    try:
        main()
    except ManagementError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print("TUIC 管理失败：" + type(error).__name__, file=sys.stderr)
        sys.exit(1)
