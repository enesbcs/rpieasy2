from __future__ import annotations

import asyncio
import logging
import os
import subprocess

logger = logging.getLogger("rpieasy2.util")


def get_wifi_rssi() -> int | None:
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                raw = parts[3].rstrip(".")
                if raw == parts[3]:
                    continue
                try:
                    return int(raw)
                except ValueError:
                    continue
    except OSError:
        pass
    return None


def get_wifi_ssid() -> str:
    import subprocess
    try:
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


async def get_wifi_ssid_async() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "iwgetid", "-r",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception:
        pass
    return ""


def get_wifi_channel() -> str:
    try:
        r = subprocess.run(["iwgetid", "-c"], capture_output=True, text=True, timeout=2)
        ch = r.stdout.strip()
        if ch:
            return ch
    except Exception:
        pass
    try:
        for iface in os.listdir("/sys/class/net"):
            if iface == "lo":
                continue
            if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
                r = subprocess.run(["iw", "dev", iface, "info"],
                                   capture_output=True, text=True, timeout=2)
                for line in r.stdout.splitlines():
                    ls = line.strip()
                    if ls.startswith("channel"):
                        return ls.split()[1]
    except Exception:
        pass
    return ""


def get_wifi_bssid() -> str:
    try:
        for iface in os.listdir("/sys/class/net"):
            if iface == "lo":
                continue
            if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
                r = subprocess.run(["iw", "dev", iface, "link"],
                                   capture_output=True, text=True, timeout=2)
                for line in r.stdout.splitlines():
                    ls = line.strip()
                    if "Connected to" in ls:
                        return ls.split("Connected to")[1].strip()
                    if ls.startswith(":"):
                        parts = ls.split()
                        return parts[0] if parts else ""
    except Exception:
        pass
    return ""
