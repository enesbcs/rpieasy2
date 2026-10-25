from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p108")

P108_NR_OUTPUT_VALUES = 4

# Supported meter variants (register maps)
P108_MODEL_ZN_FLOAT = 0   # DDS238-x ZN: IEEE754 float32 measurement registers
P108_MODEL_ZNS_INT = 1    # DDS238-2 ZN/S: uint16/i16 measurement registers
P108_MODELS = {
    P108_MODEL_ZN_FLOAT: "DDS238-x ZN (float32 registers)",
    P108_MODEL_ZNS_INT: "DDS238-2 ZN/S (uint16 registers)",
}

# Both register maps cover 0x0000..0x0011, so a single read serves all values
P108_FIRST_REG = 0x0000
P108_REG_COUNT = 18

P108_QUERIES = {
    0: "V",
    1: "A",
    2: "W",
    3: "Wh_imp",
    4: "Wh_exp",
    5: "Wh_tot",
    6: "VA",
    7: "PF",
    8: "F",
}
P108_QUERY_LIST = list(P108_QUERIES)

try:
    import serial as _pyserial
    _SERIAL_ERRORS: tuple = (_pyserial.SerialException, OSError)
except ImportError:  # pragma: no cover - pyserial is a hard requirement for this plugin
    _SERIAL_ERRORS = (OSError,)


