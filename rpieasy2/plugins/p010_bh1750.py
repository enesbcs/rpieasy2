from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE, SENSOR_V_TYPE_ILLUMINANCE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p010")

BH1750_ADDR = 0x23
BH1750_CMD_POWER_ON = 0x01
BH1750_CMD_CONT_H_RES = 0x10


class P010BH1750(PluginBase):
    PLUGIN_ID = 10
    PLUGIN_NAME = "Light/Lux - BH1750"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._addr: int = BH1750_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", BH1750_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte(self._addr, BH1750_CMD_POWER_ON)
            mode = int(self._config.get("measurement_mode") or 1)
            cmds = {0: 0x13, 1: 0x10, 2: 0x11, 3: 0x10}
            await i2c.write_byte(self._addr, cmds.get(mode, 0x10))
        except Exception as e:
            logger.error(f"BH1750 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            if self._config.get("sleep_mode", False):
                await self._hw.i2c.write_byte(self._addr, BH1750_CMD_POWER_ON)
                mode = int(self._config.get("measurement_mode") or 1)
                cmds = {0: 0x13, 1: 0x10, 2: 0x11, 3: 0x10}
                await self._hw.i2c.write_byte(self._addr, cmds.get(mode, 0x10))
                await asyncio.sleep(0.18)
            raw = await self._hw.i2c.read_i2c_block_data(self._addr, 0x00, 2)
            if not raw or len(raw) < 2 or raw[0] is None or raw[1] is None:
                logger.error(f"BH1750 read failed: invalid data {raw}")
                return False
            lux = (raw[0] << 8 | raw[1]) / 1.2
            event.data["values"] = {"Lux": round(lux, 2)}
            if self._config.get("sleep_mode", False):
                await self._hw.i2c.write_byte(self._addr, 0x00)
            return True
        except Exception as e:
            logger.error(f"BH1750 read failed: {e}")
            logger.error("BH1750 traceback", exc_info=True)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", BH1750_ADDR), "options": [
                {"value": 0x23, "label": "0x23"}, {"value": 0x5C, "label": "0x5C"},
            ]},
            {"name": "measurement_mode", "label": "Measurement Mode", "type": "select", "value": self._config.get("measurement_mode", 1), "options": [
                {"value": 0, "label": "Low"},
                {"value": 1, "label": "Normal"},
                {"value": 2, "label": "High"},
                {"value": 3, "label": "Auto High"},
            ]},
            {"name": "sleep_mode", "label": "Send Sensor to Sleep", "type": "checkbox", "value": self._config.get("sleep_mode", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_ILLUMINANCE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Lux": 0.0}
