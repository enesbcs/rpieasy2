from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p049")

CMD_READ_CO2 = b"\xFF\x01\x86\x00\x00\x00\x00\x00\x79"
CMD_ABC_ON = b"\xFF\x01\x79\xA0\x00\x00\x00\x00\xE6"
CMD_ABC_OFF = b"\xFF\x01\x79\x00\x00\x00\x00\x00\x86"
CMD_CALIBRATE_ZERO = b"\xFF\x01\x87\x00\x00\x00\x00\x00\x78"
CMD_RESET = b"\xFF\x01\x8D\x00\x00\x00\x00\x00\x72"
CMD_RANGE_2000 = b"\xFF\x01\x99\x00\x00\x00\x07\xD0\x8F"
CMD_RANGE_5000 = b"\xFF\x01\x99\x00\x00\x00\x13\x88\xCB"


class P049MHZ19(PluginBase):
    PLUGIN_ID = 49
    PLUGIN_NAME = "Gases - CO2 MH-Z19"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_SERIAL, vtype=SENSOR_TYPE_TRIPLE, value_count=3, formula_option=True, send_data_option=True, plugin_stats=True, exit_task_before_save=False)

    def __init__(self):
        super().__init__()
        self._serial: Any = None
        self._config: dict[str, Any] = {}
        self._last_good: dict[str, float] = {}
        self._buf: deque = deque(maxlen=5)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        port = self._config.get("serial_port", "/dev/ttyAMA0")
        if self._hw and self._hw.serial:
            ok = await self._hw.serial.open(port, baud=9600, timeout=1)
            if ok:
                self._serial = self._hw.serial
                return True
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(port=port, baudrate=9600,
                                           bytesize=pyserial.EIGHTBITS,
                                           parity=pyserial.PARITY_NONE,
                                           stopbits=pyserial.STOPBITS_ONE, timeout=1)
        except Exception as e:
            logger.error("MH-Z19 init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _send_cmd(self, cmd: bytes) -> bytes | None:
        if not self._serial:
            return None
        try:
            if hasattr(self._serial, "write"):
                await asyncio.to_thread(self._serial.write, cmd)
            else:
                await self._serial.write(cmd)
            await asyncio.sleep(0.1)
            if hasattr(self._serial, "read"):
                resp = await asyncio.to_thread(self._serial.read, 9)
            else:
                resp = await self._serial.read(9)
            if len(resp) == 9 and resp[0] == 0xFF and resp[1] == 0x86:
                if sum(resp[:8]) & 0xFF == resp[8]:
                    return resp
            return None
        except Exception as e:
            logger.error("MH-Z19 command failed: %s", e)
            return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._serial:
            return False
        try:
            resp = await self._send_cmd(CMD_READ_CO2)
            if resp:
                co2 = resp[2] * 256 + resp[3]
                temp = resp[4] - 40
                status = resp[5]
                vals: dict[str, Any] = {"CO2": co2, "Temperature": temp, "U": status}
                self._last_good = {"CO2": co2, "Temperature": temp, "U": status}
                flt = int(self._config.get("filter") or 0)
                if flt >= 2:
                    self._buf.append(co2)
                    if len(self._buf) >= 3:
                        if flt == 2:
                            vals["CO2"] = round(sum(self._buf) / len(self._buf))
                        elif flt == 3:
                            s = sorted(self._buf)
                            vals["CO2"] = s[len(s) // 2]
                        elif flt == 4:
                            s = sorted(self._buf)
                            trim = max(1, len(s) // 4)
                            vals["CO2"] = round(sum(s[trim:-trim]) / max(1, len(s) - 2 * trim))
                event.data["values"] = vals
                return True
            if self._config.get("filter", 0) == 0 and self._last_good:
                return False
            if self._last_good:
                event.data["values"] = dict(self._last_good)
                return True
            return False
        except Exception as e:
            logger.error("MH-Z19 read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "")
        if command == "abcdisable":
            await self._send_cmd(CMD_ABC_OFF)
            return True
        if command == "abcenable":
            await self._send_cmd(CMD_ABC_ON)
            return True
        if command == "mhzcalibratezero":
            await self._send_cmd(CMD_CALIBRATE_ZERO)
            return True
        if command == "mhzreset":
            await self._send_cmd(CMD_RESET)
            return True
        if command == "mhzmeasurementrange2000":
            await self._send_cmd(CMD_RANGE_2000)
            return True
        if command == "mhzmeasurementrange5000":
            await self._send_cmd(CMD_RANGE_5000)
            return True
        return None

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["CO2", "Temperature", "U"]
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
        form.append({"name": "rx_pin", "label": "Serial RX GPIO", "type": "number", "value": self._config.get("rx_pin", "")})
        form.append({"name": "tx_pin", "label": "Serial TX GPIO", "type": "number", "value": self._config.get("tx_pin", "")})
        form.append({"name": "abc_disabled", "label": "ABC Disabled", "type": "checkbox", "value": self._config.get("abc_disabled", False)})
        form.append({"name": "filter", "label": "Filter", "type": "select", "value": self._config.get("filter", 0), "options": [
            {"value": 0, "label": "Skip Unstable"},
            {"value": 1, "label": "Use Unstable"},
            {"value": 2, "label": "Fast"},
            {"value": 3, "label": "Medium"},
            {"value": 4, "label": "Slow"},
        ]})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        abc = bool(self._config.get("abc_disabled", False))
        if abc:
            await self._send_cmd(CMD_ABC_OFF)
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_CO2, SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"CO2": 0, "Temperature": 0, "U": 0}
