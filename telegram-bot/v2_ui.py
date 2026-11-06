#!/usr/bin/env python3
"""CHBVPS V2 free minimal Telegram UI; no Premium or third-party libraries.

The module changes only Telegram presentation and forwards privileged mutations
to existing, root-owned helpers through a tightly scoped sudo policy.
"""
import html
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

COMMANDS = [
    {"command": "start", "description": "✦ 主控制台"},
    {"command": "status", "description": "◈ 实时状态"},
    {"command": "devices", "description": "▣ 设备与流量"},
    {"command": "get", "description": "◇ 获取节点"},
    {"command": "add", "description": "＋ 添加设备"},
    {"command": "del", "description": "－ 删除设备"},
    {"command": "restart", "description": "⚙ 系统维护"},
    {"command": "update", "description": "↥ 核心更新"},
    {"command": "help", "description": "ⓘ 使用帮助"},
]
DEFAULTS = ("IPHONE", "MACBOOK", "WIN-PC", "FAMILY")
PROTOS = ("hy2", "tuic", "vision", "xhttp")
MANAGED = ("hy2", "tuic")
UI_COMMANDS = frozenset("/" + c["command"] for c in COMMANDS)
DIVIDER = "━━━━━━━━━━━━━━━━━━"
MAX_DEVICE = 32
PENDING = {}
PENDING_LOCK = threading.Lock()
OPERATIONS_LOCK = threading.Lock()
UPDATE_LOCK = threading.Lock()
CONFIRM_TTL = 120
CORE_UPDATE = "/usr/local/sbin/core-update"
SERVICES = {
    "hy2": ("hysteria-server.service", "UDP", 443),
    "tuic": ("tuic-singbox.service", "UDP", 51443),
    "vision": ("xray-vision.service", "TCP", 8443),
    "xhttp": ("xray.service", "TCP", 443),
}
DESCRIPTIONS = {"hy2": "HY2", "tuic": "TUIC", "vision": "Vision", "xhttp": "XHTTP"}


def esc(value):
    return html.escape(str(value), quote=False)


def button(label, data, style=None):
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Callback data exceeds Telegram limit")
    result = {"text": label, "callback_data": data}
    if style in ("primary", "success", "danger"):
        result["style"] = style
    return result


def keyboard(*rows):
    return {"inline_keyboard": [list(row) for row in rows if row]}


def home_row():
    return [button("⌂ 主页", "ui:nav:home", "primary")]


def back_row(page="home"):
    return [button("↩ 返回", "ui:nav:" + page), button("⌂ 主页", "ui:nav:home", "primary")]


def present(bot, chat_id, title, content, rows=(), message_id=None):
    text = f"<b>{esc(title)}</b>\n\n{content}"
    payload = {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": keyboard(*rows),
    }
    if message_id is not None:
        payload["message_id"] = message_id
        try:
            return bot.telegram("editMessageText", payload)
        except RuntimeError as exc:
            if "message is not modified" in str(exc).lower():
                return None
            # Old/deleted messages cannot be edited; use a fresh menu.
            payload.pop("message_id")
    return bot.telegram("sendMessage", payload)


def telegram_notice(bot, chat, text):
    return bot.send_message(chat, text)


def authorized(bot, user, chat):
    return bot.authorized(user, chat)


