from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("rpieasy2.fontvar")

FONT_DEFAULT = 0
FONT_SMALL = 1
FONT_MEDIUM = 2
FONT_LARGE = 3
FONT_XLARGE = 4

_FONT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static"))

FONT_DEFS: dict[int, tuple[str, int, int]] = {
    FONT_DEFAULT: ("", 5, 8),
    FONT_SMALL: ("UbuntuMono-R.ttf", 10, 10),
    FONT_MEDIUM: ("UbuntuMono-R.ttf", 13, 13),
    FONT_LARGE: ("UbuntuMono-R.ttf", 16, 16),
    FONT_XLARGE: ("UbuntuMono-R.ttf", 24, 24),
}

_truetype_fonts: dict[int, Any] = {}
_PIL_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    logger.warning("Pillow not available, only FONT_DEFAULT (5x8) will work")


def _get_font_path(font_idx: int) -> str:
    fd = FONT_DEFS.get(font_idx, FONT_DEFS[FONT_SMALL])
    return os.path.join(_FONT_DIR, fd[0])


def _get_pil_font(font_idx: int) -> Any:
    if font_idx == FONT_DEFAULT or not _PIL_AVAILABLE:
        return None
    if font_idx not in _truetype_fonts:
        fd = FONT_DEFS.get(font_idx, FONT_DEFS[FONT_SMALL])
        path = _get_font_path(font_idx)
        try:
            _truetype_fonts[font_idx] = ImageFont.truetype(path, fd[1])
        except Exception:
            logger.warning("Failed to load font from %s", path)
            return None
    return _truetype_fonts[font_idx]


def get_text_width(text: str, font_idx: int = FONT_DEFAULT) -> int:
    if font_idx == FONT_DEFAULT or not _PIL_AVAILABLE:
        return len(text) * 5
    ft = _get_pil_font(font_idx)
    if ft is None:
        return len(text) * 5
    try:
        bbox = ft.getbbox(text)
        return bbox[2] - bbox[0] if bbox else len(text) * 5
    except Exception:
        return len(text) * 5


def get_font_height(font_idx: int = FONT_DEFAULT) -> int:
    fd = FONT_DEFS.get(font_idx, FONT_DEFS[FONT_DEFAULT])
    return fd[2]


def render_text_to_pages(
    text: str,
    display_width: int,
    font_idx: int = FONT_DEFAULT,
    alignment: int = 0,
    scroll_offset: int = 0,
) -> list[list[int]]:
    if font_idx == FONT_DEFAULT or not _PIL_AVAILABLE:
        return _render_bitmap_5x8(text, display_width, alignment, scroll_offset)

    ft = _get_pil_font(font_idx)
    if ft is None:
        return _render_bitmap_5x8(text, display_width, alignment, scroll_offset)

    fh = get_font_height(font_idx)
    tw = get_text_width(text, font_idx)

    if alignment == 2:
        col = max(0, display_width - tw)
    elif alignment == 1:
        col = max(0, (display_width - tw) // 2)
    else:
        col = 0

    col += scroll_offset

    img_w = display_width
    img_h = fh
    img = Image.new("1", (img_w, img_h), 0)
    draw = ImageDraw.Draw(img)
    draw.text((col, 0), text, font=ft, fill=1)

    pages = fh // 8
    if fh % 8:
        pages += 1

    result: list[list[int]] = []
    for p in range(pages):
        row_data = []
        top = p * 8
        for x in range(img_w):
            byte_val = 0
            for bit in range(8):
                y = top + bit
                if y < img_h and img.getpixel((x, y)):
                    byte_val |= (1 << bit)
            row_data.append(byte_val)
        result.append(row_data)
    return result


def _render_bitmap_5x8(
    text: str,
    display_width: int,
    alignment: int = 0,
    scroll_offset: int = 0,
) -> list[list[int]]:
    from rpieasy2.core.font import FONT_5X8, CHAR_TO_FONT_INDEX
    from rpieasy2.core.system_chars import resolve_special_chars
    text = resolve_special_chars(str(text))
    tw = len(text) * 5
    if alignment == 2:
        col = max(0, display_width - tw)
    elif alignment == 1:
        col = max(0, (display_width - tw) // 2)
    else:
        col = 0
    col += scroll_offset

    data = [0x00] * display_width
    for ch in text:
        idx = CHAR_TO_FONT_INDEX.get(ch, ord(ch) - 32)
        if 0 <= idx < len(FONT_5X8):
            glyph = FONT_5X8[idx]
            for i in range(5):
                px = col + i
                if 0 <= px < display_width:
                    data[px] = glyph[i]
    return [data]
