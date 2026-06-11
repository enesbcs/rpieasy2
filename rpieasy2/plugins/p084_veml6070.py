from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p084")

VEML6070_ADDR_L = 0x38
VEML6070_ADDR_H = 0x39

VEML6070_RSET_DEFAULT = 270000
VEML6070_UV_MAX_INDEX = 15
VEML6070_UV_MAX_DEFAULT = 11
VEML6070_POWER_COEFF = 0.025
VEML6070_TABLE_COEFF = 32.86270591

VEML6070_BASE_VALUE = ((VEML6070_RSET_DEFAULT / VEML6070_TABLE_COEFF) / VEML6070_UV_MAX_DEFAULT) * 1
VEML6070_MAX_VALUE = ((VEML6070_RSET_DEFAULT / VEML6070_TABLE_COEFF) / VEML6070_UV_MAX_DEFAULT) * VEML6070_UV_MAX_INDEX


class P084VEML6070(PluginBase):
    PLUGIN_ID = 84
    PLUGIN_NAME = "UV - VEML6070"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        formula_option=True,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x38, 0x39]

    def __init__(self):
        super().__init__()
        self._addr: int = VEML6070_ADDR_L
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = VEML6070_ADDR_L
        if not self._hw:
            return False
        try:
            it = int(self._config.get("integration_time", 3))
        except (ValueError, TypeError):
            it = 3
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(VEML6070_ADDR_L, 0x00, ((it << 2) | 0x02))
            await asyncio.sleep(0.005)
            return True
        except Exception as e:
            logger.error("VEML6070 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("integration_time", 3)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            d_hi = await i2c.read_byte_data(VEML6070_ADDR_H, 0x00)
            d_lo = await i2c.read_byte_data(VEML6070_ADDR_L, 0x00)
            uv_raw = (d_hi << 8) | d_lo
            if uv_raw == 65535:
                event.data["values"] = {"Raw": float("nan"), "Risk": float("nan"), "Power": float("nan")}
                return False
            uv_risk = uv_raw / VEML6070_BASE_VALUE if uv_raw < VEML6070_MAX_VALUE else 99.0
            uv_power = VEML6070_POWER_COEFF * uv_risk
            event.data["values"] = {"Raw": round(uv_raw, 0), "Risk": round(uv_risk, 2), "Power": round(uv_power, 4)}
            return True
        except Exception as e:
            logger.error("VEML6070 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        it_opts = [
            {"value": 0, "label": "1/2T"},
            {"value": 1, "label": "1T"},
            {"value": 2, "label": "2T"},
            {"value": 3, "label": "4T (Default)"},
        ]
        event.data["form"] = [
            {"name": "integration_time", "label": "Refresh Time Determination", "type": "select", "value": self._config.get("integration_time", 3), "options": it_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return addr in [VEML6070_ADDR_L, VEML6070_ADDR_H]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = VEML6070_ADDR_L
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Raw": 0, "Risk": 0.0, "Power": 0.0}
