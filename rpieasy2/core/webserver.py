from __future__ import annotations

import datetime
import importlib
import logging
import os
import pkgutil
import time
from typing import Any

import json as _json
import aiohttp_jinja2
import asyncio
import jinja2
import psutil
import shutil
import zipfile
import tempfile
from pathlib import Path
from aiohttp import web

MAX_UPLOAD_SIZE = 2 * 1024 * 1024

def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.system_vars import resolve_system_var, resolve_controller_template
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.logger import get_log_buffer
from rpieasy2.core.notifier_base import NotifierBase
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.p2p_service import get_discovered_nodes
from rpieasy2.core.rpiconst import (
    BUILD,
    DEFAULT_TASK_INTERVAL,
    DEFAULT_WEB_PORT,
    DEVICE_TYPE_CATEGORIES,
    DEVICE_TYPE_SINGLE,
    FILE_CHUNK_SIZE,
    LOG_TTL,
    MB_FACTOR,
    NUM_CONTROLLER_SLOTS,
    NUM_TASK_VALUES,
    RESTART_DELAY,
    RPI_GPIO_COUNT,
    RPI_USABLE_GPIO,
    SECONDS_PER_DAY,
    SECONDS_PER_HOUR,
    SENSOR_TYPE_LABELS as _TDTV_LABELS,
    SENSOR_TYPE_TO_VALUE_COUNT,
    TASKS_PER_PAGE,
    is_raspberry_pi,
    TOTAL_TASK_SLOTS,
    build_to_date_str,
)

_TUOM_OPTIONS = [
    {"label": "None", "options": [{"value": 0, "label": "None"}]},
    {"label": "Temperature", "options": [{"value": 1, "label": "°C"}, {"value": 2, "label": "°F"}, {"value": 3, "label": "K"}]},
    {"label": "Percent", "options": [{"value": 4, "label": "%"}]},
    {"label": "Pressure", "options": [{"value": 5, "label": "Pa"}, {"value": 116, "label": "kPa"}, {"value": 6, "label": "hPa"}, {"value": 7, "label": "bar"}, {"value": 8, "label": "mbar"}, {"value": 114, "label": "cbar"}, {"value": 115, "label": "mmHg"}, {"value": 9, "label": "inHg"}, {"value": 10, "label": "psi"}]},
    {"label": "Electric", "options": [{"value": 13, "label": "V"}, {"value": 119, "label": "mV"}, {"value": 120, "label": "µV"}, {"value": 121, "label": "kV"}, {"value": 16, "label": "A"}, {"value": 117, "label": "mA"}, {"value": 118, "label": "µA"}, {"value": 52, "label": "Hz"}, {"value": 131, "label": "kHz"}, {"value": 132, "label": "MHz"}, {"value": 53, "label": "GHz"}, {"value": 168, "label": "Ah"}]},
    {"label": "Power", "options": [{"value": 11, "label": "W"}, {"value": 12, "label": "kW"}, {"value": 108, "label": "mW"}, {"value": 109, "label": "MW"}, {"value": 17, "label": "VA"}, {"value": 145, "label": "var"}, {"value": 146, "label": "kvar"}]},
    {"label": "Energy", "options": [{"value": 14, "label": "Wh"}, {"value": 15, "label": "kWh"}, {"value": 133, "label": "mWh"}, {"value": 134, "label": "MWh"}, {"value": 141, "label": "J"}, {"value": 142, "label": "kJ"}, {"value": 143, "label": "MJ"}, {"value": 137, "label": "cal"}, {"value": 138, "label": "kcal"}, {"value": 147, "label": "varh"}, {"value": 148, "label": "kvarh"}]},
    {"label": "Energy dist.", "options": [{"value": 162, "label": "kWh/100km"}, {"value": 163, "label": "Wh/km"}, {"value": 164, "label": "mi/kWh"}, {"value": 165, "label": "km/kWh"}]},
    {"label": "Distance", "options": [{"value": 18, "label": "mm"}, {"value": 19, "label": "cm"}, {"value": 20, "label": "m"}, {"value": 21, "label": "km"}, {"value": 48, "label": "in"}, {"value": 49, "label": "ft"}, {"value": 50, "label": "yd"}, {"value": 51, "label": "mi"}]},
    {"label": "Mass", "options": [{"value": 57, "label": "g"}, {"value": 58, "label": "kg"}, {"value": 59, "label": "mg"}, {"value": 60, "label": "µg"}, {"value": 61, "label": "oz"}, {"value": 62, "label": "lb"}, {"value": 149, "label": "st"}]},
    {"label": "Speed", "options": [{"value": 68, "label": "m/s"}, {"value": 70, "label": "km/h"}, {"value": 71, "label": "mph"}, {"value": 107, "label": "kn"}, {"value": 66, "label": "mm/s"}, {"value": 67, "label": "in/s"}, {"value": 106, "label": "ft/s"}, {"value": 65, "label": "mm/h"}, {"value": 69, "label": "in/h"}, {"value": 167, "label": "mm/d"}, {"value": 166, "label": "in/d"}]},
    {"label": "Duration", "options": [{"value": 39, "label": "μs"}, {"value": 40, "label": "ms"}, {"value": 41, "label": "s"}, {"value": 42, "label": "min"}, {"value": 43, "label": "h"}, {"value": 44, "label": "d"}, {"value": 45, "label": "w"}, {"value": 46, "label": "month"}, {"value": 47, "label": "y"}]},
    {"label": "Volume", "options": [{"value": 22, "label": "L"}, {"value": 23, "label": "mL"}, {"value": 24, "label": "m³"}, {"value": 25, "label": "ft³"}, {"value": 54, "label": "gal"}, {"value": 55, "label": "fl. oz"}]},
    {"label": "Flow rate", "options": [{"value": 26, "label": "m³/h"}, {"value": 27, "label": "ft³/h"}, {"value": 154, "label": "m³/s"}, {"value": 158, "label": "L/s"}, {"value": 157, "label": "L/min"}, {"value": 156, "label": "L/h"}, {"value": 160, "label": "mL/s"}, {"value": 155, "label": "ft³/min"}, {"value": 159, "label": "gal/min"}]},
    {"label": "Light", "options": [{"value": 28, "label": "lx"}, {"value": 64, "label": "W/m²"}, {"value": 112, "label": "BTU/(h⋅ft²)"}]},
    {"label": "Radiation", "options": [{"value": 152, "label": "μSv"}, {"value": 153, "label": "μSv/h"}]},
    {"label": "Concentration", "options": [{"value": 33, "label": "ppm"}, {"value": 34, "label": "ppb"}, {"value": 30, "label": "µg/m³"}, {"value": 31, "label": "mg/m³"}, {"value": 32, "label": "p/m³"}, {"value": 161, "label": "g/m³"}]},
    {"label": "Data size", "options": [{"value": 78, "label": "B"}, {"value": 79, "label": "kB"}, {"value": 80, "label": "MB"}, {"value": 81, "label": "GB"}, {"value": 82, "label": "TB"}, {"value": 74, "label": "bit"}, {"value": 75, "label": "kbit"}, {"value": 76, "label": "Mbit"}, {"value": 87, "label": "KiB"}, {"value": 88, "label": "MiB"}, {"value": 89, "label": "GiB"}]},
    {"label": "Data rate", "options": [{"value": 95, "label": "bit/s"}, {"value": 96, "label": "kbit/s"}, {"value": 97, "label": "Mbit/s"}, {"value": 99, "label": "B/s"}, {"value": 100, "label": "kB/s"}, {"value": 101, "label": "MB/s"}, {"value": 103, "label": "KiB/s"}, {"value": 104, "label": "MiB/s"}]},
    {"label": "Sound", "options": [{"value": 72, "label": "dB"}, {"value": 73, "label": "dBm"}]},
    {"label": "Area", "options": [{"value": 56, "label": "m²"}, {"value": 122, "label": "cm²"}, {"value": 124, "label": "mm²"}, {"value": 123, "label": "km²"}, {"value": 125, "label": "in²"}, {"value": 126, "label": "ft²"}, {"value": 127, "label": "yd²"}, {"value": 128, "label": "mi²"}, {"value": 129, "label": "ac"}, {"value": 130, "label": "ha"}]},
    {"label": "Angle", "options": [{"value": 35, "label": "°"}]},
    {"label": "Other", "options": [{"value": 29, "label": "UV index"}, {"value": 63, "label": "µS/cm"}, {"value": 113, "label": "pH"}, {"value": 150, "label": "mg/dL"}, {"value": 151, "label": "mmol/L"}, {"value": 36, "label": "€"}, {"value": 37, "label": "$"}, {"value": 38, "label": "¢"}]},
]

_TDTV_OPTIONS = [
    {"label": "Basic", "options": [
        {"value": 0, "label": "None"},
        {"value": 1, "label": "Single"},
        {"value": 5, "label": "Dual"},
        {"value": 6, "label": "Triple"},
        {"value": 7, "label": "Quad"},
        {"value": 2, "label": "Temp / Hum"},
        {"value": 3, "label": "Temp / Baro"},
        {"value": 8, "label": "Temp / - / Baro"},
        {"value": 4, "label": "Temp / Hum / Baro"},
        {"value": 10, "label": "Switch"},
        {"value": 11, "label": "Dimmer"},
        {"value": 20, "label": "UInt32 (1x)"},
        {"value": 21, "label": "Wind"},
        {"value": 22, "label": "String"},
    ]},
    {"label": "Extended", "options": [
        {"value": 31, "label": "UInt32 (2x)"},
        {"value": 32, "label": "UInt32 (3x)"},
        {"value": 33, "label": "UInt32 (4x)"},
        {"value": 40, "label": "Int32 (1x)"},
        {"value": 41, "label": "Int32 (2x)"},
        {"value": 42, "label": "Int32 (3x)"},
        {"value": 43, "label": "Int32 (4x)"},
        {"value": 50, "label": "UInt64 (1x)"},
        {"value": 51, "label": "UInt64 (2x)"},
        {"value": 60, "label": "Int64 (1x)"},
        {"value": 61, "label": "Int64 (2x)"},
        {"value": 70, "label": "Double (1x)"},
        {"value": 71, "label": "Double (2x)"},
    ]},
    {"label": "Sensor", "options": [
        {"value": 100, "label": "Analog"},
        {"value": 101, "label": "Temp"},
        {"value": 102, "label": "Hum"},
        {"value": 103, "label": "Lux"},
        {"value": 104, "label": "Distance"},
        {"value": 105, "label": "Direction"},
        {"value": 106, "label": "Dust PM2.5"},
        {"value": 107, "label": "Dust PM1.0"},
        {"value": 108, "label": "Dust PM10"},
        {"value": 109, "label": "Moisture"},
        {"value": 110, "label": "(e)CO2"},
        {"value": 111, "label": "GPS"},
        {"value": 112, "label": "UV"},
        {"value": 113, "label": "UV Index"},
        {"value": 114, "label": "IR"},
        {"value": 115, "label": "Weight"},
        {"value": 116, "label": "Voltage"},
        {"value": 117, "label": "Current"},
        {"value": 118, "label": "Power Usage"},
        {"value": 119, "label": "Power Factor"},
        {"value": 120, "label": "Apparent Power"},
        {"value": 121, "label": "TVOC"},
        {"value": 122, "label": "Baro"},
        {"value": 123, "label": "Red"},
        {"value": 124, "label": "Green"},
        {"value": 125, "label": "Blue"},
        {"value": 126, "label": "Color temperature"},
        {"value": 127, "label": "Reactive Power"},
        {"value": 128, "label": "AQI"},
        {"value": 129, "label": "NOx"},
        {"value": 130, "label": "Switch (inv.)"},
        {"value": 131, "label": "Wind speed"},
        {"value": 132, "label": "Duration"},
        {"value": 133, "label": "Date"},
        {"value": 134, "label": "Timestamp"},
        {"value": 135, "label": "Data rate"},
        {"value": 136, "label": "Data size"},
        {"value": 137, "label": "Sound pressure"},
        {"value": 138, "label": "Signal strength"},
        {"value": 139, "label": "Reactive Energy"},
        {"value": 140, "label": "Frequency"},
        {"value": 141, "label": "Energy"},
        {"value": 142, "label": "Energy storage"},
        {"value": 143, "label": "Absolute humidity"},
        {"value": 144, "label": "Atmospheric pressure"},
        {"value": 145, "label": "Blood glucose conc."},
        {"value": 146, "label": "CO"},
        {"value": 147, "label": "Energy distance"},
        {"value": 148, "label": "Gas"},
        {"value": 149, "label": "N2O"},
        {"value": 150, "label": "Ozone"},
        {"value": 151, "label": "Precipitation"},
        {"value": 152, "label": "Precipitation inten."},
        {"value": 153, "label": "SO2"},
        {"value": 154, "label": "VOC parts"},
        {"value": 155, "label": "Volume"},
        {"value": 156, "label": "Volume flow rate"},
        {"value": 157, "label": "Volume storage"},
        {"value": 158, "label": "Water cons."},
        {"value": 255, "label": "Not set"},
    ]},
]

_TDSC_OPTIONS = [
    {"value": 0, "label": ""},
    {"value": 1, "label": "Measurement"},
    {"value": 2, "label": "Measurement-angle"},
    {"value": 3, "label": "Total"},
    {"value": 4, "label": "Total-increasing"},
]



logger = logging.getLogger("rpieasy2.webserver")

_plugin_info: dict[int, dict[str, Any]] = {}
_controller_info: dict[int, dict[str, Any]] = {}
_notifier_info: dict[int, dict[str, Any]] = {}
_routes = web.RouteTableDef()

# In-memory runtime state for controllers (do not persist to config)
_controller_runtime_states: dict[int, dict[str, Any]] = {}


def get_controller_runtime_state(idx: int) -> dict[str, Any]:
    """Return runtime state for a controller slot index. Uses index as configured (0-based)."""
    return _controller_runtime_states.get(idx, {"connected": False, "last_error": ""})


def set_controller_runtime_state(idx: int, state: dict[str, Any]) -> None:
    """Set/merge runtime state for a controller slot index. Caller should avoid blocking ops."""
    cur = _controller_runtime_states.get(idx, {})
    cur.update(state)
    _controller_runtime_states[idx] = cur


async def _require_admin(request: web.Request) -> None:
    """Raise web.HTTPUnauthorized if admin password is configured and not provided or incorrect.

    Accepts password from JSON body field 'admin_password', form field 'admin_password', or header 'X-Admin-Password'.
    If no admin password is configured, allow by returning None.
    """
    cfg = get_config()
    admin_pwd = cfg.data.get("system", {}).get("admin_password", "")
    if not admin_pwd:
        return None

    # try header first
    header = request.headers.get("X-Admin-Password")
    if header:
        if header == admin_pwd:
            return None
        raise web.HTTPUnauthorized(text="Invalid admin password")

    # form or json
    content_type = request.content_type or ""
    try:
        if content_type.startswith("application/json"):
            body = await request.json()
            pwd = body.get("admin_password") if isinstance(body, dict) else None
        else:
            data = await request.post()
            pwd = data.get("admin_password")
    except Exception:
        pwd = None

    if pwd == admin_pwd:
        return None
    raise web.HTTPUnauthorized(text="Admin password required")

