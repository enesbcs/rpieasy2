from __future__ import annotations

import hashlib
import logging
import struct
import time
from typing import Any

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.config import get_config
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.p2p_service import get_p2p_port, p2p_sendto
from rpieasy2.core.system_vars import LIVE_TASK_VALUES

P2P_DEFAULT_PORT = 8266
VARS_PER_TASK = 4
TASKS_MAX = 32
INVALID_TASK_INDEX = TASKS_MAX
INVALID_PLUGIN_ID = 0
BROADCAST_UNIT = 255
PLUGIN_CONFIGVAR_MAX = 8
DUMMY_PLUGIN_ID = 33
BUILD_EXTRA_SETTINGS = 20871
BUILD_PLUGIN_MATCH = 20460

_P2P_HEADER = 0xFF
_P2P_TYPE_INFO_PULL = 2
_P2P_TYPE_SENSOR_INFO = 3
_P2P_TYPE_DATA_PULL = 4
_P2P_TYPE_SENSOR_DATA = 5

logger = logging.getLogger("rpieasy2.controller.c013")

_remote_task_map: dict[tuple[int, int], dict[str, Any]] = {}
_remote_values: dict[tuple[int, int], list[float]] = {}
_remote_to_local: dict[tuple[int, int], int] = {}


def get_remote_task_map() -> dict[tuple[int, int], dict[str, Any]]:
    return dict(_remote_task_map)


def get_remote_values() -> dict[tuple[int, int], list[float]]:
    return dict(_remote_values)


def _short_checksum(data: bytes, len_upto_checksum: int) -> bytes:
    md5 = hashlib.md5()
    md5.update(data[:len_upto_checksum])
    after = len_upto_checksum + 4
    if after < len(data):
        md5.update(data[after:])
    full = md5.digest()
    result = bytearray(4)
    for i in range(16):
        result[i % 4] ^= full[i]
    return bytes(result)


def _trim_trailing_zeros(buf: bytearray, min_size: int = 0) -> bytearray:
    while len(buf) > min_size and buf[-1] == 0:
        buf.pop()
    return buf


def _make_sensor_info_payload(
    source_unit: int, source_task: int, dest_task: int,
    device_number: int, task_name: str, value_names: list[str],
    sensor_type: int = 0, idx: int = 0,
    build: int = 10000,
    value_decimals: list[int] | None = None,
    plugin_config: list[int] | None = None,
    error_values: list[float] | None = None,
    min_values: list[float] | None = None,
    max_values: list[float] | None = None,
    various_bits: list[int] | None = None,
) -> bytes:
    buf = bytearray()
    buf.append(_P2P_HEADER)
    buf.append(_P2P_TYPE_SENSOR_INFO)
    buf.append(source_unit)
    buf.append(BROADCAST_UNIT)
    buf.append(source_task)
    buf.append(dest_task)
    buf.append(device_number)
    tname = task_name.encode("utf-8", "replace")[:25]
    buf.extend(tname.ljust(26, b"\x00"))
    for i in range(VARS_PER_TASK):
        raw = value_names[i] if i < len(value_names) else ""
        vn = raw.encode("utf-8", "replace")[:25]
        buf.extend(vn.ljust(26, b"\x00"))
    buf.append(sensor_type)
    checksum_offset = len(buf)
    buf.extend(b"\x00\x00\x00\x00")
    buf.extend(struct.pack("<H", build))
    buf.extend(struct.pack("<I", idx))

    config = plugin_config or [0] * PLUGIN_CONFIGVAR_MAX
    for val in config[:PLUGIN_CONFIGVAR_MAX]:
        buf.extend(struct.pack("<h", val))

    buf.append(0)

    decimals = value_decimals or [0] * VARS_PER_TASK
    for val in decimals[:VARS_PER_TASK]:
        buf.append(val & 0xFF)

    vb = various_bits or [0] * VARS_PER_TASK
    for val in vb[:VARS_PER_TASK]:
        buf.extend(struct.pack("<I", val))

    err = error_values or [0.0] * VARS_PER_TASK
    for val in err[:VARS_PER_TASK]:
        buf.extend(struct.pack("<f", val))

    mn = min_values or [0.0] * VARS_PER_TASK
    for val in mn[:VARS_PER_TASK]:
        buf.extend(struct.pack("<f", val))

    mx = max_values or [0.0] * VARS_PER_TASK
    for val in mx[:VARS_PER_TASK]:
        buf.extend(struct.pack("<f", val))

    buf = _trim_trailing_zeros(buf, checksum_offset)

    payload = bytes(buf)
    cs = _short_checksum(payload, checksum_offset)
    payload = payload[:checksum_offset] + cs + payload[checksum_offset + 4:]
    return payload


