from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUAL, SENSOR_TYPE_DUAL, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p013")


class P013HCSR04(PluginBase):
    PLUGIN_ID = 13
    PLUGIN_NAME = "Position - HC-SR04, RCW-0001, etc."
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_DUAL, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, timer_optional=True, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._echo: int = -1
        self._trig: int = -1
        self._config: dict[str, Any] = {}
        self._last: float = 0.0
        self._state: int = 0
        self._buf: deque[float] = deque(maxlen=20)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._echo = self._config.get("echo_pin", -1)
        self._trig = self._config.get("trigger_pin", -1)
        if self._echo > 0 and self._trig > 0 and self._hw:
            try:
                self._hw.gpio.claim_output(self._trig)
                self._hw.gpio.claim_input(self._echo)
            except Exception as e:
                logger.error("HC-SR04 init failed: %s", e)
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

    async def _read_distance(self) -> float | None:
        if self._echo < 0 or self._trig < 0 or not self._hw:
            return None
        gpio = self._hw.gpio
        tw = max(5, min(50, int(self._config.get("trigger_width") or 10)))
        gpio.write(self._trig, 0)
        gpio.write(self._trig, 1)
        await asyncio.sleep(tw / 1_000_000)
        gpio.write(self._trig, 0)
        max_dist = int(self._config.get("max_distance") or 400)
        echo_timeout = max_dist / 17150 + 0.02
        tout = time.monotonic() + echo_timeout
        while gpio.read(self._echo) == 0:
            if time.monotonic() > tout:
                return None
        start = time.monotonic()
        tout = start + echo_timeout
        while gpio.read(self._echo) == 1:
            if time.monotonic() > tout:
                return None
        dist = (time.monotonic() - start) * 17150
        if dist > max_dist:
            return None
        unit = int(self._config.get("measuring_unit") or 0)
        if unit == 1:
            dist = dist / 2.54
        return round(dist, 2)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._echo < 0 or self._trig < 0 or not self._hw:
            return False
        raw = await self._read_distance()
        if raw is None:
            return False
        mode = int(self._config.get("operating_mode") or 0)
        flt = int(self._config.get("filter_type") or 0)
        fsize = max(2, min(20, int(self._config.get("filter_size") or 3)))
        if flt == 1:
            self._buf.append(raw)
            if len(self._buf) >= fsize:
                s = sorted(self._buf)[:fsize]
                raw = s[len(s) // 2]
        threshold = float(self._config.get("threshold") or 0)
        self._last = raw
        if mode == 0:
            event.data["values"] = {"Distance": raw}
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        elif mode == 1:
            self._state = 1 if raw <= threshold else 0
            event.data["values"] = {"State": self._state}
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        elif mode == 2:
            self._state = 1 if raw <= threshold else 0
            event.data["values"] = {"Distance": raw, "State": self._state}
            event.data["vtype"] = SENSOR_TYPE_DUAL
            event.data["value_count"] = 2
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        mode = int(self._config.get("operating_mode") or 0)
        if mode == 2:
            event.data["value_names"] = ["Distance", "State"]
        elif mode == 1:
            event.data["value_names"] = ["State"]
        else:
            event.data["value_names"] = ["Distance"]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        mode = int(self._config.get("operating_mode") or 0)
        event.data["value_count"] = 2 if mode == 2 else 1
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        mode = int(self._config.get("operating_mode") or 0)
        if mode == 2:
            event.data["vtype"] = SENSOR_TYPE_DUAL
        else:
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "trigger_pin", "label": "Trigger GPIO", "type": "number", "value": self._config.get("trigger_pin", "")},
            {"name": "echo_pin", "label": "Echo GPIO", "type": "number", "value": self._config.get("echo_pin", "")},
            {"name": "operating_mode", "label": "Operating Mode", "type": "select", "value": self._config.get("operating_mode", 0), "options": [
                {"value": 0, "label": "Value"}, {"value": 1, "label": "State"}, {"value": 2, "label": "Combined"},
            ]},
            {"name": "threshold", "label": "Threshold Distance", "type": "number", "value": self._config.get("threshold", 0)},
            {"name": "max_distance", "label": "Max Distance (cm)", "type": "number", "value": self._config.get("max_distance", 400)},
            {"name": "measuring_unit", "label": "Measuring Unit", "type": "select", "value": self._config.get("measuring_unit", 0), "options": [
                {"value": 0, "label": "cm"}, {"value": 1, "label": "inch"},
            ]},
            {"name": "filter_type", "label": "Filter Type", "type": "select", "value": self._config.get("filter_type", 0), "options": [
                {"value": 0, "label": "None"}, {"value": 1, "label": "Median"},
            ]},
            {"name": "filter_size", "label": "Filter Size (pings 2-20)", "type": "number", "value": self._config.get("filter_size", 3)},
            {"name": "trigger_width", "label": "Trigger Width (usec)", "type": "number", "value": self._config.get("trigger_width", 10)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_DISTANCE, SENSOR_V_TYPE_SWITCH]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Distance": 0.0, "State": 0}
