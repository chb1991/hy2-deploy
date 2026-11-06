#!/usr/bin/env bash
# Install provider-API-free 500GiB VPS-wide bandwidth monitoring for CHBVPS V2.
# Does NOT modify or restart HY2, TUIC, XHTTP, or Vision.
set -Eeuo pipefail
umask 077

RAW="https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot"
BOT_DIR=/opt/hy2-telegram
CONFIG=/etc/chbvps-traffic.json
STATE=/var/lib/hy2-telegram/traffic
VNSTAT_CONF=/etc/vnstat.conf
UNIT=/etc/systemd/system/chbvps-traffic.service
TIMER=/etc/systemd/system/chbvps-traffic.timer
TMP="$(mktemp -d /tmp/chbvps-traffic.XXXXXXXX)"
BACKUP="/root/chbvps-traffic-backups/$(date +%Y%m%d-%H%M%S)-$$"
APPLIED=0
DONE=0

fail() { printf '\n[流量监控] 错误：%s\n' "$*" >&2; exit 1; }
log() { printf '\n[流量监控] %s\n' "$*"; }

cleanup() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 && APPLIED == 1 && DONE == 0 )); then
    log "部署失败，尽力恢复旧版 Telegram UI 与 vnStat 配置。"
    systemctl disable --now chbvps-traffic.timer >/dev/null 2>&1 || true
    for path in "$VNSTAT_CONF" "$CONFIG" "$UNIT" "$TIMER" \
                "$BOT_DIR/traffic_quota.py" "$BOT_DIR/v2_ui.py"; do
      local name
      name="$(basename "$path")"
      if [[ -f "$BACKUP/$name" ]]; then
        cp -a "$BACKUP/$name" "$path" || true
      else
        rm -f "$path" || true
      fi
    done
    systemctl daemon-reload || true
    systemctl restart vnstat.service || true
    systemctl restart hy2-telegram.service || true
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT
trap 'printf "[流量监控] 第 %s 行退出，状态码：%s\n" "$LINENO" "$?" >&2' ERR

[[ $EUID -eq 0 ]] || fail "请用 root SSH 执行。"
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 ]] || fail "安装器当前仅支持 Debian 13。"
[[ -f "$BOT_DIR/v2_ui.py" && -f "$BOT_DIR/bot.py" ]] || fail "先安装 CHBVPS Telegram V2.0，再安装流量统计。"
[[ -f /etc/hy2-telegram/bot.env ]] || fail "原 Telegram Bot 环境文件缺失。"
systemctl is-active --quiet hy2-telegram.service || fail "现有 Telegram 机器人未运行。"
id hy2bot >/dev/null || fail "缺少现有机器人账户 hy2bot。"
for c in curl python3 systemctl apt-get sudo; do command -v "$c" >/dev/null || fail "缺少 $c。"; done

log "下载代码并检查 Python 语法……"
curl -fsSL --retry 3 "$RAW/traffic_quota.py" -o "$TMP/traffic_quota.py"
curl -fsSL --retry 3 "$RAW/v2_ui.py" -o "$TMP/v2_ui.py"
python3 -m py_compile "$TMP/traffic_quota.py" "$TMP/v2_ui.py"
printf '\n部署范围：整机公网流量 500GB、每月16日重置、80/90/95/100%% 分级 Telegram 预警。\n'
printf '不会申请搬瓦工 API，不修改代理端口、密钥或证书。\n'
read -r -p '确认安装？[y/N]： ' ans
[[ "$ans" == y || "$ans" == Y ]] || { echo "已取消。"; exit 0; }

install -d -o root -g root -m 700 "$BACKUP"
for path in "$VNSTAT_CONF" "$CONFIG" "$UNIT" "$TIMER" \
            "$BOT_DIR/traffic_quota.py" "$BOT_DIR/v2_ui.py"; do
  if [[ -f "$path" ]]; then cp -a "$path" "$BACKUP/$(basename "$path")"; fi
done

log "安装 vnStat（只新增监控软件）……"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1
if ! command -v vnstat >/dev/null 2>&1 || ! command -v ip >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends vnstat iproute2
fi
[[ -f "$VNSTAT_CONF" ]] || fail "未找到 vnstat.conf，安装不完整。"

log "自动识别 IPv4/IPv6 公网默认路由网卡……"
if [[ -v CHBVPS_TRAFFIC_IFACES && -n "$CHBVPS_TRAFFIC_IFACES" ]]; then
  IFACES="$CHBVPS_TRAFFIC_IFACES"
else
  IFACES="$(python3 - <<'PY'
import json,subprocess
names=[]
for family in ("-4","-6"):
    p=subprocess.run(["ip","-j",family,"route","show","default"],
                     capture_output=True,text=True,timeout=10)
    if p.returncode:
        continue
    try:
        routes=json.loads(p.stdout)
    except ValueError:
        continue
    for route in routes:
        dev=route.get("dev")
        if dev and dev not in names:
            names.append(dev)
