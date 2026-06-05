from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
    _PIL_ERROR: str | None = None
except Exception as _pil_err:
    _PIL_AVAILABLE = False
    _PIL_ERROR = str(_pil_err)
    Image = None
    ImageDraw = None
    ImageFont = None

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.fontvar import (
    FONT_DEFAULT, FONT_SMALL, FONT_MEDIUM, FONT_LARGE, FONT_XLARGE,
)
from rpieasy2.core.system_chars import resolve_special_chars
from rpieasy2.core.system_vars import resolve_template, LIVE_TASK_VALUES, LIVE_TASK_NAMES

logger = logging.getLogger("rpieasy2.plugin.p036")

OLED_CMD = 0x00
OLED_DATA = 0x40

P36_NLINES = 12
P36_DEBOUNCE_THRESHOLD = 5
P36_SCROLL_LINE_SPEED = 2
P36_TICKER_INTERVAL = 3

DISP_128x64 = 0
DISP_128x32 = 1
DISP_64x48 = 2

SCROLL_VERY_SLOW = 0
SCROLL_SLOW = 1
SCROLL_FAST = 2
SCROLL_VERY_FAST = 3
SCROLL_INSTANT = 4
SCROLL_TICKER = 5

HEADER_NONE = 0
HEADER_SSID = 1
HEADER_SYSNAME = 2
HEADER_IP = 3
HEADER_MAC = 4
HEADER_RSSI = 5
HEADER_BSSID = 6
HEADER_WIFI_CH = 7
HEADER_UNIT = 8
HEADER_SYSLOAD = 9
HEADER_SYSHEAP = 10
HEADER_SYSSTACK = 11
HEADER_TIME = 12
HEADER_DATE = 13
HEADER_PAGE_NO = 14
HEADER_USERDEF1 = 15
HEADER_USERDEF2 = 16

ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2
ALIGN_GLOBAL = 7

FONT_NAMES = {
    FONT_DEFAULT: "5x8",
    FONT_SMALL: "10px",
    FONT_MEDIUM: "13px",
    FONT_LARGE: "16px",
    FONT_XLARGE: "24px",
}

DISPLAY_SIZES = {
    DISP_128x64: {"width": 128, "height": 64, "pages": 8, "max_lines": 4,
                  "pix_left": 0, "wifi_left": 113, "wifi_width": 15, "mux": 0x3F, "compins": 0x12},
    DISP_128x32: {"width": 128, "height": 32, "pages": 4, "max_lines": 2,
                  "pix_left": 0, "wifi_left": 113, "wifi_width": 15, "mux": 0x1F, "compins": 0x02},
    DISP_64x48: {"width": 64, "height": 48, "pages": 6, "max_lines": 3,
                 "pix_left": 32, "wifi_left": 32, "wifi_width": 10, "mux": 0x2F, "compins": 0x12},
}

PAGE_SCROLL_IDLE = 0
PAGE_SCROLL_IN = 1
PAGE_SCROLL_OUT = 2

_HEADER_TOP = 10
_FOOTER_TOP = 56
_FONT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static"))
_FONT_PATH = os.path.join(_FONT_DIR, "UbuntuMono-R.ttf")


