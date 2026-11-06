#!/usr/bin/env bash
# Upgrade an already-installed HY2 Telegram bot to per-device accounts.
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot"
fail() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }
log() { printf '\n[HY2 Telegram] %s\n' "$*"; }

[[ "$EUID" -eq 0 ]] || fail "请使用 root 账号运行。"
[[ -f /etc/hysteria/config.yaml ]] || fail "未找到 Hysteria 服务端配置。"
[[ -f /etc/hy2-telegram/bot.env ]] || fail "未检测到已安装的 Telegram 机器人配置。"
[[ -f /root/hy2-output/client.yaml ]] || fail "未找到客户端模板 /root/hy2-output/client.yaml。"
systemctl is-active --quiet hysteria-server.service || fail "HY2 当前未运行，请先修复服务。"
systemctl is-active --quiet hy2-telegram.service || fail "Telegram 机器人当前未运行，请先修复机器人。"

log "安装 YAML 解析器和二维码工具……"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3-yaml qrencode sudo

getent group hy2bot >/dev/null 2>&1 || fail "缺少 hy2bot 用户组。"
id hy2bot >/dev/null 2>&1 || fail "缺少 hy2bot 系统用户。"

log "备份当前机器人程序和 systemd 文件……"
STAMP="$(date +%Y%m%d-%H%M%S)"
cp -a /opt/hy2-telegram/bot.py "/opt/hy2-telegram/bot.py.backup-$STAMP"
cp -a /etc/systemd/system/hy2-telegram.service "/etc/systemd/system/hy2-telegram.service.backup-$STAMP"

log "准备客户端模板……"
install -d -o hy2bot -g hy2bot -m 750 /var/lib/hy2-telegram
install -o root -g hy2bot -m 640 /root/hy2-output/client.yaml /var/lib/hy2-telegram/base-client.yaml

log "下载多设备管理程序……"
curl -fsSL --retry 3 "$RAW/hy2-users.py" -o /usr/local/sbin/hy2-users
curl -fsSL --retry 3 "$RAW/v2_ui.py" -o /opt/hy2-telegram/v2_ui.py
curl -fsSL --retry 3 "$RAW/bot.py" -o /opt/hy2-telegram/bot.py
python3 -m py_compile /usr/local/sbin/hy2-users /opt/hy2-telegram/bot.py /opt/hy2-telegram/v2_ui.py
chown root:root /usr/local/sbin/hy2-users /opt/hy2-telegram/bot.py /opt/hy2-telegram/v2_ui.py
chmod 750 /usr/local/sbin/hy2-users
chmod 755 /opt/hy2-telegram/bot.py /opt/hy2-telegram/v2_ui.py

log "更新受限 sudo 权限……"
cat > /etc/sudoers.d/hy2-telegram <<'SUDO'
hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/hy2-restart, /usr/local/sbin/hy2-users *
SUDO
chmod 440 /etc/sudoers.d/hy2-telegram
visudo -cf /etc/sudoers.d/hy2-telegram >/dev/null

log "更新机器人 systemd 文件访问范围……"
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
ReadWritePaths=/etc/hysteria /etc/hy2-telegram /var/lib/hy2-telegram
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
NoNewPrivileges=false

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

log "撤销旧节点认证，生成四个全新的独立设备账号……"
INIT_RESULT="$(/usr/local/sbin/hy2-users init)"
printf '%s\n' "$INIT_RESULT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("旧认证：" + ("已替换并撤销" if d.get("reset") else "已经是多用户模式") + "；新设备：" + (", ".join(d.get("clients", [])) or "无"))'

# Keep the familiar output filenames pointing to the current WIN-PC account.
install -o root -g root -m 600 /var/lib/hy2-telegram/clients/WIN-PC/node.txt /root/hy2-output/node.txt
install -o root -g root -m 600 /var/lib/hy2-telegram/clients/WIN-PC/node-qr.png /root/hy2-output/node-qr.png
install -o hy2bot -g hy2bot -m 640 /var/lib/hy2-telegram/clients/WIN-PC/node.txt /var/lib/hy2-telegram/node.txt
install -o hy2bot -g hy2bot -m 640 /var/lib/hy2-telegram/clients/WIN-PC/node-qr.png /var/lib/hy2-telegram/node-qr.png
install -o root -g root -m 600 /var/lib/hy2-telegram/clients/WIN-PC/client.yaml /root/hy2-output/client.yaml
/usr/local/bin/hysteria -c /root/hy2-output/client.yaml share --notext --qr > /root/hy2-output/share-qr.txt
chmod 600 /root/hy2-output/share-qr.txt
cat > /root/hy2-output/server-info.txt <<'INFO'
Hysteria 2 multi-user deployment
Authentication: userpass (one independent credential per device)
Default users: WIN-PC, MACBOOK, IPHONE, FAMILY
Transport: UDP 443
Client credentials and QR codes: /var/lib/hy2-telegram/clients/
INFO
chmod 600 /root/hy2-output/server-info.txt

log "重启 Telegram 机器人……"
systemctl enable hy2-telegram.service >/dev/null
systemctl restart hy2-telegram.service
sleep 2
systemctl is-active --quiet hy2-telegram.service || {
  journalctl -u hy2-telegram -n 60 --no-pager >&2 || true
  fail "Telegram 机器人没有正常启动。"
}

printf '\n====================================================\n'
printf ' 多设备管理升级完成\n'
printf '====================================================\n'
printf 'HY2 服务：'
systemctl is-active hysteria-server.service || true
printf 'Telegram 机器人：'
systemctl is-active hy2-telegram.service || true
printf '\n机器人日志：journalctl -u hy2-telegram -n 30 --no-pager\n'
printf '\n现在在 Telegram 私聊机器人发送：\n'
printf '/add_defaults\n'
printf '四个独立账号已在升级时生成；发送命令会重新发送四条链接和四张二维码。\n'
printf '\n旧认证已撤销，所有原节点链接失效；新设备凭据已独立生成。\n'
printf '====================================================\n'
