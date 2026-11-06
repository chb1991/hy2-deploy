#!/usr/bin/env python3
"""Offline tests for CHBVPS vnStat billing-cycle aggregation and safe alerts."""
import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "traffic_quota",
    Path(__file__).with_name("traffic_quota.py"),
)
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 6, 12, 40, tzinfo=TZ)


def day(d):
    return {"year": d.year, "month": d.month, "day": d.day}


def iface(created=date(2026, 10, 15), current_gib=3, now=NOW):
    start = date(2026, 10, 16)
    dates = [start + timedelta(days=i) for i in range((now.date() - start).days + 1)]
    records = [{"date": day(d), "tx": 0, "rx": 0} for d in dates]
    records[0].update({"tx": current_gib * quota.GIB, "rx": quota.GIB})
    records[-1].update({"tx": 2 * quota.GIB, "rx": quota.GIB})
    return {
        "name": "eth0",
        "created": {"date": day(created)},
        "updated": {"date": day(now.date()), "time": {"hour": 12, "minute": 35}},
        "traffic": {
            "total": {"tx": 5 * quota.GIB, "rx": 2 * quota.GIB},
            "day": [
                {"date": day(date(2026, 10, 15)), "tx": 55 * quota.GIB, "rx": 1},
                *records,
            ],
        },
    }


