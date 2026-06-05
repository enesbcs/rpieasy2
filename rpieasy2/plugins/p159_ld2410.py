from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p159")


class P159LD2410(PluginBase):
    PLUGIN_ID = 159
    PLUGIN_NAME = "Presence - LD2410"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
        custom_vtype_var=True,
        exit_task_before_save=False,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._presence: int = 0
        self._distance: int = 0
        self._moving_energy: int = 0
        self._stationary_energy: int = 0
        self._buf = bytearray()

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
        try:
            self._serial = self._hw.serial.open(
                port=self._config.get("serial_port", "/dev/ttyAMA0"),
                baudrate=int(self._config.get("baudrate") or 256000),
                timeout=0,
            )
        except Exception as e:
            logger.error("LD2410 serial open failed: %s", e)
            self._serial = None

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if not self._serial:
            return None
        try:
            data = self._serial.read(256)
            if data:
                self._buf.extend(data)
                self._parse_frames()
        except Exception as e:
            logger.error("LD2410 read error: %s", e)
        return None

    def _parse_frames(self) -> None:
        while len(self._buf) >= 13:
            idx = self._buf.find(b"\xAA\xFF\xFF")
            if idx < 0:
                self._buf.clear()
                return
            if idx > 0:
                self._buf = self._buf[idx:]
            if len(self._buf) < 13:
                return
            frame_len = (self._buf[3] << 8) | self._buf[4]
            pkt_len = 5 + frame_len + 2
            if len(self._buf) < pkt_len:
                return
            frame = bytes(self._buf[:pkt_len])
            self._buf = self._buf[pkt_len:]
            if frame[5] == 0x01:
                self._presence = 1
                self._distance = frame[9] | (frame[10] << 8)
            elif frame[5] == 0x00:
                self._presence = 0
                self._distance = 0
            if len(frame) >= 19:
                self._moving_energy = frame[13]
                self._stationary_energy = frame[17]

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {
            "Presence": self._presence,
            "Distance": self._distance,
            "Stationary Energy": self._stationary_energy,
            "Moving Energy": self._moving_energy,
        }
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("ld2410"):
            parts = command.split(",")
            if len(parts) > 1 and parts[1].strip() == "factoryreset":
                if self._serial:
                    self._serial.write(b"\xAA\xFF\xFF\x02\x00\xF1\xF8")
                    await asyncio.sleep(0.1)
                return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 256000)
        self._config.setdefault("engineering_mode", False)
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        event.data["value_count"] = 9 if self._config.get("engineering_mode", False) else 4
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "serial_port", "label": "Serial Port", "type": "text", "value": self._config.get("serial_port", "/dev/ttyAMA0")},
            {"name": "baudrate", "label": "Baud Rate", "type": "number", "value": self._config.get("baudrate", 256000)},
            {"name": "engineering_mode", "label": "Engineering mode", "type": "checkbox", "value": self._config.get("engineering_mode", False)},
        ]
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
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH, SENSOR_V_TYPE_DISTANCE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SIGNAL_STRENGTH]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Presence": 0, "Distance": 0, "Stationary Energy": 0, "Moving Energy": 0}
