from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p056")

SDS_HEAD = 0xAA
SDS_TAIL = 0xAB
SDS_CMD_MODE_1 = b"\xAA\xB4\x06\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\xFF\x06\xAB"
SDS_CMD_MODE_0 = b"\xAA\xB4\x06\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\xFF\x05\xAB"
SDS_CMD_SLEEP = b"\xAA\xB4\x06\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\xFF\x05\xAB"
SDS_CMD_WAKE = b"\xAA\xB4\x06\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\xFF\x06\xAB"
SDS_CMD_QUERY = b"\xAA\xB4\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xFF\xFF\x05\xAB"
SDS_CMD_SET_PERIOD = 0x08


def _sds_checksum(data: bytes) -> int:
    return sum(data[2:10]) & 0xFF


class P056SDS011(PluginBase):
    PLUGIN_ID = 56
    PLUGIN_NAME = "Dust - SDS011/018/198"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_DUAL,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._buf = bytearray()
        self._pm2_5: float = 0.0
        self._pm10: float = 0.0
        self._data_available = False
        self._readings: list[tuple[float, float]] = []
        self._port: str = "/dev/ttyAMA0"
        self._sleep_minutes: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        try:
            self._sleep_minutes = int(self._config.get("sleep_time", 0))
        except (ValueError, TypeError):
            self._sleep_minutes = 0
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
            logger.error("SDS011 serial init failed: %s", e)
            self._serial = None
            return False

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def _send_cmd(self, cmd: bytes) -> None:
        if not self._serial:
            return
        try:
            await asyncio.to_thread(self._serial.write, cmd)
        except Exception as e:
            logger.error("SDS011 cmd error: %s", e)

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if not self._serial or not self._serial.is_open:
            return None
        try:
            if self._serial.in_waiting:
                data = await asyncio.to_thread(self._serial.read, self._serial.in_waiting)
                self._buf.extend(data)
                self._parse_frame()
        except Exception as e:
            logger.error("SDS011 read error: %s", e)
        return None

    def _parse_frame(self) -> None:
        while len(self._buf) >= 10:
            if self._buf[0] != SDS_HEAD:
                self._buf.pop(0)
                continue
            if len(self._buf) < 10:
                return
            if self._buf[1] != 0xC0:
                self._buf.pop(0)
                continue
            checksum = sum(self._buf[2:8]) & 0xFF
            if checksum != self._buf[8]:
                self._buf.pop(0)
                continue
            if self._buf[9] != SDS_TAIL:
                self._buf.pop(0)
                continue
            pm2_5 = ((self._buf[3] << 8) | self._buf[2]) / 10.0
            pm10 = ((self._buf[5] << 8) | self._buf[4]) / 10.0
            self._readings.append((pm2_5, pm10))
            self._buf = self._buf[10:]

    async def _read_average(self) -> tuple[float, float]:
        if not self._readings:
            return (0.0, 0.0)
        avg_pm25 = sum(r[0] for r in self._readings) / len(self._readings)
        avg_pm10 = sum(r[1] for r in self._readings) / len(self._readings)
        self._readings.clear()
        return (avg_pm25, avg_pm10)

    async def on_plugin_read(self, event: Event) -> bool | None:
        pm25, pm10 = await self._read_average()
        if pm25 > 0 or pm10 > 0:
            event.data["values"] = {"PM2.5": pm25, "PM10": pm10}
            return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("sleep_time", 0)
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
        form.append({"name": "sleep_time", "label": "Sleep time (minutes, 0=continuous)", "type": "number",
                     "value": self._config.get("sleep_time", 0), "min": 0, "max": 30})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin (optional)", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"PM2.5": 0.0, "PM10": 0.0}
