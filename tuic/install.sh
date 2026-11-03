#!/usr/bin/env bash
# TUIC v5 alongside an already working HY2 + Telegram installation.
# No edits/restarts of /etc/hysteria, hysteria-server, or existing HY2 credentials.
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/chb1991/hy2-deploy/main"
SING_BOX_VERSION="1.14.4"
ROOT="/etc/tuic-singbox"
BIN="/opt/tuic-singbox/sing-box"
UNIT="/etc/systemd/system/hy2-telegram.service"
TMP="$(mktemp -d)"
BACKUP=""
BOT_UPDATED=0
TUIC_STARTED=0
fail() { echo "[TUIC ERROR] $*" >&2; exit 1; }
log() { echo; echo "[TUIC] $*"; }
cleanup() {
  code=$?
  trap - EXIT
  if (( code != 0 )); then
    echo "[TUIC] Error. Restoring original Telegram unit if it was changed." >&2
    if (( BOT_UPDATED )) && [[ -n "$BACKUP" ]]; then
      cp -a "$BACKUP/hy2-telegram.service" "$UNIT"
      systemctl daemon-reload || true
      systemctl restart hy2-telegram.service || true
    fi
    if (( TUIC_STARTED )); then systemctl stop tuic-singbox.service || true; fi
    echo "[TUIC] HY2 was not modified or restarted." >&2
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

[[ $EUID == 0 ]] || fail "Root required."
. /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 && $(uname -m) == x86_64 ]] || fail "Only Debian 13 x86_64 is supported."
systemctl is-active --quiet hysteria-server.service || fail "Existing HY2 not active."
systemctl is-active --quiet hy2-telegram.service || fail "Existing Telegram bot not active."
[[ -f /etc/hysteria/config.yaml && -f /var/lib/hy2-telegram/base-client.yaml ]] || fail "HY2 config/client template not found."
[[ -f /etc/hy2-telegram/bot.env && -f /opt/hy2-telegram/bot.py ]] || fail "Original bot files missing."
[[ -f "$UNIT" ]] || fail "Expected Telegram service unit file not found."
[[ -x /usr/local/sbin/hy2-users ]] || fail "HY2 multi-user manager missing."
[[ ! -e "$ROOT/config.json" ]] || fail "Existing TUIC config detected; refusing to overwrite."
if ss -H -lunp 'sport = :51443' | grep -q .; then fail "UDP 51443 already occupied."; fi

log "Installing TUIC dependencies (no HY2 changes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-yaml certbot python3-certbot-dns-cloudflare curl jq ca-certificates tar openssl qrencode sudo iproute2

# These values never leave the VPS. Do not log any Cloudflare secrets.
eval "$(python3 - <<'PY'
import yaml, shlex
from pathlib import Path
c=yaml.safe_load(Path('/etc/hysteria/config.yaml').read_text()) or {}
a=c.get('acme') or {}
dns=a.get('dns') or {}
domain=str((a.get('domains') or [''])[0]).strip().lower()
email=str(a.get('email') or '').strip()
token=str((dns.get('config') or {}).get('cloudflare_api_token') or '').strip()
if dns.get('name')!='cloudflare' or not domain or not email or not token:
    raise SystemExit('Existing HY2 Cloudflare DNS-01 config is required.')
if not all(x.isalnum() or x in '.-' for x in domain) or '.' not in domain:
    raise SystemExit('Invalid HY2 domain.')
for k,v in (('DOMAIN',domain),('EMAIL',email),('CF_TOKEN',token)):
    print('export '+k+'='+shlex.quote(v))
PY
)"
[[ -n "$DOMAIN" && -n "$EMAIL" ]] || fail "Existing certificate parameters not found."

log "Creating isolated TUIC service account"
getent group tuic >/dev/null || groupadd --system tuic
id tuic >/dev/null 2>&1 || useradd --system -g tuic --home-dir /var/lib/tuic-singbox --shell /usr/sbin/nologin tuic
install -d -o root -g root -m 0755 /opt/tuic-singbox
install -d -o root -g tuic -m 0750 "$ROOT"
install -d -o root -g root -m 0700 /var/lib/tuic-singbox
install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy

