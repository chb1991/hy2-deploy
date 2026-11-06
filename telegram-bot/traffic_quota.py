#!/usr/bin/env python3
"""CHBVPS 500-GiB VPS-wide vnStat accounting and Telegram threshold alerts.

Root-only systemd timer (no provider API). Snapshot is readable by the existing
hy2bot service account; bot never gains access to the provider credentials.
"""
import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import fcntl
import grp
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen

CONFIG = Path("/etc/chbvps-traffic.json")
STATE = Path("/var/lib/hy2-telegram/traffic")
SNAPSHOT = STATE / "quota.json"
ALERTS = STATE / "alerts.json"
LOCK = STATE / "traffic.lock"
CALIBRATION = STATE / "calibration.json"
GIB = 1024 ** 3
THRESHOLDS = (80, 90, 95, 100)


class MonitoringError(RuntimeError):
    pass


def local_cycle(today, reset_day=16):
    if not 1 <= reset_day <= 28:
        raise ValueError("重置日只能是 1–28")
    first = date(today.year, today.month, reset_day)
    if today < first:
        year = today.year - (today.month == 1)
        month = 12 if today.month == 1 else today.month - 1
        first = date(year, month, reset_day)
    if first.month == 12:
        end = date(first.year + 1, 1, reset_day)
    else:
        end = date(first.year, first.month + 1, reset_day)
    return first, end


