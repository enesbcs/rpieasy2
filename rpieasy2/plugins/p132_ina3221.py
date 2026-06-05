from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p132")

INA3221_ADDRS = [0x40 + i for i in range(16)]
INA3221_REG_CONFIG = 0x00
INA3221_REG_CH1_V = 0x01
INA3221_REG_CH1_S = 0x02
INA3221_REG_CH2_V = 0x03
INA3221_REG_CH2_S = 0x04
INA3221_REG_CH3_V = 0x05
INA3221_REG_CH3_S = 0x06


class P132INA3221(PluginBase):
    PLUGIN_ID = 132
    PLUGIN_NAME = "Energy (DC) - INA3221"
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

    CHANNEL_OPTIONS = [
        "Current channel 1",
        "Voltage channel 1",
        "Current channel 2",
        "Voltage channel 2",
        "Current channel 3",
        "Voltage channel 3",
    ]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x40
        self._shunt: int = 1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x40)
        self._shunt = int(self._config.get("shunt") or 1)
        if not self._hw: return False
        try:
            cfg = 0x7127
            await self._hw.i2c.write_i2c_block_data(self._addr, INA3221_REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF])
        except Exception:
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x40)
        self._config.setdefault("shunt", 1)
        self._config.setdefault("value1", 0)
        self._config.setdefault("value2", 1)
        self._config.setdefault("value3", 2)
        self._config.setdefault("value4", 3)
        return True

    async def _read_reg(self, reg: int) -> int:
        if not self._hw:
            return 0
        try:
            d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 2)
            return struct.unpack(">h", bytes(d[:2]))[0]
        except Exception:
            return 0

    async def _get_voltage(self, ch: int) -> float:
        reg = INA3221_REG_CH1_V + (ch - 1) * 2
        raw = await self._read_reg(reg)
        return raw * 0.001  # mV -> V

    async def _get_current(self, ch: int) -> float:
        reg = INA3221_REG_CH1_S + (ch - 1) * 2
        raw = await self._read_reg(reg)
        return raw * 0.005 / self._shunt  # shunt voltage to current

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        vals = {}
        channels = [
            int(self._config.get("value1") or 0),
            int(self._config.get("value2") or 1),
            int(self._config.get("value3") or 2),
            int(self._config.get("value4") or 3),
        ]
        for i, ch in enumerate(channels):
            if ch % 2 == 1:
                vals[self.CHANNEL_OPTIONS[ch]] = round(await self._get_voltage(ch // 2 + 1), 3)
            else:
                vals[self.CHANNEL_OPTIONS[ch]] = round(await self._get_current(ch // 2 + 1), 3)
        event.data["values"] = vals
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x40), "options": [
                {"value": a, "label": hex(a)} for a in INA3221_ADDRS
            ]},
            {"name": "shunt", "label": "Shunt resistor", "type": "select", "value": self._config.get("shunt", 1), "options": [
                {"value": 1, "label": "0.1 Ohm"},
                {"value": 10, "label": "0.01 Ohm"},
                {"value": 20, "label": "0.005 Ohm"},
            ]},
        ]
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        opts = [{"value": i, "label": self.CHANNEL_OPTIONS[i]} for i in range(6)]
        event.data["output_selector"] = {
            "name": None,
            "label": "Power Values",
            "options": opts,
            "fields": [
                {"name": "value1", "label": "Power value 1", "value": self._config.get("value1", 0)},
                {"name": "value2", "label": "Power value 2", "value": self._config.get("value2", 1)},
                {"name": "value3", "label": "Power value 3", "value": self._config.get("value3", 2)},
                {"name": "value4", "label": "Power value 4", "value": self._config.get("value4", 3)},
            ],
        }
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in INA3221_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        vtype1 = int(self._config.get("value1") or 0)
        vtype2 = int(self._config.get("value2") or 0)
        vtype3 = int(self._config.get("value3") or 0)
        vtype4 = int(self._config.get("value4") or 0)
        vtypes = []
        for vt in (vtype1, vtype2, vtype3, vtype4):
            if vt == 0:
                vtypes.append(SENSOR_V_TYPE_CURRENT)
            elif vt == 1:
                vtypes.append(SENSOR_V_TYPE_VOLTAGE)
            elif vt in (2, 3, 4, 5):
                vtypes.append(SENSOR_V_TYPE_CURRENT)
            else:
                vtypes.append(SENSOR_V_TYPE_SINGLE)
        event.data["vtypes"] = vtypes
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Value1": 0.0, "Value2": 0.0, "Value3": 0.0, "Value4": 0.0}
