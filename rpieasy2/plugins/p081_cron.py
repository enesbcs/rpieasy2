from __future__ import annotations

import logging
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUMMY, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p081")


def _parse_cron(expr: str) -> list[list[int]] | None:
    parts = expr.strip().split()
    if len(parts) != 6:
        return None
    fields = []
    for i, part in enumerate(parts):
        ranges = {
            0: (0, 59),
            1: (0, 59),
            2: (0, 23),
            3: (1, 31),
            4: (1, 12),
            5: (0, 6),
        }
        lo, hi = ranges[i]
        values: set[int] = set()
        for item in part.split(","):
            item = item.strip()
            if item == "*":
                values.update(range(lo, hi + 1))
            elif "/" in item:
                base, step = item.split("/")
                base = base.strip()
                step = int(step)
                rng = range(lo, hi + 1) if base == "*" else range(int(base), hi + 1)
                for v in rng:
                    if (v - (lo if base == "*" else int(base))) % step == 0:
                        values.add(v)
            elif "-" in item:
                a, b = item.split("-")
                values.update(range(int(a), int(b) + 1))
            else:
                values.add(int(item))
        fields.append(sorted(values))
    return fields


def _cron_matches(fields: list[list[int]], t: time.struct_time) -> bool:
    if t.tm_sec not in fields[0]:
        return False
    if t.tm_min not in fields[1]:
        return False
    if t.tm_hour not in fields[2]:
        return False
    if t.tm_mday not in fields[3]:
        return False
    if t.tm_mon not in fields[4]:
        return False
    if t.tm_wday not in fields[5]:
        return False
    return True


class P081Cron(PluginBase):
    PLUGIN_ID = 81
    PLUGIN_NAME = "Generic - CRON"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUMMY,
        vtype=SENSOR_TYPE_NONE,
        value_count=2,
        decimals_only=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._expression: str = "* * * * * *"
        self._fields: list[list[int]] | None = None
        self._last_execution: float = 0.0
        self._next_execution: float = 0.0
        self._last_checked: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        raw = self._config.get("cron_expression", "* * * * * *")
        self._expression = raw if isinstance(raw, str) else "* * * * * *"
        self._fields = _parse_cron(self._expression)
        if self._fields:
            self._next_execution = self._compute_next()
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("cron_expression", "* * * * * *")
        return True

    def _compute_next(self) -> float:
        if not self._fields:
            return 0.0
        now = time.time()
        t = time.localtime(now)
        for _ in range(525600):
            if _cron_matches(self._fields, t):
                return time.mktime(t)
            now += 1
            t = time.localtime(now)
        return 0.0

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        now = int(time.time())
        if now == self._last_checked:
            return None
        self._last_checked = now
        if not self._fields:
            return None
        t = time.localtime(now)
        if _cron_matches(self._fields, t):
            if now != int(self._last_execution):
                self._last_execution = float(now)
                self._next_execution = self._compute_next()
                event.data["values"] = {
                    "LastExecution": self._last_execution,
                    "NextExecution": self._next_execution,
                }
                rules = self._config.get("task_device_name", "")
                if rules:
                    import asyncio
                    asyncio.ensure_future(self._fire_event(f"Cron#{rules}"))
                return True
        else:
            if now >= self._next_execution and self._next_execution > 0:
                self._next_execution = self._compute_next()
        return None

    async def _fire_event(self, event_str: str) -> None:
        event_bus = getattr(self, "_event_bus", None)
        if event_bus:
            evt = Event(type="PLUGIN_WRITE", data={"string1": event_str})
            await event_bus.publish(evt)

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {
            "LastExecution": self._last_execution,
            "NextExecution": self._next_execution,
        }
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "cron_expression", "label": "CRON Expression", "type": "text",
             "value": self._config.get("cron_expression", "* * * * * *")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._expression = self._config.get("cron_expression", "* * * * * *")
        self._fields = _parse_cron(self._expression)
        if self._fields:
            self._next_execution = self._compute_next()
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"LastExecution": 0.0, "NextExecution": 0.0}
