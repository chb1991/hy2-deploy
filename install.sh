#!/usr/bin/env bash
# Hysteria 2 one-click deployment for Debian 13 x86_64.
# Official Hysteria installer + official CLI share/QR command.
# SPDX-License-Identifier: MIT
set -Eeuo pipefail
umask 077

HY2_VERSION='v2.13.0'
INSTALLER_URL='https://get.hy2.sh/'
CF_API='https://api.cloudflare.com/client/v4'
BIN='/usr/local/bin/hysteria'
SERVICE='hysteria-server.service'
CONFIG='/etc/hysteria/config.yaml'
OUT='/root/hy2-output'
INSTALLER_TMP=''

log()  { printf '\n[HY2] %s\n' "$*"; }
info() { printf '[信息] %s\n' "$*"; }
fail() { printf '\n[失败] %s\n' "$*" >&2; exit 1; }
cleanup() { if [[ -n "$INSTALLER_TMP" ]]; then rm -f "$INSTALLER_TMP"; fi; }
on_error() {
  rc=$?
  printf '\n[错误] install.sh 执行失败，退出码：%s\n' "$rc" >&2
  journalctl -u "$SERVICE" -n 40 --no-pager 2>/dev/null || true
  exit "$rc"
}
trap cleanup EXIT
trap on_error ERR

valid_ipv4() {
  printf '%s\n' "$1" | awk -F. 'NF==4 { for (i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || $i+0>255) exit 1; exit 0 } { exit 1 }'
}
valid_domain() {
  [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]
}
get_public_ipv4() {
  local ip
  ip="$(curl -4fsS --connect-timeout 8 --max-time 15 https://api.ipify.org 2>/dev/null || true)"
  if ! valid_ipv4 "$ip"; then
    read -r -p '无法自动获取公网 IPv4，请输入 VPS 公网 IPv4：' ip
  fi
  valid_ipv4 "$ip" || fail "公网 IPv4 格式无效：$ip"
  printf '%s' "$ip"
}
ensure_swap() {
  if swapon --noheadings --show 2>/dev/null | grep -q .; then
    info '系统已有 Swap，跳过。'
    return
  fi
  if [[ -e /swapfile ]]; then
    info '/swapfile 已存在，跳过自动创建。'
    return
  fi
  local free_kb
  free_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
  if [[ "$free_kb" =~ ^[0-9]+$ ]] && (( free_kb >= 750000 )); then
    log '尝试创建 512MB Swap……'
    if fallocate -l 512M /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=512 status=none; then
      chmod 600 /swapfile
      if mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile; then
        grep -qE '^/swapfile[[:space:]]' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
        info '512MB Swap 已启用。'
      else
        rm -f /swapfile
        info '当前文件系统不支持 Swap 文件，跳过。'
      fi
    else
      rm -f /swapfile
      info 'Swap 创建失败，继续部署。'
    fi
  else
    info '根分区剩余空间不足 750MB，跳过 Swap。'
  fi
}

[[ "$EUID" -eq 0 ]] || fail '请使用 root 账号运行。'
[[ -r /etc/os-release ]] || fail '无法读取 /etc/os-release。'
. /etc/os-release
[[ "$ID" == 'debian' && "$VERSION_ID" == '13' ]] || fail "仅支持 Debian 13，当前系统：$PRETTY_NAME"
[[ "$(uname -m)" == 'x86_64' ]] || fail "仅支持 x86_64，当前架构：$(uname -m)"
command -v systemctl >/dev/null 2>&1 || fail '未检测到 systemd。'

log "Hysteria 2 一键部署，官方核心版本 $HY2_VERSION"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl jq openssl iproute2 qrencode

printf '\n请选择 TLS 证书模式：\n'
printf '  1) 自有域名 + Cloudflare DNS-01 + Let'\''s Encrypt 正式证书（推荐）\n'
printf '  2) 无域名 + 公网 IPv4 + 自签名证书 SHA256 指纹固定\n\n'
read -r -p '输入 1 或 2 [默认 1]：' MODE
if [[ -z "$MODE" ]]; then MODE='1'; fi
[[ "$MODE" == '1' || "$MODE" == '2' ]] || fail '只能选择 1 或 2。'

