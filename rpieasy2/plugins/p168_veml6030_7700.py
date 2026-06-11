from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p168")

VEML7700_ADDR = 0x10
VEML6030_ADDR = 0x48

VEML_REG_ALS_CONF = 0x00
VEML_REG_ALS_DATA = 0x04
VEML_REG_WHITE_DATA = 0x05

GAIN_VALUES = {0: 1, 1: 2, 2: 0.125, 3: 0.25}
INT_TIME_VALS = {
    0b1100: 25,
    0b1000: 50,
    0b0000: 100,
    0b0001: 200,
    0b0010: 400,
    0b0011: 800,
}
INT_TIME_CODES = {25: 0b1100, 50: 0b1000, 100: 0b0000, 200: 0b0001, 400: 0b0010, 800: 0b0011}

PSM_MODES = {
    4: (0, 0),
    0: (1, 0),
    1: (0, 1),
    2: (1, 1),
    3: (0, 0),
}


def _calculate_lux(als: int, gain_code: int, int_code: int, corrected: bool = False) -> float:
    gain = GAIN_VALUES.get(gain_code, 1)
    it = INT_TIME_VALS.get(int_code, 100)
    if corrected:
        if als < 100:
            return als * 0.0016 * (100.0 / it) * (1.0 / gain)
        return als * 0.0016 * (100.0 / it) * (1.0 / gain)
    return als * 0.0036 * (100.0 / it) * (1.0 / gain)


class P168VEML6030_7700(PluginBase):
    PLUGIN_ID = 168
    PLUGIN_NAME = "Light/Lux - VEML6030/VEML7700"
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
    I2C_ADDRESSES = [0x10, 0x48]

    def __init__(self):
        super().__init__()
        self._addr: int = VEML7700_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", VEML7700_ADDR))
        except (ValueError, TypeError):
            self._addr = VEML7700_ADDR
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                gain = int(self._config.get("als_gain", 0))
            except (ValueError, TypeError):
                gain = 0
            try:
                int_code = int(self._config.get("als_integration", 0))
            except (ValueError, TypeError):
                int_code = 0
            try:
                psm = int(self._config.get("psm_mode", 4))
            except (ValueError, TypeError):
                psm = 4
            conf = (gain & 0x03) << 11
            conf |= (int_code & 0x0F) << 6
            if psm in PSM_MODES:
                psm_bits = PSM_MODES[psm]
                conf |= (psm_bits[0] << 2) | (psm_bits[1] << 1)
            conf |= 0x01
            await i2c.write_i2c_block_data(self._addr, VEML_REG_ALS_CONF, [
                conf & 0xFF, (conf >> 8) & 0xFF])
            await asyncio.sleep(0.01)
            return True
        except Exception as e:
            logger.error("VEML7700 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", VEML7700_ADDR)
        self._config.setdefault("als_gain", 0)
        self._config.setdefault("als_integration", 0)
        self._config.setdefault("psm_mode", 4)
        self._config.setdefault("read_method", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                gain = int(self._config.get("als_gain", 0))
            except (ValueError, TypeError):
                gain = 0
            try:
                int_code = int(self._config.get("als_integration", 0))
            except (ValueError, TypeError):
                int_code = 0
            try:
                corrected = int(self._config.get("read_method", 0)) == 1
            except (ValueError, TypeError):
                corrected = False
            d_als = await i2c.read_i2c_block_data(self._addr, VEML_REG_ALS_DATA, 2)
            d_white = await i2c.read_i2c_block_data(self._addr, VEML_REG_WHITE_DATA, 2)
            als = d_als[0] | (d_als[1] << 8)
            white = d_white[0] | (d_white[1] << 8)
            lux = _calculate_lux(als, gain, int_code, corrected)
            event.data["values"] = {
                "Lux": round(lux, 2),
                "White": white,
                "Raw": als,
            }
            return True
        except Exception as e:
            logger.error("VEML7700 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [
            {"value": 0x10, "label": "0x10"},
            {"value": 0x48, "label": "0x48 (VEML6030, ADDR->VCC)"},
        ]
        rm_opts = [
            {"value": 0, "label": "Normal (no wait)"},
            {"value": 1, "label": "Corrected (no wait)"},
        ]
        gain_opts = [
            {"value": 0, "label": "x1"},
            {"value": 1, "label": "x2"},
            {"value": 2, "label": "x(1/8)"},
            {"value": 3, "label": "x(1/4)"},
        ]
        int_opts = [
            {"value": 0b1100, "label": "25 ms"},
            {"value": 0b1000, "label": "50 ms"},
            {"value": 0b0000, "label": "100 ms"},
            {"value": 0b0001, "label": "200 ms"},
            {"value": 0b0010, "label": "400 ms"},
            {"value": 0b0011, "label": "800 ms"},
        ]
        psm_opts = [
            {"value": 4, "label": "Disabled"},
            {"value": 0, "label": "Mode 1"},
            {"value": 1, "label": "Mode 2"},
            {"value": 2, "label": "Mode 3"},
            {"value": 3, "label": "Mode 4"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", VEML7700_ADDR), "options": addr_opts},
            {"name": "read_method", "label": "Lux Read-method", "type": "select", "value": self._config.get("read_method", 0), "options": rm_opts},
            {"name": "als_gain", "label": "Gain factor", "type": "select", "value": self._config.get("als_gain", 0), "options": gain_opts},
            {"name": "als_integration", "label": "Integration time", "type": "select", "value": self._config.get("als_integration", 0), "options": int_opts},
            {"name": "psm_mode", "label": "Power Save Mode", "type": "select", "value": self._config.get("psm_mode", 4), "options": psm_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in [VEML7700_ADDR, VEML6030_ADDR]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Lux": 0.0, "White": 0, "Raw": 0}
