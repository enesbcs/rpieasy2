from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p074")

TSL2591_ADDR = 0x29
TSL2591_REG_ENABLE = 0x00
TSL2591_REG_CONTROL = 0x01
TSL2591_REG_CHAN0_LOW = 0x14
TSL2591_COMMAND_BIT = 0xA0

TSL2591_INTEGRATION_TIMES = [100, 200, 300, 400, 500, 600]
TSL2591_GAIN_VALUES = [1, 25, 428, 9876]


class P074TSL2591(PluginBase):
    PLUGIN_ID = 74
    PLUGIN_NAME = "Light/Lux - TSL2591"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = TSL2591_ADDR
        self._lux: float = 0.0
        self._full: int = 0
        self._visible: int = 0
        self._ir: int = 0
        self._integration_active = False
        self._new_value = False
        self._start_integration = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = TSL2591_ADDR
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, TSL2591_COMMAND_BIT | TSL2591_REG_ENABLE, 0x03)
            itime = int(self._config.get("integration_time") or 1)
            gain = int(self._config.get("gain") or 1)
            ctrl = (itime & 0x07) << 4 | (gain & 0x03) << 0
            await i2c.write_byte_data(self._addr, TSL2591_COMMAND_BIT | TSL2591_REG_CONTROL, ctrl)
            return True
        except Exception as e:
            logger.error("TSL2591 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("integration_time", 1)
        self._config.setdefault("gain", 1)
        return True

    async def _start_read(self) -> None:
        if not self._hw:
            return
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(
                self._addr,
                TSL2591_COMMAND_BIT | TSL2591_REG_CHAN0_LOW,
                4,
            )
            self._full = struct.unpack("<H", bytes(d[:2]))[0]
            self._ir = struct.unpack("<H", bytes(d[2:4]))[0]
            self._visible = self._full - self._ir
            self._lux = self._calc_lux(self._full, self._ir)
            self._new_value = True
        except Exception as e:
            logger.error("TSL2591 read error: %s", e)

    def _calc_lux(self, full: int, ir: int) -> float:
        if full == 0 or full == 0xFFFF:
            return 0.0
        atime = TSL2591_INTEGRATION_TIMES[int(self._config.get("integration_time") or 1)]
        again = TSL2591_GAIN_VALUES[int(self._config.get("gain") or 1)]
        cpl = (atime * again) / 408.0
        lux = (full - ir) * (1.0 - ir / full) / cpl
        return round(max(0, lux), 2)

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if self._start_integration:
            self._start_integration = False
            self._integration_active = True
            await self._start_read()
            self._integration_active = False
        elif not self._integration_active and not self._new_value:
            self._start_integration = True
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._new_value:
            self._new_value = False
            event.data["values"] = {
                "Lux": self._lux,
                "Full": self._full,
                "Visible": self._visible,
                "IR": self._ir,
            }
            return True
        self._start_integration = True
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "integration_time", "label": "Integration Time", "type": "select", "value": self._config.get("integration_time", 1), "options": [
                {"value": 0, "label": "100ms"},
                {"value": 1, "label": "200ms"},
                {"value": 2, "label": "300ms"},
                {"value": 3, "label": "400ms"},
                {"value": 4, "label": "500ms"},
                {"value": 5, "label": "600ms"},
            ]},
            {"name": "gain", "label": "Gain", "type": "select", "value": self._config.get("gain", 1), "options": [
                {"value": 0, "label": "low gain (1x)"},
                {"value": 1, "label": "medium gain (25x)"},
                {"value": 2, "label": "medium gain (428x)"},
                {"value": 3, "label": "max gain (9876x)"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == TSL2591_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = TSL2591_ADDR
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_ILLUMINANCE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Lux": 0.0, "Full": 0, "Visible": 0, "IR": 0}
