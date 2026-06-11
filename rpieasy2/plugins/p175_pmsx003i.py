from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p175")

I2C_ADDR = 0x12

PMS_START_BYTE1 = 0x42
PMS_START_BYTE2 = 0x4D
PMS_EXPECTED_SIZE = 32


class P175PMSx003i(PluginBase):
    PLUGIN_ID = 175
    PLUGIN_NAME = "Dust - PMSx003i (I2C)"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
        i2c_max100khz=True,
    )
    I2C_ADDRESSES = [I2C_ADDR]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._i2c = None
        self._pm1_0: float = 0.0
        self._pm2_5: float = 0.0
        self._pm10: float = 0.0
        self._values_available = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        if not self._hw:
            return False
        self._i2c = self._hw.i2c
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("sensor_type", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._i2c:
            return False
        try:
            buf = await self._i2c.read_bytes(I2C_ADDR, PMS_EXPECTED_SIZE)
            if len(buf) >= PMS_EXPECTED_SIZE and buf[0] == PMS_START_BYTE1 and buf[1] == PMS_START_BYTE2:
                pm1_0 = ((buf[4] << 8) | buf[5]) / 10.0
                pm2_5 = ((buf[6] << 8) | buf[7]) / 10.0
                pm10 = ((buf[8] << 8) | buf[9]) / 10.0
                checksum = (sum(buf[:30]) & 0xFFFF)
                expected_cs = (buf[30] << 8) | buf[31]
                if checksum == expected_cs:
                    self._pm1_0 = pm1_0
                    self._pm2_5 = pm2_5
                    self._pm10 = pm10
                    self._values_available = True

            if self._values_available:
                event.data["values"] = {
                    "PM1.0": self._pm1_0,
                    "PM2.5": self._pm2_5,
                    "PM10": self._pm10,
                }
                return True
        except Exception as e:
            logger.error("PMSx003i read error: %s", e)
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "sensor_type", "label": "Sensor Model", "type": "select",
             "value": self._config.get("sensor_type", 0),
             "options": [
                 {"value": 0, "label": "PMSA003i"},
             ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.par1 == I2C_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = I2C_ADDR
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"PM1.0": 0.0, "PM2.5": 0.0, "PM10": 0.0}