log "Fetching official sing-box version $SING_BOX_VERSION with SHA256 verification"
ASSET="sing-box-$SING_BOX_VERSION-linux-amd64.tar.gz"
RELEASE="$(curl -fsSL --retry 3 "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v$SING_BOX_VERSION")"
URL="$(jq -r --arg n "$ASSET" '[.assets[] | select(.name==$n) | .browser_download_url][0] // empty' <<< "$RELEASE")"
DIGEST="$(jq -r --arg n "$ASSET" '[.assets[] | select(.name==$n) | .digest][0] // empty' <<< "$RELEASE")"
[[ "$URL" == "https://github.com/SagerNet/sing-box/releases/download/"* ]] || fail "Official release asset not found."
[[ "$DIGEST" =~ ^sha256:[a-fA-F0-9]{64}$ ]] || fail "Official SHA256 digest missing."
curl -fL --retry 3 "$URL" -o "$TMP/$ASSET"
echo "$DIGEST" | sed 's/^sha256://' | awk -v path="$TMP/$ASSET" '{print $1 "  " path}' | sha256sum -c - >/dev/null || fail "Binary checksum mismatch."
tar -xzf "$TMP/$ASSET" -C "$TMP"
install -m 0755 "$TMP/sing-box-$SING_BOX_VERSION-linux-amd64/sing-box" "$BIN"

log "Issuing separate Let's Encrypt DNS-01 certificate for the existing domain"
printf 'dns_cloudflare_api_token = %s\n' "$CF_TOKEN" > "$ROOT/cloudflare.ini"
chmod 0600 "$ROOT/cloudflare.ini"
unset CF_TOKEN
CERT_NAME="tuic-$DOMAIN"
CERT_DIR="/etc/letsencrypt/live/$CERT_NAME"
certbot certonly --non-interactive --agree-tos --keep-until-expiring \
  --dns-cloudflare --dns-cloudflare-credentials "$ROOT/cloudflare.ini" \
  --dns-cloudflare-propagation-seconds 45 \
  --email "$EMAIL" --cert-name "$CERT_NAME" -d "$DOMAIN"
[[ -r "$CERT_DIR/privkey.pem" ]] || fail "New certificate is missing."

# Certbot renewal hook deploys only the TUIC certificate and only restarts TUIC.
cat > /usr/local/sbin/tuic-cert-copy <<'HOOK'
#!/usr/bin/env bash
set -Eeuo pipefail
directory="$(cat /etc/tuic-singbox/cert-lineage)"
if [[ -n "${RENEWED_LINEAGE:-}" && "$RENEWED_LINEAGE" != "$directory" ]]; then exit 0; fi
install -o root -g tuic -m 0640 "$directory/fullchain.pem" /etc/tuic-singbox/cert.pem
install -o root -g tuic -m 0640 "$directory/privkey.pem" /etc/tuic-singbox/key.pem
if systemctl is-active --quiet tuic-singbox.service; then systemctl try-restart tuic-singbox.service; fi
HOOK
chmod 0750 /usr/local/sbin/tuic-cert-copy
echo "$CERT_DIR" > "$ROOT/cert-lineage"
chmod 0600 "$ROOT/cert-lineage"
install -m 0750 /usr/local/sbin/tuic-cert-copy /etc/letsencrypt/renewal-hooks/deploy/tuic-cert-copy
/usr/local/sbin/tuic-cert-copy

log "Generating TUIC v5 config with four independent device credentials"
DOMAIN="$DOMAIN" python3 - <<'PY'
import json, os, secrets, uuid
from pathlib import Path
d=os.environ['DOMAIN']
users=[{'name':n,'uuid':str(uuid.uuid4()),'password':secrets.token_urlsafe(24)}
       for n in ('WIN-PC','MACBOOK','IPHONE','FAMILY')]
config={
  'log':{'level':'warn','timestamp':True},
  'inbounds':[{
     'type':'tuic','tag':'tuic-in','listen':'0.0.0.0','listen_port':51443,
     'users':users,'congestion_control':'bbr','zero_rtt_handshake':False,
     'tls':{'enabled':True,'server_name':d,'alpn':['h3'],
            'certificate_path':'/etc/tuic-singbox/cert.pem',
            'key_path':'/etc/tuic-singbox/key.pem'}
  }],
  'outbounds':[{'type':'direct','tag':'direct'}]
}
p=Path('/etc/tuic-singbox')
(p/'config.json').write_text(json.dumps(config,indent=2)+'\n')
(p/'info.json').write_text(json.dumps({'domain':d,'port':51443},indent=2)+'\n')
PY
chown root:tuic "$ROOT/config.json"
chmod 0640 "$ROOT/config.json"
chown root:hy2bot "$ROOT/info.json"
chmod 0640 "$ROOT/info.json"
"$BIN" check -c "$ROOT/config.json" || fail "TUIC config validation failed."

