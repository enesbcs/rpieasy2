from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p173")

SHTC3_ADDR = 0x70
SHTC3_CMD_WAKEUP = 0x3517
SHTC3_CMD_SLEEP = 0xB098
SHTC3_CMD_MEAS = 0x7866
SHTC3_CMD_MEAS_LP = 0x609C
SHTC3_CMD_RESET = 0x805D


class P173SHTC3(PluginBase):
    PLUGIN_ID = 173
    PLUGIN_NAME = "Environment - SHTC3"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x70]

    def __init__(self):
        super().__init__()
        self._addr: int = SHTC3_ADDR
        self._config: dict[str, Any] = {}

    async def _write_cmd(self, cmd: int) -> None:
        if not self._hw: return
        await self._hw.i2c.write_i2c_block_data(self._addr, (cmd >> 8) & 0xFF, [cmd & 0xFF])

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", SHTC3_ADDR)
        if not self._hw: return False
        try:
            await self._write_cmd(SHTC3_CMD_WAKEUP)
            await asyncio.sleep(0.001)
            await self._write_cmd(SHTC3_CMD_RESET)
            await asyncio.sleep(0.001)
            await self._write_cmd(SHTC3_CMD_SLEEP)
            await asyncio.sleep(0.001)
        except Exception as e:
            logger.error(f"SHTC3 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", SHTC3_ADDR)
        self._config.setdefault("temp_offset", "0.0")
        self._config.setdefault("low_power", False)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await self._write_cmd(SHTC3_CMD_WAKEUP)
            await asyncio.sleep(0.001)
            cmd = SHTC3_CMD_MEAS_LP if self._config.get("low_power", False) else SHTC3_CMD_MEAS
            await self._write_cmd(cmd)
            await asyncio.sleep(0.020)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
            st = (d[0] << 8) | d[1]
            sh = (d[3] << 8) | d[4]
            temp = -45.0 + 175.0 * st / 65535.0
            hum = 100.0 * sh / 65535.0
            temp_off = float(self._config.get("temp_offset") or 0)
            temp += temp_off
            await self._write_cmd(SHTC3_CMD_SLEEP)
            event.data["values"] = {"Temperature": round(temp, 2), "Humidity": round(max(0, min(100, hum)), 2)}
            return True
        except Exception as e:
            logger.error(f"SHTC3 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", SHTC3_ADDR), "options": [
                {"value": 0x70, "label": "0x70"},
            ]},
            {"name": "temp_offset", "label": "Temperature offset (C)", "type": "number", "value": self._config.get("temp_offset", "0.0"), "step": "0.1"},
            {"name": "low_power", "label": "Low Power Mode", "type": "checkbox", "value": self._config.get("low_power", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
