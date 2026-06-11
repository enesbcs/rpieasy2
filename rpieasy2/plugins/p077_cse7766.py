from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p077")

CSE_SYNC = 0x55


class P077CSE7766(PluginBase):
    PLUGIN_ID = 77
    PLUGIN_NAME = "Energy (AC) - CSE7766"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
        mqtt_state_class=True,
    )

    QUERY_VOLTAGE = 0
    QUERY_ACTIVE_POWER = 1
    QUERY_CURRENT = 2
    QUERY_PULSES = 3
    QUERY_APPARENT_POWER = 4
    QUERY_POWER_FACTOR = 5
    QUERY_KWH = 6

    QUERY_LABELS = ["Voltage_V", "Active_Power_W", "Current_A", "Pulses",
                    "Apparent_Power_VA", "Power_Factor", "Energy_kWh"]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._buf = bytearray()
        self._voltage: float = 0.0
        self._current: float = 0.0
        self._power: float = 0.0
        self._pulses: int = 0
        self._apparent_power: float = 0.0
        self._power_factor: float = 0.0
        self._cf_pulses: int = 0
        self._cf_frequency: int = 0
        self._uref: int = 0
        self._iref: int = 0
        self._pref: int = 0
        self._port: str = "/dev/ttyAMA0"
        self._query_config: list[int] = [0, 1, 2, 3]

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        try:
            self._uref = int(self._config.get("uref", 0))
        except (ValueError, TypeError):
            self._uref = 0
        try:
            self._iref = int(self._config.get("iref", 0))
        except (ValueError, TypeError):
            self._iref = 0
        try:
            self._pref = int(self._config.get("pref", 0))
        except (ValueError, TypeError):
            self._pref = 0
        for i in range(4):
            try:
                self._query_config[i] = int(self._config.get(f"query{i + 1}", i))
            except (ValueError, TypeError):
                self._query_config[i] = i
        return await self._setup_serial()

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=4800,
                bytesize=pyserial.EIGHTBITS,
                parity=pyserial.PARITY_EVEN,
                stopbits=pyserial.STOPBITS_ONE,
                timeout=0,
            )
            return True
        except Exception as e:
            logger.error("CSE7766 serial init failed: %s", e)
            self._serial = None
            return False

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._serial or not self._serial.is_open:
            return None
        try:
            if self._serial.in_waiting:
                data = await asyncio.to_thread(self._serial.read, self._serial.in_waiting)
                self._buf.extend(data)
                self._process_serial(event)
        except Exception as e:
            logger.error("CSE7766 read error: %s", e)
        return None

    def _process_serial(self, event: Event) -> None:
        while len(self._buf) >= 24:
            idx = self._buf.find(b"\x55")
            if idx < 0:
                self._buf.clear()
                return
            if idx > 0:
                self._buf = self._buf[idx:]
            if len(self._buf) < 24:
                return
            packet = bytes(self._buf[:24])
            self._buf = self._buf[24:]

            if packet[0] != CSE_SYNC:
                continue
            if packet[1] != CSE_SYNC:
                continue

            checksum = sum(packet[:23]) & 0xFF
            if checksum != packet[23]:
                continue

            v = ((packet[2] << 16) | (packet[3] << 8) | packet[4])
            if v != 0xFFFFFF:
                self._voltage = v / 1000.0

            c = ((packet[5] << 16) | (packet[6] << 8) | packet[7])
            if c != 0xFFFFFF:
                self._current = c / 1000.0

            p = ((packet[8] << 16) | (packet[9] << 8) | packet[10])
            if p != 0xFFFFFF:
                self._power = p / 1000.0

            self._pulses = (packet[14] << 8) | packet[15]
            self._cf_frequency = (packet[20] << 8) | packet[21]

            if self._cf_frequency > 0:
                self._cf_pulses += 1

            if self._voltage > 0 and self._current > 0:
                self._apparent_power = self._voltage * self._current
                if self._apparent_power > 0:
                    self._power_factor = self._power / self._apparent_power
                    if self._power_factor > 1.0:
                        self._power_factor = 1.0
                    if self._power_factor < 0.0:
                        self._power_factor = 0.0

    async def on_plugin_read(self, event: Event) -> bool | None:
        out = {}
        for i, q in enumerate(self._query_config):
            if q == self.QUERY_VOLTAGE:
                out[self.QUERY_LABELS[q]] = self._voltage
            elif q == self.QUERY_ACTIVE_POWER:
                out[self.QUERY_LABELS[q]] = self._power
            elif q == self.QUERY_CURRENT:
                out[self.QUERY_LABELS[q]] = self._current
            elif q == self.QUERY_PULSES:
                out[self.QUERY_LABELS[q]] = float(self._pulses)
            elif q == self.QUERY_APPARENT_POWER:
                out[self.QUERY_LABELS[q]] = self._apparent_power
            elif q == self.QUERY_POWER_FACTOR:
                out[self.QUERY_LABELS[q]] = self._power_factor
            elif q == self.QUERY_KWH:
                pulses_per_kwh = self._cf_frequency * 3600 if self._cf_frequency > 0 else 1
                kwh = self._cf_pulses / pulses_per_kwh
                out[self.QUERY_LABELS[q]] = kwh
            else:
                out[self.QUERY_LABELS[q]] = 0.0
        event.data["values"] = out
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command == "csereset":
            self._cf_pulses = 0
            logger.info("CSE: pulses reset")
            return True
        if command.startswith("csecalibrate"):
            parts = command.split(",")
            if len(parts) >= 4:
                self._uref = int(parts[1])
                self._iref = int(parts[2])
                self._pref = int(parts[3])
                self._config["uref"] = self._uref
                self._config["iref"] = self._iref
                self._config["pref"] = self._pref
                logger.info("CSE: calibrated U=%d I=%d P=%d", self._uref, self._iref, self._pref)
                return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("query1", 0)
        self._config.setdefault("query2", 1)
        self._config.setdefault("query3", 2)
        self._config.setdefault("query4", 3)
        self._config.setdefault("uref", 0)
        self._config.setdefault("iref", 0)
        self._config.setdefault("pref", 0)
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
        form.append({"name": "uref", "label": "U Ref (uSec)", "type": "number",
                     "value": self._config.get("uref", 0)})
        form.append({"name": "iref", "label": "I Ref (uSec)", "type": "number",
                     "value": self._config.get("iref", 0)})
        form.append({"name": "pref", "label": "P Ref (uSec)", "type": "number",
                     "value": self._config.get("pref", 0)})
        event.data["form"] = form
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        opts = [{"value": i, "label": self.QUERY_LABELS[i]} for i in range(len(self.QUERY_LABELS))]
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
        for i in range(4):
            self._query_config[i] = int(self._config.get(f"query{i + 1}", i))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Voltage_V": 0.0, "Active_Power_W": 0.0, "Current_A": 0.0, "Pulses": 0.0}
