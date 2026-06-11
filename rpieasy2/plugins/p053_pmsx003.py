from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p053")

PMS_START_BYTE1 = 0x42
PMS_START_BYTE2 = 0x4D
PMS_PACKET_LEN = 32

PMS_INDICES = {
    "pm1_0": 4,
    "pm2_5": 5,
    "pm10": 6,
}

OUTPUT_SELECTORS = {
    0: ["pm1.0", "pm2.5", "pm10"],
}


class P053PMSx003(PluginBase):
    PLUGIN_ID = 53
    PLUGIN_NAME = "Dust - PMSx003"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_TRIPLE,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._buf = bytearray()
        self._values: dict[str, float] = {}
        self._rst_pin: int = -1
        self._pwr_pin: int = -1
        self._values_received = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        if not self._hw:
            return False
        await self._setup_serial()
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> None:
        self._rst_pin = int(self._config.get("rst_pin") or -1)
        self._pwr_pin = int(self._config.get("pwr_pin") or -1)
        if self._pwr_pin > 0:
            try:
                self._hw.gpio.claim_output(self._pwr_pin)
                self._hw.gpio.write(self._pwr_pin, 1)
            except Exception:
                self._pwr_pin = -1
        if self._rst_pin > 0:
            try:
                self._hw.gpio.claim_output(self._rst_pin)
                self._hw.gpio.write(self._rst_pin, 1)
            except Exception:
                self._rst_pin = -1
        await asyncio.sleep(0.01)
        try:
            self._serial = self._hw.serial.open(
                port=self._config.get("serial_port", "/dev/ttyAMA0"),
                baudrate=int(self._config.get("baudrate") or 9600),
                timeout=0,
            )
        except Exception as e:
            logger.error("PMSx003 serial open failed: %s", e)
            self._serial = None

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._serial:
            return None
        try:
            data = self._serial.read(128)
            if data:
                self._buf.extend(data)
                self._parse_packets()
        except Exception as e:
            logger.error("PMSx003 read error: %s", e)
        return None

    def _parse_packets(self) -> None:
        while len(self._buf) >= 4:
            if self._buf[0] != PMS_START_BYTE1 or self._buf[1] != PMS_START_BYTE2:
                self._buf.pop(0)
                continue
            length = (self._buf[2] << 8) | self._buf[3]
            pkt_len = length + 4
            if len(self._buf) < pkt_len:
                break
            frame = bytes(self._buf[:pkt_len])
            self._buf = self._buf[pkt_len:]
            self._process_frame(frame)

    def _process_frame(self, frame: bytes) -> None:
        if len(frame) < 32:
            return
        calc_cksum = sum(frame[:30]) & 0xFFFF
        frame_cksum = (frame[30] << 8) | frame[31]
        if calc_cksum != frame_cksum:
            logger.debug("PMSx003 CRC failed")
            return
        self._values = {}
        fmt = ">HHHHHHHHHHHHHHH"
        vals = struct.unpack(fmt, frame[4:34])
        self._values["pm1_0"] = round(vals[0] / 10.0, 1)
        self._values["pm2_5"] = round(vals[1] / 10.0, 1)
        self._values["pm10"] = round(vals[2] / 10.0, 1)
        self._values_received = True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._values_received:
            return False
        self._values_received = False
        output = int(self._config.get("output_selector") or 0)
        if output == 0:
            event.data["values"] = {
                "pm1.0": self._values.get("pm1_0", 0),
                "pm2.5": self._values.get("pm2_5", 0),
                "pm10": self._values.get("pm10", 0),
            }
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",", 1)
        cmd = parts[0]
        if cmd == "pmsx003" and len(parts) > 1:
            sub = parts[1].strip()
            if sub == "wake":
                await self._wake()
                return True
            if sub == "sleep":
                await self._sleep()
                return True
            if sub == "reset":
                await self._reset()
                return True
        return False

    async def _wake(self) -> None:
        if self._pwr_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._pwr_pin, 1)
            except Exception:
                pass
        delay = int(self._config.get("wake_delay") or 0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _sleep(self) -> None:
        if self._pwr_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._pwr_pin, 0)
            except Exception:
                pass

    async def _reset(self) -> None:
        if self._rst_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._rst_pin, 0)
                await asyncio.sleep(0.5)
                self._hw.gpio.write(self._rst_pin, 1)
            except Exception:
                pass

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("output_selector", 0)
        self._config.setdefault("wake_delay", 0)
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
        form.append({"name": "output_selector", "label": "Output Values", "type": "select", "value": self._config.get("output_selector", 0), "options": [
            {"value": 0, "label": "Particles (PM1.0, PM2.5, PM10)"},
        ]})
        form.append({"name": "rst_pin", "label": "RST Pin", "type": "number", "value": self._config.get("rst_pin", -1)})
        form.append({"name": "pwr_pin", "label": "SET/PWR Pin", "type": "number", "value": self._config.get("pwr_pin", -1)})
        form.append({"name": "wake_delay", "label": "Sensor init time after wake (sec)", "type": "number", "value": self._config.get("wake_delay", 0)})
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

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_PM1_0, SENSOR_V_TYPE_PM2_5, SENSOR_V_TYPE_PM10]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"pm1.0": 0, "pm2.5": 0, "pm10": 0}
