from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import psutil

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_DUMMY, SENSOR_TYPE_QUAD, SENSOR_TYPE_SINGLE, SENSOR_TYPE_DUAL,
    SENSOR_TYPE_TRIPLE, SENSOR_TYPE_TO_VALUE_COUNT, SENSOR_TYPE_LABELS,
    SENSOR_TYPE_TO_DISCOVERY_VTYPES, get_local_ip,
    SENSOR_V_TYPE_DURATION, SENSOR_V_TYPE_SIGNAL, SENSOR_V_TYPE_SINGLE,
    SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_TEXT,
)
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p026")

_VALUE_OPTIONS = [
    {"value": 0, "label": "Uptime"},
    {"value": 1, "label": "Free RAM"},
    {"value": 2, "label": "WiFi RSSI"},
    {"value": 3, "label": "CPU Load"},
    {"value": 4, "label": "System load"},
    {"value": 5, "label": "IP 1. Octet"},
    {"value": 6, "label": "IP 2. Octet"},
    {"value": 7, "label": "IP 3. Octet"},
    {"value": 8, "label": "IP 4. Octet"},
    {"value": 9, "label": "IP Address"},
    {"value": 10, "label": "Free Stack"},
    {"value": 11, "label": "None"},
    {"value": 12, "label": "CPU Load 2"},
    {"value": 13, "label": "CPU Load 3"},
    {"value": 14, "label": "Internal Temperature"},
    {"value": 15, "label": "CPU Load 1m"},
    {"value": 16, "label": "CPU Load 5m"},
    {"value": 17, "label": "CPU Load 15m"},
    {"value": 18, "label": "Hostname"},
]

_START_TIME = time.time()

_VALUE_ID_TO_LABEL: dict[int, str] = {o["value"]: o["label"] for o in _VALUE_OPTIONS}

_VALUE_ID_TO_VTYPE: dict[int, int] = {
    0: SENSOR_V_TYPE_DURATION,
    1: SENSOR_V_TYPE_SINGLE,
    2: SENSOR_V_TYPE_SIGNAL,
    3: SENSOR_V_TYPE_SINGLE,
    4: SENSOR_V_TYPE_SINGLE,
    5: SENSOR_V_TYPE_SINGLE,
    6: SENSOR_V_TYPE_SINGLE,
    7: SENSOR_V_TYPE_SINGLE,
    8: SENSOR_V_TYPE_SINGLE,
    9: SENSOR_V_TYPE_TEXT,
    10: SENSOR_V_TYPE_SINGLE,
    12: SENSOR_V_TYPE_SINGLE,
    13: SENSOR_V_TYPE_SINGLE,
    14: SENSOR_V_TYPE_TEMP,
    15: SENSOR_V_TYPE_SINGLE,
    16: SENSOR_V_TYPE_SINGLE,
    17: SENSOR_V_TYPE_SINGLE,
    18: SENSOR_V_TYPE_TEXT,
}


