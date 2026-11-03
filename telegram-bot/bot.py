#!/usr/bin/env python3
"""Private Telegram monitor and multi-device manager for Hysteria 2."""
import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TOKEN=os.environ["BOT_TOKEN"].strip()
ADMIN_ID=int(os.environ["ADMIN_USER_ID"])
STATS_SECRET=os.environ["HY2_STATS_SECRET"]
STATS_URL=os.environ.get("HY2_STATS_URL","http://127.0.0.1:9999").rstrip("/")
NODE_FILE=os.environ.get("NODE_FILE","/var/lib/hy2-telegram/node.txt")
HELPER="/usr/local/sbin/hy2-users"
HYSTERIA_BIN="/usr/local/bin/hysteria"
BOT_API="https://api.telegram.org/bot"+TOKEN
COMMANDS=[
    {"command":"start","description":"完整状态和设备概览"},
    {"command":"status","description":"查看服务器实时状态"},
    {"command":"devices","description":"查看设备在线情况和独立流量"},
    {"command":"add","description":"添加设备，例如 /add WIN-PC"},
    {"command":"add_defaults","description":"一键创建电脑、手机、家人账号"},
    {"command":"get","description":"获取设备链接和二维码"},
    {"command":"node","description":"获取指定设备节点链接"},
    {"command":"qr","description":"获取指定设备二维码"},
    {"command":"delete","description":"删除设备（需要二次确认）"},
    {"command":"traffic","description":"查看服务器总流量"},
    {"command":"restart","description":"重启 HY2（需要二次确认）"},
    {"command":"help","description":"显示命令帮助"},
]

try:
    time.tzset()
except AttributeError:
    pass


def log(message):
    print("[hy2-telegram] "+str(message),flush=True)


def telegram(method,payload=None,timeout=35):
    req=Request(
        BOT_API+"/"+method,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type":"application/json"},
        method="POST",
    )
    try:
        with urlopen(req,timeout=timeout) as response:
            result=json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None
    if not result.get("ok"):
        raise RuntimeError(result.get("description","Telegram API error"))
    return result.get("result")


def send_message(chat_id,text,reply_markup=None):
    payload={"chat_id":chat_id,"text":text,"disable_web_page_preview":True}
    if reply_markup:
        payload["reply_markup"]=reply_markup
    return telegram("sendMessage",payload)


