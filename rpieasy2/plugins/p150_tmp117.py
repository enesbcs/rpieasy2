from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p150")

TMP117_ADDR_DEFAULT = 0x48
TMP117_REG_TEMP = 0x00


class P150TMP117(PluginBase):
    PLUGIN_ID = 150
    PLUGIN_NAME = "Temperature - TMP117"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x48, 0x49, 0x4A, 0x4B]

    def __init__(self):
        super().__init__()
        self._addr: int = TMP117_ADDR_DEFAULT
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", TMP117_ADDR_DEFAULT)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", TMP117_ADDR_DEFAULT)
        self._config.setdefault("temp_offset", "0")
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, TMP117_REG_TEMP, 2)
            raw = (d[0] << 8) | d[1]
            val = raw if raw <= 32767 else raw - 65536
            temp = val * 0.0078125
            temp_off = float(self._config.get("temp_offset") or 0) / 10.0
            temp += temp_off
            event.data["values"] = {"Temperature": round(temp, 2)}
            return True
        except Exception as e:
            logger.error(f"TMP117 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", TMP117_ADDR_DEFAULT), "options": [
                {"value": 0x48, "label": "0x48"}, {"value": 0x49, "label": "0x49"},
                {"value": 0x4A, "label": "0x4A"}, {"value": 0x4B, "label": "0x4B"},
            ]},
            {"name": "temp_offset", "label": "Temperature offset (x 0.1C)", "type": "number", "value": self._config.get("temp_offset", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0}
