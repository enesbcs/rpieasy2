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
        self._model: int = 0
        self._de_pin: int = -1
        self._port: str = "/dev/ttyAMA0"
        self._query_config: list[int] = [0, 1, 2, 5]

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        try:
            self._addr = int(self._config.get("modbus_addr", 1))
        except (ValueError, TypeError):
            self._addr = 1
        try:
            self._baud = int(self._config.get("baudrate", 9600))
        except (ValueError, TypeError):
            self._baud = 9600
        try:
            self._de_pin = int(self._config.get("de_pin", -1))
        except (ValueError, TypeError):
            self._de_pin = -1
        for i in range(P108_NR_OUTPUT_VALUES):
            try:
                self._query_config[i] = int(self._config.get(f"query{i + 1}", [0, 1, 2, 5][i]))
            except (ValueError, TypeError):
                self._query_config[i] = [0, 1, 2, 5][i]
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
        try:
            if self._de_pin >= 0 and self._hw:
                self._hw.gpio.write(self._de_pin, 1)
            await asyncio.to_thread(self._serial.write, req)
            if self._de_pin >= 0 and self._hw:
                await asyncio.sleep(0.001)
                self._hw.gpio.write(self._de_pin, 0)
            await asyncio.sleep(0.2)
            resp = await asyncio.to_thread(self._serial.read, 256)
            if len(resp) < 5:
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
        except Exception as e:
            logger.error("DDS238 modbus error: %s", e)
        return None

    async def _read_float(self, hi_reg: int) -> float:
        vals = await self._modbus_read(hi_reg, 2)
        if vals and len(vals) >= 2:
            raw = (vals[0] << 16) | vals[1]
            import struct as st
            return st.unpack(">f", st.pack(">I", raw))[0]
        return 0.0

    async def _read_int32(self, reg: int) -> int:
        vals = await self._modbus_read(reg, 2)
        if vals and len(vals) >= 2:
            return (vals[0] << 16) | vals[1]
        return 0

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._serial:
            return False
        v = await self._read_float(0x00)
        a = await self._read_float(0x02)
        w = await self._read_float(0x04)
        wh_imp = await self._read_float(0x06)
        wh_exp = await self._read_float(0x08)
        wh_tot = await self._read_float(0x0A)
        va = await self._read_float(0x0C)
        pf = await self._read_float(0x0E)
        f_raw = await self._read_int32(0x10)
        f = f_raw / 100.0 if f_raw else 0.0

        all_vals = [v, a, w, wh_imp, wh_exp, wh_tot, va, pf, f]
        out = {}
        for i, q in enumerate(self._query_config):
            if q < len(all_vals):
                label = P108_QUERIES.get(q, f"Val{i}")
                out[label] = all_vals[q]
        event.data["values"] = out
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("modbus_addr", 1)
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("model", 0)
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
            self._query_config[i] = int(self._config.get(f"query{i + 1}", [0, 1, 2, 5][i]))
        self._addr = int(self._config.get("modbus_addr", 1))
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
