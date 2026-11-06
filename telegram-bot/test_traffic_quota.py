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
    return {
        "name": "eth0",
        "created": {"date": day(created)},
        "updated": {"date": day(now.date()), "time": {"hour": 12, "minute": 35}},
        "traffic": {
            "total": {"tx": 5 * quota.GIB, "rx": 2 * quota.GIB},
            "day": [
                {"date": day(date(2026, 10, 15)), "tx": 55 * quota.GIB, "rx": 1},
                {"date": day(date(2026, 10, 16)), "tx": current_gib * quota.GIB, "rx": quota.GIB},
                {"date": day(date(2026, 11, 6)), "tx": 2 * quota.GIB, "rx": quota.GIB},
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

    def test_midcycle_future_date_does_not_count(self):
        item = iface()
        item["traffic"]["day"].append({
            "date": day(date(2026, 11, 12)), "tx": 30*quota.GIB, "rx": 0
        })
        sample = quota.collect({"interfaces": ["eth0"]}, now=NOW, reader=lambda _: item)
        self.assertEqual(sample["used_bytes"], 7 * quota.GIB)


if __name__ == "__main__":
    unittest.main()
