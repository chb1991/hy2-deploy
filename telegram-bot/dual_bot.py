#!/usr/bin/env python3
"""Add TUIC functionality to the existing, unchanged HY2 Telegram bot."""
import html
import os
import subprocess

import bot as hy2

TUIC_HELPER = "/usr/local/sbin/tuic-users"
TUIC_SERVICE = "tuic-singbox.service"
TUIC_PORT = 51443

hy2.COMMANDS.extend([
    {"command": "devices_tuic", "description": "查看 TUIC 设备"},
    {"command": "add_tuic", "description": "新增 TUIC 设备: /add_tuic IPAD"},
    {"command": "get_tuic", "description": "获取 TUIC 节点和二维码"},
    {"command": "delete_tuic", "description": "删除 TUIC 设备，需确认"},
    {"command": "restart_tuic", "description": "重启 TUIC，需确认"},
])
old_handle_message = hy2.handle_message
old_handle_callback = hy2.handle_callback


def manage(*args):
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", TUIC_HELPER, *args],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or "TUIC 操作失败").strip()[:350])
    import json
    value = json.loads(result.stdout)
    if not value.get("ok"):
        raise RuntimeError("TUIC 管理程序返回失败。")
    return value


def state(name):
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return "UP" if r.stdout.strip() == "active" else "DOWN"
    except Exception:
        return "UNKNOWN"


def listening(port):
    try:
        r = subprocess.run(
            ["ss", "-H", "-lun", "sport = :" + str(port)],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return "YES" if r.stdout.strip() else "NO"
    except Exception:
        return "N/A"


def count_users(helper):
    try:
        return str(len(helper("list").get("clients", [])))
    except Exception:
        return "N/A"


def status():
    try:
        hy2_online = str(hy2.online_totals()[0])
    except Exception:
        hy2_online = "N/A"
    row = [
        ("", "HY2", "TUIC"),
        ("SERVICE", state("hysteria-server.service"), state(TUIC_SERVICE)),
        ("PORT/UDP", "443", str(TUIC_PORT)),
        ("LISTEN", listening(443), listening(TUIC_PORT)),
        ("USERS", count_users(hy2.run_manager), count_users(manage)),
        ("ONLINE", hy2_online, "N/A"),
    ]
    table = "\n".join(f"{key:<11}{a:<9}{b}" for key, a, b in row)
    memory = hy2.read_meminfo()
    total = memory.get("MemTotal", 0)
    used = max(0, total - memory.get("MemAvailable", 0))
    details = (
        f"主机：{hy2.socket.gethostname()}\n"
        f"运行时间：{hy2.uptime_text()}\n"
        f"RAM：{hy2.mb(used):.0f} / {hy2.mb(total):.0f} MB\n"
        "注意：LISTEN 是本机监听，不代表国内连接成功；TUIC 暂无在线统计接口。"
    )
    return "🖥 HY2 + TUIC 双协议状态\n\n<pre>" + html.escape(table) + "</pre>\n" + html.escape(details)


def send_status(chat_id):
    hy2.telegram("sendMessage", {
        "chat_id": chat_id, "text": status(),
        "parse_mode": "HTML", "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [
            [{"text": "HY2 设备", "callback_data": "dual:users:hy2"},
             {"text": "TUIC 设备", "callback_data": "dual:users:tuic"}],
            [{"text": "创建默认设备", "callback_data": "dual:defaults:prompt"}],
        ]},
    })


def send_item(chat_id, item):
    hy2.send_message(chat_id, "TUIC 设备：" + item["name"] + "\n" + item["uri"])
    hy2.send_photo(chat_id, item["qr_path"], "TUIC " + item["name"] + " 二维码")


def tuic_devices():
    users = manage("list").get("clients", [])
    lines = ["TUIC 设备列表（仅账号列表，不代表在线）："]
    lines.extend("• " + u["name"] for u in users)
    return "\n".join(lines)


def send_defaults(chat_id, which):
    if which in ("hy2", "both"):
        result = hy2.run_manager("add-defaults")
        for item in result.get("clients", []):
            hy2.send_client(chat_id, item, prefix="HY2\n")
    if which in ("tuic", "both"):
        result = manage("add-defaults")
        for item in result.get("clients", []):
            send_item(chat_id, item)