async def _get_sensor_value(value_id: int) -> str:
    if value_id == 0:
        uptime_sec = time.time() - _START_TIME
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        minutes = int((uptime_sec % 3600) // 60)
        if days > 0:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        return f"{hours:02d}h{minutes:02d}m"
    if value_id == 1:
        mem = psutil.virtual_memory()
        return f"{mem.available // 1024} kB"
    if value_id == 2:
        from rpieasy2.core.util import get_wifi_rssi, get_wifi_ssid_async
        rssi_raw = get_wifi_rssi()
        rssi = str(rssi_raw) if rssi_raw is not None else "-"
        if rssi_raw is not None:
            rssi += " [dBm]"
        ssid = await get_wifi_ssid_async()
        if ssid:
            rssi += f" ({ssid})"
        return rssi
    if value_id == 3:
        return f"{psutil.cpu_percent(interval=0)} [%]"
    if value_id == 4:
        load = psutil.getloadavg()
        return f"{load[0]:.2f}"
    if value_id in (5, 6, 7, 8, 9):
        ip = get_local_ip()
        if ip == "-":
            return "-"
        parts = ip.split(".")
        if value_id == 5:
            return parts[0] if len(parts) > 0 else "-"
        if value_id == 6:
            return parts[1] if len(parts) > 1 else "-"
        if value_id == 7:
            return parts[2] if len(parts) > 2 else "-"
        if value_id == 8:
            return parts[3] if len(parts) > 3 else "-"
        if value_id == 9:
            return ip
    if value_id == 10:
        mem = psutil.virtual_memory()
        free = mem.available
        return f"{free} B"
    if value_id == 11:
        return ""
    if value_id == 12:
        return f"{psutil.cpu_percent(interval=0)} [%]"
    if value_id == 13:
        return f"{psutil.cpu_percent(interval=0)} [%]"
    if value_id == 14:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                t = int(f.read().strip())
                return f"{t / 1000:.1f} [°C]"
        except Exception:
            return "-"
    if value_id == 15:
        return f"{psutil.getloadavg()[0]:.2f}"
    if value_id == 16:
        return f"{psutil.getloadavg()[1]:.2f}"
    if value_id == 17:
        return f"{psutil.getloadavg()[2]:.2f}"
    if value_id == 18:
        import socket
        return socket.gethostname()
    return ""


async def _get_sensor_value_raw(value_id: int):
    # Return a numeric/raw value suitable for MQTT state (no unit suffix)
    if value_id == 0:
        return None
    if value_id == 1:
        mem = psutil.virtual_memory()
        return int(mem.available)
    if value_id == 2:
        from rpieasy2.core.util import get_wifi_rssi
        return get_wifi_rssi()
    if value_id in (3, 12, 13):
        return float(psutil.cpu_percent(interval=0))
    if value_id == 4:
        load = psutil.getloadavg()
        return float(load[0])
    if value_id in (5, 6, 7, 8):
        ip = get_local_ip()
        if ip == "-":
            return None
        parts = ip.split('.')
        try:
            return int(parts[value_id - 5])
        except Exception:
            return None
    if value_id == 9:
        ip = get_local_ip()
        return ip
    if value_id == 10:
        mem = psutil.virtual_memory()
        return int(mem.available)
    if value_id == 14:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                t = int(f.read().strip())
                return float(t / 1000.0)
        except Exception:
            return None
    return None


class P026Sysinfo(PluginBase):
    PLUGIN_ID = 26
    PLUGIN_NAME = "Generic - System Info"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_DUMMY, vtype=SENSOR_TYPE_QUAD, value_count=4, send_data_option=True, timer_option=True, formula_option=True, output_data_type=OutputDataType.SIMPLE, plugin_stats=True, custom_vtype_var=True, mqtt_state_class=True, no_device_settings=True)

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    def _get_num_values(self) -> int:
        return SENSOR_TYPE_TO_VALUE_COUNT.get(int(self._config.get("TDNUM_out") or SENSOR_TYPE_QUAD), 4)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        try:
            values = {}
            n = self._get_num_values()
            for i in range(n):
                vkey = f"value_{i + 1}"
                vid = self._config.get(vkey, 11)
                if vid != 11:
                    vname = _VALUE_ID_TO_LABEL.get(vid, f"Value_{i + 1}")
                    raw = await _get_sensor_value_raw(vid)
                    if raw is None:
                        values[vname] = await _get_sensor_value(vid)
                    else:
                        values[vname] = raw
            event.data["values"] = values
            return True
        except Exception as e:
            logger.error(f"Sysinfo read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = []
        for i in range(4):
            event.data["form"].append({
                "name": f"value_{i + 1}", "label": f"Output Value {i + 1}",
                "type": "select", "value": self._config.get(f"value_{i + 1}", 11),
                "options": _VALUE_OPTIONS,
            })
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        event.data["value_count"] = self._get_num_values()
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        sensor_type = int(self._config.get("TDNUM_out") or SENSOR_TYPE_QUAD)
        event.data["sensor_type"] = sensor_type
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        try:
            n = self._get_num_values()
            vtypes = []
            for i in range(n):
                vkey = f"value_{i + 1}"
                vid = self._config.get(vkey, 11)
                vt = _VALUE_ID_TO_VTYPE.get(vid, SENSOR_V_TYPE_SINGLE)
                vtypes.append(vt)
            event.data["vtypes"] = vtypes
            return True
        except Exception as e:
            logger.error(f"Sysinfo discovery vtypes failed: {e}")
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["TDNUM_out"] = SENSOR_TYPE_QUAD
        for i in range(4):
            self._config[f"value_{i + 1}"] = 11
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        current = int(self._config.get("TDNUM_out") or SENSOR_TYPE_QUAD)
        basic_types = [SENSOR_TYPE_SINGLE, SENSOR_TYPE_DUAL, SENSOR_TYPE_TRIPLE, SENSOR_TYPE_QUAD]
        options = []
        for st in basic_types:
            label = SENSOR_TYPE_LABELS.get(st, f"Type {st}")
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
        options_map = {o["value"]: o["label"] for o in _VALUE_OPTIONS}
        values = {}
        n = SENSOR_TYPE_TO_VALUE_COUNT.get(int(task_config.get("TDNUM_out", SENSOR_TYPE_QUAD)), 4)
        for i in range(n):
            vkey = f"value_{i + 1}"
            vid = task_config.get(vkey, 11)
            if vid != 11:
                label = options_map.get(vid, f"Value {i + 1}")
                values[f"Value {i + 1}"] = label
        return values
