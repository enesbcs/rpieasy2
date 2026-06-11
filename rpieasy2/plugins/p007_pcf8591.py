from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p007")

PCF8591_ADDR_BASE = 0x48

INPUT_MODES = {
    0: 0b00000000,
    1: 0b00010000,
    2: 0b00100000,
    3: 0b00110000,
}
OUTPUT_ENABLED = 0b01000000

logger = logging.getLogger("rpieasy2.plugin.p007")

PCF8591_ADDR_BASE = 0x48

INPUT_MODES = {
    0: 0b00000000,
    1: 0b00010000,
    2: 0b00100000,
    3: 0b00110000,
}
OUTPUT_ENABLED = 0b01000000


class P007PCF8591(PluginBase):
    PLUGIN_ID = 7
    PLUGIN_NAME = "Analog input - PCF8591"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        formula_option=True,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        i2c_max100khz=True,
    )
    I2C_ADDRESSES = [0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = PCF8591_ADDR_BASE

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", PCF8591_ADDR_BASE)
        self._config.setdefault("port", 0)
        self._config.setdefault("input_mode", 0)
        self._config.setdefault("output_mode", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                addr = int(self._config.get("address", PCF8591_ADDR_BASE))
            except (ValueError, TypeError):
                addr = PCF8591_ADDR_BASE
            try:
                port = int(self._config.get("port", 0))
            except (ValueError, TypeError):
                port = 0
            try:
                input_mode = int(self._config.get("input_mode", 0))
            except (ValueError, TypeError):
                input_mode = 0
            try:
                output_mode = int(self._config.get("output_mode", 0))
            except (ValueError, TypeError):
                output_mode = 0
            config_reg = port | input_mode | output_mode
            await i2c.write_byte_data(addr, 0x00, config_reg)
            await asyncio.sleep(0.001)
            d = await i2c.read_i2c_block_data(addr, 0x00, 2)
            value = d[1] if len(d) > 1 else d[0]
            vals: dict[str, Any] = {"Analog": value}
            event.data["values"] = vals
            return True
        except Exception as e:
            logger.error("PCF8591 read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "").strip().lower()
        if command.startswith("pcfdac"):
            parts = command.split(",")
            if len(parts) < 3:
                return False
            try:
                port = int(parts[1].strip())
                value = int(parts[2].strip())
                if port < 1 or port > 8 or value < 0 or value > 255:
                    return False
                addr = 0x47 + port
                try:
                    input_mode = int(self._config.get("input_mode", 0))
                except (ValueError, TypeError):
                    input_mode = 0
                ctrl = (port - 1) % 4 | INPUT_MODES.get(input_mode, 0) | OUTPUT_ENABLED
                await self._hw.i2c.write_byte_data(addr, ctrl, value)
                return True
            except Exception as e:
                logger.error("PCF8591 DAC failed: %s", e)
                return False
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x48, 0x50)]
        port_opts = [{"value": i + 1, "label": f"A{i}"} for i in range(4)]
        im_opts = [
            {"value": 0, "label": "4 single-ended inputs"},
            {"value": 1, "label": "3 differential inputs"},
            {"value": 2, "label": "2 single-ended + differential"},
            {"value": 3, "label": "2 differential pairs"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", PCF8591_ADDR_BASE), "options": addr_opts},
            {"name": "port", "label": "Port", "type": "select", "value": self._config.get("port", 0), "options": port_opts},
            {"name": "input_mode", "label": "Input mode", "type": "select", "value": self._config.get("input_mode", 0), "options": im_opts},
            {"name": "output_mode", "label": "Enable Analog output (AOUT)", "type": "checkbox", "value": self._config.get("output_mode", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x48 <= addr <= 0x4F

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        try:
            event.data["address"] = int(self._config.get("address", PCF8591_ADDR_BASE))
        except (ValueError, TypeError):
            event.data["address"] = PCF8591_ADDR_BASE
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Analog": 0}
