from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_STRING
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p524")


class P524GenSerial(PluginBase):
    PLUGIN_ID = 524
    PLUGIN_NAME = "Communication - Serial"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_STRING,
        value_count=1,
        send_data_option=True,
        timer_option=False,
        timer_optional=True,
        formula_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._port: str = "/dev/ttyAMA0"
        self._baud: int = 9600
        self._bytesize: int = 8
        self._parity: str = "N"
        self._stopbits: float = 1.0
        self._timeout: float = 0.001
        self._max_packet: int = 512
        self._input_format: int = 0
        self._buf = bytearray()
        self._reader_task: asyncio.Task | None = None
        self._reader_running = False
        self._last_data: str = ""

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        try:
            self._baud = int(self._config.get("baudrate", 9600))
        except (ValueError, TypeError):
            self._baud = 9600
        try:
            self._bytesize = int(self._config.get("bytesize", 8))
        except (ValueError, TypeError):
            self._bytesize = 8
        self._parity = self._config.get("parity", "N")
        try:
            self._stopbits = float(self._config.get("stopbits", 1.0))
        except (ValueError, TypeError):
            self._stopbits = 1.0
        try:
            self._max_packet = int(self._config.get("max_packet", 512))
        except (ValueError, TypeError):
            self._max_packet = 512
        try:
            self._input_format = int(self._config.get("input_format", 0))
        except (ValueError, TypeError):
            self._input_format = 0
        self._recalc_timeout()
        ok = await self._setup_serial()
        if ok:
            self._start_reader()
        return ok

    async def on_plugin_exit(self, event: Event) -> bool | None:
        self._stop_reader()
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        return True

    def _recalc_timeout(self) -> None:
        if self._baud < 50:
            self._baud = 50
        mpk = self._max_packet
        if mpk > 4096:
            mpk = 4096
        if mpk < 1:
            mpk = 1
        self._timeout = (self._bytesize + self._stopbits) * mpk / self._baud

    def _map_parity(self) -> str:
        return self._parity

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            parity_map = {"N": pyserial.PARITY_NONE, "E": pyserial.PARITY_EVEN,
                          "O": pyserial.PARITY_ODD, "M": pyserial.PARITY_MARK,
                          "S": pyserial.PARITY_SPACE}
            bsize_map = {5: pyserial.FIVEBITS, 6: pyserial.SIXBITS,
                         7: pyserial.SEVENBITS, 8: pyserial.EIGHTBITS}
            sbit_map = {1: pyserial.STOPBITS_ONE, 2: pyserial.STOPBITS_TWO}
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=bsize_map.get(self._bytesize, pyserial.EIGHTBITS),
                parity=parity_map.get(self._parity, pyserial.PARITY_NONE),
                stopbits=sbit_map.get(int(self._stopbits), pyserial.STOPBITS_ONE),
                timeout=self._timeout,
            )
            return True
        except Exception as e:
            logger.error("Serial init failed: %s", e)
            self._serial = None
            return False

    def _start_reader(self) -> None:
        if self._reader_task is not None:
            return
        self._reader_running = True
        self._reader_task = asyncio.create_task(self._serial_reader())

    def _stop_reader(self) -> None:
        self._reader_running = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

    async def _serial_reader(self) -> None:
        reconnect_wait = 0.0
        while self._reader_running:
            if not self._serial or not self._serial.is_open:
                await asyncio.sleep(0.1)
                continue
            try:
                if self._serial.in_waiting:
                    data = await asyncio.to_thread(
                        self._serial.read, self._serial.in_waiting
                    )
                    if data:
                        self._buf.extend(data)
                else:
                    await asyncio.sleep(0.001)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Serial reader error: %s", e)
                await asyncio.sleep(0.5)
                reconnect_wait += 0.5
                if reconnect_wait >= 10.0:
                    reconnect_wait = 0.0
                    try:
                        if self._serial and not self._serial.is_open:
                            await asyncio.to_thread(self._serial.close)
                    except Exception:
                        pass
                    self._setup_serial()

    def _convert(self, data: bytearray) -> str:
        if not data:
            return ""
        if self._input_format == 1:
            try:
                return data.decode("utf-8", errors="ignore").strip()
            except Exception:
                return ""
        res = ""
        for b in data:
            res += f"0x{b:02X}"
        return res

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if not self._buf:
            return None
        data = bytearray(self._buf)
        self._buf.clear()
        converted = self._convert(data)
        if converted and converted != "0x":
            self._last_data = converted
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._last_data:
            return False
        event.data["values"] = {"Data": self._last_data}
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0] if parts else ""
        sepp = command.find(",")
        text = command[sepp + 1:].strip() if sepp >= 0 else ""

        if cmd not in ("serialwrite", "serialwriteln"):
            return False

        sbuf = []
        if text[:2] == "0x":
            text = text[2:]
            try:
                text = "".join(text.split(" "))
                sbuf = bytes(int(text[i:i + 2], 16) for i in range(0, len(text), 2))
            except Exception:
                sbuf = text.encode()
        else:
            sbuf = text.encode()

        if cmd == "serialwriteln":
            sbuf += b"\n"

        if not self._serial or not self._serial.is_open:
            return False

        try:
            written = await asyncio.to_thread(self._serial.write, sbuf)
            return written > 0
        except Exception as e:
            logger.error("Serial write error: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("bytesize", 8)
        self._config.setdefault("parity", "N")
        self._config.setdefault("stopbits", 1.0)
        self._config.setdefault("max_packet", 512)
        self._config.setdefault("input_format", 0)
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
        form.append({"name": "baudrate", "label": "Baudrate", "type": "number",
                     "value": self._config.get("baudrate", 9600), "min": 50, "max": 4000000})
        form.append({"name": "bytesize", "label": "Bytesize", "type": "select",
                     "value": self._config.get("bytesize", 8), "options": [
                         {"value": 5, "label": "5"}, {"value": 6, "label": "6"},
                         {"value": 7, "label": "7"}, {"value": 8, "label": "8"}]})
        form.append({"name": "parity", "label": "Parity", "type": "select",
                     "value": self._config.get("parity", "N"), "options": [
                         {"value": "N", "label": "None"}, {"value": "E", "label": "Even"},
                         {"value": "O", "label": "Odd"}, {"value": "M", "label": "Mark"},
                         {"value": "S", "label": "Space"}]})
        form.append({"name": "stopbits", "label": "Stopbits", "type": "select",
                     "value": self._config.get("stopbits", 1.0), "options": [
                         {"value": 1, "label": "1"}, {"value": 2, "label": "2"}]})
        form.append({"name": "max_packet", "label": "Expected max packet size", "type": "number",
                     "value": self._config.get("max_packet", 512), "min": 1, "max": 4096})
        form.append({"name": "input_format", "label": "Input Data format", "type": "select",
                     "value": self._config.get("input_format", 0), "options": [
                         {"value": 0, "label": "Hex values"},
                         {"value": 1, "label": "String"}]})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        try:
            self._baud = int(self._config.get("baudrate", 9600))
        except (ValueError, TypeError):
            self._baud = 9600
        try:
            self._bytesize = int(self._config.get("bytesize", 8))
        except (ValueError, TypeError):
            self._bytesize = 8
        self._parity = self._config.get("parity", "N")
        try:
            self._stopbits = float(self._config.get("stopbits", 1.0))
        except (ValueError, TypeError):
            self._stopbits = 1.0
        try:
            self._max_packet = int(self._config.get("max_packet", 512))
        except (ValueError, TypeError):
            self._max_packet = 512
        try:
            self._input_format = int(self._config.get("input_format", 0))
        except (ValueError, TypeError):
            self._input_format = 0
        self._recalc_timeout()
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Data"]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Data": ""}
