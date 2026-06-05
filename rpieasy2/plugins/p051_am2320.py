from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p051")

AM2320_ADDR = 0x5C


class P051AM2320(PluginBase):
    PLUGIN_ID = 51
    PLUGIN_NAME = "Environment - AM2320"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, i2c_no_device_check=True, plugin_stats=True, send_data_option=True, timer_option=True)

    def __init__(self):
        super().__init__()
        self._addr: int = AM2320_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", AM2320_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _wake(self) -> None:
        if not self._hw: return
        try:
            await self._hw.i2c.write_byte(self._addr, 0x00)
        except Exception:
            pass
        await asyncio.sleep(0.003)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await self._wake()
            await i2c.write_i2c_block_data(self._addr, 0x03, [0x00, 0x04])
            await asyncio.sleep(0.003)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 8)
            rh = (d[2] << 8) | d[3]
            rt = (d[4] << 8) | d[5]
            temp = rt / 10.0 if not (rt & 0x8000) else (rt & 0x7FFF) / -10.0
            event.data["values"] = {"Temperature": round(temp, 1), "Humidity": round(rh / 10.0, 1)}
            return True
        except Exception as e:
            logger.error(f"AM2320 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", AM2320_ADDR), "options": [
                {"value": 0x5C, "label": "0x5C"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == AM2320_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = AM2320_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
