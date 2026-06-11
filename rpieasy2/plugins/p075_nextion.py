from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p075")

P75_NLINES = 20
P75_NCHARS = 80
NEXTION_END = b"\xFF\xFF\xFF"
TOUCH_BASE = 0x1000


class P075Nextion(PluginBase):
    PLUGIN_ID = 75
    PLUGIN_NAME = "Display - Nextion"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_DUAL,
        value_count=2,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._rx_pin: int = -1
        self._tx_pin: int = -1
        self._baud: int = 9600
        self._display_lines: list[str] = [""] * P75_NLINES
        self._include_values: bool = False
        self._touch_idx: float = 0.0
        self._touch_value: float = 0.0
        self._buf = bytearray()
        self._port: str = "/dev/ttyAMA0"

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._baud = int(self._config.get("baudrate", 9600))
        except (ValueError, TypeError):
            self._baud = 9600
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        self._include_values = self._config.get("include_values", False)
        raw_lines = self._config.get("display_lines", [""] * P75_NLINES)
        for i in range(min(P75_NLINES, len(raw_lines))):
            self._display_lines[i] = str(raw_lines[i])
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
                timeout=0,
            )
            return True
        except Exception as e:
            logger.error("Nextion serial init failed: %s", e)
            self._serial = None
            return False

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def _send_command(self, cmd: str) -> None:
        if not self._serial:
            return
        try:
            data = cmd.encode("utf-8") + NEXTION_END
            await asyncio.to_thread(self._serial.write, data)
        except Exception as e:
            logger.error("Nextion send error: %s", e)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._serial:
            return False
        for x, line in enumerate(self._display_lines):
            if line:
                line_lower = line.upper()
                if "RSSIBAR" in line_lower:
                    import random
                    rssi_quality = random.randint(0, 100)
                    new_string = line[:line_lower.index("RSSIBAR")] + str(rssi_quality * 10)
                else:
                    new_string = line
                await self._send_command(new_string)
                await asyncio.sleep(0.005)
        if self._include_values:
            event.data["values"] = {"idx": self._touch_idx, "value": self._touch_value}
            return True
        return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip()
        parts = command.split(",")
        device_name = self._config.get("task_device_name", "NEXTION")
        if parts[0].upper() == device_name.upper():
            args = ",".join(parts[1:])
            await self._send_command(args)
            logger.debug("Nextion WRITE: %s", args)
            return True
        return False

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._serial or not self._serial.is_open:
            return None
        try:
            if self._serial.in_waiting:
                data = await asyncio.to_thread(self._serial.read, self._serial.in_waiting)
                self._buf.extend(data)
                self._parse_frames(event)
        except Exception as e:
            logger.error("Nextion serial read error: %s", e)
        return None

    def _parse_frames(self, event: Event) -> None:
        while len(self._buf) >= 7:
            if self._buf[0] == 0x65:
                if len(self._buf) >= 7:
                    if (self._buf[4] == 0xFF and self._buf[5] == 0xFF and self._buf[6] == 0xFF):
                        idx = (self._buf[1] << 8) | self._buf[2]
                        val = self._buf[3]
                        self._touch_idx = float(idx + TOUCH_BASE)
                        self._touch_value = float(val)
                        event.data["values"] = {"idx": self._touch_idx, "value": self._touch_value}
                        self._buf = self._buf[7:]
                        continue
            elif self._buf[0] == ord("|"):
                end_idx = self._buf.find(b"\x0A")
                if end_idx >= 0:
                    line = self._buf[1:end_idx].decode("utf-8", errors="replace")
                    self._parse_pipe_command(line, event)
                    self._buf = self._buf[end_idx + 1:]
                    continue
            self._buf.pop(0)

    def _parse_pipe_command(self, line: str, event: Event) -> None:
        parts = line.split(",")
        if not parts:
            return
        if line.startswith("u,"):
            vidx = ""
            nvalue = ""
            svalue = ""
            for p in parts:
                if p.startswith("i"):
                    vidx = p[1:]
                elif "=" in p:
                    kv = p.split("=", 1)
                    if kv[0] == "n":
                        nvalue = kv[1]
                    elif kv[0] == "s":
                        svalue = kv[1]
            if vidx:
                self._touch_idx = float(vidx)
                self._touch_value = float(svalue) if svalue else 0.0
                event.data["values"] = {"idx": self._touch_idx, "value": self._touch_value}
        elif line.startswith("s,"):
            for p in parts:
                if p.startswith("i"):
                    vidx_str = p[1:]
                    self._touch_idx = float(vidx_str) if vidx_str else 0.0
                elif "=" in p:
                    kv = p.split("=", 1)
                    if kv[0] == "n":
                        nv = kv[1]
                        sv = "1" if nv.upper() == "ON" else ("0" if nv.upper() == "OFF" else nv)
                        self._touch_value = float(sv) if sv else 0.0
                        event.data["values"] = {"idx": self._touch_idx, "value": self._touch_value}

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("include_values", False)
        self._config.setdefault("display_lines", [""] * P75_NLINES)
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
        form.append({"name": "baudrate", "label": "Baud Rate", "type": "select",
                     "value": self._config.get("baudrate", 9600),
                     "options": [
                         {"value": 9600, "label": "9600"},
                         {"value": 38400, "label": "38400"},
                         {"value": 57600, "label": "57600"},
                         {"value": 115200, "label": "115200"},
                     ]})
        form.append({"name": "include_values", "label": "Resend Values at Interval", "type": "checkbox",
                     "value": self._config.get("include_values", False)})
        event.data["form"] = form
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

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"idx": 0.0, "value": 0.0}
