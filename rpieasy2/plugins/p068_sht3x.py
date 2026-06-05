from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p068")

SHT3X_I2C_ADDR = 0x44
SHT3X_ALT_ADDR = 0x45
SHT3X_MEAS_CMD = [0x2C, 0x06]


class P068SHT3x(PluginBase):
    PLUGIN_ID = 68
    PLUGIN_NAME = "Environment - SHT3x"
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

    def __init__(self):
        super().__init__()
        self._addr: int = SHT3X_I2C_ADDR
        self._config: dict[str, Any] = {}
        self._temp: float = 0.0
        self._hum: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or SHT3X_I2C_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", SHT3X_I2C_ADDR)
        self._config.setdefault("temp_offset", "0")
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, SHT3X_MEAS_CMD[0], SHT3X_MEAS_CMD[1:])
            await asyncio.sleep(0.015)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
            st = (d[0] << 8) | d[1]
            sh = (d[3] << 8) | d[4]
            temp = -45.0 + 175.0 * st / 65535.0
            hum = 100.0 * sh / 65535.0
            temp_off = float(self._config.get("temp_offset") or 0) / 10.0
            temp += temp_off
            self._temp = round(temp, 2)
            self._hum = round(max(0, min(100, hum)), 2)
            event.data["values"] = {"Temperature": self._temp, "Humidity": self._hum}
            return True
        except Exception as e:
            logger.error("SHT3x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", SHT3X_I2C_ADDR), "options": [
                {"value": 0x44, "label": "0x44"},
                {"value": 0x45, "label": "0x45"},
            ]},
            {"name": "temp_offset", "label": "Temperature offset (x 0.1C)", "type": "number", "value": self._config.get("temp_offset", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return addr in (SHT3X_I2C_ADDR, SHT3X_ALT_ADDR)

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