def _make_sensor_data_payload(
    source_unit: int, source_task: int, dest_task: int,
    device_number: int, values: list[float],
    sensor_type: int = 0, idx: int = 0,
    build: int = 10000, timestamp_sec: int = 0, timestamp_frac: int = 0,
) -> bytes:
    buf = bytearray()
    buf.append(_P2P_HEADER)
    buf.append(_P2P_TYPE_SENSOR_DATA)
    buf.append(source_unit)
    buf.append(BROADCAST_UNIT)
    buf.append(source_task)
    buf.append(dest_task)
    buf.append(device_number)
    buf.append(sensor_type)
    for i in range(VARS_PER_TASK):
        buf.extend(struct.pack("<f", values[i] if i < len(values) else 0.0))
    checksum_offset = len(buf)
    buf.extend(b"\x00\x00\x00\x00")
    buf.extend(struct.pack("<H", build))
    buf.extend(struct.pack("<H", timestamp_frac & 0xFFFF))
    buf.extend(struct.pack("<I", timestamp_sec))
    buf.extend(struct.pack("<I", idx))
    # No trim needed for data payload; values are always present
    payload = bytes(buf)
    cs = _short_checksum(payload, checksum_offset)
    payload = payload[:checksum_offset] + cs + payload[checksum_offset + 4:]
    return payload


def parse_sensor_info(data: bytes) -> dict[str, Any] | None:
    if len(data) < 8 or data[0] != _P2P_HEADER or data[1] != _P2P_TYPE_SENSOR_INFO:
        return None
    result: dict[str, Any] = {}
    result["source_unit"] = data[2]
    result["dest_unit"] = data[3]
    result["source_task_index"] = data[4]
    result["dest_task_index"] = data[5]
    result["device_number"] = data[6]
    result["task_name"] = data[7:33].split(b"\x00", 1)[0].decode("utf-8", "replace")
    vnames: list[str] = []
    for i in range(VARS_PER_TASK):
        start = 33 + i * 26
        vn = data[start:start + 26].split(b"\x00", 1)[0].decode("utf-8", "replace")
        vnames.append(vn)
    result["value_names"] = vnames
    if len(data) > 137:
        result["sensor_type"] = data[137]
    else:
        result["sensor_type"] = 0
    if len(data) > 141:
        cs_bytes = data[138:142]
        expected_cs = _short_checksum(data, 138)
        result["checksum_valid"] = cs_bytes == expected_cs if any(cs_bytes) else True
    else:
        result["checksum_valid"] = True
    if len(data) > 143:
        result["source_node_build"] = struct.unpack("<H", data[142:144])[0]
    else:
        result["source_node_build"] = 0
    if len(data) > 147:
        result["idx"] = struct.unpack("<I", data[144:148])[0]
    else:
        result["idx"] = 0
    result["plugin_config"] = [0] * PLUGIN_CONFIGVAR_MAX
    for i in range(PLUGIN_CONFIGVAR_MAX):
        off = 148 + i * 2
        if len(data) >= off + 2:
            result["plugin_config"][i] = struct.unpack("<h", data[off:off+2])[0]
    result["extra_settings_version"] = data[164] if len(data) > 164 else 0
    result["value_decimals"] = [0] * VARS_PER_TASK
    for i in range(VARS_PER_TASK):
        if len(data) > 165 + i:
            result["value_decimals"][i] = data[165 + i]
    result["various_bits"] = [0] * VARS_PER_TASK
    for i in range(VARS_PER_TASK):
        off = 169 + i * 4
        if len(data) >= off + 4:
            result["various_bits"][i] = struct.unpack("<I", data[off:off+4])[0]
    result["error_values"] = [0.0] * VARS_PER_TASK
    for i in range(VARS_PER_TASK):
        off = 185 + i * 4
        if len(data) >= off + 4:
            result["error_values"][i] = struct.unpack("<f", data[off:off+4])[0]
    result["min_values"] = [0.0] * VARS_PER_TASK
    for i in range(VARS_PER_TASK):
        off = 201 + i * 4
        if len(data) >= off + 4:
            result["min_values"][i] = struct.unpack("<f", data[off:off+4])[0]
    result["max_values"] = [0.0] * VARS_PER_TASK
    for i in range(VARS_PER_TASK):
        off = 217 + i * 4
        if len(data) >= off + 4:
            result["max_values"][i] = struct.unpack("<f", data[off:off+4])[0]
    return result


