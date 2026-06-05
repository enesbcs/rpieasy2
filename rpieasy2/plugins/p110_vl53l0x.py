from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p110")

VL53L0X_ADDRS = [0x29, 0x30]
VL53L0X_REG_IDENTIFICATION_MODEL_ID = 0xC0
VL53L0X_REG_SYSRANGE_START = 0x00
VL53L0X_REG_RESULT_RANGE_STATUS = 0x14
VL53L0X_REG_SLAVE_DEVICE_ADDRESS = 0x8A


class P110VL53L0X(PluginBase):
    PLUGIN_ID = 110
    PLUGIN_NAME = "Distance - VL53L0X (200cm)"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x29
        self._distance: int = 0
        self._direction: int = 0
        self._prev_distance: int = -1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x29)
        self._prev_distance = -1
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x29)
        self._config.setdefault("timing", 0)
        self._config.setdefault("range", 0)
        self._config.setdefault("send_always", False)
        self._config.setdefault("delta", 0)
        return True

    async def _init_sensor(self) -> bool:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            model_id = await i2c.read_byte_data(self._addr, VL53L0X_REG_IDENTIFICATION_MODEL_ID)
            if model_id != 0xEE:
                logger.error("VL53L0X model ID mismatch: 0x%02x", model_id)
                return False
            init_seq = [
                (0x88, 0x00), (0x80, 0x01), (0xFF, 0x01), (0x00, 0x00),
                (0x91, 0x3C), (0x00, 0x01), (0xFF, 0x00), (0x80, 0x00),
                (0xFD, 0x08), (0xFE, 0x08),
            ]
            for reg, val in init_seq:
                await i2c.write_byte_data(self._addr, reg, val)
            range_mode = int(self._config.get("range") or 0)
            if range_mode == 1:
                await i2c.write_byte_data(self._addr, 0xE0, 0x01)
                await i2c.write_byte_data(self._addr, 0xF9, 0x51)
                await i2c.write_byte_data(self._addr, 0xE0, 0x00)
            return True
        except Exception as e:
            logger.error("VL53L0X init failed: %s", e)
            return False

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            timing = int(self._config.get("timing") or 80)
            await i2c.write_byte_data(self._addr, VL53L0X_REG_SYSRANGE_START, 0x01)
            await asyncio.sleep(timing / 1000.0 + 0.01)
            d = await i2c.read_i2c_block_data(self._addr, VL53L0X_REG_RESULT_RANGE_STATUS, 12)
            if len(d) < 12:
                return False
            dist = (d[10] << 8) | d[11]
            if dist > 0:
                self._distance = dist
                delta = int(self._config.get("delta") or 0)
                send_always = bool(self._config.get("send_always", False))
                if send_always or abs(dist - self._prev_distance) >= delta:
                    if self._prev_distance >= 0:
                        diff = dist - self._prev_distance
                        self._direction = 1 if diff > 0 else -1 if diff < 0 else 0
                    else:
                        self._direction = 0
                    self._prev_distance = dist
                else:
                    return False
            event.data["values"] = {"Distance": self._distance, "Direction": self._direction}
            return True
        except Exception as e:
            logger.error("VL53L0X read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x29), "options": [
                {"value": 0x29, "label": "0x29"},
                {"value": 0x30, "label": "0x30"},
            ]},
            {"name": "timing", "label": "Timing", "type": "select", "value": self._config.get("timing", 80), "options": [
                {"value": 80, "label": "Normal"},
                {"value": 20, "label": "Fast"},
                {"value": 320, "label": "Accurate"},
            ]},
            {"name": "range", "label": "Range", "type": "select", "value": self._config.get("range", 0), "options": [
                {"value": 0, "label": "Normal"},
                {"value": 1, "label": "Long"},
            ]},
            {"name": "send_always", "label": "Send event when value unchanged", "type": "checkbox", "value": self._config.get("send_always", False)},
            {"name": "delta", "label": "Trigger delta (mm)", "type": "number", "value": self._config.get("delta", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in VL53L0X_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_DISTANCE, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Distance": 0, "Direction": 0}