_start_time: float = time.time()
_sysinfo: dict[str, Any] = {}
_sysinfo_last_refresh: float = 0.0
_last_task_values: dict[int, dict[str, Any]] = {}


def _get_sysinfo() -> dict[str, Any]:
    return _sysinfo


async def _refresh_sysinfo_loop() -> None:
    while True:
        try:
            global _sysinfo
            _sysinfo = await asyncio.to_thread(_build_sysinfo)
        except Exception:
            pass
        await asyncio.sleep(30)


def _build_sysinfo() -> dict[str, Any]:
    uptime_sec = time.time() - _start_time
    days = int(uptime_sec // SECONDS_PER_DAY)
    hours = int((uptime_sec % SECONDS_PER_DAY) // SECONDS_PER_HOUR)
    minutes = int((uptime_sec % 3600) // 60)
    if days > 0:
        uptime_str = f"{days}d {hours:02d}h {minutes:02d}m"
    else:
        uptime_str = f"{hours:02d}h{minutes:02d}m"

    load = psutil.getloadavg()
    mem = psutil.virtual_memory()
    free_ram_bytes = mem.available
    total_ram_bytes = mem.total
    used_ram_bytes = mem.total - mem.available

    from rpieasy2.core.util import get_wifi_rssi, get_wifi_ssid
    rssi_val = get_wifi_rssi()
    ssid = get_wifi_ssid()
    rssi_ssid = f" ({ssid})" if ssid else ""
    rssi = f"{rssi_val} [dBm]" if rssi_val is not None else "-"

    from rpieasy2.core.rpiconst import get_local_ip
    ip_addr = get_local_ip()

    import platform
    import socket
    cpu_temp = "-"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            t = f.read().strip()
            cpu_temp = f"{int(t) / 1000:.2f} [°C]"
    except Exception:
        pass
    storage_info = {}
    try:
        du = shutil.disk_usage("/")
        storage_info = {
            "total": du.total, "used": du.used, "free": du.free,
            "percent": f"{du.used / du.total * 100:.1f}%",
            "total_hr": f"{du.total // MB_FACTOR} [MB]",
            "used_hr": f"{du.used // MB_FACTOR} [MB]",
            "free_hr": f"{du.free // MB_FACTOR} [MB]",
        }
    except Exception:
        pass
    import platform
    cpu_model = ""
    cpu_cores = 0
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                elif line.startswith("processor"):
                    cpu_cores += 1
    except OSError:
        pass
    if not cpu_model:
        try:
            import subprocess
            result = subprocess.run(["uname", "-m"], capture_output=True, text=True, timeout=2)
            arch = result.stdout.strip()
            proc = platform.processor()
            if proc and proc != arch:
                cpu_model = f"{proc} ({arch})"
            elif arch:
                cpu_model = arch
            else:
                cpu_model = proc or "unknown"
        except Exception:
            cpu_model = platform.processor() or "unknown"
        try:
            import os as _os
            cpu_cores = len(_os.sched_getaffinity(0))
        except AttributeError:
            cpu_cores = _os.cpu_count() or 0
    if cpu_cores == 0:
        try:
            import os as _os
            cpu_cores = len(_os.sched_getaffinity(0))
        except AttributeError:
            cpu_cores = _os.cpu_count() or 0

    rpi_model = ""
    if is_raspberry_pi():
        try:
            with open("/proc/device-tree/model") as f:
                rpi_model = f.read().strip("\x00").strip()
        except Exception:
            pass
        try:
            if not rpi_model:
                with open("/sys/firmware/devicetree/base/model") as f:
                    rpi_model = f.read().strip("\x00").strip()
        except Exception:
            pass
    board_vendor = ""
    board_name = ""
    try:
        with open("/sys/devices/virtual/dmi/id/board_vendor") as f:
            board_vendor = f.read().strip()
    except Exception:
        pass
    try:
        with open("/sys/devices/virtual/dmi/id/board_name") as f:
            board_name = f.read().strip()
    except Exception:
        pass
    os_name = ""
    os_version = ""
    kernel = platform.release()
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("VERSION_ID="):
                    os_version = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    partitions = []
    try:
        import subprocess
        result = subprocess.run(["df", "-B1", "--exclude-type=tmpfs", "--exclude-type=devtmpfs"],
                                capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 6:
                mount = parts[5]
                total_b = int(parts[1])
                used_b = int(parts[2])
                free_b = int(parts[3])
                if total_b > 0:
                    partitions.append({
                        "mount": mount,
                        "total_hr": f"{total_b // MB_FACTOR} [MB]",
                        "used_hr": f"{used_b // MB_FACTOR} [MB]",
                        "free_hr": f"{free_b // MB_FACTOR} [MB]",
                        "percent": f"{used_b / total_b * 100:.1f}%",
                    })
    except Exception:
        pass
    return {
        "unit": get_config().data.get("system", {}).get("unit", 1),
        "version": build_to_date_str(BUILD),
        "localtime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timesource": "System",
        "uptime": uptime_str,
        "load": f"{load[0]:.2f} [%]",
        "free_ram": f"{int(free_ram_bytes)} [byte]",
        "total_ram": f"{int(total_ram_bytes)} [byte]",
        "used_ram": f"{int(used_ram_bytes)} [byte]",
        "ip": ip_addr,
        "rssi": rssi + rssi_ssid,
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_temp": cpu_temp,
        "storage": storage_info,
        "cpu_model": cpu_model,
        "cpu_cores": cpu_cores,
        "rpi_model": rpi_model,
        "board_vendor": board_vendor,
        "board_name": board_name,
        "os_name": os_name,
        "os_version": os_version,
        "kernel": kernel,
        "partitions": partitions,
    }


def _extract_plugin_ast(filepath: str) -> dict | None:
    """Extract PLUGIN_ID, PLUGIN_NAME, PLUGIN_VALUES via AST (no module import)."""
    import ast
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
        meta: dict[str, Any] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            if isinstance(t, ast.Name) and isinstance(item.value, ast.Constant):
                                if t.id == "PLUGIN_ID":
                                    meta["id"] = item.value.value
                                elif t.id == "PLUGIN_NAME":
                                    meta["name"] = item.value.value
                                elif t.id == "PLUGIN_VALUES":
                                    meta["values"] = item.value.value
        if meta.get("id") and meta.get("name"):
            return meta
    except Exception:
        pass
    return None


def _import_plugin_class(modname: str) -> type | None:
    """Fully import a plugin module and return its PluginBase subclass."""
    fqname = f"rpieasy2.plugins.{modname}"
    logger.debug("Importing plugin module %s", fqname)
    try:
        module = importlib.import_module(fqname)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                logger.debug("Found plugin class %s in %s", attr.__name__, fqname)
                return attr
        logger.warning("No PluginBase subclass found in %s", fqname)
    except Exception as e:
        logger.error("Failed to import plugin %s: %s: %s", fqname, type(e).__name__, e)
    return None


def _lazy_load_plugin(plugin_id: int) -> bool:
    """Fully import a plugin that was previously loaded via AST stub. Returns True if successful."""
    entry = _plugin_info.get(plugin_id)
    if not entry:
        return False
    if "class" in entry and entry["class"] is not None:
        return True
    modname = entry.get("_module")
    if not modname:
        return False
    attr = _import_plugin_class(modname)
    if attr is None:
        return False
    device_props = attr.DEVICE_PROPERTIES.to_dict() if hasattr(attr, "DEVICE_PROPERTIES") else {}
    entry["class"] = attr
    entry["name"] = attr.PLUGIN_NAME
    entry["display_name"] = _get_plugin_display_name(attr)
    entry["num_values"] = attr.PLUGIN_VALUES
    entry["device_properties"] = device_props
    return True


def _get_plugin_cache_path() -> str | None:
    try:
        from rpieasy2.core.config import get_config
        cfg = get_config()
        if cfg.path:
            return str(cfg.path.parent / "rpieasy2_plugin_cache.json")
    except Exception:
        pass
    return None


def _load_plugin_cache() -> dict[str, dict]:
    path = _get_plugin_cache_path()
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                return _json.load(f)
        except Exception:
            pass
    return {}


def _save_plugin_cache(cache: dict[str, dict]) -> None:
    path = _get_plugin_cache_path()
    if path:
        try:
            with open(path, "w") as f:
                _json.dump(cache, f)
        except Exception:
            pass


def auto_discover_plugins(plugins_dir: str) -> dict[int, dict[str, Any]]:
    from rpieasy2.core.config import get_config
    cfg = get_config()
    used_ids: set[int] = set()
    for task in cfg.data.get("tasks", []):
        pid = task.get("plugin_id") or task.get("plugin", 0)
        if pid:
            used_ids.add(int(pid))

    _t_import = 0.0
    _t_ast = 0.0
    _count_used = 0
    _count_unused = 0
    _cache_dirty = False
    cache = _load_plugin_cache()
    info: dict[int, dict[str, Any]] = {}
    for importer, modname, ispkg in pkgutil.iter_modules([plugins_dir]):
        if not modname.startswith("p") or ispkg:
            continue
        pid_str = modname[1:].split("_")[0]
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid in used_ids:
            _t0 = time.time()
            attr = _import_plugin_class(modname)
            _t_import += time.time() - _t0
            if attr is None:
                continue
            _count_used += 1
            device_props = attr.DEVICE_PROPERTIES.to_dict() if hasattr(attr, "DEVICE_PROPERTIES") else {}
            info[pid] = {
                "class": attr, "name": attr.PLUGIN_NAME,
                "display_name": _get_plugin_display_name(attr),
                "num_values": attr.PLUGIN_VALUES,
                "device_properties": device_props,
                "_module": modname,
            }
        else:
            _t0 = time.time()
            finder_path = getattr(importer, "path", None) or getattr(importer, "filename", None) or plugins_dir
            import os as _os
            filepath = _os.path.join(finder_path, modname + ".py")
            cached = cache.get(modname)
            if cached and os.path.getmtime(filepath) == cached.get("mtime", 0):
                meta = cached["meta"]
            else:
                meta = _extract_plugin_ast(filepath)
                if meta:
                    cache[modname] = {"mtime": os.path.getmtime(filepath), "meta": meta}
                    _cache_dirty = True
            _t_ast += time.time() - _t0
            if meta is None:
                continue
            _count_unused += 1
            info[pid] = {
                "class": None, "name": meta["name"],
                "display_name": meta["name"],
                "num_values": meta.get("values", 1),
                "device_properties": {},
                "_module": modname,
            }
    if _cache_dirty:
        _save_plugin_cache(cache)
    _plugin_info.update(info)
    logger.debug("[BOOT] auto_discover_plugins: %d used (import %.3fs), %d unused (AST %.3fs)",
                 _count_used, _t_import, _count_unused, _t_ast)
    return info


def _ctx(page_title: str, active_page: str, **kw) -> dict:
    return {"page_title": page_title, "active_page": active_page,
            "config": get_config().data, "plugins": _plugin_info,
            "controllers": _controller_info, "notifiers": _notifier_info,
            "sysinfo": _get_sysinfo(), "ctrl_runtime": _controller_runtime_states, **kw}


async def _get_plugin_form_fields(plugin_id: int, task_config: dict) -> list[dict]:
    if not _lazy_load_plugin(plugin_id):
        return []
    entry = _plugin_info.get(plugin_id)
    cls = entry["class"]
    try:
        inst = cls()
        inst._config = task_config
        form: list[dict] = []
        event = Event(type="PLUGIN_WEBFORM_LOAD", task_index=-1, data={
            "task_config": task_config, "form": form,
        })
        await inst.on_plugin_webform_load(event)
        return event.data.get("form", [])
    except Exception as e:
        logger.warning(f"Failed to get form fields for plugin {plugin_id}: {e}")
        return []


@_routes.get("/")
async def index(request: web.Request) -> web.Response:
    cfg = get_config()
    unit = cfg.data.get("system", {}).get("unit", 1)
    name = cfg.data.get("system", {}).get("name", "RPIEasy")
    from rpieasy2.core.rpiconst import get_local_ip
    ip_addr = get_local_ip()
    web_port = cfg.data.get("system", {}).get("web_port", DEFAULT_WEB_PORT)
    nodes = [{
        "id": unit,
        "name": name,
        "build": build_to_date_str(BUILD),
        "type": "RPIEasy",
        "ip": ip_addr,
        "web_port": web_port,
        "load": f"{psutil.getloadavg()[0]:.2f}",
        "age": str(int(time.time() - _start_time)),
    }]
    discovered = get_discovered_nodes()
    for uid, nd in discovered.items():
        if uid == unit:
            continue
        last_seen = nd.get("last_seen", time.time())
        nodes.append({
            "id": nd.get("id", uid),
            "name": nd.get("name", ""),
            "build": nd.get("build", ""),
                "type": nd.get("type", "RPIEasy"),
            "ip": nd.get("ip", "-"),
            "web_port": nd.get("web_port", DEFAULT_WEB_PORT),
            "load": f"{nd.get('load', 0.0):.2f}" if isinstance(nd.get("load"), (int, float)) else nd.get("load", ""),
            "age": str(int(time.time() - last_seen)),
        })
    return aiohttp_jinja2.render_template("index.html", request, _ctx("Main", "main", nodes=nodes))


@_routes.get("/config")
async def config_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("config.html", request, _ctx("Config", "config"))


@_routes.get("/controllers")
async def controllers_page(request: web.Request) -> web.Response:
    index = request.query.get("index")
    if index is not None:
        idx = max(0, _safe_int(index, 1) - 1)
        cfg = get_config()
        ctrl_data = cfg.get_controller(idx) or {}
        ctrl_id = ctrl_data.get("id", 0)
        _lazy_load_controller(ctrl_id)
        ctrl_name = _controller_info.get(ctrl_id, {}).get("name", "Unknown")
        ctrl_defaults = _get_controller_defaults(ctrl_id)
        ctrl_flags = _controller_info.get(ctrl_id, {}).get("flags", {})
        client_id_raw = ctrl_data.get("controllerclientid") or ctrl_data.get("client_id") or ctrl_defaults.get("controllerclientid", "")
        client_id_resolved = resolve_controller_template(client_id_raw)
        # include runtime connection state for UI (do not persist)
        runtime_state = get_controller_runtime_state(idx)
        return aiohttp_jinja2.render_template("controller_edit.html", request,
            _ctx("Controller Edit", "controllers", index=idx, ctrl_id=ctrl_id,
                 controller_name=ctrl_name, ctrl_config=ctrl_data,
                 ctrl_defaults=ctrl_defaults, ctrl_flags=ctrl_flags,
                 client_id_resolved=client_id_resolved,
                 ctrl_runtime=runtime_state))
    return aiohttp_jinja2.render_template("controllers.html", request, _ctx("Controllers", "controllers"))


@_routes.post("/controllers")
async def controllers_post(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.post()
    idx = max(0, _safe_int(data.get("index", "1"), 1) - 1)
    ctrl_id = _safe_int(data.get("protocol", 0))
    _lazy_load_controller(ctrl_id)
    ctrl_defaults = _get_controller_defaults(ctrl_id)
    ctrl_data = {
        "id": ctrl_id,
        "protocol": _controller_info.get(ctrl_id, {}).get("name", ""),
        "controllerenabled": "controllerenabled" in data,
        "controllerclientid": data.get("controllerclientid") or ctrl_defaults.get("controllerclientid", ""),
        "locatecontroller": _safe_int(data.get("locatecontroller", 0)),
        "controllerip": data.get("controllerip", ""),
        "controllerport": _safe_int(data.get("controllerport") or ctrl_defaults.get("controllerport", 0)),
        "usetls": _safe_int(data.get("usetls", 0)),
        "minimumsendinterval": _safe_int(data.get("minimumsendinterval", 100)),
        "maxqueuedepth": _safe_int(data.get("maxqueuedepth", 10)),
        "maxretries": _safe_int(data.get("maxretries", 10)),
        "fullqueueaction": _safe_int(data.get("fullqueueaction", 0)),
        "allowexpire": "allowexpire" in data,
        "de-duplicate": "de-duplicate" in data,
        "checkreply": _safe_int(data.get("checkreply", 0)),
        "clienttimeout": _safe_int(data.get("clienttimeout", 100)),
        "useextendedcredentials": "useextendedcredentials" in data,
        "controlleruser": data.get("controlleruser", ""),
        "controllerpassword": data.get("controllerpassword", ""),
        "uniqueclientidonreconnect": "uniqueclientidonreconnect" in data,
        "publishretainflag": "publishretainflag" in data,
        "controllersubscribe": data.get("controllersubscribe") or ctrl_defaults.get("controllersubscribe", ""),
        "controllerpublish": data.get("controllerpublish") or ctrl_defaults.get("controllerpublish", ""),
        "controllerlwttopic": data.get("controllerlwttopic", ""),
        "lwtconnectmessage": data.get("lwtconnectmessage", ""),
        "lwtdisconnectmessage": data.get("lwtdisconnectmessage", ""),
        "sendlwttobroker": "sendlwttobroker" in data,
        "willretain": "willretain" in data,
        "cleansession": "cleansession" in data,
        "keepalivetime": _safe_int(data.get("keepalivetime", 60)),
        "enableautodiscovery": "enableautodiscovery" in data,
        "discoverytriggertopic": data.get("discoverytriggertopic", "homeassistant/status"),
        "autodiscoverytopic": data.get("autodiscoverytopic") or ctrl_defaults.get("autodiscoverytopic", "homeassistant/%devclass%/%unique_id%"),
        "configsuffix": data.get("configsuffix", "/config"),
        "onlinemessage": data.get("onlinemessage", "online"),
        "retaindiscovery": "retaindiscovery" in data,
    }
    if ctrl_id == 11:
        ctrl_data["httpmethod"] = data.get("httpmethod", "GET")
        ctrl_data["httpuri"] = data.get("httpuri", "")
        ctrl_data["httpheader"] = data.get("httpheader", "")
        ctrl_data["httpbody"] = data.get("httpbody", "")
        ctrl_data["sendbinary"] = "sendbinary" in data
    if ctrl_id == 34:
        ctrl_data["c034_dbtype"] = _safe_int(data.get("c034_dbtype", 0))
        ctrl_data["c034_dbname"] = data.get("c034_dbname", "")
    cfg.set_controller(idx, ctrl_data)
    cfg.save()
    return web.HTTPFound("/controllers")


BOOT_CONFIG_CANDIDATES = [
    "/boot/firmware/config.txt",
    "/boot/config.txt",
    "/boot/efi/config.txt",
]

_BOOT_CONFIG_PATH: str | None = None


def _find_boot_config() -> str | None:
    global _BOOT_CONFIG_PATH
    if _BOOT_CONFIG_PATH:
        return _BOOT_CONFIG_PATH
    for p in BOOT_CONFIG_CANDIDATES:
        if os.path.exists(p):
            _BOOT_CONFIG_PATH = p
            return p
    return None


def _read_boot_config() -> dict[str, bool | set[int]]:
    result: dict[str, bool | set[int]] = {"i2c_arm": False, "spi": False, "spi1": False, "uart": False, "pwm": False, "audio": False, "w1_gpio": set()}
    path = _find_boot_config()
    if not path:
        return result
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("dtparam=i2c_arm=on"):
                    result["i2c_arm"] = True
                elif line.startswith("dtparam=spi=on"):
                    result["spi"] = True
                elif line.startswith("dtoverlay=spi1"):
                    result["spi1"] = True
                elif line.startswith("enable_uart=1"):
                    result["uart"] = True
                elif line.startswith("dtparam=audio=on"):
                    result["audio"] = True
                elif line.startswith("dtoverlay=pwm"):
                    result["pwm"] = True
                elif line.startswith("dtoverlay=w1-gpio"):
                    if "gpiopin=" in line:
                        try:
                            pin = int(line.split("gpiopin=")[1].split(",")[0])
                            result["w1_gpio"].add(pin)
                        except (ValueError, IndexError):
                            pass
    except Exception:
        pass
    return result


def _read_boot_gpio_config() -> dict[int, str]:
    result: dict[int, str] = {}
    path = _find_boot_config()
    if not path:
        return result
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("gpio="):
                    parts = line[5:].split("=", 1)
                    if len(parts) == 2:
                        try:
                            gpio_num = int(parts[0])
                            result[gpio_num] = parts[1]
                        except ValueError:
                            pass
                elif line.startswith("dtoverlay=w1-gpio"):
                    if "gpiopin=" in line:
                        try:
                            pin = int(line.split("gpiopin=")[1].split(",")[0])
                            result[pin] = "1WIRE"
                        except (ValueError, IndexError):
                            pass
    except Exception:
        pass
    return result


def _write_boot_config(form_data: dict[str, str], password: str = "", force_disabled_pins: set[int] | None = None) -> str | None:
    import subprocess
    path = _find_boot_config()
    if not path:
        return "No boot config file found"
    changes = {
        "i2c_arm": ("dtparam=i2c_arm=", lambda v: f"dtparam=i2c_arm={'on' if v else 'off'}"),
        "spi": ("dtparam=spi=", lambda v: f"dtparam=spi={'on' if v else 'off'}"),
        "spi1": ("dtoverlay=spi1", lambda v: f"dtoverlay=spi1-1cs" if v else f"#dtoverlay=spi1-1cs (disabled)"),
        "uart": ("enable_uart=", lambda v: f"enable_uart={1 if v else 0}"),
        "pwm": ("dtoverlay=pwm", lambda v: f"dtoverlay=pwm-2chan" if v else f"#dtoverlay=pwm-2chan (disabled)"),
        "audio": ("dtparam=audio=", lambda v: f"dtparam=audio={'on' if v else 'off'}"),
    }
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except Exception as e:
        return f"Failed to read {path}: {e}"
    for key, (prefix, make_line) in changes.items():
        enabled = key in form_data
        new_line = make_line(enabled)
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(prefix):
                lines[i] = new_line
                found = True
                break
        if not found:
            lines.append(new_line)
    force_off = force_disabled_pins or set()
    gpio_keys = {k for k in form_data if k.startswith("gpio_")}
    handled: set[int] = set()
    w1_pins: set[int] = set()
    for k in gpio_keys:
        try:
            gpio_num = int(k.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        handled.add(gpio_num)
        if gpio_num in force_off:
            val = ""
        else:
            val = form_data[k].strip()
        if val == "1WIRE":
            w1_pins.add(gpio_num)
            gpio_prefix = f"gpio={gpio_num}="
            for i, line in enumerate(lines):
                if line.strip().startswith(gpio_prefix):
                    lines[i] = ""
                    break
        else:
            gpio_prefix = f"gpio={gpio_num}="
            for i, line in enumerate(lines):
                if line.strip().startswith(gpio_prefix):
                    lines[i] = ""
                    break
            if val:
                lines.append(f"gpio={gpio_num}={val}")
    for pin in force_off:
        if pin not in handled:
            gpio_prefix = f"gpio={pin}="
            for i, line in enumerate(lines):
                if line.strip().startswith(gpio_prefix):
                    lines[i] = ""
                    break
    lines = [l for l in lines if not l.strip().startswith("dtoverlay=w1-gpio")]
    for pin in sorted(w1_pins):
        lines.append(f"dtoverlay=w1-gpio,gpiopin={pin}")
    new_content = "\n".join(lines) + "\n"
    try:
        with open(path, "w") as f:
            f.write(new_content)
        _BOOT_CONFIG_PATH = path
        return None
    except PermissionError:
        pass
    try:
        if password:
            proc = subprocess.run(
                ["sudo", "-S", "tee", path],
                input=password + "\n" + new_content,
                capture_output=True, text=True, timeout=10,
            )
        else:
            proc = subprocess.run(
                ["sudo", "tee", path],
                input=new_content, capture_output=True, text=True, timeout=10,
            )
        if proc.returncode == 0:
            _BOOT_CONFIG_PATH = path
            return None
        if password:
            return f"Write failed (wrong password?): {proc.stderr.strip()}"
        return "NEED_PASSWORD"
    except FileNotFoundError:
        return "sudo not found"
    except Exception as e:
        return f"Write error: {e}"


@_routes.get("/api/ftdi/devices")
async def api_ftdi_devices(request: web.Request) -> web.Response:
    try:
        from rpieasy2.core.hw.ftdi import list_ftdi_devices
        devices = list_ftdi_devices()
        return web.json_response(devices)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@_routes.post("/api/ftdi/config")
async def api_ftdi_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        cfg = get_config()
        ftdi_devices = cfg.data.setdefault("system", {}).setdefault("ftdi_devices", [])
        dev_id = body.get("device_id", "")
        entry = {
            "url": body.get("url", ""),
            "description": body.get("description", ""),
            "port_width": _safe_int(body.get("port_width", 0)),
            "has_mpsse": bool(body.get("has_mpsse", False)),
            "mpsse_channels": body.get("mpsse_channels", []),
            "gpio_pins": body.get("gpio_pins", {}),
            "i2c_frequency": _safe_int(body.get("i2c_frequency", 100000)),
            "spi_frequency": _safe_int(body.get("spi_frequency", 6000000)),
        }
        if dev_id:
            entry["device_id"] = dev_id
        else:
            for prev in ftdi_devices:
                if prev.get("url") == body.get("url") and prev.get("device_id"):
                    entry["device_id"] = prev["device_id"]
                    dev_id = prev["device_id"]
                    break
        idx = next((i for i, d in enumerate(ftdi_devices) if d.get("device_id") == dev_id), None)
        if idx is not None:
            ftdi_devices[idx] = entry
        else:
            ftdi_devices.append(entry)
        cfg.save()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@_routes.post("/api/ftdi/config/bulk")
async def api_ftdi_config_bulk(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, list):
            return web.json_response({"error": "expected array"}, status=400)
        cfg = get_config()
        existing = cfg.data.get("system", {}).get("ftdi_devices", [])
        cleaned: list[dict] = []
        for item in body:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            entry: dict[str, Any] = {
                "url": item["url"],
                "description": item.get("description", ""),
                "port_width": _safe_int(item.get("port_width", 0)),
                "has_mpsse": bool(item.get("has_mpsse", False)),
                "mpsse_channels": item.get("mpsse_channels", []),
                "gpio_pins": item.get("gpio_pins", {}),
                "i2c_frequency": _safe_int(item.get("i2c_frequency", 100000)),
                "spi_frequency": _safe_int(item.get("spi_frequency", 6000000)),
            }
            dev_id = item.get("device_id", "")
            if dev_id:
                entry["device_id"] = dev_id
            else:
                for prev in existing:
                    if prev.get("url") == item["url"] and prev.get("device_id"):
                        entry["device_id"] = prev["device_id"]
                        break
            cleaned.append(entry)
        cfg.data.setdefault("system", {})["ftdi_devices"] = cleaned
        cfg.save()
        return web.json_response({"status": "ok", "count": len(cleaned)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@_routes.get("/api/ftdi/pin-values")
async def api_ftdi_pin_values(request: web.Request) -> web.Response:
    hw = request.app.get("hw_manager")
    if not hw or not hasattr(hw, "gpio") or not hasattr(hw.gpio, "get_pin_states"):
        return web.json_response({})
    try:
        states = hw.gpio.get_pin_states()
        result: dict[str, int] = {}
        for pin, s in states.items():
            result[str(pin)] = s.get("value", 0)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@_routes.post("/api/ftdi/fix-permissions")
async def api_ftdi_fix_permissions(request: web.Request) -> web.Response:
    import subprocess
    import os
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        body = {}
    password = body.get("password", "")
    rules_file = "/etc/udev/rules.d/99-ftdi.rules"
    rules_content = 'SUBSYSTEM=="usb", ATTR{idVendor}=="0403", MODE="0666"\n'
    is_root = os.geteuid() == 0
    cmds: list[list[str]] = []
    if not os.path.exists(rules_file):
        if is_root:
            cmds.append(["sh", "-c", f"printf '{rules_content}' > {rules_file}"])
        else:
            cmds.append(["sudo", "-S", "sh", "-c", f"printf '{rules_content}' > {rules_file}"])
        cmds.append(["sudo", "udevadm", "control", "--reload-rules"])
    else:
        return web.json_response({"status": "ok", "message": "Rules file already exists."})
    for cmd in cmds:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=subprocess.PIPE if "sudo -S" in " ".join(cmd) else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if password and "sudo -S" in " ".join(cmd):
                stdout, stderr = await proc.communicate(input=(password + "\n").encode())
            else:
                stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_text = stderr.decode().strip() if stderr else f"exit code {proc.returncode}"
                err_lower = err_text.lower()
                if any(kw in err_lower for kw in ["incorrect password", "try again", "password is required", "no password", "a password is required"]):
                    return web.json_response({"error": "NEED_PASSWORD", "message": "Sudo password is required or incorrect."}, status=400)
                return web.json_response({"error": err_text}, status=500)
        except FileNotFoundError:
            return web.json_response({"error": "sudo not found"}, status=500)
    return web.json_response({"status": "ok", "message": "FTDI udev rules installed. Reconnect the device."})


@_routes.get("/hardware")
async def hardware_page(request: web.Request) -> web.Response:
    boot_config = _read_boot_config()
    boot_gpio_config = _read_boot_gpio_config()
    is_rpi = is_raspberry_pi()
    drivers = {}
    for drv in (["lgpio", "smbus2", "spidev"] if is_rpi else ["pyftdi"]):
        avail, note = _check_module(drv)
        drivers[drv] = {"available": avail, "note": note}
    special_pins: set[int] = {0, 1}
    if boot_config.get("i2c_arm"):
        special_pins.update({2, 3})
    if boot_config.get("spi"):
        special_pins.update({8, 9, 10, 11})
    if boot_config.get("spi1"):
        special_pins.update({16, 17, 18, 19})
    if boot_config.get("uart"):
        special_pins.update({14, 15})
    if boot_config.get("pwm"):
        special_pins.update({12, 13, 18, 19})
    if boot_config.get("audio"):
        special_pins.update({12, 13})
    cfg = get_config()
    ftdi_devices = cfg.data.get("system", {}).get("ftdi_devices", [])
    return aiohttp_jinja2.render_template("hardware.html", request,
        _ctx("Hardware", "hardware", boot_config=boot_config,
             boot_gpio_config=boot_gpio_config,
             boot_config_path=_BOOT_CONFIG_PATH,
             usable_gpios=sorted(RPI_USABLE_GPIO),
             drivers=drivers, is_rpi=is_rpi,
             special_pins=special_pins,
             ftdi_devices=ftdi_devices))


@_routes.post("/hardware")
async def hardware_post(request: web.Request) -> web.Response:
    data = await request.post()
    password = data.get("password", "")
    special_pins_write: set[int] = {0, 1}
    if "i2c_arm" in data:
        special_pins_write.update({2, 3})
    if "spi" in data:
        special_pins_write.update({8, 9, 10, 11})
    if "spi1" in data:
        special_pins_write.update({16, 17, 18, 19})
    if "uart" in data:
        special_pins_write.update({14, 15})
    if "pwm" in data:
        special_pins_write.update({12, 13, 18, 19})
    if "audio" in data:
        special_pins_write.update({12, 13})
    result = _write_boot_config(data, password, force_disabled_pins=special_pins_write)
    boot_config = _read_boot_config()
    boot_gpio_config = _read_boot_gpio_config()
    is_rpi = is_raspberry_pi()
    drivers = {}
    for drv in (["lgpio", "smbus2", "spidev"] if is_rpi else ["pyftdi"]):
        avail, note = _check_module(drv)
        drivers[drv] = {"available": avail, "note": note}
    special_pins: set[int] = {0, 1}
    if boot_config.get("i2c_arm"):
        special_pins.update({2, 3})
    if boot_config.get("spi"):
        special_pins.update({8, 9, 10, 11})
    if boot_config.get("spi1"):
        special_pins.update({16, 17, 18, 19})
    if boot_config.get("uart"):
        special_pins.update({14, 15})
    if boot_config.get("pwm"):
        special_pins.update({12, 13, 18, 19})
    if boot_config.get("audio"):
        special_pins.update({12, 13})
    cfg = get_config()
    ftdi_devices = cfg.data.get("system", {}).get("ftdi_devices", [])
    ctx = _ctx("Hardware", "hardware", boot_config=boot_config,
               boot_gpio_config=boot_gpio_config,
               boot_config_path=_BOOT_CONFIG_PATH,
               usable_gpios=sorted(RPI_USABLE_GPIO),
               drivers=drivers, is_rpi=is_rpi,
               special_pins=special_pins,
               ftdi_devices=ftdi_devices)
    if result == "NEED_PASSWORD":
        ctx["need_password"] = True
        return aiohttp_jinja2.render_template("hardware.html", request, ctx)
    if result:
        ctx["result"] = result
        return aiohttp_jinja2.render_template("hardware.html", request, ctx)
    ctx["result"] = "Settings saved. Please reboot from the Tools menu for changes to take effect."
    return aiohttp_jinja2.render_template("hardware.html", request, ctx)


@_routes.get("/devices")
async def devices_page(request: web.Request) -> web.Response:
    index = request.query.get("index")
    if index is not None:
        idx = _safe_int(index)
        cfg = get_config()
        task_data = cfg.get_task(idx) or {}
        if task_data.get("name") and "TDN" not in task_data:
            task_data["TDN"] = task_data.pop("name")
        if "enabled" in task_data and "TDE" not in task_data:
            task_data["TDE"] = task_data.pop("enabled")
        if task_data.get("interval") and "TDT" not in task_data:
            task_data["TDT"] = task_data.pop("interval")
        plugin_id = _safe_int(task_data.get("plugin_id") or task_data.get("plugin", 0))
        plugin_name = _plugin_info.get(plugin_id, {}).get("name", "Unknown")
        form_fields = await _get_plugin_form_fields(plugin_id, task_data)
        _lazy_load_plugin(plugin_id)
        entry = _plugin_info.get(plugin_id)
        default_num = entry["class"].PLUGIN_VALUES if entry and entry["class"] else NUM_TASK_VALUES
        tdnum_out_raw = task_data.get("TDNUM_out")
        if tdnum_out_raw is None:
            num_out = default_num
        else:
            num_out = SENSOR_TYPE_TO_VALUE_COUNT.get(_safe_int(tdnum_out_raw), default_num)
        device_props = entry["class"].DEVICE_PROPERTIES if entry and entry["class"] else DeviceProperties()

        show_config_html = ""
        show_values_html = ""
        if plugin_id > 0 and entry and entry["class"]:
            try:
                inst = entry["class"]()
                inst._config = task_data
                sc_ev = Event(type="PLUGIN_WEBFORM_SHOW_CONFIG", task_index=-1, data={
                    "task_config": task_data, "html": "",
                })
                await inst.on_plugin_webform_show_config(sc_ev)
                show_config_html = sc_ev.data.get("html", "")
            except Exception:
                pass
            try:
                inst = entry["class"]()
                inst._config = task_data
                sv_ev = Event(type="PLUGIN_WEBFORM_SHOW_VALUES", task_index=-1, data={
                    "task_config": task_data, "html": "",
                })
                await inst.on_plugin_webform_show_values(sv_ev)
                show_values_html = sv_ev.data.get("html", "")
            except Exception:
                pass
        output_selector_html = ""
        if device_props.output_data_type != 0:
            try:
                inst = entry["class"]()
                inst._config = task_data
                sel_ev = Event(type="PLUGIN_WEBFORM_LOAD_OUTPUT_SELECTOR", task_index=-1, data={})
                result = await inst.on_plugin_webform_load_output_selector(sel_ev)
                selector_data = sel_ev.data.get("output_selector")
                if selector_data:
                    opts_html = ""
                    for opt in selector_data.get("options", []):
                        sel = " selected" if opt["value"] == selector_data["value"] else ""
                        opts_html += f'<option value="{opt["value"]}"{sel}>{opt["label"]}</option>'
                    output_selector_html = f'<TR><TD>{selector_data["label"]}:</TD><TD><select class="wide" name="{selector_data["name"]}" onchange="num_out_onchange(this)">{opts_html}</select></TD></TR>'
            except Exception:
                pass
        ctrls = cfg.data.get("controllers", [])
        controller_list = []
        for i in range(NUM_CONTROLLER_SLOTS):
            if i < len(ctrls) and ctrls[i].get("id"):
                cid = _safe_int(ctrls[i]["id"])
                _lazy_load_controller(cid)
                controller_list.append({
                    "idx": i,
                    "name": _controller_info.get(cid, {}).get("name", f"Controller {i+1}"),
                })
        page = request.query.get("page", "1")
        gpio_list = _build_gpio_list(cfg)
        return aiohttp_jinja2.render_template("device_edit.html", request,
            _ctx("Device Edit", "devices", index=idx, plugin_id=plugin_id,
                 plugin_name=plugin_name, task_config=task_data,
                 form_fields=form_fields, num_out=num_out,
                 output_selector_html=output_selector_html,
                 device_properties=device_props,
                 show_config_html=show_config_html,
                 show_values_html=show_values_html,
                 value_count_map=SENSOR_TYPE_TO_VALUE_COUNT,
                 tuom_options=_TUOM_OPTIONS, tdtv_options=_TDTV_OPTIONS,
                 tdsc_options=_TDSC_OPTIONS,
                 controller_list=controller_list, page=page,
                 gpio_list=gpio_list))
    page = _safe_int(request.query.get("page", 1), 1)
    total_pages = max(1, (TOTAL_TASK_SLOTS + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start_slot = (page - 1) * TASKS_PER_PAGE
    end_slot = min(start_slot + TASKS_PER_PAGE, TOTAL_TASK_SLOTS)
    page_slots = range(start_slot, end_slot)
    return aiohttp_jinja2.render_template("devices.html", request,
        _ctx("Devices", "devices", page=page, total_pages=total_pages,
             page_slots=page_slots, value_count_map=SENSOR_TYPE_TO_VALUE_COUNT))


@_routes.post("/devices")
async def devices_post(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.post()
    idx = _safe_int(data.get("index", "-1"), -1)
    if idx < 0:
        idx = len(cfg.data.get("tasks", []))
    if "del" in data:
        tasks = cfg.data.get("tasks", [])
        if 0 <= idx < len(tasks):
            tasks[idx] = {}
            cfg.save()
        bus = get_event_bus()
        await bus.publish(Event(type="TASK_CONFIG_CHANGED", task_index=idx, data={"task_config": {}}))
        page = data.get("page", "1")
        return web.HTTPFound(f"/devices?page={page}")
    plugin_id = _safe_int(data.get("TDNUM", 0))
    _lazy_load_plugin(plugin_id)
    entry = _plugin_info.get(plugin_id)
    _default_vtype = entry["class"].DEVICE_PROPERTIES.vtype if entry and entry["class"] else 1
    task_data = {
        "plugin_id": plugin_id,
        "TDN": data.get("TDN", ""),
        "TDE": "TDE" in data,
        "TDT": _safe_int(data.get("TDT", DEFAULT_TASK_INTERVAL), DEFAULT_TASK_INTERVAL),
        "TDNUM_out": _safe_int(data.get("TDNUM_out", _default_vtype), _default_vtype),
        "TVSE": "TVSE" in data,
        "TSDV": "TSDV" in data,
        "TELD": "TELD" in data,
    }
    tdnum_out_val = _safe_int(data.get("TDNUM_out", _default_vtype), _default_vtype)
    default_num = entry["class"].PLUGIN_VALUES if entry and entry["class"] else NUM_TASK_VALUES
    num_out = SENSOR_TYPE_TO_VALUE_COUNT.get(tdnum_out_val, default_num)
    for vi in range(1, num_out + 1):
        for prefix in ("TDVN", "TDF", "TDVD", "TUOM", "TDTV", "TDSC"):
            fn = f"{prefix}{vi}"
            val = data.get(fn)
            if val is not None:
                if prefix in ("TDVD", "TUOM", "TDTV", "TDSC"):
                    try:
                        task_data[fn] = int(val)
                    except ValueError:
                        task_data[fn] = val
                else:
                    task_data[fn] = val
        task_data[f"TDS{vi}"] = f"TDS{vi}" in data
        task_data[f"TDSH{vi}"] = f"TDSH{vi}" in data
        tdsa_val = data.get(f"TDSA{vi}")
        if tdsa_val is not None:
            try:
                task_data[f"TDSA{vi}"] = int(tdsa_val)
            except ValueError:
                task_data[f"TDSA{vi}"] = tdsa_val
    for ci in range(NUM_CONTROLLER_SLOTS):
        task_data[f"TDSD{ci}"] = f"TDSD{ci}" in data
        tdid_val = data.get(f"TDID{ci}")
        if tdid_val is not None:
            try:
                task_data[f"TDID{ci}"] = int(tdid_val)
            except ValueError:
                task_data[f"TDID{ci}"] = tdid_val
    if plugin_id > 0 and entry and entry["class"]:
            form_fields = await _get_plugin_form_fields(plugin_id, {})
            for field in form_fields:
                val = data.get(field["name"])
                if val is not None:
                    if field["type"] == "checkbox":
                        task_data[field["name"]] = field["name"] in data
                    elif field["type"] == "select":
                        try:
                            task_data[field["name"]] = int(val)
                        except ValueError:
                            task_data[field["name"]] = val
                    elif field["type"] == "number":
                        try:
                            task_data[field["name"]] = int(val)
                        except ValueError:
                            task_data[field["name"]] = val
                    else:
                        task_data[field["name"]] = val
            try:
                inst = entry["class"]()
                inst._config = task_data
                form_data = {f["name"]: task_data.get(f["name"]) for f in form_fields}
                save_ev = Event(type="PLUGIN_WEBFORM_SAVE", task_index=-1, data={
                    "task_config": task_data, "form_data": form_data,
                })
                await inst.on_plugin_webform_save(save_ev)
            except Exception as e:
                logger.warning(f"Plugin form processing error: {e}")
            try:
                inst = entry["class"]()
                inst._config = task_data
                inst._task_index = idx
                read_ev = Event(type="PLUGIN_READ", task_index=idx, data={"task_config": task_data})
                result = await inst.on_plugin_read(read_ev)
                if result and read_ev.data.get("values"):
                    from rpieasy2.core.formula import evaluate as eval_formula
                    device_props = entry["class"].DEVICE_PROPERTIES
                    if device_props and device_props.formula_option:
                        vals = read_ev.data["values"]
                        for vk, rv in list(vals.items()):
                            try:
                                vi = int(vk)
                            except (ValueError, TypeError):
                                continue
                            fstr = task_data.get(f"TDF{vi}", "")
                            if fstr:
                                try:
                                    vals[vk] = eval_formula(fstr, float(rv))
                                except Exception:
                                    pass
                        read_ev.data["values"] = vals
                    _last_task_values[idx] = read_ev.data["values"]
            except Exception as e:
                logger.warning(f"PLUGIN_READ after save failed: {e}")
    cfg.set_task(idx, task_data)
    cfg.save()
    bus = get_event_bus()
    await bus.publish(Event(type="TASK_CONFIG_CHANGED", task_index=idx, data={"task_config": task_data}))
    page = data.get("page", "1")
    return web.HTTPFound(f"/devices?page={page}")


@_routes.get("/rules")
async def rules_page(request: web.Request) -> web.Response:
    cfg = get_config()
    rules_list = cfg.get_rules()
    engine = request.app.get("rules_engine")
    vars_snapshot = {}
    str_vars_snapshot = {}
    task_vals = {}
    if engine:
        vars_snapshot = engine.get_vars()
        str_vars_snapshot = engine.get_str_vars()
        task_vals = engine.get_task_values_snapshot()
    cfg_sys = get_config().data.get("system", {})
    return aiohttp_jinja2.render_template("rules.html", request,
        _ctx("Rules", "rules", rules_list=rules_list,
             rules_vars=vars_snapshot, rules_str_vars=str_vars_snapshot,
             rules_task_values=task_vals, task_names=engine._task_names if engine else {},
             rules_enabled=engine.is_enabled() if engine else False,
             disable_auto_completion=cfg_sys.get("disable_rules_auto_completion", False)))


@_routes.post("/rules")
async def rules_post(request: web.Request) -> web.Response:
    cfg = get_config()
    data = await request.post()
    if "rules_json" in data:
        rules_json = data.get("rules_json", "[]")
        import json as _json
        try:
            new_rules = _json.loads(rules_json)
        except Exception:
            new_rules = []
        cfg.set_rules(new_rules)
        engine = request.app.get("rules_engine")
        if engine:
            engine.load_rules(new_rules)
    return web.HTTPFound("/rules")


@_routes.post("/api/rules")
async def api_rules_set(request: web.Request) -> web.Response:
    cfg = get_config()
    body = await request.json()
    if isinstance(body, list):
        cfg.set_rules(body)
        cfg.save()
        engine = request.app.get("rules_engine")
        if engine:
            engine.load_rules(body)
        return web.json_response({"status": "ok"})
    return web.json_response({"status": "error", "message": "expected array"})


@_routes.get("/api/rules")
async def api_rules_get(request: web.Request) -> web.Response:
    cfg = get_config()
    return web.json_response(cfg.get_rules())


@_routes.get("/api/rules/state")
async def api_rules_state(request: web.Request) -> web.Response:
    engine = request.app.get("rules_engine")
    if not engine:
        return web.json_response({"enabled": False})
    return web.json_response({
        "enabled": engine.is_enabled(),
        "vars": engine.get_vars(),
        "str_vars": engine.get_str_vars(),
        "task_values": engine.get_task_values_snapshot(),
        "timers": {str(k): round(v - time.time(), 1) for k, v in engine._timers.items() if v > time.time()},
    })


@_routes.get("/pinstates")
async def pinstates_page(request: web.Request) -> web.Response:
    try:
        cfg = get_config()
        hw = request.app.get("hw_manager")
        used_pins: dict[int, list[dict]] = {}
        for ti, task in enumerate(cfg.data.get("tasks", [])):
            pin = task.get("pin", 0)
            if pin:
                p = int(pin)
                pid = task.get("plugin_id") or task.get("plugin", 0)
                entry = _plugin_info.get(int(pid)) if pid else None
                tname = task.get("TDN", task.get("name", f"Task {ti + 1}"))
                used_pins.setdefault(p, []).append({
                    "task": ti + 1,
                    "name": tname,
                    "plugin": entry["name"] if entry else f"ID {pid}",
                })
        pin_values: dict[int, int | str] = {}
        if hw and hasattr(hw, "gpio") and hasattr(hw.gpio, "get_pin_states"):
            raw = hw.gpio.get_pin_states()
            if raw:
                pin_values = {p: s.get("value", "?") for p, s in raw.items()}
        from rpieasy2.core.hw import has_native_hw
        if has_native_hw():
            boot = _read_boot_config()
            special_gpio: dict[int, str] = {0: "I2C0 SDA", 1: "I2C0 SCL"}
            if boot.get("i2c_arm"):
                special_gpio.update({2: "I2C1 SDA (HAT ID)", 3: "I2C1 SCL (HAT ID)"})
            if boot.get("spi"):
                special_gpio.update({8: "SPI0 CE0", 9: "SPI0 MISO", 10: "SPI0 MOSI", 11: "SPI0 SCLK"})
            if boot.get("spi1"):
                special_gpio.update({16: "SPI1 CE0", 17: "SPI1 MISO", 18: "SPI1 MOSI", 19: "SPI1 SCLK"})
            if boot.get("uart"):
                special_gpio.update({14: "UART TX", 15: "UART RX"})
            gpio_states = []
            for pin in range(RPI_GPIO_COUNT):
                available = pin in RPI_USABLE_GPIO
                users = used_pins.get(pin, [])
                val = pin_values.get(pin, "?")
                func = special_gpio.get(pin, "")
                gpio_states.append({
                    "pin": pin, "available": available,
                    "in_use": bool(users), "value": val, "function": func,
                    "users": users,
                })
            usable_count = len(RPI_USABLE_GPIO)
            total_count = RPI_GPIO_COUNT
        else:
            ftdi_devices = cfg.data.get("system", {}).get("ftdi_devices", [])
            from rpieasy2.core.hw.ftdi import all_reserved_pins, url_identifier
            gpio_states = []
            if ftdi_devices:
                sorted_devices = sorted(ftdi_devices, key=lambda d: (d.get("device_id") or d.get("url", "") or ""))
                global_pin = 0
                for di, dev in enumerate(sorted_devices):
                    port_width = dev.get("port_width", 16)
                    mpsse = dev.get("mpsse_channels", [])
                    reserved = all_reserved_pins(mpsse, port_width)
                    identifier = dev.get("device_id") or (url_identifier(dev.get("url", "")) if dev.get("url") else f"FTDI #{di}")
                    for local_pin in range(port_width):
                        available = local_pin not in reserved
                        users = used_pins.get(global_pin, [])
                        val = pin_values.get(global_pin, "?")
                        func = ""
                        if local_pin in reserved:
                            base = 0
                            for ci, mode in enumerate(mpsse):
                                ch_pins = 3 if mode == "i2c" else (4 if mode == "spi" else port_width)
                                if base <= local_pin < base + ch_pins:
                                    if mode == "i2c":
                                        ch_label = ["SCL", "SDA", "SDA"][local_pin - base]
                                        func = f"{identifier} I2C#{ci} {ch_label}"
                                    elif mode == "spi":
                                        ch_label = ["SCLK", "MOSI", "MISO", "CS"][local_pin - base]
                                        func = f"{identifier} SPI#{ci} {ch_label}"
                                    break
                                base += ch_pins
                        gpio_states.append({
                            "pin": global_pin, "available": available,
                            "in_use": bool(users), "value": val,
                            "function": func, "users": users,
                        })
                        global_pin += 1
            else:
                for pin in range(16):
                    users = used_pins.get(pin, [])
                    val = pin_values.get(pin, "?")
                    gpio_states.append({
                        "pin": pin, "available": True,
                        "in_use": bool(users), "value": val,
                        "function": "", "users": users,
                    })
            usable_count = len([s for s in gpio_states if s["available"]])
            total_count = len(gpio_states)
        return aiohttp_jinja2.render_template("pinstates.html", request,
            _ctx("Pin States", "tools", gpio_states=gpio_states,
                 usable_count=usable_count, total_count=total_count))
    except Exception as e:
        logger.error(f"Pinstates error: {e}")
        return web.HTTPFound("/tools")


@_routes.get("/control")
async def control_cmd(request: web.Request) -> web.Response:
    cmd = request.query.get("cmd", "")
    if not cmd:
        return web.Response(text="No command specified", status=400)
    engine = request.app.get("rules_engine")
    if not engine or not engine.is_enabled():
        return web.Response(text="Rules engine is disabled or not available", status=503)
    from rpieasy2.core.rules_engine import RuleCommand
    rc = RuleCommand(cmd)
    if not rc.name:
        return web.Response(text=f"Unable to parse command: {cmd}", status=400)
    try:
        ctx = {
            "task_values": {},
            "vars": engine._vars if hasattr(engine, "_vars") else {},
            "str_vars": engine._str_vars if hasattr(engine, "_str_vars") else {},
        }
        await engine._execute_command(rc, ctx)
        return web.Response(text=f"OK\n{cmd}\n", content_type="text/plain")
    except Exception as e:
        logger.warning(f"Control cmd failed: {e}")
        return web.Response(text=f"Error: {e}\n", status=500, content_type="text/plain")


@_routes.get("/sysinfo")
async def sysinfo_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("sysinfo.html", request, _ctx("Sysinfo", "sysinfo"))


@_routes.get("/advanced")
async def advanced_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("advanced.html", request,
        _ctx("Advanced", "tools"))


@_routes.get("/tools")
async def tools_page(request: web.Request) -> web.Response:
    cmd = request.query.get("cmd", "")
    cmd_input = request.query.get("command", "")
    cmd_result = ""
    if cmd == "i2cscan":
        logger.info("I2C scan started (async)...")
        try:
            from rpieasy2.core.hw import has_native_hw, is_stub_i2c
            if has_native_hw():
                from rpieasy2.core.hw import scan_i2c_bus
                found = await scan_i2c_bus()
            else:
                hw = request.app.get("hw_manager")
                if hw and hasattr(hw, "i2c") and not is_stub_i2c(hw.i2c):
                    found = await hw.i2c.scan()
                else:
                    cmd_result = "I2C scan failed: no I2C backend configured (set MPSSE channel to I2C on Hardware page)"
                    found = None
            if found is not None:
                logger.info(f"I2C scan finished, found {len(found)} device(s)")
                if found:
                    i2c_map, _ = await _build_i2c_plugin_map()
                    hex_found = [hex(a) for a in found]
                    lines = ["     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f"]
                    for row in range(0, 8):
                        base = row * 16
                        line = f"{base:02x}: "
                        for col in range(16):
                            addr = base + col
                            if addr < 0x03:
                                line += "   "
                            elif addr in found:
                                line += f"{addr:02x} "
                            else:
                                line += "-- "
                        lines.append(line)
                    cmd_result = "I2C scan complete. Found devices at:\n" + "\n".join(lines)
                    for addr in sorted(found):
                        names = i2c_map.get(addr, [])
                        if names:
                            cmd_result += f"\n  0x{addr:02x}: {', '.join(names)}"
                else:
                    cmd_result = "I2C scan complete. No devices found."
        except PermissionError:
            cmd_result = "I2C scan failed: No permission to access I2C bus (add user to i2c group)"
        except FileNotFoundError:
            cmd_result = "I2C scan failed: I2C is disabled (enable with raspi-config or dtparam=i2c_arm=on)"
        except ImportError:
            cmd_result = "I2C scan failed: smbus2 not installed (pip install smbus2)"
        except Exception as e:
            cmd_result = f"I2C scan error: {e}"
    elif cmd == "pinstates":
        return web.HTTPFound("/pinstates")
    elif cmd == "backup":
        import io
        import datetime
        base_dir = Path(__file__).resolve().parent.parent.parent
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            cfg = get_config()
            zf.writestr("rpieasy2.json", _json.dumps(cfg.data, indent=2))
            for p in [base_dir / "config" / "rpieasy2.json", base_dir / "rpieasy2.json"]:
                if p.exists() and p.resolve() != cfg.path.resolve():
                    zf.write(str(p), p.name)
        buf.seek(0)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return web.Response(body=buf.getvalue(), content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="rpieasy2_backup_{ts}.zip"'})
    elif cmd == "restart":
        api_host = request.host
        pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_dir = os.path.dirname(pkg_dir)
        main_py = os.path.join(base_dir, "main.py")
        import sys as _sys
        _sys.stdout.flush()
        _sys.stderr.flush()
        os.execv(_sys.executable, [_sys.executable, main_py])
        cmd_result = "Service restart initiated..."
    elif cmd == "reboot":
        _do_reboot()
        cmd_result = "Reboot initiated..."
    elif cmd == "shutdown":
        _do_shutdown()
        cmd_result = "Shutdown initiated..."
    elif cmd_input and not cmd:
        engine = request.app.get("rules_engine")
        if engine and engine.is_enabled():
            try:
                from rpieasy2.core.rules_engine import RuleCommand
                rc = RuleCommand(cmd_input)
                if rc.name:
                    await engine._execute_command(rc, {
                        "task_values": {}, "vars": engine._vars, "str_vars": engine._str_vars,
                    })
                    cmd_result = f"Command executed: {cmd_input}"
                else:
                    cmd_result = f"Unable to parse command: {cmd_input}"
            except Exception as e:
                cmd_result = f"Command error: {e}"
        else:
            cmd_result = "Rules engine is disabled or not available"
    return aiohttp_jinja2.render_template("tools.html", request,
        _ctx("Tools", "tools", cmd_result=cmd_result, cmd_input=cmd_input))


@_routes.get("/notifications")
async def notifications_page(request: web.Request) -> web.Response:
    index = request.query.get("index")
    if index is not None:
        idx = _safe_int(index)
        cfg = get_config()
        notif_data = cfg.get_notification(idx) or {}
        notif_id = notif_data.get("id", 0)
        notif_name = _notifier_info.get(notif_id, {}).get("name", "Unknown")
        return aiohttp_jinja2.render_template("notification_edit.html", request,
            _ctx("Notification Edit", "notifications", index=idx, notif_id=notif_id,
                 notifier_name=notif_name, notif_config=notif_data))
    return aiohttp_jinja2.render_template("notifications.html", request, _ctx("Notifications", "notifications"))


@_routes.post("/notifications")
async def notifications_post(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.post()
    idx = _safe_int(data.get("index", "-1"), -1)
    if idx < 0:
        idx = len(cfg.data.get("notifications", []))
    notif_id = _safe_int(data.get("notification", 0))
    notif_data = {
        "id": notif_id,
        "name": data.get("name", ""),
        "enabled": "enabled" in data,
        "timeout": _safe_int(data.get("timeout", 10), 10) if data.get("timeout") else 10,
        "server": data.get("server", ""),
        "port": _safe_int(data.get("port", 0)) if data.get("port") else 0,
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "sender": data.get("sender", ""),
        "recipients": data.get("recipients", ""),
        "tls": "tls" in data,
        "pin": _safe_int(data.get("pin", 0)) if data.get("pin") else 0,
        "chatid": data.get("chatid", ""),
        "fullurl": data.get("fullurl", ""),
        "body": data.get("body", ""),
    }
    cfg.set_notification(idx, notif_data)
    cfg.save()
    return web.HTTPFound("/notifications")


@_routes.post("/api/notification/{idx}/test")
async def api_notification_test(request: web.Request) -> web.Response:
    idx = _safe_int(request.match_info.get("idx", "-1"), -1)
    cfg = get_config()
    notif_data = cfg.get_notification(idx)
    if not notif_data or not notif_data.get("id"):
        return web.json_response({"status": "error", "message": "Notification not found"})
    bus = get_event_bus()
    ev = Event(type="NOTIFIER_SEND", notifier_index=idx, data={
        "notifier_config": notif_data,
        "message": "Test notification from RPIEasy",
        "subject": "RPIEasy Test",
    })
    await bus.publish(ev)
    return web.json_response({"status": "ok", "message": "Test notification sent"})


@_routes.get("/i2cscanner")
async def i2cscanner_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("i2cscanner.html", request, _ctx("I2C Scanner", "tools"))


@_routes.get("/api/i2cscan/stream")
async def api_i2cscan_stream(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)

    from rpieasy2.core.hw import has_native_hw, is_stub_i2c
    if has_native_hw():
        logger.debug("I2C scan: using native I2C")
        try:
            from rpieasy2.core.hw import create_native_i2c
            i2c = create_native_i2c()
        except ImportError:
            await resp.write(
                f"event: error\ndata: {_json.dumps({'message': 'smbus2 not installed (pip install smbus2)'})}\n\n".encode()
            )
            return resp
        except PermissionError:
            await resp.write(
                f"event: error\ndata: {_json.dumps({'message': 'Permission denied (add user to i2c group)'})}\n\n".encode()
            )
            return resp
        except FileNotFoundError:
            await resp.write(
                f"event: error\ndata: {_json.dumps({'message': 'I2C is disabled (enable with raspi-config)'})}\n\n".encode()
            )
            return resp
        except Exception as e:
            await resp.write(
                f"event: error\ndata: {_json.dumps({'message': str(e)})}\n\n".encode()
            )
            return resp
        need_close = True
    else:
        hw = request.app.get("hw_manager")
        if hw and hasattr(hw, "i2c") and not is_stub_i2c(hw.i2c):
            i2c = hw.i2c
            need_close = False
            logger.debug("I2C scan: using FTDI I2C from hw_manager, i2c=%s (type=%s)", i2c, type(i2c).__name__)
            import rpieasy2.core.hw.ftdi as ftdi_mod
            logger.debug("I2C scan: _inuse_urls=%s, _device_info_cache keys=%s",
                         ftdi_mod._inuse_urls, list(ftdi_mod._device_info_cache.keys()))
            if hasattr(i2c, '_i2c'):
                logger.debug("I2C scan: i2c._i2c=%s", i2c._i2c)
                if i2c._i2c:
                    ftdi = getattr(i2c._i2c, '_ftdi', None)
                    if ftdi:
                        try:
                            logger.debug("I2C scan: ftdi ic_name=%s, port_width=%d, connected=%s",
                                         ftdi.ic_name, ftdi.port_width, ftdi.is_connected)
                        except Exception as ex:
                            logger.debug("I2C scan: ftdi attributes error: %s", ex)
        else:
            logger.debug("I2C scan: no valid I2C backend found, hw=%s, has_i2c=%s, not_stub=%s",
                         hw, hasattr(hw, 'i2c') if hw else 'N/A',
                         not is_stub_i2c(hw.i2c) if hw and hasattr(hw, 'i2c') else 'N/A')
            await resp.write(
                f"event: error\ndata: {_json.dumps({'message': 'No I2C backend configured (set MPSSE channel to I2C on Hardware page)'})}\n\n".encode()
            )
            return resp

    i2c_map, i2c_unknown = await _build_i2c_plugin_map()
    if i2c_unknown:
        logger.warning("I2C plugins without determinable addresses: %s", i2c_unknown)

    logger.debug("I2C scan: starting scan loop, need_close=%s", need_close)
    for addr in range(1, 128):
        await resp.write(f"event: scanning\ndata: {_json.dumps({'addr': addr})}\n\n".encode())
        await resp.drain()
        try:
            found = await asyncio.wait_for(i2c.probe(addr), timeout=0.5)
            status = "found" if found else "empty"
            if found:
                logger.debug("I2C scan: found device at 0x%02x", addr)
        except Exception as e:
            logger.debug("I2C scan: probe(0x%02x) raised: %s", addr, e)
            status = "empty"
        payload: dict[str, Any] = {"addr": addr, "status": status}
        if status == "found" and addr in i2c_map:
            payload["plugins"] = i2c_map[addr]
        await resp.write(
            f"event: progress\ndata: {_json.dumps(payload)}\n\n".encode()
        )
        await resp.drain()

    if need_close:
        logger.debug("I2C scan: closing native I2C")
        await i2c.close()
    logger.debug("I2C scan: done")
    await resp.write(b"event: done\ndata: {}\n\n")
    await resp.drain()
    return resp


@_routes.get("/about")
async def about_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("about.html", request, _ctx("About", "about"))


@_routes.get("/api/config")
async def api_config_get(request: web.Request) -> web.Response:
    return web.json_response(get_config().data)


@_routes.post("/api/config")
async def api_config_set(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.json()
    for key, value in data.items():
        if key == "system" and isinstance(value, dict):
            cfg.data["system"].update(value)
        elif key in ("controllers", "notifications", "tasks"):
            cfg.set(key, value)
    cfg.save()
    return web.json_response({"status": "ok"})


@_routes.post("/api/system/restart")
async def api_system_restart(request: web.Request) -> web.Response:
    pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_dir = os.path.dirname(pkg_dir)
    main_py = os.path.join(base_dir, "main.py")
    import sys as _sys
    import subprocess

    _sys.stdout.flush()
    _sys.stderr.flush()

    async def _do_restart():
        await asyncio.sleep(0.1)
        os.execv(_sys.executable, [_sys.executable, main_py])

    asyncio.get_event_loop().create_task(_do_restart())
    return web.json_response({"status": "ok", "message": "Restarting"})


def _do_reboot():
    import subprocess
    subprocess.run(["sudo", "reboot"], check=False)


def _do_shutdown():
    import subprocess
    subprocess.run(["sudo", "poweroff"], check=False)


@_routes.post("/api/system/reboot")
async def api_system_reboot(request: web.Request) -> web.Response:
    await _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _do_reboot()
    return web.json_response({"status": "ok", "message": "Rebooting"})


@_routes.post("/api/system/shutdown")
async def api_system_shutdown(request: web.Request) -> web.Response:
    await _require_admin(request)
    _do_shutdown()
    return web.json_response({"status": "ok", "message": "Shutting down"})


LOG_LEVEL_NAMES = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


@_routes.post("/api/system/loglevel")
async def api_system_loglevel(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        level_name = body.get("level", "").upper()
        if level_name in LOG_LEVEL_NAMES:
            level = LOG_LEVEL_NAMES[level_name]
            logging.getLogger("rpieasy2").setLevel(level)
            logging.getLogger("rpieasy2").debug("Log level set to %s", level_name)
            return web.json_response({"status": "ok", "level": level_name})
        return web.json_response({"status": "error", "message": f"Invalid level: {level_name}"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)})


@_routes.get("/api/task/{task_index}")
async def api_task_get(request: web.Request) -> web.Response:
    task = get_config().get_task(_safe_int(request.match_info.get("task_index", "-1"), -1))
    return web.json_response(task or {})


@_routes.post("/api/task/{task_index}")
async def api_task_set(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.json()
    cfg.set_task(_safe_int(request.match_info.get("task_index", "-1"), -1), data)
    cfg.save()
    return web.json_response({"status": "ok"})


@_routes.get("/api/controller/{idx}")
async def api_controller_get(request: web.Request) -> web.Response:
    ctrl = get_config().get_controller(_safe_int(request.match_info.get("idx", "-1"), -1))
    return web.json_response(ctrl or {})


@_routes.post("/api/controller/{idx}")
async def api_controller_set(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.json()
    cfg.set_controller(_safe_int(request.match_info.get("idx", "-1"), -1), data)
    cfg.save()
    return web.json_response({"status": "ok"})


@_routes.get("/api/notification/{idx}")
async def api_notification_get(request: web.Request) -> web.Response:
    notif = get_config().get_notification(_safe_int(request.match_info.get("idx", "-1"), -1))
    return web.json_response(notif or {})


@_routes.post("/api/notification/{idx}")
async def api_notification_set(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    data = await request.json()
    cfg.set_notification(_safe_int(request.match_info.get("idx", "-1"), -1), data)
    cfg.save()
    return web.json_response({"status": "ok"})


def _get_controller_defaults(ctrl_id: int) -> dict[str, Any]:
    _lazy_load_controller(ctrl_id)
    info = _controller_info.get(ctrl_id, {})
    flags: dict[str, Any] = info.get("flags", {})
    defaults: dict[str, Any] = {
        "controllerport": flags.get("defaultPort", 80),
        "controllerclientid": "%sysname%_%unit%",
    }
    if flags.get("usesTemplate"):
        if ctrl_id == 5:
            defaults["controllersubscribe"] = "%sysname%/#"
            defaults["controllerpublish"] = "%sysname%/%tskname%/%valname%"
            defaults["autodiscoverytopic"] = "homeassistant/%devclass%/%unique_id%"
        elif ctrl_id == 2:
            defaults["controllersubscribe"] = "domoticz/out"
            defaults["controllerpublish"] = "domoticz/in"
    return defaults


def _extract_controller_ast(filepath: str) -> dict | None:
    import ast
    _FLAG_NAMES = {
        "usesMQTT", "usesAccount", "usesPassword", "usesTemplate", "usesID",
        "Custom", "usesHost", "usesPort", "usesQueue", "usesCheckReply",
        "usesTimeout", "usesSampleSets", "usesExtCreds", "needsNetwork",
        "allowsExpire", "allowLocalSystemTime", "mqttAutoDiscover", "defaultPort",
        "CONTROLLER_ID", "CONTROLLER_NAME", "CONTROLLER_HAS_MQTT",
    }
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
        meta: dict[str, Any] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            if isinstance(t, ast.Name) and t.id in _FLAG_NAMES:
                                val = item.value
                                if isinstance(val, ast.Constant):
                                    meta[t.id] = val.value
                                elif isinstance(val, ast.Name) and val.id in ("True", "False"):
                                    meta[t.id] = val.id == "True"
        if meta.get("CONTROLLER_ID") and meta.get("CONTROLLER_NAME"):
            return meta
    except Exception:
        pass
    return None


def _lazy_load_controller(ctrl_id: int) -> bool:
    entry = _controller_info.get(ctrl_id)
    if not entry:
        return False
    if "class" in entry and entry["class"] is not None:
        return True
    modname = entry.get("_module")
    if not modname:
        return False
    try:
        module = importlib.import_module(f"rpieasy2.controllers.{modname}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, ControllerBase) and attr is not ControllerBase:
                _FLAG_NAMES = [
                    "usesMQTT", "usesAccount", "usesPassword", "usesTemplate", "usesID",
                    "Custom", "usesHost", "usesPort", "usesQueue", "usesCheckReply",
                    "usesTimeout", "usesSampleSets", "usesExtCreds", "needsNetwork",
                    "allowsExpire", "allowLocalSystemTime", "mqttAutoDiscover", "defaultPort",
                ]
                entry["class"] = attr
                entry["name"] = attr.CONTROLLER_NAME
                entry["has_mqtt"] = getattr(attr, "CONTROLLER_HAS_MQTT", False)
                entry["flags"] = {name: getattr(attr, name, False) for name in _FLAG_NAMES}
                return True
    except Exception as e:
        logger.warning(f"Failed to lazy load controller {modname}: {e}")
    return False


def auto_discover_controllers(controllers_dir: str) -> dict[int, dict[str, Any]]:
    info: dict[int, dict[str, Any]] = {}
    for importer, modname, ispkg in pkgutil.iter_modules([controllers_dir]):
        if modname.startswith("c") and not ispkg:
            finder_path = getattr(importer, "path", None) or getattr(importer, "filename", None) or controllers_dir
            filepath = os.path.join(finder_path, modname + ".py")
            meta = _extract_controller_ast(filepath)
            cid = None
            cname = None
            if meta:
                cid = meta.get("CONTROLLER_ID")
                cname = meta.get("CONTROLLER_NAME")
            if cid is None:
                try:
                    cid = int(modname[1:].split("_")[0])
                except ValueError:
                    continue
            if cname is None:
                cname = modname
            info[int(cid)] = {
                "class": None,
                "name": cname,
                "has_mqtt": bool(meta.get("CONTROLLER_HAS_MQTT", False)) if meta else False,
                "flags": {},
                "_module": modname,
            }
    _controller_info.update(info)
    return info


def auto_discover_notifiers(notifiers_dir: str) -> dict[int, dict[str, Any]]:
    info: dict[int, dict[str, Any]] = {}
    for importer, modname, ispkg in pkgutil.iter_modules([notifiers_dir]):
        if modname.startswith("n") and not ispkg:
            try:
                module = importlib.import_module(f"rpieasy2.notifiers.{modname}")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, NotifierBase) and attr is not NotifierBase:
                        info[attr.NOTIFIER_ID] = {"class": attr, "name": attr.NOTIFIER_NAME}
                        logger.debug(f"Discovered notifier: {attr.NOTIFIER_NAME} (ID={attr.NOTIFIER_ID})")
            except Exception as e:
                logger.warning(f"Failed to load notifier {modname}: {e}")
    _notifier_info.update(info)
    return info


@_routes.post("/api/controller/{idx}/delete")
async def api_controller_delete(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    idx = _safe_int(request.match_info.get("idx", "-1"), -1)
    ctrls = cfg.data.get("controllers", [])
    if 0 <= idx < len(ctrls):
        ctrls[idx] = {}
        cfg.save()
    return web.HTTPFound("/controllers")


@_routes.post("/api/task/{task_index}/delete")
async def api_task_delete(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    idx = _safe_int(request.match_info.get("task_index", "-1"), -1)
    tasks = cfg.data.get("tasks", [])
    if 0 <= idx < len(tasks):
        tasks[idx] = {}
        cfg.save()
    return web.HTTPFound("/devices")


@_routes.post("/api/notification/{idx}/delete")
async def api_notification_delete(request: web.Request) -> web.Response:
    await _require_admin(request)
    cfg = get_config()
    idx = _safe_int(request.match_info.get("idx", "-1"), -1)
    notifs = cfg.data.get("notifications", [])
    if 0 <= idx < len(notifs):
        notifs[idx] = {}
        cfg.save()
    return web.HTTPFound("/notifications")


@_routes.get("/log")
async def log_page(request: web.Request) -> web.Response:
    return aiohttp_jinja2.render_template("log.html", request, _ctx("Log", "tools"))


@_routes.get("/logjson")
async def log_json(request: web.Request) -> web.Response:
    buf = get_log_buffer()
    if buf is None:
        return web.json_response({"Log": {"nrEntries": 0, "TTL": LOG_TTL, "SettingsWebLogLevel": 0, "Entries": []}})
    return web.json_response(buf.get_log_json())


@_routes.post("/tools")
async def tools_post(request: web.Request) -> web.Response:
    cmd = request.query.get("cmd", "")
    if cmd == "restore":
        await _require_admin(request)
        reader = await request.multipart()
        field = await reader.next()
        if field and field.name == "backup" and field.filename and field.filename.endswith(".zip"):
            tmpdir = tempfile.mkdtemp(prefix="rpieasy_restore_")
            zip_path = os.path.join(tmpdir, field.filename)
            total = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk(FILE_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_SIZE:
                        f.close()
                        os.unlink(zip_path)
                        os.rmdir(tmpdir)
                        return web.HTTPFound("/tools?restore_error=file_too_large")
                    f.write(chunk)
            try:
                import zipfile as zf_mod
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                with zf_mod.ZipFile(zip_path, "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        fname = info.filename
                        if fname.startswith("/") or ".." in fname:
                            continue
                        dest = os.path.normpath(os.path.join(base_dir, fname))
                        if not dest.startswith(base_dir):
                            continue
                        if fname == "rpieasy2.json":
                            cfg = get_config()
                            import json as _json
                            cfg.data = _json.loads(zf.read(info))
                            cfg.save()
                        elif fname.endswith(".py") or fname.endswith(".html") or fname.endswith(".css") or fname.endswith(".js"):
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with zf.open(info) as src:
                                with open(dest, "wb") as dst:
                                    while True:
                                        buf = src.read(FILE_CHUNK_SIZE)
                                        if not buf:
                                            break
                                        dst.write(buf)
                os.unlink(zip_path)
                os.rmdir(tmpdir)
                return web.HTTPFound("/tools?restore_ok=1")
            except Exception as e:
                logger.error(f"Restore failed: {e}")
                return web.HTTPFound(f"/tools?restore_error={e}")
        return web.HTTPFound("/tools?restore_error=invalid_file")
    return web.HTTPFound("/tools")


def _get_plugin_display_name(cls) -> str:
    return getattr(cls, "PLUGIN_NAME", "Unknown")


async def _build_i2c_plugin_map() -> tuple[dict[int, list[str]], list[str]]:
    mapping: dict[int, list[str]] = {}
    unknown: list[str] = []
    _import_logger = logging.getLogger("rpieasy2.webserver")
    _old_level = _import_logger.level
    _import_logger.setLevel(logging.CRITICAL)
    try:
        for pid, info in _plugin_info.items():
            cls = info.get("class")
            if cls is None:
                _lazy_load_plugin(pid)
                cls = info.get("class")
            if cls is None:
                continue
            display_name = info.get("display_name", info.get("name", str(pid)))
            addrs: list[int] = []
            cls_addrs = getattr(cls, "I2C_ADDRESSES", None)
            if cls_addrs is not None:
                addrs.extend(cls_addrs)
            if hasattr(cls, "on_plugin_i2c_get_address"):
                try:
                    inst = cls()
                    event = Event(type="PLUGIN_I2C_GET_ADDRESS", data={})
                    await inst.on_plugin_i2c_get_address(event)
                    if "addresses" in event.data:
                        addrs.extend(int(a) for a in event.data["addresses"])
                    elif "address" in event.data:
                        addrs.append(int(event.data["address"]))
                except Exception:
                    pass
            if hasattr(cls, "on_plugin_webform_load"):
                try:
                    inst = cls()
                    event = Event(type="PLUGIN_WEBFORM_LOAD", data={})
                    await inst.on_plugin_webform_load(event)
                    form = event.data.get("form", [])
                    for field in form:
                        if isinstance(field, dict) and field.get("name") == "address" and field.get("type") == "select":
                            for opt in field.get("options", []):
                                if isinstance(opt, dict):
                                    addrs.append(int(opt["value"]))
                except Exception:
                    pass
            if addrs:
                for addr in set(addrs):
                    mapping.setdefault(addr, []).append(display_name)
            else:
                from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C
                props = info.get("device_properties", {})
                dt = props.get("type", 0) if isinstance(props, dict) else getattr(props, "type", 0)
                if dt == DEVICE_TYPE_I2C:
                    unknown.append(f"{display_name} (p{pid:03d})")
    finally:
        _import_logger.setLevel(_old_level)
    return mapping, unknown


_FTDI_NATIVE_DEPS = {"lgpio", "smbus2", "spidev"}


def _build_plugins_info(ftdi_active: bool = False) -> list[dict]:
    from rpieasy2.core.rpiconst import is_raspberry_pi
    is_rpi = is_raspberry_pi()
    items: list[dict] = []
    for pid, info in sorted(_plugin_info.items()):
        _lazy_load_plugin(pid)
        cls = info.get("class")
        mod_key = f"p{pid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        if ftdi_active or not is_rpi:
            deps = [d for d in deps if d not in _FTDI_NATIVE_DEPS]
        missing, warnings, ok = _check_deps(deps)
        display_name = _get_plugin_display_name(cls) if cls else info["name"]
        items.append({
            "id": pid, "name": info["name"], "display_name": display_name, "type": "plugin",
            "values": getattr(cls, "PLUGIN_VALUES", 0) if cls else 0,
            "deps": ok, "missing": missing, "warnings": warnings,
        })
    # CORE system dependencies
    core_missing, core_warnings, core_ok = _check_deps(_KNOWN_DEPS.get("p000", []))
    items.append({
        "id": 0, "name": "CORE", "display_name": "CORE", "type": "plugin",
        "values": 0, "deps": core_ok, "missing": core_missing, "warnings": core_warnings,
    })
    for cid, info in sorted(_controller_info.items()):
        mod_key = f"c{cid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        if ftdi_active or not is_rpi:
            deps = [d for d in deps if d not in _FTDI_NATIVE_DEPS]
        missing, warnings, ok = _check_deps(deps)
        items.append({
            "id": cid, "name": info["name"], "type": "controller",
            "deps": ok, "missing": missing, "warnings": warnings,
        })
    for nid, info in sorted(_notifier_info.items()):
        mod_key = f"n{nid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        if ftdi_active or not is_rpi:
            deps = [d for d in deps if d not in _FTDI_NATIVE_DEPS]
        missing, warnings, ok = _check_deps(deps)
        items.append({
            "id": nid, "name": info["name"], "type": "notifier",
            "deps": ok, "missing": missing, "warnings": warnings,
        })
    return items


@_routes.get("/pluginlist")
async def pluginlist_page(request: web.Request) -> web.Response:
    hw = request.app.get("hw_manager")
    ftdi_active = False
    if hw and hasattr(hw, "gpio"):
        try:
            from rpieasy2.core.hw.ftdi import FtdiGPIOManager, FtdiMultiGPIOManager
            ftdi_active = isinstance(hw.gpio, (FtdiGPIOManager, FtdiMultiGPIOManager))
        except ImportError:
            pass
    plugins_info = _build_plugins_info(ftdi_active=ftdi_active)
    plugins_by_id = sorted([p for p in plugins_info if p["type"] == "plugin"], key=lambda x: x["id"])
    plugins_by_name = sorted([p for p in plugins_info if p["type"] == "plugin"], key=lambda x: x["name"].lower())
    controllers = sorted([p for p in plugins_info if p["type"] == "controller"], key=lambda x: x["id"])
    notifiers = sorted([p for p in plugins_info if p["type"] == "notifier"], key=lambda x: x["id"])
    return aiohttp_jinja2.render_template("pluginlist.html", request,
        _ctx("Plugin List", "tools", plugins_by_id=plugins_by_id,
             plugins_by_name=plugins_by_name, controllers=controllers,
             notifiers=notifiers))


@_routes.get("/update")
async def update_page(request: web.Request) -> web.Response:
    error = request.query.get("error", "")
    ok = request.query.get("ok", "")
    return aiohttp_jinja2.render_template("update.html", request,
        _ctx("Update Firmware", "tools", error=error, ok=ok))


@_routes.post("/update")
async def update_post(request: web.Request) -> web.Response:
    await _require_admin(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name not in ("firmware", "update"):
        return web.HTTPFound("/update?error=no_file")
    filename = field.filename
    if not filename or not filename.endswith(".zip"):
        return web.HTTPFound("/update?error=not_zip")
    tmpdir = tempfile.mkdtemp(prefix="rpieasy_update_")
    zip_path = os.path.join(tmpdir, filename)
    total = 0
    with open(zip_path, "wb") as f:
        while True:
            chunk = await field.read_chunk(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_SIZE:
                f.close()
                os.unlink(zip_path)
                os.rmdir(tmpdir)
                return web.HTTPFound("/update?error=file_too_large")
            f.write(chunk)
    try:
        pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_dir = os.path.dirname(pkg_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                fname = info.filename
                if fname.startswith("/") or ".." in fname:
                    continue
                if fname.endswith(".json") or fname.endswith(".pyc"):
                    continue
                if "/__pycache__/" in fname or fname.startswith("__pycache__/"):
                    continue
                dest = os.path.normpath(os.path.join(base_dir, fname))
                if not dest.startswith(base_dir):
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src:
                    with open(dest, "wb") as dst:
                        while True:
                            buf = src.read(FILE_CHUNK_SIZE)
                            if not buf:
                                break
                            dst.write(buf)
        os.unlink(zip_path)
        os.rmdir(tmpdir)
        logger.info("Update extracted, restarting in 1s...")

        async def _restart():
            await asyncio.sleep(RESTART_DELAY)
            import subprocess as _sp
            _sp.run(["find", base_dir, "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                    capture_output=True, timeout=30)
            import sys as _sys
            _sys.stdout.flush()
            _sys.stderr.flush()
            main_py = os.path.join(base_dir, "main.py")
            os.execv(_sys.executable, [_sys.executable, main_py])

        asyncio.get_event_loop().create_task(_restart())
        body = "<html><body><h2>Update successful</h2><p>Restarting... Please refresh in a moment.</p><script>setTimeout(function(){window.location.href='/tools';},9000);</script></body></html>"
        return web.Response(content_type="text/html", body=body)
    except Exception as e:
        logger.error(f"Update failed: {e}")
        return web.HTTPFound(f"/update?error={e}")




@_routes.get("/json")
async def json_api(request: web.Request) -> web.Response:
    view = request.query.get("view", "")
    if view == "sensorupdate" or not view:
        cfg = get_config()
        si = _get_sysinfo()
        ip_addr = si.get("ip", "-")
        ip_parts = ip_addr.split(".") if ip_addr != "-" else ["0", "0", "0", "0"]
        sensors = []
        cfg_sys = cfg.data.get("system", {})
        show_uom = cfg_sys.get("show_unit_of_measure", True)
        for i, task in enumerate(cfg.data.get("tasks", [])):
            pid = task.get("plugin_id") or task.get("plugin", 0)
            entry = _plugin_info.get(int(pid)) if pid else None
            task_values = []
            if i in _last_task_values:
                for vi, (vname, vval) in enumerate(_last_task_values[i].items()):
                    uom = task.get(f"TUOM{vi + 1}", "")
                    nr_dec = task.get(f"TDVD{vi + 1}", 255)
                    tdtv = task.get(f"TDTV{vi + 1}", 0)
                    presentation = _TDTV_LABELS.get(tdtv, "None")
                    if uom:
                        show_uom = True
                    task_values.append({
                        "Value": str(vval),
                        "ValueNumber": vi + 1,
                        "Name": vname,
                        "NrDecimals": nr_dec,
                        "UoM": uom,
                        "Presentation": presentation,
                    })
            sensors.append({
                "TaskNumber": i + 1,
                "TaskValues": task_values,
            })
        nodes = []
        from rpieasy2.core.p2p_service import get_discovered_nodes
        for uid, nd in get_discovered_nodes().items():
            nodes.append({
                "nr": nd.get("id", uid),
                "name": nd.get("name", ""),
                "build": nd.get("build", ""),
                "platform": nd.get("type", ""),
                "rssi": nd.get("rssi", 0),
                "load": nd.get("load", 0.0),
                "ip": nd.get("ip", ""),
                "age": nd.get("age", 0),
            })
        nodes.append({
            "nr": cfg.data.get("system", {}).get("unit", 0),
        "name": cfg.data.get("system", {}).get("name", "RPIEasy"),
            "build": build_to_date_str(BUILD),
            "platform": "RPI Easy",
            "rssi": -45,
            "load": float(si.get("load", "0").split()[0]),
            "webport": cfg.data.get("system", {}).get("web_port", DEFAULT_WEB_PORT),
            "ip": ip_addr,
            "age": int(time.time() - _start_time),
        })
        return web.json_response({
            "System": {
                "Load": si.get("load", "0"),
                "Build": BUILD,
                "Binary Filename": "RPIEasy",
                "Unit Name": cfg.data.get("system", {}).get("name", "RPIEasy"),
                "Unit Number": cfg.data.get("system", {}).get("unit", 0),
                "Uptime": si.get("uptime", "0"),
                "Free RAM": si.get("free_ram", "0"),
                "Hostname": si.get("hostname", ""),
                "Local Time": si.get("localtime", ""),
                "IP Address": ip_addr,
                "IP Subnet": "255.255.255.0",
                "Gateway": (ip_parts[0] + "." + ip_parts[1] + "." + ip_parts[2] + ".1") if len(ip_parts) == 4 else "-",
                "RSSI": si.get("rssi", "-"),
                "CPU Load": si.get("load", "0"),
                "CPU Temp": si.get("cpu_temp", "-"),
                "CPU Cores": si.get("cpu_cores", 0),
                "CPU Model": si.get("cpu_model", ""),
                "Python": si.get("python", ""),
                "Platform": si.get("platform", ""),
                "Free RAM Bytes": 0,
                "Total RAM": si.get("total_ram", "0"),
            },
            "WiFi": {
                "SSID": "",
                "RSSI": si.get("rssi", "-"),
                "IP Address": ip_addr,
                "Hostname": si.get("hostname", ""),
                "STA MAC": "",
            },
            "Sensors": sensors,
            "nodes": nodes,
            "TTL": LOG_TTL,
            "ShowUoM": show_uom,
        })
    return web.json_response({"error": "unknown_view"})


@_routes.get("/sysvars")
async def sysvars_page(request: web.Request) -> web.Response:
    cfg = get_config()
    si = _get_sysinfo()
    ip_addr = si.get("ip", "-")
    ip_parts = ip_addr.split(".") if ip_addr != "-" else ["0", "0", "0", "0"]
    uptime_sec = time.time() - _start_time
    vars_data = {
        "System": {
            "%unit%": str(cfg.data.get("system", {}).get("unit", 0)),
            "%unit_0%": f"{cfg.data.get('system', {}).get('unit', 0):03d}",
            "%sysname%": cfg.data.get("system", {}).get("name", "RPIEasy"),
            "%sysload%": si.get("load", "0").split()[0],
            "%uptime%": str(int(uptime_sec)),
            "%uptime_ms%": str(int(uptime_sec * 1000)),
        },
        "Network": {
            "%ip%": ip_addr,
            "%ip4%": ip_parts[3] if len(ip_parts) == 4 else "0",
            "%mac%": resolve_system_var("mac"),
            "%rssi%": si.get("rssi", "-").split()[0] if si.get("rssi", "-") != "-" else "-",
        },
        "Time": {
            "%lcltime%": si.get("localtime", ""),
            "%systime%": time.strftime("%H:%M:%S"),
            "%systm_hm%": time.strftime("%H:%M"),
            "%sysyear%": time.strftime("%Y"),
            "%sysmonth%": time.strftime("%-m"),
            "%sysday%": time.strftime("%-d"),
            "%syshour%": time.strftime("%H"),
            "%sysmin%": time.strftime("%M"),
            "%syssec%": time.strftime("%S"),
            "%unixtime%": str(int(time.time())),
        },
        "ESP Board": {
            "%cpu_model%": si.get("cpu_model", ""),
            "%cpu_cores%": str(si.get("cpu_cores", 0)),
            "%cpu_freq%": resolve_system_var("cpu_freq"),
        },
        "Special": {
            "%pid%": str(os.getpid()),
            "%vcc%": "-",
        },
    }
    return aiohttp_jinja2.render_template("sysvars.html", request,
        _ctx("System Variables", "tools", sysvars=vars_data))


def _check_module(name: str) -> tuple[bool, str]:
    if name == "pillow":
        try:
            from PIL import Image
            Image.new("1", (1, 1))
        except ImportError as e:
            err = str(e)
            if "libtiff" in err or "libopenjp2" in err or "libxcb" in err:
                return True, "Python package ok, but system libs missing (install pillow_extra_deps)"
            return False, ""
        except Exception:
            return False, ""
        return True, ""
    if name == "pillow_extra_deps":
        try:
            from PIL import Image, features
            Image.new("1", (1, 1))
            if not features.check("freetype2"):
                return False, "freetype2 support missing"
            return True, ""
        except ImportError as e:
            err = str(e)
            if "libtiff" in err or "libopenjp2" in err or "libxcb" in err or "freetype" in err:
                return False, ""
            return True, ""
        except Exception:
            return True, ""
    if name == "hidapi":
        try:
            import hid
            hid.device
            return True, ""
        except Exception:
            return False, "hidapi package not found (pip install hidapi)"
    if name == "usbrelay_udev":
        import os
        udev_file = "/etc/udev/rules.d/99-usbrelay.rules"
        if os.path.exists(udev_file):
            with open(udev_file) as f:
                if "16c0" in f.read() and "05df" in f.read():
                    return True, ""
            return False, "udev rule exists but does not match USB Relay (16c0:05df)"
        return False, "udev rule missing (install usbrelay_udev)"
    if name == "temper_udev":
        import os
        udev_file = "/etc/udev/rules.d/99-temper.rules"
        if os.path.exists(udev_file):
            with open(udev_file) as f:
                content = f.read()
                if all(v in content for v in ("0c45", "413d", "1a86", "3553")):
                    return True, ""
            return False, "udev rule exists but missing some TEMPer VID/PID entries"
        return False, "udev rule missing (install temper_udev)"
    _import_name = {"pillow": "PIL"}.get(name, name)
    try:
        import importlib
        importlib.import_module(_import_name)
    except Exception:
        return False, ""
    if name == "lgpio":
        import ctypes.util
        if ctypes.util.find_library("lgpio") is None:
            return True, "Python package ok, but liblgpio.so missing (apt install liblgpio-dev)"
        try:
            import lgpio as lg
            chip = lg.gpiochip_open(0)
            lg.gpiochip_close(chip)
        except PermissionError:
            return True, "Package ok, but no permission to access GPIO (add user to gpio group)"
        except FileNotFoundError:
            return True, "Package ok, but /dev/gpiochip0 not found (not a Raspberry Pi?)"
        except Exception as e:
            return True, f"Package ok, but GPIO init failed: {e}"
        return True, ""
    if name == "smbus2":
        try:
            import smbus2
            smbus2.SMBus(1)
            return True, ""
        except PermissionError:
            return True, "Package OK, but no permission to access I2C bus (add user to i2c group)"
        except FileNotFoundError:
            return True, "Package OK, but I2C is disabled (enable with raspi-config or dtparam=i2c_arm=on)"
        except Exception:
            return False, ""
    if name == "rpi_ws281x":
        try:
            import rpi_ws281x  # noqa: F401
        except Exception:
            return False, ""
        from rpieasy2.core.rpiconst import is_raspberry_pi
        if not is_raspberry_pi():
            return True, "Library only works on Raspberry Pi hardware"
        return True, ""
    return True, ""


def _check_deps(deps: list[str]) -> tuple[list[str], list[str], list[str]]:
    missing = []
    warnings = []
    ok = []
    for d in deps:
        avail, note = _check_module(d)
        if not avail:
            missing.append(d)
        elif note:
            warnings.append(note)
        else:
            ok.append(d)
    return missing, warnings, ok


def _build_gpio_list(cfg) -> list[dict]:
    from rpieasy2.core.hw import has_native_hw
    gpio_list: list[dict] = []
    used_pins: set[int] = set()
    for task in cfg.data.get("tasks", []):
        pin = task.get("pin", 0)
        if pin:
            used_pins.add(int(pin))
    if has_native_hw():
        boot = _read_boot_config()
        boot_gpio = _read_boot_gpio_config()
        special_gpio: dict[int, str] = {
            0: "I2C0 SDA",
            1: "I2C0 SCL",
        }
        if boot.get("i2c_arm"):
            special_gpio.update({2: "I2C1 SDA (HAT ID)", 3: "I2C1 SCL (HAT ID)"})
        if boot.get("spi"):
            special_gpio.update({8: "SPI0 CE0", 9: "SPI0 MISO", 10: "SPI0 MOSI", 11: "SPI0 SCLK"})
        if boot.get("spi1"):
            special_gpio.update({16: "SPI1 CE0", 17: "SPI1 MISO", 18: "SPI1 MOSI", 19: "SPI1 SCLK"})
        if boot.get("uart"):
            special_gpio.update({14: "UART TX", 15: "UART RX"})
        for pin, val in boot_gpio.items():
            label_map = {"op,dl": "Boot Out Low", "op,dh": "Boot Out High",
                         "ip,pu": "Boot In Pull-Up", "ip,pd": "Boot In Pull-Down",
                         "ip,np": "Boot In Float",
                         "1WIRE": "1-Wire (dtoverlay)"}
            special_gpio.setdefault(pin, label_map.get(val, f"Boot {val}"))
        for pin in range(RPI_GPIO_COUNT):
            available = pin in RPI_USABLE_GPIO
            in_use = pin in used_pins
            special = special_gpio.get(pin, "")
            label = f"GPIO {pin}"
            if not available:
                label += " [NOT AVAILABLE]"
            if in_use:
                label += " [IN USE]"
            if special:
                label += f" ({special})"
            gpio_list.append({
                "pin": pin,
                "label": label,
                "available": available,
                "in_use": in_use,
                "special": special,
            })
    else:
        ftdi_devices = cfg.data.get("system", {}).get("ftdi_devices", [])
        from rpieasy2.core.hw.ftdi import all_reserved_pins, url_identifier
        if ftdi_devices:
            sorted_devices = sorted(ftdi_devices, key=lambda d: (d.get("device_id") or d.get("url", "") or ""))
            global_pin = 0
            for di, dev in enumerate(sorted_devices):
                port_width = dev.get("port_width", 16)
                mpsse = dev.get("mpsse_channels", [])
                reserved = all_reserved_pins(mpsse, port_width)
                identifier = dev.get("device_id") or (url_identifier(dev.get("url", "")) if dev.get("url") else f"FTDI #{di}")
                for local_pin in range(port_width):
                    in_use = global_pin in used_pins
                    special = ""
                    if local_pin in reserved:
                        special = "reserved by MPSSE"
                    label = f"GPIO {global_pin} ({identifier})"
                    if in_use:
                        label += " [IN USE]"
                    if special:
                        label += f" ({special})"
                    gpio_list.append({
                        "pin": global_pin,
                        "label": label,
                        "available": local_pin not in reserved,
                        "in_use": in_use,
                        "special": special,
                        "ftdi_device": di,
                        "ftdi_pin": local_pin,
                    })
                    global_pin += 1
        else:
            for pin in range(16):
                in_use = pin in used_pins
                label = f"GPIO {pin} (FTDI)"
                if in_use:
                    label += " [IN USE]"
                gpio_list.append({
                    "pin": pin,
                    "label": label,
                    "available": True,
                    "in_use": in_use,
                    "special": "",
                })
    return gpio_list


_KNOWN_DEPS: dict[str, list[str]] = {
    "p000": ["suntime", "pysolar"],
    "p001": ["lgpio"],
    "p003": ["lgpio"],
    "p004": [],
    "p005": ["lgpio"],
    "p006": ["smbus2"],
    "p007": ["smbus2"],
    "p009": ["smbus2"],
    "p010": ["smbus2"],
    "p011": ["smbus2"],
    "p012": ["smbus2"],
    "p013": ["lgpio"],
    "p014": ["smbus2"],
    "p015": ["smbus2"],
    "p018": ["lgpio"],
    "p019": ["smbus2"],
    "p020": [],
    "p022": ["smbus2"],
    "p023": ["smbus2"],
    "p024": ["smbus2"],
    "p025": ["smbus2"],
    "p026": [],
    "p027": ["smbus2"],
    "p028": ["smbus2"],
    "p029": ["lgpio"],
    "p033": [],
    "p036": ["smbus2", "pillow", "pillow_extra_deps"],
    "p037": ["aiomqtt"],
    "p038": ["rpi_ws281x"],
    "p045": ["smbus2"],
    "p047": ["smbus2"],
    "p049": [],
    "p050": ["smbus2"],
    "p051": ["smbus2"],
    "p053": [],
    "p059": ["lgpio"],
    "p067": ["lgpio"],
    "p068": ["smbus2"],
    "p074": ["smbus2"],
    "p083": ["smbus2"],
    "p087": [],
    "p089": [],
    "p101": [],
    "p102": [],
    "p105": ["smbus2"],
    "p106": ["smbus2"],
    "p110": ["smbus2"],
    "p111": ["spidev"],
    "p113": ["smbus2"],
    "p117": ["smbus2"],
    "p126": ["lgpio"],
    "p129": ["lgpio"],
    "p132": ["smbus2"],
    "p135": ["smbus2"],
    "p147": ["smbus2"],
    "p153": ["smbus2"],
    "p159": [],
    "p512": [],
    "p513": ["lgpio"],
    "p514": [],
    "p515": ["smbus2"],
    "p516": ["smbus2"],
    "p517": ["hidapi", "usbrelay_udev"],
    "p518": ["temper_udev"],
    "c002": ["aiomqtt"],
    "c005": ["aiomqtt"],
    "c006": ["aiomqtt"],
    "c034": ["pymysql"],
    "n001": [],
    "n002": ["lgpio"],
}


@_routes.get("/api/plugins/info")
async def api_plugins_info(request: web.Request) -> web.Response:
    plugins_list = []
    for pid, info in sorted(_plugin_info.items()):
        _lazy_load_plugin(pid)
        cls = info.get("class")
        mod_key = f"p{pid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        missing = [d for d in deps if not _check_module(d)]
        plugins_list.append({
            "id": pid,
            "name": info["name"],
            "type": "plugin",
            "values": getattr(cls, "PLUGIN_VALUES", 0) if cls else 0,
            "dependencies": deps,
            "missing": missing,
        })
    for cid, info in sorted(_controller_info.items()):
        mod_key = f"c{cid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        missing = [d for d in deps if not _check_module(d)]
        plugins_list.append({
            "id": cid,
            "name": info["name"],
            "type": "controller",
            "dependencies": deps,
            "missing": missing,
        })
    for nid, info in sorted(_notifier_info.items()):
        mod_key = f"n{nid:03d}"
        deps = _KNOWN_DEPS.get(mod_key, [])
        missing = [d for d in deps if not _check_module(d)]
        plugins_list.append({
            "id": nid,
            "name": info["name"],
            "type": "notifier",
            "dependencies": deps,
            "missing": missing,
        })
    return web.json_response(plugins_list)


_SYSTEM_APT_DEPS: dict[str, list[str]] = {
    "pillow_extra_deps": ["libtiff6", "libopenjp2-7", "libxcb1", "libfreetype6-dev"],
    "hidapi": ["libhidapi-dev"],
}


@_routes.post("/api/plugins/install")
async def api_plugins_install(request: web.Request) -> web.Response:
    await _require_admin(request)
    body = await request.json()
    pkg = body.get("package", "")
    if not pkg:
        return web.json_response({"status": "error", "message": "no package specified"})

    if pkg in ("pillow_extra_deps", "hidapi"):
        pkgs = _SYSTEM_APT_DEPS.get(pkg, [])
        if not pkgs:
            return web.json_response({"status": "error", "message": "unknown package"})
        cmd = ["sudo", "-n", "apt-get", "install", "-y"] + pkgs
        sudo_password = body.get("sudo_password", "")
        if sudo_password:
            cmd = ["sudo", "-S", "apt-get", "install", "-y"] + pkgs
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if sudo_password else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(input=(sudo_password + "\n").encode() if sudo_password else None), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return web.json_response({"status": "error", "message": "installation timed out"})
            if proc.returncode == 0:
                pip_pkg = "Pillow" if pkg == "pillow_extra_deps" else "hidapi"
                import sys as _sys
                pip_path = _sys.executable.replace("python", "pip")
                pip_proc = await asyncio.create_subprocess_exec(
                    pip_path, "install", "--force-reinstall", pip_pkg,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    pip_stdout, pip_stderr = await asyncio.wait_for(pip_proc.communicate(), timeout=120)
                except asyncio.TimeoutError:
                    pip_proc.kill()
                    await pip_proc.wait()
                    return web.json_response({"status": "ok", "message": "System deps installed, but reinstall timed out. Try: pip install --force-reinstall " + pip_pkg})
                if pip_proc.returncode == 0:
                    return web.json_response({"status": "ok", "message": "System deps + " + pip_pkg + " reinstalled"})
                else:
                    return web.json_response({"status": "ok", "message": "System deps installed. Reinstall: " + pip_stderr.decode().strip()})
            err_msg = stderr.decode().strip()
            if "a password is required" in err_msg or "no password was provided" in err_msg.lower():
                return web.json_response({"status": "error", "message": "sudo password required", "need_sudo": True, "pkgs": pkgs})
            return web.json_response({"status": "error", "message": err_msg or f"apt-get failed (exit code {proc.returncode})"})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)})

    if pkg in ("usbrelay_udev", "temper_udev"):
        if pkg == "usbrelay_udev":
            udev_file = "/etc/udev/rules.d/99-usbrelay.rules"
            rule = 'SUBSYSTEM=="usb", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05df", MODE="0666"\n'
        else:
            udev_file = "/etc/udev/rules.d/99-temper.rules"
            rule = (
                '# TEMPer USB temperature sensors\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="7401", MODE="0666"\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0c45", ATTRS{idProduct}=="7402", MODE="0666"\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="413d", ATTRS{idProduct}=="2107", MODE="0666"\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5523", MODE="0666"\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="e025", MODE="0666"\n'
                'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3553", ATTRS{idProduct}=="a001", MODE="0666"\n'
            )
        import os
        if os.path.exists(udev_file):
            with open(udev_file) as f:
                if rule.strip() in f.read():
                    return web.json_response({"status": "ok", "message": "udev rule already present"})
        cmd = ["sudo", "-n", "tee", udev_file]
        sudo_password = body.get("sudo_password", "")
        if sudo_password:
            cmd = ["sudo", "-S", "tee", udev_file]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=rule.encode()),
                timeout=30,
            )
            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                if "a password is required" in err_msg or "no password was provided" in err_msg.lower():
                    return web.json_response({"status": "error", "message": "sudo password required", "need_sudo": True, "pkgs": [udev_file]})
                return web.json_response({"status": "error", "message": err_msg or f"tee failed (exit {proc.returncode})"})
        except asyncio.TimeoutError:
            return web.json_response({"status": "error", "message": "writing udev rule timed out"})
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)})
        reload_cmd = ["sudo", "-n"] if not sudo_password else ["sudo", "-S"]
        reload_cmd += ["udevadm", "control", "--reload-rules"]
        try:
            await asyncio.create_subprocess_exec(*reload_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except Exception:
            pass
        trigger_cmd = ["sudo", "-n"] if not sudo_password else ["sudo", "-S"]
        trigger_cmd += ["udevadm", "trigger"]
        try:
            await asyncio.create_subprocess_exec(*trigger_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except Exception:
            pass
        return web.json_response({"status": "ok", "message": "udev rule installed, reloaded. Re-plug the USB relay or restart."})

    import sys as _sys
    pip_path = _sys.executable.replace("python", "pip")
    try:
        proc = await asyncio.create_subprocess_exec(
            pip_path, "install", pkg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return web.json_response({"status": "error", "message": "installation timed out"})
        if proc.returncode == 0:
            note = ""
            if pkg == "lgpio":
                import ctypes.util
                if ctypes.util.find_library("lgpio") is None:
                    note = " (Python package installed, but liblgpio.so missing. Run: sudo apt install liblgpio-dev)"
            return web.json_response({"status": "ok", "message": f"{pkg} installed{note}"})
        else:
            return web.json_response({"status": "error", "message": stderr.decode().strip()})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)})


async def _on_plugin_read(event: Event) -> bool | None:
    ti = event.task_index
    values = event.data.get("values", {})
    if ti >= 0 and values:
        _last_task_values[ti] = values
    return True


def subscribe_plugin_read() -> None:
    get_event_bus().subscribe("PLUGIN_READ", _on_plugin_read)


def create_app(plugins_dir: str | None = None, controllers_dir: str | None = None, notifiers_dir: str | None = None) -> web.Application:
    _t0 = time.time()
    app = web.Application()
    app["_start_time"] = _start_time
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(os.path.join(base, "templates")))
    app.add_routes(_routes)
    app.router.add_static("/static", os.path.join(base, "static"))
    logger.debug("[BOOT] create_app: routes+j2 setup done (%.3fs)", time.time() - _t0)
    if plugins_dir:
        _tp = time.time()
        auto_discover_plugins(plugins_dir)
        logger.debug("[BOOT] create_app: auto_discover_plugins done (%.3fs)", time.time() - _tp)
    if controllers_dir:
        _tc = time.time()
        auto_discover_controllers(controllers_dir)
        logger.debug("[BOOT] create_app: auto_discover_controllers done (%.3fs)", time.time() - _tc)
    if notifiers_dir:
        _tn = time.time()
        auto_discover_notifiers(notifiers_dir)
        logger.debug("[BOOT] create_app: auto_discover_notifiers done (%.3fs)", time.time() - _tn)
    asyncio.create_task(_refresh_sysinfo_loop())
    from rpieasy2.core.custompage import handle_custom
    app.router.add_get("/{tail:.*}", handle_custom)
    return app
