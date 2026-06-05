from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    NUM_TASK_VALUES, SENSOR_TYPE_TO_VALUE_COUNT, SENSOR_TYPE_TO_DISCOVERY_VTYPES,
    SENSOR_TYPE_LABELS, SENSOR_TYPE_SINGLE, SENSOR_TYPE_DUAL, SENSOR_TYPE_TRIPLE, SENSOR_TYPE_QUAD,
    DEVICE_TYPE_DUMMY,
)
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p033")


class P033Dummy(PluginBase):
    PLUGIN_ID = 33
    PLUGIN_NAME = "Generic - Dummy Device"
    PLUGIN_VALUES = NUM_TASK_VALUES
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_DUMMY, vtype=SENSOR_TYPE_SINGLE, value_count=4, formula_option=True, timer_optional=True, output_data_type=OutputDataType.ALL, plugin_stats=True, custom_vtype_var=True, mqtt_state_class=True, no_device_settings=True)

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._values: list[str] = ["0"] * NUM_TASK_VALUES

    def _get_num_values(self) -> int:
        return SENSOR_TYPE_TO_VALUE_COUNT.get(self._config.get("TDNUM_out", 1), 1)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        n = self._get_num_values()
        for i in range(n):
            sv = self._config.get(f"SV{i + 1}")
            if sv is not None:
                self._values[i] = str(sv)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        values = {}
        n = self._get_num_values()
        for i in range(n):
            vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
            values[vname] = self._values[i]
        event.data["values"] = values
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        sv = event.data.get("values", {})
        if sv:
            n = self._get_num_values()
            for i in range(n):
                vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                if vname in sv:
                    self._values[i] = str(sv[vname])
            # Report resulting values so controller can publish them immediately
            values = {}
            for i in range(n):
                vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                values[vname] = self._values[i]
            event.data["values"] = values
            event.data["named_values"] = dict(values)
            event.data["value_names"] = list(values.keys())
            return True
        txt = event.string1 or ""
        parts = txt.split(",")
        if len(parts) >= 1 and parts[0].strip():
            n = self._get_num_values()
            for i, p in enumerate(parts):
                if i < n:
                    self._values[i] = p.strip()
            # Report resulting values so controller can publish them immediately
            values = {}
            for i in range(n):
                vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                values[vname] = self._values[i]
            event.data["values"] = values
            event.data["named_values"] = dict(values)
            event.data["value_names"] = list(values.keys())
            return True
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        n = self._get_num_values()
        for i in range(n):
            form.append({
                "name": f"SV{i + 1}",
                "label": f"Value {i + 1}",
                "type": "text",
                "value": self._config.get(f"SV{i + 1}", ""),
            })
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_webform_show_values(self, event: Event) -> bool | None:
        lines = []
        n = self._get_num_values()
        for i in range(n):
            vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
            lines.append(f"{vname}: {self._values[i]}")
        event.data["values_display"] = "\n".join(lines)
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        event.data["value_count"] = self._get_num_values()
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        sensor_type = self._config.get("TDNUM_out", SENSOR_TYPE_SINGLE)
        event.data["sensor_type"] = int(sensor_type)
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        sensor_type = int(self._config.get("TDNUM_out") or SENSOR_TYPE_SINGLE)
        n = self._get_num_values()
        vtypes = SENSOR_TYPE_TO_DISCOVERY_VTYPES.get(sensor_type, [])
        if len(vtypes) < n:
            vtypes = vtypes + [1] * (n - len(vtypes))
        event.data["vtypes"] = vtypes[:n]
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["TDNUM_out"] = SENSOR_TYPE_SINGLE
        for i in range(4):
            self._config[f"SV{i + 1}"] = "0"
            self._config[f"TDVN{i + 1}"] = ""
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        current = int(self._config.get("TDNUM_out") or SENSOR_TYPE_SINGLE)
        options = []
        for st, label in SENSOR_TYPE_LABELS.items():
            if st == 0 or st == 255:
                continue
            count = SENSOR_TYPE_TO_VALUE_COUNT.get(st, 1)
            options.append({"value": st, "label": f"{label} ({count} values)", "count": count})
        event.data["output_selector"] = {
            "name": "TDNUM_out",
            "label": "Number Output Values",
            "value": current,
            "options": options,
        }
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        values = {}
        n = SENSOR_TYPE_TO_VALUE_COUNT.get(task_config.get("TDNUM_out", 1), 1)
        for i in range(n):
            sv = task_config.get(f"SV{i + 1}")
            if sv:
                vname = task_config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                values[vname] = sv
            else:
                values[f"Value {i + 1}"] = "0"
        return values
