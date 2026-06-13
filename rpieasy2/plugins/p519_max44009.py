from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p519")

MAX44009_ADDR = 0x4A


class P519MAX44009(PluginBase):
    PLUGIN_ID = 519
    PLUGIN_NAME = "Environment - MAX44009 ambient light sensor"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x4A]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = MAX44009_ADDR

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", MAX44009_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MAX44009_ADDR)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, 0x02, 0x40)
            await asyncio.sleep(0.5)
            d = await i2c.read_i2c_block_data(self._addr, 0x03, 2)
            exponent = (d[0] & 0xF0) >> 4
            mantissa = ((d[0] & 0x0F) << 4) | (d[1] & 0x0F)
            luminance = ((2 ** exponent) * mantissa) * 0.045
            event.data["values"] = {"Lux": round(luminance, 2)}
            return True
        except Exception as e:
            logger.error("MAX44009 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MAX44009_ADDR), "options": [
                {"value": 0x4A, "label": "0x4A"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Lux": 0.0}
