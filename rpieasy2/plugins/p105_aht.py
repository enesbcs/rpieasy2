from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p105")

AHT_ADDRS = [0x38, 0x39]
AHT_INIT_CMD = [0xBE, 0x08, 0x00]
AHT_MEAS_CMD = [0xAC, 0x33, 0x00]
AHT_SOFT_RESET = 0xBA
AHT_STATUS_BUSY = 0x80


class P105AHT(PluginBase):
    PLUGIN_ID = 105
    PLUGIN_NAME = "Environment - AHT1x/AHT2x/DHT20/AM2301B"
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
        self._config: dict[str, Any] = {}
        self._addr: int = 0x38
        self._temp: float = 0.0
        self._hum: float = 0.0
        self._initialized = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x38)
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, AHT_SOFT_RESET, [])
            await asyncio.sleep(0.02)
            await i2c.write_i2c_block_data(self._addr, AHT_INIT_CMD[0], AHT_INIT_CMD[1:])
            await asyncio.sleep(0.01)
            self._initialized = True
            return True
        except Exception as e:
            logger.error("AHT init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x38)
        self._config.setdefault("aht_type", 1)
        self._config.setdefault("temp_offset", "0")
        return True

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if not self._initialized or not self._hw:
            return None
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, AHT_MEAS_CMD[0], AHT_MEAS_CMD[1:])
            await asyncio.sleep(0.08)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
            if len(d) < 6:
                return None
            if d[0] & AHT_STATUS_BUSY:
                return None
            raw_h = ((d[1] << 12) | (d[2] << 4) | (d[3] >> 4)) & 0xFFFFF
            raw_t = ((d[3] << 16) | (d[4] << 8) | d[5]) & 0xFFFFF
            self._hum = round((raw_h / 0x100000) * 100.0, 1)
            self._temp = round((raw_t / 0x100000) * 200.0 - 50.0, 1)
            temp_off = float(self._config.get("temp_offset") or 0) / 10.0
            self._temp += temp_off
            self._hum = round(self._hum * (1 - 0.005 * temp_off / 0.1), 1)
        except Exception as e:
            logger.error("AHT read error: %s", e)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Temperature": self._temp, "Humidity": self._hum}
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x38), "options": [
                {"value": 0x38, "label": "0x38"},
                {"value": 0x39, "label": "0x39 (AHT1x only)"},
            ]},
            {"name": "aht_type", "label": "Sensor model", "type": "select", "value": self._config.get("aht_type", 1), "options": [
                {"value": 0, "label": "AHT1x"},
                {"value": 1, "label": "AHT20"},
                {"value": 2, "label": "AHT21"},
            ]},
            {"name": "temp_offset", "label": "Temperature offset (x 0.1C)", "type": "number", "value": self._config.get("temp_offset", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in AHT_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
