from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUMMY, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p101")


class P101WakeOnLan(PluginBase):
    PLUGIN_ID = 101
    PLUGIN_NAME = "Communication - Wake On LAN"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUMMY,
        vtype=SENSOR_TYPE_NONE,
        value_count=0,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._mac: str = "00:00:00:00:00:00"
        self._ip: str = "255.255.255.255"
        self._port: int = 9

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._mac = self._config.get("mac", "00:00:00:00:00:00")
        self._ip = self._config.get("ip", "255.255.255.255")
        self._port = int(self._config.get("port") or 9)
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("mac", "00:00:00:00:00:00")
        self._config.setdefault("ip", "255.255.255.255")
        self._config.setdefault("port", 9)
        return True

    def _parse_mac(self, mac: str) -> bytes | None:
        try:
            mac = mac.replace("-", ":").strip()
            parts = mac.split(":")
            if len(parts) != 6:
                return None
            return bytes(int(p, 16) for p in parts)
        except ValueError:
            return None

    def _send_wol(self, mac: str, ip: str, port: int) -> bool:
        mac_bytes = self._parse_mac(mac)
        if not mac_bytes:
            return False
        magic = b"\xff" * 6 + mac_bytes * 16
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(magic, (ip, port))
            return True
        except Exception as e:
            logger.error("WOL send failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = [p.strip() for p in command.split(",")]
        if parts[0] in ("wakeonlan", (self._config.get("task_name", "") or "").lower()):
            mac = parts[1] if len(parts) > 1 and parts[1] else self._mac
            ip = parts[2] if len(parts) > 2 and parts[2] else self._ip
            port = int(parts[3]) if len(parts) > 3 and parts[3] else self._port
            success = self._send_wol(mac, ip, port)
            if success:
                logger.info("WOL sent to %s via %s:%d", mac, ip, port)
            return success
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "mac", "label": "MAC Address", "type": "text", "value": self._config.get("mac", "00:00:00:00:00:00"), "placeholder": "00:00:00:00:00:00"},
            {"name": "ip", "label": "IPv4 Address", "type": "text", "value": self._config.get("ip", "255.255.255.255"), "placeholder": "255.255.255.255"},
            {"name": "port", "label": "UDP Port", "type": "number", "value": self._config.get("port", 9)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._mac = self._config.get("mac", "00:00:00:00:00:00")
        self._ip = self._config.get("ip", "255.255.255.255")
        self._port = int(self._config.get("port") or 9)
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}
