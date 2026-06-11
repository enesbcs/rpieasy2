from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p047")

CATNIP_DEFAULT_ADDR = 0x20
BEFLE_DEFAULT_ADDR = 0x20
ADAFRUIT_DEFAULT_ADDR = 0x36

CATNIP_REG_TEMP = 0x00
CATNIP_REG_MOISTURE = 0x01
CATNIP_REG_LIGHT = 0x02
CATNIP_REG_VERSION = 0x03
CATNIP_REG_ADDR = 0x04
CATNIP_REG_SLEEP = 0x07

MODEL_CATNIP = 0
MODEL_BEFLE = 1
MODEL_BEFLE_V3 = 2
MODEL_ADAFRUIT = 3


class P047I2CSoilMoisture(PluginBase):
    PLUGIN_ID = 47
    PLUGIN_NAME = "Environment - Soil moisture sensor"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_TRIPLE,
        formula_option=True,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x20, 0x30, 0x36, 0x37, 0x38, 0x39]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = CATNIP_DEFAULT_ADDR

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", CATNIP_DEFAULT_ADDR))
        except (ValueError, TypeError):
            self._addr = CATNIP_DEFAULT_ADDR
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", CATNIP_DEFAULT_ADDR)
        self._config.setdefault("model", MODEL_CATNIP)
        self._config.setdefault("sensor_sleep", False)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            model = int(self._config.get("model", MODEL_CATNIP))
            if model == MODEL_ADAFRUIT:
                d = await i2c.read_i2c_block_data(self._addr, 0x00, 4)
                cap = (d[0] << 8) | d[1]
                temp_raw = (d[2] << 8) | d[3]
                temp = temp_raw * 0.01
                event.data["values"] = {"Temperature": round(temp, 2), "Moisture": cap, "Light": 0}
            else:
                d = await i2c.read_i2c_block_data(self._addr, 0x00, 4)
                temp_raw = d[0] | (d[1] << 8)
                moist = d[2]
                light = d[3]
                temp = -40.0 + (temp_raw * 0.01) if temp_raw != 65535 else 0.0
                event.data["values"] = {"Temperature": round(temp, 2), "Moisture": moist, "Light": light}
            if int(self._config.get("sensor_sleep", 0)):
                await i2c.write_byte_data(self._addr, CATNIP_REG_SLEEP, 0x00)
            return True
        except Exception as e:
            logger.error("Soil moisture read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        model_opts = [
            {"value": MODEL_CATNIP, "label": "Catnip Electronics"},
            {"value": MODEL_BEFLE, "label": "BeFlE"},
            {"value": MODEL_BEFLE_V3, "label": "BeFlE v3"},
            {"value": MODEL_ADAFRUIT, "label": "Adafruit"},
        ]
        event.data["form"] = [
            {"name": "model", "label": "Sensor model", "type": "select", "value": self._config.get("model", MODEL_CATNIP), "options": model_opts},
            {"name": "address", "label": "I2C Address", "type": "number", "value": self._config.get("address", CATNIP_DEFAULT_ADDR)},
            {"name": "sensor_sleep", "label": "Send sensor to sleep", "type": "checkbox", "value": self._config.get("sensor_sleep", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", CATNIP_DEFAULT_ADDR))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == self._addr

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Moisture": 0, "Light": 0}
