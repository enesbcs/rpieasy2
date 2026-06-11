from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p122")

SHT2X_ADDR = 0x40
SHT2X_CMD_TEMP = 0xF3
SHT2X_CMD_HUM = 0xF5


class P122SHT2x(PluginBase):
    PLUGIN_ID = 122
    PLUGIN_NAME = "Environment - SHT2x"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x40]

    def __init__(self):
        super().__init__()
        self._addr: int = SHT2X_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", SHT2X_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", SHT2X_ADDR)
        self._config.setdefault("temp_offset", "0")
        return True

    async def _read_sht2x(self, cmd: int) -> int:
        if not self._hw: return 0
        i2c = self._hw.i2c
        await i2c.write_byte_data(self._addr, 0x00, cmd)
        await asyncio.sleep(0.050)
        d = await i2c.read_i2c_block_data(self._addr, 0x00, 3)
        raw = (d[0] << 8) | d[1]
        raw &= 0xFFFC
        return raw

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            raw_temp = await self._read_sht2x(SHT2X_CMD_TEMP)
            raw_hum = await self._read_sht2x(SHT2X_CMD_HUM)
            temp = -46.85 + 175.72 * raw_temp / 65536.0
            hum = -6.0 + 125.0 * raw_hum / 65536.0
            temp_off = float(self._config.get("temp_offset") or 0) / 10.0
            temp += temp_off
            event.data["values"] = {"Temperature": round(temp, 2), "Humidity": round(max(0, min(100, hum)), 2)}
            return True
        except Exception as e:
            logger.error(f"SHT2x read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", SHT2X_ADDR), "options": [
                {"value": 0x40, "label": "0x40"},
            ]},
            {"name": "temp_offset", "label": "Temperature offset (x 0.1C)", "type": "number", "value": self._config.get("temp_offset", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
