from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p061")


class P061KeyPad(PluginBase):
    PLUGIN_ID = 61
    PLUGIN_NAME = "Keypad - PCF8574 / MCP23017 / PCF8575"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        custom_vtype_var=True,
    )
    I2C_ADDRESSES = [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27,
                     0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F]

    KEYPAD_MCP23017_MATRIX = 0
    KEYPAD_PCF8574_MATRIX = 1
    KEYPAD_PCF8574_DIRECT = 2
    KEYPAD_MCP23017_DIRECT = 3
    KEYPAD_PCF8575_MATRIX = 4
    KEYPAD_PCF8575_DIRECT = 5

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x20
        self._keypad_type: int = 0
        self._i2c = None
        self._last_scancode: int = -1
        self._rows: int = 4
        self._cols: int = 4

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("i2c_addr", 0x20))
        except (ValueError, TypeError):
            self._addr = 0x20
        try:
            self._keypad_type = int(self._config.get("keypad_type", 0))
        except (ValueError, TypeError):
            self._keypad_type = 0
        if not self._hw:
            return False
        self._i2c = self._hw.i2c
        self._setup_dimensions()
        return True

    def _setup_dimensions(self) -> None:
        if self._keypad_type == self.KEYPAD_MCP23017_MATRIX:
            self._rows, self._cols = 9, 8
        elif self._keypad_type == self.KEYPAD_PCF8574_MATRIX:
            self._rows, self._cols = 5, 4
        elif self._keypad_type == self.KEYPAD_PCF8574_DIRECT:
            self._rows, self._cols = 8, 1
        elif self._keypad_type == self.KEYPAD_MCP23017_DIRECT:
            self._rows, self._cols = 16, 1
        elif self._keypad_type == self.KEYPAD_PCF8575_MATRIX:
            self._rows, self._cols = 9, 8
        elif self._keypad_type == self.KEYPAD_PCF8575_DIRECT:
            self._rows, self._cols = 16, 1
        else:
            self._rows, self._cols = 4, 4

    async def _i2c_write(self, value: int) -> None:
        if not self._i2c:
            return
        try:
            await self._i2c.write_byte(self._addr, value & 0xFF)
        except Exception:
            pass

    async def _i2c_read(self) -> int:
        if not self._i2c:
            return 0xFF
        try:
            return await self._i2c.read_byte(self._addr)
        except Exception:
            return 0xFF

    async def _i2c_write_word(self, value: int) -> None:
        if not self._i2c:
            return
        try:
            data = [value & 0xFF, (value >> 8) & 0xFF]
            await self._i2c.write_i2c_block_data(self._addr, 0, data)
        except Exception:
            pass

    async def _i2c_read_word(self) -> int:
        if not self._i2c:
            return 0xFFFF
        try:
            buf = await self._i2c.read_bytes(self._addr, 2)
            if len(buf) >= 2:
                return buf[0] | (buf[1] << 8)
            return 0xFFFF
        except Exception:
            return 0xFFFF

    async def _scan_matrix_8bit(self) -> int:
        for row in range(self._rows):
            mask = ~(1 << row) & 0xFF
            await self._i2c_write(mask)
            import asyncio
            await asyncio.sleep(0.001)
            col_data = await self._i2c_read()
            if col_data != 0xFF:
                for col in range(self._cols):
                    if not (col_data & (1 << col)):
                        return 16 * col + row + 1
        return 0

    async def _scan_direct_8bit(self) -> int:
        val = await self._i2c_read()
        if val != 0xFF:
            for i in range(8):
                if not (val & (1 << i)):
                    return i + 1
        return 0

    async def _scan_matrix_16bit(self) -> int:
        for row in range(self._rows):
            mask = ~(1 << row) & 0xFFFF
            if row < 8:
                await self._i2c_write(mask & 0xFF)
            else:
                await self._i2c_write_word(mask)
            import asyncio
            await asyncio.sleep(0.001)
            col_data = await self._i2c_read_word()
            if col_data != 0xFFFF:
                for col in range(self._cols):
                    if not (col_data & (1 << col)):
                        return 16 * col + row + 1
        return 0

    async def _scan_direct_16bit(self) -> int:
        val = await self._i2c_read_word()
        if val != 0xFFFF:
            for i in range(16):
                if not (val & (1 << i)):
                    return i + 1
        return 0

    async def _scan(self) -> int:
        if self._keypad_type == self.KEYPAD_MCP23017_MATRIX:
            return await self._scan_matrix_16bit()
        elif self._keypad_type == self.KEYPAD_PCF8574_MATRIX:
            return await self._scan_matrix_8bit()
        elif self._keypad_type == self.KEYPAD_PCF8574_DIRECT:
            return await self._scan_direct_8bit()
        elif self._keypad_type == self.KEYPAD_MCP23017_DIRECT:
            return await self._scan_direct_16bit()
        elif self._keypad_type == self.KEYPAD_PCF8575_MATRIX:
            return await self._scan_matrix_16bit()
        elif self._keypad_type == self.KEYPAD_PCF8575_DIRECT:
            return await self._scan_direct_16bit()
        return 0

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        scancode = await self._scan()
        if scancode != self._last_scancode:
            self._last_scancode = scancode
            event.data["values"] = {"ScanCode": float(scancode)}
            return True
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("i2c_addr", 0x20)
        self._config.setdefault("keypad_type", 0)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "keypad_type", "label": "Chip (Mode)", "type": "select",
             "value": self._config.get("keypad_type", 0),
             "options": [
                 {"value": 0, "label": "MCP23017 (Matrix 9x8)"},
                 {"value": 1, "label": "PCF8574 (Matrix 5x4)"},
                 {"value": 2, "label": "PCF8574 (Direct 8)"},
                 {"value": 3, "label": "MCP23017 (Direct 16)"},
                 {"value": 4, "label": "PCF8575 (Matrix 9x8)"},
                 {"value": 5, "label": "PCF8575 (Direct 16)"},
             ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("i2c_addr", 0x20))
        self._keypad_type = int(self._config.get("keypad_type", 0))
        self._setup_dimensions()
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.par1 in self.I2C_ADDRESSES

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"ScanCode": 0.0}
