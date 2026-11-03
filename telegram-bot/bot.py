#!/usr/bin/env python3
"""Private Telegram monitor for a local Hysteria 2 server."""
import json
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TOKEN = os.environ["BOT_TOKEN"].strip()
ADMIN_ID = int(os.environ["ADMIN_USER_ID"])
STATS_SECRET = os.environ["HY2_STATS_SECRET"]
STATS_URL = os.environ.get("HY2_STATS_URL", "http://127.0.0.1:9999").rstrip("/")
NODE_FILE = os.environ.get("NODE_FILE", "/var/lib/hy2-telegram/node.txt")
QR_FILE = os.environ.get("QR_FILE", "/var/lib/hy2-telegram/node-qr.png")
HYSTERIA_BIN = "/usr/local/bin/hysteria"
BOT_API = "https://api.telegram.org/bot" + TOKEN
COMMANDS = [
    {"command": "start", "description": "完整状态、节点链接和二维码"},
    {"command": "status", "description": "查看服务器实时状态"},
    {"command": "node", "description": "获取 HY2 节点分享链接"},
    {"command": "qr", "description": "获取 HY2 节点二维码"},
    {"command": "traffic", "description": "查看 HY2 累计流量"},
    {"command": "restart", "description": "重启 HY2（需要二次确认）"},
    {"command": "help", "description": "显示命令帮助"},
]

try:
    time.tzset()
except AttributeError:
    pass


def log(message):
    print("[hy2-telegram] " + str(message), flush=True)


