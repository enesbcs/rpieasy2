from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.system_vars import resolve_template, LIVE_TASK_VALUES, LIVE_TASK_NAMES
from rpieasy2.core.system_chars import resolve_special_chars

logger = logging.getLogger("rpieasy2.plugin.p012")

LCD_ADDR = 0x27
LCD_WIDTH = 20
LCD_CHR = 1
LCD_CMD = 0
LCD_LINE_1 = 0x80
LCD_LINE_2 = 0xC0
LCD_LINE_3 = 0x94
LCD_LINE_4 = 0xD4
ENABLE = 0b00000100
BACKLIGHT = 0b00001000


class P012LCD2004(PluginBase):
    PLUGIN_ID = 12
    PLUGIN_NAME = "Display - LCD2004"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_NONE, value_count=0, timer_option=True, send_data_option=True)

    I2C_ADDRESSES = [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27,
                     0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F]

    def __init__(self):
        super().__init__()
        self._addr: int = LCD_ADDR
        self._config: dict[str, Any] = {}
        self._lines: list[str] = ["", "", "", ""]
        self._last_resolved_lines: dict[int, str] = {}
        self._button_pin: int = -1
        self._btn_state: int = 1
        self._display_on: bool = True
        self._splash_shown: bool = False
        self._last_refresh_time: float = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", LCD_ADDR))
        except (ValueError, TypeError):
            self._addr = LCD_ADDR
        try:
            self._button_pin = int(self._config.get("button_pin") or -1)
        except (ValueError, TypeError):
            self._button_pin = -1
        if not self._hw:
            return False
        try:
            await self._lcd_init()
            if self._button_pin > 0:
                self._hw.gpio.claim_input(self._button_pin)
                self._btn_state = self._hw.gpio.read(self._button_pin)
            lines = [
                self._config.get("line_1", ""),
                self._config.get("line_2", ""),
                self._config.get("line_3", ""),
                self._config.get("line_4", ""),
            ]
            self._last_refresh_time = time.time()
            self._last_resolved_lines.clear()
            for i, line in enumerate(lines):
                resolved = resolve_special_chars(resolve_template(line, LIVE_TASK_VALUES, LIVE_TASK_NAMES))
                if resolved:
                    await self._display(resolved, i)
                self._lines[i] = resolved
                self._last_resolved_lines[i] = resolved
            self._splash_shown = True
        except Exception as e:
            logger.error("LCD init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _lcd_byte(self, bits: int, mode: int) -> None:
        if not self._hw:
            return
        bl = BACKLIGHT
        await self._hw.i2c.write_byte(self._addr, mode | (bits & 0xF0) | bl)
        await self._hw.i2c.write_byte(self._addr, mode | (bits & 0xF0) | ENABLE | bl)
        await asyncio.sleep(0.0005)
        await self._hw.i2c.write_byte(self._addr, mode | (bits & 0xF0) & ~ENABLE | bl)
        await asyncio.sleep(0.0001)
        await self._hw.i2c.write_byte(self._addr, mode | ((bits << 4) & 0xF0) | bl)
        await self._hw.i2c.write_byte(self._addr, mode | ((bits << 4) & 0xF0) | ENABLE | bl)
        await asyncio.sleep(0.0005)
        await self._hw.i2c.write_byte(self._addr, mode | ((bits << 4) & 0xF0) & ~ENABLE | bl)
        await asyncio.sleep(0.0001)

    async def _lcd_init(self) -> None:
        await asyncio.sleep(0.05)
        for cmd in [0x33, 0x32, 0x28, 0x0C, 0x06, 0x01]:
            await self._lcd_byte(cmd, LCD_CMD)
        await asyncio.sleep(0.002)

    async def _display(self, msg: str, line: int) -> None:
        lines = [LCD_LINE_1, LCD_LINE_2, LCD_LINE_3, LCD_LINE_4]
        if line < 0 or line >= 4:
            return
        await self._lcd_byte(lines[line], LCD_CMD)
        for c in msg.ljust(LCD_WIDTH)[:LCD_WIDTH]:
            await self._lcd_byte(ord(c), LCD_CHR)

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if self._button_pin > 0 and self._hw:
            try:
                inv = bool(self._config.get("inverse_button", False))
                state = self._hw.gpio.read(self._button_pin)
                if inv:
                    state = 1 - state
                if state == 0 and self._btn_state == 1:
                    self._display_on = not self._display_on
                    if self._display_on:
                        for i, line in enumerate(self._lines):
                            await self._display(line, i)
                    else:
                        for i in range(4):
                            await self._display("", i)
                self._btn_state = state
            except Exception:
                pass
        if not self._display_on or not self._hw:
            return None
        interval = int(self._config.get("TDT") or 60)
        if interval <= 0:
            return None
        now = time.time()
        if now - self._last_refresh_time < interval:
            return None
        self._last_refresh_time = now
        for i in range(4):
            raw = self._config.get(f"line_{i + 1}", "")
            resolved = resolve_special_chars(resolve_template(raw, LIVE_TASK_VALUES, LIVE_TASK_NAMES))
            if resolved != self._last_resolved_lines.get(i, ""):
                await self._display(resolved, i)
                self._lines[i] = resolved
                self._last_resolved_lines[i] = resolved
        return None

    async def on_plugin_write(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            for i, part in enumerate(event.string1.split("\n")[:4]):
                resolved = resolve_special_chars(resolve_template(part, LIVE_TASK_VALUES, LIVE_TASK_NAMES))
                await self._display(resolved, i)
                self._lines[i] = resolved
                self._last_resolved_lines[i] = resolved
            return True
        except Exception as e:
            logger.error("LCD write failed: %s", e)
            return False

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"LCD": self._lines[0]}
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", LCD_ADDR), "options": [
                {"value": a, "label": hex(a)} for a in self.I2C_ADDRESSES
            ]},
            {"name": "display_size", "label": "Display Size", "type": "select", "value": self._config.get("display_size", 1), "options": [
                {"value": 1, "label": "2x16"}, {"value": 2, "label": "4x20"},
            ]},
            {"name": "display_timeout", "label": "Display Timeout (s)", "type": "number", "value": self._config.get("display_timeout", 0)},
            {"name": "command_mode", "label": "Command Mode", "type": "select", "value": self._config.get("command_mode", 0), "options": [
                {"value": 0, "label": "Continue"}, {"value": 1, "label": "Truncate"}, {"value": 2, "label": "Clear + Truncate"},
            ]},
            {"name": "button_pin", "label": "Display Button GPIO", "type": "select", "value": self._config.get("button_pin") or "", "options": [
                {"value": "", "label": "None"},
            ] + [{"value": p, "label": f"GPIO {p}"} for p in range(28)]},
            {"name": "inverse_button", "label": "Inversed Button Logic", "type": "checkbox", "value": self._config.get("inverse_button", False)},
            {"name": "line_1", "label": "Line 1", "type": "text", "value": self._config.get("line_1", "")},
            {"name": "line_2", "label": "Line 2", "type": "text", "value": self._config.get("line_2", "")},
            {"name": "line_3", "label": "Line 3", "type": "text", "value": self._config.get("line_3", "")},
            {"name": "line_4", "label": "Line 4", "type": "text", "value": self._config.get("line_4", "")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._lines = [self._config.get(f"line_{i + 1}", "") for i in range(4)]
        self._last_resolved_lines.clear()
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"LCD": ""}
