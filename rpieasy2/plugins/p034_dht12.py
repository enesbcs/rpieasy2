from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p034")

DHT12_ADDR = 0x5C


class P034DHT12(PluginBase):
    PLUGIN_ID = 34
    PLUGIN_NAME = "Environment - DHT12"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x5C]

    def __init__(self):
        super().__init__()
        self._addr: int = DHT12_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", DHT12_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", DHT12_ADDR)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 5)
            checksum = (d[0] + d[1] + d[2] + d[3]) & 0xFF
            if d[4] != checksum:
                logger.warning("DHT12 checksum error")
                return False
            hum = d[0] + d[1] / 10.0
            temp_int = d[2]
            temp_dec = d[3] & 0x7F
            temp = temp_int + temp_dec / 10.0
            if d[3] & 0x80:
                temp = -temp
            event.data["values"] = {"Temperature": round(temp, 2), "Humidity": round(hum, 2)}
            return True
        except Exception as e:
            logger.error(f"DHT12 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", DHT12_ADDR), "options": [
                {"value": 0x5C, "label": "0x5C"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
