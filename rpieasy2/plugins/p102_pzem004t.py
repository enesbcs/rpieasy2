from __future__ import annotations

import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_SERIAL,
    SENSOR_TYPE_DUAL,
    SENSOR_TYPE_LABELS,
    SENSOR_TYPE_QUAD,
    SENSOR_TYPE_SINGLE,
    SENSOR_TYPE_TO_VALUE_COUNT,
    SENSOR_TYPE_TRIPLE,
    SENSOR_V_TYPE_CURRENT,
    SENSOR_V_TYPE_KWH,
    SENSOR_V_TYPE_SINGLE,
    SENSOR_V_TYPE_VOLTAGE,
    SENSOR_V_TYPE_WATT,
)
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p102")

PZEM_CMD_READ = 0x04
PZEM_CMD_RESET = 0x42
PZEM_CMD_SET_ADDR = 0x06

# Supported PZEM models. All of them speak Modbus-RTU (function code 0x04) at
# 9600 8N1 over a serial line (TTL for the 004T, RS485 for the 016/017).
PZEM_MODEL_004T = 0  # PZEM-004T v3.0 (AC)
PZEM_MODEL_017 = 1   # PZEM-017 v1 (DC)
PZEM_MODEL_016 = 2   # PZEM-016 (AC, RS485)
PZEM_MODELS = {
    PZEM_MODEL_004T: "PZEM-004Tv30 (AC)",
    PZEM_MODEL_017: "PZEM-017v1 (DC)",
    PZEM_MODEL_016: "PZEM-016 (AC RS485)",
}

# Number of registers requested from 0x0000. Values taken from the ESPEasy
# PZEM-004T-v30 library (PZEM004Tv30_REQUEST_REGISTERS / PZEM017v1_...).
PZEM_REG_COUNT = {
    PZEM_MODEL_004T: 10,
    PZEM_MODEL_016: 10,
    PZEM_MODEL_017: 8,
}

PZEM_QUERIES = [
    "Voltage_V",
    "Current_A",
    "Power_W",
    "Energy_kWh",
    "Power_Factor_cosphi",
    "Frequency_Hz",
]

# AC models report all 6 quantities, the DC model only the first 4
PZEM_QUERY_COUNT = {
    PZEM_MODEL_004T: 6,
    PZEM_MODEL_016: 6,
    PZEM_MODEL_017: 4,
}

PZEM_QUERY_VTYPES = {
    0: SENSOR_V_TYPE_VOLTAGE,
    1: SENSOR_V_TYPE_CURRENT,
    2: SENSOR_V_TYPE_WATT,
    3: SENSOR_V_TYPE_KWH,
    4: SENSOR_V_TYPE_SINGLE,  # power factor
    5: SENSOR_V_TYPE_SINGLE,  # frequency
}

PZEM_QUERY_DEFAULTS = [0, 1, 2, 3]
PZEM_READ_TIMEOUT = 0.5