class QuotaTests(unittest.TestCase):
    def test_cycle_date_16_including_year_rollover(self):
        self.assertEqual(
            quota.local_cycle(date(2026, 11, 6)),
            (date(2026, 10, 16), date(2026, 11, 16)),
        )
        self.assertEqual(
            quota.local_cycle(date(2026, 11, 16)),
            (date(2026, 11, 16), date(2026, 12, 16)),
        )
        self.assertEqual(
            quota.local_cycle(date(2027, 1, 3)),
            (date(2026, 12, 16), date(2027, 1, 16)),
        )

    def test_interface_totals_are_both_directions_without_old_day(self):
        data = quota.collect(
            {"interfaces": ["eth0"], "reset_day": 16, "allowance_gib": 500},
            now=NOW, reader=lambda _: iface(),
        )
        self.assertTrue(data["complete"])
        self.assertEqual(data["cycle"]["tx"], 5 * quota.GIB)
        self.assertEqual(data["cycle"]["rx"], 2 * quota.GIB)
        self.assertEqual(data["used_bytes"], 7 * quota.GIB)
        self.assertEqual(data["remaining_bytes"], 493 * quota.GIB)
        self.assertEqual(data["today"]["rx"], quota.GIB)
        self.assertEqual(data["next_reset"], "2026-11-16")

    def test_newly_installed_vnstat_disables_quota_and_alerts(self):
        data = quota.collect(
            {"interfaces": ["eth0"], "reset_day": 16, "allowance_gib": 500},
            now=NOW, reader=lambda _: iface(created=date(2026, 11, 5)),
        )
        self.assertFalse(data["complete"])
        self.assertIsNone(data["percent"])
        self.assertIsNone(data["remaining_bytes"])
        sender = mock.Mock()
        with mock.patch.object(quota, "write_json") as write:
            self.assertIsNone(quota.check_alerts(data, sender=sender))
            write.assert_not_called()
            sender.assert_not_called()

    def test_user_report_204_95_calibrates_midcycle_without_double_counting(self):
        config = {"interfaces": ["eth0"], "reset_day": 16, "allowance_gib": 500}
        earlier = iface(created=date(2026, 11, 6))
        base = quota.create_calibration(
            config, "204.95", "2026-10-16", "2026-11-06",
            now=NOW, reader=lambda _: earlier,
        )
        self.assertEqual(base["base_used_bytes"], quota.calibration_amount("204.95"))
        first = quota.collect(config, now=NOW, reader=lambda _: earlier, calibration=base)
        self.assertTrue(first["complete"])
        self.assertTrue(first["calibrated"])
        self.assertEqual(first["cycle"], {"tx": 0, "rx": 0})
        self.assertEqual(first["used_bytes"], base["base_used_bytes"])
        self.assertAlmostEqual(first["remaining_bytes"] / quota.GIB, 295.05, places=5)
        self.assertEqual(first["cycle_start"], "2026-10-16")
        self.assertEqual(first["next_reset"], "2026-11-16")

        later_time = NOW + timedelta(minutes=15)
        later = iface(created=date(2026, 11, 6), now=later_time)
        later["traffic"]["total"]["tx"] += 2 * quota.GIB
        later["traffic"]["total"]["rx"] += quota.GIB // 2
        later["updated"]["time"] = {"hour": later_time.hour, "minute": later_time.minute - 1}
        # Old 10/16 history is still present but MUST NOT be counted twice.
        actual = quota.collect(
            config, now=later_time, reader=lambda _: later, calibration=base,
        )
        self.assertTrue(actual["complete"])
        self.assertEqual(actual["cycle"]["tx"], 2 * quota.GIB)
        self.assertEqual(actual["cycle"]["rx"], quota.GIB // 2)
        self.assertAlmostEqual(actual["used_bytes"] / quota.GIB, 207.45, places=5)
        self.assertAlmostEqual(actual["remaining_bytes"] / quota.GIB, 292.55, places=5)
        self.assertIn("人工校准", quota.alert_text(actual, 80))
        self.assertIn("校准后新增上传", quota.alert_text(actual, 80))

    def test_same_cycle_reinstall_keeps_original_anchor(self):
        config = {"interfaces": ["eth0"], "reset_day": 16}
        first = quota.create_calibration(
            config, "204.95", "2026-10-16", "2026-11-06",
            now=NOW, reader=lambda _: iface(created=date(2026, 11, 6)),
        )
        # Even on a later day, reinstallation cannot reset the counter baseline.
        restored = quota.create_calibration(
            config, "204.95", "2026-10-16", "2026-11-06",
            now=NOW + timedelta(days=2), previous=first,
            reader=lambda _: self.fail("Reinstall must not recapture counters"),
        )
        self.assertIs(first, restored)
        with self.assertRaises(quota.MonitoringError):
            quota.create_calibration(config, "205", "2026-10-16", "2026-11-06",
                                     now=NOW, previous=first)
        with self.assertRaises(quota.MonitoringError):
            quota.create_calibration(config, "204.95", "2026-10-16", "2026-11-05",
                                     now=NOW, reader=lambda _: iface())
        with self.assertRaises(quota.MonitoringError):
            quota.create_calibration(config, "204.95", "2026-11-16", "2026-11-06",
                                     now=NOW, reader=lambda _: iface())

    def test_new_billing_cycle_excludes_previous_204_95_calibration(self):
        config = {"interfaces": ["eth0"], "reset_day": 16}
        base = quota.create_calibration(
            config, "204.95", "2026-10-16", "2026-11-06",
            now=NOW, reader=lambda _: iface(),
        )
        next_time = datetime(2026, 11, 16, 0, 20, tzinfo=TZ)
        new_iface = iface(now=NOW)
        new_iface["updated"] = {
            "date": day(next_time.date()),
            "time": {"hour": 0, "minute": 15},
        }
        new_iface["traffic"]["day"].append({
            "date": day(next_time.date()), "tx": quota.GIB, "rx": quota.GIB // 2,
        })
        snapshot = quota.collect(config, now=next_time,
                                 reader=lambda _: new_iface, calibration=base)
        self.assertFalse(snapshot["calibrated"])
        self.assertEqual(snapshot["cycle_start"], "2026-11-16")
        self.assertEqual(snapshot["used_bytes"], quota.GIB + quota.GIB // 2)
        self.assertAlmostEqual(snapshot["remaining_bytes"] / quota.GIB, 498.5)

    def test_counter_rollback_prevents_calibrated_quota_alert(self):
        config = {"interfaces": ["eth0"], "reset_day": 16}
        earlier = iface()
        base = quota.create_calibration(config, "204.95", "2026-10-16",
                                        "2026-11-06", now=NOW, reader=lambda _: earlier)
        modified = iface()
        modified["traffic"]["total"]["tx"] = 0
        result = quota.collect(config, now=NOW, reader=lambda _: modified, calibration=base)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["remaining_bytes"])
        with mock.patch.object(quota, "write_json") as write:
            self.assertIsNone(quota.check_alerts(result, sender=mock.Mock()))
            write.assert_not_called()

    def test_calibration_next_day_gap_disables_alerts_without_repeating_old_days(self):
        config = {"interfaces": ["eth0"], "reset_day": 16}
        base = quota.create_calibration(config, "204.95", "2026-10-16",
                                        "2026-11-06", now=NOW, reader=lambda _: iface())
        later = iface(now=NOW + timedelta(days=2))
        later["updated"] = {"date": day(date(2026, 11, 8)),
                            "time": {"hour": 12, "minute": 35}}
        later["traffic"]["day"] = [
            item for item in later["traffic"]["day"]
            if item["date"] != day(date(2026, 11, 7))
        ]
        result = quota.collect(config, now=NOW + timedelta(days=2),
                               reader=lambda _: later, calibration=base)
        self.assertFalse(result["complete"])
        self.assertIn("校准后", result["note"])

    def test_stale_data_disables_quota(self):
        old = iface()
        old["updated"] = {"date": day(date(2026, 11, 5)), "time": {"hour": 8, "minute": 0}}
        result = quota.collect(
            {"interfaces": ["eth0"], "allowance_gib": 500},
            now=NOW, reader=lambda _: old,
        )
        self.assertFalse(result["complete"])
        self.assertIn("不及时", result["note"])

    def test_multiple_default_route_ifaces_aggregate_once(self):
        def reader(name):
            return dict(iface(), name=name)
        q = quota.collect(
            {"interfaces": ["eth0", "ens4"], "allowance_gib": 500},
            now=NOW, reader=reader,
        )
        self.assertTrue(q["complete"])
        self.assertEqual(q["used_bytes"], 14 * quota.GIB)
        with self.assertRaises(quota.MonitoringError):
            quota.collect({"interfaces": ["eth0", "eth0"]}, now=NOW, reader=reader)

    def test_alert_only_once_per_threshold_per_cycle(self):
        sample = quota.collect(
            {"interfaces": ["eth0"], "allowance_gib": 500},
            now=NOW, reader=lambda _: iface(current_gib=449),
        )
        self.assertEqual(sample["percent"], 90.6)
        saved = {}
        def reader_state():
            return dict(saved)
        def writer(path, obj, mode):
            saved.clear()
            saved.update(obj)
        sender = mock.Mock()
        with mock.patch.object(quota, "load_state", side_effect=reader_state), \
                mock.patch.object(quota, "write_json", side_effect=writer):
            self.assertEqual(quota.check_alerts(sample, sender=sender), 90)
            self.assertEqual(saved["notified"], [80, 90])
            self.assertIsNone(quota.check_alerts(sample, sender=sender))
            self.assertEqual(sender.call_count, 1)
            higher = dict(sample, percent=95.5)
            self.assertEqual(quota.check_alerts(higher, sender=sender), 95)
            self.assertEqual(sender.call_count, 2)
            next_cycle = dict(higher, cycle_start="2026-11-16")
            self.assertEqual(quota.check_alerts(next_cycle, sender=sender), 95)
            self.assertEqual(sender.call_count, 3)

    def test_failed_alert_sending_does_not_mark_as_sent(self):
        snapshot = quota.collect(
            {"interfaces": ["eth0"], "allowance_gib": 500},
            now=NOW, reader=lambda _: iface(current_gib=449),
        )
        with mock.patch.object(quota, "load_state", return_value={}), \
                mock.patch.object(quota, "write_json") as write:
            with self.assertRaises(RuntimeError):
                quota.check_alerts(snapshot, sender=mock.Mock(side_effect=RuntimeError("network down")))
            write.assert_not_called()

    def test_invalid_vnstat_data_and_ifaces_fail_closed(self):
        with self.assertRaises(quota.MonitoringError):
            quota.traffic_entry({"tx": -1, "rx": 3})
        with self.assertRaises(quota.MonitoringError):
            quota.read_interface("eth0;bad")
        with self.assertRaises(ValueError):
            quota.local_cycle(date(2026, 11, 6), 31)

    def test_missing_day_disables_quota_even_if_monitor_created_before_cycle(self):
        item = iface()
        item["traffic"]["day"] = [
            d for d in item["traffic"]["day"]
            if d["date"] != day(date(2026, 10, 25))
        ]
        snapshot = quota.collect(
            {"interfaces": ["eth0"], "allowance_gib": 500},
            now=NOW, reader=lambda _: item,
        )
        self.assertFalse(snapshot["complete"])
        self.assertIn("缺少 1 天", snapshot["note"])
        self.assertIsNone(snapshot["remaining_bytes"])

    def test_midcycle_future_date_does_not_count(self):
        item = iface()
        item["traffic"]["day"].append({
            "date": day(date(2026, 11, 12)), "tx": 30*quota.GIB, "rx": 0
        })
        sample = quota.collect({"interfaces": ["eth0"]}, now=NOW, reader=lambda _: item)
        self.assertEqual(sample["used_bytes"], 7 * quota.GIB)


if __name__ == "__main__":
    unittest.main()