if not names:
    raise SystemExit("未找到默认路由网卡；请指定 CHBVPS_TRAFFIC_IFACES=eth0")
if any(name=="lo" or name.startswith(("tun","tap","wg","tailscale","veth","docker","br-"))
       for name in names):
    raise SystemExit("默认路由经过虚拟接口，不能直接视为公网流量。请手动指定物理公网网卡。")
if len(names)>4:
    raise SystemExit("网卡数量异常，请人工确认。")
print(",".join(names))
PY
)"
fi
printf '将监控网卡：%s\n' "$IFACES"

# From now on any failure attempts rollback for files, never for proxy services.
APPLIED=1
log "按每月16日配置 vnStat 的统计月起点……"
python3 - "$VNSTAT_CONF" <<'PY'
from pathlib import Path
import re,sys
p=Path(sys.argv[1])
old=p.read_text(encoding="utf-8")
lines=[l for l in old.splitlines()
       if not re.match(r"^\s*[;#]?\s*(?:MonthRotate|UseUTC)\s+\d+(?:\s*(?:[#;].*)?)?$",l)]
lines.extend(["MonthRotate 16","UseUTC 0"])
p.write_text("\n".join(lines)+"\n",encoding="utf-8")
PY
python3 - "$CONFIG" "$IFACES" <<'PY'
from pathlib import Path
import json,sys,re
p=Path(sys.argv[1]); names=sys.argv[2].split(",")
if not names or len(names)>4 or len(set(names))!=len(names):
    raise SystemExit("网卡列表异常")
for name in names:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}",name):
        raise SystemExit("无效网卡名称")
doc={"interfaces":names,"allowance_gib":500,"reset_day":16,
     "note":"Provider API-free local estimate, usage includes TX+RX"}
p.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
p.chmod(0o600)
PY

install -o root -g root -m 0755 "$TMP/traffic_quota.py" "$BOT_DIR/traffic_quota.py"
install -o root -g root -m 0755 "$TMP/v2_ui.py" "$BOT_DIR/v2_ui.py"
install -d -o root -g hy2bot -m 0750 "$STATE"

systemctl enable --now vnstat.service >/dev/null
systemctl restart vnstat.service
for interface in $(printf '%s\n' "$IFACES" | tr ',' ' '); do
  if ! vnstat --dbiflist 1 2>/dev/null | grep -Fxq "$interface"; then
    vnstat --add -i "$interface" >/dev/null || fail "不能将网卡 $interface 加入 vnStat。"
  fi
done
systemctl restart vnstat.service

log "创建只读统计快照和独立的后台计时器……"
cat > "$UNIT" <<'UNIT'
[Unit]
Description=CHBVPS VPS-wide vnStat 500GiB billing-cycle monitor
After=network-online.target vnstat.service
Wants=network-online.target
Requires=vnstat.service

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=/etc/hy2-telegram/bot.env
ExecStart=/usr/bin/env -u TZ /usr/bin/python3 /opt/hy2-telegram/traffic_quota.py --notify
UMask=0077
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/hy2-telegram/traffic
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
NoNewPrivileges=true
UNIT

cat > "$TIMER" <<'TIMER'
[Unit]
Description=Update CHBVPS VPS-wide traffic every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true
Unit=chbvps-traffic.service

[Install]
WantedBy=timers.target
TIMER
chmod 644 "$UNIT" "$TIMER"
systemctl daemon-reload

# Produce a snapshot before starting scheduled notifications.
/usr/bin/env -u TZ python3 "$BOT_DIR/traffic_quota.py"
[[ -r "$STATE/quota.json" ]] || fail "未能生成流量快照"
sudo -u hy2bot test -r "$STATE/quota.json" || fail "Telegram 账户无法读取流量快照"
systemctl enable --now chbvps-traffic.timer >/dev/null
systemctl restart hy2-telegram.service
sleep 2
systemctl is-active --quiet hy2-telegram.service || fail "Telegram 机器人没有保持 active"
systemctl is-active --quiet chbvps-traffic.timer || fail "流量监控计时器没有保持 active"

DONE=1
echo
echo "=========== CHBVPS 500GB 流量监控已部署 ==========="
printf '网卡：%s\n' "$IFACES"
echo "周期：每月16日开始，按 VPS 本地时间；包含所有协议及系统流量"
echo "提醒：80%、90%、95%、100%，每周期每阈值最多一次"
echo "统计初期如果不是完整周期，会明确标记并暂不发送预警"
echo "未重启 HY2/TUIC/Vision/XHTTP；只重启了 vnStat 和 Telegram"
printf '配置备份：%s\n' "$BACKUP"
echo "Telegram：/status 查看整机统计，/start 查看流量概要"
echo "诊断：systemctl status chbvps-traffic.timer --no-pager"
