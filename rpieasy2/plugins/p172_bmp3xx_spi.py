from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SPI, SENSOR_TYPE_TEMP_BARO
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p172")

BMP3XX_CHIP_ID = 0x50
BMP3XX_REG_CHIP_ID = 0x00
BMP3XX_REG_ERR = 0x02
BMP3XX_REG_STATUS = 0x03
BMP3XX_REG_DATA = 0x04
BMP3XX_REG_CMD = 0x7E
BMP3XX_REG_PWR_CTRL = 0x1B
BMP3XX_REG_OSR = 0x1C
BMP3XX_REG_ODR = 0x1D
BMP3XX_REG_CONFIG = 0x1F
BMP3XX_CMD_SOFT_RESET = 0xB6

BMP3XX_CHIP_ID_390 = 0x60


class P172BMP3xxSPI(PluginBase):
    PLUGIN_ID = 172
    PLUGIN_NAME = "Environment - BMP3xx (SPI)"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SPI,
        vtype=SENSOR_TYPE_TEMP_BARO,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._cs_pin: int = -1
        self._temperature: float = 0.0
        self._pressure: float = 0.0
        self._spi_bus: int = 0
        self._spi_dev: int = 0
        self._t_fine: float = 0.0

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
        if self._hw:
            if self._cs_pin >= 0:
                self._hw.gpio.claim_output(self._cs_pin)
                self._hw.gpio.write(self._cs_pin, 1)
        return await self._init_sensor()

    async def _spi_transfer(self, data: list[int]) -> list[int]:
        if not self._hw:
            return [0] * len(data)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 0)
        await asyncio.sleep(0.001)
        result = self._hw.spi.transfer(self._spi_bus, self._spi_dev, data)
        if self._cs_pin >= 0:
            self._hw.gpio.write(self._cs_pin, 1)
        return result

    async def _read_reg(self, reg: int) -> int:
        data = await self._spi_transfer([reg | 0x80, 0x00])
        return data[1] if len(data) > 1 else 0

    async def _read_regs(self, reg: int, count: int) -> list[int]:
        result = await self._spi_transfer([reg | 0x80] + [0x00] * count)
        return result[1:] if len(result) > count else [0] * count

    async def _write_reg(self, reg: int, value: int) -> None:
        await self._spi_transfer([reg & 0x7F, value])

    async def _init_sensor(self) -> bool:
        if not self._hw:
            return False
        chip_id = await self._read_reg(BMP3XX_REG_CHIP_ID)
        if chip_id not in (BMP3XX_CHIP_ID, BMP3XX_CHIP_ID_390):
            logger.warning("BMP3xx SPI chip ID mismatch: 0x%02X", chip_id)
            return False
        await self._write_reg(BMP3XX_REG_CMD, BMP3XX_CMD_SOFT_RESET)
        await asyncio.sleep(0.01)
        await self._write_reg(BMP3XX_REG_PWR_CTRL, 0x33)
        await self._write_reg(BMP3XX_REG_OSR, 0x05)
        await self._write_reg(BMP3XX_REG_ODR, 0x00)
        await self._write_reg(BMP3XX_REG_CONFIG, 0x00)
        self._cal_t1 = await self._read_regs(0x36, 2)
        self._cal_t2 = await self._read_regs(0x38, 2)
        self._cal_t3 = await self._read_regs(0x3A, 2)
        self._cal_p1 = await self._read_regs(0x3C, 2)
        self._cal_p2 = await self._read_regs(0x3E, 2)
        self._cal_p3 = await self._read_regs(0x40, 2)
        self._cal_p4 = await self._read_regs(0x42, 2)
        self._cal_p5 = await self._read_regs(0x44, 2)
        self._cal_p6 = await self._read_regs(0x46, 2)
        self._cal_p7 = await self._read_regs(0x48, 2)
        self._cal_p8 = await self._read_regs(0x4A, 2)
        self._cal_p9 = await self._read_regs(0x4C, 2)
        self._cal_p10 = await self._read_regs(0x4E, 2)
        self._cal_p11 = await self._read_regs(0x50, 2)
        return True

    def _uint16(self, data: list[int], idx: int = 0) -> int:
        if idx >= len(data) - 1:
            return 0
        return (data[idx + 1] << 8) | data[idx]

    def _int16(self, data: list[int], idx: int = 0) -> int:
        v = self._uint16(data, idx)
        return v - 65536 if v >= 32768 else v

    def _compensate_temp(self, raw_temp: int) -> float:
        t1 = self._uint16(self._cal_t1)
        t2 = self._int16(self._cal_t2)
        t3 = self._int16(self._cal_t3)
        x1 = (raw_temp / 16384.0 - t1 / 1024.0) * t2
        x2 = ((raw_temp / 131072.0 - t1 / 8192.0) ** 2) * t3
        self._t_fine = x1 + x2
        return self._t_fine / 5120.0

    def _compensate_press(self, raw_press: int) -> float:
        t1 = self._uint16(self._cal_t1)
        t2 = self._int16(self._cal_t2)
        t3 = self._int16(self._cal_t3)
        p1 = self._int16(self._cal_p1)
        p2 = self._int16(self._cal_p2)
        p3 = self._int16(self._cal_p3)
        p4 = self._int16(self._cal_p4)
        p5 = self._int16(self._cal_p5)
        p6 = self._int16(self._cal_p6)
        p7 = self._int16(self._cal_p7)
        p8 = self._int16(self._cal_p8)
        p9 = self._int16(self._cal_p9)
        p10 = self._int16(self._cal_p10)
        p11 = self._int16(self._cal_p11)

        x1 = (raw_temp / 16384.0 - t1 / 1024.0) * t2
        x2 = ((raw_temp / 131072.0 - t1 / 8192.0) ** 2) * t3
        tf = x1 + x2

        p1_v = p1
        p2_v = p2
        p3_v = p3
        p4_v = p4
        p5_v = p5
        p6_v = p6
        p7_v = p7
        p8_v = p8
        p9_v = p9
        p10_v = p10
        p11_v = p11

        x1 = (p10_v * tf) / 2.0
        x2 = (p11_v * tf * tf) / 4.0
        partial_data1 = p9_v / 2.0 + x1 + x2
        x1 = p5_v / 2.0
        x2 = p6_v * tf
        x3 = p7_v * tf * tf
        partial_data2 = x1 + x2 + x3

        x1 = partial_data1 * raw_press / 2.0
        x2 = p3_v * raw_press / 4.0
        x3 = partial_data2 * raw_press * raw_press / 8.0
        partial_data3 = p1_v + x1 + x2 + x3

        x1 = p2_v / 2.0
        x2 = p4_v * tf
        partial_out = partial_data3 + x1 + x2

        if partial_out == 0:
            return 0.0
        pressure = p8_v / 2.0 + partial_data1 + partial_out
        if pressure < 0:
            pressure = 0.0
        return pressure / 100.0

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        data = await self._read_regs(BMP3XX_REG_DATA, 6)
        if len(data) >= 6:
            raw_press = (data[2] << 16) | (data[1] << 8) | data[0]
            raw_temp = (data[5] << 16) | (data[4] << 8) | data[3]
            self._temperature = self._compensate_temp(raw_temp)
            self._pressure = self._compensate_press(raw_press)
            event.data["values"] = {
                "Temperature": round(self._temperature, 2),
                "Pressure": round(self._pressure, 2),
            }
            return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("cs_pin", -1)
        self._config.setdefault("spi_bus", 0)
        self._config.setdefault("spi_dev", 0)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "cs_pin", "label": "CS Pin", "type": "number",
             "value": self._config.get("cs_pin", -1)},
            {"name": "spi_bus", "label": "SPI Bus", "type": "number",
             "value": self._config.get("spi_bus", 0)},
            {"name": "spi_dev", "label": "SPI Device", "type": "number",
             "value": self._config.get("spi_dev", 0)},
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
        return {"Temperature": 0.0, "Pressure": 0.0}
