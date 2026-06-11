from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p163")

RS_DEFAULT_I2C_ADDRESS = 0x66

RS_REG_FW_VER = 0x00
RS_REG_RAD_INTENS_DYNAMIC = 0x03
RS_REG_RAD_INTENS_STATIC_L = 0x06
RS_REG_RAD_INTENS_STATIC_H = 0x07
RS_REG_PULSE_COUNT_0 = 0x0A
RS_REG_CALIBRATION = 0x0C
RS_REG_HV_GENERATOR = 0x0E
RS_REG_LED_STATE = 0x0F


class P163RadSens(PluginBase):
    PLUGIN_ID = 163
    PLUGIN_NAME = "Environment - RadSens I2C radiation counter"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        formula_option=True,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x66]

    def __init__(self):
        super().__init__()
        self._addr: int = RS_DEFAULT_I2C_ADDRESS
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = RS_DEFAULT_I2C_ADDRESS
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            try:
                led = int(self._config.get("led_state", 1))
            except (ValueError, TypeError):
                led = 1
            try:
                lp = int(self._config.get("low_power", 0))
            except (ValueError, TypeError):
                lp = 0
            await i2c.write_byte_data(self._addr, RS_REG_LED_STATE, led)
            await i2c.write_byte_data(self._addr, RS_REG_HV_GENERATOR, 0 if lp else 1)
            return True
        except Exception as e:
            logger.error("RadSens init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("led_state", 1)
        self._config.setdefault("low_power", 0)
        self._config.setdefault("read_increment", 0)
        self._config.setdefault("reset_on_read", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            dyn = await i2c.read_byte_data(self._addr, RS_REG_RAD_INTENS_DYNAMIC)
            sh = await i2c.read_byte_data(self._addr, RS_REG_RAD_INTENS_STATIC_H)
            sl = await i2c.read_byte_data(self._addr, RS_REG_RAD_INTENS_STATIC_L)
            static_intensity = (sh << 8) | sl
            cnt0 = await i2c.read_byte_data(self._addr, RS_REG_PULSE_COUNT_0)
            cnt1 = await i2c.read_byte_data(self._addr, RS_REG_PULSE_COUNT_0 + 1)
            cnt2 = await i2c.read_byte_data(self._addr, RS_REG_PULSE_COUNT_0 + 2)
            cnt3 = await i2c.read_byte_data(self._addr, RS_REG_PULSE_COUNT_0 + 3)
            pulse_count = (cnt3 << 24) | (cnt2 << 16) | (cnt1 << 8) | cnt0
            event.data["values"] = {
                "Count": pulse_count,
                "iDynamic": dyn,
                "iStatic": static_intensity,
                "IncrCount": pulse_count,
            }
            if int(self._config.get("reset_on_read", 0)):
                await i2c.write_byte_data(self._addr, RS_REG_PULSE_COUNT_0, 0)
            return True
        except Exception as e:
            logger.error("RadSens read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "read_increment", "label": "Read incremental count", "type": "checkbox", "value": self._config.get("read_increment", 0)},
            {"name": "reset_on_read", "label": "Reset after read", "type": "checkbox", "value": self._config.get("reset_on_read", 0)},
            {"name": "low_power", "label": "Use Low Power mode", "type": "checkbox", "value": self._config.get("low_power", 0)},
            {"name": "led_state", "label": "Enable onboard Led", "type": "checkbox", "value": self._config.get("led_state", 1)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == RS_DEFAULT_I2C_ADDRESS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = RS_DEFAULT_I2C_ADDRESS
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Count": 0, "iDynamic": 0, "iStatic": 0, "IncrCount": 0}
