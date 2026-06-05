from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p113")

VL53L1X_ADDRS = [0x29, 0x30]
VL53L1X_REG_RESULT_RANGE_STATUS = 0x00
VL53L1X_REG_SYSRANGE_START = 0x00
VL53L1X_REG_IDENTIFICATION_MODEL_ID = 0x010F


class P113VL53L1X(PluginBase):
    PLUGIN_ID = 113
    PLUGIN_NAME = "Distance - VL53L1X (400cm)"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=3,
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
        self._ambient: int = 0
        self._direction: int = 0
        self._prev_distance: int = -1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x29)
        self._prev_distance = -1
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, 0x01, 0x0F)
            model_id = (d[0] << 8) | d[1] if len(d) >= 2 else 0
            if model_id not in (0xEACC, 0xEBAA):
                logger.warning("VL53L1X unexpected model ID: 0x%04x", model_id)
            await i2c.write_byte_data(self._addr, 0x00, 0x00)
            range_mode = int(self._config.get("range") or 0)
            if range_mode == 1:
                await i2c.write_byte_data(self._addr, 0x00, 0x01)
                await i2c.write_byte_data(self._addr, 0x00, 0x03)
        except Exception as e:
            logger.error("VL53L1X init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x29)
        self._config.setdefault("timing", 100)
        self._config.setdefault("range", 0)
        self._config.setdefault("send_always", False)
        self._config.setdefault("delta", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            timing = int(self._config.get("timing") or 100)
            await i2c.write_byte_data(self._addr, VL53L1X_REG_SYSRANGE_START, 0x01)
            await asyncio.sleep(max(timing / 1000.0, 0.05))
            d = await i2c.read_i2c_block_data(self._addr, VL53L1X_REG_RESULT_RANGE_STATUS, 17)
            if len(d) < 17:
                return False
            dist = (d[7] << 8) | d[8]
            amb = (d[9] << 8) | d[10]
            if dist > 0 and dist != 0xFFFF:
                self._distance = dist
                self._ambient = amb
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
            event.data["values"] = {"Distance": self._distance, "Ambient": self._ambient, "Direction": self._direction}
            return True
        except Exception as e:
            logger.error("VL53L1X read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x29), "options": [
                {"value": 0x29, "label": "0x29"},
                {"value": 0x30, "label": "0x30"},
            ]},
            {"name": "timing", "label": "Timing (ms)", "type": "select", "value": self._config.get("timing", 100), "options": [
                {"value": 20, "label": "20 (Fastest)"},
                {"value": 33, "label": "33 (Fast)"},
                {"value": 50, "label": "50"},
                {"value": 100, "label": "100 (Normal)"},
                {"value": 200, "label": "200 (Accurate)"},
                {"value": 500, "label": "500"},
            ]},
            {"name": "range", "label": "Range", "type": "select", "value": self._config.get("range", 0), "options": [
                {"value": 0, "label": "Normal (~130cm)"},
                {"value": 1, "label": "Long (~400cm)"},
            ]},
            {"name": "send_always", "label": "Send event when value unchanged", "type": "checkbox", "value": self._config.get("send_always", False)},
            {"name": "delta", "label": "Trigger delta (mm)", "type": "number", "value": self._config.get("delta", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in VL53L1X_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_DISTANCE, SENSOR_V_TYPE_ILLUMINANCE, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Distance": 0, "Ambient": 0, "Direction": 0}
