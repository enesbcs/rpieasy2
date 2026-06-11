from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_ULONG
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p040")


class P040ID12(PluginBase):
    PLUGIN_ID = 40
    PLUGIN_NAME = "RFID - ID12LA/RDM6300"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_ULONG,
        value_count=1,
        send_data_option=True,
        custom_vtype_var=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._buf = bytearray()
        self._last_tag: int = 0
        self._port: str = "/dev/ttyAMA0"

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        return await self._setup_serial()

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=9600,
                bytesize=pyserial.EIGHTBITS,
                parity=pyserial.PARITY_NONE,
                stopbits=pyserial.STOPBITS_ONE,
                timeout=0,
            )
            return True
        except Exception as e:
            logger.error("ID12 serial init failed: %s", e)
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
                self._parse_tag(event)
        except Exception as e:
            logger.error("ID12 serial read error: %s", e)
        return None

    def _hex_val(self, c: int) -> int:
        if ord("0") <= c <= ord("9"):
            return c - ord("0")
        if ord("A") <= c <= ord("F"):
            return 10 + c - ord("A")
        if ord("a") <= c <= ord("f"):
            return 10 + c - ord("a")
        return 0

    def _parse_tag(self, event: Event) -> None:
        while len(self._buf) > 0:
            if self._buf[0] != 0x02:
                self._buf.pop(0)
                continue
            idx = self._buf.find(b"\x02")
            if idx > 0:
                self._buf = self._buf[idx:]
            if len(self._buf) < 13:
                return
            code = [0] * 6
            checksum = 0
            bytesread = 0
            i = 1
            while bytesread < 12 and i < len(self._buf):
                val = self._buf[i]
                if val in (0x0D, 0x0A, 0x03, 0x02):
                    break
                if bytesread & 1:
                    code[bytesread >> 1] = val | (self._buf[i - 1] << 4)
                    if bytesread >> 1 != 5:
                        checksum ^= code[bytesread >> 1]
                bytesread += 1
                i += 1
            consumed = i
            if bytesread == 12:
                if code[5] == checksum:
                    key = 0
                    for j in range(1, 5):
                        key = key | (code[j] << ((4 - j) * 8))
                    if key != self._last_tag:
                        self._last_tag = key
                        logger.info("RFID: New Tag: %d", key)
                        event.data["values"] = {"Tag": float(key)}
                        return True
            self._buf = self._buf[consumed:]
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
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
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Tag": 0.0}