PUBLIC_IP="$(get_public_ipv4)"
info "VPS 公网 IPv4：$PUBLIC_IP"
DOMAIN=''
EMAIL=''
CF_ZONE=''
CF_TOKEN=''
SERVER_ADDR="$PUBLIC_IP"
SNI=''

if [[ "$MODE" == '1' ]]; then
  read -r -p '输入 HY2 域名（例如 hy2.example.com）：' DOMAIN
  DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
  valid_domain "$DOMAIN" || fail 'HY2 域名格式不正确。'

  read -r -p '输入 Cloudflare Zone 主域名（例如 example.com）：' CF_ZONE
  CF_ZONE="$(printf '%s' "$CF_ZONE" | tr '[:upper:]' '[:lower:]')"
  valid_domain "$CF_ZONE" || fail 'Cloudflare Zone 主域名格式不正确。'
  [[ "$DOMAIN" == "$CF_ZONE" || "$DOMAIN" == *."$CF_ZONE" ]] || fail 'HY2 域名必须属于所填写的 Cloudflare Zone。'

  read -r -p '输入证书联系邮箱：' EMAIL
  [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || fail '邮箱格式不正确。'

  printf '\nCloudflare API Token 最小权限：Zone / DNS / Edit 和 Zone / Zone / Read；资源范围仅选择该 Zone。\n'
  read -r -s -p '粘贴 Cloudflare API Token（输入不会显示）：' CF_TOKEN
  printf '\n'
  [[ -n "$CF_TOKEN" && "$CF_TOKEN" =~ ^[A-Za-z0-9_-]+$ ]] || fail 'Cloudflare Token 为空或格式异常。'

  log '验证 Cloudflare API Token……'
  VERIFY_JSON="$(curl -fsS --connect-timeout 10 --max-time 25 -H "Authorization: Bearer $CF_TOKEN" "$CF_API/user/tokens/verify")"
  jq -e '.success == true' >/dev/null <<< "$VERIFY_JSON" || fail 'Cloudflare API Token 验证失败。'

  log "检查 Cloudflare Zone：$CF_ZONE……"
  ZONE_JSON="$(curl -fsS --get --connect-timeout 10 --max-time 25 -H "Authorization: Bearer $CF_TOKEN" --data-urlencode "name=$CF_ZONE" --data-urlencode 'per_page=100' "$CF_API/zones")"
  ZONE_ID="$(jq -r --arg zone "$CF_ZONE" '[.result[]? | select(.name == $zone)][0].id // empty' <<< "$ZONE_JSON")"
  [[ -n "$ZONE_ID" ]] || fail '找不到 Cloudflare Zone，或 Token 缺少 Zone:Read 权限。'

  log "检查 DNS A 记录：$DOMAIN……"
  DNS_JSON="$(curl -fsS --get --connect-timeout 10 --max-time 25 -H "Authorization: Bearer $CF_TOKEN" --data-urlencode 'type=A' --data-urlencode "name=$DOMAIN" --data-urlencode 'per_page=100' "$CF_API/zones/$ZONE_ID/dns_records")"
  if ! jq -e --arg ip "$PUBLIC_IP" '(.result | length) > 0 and all(.result[]; .content == $ip and .proxied == false)' >/dev/null <<< "$DNS_JSON"; then
    jq -r '.result[]? | "  \(.type) \(.name) -> \(.content), proxied=\(.proxied)"' <<< "$DNS_JSON" || true
    fail "请先将 $DOMAIN 的 A 记录指向 $PUBLIC_IP，并设置为 DNS only（灰云）。"
  fi
  SERVER_ADDR="$DOMAIN"
  SNI="$DOMAIN"
  info 'Cloudflare Token、Zone、A 记录和 DNS only 检查通过。'
fi

printf '\n部署摘要：\n系统：Debian 13 x86_64\nHysteria：官方 %s\n监听：UDP 443\n地址：%s\n' "$HY2_VERSION" "$SERVER_ADDR"
if [[ "$MODE" == '1' ]]; then
  printf 'TLS：Cloudflare DNS-01 / Let'\''s Encrypt\n'
else
  printf 'TLS：自签名证书 + SHA256 pin\n'
fi
printf '\n请确认 VPS 服务商防火墙允许入站 UDP 443。\n'
read -r -p '确认开始部署？[y/N]：' CONFIRM
CONFIRM="$(printf '%s' "$CONFIRM" | tr '[:upper:]' '[:lower:]')"
[[ "$CONFIRM" == 'y' || "$CONFIRM" == 'yes' ]] || fail '用户取消部署。'

log '检查 UDP 443 端口……'
if systemctl cat "$SERVICE" >/dev/null 2>&1; then
  systemctl stop "$SERVICE" || true
  sleep 1
fi
if ss -H -lunp 'sport = :443' 2>/dev/null | grep -q .; then
  ss -lunp 'sport = :443' >&2 || true
  fail 'UDP 443 已被其他进程占用，脚本不会终止未知进程。'
fi

if [[ -f "$CONFIG" ]]; then
  BACKUP_DIR="/root/hy2-backup-$(date +%Y%m%d-%H%M%S)"
  install -d -m 700 "$BACKUP_DIR"
  cp -a "$CONFIG" "$BACKUP_DIR/config.yaml"
  info "原配置已备份到 $BACKUP_DIR/config.yaml"
fi

ensure_swap

log "通过 Hysteria 官方安装器安装 $HY2_VERSION……"
INSTALLER_TMP="$(mktemp /tmp/hy2-installer.XXXXXX.sh)"
curl -fsSL --retry 3 --connect-timeout 10 "$INSTALLER_URL" -o "$INSTALLER_TMP"
HYSTERIA_USER=hysteria bash "$INSTALLER_TMP" --force --version "$HY2_VERSION"
rm -f "$INSTALLER_TMP"
INSTALLER_TMP=''
[[ -x "$BIN" ]] || fail "官方安装器后未找到 $BIN。"
systemctl cat "$SERVICE" >/dev/null 2>&1 || fail "官方安装器未创建 $SERVICE。"
"$BIN" version

SERVICE_USER="$(systemctl show -p User --value "$SERVICE")"
SERVICE_GROUP="$(systemctl show -p Group --value "$SERVICE")"
if [[ -z "$SERVICE_USER" ]]; then SERVICE_USER='hysteria'; fi
if [[ -z "$SERVICE_GROUP" ]]; then SERVICE_GROUP="$SERVICE_USER"; fi
getent passwd "$SERVICE_USER" >/dev/null 2>&1 || fail "服务用户不存在：$SERVICE_USER"
getent group "$SERVICE_GROUP" >/dev/null 2>&1 || fail "服务组不存在：$SERVICE_GROUP"

log '生成服务端配置……'
install -d -o root -g "$SERVICE_GROUP" -m 0750 /etc/hysteria
PASSWORD="$(openssl rand -hex 24)"
PIN_SHA256=''

if [[ "$MODE" == '1' ]]; then
  cat > "$CONFIG" <<YAML
listen: :443
acme:
  domains:
    - "$DOMAIN"
  email: "$EMAIL"
  ca: letsencrypt
  type: dns
  dns:
    name: cloudflare
    config:
      cloudflare_api_token: "$CF_TOKEN"
auth:
  type: password
  password: "$PASSWORD"
masquerade:
  type: string
  string:
    content: "Welcome"
    headers:
      content-type: text/plain; charset=utf-8
    statusCode: 200
YAML
else
  openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
    -keyout /etc/hysteria/server.key \
    -out /etc/hysteria/server.crt \
    -subj "/CN=$PUBLIC_IP" \
    -addext "subjectAltName=IP:$PUBLIC_IP" >/dev/null 2>&1
  PIN_SHA256="$(openssl x509 -noout -fingerprint -sha256 -in /etc/hysteria/server.crt | sed 's/^[^=]*=//')"
  chown "root:$SERVICE_GROUP" /etc/hysteria/server.crt /etc/hysteria/server.key
  chmod 644 /etc/hysteria/server.crt
  chmod 640 /etc/hysteria/server.key
  cat > "$CONFIG" <<YAML
listen: :443
tls:
  cert: /etc/hysteria/server.crt
  key: /etc/hysteria/server.key
auth:
  type: password
  password: "$PASSWORD"
masquerade:
  type: string
  string:
    content: "Welcome"
    headers:
      content-type: text/plain; charset=utf-8
    statusCode: 200
YAML
fi
chown "root:$SERVICE_GROUP" "$CONFIG"
chmod 640 "$CONFIG"

log '配置本机防火墙（不会启用新防火墙）……'
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
  ufw allow 443/udp >/dev/null
  info 'UFW 已放行 UDP 443。'
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=443/udp >/dev/null
  firewall-cmd --reload >/dev/null
  info 'firewalld 已放行 UDP 443。'
else
  info '未检测到活动的 UFW/firewalld，请检查 VPS 服务商安全组。'
fi

log '启动 Hysteria 服务……'
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 3
if ! systemctl is-active --quiet "$SERVICE"; then
  journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
  fail 'Hysteria 服务未能保持 active。'
fi
ss -H -lunp 'sport = :443' 2>/dev/null | grep -q . || fail '服务启动但未检测到 UDP 443 监听。'

log '生成客户端配置、官方分享链接和二维码……'
install -d -m 700 "$OUT"
CLIENT_CONFIG="$OUT/client.yaml"
if [[ "$MODE" == '1' ]]; then
  cat > "$CLIENT_CONFIG" <<YAML
server: "$SERVER_ADDR:443"
auth: "$PASSWORD"
tls:
  sni: "$SNI"
YAML
else
  cat > "$CLIENT_CONFIG" <<YAML
server: "$SERVER_ADDR:443"
auth: "$PASSWORD"
tls:
  insecure: true
  pinSHA256: "$PIN_SHA256"
YAML
fi
chmod 600 "$CLIENT_CONFIG"

URI="$("$BIN" -c "$CLIENT_CONFIG" share)"
[[ "$URI" == hysteria2://* ]] || fail '官方 hysteria share 未生成有效 hysteria2:// 链接。'
printf '%s\n' "$URI" > "$OUT/node.txt"
chmod 600 "$OUT/node.txt"
"$BIN" -c "$CLIENT_CONFIG" share --notext --qr | tee "$OUT/share-qr.txt"
qrencode -o "$OUT/node-qr.png" -s 8 -m 2 "$URI"
chmod 600 "$OUT/node-qr.png"

cat > "$OUT/server-info.txt" <<INFO
Hysteria 2 deployment information
Version: $HY2_VERSION
Address: $SERVER_ADDR
Port: 443/UDP
Auth: $PASSWORD
INFO
if [[ "$MODE" == '1' ]]; then
  printf 'TLS: Let'\''s Encrypt via Cloudflare DNS-01\nSNI: %s\n' "$DOMAIN" >> "$OUT/server-info.txt"
else
  printf 'TLS: self-signed certificate with SHA256 pin\nSHA256: %s\n' "$PIN_SHA256" >> "$OUT/server-info.txt"
fi
chmod 600 "$OUT/server-info.txt"

log '部署完成（本机服务及 UDP 443 监听检查通过）'
printf '\n================ HY2 节点信息 ================\n'
printf '服务器：%s\n端口：443/UDP\n密码：%s\n' "$SERVER_ADDR" "$PASSWORD"
if [[ "$MODE" == '1' ]]; then
  printf 'TLS：Let'\''s Encrypt 正式证书\nSNI：%s\n' "$DOMAIN"
else
  printf 'TLS：自签名证书（分享链接含 SHA256 pin）\nSHA256：%s\n' "$PIN_SHA256"
fi
printf '\n【节点分享链接】\n%s\n' "$URI"
printf '\n【二维码 PNG】%s/node-qr.png\n' "$OUT"
printf '\n【终端二维码】\n'
cat "$OUT/share-qr.txt"
printf '\n【文件】\n节点链接：%s/node.txt\n二维码：%s/node-qr.png\n客户端配置：%s\n服务端配置：%s\n' "$OUT" "$OUT" "$CLIENT_CONFIG" "$CONFIG"
printf '\n【服务状态】\n'
systemctl --no-pager --full status "$SERVICE" | sed -n '1,12p'
printf '\n【UDP 443 监听】\n'
ss -lunp 'sport = :443' || true
printf '\n必须在 VPS 服务商防火墙/安全组放行入站 UDP 443。Cloudflare 模式必须使用 DNS only（灰云）。\n'
printf '服务 active 和本机监听不代表公网 UDP 一定可达，请用手机蜂窝网络实测。\n'
printf '节点文件和 /etc/hysteria/config.yaml 含密码或密钥，严禁上传 GitHub。\n'
printf '===============================================\n'
