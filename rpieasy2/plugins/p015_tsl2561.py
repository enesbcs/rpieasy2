from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD, SENSOR_V_TYPE_ILLUMINANCE, SENSOR_V_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p015")

TSL2561_ADDR = 0x29
TSL2561_CMD = 0x80
TSL2561_REG_CTRL = 0x00
TSL2561_REG_CHAN0 = 0x0C
TSL2561_REG_CHAN1 = 0x0E
DELAY_TIME = [0.015, 0.12, 0.45]


class P015TSL2561(PluginBase):
    PLUGIN_ID = 15
    PLUGIN_NAME = "Light/Lux - TSL2561"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_QUAD, value_count=4, formula_option=True, send_data_option=True, plugin_stats=True)

    I2C_ADDRESSES = [0x29, 0x39, 0x49]

    def __init__(self):
        super().__init__()
        self._addr: int = TSL2561_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", TSL2561_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, TSL2561_CMD | TSL2561_REG_CTRL, 0x03)
            integration = int(self._config.get("integration") or 2)
            gain = int(self._config.get("gain") or 1)
            timing = integration & 0x03
            if gain == 1:
                timing |= 0x10
            await i2c.write_byte_data(self._addr, TSL2561_CMD | 0x01, timing)
        except Exception as e:
            logger.error(f"TSL2561 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            async def rw(r): return await i2c.read_byte_data(self._addr, TSL2561_CMD | r)
            ch0 = ((await rw(TSL2561_REG_CHAN0 + 1)) << 8) | await rw(TSL2561_REG_CHAN0)
            ch1 = ((await rw(TSL2561_REG_CHAN1 + 1)) << 8) | await rw(TSL2561_REG_CHAN1)
            gain_idx = int(self._config.get("gain") or 1)
            if gain_idx == 0:
                ch0 *= 16
                ch1 *= 16
            if ch0 == 0:
                lux = 0.0
            else:
                r = ch1 / ch0
                if r <= 0.5:
                    lux = 0.0304 * ch0 - 0.062 * ch0 * (r ** 1.4)
                elif r <= 0.61:
                    lux = 0.0224 * ch0 - 0.031 * ch1
                elif r <= 0.8:
                    lux = 0.0128 * ch0 - 0.0153 * ch1
                elif r <= 1.3:
                    lux = 0.00146 * ch0 - 0.00112 * ch1
                else:
                    lux = 0.0
            lux = round(max(0, lux), 2)
            ratio = round(ch1 / ch0, 4) if ch0 else 0.0
            event.data["values"] = {
                "Lux": lux,
                "Infrared": ch1,
                "Broadband": ch0,
                "Ratio": ratio,
            }
            if self._config.get("sleep", False):
                await i2c.write_byte_data(self._addr, TSL2561_CMD | TSL2561_REG_CTRL, 0x00)
            return True
        except Exception as e:
            logger.error(f"TSL2561 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", TSL2561_ADDR), "options": [
                {"value": a, "label": hex(a)} for a in self.I2C_ADDRESSES
            ]},
            {"name": "integration", "label": "Integration Time", "type": "select", "value": self._config.get("integration", 2), "options": [
                {"value": 0, "label": "13.7 ms"},
                {"value": 1, "label": "101 ms"},
                {"value": 2, "label": "402 ms"},
            ]},
            {"name": "sleep", "label": "Send Sensor to Sleep", "type": "checkbox", "value": self._config.get("sleep", False)},
            {"name": "gain", "label": "Gain", "type": "select", "value": self._config.get("gain", 1), "options": [
                {"value": 0, "label": "No Gain"},
                {"value": 1, "label": "16x"},
                {"value": 2, "label": "Auto"},
                {"value": 3, "label": "Extended Auto"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_ILLUMINANCE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Lux": 0.0, "Infrared": 0, "Broadband": 0, "Ratio": 0.0}
