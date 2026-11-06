#!/usr/bin/env bash
# Non-privileged CI smoke test against the exact official Xray release.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
VERSION=v26.10.31
# Regression: /etc/os-release contains VERSION="13 (trixie)", which must
# never overwrite the requested Xray release in the installer.
INSTALLER_VERSION="$(sed -nE 's/^XRAY_VERSION=(v[0-9]+\.[0-9]+\.[0-9]+)$/\1/p' "$ROOT/xray-xhttp/install.sh")"
[[ "$INSTALLER_VERSION" == "$VERSION" ]] || {
  printf 'FAIL: installer Xray version overwritten or does not match smoke target\n' >&2
  exit 1
}
grep -Fq 'install --version "$XRAY_VERSION"' "$ROOT/xray-xhttp/install.sh"
(
  XRAY_VERSION="$INSTALLER_VERSION"
  . /etc/os-release
  [[ "$XRAY_VERSION" == "$INSTALLER_VERSION" ]] || {
    printf 'FAIL: /etc/os-release overwrote XRAY_VERSION\n' >&2
    exit 1
  }
)
ZIP="$TMP/Xray-linux-64.zip"
curl -fL --retry 3 --retry-delay 2 --connect-timeout 15 \
  "https://github.com/XTLS/Xray-core/releases/download/$VERSION/Xray-linux-64.zip" \
  -o "$ZIP"
printf '%s  %s\n' \
  e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214 "$ZIP" |
  sha256sum -c -
unzip -p "$ZIP" xray > "$TMP/xray"
chmod 755 "$TMP/xray"
"$TMP/xray" version | head -n 1

keys="$("$TMP/xray" x25519)"
PRIVATE=$(printf '%s\n' "$keys" | sed -nE 's/^PrivateKey:[[:space:]]*//p' | head -1)
PUBLIC=$(printf '%s\n' "$keys" | sed -nE 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' | head -1)
[[ "$PRIVATE" =~ ^[A-Za-z0-9_-]{43,44}$ && "$PUBLIC" =~ ^[A-Za-z0-9_-]{43,44}$ ]] || {
  printf 'Failed to parse official Xray x25519 output\n' >&2
  exit 1
}
ADDRESS=proxy.example.com
SNI=www.example.org
SID=0123456789abcdef
XPATH=/a1b2c3d4e5f6
UUIDS=''
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  one="$("$TMP/xray" uuid | tr -d '\r\n')"
  [[ "$one" =~ ^[0-9a-fA-F-]{36}$ ]] || exit 1
  if [[ -z "$UUIDS" ]]; then UUIDS="$one"; else UUIDS="$UUIDS,$one"; fi
done
CONF="$TMP/config.json"
CLIENTS="$TMP/clients"
export ADDRESS SNI PRIVATE PUBLIC SID XPATH UUIDS CONF CLIENTS
# Test the very same Python config-rendering block embedded in install.sh.
awk '/^python3 <<\x27PY\x27$/ {inside=1; next} inside && /^PY$/ {exit} inside {print}' \
  "$ROOT/xray-xhttp/install.sh" > "$TMP/render.py"
[[ -s "$TMP/render.py" ]] || exit 1
python3 "$TMP/render.py"
"$TMP/xray" run -test -config "$CONF"
python3 - <<'PY'
import json, os, pathlib, urllib.parse
conf=json.loads(pathlib.Path(os.environ["CONF"]).read_text())
inbound=conf["inbounds"][0]
assert inbound["protocol"]=="vless" and inbound["port"]==443
settings=inbound["streamSettings"]
assert settings["network"]=="xhttp" and settings["security"]=="reality"
assert settings["xhttpSettings"]["path"]==os.environ["XPATH"]
assert settings["realitySettings"]["privateKey"]==os.environ["PRIVATE"]
assert len(inbound["settings"]["clients"])==4
assert len({c["id"] for c in inbound["settings"]["clients"]})==4
for name in ("WIN-PC","MACBOOK","IPHONE","FAMILY"):
    uri=(pathlib.Path(os.environ["CLIENTS"])/name/"node.txt").read_text().strip()
    u=urllib.parse.urlsplit(uri)
    q=urllib.parse.parse_qs(u.query)
    assert u.scheme=="vless" and u.hostname=="proxy.example.com" and u.port==443
    assert q["type"]==["xhttp"] and q["security"]==["reality"]
    assert q["path"]==[os.environ["XPATH"]]
    assert q["pbk"]==[os.environ["PUBLIC"]]
print("PASS: Xray official version, REALITY keys, config, four VLESS links")
PY
