#!/usr/bin/env bash
# Adds independent Vision TCP service; never restarts HY2, XHTTP, or TUIC.
set -Eeuo pipefail
umask 077

XRAY=/usr/local/bin/xray
XRAY_CONF=/usr/local/etc/xray/config.json
VISION_CONF=/usr/local/etc/xray/vision.json
VISION_UNIT=/etc/systemd/system/xray-vision.service
VISION_ROOT=/var/lib/hy2-telegram/vision
BOT=/opt/hy2-telegram/bot.py
DUAL=/opt/hy2-telegram/dual_bot.py
RAW=https://raw.githubusercontent.com/chb1991/hy2-deploy/main
TMP="$(mktemp -d /tmp/vision-install.XXXXXXXX)"
DONE=0
SERVICE_CREATED=0
CONF_CREATED=0
CREDS_CREATED=0
BOT_UPDATED=0
DUAL_UPDATED=0

fail() { printf '\n[Vision 错误] %s\n' "$*" >&2; exit 1; }
log() { printf '\n[Vision] %s\n' "$*"; }
cleanup() {
  code=$?
  trap - EXIT
  if (( code != 0 && DONE == 0 )); then
    printf '\n[Vision] 仅回滚新 Vision；原 HY2、XHTTP、TUIC 不修改。\n' >&2
    if (( BOT_UPDATED || DUAL_UPDATED )); then
      if (( BOT_UPDATED )); then install -o root -g root -m 755 "$TMP/bot.backup" "$BOT" || true; fi
      if (( DUAL_UPDATED )); then install -o root -g root -m 755 "$TMP/dual.backup" "$DUAL" || true; fi
      systemctl restart hy2-telegram.service || true
    fi
    if (( SERVICE_CREATED )); then
      systemctl disable --now xray-vision.service >/dev/null 2>&1 || true
      rm -f "$VISION_UNIT"
      systemctl daemon-reload || true
    fi
    if (( CONF_CREATED )); then rm -f "$VISION_CONF"; fi
    if (( CREDS_CREATED )); then rm -rf "$VISION_ROOT"; fi
  fi
  rm -rf "$TMP"
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$(id -u)" -eq 0 ]] || fail "请使用 root SSH 执行。"
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 && "$(uname -m)" == x86_64 ]] || fail "仅支持 Debian 13 x86_64。"
[[ -x "$XRAY" && -f "$XRAY_CONF" ]] || fail "未找到现有 Xray + XHTTP 安装。"
"$XRAY" run -test -config "$XRAY_CONF" >/dev/null 2>&1 || fail "原有 XHTTP 配置不通过官方校验；拒绝启动新服务。"
[[ -f /etc/hysteria/config.yaml && -f /etc/hy2-telegram/bot.env && -f "$BOT" ]] || fail "现有 HY2/Telegram 不完整。"
systemctl is-active --quiet xray.service || fail "原 XHTTP 服务未运行。"
systemctl is-active --quiet hysteria-server.service || fail "原 HY2 服务未运行。"
systemctl is-active --quiet hy2-telegram.service || fail "Telegram 机器人未运行。"
id hy2bot >/dev/null 2>&1 || fail "找不到 hy2bot 用户。"
getent group xray >/dev/null || fail "Xray 服务组不存在。"
[[ ! -e "$VISION_CONF" && ! -e "$VISION_UNIT" && ! -e "$VISION_ROOT" ]] || fail "Vision 已存在或安装中断，拒绝覆盖。"
if systemctl cat xray-vision.service >/dev/null 2>&1; then fail "已有 xray-vision 服务。"; fi
for command in python3 openssl curl qrencode ss runuser timeout; do
  command -v "$command" >/dev/null 2>&1 || fail "缺少 $command 命令。"
done
log "复用当前官方 Xray 核心："
"$XRAY" version | head -n 1

DEFAULT_HOST="$(python3 - /var/lib/hy2-telegram/xray/clients/IPHONE/node.txt <<'PY'
import pathlib,sys,urllib.parse
try: print(urllib.parse.urlsplit(pathlib.Path(sys.argv[1]).read_text().strip()).hostname or "")
except (OSError,ValueError): print("")
PY
)"
DEFAULT_SNI="$(python3 - "$XRAY_CONF" <<'PY'
import json,sys
try:
    with open(sys.argv[1],encoding="utf-8") as f: c=json.load(f)
    print(c["inbounds"][0]["streamSettings"]["realitySettings"]["serverNames"][0])
except (OSError,ValueError,KeyError,IndexError,TypeError): print("")
PY
)"

printf '\n新增 VLESS + REALITY + Vision；原 TCP 443 与 UDP 443 不变。\n'
if [[ -n "$DEFAULT_HOST" ]]; then
  read -r -p "连接域名/IP [$DEFAULT_HOST]： " HOST
  if [[ -z "$HOST" ]]; then HOST="$DEFAULT_HOST"; fi
