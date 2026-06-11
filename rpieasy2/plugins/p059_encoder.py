from __future__ import annotations

import logging
import time as time_mod
from typing import Any

from rpieasy2.core.config import get_config
from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.hw.base import EDGE_FALLING
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_TRIPLE,
    SENSOR_TYPE_SWITCH,
    SENSOR_V_TYPE_CAN_SET,
    SENSOR_V_TYPE_SWITCH,
)

logger = logging.getLogger("rpieasy2.plugin.p059")


class P059Encoder(PluginBase):
    PLUGIN_ID = 59
    PLUGIN_NAME = "Switch Input - Rotary Encoder"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_TRIPLE,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        custom_vtype_var=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._pin_a: int = -1
        self._pin_b: int = -1
        self._pin_i: int = -1
        self._counter: int = 0
        self._last_a: int = -1
        self._last_b: int = -1
        self._step: int = 1
        self._bounce_ms: int = 5
        self._last_edge_ts: float = 0.0
        self._watch_fired: bool = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin_a = int(self._config.get("pin1") or -1)
        self._pin_b = int(self._config.get("pin2") or -1)
        self._pin_i = int(self._config.get("pin3") or -1)
        self._step = max(1, int(self._config.get("step") or 1))
        self._bounce_ms = max(0, int(self._config.get("bounce") or 5))
        if self._pin_a < 0 or self._pin_b < 0 or not self._hw:
            return False
        try:
            self._hw.gpio.claim_input(self._pin_a)
            self._hw.gpio.claim_input(self._pin_b)
            self._last_a = self._hw.gpio.read(self._pin_a)
            self._last_b = self._hw.gpio.read(self._pin_b)
            if self._pin_i > 0:
                self._hw.gpio.claim_input(self._pin_i)
        except Exception as e:
            logger.error("Encoder pin init failed: %s", e)
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
        self._watch_fired = False
        self._hw.gpio.watch(self._pin_a, EDGE_FALLING, self._edge_cb)
        if self._pin_i > 0:
            self._hw.gpio.watch(self._pin_i, EDGE_FALLING, self._z_cb)
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._hw:
            try:
                self._hw.gpio.unwatch(self._pin_a)
            except Exception:
                pass
            if self._pin_i > 0:
                try:
                    self._hw.gpio.unwatch(self._pin_i)
                except Exception:
                    pass
        return True

    def _edge_cb(self, gpio: int, level: int, ts: int) -> None:
        self._watch_fired = True
        if self._bounce_ms > 0:
            now = time_mod.monotonic()
            if (now - self._last_edge_ts) * 1000 < self._bounce_ms:
                return
            self._last_edge_ts = now
        if not self._hw:
            return
        if self._hw.gpio.read(self._pin_b) == 0:
            self._counter -= self._step
        else:
            self._counter += self._step
        self._clamp()

    def _z_cb(self, gpio: int, level: int, ts: int) -> None:
        self._watch_fired = True
        self._counter = 0

    def _clamp(self) -> None:
        lo = int(self._config.get("limit_min") or 0)
        hi = int(self._config.get("limit_max") or 100)
        if self._counter < lo:
            self._counter = lo
        elif self._counter > hi:
            self._counter = hi

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if self._pin_a < 0 or self._pin_b < 0 or not self._hw:
            return None
        if self._watch_fired:
            return None
        try:
            a = self._hw.gpio.read(self._pin_a)
            b = self._hw.gpio.read(self._pin_b)
            if a != self._last_a or b != self._last_b:
                if a != self._last_a:
                    if a != b:
                        self._counter += self._step
                    else:
                        self._counter -= self._step
                elif b != self._last_b:
                    if b != a:
                        self._counter += self._step
                    else:
                        self._counter -= self._step
                self._last_a = a
                self._last_b = b
                self._clamp()
            if self._pin_i > 0:
                try:
                    if self._hw.gpio.read(self._pin_i) == 0:
                        self._counter = 0
                except Exception:
                    pass
        except Exception:
            pass
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Counter": self._counter}
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("encwrite"):
            parts = command.split(",")
            if len(parts) > 1:
                try:
                    self._counter = int(parts[1].strip())
                    self._clamp()
                    return True
                except ValueError:
                    pass
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("step", 1)
        self._config.setdefault("limit_min", 0)
        self._config.setdefault("limit_max", 100)
        self._config.setdefault("bounce", 5)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "pin1", "label": "GPIO A (CLK)", "type": "number", "value": self._config.get("pin1", "")},
            {"name": "pin2", "label": "GPIO B (DT)", "type": "number", "value": self._config.get("pin2", "")},
            {"name": "pin3", "label": "GPIO I (Z, optional)", "type": "number", "value": self._config.get("pin3", -1)},
            {"name": "step", "label": "Step size", "type": "select", "value": self._config.get("step", 1), "options": [
                {"value": 1, "label": "1"},
                {"value": 2, "label": "2"},
                {"value": 3, "label": "3"},
                {"value": 4, "label": "4"},
            ]},
            {"name": "bounce", "label": "Debounce (ms)", "type": "number", "value": self._config.get("bounce", 5), "min": 0, "max": 1000},
            {"name": "limit_min", "label": "Limit min.", "type": "number", "value": self._config.get("limit_min", 0)},
            {"name": "limit_max", "label": "Limit max.", "type": "number", "value": self._config.get("limit_max", 100)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "GPIO A (CLK)", "number": 1},
            {"label": "GPIO B (DT)", "number": 2},
            {"label": "GPIO I (Z) (optional)", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH | SENSOR_V_TYPE_CAN_SET]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Counter": 0}
