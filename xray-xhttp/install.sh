#!/usr/bin/env bash
# Official Xray-core VLESS + XHTTP + REALITY alongside HY2 UDP 443.
set -Eeuo pipefail
umask 077
VERSION=v26.10.31
XRAY=/usr/local/bin/xray
CONF=/usr/local/etc/xray/config.json
BOT=/opt/hy2-telegram/bot.py
DUAL=/opt/hy2-telegram/dual_bot.py
CLIENTS=/var/lib/hy2-telegram/xray/clients
RAW=https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/bot.py
RAW_DUAL=https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/dual_bot.py
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail() { printf '[错误] %s\n' "$*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || fail '请使用 root。'
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 && $(uname -m) == x86_64 ]] || fail '仅支持 Debian 13 x86_64。'
[[ -f /etc/hysteria/config.yaml && -f /etc/hy2-telegram/bot.env && -f "$BOT" ]] || fail '需要现有 HY2 + Telegram 机器人。'
systemctl is-active --quiet hysteria-server.service || fail 'HY2 未运行。'
systemctl is-active --quiet hy2-telegram.service || fail 'Telegram 未运行。'
# A root-only marker permits recovery of this script's own interrupted installation.
# An unrelated Xray installation is never modified.
PENDING=/var/lib/hy2-telegram/.xhttp-install-pending
RESUME=0
if [[ -e "$XRAY" || -e "$CONF" ]] || systemctl cat xray.service >/dev/null 2>&1; then
  [[ -f "$PENDING" && -x "$XRAY" && -f "$CONF" ]] || fail '检测到已有 Xray；不是本脚本的中断安装，拒绝覆盖。'
  RESUME=1
  printf '检测到前次 XHTTP 安装中断，将检查并继续完成。\n'
fi
if ss -H -ltn 'sport = :443' | grep -q .; then
  if (( RESUME )) && ss -H -ltnp 'sport = :443' | grep -q '"xray"'; then
    printf 'TCP 443 由前次安装的 Xray 监听，可继续恢复。\n'
  else
    ss -ltnp 'sport = :443' || true
    fail 'TCP 443 已被其他程序占用，脚本不会抢占端口。'
  fi
fi

printf '准备安装 Xray VLESS + XHTTP + REALITY（TCP 443）。HY2 UDP 443 不变。\n'
read -r -p '节点连接地址（灰云域名或 VPS IP）： ' ADDRESS
read -r -p 'REALITY 目标域名（支持 TLS1.3/H2；避免 Apple/Microsoft/iCloud 等高风险目标）： ' SNI
python3 - "$ADDRESS" "$SNI" <<'PY'
import ipaddress,sys
address,sni=sys.argv[1:]
def domain(v):
    return 3<=len(v)<=253 and all(
        0<len(part)<=63 and part[0].isalnum() and part[-1].isalnum() and
        all(c.isascii() and (c.isalnum() or c=='-') for c in part)
        for part in v.split('.'))
try: ipaddress.ip_address(address)
except ValueError:
    if not domain(address): sys.exit('连接地址格式不正确')
if not domain(sni): sys.exit('REALITY 目标域名格式不正确')
if address.lower()==sni.lower():
    sys.exit('REALITY 目标不得与当前 VPS 的连接地址相同，请选用独立的第三方 TLS 目标。')
parts=sni.lower().split('.')
if any(x in ('apple','icloud','microsoft') for x in parts) or parts[-1] in ('cn','ru','ir'):
    sys.exit('该 REALITY 目标可能触发 Xray 的封锁风险警告，请更换目标网站。')
PY
printf '验证目标 TLS1.3、H2、证书链和域名匹配情况……\n'
if ! result=$(timeout 15 openssl s_client -connect "$SNI:443" -servername "$SNI" \
    -tls1_3 -alpn h2 -verify_hostname "$SNI" -verify_return_error </dev/null 2>&1); then
  printf '%s\n' "$result" | tail -10 >&2
  fail '目标 TLS 握手或证书验证失败，请换一个 REALITY 目标。'
fi
[[ "$result" == *TLSv1.3* && "$result" == *'ALPN protocol: h2'* && "$result" == *'Verify return code: 0 (ok)'* ]] \
  || fail '目标未通过 TLS1.3 / HTTP2 / 证书验证，请更换 REALITY 目标。'