class P036FrameOLED(PluginBase):
    PLUGIN_ID = 36
    PLUGIN_NAME = "Display - OLED SSD1306/SH1106 Framed"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_NONE,
        value_count=0, timer_option=True, timer_optional=True,
        send_data_option=True)

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x3C
        self._controller: int = 0
        self._sh1106_offset: int = 0
        self._width: int = 128
        self._height: int = 64
        self._pages: int = 8
        self._pix_left: int = 0
        self._wifi_left: int = 113
        self._wifi_width: int = 15

        self._lines: list[str] = [""] * P36_NLINES
        self._resolved_lines: list[str] = [""] * P36_NLINES
        self._line_settings: list[int] = [0xFF] * P36_NLINES

        self._lines_per_frame: int = 1
        self._current_frame: int = 0
        self._total_frames: int = 1
        self._valid_frames: list[list[tuple[int, str]]] = []

        self._display_on: bool = True
        self._display_timer: int = 0
        self._display_timer_max: int = 0
        self._contrast: int = 128

        self._scroll_speed: int = SCROLL_VERY_SLOW
        self._scroll_without_wifi: bool = True
        self._scroll_tick: int = 0

        self._hide_header: bool = False
        self._header_content: int = HEADER_SYSNAME
        self._header_content_alt: int = HEADER_SYSNAME
        self._header_alternate: bool = False
        self._header_count: int = 0
        self._time_format: int = 0
        self._userdef1: str = ""
        self._userdef2: str = ""

        self._hide_footer: bool = False
        self._hide_logo: bool = False
        self._logo_shown: bool = False

        self._btn_pin: int = -1
        self._btn_state: int = 1
        self._btn_last_state: int = 1
        self._btn_debounce: int = 0
        self._btn_repeat: int = 0
        self._btn_inversed: bool = False
        self._btn_step_pages: bool = False
        self._btn_pin_mode: int = 0

        self._send_events: int = 0
        self._wake_on_receive: bool = True
        self._reduce_line_no: bool = False
        self._global_alignment: int = ALIGN_CENTER

        self._last_template_refresh: float = 0
        self._display_interval: int = 60
        self._ticker_tick: int = 0

        self._scroll_offsets: list[int] = [0] * P36_NLINES
        self._scroll_directions: list[int] = [0] * P36_NLINES
        self._scroll_waits: list[int] = [0] * P36_NLINES

        self._page_scroll_state: int = PAGE_SCROLL_IDLE
        self._page_scroll_progress: int = 0
        self._page_scroll_target_frame: int = 0
        self._page_scroll_delta: int = 0
        self._page_scroll_direction: int = 0
        self._fb_old: Image.Image | None = None
        self._fb_new: Image.Image | None = None

        self._fb: Image.Image | None = None
        self._draw: ImageDraw.ImageDraw | None = None
        self._header_font: ImageFont.FreeTypeFont | None = None
        self._content_fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._wifi_size: int = 10
        self._i2c_lock = asyncio.Lock()

    # ---------------------------------------------------------------------------
    # Framebuffer
    # ---------------------------------------------------------------------------

    def _fb_ensure(self) -> None:
        if not _PIL_AVAILABLE:
            self._fb = None
            self._draw = None
            return
        if self._fb is None or self._fb.size != (self._width, self._height):
            self._fb = Image.new("1", (self._width, self._height), 0)
            self._draw = ImageDraw.Draw(self._fb)

    def _load_fonts(self) -> None:
        self._header_font = None
        self._content_fonts = {}
        try:
            self._header_font = ImageFont.truetype(_FONT_PATH, 10)
        except Exception:
            logger.warning("Failed to load header font from %s", _FONT_PATH)
        if self._header_font is None:
            try:
                self._header_font = ImageFont.load_default()
            except Exception:
                pass
        sizes = {FONT_DEFAULT: 10, FONT_SMALL: 10, FONT_MEDIUM: 13,
                 FONT_LARGE: 16, FONT_XLARGE: 24}
        for idx, pts in sizes.items():
            try:
                self._content_fonts[idx] = ImageFont.truetype(_FONT_PATH, pts)
            except Exception:
                pass
        if not self._content_fonts or all(f is None for f in self._content_fonts.values()):
            try:
                df = ImageFont.load_default()
                for idx in sizes:
                    self._content_fonts.setdefault(idx, df)
            except Exception:
                pass

    def _get_font(self, font_idx: int) -> ImageFont.FreeTypeFont | None:
        if font_idx == FONT_DEFAULT:
            return self._content_fonts.get(FONT_SMALL)
        return self._content_fonts.get(font_idx)

    async def _fb_send(self) -> None:
        if self._fb is None:
            return
        w, h = self._fb.size
        pages = h // 8
        if h % 8:
            pages += 1
        pages = min(pages, self._pages)

        if self._controller == 0:
            # SSD1306: set address window once, then stream all data
            # Uses Horizontal Addressing Mode (0x20, 0x00)
            await self._write_cmd(0x21)
            await self._write_cmd(0)
            await self._write_cmd(self._width - 1)
            await self._write_cmd(0x22)
            await self._write_cmd(0)
            await self._write_cmd(pages - 1)
            all_data = []
            for p in range(pages):
                top = p * 8
                for x in range(w):
                    byte_val = 0
                    for bit in range(8):
                        y = top + bit
                        if y < h and self._fb.getpixel((x, y)):
                            byte_val |= (1 << bit)
                    all_data.append(byte_val)
            await self._write_data(all_data)
        else:
            # SH1106: per-page with explicit page/column commands
            # Uses Page Addressing Mode (0x20, 0x02)
            for p in range(pages):
                top = p * 8
                data = []
                for x in range(w):
                    byte_val = 0
                    for bit in range(8):
                        y = top + bit
                        if y < h and self._fb.getpixel((x, y)):
                            byte_val |= (1 << bit)
                    data.append(byte_val)
                await self._write_page_data(p, data)

    # ---------------------------------------------------------------------------
    # I2C primitives
    # ---------------------------------------------------------------------------

    async def _write_cmd(self, cmd: int) -> None:
        if self._hw:
            async with self._i2c_lock:
                await self._hw.i2c.write_i2c_block_data(self._addr, OLED_CMD, [cmd])

    async def _write_data(self, data: list[int]) -> None:
        if not self._hw:
            return
        async with self._i2c_lock:
            for i in range(0, len(data), 31):
                await self._hw.i2c.write_i2c_block_data(self._addr, OLED_DATA, data[i:i + 31])

    async def _set_page_col(self, page: int, col: int) -> None:
        c = col + (self._sh1106_offset if self._controller == 1 else 0)
        if c > 255:
            c = 255
        await self._write_cmd(0xB0 + page)
        await self._write_cmd(0x00 + (c & 0x0F))
        await self._write_cmd(0x10 + ((c >> 4) & 0x0F))

    async def _clear_page(self, page: int) -> None:
        await self._set_page_col(page, 0)
        await self._write_data([0x00] * self._width)

    async def _clear(self) -> None:
        for p in range(self._pages):
            await self._clear_page(p)

    async def _write_page_data(self, page: int, data: list[int]) -> None:
        if page >= self._pages:
            return
        if len(data) < self._width:
            data = data + [0x00] * (self._width - len(data))
        await self._set_page_col(page, 0)
        await self._write_data(data[:self._width])

    # ---------------------------------------------------------------------------
    # Font helpers
    # ---------------------------------------------------------------------------

    def _get_line_font(self, line_idx: int) -> int:
        s = self._line_settings[line_idx]
        if s == 0xFF:
            return FONT_DEFAULT
        f = s & 0x07
        return f if f <= FONT_XLARGE else FONT_DEFAULT

    def _get_alignment(self, line_idx: int) -> int:
        s = self._line_settings[line_idx]
        if s == 0xFF:
            return self._global_alignment
        a = (s >> 3) & 0x07
        return self._global_alignment if a == ALIGN_GLOBAL else a

    def _get_align_x(self, text_width: int, alignment: int) -> int:
        if alignment == ALIGN_RIGHT:
            return max(0, self._width - text_width)
        elif alignment == ALIGN_CENTER:
            return max(0, (self._width - text_width) // 2)
        return 0

    # ---------------------------------------------------------------------------
    # Display state
    # ---------------------------------------------------------------------------

    async def _display_on_cmd(self) -> None:
        await self._write_cmd(0xAF)
        self._display_on = True

    async def _display_off_cmd(self) -> None:
        await self._write_cmd(0xAE)
        self._display_on = False

    async def _set_contrast(self, value: int) -> None:
        self._contrast = max(0, min(255, value))
        await self._write_cmd(0x81)
        await self._write_cmd(self._contrast)

    async def _set_contrast_level(self, level: int) -> None:
        levels = [10, 100, 200]
        if 0 <= level < len(levels):
            await self._set_contrast(levels[level])

    # ---------------------------------------------------------------------------
    # Header
    # ---------------------------------------------------------------------------

    def _get_header_text(self, use_alt: bool = False) -> str:
        hc = self._header_content_alt if use_alt else self._header_content
        m = {
            HEADER_NONE: "",
            HEADER_SSID: "%sysname%",
            HEADER_SYSNAME: "%sysname%",
            HEADER_IP: "%ip%",
            HEADER_MAC: "%mac%",
            HEADER_RSSI: "%rssi%dBm",
            HEADER_BSSID: "%bssid%",
            HEADER_WIFI_CH: "Ch: %wi_ch%",
            HEADER_UNIT: "Unit: %unit%",
            HEADER_SYSLOAD: "Load: %sysload%%",
            HEADER_SYSHEAP: "Mem: %sysheap%",
            HEADER_SYSSTACK: "Stack: %sysstack%",
            HEADER_TIME: "%systime%",
            HEADER_DATE: "%sysday_0%.%sysmonth_0%.%sysyear%",
            HEADER_PAGE_NO: f"P{self._current_frame + 1}/{self._total_frames}",
            HEADER_USERDEF1: self._userdef1,
            HEADER_USERDEF2: self._userdef2,
        }
        return m.get(hc, "")

    def _format_header_time(self) -> str:
        tf = self._time_format
        if tf == 1:
            return time.strftime("%H:%M")
        elif tf == 2:
            return time.strftime("%I:%M:%S %p")
        elif tf == 3:
            return time.strftime("%I:%M %p")
        return time.strftime("%H:%M:%S")

    # ---------------------------------------------------------------------------
    # Rendering (all drawing goes to framebuffer, then _fb_send sends to OLED)
    # ---------------------------------------------------------------------------

    def _calc_content_rect(self) -> tuple[int, int, int, int]:
        y0 = _HEADER_TOP if not self._hide_header else 0
        y1 = _FOOTER_TOP if not self._hide_footer else self._height
        return (0, y0, self._width - 1, y1 - 1)

    def _calc_line_params(self) -> list[tuple[int, str, int, int, int]]:
        lines = self._get_frame_lines(self._current_frame)
        d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
        _, y0, _, y1 = self._calc_content_rect()
        avail = y1 - y0 + 1
        cnt = min(len(lines), d["max_lines"], self._lines_per_frame)
        if cnt == 0:
            return []
        line_h = avail // cnt
        result = []
        for i in range(cnt):
            idx, text = lines[i]
            fi = self._get_line_font(idx)
            y = y0 + i * line_h
            result.append((idx, text, fi, y, line_h))
        return result

    async def _render(self) -> None:
        if not self._display_on:
            return
        if not _PIL_AVAILABLE:
            return
        if self._page_scroll_state != PAGE_SCROLL_IDLE:
            await self._render_page_scroll()
            return
        self._fb_ensure()
        fb = self._fb
        d = self._draw
        if fb is None or d is None:
            return
        d.rectangle((0, 0, self._width - 1, self._height - 1), fill=0)
        self._draw_header()
        self._draw_wifi_bars()
        self._draw_frame()
        self._draw_footer()
        await self._fb_send()

    def _draw_header(self) -> None:
        if self._hide_header:
            return
        d = self._draw
        if d is None:
            return
        time_str = self._format_header_time()
        if self._header_font:
            d.text((0, 0), time_str, fill=1, font=self._header_font)
        txt = self._get_header_text(self._header_alternate)
        if txt and self._header_font:
            p = resolve_template(txt, LIVE_TASK_VALUES, LIVE_TASK_NAMES)
            tw = d.textlength(p, font=self._header_font)
            x = self._get_align_x(int(tw), ALIGN_CENTER)
            d.text((x, 0), p, fill=1, font=self._header_font)

    def _draw_wifi_bars(self) -> None:
        d = self._draw
        if d is None:
            return
        try:
            from rpieasy2.core.util import get_wifi_rssi
            rssi = get_wifi_rssi()
        except Exception:
            return
        if rssi is None:
            return
        bars = 0
        if rssi > -50:
            bars = 4
        elif rssi > -67:
            bars = 3
        elif rssi > -80:
            bars = 2
        elif rssi > -90:
            bars = 1
        w = self._width
        nb = 4
        spacing = 3
        bw = 2
        x_start = w - (nb * (bw + spacing))
        y_base = _HEADER_TOP - 2
        for i in range(nb):
            x = x_start + i * (bw + spacing)
            if i < bars:
                h = (i + 1) * 2
                y = y_base - h + 1
                d.rectangle((x, y, x + bw - 1, y_base), fill=1)

    def _draw_frame(self) -> None:
        d = self._draw
        if d is None:
            return
        params = self._calc_line_params()
        for idx, text, fi, y, line_h in params:
            text = resolve_special_chars(str(text))
            if not text:
                continue
            so = self._scroll_offsets[idx] if fi == FONT_DEFAULT else 0
            ft = self._get_font(fi)
            if ft is None:
                continue
            tw = int(d.textlength(text, font=ft))
            align = self._get_alignment(idx)
            x = self._get_align_x(tw, align) + so
            d.text((x, y), text, fill=1, font=ft)

    def _draw_footer(self) -> None:
        if self._hide_footer or self._total_frames <= 1:
            return
        d = self._draw
        if d is None or self._header_font is None:
            return
        ft = ""
        for p in range(self._total_frames):
            ft += "\u2022" if p == self._current_frame else "\u00b7"
        tw = int(d.textlength(ft, font=self._header_font))
        x = self._get_align_x(tw, ALIGN_CENTER)
        d.text((x, _FOOTER_TOP), ft, fill=1, font=self._header_font)

    # ---------------------------------------------------------------------------
    # Page scrolling
    # ---------------------------------------------------------------------------

    async def _start_page_scroll(self, target_frame: int) -> None:
        if target_frame == self._current_frame:
            return
        if self._page_scroll_state != PAGE_SCROLL_IDLE:
            self._current_frame = target_frame
            await self._render()
            return
        self._fb_ensure()
        _, y0, _, y1 = self._calc_content_rect()
        content_h = y1 - y0 + 1
        delta = P36_SCROLL_LINE_SPEED * (self._scroll_speed + 1 if self._scroll_speed <= SCROLL_VERY_FAST else 1)
        self._page_scroll_target_frame = target_frame
        self._page_scroll_delta = delta
        self._page_scroll_progress = 0
        self._page_scroll_state = PAGE_SCROLL_OUT
        self._page_scroll_direction = 1 if target_frame > self._current_frame else -1
        self._fb_old = self._render_frame_to_image(self._current_frame)
        self._fb_new = self._render_frame_to_image(target_frame)
        if self._fb_old is None or self._fb_new is None:
            self._current_frame = target_frame
            self._page_scroll_state = PAGE_SCROLL_IDLE
            await self._render()
            return
        self._fb_ensure()
        fb = self._fb
        d = self._draw
        if fb is not None and d is not None:
            d.rectangle((0, 0, self._width - 1, self._height - 1), fill=0)
            self._draw_header()
            self._draw_wifi_bars()
            if self._fb_old:
                fb.paste(self._fb_old, (0, y0))
            self._draw_footer()
            await self._fb_send()

    def _render_frame_to_image(self, frame: int) -> Image.Image | None:
        if self._fb is None:
            return None
        _, y0, _, y1 = self._calc_content_rect()
        content_h = y1 - y0 + 1
        w = self._width
        img = Image.new("1", (w, content_h), 0)
        draw = ImageDraw.Draw(img)
        saved_frame = self._current_frame
        self._current_frame = frame
        lines = self._get_frame_lines(frame)
        d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
        cnt = min(len(lines), d["max_lines"], self._lines_per_frame)
        if cnt:
            line_h = content_h // cnt
            for i in range(cnt):
                idx, text = lines[i]
                text = resolve_special_chars(str(text))
                if not text:
                    continue
                fi = self._get_line_font(idx)
                so = self._scroll_offsets[idx] if fi == FONT_DEFAULT else 0
                ft = self._get_font(fi)
                if ft is None:
                    continue
                tw = int(draw.textlength(text, font=ft))
                align = self._get_alignment(idx)
                x = self._get_align_x(tw, align) + so
                draw.text((x, i * line_h), text, fill=1, font=ft)
        self._current_frame = saved_frame
        return img

    async def _render_page_scroll(self) -> None:
        if not self._display_on:
            return
        _, y0, _, y1 = self._calc_content_rect()
        content_h = y1 - y0 + 1
        self._page_scroll_progress += self._page_scroll_delta
        if self._page_scroll_progress >= content_h:
            self._current_frame = self._page_scroll_target_frame
            self._page_scroll_state = PAGE_SCROLL_IDLE
            self._fb_old = None
            self._fb_new = None
            await self._render()
            return
        prog = self._page_scroll_progress
        self._fb_ensure()
        fb = self._fb
        d = self._draw
        if fb is None or d is None:
            return
        if self._page_scroll_direction > 0:
            out_y = -prog
            in_y = content_h - prog
        else:
            out_y = prog
            in_y = -(content_h - prog)
        d.rectangle((0, 0, self._width - 1, self._height - 1), fill=0)
        self._draw_header()
        self._draw_wifi_bars()
        content_img = Image.new("1", (self._width, content_h), 0)
        if self._fb_old:
            content_img.paste(self._fb_old, (0, int(out_y)))
        if self._fb_new:
            content_img.paste(self._fb_new, (0, int(in_y)))
        fb.paste(content_img, (0, y0))
        self._draw_footer()
        await self._fb_send()

    # ---------------------------------------------------------------------------
    # Frame navigation
    # ---------------------------------------------------------------------------

    def _build_valid_frames(self) -> list[list[tuple[int, str]]]:
        n = self._lines_per_frame
        raw_frames = max(1, (P36_NLINES + n - 1) // n) if n > 0 else 1
        valid: list[list[tuple[int, str]]] = []
        for raw_idx in range(raw_frames):
            start = raw_idx * n
            lines: list[tuple[int, str]] = []
            for i in range(n):
                idx = start + i
                if idx >= P36_NLINES:
                    break
                text = self._resolved_lines[idx]
                if text:
                    lines.append((idx, text))
            if lines:
                valid.append(lines)
        if not valid:
            valid.append([])
        return valid

    def _get_frame_count(self) -> int:
        return len(self._valid_frames)

    def _get_frame_lines(self, frame: int) -> list[tuple[int, str]]:
        if 0 <= frame < len(self._valid_frames):
            return self._valid_frames[frame]
        return []

    def _advance_frame(self) -> int:
        self._current_frame = (self._current_frame + 1) % self._total_frames
        return self._current_frame

    def _jump_to_frame(self, frame: int) -> int:
        self._current_frame = max(0, min(frame, self._total_frames - 1))
        return self._current_frame

    async def _advance_frame_animated(self) -> None:
        if self._total_frames <= 1:
            return
        target = (self._current_frame + 1) % self._total_frames
        await self._start_page_scroll(target)

    # ---------------------------------------------------------------------------
    # Line scrolling
    # ---------------------------------------------------------------------------

    def _update_line_scroll(self) -> None:
        if not self._scroll_lines_enabled or self._draw is None:
            return
        lines = self._get_frame_lines(self._current_frame)
        d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
        for i in range(min(len(lines), d["max_lines"])):
            idx, text = lines[i]
            if not text:
                continue
            fi = self._get_line_font(idx)
            ft = self._get_font(fi)
            if ft is None:
                continue
            tw = int(self._draw.textlength(text, font=ft))
            if tw <= self._width:
                self._scroll_offsets[idx] = 0
                self._scroll_directions[idx] = 0
                self._scroll_waits[idx] = 0
                continue
            if self._scroll_directions[idx] == 0:
                self._scroll_waits[idx] += 1
                if self._scroll_waits[idx] >= 10:
                    self._scroll_directions[idx] = -1
                    self._scroll_waits[idx] = 0
                continue
            self._scroll_offsets[idx] += self._scroll_directions[idx] * P36_SCROLL_LINE_SPEED
            limit = tw - self._width + 10
            if self._scroll_offsets[idx] <= -limit:
                self._scroll_offsets[idx] = -limit
                self._scroll_waits[idx] += 1
                if self._scroll_waits[idx] >= 10:
                    self._scroll_directions[idx] = 1
                    self._scroll_waits[idx] = 0
            if self._scroll_offsets[idx] >= 0:
                self._scroll_offsets[idx] = 0
                self._scroll_waits[idx] += 1
                if self._scroll_waits[idx] >= 10:
                    self._scroll_directions[idx] = -1
                    self._scroll_waits[idx] = 0

    # ---------------------------------------------------------------------------
    # Init
    # ---------------------------------------------------------------------------

    async def _init_display(self) -> None:
        d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
        self._width = d["width"]
        self._height = d["height"]
        self._pages = d["pages"]
        self._pix_left = d["pix_left"]
        self._wifi_left = d["wifi_left"]
        self._wifi_width = d["wifi_width"]

        self._controller = int(self._config.get("controller") or 0)
        rotate = int(self._config.get("rotate") or 1)
        self._contrast = int(self._config.get("contrast") or 128)

        if self._controller == 1:
            self._sh1106_offset = 2

        await self._write_cmd(0xAE)

        cmds = []
        if self._controller == 1:
            cmds.extend([0xD5, 0x50, 0xA8, d["mux"], 0xD3, 0x00, 0x40])
            cmds.extend([0x20, 0x02])
        else:
            cmds.extend([0xD5, 0x80, 0xA8, d["mux"], 0xD3, 0x00, 0x40])
            cmds.extend([0x20, 0x00])

        if rotate == 2:
            cmds.extend([0xA0, 0xC0])
        else:
            cmds.extend([0xA1, 0xC8])

        cmds.extend([0xDA, d["compins"]])
        cmds.extend([0x81, self._contrast])

        if self._controller == 1:
            cmds.extend([0xD9, 0x22, 0xDB, 0x35])
        else:
            cmds.extend([0xD9, 0xF1, 0xDB, 0x40])

        cmds.extend([0x8D, 0x14, 0xA4, 0xA6, 0x2E, 0xAF])

        for cmd in cmds:
            await self._write_cmd(cmd)
        self._display_on = True

        self._load_fonts()
        self._fb_ensure()

    # ---------------------------------------------------------------------------
    # Plugin event handlers
    # ---------------------------------------------------------------------------

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x3C)
        self._read_config()

        if not self._hw:
            return False

        try:
            await self._init_display()
            for i in range(P36_NLINES):
                self._lines[i] = self._config.get(f"line_{i}", "")
                self._line_settings[i] = int(self._config.get(f"line_settings_{i}") or 0xFF)
            await self._resolve_all()

            if self._btn_pin > 0:
                pu = self._btn_pin_mode == 1
                self._hw.gpio.claim_input(self._btn_pin, pull_up=pu)
                self._btn_state = self._hw.gpio.read(self._btn_pin)
                self._btn_last_state = self._btn_state

            if not self._hide_logo and not self._logo_shown:
                await self._clear()
                self._fb_ensure()
                if self._draw and self._header_font:
                    tw = int(self._draw.textlength("ESP Easy", font=self._header_font))
                    x = self._get_align_x(tw, ALIGN_CENTER)
                    self._draw.rectangle((0, 0, self._width - 1, self._height - 1), fill=0)
                    self._draw.text((x, 20), "ESP", fill=1, font=self._header_font)
                    self._draw.text((x, 30), "Easy", fill=1, font=self._header_font)
                    await self._fb_send()
                self._logo_shown = True

            await self._render()
        except Exception as e:
            logger.error("FrameOLED init failed: %s", e)
            return False
        return True

    def _read_config(self) -> None:
        self._lines_per_frame = max(1, int(self._config.get("nlines") or 1))
        self._scroll_speed = int(self._config.get("scroll") or 0)
        self._display_timer_max = int(self._config.get("timer") or 0)
        self._display_timer = self._display_timer_max
        self._hide_header = self._config.get("hide_header", False) in (True, "true", "1", 1)
        self._hide_footer = self._config.get("hide_footer", False) in (True, "true", "1", 1)
        self._hide_logo = self._config.get("hide_logo", False) in (True, "true", "1", 1)
        self._scroll_lines_enabled = self._config.get("scroll_lines", False) in (True, "true", "1", 1)
        self._scroll_without_wifi = not (self._config.get("scroll_without_wifi", False) in (True, "true", "1", 1))
        self._wake_on_receive = not (self._config.get("no_display_on_receive", False) in (True, "true", "1", 1))
        self._btn_step_pages = self._config.get("step_pages", False) in (True, "true", "1", 1)
        self._btn_inversed = self._config.get("pin3_invers", False) in (True, "true", "1", 1)
        self._btn_pin_mode = int(self._config.get("pin_mode") or 0)
        self._reduce_line_no = self._config.get("reduce_line_no", False) in (True, "true", "1", 1)
        self._global_alignment = int(self._config.get("global_alignment") or ALIGN_CENTER)
        self._time_format = int(self._config.get("time_format") or 0)
        self._send_events = int(self._config.get("generate_events") or 0)
        self._header_content = int(self._config.get("header") or HEADER_SYSNAME)
        self._header_content_alt = int(self._config.get("header_alt") or HEADER_SYSNAME)
        self._userdef1 = self._config.get("userdef1", "")
        self._userdef2 = self._config.get("userdef2", "")
        self._display_interval = int(self._config.get("TDT") or 60)

        bp = self._config.get("button_pin", "")
        self._btn_pin = int(bp) if bp not in ("", None, "None") else -1

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        try:
            await self._display_off_cmd()
        except Exception:
            pass
        return True

    # ---------------------------------------------------------------------------
    # Periodic
    # ---------------------------------------------------------------------------

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if not self._hw:
            return None

        if self._display_timer_max > 0 and self._display_on:
            self._display_timer -= 1
            if self._display_timer <= 0:
                await self._display_off_cmd()
                if self._send_events & 1:
                    get_event_bus().publish(Event(
                        type="PLUGIN_WRITE", task_index=self._task_index,
                        data={"command": "event",
                              "args": [f"{self._config.get('TDN', 'OLED')}#display=0"]}))

        if self._display_on and self._header_content_alt != self._header_content:
            iv = self._display_interval
            if iv > 0:
                self._header_count += 1
                if self._header_count > iv * 5:
                    self._header_alternate = not self._header_alternate
                    self._header_count = 0

        if self._display_on and self._page_scroll_state == PAGE_SCROLL_IDLE:
            if self._scroll_speed == SCROLL_TICKER:
                self._ticker_tick += 1
                if self._ticker_tick >= P36_TICKER_INTERVAL:
                    self._ticker_tick = 0
                    self._advance_frame()
                    await self._render()
            elif self._scroll_speed == SCROLL_INSTANT:
                self._advance_frame()
                await self._render()
            elif self._scroll_speed <= SCROLL_VERY_FAST:
                speeds = {SCROLL_VERY_SLOW: 4, SCROLL_SLOW: 3, SCROLL_FAST: 2, SCROLL_VERY_FAST: 1}
                self._scroll_tick += 1
                if self._scroll_tick >= speeds.get(self._scroll_speed, 2):
                    self._scroll_tick = 0
                    await self._advance_frame_animated()

        now = time.time()
        if self._display_interval > 0 and (now - self._last_template_refresh >= self._display_interval):
            await self._resolve_all()
            self._last_template_refresh = now
            if self._display_on:
                await self._render()
        return None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._hw:
            return None

        if self._page_scroll_state != PAGE_SCROLL_IDLE:
            await self._render()
            return None

        if self._scroll_lines_enabled and self._display_on:
            old_offsets = self._scroll_offsets[:]
            self._update_line_scroll()
            if self._scroll_offsets != old_offsets:
                await self._render()

        if self._btn_pin > 0:
            await self._handle_button()
        return None

    async def _handle_button(self) -> None:
        try:
            state = self._hw.gpio.read(self._btn_pin)
            if self._btn_inversed:
                state = 1 - state
            if state != self._btn_last_state:
                self._btn_debounce += 1
                if self._btn_debounce >= P36_DEBOUNCE_THRESHOLD:
                    self._btn_last_state = state
                    self._btn_debounce = 0
                    if state == 0:
                        if self._btn_step_pages and self._display_on:
                            await self._advance_frame_animated()
                        elif not self._btn_repeat:
                            if self._display_on:
                                await self._display_off_cmd()
                            else:
                                await self._display_on_cmd()
                                self._display_timer = self._display_timer_max
                                await self._render()
                        self._btn_repeat += 1
                    else:
                        self._btn_repeat = 0
            else:
                self._btn_debounce = 0
            self._btn_state = state
        except Exception:
            pass

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Status": "OK"}
        return True

    # ---------------------------------------------------------------------------
    # Write / Commands
    # ---------------------------------------------------------------------------

    async def on_plugin_write(self, event: Event) -> bool | None:
        cmd = event.data.get("command", "")
        if cmd == "oledframedcmd":
            return await self._handle_cmd(event.data.get("args", []))

        if event.string1 and self._hw:
            try:
                txt = event.string1.split("\n")[:P36_NLINES]
                for i, ln in enumerate(txt):
                    self._lines[i] = ln
                    self._resolved_lines[i] = resolve_template(ln, LIVE_TASK_VALUES, LIVE_TASK_NAMES)
                self._valid_frames = self._build_valid_frames()
                self._total_frames = self._get_frame_count()
                if self._display_on or self._wake_on_receive:
                    if not self._display_on:
                        await self._display_on_cmd()
                        self._display_timer = self._display_timer_max
                    self._jump_to_frame(0)
                    await self._render()
                return True
            except Exception as e:
                logger.error("FrameOLED write failed: %s", e)
                return False
        return False

    async def _handle_cmd(self, args: list[str]) -> bool:
        if not args:
            return False
        sub = args[0].lower()

        try:
            ln = int(sub)
            txt = ",".join(args[1:]) if len(args) > 1 else ""
            if 1 <= ln <= P36_NLINES:
                idx = ln - 1
                self._lines[idx] = txt
                self._resolved_lines[idx] = resolve_template(txt, LIVE_TASK_VALUES, LIVE_TASK_NAMES)
                self._valid_frames = self._build_valid_frames()
                self._total_frames = self._get_frame_count()
                if not self._display_on and self._wake_on_receive:
                    await self._display_on_cmd()
                    self._display_timer = self._display_timer_max
                if self._display_on:
                    fr = idx // self._lines_per_frame
                    if fr < self._total_frames:
                        self._jump_to_frame(fr)
                    await self._render()
                    if self._send_events & 2:
                        get_event_bus().publish(Event(
                            type="PLUGIN_WRITE", task_index=self._task_index,
                            data={"command": "event",
                                  "args": [f"{self._config.get('TDN', 'OLED')}#line={ln}"]}))
                return True
        except ValueError:
            pass

        handlers = {
            "display": self._cmd_display,
            "frame": self._cmd_frame,
            "linecount": self._cmd_linecount,
            "restore": self._cmd_restore,
            "scroll": self._cmd_scroll,
            "leftalign": self._cmd_leftalign,
            "align": self._cmd_align,
            "userdef1": self._cmd_userdef,
            "userdef2": self._cmd_userdef,
        }
        h = handlers.get(sub)
        if h:
            return await h(args)
        return False

    async def _cmd_display(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        a = args[1].lower()
        if a == "on":
            await self._display_on_cmd()
            self._display_timer = self._display_timer_max
            if self._send_events & 1:
                get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                    data={"command": "event", "args": [f"{self._config.get('TDN', 'OLED')}#display=1"]}))
            return True
        if a == "off":
            await self._display_off_cmd()
            self._display_timer = 0
            if self._send_events & 1:
                get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                    data={"command": "event", "args": [f"{self._config.get('TDN', 'OLED')}#display=0"]}))
            return True
        if a in ("low", "med", "high"):
            level = {"low": 0, "med": 1, "high": 2}[a]
            await self._set_contrast_level(level)
            await self._display_on_cmd()
            if self._send_events & 1:
                get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                    data={"command": "event",
                          "args": [f"{self._config.get('TDN', 'OLED')}#contrast={level}"]}))
            return True
        if a == "user" and len(args) >= 5:
            try:
                await self._set_contrast(int(args[2]))
                await self._write_cmd(0xD9)
                await self._write_cmd(int(args[3]) & 0xFF)
                await self._write_cmd(0xDB)
                await self._write_cmd(int(args[4]) & 0xFF)
                await self._display_on_cmd()
                return True
            except (ValueError, IndexError):
                return False
        return False

    async def _cmd_frame(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            n = int(args[1])
            if n == 0:
                target = (self._current_frame + 1) % self._total_frames
            else:
                target = max(0, min(n - 1, self._total_frames - 1))
            if not self._display_on:
                await self._display_on_cmd()
                self._display_timer = self._display_timer_max
            self._jump_to_frame(target)
            await self._render()
            if self._send_events & 2:
                get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                    data={"command": "event",
                          "args": [f"{self._config.get('TDN', 'OLED')}#frame={self._current_frame + 1}"]}))
            return True
        except ValueError:
            return False

    async def _cmd_linecount(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            n = int(args[1])
            d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
            if 1 <= n <= d["max_lines"]:
                self._lines_per_frame = n
                self._valid_frames = self._build_valid_frames()
                self._total_frames = self._get_frame_count()
                self._jump_to_frame(0)
                await self._render()
                if self._send_events & 2:
                    get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                        data={"command": "event",
                              "args": [f"{self._config.get('TDN', 'OLED')}#linecount={n}"]}))
                return True
        except ValueError:
            pass
        return False

    async def _cmd_restore(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            ln = int(args[1])
            if ln == 0:
                for i in range(P36_NLINES):
                    self._lines[i] = self._config.get(f"line_{i}", "")
            elif 1 <= ln <= P36_NLINES:
                self._lines[ln - 1] = self._config.get(f"line_{ln - 1}", "")
            await self._resolve_all()
            if self._display_on:
                await self._render()
            return True
        except ValueError:
            return False

    async def _cmd_scroll(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            n = int(args[1])
            if 1 <= n <= 6:
                sm = {1: SCROLL_VERY_SLOW, 2: SCROLL_SLOW, 3: SCROLL_FAST,
                      4: SCROLL_VERY_FAST, 5: SCROLL_INSTANT, 6: SCROLL_TICKER}
                self._scroll_speed = sm[n]
                self._scroll_tick = 0
                self._ticker_tick = 0
                self._page_scroll_state = PAGE_SCROLL_IDLE
                self._jump_to_frame(0)
                if self._display_on:
                    await self._render()
                if self._send_events & 2:
                    get_event_bus().publish(Event(type="PLUGIN_WRITE", task_index=self._task_index,
                        data={"command": "event",
                              "args": [f"{self._config.get('TDN', 'OLED')}#scroll={n}"]}))
                return True
        except ValueError:
            pass
        return False

    async def _cmd_leftalign(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            n = int(args[1])
            if n in (0, 1):
                self._global_alignment = ALIGN_LEFT if n == 1 else ALIGN_CENTER
                if self._display_on:
                    await self._render()
                return True
        except ValueError:
            pass
        return False

    async def _cmd_align(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        try:
            n = int(args[1])
            if n in (0, 1, 2):
                self._global_alignment = n
                if self._display_on:
                    await self._render()
                return True
        except ValueError:
            pass
        return False

    async def _cmd_userdef(self, args: list[str]) -> bool:
        if len(args) < 2:
            return False
        key = args[0].lower()
        val = ",".join(args[1:])
        if key == "userdef1":
            self._userdef1 = val
        else:
            self._userdef2 = val
        return True

    # ---------------------------------------------------------------------------
    # Web form
    # ---------------------------------------------------------------------------

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        cfg = self._config
        d = DISPLAY_SIZES.get(int(cfg.get("size", DISP_128x64)), DISPLAY_SIZES[DISP_128x64])

        form: list[dict[str, Any]] = []
        if _PIL_ERROR:
            form.append({"name": "_pil_error", "label": "PIL Error",
                         "type": "text", "value": f"Pillow/PIL failed to load: {_PIL_ERROR}. "
                         "Install: sudo apt-get install -y libtiff6 libopenjp2-7 libxcb1 && pip install Pillow"})
        form += [
            {"name": "address", "label": "I2C Address", "type": "select",
             "value": cfg.get("address") or 0x3C, "options": [
                 {"value": 0x3C, "label": "0x3C"}, {"value": 0x3D, "label": "0x3D"}]},

            {"name": "controller", "label": "OLED Controller", "type": "select",
             "value": cfg.get("controller") or 0, "options": [
                 {"value": 0, "label": "SSD1306"}, {"value": 1, "label": "SH1106"}]},

            {"name": "size", "label": "Display Size", "type": "select",
             "value": cfg.get("size") or DISP_128x64, "options": [
                 {"value": DISP_128x64, "label": "128x64"},
                 {"value": DISP_128x32, "label": "128x32"},
                 {"value": DISP_64x48, "label": "64x48"}]},

            {"name": "rotate", "label": "Rotation", "type": "select",
             "value": cfg.get("rotate") or 1, "options": [
                 {"value": 1, "label": "Normal"}, {"value": 2, "label": "Rotated"}]},

            {"name": "nlines", "label": "Lines per Frame", "type": "number",
             "value": cfg.get("nlines") or 1, "min": 1, "max": d["max_lines"]},

            {"name": "reduce_line_no", "label": "Reduce no. of lines to fit font",
             "type": "checkbox", "value": cfg.get("reduce_line_no", False)},

            {"name": "scroll", "label": "Scroll Speed", "type": "select",
             "value": cfg.get("scroll") or 0, "options": [
                 {"value": SCROLL_VERY_SLOW, "label": "Very Slow"},
                 {"value": SCROLL_SLOW, "label": "Slow"},
                 {"value": SCROLL_FAST, "label": "Fast"},
                 {"value": SCROLL_VERY_FAST, "label": "Very Fast"},
                 {"value": SCROLL_INSTANT, "label": "Instant"},
                 {"value": SCROLL_TICKER, "label": "Ticker"}]},

            {"name": "button_pin", "label": "Display Button GPIO", "type": "select",
             "value": cfg.get("button_pin") or "", "options": [
                 {"value": "", "label": "None"}] + [
                 {"value": p, "label": f"GPIO {p}"} for p in range(1, 28)]},

            {"name": "pin_mode", "label": "Pin mode", "type": "select",
             "value": cfg.get("pin_mode") or 0, "options": [
                 {"value": 0, "label": "Input"}, {"value": 1, "label": "Input pullup"}]},

            {"name": "pin3_invers", "label": "Inversed Logic",
             "type": "checkbox", "value": cfg.get("pin3_invers", False)},

            {"name": "step_pages", "label": "Step through frames with Display button",
             "type": "checkbox", "value": cfg.get("step_pages", False)},

            {"name": "timer", "label": "Display Timeout (seconds, 0=no timeout)",
             "type": "number", "value": cfg.get("timer") or 0, "min": 0, "max": 65535},

            {"name": "contrast", "label": "Contrast Value (0-255)",
             "type": "number", "value": cfg.get("contrast") or 128, "min": 0, "max": 255},

            {"name": "scroll_without_wifi", "label": "Disable scrolling while WiFi is disconnected",
             "type": "checkbox", "value": cfg.get("scroll_without_wifi", False)},

            {"name": "hide_logo", "label": "Hide startup logo",
             "type": "checkbox", "value": cfg.get("hide_logo", False)},

            {"name": "generate_events", "label": "Generate events", "type": "select",
             "value": cfg.get("generate_events") or 0, "options": [
                 {"value": 0, "label": "None"},
                 {"value": 1, "label": "Display & Contrast"},
                 {"value": 3, "label": "Display, Contrast, Frame, Line & Linecount"}]},
        ]

        form.append({"name": "hide_header", "label": "Hide header",
                     "type": "checkbox", "value": cfg.get("hide_header", False)})

        hc_opts = [
            {"value": HEADER_SYSNAME, "label": "SysName"},
            {"value": HEADER_SSID, "label": "SSID"},
            {"value": HEADER_IP, "label": "IP"},
            {"value": HEADER_MAC, "label": "MAC"},
            {"value": HEADER_RSSI, "label": "RSSI"},
            {"value": HEADER_BSSID, "label": "BSSID"},
            {"value": HEADER_WIFI_CH, "label": "WiFi channel"},
            {"value": HEADER_UNIT, "label": "Unit"},
            {"value": HEADER_SYSLOAD, "label": "SysLoad"},
            {"value": HEADER_SYSHEAP, "label": "SysHeap"},
            {"value": HEADER_SYSSTACK, "label": "SysStack"},
            {"value": HEADER_DATE, "label": "Date"},
            {"value": HEADER_TIME, "label": "Time"},
            {"value": HEADER_PAGE_NO, "label": "PageNumbers"},
            {"value": HEADER_USERDEF1, "label": "User defined 1"},
            {"value": HEADER_USERDEF2, "label": "User defined 2"},
        ]

        form.append({"name": "header", "label": "Header", "type": "select",
                     "value": cfg.get("header") or HEADER_SYSNAME, "options": hc_opts})
        form.append({"name": "header_alt", "label": "Header (alternate)", "type": "select",
                     "value": cfg.get("header_alt") or HEADER_SYSNAME, "options": hc_opts})
        form.append({"name": "time_format", "label": "Header Time format", "type": "select",
                     "value": cfg.get("time_format") or 0, "options": [
                         {"value": 0, "label": "HH:MM:SS (24h)"},
                         {"value": 1, "label": "HH:MM (24h)"},
                         {"value": 2, "label": "HH:MM:SS (am/pm)"},
                         {"value": 3, "label": "HH:MM (am/pm)"}]})
        form.append({"name": "scroll_lines", "label": "Scroll long lines",
                     "type": "checkbox", "value": cfg.get("scroll_lines", False)})
        form.append({"name": "no_display_on_receive", "label": "Wake display on receiving text",
                     "type": "checkbox", "value": cfg.get("no_display_on_receive", False)})

        font_opts = [
            {"value": FONT_DEFAULT, "label": "Default (5x8)"},
            {"value": FONT_SMALL, "label": "Small (10px)"},
            {"value": FONT_MEDIUM, "label": "Medium (13px)"},
            {"value": FONT_LARGE, "label": "Large (16px)"},
            {"value": FONT_XLARGE, "label": "XLarge (24px)"},
        ]

        form.append({"name": "global_alignment", "label": "Align content (global)", "type": "select",
                     "value": cfg.get("global_alignment") or ALIGN_CENTER, "options": [
                         {"value": ALIGN_LEFT, "label": "left"},
                         {"value": ALIGN_CENTER, "label": "center"},
                         {"value": ALIGN_RIGHT, "label": "right"}]})

        for i in range(P36_NLINES):
            raw = cfg.get(f"line_{i}", "")
            s = int(cfg.get(f"line_settings_{i}", 0xFF))
            fv = s & 0x07 if s != 0xFF else FONT_DEFAULT
            av = (s >> 3) & 0x07 if s != 0xFF else ALIGN_GLOBAL
            form.append({"name": f"line_{i}", "label": f"Line {i + 1} Content",
                         "type": "text", "value": raw})
            form.append({"name": f"line_font_{i}", "label": f"Line {i + 1} Font",
                         "type": "select", "value": fv, "options": font_opts})
            form.append({"name": f"line_align_{i}", "label": f"Line {i + 1} Align",
                         "type": "select", "value": av, "options": [
                             {"value": ALIGN_GLOBAL, "label": "Use global"},
                             {"value": ALIGN_LEFT, "label": "left"},
                             {"value": ALIGN_CENTER, "label": "center"},
                             {"value": ALIGN_RIGHT, "label": "right"}]})

        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        fd = event.data.get("form_data", {})

        for i in range(P36_NLINES):
            fk = f"line_font_{i}"
            ak = f"line_align_{i}"
            fv = int(fd.get(fk, FONT_DEFAULT))
            av = int(fd.get(ak, ALIGN_GLOBAL))
            self._config[f"line_settings_{i}"] = (fv & 0x07) | ((av & 0x07) << 3)

        self._read_config()

        for i in range(P36_NLINES):
            self._lines[i] = self._config.get(f"line_{i}", "")
            self._line_settings[i] = int(self._config.get(f"line_settings_{i}") or 0xFF)
        await self._resolve_all()
        self._jump_to_frame(0)

        if self._hw and self._display_on:
            await self._render()
        return True

    async def on_plugin_webform_show_values(self, event: Event) -> bool | None:
        if not self._hw:
            return True
        try:
            lines = self._get_frame_lines(self._current_frame)
            d = DISPLAY_SIZES.get(self._config.get("size", DISP_128x64), DISPLAY_SIZES[DISP_128x64])
            html = '<table><tr><th>Frame {}/{}:</th></tr>'.format(self._current_frame + 1, self._total_frames)
            for i in range(min(len(lines), d["max_lines"])):
                _, text = lines[i]
                html += f"<tr><td>{text}&nbsp;</td></tr>"
            html += "</table>"
            event.data["html"] = html
        except Exception:
            pass
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Status": ""}

    async def _resolve_all(self) -> None:
        for i in range(P36_NLINES):
            self._resolved_lines[i] = resolve_template(self._lines[i], LIVE_TASK_VALUES, LIVE_TASK_NAMES)
        self._valid_frames = self._build_valid_frames()
        self._total_frames = self._get_frame_count()
