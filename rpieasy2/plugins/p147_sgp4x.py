from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p147")

SGP4x_ADDR = 0x59
SGP40_MEAS_CMD = [0x26, 0x0F]
SGP41_MEAS_CMD = [0x26, 0x12]


def _sgp_crc(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


class P147SGP4x(PluginBase):
    PLUGIN_ID = 147
    PLUGIN_NAME = "Gases - SGP4x VOC(/NOx)"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = SGP4x_ADDR
        self._voc: int = 0
        self._nox: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = SGP4x_ADDR
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, 0x20, [0x03])
            await asyncio.sleep(0.05)
        except Exception:
            pass
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("sensor_type", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            sensor_type = int(self._config.get("sensor_type") or 0)
            compensation = event.data.get("task_values", {})
            rh = compensation.get("Humidity", compensation.get("hum", 50.0))
            temp = compensation.get("Temperature", compensation.get("temp", 25.0))
            rh = max(0, min(100, float(rh)))
            temp = max(-10, min(60, float(temp)))
            rh_raw = int(rh * 65535.0 / 100.0)
            t_raw = int((temp + 45.0) * 65535.0 / 175.0)
            rh_bytes = struct.pack(">H", rh_raw)
            t_bytes = struct.pack(">H", t_raw)
            payload = SGP40_MEAS_CMD[1:] + list(rh_bytes) + [_sgp_crc(rh_bytes)] + list(t_bytes) + [_sgp_crc(t_bytes)]
            await i2c.write_i2c_block_data(self._addr, SGP40_MEAS_CMD[0], payload)
            await asyncio.sleep(0.05)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6 if sensor_type == 1 else 3)
            if len(d) < 3:
                return False
            self._voc = (d[0] << 8) | d[1]
            if sensor_type == 1 and len(d) >= 6:
                self._nox = (d[3] << 8) | d[4]
            else:
                self._nox = 0
            event.data["values"] = {"VOC": self._voc, "NOx": self._nox}
            return True
        except Exception as e:
            logger.error("SGP4x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "sensor_type", "label": "Sensor model", "type": "select", "value": self._config.get("sensor_type", 0), "options": [
                {"value": 0, "label": "SGP40"},
                {"value": 1, "label": "SGP41"},
            ]},
            {"name": "low_power", "label": "Low-power measurement", "type": "checkbox", "value": self._config.get("low_power", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == SGP4x_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = SGP4x_ADDR
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        sensor_type = int(self._config.get("sensor_type") or 0)
        event.data["value_count"] = 2 if sensor_type == 1 else 1
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_NOX]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"VOC": 0, "NOx": 0}
