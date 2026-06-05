from __future__ import annotations

import logging
import time as time_mod
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.hw.base import EDGE_BOTH, EDGE_FALLING, EDGE_RISING
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_SINGLE,
    SENSOR_TYPE_DUAL,
    SENSOR_TYPE_SINGLE,
    SENSOR_TYPE_TRIPLE,
)
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p003")

CT_DELTA = 0
CT_DELTA_TOTAL_TIME = 1
CT_TOTAL = 2
CT_DELTA_TOTAL = 3
CT_TIME = 4
CT_TOTAL_TIME = 5
CT_TIME_DELTA = 6

VALUE_NAMES_MAP: dict[int, list[str]] = {
    CT_DELTA: ["Count"],
    CT_DELTA_TOTAL_TIME: ["Count", "Total", "Time"],
    CT_TOTAL: ["Total"],
    CT_DELTA_TOTAL: ["Count", "Total"],
    CT_TIME: ["Time"],
    CT_TOTAL_TIME: ["Total", "Time"],
    CT_TIME_DELTA: ["Time", "Count"],
}

VTYPE_MAP: dict[int, int] = {
    CT_DELTA: SENSOR_TYPE_SINGLE,
    CT_DELTA_TOTAL_TIME: SENSOR_TYPE_TRIPLE,
    CT_TOTAL: SENSOR_TYPE_SINGLE,
    CT_DELTA_TOTAL: SENSOR_TYPE_DUAL,
    CT_TIME: SENSOR_TYPE_SINGLE,
    CT_TOTAL_TIME: SENSOR_TYPE_DUAL,
    CT_TIME_DELTA: SENSOR_TYPE_DUAL,
}


class P003PulseCounter(PluginBase):
    PLUGIN_ID = 3
    PLUGIN_NAME = "Generic - Pulse counter"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SINGLE, vtype=SENSOR_TYPE_SINGLE, value_count=3,
        pull_up_option=True, formula_option=True, send_data_option=True,
        timer_option=True, timer_optional=True, plugin_stats=True,
        custom_vtype_var=True, mqtt_state_class=True,
    )

    def __init__(self):
        super().__init__()
        self._pin: int = -1
        self._count: int = 0
        self._total: int = 0
        self._last_reset_time: float = 0.0
        self._config: dict[str, Any] = {}

    def _edge(self, gpio: int, level: int, ts: int) -> None:
        debounce = int(self._config.get("debounce_time") or 0)
        now = time_mod.monotonic()
        if debounce > 0:
            elapsed = (now - self._last_reset_time) * 1000
            if elapsed < debounce:
                return
        self._count += 1
        self._total += 1
        self._last_reset_time = now

    def _get_edge_type(self) -> int:
        mode = int(self._config.get("mode_type") or 0)
        if mode == 1:
            return EDGE_FALLING
        elif mode == 2:
            return EDGE_BOTH
        return EDGE_RISING

    def _get_value_names(self) -> list[str]:
        ct = int(self._config.get("counter_type") or 0)
        return VALUE_NAMES_MAP.get(ct, ["Count"])

    def _get_values(self) -> dict[str, Any]:
        ct = int(self._config.get("counter_type") or 0)
        names = VALUE_NAMES_MAP.get(ct, ["Count"])
        elapsed = time_mod.time() - self._last_reset_time if self._last_reset_time > 0 else 0
        avail: dict[str, Any] = {
            "Count": self._count,
            "Total": self._total,
            "Time": elapsed,
        }
        return {n: avail.get(n, 0) for n in names}

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin = int(self._config.get("pin") or -1)
        self._count = 0
        self._total = int(self._config.get("total") or 0)
        self._last_reset_time = time_mod.time()
        if self._pin > 0 and self._hw:
            try:
                pull_up = self._config.get("pull_up", False)
                self._hw.gpio.claim_input(self._pin, pull_up=bool(pull_up))
                self._hw.gpio.watch(self._pin, self._get_edge_type(), self._edge)
            except Exception as e:
                logger.error("Pulse counter init failed: %s", e)
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

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._pin > 0 and self._hw:
            try:
                self._hw.gpio.unwatch(self._pin)
            except Exception:
                pass
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = self._get_values()
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "")
        args = event.data.get("args", [])
        if command == "resetpulsecounter":
            self._count = 0
            self._last_reset_time = time_mod.time()
            return True
        if command == "setpulsecountertotal":
            if args:
                try:
                    self._total = int(args[0])
                except (ValueError, IndexError):
                    pass
            return True
        return None

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [{"label": "GPIO Pin (Pulse)", "number": 1}]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        names = self._get_value_names()
        event.data["value_count"] = len(names)
        return True

    
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        ct = int(self._config.get("counter_type") or 0)
        value_names = VALUE_NAMES_MAP.get(ct, ["Count"])
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE] * len(value_names)
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        ct = int(self._config.get("counter_type") or 0)
        event.data["vtype"] = VTYPE_MAP.get(ct, SENSOR_TYPE_SINGLE)
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        names = self._get_value_names()
        event.data["value_names"] = names
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        ct = int(self._config.get("counter_type") or 0)
        event.data["form"] = [
            {"name": "pin", "label": "GPIO Pin", "type": "number", "value": self._config.get("pin", "")},
            {"name": "pull_up", "label": "Pull-up", "type": "checkbox", "value": self._config.get("pull_up", False)},
            {"name": "debounce_time", "label": "Debounce Time (ms)", "type": "number", "value": self._config.get("debounce_time", 0)},
            {"name": "counter_type", "label": "Counter Type", "type": "select", "value": ct, "options": [
                {"value": 0, "label": "Delta"},
                {"value": 1, "label": "Delta/Total/Time"},
                {"value": 2, "label": "Total"},
                {"value": 3, "label": "Delta/Total"},
                {"value": 4, "label": "Time"},
                {"value": 5, "label": "Total/Time"},
                {"value": 6, "label": "Time/Delta"},
            ]},
            {"name": "mode_type", "label": "GPIO Trigger Mode", "type": "select", "value": self._config.get("mode_type", 0), "options": [
                {"value": 0, "label": "Rising edge"},
                {"value": 1, "label": "Falling edge"},
                {"value": 2, "label": "Both edges"},
            ]},
            {"name": "ignore_zero", "label": "Ignore Multiple Delta=0", "type": "checkbox", "value": self._config.get("ignore_zero", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        ct = int(self._config.get("counter_type") or 0)
        if ct in (CT_TOTAL, CT_TIME, CT_TOTAL_TIME):
            logger.warning("Counter type %d: value names are not auto-updated!", ct)
        old_pin = self._pin
        self._pin = int(self._config.get("pin") or -1)
        if old_pin != self._pin and old_pin > 0 and self._hw:
            try:
                self._hw.gpio.unwatch(old_pin)
            except Exception:
                pass
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Count": 0, "Total": 0, "Time": 0}
