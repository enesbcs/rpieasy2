from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SPI, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p039")

MAX6675_TYPE = 6675
MAX31855_TYPE = 31855


class P039ThermoSensors(PluginBase):
    PLUGIN_ID = 39
    PLUGIN_NAME = "Environment - Thermosensors"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SPI,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=1,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._cs_pin: int = -1
        self._spi_bus: int = 0
        self._spi_dev: int = 0
        self._sensor_type: int = MAX6675_TYPE
        self._samples: int = 3
        self._sample_buf: list[float] = []

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._cs_pin = int(self._config.get("cs_pin", -1))
        except (ValueError, TypeError):
            self._cs_pin = -1
        try:
            self._spi_bus = int(self._config.get("spi_bus", 0))
        except (ValueError, TypeError):
            self._spi_bus = 0
        try:
            self._spi_dev = int(self._config.get("spi_dev", 0))
        except (ValueError, TypeError):
            self._spi_dev = 0
        try:
            self._sensor_type = int(self._config.get("sensor_type", MAX6675_TYPE))
        except (ValueError, TypeError):
            self._sensor_type = MAX6675_TYPE
        try:
            self._samples = int(self._config.get("samples", 3))
        except (ValueError, TypeError):
            self._samples = 3
        self._sample_buf = []
        if self._hw:
            if self._cs_pin >= 0:
                self._hw.gpio.claim_output(self._cs_pin)
                self._hw.gpio.write(self._cs_pin, 1)
        return True

    async def _spi_read(self, count: int) -> list[int]:
        if not self._hw:
            return [0] * count
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 0)
        await asyncio.sleep(0.001)
        result = self._hw.spi.transfer(self._spi_bus, self._spi_dev, [0x00] * count, speed_hz=3900000)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 1)
        return result

    async def _read_max6675(self) -> float | None:
        data = await self._spi_read(2)
        if len(data) < 2:
            return None
        raw = (data[0] << 8) | data[1]
        if raw & 0x04:
            logger.warning("MAX6675 thermocouple open")
            return None
        if raw & 0x02:
            logger.warning("MAX6675 device ID error")
            return None
        temp = ((raw >> 3) & 0xFFF) * 0.25
        return temp

    async def _read_max31855(self) -> float | None:
        data = await self._spi_read(4)
        if len(data) < 4:
            return None
        raw = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        if raw & 0x00010000:
            logger.warning("MAX31855 thermocouple open")
            return None
        if raw & 0x00020000:
            logger.warning("MAX31855 thermocouple short to GND")
            return None
        if raw & 0x00040000:
            logger.warning("MAX31855 thermocouple short to VCC")
            return None
        raw_temp = (raw >> 18) & 0x3FFF
        if raw_temp & 0x2000:
            raw_temp -= 16384
        return raw_temp * 0.25

    async def _read_temp(self) -> float | None:
        if self._sensor_type == MAX31855_TYPE:
            return await self._read_max31855()
        return await self._read_max6675()

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        val = await self._read_temp()
        if val is None:
            return False
        self._sample_buf.append(val)
        if len(self._sample_buf) >= self._samples:
            avg = round(sum(self._sample_buf) / len(self._sample_buf), 2)
            self._sample_buf = []
            event.data["values"] = {"Temperature": avg}
            return True
        if len(self._sample_buf) > 0 and self._sample_buf[-1] is not None:
            event.data["values"] = {"Temperature": round(self._sample_buf[-1], 2)}
            return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("cs_pin", -1)
        self._config.setdefault("spi_bus", 0)
        self._config.setdefault("spi_dev", 0)
        self._config.setdefault("sensor_type", MAX6675_TYPE)
        self._config.setdefault("samples", 3)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "cs_pin", "label": "CS Pin", "type": "number",
             "value": self._config.get("cs_pin", -1)},
            {"name": "spi_bus", "label": "SPI Bus", "type": "number",
             "value": self._config.get("spi_bus", 0)},
            {"name": "spi_dev", "label": "SPI Device", "type": "number",
             "value": self._config.get("spi_dev", 0)},
            {"name": "sensor_type", "label": "Sensor Type", "type": "select",
             "value": self._config.get("sensor_type", MAX6675_TYPE),
             "options": [
                 {"value": MAX6675_TYPE, "label": "MAX6675"},
                 {"value": MAX31855_TYPE, "label": "MAX31855"},
             ]},
            {"name": "samples", "label": "Samples", "type": "number",
             "value": self._config.get("samples", 3), "min": 1, "max": 10},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "CS Pin", "number": 1},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0}
