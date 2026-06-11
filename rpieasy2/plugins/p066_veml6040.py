from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p066")

VEML6040_ADDR = 0x10
VEML6040_REG_CTRL = 0x00
VEML6040_REG_R = 0x08
VEML6040_REG_G = 0x09
VEML6040_REG_B = 0x0A
VEML6040_REG_W = 0x0B

SENSITIVITY = [0.25168, 0.12584, 0.06292, 0.03146, 0.01573, 0.007865]


def _calc_cct(r: float, g: float, b: float) -> float:
    if g == 0:
        return 0.0
    ccti = (r - b) / g + 0.5
    return 4278.6 * (ccti ** -1.2455)


def _calc_ambient_light(g: float, it: int) -> float:
    return g * SENSITIVITY[it] if it < len(SENSITIVITY) else 0.0


def _calc_relw(x: float, w: float) -> float:
    return x / w if w != 0 else 0.0


class P066VEML6040(PluginBase):
    PLUGIN_ID = 66
    PLUGIN_NAME = "Color - VEML6040"
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
    I2C_ADDRESSES = [0x10]

    def __init__(self):
        super().__init__()
        self._addr: int = VEML6040_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", VEML6040_ADDR)
        if not self._hw:
            return False
        try:
            it = int(self._config.get("integration_time", 0))
        except (ValueError, TypeError):
            it = 0
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, VEML6040_REG_CTRL, [it << 4, 0x00])
            await asyncio.sleep(0.005)
            return True
        except Exception as e:
            logger.error("VEML6040 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", VEML6040_ADDR)
        self._config.setdefault("integration_time", 0)
        self._config.setdefault("value_mapping", 0)
        return True

    async def _read_channel(self, reg: int) -> float:
        i2c = self._hw.i2c
        d = await i2c.read_i2c_block_data(self._addr, reg, 2)
        return float((d[1] << 8) | d[0])

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            r = await self._read_channel(VEML6040_REG_R)
            g = await self._read_channel(VEML6040_REG_G)
            b = await self._read_channel(VEML6040_REG_B)
            w = await self._read_channel(VEML6040_REG_W)
            try:
                mapping = int(self._config.get("value_mapping", 0))
            except (ValueError, TypeError):
                mapping = 0
            try:
                it = int(self._config.get("integration_time", 0))
            except (ValueError, TypeError):
                it = 0

            if mapping == 0:
                vals = {"R": r, "G": g, "B": b, "W": w}
            elif mapping == 1:
                vals = {"R": _calc_relw(r, w) * 100, "G": _calc_relw(g, w) * 100,
                        "B": _calc_relw(b, w) * 100, "W": w}
            elif mapping == 2:
                vals = {"R": (_calc_relw(r, w) ** 0.4545) * 100,
                        "G": (_calc_relw(g, w) ** 0.4545) * 100,
                        "B": (_calc_relw(b, w) ** 0.4545) * 100, "W": w}
            elif mapping == 3:
                vals = {"R": r, "G": g, "B": b, "CCT": _calc_cct(r, g, b)}
            elif mapping == 4:
                vals = {"R": r, "G": g, "B": b, "Lux": _calc_ambient_light(g, it)}
            else:
                vals = {"CCT": _calc_cct(r, g, b), "Lux": _calc_ambient_light(g, it),
                        "Y": (r + g + b) / 3.0, "W": w}
            event.data["values"] = {k: round(v, 2) for k, v in vals.items()}
            return True
        except Exception as e:
            logger.error("VEML6040 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        it_opts = [
            {"value": 0, "label": "40ms (16496)"},
            {"value": 1, "label": "80ms (8248)"},
            {"value": 2, "label": "160ms (4124)"},
            {"value": 3, "label": "320ms (2062)"},
            {"value": 4, "label": "640ms (1031)"},
            {"value": 5, "label": "1280ms (515)"},
        ]
        map_opts = [
            {"value": 0, "label": "R, G, B, W"},
            {"value": 1, "label": "r, g, b, W - relative rgb [%]"},
            {"value": 2, "label": "r, g, b, W - relative rgb^Gamma [%]"},
            {"value": 3, "label": "R, G, B, Color Temperature [K]"},
            {"value": 4, "label": "R, G, B, Ambient Light [Lux]"},
            {"value": 5, "label": "Color Temp [K], Lux, Y, W"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", VEML6040_ADDR), "options": [{"value": 0x10, "label": "0x10"}]},
            {"name": "integration_time", "label": "Integration Time (Max Lux)", "type": "select", "value": self._config.get("integration_time", 0), "options": it_opts},
            {"name": "value_mapping", "label": "Value Mapping", "type": "select", "value": self._config.get("value_mapping", 0), "options": map_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == VEML6040_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = VEML6040_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"R": 0, "G": 0, "B": 0, "W": 0}
