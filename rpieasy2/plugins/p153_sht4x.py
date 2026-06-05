from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p153")

SHT4x_ADDRS = [0x44, 0x45, 0x46]
SHT4x_CMD_HIGH = 0xFD
SHT4x_CMD_MED = 0xF6
SHT4x_CMD_LOW = 0xE0


class P153SHT4x(PluginBase):
    PLUGIN_ID = 153
    PLUGIN_NAME = "Environment - SHT4x"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_TEMP_HUM,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    CONFIG_NAMES = [
        "Low resolution",
        "Medium resolution",
        "High resolution",
    ]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x44
        self._temp: float = 0.0
        self._hum: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x44)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x44)
        self._config.setdefault("temp_offset", "0")
        self._config.setdefault("startup_config", 2)
        self._config.setdefault("normal_config", 2)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            startup = int(self._config.get("startup_config") or 2)
            cmds = [SHT4x_CMD_LOW, SHT4x_CMD_MED, SHT4x_CMD_HIGH]
            cmd = cmds[min(startup, 2)]
            await i2c.write_byte(self._addr, cmd)
            await asyncio.sleep(0.01)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
            if len(d) < 6:
                return False
            raw_t = struct.unpack(">H", bytes(d[0:2]))[0]
            raw_h = struct.unpack(">H", bytes(d[3:5]))[0]
            self._temp = round(-45.0 + 175.0 * raw_t / 65535.0, 2)
            self._hum = round(-6.0 + 125.0 * raw_h / 65535.0, 1)
            if self._hum < 0:
                self._hum = 0
            if self._hum > 100:
                self._hum = 100
            temp_off = float(self._config.get("temp_offset") or "0")
            self._temp += temp_off
            event.data["values"] = {"Temperature": self._temp, "Humidity": self._hum}
            return True
        except Exception as e:
            logger.error("SHT4x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x44), "options": [
                {"value": a, "label": hex(a)} for a in SHT4x_ADDRS
            ]},
            {"name": "temp_offset", "label": "Temperature offset (C)", "type": "text", "value": self._config.get("temp_offset", "0")},
            {"name": "startup_config", "label": "Startup Configuration", "type": "select", "value": self._config.get("startup_config", 2), "options": [
                {"value": i, "label": n} for i, n in enumerate(self.CONFIG_NAMES)
            ]},
            {"name": "normal_config", "label": "Normal Configuration", "type": "select", "value": self._config.get("normal_config", 2), "options": [
                {"value": i, "label": n} for i, n in enumerate(self.CONFIG_NAMES[:3])
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in SHT4x_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