def defaults_prompt(chat_id):
    hy2.send_message(
        chat_id, "请选择 /add_defaults 需要发送的协议：",
        {"inline_keyboard": [
            [{"text": "HY2", "callback_data": "dual:defaults:hy2"},
             {"text": "TUIC", "callback_data": "dual:defaults:tuic"}],
            [{"text": "HY2 + TUIC", "callback_data": "dual:defaults:both"}],
        ]},
    )


def send_help(chat_id):
    hy2.send_message(chat_id,
        hy2.help_text() + "\n\n"
        "TUIC 扩展命令：\n"
        "/status - HY2 和 TUIC 左右并排状态\n"
        "/add_defaults - 选择 HY2、TUIC 或两者\n"
        "/devices_tuic - 查看 TUIC 账号\n"
        "/add_tuic IPAD - 添加 TUIC 设备\n"
        "/get_tuic IPAD - 获取 TUIC 节点二维码\n"
        "/delete_tuic IPAD - 删除 TUIC 账号\n"
        "/restart_tuic - 单独重启 TUIC\n"
        "原有 HY2 命令保持可用。")


def handle_message(message):
    if not hy2.authorized(message.get("from") or {}, message.get("chat") or {}):
        return
    args = (message.get("text") or "").strip().split()
    if not args:
        return
    command = args[0].split("@", 1)[0].lower()
    chat_id = message["chat"]["id"]
    try:
        if command == "/status":
            send_status(chat_id)
        elif command == "/start":
            send_status(chat_id)
            hy2.send_message(chat_id, hy2.devices_text())
        elif command == "/help":
            send_help(chat_id)
        elif command in ("/add_defaults", "/adddefaults", "/add-defaults"):
            defaults_prompt(chat_id)
        elif command == "/devices_tuic":
            hy2.send_message(chat_id, tuic_devices())
        elif command in ("/add_tuic", "/get_tuic", "/delete_tuic"):
            if len(args) != 2:
                hy2.send_message(chat_id, "用法：" + command + " WIN-PC")
                return
            name = args[1]
            if command == "/delete_tuic":
                hy2.send_message(chat_id, f"确认删除 TUIC 设备 {name}？只有 TUIC 会重启。", {
                    "inline_keyboard": [[
                        {"text": "确认", "callback_data": "dual:delete:" + name.upper()},
                        {"text": "取消", "callback_data": "dual:cancel"},
                    ]]
                })
            else:
                send_item(chat_id, manage("add" if command == "/add_tuic" else "get", name))
        elif command == "/restart_tuic":
            hy2.send_message(chat_id, "确定只重启 TUIC？不会重启 HY2。", {
                "inline_keyboard": [[
                    {"text": "确认重启 TUIC", "callback_data": "dual:restart:confirm"},
                    {"text": "取消", "callback_data": "dual:cancel"},
                ]]
            })
        else:
            old_handle_message(message)
    except Exception as exc:
        hy2.send_message(chat_id, "❌ TUIC 操作失败：" + str(exc)[:350])


def handle_callback(query):
    if not hy2.authorized(query.get("from") or {}, (query.get("message") or {}).get("chat") or {}):
        return
    data = query.get("data", "")
    if not data.startswith("dual:"):
        return old_handle_callback(query)
    hy2.telegram("answerCallbackQuery", {"callback_query_id": query["id"]})
    chat_id = query["message"]["chat"]["id"]
    try:
        if data == "dual:cancel":
            hy2.send_message(chat_id, "已取消。")
        elif data == "dual:users:hy2":
            hy2.send_message(chat_id, hy2.devices_text())
        elif data == "dual:users:tuic":
            hy2.send_message(chat_id, tuic_devices())
        elif data == "dual:defaults:prompt":
            defaults_prompt(chat_id)
        elif data.startswith("dual:defaults:"):
            send_defaults(chat_id, data.rsplit(":", 1)[1])
        elif data == "dual:restart:confirm":
            manage("restart")
            hy2.send_message(chat_id, "TUIC 重启命令执行成功。")
        elif data.startswith("dual:delete:"):
            name = data.split(":", 2)[2]
            result = manage("delete", name)
            hy2.send_message(chat_id, "TUIC 用户已删除：" + result["deleted"])
    except Exception as exc:
        hy2.send_message(chat_id, "❌ TUIC 操作失败：" + str(exc)[:350])


hy2.handle_message = handle_message
hy2.handle_callback = handle_callback

if __name__ == "__main__":
    hy2.main()