def parse_sensor_data(data: bytes) -> dict[str, Any] | None:
    if len(data) < 8 or data[0] != _P2P_HEADER or data[1] != _P2P_TYPE_SENSOR_DATA:
        return None
    result: dict[str, Any] = {}
    result["source_unit"] = data[2]
    result["dest_unit"] = data[3]
    result["source_task_index"] = data[4]
    result["dest_task_index"] = data[5]
    result["device_number"] = data[6] if len(data) > 6 else INVALID_PLUGIN_ID
    result["sensor_type"] = data[7] if len(data) > 7 else 0
    raw_values = data[8:24]
    raw_values = raw_values.ljust(16, b"\x00")
    values: list[float] = []
    for i in range(VARS_PER_TASK):
        v = struct.unpack("<f", raw_values[i * 4:(i + 1) * 4])[0]
        values.append(v)
    result["values"] = values
    if len(data) > 24:
        cs_offset = 24
        cs_bytes = data[cs_offset:cs_offset + 4]
        expected_cs = _short_checksum(data, cs_offset)
        result["checksum_valid"] = cs_bytes == expected_cs if any(cs_bytes) else True
    else:
        result["checksum_valid"] = True
    if len(data) > 27:
        result["source_node_build"] = struct.unpack("<H", data[28:30])[0]
    else:
        result["source_node_build"] = 0
    if len(data) > 31:
        result["timestamp_frac"] = struct.unpack("<H", data[30:32])[0]
    else:
        result["timestamp_frac"] = 0
    if len(data) > 35:
        result["timestamp_sec"] = struct.unpack("<I", data[32:36])[0]
    else:
        result["timestamp_sec"] = 0
    if len(data) > 39:
        result["idx"] = struct.unpack("<I", data[36:40])[0]
    else:
        result["idx"] = 0
    return result


def parse_pull_header(data: bytes) -> dict[str, Any] | None:
    if len(data) < 6 or data[0] != _P2P_HEADER:
        return None
    if data[1] not in (_P2P_TYPE_INFO_PULL, _P2P_TYPE_DATA_PULL):
        return None
    return {
        "type": data[1],
        "source_unit": data[2],
        "dest_unit": data[3],
        "source_task_index": data[4],
        "dest_task_index": data[5],
    }


