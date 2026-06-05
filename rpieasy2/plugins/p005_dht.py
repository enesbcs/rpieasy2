from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SINGLE, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p005")


class P005DHT(PluginBase):
    PLUGIN_ID = 5
    PLUGIN_NAME = "Environment - DHT11/12/22  SONOFF2301/7021/MS01"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_SINGLE, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._pin: int = -1
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin = self._config.get("pin", -1)
        if self._pin > 0 and self._hw:
            try:
                self._hw.gpio.claim_output(self._pin)
            except Exception as e:
                logger.error(f"DHT init failed: {e}")
                try:
                    cfg = get_config()
                    task = cfg.get_task(event.task_index) if event.task_index >= 0 else None
                    if task is not None:
                        task["enabled"] = False
                        task["TDE"] = False
                        cfg.set_task(event.task_index, task)
                        cfg.save()
                        await get_event_bus().publish(Event(type="TASK_CONFIG_CHANGED", task_index=event.task_index, data={"task_config": task}))
                except Exception:
                    logger.exception("Failed to disable task after GPIO init error")
                return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._pin < 0 or not self._hw:
            return False
        sensor = int(self._config.get("sensor_model") or 1)
        gpio = self._hw.gpio
        gpio.write(self._pin, 0)
        await asyncio.sleep(0.018 if sensor != 0 else 0.020)
        gpio.claim_input(self._pin)
        tout = time.monotonic() + 0.001
        while gpio.read(self._pin) == 1:
            if time.monotonic() > tout: return False
        tout = time.monotonic() + 0.001
        while gpio.read(self._pin) == 0:
            if time.monotonic() > tout: return False
        bits = []
        for _ in range(40):
            tout = time.monotonic() + 0.00015
            while gpio.read(self._pin) == 0:
                if time.monotonic() > tout: break
            start = time.monotonic()
            tout = time.monotonic() + 0.00015
            while gpio.read(self._pin) == 1:
                if time.monotonic() > tout: break
            bits.append(1 if time.monotonic() - start > 0.00005 else 0)
        if len(bits) < 40: return False
        rh = int("".join(str(b) for b in bits[:16]), 2)
        t = int("".join(str(b) for b in bits[16:32]), 2)
        if sensor == 0:
            temp = float(t)
            hum = float(rh)
        elif sensor == 5:
            hum = rh / 10.0 if rh < 0x8000 else (rh & 0x7FFF) / -10.0
            event.data["values"] = {"RAW": t, "Humidity": round(hum, 1)}
            return True
        else:
            temp = t / 10.0 if t < 0x8000 else (t & 0x7FFF) / -10.0
            hum = rh / 10.0 if rh < 0x8000 else (rh & 0x7FFF) / -10.0
        event.data["values"] = {"Temperature": round(temp, 1), "Humidity": round(hum, 1)}
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "pin", "label": "GPIO Pin", "type": "number", "value": self._config.get("pin", "")},
            {"name": "sensor_model", "label": "Sensor Model", "type": "select", "value": self._config.get("sensor_model", 1), "options": [
                {"value": 0, "label": "DHT11"},
                {"value": 1, "label": "DHT22"},
                {"value": 2, "label": "DHT12"},
                {"value": 3, "label": "AM2301"},
                {"value": 4, "label": "SI7021"},
                {"value": 5, "label": "MS01"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [{"label": "GPIO Pin (Data, bidirectional)", "number": 1}]
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        sensor = int(self._config.get("sensor_model") or 1)
        if sensor == 5:
            event.data["value_names"] = ["RAW", "Humidity"]
        else:
            event.data["value_names"] = ["Temperature", "Humidity"]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
