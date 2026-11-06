#!/usr/bin/env bash
# Smoke-test actual VLESS+REALITY+Vision generator against official Xray release.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL=https://github.com/XTLS/Xray-core/releases/download/v26.10.31/Xray-linux-64.zip
curl -fsSL --retry 3 --connect-timeout 15 "$URL" -o "$TMP/xray.zip"
echo "e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214  $TMP/xray.zip" | sha256sum -c -
unzip -p "$TMP/xray.zip" xray > "$TMP/xray"
chmod 755 "$TMP/xray"
# Regression for set -Eeuo pipefail: 'xray version | head -n 1' may fail
# with SIGPIPE (141) before the installer prints any interactive prompt.
if grep -nE '\|[[:space:]]*head[[:space:]]+-n[[:space:]]+1' "$ROOT/xray-vision/install.sh"; then
  echo "FAIL: installer contains unsafe head pipeline under pipefail" >&2
  exit 1
fi
banner="$("$TMP/xray" version | sed -n '1p')"
[[ "$banner" == Xray* ]] || { echo "FAIL: cannot read Xray banner" >&2; exit 1; }
keys="$("$TMP/xray" x25519)"
VISION_PRIVATE="$(printf '%s\n' "$keys" | sed -nE 's/^PrivateKey:[[:space:]]*//p' | head -n 1)"
VISION_PUBLIC="$(printf '%s\n' "$keys" | sed -nE 's/^(Password( \(PublicKey\))?|PublicKey):[[:space:]]*//p' | head -n 1)"
[[ "$VISION_PRIVATE" =~ ^[A-Za-z0-9_-]{43,44}$ && "$VISION_PUBLIC" =~ ^[A-Za-z0-9_-]{43,44}$ ]] || exit 1
VISION_SID=0a1b2c3d4e5f6071
VISION_UUIDS=''
for name in WIN-PC MACBOOK IPHONE FAMILY; do
  id="$("$TMP/xray" uuid | tr -d '\r\n')"
  if [[ -z "$VISION_UUIDS" ]]; then VISION_UUIDS="$id"; else VISION_UUIDS="$VISION_UUIDS,$id"; fi
done
export VISION_PRIVATE VISION_PUBLIC VISION_SID VISION_UUIDS
python3 "$ROOT/xray-vision/configure.py" \
  --host proxy.example.com --sni www.example.org --port 8443 --out "$TMP/vision"
"$TMP/xray" run -test -config "$TMP/vision/config.json"
python3 - "$TMP/vision" <<'PY'
import json,sys,urllib.parse,pathlib
from uuid import UUID
d=pathlib.Path(sys.argv[1])
c=json.loads((d/"config.json").read_text())
inbound=c["inbounds"][0]
assert inbound["port"]==8443
assert inbound["protocol"]=="vless"
assert inbound["streamSettings"]["network"]=="tcp"
assert inbound["streamSettings"]["security"]=="reality"
assert inbound["streamSettings"]["realitySettings"]["target"]=="www.example.org:443"
clients=inbound["settings"]["clients"]
assert len(clients)==4 and len({u["id"] for u in clients})==4
assert all(u["flow"]=="xtls-rprx-vision" for u in clients)
for name in ("WIN-PC","MACBOOK","IPHONE","FAMILY"):
    uri=(d/"clients"/name/"node.txt").read_text().strip()
    loon=(d/"clients"/name/"loon.txt").read_text().strip()
    link=urllib.parse.urlsplit(uri)
    query=urllib.parse.parse_qs(link.query)
    UUID(link.username)
    assert link.hostname=="proxy.example.com" and link.port==8443
    assert query["type"]==["tcp"] and query["security"]==["reality"]
    assert query["flow"]==["xtls-rprx-vision"]
    assert query["sni"]==["www.example.org"]
    assert query["fp"]==["chrome147"]
    assert query["tls-profile"]==["chrome147"]
    assert query["pbk"] and query["sid"]
    assert f"VISION-{name} = VLESS,proxy.example.com,8443" in loon
    assert "transport=tcp,flow=xtls-rprx-vision" in loon
    assert "over-tls=true" in loon
    assert "tls-profile=chrome147" in loon
print("PASS: Xray official Vision config, 4 UUIDs, VLESS URI and Loon lines")
PY