def _is_supported_plugin(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        from rpieasy2.core.webserver import _lazy_load_plugin, _plugin_info
        _lazy_load_plugin(pid)
        return pid in _plugin_info
    except Exception:
        return False


def _build_task_from_info(info: dict) -> dict[str, Any]:
    task: dict[str, Any] = {
        "plugin_id": info["device_number"],
        "data_feed_source": info["source_unit"],
        "TDN": info["task_name"],
        "TDE": True,
        "TDNUM_out": info.get("sensor_type", 0),
    }
    for i, vn in enumerate(info.get("value_names", [])):
        if vn:
            task[f"TDVN{i + 1}"] = vn

    if info.get("source_node_build", 0) >= BUILD_EXTRA_SETTINGS:
        for i in range(PLUGIN_CONFIGVAR_MAX):
            val = info.get("plugin_config", [0] * PLUGIN_CONFIGVAR_MAX)[i]
            if val != 0:
                task[f"plugin_config_{i}"] = val

        for i in range(VARS_PER_TASK):
            dec = info.get("value_decimals", [0] * VARS_PER_TASK)[i]
            if dec != 0:
                task[f"TDVD{i + 1}"] = dec

    if info["device_number"] == DUMMY_PLUGIN_ID and info.get("sensor_type", 0) != 0:
        task["TDNUM_out"] = info["sensor_type"]
        task["plugin_config_0"] = info["sensor_type"]

    return task


def _find_free_task(cfg) -> int:
    tasks = cfg.data.get("tasks", [])
    for i in range(TASKS_MAX):
        if i >= len(tasks) or not tasks[i].get("plugin_id", 0):
            return i
    return INVALID_TASK_INDEX


class C013ESPEasyP2P(ControllerBase):
    CONTROLLER_ID = 13
    CONTROLLER_NAME = "ESPEasy P2P Networking"
    Custom = True
    usesHost = False
    usesPort = True
    usesID = False
    usesMQTT = False
    usesAccount = False
    usesPassword = False
    usesTemplate = False
    usesQueue = False
    usesTimeout = False
    defaultPort = P2P_DEFAULT_PORT

    def __init__(self):
        super().__init__()
        self._unit: int = 0
        self._build: int = 10000
        self._dynamictasknum: bool = False

    def subscribe(self) -> None:
        super().subscribe()
        bus = get_event_bus()
        bus.subscribe("P2P_SENSOR_INFO", self._dispatch)
        bus.subscribe("P2P_SENSOR_DATA", self._dispatch)
        bus.subscribe("P2P_SENSOR_INFO_PULL", self._dispatch)
        bus.subscribe("P2P_SENSOR_DATA_PULL", self._dispatch)
        bus.subscribe("TASK_CONFIG_CHANGED", self._dispatch)

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._unit = int(config.get("unit", config.get("controllerid", 0)))
        self._build = int(config.get("build", 10000))
        self._dynamictasknum = bool(config.get("dynamictasknum", config.get("c013_dyntask", False)))
        logger.info(f"C013 P2P controller ready, unit {self._unit}")
        return True

    async def on_controller_subscribe(self, event: Event) -> bool | None:
        port = get_p2p_port()
        if port > 0:
            logger.info(f"C013 P2P using p2p_service on port {port}")
            return True
        logger.warning("C013 P2P: p2p_service not running, sensor sharing unavailable")
        return False

    def _build_extended_fields(self, task_config: dict) -> dict:
        decimals = []
        for i in range(1, VARS_PER_TASK + 1):
            val = task_config.get(f"TDVD{i}", 0)
            try:
                decimals.append(int(val))
            except (ValueError, TypeError):
                decimals.append(0)
        plugin_config = []
        for i in range(PLUGIN_CONFIGVAR_MAX):
            val = task_config.get(f"plugin_config_{i}", 0)
            try:
                plugin_config.append(int(val))
            except (ValueError, TypeError):
                plugin_config.append(0)
        return {
            "value_decimals": decimals,
            "plugin_config": plugin_config,
            "error_values": [],
            "min_values": [],
            "max_values": [],
            "various_bits": [],
        }

    def _get_value_names(self, task_config: dict, device_number: int) -> list[str]:
        value_names = [task_config.get(f"TDVN{i}", "") for i in range(1, VARS_PER_TASK + 1)]
        if not any(value_names):
            try:
                from rpieasy2.core.webserver import _lazy_load_plugin, _plugin_info
                _lazy_load_plugin(device_number)
                entry = _plugin_info.get(device_number)
                if entry and entry.get("class"):
                    inst = entry["class"]()
                    inst._config = task_config
                    tv = inst.get_task_values(task_config)
                    if tv:
                        value_names = list(tv.keys())[:VARS_PER_TASK]
            except Exception:
                pass
        if not any(value_names):
            value_names = [f"Value {i+1}" for i in range(VARS_PER_TASK)]
        return value_names

    async def on_controller_send(self, event: Event) -> bool | None:
        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        idx_config = config.get("task_values", {}).get("idx", config.get("idx", 0))
        try:
            idx_val = int(idx_config) if idx_config else 0
        except (ValueError, TypeError):
            idx_val = 0
        task_index = event.task_index if hasattr(event, "task_index") else config.get("task_index", 0)
        try:
            task_index = int(task_index)
        except (ValueError, TypeError):
            task_index = 0
        device_number = int(config.get("plugin_id", config.get("TDSF", config.get("deviceNumber", 1))))
        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))
        float_values: list[float] = []
        for vn in value_names[:VARS_PER_TASK]:
            try:
                float_values.append(float(named_values.get(vn, 0)))
            except (ValueError, TypeError):
                float_values.append(0.0)
        while len(float_values) < VARS_PER_TASK:
            float_values.append(0.0)
        sensor_type = int(config.get("TDNUM_out", 0))
        actual_count = len([n for n in value_names[:VARS_PER_TASK] if n]) or 1
        logger.debug(f"C013 send task={task_index} plugin={device_number} "
                     f"sensor_type={sensor_type} count={actual_count} values={float_values}")
        now = time.time()
        ts_sec = int(now)
        ts_frac = int((now - ts_sec) * 65536) & 0xFFFF

        payload_data = _make_sensor_data_payload(
            source_unit=self._unit,
            source_task=task_index,
            dest_task=task_index,
            device_number=device_number,
            values=float_values,
            sensor_type=sensor_type,
            idx=idx_val,
            build=self._build,
            timestamp_sec=ts_sec,
            timestamp_frac=ts_frac,
        )
        p2p_sendto(payload_data)
        return True

    async def on_task_config_changed(self, event: Event) -> bool | None:
        task_config = event.data.get("task_config", {})
        task_index = event.task_index
        if task_index < 0:
            return False
        if not task_config:
            stale = [k for k, v in _remote_to_local.items() if v == task_index]
            for k in stale:
                del _remote_to_local[k]
            return False
        device_number = int(task_config.get("plugin_id", task_config.get("TDSF", 0)))
        if device_number == INVALID_PLUGIN_ID:
            return False
        value_names = self._get_value_names(task_config, device_number)
        extended = self._build_extended_fields(task_config)
        info_build = max(self._build, BUILD_EXTRA_SETTINGS) if any(v != 0 for v in extended["plugin_config"]) else self._build
        payload_info = _make_sensor_info_payload(
            source_unit=self._unit,
            source_task=task_index,
            dest_task=task_index,
            device_number=device_number,
            task_name=task_config.get("TDN", task_config.get("name", "task")),
            value_names=value_names[:VARS_PER_TASK],
            sensor_type=int(task_config.get("TDNUM_out", 1)),
            idx=int(task_config.get("idx", 0)),
            build=info_build,
            value_decimals=extended["value_decimals"],
            plugin_config=extended["plugin_config"],
        )
        p2p_sendto(payload_info)
        logger.info(f"C013 broadcast sensor info for task {task_index} (device {device_number})")
        return True

    async def on_p2p_sensor_info_pull(self, event: Event) -> bool | None:
        data: bytes = event.data.get("raw", b"")
        parsed = parse_pull_header(data)
        if parsed is None:
            return False
        if parsed["dest_unit"] not in (BROADCAST_UNIT, self._unit):
            return True
        task_index = parsed["dest_task_index"]
        if not (0 <= task_index < TASKS_MAX):
            return True

        cfg = get_config()
        task_config = cfg.get_task(task_index)
        if not task_config:
            return True
        plugin_id = task_config.get("plugin_id", INVALID_PLUGIN_ID)
        if plugin_id == INVALID_PLUGIN_ID:
            return True

        value_names = self._get_value_names(task_config, plugin_id)
        extended = self._build_extended_fields(task_config)
        info_build = max(self._build, BUILD_EXTRA_SETTINGS) if any(v != 0 for v in extended["plugin_config"]) else self._build

        payload = _make_sensor_info_payload(
            source_unit=self._unit,
            source_task=task_index,
            dest_task=parsed["source_task_index"],
            device_number=plugin_id,
            task_name=task_config.get("TDN", task_config.get("name", f"Task{task_index+1}")),
            value_names=value_names,
            sensor_type=int(task_config.get("TDNUM_out", task_config.get("TDSF", 1))),
            idx=int(task_config.get("idx", 0)),
            build=info_build,
            value_decimals=extended["value_decimals"],
            plugin_config=extended["plugin_config"],
        )
        p2p_sendto(payload)
        logger.debug(f"C013 replied to info pull for task {task_index} from unit {parsed['source_unit']}")
        return True

    async def on_p2p_sensor_data_pull(self, event: Event) -> bool | None:
        data: bytes = event.data.get("raw", b"")
        parsed = parse_pull_header(data)
        if parsed is None:
            return False
        if parsed["dest_unit"] not in (BROADCAST_UNIT, self._unit):
            return True
        task_index = parsed["dest_task_index"]
        if not (0 <= task_index < TASKS_MAX):
            return True

        cfg = get_config()
        task_config = cfg.get_task(task_index)
        if not task_config:
            return True
        plugin_id = task_config.get("plugin_id", INVALID_PLUGIN_ID)
        if plugin_id == INVALID_PLUGIN_ID:
            return True

        live_vals = LIVE_TASK_VALUES.get(task_index, {})
        value_names = [task_config.get(f"TDVN{i+1}", "") for i in range(VARS_PER_TASK)]
        float_values: list[float] = []
        for vn in value_names:
            try:
                float_values.append(float(live_vals.get(vn, 0)))
            except (ValueError, TypeError):
                float_values.append(0.0)
        while len(float_values) < VARS_PER_TASK:
            float_values.append(0.0)

        payload = _make_sensor_data_payload(
            source_unit=self._unit,
            source_task=task_index,
            dest_task=parsed["source_task_index"],
            device_number=plugin_id,
            values=float_values,
            sensor_type=int(task_config.get("TDNUM_out", task_config.get("TDSF", 1))),
            idx=int(task_config.get("idx", 0)),
            build=self._build,
        )
        p2p_sendto(payload)
        logger.debug(f"C013 replied to data pull for task {task_index} from unit {parsed['source_unit']}")
        return True

    async def on_p2p_sensor_info(self, event: Event) -> bool | None:
        data: bytes = event.data.get("raw", b"")
        addr: tuple[str, int] = event.data.get("addr", ("0.0.0.0", 0))
        info = parse_sensor_info(data)
        if info is None:
            return False
        key = (info["source_unit"], info["source_task_index"])
        _remote_task_map[key] = info
        logger.debug(f"P2P sensor info from unit {info['source_unit']} @ {addr[0]}: "
                     f"task='{info['task_name']}' plugin={info['device_number']} "
                     f"sensor_type={info.get('sensor_type', 0)} values={info['value_names']}")

        dest_ti = info["dest_task_index"]
        if dest_ti >= TASKS_MAX:
            return True

        if self._dynamictasknum:
            mapped_ti = _remote_to_local.get(key)
            if mapped_ti is not None:
                dest_ti = mapped_ti
            else:
                alloc_cfg = get_config()
                existing = alloc_cfg.get_task(dest_ti)
                if existing and existing.get("plugin_id", 0) != 0 and existing.get("plugin_id", 0) != info["device_number"]:
                    free_ti = _find_free_task(alloc_cfg)
                    if free_ti != INVALID_TASK_INDEX:
                        dest_ti = free_ti
                _remote_to_local[key] = dest_ti

        cfg = get_config()
        current_task = cfg.get_task(dest_ti)
        current_plugin_id = current_task.get("plugin_id", INVALID_PLUGIN_ID) if current_task else INVALID_PLUGIN_ID
        device_number = info["device_number"]

        if current_plugin_id == INVALID_PLUGIN_ID or current_plugin_id == device_number:
            must_update = False
            if current_plugin_id == device_number and current_task:
                data_feed = current_task.get("data_feed_source", 0)
                if data_feed == info["source_unit"]:
                    must_update = True

            if must_update or current_plugin_id == INVALID_PLUGIN_ID:
                if current_plugin_id == INVALID_PLUGIN_ID and not _is_supported_plugin(device_number):
                    return True

                task = _build_task_from_info(info)
                if must_update:
                    task["TDE"] = True

                cfg.set_task(dest_ti, task)
                cfg.save()

                bus = get_event_bus()
                await bus.publish(Event(
                    type="TASK_CONFIG_CHANGED",
                    task_index=dest_ti,
                    data={"task_config": task},
                ))
                logger.info(f"C013 auto-{'updated' if must_update else 'created'} task {dest_ti} "
                           f"from unit {info['source_unit']} plugin={device_number}")

        return True

    async def on_p2p_sensor_data(self, event: Event) -> bool | None:
        data: bytes = event.data.get("raw", b"")
        addr: tuple[str, int] = event.data.get("addr", ("0.0.0.0", 0))
        info = parse_sensor_data(data)
        if info is None:
            return False
        key = (info["source_unit"], info["source_task_index"])
        _remote_values[key] = info["values"]
        logger.debug(f"P2P sensor data from unit {info['source_unit']} @ {addr[0]}: "
                     f"sensor_type={info.get('sensor_type', 0)} values={info['values']}")

        dest_ti = info["dest_task_index"]
        if dest_ti >= TASKS_MAX:
            return True

        if self._dynamictasknum:
            mapped_ti = _remote_to_local.get(key)
            if mapped_ti is not None:
                dest_ti = mapped_ti

        cfg = get_config()
        task_config = cfg.get_task(dest_ti)
        if not task_config:
            return True

        data_feed = task_config.get("data_feed_source", 0)
        if data_feed == 0 or data_feed != info["source_unit"]:
            return True

        src_build = info.get("source_node_build", 0)
        must_match = src_build >= BUILD_PLUGIN_MATCH
        task_plugin = task_config.get("plugin_id", 0)
        if must_match and task_plugin != info["device_number"]:
            logger.warning(f"P2P data: PluginID mismatch for task {dest_ti} "
                          f"from unit {info['source_unit']} remote:{info['device_number']} local:{task_plugin}")
            return True

        task_st = task_config.get("TDNUM_out", task_config.get("TDSF", 0))
        remote_st = info.get("sensor_type", 0)
        if must_match and task_st != 0 and remote_st != 0 and task_st != remote_st:
            logger.warning(f"P2P data: SensorType mismatch for task {dest_ti} "
                          f"from unit {info['source_unit']}")
            return True

        remote_info = _remote_task_map.get((info["source_unit"], info["source_task_index"]), {})
        value_names: list[str] = remote_info.get("value_names", [])
        if not value_names:
            value_names = [task_config.get(f"TDVN{i+1}", "") for i in range(VARS_PER_TASK)]

        named_values: dict[str, float] = {}
        for i in range(VARS_PER_TASK):
            if i < len(info["values"]):
                vn = value_names[i] if i < len(value_names) else ""
                named_values[vn] = info["values"][i]

        LIVE_TASK_VALUES[dest_ti] = {k: str(v) for k, v in named_values.items()}

        bus = get_event_bus()
        ev = Event(
            type="PLUGIN_READ",
            task_index=dest_ti,
            data={"values": named_values},
        )
        await bus.publish(ev)
        return True

    async def on_controller_send_udp(self, event: Event) -> bool | None:
        payload = event.data.get("payload", b"")
        host = event.data.get("host", "255.255.255.255")
        port = int(event.data.get("port", get_p2p_port() or P2P_DEFAULT_PORT))
        if not payload:
            return False
        p2p_sendto(payload if isinstance(payload, bytes) else payload.encode(), addr=host, port=port)
        return True
