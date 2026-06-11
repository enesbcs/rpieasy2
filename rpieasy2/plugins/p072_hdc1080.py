from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p072")

HDC1080_ADDR = 0x40
HDC1080_REG_CONFIG = 0x02
HDC1080_REG_TEMP = 0x00
HDC1080_REG_HUM = 0x01


class P072HDC1080(PluginBase):
    PLUGIN_ID = 72
    PLUGIN_NAME = "Environment - HDC1080"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x40]

    def __init__(self):
        super().__init__()
        self._addr: int = HDC1080_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", HDC1080_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, HDC1080_REG_CONFIG, [0x00, 0x00])
            await asyncio.sleep(0.015)
        except Exception as e:
            logger.error(f"HDC1080 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", HDC1080_ADDR)
        self._config.setdefault("temp_offset", "0")
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, HDC1080_REG_TEMP, 0x00)
            await asyncio.sleep(0.015)
            d = await i2c.read_i2c_block_data(self._addr, HDC1080_REG_TEMP, 4)
            st = (d[0] << 8) | d[1]
            sh = (d[2] << 8) | d[3]
            temp = st / 65536.0 * 165.0 - 40.0
            hum = sh / 65536.0 * 100.0
            temp_off = float(self._config.get("temp_offset") or 0) / 10.0
            temp += temp_off
            event.data["values"] = {"Temperature": round(temp, 2), "Humidity": round(max(0, min(100, hum)), 2)}
            return True
        except Exception as e:
            logger.error(f"HDC1080 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", HDC1080_ADDR), "options": [
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
