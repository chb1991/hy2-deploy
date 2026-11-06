#!/usr/bin/env bash
# CHBVPS Telegram Bot V2-only migration. Never restart proxy core services.
set -Eeuo pipefail
umask 077

RAW=https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot
BOT_DIR=/opt/hy2-telegram
SERVICE=hy2-telegram.service
SUDOERS=/etc/sudoers.d/hy2-telegram-v2
TMP="$(mktemp -d /tmp/chbvps-v2.XXXXXXXX)"
BACKUP="/root/chbvps-bot-backups/v2-$(date +%Y%m%d-%H%M%S)-$$"
APPLIED=0
DONE=0

fail() { printf '\n[V2 错误] %s\n' "$*" >&2; exit 1; }
log() { printf '\n[V2] %s\n' "$*"; }
cleanup() {
  rc=$?
  trap - EXIT
  if (( rc != 0 && APPLIED == 1 && DONE == 0 )); then
    log "升级失败，恢复旧机器人文件和 sudo 权限……"
    for file in bot.py dual_bot.py v2_ui.py; do
      if [[ -f "$BACKUP/$file" ]]; then
        install -o root -g root -m 755 "$BACKUP/$file" "$BOT_DIR/$file" || true
      else
        rm -f "$BOT_DIR/$file"
      fi
    done
    if [[ -f "$BACKUP/hy2-telegram-v2.sudoers" ]]; then
      install -o root -g root -m 0440 "$BACKUP/hy2-telegram-v2.sudoers" "$SUDOERS" || true
    else
      rm -f "$SUDOERS"
    fi
    systemctl restart "$SERVICE" || true
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT
trap 'printf "[V2] 脚本在第 %s 行退出（状态码 %s）\n" "$LINENO" "$?" >&2' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $EUID -eq 0 ]] || fail "请使用 root SSH。"
. /etc/os-release
[[ "$ID" == "debian" && "$VERSION_ID" == "13" ]] || fail "当前安装器仅面向 Debian 13。"
[[ -f "$BOT_DIR/bot.py" && -f /etc/hy2-telegram/bot.env ]] || fail "未发现原有 Telegram HY2 机器人。"
systemctl is-active --quiet "$SERVICE" || fail "现有 Telegram 机器人未运行，拒绝执行升级。"
[[ -x /usr/local/sbin/core-update ]] || fail "缺少已有 core-update，先恢复原机器人环境。"
[[ -x /usr/local/sbin/hy2-restart ]] || fail "原有 HY2 重启帮助程序缺失。"
for program in curl python3 visudo sudo systemctl; do
  command -v "$program" >/dev/null 2>&1 || fail "缺少 $program。"
done
if [[ -f "$BOT_DIR/dual_bot.py" ]]; then
  log "检测到原 HY2/TUIC 机器人适配器，将同步更新。"
fi

log "下载 V2 UI 与原协议机器人适配器……"
for filename in bot.py dual_bot.py v2_ui.py; do
  curl -fsSL --retry 3 "$RAW/$filename" -o "$TMP/$filename"
done
python3 -m py_compile "$TMP/bot.py" "$TMP/dual_bot.py" "$TMP/v2_ui.py"
PYTHONPATH="$TMP" BOT_TOKEN=CI-LOCAL-NEVER-SEND ADMIN_USER_ID=123456 \
  HY2_STATS_SECRET=CI-LOCAL python3 - <<'PY'
import bot
import v2_ui
assert len(bot.COMMANDS) == 9
assert len({x["command"] for x in bot.COMMANDS}) == 9
assert {x["command"] for x in bot.COMMANDS} == {
    "start","status","devices","get","add","del","restart","update","help"
}
PY

cat >"$TMP/sudoers" <<'SUDO'
# CHBVPS Telegram V2: fixed, least-privilege actions only.
hy2bot ALL=(root) NOPASSWD: /usr/bin/systemctl restart xray.service
hy2bot ALL=(root) NOPASSWD: /usr/bin/systemctl restart xray-vision.service
hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/core-update --hy2 --yes --json
hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/core-update --singbox --yes --json
SUDO
chmod 440 "$TMP/sudoers"
visudo -cf "$TMP/sudoers" >/dev/null || fail "sudoers 安全规则校验失败。"

log "验证服务入口与后续兼容性……"
if systemctl cat "$SERVICE" | grep -Fq 'ExecStart=/usr/bin/python3 /opt/hy2-telegram/dual_bot.py'; then
  [[ -f "$BOT_DIR/dual_bot.py" ]] || fail "TUIC 服务入口存在但双协议适配器缺失。"
elif systemctl cat "$SERVICE" | grep -Fq 'ExecStart=/usr/bin/python3 /opt/hy2-telegram/bot.py'; then
  log "现有服务入口为单协议 bot.py。"
else
  fail "机器人入口与已知部署版本不一致，拒绝覆盖。"
fi

log "将保留全部原始账号、证书、Bot Token 和服务端配置。"
printf '仅更新机器人文件、专用 sudoers 权限并重启 Telegram 服务；HY2/TUIC/Vision/XHTTP 不会重启。\n'
read -r -p '确认升级 V2.0？[y/N]： ' answer
[[ "$answer" == y || "$answer" == Y ]] || fail "已取消。"

install -d -o root -g root -m 700 "$BACKUP"
for file in bot.py dual_bot.py v2_ui.py; do
  if [[ -f "$BOT_DIR/$file" ]]; then cp -a "$BOT_DIR/$file" "$BACKUP/$file"; fi
done
if [[ -f "$SUDOERS" ]]; then cp -a "$SUDOERS" "$BACKUP/hy2-telegram-v2.sudoers"; fi

# Arm rollback before modifying even the first file.
APPLIED=1
install -o root -g root -m 755 "$TMP/v2_ui.py" "$BOT_DIR/v2_ui.py"
install -o root -g root -m 755 "$TMP/bot.py" "$BOT_DIR/bot.py"
if [[ -f "$BOT_DIR/dual_bot.py" ]]; then
  install -o root -g root -m 755 "$TMP/dual_bot.py" "$BOT_DIR/dual_bot.py"
fi
install -o root -g root -m 0440 "$TMP/sudoers" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null
systemctl restart "$SERVICE" || fail "Telegram 重启失败。"
sleep 2
systemctl is-active --quiet "$SERVICE" || fail "Telegram 未保持 active。"

DONE=1
printf '\n===== CHBVPS BOT V2.0 升级成功 =====\n'
printf 'Telegram 服务：%s\n' "$(systemctl is-active "$SERVICE")"
printf '9 个可见命令：/start /status /devices /get /add /del /restart /update /help\n'
printf '旧命令兼容保留。确认按钮有效期为 120 秒，且不可重复执行。\n'
printf '机器人备份目录：%s\n' "$BACKUP"
printf '代理服务配置与节点凭据均未修改；所有代理保持原状态。\n'
