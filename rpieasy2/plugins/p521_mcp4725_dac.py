from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DIMMER
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p521")

MCP4725_CMD_WRITE = 0x40
MCP4725_CMD_WRITE_EEPROM = 0x60
MCP4725_ADDR_BASE = 0x60


class P521MCP4725DAC(PluginBase):
    PLUGIN_ID = 521
    PLUGIN_NAME = "Output - MCP4725 DAC"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_DIMMER, value_count=0, send_data_option=False, timer_option=False)
    I2C_ADDRESSES = [0x60, 0x61]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        return bool(self._hw)

    async def on_plugin_write(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        command = event.data.get("command", "").strip().lower()
        if command.startswith("dac"):
            parts = command.split(",")
            if len(parts) < 3:
                return False
            try:
                num = int(parts[1].strip())
                val = int(parts[2].strip())
            except (ValueError, IndexError):
                return False
            if num not in [0, 1] or val < 0 or val > 4095:
                return False
            addr = MCP4725_ADDR_BASE + num
            val = val & 0xFFF
            reg_data = [(val >> 4) & 0xFF, (val << 4) & 0xFF]
            try:
                await self._hw.i2c.write_i2c_block_data(addr, MCP4725_CMD_WRITE_EEPROM, reg_data)
                return True
            except Exception as e:
                logger.error("MCP4725 write failed: %s", e)
                return False
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = []
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}
