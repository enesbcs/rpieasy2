from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p164")

ENS160_ADDR_0 = 0x52
ENS160_ADDR_1 = 0x53

ENS160_REG_PART_ID = 0x00
ENS160_REG_OPMODE = 0x10
ENS160_REG_CONFIG = 0x11
ENS160_REG_COMMAND = 0x12
ENS160_REG_TEMP_IN = 0x13
ENS160_REG_RH_IN = 0x15
ENS160_REG_DATA_AQI = 0x20
ENS160_REG_DATA_TVOC = 0x22
ENS160_REG_DATA_ECO2 = 0x24
ENS160_REG_DATA_T = 0x30
ENS160_REG_DATA_RH = 0x32


class P164ENS160(PluginBase):
    PLUGIN_ID = 164
    PLUGIN_NAME = "Gases - ENS16x"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        formula_option=True,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x52, 0x53]

    def __init__(self):
        super().__init__()
        self._addr: int = ENS160_ADDR_0
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", ENS160_ADDR_0))
        except (ValueError, TypeError):
            self._addr = ENS160_ADDR_0
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            part_id = await i2c.read_i2c_block_data(self._addr, ENS160_REG_PART_ID, 2)
            pid = (part_id[1] << 8) | part_id[0]
            if pid != 0x0160:
                logger.warning("ENS160 PART_ID mismatch: 0x%04X", pid)
            await i2c.write_byte_data(self._addr, ENS160_REG_OPMODE, 0x00)
            await asyncio.sleep(0.02)
            await i2c.write_byte_data(self._addr, ENS160_REG_OPMODE, 0x02)
            await asyncio.sleep(0.02)
            return True
        except Exception as e:
            logger.error("ENS160 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", ENS160_ADDR_0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            temp = float(self._config.get("comp_temp", 25.0))
            hum = float(self._config.get("comp_hum", 50.0))
            temp_raw = int((temp + 273.15) * 64)
            hum_raw = int(hum * 64)
            await i2c.write_i2c_block_data(self._addr, ENS160_REG_TEMP_IN, [
                temp_raw & 0xFF, (temp_raw >> 8) & 0xFF])
            await i2c.write_i2c_block_data(self._addr, ENS160_REG_RH_IN, [
                hum_raw & 0xFF, (hum_raw >> 8) & 0xFF])
            await asyncio.sleep(0.05)
            d_aqi = await i2c.read_byte_data(self._addr, ENS160_REG_DATA_AQI)
            d_tvoc = await i2c.read_i2c_block_data(self._addr, ENS160_REG_DATA_TVOC, 2)
            d_eco2 = await i2c.read_i2c_block_data(self._addr, ENS160_REG_DATA_ECO2, 2)
            tvoc = (d_tvoc[1] << 8) | d_tvoc[0]
            eco2 = (d_eco2[1] << 8) | d_eco2[0]
            aqi = d_aqi & 0x07
            event.data["values"] = {"TVOC": tvoc, "eCO2": eco2, "AQI": aqi}
            return True
        except Exception as e:
            logger.error("ENS160 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [
            {"value": 0x52, "label": "0x52 (ADDR Low)"},
            {"value": 0x53, "label": "0x53 (ADDR High)"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", ENS160_ADDR_0), "options": addr_opts},
            {"name": "comp_temp", "label": "Temperature (C)", "type": "number", "value": self._config.get("comp_temp", "25.0")},
            {"name": "comp_hum", "label": "Humidity (%)", "type": "number", "value": self._config.get("comp_hum", "50.0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in [ENS160_ADDR_0, ENS160_ADDR_1]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"TVOC": 0, "eCO2": 0, "AQI": 0}
