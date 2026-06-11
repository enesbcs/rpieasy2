from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_QUAD, SENSOR_V_TYPE_VOLTAGE, SENSOR_V_TYPE_CURRENT, SENSOR_V_TYPE_WATT, SENSOR_V_TYPE_KWH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p102")

PZEM_CMD_READ = 0x04
PZEM_CMD_RESET = 0x42
PZEM_CMD_SET_ADDR = 0x06


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
    PLUGIN_NAME = "Energy - PZEM-004T (AC) / PZEM-017 (DC)"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        mqtt_state_class=True,
    )

    PZEM_QUERIES = ["Voltage_V", "Current_A", "Power_W", "Energy_kWh", "Power_Factor_cosphi", "Frequency_Hz"]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x01
        self._pzem_type: int = 0
        self._values: list[float] = [0.0] * 6

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address") or 1)
        except (ValueError, TypeError):
            self._addr = 1
        try:
            self._pzem_type = int(self._config.get("pzem_type") or 0)
        except (ValueError, TypeError):
            self._pzem_type = 0
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

    async def _modbus_read(self, addr: int, reg: int, count: int) -> list[int] | None:
        if not self._hw or not self._hw.serial or not self._hw.serial.is_open:
            return None
        req = struct.pack(">BBHH", addr, PZEM_CMD_READ, reg, count)
        crc = _pzem_crc(req)
        req += struct.pack("<H", crc)
        try:
            await self._hw.serial.write(req)
            await asyncio.sleep(0.2)
            resp = await self._hw.serial.read(256)
            if len(resp) < 5:
                return None
            data_len = resp[2]
            if len(resp) < 3 + data_len + 2:
                return None
            vals = []
            for i in range(0, data_len, 2):
                vals.append((resp[3 + i] << 8) | resp[4 + i])
            return vals
        except Exception as e:
            logger.error("PZEM modbus read error: %s", e)
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._hw.serial or not self._hw.serial.is_open:
            return False
        vals = await self._modbus_read(self._addr, 0x00, 10)
        if not vals:
            return False
        v = vals[0] / 10.0
        c = ((vals[1] << 16) | vals[2]) / 1000.0
        p = ((vals[3] << 16) | vals[4]) / 10.0
        e = ((vals[5] << 16) | vals[6]) / 1000.0
        pf = vals[7] / 100.0
        f = vals[8] / 10.0

        queries = [
            int(self._config.get("query1") or 0),
            int(self._config.get("query2") or 1),
            int(self._config.get("query3") or 2),
            int(self._config.get("query4") or 3),
        ]
        all_vals = [v, c, p, e, pf, f]
        out = {}
        for i, q in enumerate(queries):
            if q < len(all_vals):
                out[self.PZEM_QUERIES[q]] = all_vals[q]
        event.data["values"] = out
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("resetenergy"):
            parts = command.split(",")
            addr = int(parts[1]) if len(parts) > 1 else self._addr
            if self._hw and self._hw.serial and self._hw.serial.is_open:
                req = struct.pack(">BBHH", addr, PZEM_CMD_RESET, 0x00, 0x00)
                crc = _pzem_crc(req)
                await self._hw.serial.write(req + struct.pack("<H", crc))
                return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("address", 1)
        self._config.setdefault("pzem_type", 0)
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
        form.append({"name": "baudrate", "label": "Baud Rate", "type": "number", "value": self._config.get("baudrate", 9600)})
        form.append({"name": "pzem_type", "label": "PZEM Model", "type": "select", "value": self._config.get("pzem_type", 0), "options": [
            {"value": 0, "label": "PZEM-004Tv30 (AC)"},
            {"value": 1, "label": "PZEM-017v1 (DC)"},
        ]})
        form.append({"name": "address", "label": "Modbus Address", "type": "number", "value": self._config.get("address", 1)})
        event.data["form"] = form
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        nr = 6 if self._pzem_type == 0 else 4
        opts = [{"value": i, "label": self.PZEM_QUERIES[i]} for i in range(nr)]
        event.data["output_selector"] = {
            "name": None,
            "label": "Output Values",
            "options": opts,
            "fields": [
                {"name": "query1", "label": "Value 1", "value": self._config.get("query1", 0)},
                {"name": "query2", "label": "Value 2", "value": self._config.get("query2", 1)},
                {"name": "query3", "label": "Value 3", "value": self._config.get("query3", 2)},
                {"name": "query4", "label": "Value 4", "value": self._config.get("query4", 3)},
            ],
        }
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
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
        PZEM_QUERIES = ["Voltage_V", "Current_A", "Power_W", "Energy_kWh", "Power_Factor_cosphi", "Frequency_Hz"]
        vtype_map = {
            "Voltage_V": SENSOR_V_TYPE_VOLTAGE,
            "Current_A": SENSOR_V_TYPE_CURRENT,
            "Power_W": SENSOR_V_TYPE_WATT,
            "Energy_kWh": SENSOR_V_TYPE_KWH,
            "Power_Factor_cosphi": SENSOR_V_TYPE_SINGLE,
            "Frequency_Hz": SENSOR_V_TYPE_SINGLE,
        }
        types = []
        for qi in range(1, 5):
            q = self._config.get(f"query{qi}", "")
            if q and q in vtype_map:
                types.append(vtype_map[q])
            else:
                types.append(SENSOR_V_TYPE_SINGLE)
        event.data["vtypes"] = types
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Voltage_V": 0.0, "Current_A": 0.0, "Power_W": 0.0, "Energy_kWh": 0.0}
