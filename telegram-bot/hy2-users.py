#!/usr/bin/env python3
"""Root-only multi-user manager for Hysteria 2; URI generation uses the official HY2 CLI."""
import copy
import fcntl
import grp
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

CONFIG=Path("/etc/hysteria/config.yaml")
BASE=Path("/var/lib/hy2-telegram/base-client.yaml")
STATE=Path("/var/lib/hy2-telegram")
CLIENTS=STATE/"clients"
BACKUPS=STATE/"backups"
REGISTRY=Path("/etc/hy2-telegram/clients.json")
LOCK=STATE/".users.lock"
HY2="/usr/local/bin/hysteria"
SERVICE="hysteria-server.service"
GROUP="hy2bot"
DEFAULTS=["WIN-PC","MACBOOK","IPHONE","FAMILY"]
NAME_RE=re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


class ManagerError(Exception):
    pass


def gid():
    try:
        return grp.getgrnam(GROUP).gr_gid
    except KeyError:
        raise ManagerError("缺少 hy2bot 用户组，请先运行升级脚本。")


def atomic(path, data, mode=0o640, uid=0, group=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    group = gid() if group is None else group
    fd, tmp = tempfile.mkstemp(prefix="."+path.name+".", dir=str(path.parent))
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chown(tmp,uid,group)
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def name_ok(raw):
    name=str(raw).strip().upper()
    if not NAME_RE.fullmatch(name):
        raise ManagerError("设备名称只允许 1-32 位英文字母、数字、下划线和连字符，例如 WIN-PC。")
    return name


def load_yaml(path):
    try:
        value=yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        raise ManagerError("YAML 读取失败，原配置未修改。")
    if not isinstance(value,dict):
        raise ManagerError("YAML 根节点不是对象。")
    return value


def users_from(doc):
    auth=doc.get("auth")
    if not isinstance(auth,dict) or auth.get("type")!="userpass":
        raise ManagerError("当前不是 userpass 认证，请先运行升级脚本完成迁移。")
    users=auth.get("userpass")
    if not isinstance(users,dict):
        raise ManagerError("auth.userpass 格式错误。")
    return {name_ok(k):str(v) for k,v in users.items()}


def load_registry():
    if not REGISTRY.exists():
        return {"clients":{}}
    try:
        value=json.loads(REGISTRY.read_text(encoding="utf-8"))
        if not isinstance(value,dict) or not isinstance(value.get("clients"),dict):
            raise ValueError()
        return value
    except Exception:
        raise ManagerError("设备登记文件损坏，拒绝继续修改。")


def save_registry(registry):
    data=(json.dumps(registry,ensure_ascii=False,indent=2)+"\n").encode()
    atomic(REGISTRY,data,0o640,0,gid())


def backup_config(original):
    BACKUPS.mkdir(parents=True,exist_ok=True)
    os.chown(BACKUPS,0,gid())
    os.chmod(BACKUPS,0o750)
    p=BACKUPS/("config-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".yaml")
    atomic(p,original,0o640,0,gid())
    return p


def write_config(doc, old_stat):
    data=yaml.safe_dump(doc,allow_unicode=True,sort_keys=False,default_flow_style=False).encode()
    atomic(CONFIG,data,stat.S_IMODE(old_stat.st_mode),old_stat.st_uid,old_stat.st_gid)


def restore_config(original, old_stat):
    atomic(CONFIG,original,stat.S_IMODE(old_stat.st_mode),old_stat.st_uid,old_stat.st_gid)


def restart_or_rollback(original, old_stat):
    result=subprocess.run(["/usr/bin/systemctl","restart",SERVICE],capture_output=True,text=True,timeout=30,check=False)
    active=subprocess.run(["/usr/bin/systemctl","is-active","--quiet",SERVICE],timeout=5,check=False).returncode==0
    if result.returncode==0 and active:
        return
    restore_config(original,old_stat)
    subprocess.run(["/usr/bin/systemctl","restart",SERVICE],capture_output=True,timeout=30,check=False)
    raise ManagerError("HY2 重启失败，已恢复原配置；请检查 journalctl -u hysteria-server。")


def base_client():
    if not BASE.is_file():
        raise ManagerError("缺少客户端模板，请重新运行升级脚本。")
    doc=load_yaml(BASE)
    if not doc.get("server"):
        raise ManagerError("客户端模板缺少 server 字段。")
    return doc


def build_artifact(name,password):
    client=base_client()
    client["auth"]=name+":"+password
    directory=CLIENTS/name
    directory.mkdir(parents=True,exist_ok=True)
    os.chown(directory,0,gid())
    os.chmod(directory,0o750)
    fd,tmp=tempfile.mkstemp(prefix=".share-",suffix=".yaml",dir=str(directory))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            yaml.safe_dump(client,f,allow_unicode=True,sort_keys=False)
        os.chown(tmp,0,gid())
        os.chmod(tmp,0o640)
        proc=subprocess.run([HY2,"-c",tmp,"share"],capture_output=True,text=True,timeout=15,check=False)
        if proc.returncode!=0:
            raise ManagerError("Hysteria 官方 share 命令生成 "+name+" 失败。")
        uri=proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not uri.startswith("hysteria2://"):
            raise ManagerError("官方 share 命令没有返回 hysteria2:// 链接。")
        parts=urlsplit(uri)
        uri=urlunsplit((parts.scheme,parts.netloc,parts.path,parts.query,quote(name,safe="-_")))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    node=directory/"node.txt"
    qr=directory/"node-qr.png"
    atomic(node,(uri+"\n").encode(),0o640,0,gid())
    proc=subprocess.run(["/usr/bin/qrencode","-o",str(qr),"-s","8","-m","2",uri],capture_output=True,text=True,timeout=10,check=False)
    if proc.returncode!=0 or not qr.is_file():
        raise ManagerError("二维码生成失败："+name)
    os.chown(qr,0,gid())
    os.chmod(qr,0o640)
    return {"name":name,"uri":uri,"node_path":str(node),"qr_path":str(qr)}


def update_registry(users):
    old=load_registry().get("clients",{})
    now=datetime.now().astimezone().isoformat(timespec="seconds")
    return {"clients":{
        n:{"name":n,"active":True,"created_at":old.get(n,{}).get("created_at",now)}
        for n in users
    }}


def cmd_init():
    doc=load_yaml(CONFIG)
    original=CONFIG.read_bytes()
    old_stat=CONFIG.stat()
    auth=doc.get("auth") or {}
    if not isinstance(auth,dict):
        raise ManagerError("auth 配置格式错误。")
    auth_type=auth.get("type")
    if auth_type not in ("password","userpass"):
        raise ManagerError("仅支持 password 或 userpass 认证，当前类型："+str(auth_type))

    # Replace legacy credentials with four fresh device credentials. Re-running
    # this upgrade is idempotent once this exact set has been registered.
    current={}
    if auth_type=="userpass":
        raw=auth.get("userpass")
        if isinstance(raw,dict):
            current={name_ok(k):str(v) for k,v in raw.items()}
    registry_clients=load_registry().get("clients",{})
    initialized=(set(current)==set(DEFAULTS) and set(registry_clients)==set(DEFAULTS))
    users=current if initialized else {name:os.urandom(24).hex() for name in DEFAULTS}
    changed=(auth_type!="userpass" or current!=users)
    doc["auth"]={"type":"userpass","userpass":users}

    artifacts=[]
    try:
        for n,p in users.items():
            artifacts.append(build_artifact(n,p))
        if changed:
            backup_config(original)
            write_config(doc,old_stat)
            restart_or_rollback(original,old_stat)
    except Exception:
        if changed:
            restore_config(original,old_stat)
            subprocess.run(["/usr/bin/systemctl","restart",SERVICE],capture_output=True,timeout=30,check=False)
        raise

    save_registry(update_registry(users))
    for entry in CLIENTS.iterdir():
        if entry.is_dir() and entry.name not in users:
            shutil.rmtree(entry,ignore_errors=True)
    primary=next(x for x in artifacts if x["name"]=="WIN-PC")
    atomic(STATE/"node.txt",(primary["uri"]+"\n").encode(),0o640,0,gid())
    shutil.copyfile(primary["qr_path"],STATE/"node-qr.png")
    os.chown(STATE/"node-qr.png",0,gid())
    os.chmod(STATE/"node-qr.png",0o640)
    print(json.dumps({"ok":True,"reset":changed,"clients":[x["name"] for x in artifacts]},ensure_ascii=False))


def add_many(names):
    doc=load_yaml(CONFIG)
    current=users_from(doc)
    requested=[name_ok(x) for x in names]
    if len(set(requested))!=len(requested):
        raise ManagerError("设备名称重复。")
    duplicate=[x for x in requested if x in current]
    if duplicate:
        raise ManagerError("设备已存在："+", ".join(duplicate))
    if not requested:
        return []
    original=CONFIG.read_bytes()
    old_stat=CONFIG.stat()
    new_users=dict(current)
    made=[]
    try:
        for n in requested:
            password=os.urandom(24).hex()
            new_users[n]=password
            made.append(build_artifact(n,password))
        new_doc=copy.deepcopy(doc)
        new_doc["auth"]={"type":"userpass","userpass":new_users}
        backup_config(original)
        write_config(new_doc,old_stat)
        restart_or_rollback(original,old_stat)
    except Exception:
        for n in requested:
            shutil.rmtree(CLIENTS/n,ignore_errors=True)
        raise
    save_registry(update_registry(new_users))
    return made


def cmd_add(name):
    item=add_many([name])[0]
    print(json.dumps({"ok":True,**item},ensure_ascii=False))


def cmd_add_defaults():
    current=users_from(load_yaml(CONFIG))
    missing=[n for n in DEFAULTS if n not in current]
    created=add_many(missing) if missing else []
    users=users_from(load_yaml(CONFIG))
    clients=[artifact_for(n,users[n]) for n in DEFAULTS]
    print(json.dumps({"ok":True,"created":created,"existing":[n for n in DEFAULTS if n not in missing],"clients":clients},ensure_ascii=False))


def cmd_list():
    users=users_from(load_yaml(CONFIG))
    registry=load_registry().get("clients",{})
    items=[]
    for n in sorted(users):
        items.append({"name":n,"active":True,"created_at":registry.get(n,{}).get("created_at","未知")})
    print(json.dumps({"ok":True,"clients":items},ensure_ascii=False))


def artifact_for(name,password):
    directory=CLIENTS/name
    node=directory/"node.txt"
    qr=directory/"node-qr.png"
    if not node.is_file() or not qr.is_file():
        return build_artifact(name,password)
    return {"name":name,"uri":node.read_text(encoding="utf-8").strip(),"node_path":str(node),"qr_path":str(qr)}


def cmd_get(name):
    n=name_ok(name)
    users=users_from(load_yaml(CONFIG))
    if n not in users:
        raise ManagerError("找不到活动设备："+n)
    item=artifact_for(n,users[n])
    print(json.dumps({"ok":True,**item},ensure_ascii=False))


def cmd_delete(name):
    n=name_ok(name)
    doc=load_yaml(CONFIG)
    users=users_from(doc)
    if n not in users:
        raise ManagerError("找不到活动设备："+n)
    if len(users)<=1:
        raise ManagerError("不能删除最后一个账号，至少保留一个有效认证账号。")
    original=CONFIG.read_bytes()
    old_stat=CONFIG.stat()
    new_users=dict(users)
    del new_users[n]
    new_doc=copy.deepcopy(doc)
    new_doc["auth"]={"type":"userpass","userpass":new_users}
    backup_config(original)
    write_config(new_doc,old_stat)
    restart_or_rollback(original,old_stat)
    registry=load_registry()
    registry.setdefault("clients",{}).pop(n,None)
    save_registry(registry)
    shutil.rmtree(CLIENTS/n,ignore_errors=True)
    print(json.dumps({"ok":True,"deleted":n},ensure_ascii=False))


def main():
    if os.geteuid()!=0:
        raise ManagerError("必须以 root 权限运行。")
    STATE.mkdir(parents=True,exist_ok=True)
    os.chown(STATE,0,gid())
    os.chmod(STATE,0o750)
    CLIENTS.mkdir(parents=True,exist_ok=True)
    os.chown(CLIENTS,0,gid())
    os.chmod(CLIENTS,0o750)
    with open(LOCK,"a",encoding="ascii") as lock:
        os.chown(LOCK,0,gid())
        os.chmod(LOCK,0o640)
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        args=sys.argv[1:]
        if args==["init"]:
            cmd_init()
        elif len(args)==2 and args[0]=="add":
            cmd_add(args[1])
        elif args==["add-defaults"]:
            cmd_add_defaults()
        elif args==["list"]:
            cmd_list()
        elif len(args)==2 and args[0]=="get":
            cmd_get(args[1])
        elif len(args)==2 and args[0]=="delete":
            cmd_delete(args[1])
        else:
            raise ManagerError("用法：hy2-users init|add NAME|add-defaults|list|get NAME|delete NAME")


if __name__=="__main__":
    try:
        main()
    except ManagerError as e:
        print(str(e),file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("操作超时，请检查 HY2 服务状态。",file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("设备管理失败："+type(e).__name__,file=sys.stderr)
        sys.exit(1)
