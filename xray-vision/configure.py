#!/usr/bin/env python3
"""Render independent VLESS+TCP+REALITY+Vision config and private client artifacts."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import urllib.parse
import uuid

NAMES = ("WIN-PC", "MACBOOK", "IPHONE", "FAMILY")


def valid_host(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return (
            3 <= len(host) <= 253
            and "." in host
            and all(
                0 < len(part) <= 63
                and part[0].isalnum()
                and part[-1].isalnum()
                and all(c.isascii() and (c.isalnum() or c == "-") for c in part)
                for part in host.split(".")
            )
        )


def render(host, sni, port, private, public, sid, users, output):
    host = host.strip().strip("[]")
    sni = sni.strip().lower()
    if not valid_host(host) or not valid_host(sni):
        raise ValueError("Invalid host or REALITY SNI")
    if host.lower() == sni:
        raise ValueError("REALITY SNI cannot be the same as the proxy endpoint")
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid TCP port")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,44}", private):
        raise ValueError("Invalid REALITY private key")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,44}", public):
        raise ValueError("Invalid REALITY public key")
    if not re.fullmatch(r"(?:[a-fA-F0-9]{2}){1,8}", sid):
        raise ValueError("Invalid REALITY short ID")
    parsed = [str(uuid.UUID(user)) for user in users]
    if len(parsed) != len(NAMES) or len(set(parsed)) != len(NAMES):
        raise ValueError("Exactly four unique UUIDs are required")
    output.mkdir(parents=True, exist_ok=True)
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "vision-tcp",
            "listen": "0.0.0.0",
            "port": port,
            "protocol": "vless",
            "settings": {
                "clients": [
                    {"id": user, "email": name, "flow": "xtls-rprx-vision"}
                    for name, user in zip(NAMES, parsed)
                ],
                "decryption": "none",
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "target": f"{sni}:443",
                    "serverNames": [sni],
                    "privateKey": private,
                    "shortIds": [sid],
                },
            },
            "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True},
        }],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }
    (output / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    endpoint = f"[{host}]" if ":" in host else host
    for name, user in zip(NAMES, parsed):
        folder = output / "clients" / name
        folder.mkdir(parents=True, exist_ok=True)
        params = {
            "encryption": "none",
            "type": "tcp",
            "security": "reality",
            "flow": "xtls-rprx-vision",
            "sni": sni,
            "fp": "chrome",
            "pbk": public,
            "sid": sid,
            "spx": "/",
        }
        uri = (
            f"vless://{user}@{endpoint}:{port}?"
            f"{urllib.parse.urlencode(params)}#VISION-{name}"
        )
        loon = (
            f"VISION-{name} = VLESS,{host},{port},\"{user}\","
            "transport=tcp,flow=xtls-rprx-vision,"
            f"public-key=\"{public}\",short-id={sid},"
            f"over-tls=true,sni={sni},tls-profile=chrome,udp=true"
        )
        (folder / "node.txt").write_text(uri + "\n", encoding="utf-8")
        (folder / "loon.txt").write_text(loon + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--sni", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(
        args.host,
        args.sni,
        args.port,
        os.environ["VISION_PRIVATE"],
        os.environ["VISION_PUBLIC"],
        os.environ["VISION_SID"],
        os.environ["VISION_UUIDS"].split(","),
        args.out,
    )
