from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_TRIPLE, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p073")

TM1637_CMD_DATA = 0x40
TM1637_CMD_DISP = 0x80
TM1637_ADDR_AUTO = 0x40
TM1637_ADDR_FIXED = 0x44

SEGMENTS = {
    "0": 0x3F, "1": 0x06, "2": 0x5B, "3": 0x4F, "4": 0x66,
    "5": 0x6D, "6": 0x7D, "7": 0x07, "8": 0x7F, "9": 0x6F,
    "a": 0x77, "b": 0x7C, "c": 0x39, "d": 0x5E, "e": 0x79, "f": 0x71,
    "A": 0x77, "B": 0x7C, "C": 0x39, "D": 0x5E, "E": 0x79, "F": 0x71,
    "-": 0x40, "_": 0x08, " ": 0x00, ".": 0x80,
}


class P0737DGT(PluginBase):
    PLUGIN_ID = 73
    PLUGIN_NAME = "Display - 7-segment display"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_TRIPLE,
        vtype=SENSOR_TYPE_NONE,
        value_count=0,
        send_data_option=False,
        timer_option=True,
        timer_optional=True,
    )

    DISP_TM1637_4_COLON = 0
    DISP_TM1637_4_DOT = 1
    DISP_TM1637_6 = 2
    DISP_MAX7219_8 = 3
    DISP_74HC595 = 4

    OUTPUT_MANUAL = 0
    OUTPUT_CLOCK_BLINK = 1
    OUTPUT_CLOCK_NOBLINK = 2
    OUTPUT_CLOCK12_BLINK = 3
    OUTPUT_CLOCK12_NOBLINK = 4
    OUTPUT_DATE = 5

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._pin1: int = -1
        self._pin2: int = -1
        self._pin3: int = -1
        self._disp_type: int = 0
        self._output_type: int = 0
        self._brightness: int = 7
        self._digits: int = 4
        self._content: str = ""
        self._scroll_pos: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._pin1 = int(self._config.get("pin1", -1))
        except (ValueError, TypeError):
            self._pin1 = -1
        try:
            self._pin2 = int(self._config.get("pin2", -1))
        except (ValueError, TypeError):
            self._pin2 = -1
        try:
            self._pin3 = int(self._config.get("pin3", -1))
        except (ValueError, TypeError):
            self._pin3 = -1
        try:
            self._disp_type = int(self._config.get("disp_type", 0))
        except (ValueError, TypeError):
            self._disp_type = 0
        try:
            self._output_type = int(self._config.get("output_type", 0))
        except (ValueError, TypeError):
            self._output_type = 0
        try:
            self._brightness = int(self._config.get("brightness", 7))
        except (ValueError, TypeError):
            self._brightness = 7
        try:
            self._digits = int(self._config.get("digits", 4))
        except (ValueError, TypeError):
            self._digits = 4
        if self._hw:
            if self._pin1 >= 0:
                self._hw.gpio.claim_output(self._pin1)
            if self._pin2 >= 0:
                self._hw.gpio.claim_output(self._pin2)
            if self._pin3 >= 0:
                self._hw.gpio.claim_output(self._pin3)
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("disp_type", 0)
        self._config.setdefault("output_type", 0)
        self._config.setdefault("brightness", 7)
        self._config.setdefault("digits", 4)
        return True

    async def _tm1637_start(self) -> None:
        if not self._hw:
            return
        self._hw.gpio.write(self._pin2, 1)
        self._hw.gpio.write(self._pin1, 1)
        self._hw.gpio.write(self._pin2, 0)

    async def _tm1637_stop(self) -> None:
        if not self._hw:
            return
        self._hw.gpio.write(self._pin1, 0)
        self._hw.gpio.write(self._pin2, 1)
        self._hw.gpio.write(self._pin1, 1)

    async def _tm1637_write_byte(self, b: int) -> None:
        if not self._hw:
            return
        for _ in range(8):
            self._hw.gpio.write(self._pin1, 0)
            self._hw.gpio.write(self._pin2, b & 1)
            b >>= 1
            self._hw.gpio.write(self._pin1, 1)
        self._hw.gpio.write(self._pin1, 0)
        self._hw.gpio.write(self._pin1, 1)
        self._hw.gpio.write(self._pin2, 1)

    async def _tm1637_display(self, segments: list[int], colon: bool = False) -> None:
        if not self._hw:
            return
        await self._tm1637_start()
        await self._tm1637_write_byte(TM1637_CMD_DATA | TM1637_ADDR_AUTO)
        await self._tm1637_stop()
        await self._tm1637_start()
        await self._tm1637_write_byte(TM1637_CMD_DISP | 0x08)
        await self._tm1637_stop()
        await self._tm1637_start()
        await self._tm1637_write_byte(0xC0)
        for seg in segments:
            await self._tm1637_write_byte(seg)
        if colon:
            pass
        await self._tm1637_stop()
        await self._tm1637_start()
        await self._tm1637_write_byte(TM1637_CMD_DISP | 0x08 | min(self._brightness, 7))
        await self._tm1637_stop()

    async def _max7219_write(self, reg: int, data: int) -> None:
        if not self._hw:
            return
        self._hw.gpio.write(self._pin3, 0)
        for _ in range(8):
            self._hw.gpio.write(self._pin1, 0)
            self._hw.gpio.write(self._pin2, (reg >> 7) & 1)
            reg <<= 1
            self._hw.gpio.write(self._pin1, 1)
        for _ in range(8):
            self._hw.gpio.write(self._pin1, 0)
            self._hw.gpio.write(self._pin2, (data >> 7) & 1)
            data <<= 1
            self._hw.gpio.write(self._pin1, 1)
        self._hw.gpio.write(self._pin3, 1)

    async def _max7219_init(self) -> None:
        await self._max7219_write(0x09, 0xFF)
        await self._max7219_write(0x0A, self._brightness)
        await self._max7219_write(0x0B, self._digits - 1)
        await self._max7219_write(0x0C, 0x01)
        await self._max7219_write(0x0F, 0x00)

    async def _max7219_display(self, segments: list[int]) -> None:
        for i, seg in enumerate(segments):
            if i < 8:
                await self._max7219_write(i + 1, seg)

    def _text_to_segments(self, text: str) -> list[int]:
        segs = []
        for ch in text:
            s = SEGMENTS.get(ch, 0x00)
            segs.append(s & 0x7F)
        return segs

    async def _update_display(self, text: str) -> None:
        segs = self._text_to_segments(text)
        if self._disp_type <= self.DISP_TM1637_6:
            nd = self._digits
            segs = segs[:nd] + [0] * max(0, nd - len(segs))
            await self._tm1637_display(segs[:nd])
        elif self._disp_type == self.DISP_MAX7219_8:
            nd = min(self._digits, 8)
            segs = segs[:nd] + [0] * max(0, nd - len(segs))
            if not hasattr(self, "_max7219_inited"):
                await self._max7219_init()
                self._max7219_inited = True
            await self._max7219_display(segs[:nd])

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._output_type == self.OUTPUT_MANUAL:
            if self._content:
                await self._update_display(self._content)
            return True
        return True

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if self._output_type in (self.OUTPUT_CLOCK_BLINK, self.OUTPUT_CLOCK_NOBLINK,
                                 self.OUTPUT_CLOCK12_BLINK, self.OUTPUT_CLOCK12_NOBLINK):
            import time
            t = time.localtime()
            h = t.tm_hour
            m = t.tm_min
            if self._output_type >= self.OUTPUT_CLOCK12_BLINK:
                h = h % 12
                if h == 0:
                    h = 12
            text = f"{h:02d}{m:02d}"
            await self._update_display(text)
        elif self._output_type == self.OUTPUT_DATE:
            import time
            t = time.localtime()
            text = f"{t.tm_mday:02d}{t.tm_mon:02d}"
            await self._update_display(text)
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0] if parts else ""

        if cmd == "7don":
            self._config["brightness"] = self._brightness
            return True
        if cmd == "7doff":
            self._config["brightness"] = 0
            return True
        if cmd.startswith("7db,0"):
            b = int(parts[1]) if len(parts) > 1 else self._brightness
            self._brightness = max(0, min(15, b))
            return True
        if cmd == "7dn" and len(parts) > 1:
            self._content = parts[1]
            await self._update_display(self._content)
            return True
        if cmd == "7dt" and len(parts) > 1:
            self._content = parts[1]
            await self._update_display(self._content)
            return True
        if cmd == "7dtext" and len(parts) > 1:
            self._content = parts[1]
            await self._update_display(self._content)
            return True
        if cmd == "7output" and len(parts) > 1:
            self._output_type = int(parts[1])
            if self._output_type > self.OUTPUT_DATE:
                self._output_type = self.OUTPUT_MANUAL
            return True

        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "disp_type", "label": "Display Type", "type": "select",
             "value": self._config.get("disp_type", 0),
             "options": [
                 {"value": 0, "label": "TM1637 - 4 digit (colon)"},
                 {"value": 1, "label": "TM1637 - 4 digit (dots)"},
                 {"value": 2, "label": "TM1637 - 6 digit"},
                 {"value": 3, "label": "MAX7219 - 8 digit"},
                 {"value": 4, "label": "74HC595 - 2..8 digit"},
             ]},
            {"name": "output_type", "label": "Display Output", "type": "select",
             "value": self._config.get("output_type", 0),
             "options": [
                 {"value": 0, "label": "Manual"},
                 {"value": 1, "label": "Clock 24h - Blink"},
                 {"value": 2, "label": "Clock 24h - No Blink"},
                 {"value": 3, "label": "Clock 12h - Blink"},
                 {"value": 4, "label": "Clock 12h - No Blink"},
                 {"value": 5, "label": "Date"},
             ]},
            {"name": "brightness", "label": "Brightness", "type": "number",
             "value": self._config.get("brightness", 7), "min": 0, "max": 15},
            {"name": "digits", "label": "Number of Digits", "type": "number",
             "value": self._config.get("digits", 4), "min": 2, "max": 8},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._disp_type = int(self._config.get("disp_type", 0))
        self._output_type = int(self._config.get("output_type", 0))
        self._brightness = int(self._config.get("brightness", 7))
        self._digits = int(self._config.get("digits", 4))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "CLK/DIN Pin", "number": 1},
            {"label": "DIO/CLK Pin", "number": 2},
            {"label": "CS/LOAD Pin (optional)", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}
