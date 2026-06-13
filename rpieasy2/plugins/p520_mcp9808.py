from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p520")

MCP9808_ADDR_DEFAULT = 0x18
MCP9808_REG_CONFIG = 0x01
MCP9808_REG_RESOLUTION = 0x08
MCP9808_REG_TEMP = 0x05


class P520MCP9808(PluginBase):
    PLUGIN_ID = 520
    PLUGIN_NAME = "Environment - MCP9808"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = MCP9808_ADDR_DEFAULT
        self._initialized = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        if event is not None:
            self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address")
        if self._addr is None:
            self._addr = MCP9808_ADDR_DEFAULT
        addr = int(self._addr)
        if addr > 0 and self._hw:
            try:
                i2c = self._hw.i2c
                await i2c.write_i2c_block_data(addr, MCP9808_REG_CONFIG, [0x00, 0x00])
                await i2c.write_byte_data(addr, MCP9808_REG_RESOLUTION, 0x03)
                self._initialized = True
            except Exception as e:
                logger.error("MCP9808 init failed: %s", e)
                self._initialized = False
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MCP9808_ADDR_DEFAULT)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._initialized:
            return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(int(self._addr), MCP9808_REG_TEMP, 2)
            ctemp = ((d[0] & 0x1F) * 256) + d[1]
            if ctemp > 4095:
                ctemp -= 8192
            temp = ctemp * 0.0625
            event.data["values"] = {"Temperature": round(temp, 2)}
            return True
        except Exception as e:
            logger.error("MCP9808 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x18, 0x20)]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MCP9808_ADDR_DEFAULT), "options": addr_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        old = self._addr
        self._config.update(event.data.get("form_data", {}))
        self._addr = self._config.get("address")
        if self._addr is None:
            self._addr = MCP9808_ADDR_DEFAULT
        if old != self._addr and int(self._addr) > 0:
            await self.on_plugin_init(None)
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0}
