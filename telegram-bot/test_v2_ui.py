#!/usr/bin/env python3
"""V2 offline UI tests: permissions, routing, credentials, nonce safety, styles."""
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "CI-TEST-BOT-TOKEN")
os.environ.setdefault("ADMIN_USER_ID", "7123456")
os.environ.setdefault("HY2_STATS_SECRET", "CI-TEST-LOCAL-STATS")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bot
import v2_ui as ui


class Backend:
    XRAY_BIN = "/usr/local/bin/xray"
    def __init__(self):
        self.calls = []
        self.messages = []
        self.photos = []
        self._seq = 10
        self.managed = {
            "hy2": ["WIN-PC", "MACBOOK", "IPHONE", "FAMILY", "IPAD"],
            "tuic": ["WIN-PC", "MACBOOK", "IPHONE", "FAMILY"],
        }

    @staticmethod
    def authorized(user, chat):
        return user.get("id") == 7123456 and chat.get("id") == 7123456 and chat.get("type") == "private"

    def telegram(self, method, payload=None, timeout=35):
        self.calls.append((method, payload))
        if method == "sendMessage":
            self._seq += 1
            self.messages.append(payload)
            return {"message_id": self._seq}
        if method == "editMessageText":
            self.messages.append(payload)
            return {"message_id": payload["message_id"]}
        if method == "answerCallbackQuery":
            return True
        raise AssertionError("Network action not mocked: " + method)

    def send_message(self, chat, text, reply_markup=None):
        p = {"chat_id": chat, "text": text, "reply_markup": reply_markup}
        self.messages.append(p)
        self._seq += 1
        return {"message_id": self._seq}

    def send_photo(self, chat_id, path, caption=""):
        self.photos.append((chat_id, path, caption))
        return {"message_id": 999}

    def run_manager(self, *args):
        if args[0] == "list":
            return {"clients": [{"name": n} for n in self.managed["hy2"]]}
        if args[0] == "get":
            if args[1] not in self.managed["hy2"]:
                raise RuntimeError("missing")
            return {"name": args[1], "uri": "hysteria2://testnode", "qr_path": "/fake/hy2.png"}
        if args[0] == "add":
            self.managed["hy2"].append(args[1])
            return {"name": args[1], "uri": "hysteria2://new", "qr_path": "/fake/new.png"}
        if args[0] == "delete":
            self.managed["hy2"].remove(args[1])
            return {"deleted": args[1]}
        raise AssertionError("unexpected " + repr(args))

    @staticmethod
    def read_meminfo():
        return {"MemTotal": 1073741824, "MemAvailable": 600000000, "SwapTotal": 0, "SwapFree": 0}

    @staticmethod
    def mb(n): return n / (1024*1024)
    @staticmethod
    def fmt_bytes(n): return str(n) + " B"
    @staticmethod
    def uptime_text(): return "5天 2小时"
    @staticmethod
    def hy2_version(): return "v2.13.0"
    @staticmethod
    def online_totals(): return (3, 2)
    @staticmethod
    def traffic_totals(): return (1000, 2000)
    @staticmethod
    def online_data(): return {"iphone": 2, "macbook": 1}
    @staticmethod
    def traffic_data(): return {"iphone": {"tx": 1000, "rx": 1500}}
    @staticmethod
    def vision_names(): return ["IPHONE"]
    @staticmethod
    def xray_names(): return ["IPHONE"]
    @staticmethod
    def vision_client(name):
        if name != "IPHONE": raise ValueError("missing Vision device")
        return {
            "name": name, "uri": "vless://vision@node:8443",
            "qr_path": "/fake/vision.png",
            "loon": "VISION-IPHONE = VLESS,example.com,8443,tls-profile=chrome147",
        }
    @staticmethod
    def xray_client(name):
        if name != "IPHONE": raise ValueError("missing XHTTP device")
        return {"name": name, "uri": "vless://xhttp@node:443", "qr_path": "/fake/xhttp.png"}


def message(command, uid=7123456, chat_id=7123456, chat_type="private"):
    return {"text": command, "from": {"id": uid},
            "chat": {"id": chat_id, "type": chat_type}}