read -r -p '确认安装官方 Xray v26.10.31 并接入已有 Telegram？[y/N]： ' CONFIRM
[[ "$CONFIRM" == y || "$CONFIRM" == Y ]] || fail '已取消。'

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends curl openssl python3 qrencode ca-certificates
# Validate both downloaded programs before modifying any Xray service.
curl -fsSL --retry 3 "$RAW" -o "$TMP/bot.py"
python3 -m py_compile "$TMP/bot.py"
grep -Fq 'if command=="/xget"' "$TMP/bot.py" || fail '机器人版本缺少 /xget，已停止安装。'
# Preserve optional TUIC dual-protocol command handling; update its adapter only
# if the existing bot unit actually launches dual_bot.py.
if systemctl cat hy2-telegram.service | grep -Fq 'ExecStart=/usr/bin/python3 /opt/hy2-telegram/dual_bot.py'; then
  [[ -f "$DUAL" ]] || fail '现有机器人以双协议模式运行，但 dual_bot.py 不存在。'
  curl -fsSL --retry 3 "$RAW_DUAL" -o "$TMP/dual_bot.py"
  python3 -m py_compile "$TMP/dual_bot.py"
fi
curl -fsSL --retry 3 https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh -o "$TMP/install-release.sh"
bash -n "$TMP/install-release.sh"
# Keep this marker on errors so an interrupted installation can be retried.
install -o root -g root -m 600 /dev/null "$PENDING"
bash "$TMP/install-release.sh" install --version "$VERSION" --without-geodata --without-logfiles
[[ -x "$XRAY" ]] || fail '官方 Xray 安装失败。'
getent group xray >/dev/null || fail '官方 xray 用户组缺失。'
keys=$($XRAY x25519)
PRIVATE=$(printf '%s\n' "$keys" | sed -nE 's/^PrivateKey:[[:space:]]*//p' | head -1)
# Official 26.10.31 outputs "Password (PublicKey):", older versions use
# "Password:" or "PublicKey:". All three spellings are supported.
PUBLIC=$(printf '%s\n' "$keys" | sed -nE 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' | head -1)
[[ "$PRIVATE" =~ ^[A-Za-z0-9_-]{43,44}$ && "$PUBLIC" =~ ^[A-Za-z0-9_-]{43,44}$ ]] || fail 'REALITY 密钥输出异常，停止安装。'
SID=$(openssl rand -hex 8)
XPATH=/$(openssl rand -hex 10)
UUIDS=''
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  one=$($XRAY uuid | tr -d '\r\n')
  [[ "$one" =~ ^[0-9a-fA-F-]{36}$ ]] || fail '生成 UUID 失败。'
  if [[ -z "$UUIDS" ]]; then UUIDS=$one; else UUIDS=$UUIDS,$one; fi
done
export ADDRESS SNI PRIVATE PUBLIC SID XPATH UUIDS CONF CLIENTS
python3 <<'PY'
import json,os,pathlib,urllib.parse
names=['WIN-PC','MACBOOK','IPHONE','FAMILY']
ids=os.environ['UUIDS'].split(',')
sni=os.environ['SNI']; address=os.environ['ADDRESS']
path=os.environ['XPATH']; sid=os.environ['SID']
config={
 'log':{'loglevel':'warning'},
 'inbounds':[{'listen':'0.0.0.0','port':443,'protocol':'vless',
  'settings':{'clients':[{'id':uid,'flow':'','email':name} for name,uid in zip(names,ids)],'decryption':'none'},
  'streamSettings':{'network':'xhttp','xhttpSettings':{'path':path},'security':'reality',
   'realitySettings':{'target':sni+':443','serverNames':[sni],'privateKey':os.environ['PRIVATE'],'shortIds':[sid]}},
  'sniffing':{'enabled':True,'destOverride':['http','tls']} }],
 'outbounds':[{'protocol':'freedom','tag':'direct'},{'protocol':'blackhole','tag':'block'}]
}
conf=pathlib.Path(os.environ['CONF']);conf.parent.mkdir(parents=True,exist_ok=True)
conf.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
os.chmod(conf,0o640)
root=pathlib.Path(os.environ['CLIENTS']);root.mkdir(parents=True,exist_ok=True)
for name,uid in zip(names,ids):
 d=root/name;d.mkdir(parents=True,exist_ok=True)
 p={'encryption':'none','type':'xhttp','security':'reality','sni':sni,'fp':'chrome',
    'pbk':os.environ['PUBLIC'],'sid':sid,'path':path,'mode':'auto','spx':'/'}
 host='['+address+']' if ':' in address and not address.startswith('[') else address
 uri=f'vless://{uid}@{host}:443?{urllib.parse.urlencode(p)}#{urllib.parse.quote("XHTTP-"+name)}'
 (d/'node.txt').write_text(uri+'\n')
PY
chgrp xray "$CONF"
chmod 640 "$CONF"
chgrp -R hy2bot /var/lib/hy2-telegram/xray
find /var/lib/hy2-telegram/xray -type d -exec chmod 750 {} +
find /var/lib/hy2-telegram/xray -type f -exec chmod 640 {} +
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  target="$CLIENTS/$name"
  qrencode -o "$target/node-qr.png" -s 8 -m 2 "$(cat "$target/node.txt")"
  chgrp hy2bot "$target/node-qr.png"
  chmod 640 "$target/node-qr.png"
done

if ! "$XRAY" run -test -config "$CONF" >"$TMP/xray-test.log" 2>&1; then
  cat "$TMP/xray-test.log" >&2
  fail 'Xray 官方配置校验失败。'
fi
if grep -qi 'will increase the likelihood of your server' "$TMP/xray-test.log"; then
  cat "$TMP/xray-test.log" >&2
  fail 'REALITY 目标触发 Xray 官方封锁风险警告。请重新运行脚本并更换目标。'
fi
grep -Fq 'Configuration OK.' "$TMP/xray-test.log" || fail '没有检测到官方 Configuration OK 确认。'
systemctl daemon-reload
systemctl enable xray.service
# The upstream installer starts Xray with an empty default config. Explicit
# restart is essential to load the REALITY config written above.
systemctl restart xray.service || {
  journalctl -u xray.service -n 40 --no-pager >&2 || true
  fail 'Xray 无法加载 REALITY 配置。'
}
sleep 2
systemctl is-active --quiet xray.service || fail 'Xray 服务未保持 active，请检查 journalctl -u xray。'
ss -H -ltnp 'sport = :443' | grep -q '"xray"' || fail 'TCP 443 没有由 Xray 监听。'

# Verify access as the actual restricted Telegram service user.
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  sudo -u hy2bot test -r "$CLIENTS/$name/node.txt" || fail "机器人无法读取 $name 的节点链接。"
  sudo -u hy2bot test -r "$CLIENTS/$name/node-qr.png" || fail "机器人无法读取 $name 的二维码。"
done

backup=$BOT.backup-xhttp-$(date +%Y%m%d-%H%M%S)
cp -a "$BOT" "$backup"
if [[ -f "$TMP/dual_bot.py" ]]; then
  dual_backup=$DUAL.backup-xhttp-$(date +%Y%m%d-%H%M%S)
  cp -a "$DUAL" "$dual_backup"
  install -o root -g root -m 755 "$TMP/dual_bot.py" "$DUAL"
fi
install -o root -g root -m 755 "$TMP/bot.py" "$BOT"
if ! systemctl restart hy2-telegram.service; then
  install -o root -g root -m 755 "$backup" "$BOT"
  if [[ -n "${dual_backup:-}" ]]; then
    install -o root -g root -m 755 "$dual_backup" "$DUAL"
  fi
  systemctl restart hy2-telegram.service || true
  fail '机器人重启失败，已恢复旧版本。Xray 保持运行。'
fi
sleep 2
if ! systemctl is-active --quiet hy2-telegram.service; then
  install -o root -g root -m 755 "$backup" "$BOT"
  if [[ -n "${dual_backup:-}" ]]; then
    install -o root -g root -m 755 "$dual_backup" "$DUAL"
  fi
  systemctl restart hy2-telegram.service || true
  fail '机器人未保持 active，已恢复旧版本。Xray 保持运行。'
fi
if command -v ufw >/dev/null && ufw status | grep -q 'Status: active'; then ufw allow 443/tcp; fi
rm -f "$PENDING"
printf '\n完成：HY2 UDP 443、XHTTP REALITY TCP 443 已同时运行。\n'
printf 'Telegram 命令：/xstatus  /xall  /xget IPHONE\n'
printf '凭据目录：%s\n' "$CLIENTS"
printf '请确认服务商防火墙允许 TCP 443，Cloudflare 保持灰云。\n'
