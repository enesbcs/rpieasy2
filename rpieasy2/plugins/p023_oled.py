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
from rpieasy2.core.font import FONT_5X8, CHAR_TO_FONT_INDEX
from rpieasy2.core.system_chars import resolve_special_chars

logger = logging.getLogger("rpieasy2.plugin.p023")

OLED_ADDR = 0x3C
OLED_CMD = 0x00
OLED_DATA = 0x40
OLED_WIDTH = 128
OLED_HEIGHT = 64
OLED_PAGES = OLED_HEIGHT // 8

FONT = FONT_5X8


class P023OLED(PluginBase):
    PLUGIN_ID = 23
    PLUGIN_NAME = "Display - OLED SSD1306"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_NONE, value_count=0, timer_option=True, timer_optional=True, send_data_option=True)

    def __init__(self):
        super().__init__()
        self._addr: int = OLED_ADDR
        self._config: dict[str, Any] = {}
        self._btn_pin: int | None = -1
        self._btn_state: int = 1
        self._display_on: bool = True
        self._last_resolved_lines: dict[int, str] = {}
        self._last_refresh_time: float = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address") or OLED_ADDR
        self._btn_pin = int(self._config.get("button_pin") or -1)
        if not self._hw:
            return False
        try:
            await self._write_cmd(0xAE)
            cmds = [0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14,
                    0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0xCF,
                    0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0x2E, 0xAF]
            rotation = int(self._config.get("rotation") or 1)
            if rotation == 2:
                cmds[cmds.index(0xA1)] = 0xA0
                cmds[cmds.index(0xC8)] = 0xC0
            for cmd in cmds:
                await self._write_cmd(cmd)
            if self._btn_pin > 0 and self._hw:
                self._hw.gpio.claim_input(self._btn_pin)
                self._btn_state = self._hw.gpio.read(self._btn_pin)
            lines = [self._config.get(f"line_{i}") or "" for i in range(1, 9)]
            await self._clear()
            self._last_resolved_lines.clear()
            self._last_refresh_time = time.time()
            for i, line in enumerate(lines):
                resolved = resolve_template(line, LIVE_TASK_VALUES, LIVE_TASK_NAMES)
                if resolved:
                    await self._draw_text(resolved, i)
                self._last_resolved_lines[i] = resolved
        except Exception as e:
            logger.error("OLED init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _write_cmd(self, cmd: int) -> None:
        if self._hw:
            await self._hw.i2c.write_i2c_block_data(self._addr, OLED_CMD, [cmd])

    async def _write_data(self, data: list[int]) -> None:
        if not self._hw:
            return
        for i in range(0, len(data), 31):
            await self._hw.i2c.write_i2c_block_data(self._addr, OLED_DATA, data[i:i + 31])

    async def _clear(self) -> None:
        for p in range(OLED_PAGES):
            await self._clear_page(p)

    async def _clear_page(self, page: int) -> None:
        await self._write_cmd(0xB0 + page)
        await self._write_cmd(0x00)
        await self._write_cmd(0x10)
        await self._write_data([0x00] * OLED_WIDTH)

    async def _draw_text(self, text: str, page: int = 0) -> None:
        if page >= OLED_PAGES:
            return
        await self._write_cmd(0xB0 + page)
        await self._write_cmd(0x00)
        await self._write_cmd(0x10)
        data = []
        text = resolve_special_chars(str(text))
        for ch in text:
            idx = CHAR_TO_FONT_INDEX.get(ch, ord(ch) - 32)
            if 0 <= idx < len(FONT):
                data.extend(FONT[idx])
            if len(data) >= OLED_WIDTH:
                break
        data.extend([0x00] * (OLED_WIDTH - len(data)))
        await self._write_data(data[:OLED_WIDTH])

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if self._btn_pin > 0 and self._hw:
            try:
                inv = bool(self._config.get("inverse_button", False))
                state = self._hw.gpio.read(self._btn_pin)
                if inv:
                    state = 1 - state
                if state == 0 and self._btn_state == 1:
                    self._display_on = not self._display_on
                    await self._write_cmd(0xAF if self._display_on else 0xAE)
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
        lines = [self._config.get(f"line_{i}") or "" for i in range(1, 9)]
        for i, line in enumerate(lines[:OLED_PAGES]):
            resolved = resolve_template(line, LIVE_TASK_VALUES, LIVE_TASK_NAMES)
            if resolved != self._last_resolved_lines.get(i, ""):
                await self._clear_page(i)
                if resolved:
                    await self._draw_text(resolved, i)
                self._last_resolved_lines[i] = resolved
        self._last_refresh_time = time.time()
        return None

    async def on_plugin_write(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            await self._clear()
            for i, line in enumerate(event.string1.split("\n")[:OLED_PAGES]):
                await self._draw_text(line, i)
            return True
        except Exception as e:
            logger.error("OLED write failed: %s", e)
            return False

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Status": "OK"}
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address") or OLED_ADDR, "options": [
                {"value": 0x3C, "label": "0x3C"}, {"value": 0x3D, "label": "0x3D"},
            ]},
            {"name": "rotation", "label": "Rotation", "type": "select", "value": self._config.get("rotation") or 1, "options": [
                {"value": 1, "label": "Normal"}, {"value": 2, "label": "Rotated"},
            ]},
            {"name": "display_timeout", "label": "Display Timeout (s)", "type": "number", "value": self._config.get("display_timeout") or 0},
            {"name": "display_size", "label": "Display Size", "type": "select", "value": self._config.get("display_size") or 1, "options": [
                {"value": 1, "label": "128x64"}, {"value": 2, "label": "64x48"}, {"value": 3, "label": "128x32"},
            ]},
            {"name": "font_spacing", "label": "Font Width", "type": "select", "value": self._config.get("font_spacing") or 1, "options": [
                {"value": 1, "label": "Normal"}, {"value": 2, "label": "Optimized"},
            ]},
            {"name": "controller_type", "label": "Controller Type", "type": "select", "value": self._config.get("controller_type") or 0, "options": [
                {"value": 0, "label": "SSD1306"}, {"value": 1, "label": "SH1106"},
            ]},
            {"name": "button_pin", "label": "Button GPIO", "type": "select", "value": self._config.get("button_pin") or "", "options": [
                {"value": "", "label": "None"},
            ] + [{"value": p, "label": f"GPIO {p}"} for p in range(28)]},
            {"name": "inverse_button", "label": "Inversed Button Logic", "type": "checkbox", "value": self._config.get("inverse_button", False)},
        ]
        for i in range(1, 9):
            event.data["form"].append({"name": f"line_{i}", "label": f"Line {i}", "type": "text", "value": self._config.get(f"line_{i}") or ""})
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Status": ""}