def _modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class P108DDS238(PluginBase):
    PLUGIN_ID = 108
    PLUGIN_NAME = "Energy (AC) - DDS238-x ZN"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
        mqtt_state_class=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._addr: int = 1
        self._baud: int = 9600
        self._model: int = P108_MODEL_ZN_FLOAT
        self._de_pin: int = -1
        self._port: str = "/dev/ttyAMA0"
        self._query_config: list[int] = [0, 1, 2, 5]
        # Serial access lock: prevents closing the port while a read is in flight
        # (closing sets the underlying fd to None and an in-flight os.write raises
        # "'NoneType' object cannot be interpreted as an integer")
        self._io_lock = asyncio.Lock()

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        self._addr = self._safe_int(self._config.get("modbus_addr", 1), 1)
        self._baud = self._safe_int(self._config.get("baudrate", 9600), 9600)
        self._de_pin = self._safe_int(self._config.get("de_pin", -1), -1)
        self._model = self._safe_int(self._config.get("model", P108_MODEL_ZN_FLOAT), P108_MODEL_ZN_FLOAT)
        for i in range(P108_NR_OUTPUT_VALUES):
            self._query_config[i] = self._safe_int(self._config.get(f"query{i + 1}"), [0, 1, 2, 5][i])
        return await self._setup_serial()

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=pyserial.EIGHTBITS,
                parity=pyserial.PARITY_NONE,
                stopbits=pyserial.STOPBITS_ONE,
                timeout=1,
            )
            return True
        except Exception as e:
            logger.error("DDS238 serial init failed: %s", e)
            self._serial = None
            return False

    async def _close_serial(self) -> None:
        async with self._io_lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

    async def _modbus_read(self, reg: int, count: int) -> list[int] | None:
        if not self._serial:
            return None
        req = struct.pack(">BBHH", self._addr, 0x03, reg, count)
        crc = _modbus_crc(req)
        req += struct.pack("<H", crc)
        async with self._io_lock:
            port = self._serial
            if port is None:
                return None
            try:
                if self._de_pin >= 0 and self._hw:
                    self._hw.gpio.write(self._de_pin, 1)
                await asyncio.to_thread(port.write, req)
                if self._de_pin >= 0 and self._hw:
                    await asyncio.sleep(0.001)
                    self._hw.gpio.write(self._de_pin, 0)
                resp = await asyncio.to_thread(port.read, 256)
            except _SERIAL_ERRORS as e:
                logger.warning("DDS238 serial error: %s", e)
                return None
            except Exception as e:
                logger.debug("DDS238 modbus error: %s", e)
                return None
        if len(resp) < 5:
            logger.debug("DDS238: short response (%d bytes)", len(resp))
            return None
        if resp[1] & 0x80:
            logger.warning("DDS238: exception response, code %d", resp[2] if len(resp) > 2 else -1)
            return None
        data_len = resp[2]
        if len(resp) < 3 + data_len + 2:
            return None
        expected_crc = _modbus_crc(resp[:3 + data_len])
        recv_crc = struct.unpack("<H", resp[3 + data_len:5 + data_len])[0]
        if expected_crc != recv_crc:
            logger.warning("DDS238 CRC mismatch")
            return None
        vals = []
        for i in range(0, data_len, 2):
            vals.append((resp[3 + i] << 8) | resp[4 + i])
        return vals

    @staticmethod
    def _words_to_float(words: list[int], reg: int) -> float:
        raw = (words[reg] << 16) | words[reg + 1]
        return struct.unpack(">f", struct.pack(">I", raw))[0]

    @staticmethod
    def _words_to_u32(words: list[int], reg: int) -> int:
        return (words[reg] << 16) | words[reg + 1]

    @staticmethod
    def _to_i16(val: int) -> int:
        return val - 0x10000 if val & 0x8000 else val

    def _decode_zn_float(self, words: list[int]) -> list[float]:
        # DDS238-x ZN: IEEE754 float32 values, 2 registers each
        # 0x0000 V, 0x0002 A, 0x0004 W, 0x0006 Wh_imp, 0x0008 Wh_exp,
        # 0x000A Wh_tot, 0x000C VA, 0x000E PF, 0x0010 F
        return [self._words_to_float(words, r) for r in (0x00, 0x02, 0x04, 0x06, 0x08, 0x0A, 0x0C, 0x0E, 0x10)]

    def _decode_zns_int(self, words: list[int]) -> list[float]:
        # DDS238-2 ZN/S: integer registers
        # 0x0000-0x0001 total energy (1/100 kWh), 0x0008-0x0009 export, 0x000A-0x000B import
        # 0x000C voltage (1/10 V), 0x000D current (1/100 A), 0x000E active power (1 W, signed)
        # 0x000F reactive power (1 VAr), 0x0010 power factor (1/1000), 0x0011 frequency (1/100 Hz)
        wh = 1000.0 / 100.0  # 1/100 kWh -> Wh
        return [
            words[0x0C] / 10.0,                             # V
            words[0x0D] / 100.0,                            # A
            float(self._to_i16(words[0x0E])),               # W
            self._words_to_u32(words, 0x0A) * wh,           # Wh import
            self._words_to_u32(words, 0x08) * wh,           # Wh export
            self._words_to_u32(words, 0x00) * wh,           # Wh total
            float(words[0x0F]),                             # VA (reactive power)
            words[0x10] / 1000.0,                           # PF
            words[0x11] / 100.0,                            # F (Hz)
        ]

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._serial:
            return False
        words = await self._modbus_read(P108_FIRST_REG, P108_REG_COUNT)
        if words is None or len(words) < P108_REG_COUNT:
            logger.warning("DDS238: no valid response from device at address %d", self._addr)
            return False
        if self._model == P108_MODEL_ZNS_INT:
            all_vals = self._decode_zns_int(words)
        else:
            all_vals = self._decode_zn_float(words)
        out = {}
        for i, q in enumerate(self._query_config):
            if 0 <= q < len(all_vals):
                label = P108_QUERIES.get(q, f"Val{i + 1}")
                out[label] = all_vals[q]
        event.data["values"] = out
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("modbus_addr", 1)
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("model", P108_MODEL_ZN_FLOAT)
        self._config.setdefault("de_pin", -1)
        self._config.setdefault("query1", 0)
        self._config.setdefault("query2", 1)
        self._config.setdefault("query3", 2)
        self._config.setdefault("query4", 5)
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
        form.append({"name": "modbus_addr", "label": "Modbus Address", "type": "number",
                     "value": self._config.get("modbus_addr", 1), "min": 1, "max": 247})
        form.append({"name": "model", "label": "Meter Model", "type": "select",
                     "value": self._config.get("model", P108_MODEL_ZN_FLOAT),
                     "options": [{"value": k, "label": v} for k, v in P108_MODELS.items()]})
        form.append({"name": "de_pin", "label": "DE Pin (RS485)", "type": "number",
                     "value": self._config.get("de_pin", -1)})
        event.data["form"] = form
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        opts = [{"value": k, "label": v} for k, v in P108_QUERIES.items()]
        event.data["output_selector"] = {
            "name": None,
            "label": "Output Values",
            "options": opts,
            "fields": [
                {"name": "query1", "label": "Value 1", "value": self._config.get("query1", 0)},
                {"name": "query2", "label": "Value 2", "value": self._config.get("query2", 1)},
                {"name": "query3", "label": "Value 3", "value": self._config.get("query3", 2)},
                {"name": "query4", "label": "Value 4", "value": self._config.get("query4", 5)},
            ],
        }
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        for i in range(P108_NR_OUTPUT_VALUES):
            self._query_config[i] = self._safe_int(self._config.get(f"query{i + 1}"), [0, 1, 2, 5][i])
        self._addr = self._safe_int(self._config.get("modbus_addr"), 1)
        self._baud = self._safe_int(self._config.get("baudrate"), 9600)
        self._model = self._safe_int(self._config.get("model"), P108_MODEL_ZN_FLOAT)
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin", "number": 2},
            {"label": "DE Pin", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"V": 0.0, "A": 0.0, "W": 0.0, "Wh_tot": 0.0}