else
  read -r -p "连接域名/IP： " HOST
fi
if [[ -n "$DEFAULT_SNI" ]]; then
  read -r -p "REALITY 目标 SNI [$DEFAULT_SNI]： " SNI
  if [[ -z "$SNI" ]]; then SNI="$DEFAULT_SNI"; fi
else
  read -r -p "REALITY 目标 SNI（支持 TLS1.3/H2）： " SNI
fi
read -r -p "Vision TCP 端口 [8443]： " PORT
if [[ -z "$PORT" ]]; then PORT=8443; fi

python3 - "$HOST" "$SNI" "$PORT" <<'PY'
import ipaddress,sys
host,sni,port=sys.argv[1:]
def good(v):
    try: ipaddress.ip_address(v.strip("[]")); return True
    except ValueError:
        parts=v.split(".")
        return len(parts)>=2 and len(v)<=253 and all(
            p and len(p)<=63 and p[0].isalnum() and p[-1].isalnum()
            and all(c.isascii() and (c.isalnum() or c=="-") for c in p)
            for p in parts)
if not good(host) or not good(sni): sys.exit("连接域名或 SNI 不合法")
if host.strip("[]").lower()==sni.lower(): sys.exit("SNI 不能与 VPS 连接地址相同")
if any(p in ("apple","icloud","microsoft") for p in sni.lower().split(".")):
    sys.exit("该 SNI 可能触发 Xray 封锁风险警告")
if not port.isdigit() or not 1024<=int(port)<=65535: sys.exit("无效 TCP 端口")
PY

if ss -H -ltn "sport = :$PORT" | grep -q .; then
  ss -ltnp "sport = :$PORT" || true
  fail "TCP $PORT 已被占用。"
fi
log "验证 SNI 的 TLS1.3、H2、证书链和域名……"
if ! result="$(timeout 15 openssl s_client -connect "$SNI:443" -servername "$SNI" -tls1_3 -alpn h2 -verify_hostname "$SNI" -verify_return_error </dev/null 2>&1)"; then
  printf '%s\n' "$result" | tail -10 >&2
  fail "目标 TLS 握手或证书验证失败。"
fi
[[ "$result" == *TLSv1.3* && "$result" == *'ALPN protocol: h2'* && "$result" == *'Verify return code: 0 (ok)'* ]] || fail "目标 TLS1.3/H2/证书验证不通过。"

log "下载配置生成器与机器人扩展……"
curl -fsSL --retry 3 "$RAW/xray-vision/configure.py" -o "$TMP/configure.py"
curl -fsSL --retry 3 "$RAW/telegram-bot/bot.py" -o "$TMP/bot.py"
python3 -m py_compile "$TMP/configure.py" "$TMP/bot.py"
grep -Fq 'if command=="/vget"' "$TMP/bot.py" || fail "机器人版本不支持 /vget。"
if systemctl cat hy2-telegram.service | grep -Fq 'ExecStart=/usr/bin/python3 /opt/hy2-telegram/dual_bot.py'; then
  [[ -f "$DUAL" ]] || fail "TUIC 双协议机器人入口缺失。"
  curl -fsSL --retry 3 "$RAW/telegram-bot/dual_bot.py" -o "$TMP/dual_bot.py"
  python3 -m py_compile "$TMP/dual_bot.py"
fi
printf '\n即将新增 Vision TCP %s、四个独立 UUID，并升级原 Telegram 机器人。\n' "$PORT"
read -r -p "确认安装？[y/N]： " ANSWER
[[ "$ANSWER" == y || "$ANSWER" == Y ]] || fail "已取消。"

log "生成独立 REALITY 密钥及四个 UUID……"
keys="$("$XRAY" x25519)"
VISION_PRIVATE="$(printf '%s\n' "$keys" | sed -nE 's/^PrivateKey:[[:space:]]*//p' | head -n 1)"
VISION_PUBLIC="$(printf '%s\n' "$keys" | sed -nE 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' | head -n 1)"
[[ "$VISION_PRIVATE" =~ ^[A-Za-z0-9_-]{43,44}$ && "$VISION_PUBLIC" =~ ^[A-Za-z0-9_-]{43,44}$ ]] || fail "REALITY 公私钥格式异常。"
VISION_SID="$(openssl rand -hex 8)"
VISION_UUIDS=''
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  one="$("$XRAY" uuid | tr -d '\r\n')"
  [[ "$one" =~ ^[0-9a-fA-F-]{36}$ ]] || fail "UUID 生成失败。"
  if [[ -z "$VISION_UUIDS" ]]; then VISION_UUIDS="$one"; else VISION_UUIDS="$VISION_UUIDS,$one"; fi
