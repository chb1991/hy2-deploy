#!/usr/bin/env python3
"""Offline integration checks for Vision Telegram routing (no real bot token/API)."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["BOT_TOKEN"] = "TEST_TOKEN_NEVER_SEND"
os.environ["ADMIN_USER_ID"] = "7123456"
os.environ["HY2_STATS_SECRET"] = "TEST_STATS_SECRET_NEVER_SEND"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bot

original_handler = bot.handle_message
import dual_bot


class VisionTelegramTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.old_dir = bot.VISION_CLIENTS_DIR
        self.addCleanup(setattr, bot, "VISION_CLIENTS_DIR", self.old_dir)
        bot.VISION_CLIENTS_DIR = self.t.name
        for name in ("WIN-PC", "MACBOOK", "IPHONE", "FAMILY"):
            folder = pathlib.Path(self.t.name) / name
            folder.mkdir()
            (folder / "node.txt").write_text(
                "vless://fake-uuid@proxy.example.com:8443?type=tcp&security=reality&flow=xtls-rprx-vision#VISION-" + name + "\n"
            )
            (folder / "loon.txt").write_text("VISION-" + name + " = VLESS,proxy.example.com,8443,\"test-uuid\",transport=tcp\n")
            (folder / "node-qr.png").write_bytes(b"fake png for transport-mocked test")

    @staticmethod
    def message(command, user_id=7123456, chat_id=7123456, chat_type="private"):
        return {
            "text": command,
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
        }

    def test_metadata_and_four_accounts(self):
        self.assertEqual(bot.vision_names(), ["FAMILY", "IPHONE", "MACBOOK", "WIN-PC"])
        self.assertEqual(bot.vision_client("iphone")["name"], "IPHONE")
        with self.assertRaises(ValueError):
            bot.vision_client("../../etc/passwd")

    def test_base_bot_vget_sends_uri_and_qr(self):
        with patch.object(bot, "send_message") as msg, patch.object(bot, "send_photo") as photo:
            original_handler(self.message("/vget IPHONE"))
            self.assertEqual(msg.call_count, 1)
            self.assertEqual(photo.call_count, 1)
            content = msg.call_args.args[1]
            self.assertIn("vless://", content)
            self.assertIn("Loon 专用配置行", content)
            self.assertIn("8443", content)

    def test_all_four_accounts_are_sent(self):
        with patch.object(bot, "send_message") as msg, patch.object(bot, "send_photo") as photo:
            original_handler(self.message("/vall"))
            self.assertEqual(msg.call_count, 4)
            self.assertEqual(photo.call_count, 4)

    def test_only_authorized_private_chat_can_retrieve_credentials(self):
        with patch.object(bot, "send_message") as msg, patch.object(bot, "send_photo") as photo:
            original_handler(self.message("/vget IPHONE", user_id=9))
            original_handler(self.message("/vget IPHONE", chat_id=123))
            original_handler(self.message("/vget IPHONE", chat_type="group"))
            self.assertEqual(msg.call_count, 0)
            self.assertEqual(photo.call_count, 0)

    def test_invalid_and_unknown_devices_do_not_send_credentials(self):
        with patch.object(bot, "send_message") as msg, patch.object(bot, "send_photo") as photo:
            original_handler(self.message("/vget ../../etc"))
            original_handler(self.message("/vget NOT-FOUND"))
            self.assertEqual(msg.call_count, 2)
            self.assertEqual(photo.call_count, 0)
            for call in msg.call_args_list:
                self.assertIn("操作失败", call.args[1])

    def test_dual_tuic_bot_forwards_vision_commands(self):
        with patch.object(bot, "send_message") as msg, patch.object(bot, "send_photo") as photo:
            dual_bot.handle_message(self.message("/vget MACBOOK"))
            self.assertEqual(msg.call_count, 1)
            self.assertEqual(photo.call_count, 1)
            self.assertIn("VISION-MACBOOK", msg.call_args.args[1])

    def test_vision_status_and_help(self):
        with patch.object(bot, "vision_service_state", return_value="active"):
            with patch.object(bot, "send_message") as msg:
                original_handler(self.message("/vstatus"))
                self.assertEqual(msg.call_count, 1)
                self.assertIn("active", msg.call_args.args[1])
        self.assertIn("/vget IPHONE", bot.help_text())
        # V2 hides legacy commands from the visible menu while retaining handling.
        self.assertNotIn("/vget", ["/" + command["command"] for command in bot.COMMANDS])
        self.assertIn("/get", ["/" + command["command"] for command in bot.COMMANDS])


if __name__ == "__main__":
    unittest.main()
