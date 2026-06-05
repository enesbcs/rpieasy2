from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Any

from rpieasy2.core.config import get_config
from rpieasy2.core.rpiconst import BUILD, DEFAULT_WEB_PORT
from rpieasy2.core.system_chars import resolve_special_chars

logger = logging.getLogger("rpieasy2.system_vars")

VAR_PATTERN = re.compile(r"%(\w+)%")
TASKVAL_PATTERN = re.compile(r"\[(\w+)#(\w+)\]")

_START_TIME: float = time.time()


def set_start_time(t: float) -> None:
    global _START_TIME
    _START_TIME = t


def resolve_system_var(name: str) -> str:
    n = name.lower()
    cfg = get_config()
    sys_cfg = cfg.data.get("system", {})

    if n == "ip":
        from rpieasy2.core.rpiconst import get_local_ip
        return get_local_ip()
    if n == "unit":
        return str(sys_cfg.get("unit", 1))
    if n == "unit_0":
        return f"{int(sys_cfg.get('unit', 1)):03d}"
    if n == "sysname":
        return sys_cfg.get("name", "RPIEasy2")
    if n == "time":
        return time.strftime("%H:%M")
    if n == "systime":
        return time.strftime("%H:%M:%S")
    if n in ("unix_time", "unixtime"):
        return str(int(time.time()))
    if n == "time_sec":
        return str(int(time.time()) % 86400)
    if n == "uptime":
        uptime_sec = time.time() - _START_TIME
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        mins = int((uptime_sec % 3600) // 60)
        return f"{days}d {hours:02d}h {mins:02d}m"
    if n == "uptime_ms":
        return str(int((time.time() - _START_TIME) * 1000))
    if n == "sysyear":
        return time.strftime("%Y")
    if n == "sysmonth":
        return time.strftime("%-m")
    if n == "sysmonth_0":
        return time.strftime("%m")
    if n == "sysday":
        return time.strftime("%-d")
    if n == "sysday_0":
        return time.strftime("%d")
    if n in ("syshour", "syshour_0"):
        return time.strftime("%H")
    if n in ("sysmin", "sysmin_0"):
        return time.strftime("%M")
    if n in ("syssec", "syssec_0"):
        return time.strftime("%S")
    if n == "lcltime":
        return time.strftime("%Y-%m-%d %H:%M:%S")
    if n == "build":
        return str(BUILD)
    if n in ("web_port", "webport"):
        return str(sys_cfg.get("web_port", DEFAULT_WEB_PORT))
    if n == "vcc":
        return "-"
    if n == "mac":
        try:
            # Iterate through all network interfaces
            for iface_name in os.listdir("/sys/class/net"):
                if iface_name == "lo":  # Skip loopback interface
                    continue
                # Check if the interface is active (operstate is "up")
                operstate_path = f"/sys/class/net/{iface_name}/operstate"
                if os.path.exists(operstate_path):
                    with open(operstate_path) as f:
                        if f.read().strip() != "up":
                            continue

                # Get MAC address
                mac_path = f"/sys/class/net/{iface_name}/address"
                if os.path.exists(mac_path):
                    with open(mac_path) as f:
                        return f.read().strip().upper()
        except Exception:
            pass
        return "-"
    if n == "pid":
        return str(os.getpid())
    if n == "rssi":
        from rpieasy2.core.util import get_wifi_rssi
        rssi = get_wifi_rssi()
        return str(rssi) if rssi is not None else "-"
    if n in ("wi_ch", "wifi_ch", "wifi_channel"):
        from rpieasy2.core.util import get_wifi_channel
        ch = get_wifi_channel()
        return ch if ch else "-"
    if n == "bssid":
        from rpieasy2.core.util import get_wifi_bssid
        bssid = get_wifi_bssid()
        return bssid if bssid else "-"
    if n in ("cpu_load", "sysload"):
        import psutil
        return f"{psutil.getloadavg()[0]:.2f}"
    if n == "cpu_temp":
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                t = f.read().strip()
                return f"{int(t) / 1000:.2f}"
        except Exception:
            return "-"
    if n == "cpu_model":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        import platform
        return platform.processor() or "-"
    if n == "cpu_cores":
        try:
            import psutil
            return str(psutil.cpu_count())
        except Exception:
            pass
        return str(os.cpu_count() or 0)
    if n in ("cpu_freq", "cpu_frequency"):
        try:
            with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") as f:
                khz = int(f.read().strip())
                return f"{khz / 1000:.0f} MHz"
        except Exception:
            pass
        try:
            import psutil
            freq = psutil.cpu_freq()
            if freq:
                return f"{freq.current:.0f} MHz"
        except Exception:
            pass
        return "-"
    return ""


def resolve_taskval(
    text: str,
    task_values: dict[int, dict[str, str]] | None = None,
    task_names: dict[int, str] | None = None,
) -> str:
    if task_values is None:
        task_values = {}
    if task_names is None:
        task_names = {}

    def _replace(m: re.Match) -> str:
        task = m.group(1).lower()
        val = m.group(2).lower()
        for ti, vd in task_values.items():
            tname = task_names.get(ti, "").lower()
            if tname == task:
                for vn, vv in vd.items():
                    if vn.lower() == val:
                        return str(vv)
        return "0"

    return TASKVAL_PATTERN.sub(_replace, text)


def resolve_template(
    text: str,
    task_values: dict[int, dict[str, str]] | None = None,
    task_names: dict[int, str] | None = None,
) -> str:
    def _replace_var(m: re.Match) -> str:
        return resolve_system_var(m.group(1))

    result = VAR_PATTERN.sub(_replace_var, text)
    if task_values is not None or task_names is not None:
        result = resolve_taskval(result, task_values, task_names)
    return resolve_special_chars(result)


LIVE_TASK_VALUES: dict[int, dict[str, str]] = {}
LIVE_TASK_NAMES: dict[int, str] = {}


def resolve_controller_template(
    text: str,
    task_index: int = -1,
    task_config: dict[str, Any] | None = None,
    task_values: dict[str, Any] | None = None,
    controller_config: dict[str, Any] | None = None,
    all_task_values: dict[int, dict[str, str]] | None = None,
    all_task_names: dict[int, str] | None = None,
) -> str:
    if task_config is None:
        task_config = {}
    if task_values is None:
        task_values = {}
    if controller_config is None:
        controller_config = {}

    tskname = task_config.get("TDN", task_config.get("name", f"Task{task_index + 1}"))
    tskidx = str(task_index + 1)
    idx = str(task_values.get("idx", task_config.get("idx", "0")))

    raw = task_values.get("named_values", task_values)
    valname = ""
    if "named_values" in task_values:
        vnames = task_values.get("value_names", [])
        valname = vnames[0] if vnames else ""
    if not valname:
        vkeys = [k for k in raw if k not in ("named_values", "value_names", "idx")]
        valname = vkeys[0] if vkeys else ""

    extra_vars: dict[str, str] = {
        "tskname": tskname,
        "valname": valname,
        "tskidx": tskidx,
        "id": idx,
        "idx": idx,
        "name": tskname,
        "devclass": task_config.get("devclass", ""),
        "unique_id": task_config.get("unique_id", ""),
        "taskname": tskname,
        "rnd": str(random.randint(0, 32767)),
    }

    def _replace_var(m: re.Match) -> str:
        name = m.group(1).lower()
        if name in extra_vars:
            return extra_vars[name]
        return resolve_system_var(name)

    result = VAR_PATTERN.sub(_replace_var, text)
    atv = all_task_values
    atn = all_task_names
    if atv is None and atn is None and (LIVE_TASK_VALUES or LIVE_TASK_NAMES):
        atv = LIVE_TASK_VALUES
        atn = LIVE_TASK_NAMES
    if atv is not None or atn is not None:
        result = resolve_taskval(result, atv, atn)
    return resolve_special_chars(result)