cat > /etc/systemd/system/tuic-singbox.service <<'SERVICE'
[Unit]
Description=TUIC v5 sing-box UDP 51443
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
User=tuic
Group=tuic
ExecStartPre=/opt/tuic-singbox/sing-box check -c /etc/tuic-singbox/config.json
ExecStart=/opt/tuic-singbox/sing-box run -c /etc/tuic-singbox/config.json
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
SERVICE
systemctl daemon-reload
systemctl enable --now tuic-singbox.service
TUIC_STARTED=1
sleep 2
systemctl is-active --quiet tuic-singbox.service || fail "TUIC not active."
ss -H -lunp 'sport = :51443' | grep -q . || fail "UDP 51443 not listening."

log "Opening UDP 51443 only in existing managed firewall"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
  ufw allow 51443/udp
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --add-port=51443/udp
  firewall-cmd --permanent --add-port=51443/udp
else
  echo "No active UFW/firewalld detected; upstream firewall must allow UDP 51443."
fi

log "Installing TUIC manager, core updater and Telegram adapter"
curl -fsSL --retry 3 "$RAW/tuic/users.py" -o "$TMP/users.py"
curl -fsSL --retry 3 "$RAW/telegram-bot/dual_bot.py" -o "$TMP/dual_bot.py"
curl -fsSL --retry 3 "$RAW/core-update.sh" -o "$TMP/core-update.sh"
python3 -m py_compile "$TMP/users.py" "$TMP/dual_bot.py"
bash -n "$TMP/core-update.sh"
install -o root -g root -m 0750 "$TMP/users.py" /usr/local/sbin/tuic-users
install -o root -g root -m 0755 "$TMP/dual_bot.py" /opt/hy2-telegram/dual_bot.py
install -o root -g root -m 0750 "$TMP/core-update.sh" /usr/local/sbin/core-update
if [[ -e /usr/local/bin/update || -L /usr/local/bin/update ]]; then
  [[ "$(readlink -f /usr/local/bin/update 2>/dev/null || true)" == "/usr/local/sbin/core-update" ]] || fail "/usr/local/bin/update 已被其他程序占用；为避免覆盖，未创建 update 快捷命令。"
fi
ln -sfn /usr/local/sbin/core-update /usr/local/bin/update
/usr/local/sbin/tuic-users add-defaults >/dev/null

BACKUP="/root/tuic-upgrade-backup-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$BACKUP"
cp -a "$UNIT" "$BACKUP/hy2-telegram.service"
[[ ! -e /etc/sudoers.d/tuic-telegram ]] || fail "Existing TUIC sudoers file; review manually."
echo 'hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/tuic-users *' > /etc/sudoers.d/tuic-telegram
chmod 0440 /etc/sudoers.d/tuic-telegram
visudo -cf /etc/sudoers.d/tuic-telegram >/dev/null

cat > /etc/sudoers.d/core-update <<'SUDO'
hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/core-update --check --json
hy2bot ALL=(root) NOPASSWD: /usr/local/sbin/core-update --all --yes --json
SUDO
chmod 0440 /etc/sudoers.d/core-update
visudo -cf /etc/sudoers.d/core-update >/dev/null
grep -q '^ExecStart=/usr/bin/python3 /opt/hy2-telegram/bot.py$' "$UNIT" || fail "Unknown bot unit ExecStart; aborting instead of overwriting."
sed -i 's@^ExecStart=/usr/bin/python3 /opt/hy2-telegram/bot.py$@ExecStart=/usr/bin/python3 /opt/hy2-telegram/dual_bot.py@' "$UNIT"
BOT_UPDATED=1
systemctl daemon-reload
systemctl restart hy2-telegram.service
sleep 3
systemctl is-active --quiet hy2-telegram.service || fail "Dual bot failed, rolling back."
systemctl enable --now certbot.timer >/dev/null 2>&1 || true

echo
echo "=============== DEPLOYED ==============="
echo "HY2 UDP 443:       $(systemctl is-active hysteria-server.service)"
echo "TUIC UDP 51443:    $(systemctl is-active tuic-singbox.service)"
echo "Telegram dual bot: $(systemctl is-active hy2-telegram.service)"
echo "Domain: $DOMAIN"
echo "Old bot unit backed up at $BACKUP"
echo "Open Telegram: /status or /add_defaults"
echo "Test actual TUIC connectivity from phone cellular data."
echo "========================================"
