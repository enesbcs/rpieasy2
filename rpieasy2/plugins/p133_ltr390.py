from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p133")

LTR390_ADDR = 0x53

LTR390_REG_MAIN_CTRL = 0x00
LTR390_REG_MEAS_RATE = 0x04
LTR390_REG_GAIN = 0x05
LTR390_REG_UV_DATA = 0x0D
LTR390_REG_ALS_DATA = 0x10
LTR390_REG_PART_ID = 0x06

LTR390_GAIN_1 = 0
LTR390_GAIN_3 = 1
LTR390_GAIN_6 = 2
LTR390_GAIN_9 = 3
LTR390_GAIN_18 = 4

LTR390_RESOLUTION_20BIT = 0
LTR390_RESOLUTION_19BIT = 1
LTR390_RESOLUTION_18BIT = 2
LTR390_RESOLUTION_17BIT = 3
LTR390_RESOLUTION_16BIT = 4
LTR390_RESOLUTION_13BIT = 5

LTR390_WFAC = 1.0
LTR390_UV_SENSITIVITY = 2300

GAIN_FACTORS = [1, 3, 6, 9, 18]
RESOLUTION_FACTORS = [20, 19, 18, 17, 16, 13]


class P133LTR390(PluginBase):
    PLUGIN_ID = 133
    PLUGIN_NAME = "UV - LTR390"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        formula_option=True,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x53]

    def __init__(self):
        super().__init__()
        self._addr: int = LTR390_ADDR
        self._config: dict[str, Any] = {}
        self._mode: int = 0
        self._uv: int = 0
        self._als: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = LTR390_ADDR
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                init_reset = int(self._config.get("init_reset", 1))
            except (ValueError, TypeError):
                init_reset = 1
            if init_reset:
                await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x00)
                await asyncio.sleep(0.01)
            await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x02)
            try:
                self._mode = int(self._config.get("read_mode", 0))
            except (ValueError, TypeError):
                self._mode = 0
            try:
                uv_gain = int(self._config.get("uv_gain", LTR390_GAIN_3))
            except (ValueError, TypeError):
                uv_gain = LTR390_GAIN_3
            try:
                als_gain = int(self._config.get("als_gain", LTR390_GAIN_3))
            except (ValueError, TypeError):
                als_gain = LTR390_GAIN_3
            try:
                uv_res = int(self._config.get("uv_resolution", LTR390_RESOLUTION_18BIT))
            except (ValueError, TypeError):
                uv_res = LTR390_RESOLUTION_18BIT
            try:
                als_res = int(self._config.get("als_resolution", LTR390_RESOLUTION_18BIT))
            except (ValueError, TypeError):
                als_res = LTR390_RESOLUTION_18BIT
            gain_val = (als_gain << 4) | uv_gain if self._mode != 2 else uv_gain
            if self._mode != 2:
                gain_val = (als_gain << 4) | uv_gain
            else:
                gain_val = uv_gain
            await i2c.write_byte_data(self._addr, LTR390_REG_GAIN, gain_val)
            res_val = (als_res << 4) | uv_res if self._mode != 2 else uv_res
            if self._mode != 2:
                res_val = (als_res << 4) | uv_res
            else:
                res_val = uv_res
            await i2c.write_byte_data(self._addr, LTR390_REG_MEAS_RATE, res_val)
            await asyncio.sleep(0.01)
            return True
        except Exception as e:
            logger.error("LTR390 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("read_mode", 0)
        self._config.setdefault("uv_gain", LTR390_GAIN_3)
        self._config.setdefault("uv_resolution", LTR390_RESOLUTION_18BIT)
        self._config.setdefault("als_gain", LTR390_GAIN_3)
        self._config.setdefault("als_resolution", LTR390_RESOLUTION_18BIT)
        self._config.setdefault("init_reset", 1)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                mode = int(self._config.get("read_mode", 0))
            except (ValueError, TypeError):
                mode = 0
            try:
                uv_gain = int(self._config.get("uv_gain", LTR390_GAIN_3))
            except (ValueError, TypeError):
                uv_gain = LTR390_GAIN_3
            try:
                als_gain = int(self._config.get("als_gain", LTR390_GAIN_3))
            except (ValueError, TypeError):
                als_gain = LTR390_GAIN_3
            try:
                uv_res = int(self._config.get("uv_resolution", LTR390_RESOLUTION_18BIT))
            except (ValueError, TypeError):
                uv_res = LTR390_RESOLUTION_18BIT
            try:
                als_res = int(self._config.get("als_resolution", LTR390_RESOLUTION_18BIT))
            except (ValueError, TypeError):
                als_res = LTR390_RESOLUTION_18BIT

            uv_val = 0
            als_val = 0

            if mode in (0, 1):
                await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x0A)
                await asyncio.sleep(0.1)
                d = await i2c.read_i2c_block_data(self._addr, LTR390_REG_UV_DATA, 3)
                uv_val = (d[0] << 16) | (d[1] << 8) | d[2]
                await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x02)

            if mode in (0, 2):
                await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x09)
                await asyncio.sleep(0.1)
                d = await i2c.read_i2c_block_data(self._addr, LTR390_REG_ALS_DATA, 3)
                als_val = (d[0] << 16) | (d[1] << 8) | d[2]
                await i2c.write_byte_data(self._addr, LTR390_REG_MAIN_CTRL, 0x02)

            uv_gain_f = GAIN_FACTORS[uv_gain] if uv_gain < len(GAIN_FACTORS) else 3
            als_gain_f = GAIN_FACTORS[als_gain] if als_gain < len(GAIN_FACTORS) else 3

            uvi = (uv_val / LTR390_UV_SENSITIVITY) * uv_gain_f if uv_val > 0 else 0.0
            w = RESOLUTION_FACTORS[als_res] if als_res < len(RESOLUTION_FACTORS) else 18
            lux = (als_val / (als_gain_f * (2 ** w) * LTR390_WFAC)) * 100.0 if als_val > 0 else 0.0

            event.data["values"] = {
                "UV": uv_val,
                "UVIndex": round(uvi, 2),
                "Ambient": als_val,
                "Lux": round(lux, 2),
            }
            return True
        except Exception as e:
            logger.error("LTR390 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        mode_opts = [
            {"value": 0, "label": "Dual mode, read alternating UV/Ambient"},
            {"value": 1, "label": "UV reading only"},
            {"value": 2, "label": "Ambient reading only"},
        ]
        gain_opts = [
            {"value": 0, "label": "1x"},
            {"value": 1, "label": "3x"},
            {"value": 2, "label": "6x"},
            {"value": 3, "label": "9x"},
            {"value": 4, "label": "18x"},
        ]
        res_opts = [
            {"value": 0, "label": "20 bit"},
            {"value": 1, "label": "19 bit"},
            {"value": 2, "label": "18 bit"},
            {"value": 3, "label": "17 bit"},
            {"value": 4, "label": "16 bit"},
            {"value": 5, "label": "13 bit"},
        ]
        event.data["form"] = [
            {"name": "read_mode", "label": "Read mode", "type": "select", "value": self._config.get("read_mode", 0), "options": mode_opts},
            {"name": "uv_gain", "label": "UV Gain", "type": "select", "value": self._config.get("uv_gain", LTR390_GAIN_3), "options": gain_opts},
            {"name": "uv_resolution", "label": "UV Resolution", "type": "select", "value": self._config.get("uv_resolution", LTR390_RESOLUTION_18BIT), "options": res_opts},
            {"name": "als_gain", "label": "Ambient Gain", "type": "select", "value": self._config.get("als_gain", LTR390_GAIN_3), "options": gain_opts},
            {"name": "als_resolution", "label": "Ambient Resolution", "type": "select", "value": self._config.get("als_resolution", LTR390_RESOLUTION_18BIT), "options": res_opts},
            {"name": "init_reset", "label": "Reset sensor on init", "type": "checkbox", "value": self._config.get("init_reset", 1)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == LTR390_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = LTR390_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"UV": 0, "UVIndex": 0.0, "Ambient": 0, "Lux": 0.0}