def query(data, mid, uid=7123456, chat_id=7123456, chat_type="private"):
    return {
        "id": "callback-test",
        "data": data,
        "from": {"id": uid},
        "message": {"chat": {"id": chat_id, "type": chat_type}, "message_id": mid},
    }


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.b = Backend()
        ui.PENDING.clear()
        self.addCleanup(ui.PENDING.clear)
        self.state_patch = mock.patch.object(ui, "service_state", return_value="active")
        self.listener_patch = mock.patch.object(ui, "listener", return_value=True)
        self.state_patch.start()
        self.listener_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.listener_patch.stop)
        self.tuic_calls = []

    def tuic(self, *args):
        self.tuic_calls.append(args)
        if args[0] == "list":
            return {"clients": [{"name": n} for n in self.b.managed["tuic"]]}
        if args[0] == "get":
            if args[1] not in self.b.managed["tuic"]:
                raise ValueError("missing")
            return {"name": args[1], "uri": "tuic://test", "qr_path": "/fake/tuic.png"}
        if args[0] == "restart":
            return {"ok": True}
        if args[0] == "delete":
            self.b.managed["tuic"].remove(args[1])
            return {"deleted": args[1]}
        if args[0] == "add":
            self.b.managed["tuic"].append(args[1])
            return {"name": args[1], "uri": "tuic://new", "qr_path": "/fake/tuic2.png"}
        raise AssertionError("unexpected " + repr(args))

    def test_nine_commands_and_free_buttons(self):
        self.assertEqual(len(ui.COMMANDS), 9)
        self.assertEqual(len({x["command"] for x in ui.COMMANDS}), 9)
        self.assertEqual({x["command"] for x in ui.COMMANDS},
                         {"start", "status", "help", "devices", "get", "add", "del", "restart", "update"})
        ui.handle_message(message("/start"), self.b, self.tuic)
        p = self.b.messages[0]
        self.assertIn("✦ CHBVPS", p["text"])
        for row in p["reply_markup"]["inline_keyboard"]:
            for item in row:
                self.assertNotIn("icon_custom_emoji_id", item)
                self.assertLessEqual(len(item["callback_data"].encode()), 64)
                if "style" in item: self.assertIn(item["style"], ("primary", "success", "danger"))
        self.assertEqual(len(p["reply_markup"]["inline_keyboard"]), 3)

    def test_help_and_status_are_distinct(self):
        ui.handle_message(message("/help"), self.b)
        ui.handle_message(message("/status"), self.b)
        self.assertIn("/get [设备] [协议]", self.b.messages[0]["text"])
        self.assertIn("整机流量", self.b.messages[1]["text"])
        self.assertIn("500GB / 每月16日重置", self.b.messages[1]["text"])

    def test_status_shows_complete_whole_vps_quota(self):
        snapshot = {
            "schema": 1, "complete": True,
            "allowance_gib": 500, "cycle_start": "2026-10-16",
            "next_reset": "2026-11-16", "interfaces": ["eth0"],
            "today": {"tx": 1*ui.GIB, "rx": 2*ui.GIB},
            "cycle": {"tx": 200*ui.GIB, "rx": 20*ui.GIB},
            "lifetime": {"tx": 300*ui.GIB, "rx": 90*ui.GIB},
            "used_bytes": 220*ui.GIB, "remaining_bytes": 280*ui.GIB,
            "percent": 44.0, "sampled_local": "2026-11-06T12:30+08:00",
        }
        with mock.patch.object(ui, "read_quota_snapshot", return_value=snapshot):
            ui.handle_message(message("/start"), self.b, self.tuic)
            self.assertIn("220.00 GB / 500 GB", self.b.messages[-1]["text"])
            ui.handle_message(message("/status"), self.b, self.tuic)
        body = self.b.messages[-1]["text"]
        self.assertIn("剩余额度（估算）  280.00 GB", body)
        self.assertIn("↑ 上传  200.00 GB", body)
        self.assertIn("↓ 下载  20.00 GB", body)
        self.assertIn("整机网卡流量", body)

    def test_204_95_manual_calibration_hides_incomplete_banner_and_keeps_balance(self):
        snapshot = {
            "schema": 1, "complete": True, "calibrated": True,
            "allowance_gib": 500, "cycle_start": "2026-10-16",
            "next_reset": "2026-11-16", "interfaces": ["eth0"],
            "today": {"tx": 0, "rx": 0},
            "cycle": {"tx": 0, "rx": 0},
            "baseline_bytes": int(204.95 * ui.GIB),
            "lifetime": {"tx": 0, "rx": 0},
            "used_bytes": int(204.95 * ui.GIB),
            "remaining_bytes": int(295.05 * ui.GIB),
            "percent": 40.99,
            "sampled_local": "2026-11-06T14:17+08:00",
            "calibrated_at": "2026-11-06T14:17+08:00",
        }
        with mock.patch.object(ui, "read_quota_snapshot", return_value=snapshot):
            ui.handle_message(message("/start"), self.b, self.tuic)
            self.assertIn("204.95 GB / 500 GB", self.b.messages[-1]["text"])
            self.assertIn("人工校准", self.b.messages[-1]["text"])
            ui.handle_message(message("/status"), self.b, self.tuic)
        text = self.b.messages[-1]["text"]
        self.assertIn("◈ 人工校准起点  204.95 GB", text)
        self.assertIn("剩余额度（估算）  295.05 GB", text)
        self.assertIn("Σ 本期合计已用  <b>204.95 GB</b>", text)
        self.assertNotIn("统计不完整", text)

    def test_status_incomplete_never_invents_remaining_quota(self):
        snapshot = {
            "schema": 1, "complete": False, "interfaces": ["eth0"],
            "cycle_start": "2026-10-16", "next_reset": "2026-11-16",
            "today": {"tx": 100, "rx": 100}, "cycle": {"tx": 200, "rx": 300},
            "lifetime": {"tx": 500, "rx": 1000}, "used_bytes": 500,
            "sampled_local": "2026-11-06T12:40+08:00",
            "note": "eth0 的监控始于 2026-11-06",
        }
        with mock.patch.object(ui, "read_quota_snapshot", return_value=snapshot):
            ui.handle_message(message("/status"), self.b, self.tuic)
        body = self.b.messages[-1]["text"]
        self.assertIn("本周期统计不完整", body)
        self.assertNotIn("剩余额度（估算）", body)
        self.assertIn("暂停剩余额度及比例估算", body)

    def test_get_all_links_and_qr_on_demand(self):
        ui.handle_message(message("/get IPHONE"), self.b, self.tuic)
        text = self.b.messages[0]["text"]
        for uri in ("hysteria2://testnode", "tuic://test", "vless://vision", "vless://xhttp"):
            self.assertIn(uri, text)
        self.assertFalse(self.b.photos)
        self.assertTrue(ui.handle_callback(query("ui:qr:IPHONE:vision", self.b._seq), self.b, self.tuic))
        self.assertEqual(self.b.photos[-1][1], "/fake/vision.png")
        ui.handle_callback(query("ui:cfg:IPHONE:vision", self.b._seq), self.b, self.tuic)
        self.assertIn("chrome147", self.b.messages[-1]["text"])

    def test_get_single_protocol_does_not_create_missing_accounts(self):
        ui.handle_message(message("/get IPHONE vision"), self.b, self.tuic)
        text = self.b.messages[-1]["text"]
        self.assertIn("vless://vision", text)
        self.assertNotIn("hysteria2://", text)
        self.assertFalse(self.tuic_calls)

    def test_admin_private_authorization(self):
        for invalid in (
            message("/get IPHONE", uid=99),
            message("/get IPHONE", chat_id=888),
            message("/get IPHONE", chat_type="group"),
        ):
            ui.handle_message(invalid, self.b, self.tuic)
        self.assertFalse(self.b.messages)
        ui.handle_callback(query("ui:get:IPHONE:all", 14, uid=99), self.b, self.tuic)
        self.assertEqual(self.b.calls[-1][0], "answerCallbackQuery")
        self.assertTrue(self.b.calls[-1][1]["show_alert"])

    def test_devices_hy2_current_cycle_and_no_tuic_online(self):
        ui.handle_message(message("/devices hy2"), self.b, self.tuic)
        self.assertIn("流量统一在 /status", self.b.messages[-1]["text"])
        ui.handle_message(message("/devices tuic"), self.b, self.tuic)
        self.assertIn("不提供此协议的可靠在线人数", self.b.messages[-1]["text"])

    def test_confirm_nonce_one_time_and_message_bound(self):
        ui.handle_message(message("/del IPAD hy2"), self.b, self.tuic)
        first = self.b.messages[-1]
        confirm = first["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        token = confirm.split(":")[-1]
        self.assertIn(token, ui.PENDING)
        old_mid = self.b._seq
        with mock.patch.object(ui, "async_action") as started:
            ui.handle_callback(query(confirm, old_mid + 1), self.b, self.tuic)
            self.assertEqual(started.call_count, 0)
            ui.handle_callback(query(confirm, old_mid), self.b, self.tuic)
            self.assertEqual(started.call_count, 1)
            ui.handle_callback(query(confirm, old_mid), self.b, self.tuic)
            self.assertEqual(started.call_count, 1)
        self.assertNotIn(token, ui.PENDING)

    def test_confirmation_expiration_and_no_execute(self):
        ui.handle_message(message("/restart all"), self.b, self.tuic)
        btn = self.b.messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        tok = btn.split(":")[-1]
        ui.PENDING[tok]["expires"] = time.monotonic() - 1
        with mock.patch.object(ui, "async_action") as action:
            ui.handle_callback(query(btn, self.b._seq), self.b, self.tuic)
            action.assert_not_called()

    def test_restart_allowlist_not_arbitrary(self):
        self.assertEqual(ui.SERVICES["vision"][0], "xray-vision.service")
        with mock.patch.object(ui, "sudo_run") as privileged:
            ui.restart_one("xhttp", self.tuic)
            privileged.assert_called_once_with(
                ["/usr/bin/systemctl", "restart", "xray.service"]
            )
        with self.assertRaises(ValueError):
            ui.restart_one("sshd", self.tuic)

    def test_update_command_never_auto_upgrades(self):
        ui.handle_message(message("/update"), self.b, self.tuic)
        self.assertIn("Xray 暂不自动升级", self.b.messages[-1]["text"])
        self.assertFalse(any("core-update" in repr(x) for x in self.b.calls))

    def test_all_provisioning_entrypoints_ship_v2_dependency(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        for path in (
            "telegram-bot/install.sh",
            "telegram-bot/upgrade-devices.sh",
            "tuic/install.sh",
            "xray-xhttp/install.sh",
            "xray-vision/install.sh",
            "telegram-bot/upgrade-v2.sh",
        ):
            script = (root / path).read_text(encoding="utf-8")
            self.assertIn("v2_ui.py", script, "Missing V2 runtime dependency: " + path)

    def test_v2_upgrader_does_not_restart_proxy_services(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        script = (root / "telegram-bot/upgrade-v2.sh").read_text(encoding="utf-8")
        self.assertIn('systemctl restart "$SERVICE"', script)
        # The installer intentionally contains sudoers entries permitting
        # later user-confirmed restarts; those are not executable restarts.
        executable = [
            line.strip() for line in script.splitlines()
            if line.strip().startswith("systemctl restart ")
        ]
        self.assertEqual(executable, ['systemctl restart "$SERVICE" || true',
                                      'systemctl restart "$SERVICE" || fail "Telegram 重启失败。"'])
        self.assertIn("visudo -cf", script)
        self.assertIn("chbvps-bot-backups", script)

    def test_token_validation_limits_callback_size(self):
        with self.assertRaises(ValueError):
            ui.button("malicious", "a"*65)

    def test_dual_protocol_commands_are_handled_by_shared_ui(self):
        import dual_bot
        with mock.patch.object(ui, "handle_message", return_value=True) as handler:
            dual_bot.handle_message(message("/start"))
            self.assertEqual(handler.call_count, 1)
            self.assertIs(handler.call_args.kwargs["tuic"], dual_bot.manage)


if __name__ == "__main__":
    unittest.main()
