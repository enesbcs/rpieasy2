from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUAL, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p063")


class P063TTP229(PluginBase):
    PLUGIN_ID = 63
    PLUGIN_NAME = "Keypad - TTP229 Touch"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUAL,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("pin_scl", -1)
        self._config.setdefault("pin_sdo", -1)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            scl = int(self._config.get("pin_scl", -1))
            sdo = int(self._config.get("pin_sdo", -1))
            if scl < 0 or sdo < 0:
                return False
            gpio = self._hw.gpio
            await gpio.set_mode(sdo, "output")
            await gpio.write(sdo, 1)
            await asyncio.sleep(0.0001)
            await gpio.write(sdo, 0)
            await asyncio.sleep(0.00001)
            await gpio.set_mode(sdo, "input")
            value = 0
            mask = 1
            for _ in range(16):
                await gpio.write(scl, 1)
                await asyncio.sleep(0.000001)
                await gpio.write(scl, 0)
                v = await gpio.read(sdo)
                if not v:
                    value |= mask
                await asyncio.sleep(0.000001)
                mask <<= 1
            event.data["values"] = {"ScanCode": value}
            return True
        except Exception as e:
            logger.error("TTP229 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        if self._hw:
            gpio_list = [{"value": p, "label": f"GPIO{p}"} for p in range(28)]
        else:
            gpio_list = []
        event.data["form"] = [
            {"name": "pin_scl", "label": "SCL GPIO pin", "type": "select", "value": self._config.get("pin_scl", -1), "options": gpio_list},
            {"name": "pin_sdo", "label": "SDO GPIO pin", "type": "select", "value": self._config.get("pin_sdo", -1), "options": gpio_list},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"ScanCode": 0}