def parse_day(raw):
    try:
        return date(int(raw["year"]), int(raw["month"]), int(raw["day"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitoringError("vnStat 日期字段无效") from exc


def traffic_entry(value):
    if not isinstance(value, dict):
        raise MonitoringError("vnStat 流量字段不是对象")
    try:
        tx, rx = int(value["tx"]), int(value["rx"])
        if tx < 0 or rx < 0:
            raise ValueError("negative traffic")
        return {"tx": tx, "rx": rx}
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitoringError("vnStat TX/RX 流量字段无效") from exc


def add(a, b):
    for key in ("tx", "rx"):
        a[key] += b[key]


def read_interface(iface, runner=subprocess.run):
    if not iface or len(iface) > 32 or not all(
        c.isascii() and (c.isalnum() or c in "._-") for c in iface
    ):
        raise MonitoringError("无效网卡名称")
    try:
        p = runner(
            ["vnstat", "-i", iface, "--json", "d", "0"],
            capture_output=True, text=True, check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MonitoringError("无法执行 vnstat") from exc
    if p.returncode:
        raise MonitoringError("vnStat 网卡 " + iface + " 未就绪：" + (p.stderr or "")[-160:])
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise MonitoringError("vnStat JSON 解析失败") from exc
    matches = [row for row in doc.get("interfaces", []) if row.get("name") == iface]
    if len(matches) != 1:
        raise MonitoringError("vnStat 未找到网卡：" + iface)
    return matches[0]


def updated_at(entry):
    try:
        updated = entry["updated"]
        d = parse_day(updated["date"])
        t = updated["time"]
        return datetime(d.year, d.month, d.day, int(t["hour"]), int(t["minute"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MonitoringError("vnStat 更新时间不可用") from exc



def calibration_amount(raw):
    """Interpret 204.95G in the same binary GB unit as the 500G quota."""
    try:
        amount = Decimal(str(raw))
        if not amount.is_finite() or not Decimal("0") <= amount <= Decimal("500"):
            raise ValueError("校准值超出 0–500 GB 范围")
        return int((amount * GIB).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise MonitoringError("人工校准流量必须是 0–500 GB 的有效数字") from exc


def load_calibration():
    try:
        with CALIBRATION.open(encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise MonitoringError("人工校准文件格式无效，请不要清空校准记录")
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise MonitoringError("人工校准记录无法读取，暂停配额统计") from exc


def create_calibration(config, amount_gib, expected_cycle, expected_date=None,
                       now=None, reader=read_interface, previous=None):
    """Capture the immutable vnStat total counters at the manual baseline.

    204.95 GiB is the *whole cycle* already billed at capture time, not an
    extra amount to add to vnStat's earlier daily history.
    """
    if now is None:
        now = datetime.now().astimezone()
    if now.tzinfo is None:
        raise MonitoringError("校准时间必须包含时区")
    start, _ = local_cycle(now.date(), int(config.get("reset_day", 16)))
    if expected_cycle != start.isoformat():
        raise MonitoringError("当前计费周期与校准数据日期不匹配；禁止把上期用量套到新周期")
    if expected_date and now.date().isoformat() != expected_date:
        raise MonitoringError("人工流量数据对应 2026-11-06，请重新提供当前日期的已用量再校准")
    amount_bytes = calibration_amount(amount_gib)
    ifaces = config["interfaces"]
    if not isinstance(ifaces, list) or not ifaces or len(set(ifaces)) != len(ifaces):
        raise MonitoringError("校准网卡配置无效")
    if previous is not None:
        if (previous.get("cycle_start") == start.isoformat()
                and previous.get("interfaces") == ifaces
                and int(previous.get("base_used_bytes", -1)) == amount_bytes):
            # Reinstalling must NOT zero the already accumulated post-anchor usage.
            return previous
        raise MonitoringError("已有人工校准记录，拒绝覆盖，以免丢失校准后的流量")
    counters = {}
    for iface in ifaces:
        entry = reader(iface)
        try:
            counters[iface] = traffic_entry(entry["traffic"]["total"])
            changed = updated_at(entry).replace(tzinfo=now.tzinfo)
            if now - changed > timedelta(minutes=60) or changed - now > timedelta(minutes=10):
                raise MonitoringError("无法校准：vnStat 网卡数据不是最近一小时的有效快照")
        except (KeyError, TypeError) as exc:
            raise MonitoringError("无法读取网卡总计数器，拒绝建立不可靠的校准点") from exc
    return {
        "schema": 1,
        "cycle_start": start.isoformat(),
        "base_used_bytes": amount_bytes,
        "base_used_gib": str(Decimal(str(amount_gib))),
        "captured_at": int(now.timestamp()),
        "captured_local": now.isoformat(timespec="minutes"),
        "interfaces": list(ifaces),
        "counters": counters,
        "source": "manual_user_report",
    }


def collect(config, now=None, reader=read_interface, calibration=None):
    if now is None:
        now = datetime.now().astimezone()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reset_day = int(config.get("reset_day", 16))
    start, end = local_cycle(now.date(), reset_day)
    ifaces = config["interfaces"]
    if not isinstance(ifaces, list) or not ifaces or len(ifaces) > 4 or len(set(ifaces)) != len(ifaces):
        raise MonitoringError("必须配置1至4个不重复的网卡")
    allowance = int(config.get("allowance_gib", 500))
    if allowance != 500:
        raise MonitoringError("本部署仅支持明确确认的 500GiB 额度")
    period = {"tx": 0, "rx": 0}
    today = {"tx": 0, "rx": 0}
    lifetime = {"tx": 0, "rx": 0}
    issues = []
    calibrated = bool(calibration and calibration.get("cycle_start") == start.isoformat())
    if calibrated:
        if calibration.get("interfaces") != ifaces:
            raise MonitoringError("校准后网卡列表发生变化，必须重新核对监控范围")
        try:
            captured_date = datetime.fromtimestamp(
                int(calibration["captured_at"]), now.tzinfo
            ).date()
            if captured_date < start or captured_date > now.date():
                raise ValueError("calibration date outside the cycle")
            baseline = int(calibration["base_used_bytes"])
            if baseline < 0:
                raise ValueError("negative baseline")
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise MonitoringError("校准记录损坏，停止显示可能不准确的余额") from exc
    for iface in ifaces:
        row = reader(iface)
        try:
            created = parse_day(row["created"]["date"])
            changed = updated_at(row)
            updated = changed.replace(tzinfo=now.tzinfo)
            days = row["traffic"]["day"]
            totals = traffic_entry(row["traffic"]["total"])
        except (KeyError, TypeError) as exc:
            raise MonitoringError("vnStat 未提供必要统计字段：" + iface) from exc
        add(lifetime, totals)
        if now - updated > timedelta(minutes=60) or updated - now > timedelta(minutes=10):
            issues.append(f"{iface} 的统计更新不及时")
        current_dates = set()
        recorded = {"tx": 0, "rx": 0}
        for entry in days:
            entry_date = parse_day(entry["date"])
            counts = traffic_entry(entry)
            if start <= entry_date <= now.date():
                add(recorded, counts)
                current_dates.add(entry_date)
            if entry_date == now.date():
                add(today, counts)
        if calibrated:
            try:
                base = traffic_entry(calibration["counters"][iface])
            except (KeyError, TypeError) as exc:
                raise MonitoringError("校准基线网卡计数器缺失") from exc
            if totals["tx"] < base["tx"] or totals["rx"] < base["rx"]:
                issues.append(f"{iface} 的 vnStat 数据库累计计数器已经重置")
                continue
            # Only count traffic AFTER the calibration snapshot. Never add
            # the historical daily totals a second time.
            period["tx"] += totals["tx"] - base["tx"]
            period["rx"] += totals["rx"] - base["rx"]
            # Day records are checked only after the anchor, not before it.
            # TrafficlessEntries in vnStat normally creates zero-traffic days.
            expected_days = (now.date() - captured_date).days
            valid_days = sum(
                captured_date < d <= now.date() for d in current_dates
            )
            if valid_days < expected_days:
                issues.append(f"{iface} 校准后有 {expected_days - valid_days} 天缺少日统计")
        else:
            add(period, recorded)
            if created > start:
                issues.append(f"{iface} 的监控始于 {created.isoformat()}，晚于本期起点")
            expected_days = (now.date() - start).days + 1
            if len(current_dates) < expected_days:
                issues.append(f"{iface} 本周期缺少 {expected_days - len(current_dates)} 天的日统计记录")
    is_complete = not issues
    observed = period["tx"] + period["rx"]
    used = observed + (baseline if calibrated else 0)
    limit = allowance * GIB
    result = {
        "schema": 1,
        "sampled_at": int(now.timestamp()),
        "sampled_local": now.isoformat(timespec="minutes"),
        "timezone": now.strftime("%Z %z").strip() or str(now.utcoffset()),
        "interfaces": ifaces,
        "allowance_gib": allowance,
        "cycle_start": start.isoformat(),
        "next_reset": end.isoformat(),
        "reset_day": reset_day,
        "complete": is_complete,
        "calibrated": calibrated,
        "baseline_bytes": baseline if calibrated else 0,
        "calibrated_at": calibration.get("captured_local") if calibrated else None,
        "note": ("; ".join(issues) if issues else
                 "人工校准+vnStat网卡增量" if calibrated else
                 "本机网卡双向流量估算，非服务商计费 API；重置时间按服务器本地日期"),
        "today": today,
        "cycle": period,
        "lifetime": lifetime,
        "used_bytes": used,
        "remaining_bytes": max(0, limit - used) if is_complete else None,
        "percent": round(100 * used / limit, 3) if is_complete else None,
    }
    return result


def write_json(path, obj, mode):
    STATE.mkdir(parents=True, exist_ok=True)
    group = grp.getgrnam("hy2bot").gr_gid
    os.chmod(STATE, 0o750)
    os.chown(STATE, 0, group)
    fd, temp = tempfile.mkstemp(prefix=".traffic-", dir=str(STATE))
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, group)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_state():
    try:
        with ALERTS.open(encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def friendly(value):
    return f"{value / GIB:.2f} GB"


def alert_text(snapshot, threshold):
    used = snapshot["used_bytes"]
    return (
        "⚠ CHBVPS · 整机流量预警\n\n"
        f"计费周期  {snapshot['cycle_start']} 至 {snapshot['next_reset']}（不含）\n"
        "额度  500 GB（按 1024³ 字节）\n"
        f"已使用  {friendly(used)} ({snapshot['percent']:.1f}%)\n"
        f"剩余估算  {friendly(snapshot['remaining_bytes'])}\n"
        f"提醒等级  {threshold}%\n"
        f"上传  {friendly(snapshot['cycle']['tx'])}\n"
        f"下载  {friendly(snapshot['cycle']['rx'])}\n\n"
        "ⓘ vnStat 本机网卡流量不等于搬瓦工后台的精确计费量；不会自动停机。"
    )


def notify_telegram(token, admin_id, body):
    if not token or not admin_id:
        raise MonitoringError("缺少 Telegram Bot Token 或管理员 ID")
    data = json.dumps({
        "chat_id": int(admin_id), "text": body, "disable_web_page_preview": True,
    }).encode("utf-8")
    req = Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=data, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=12) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise MonitoringError("Telegram API 未确认通知成功")
    except Exception as exc:
        # Never print URLs or HTTP exceptions because Bot Token appears in URL.
        raise MonitoringError("Telegram 流量预警发送失败（未保存已提醒状态）") from None


def check_alerts(snapshot, sender=notify_telegram):
    if not snapshot["complete"]:
        return None
    percent = snapshot["percent"]
    due = [level for level in THRESHOLDS if percent >= level]
    if not due:
        return None
    state = load_state()
    key = snapshot["cycle_start"]
    already = state.get("notified", []) if state.get("cycle") == key else []
    new_levels = [level for level in due if level not in already]
    if not new_levels:
        return None
    highest = max(new_levels)
    sender(os.environ.get("BOT_TOKEN", ""), os.environ.get("ADMIN_USER_ID", ""),
           alert_text(snapshot, highest))
    write_json(ALERTS, {"cycle": key, "notified": sorted(set(already + due))}, 0o600)
    return highest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="发送达到阈值的新提醒")
    parser.add_argument("--calibrate", help="本计费周期当前已用的人工校准值（GB）")
    parser.add_argument("--target-cycle", help="校准适用的计费周期起点，例如 2026-10-16")
    parser.add_argument("--calibration-date", help="人工报告日期，例如 2026-11-06")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise MonitoringError("需由 root 的 systemd 定时服务运行")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = load_calibration()
        if args.calibrate is not None:
            if not args.target_cycle or not args.calibration_date:
                raise MonitoringError("校准需要明确的周期及人工流量报告日期")
            calibration = create_calibration(
                config, args.calibrate, args.target_cycle, args.calibration_date,
                previous=existing,
            )
            if existing is None:
                write_json(CALIBRATION, calibration, 0o600)
            print("CHBVPS 人工流量校准已保存（或已存在），校准值："
                  + calibration["base_used_gib"] + " GB；校准后增量由 vnStat 统计。")
            return
        try:
            snapshot = collect(config, calibration=existing)
        except (MonitoringError, ValueError) as exc:
            # Clear old snapshots on read errors: never show outdated "remaining".
            snapshot = {
                "schema": 1, "sampled_at": int(time.time()),
                "complete": False, "unavailable": True,
                "note": str(exc)[:300],
                "allowance_gib": 500,
            }
        write_json(SNAPSHOT, snapshot, 0o640)
        if args.notify:
            check_alerts(snapshot)
        print("CHBVPS traffic: " + ("complete" if snapshot["complete"] else "incomplete/unavailable")
              + " · " + snapshot.get("note", ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("流量监控失败：" + str(exc)[:300], file=sys.stderr)
        sys.exit(1)
