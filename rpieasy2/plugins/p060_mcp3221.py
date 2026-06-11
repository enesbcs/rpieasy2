from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p060")

MCP3221_DEFAULT_ADDR = 0x4D


class P060MCP3221(PluginBase):
    PLUGIN_ID = 60
    PLUGIN_NAME = "Analog input - MCP3221"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        formula_option=True,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = MCP3221_DEFAULT_ADDR

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", MCP3221_DEFAULT_ADDR))
        except (ValueError, TypeError):
            self._addr = MCP3221_DEFAULT_ADDR
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MCP3221_DEFAULT_ADDR)
        self._config.setdefault("oversampling", False)
        self._config.setdefault("calibration", False)
        self._config.setdefault("adc1", 0)
        self._config.setdefault("out1", 0.0)
        self._config.setdefault("adc2", 4095)
        self._config.setdefault("out2", 100.0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 2)
            raw = (d[0] << 8) | d[1]
            value = raw >> 4 if raw > 4095 else raw
            if int(self._config.get("calibration", 0)):
                adc1 = int(self._config.get("adc1", 0))
                adc2 = int(self._config.get("adc2", 4095))
                out1 = float(self._config.get("out1", 0.0))
                out2 = float(self._config.get("out2", 100.0))
                if adc2 != adc1:
                    normalized = (value - adc1) / (adc2 - adc1)
                    value = normalized * (out2 - out1) + out1
            event.data["values"] = {"Analog": round(value, 2)}
            return True
        except Exception as e:
            logger.error("MCP3221 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x48, 0x50)]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MCP3221_DEFAULT_ADDR), "options": addr_opts},
            {"name": "oversampling", "label": "Oversampling", "type": "checkbox", "value": self._config.get("oversampling", False)},
            {"name": "calibration", "label": "Calibration Enabled", "type": "checkbox", "value": self._config.get("calibration", False)},
            {"name": "adc1", "label": "Point 1 ADC", "type": "number", "value": self._config.get("adc1", 0)},
            {"name": "out1", "label": "Point 1 Output", "type": "number", "value": str(self._config.get("out1", 0.0))},
            {"name": "adc2", "label": "Point 2 ADC", "type": "number", "value": self._config.get("adc2", 4095)},
            {"name": "out2", "label": "Point 2 Output", "type": "number", "value": str(self._config.get("out2", 100.0))},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", MCP3221_DEFAULT_ADDR))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x48 <= addr <= 0x4F

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Analog": 0}
