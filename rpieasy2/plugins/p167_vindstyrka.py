from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p167")

SEN5X_ADDR = 0x69

CMD_MEASURE = bytes([0x00, 0x21])
CMD_STOP = bytes([0x01, 0x04])
CMD_READ_DATA = bytes([0x03, 0xC4])
CMD_START_CLEAN = bytes([0x56, 0x07])
CMD_GET_SERIAL = bytes([0xD0, 0x33])
CMD_GET_PRODUCT = bytes([0xD1, 0x14])
CMD_GET_FIRMWARE = bytes([0xD1, 0x01])
CMD_RESET = bytes([0xD3, 0x04])


def _crc(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


class P167Vindstyrka(PluginBase):
    PLUGIN_ID = 167
    PLUGIN_NAME = "Environment - Sensirion SEN5x (IKEA Vindstyrka)"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        formula_option=True,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        i2c_no_device_check=True,
        i2c_max100khz=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x69]

    def __init__(self):
        super().__init__()
        self._addr: int = SEN5X_ADDR
        self._config: dict[str, Any] = {}

    async def _send_cmd(self, cmd: bytes, data: bytes = b"") -> None:
        i2c = self._hw.i2c
        payload = list(cmd + data)
        await i2c.write_i2c_block_data(self._addr, 0x00, payload)
        await asyncio.sleep(0.05)

    async def _read_data(self, num_words: int) -> list[float]:
        i2c = self._hw.i2c
        await self._send_cmd(CMD_READ_DATA)
        await asyncio.sleep(0.02)
        d = await i2c.read_i2c_block_data(self._addr, 0x00, num_words * 3)
        values = []
        for i in range(num_words):
            off = i * 3
            if off + 2 < len(d):
                high = d[off]
                low = d[off + 1]
                raw = (high << 8) | low
                if raw == 0xFFFF:
                    values.append(float("nan"))
                elif raw & 0x8000:
                    values.append(-((raw ^ 0xFFFF) + 1) * 0.01)
                else:
                    values.append(raw * 0.01)
        return values

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = SEN5X_ADDR
        if not self._hw:
            return False
        try:
            await self._send_cmd(CMD_STOP)
            await asyncio.sleep(0.1)
            await self._send_cmd(CMD_MEASURE)
            return True
        except Exception as e:
            logger.error("SEN5x init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("model", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            values = await self._read_data(6)
            model = int(self._config.get("model", 0))
            if len(values) >= 4:
                vals = {
                    "Temperature": round(values[0], 2) if not (isinstance(values[0], float) and values[0] != values[0]) else 0,
                    "Humidity": round(values[1], 2) if not (isinstance(values[1], float) and values[1] != values[1]) else 0,
                    "PM2_5": round(values[2], 1) if not (isinstance(values[2], float) and values[2] != values[2]) else 0,
                    "VOC": round(values[3], 0) if not (isinstance(values[3], float) and values[3] != values[3]) else 0,
                }
                if model >= 2 and len(values) >= 6:
                    vals["NOx"] = round(values[5], 0) if not (isinstance(values[5], float) and values[5] != values[5]) else 0
                event.data["values"] = vals
                return True
            return False
        except Exception as e:
            logger.error("SEN5x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        model_opts = [
            {"value": 0, "label": "IKEA Vindstyrka"},
            {"value": 1, "label": "Sensirion SEN54"},
            {"value": 2, "label": "Sensirion SEN55"},
        ]
        event.data["form"] = [
            {"name": "model", "label": "Model Type", "type": "select", "value": self._config.get("model", 0), "options": model_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == SEN5X_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = SEN5X_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0, "PM2_5": 0.0, "VOC": 0}
