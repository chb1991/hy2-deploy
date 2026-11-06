#!/usr/bin/env python3
"""Offline tests: refresh Vision Loon fingerprint without changing credentials."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

MODULE = Path(__file__).with_name("refresh-loon.py")
spec = importlib.util.spec_from_file_location("vision_refresh", MODULE)
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class RefreshFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        base = Path(self.t.name)
        self.config = base / "vision.json"
        self.clients = base / "clients"
        self.clients.mkdir()
        self.backups = base / "backups"
        self.client_ids = {name: str(uuid.uuid4()) for name in refresh.DEVICES}
        server = {
            "inbounds": [{
                "protocol": "vless",
                "port": 8443,
                "settings": {"clients": [
                    {"email": name, "id": uid, "flow": "xtls-rprx-vision"}
                    for name, uid in self.client_ids.items()
                ]},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {"serverNames": ["www.example.org"]},
                },
            }]
        }
        self.config.write_text(json.dumps(server))
        for name, uid in self.client_ids.items():
            folder = self.clients / name
            folder.mkdir()
            q = {
                "type": "tcp", "security": "reality",
                "flow": "xtls-rprx-vision", "sni": "www.example.org",
                "fp": "chrome", "pbk": "publickey",
                "sid": "aabbccdd",
            }
            (folder / "node.txt").write_text(
                f"vless://{uid}@proxy.example.com:8443?{urlencode(q)}#VISION-{name}\n"
            )
            (folder / "loon.txt").write_text(
                f'VISION-{name} = VLESS,proxy.example.com,8443,"{uid}",'
                "transport=tcp,flow=xtls-rprx-vision,over-tls=true,"
                "sni=www.example.org,tls-profile=chrome,udp=true\n"
            )
            (folder / "node-qr.png").write_bytes(b"old-qr")
        self.patches = [
            mock.patch.object(refresh, "CONFIG", self.config),
            mock.patch.object(refresh, "CLIENTS", self.clients),
            mock.patch.object(refresh, "BACKUPS", self.backups),
            mock.patch.object(refresh.os, "geteuid", return_value=0),
            mock.patch.object(refresh.os, "fchown"),
            mock.patch.object(refresh.shutil, "which", return_value="/usr/bin/qrencode"),
            mock.patch.object(refresh.subprocess, "run", side_effect=self.fake_qrencode),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def fake_qrencode(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"qr-for-" + cmd[-1].encode())
        return mock.Mock(returncode=0)

    def test_refresh_link_qr_loon_and_no_uuid_rotation(self):
        refresh.main()
        for name in refresh.DEVICES:
            folder = self.clients / name
            uri = (folder / "node.txt").read_text().strip()
            q = parse_qs(urlsplit(uri).query)
            self.assertEqual(q["fp"], ["chrome147"])
            self.assertEqual(q["tls-profile"], ["chrome147"])
            self.assertEqual(urlsplit(uri).username, self.client_ids[name])
            self.assertIn("tls-profile=chrome147", (folder / "loon.txt").read_text())
            self.assertEqual((folder / "node-qr.png").read_bytes(), b"qr-for-" + uri.encode())
        self.assertEqual(json.loads(self.config.read_text())["inbounds"][0]["port"], 8443)
        self.assertTrue(list(self.backups.iterdir()))
        refresh.main()  # Idempotent; should not rotate existing accounts.

    def test_invalid_uuid_blocks_all_writes(self):
        path = self.clients / "IPHONE" / "node.txt"
        old = path.read_text().replace(self.client_ids["IPHONE"], str(uuid.uuid4()))
        path.write_text(old)
        with self.assertRaisesRegex(RuntimeError, "UUID"):
            refresh.main()
        self.assertIn("fp=chrome", (self.clients / "WIN-PC" / "node.txt").read_text())

    def test_refuses_non_root(self):
        with mock.patch.object(refresh.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(RuntimeError, "root"):
                refresh.main()


if __name__ == "__main__":
    unittest.main()
