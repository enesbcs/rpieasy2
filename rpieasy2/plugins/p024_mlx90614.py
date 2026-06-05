from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p024")

MLX90614_ADDR = 0x5A
MLX90614_TA = 0x06
MLX90614_TOBJ1 = 0x07


class P024MLX90614(PluginBase):
    PLUGIN_ID = 24
    PLUGIN_NAME = "Environment - MLX90614"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, ports=16, formula_option=True, send_data_option=True, plugin_stats=True, i2c_max100khz=True)

    I2C_ADDRESSES = [0x5A + p for p in range(16)]

    def __init__(self):
        super().__init__()
        self._addr: int = MLX90614_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", MLX90614_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _read_temp(self, reg: int) -> float:
        if not self._hw: return 0.0
        raw = await self._hw.i2c.read_word_data(self._addr, reg)
        raw = (raw >> 8) | ((raw & 0xFF) << 8)
        return raw * 0.02 - 273.15

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            option = int(self._config.get("option") or 0)
            if option == 1:
                val = round(await self._read_temp(MLX90614_TA), 2)
                event.data["values"] = {"Ambient": val}
            else:
                val = round(await self._read_temp(MLX90614_TOBJ1), 2)
                event.data["values"] = {"Object": val}
            return True
        except Exception as e:
            logger.error(f"MLX90614 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MLX90614_ADDR), "options": [
                {"value": a, "label": hex(a)} for a in self.I2C_ADDRESSES
            ]},
            {"name": "option", "label": "Output Option", "type": "select", "value": self._config.get("option", 0), "options": [
                {"value": 0, "label": "IR Object Temperature"},
                {"value": 1, "label": "Ambient Temperature"},
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
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_TEMP]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        option = int(task_config.get("option", 0))
        if option == 1:
            return {"Ambient": 0.0}
        return {"Object": 0.0}
