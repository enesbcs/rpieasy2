from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p166")

GP8403_DEFAULT_ADDR = 0x5F

GP8403_CMD_SET_VOLTAGE = 0x10
GP8403_CMD_SET_RANGE = 0x20


class P166GP8403(PluginBase):
    PLUGIN_ID = 166
    PLUGIN_NAME = "Output - GP8403 Dual-channel DAC 0-10V"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        formula_option=True,
        value_count=2,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F]

    def __init__(self):
        super().__init__()
        self._addr: int = GP8403_DEFAULT_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", GP8403_DEFAULT_ADDR))
        except (ValueError, TypeError):
            self._addr = GP8403_DEFAULT_ADDR
        if not self._hw:
            return False
        try:
            try:
                max_v = int(self._config.get("max_voltage", 1))
            except (ValueError, TypeError):
                max_v = 1
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, GP8403_CMD_SET_RANGE, [0x00, max_v])
            ch0 = int(float(self._config.get("initial_0", 0)) * 409.5)
            ch1 = int(float(self._config.get("initial_1", 0)) * 409.5)
            await i2c.write_i2c_block_data(self._addr, GP8403_CMD_SET_VOLTAGE, [0x00, ch0 >> 4, (ch0 & 0x0F) << 4])
            await i2c.write_i2c_block_data(self._addr, GP8403_CMD_SET_VOLTAGE, [0x01, ch1 >> 4, (ch1 & 0x0F) << 4])
            return True
        except Exception as e:
            logger.error("GP8403 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", GP8403_DEFAULT_ADDR)
        self._config.setdefault("max_voltage", 1)
        self._config.setdefault("initial_0", 0)
        self._config.setdefault("initial_1", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {
            "Output0": float(self._config.get("initial_0", 0)),
            "Output1": float(self._config.get("initial_1", 0)),
        }
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "").lower()
        params = event.data.get("params", {})
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            if command in ("volt", "mvolt"):
                ch = int(params.get("channel", 0))
                val = float(params.get("value", 0))
                if command == "mvolt":
                    val = val / 1000.0
                dac = int(val * 409.5)
                dac = max(0, min(4095, dac))
                await i2c.write_i2c_block_data(self._addr, 0x10, [ch & 0x01, dac >> 4, (dac & 0x0F) << 4])
                return True
        except Exception as e:
            logger.error("GP8403 write failed: %s", e)
            return False
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x58, 0x60)]
        range_opts = [
            {"value": 0, "label": "0-5V"},
            {"value": 1, "label": "0-10V"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", GP8403_DEFAULT_ADDR), "options": addr_opts},
            {"name": "max_voltage", "label": "Output range", "type": "select", "value": self._config.get("max_voltage", 1), "options": range_opts},
            {"name": "initial_0", "label": "Initial value output 0 (V)", "type": "number", "value": str(self._config.get("initial_0", 0))},
            {"name": "initial_1", "label": "Initial value output 1 (V)", "type": "number", "value": str(self._config.get("initial_1", 0))},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", GP8403_DEFAULT_ADDR))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x58 <= addr <= 0x5F

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Output0": 0.0, "Output1": 0.0}