def _pzem_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class P102PZEM(PluginBase):
    PLUGIN_ID = 102
    PLUGIN_NAME = "Energy - PZEM-004T/016 (AC) / PZEM-017 (DC)"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        output_data_type=OutputDataType.SIMPLE,
        mqtt_state_class=True,
    )

    PZEM_QUERIES = PZEM_QUERIES

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 1
        self._pzem_type: int = PZEM_MODEL_004T
        self._query_config: list[int] = list(PZEM_QUERY_DEFAULTS)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def _queries_from_config(cls, task_config: dict[str, Any]) -> list[int]:
        queries: list[int] = []
        for i in range(4):
            default = PZEM_QUERY_DEFAULTS[i]
            raw = task_config.get(f"query{i + 1}")
            if raw is None:
                queries.append(default)
            else:
                queries.append(cls._safe_int(raw, default))
        return queries

    def _get_num_values_from(self, task_config: dict[str, Any]) -> int:
        st = self._safe_int(task_config.get("TDNUM_out") or SENSOR_TYPE_QUAD, SENSOR_TYPE_QUAD)
        return SENSOR_TYPE_TO_VALUE_COUNT.get(st, self.PLUGIN_VALUES)

    def _get_num_values(self) -> int:
        return self._get_num_values_from(self._config)

    @staticmethod
    def _u32(low: int, high: int) -> int:
        # PZEM registers store multi-register values little-endian: low word
        # first, high word second (verified against the RPiEasy lib_pzem.py and
        # the ESPEasy bundled PZEM-004T-v30 library).
        return (low & 0xFFFF) + ((high & 0xFFFF) << 16)

    def _decode_ac(self, vals: list[int]) -> list[float]:
        # PZEM-004T v3 / PZEM-016 (AC)
        # 0x0000 V (0.1V), 0x0001-2 A (0.001A), 0x0003-4 W (0.1W),
        # 0x0005-6 E (1Wh -> kWh), 0x0007 Hz (0.1Hz), 0x0008 PF (0.01)
        return [
            vals[0] / 10.0,
            self._u32(vals[1], vals[2]) / 1000.0,
            self._u32(vals[3], vals[4]) / 10.0,
            self._u32(vals[5], vals[6]) / 1000.0,
            vals[8] / 100.0,
            vals[7] / 10.0,
        ]

    def _decode_dc(self, vals: list[int]) -> list[float]:
        # PZEM-017 v1 (DC)
        # 0x0000 V (0.01V), 0x0001 A (0.01A), 0x0002-3 W (0.1W), 0x0004-5 E (1Wh)
        return [
            vals[0] / 100.0,
            vals[1] / 100.0,
            self._u32(vals[2], vals[3]) / 10.0,
            self._u32(vals[4], vals[5]) / 1000.0,
        ]

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._safe_int(self._config.get("address", 1) or 1, 1)
        self._pzem_type = self._safe_int(self._config.get("pzem_type", PZEM_MODEL_004T), PZEM_MODEL_004T)
        if self._pzem_type not in PZEM_MODELS:
            self._pzem_type = PZEM_MODEL_004T
        self._query_config = self._queries_from_config(self._config)
        if not self._hw:
            return False
        await self._setup_serial()
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._hw and self._hw.serial:
            try:
                await self._hw.serial.close()
            except Exception:
                pass
        return True

    async def _setup_serial(self) -> None:
        try:
            await self._hw.serial.open(
                port=self._config.get("serial_port", "/dev/ttyAMA0") or "/dev/ttyAMA0",
                baud=int(self._config.get("baudrate") or 9600),
                timeout=1,
            )
        except Exception as e:
            logger.error("PZEM serial open failed: %s", e)

    async def _modbus_read(self, addr: int, count: int) -> list[int] | None:
        if not self._hw or not self._hw.serial or not self._hw.serial.is_open:
            return None
        req = struct.pack(">BBHH", addr, PZEM_CMD_READ, 0x0000, count)
        req += struct.pack("<H", _pzem_crc(req))
        expected = 5 + 2 * count
        try:
            resp = await self._hw.serial.write_then_read(
                req, read_size=expected, timeout=PZEM_READ_TIMEOUT
            )
        except Exception as e:
            logger.error("PZEM modbus read error: %s", e)
            return None
        if len(resp) < expected:
            logger.debug("PZEM: short response (%d/%d bytes)", len(resp), expected)
            return None
        if resp[1] & 0x80:
            logger.warning("PZEM: exception response, code %d", resp[2] if len(resp) > 2 else -1)
            return None
        data_len = resp[2]
        if data_len != 2 * count or len(resp) < 3 + data_len + 2:
            return None
        expected_crc = _pzem_crc(resp[:3 + data_len])
        recv_crc = struct.unpack("<H", resp[3 + data_len:5 + data_len])[0]
        if expected_crc != recv_crc:
            logger.warning("PZEM: CRC mismatch")
            return None
        return [(resp[3 + i] << 8) | resp[4 + i] for i in range(0, data_len, 2)]

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._hw.serial or not self._hw.serial.is_open:
            return False
        count = PZEM_REG_COUNT.get(self._pzem_type, 10)
        vals = await self._modbus_read(self._addr, count)
        if not vals or len(vals) < count:
            logger.warning("PZEM: no valid response from device at address %d", self._addr)
            return False
        if self._pzem_type == PZEM_MODEL_017:
            all_vals = self._decode_dc(vals)
        else:
            all_vals = self._decode_ac(vals)
        out = {}
        for i in range(self._get_num_values()):
            q = self._query_config[i]
            if 0 <= q < len(all_vals):
                out[PZEM_QUERIES[q]] = all_vals[q]
        if not out:
            return False
        event.data["values"] = out
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("resetenergy"):
            parts = command.split(",")
            addr = int(parts[1]) if len(parts) > 1 else self._addr
            if self._hw and self._hw.serial and self._hw.serial.is_open:
                req = struct.pack(">BBHH", addr, PZEM_CMD_RESET, 0x00, 0x00)
                await self._hw.serial.write(req + struct.pack("<H", _pzem_crc(req)))
                return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("address", 1)
        self._config.setdefault("pzem_type", PZEM_MODEL_004T)
        self._config.setdefault("TDNUM_out", SENSOR_TYPE_QUAD)
        self._config.setdefault("query1", 0)
        self._config.setdefault("query2", 1)
        self._config.setdefault("query3", 2)
        self._config.setdefault("query4", 3)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        port = str(self._config.get("serial_port", "/dev/ttyAMA0"))
        try:
            import serial.tools.list_ports
            ports_found = serial.tools.list_ports.comports()
            port_options = [{"value": p.device, "label": p.device} for p in ports_found]
            if not port_options:
                port_options = [{"value": "", "label": "No serial ports found"}]
            elif port and not any(p["value"] == port for p in port_options):
                port_options.append({"value": port, "label": port})
        except Exception:
            port_options = [{"value": port or "", "label": port or "/dev/ttyAMA0"}]
        form.append({"name": "serial_port", "label": "Serial Device", "type": "select",
                     "value": port, "options": port_options})
        form.append({"name": "baudrate", "label": "Baud Rate", "type": "number",
                     "value": self._config.get("baudrate", 9600)})
        form.append({"name": "pzem_type", "label": "PZEM Model", "type": "select",
                     "value": self._config.get("pzem_type", PZEM_MODEL_004T),
                     "options": [{"value": k, "label": v} for k, v in PZEM_MODELS.items()]})
        form.append({"name": "address", "label": "Modbus Address", "type": "number",
                     "value": self._config.get("address", 1), "min": 1, "max": 247})
        model = self._safe_int(self._config.get("pzem_type", PZEM_MODEL_004T), PZEM_MODEL_004T)
        if model not in PZEM_MODELS:
            model = PZEM_MODEL_004T
        query_options = [
            {"value": i, "label": PZEM_QUERIES[i]}
            for i in range(PZEM_QUERY_COUNT.get(model, 6))
        ]
        for i in range(4):
            form.append({
                "name": f"query{i + 1}",
                "label": f"Value {i + 1} Source",
                "type": "select",
                "value": self._config.get(f"query{i + 1}", PZEM_QUERY_DEFAULTS[i]),
                "options": query_options,
            })
        event.data["form"] = form
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        # v2 "Output Configuration" selector: choose how many of the configured
        # values are exposed (drives TDNUM_out / the Values table).
        current = self._safe_int(self._config.get("TDNUM_out") or SENSOR_TYPE_QUAD, SENSOR_TYPE_QUAD)
        options = []
        for st in (SENSOR_TYPE_SINGLE, SENSOR_TYPE_DUAL, SENSOR_TYPE_TRIPLE, SENSOR_TYPE_QUAD):
            count = SENSOR_TYPE_TO_VALUE_COUNT.get(st, 1)
            label = SENSOR_TYPE_LABELS.get(st, f"Type {st}")
            options.append({"value": st, "label": f"{label} ({count} values)", "count": count})
        event.data["output_selector"] = {
            "name": "TDNUM_out",
            "label": "Number Output Values",
            "value": current,
            "options": options,
        }
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = self._safe_int(self._config.get("address", 1) or 1, 1)
        self._pzem_type = self._safe_int(self._config.get("pzem_type", PZEM_MODEL_004T), PZEM_MODEL_004T)
        if self._pzem_type not in PZEM_MODELS:
            self._pzem_type = PZEM_MODEL_004T
        self._query_config = self._queries_from_config(self._config)
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        queries = self._queries_from_config(self._config)
        vtypes = []
        for i in range(self._get_num_values()):
            q = queries[i] if i < len(queries) else i
            vtypes.append(PZEM_QUERY_VTYPES.get(q, SENSOR_V_TYPE_SINGLE))
        event.data["vtypes"] = vtypes
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        st = self._safe_int(self._config.get("TDNUM_out") or SENSOR_TYPE_QUAD, SENSOR_TYPE_QUAD)
        event.data["sensor_type"] = st
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        queries = self._queries_from_config(task_config)
        for i in range(self._get_num_values_from(task_config)):
            q = queries[i]
            name = PZEM_QUERIES[q] if 0 <= q < len(PZEM_QUERIES) else f"Value{i + 1}"
            values[name] = 0.0
        return values
