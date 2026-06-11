from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p057")

HT16K33_ADDR = 0x70
HT16K33_REG_SYSTEM = 0x20
HT16K33_REG_BRIGHT = 0xE0


SEGMENT_MAP = {
    '0': 0x3F, '1': 0x06, '2': 0x5B, '3': 0x4F,
    '4': 0x66, '5': 0x6D, '6': 0x7D, '7': 0x07,
    '8': 0x7F, '9': 0x6F, 'A': 0x77, 'B': 0x7C,
    'C': 0x39, 'D': 0x5E, 'E': 0x79, 'F': 0x71,
    '-': 0x40, ' ': 0x00, '.': 0x80,
}
COLON_MASK = 0x02

NUM_MAP = {
    0: 0x3F, 1: 0x06, 2: 0x5B, 3: 0x4F,
    4: 0x66, 5: 0x6D, 6: 0x7D, 7: 0x07,
    8: 0x7F, 9: 0x6F,
}


class P057HT16K33LED(PluginBase):
    PLUGIN_ID = 57
    PLUGIN_NAME = "Display - HT16K33"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_NONE,
        value_count=0,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )
    I2C_ADDRESSES = [0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77]

    def __init__(self):
        super().__init__()
        self._addr: int = HT16K33_ADDR
        self._config: dict[str, Any] = {}
        self._display_type: int = 0

    async def _write_display(self, data: list[int]) -> None:
        i2c = self._hw.i2c
        await i2c.write_i2c_block_data(self._addr, 0x00, data)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", HT16K33_ADDR))
        except (ValueError, TypeError):
            self._addr = HT16K33_ADDR
        try:
            self._display_type = int(self._config.get("display_type", 0))
        except (ValueError, TypeError):
            self._display_type = 0
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, 0x00, HT16K33_REG_SYSTEM | 0x01)
            await asyncio.sleep(0.001)
            await i2c.write_byte_data(self._addr, 0x00, HT16K33_REG_BRIGHT | 0x0F)
            await asyncio.sleep(0.001)
            await self._write_display([0] * 16)
            return True
        except Exception as e:
            logger.error("HT16K33 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", HT16K33_ADDR)
        self._config.setdefault("brightness", 15)
        self._config.setdefault("display_type", 0)
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        command = event.data.get("command", "").upper()
        params = event.data.get("params", {})
        try:
            if command == "M":
                values = params.get("values", [])
                if self._display_type == 1:
                    buf = [0] * 16
                    for i, v in enumerate(values[:8]):
                        buf[i * 2] = v & 0xFF
                    await self._write_display(buf)
                else:
                    if len(values) >= 8:
                        await self._write_display(values[:8] + values[8:16])
                    else:
                        for i, v in enumerate(values):
                            buf = [0] * 16
                            buf[i] = v & 0xFF
                            await self._write_display(buf)
                return True
            elif command == "MNUM":
                if self._display_type != 1:
                    return False
                values = params.get("values", [])
                buf = [0] * 16
                for i, v in enumerate(values[:8]):
                    if 0 <= v <= 9:
                        buf[i * 2] = NUM_MAP.get(v, 0)
                    else:
                        buf[i * 2] = v & 0xFF
                await self._write_display(buf)
                return True
            elif command == "MBR":
                b = max(0, min(15, int(params.get("value", 15))))
                await self._hw.i2c.write_byte_data(self._addr, 0x00, HT16K33_REG_BRIGHT | b)
                return True
            elif command == "MPRINT":
                text = str(params.get("text", ""))
                buf = [0] * 16
                for i, ch in enumerate(text[:8]):
                    seg = SEGMENT_MAP.get(ch.upper(), 0x00)
                    buf[i * 2] = seg
                    if i + 1 < len(text) and text[i + 1] == '.':
                        buf[i * 2] |= 0x80
                await self._write_display(buf)
                return True
            elif command == "CLEAR":
                await self._write_display([0] * 16)
                return True
        except Exception as e:
            logger.error("HT16K33 write failed: %s", e)
            return False
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x70, 0x78)]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", HT16K33_ADDR), "options": addr_opts},
            {"name": "display_type", "label": "Display Type", "type": "select", "value": self._config.get("display_type", 0), "options": [
                {"value": 0, "label": "8x8 Matrix"},
                {"value": 1, "label": "7-Segment"},
            ]},
            {"name": "brightness", "label": "Brightness (0-15)", "type": "number", "value": self._config.get("brightness", 15)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        try:
            self._addr = int(self._config.get("address", HT16K33_ADDR))
        except (ValueError, TypeError):
            self._addr = HT16K33_ADDR
        try:
            self._display_type = int(self._config.get("display_type", 0))
        except (ValueError, TypeError):
            self._display_type = 0
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x70 <= addr <= 0x77

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}