def telegram(method, payload=None, timeout=35):
    data = json.dumps(payload or {}).encode("utf-8")
    req = Request(
        BOT_API + "/" + method,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        # Never print request URLs: they contain the bot token.
        raise RuntimeError(type(exc).__name__) from None
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API returned ok=false"))
    return result.get("result")


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram("sendMessage", payload)


def send_photo(chat_id, path, caption=""):
    boundary = "----HY2Telegram" + uuid.uuid4().hex
    parts = []

    def field(name, value):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="' + name + '"\r\n\r\n').encode())
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    field("chat_id", chat_id)
    if caption:
        field("caption", caption)
    with open(path, "rb") as image_file:
        image_data = image_file.read()
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(('Content-Disposition: form-data; name="photo"; filename="hy2-node.png"\r\n').encode())
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(image_data)
    parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)
    req = Request(
        BOT_API + "/sendPhoto",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    try:
        with urlopen(req, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram sendPhoto failed"))
    return result.get("result")


def stats(path):
    req = Request(
        STATS_URL + path,
        headers={"Authorization": STATS_SECRET, "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None


def read_node():
    try:
        with open(NODE_FILE, "r", encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return ""


def node_host():
    uri = read_node()
    try:
        return urlsplit(uri).hostname or ""
    except ValueError:
        return ""


def resolve_ip(host, family):
    if not host:
        return "未配置"
    try:
        values = socket.getaddrinfo(host, None, family, socket.SOCK_DGRAM)
        addresses = sorted({item[4][0] for item in values})
        return ", ".join(addresses) if addresses else "未配置"
    except OSError:
        return "未配置"


def read_meminfo():
    values = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as file:
            for line in file:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return values


def mb(value):
    return value / (1024 * 1024)


def fmt_bytes(value):
    value = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f}{unit}"
        value /= 1024
    return f"{value:.2f}TB"


def uptime_text():
    try:
        seconds = int(float(open("/proc/uptime", "r", encoding="ascii").read().split()[0]))
    except (OSError, ValueError, IndexError):
        return "未知"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}天 {hours}小时 {minutes}分钟"
    return f"{hours}小时 {minutes}分钟"


def socket_count(args):
    try:
        result = subprocess.run(
            ["ss", "-H"] + args,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return len([line for line in result.stdout.splitlines() if line.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return 0


def service_state():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "hysteria-server.service"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def hy2_version():
    try:
        result = subprocess.run(
            [HYSTERIA_BIN, "version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        text = (result.stdout or result.stderr).strip()
        return text.splitlines()[0][:100] if text else "未知"
    except (OSError, subprocess.TimeoutExpired):
        return "未知"


def traffic_totals():
    data = stats("/traffic")
    tx = 0
    rx = 0
    if isinstance(data, dict):
        for item in data.values():
            if isinstance(item, dict):
                tx += int(item.get("tx", 0) or 0)
                rx += int(item.get("rx", 0) or 0)
    return tx, rx


def online_totals():
    data = stats("/online")
    if not isinstance(data, dict):
        return 0, 0
    devices = sum(int(value or 0) for value in data.values())
    return devices, len(data)


def status_text():
    mem = read_meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", 0)
    used = max(0, total - available)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    try:
        load = ", ".join(f"{item:.2f}" for item in os.getloadavg())
    except OSError:
        load = "未知"

    host = node_host()
    ipv4 = resolve_ip(host, socket.AF_INET)
    ipv6 = resolve_ip(host, socket.AF_INET6)
    tcp = socket_count(["-tan"])
    udp = socket_count(["-uan"])
    state = service_state()
    version = hy2_version()

    try:
        devices, identities = online_totals()
        online_text = f"{devices} 台设备（{identities} 个身份）"
    except Exception:
        online_text = "统计 API 暂不可用"

    try:
        tx, rx = traffic_totals()
        traffic_text = (
            f"{fmt_bytes(tx + rx)} "
            f"(↑上传 {fmt_bytes(tx)}, ↓下载 {fmt_bytes(rx)})"
        )
    except Exception:
        traffic_text = "统计 API 暂不可用"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "🖥 CHBVPS · HY2 SERVER\n\n"
        f"📅 日期时间：{now}\n\n"
        f"💻 主机：{socket.gethostname()}\n"
        "🖥 系统：Debian 13\n"
        f"📦 HY2版本：{version}\n\n"
        f"🌐 IPv4：{ipv4}\n"
        f"🌐 IPv6：{ipv6}\n\n"
        f"⏳ 服务器运行时间：{uptime_text()}\n"
        f"📈 服务器负载：{load}\n\n"
        f"📋 RAM：{mb(used):.2f}MB / {mb(total):.2f}MB\n"
        f"💾 Swap：{mb(swap_used):.2f}MB / {mb(swap_total):.2f}MB\n\n"
        f"🌐 在线客户：{online_text}\n"
        f"🔹 TCP：{tcp}\n"
        f"🔸 UDP：{udp}\n"
        f"🚦 流量：{traffic_text}\n\n"
        f"ℹ️ HY2 状态：{'🟢' if state == 'active' else '🔴'} {state}"
    )


def traffic_text():
    tx, rx = traffic_totals()
    devices, identities = online_totals()
    return (
        "🚦 HY2 流量统计\n\n"
        f"📤 上传：{fmt_bytes(tx)}\n"
        f"📥 下载：{fmt_bytes(rx)}\n"
        f"📊 合计：{fmt_bytes(tx + rx)}\n\n"
        f"🌐 在线设备：{devices}\n"
        f"👤 在线身份：{identities}\n\n"
        "说明：流量为 Hysteria 当前进程累计统计，服务重启后可能重新累计。"
    )


def help_text():
    return (
        "CHBVPS HY2 机器人命令\n\n"
        "/start - 完整状态、节点链接和二维码\n"
        "/status - 服务器实时状态\n"
        "/node - 获取 HY2 节点分享链接\n"
        "/qr - 获取节点二维码\n"
        "/traffic - 查看 HY2 流量\n"
        "/restart - 重启 HY2（需要二次确认）\n"
        "/help - 命令帮助\n\n"
        "安全说明：机器人只响应安装时绑定的 Telegram 用户 ID，且仅限私聊。"
    )


def authorized(user, chat):
    try:
        return (
            int(user.get("id", 0)) == ADMIN_ID
            and int(chat.get("id", 0)) == ADMIN_ID
            and chat.get("type") == "private"
        )
    except (TypeError, ValueError):
        return False


def handle_message(message):
    user = message.get("from") or {}
    chat = message.get("chat") or {}
    if not authorized(user, chat):
        return
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    command = text.split()[0].split("@", 1)[0].lower()
    chat_id = chat["id"]

    if command == "/start":
        send_message(chat_id, status_text())
        uri = read_node()
        if uri:
            send_message(chat_id, "🔗 HY2 节点分享链接：\n\n" + uri)
        if os.path.isfile(QR_FILE):
            send_photo(chat_id, QR_FILE, "📷 HY2 节点二维码（与上方分享链接对应）")
        return
    if command == "/status":
        send_message(chat_id, status_text())
        return
    if command == "/node":
        uri = read_node()
        send_message(chat_id, "🔗 HY2 节点分享链接：\n\n" + uri if uri else "未找到节点链接文件。")
        return
    if command == "/qr":
        if os.path.isfile(QR_FILE):
            send_photo(chat_id, QR_FILE, "📷 HY2 节点二维码")
        else:
            send_message(chat_id, "未找到二维码文件。")
        return
    if command == "/traffic":
        try:
            send_message(chat_id, traffic_text())
        except Exception:
            send_message(chat_id, "无法读取 Hysteria 流量统计 API，请检查服务状态。")
        return
    if command == "/restart":
        keyboard = {
            "inline_keyboard": [[
                {"text": "确认重启 HY2", "callback_data": "restart:confirm"},
                {"text": "取消", "callback_data": "restart:cancel"},
            ]]
        }
        send_message(chat_id, "⚠️ 确定要重启 Hysteria 2 吗？现有连接会短暂中断。", keyboard)
        return
    if command == "/help":
        send_message(chat_id, help_text())
        return
    send_message(chat_id, "未知命令，请发送 /help 查看支持的命令。")


def handle_callback(query):
    user = query.get("from") or {}
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    if not authorized(user, chat):
        return
    data = query.get("data", "")
    telegram("answerCallbackQuery", {"callback_query_id": query["id"]})
    if data == "restart:cancel":
        send_message(chat["id"], "已取消重启。")
        return
    if data == "restart:confirm":
        send_message(chat["id"], "正在重启 Hysteria 2，请稍候……")
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-n", "/usr/local/sbin/hy2-restart"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            time.sleep(2)
            state = service_state()
            if result.returncode == 0 and state == "active":
                send_message(chat["id"], "✅ HY2 重启成功，服务状态：active")
            else:
                send_message(chat["id"], "❌ HY2 重启未确认成功，请检查 VPS 日志。")
        except Exception:
            send_message(chat["id"], "❌ 重启命令执行失败，请检查 sudo 权限和 systemd 日志。")


def main():
    log("Telegram HY2 monitor starting")
    backoff = 5
    while True:
        try:
            me = telegram("getMe")
            log("Connected as @" + me.get("username", "bot"))
            telegram("deleteWebhook", {"drop_pending_updates": False})
            telegram("setMyCommands", {"commands": COMMANDS})
            offset = None
            backoff = 5
            while True:
                payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    payload["offset"] = offset
                updates = telegram("getUpdates", payload, timeout=35) or []
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    try:
                        if "message" in update:
                            handle_message(update["message"])
                        elif "callback_query" in update:
                            handle_callback(update["callback_query"])
                    except Exception as exc:
                        log("Update handler error: " + type(exc).__name__)
        except Exception as exc:
            log("Telegram connection error: " + type(exc).__name__ + "; retrying")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
