from __future__ import annotations

import glob
import logging
import os
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUAL, SENSOR_TYPE_SINGLE, SENSOR_V_TYPE_TEMP
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p004")

W1_BASE_DIR = "/sys/bus/w1/devices"

DS18B20_FAMILY_CODES = {"10", "22", "28", "3b", "42"}


class P004Dallas(PluginBase):
    PLUGIN_ID = 4
    PLUGIN_NAME = "Environment - DS18xxx/MAX31xxx/1-Wire Temperature"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_DUAL, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, output_data_type=OutputDataType.SIMPLE, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._sensor_id: str = ""

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._sensor_id = self._config.get("sensor_id", "")
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    def _find_ds18b20_devices(self) -> list[str]:
        rlist: list[str] = []
        try:
            devlist = glob.glob("/sys/bus/w1/devices/*")
            for d in devlist:
                td = d.split("/")
                tdname = td[-1]
                if "-" in tdname:
                    tf = tdname.split("-")[0].lower()
                    if tf in DS18B20_FAMILY_CODES:
                        rlist.append(tdname)
        except Exception:
            pass
        return rlist

    def _find_sensor(self) -> str | None:
        auto_select = self._config.get("auto_select", False)
        if auto_select:
            devices = self._find_ds18b20_devices()
            return devices[0] if devices else None
        if self._sensor_id and self._sensor_id != "None":
            if os.path.exists(os.path.join(W1_BASE_DIR, self._sensor_id)):
                return self._sensor_id
        return None

    def _read_temp_raw(self, sensor_id: str) -> list[str] | None:
        try:
            with open(os.path.join(W1_BASE_DIR, sensor_id, "w1_slave")) as f:
                return f.readlines()
        except Exception:
            return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        sensor = self._find_sensor()
        if not sensor:
            logger.warning("No DS18B20 sensor found")
            return False
        lines = self._read_temp_raw(sensor)
        if not lines:
            return False
        if "YES" not in lines[0]:
            lines = self._read_temp_raw(sensor)
            if not lines or "YES" not in lines[0]:
                return False
        pos = lines[1].find("t=")
        if pos == -1:
            return False
        try:
            temp = int(lines[1][pos + 2:].strip()) / 1000.0
            event.data["values"] = {"Temperature": round(temp, 2)}
            return True
        except (ValueError, IndexError):
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        current_sensor_id = self._config.get("sensor_id", "None")
        devices = self._find_ds18b20_devices()
        sensor_options = [{"value": "None", "label": "None"}]
        for addr in devices:
            sensor_options.append({"value": addr, "label": addr})
        event.data["form"] = [
            {"name": "pin", "label": "Data GPIO Pin", "type": "number", "value": self._config.get("pin", "")},
            {"name": "sensor_id", "label": "Sensor ID", "type": "select", "value": current_sensor_id, "options": sensor_options},
            {"name": "resolution", "label": "Resolution (bit)", "type": "select", "value": self._config.get("resolution", 12), "options": [
                {"value": 9, "label": "9 bit"},
                {"value": 10, "label": "10 bit"},
                {"value": 11, "label": "11 bit"},
                {"value": 12, "label": "12 bit"},
            ]},
            {"name": "error_state", "label": "Error State Output", "type": "select", "value": self._config.get("error_state", 0), "options": [
                {"value": 0, "label": "NaN"},
                {"value": 1, "label": "-127"},
                {"value": 2, "label": "0"},
                {"value": 3, "label": "125"},
                {"value": 4, "label": "Ignore"},
            ]},
            {"name": "auto_select", "label": "Auto Select Sensor", "type": "checkbox", "value": self._config.get("auto_select", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_TEMP]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0}