def service_state(service):
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=4, check=False,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def listener(proto):
    unit, network, port = SERVICES[proto]
    args = ["ss", "-H", "-lun" if network == "UDP" else "-ltn", "sport = :" + str(port)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False)
        return bool(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def state_line(proto):
    unit, network, port = SERVICES[proto]
    state = service_state(unit)
    listening = listener(proto) if state == "active" else False
    symbol = "🟢" if state == "active" and listening else "🔴" if state != "active" or listening is False else "⚪"
    detail = f"{network} {port}"
    if state != "active":
        detail += " · " + state
    elif listening is False:
        detail += " · 未监听"
    return f"{symbol} <b>{DESCRIPTIONS[proto]}</b>  <code>{detail}</code>"


def menu_home(bot, chat_id, mid=None):
    lines = [
        "<b>◈ 服务状态</b>", DIVIDER,
        *(state_line(p) for p in PROTOS),
        "", f"系统  <code>{esc(socket.gethostname())}</code>",
        f"运行  {esc(bot.uptime_text())}",
    ]
    mem = bot.read_meminfo()
    if mem.get("MemTotal"):
        used = max(0, mem["MemTotal"] - mem.get("MemAvailable", 0))
        lines.append(f"内存  {bot.mb(used):.0f} / {bot.mb(mem['MemTotal']):.0f} MB")
    rows = [
        [button("◈ 实时状态", "ui:nav:status"), button("◇ 获取节点", "ui:nav:get", "primary")],
        [button("▣ 设备管理", "ui:nav:devices"), button("↥ 核心更新", "ui:nav:update")],
        [button("⚙ 系统维护", "ui:nav:restart"), button("ⓘ 使用帮助", "ui:nav:help")],
    ]
    return present(bot, chat_id, "✦ CHBVPS · 控制中心", "\n".join(lines), rows, mid)


def menu_status(bot, chat_id, mid=None):
    m = bot.read_meminfo()
    total = m.get("MemTotal", 0)
    used = max(0, total - m.get("MemAvailable", 0))
    swap_total = m.get("SwapTotal", 0)
    swap_used = max(0, swap_total - m.get("SwapFree", 0))
    try:
        load = " / ".join(f"{x:.2f}" for x in os.getloadavg())
    except OSError:
        load = "不可用"
    try:
        online, accounts = bot.online_totals()
        online_text = f"{online} 个连接实例（{accounts} 个账号）"
    except Exception:
        online_text = "统计不可用"
    try:
        tx, rx = bot.traffic_totals()
        traffic = (
            f"↑ 上传  {bot.fmt_bytes(tx)}\n"
            f"↓ 下载  {bot.fmt_bytes(rx)}\n"
            f"Σ 合计  {bot.fmt_bytes(tx + rx)}"
        )
    except Exception:
        traffic = "统计 API 暂不可用"
    lines = [
        "<b>系统资源</b>", DIVIDER,
        f"⏱ 运行  {esc(bot.uptime_text())}",
        f"负载  <code>{esc(load)}</code>",
        f"RAM  {bot.mb(used):.0f} / {bot.mb(total):.0f} MB",
        f"Swap  {bot.mb(swap_used):.0f} / {bot.mb(swap_total):.0f} MB",
        "", "<b>服务状态</b>", DIVIDER,
        *(state_line(p) for p in PROTOS),
        "", "<b>HY2 流量（本次服务运行周期）</b>", DIVIDER,
        traffic, f"HY2 在线  {online_text}",
        "", "ⓘ TUIC / Vision / XHTTP 暂无统一实时流量或在线数据。",
        "ⓘ 本机监听不等于公网连通。",
    ]
    rows = [
        [button("↻ 刷新", "ui:nav:status"), button("▣ 设备流量", "ui:dev:hy2")],
        home_row(),
    ]
    return present(bot, chat_id, "◈ CHBVPS · 实时状态", "\n".join(lines), rows, mid)


def proto_clients(bot, proto, tuic=None):
    if proto == "hy2":
        return sorted(item["name"] for item in bot.run_manager("list").get("clients", []))
    if proto == "tuic":
        if tuic is None:
            return []
        return sorted(item["name"] for item in tuic("list").get("clients", []))
    if proto == "vision":
        return bot.vision_names()
    if proto == "xhttp":
        return bot.xray_names()
    raise ValueError("不支持的协议")


def get_item(bot, name, proto, tuic=None):
    if proto == "hy2":
        return bot.run_manager("get", name)
    if proto == "tuic":
        if tuic is None:
            raise RuntimeError("TUIC 管理模块未安装")
        return tuic("get", name)
    if proto == "vision":
        return bot.vision_client(name)
    if proto == "xhttp":
        return bot.xray_client(name)
    raise ValueError("不支持的协议")


def check_device(name):
    name = str(name).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", name):
        raise ValueError("设备名称不合法（只支持字母、数字、_、-，最多32位）")
    return name


def menu_devices(bot, chat_id, mid=None, tuic=None):
    lines = ["<b>协议账号概览</b>", DIVIDER]
    for p in PROTOS:
        try:
            count = len(proto_clients(bot, p, tuic))
            label = f"{count} 个账号"
        except Exception:
            label = "读取失败"
        lines.append(f"{DESCRIPTIONS[p]}  {label}")
    lines.extend(["", "选择协议查看设备详情。"])
    rows = [
        [button("HY2", "ui:dev:hy2"), button("TUIC", "ui:dev:tuic")],
        [button("Vision", "ui:dev:vision"), button("XHTTP", "ui:dev:xhttp")],
        [button("＋ 添加设备", "ui:nav:add"), button("－ 删除设备", "ui:nav:del", "danger")],
        home_row(),
    ]
    return present(bot, chat_id, "▣ CHBVPS · 设备管理", "\n".join(lines), rows, mid)


def menu_device_proto(bot, chat_id, proto, mid=None, tuic=None):
    names = proto_clients(bot, proto, tuic)
    lines = [f"当前账号：<b>{len(names)}</b> 个", DIVIDER]
    if proto == "hy2":
        try:
            online = bot.online_data()
            traffic = bot.traffic_data()
            available = True
        except Exception:
            online, traffic, available = {}, {}, False
        for name in names:
            n = int(online.get(name.lower(), 0) or 0)
            item = traffic.get(name.lower(), {})
            if not isinstance(item, dict):
                item = {}
            tx, rx = int(item.get("tx", 0) or 0), int(item.get("rx", 0) or 0)
            status = ("🟢 在线 " + str(n)) if n else ("⚪ 离线" if available else "⚪ 未知")
            lines.append(f"<b>{esc(name)}</b>  {status}")
            if available:
                lines.append(f"  ↑ {bot.fmt_bytes(tx)}  ↓ {bot.fmt_bytes(rx)}")
                lines.append(f"  Σ {bot.fmt_bytes(tx + rx)}")
        lines.append("\nⓘ HY2 流量仅为当前统计周期，不代表历史累计。")
    else:
        lines.extend(f"• <code>{esc(n)}</code>" for n in names)
        lines.append("\nⓘ 当前不提供此协议的可靠在线人数统计。")
    if not names:
        lines.append("暂无已配置的账号。")
    rows = []
    if proto in MANAGED:
        rows.append([button("＋ 添加设备", "ui:nav:add"), button("－ 删除设备", "ui:nav:del", "danger")])
    rows.append([button("↻ 刷新", "ui:dev:" + proto), button("↩ 返回", "ui:nav:devices")])
    rows.append(home_row())
    return present(bot, chat_id, f"▣ CHBVPS · {DESCRIPTIONS[proto]} 设备", "\n".join(lines), rows, mid)


def menu_get(bot, chat_id, mid=None):
    text = "<b>请选择设备</b>\n" + DIVIDER + "\n可获取 HY2、TUIC、Vision、XHTTP 已有节点。"
    rows = [
        [button("IPHONE", "ui:get:IPHONE:all"), button("MACBOOK", "ui:get:MACBOOK:all")],
        [button("WIN-PC", "ui:get:WIN-PC:all"), button("FAMILY", "ui:get:FAMILY:all")],
        [button("◇ 全部默认设备", "ui:get:defaults")],
        home_row(),
    ]
    return present(bot, chat_id, "◇ CHBVPS · 获取节点", text, rows, mid)


def get_links(bot, chat_id, name, proto="all", mid=None, tuic=None):
    name = check_device(name)
    if proto not in PROTOS and proto != "all":
        raise ValueError("协议仅支持 hy2/tuic/vision/xhttp")
    selected = PROTOS if proto == "all" else (proto,)
    lines = [f"设备  <code>{esc(name)}</code>", DIVIDER]
    available = []
    for p in selected:
        try:
            item = get_item(bot, name, p, tuic)
            lines.append(f"<b>{DESCRIPTIONS[p]}</b>\n<code>{esc(item['uri'])}</code>\n")
            available.append(p)
        except Exception:
            # Do not create missing accounts or alter configuration merely by
            # reading node links; report absence explicitly.
            lines.append(f"{DESCRIPTIONS[p]}  · 未找到节点或读取失败")
    if not available:
        lines.append("\n未找到可用节点，请检查账号或服务配置。")
    else:
        lines.append("按需获取二维码或 Loon 专用配置。")
    rows = []
    for offset in range(0, len(available), 2):
        rows.append([
            button(DESCRIPTIONS[p] + " 二维码", f"ui:qr:{name}:{p}")
            for p in available[offset:offset+2]
        ])
    if "vision" in available:
        rows.append([button("▤ Vision Loon 配置", f"ui:cfg:{name}:vision")])
    rows.append([button("↩ 选设备", "ui:nav:get"), button("⌂ 主页", "ui:nav:home", "primary")])
    # A four-protocol URI collection fits in Telegram 4096 chars for normal
    # credentials; do not silently truncate links if custom values are huge.
    content = "\n".join(lines)
    if len(content) > 3500:
        raise ValueError("节点内容过长，请使用 /get DEVICE PROTOCOL 单独获取")
    return present(bot, chat_id, "◇ CHBVPS · 获取节点", content, rows, mid)


def send_qr(bot, chat_id, name, proto, tuic=None):
    item = get_item(bot, check_device(name), proto, tuic)
    bot.send_photo(chat_id, item["qr_path"], f"◇ {DESCRIPTIONS[proto]} · {item['name']} 二维码")


def send_loon(bot, chat_id, name):
    item = bot.vision_client(check_device(name))
    bot.send_message(chat_id, "▤ Vision · " + item["name"] + "\n\n" + item["loon"])


def menu_help(bot, chat_id, mid=None):
    text = (
        "<b>基础与状态</b>\n"
        "/start  主控制台\n/status  实时状态\n/help  命令帮助\n\n"
        "<b>设备与节点</b>\n"
        "/devices [协议]  设备统计\n"
        "/get [设备] [协议]  获取节点\n"
        "/add &lt;设备&gt; [协议]  添加账号\n"
        "/del &lt;设备&gt; [协议]  删除账号\n\n"
        "<b>系统维护</b>\n"
        "/restart [协议]  重启服务\n"
        "/update  检查核心更新\n\n"
        "ⓘ 支持 HY2 / TUIC / Vision / XHTTP；仅管理员私聊可用。\n"
        "ⓘ 旧命令继续兼容，但不显示在命令菜单中。"
    )
    return present(bot, chat_id, "ⓘ CHBVPS · 使用帮助", text, [home_row()], mid)


def add_menu(bot, chat_id, name=None, mid=None, tuic=None):
    if name is None:
        return present(
            bot, chat_id, "＋ CHBVPS · 添加设备",
            "请输入 <code>/add IPAD</code>，然后选择 HY2 / TUIC。\n\n"
            "ⓘ Vision、XHTTP 暂无动态账号管理功能。",
            [home_row()], mid,
        )
    name = check_device(name)
    rows = [[button("HY2", f"ui:add:{name}:hy2"), button("TUIC", f"ui:add:{name}:tuic")]]
    if tuic is not None:
        rows.append([button("HY2 + TUIC", f"ui:add:{name}:both")])
    rows.append(home_row())
    return present(
        bot, chat_id, "＋ CHBVPS · 添加设备",
        f"设备  <code>{esc(name)}</code>\n\n选择创建协议。"
        "\nⓘ 若两协议仅部分成功，会明确报告；不会隐式删除已创建的账号。",
        rows, mid,
    )


def add_execute(bot, chat_id, name, proto, tuic=None):
    name = check_device(name)
    if proto not in ("hy2", "tuic", "both"):
        raise ValueError("新增只支持 HY2、TUIC")
    if proto in ("tuic", "both") and tuic is None:
        raise RuntimeError("TUIC 管理器不可用，未修改任何账号")
    lines = [f"设备：{name}"]
    created = []
    if proto in ("hy2", "both"):
        try:
            item = bot.run_manager("add", name)
            created.append(("hy2", item))
            lines.append("✓ HY2 创建成功")
        except Exception as exc:
            lines.append("× HY2 创建失败：" + str(exc)[:150])
    if proto in ("tuic", "both"):
        if tuic is None:
            lines.append("× TUIC 管理器不可用")
        else:
            try:
                item = tuic("add", name)
                created.append(("tuic", item))
                lines.append("✓ TUIC 创建成功")
            except Exception as exc:
                lines.append("× TUIC 创建失败：" + str(exc)[:150])
    if not created:
        bot.send_message(chat_id, "× 创建失败\n" + "\n".join(lines))
        return
    if len(created) == (2 if proto == "both" else 1):
        lines.insert(0, "✓ 设备创建成功")
    else:
        lines.insert(0, "⚠ 部分创建成功：请根据结果单独补建缺失协议")
    bot.send_message(chat_id, "\n".join(lines))
    get_links(bot, chat_id, name, "all" if proto == "both" else proto, tuic=tuic)


def del_menu(bot, chat_id, name=None, mid=None):
    if name is None:
        return present(
            bot, chat_id, "－ CHBVPS · 删除设备",
            "请输入 <code>/del IPAD</code> 选择协议。\n"
            "ⓘ 删除操作始终需要二次确认。",
            [home_row()], mid,
        )
    name = check_device(name)
    return present(
        bot, chat_id, "－ CHBVPS · 删除设备",
        f"设备  <code>{esc(name)}</code>\n\n请选择协议（暂仅支持 HY2 / TUIC）。",
        [[button("HY2", f"ui:ask:del:{name}:hy2", "danger"),
          button("TUIC", f"ui:ask:del:{name}:tuic", "danger")], home_row()], mid,
    )


def restart_menu(bot, chat_id, mid=None):
    rows = [
        [button("HY2", "ui:ask:rst:hy2"), button("TUIC", "ui:ask:rst:tuic")],
        [button("Vision", "ui:ask:rst:vision"), button("XHTTP", "ui:ask:rst:xhttp")],
        [button("↻ 重启全部服务", "ui:ask:rst:all", "danger")],
        home_row(),
    ]
    return present(
        bot, chat_id, "⚙ CHBVPS · 系统维护",
        "选择需要重启的服务。\n\n⚠ 重启会短暂中断对应连接；执行前必须确认。",
        rows, mid,
    )


def menu_update(bot, chat_id, mid=None, details=None):
    lines = [
        "<b>当前核心与升级范围</b>", DIVIDER,
        f"HY2  <code>{esc(bot.hy2_version())}</code>",
        f"Xray  <code>{esc(xray_version(bot))}</code>",
        "TUIC (sing-box)  按检查结果显示",
        "", "ⓘ 自动升级仅管理 HY2 和 sing-box；Xray 暂不自动升级。",
    ]
    if details:
        lines.extend(["", DIVIDER, *details])
    rows = [
        [button("↻ 检查更新", "ui:upd:check", "primary")],
        [button("↥ 更新 HY2", "ui:ask:upd:hy2"), button("↥ 更新 TUIC", "ui:ask:upd:tuic")],
        [button("↥ 更新可管理核心", "ui:ask:upd:all")],
        home_row(),
    ]
    return present(bot, chat_id, "↥ CHBVPS · 核心更新", "\n".join(lines), rows, mid)


def xray_version(bot):
    try:
        r = subprocess.run(
            [bot.XRAY_BIN, "version"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return (r.stdout or r.stderr).splitlines()[0].strip().split(" ")[1]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return "未知"


def update_check():
    proc = subprocess.run(
        ["/usr/bin/sudo", "-n", CORE_UPDATE, "--check", "--json"],
        capture_output=True, text=True, timeout=150, check=False,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or "核心版本检查失败")[-500:])
    return json.loads(proc.stdout)


def update_check_worker(bot, chat_id, mid):
    try:
        result = update_check()
        lines = []
        for key, label in (("hy2", "HY2"), ("singbox", "sing-box")):
            item = result.get(key, {})
            if not item.get("installed"):
                lines.append(label + "：未安装")
            else:
                current = item.get("current") or "未知"
                latest = item.get("latest") or "未知"
                lines.append(
                    f"{label}：{esc(current)} → {esc(latest)}"
                    if item.get("update_available") else
                    f"{label}：{esc(current)}（无可用更新）"
                )
        menu_update(bot, chat_id, mid, lines)
    except Exception as exc:
        present(bot, chat_id, "× CHBVPS · 版本检查失败",
                esc(str(exc)[:400]), [home_row()], mid)


def issue_confirm(bot, chat_id, mid, op, proto, name=None, tuic=None):
    if op == "del":
        if proto == "tuic" and tuic is None:
            raise ValueError("TUIC 管理器不可用")
        if proto not in MANAGED or not name:
            raise ValueError("仅 HY2/TUIC 支持删除")
        name = check_device(name)
        # Fail closed: do not offer an approval for a missing account.
        if name not in proto_clients(bot, proto, tuic):
            raise ValueError("设备不存在：" + name)
        title = "⚠ CHBVPS · 删除确认"
        body = f"协议  {DESCRIPTIONS[proto]}\n设备  <code>{esc(name)}</code>\n\n删除后该账号的节点将立即失效。"
    elif op == "rst":
        if proto not in PROTOS and proto != "all":
            raise ValueError("不支持的服务")
        title = "⚠ CHBVPS · 重启确认"
        targets = PROTOS if proto == "all" else (proto,)
        body = "操作  重启代理服务\n" + "\n".join(
            f"• {DESCRIPTIONS[p]}" for p in targets
        ) + "\n\n当前连接可能短暂断开。"
    elif op == "upd":
        if proto not in ("hy2", "tuic", "all"):
            raise ValueError("仅 HY2 和 sing-box 支持自动升级")
        title = "⚠ CHBVPS · 升级确认"
        target = {"hy2": "HY2", "tuic": "sing-box / TUIC", "all": "HY2 + sing-box"}[proto]
        body = f"即将检查并升级  {target}\n\n更新期间对应连接可能短暂断开。\nXray 不参与自动升级。"
    else:
        raise ValueError("未知操作")
    token = secrets.token_urlsafe(9)
    # Approval is bound to chat and exact message ID, expires in 2 minutes,
    # and is removed before execution to avoid double-tap replay.
    with PENDING_LOCK:
        now = time.monotonic()
        for key, value in list(PENDING.items()):
            if value["expires"] <= now:
                PENDING.pop(key, None)
        PENDING[token] = {
            "op": op, "proto": proto, "name": name, "chat": chat_id,
            "mid": mid, "expires": now + CONFIRM_TTL,
        }
    rows = [[button("✓ 确认执行", "ui:do:" + token,
                    "danger" if op in ("del", "rst") else "success"),
             button("× 取消", "ui:cancel:" + token)]]
    result = present(bot, chat_id, title, body + "\n\nⓘ 确认有效期 2 分钟。", rows, mid)
    if mid is None and isinstance(result, dict):
        with PENDING_LOCK:
            if token in PENDING:
                PENDING[token]["mid"] = result.get("message_id")
    return result


def take_pending(token, chat_id, mid, consume=True):
    with PENDING_LOCK:
        value = PENDING.get(token)
        if not value or value["chat"] != chat_id or value["mid"] != mid:
            return None
        if value["expires"] < time.monotonic():
            PENDING.pop(token, None)
            return None
        if consume:
            PENDING.pop(token, None)
        return value


def sudo_run(args, timeout=55):
    result = subprocess.run(
        ["/usr/bin/sudo", "-n"] + args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "执行失败")[-450:])
    return result


def restart_one(proto, tuic=None):
    if proto == "hy2":
        sudo_run(["/usr/local/sbin/hy2-restart"])
    elif proto == "tuic":
        if tuic is None:
            raise RuntimeError("TUIC 未部署")
        tuic("restart")
    elif proto in ("vision", "xhttp"):
        svc = SERVICES[proto][0]
        sudo_run(["/usr/bin/systemctl", "restart", svc])
    else:
        raise ValueError("未知协议")
    if service_state(SERVICES[proto][0]) != "active":
        raise RuntimeError("重启后服务未保持 active")
    return "active"


def action_worker(bot, chat_id, mid, action, tuic=None):
    op, proto, name = action["op"], action["proto"], action["name"]
    if not OPERATIONS_LOCK.acquire(blocking=False):
        present(bot, chat_id, "⚠ CHBVPS · 操作繁忙",
                "已有系统维护或升级任务正在执行。", [home_row()], mid)
        return
    try:
        if op == "del":
            result = bot.run_manager("delete", name) if proto == "hy2" else tuic("delete", name)
            present(bot, chat_id, "✓ CHBVPS · 删除完成",
                    f"协议  {DESCRIPTIONS[proto]}\n设备  <code>{esc(result['deleted'])}</code>\n"
                    "该账号凭据已经失效。",
                    [[button("▣ 设备管理", "ui:nav:devices")], home_row()], mid)
        elif op == "rst":
            lines = []
            targets = PROTOS if proto == "all" else (proto,)
            for p in targets:
                try:
                    # An uninstalled TUIC service is not an error for "all".
                    if p == "tuic" and tuic is None:
                        lines.append("⚪ TUIC  未安装，跳过")
                        continue
                    restart_one(p, tuic)
                    lines.append(f"✓ {DESCRIPTIONS[p]}  active")
                except Exception as exc:
                    lines.append(f"× {DESCRIPTIONS[p]}  {esc(str(exc)[:150])}")
            present(bot, chat_id, "⚙ CHBVPS · 重启结果", "\n".join(lines),
                    [[button("◈ 查看状态", "ui:nav:status")], home_row()], mid)
        elif op == "upd":
            mode = {"hy2": "--hy2", "tuic": "--singbox", "all": "--all"}[proto]
            result = sudo_run([CORE_UPDATE, mode, "--yes", "--json"], timeout=1800)
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise RuntimeError("更新命令已结束但结果无法解析，请检查 SSH 日志")
            if not data.get("ok"):
                raise RuntimeError("核心更新未报告成功")
            lines = [
                f"模式  {esc(proto)}",
                "结果  " + ("已完成" if data.get("changed") else "当前已是最新稳定版"),
            ]
            for key in ("hy2", "singbox"):
                item = data.get(key, {})
                if item.get("from") or item.get("to"):
                    lines.append(f"{key}  {esc(item.get('from') or '?')} → {esc(item.get('to') or '?')}")
            present(bot, chat_id, "↥ CHBVPS · 核心更新结果",
                    "\n".join(lines), [home_row()], mid)
    except Exception as exc:
        present(bot, chat_id, "× CHBVPS · 操作失败",
                esc(str(exc)[:500]) + "\n\nⓘ 如涉及服务，请通过 SSH 查看日志。",
                [home_row()], mid)
    finally:
        OPERATIONS_LOCK.release()


def async_action(bot, chat_id, mid, value, tuic=None):
    present(bot, chat_id, "◌ CHBVPS · 正在执行",
            "操作已确认。执行完成后会在此处显示结果。", [], mid)
    threading.Thread(
        target=action_worker, args=(bot, chat_id, mid, value, tuic),
        daemon=True, name="chbvps-action",
    ).start()


def render_nav(bot, chat_id, target, mid=None, tuic=None):
    if target == "home":
        return menu_home(bot, chat_id, mid)
    if target == "status":
        return menu_status(bot, chat_id, mid)
    if target == "devices":
        return menu_devices(bot, chat_id, mid, tuic)
    if target == "get":
        return menu_get(bot, chat_id, mid)
    if target == "help":
        return menu_help(bot, chat_id, mid)
    if target == "restart":
        return restart_menu(bot, chat_id, mid)
    if target == "update":
        return menu_update(bot, chat_id, mid)
    if target == "add":
        return add_menu(bot, chat_id, mid=mid, tuic=tuic)
    if target == "del":
        return del_menu(bot, chat_id, mid=mid)
    raise ValueError("未知页面")


def handle_message(message, bot, tuic=None):
    user, chat = message.get("from") or {}, message.get("chat") or {}
    if not authorized(bot, user, chat):
        return True
    tokens = (message.get("text") or "").strip().split()
    if not tokens:
        return False
    command = tokens[0].split("@", 1)[0].lower()
    if command not in UI_COMMANDS:
        return False
    args = tokens[1:]
    chat_id = chat["id"]
    try:
        if command in ("/start", "/help", "/status"):
            if args: raise ValueError("该命令不接受参数")
            render_nav(bot, chat_id, {"/start":"home", "/help":"help", "/status":"status"}[command], tuic=tuic)
        elif command == "/devices":
            if len(args) > 1: raise ValueError("用法：/devices [hy2|tuic|vision|xhttp]")
            if args:
                proto = args[0].lower()
                if proto not in PROTOS: raise ValueError("不支持的协议")
                menu_device_proto(bot, chat_id, proto, tuic=tuic)
            else: menu_devices(bot, chat_id, tuic=tuic)
        elif command == "/get":
            if not args: menu_get(bot, chat_id)
            elif len(args) in (1, 2):
                proto = args[1].lower() if len(args)==2 else "all"
                get_links(bot, chat_id, args[0], proto, tuic=tuic)
            else: raise ValueError("用法：/get [设备] [协议]")
        elif command == "/add":
            if not args: add_menu(bot, chat_id, tuic=tuic)
            elif len(args) == 1: add_menu(bot, chat_id, args[0], tuic=tuic)
            elif len(args) == 2: add_execute(bot, chat_id, args[0], args[1].lower(), tuic)
            else: raise ValueError("用法：/add 设备 [hy2|tuic|both]")
        elif command == "/del":
            if not args: del_menu(bot, chat_id)
            elif len(args) == 1: del_menu(bot, chat_id, args[0])
            elif len(args) == 2: issue_confirm(bot, chat_id, None, "del", args[1].lower(), args[0], tuic)
            else: raise ValueError("用法：/del 设备 [hy2|tuic]")
        elif command == "/restart":
            if not args: restart_menu(bot, chat_id)
            elif len(args) == 1: issue_confirm(bot, chat_id, None, "rst", args[0].lower(), tuic=tuic)
            else: raise ValueError("用法：/restart [hy2|tuic|vision|xhttp|all]")
        elif command == "/update":
            if args: raise ValueError("用法：/update")
            menu_update(bot, chat_id)
    except Exception as exc:
        bot.send_message(chat_id, "× 操作失败：" + str(exc)[:500])
    return True


def handle_callback(query, bot, tuic=None):
    user, message = query.get("from") or {}, query.get("message") or {}
    chat = message.get("chat") or {}
    if not authorized(bot, user, chat):
        bot.telegram("answerCallbackQuery", {"callback_query_id": query["id"], "text": "无权限", "show_alert": True})
        return True
    data = query.get("data") or ""
    if not data.startswith("ui:"):
        return False
    bot.telegram("answerCallbackQuery", {"callback_query_id": query["id"]})
    chat_id, mid = chat["id"], message.get("message_id")
    try:
        parts = data.split(":")
        if len(parts) < 3:
            raise ValueError("无效的按钮")
        action = parts[1]
        if action == "nav" and len(parts)==3:
            render_nav(bot, chat_id, parts[2], mid, tuic)
        elif action == "dev" and len(parts)==3 and parts[2] in PROTOS:
            menu_device_proto(bot, chat_id, parts[2], mid, tuic)
        elif action == "get":
            if data == "ui:get:defaults":
                for name in DEFAULTS:
                    get_links(bot, chat_id, name, tuic=tuic)
                present(bot, chat_id, "◇ CHBVPS · 全部设备", "默认设备链接已发送。", [home_row()], mid)
            elif len(parts)==4:
                get_links(bot, chat_id, parts[2], parts[3], mid, tuic)
            else: raise ValueError("无效节点按钮")
        elif action == "qr" and len(parts)==4 and parts[3] in PROTOS:
            send_qr(bot, chat_id, parts[2], parts[3], tuic)
        elif action == "cfg" and len(parts)==4 and parts[3]=="vision":
            send_loon(bot, chat_id, parts[2])
        elif action == "add" and len(parts)==4:
            if parts[3] not in ("hy2", "tuic", "both"): raise ValueError("不支持的新增协议")
            present(bot, chat_id, "＋ CHBVPS · 正在添加",
                    f"设备 <code>{esc(check_device(parts[2]))}</code>，请查看随后发送的创建结果。", [], mid)
            add_execute(bot, chat_id, parts[2], parts[3], tuic)
        elif action == "ask" and len(parts) in (4, 5):
            op = parts[2]
            if op=="del" and len(parts)==5:
                issue_confirm(bot, chat_id, mid, op, parts[4], parts[3], tuic)
            elif op in ("rst", "upd") and len(parts)==4:
                issue_confirm(bot, chat_id, mid, op, parts[3], tuic=tuic)
            else: raise ValueError("无效确认请求")
        elif action in ("do", "cancel") and len(parts)==3:
            pending = take_pending(parts[2], chat_id, mid)
            if pending is None:
                present(bot, chat_id, "⚠ CHBVPS · 确认已失效",
                        "确认可能已经使用或超过 2 分钟，请重新发起操作。", [home_row()], mid)
            elif action=="cancel":
                present(bot, chat_id, "× CHBVPS · 操作取消",
                        "未执行任何变更。", [home_row()], mid)
            else:
                async_action(bot, chat_id, mid, pending, tuic)
        elif action == "upd" and data=="ui:upd:check":
            present(bot, chat_id, "◌ CHBVPS · 正在检查版本",
                    "正在查询官方最新稳定版本……", [], mid)
            threading.Thread(
                target=update_check_worker, args=(bot, chat_id, mid),
                name="chbvps-version-check", daemon=True,
            ).start()
        else:
            raise ValueError("未知或已过期的按钮")
    except Exception as exc:
        present(bot, chat_id, "× CHBVPS · 操作失败",
                esc(str(exc)[:450]), [home_row()], mid)
    return True
