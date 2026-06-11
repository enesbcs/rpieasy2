from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SPI, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p141")

LCD_WIDTH = 84
LCD_HEIGHT = 48
P141_NLINES = 10
P141_NCHARS = 22

PCD8544_POWERDOWN = 0x04
PCD8544_ENTRYMODE = 0x02
PCD8544_EXTENDED_INSTR = 0x01
PCD8544_DISPLAYBLANK = 0x00
PCD8544_DISPLAYNORMAL = 0x04
PCD8544_DISPLAYALLON = 0x01
PCD8544_DISPLAYINVERTED = 0x05
PCD8544_FUNCTIONSET = 0x20
PCD8544_DISPLAYCONTROL = 0x08
PCD8544_SETYADDR = 0x40
PCD8544_SETXADDR = 0x80
PCD8544_SETTEMP = 0x04
PCD8544_SETBIAS = 0x10
PCD8544_SETVOP = 0x80


class P141PCD8544(PluginBase):
    PLUGIN_ID = 141
    PLUGIN_NAME = "Display - PCD8544 Nokia 5110 LCD"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SPI,
        vtype=SENSOR_TYPE_NONE,
        value_count=0,
        send_data_option=False,
        timer_option=True,
        timer_optional=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._cs_pin: int = -1
        self._dc_pin: int = -1
        self._rst_pin: int = -1
        self._bl_pin: int = -1
        self._contrast: int = 60
        self._backlight_pct: int = 50
        self._inverted: bool = False
        self._display_buffer: list[int] = [0] * (LCD_WIDTH * LCD_HEIGHT // 8)
        self._lines: list[str] = [""] * P141_NLINES
        self._spi_bus: int = 0
        self._spi_dev: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._cs_pin = int(self._config.get("cs_pin", -1))
        except (ValueError, TypeError):
            self._cs_pin = -1
        try:
            self._dc_pin = int(self._config.get("dc_pin", -1))
        except (ValueError, TypeError):
            self._dc_pin = -1
        try:
            self._rst_pin = int(self._config.get("rst_pin", -1))
        except (ValueError, TypeError):
            self._rst_pin = -1
        try:
            self._bl_pin = int(self._config.get("backlight_pin", -1))
        except (ValueError, TypeError):
            self._bl_pin = -1
        try:
            self._contrast = int(self._config.get("contrast", 60))
        except (ValueError, TypeError):
            self._contrast = 60
        try:
            self._backlight_pct = int(self._config.get("backlight_pct", 50))
        except (ValueError, TypeError):
            self._backlight_pct = 50
        self._inverted = self._config.get("inverted", False)
        try:
            self._spi_bus = int(self._config.get("spi_bus", 0))
        except (ValueError, TypeError):
            self._spi_bus = 0
        try:
            self._spi_dev = int(self._config.get("spi_dev", 0))
        except (ValueError, TypeError):
            self._spi_dev = 0
        raw_lines = self._config.get("display_lines", [""] * P141_NLINES)
        for i in range(min(P141_NLINES, len(raw_lines))):
            self._lines[i] = str(raw_lines[i])
        if self._hw:
            if self._cs_pin >= 0:
                self._hw.gpio.claim_output(self._cs_pin)
            if self._dc_pin >= 0:
                self._hw.gpio.claim_output(self._dc_pin)
            if self._rst_pin >= 0:
                self._hw.gpio.claim_output(self._rst_pin)
            if self._bl_pin >= 0:
                self._hw.gpio.claim_output(self._bl_pin)
        await self._init_display()
        return True

    async def _init_display(self) -> None:
        if not self._hw:
            return
        if self._rst_pin >= 0:
            self._hw.gpio.write(self._rst_pin, 0)
            await asyncio.sleep(0.01)
            self._hw.gpio.write(self._rst_pin, 1)
            await asyncio.sleep(0.01)
        await self._write_cmd(PCD8544_FUNCTIONSET | PCD8544_EXTENDED_INSTR)
        await self._write_cmd(PCD8544_SETVOP | int(self._contrast * 0.8))
        await self._write_cmd(PCD8544_SETTEMP)
        await self._write_cmd(PCD8544_SETBIAS | 0x04)
        await self._write_cmd(PCD8544_FUNCTIONSET)
        await self._write_cmd(PCD8544_DISPLAYCONTROL | PCD8544_DISPLAYNORMAL)
        self._display_buffer = [0] * (LCD_WIDTH * LCD_HEIGHT // 8)
        await self._set_backlight(self._backlight_pct > 0)

    async def _set_backlight(self, on: bool) -> None:
        if self._bl_pin >= 0 and self._hw:
            self._hw.gpio.write(self._bl_pin, 1 if on else 0)

    async def _write_cmd(self, cmd: int) -> None:
        if not self._hw:
            return
        if self._dc_pin >= 0:
            self._hw.gpio.write(self._dc_pin, 0)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 0)
        self._hw.spi.transfer(self._spi_bus, self._spi_dev, [cmd])
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 1)

    async def _write_data(self, data: list[int]) -> None:
        if not self._hw:
            return
        if self._dc_pin >= 0:
            self._hw.gpio.write(self._dc_pin, 1)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 0)
        self._hw.spi.transfer(self._spi_bus, self._spi_dev, data)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 1)

    async def _clear_display(self) -> None:
        self._display_buffer = [0] * (LCD_WIDTH * LCD_HEIGHT // 8)
        await self._write_cmd(PCD8544_SETYADDR)
        await self._write_cmd(PCD8544_SETXADDR)
        await self._write_data(self._display_buffer)

    async def _draw_char(self, ch: str, x: int, y: int) -> None:
        if ord(ch) < 32 or ord(ch) > 126:
            ch = " "
        idx = ord(ch) - 32
        font_data = FONT5x7[idx] if idx < len(FONT5x7) else FONT5x7[0]
        for col in range(5):
            byte = font_data[col]
            if self._inverted:
                byte = ~byte & 0x7F
            if 0 <= y < LCD_HEIGHT // 8 and 0 <= x + col < LCD_WIDTH:
                pos = y * LCD_WIDTH + x + col
                if pos < len(self._display_buffer):
                    self._display_buffer[pos] = byte

    async def _draw_text(self, text: str, line: int) -> None:
        y = line * 6 // 8
        x_offset = 0
        for ch in text:
            await self._draw_char(ch, x_offset, y)
            x_offset += 6
            if x_offset >= LCD_WIDTH:
                break

    async def _update_display(self) -> None:
        await self._write_cmd(PCD8544_SETYADDR)
        await self._write_cmd(PCD8544_SETXADDR)
        await self._write_data(self._display_buffer)

    async def on_plugin_read(self, event: Event) -> bool | None:
        self._display_buffer = [0] * (LCD_WIDTH * LCD_HEIGHT // 8)
        for i, line in enumerate(self._lines):
            if line:
                await self._draw_text(line, i)
        await self._update_display()
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0] if parts else ""

        if cmd == "pcd8544":
            await self._clear_display()
            text = ",".join(parts[1:])
            for i, line in enumerate(text.split("\\n")[:P141_NLINES]):
                if line:
                    await self._draw_text(line, i)
            await self._update_display()
            return True
        if cmd == "pcd8544,off":
            await self._write_cmd(PCD8544_DISPLAYCONTROL | PCD8544_DISPLAYBLANK)
            return True
        if cmd == "pcd8544,on":
            await self._write_cmd(PCD8544_DISPLAYCONTROL | PCD8544_DISPLAYNORMAL)
            return True
        if cmd.startswith("pcd8544,inv"):
            self._inverted = not self._inverted if len(parts) < 3 else bool(int(parts[2]))
            return True

        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("contrast", 60)
        self._config.setdefault("backlight_pct", 50)
        self._config.setdefault("inverted", False)
        self._config.setdefault("spi_bus", 0)
        self._config.setdefault("spi_dev", 0)
        self._config.setdefault("display_lines", [""] * P141_NLINES)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "cs_pin", "label": "SCE Pin (CS)", "type": "number",
             "value": self._config.get("cs_pin", -1)},
            {"name": "dc_pin", "label": "D/C Pin", "type": "number",
             "value": self._config.get("dc_pin", -1)},
            {"name": "rst_pin", "label": "RST Pin", "type": "number",
             "value": self._config.get("rst_pin", -1)},
            {"name": "backlight_pin", "label": "Backlight Pin", "type": "number",
             "value": self._config.get("backlight_pin", -1)},
            {"name": "backlight_pct", "label": "Backlight %", "type": "number",
             "value": self._config.get("backlight_pct", 50), "min": 0, "max": 100},
            {"name": "contrast", "label": "Contrast %", "type": "number",
             "value": self._config.get("contrast", 60), "min": 0, "max": 100},
            {"name": "inverted", "label": "Inverted display", "type": "checkbox",
             "value": self._config.get("inverted", False)},
            {"name": "spi_bus", "label": "SPI Bus", "type": "number",
             "value": self._config.get("spi_bus", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "SCE (CS) Pin", "number": 1},
            {"label": "D/C Pin", "number": 2},
            {"label": "RST Pin (optional)", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}


FONT5x7 = [
    [0x00, 0x00, 0x00, 0x00, 0x00],
    [0x00, 0x00, 0x5F, 0x00, 0x00],
    [0x00, 0x07, 0x00, 0x07, 0x00],
    [0x14, 0x7F, 0x14, 0x7F, 0x14],
    [0x24, 0x2A, 0x7F, 0x2A, 0x12],
    [0x23, 0x13, 0x08, 0x64, 0x62],
    [0x36, 0x49, 0x55, 0x22, 0x50],
    [0x00, 0x05, 0x03, 0x00, 0x00],
    [0x00, 0x1C, 0x22, 0x41, 0x00],
    [0x00, 0x41, 0x22, 0x1C, 0x00],
    [0x08, 0x2A, 0x1C, 0x2A, 0x08],
    [0x08, 0x08, 0x3E, 0x08, 0x08],
    [0x00, 0x50, 0x30, 0x00, 0x00],
    [0x08, 0x08, 0x08, 0x08, 0x08],
    [0x00, 0x60, 0x60, 0x00, 0x00],
    [0x20, 0x10, 0x08, 0x04, 0x02],
    [0x3E, 0x51, 0x49, 0x45, 0x3E],
    [0x00, 0x42, 0x7F, 0x40, 0x00],
    [0x42, 0x61, 0x51, 0x49, 0x46],
    [0x21, 0x41, 0x45, 0x4B, 0x31],
    [0x18, 0x14, 0x12, 0x7F, 0x10],
    [0x27, 0x45, 0x45, 0x45, 0x39],
    [0x3C, 0x4A, 0x49, 0x49, 0x30],
    [0x01, 0x71, 0x09, 0x05, 0x03],
    [0x36, 0x49, 0x49, 0x49, 0x36],
    [0x06, 0x49, 0x49, 0x29, 0x1E],
    [0x00, 0x36, 0x36, 0x00, 0x00],
    [0x00, 0x56, 0x36, 0x00, 0x00],
    [0x00, 0x08, 0x14, 0x22, 0x41],
    [0x14, 0x14, 0x14, 0x14, 0x14],
    [0x41, 0x22, 0x14, 0x08, 0x00],
    [0x02, 0x01, 0x51, 0x09, 0x06],
    [0x32, 0x49, 0x79, 0x41, 0x3E],
    [0x7E, 0x11, 0x11, 0x11, 0x7E],
    [0x7F, 0x49, 0x49, 0x49, 0x36],
    [0x3E, 0x41, 0x41, 0x41, 0x22],
    [0x7F, 0x41, 0x41, 0x22, 0x1C],
    [0x7F, 0x49, 0x49, 0x49, 0x41],
    [0x7F, 0x09, 0x09, 0x01, 0x01],
    [0x3E, 0x41, 0x41, 0x51, 0x32],
    [0x7F, 0x08, 0x08, 0x08, 0x7F],
    [0x00, 0x41, 0x7F, 0x41, 0x00],
    [0x20, 0x40, 0x41, 0x3F, 0x01],
    [0x7F, 0x08, 0x14, 0x22, 0x41],
    [0x7F, 0x40, 0x40, 0x40, 0x40],
    [0x7F, 0x02, 0x04, 0x02, 0x7F],
    [0x7F, 0x04, 0x08, 0x10, 0x7F],
    [0x3E, 0x41, 0x41, 0x41, 0x3E],
    [0x7F, 0x09, 0x09, 0x09, 0x06],
    [0x3E, 0x41, 0x51, 0x21, 0x5E],
    [0x7F, 0x09, 0x19, 0x29, 0x46],
    [0x46, 0x49, 0x49, 0x49, 0x31],
    [0x01, 0x01, 0x7F, 0x01, 0x01],
    [0x3F, 0x40, 0x40, 0x40, 0x3F],
    [0x1F, 0x20, 0x40, 0x20, 0x1F],
    [0x7F, 0x20, 0x18, 0x20, 0x7F],
    [0x63, 0x14, 0x08, 0x14, 0x63],
    [0x03, 0x04, 0x78, 0x04, 0x03],
    [0x61, 0x51, 0x49, 0x45, 0x43],
    [0x00, 0x00, 0x7F, 0x41, 0x41],
    [0x02, 0x04, 0x08, 0x10, 0x20],
    [0x41, 0x41, 0x7F, 0x00, 0x00],
    [0x04, 0x02, 0x01, 0x02, 0x04],
    [0x40, 0x40, 0x40, 0x40, 0x40],
    [0x00, 0x01, 0x02, 0x04, 0x00],
    [0x20, 0x54, 0x54, 0x54, 0x78],
    [0x7F, 0x48, 0x44, 0x44, 0x38],
    [0x38, 0x44, 0x44, 0x44, 0x20],
    [0x38, 0x44, 0x44, 0x48, 0x7F],
    [0x38, 0x54, 0x54, 0x54, 0x18],
    [0x08, 0x7E, 0x09, 0x01, 0x02],
    [0x08, 0x14, 0x54, 0x54, 0x3C],
    [0x7F, 0x08, 0x04, 0x04, 0x78],
    [0x00, 0x44, 0x7D, 0x40, 0x00],
    [0x20, 0x40, 0x44, 0x3D, 0x00],
    [0x00, 0x7F, 0x10, 0x28, 0x44],
    [0x00, 0x41, 0x7F, 0x40, 0x00],
    [0x7C, 0x04, 0x18, 0x04, 0x78],
    [0x7C, 0x08, 0x04, 0x04, 0x78],
    [0x38, 0x44, 0x44, 0x44, 0x38],
    [0x7C, 0x14, 0x14, 0x14, 0x08],
    [0x08, 0x14, 0x14, 0x18, 0x7C],
    [0x7C, 0x08, 0x04, 0x04, 0x08],
    [0x48, 0x54, 0x54, 0x54, 0x20],
    [0x04, 0x3F, 0x44, 0x40, 0x20],
    [0x3C, 0x40, 0x40, 0x20, 0x7C],
    [0x1C, 0x20, 0x40, 0x20, 0x1C],
    [0x3C, 0x40, 0x30, 0x40, 0x3C],
    [0x44, 0x28, 0x10, 0x28, 0x44],
    [0x0C, 0x50, 0x50, 0x50, 0x3C],
    [0x44, 0x64, 0x54, 0x4C, 0x44],
    [0x00, 0x08, 0x36, 0x41, 0x00],
    [0x00, 0x00, 0x7F, 0x00, 0x00],
    [0x00, 0x41, 0x36, 0x08, 0x00],
    [0x08, 0x08, 0x2A, 0x1C, 0x08],
    [0x08, 0x1C, 0x2A, 0x08, 0x08],
]