done
export VISION_PRIVATE VISION_PUBLIC VISION_SID VISION_UUIDS
STAGED="$TMP/staged"
python3 "$TMP/configure.py" --host "$HOST" --sni "$SNI" --port "$PORT" --out "$STAGED"
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  qrencode -o "$STAGED/clients/$name/node-qr.png" -s 7 -m 2 "$(cat "$STAGED/clients/$name/node.txt")"
done

log "通过官方 Xray 核心检查配置……"
if ! "$XRAY" run -test -config "$STAGED/config.json" >"$TMP/test.log" 2>&1; then
  cat "$TMP/test.log" >&2
  fail "Vision 配置不兼容当前 Xray 版本。"
fi
if grep -qi 'will increase the likelihood of your server' "$TMP/test.log"; then
  cat "$TMP/test.log" >&2
  fail "官方 Xray 提示 REALITY 目标封锁风险。"
fi
grep -Fq 'Configuration OK.' "$TMP/test.log" || fail "配置未通过官方测试。"

log "安装独立配置与节点凭据……"
install -o root -g xray -m 640 "$STAGED/config.json" "$VISION_CONF"
CONF_CREATED=1
install -d -o root -g hy2bot -m 750 "$VISION_ROOT"
CREDS_CREATED=1
install -d -o root -g hy2bot -m 750 "$VISION_ROOT/clients"
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  install -d -o root -g hy2bot -m 750 "$VISION_ROOT/clients/$name"
  for filename in node.txt loon.txt node-qr.png; do
    install -o root -g hy2bot -m 640 "$STAGED/clients/$name/$filename" "$VISION_ROOT/clients/$name/$filename"
    runuser -u hy2bot -- test -r "$VISION_ROOT/clients/$name/$filename" || fail "机器人无法读取 $name/$filename"
  done
done
cat > "$VISION_UNIT" <<'UNIT'
[Unit]
Description=Xray VLESS REALITY Vision isolated TCP service
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=xray
Group=xray
ExecStartPre=/usr/local/bin/xray run -test -config /usr/local/etc/xray/vision.json
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/vision.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$VISION_UNIT"
SERVICE_CREATED=1
systemctl daemon-reload
systemctl enable xray-vision.service >/dev/null
if ! systemctl start xray-vision.service; then
  journalctl -u xray-vision.service -n 30 --no-pager >&2 || true
  fail "Vision 服务启动失败。"
fi
sleep 2
systemctl is-active --quiet xray-vision.service || fail "Vision 未保持 active。"
ss -H -ltnp "sport = :$PORT" | grep -Fq '"xray"' || fail "Vision TCP 端口未由 Xray 监听。"
systemctl is-active --quiet xray.service || fail "原 XHTTP 服务异常，请检查。"
systemctl is-active --quiet hysteria-server.service || fail "原 HY2 服务异常，请检查。"

log "备份并更新 Telegram 机器人……"
BACKUP_DIR="/root/vision-backups/$(date +%Y%m%d-%H%M%S)-$"
install -d -o root -g root -m 700 "$BACKUP_DIR"
cp -a "$BOT" "$TMP/bot.backup"
cp -a "$BOT" "$BACKUP_DIR/bot.py"
if [[ -f "$TMP/dual_bot.py" ]]; then
  cp -a "$DUAL" "$TMP/dual.backup"
  cp -a "$DUAL" "$BACKUP_DIR/dual_bot.py"
  DUAL_UPDATED=1
  install -o root -g root -m 755 "$TMP/dual_bot.py" "$DUAL"
fi
BOT_UPDATED=1
install -o root -g root -m 755 "$TMP/bot.py" "$BOT"
systemctl restart hy2-telegram.service || fail "Telegram 重启失败。"
sleep 2
systemctl is-active --quiet hy2-telegram.service || fail "Telegram 未保持 active。"

DONE=1
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
  ufw allow "$PORT/tcp" || printf '请手动检查 UFW TCP %s 放行情况。\n' "$PORT"
fi
printf '\n========== Vision 部署完成 ==========\n'
printf 'HY2 UDP 443：%s\n' "$(systemctl is-active hysteria-server.service)"
printf 'XHTTP TCP 443：%s\n' "$(systemctl is-active xray.service)"
printf 'Vision TCP %s：%s\n' "$PORT" "$(systemctl is-active xray-vision.service)"
printf 'Telegram：%s\n' "$(systemctl is-active hy2-telegram.service)"
printf '机器人命令：/vstatus、/vall、/vget IPHONE\n'
printf '机器人备份：%s\n' "$BACKUP_DIR"
printf '私有节点目录：%s/clients/\n' "$VISION_ROOT"
printf '请在服务商防火墙放行 TCP %s；Cloudflare 保持灰云。\n' "$PORT"
printf '====================================\n'
