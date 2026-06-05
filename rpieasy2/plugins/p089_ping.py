from __future__ import annotations

import asyncio
import logging
import re as regex_module
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_CUSTOM0, SENSOR_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p089")


class P089Ping(PluginBase):
    PLUGIN_ID = 89
    PLUGIN_NAME = "Communication - Ping"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_CUSTOM0,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=1,
        decimals_only=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._hostname: str = ""
        self._fails: int = 0
        self._avg_ms: float = 0.0
        self._initialized = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._hostname = self._config.get("hostname", "")
        self._fails = 0
        self._avg_ms = 0.0
        self._initialized = True
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._initialized or not self._hostname:
            return False
        try:
            ping_count = int(self._config.get("ping_count") or 5)
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", str(ping_count), "-W", "2", self._hostname,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = stdout.decode("utf-8", errors="replace")
            if proc.returncode == 0:
                self._fails = 0
                times = regex_module.findall(r"time[=<]\s*([\d.]+)\s*ms", output)
                if times:
                    self._avg_ms = round(sum(float(t) for t in times) / len(times), 2)
            else:
                self._fails += 1
        except (asyncio.TimeoutError, FileNotFoundError, Exception) as e:
            logger.debug("Ping failed: %s", e)
            self._fails += 1

        vc = int(self._config.get("value_count") or 1)
        values: dict[str, Any] = {"Fails": self._fails}
        if vc > 1:
            values["Avg_ms"] = self._avg_ms
        event.data["values"] = values
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("pingset"):
            parts = command.split(",")
            if len(parts) > 1:
                try:
                    val = int(parts[1].strip())
                    if -1024 < val < 1024:
                        self._fails = val
                        return True
                except ValueError:
                    pass
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("hostname", "8.8.8.8")
        self._config.setdefault("ping_count", 5)
        self._config.setdefault("value_count", 1)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "hostname", "label": "Hostname", "type": "text", "value": self._config.get("hostname", "")},
            {"name": "ping_count", "label": "Ping count", "type": "number", "value": self._config.get("ping_count", 5)},
            {"name": "value_count", "label": "Available Values", "type": "select", "value": self._config.get("value_count", 1), "options": [
                {"value": 1, "label": "Fails"},
                {"value": 2, "label": "Fails, Avg_ms"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._hostname = self._config.get("hostname", "")
        self._fails = 0
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        event.data["value_count"] = int(self._config.get("value_count") or 1)
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Fails": 0}
