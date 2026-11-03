#!/usr/bin/env bash
# Install the private Telegram monitor for an existing HY2 deployment.
set -Eeuo pipefail
umask 077

REPO_RAW="https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot"
CONFIG="/etc/hysteria/config.yaml"
SERVICE="hysteria-server.service"
BOT_DIR="/opt/hy2-telegram"
STATE_DIR="/var/lib/hy2-telegram"
ETC_DIR="/etc/hy2-telegram"
ENV_FILE="$ETC_DIR/bot.env"

fail() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }
log() { printf '\n[HY2 Telegram] %s\n' "$*"; }

[[ "$EUID" -eq 0 ]] || fail "请使用 root 账号运行。"
[[ -f "$CONFIG" ]] || fail "未找到 $CONFIG，请先完成 HY2 部署。"
[[ -f /root/hy2-output/node.txt && -s /root/hy2-output/node.txt ]] || fail "未找到节点链接 /root/hy2-output/node.txt。"
[[ -f /root/hy2-output/node-qr.png && -s /root/hy2-output/node-qr.png ]] || fail "未找到二维码 /root/hy2-output/node-qr.png。"
systemctl is-active --quiet "$SERVICE" || fail "Hysteria 服务当前不是 active，请先修复 HY2。"

printf '====================================================\n'
printf ' CHBVPS Hysteria 2 Telegram Monitor 安装器\n'
printf ' Telegram Bot API + Hysteria 官方 Traffic Stats API\n'
printf '====================================================\n\n'
printf '准备工作：\n'
printf '  1. 已通过 @BotFather 创建 Telegram Bot。\n'
printf '  2. 已取得 Bot Token。\n'
printf '  3. 已取得你自己的 Telegram 数字 User ID（不是用户名）。\n\n'

log "安装 Python、sudo、时区数据和基础证书……"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 sudo ca-certificates curl openssl tzdata

BOT_TOKEN="${BOT_TOKEN:-}"
ADMIN_USER_ID="${ADMIN_USER_ID:-}"

if [[ -z "$BOT_TOKEN" ]]; then
  read -r -s -p "粘贴 Telegram Bot Token（不会显示）： " BOT_TOKEN
  printf '\n'
