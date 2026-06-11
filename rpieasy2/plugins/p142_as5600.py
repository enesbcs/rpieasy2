from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p142")

AS5600_ADDR = 0x36
AS5600L_ADDR = 0x40

AS5600_REG_RAW_ANGLE_H = 0x0C
AS5600_REG_ANGLE_H = 0x0E
AS5600_REG_STATUS = 0x0B
AS5600_REG_AGC = 0x1A
AS5600_REG_CONF_H = 0x02


class P142AS5600(PluginBase):
    PLUGIN_ID = 142
    PLUGIN_NAME = "Position - AS5600(L) Magnetic angle"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        formula_option=True,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x36, 0x40]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = AS5600_ADDR
        self._prev_angle: int = 0
        self._last_angle: int = 0
        self._rpm: float = 0.0
        self._last_time: float = 0.0

    async def _read_word(self, reg: int) -> int:
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 2)
        return (d[0] << 8) | d[1] if len(d) >= 2 else 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", AS5600_ADDR))
        except (ValueError, TypeError):
            self._addr = AS5600_ADDR
        self._last_time = asyncio.get_event_loop().time()
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", AS5600_ADDR)
        self._config.setdefault("output_mode", 0)
        self._config.setdefault("direction", 0)
        self._config.setdefault("start_position", 0)
        self._config.setdefault("max_position", 0)
        self._config.setdefault("angle_offset", 0.0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            now = asyncio.get_event_loop().time()
            raw_angle = await self._read_word(AS5600_REG_RAW_ANGLE_H)
            status = await self._hw.i2c.read_byte_data(self._addr, AS5600_REG_STATUS)
            agc = await self._hw.i2c.read_byte_data(self._addr, AS5600_REG_AGC)
            has_magnet = (status & 0x20) != 0
            magnet_strength = (status >> 4) & 0x03
            angle_deg = (raw_angle / 4096.0) * 360.0
            if self._last_time > 0 and has_magnet:
                delta = now - self._last_time
                if delta > 0:
                    angle_diff = raw_angle - self._prev_angle
                    if angle_diff > 2048:
                        angle_diff -= 4096
                    elif angle_diff < -2048:
                        angle_diff += 4096
                    self._rpm = (angle_diff / 4096.0) * (60.0 / delta)
            self._prev_angle = raw_angle
            self._last_time = now
            event.data["values"] = {
                "Angle": round(angle_deg, 2),
                "Direction": 1 if raw_angle >= self._last_angle else 0,
                "Rpm": round(abs(self._rpm), 2),
                "Raw": raw_angle,
            }
            self._last_angle = raw_angle
            return True
        except Exception as e:
            logger.error("AS5600 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [
            {"value": 0x36, "label": "0x36 (AS5600)"},
            {"value": 0x40, "label": "0x40 (AS5600L)"},
        ]
        range_opts = [
            {"value": 0, "label": "Degrees"},
            {"value": 1, "label": "Radians"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", AS5600_ADDR), "options": addr_opts},
            {"name": "output_mode", "label": "Output range", "type": "select", "value": self._config.get("output_mode", 0), "options": range_opts},
            {"name": "direction", "label": "Direction Counter-clockwise", "type": "checkbox", "value": self._config.get("direction", 0)},
            {"name": "start_position", "label": "Start position (0-4095)", "type": "number", "value": self._config.get("start_position", 0)},
            {"name": "max_position", "label": "Max position (0-4095)", "type": "number", "value": self._config.get("max_position", 0)},
            {"name": "angle_offset", "label": "Angle offset (-359.99..359.99)", "type": "number", "value": str(self._config.get("angle_offset", 0.0))},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", AS5600_ADDR))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in [AS5600_ADDR, AS5600L_ADDR]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Angle": 0.0, "Direction": 0, "Rpm": 0.0, "Raw": 0}