def send_photo(chat_id,path,caption=""):
    boundary="----HY2Telegram"+uuid.uuid4().hex
    parts=[]
    def field(name,value):
        parts.extend([
            ("--"+boundary+"\r\n").encode(),
            ('Content-Disposition: form-data; name="'+name+'"\r\n\r\n').encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    field("chat_id",chat_id)
    if caption:
        field("caption",caption)
    with open(path,"rb") as f:
        photo=f.read()
    parts.extend([
        ("--"+boundary+"\r\n").encode(),
        b'Content-Disposition: form-data; name="photo"; filename="hy2-node.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        photo,
        b"\r\n",
        ("--"+boundary+"--\r\n").encode(),
    ])
    req=Request(
        BOT_API+"/sendPhoto",
        data=b"".join(parts),
        headers={"Content-Type":"multipart/form-data; boundary="+boundary},
        method="POST",
    )
    try:
        with urlopen(req,timeout=45) as response:
            result=json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None
    if not result.get("ok"):
        raise RuntimeError(result.get("description","Telegram sendPhoto error"))
    return result.get("result")


def stats(path):
    req=Request(
        STATS_URL+path,
        headers={"Authorization":STATS_SECRET,"Accept":"application/json"},
    )
    try:
        with urlopen(req,timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None


def online_data():
    value=stats("/online")
    return value if isinstance(value,dict) else {}


def traffic_data():
    value=stats("/traffic")
    return value if isinstance(value,dict) else {}


def read_node():
    try:
        return open(NODE_FILE,"r",encoding="utf-8").read().strip()
    except OSError:
        return ""


def node_host():
    try:
        return urlsplit(read_node()).hostname or ""
    except ValueError:
        return ""


def resolve_ip(host,family):
    if not host:
        return "未配置"
    try:
        values=socket.getaddrinfo(host,None,family,socket.SOCK_DGRAM)
        result=sorted({item[4][0] for item in values})
        return ", ".join(result) if result else "未配置"
    except OSError:
        return "未配置"


def read_meminfo():
    values={}
    try:
        with open("/proc/meminfo","r",encoding="ascii") as f:
            for line in f:
                key,value=line.split(":",1)
                values[key]=int(value.strip().split()[0])*1024
    except (OSError,ValueError):
        pass
    return values


def mb(value):
    return value/(1024*1024)


def fmt_bytes(value):
    value=float(value)
    for unit in ["B","KB","MB","GB","TB"]:
        if value<1024 or unit=="TB":
            return f"{value:.2f}{unit}"
        value/=1024
    return f"{value:.2f}TB"


def uptime_text():
    try:
        seconds=int(float(open("/proc/uptime","r",encoding="ascii").read().split()[0]))
    except (OSError,ValueError,IndexError):
        return "未知"
    days,rem=divmod(seconds,86400)
    hours,rem=divmod(rem,3600)
    minutes,_=divmod(rem,60)
    if days:
        return f"{days}天 {hours}小时 {minutes}分钟"
    return f"{hours}小时 {minutes}分钟"


def socket_count(args):
    try:
        result=subprocess.run(["ss","-H"]+args,capture_output=True,text=True,timeout=3,check=False)
        return len([line for line in result.stdout.splitlines() if line.strip()])
    except (OSError,subprocess.TimeoutExpired):
        return 0


def service_state():
    try:
        result=subprocess.run(["systemctl","is-active","hysteria-server.service"],capture_output=True,text=True,timeout=3,check=False)
        return result.stdout.strip() or "unknown"
    except (OSError,subprocess.TimeoutExpired):
        return "unknown"


def hy2_version():
    try:
        result=subprocess.run([HYSTERIA_BIN,"version"],capture_output=True,text=True,timeout=3,check=False)
        value=(result.stdout or result.stderr).strip()
        return value.splitlines()[0][:100] if value else "未知"
    except (OSError,subprocess.TimeoutExpired):
        return "未知"


def online_totals():
    data=online_data()
    return sum(int(v or 0) for v in data.values()),len(data)


def traffic_totals():
    data=traffic_data()
    tx=sum(int(v.get("tx",0) or 0) for v in data.values() if isinstance(v,dict))
    rx=sum(int(v.get("rx",0) or 0) for v in data.values() if isinstance(v,dict))
    return tx,rx


def status_text():
    mem=read_meminfo()
    total=mem.get("MemTotal",0)
    available=mem.get("MemAvailable",0)
    used=max(0,total-available)
    swap_total=mem.get("SwapTotal",0)
    swap_used=max(0,swap_total-mem.get("SwapFree",0))
    try:
        load=", ".join(f"{x:.2f}" for x in os.getloadavg())
    except OSError:
        load="未知"
    state=service_state()
    try:
        count,accounts=online_totals()
        online=f"{count} 台设备实例（{accounts} 个账号）"
    except Exception:
        online="统计 API 暂不可用"
    try:
        tx,rx=traffic_totals()
        traffic=f"{fmt_bytes(tx+rx)} (↑上传 {fmt_bytes(tx)}, ↓下载 {fmt_bytes(rx)})"
    except Exception:
        traffic="统计 API 暂不可用"
    return (
        "🖥 CHBVPS · HY2 SERVER\n\n"
        f"📅 日期时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"💻 主机：{socket.gethostname()}\n"
        "🖥 系统：Debian 13\n"
        f"📦 HY2版本：{hy2_version()}\n\n"
        f"🌐 IPv4：{resolve_ip(node_host(),socket.AF_INET)}\n"
        f"🌐 IPv6：{resolve_ip(node_host(),socket.AF_INET6)}\n\n"
        f"⏳ 服务器运行时间：{uptime_text()}\n"
        f"📈 服务器负载：{load}\n\n"
        f"📋 RAM：{mb(used):.2f}MB / {mb(total):.2f}MB\n"
        f"💾 Swap：{mb(swap_used):.2f}MB / {mb(swap_total):.2f}MB\n\n"
        f"🌐 在线客户：{online}\n"
        f"🔹 TCP：{socket_count(['-tan'])}\n"
        f"🔸 UDP：{socket_count(['-uan'])}\n"
        f"🚦 流量：{traffic}\n\n"
        f"ℹ️ HY2 状态：{'🟢' if state=='active' else '🔴'} {state}"
    )


def run_manager(*args):
    result=subprocess.run(
        ["/usr/bin/sudo","-n",HELPER,*args],
        capture_output=True,text=True,timeout=90,check=False,
    )
    if result.returncode!=0:
        raise RuntimeError((result.stderr or "设备管理命令失败。").strip()[:700])
    try:
        value=json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("设备管理程序返回格式错误。")
    if not value.get("ok"):
        raise RuntimeError("设备管理失败。")
    return value


def devices_text():
    clients=run_manager("list").get("clients",[])
    if not clients:
        return "当前没有设备账号。发送 /add_defaults 一键创建四个默认设备。"
    try:
        online=online_data()
        traffic=traffic_data()
    except Exception:
        online,traffic={},{}
    lines=["📱 HY2 独立设备列表",""]
    for item in clients:
        name=item["name"]
        # Hysteria 2 normalizes userpass usernames to lowercase for Stats API IDs.
        stat_name=name.lower()
        count=int(online.get(stat_name,0) or 0)
        data=traffic.get(stat_name,{})
        if not isinstance(data,dict):
            data={}
        tx=int(data.get("tx",0) or 0)
        rx=int(data.get("rx",0) or 0)
        state=f"🟢 在线（{count}）" if count else "⚪ 离线"
        lines.append(f"• {name}  {state}")
        lines.append(f"  ↑ {fmt_bytes(tx)}  ↓ {fmt_bytes(rx)}")
    lines.extend(["","获取设备链接和二维码：/get WIN-PC","添加设备：/add IPAD"])
    return "\n".join(lines)


def send_client(chat_id,item,prefix=""):
    send_message(chat_id,prefix+f"📱 设备：{item['name']}\n\n🔗 独立节点链接：\n{item['uri']}")
    send_photo(chat_id,item["qr_path"],f"📷 {item['name']} 专属二维码")


def help_text():
    return (
        "CHBVPS HY2 机器人命令\n\n"
        "/start - 服务器状态和设备概览\n"
        "/status - 查看服务器实时状态\n"
        "/devices - 查看每台设备在线情况和独立流量\n"
        "/add_defaults - 一键创建 WIN-PC、MACBOOK、IPHONE、FAMILY\n"
        "/add WIN-PC - 添加一个设备并生成独立链接、二维码\n"
        "/get WIN-PC - 发送指定设备的链接和二维码\n"
        "/node WIN-PC - 只获取指定设备链接\n"
        "/qr WIN-PC - 只获取指定设备二维码\n"
        "/delete WIN-PC - 删除设备（需要二次确认）\n"
        "/traffic - 查看服务器总流量\n"
        "/restart - 重启 HY2（需要二次确认）\n"
        "/help - 命令帮助\n\n"
        "仅已绑定的 Telegram 管理员数字 ID 可在私聊中操作。"
    )


def authorized(user,chat):
    try:
        return int(user.get("id",0))==ADMIN_ID and int(chat.get("id",0))==ADMIN_ID and chat.get("type")=="private"
    except (TypeError,ValueError):
        return False


def handle_message(message):
    user=message.get("from") or {}
    chat=message.get("chat") or {}
    if not authorized(user,chat):
        return
    text=(message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts=text.split()
    command=parts[0].split("@",1)[0].lower()
    args=parts[1:]
    chat_id=chat["id"]
    try:
        if command=="/start":
            send_message(chat_id,status_text())
            send_message(chat_id,devices_text())
            return
        if command=="/status":
            send_message(chat_id,status_text())
            return
        if command in ("/devices","/clients"):
            send_message(chat_id,devices_text())
            return
        if command in ("/add_defaults","/adddefaults","/add-defaults"):
            result=run_manager("add-defaults")
            created=result.get("created",[])
            existing=result.get("existing",[])
            if not created:
                send_message(chat_id,"四个默认设备已经存在，无需重复创建。\n"+devices_text())
                return
            send_message(chat_id,"✅ 默认设备创建成功："+", ".join(x["name"] for x in created))
            for item in created:
                send_client(chat_id,item)
            if existing:
                send_message(chat_id,"以下设备原本已存在，未重复创建："+", ".join(existing))
            return
        if command=="/add":
            if len(args)!=1:
                send_message(chat_id,"用法：/add WIN-PC\n名称只允许英文字母、数字、下划线和连字符。")
                return
            item=run_manager("add",args[0])
            send_client(chat_id,item,"✅ 新设备已添加\n")
            return
        if command in ("/get","/node","/qr"):
            if len(args)!=1:
                send_message(chat_id,"请指定设备名称，例如：/get WIN-PC")
                return
            item=run_manager("get",args[0])
            if command=="/node":
                send_message(chat_id,f"🔗 {item['name']} 节点链接：\n\n{item['uri']}")
            elif command=="/qr":
                send_photo(chat_id,item["qr_path"],f"📷 {item['name']} 专属二维码")
            else:
                send_client(chat_id,item)
            return
        if command=="/delete":
            if len(args)!=1:
                send_message(chat_id,"用法：/delete WIN-PC")
                return
            name=args[0].upper()
            keyboard={"inline_keyboard":[[
                {"text":"确认删除 "+name,"callback_data":"delete:confirm:"+name},
                {"text":"取消","callback_data":"delete:cancel"},
            ]]}
            send_message(chat_id,f"⚠️ 确认删除设备 {name}？该设备凭据将失效。HY2 会重启一次，其他设备连接会短暂中断。",keyboard)
            return
        if command=="/traffic":
            tx,rx=traffic_totals()
            count,accounts=online_totals()
            send_message(chat_id,
                "🚦 HY2 总流量统计\n\n"
                f"📤 上传：{fmt_bytes(tx)}\n"
                f"📥 下载：{fmt_bytes(rx)}\n"
                f"📊 合计：{fmt_bytes(tx+rx)}\n"
                f"🌐 在线设备实例：{count}\n"
                f"👤 在线账号：{accounts}\n\n"
                "流量为 Hysteria 当前进程累计统计，重启服务后可能重新累计。")
            return
        if command=="/restart":
            keyboard={"inline_keyboard":[[
                {"text":"确认重启 HY2","callback_data":"restart:confirm"},
                {"text":"取消","callback_data":"restart:cancel"},
            ]]}
            send_message(chat_id,"⚠️ 确定重启 Hysteria 2 吗？现有连接会短暂中断。",keyboard)
            return
        if command=="/help":
            send_message(chat_id,help_text())
            return
        send_message(chat_id,"未知命令，请发送 /help 查看支持的命令。")
    except Exception as exc:
        send_message(chat_id,"❌ 操作失败："+str(exc)[:700])


def handle_callback(query):
    user=query.get("from") or {}
    message=query.get("message") or {}
    chat=message.get("chat") or {}
    if not authorized(user,chat):
        return
    data=query.get("data","")
    telegram("answerCallbackQuery",{"callback_query_id":query["id"]})
    if data in ("restart:cancel","delete:cancel"):
        send_message(chat["id"],"已取消操作。")
        return
    if data=="restart:confirm":
        send_message(chat["id"],"正在重启 Hysteria 2，请稍候……")
        try:
            result=subprocess.run(["/usr/bin/sudo","-n","/usr/local/sbin/hy2-restart"],capture_output=True,text=True,timeout=20,check=False)
            time.sleep(2)
            state=service_state()
            if result.returncode==0 and state=="active":
                send_message(chat["id"],"✅ HY2 重启成功，服务状态：active")
            else:
                send_message(chat["id"],"❌ HY2 重启未确认成功，请检查 VPS 日志。")
        except Exception:
            send_message(chat["id"],"❌ 重启命令执行失败，请检查 sudo 权限和 systemd 日志。")
        return
    if data.startswith("delete:confirm:"):
        name=data.split(":",2)[2]
        try:
            result=run_manager("delete",name)
            send_message(chat["id"],"✅ 已删除设备 "+result["deleted"]+"。该设备凭据已从服务端移除。")
        except Exception as exc:
            send_message(chat["id"],"❌ 删除失败："+str(exc)[:700])


def main():
    log("Telegram HY2 multi-device manager starting")
    backoff=5
    while True:
        try:
            me=telegram("getMe")
            log("Connected as @"+me.get("username","bot"))
            telegram("deleteWebhook",{"drop_pending_updates":False})
            telegram("setMyCommands",{"commands":COMMANDS})
            offset=None
            backoff=5
            while True:
                payload={"timeout":25,"allowed_updates":["message","callback_query"]}
                if offset is not None:
                    payload["offset"]=offset
                updates=telegram("getUpdates",payload,timeout=35) or []
                for update in updates:
                    offset=int(update["update_id"])+1
                    try:
                        if "message" in update:
                            handle_message(update["message"])
                        elif "callback_query" in update:
                            handle_callback(update["callback_query"])
                    except Exception as exc:
                        log("Update handler error: "+type(exc).__name__)
        except Exception as exc:
            log("Telegram connection error: "+type(exc).__name__+"; retrying")
            time.sleep(backoff)
            backoff=min(backoff*2,60)


if __name__=="__main__":
    main()
