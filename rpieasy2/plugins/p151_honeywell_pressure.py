from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_BARO
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p151")

HONEYWELL_ADDR_DEFAULT = 0x28


class P151HoneywellPressure(PluginBase):
    PLUGIN_ID = 151
    PLUGIN_NAME = "Environment - Honeywell Pressure"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_BARO, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F]

    def __init__(self):
        super().__init__()
        self._addr: int = HONEYWELL_ADDR_DEFAULT
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", HONEYWELL_ADDR_DEFAULT)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", HONEYWELL_ADDR_DEFAULT)
        self._config.setdefault("output_min", 1638)
        self._config.setdefault("output_max", 14745)
        self._config.setdefault("pressure_min", 0.0)
        self._config.setdefault("pressure_max", 1.0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 4)
            status = (d[0] >> 6) & 0x03
            if status != 0 and status != 2:
                return False
            pressure_raw = ((d[0] & 0x3F) << 8) | d[1]
            temp_raw = (d[2] << 8) | d[3]
            out_min = int(self._config.get("output_min", 1638))
            out_max = int(self._config.get("output_max", 14745))
            p_min = float(self._config.get("pressure_min", 0.0))
            p_max = float(self._config.get("pressure_max", 1.0))
            if out_max != out_min:
                pressure = (pressure_raw - out_min) * (p_max - p_min) / (out_max - out_min) + p_min
            else:
                pressure = 0.0
            temp = temp_raw * 200.0 / 2047.0 - 50.0
            event.data["values"] = {"Pressure": round(pressure, 3), "Temperature": round(temp, 2)}
            return True
        except Exception as e:
            logger.error(f"Honeywell pressure read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", HONEYWELL_ADDR_DEFAULT), "options": [
                {"value": 0x28, "label": "0x28"}, {"value": 0x29, "label": "0x29"},
                {"value": 0x2A, "label": "0x2A"}, {"value": 0x2B, "label": "0x2B"},
                {"value": 0x2C, "label": "0x2C"}, {"value": 0x2D, "label": "0x2D"},
                {"value": 0x2E, "label": "0x2E"}, {"value": 0x2F, "label": "0x2F"},
            ]},
            {"name": "output_min", "label": "Output Min", "type": "number", "value": self._config.get("output_min", 1638)},
            {"name": "output_max", "label": "Output Max", "type": "number", "value": self._config.get("output_max", 14745)},
            {"name": "pressure_min", "label": "Pressure Min", "type": "number", "value": self._config.get("pressure_min", 0.0), "step": "0.001"},
            {"name": "pressure_max", "label": "Pressure Max", "type": "number", "value": self._config.get("pressure_max", 1.0), "step": "0.001"},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Pressure": 0.0, "Temperature": 0.0}
