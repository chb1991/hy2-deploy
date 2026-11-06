#!/usr/bin/env python3
"""Refresh existing Vision client links, Loon profile, and QR without rotating credentials.

This script does NOT change /usr/local/etc/xray/vision.json or restart any service.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

CONFIG = Path("/usr/local/etc/xray/vision.json")
CLIENTS = Path("/var/lib/hy2-telegram/vision/clients")
BACKUPS = Path("/root/vision-backups")
DEVICES = ("WIN-PC", "MACBOOK", "IPHONE", "FAMILY")
PROFILE = "chrome147"


def fail(message):
    raise RuntimeError(message)


def inspect():
    if os.geteuid() != 0:
        fail("请使用 root 执行")
    if not CONFIG.is_file() or not CLIENTS.is_dir():
        fail("Vision 尚未安装，找不到配置或设备目录")
    if shutil.which("qrencode") is None:
        fail("缺少 qrencode")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    inbound = config["inbounds"][0]
    if (
        inbound["protocol"] != "vless"
        or inbound["streamSettings"]["network"] != "tcp"
        or inbound["streamSettings"]["security"] != "reality"
    ):
        fail("现有 Vision 配置与预期不符，不做修改")
    registered = {
        row["email"]: (str(UUID(row["id"])), row.get("flow"))
        for row in inbound["settings"]["clients"]
    }
    if sorted(registered) != sorted(DEVICES):
        fail("设备账户与原部署不一致，请人工核查")
    if any(registered[name][1] != "xtls-rprx-vision" for name in DEVICES):
        fail("Vision flow 配置与预期不符，不做修改")
    port = int(inbound["port"])
    sni = inbound["streamSettings"]["realitySettings"]["serverNames"][0]
    staged = []
    for name in DEVICES:
        folder = CLIENTS / name
        if folder.is_symlink() or not folder.is_dir():
            fail("设备文件夹缺失或存在符号链接：" + name)
        node_path = folder / "node.txt"
        loon_path = folder / "loon.txt"
        qr_path = folder / "node-qr.png"
        for p in (node_path, loon_path, qr_path):
            if p.is_symlink() or not p.is_file():
                fail("设备文件缺失或为符号链接：" + name + "/" + p.name)
        old_url = node_path.read_text(encoding="utf-8").strip()
        u = urlsplit(old_url)
        if u.scheme != "vless" or u.port != port:
            fail("节点链接与服务端端口不符：" + name)
        if str(UUID(u.username)) != registered[name][0]:
            fail("节点 UUID 与现有服务端不一致：" + name)
        query = dict(parse_qsl(u.query, keep_blank_values=True))
        if query.get("security") != "reality" or query.get("type") != "tcp":
            fail("节点传输或 REALITY 参数不一致：" + name)
        if query.get("flow") != "xtls-rprx-vision" or query.get("sni") != sni:
            fail("节点 flow/SNI 与服务端不一致：" + name)
        if not query.get("pbk") or not query.get("sid"):
            fail("原链接缺少 REALITY 公钥或 Short ID：" + name)
        query["fp"] = PROFILE
        query["tls-profile"] = PROFILE
        new_url = urlunsplit((u.scheme, u.netloc, u.path, urlencode(query), u.fragment))
        native = loon_path.read_text(encoding="utf-8").strip()
        if "transport=tcp" not in native or "flow=xtls-rprx-vision" not in native:
            fail("原 Loon 配置与 Vision 不一致：" + name)
        if re.search(r"(^|,)tls-profile=[^,]*", native):
            native = re.sub(
                r"(^|,)tls-profile=[^,]*",
                lambda match: match.group(1) + "tls-profile=" + PROFILE,
                native,
                count=1,
            )
        else:
            native += ",tls-profile=" + PROFILE
        staged.append((name, node_path, loon_path, qr_path, new_url, native))
    return staged


def write_atomic(path, data):
    fd, temp_path = tempfile.mkstemp(prefix=".vision-refresh-", dir=str(path.parent))
    try:
        stat = path.stat()
        os.fchmod(fd, stat.st_mode & 0o777)
        os.fchown(fd, stat.st_uid, stat.st_gid)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    data = inspect()  # Validate all four users before touching any file.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUPS / ("loon-profile-" + stamp + "-" + str(os.getpid()))
    backup.mkdir(parents=True, mode=0o700)
    originals = []
    try:
        for name, node, loon, qr, new_url, new_native in data:
            folder = backup / name
            folder.mkdir(mode=0o700)
            for file in (node, loon, qr):
                dest = folder / file.name
                shutil.copy2(file, dest)
                originals.append((file, dest))
        for name, node, loon, qr, new_url, new_native in data:
            with tempfile.TemporaryDirectory(prefix="vision-qr-") as tempdir:
                png = Path(tempdir) / "code.png"
                subprocess.run(
                    ["qrencode", "-s", "7", "-m", "2", "-o", str(png), new_url],
                    check=True, capture_output=True, timeout=20,
                )
                write_atomic(node, (new_url + "\n").encode("utf-8"))
                write_atomic(loon, (new_native + "\n").encode("utf-8"))
                write_atomic(qr, png.read_bytes())
    except Exception:
        for dest, saved in originals:
            try:
                shutil.copy2(saved, dest)
            except OSError:
                pass
        raise
    print("已更新 Vision 四设备的 VLESS 链接、Loon 原生配置和二维码。")
    print("TLS 指纹参数：fp=" + PROFILE + " / tls-profile=" + PROFILE)
    print("未修改 UUID、REALITY 服务端配置、端口、TLS 密钥，未重启任何服务。")
    print("备份：" + str(backup))
    print("Telegram 私聊重新发送 /vget IPHONE 即可获取最新链接和二维码。")
    print("注意：Loon 扫码是否应用非标准的 tls-profile URI 参数须客户端实测。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        sys.exit(1)