fi
[[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || fail "Bot Token 格式不正确。"

if [[ -z "$ADMIN_USER_ID" ]]; then
  read -r -p "输入你的 Telegram 数字 User ID： " ADMIN_USER_ID
fi
[[ "$ADMIN_USER_ID" =~ ^[0-9]{5,15}$ ]] || fail "Telegram User ID 必须是数字。"

log "验证 Telegram Bot Token……"
BOT_TOKEN="$BOT_TOKEN" python3 - <<'PY'
import json, os, urllib.request
token = os.environ["BOT_TOKEN"]
url = "https://api.telegram.org/bot" + token + "/getMe"
try:
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.loads(response.read().decode())
    if not data.get("ok"):
        raise SystemExit("Telegram 拒绝了这个 Token。")
    bot = data["result"]
    print("Token 验证成功：@" + bot.get("username", "unknown"))
except Exception as exc:
    raise SystemExit("Token 验证失败或 VPS 无法访问 Telegram API：" + type(exc).__name__)
PY

if grep -Eq '^trafficStats:' "$CONFIG"; then
  fail "检测到现有 trafficStats 配置。为避免覆盖现有统计服务，请先人工检查 $CONFIG；本脚本没有修改它。"
fi

STATS_SECRET="$(openssl rand -hex 32)"
BACKUP="/root/hy2-config-before-telegram-$(date +%Y%m%d-%H%M%S).yaml"
cp -a "$CONFIG" "$BACKUP"

log "为 Hysteria 启用仅本机可访问的官方统计 API……"
printf '\n# Local-only API for the private Telegram monitor\ntrafficStats:\n  listen: 127.0.0.1:9999\n  secret: "%s"\n' "$STATS_SECRET" >> "$CONFIG"
chmod 640 "$CONFIG"

if ! systemctl restart "$SERVICE"; then
  cp -a "$BACKUP" "$CONFIG"
  systemctl restart "$SERVICE" || true
  fail "添加统计 API 后 Hysteria 未能启动，已恢复原配置。备份：$BACKUP"
fi
sleep 2
if ! systemctl is-active --quiet "$SERVICE"; then
  cp -a "$BACKUP" "$CONFIG"
  systemctl restart "$SERVICE" || true
  fail "Hysteria 未保持 active，已恢复原配置。备份：$BACKUP"
fi

HY2_STATS_SECRET="$STATS_SECRET" python3 - <<'PY'
import json, os, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:9999/online",
    headers={"Authorization": os.environ["HY2_STATS_SECRET"]},
)
try:
    with urllib.request.urlopen(req, timeout=5) as response:
        json.loads(response.read().decode())
except Exception as exc:
    raise SystemExit("Hysteria 统计 API 检查失败：" + type(exc).__name__)
PY

log "创建受限的机器人系统账户……"
if ! id hy2bot >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin hy2bot
fi
install -d -o root -g root -m 755 "$BOT_DIR"
install -d -o hy2bot -g hy2bot -m 750 "$STATE_DIR"
install -d -o root -g hy2bot -m 750 "$ETC_DIR"

log "从 GitHub 下载机器人程序……"
curl -fsSL --retry 3 "$REPO_RAW/bot.py" -o "$BOT_DIR/bot.py"
python3 -m py_compile "$BOT_DIR/bot.py"
chown root:root "$BOT_DIR/bot.py"
chmod 755 "$BOT_DIR/bot.py"

install -o hy2bot -g hy2bot -m 640 /root/hy2-output/node.txt "$STATE_DIR/node.txt"
install -o hy2bot -g hy2bot -m 640 /root/hy2-output/node-qr.png "$STATE_DIR/node-qr.png"

log "写入仅机器人可读的凭据文件……"
printf 'BOT_TOKEN=%s\nADMIN_USER_ID=%s\nHY2_STATS_SECRET=%s\nHY2_STATS_URL=http://127.0.0.1:9999\nNODE_FILE=%s/node.txt\nQR_FILE=%s/node-qr.png\nTZ=Asia/Shanghai\n' \
  "$BOT_TOKEN" "$ADMIN_USER_ID" "$STATS_SECRET" "$STATE_DIR" "$STATE_DIR" > "$ENV_FILE"
chown root:hy2bot "$ENV_FILE"
chmod 640 "$ENV_FILE"

log "配置仅允许机器人重启 HY2 的 sudo 规则……"
cat > /usr/local/sbin/hy2-restart <<'SH'
#!/bin/sh
exec /usr/bin/systemctl restart hysteria-server.service
SH
chown root:root /usr/local/sbin/hy2-restart
chmod 750 /usr/local/sbin/hy2-restart

printf 'hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/hy2-restart\n' > /etc/sudoers.d/hy2-telegram
chmod 440 /etc/sudoers.d/hy2-telegram
visudo -cf /etc/sudoers.d/hy2-telegram >/dev/null

log "创建 Telegram Bot systemd 服务……"
cat > /etc/systemd/system/hy2-telegram.service <<'UNIT'
[Unit]
Description=Private Telegram Monitor for Hysteria 2
After=network-online.target hysteria-server.service
Wants=network-online.target
Requires=hysteria-server.service

[Service]
Type=simple
User=hy2bot
Group=hy2bot
EnvironmentFile=/etc/hy2-telegram/bot.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/hy2-telegram/bot.py
Restart=always
RestartSec=5
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
NoNewPrivileges=false

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now hy2-telegram.service
sleep 2

printf '\n====================================================\n'
printf ' Telegram 机器人安装流程已完成\n'
printf '====================================================\n'
printf '机器人服务：'
systemctl is-active hy2-telegram.service || true
printf 'HY2 服务：'
systemctl is-active hysteria-server.service || true
printf '\n统计 API 监听（必须仅为 127.0.0.1:9999）：\n'
ss -lntp 'sport = :9999' || true
printf '\n常用命令：\n'
printf '机器人状态：systemctl status hy2-telegram --no-pager\n'
printf '机器人日志：journalctl -u hy2-telegram -f\n'
printf '重启机器人：systemctl restart hy2-telegram\n'
printf '停止机器人：systemctl stop hy2-telegram\n'
printf '\n安全提示：Bot Token、节点链接和统计 API 密钥均未上传 GitHub。\n'
printf '机器人只响应安装时绑定的 Telegram 数字 User ID，且仅接受私聊。\n'
printf 'Telegram 中打开你的 Bot，发送 /start，即可收到状态、节点链接和二维码。\n'
