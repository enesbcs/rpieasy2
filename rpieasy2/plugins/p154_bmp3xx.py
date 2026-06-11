from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_BARO
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p154")

BMP3XX_ADDR_DEFAULT = 0x77
BMP3XX_REG_CHIP_ID = 0x00
BMP3XX_CHIP_ID_BMP38X = 0x50
BMP3XX_CHIP_ID_BMP390 = 0x60
BMP3XX_REG_CAL = 0x31
BMP3XX_REG_TEMP = 0x07
BMP3XX_REG_PRESS = 0x04
BMP3XX_REG_CTRL_MEAS = 0x1B
BMP3XX_REG_PWR_CTRL = 0x1C
BMP3XX_REG_OSR = 0x1D
BMP3XX_REG_ODR = 0x1E
BMP3XX_REG_CONFIG = 0x1F
BMP3XX_REG_CMD = 0x7E


class P154BMP3xx(PluginBase):
    PLUGIN_ID = 154
    PLUGIN_NAME = "Environment - BMP3xx"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_BARO, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x76, 0x77]

    def __init__(self):
        super().__init__()
        self._addr: int = BMP3XX_ADDR_DEFAULT
        self._cal: dict[str, float] = {}
        self._config: dict[str, Any] = {}
        self._chip_id: int = 0

    async def _s16(self, hi: int, lo: int) -> int:
        v = (hi << 8) | lo
        return v - 65536 if v > 32767 else v

    async def _u16(self, hi: int, lo: int) -> int:
        return (hi << 8) | lo

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", BMP3XX_ADDR_DEFAULT)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            self._chip_id = await i2c.read_byte_data(self._addr, BMP3XX_REG_CHIP_ID)
            if self._chip_id not in (BMP3XX_CHIP_ID_BMP38X, BMP3XX_CHIP_ID_BMP390):
                logger.warning("Unknown BMP3xx chip ID: 0x%02x", self._chip_id)
                return False
            await self._read_cal()
            try:
                t_os = int(self._config.get("temp_os", 2))
            except (ValueError, TypeError):
                t_os = 2
            try:
                p_os = int(self._config.get("press_os", 2))
            except (ValueError, TypeError):
                p_os = 2
            osr = ((t_os & 0x07) << 3) | (p_os & 0x07)
            await i2c.write_byte_data(self._addr, BMP3XX_REG_OSR, osr)
            await i2c.write_byte_data(self._addr, BMP3XX_REG_ODR, 0x00)
            await i2c.write_byte_data(self._addr, BMP3XX_REG_CONFIG, 0x06)
            await i2c.write_byte_data(self._addr, BMP3XX_REG_PWR_CTRL, 0x33)
            await asyncio.sleep(0.005)
        except Exception as e:
            logger.error(f"BMP3xx init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", BMP3XX_ADDR_DEFAULT)
        self._config.setdefault("temp_os", 2)
        self._config.setdefault("press_os", 2)
        self._config.setdefault("altitude", 0)
        return True

    async def _read_cal(self) -> None:
        if not self._hw: return
        i2c = self._hw.i2c
        d = await i2c.read_i2c_block_data(self._addr, BMP3XX_REG_CAL, 33)
        self._cal["T1"] = (d[1] << 8) | d[0]
        self._cal["T2"] = await self._s16(d[3], d[2])
        self._cal["T3"] = (d[5] << 8) | d[4]
        self._cal["P1"] = (d[7] << 8) | d[6]
        self._cal["P2"] = await self._s16(d[9], d[8])
        self._cal["P3"] = (d[11] << 8) | d[10]
        self._cal["P4"] = await self._s16(d[13], d[12])
        self._cal["P5"] = await self._s16(d[15], d[14])
        self._cal["P6"] = (d[17] << 8) | d[16]
        self._cal["P7"] = await self._s16(d[19], d[18])
        self._cal["P8"] = await self._s16(d[21], d[20])
        self._cal["P9"] = (d[23] << 8) | d[22]
        self._cal["P10"] = (d[25] << 8) | d[24]
        self._cal["P11"] = await self._s16(d[27], d[26])
        self._cal["P12"] = await self._s16(d[29], d[28])
        self._cal["P13"] = (d[31] << 8) | d[30]

    async def _read_24(self, reg: int) -> int:
        if not self._hw: return 0
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 3)
        return (d[0] << 16) | (d[1] << 8) | d[2]

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            await i2c.write_byte_data(self._addr, BMP3XX_REG_PWR_CTRL, 0x33)
            await i2c.write_byte_data(self._addr, BMP3XX_REG_CTRL_MEAS, 0x34)
            await asyncio.sleep(0.050)
            raw_temp = await self._read_24(BMP3XX_REG_TEMP)
            raw_press = await self._read_24(BMP3XX_REG_PRESS)
            t1 = self._cal["T1"]
            t2 = self._cal["T2"]
            t3 = self._cal["T3"]
            pd1 = raw_temp - t1 * 256
            pd2 = (t2 * pd1) / (1 << 30)
            pd3 = ((t3 * pd1) / (1 << 30)) * (pd1 / (1 << 30)) / (1 << 30) * (pd1 / (1 << 30))
            temp = (pd2 + pd3) / 100.0
            p1 = self._cal["P1"]
            p2 = self._cal["P2"]
            p3 = self._cal["P3"]
            p4 = self._cal["P4"]
            p5 = self._cal["P5"]
            p6 = self._cal["P6"]
            p7 = self._cal["P7"]
            p8 = self._cal["P8"]
            p9 = self._cal["P9"]
            p10 = self._cal["P10"]
            p11 = self._cal["P11"]
            p12 = self._cal["P12"]
            p13 = self._cal["P13"]
            p_pd1 = raw_press - (p1 * 512)
            p_pd2 = (p2 * p_pd1) / (1 << 29)
            p_partial1a = (p9 * p_pd1) / (1 << 28)
            p_partial1b = (p10 * p_pd1) / (1 << 33)
            p_pd3 = (p_pd1 * p_pd1) / (1 << 32) * ((p3 / 1.0 + p_partial1a + p_partial1b) * (p_pd1 / (1 << 33)) / (1 << 13))
            p_pd4 = (p_pd1 * p_pd1) / (1 << 30) * p4 + p_pd2
            p_pd5 = (p_pd1 * p_pd1 * p_pd1) / (1 << 48) * p5 * (p_pd1 / (1 << 32))
            p_pd6 = (p_pd1 * p_pd1 * p_pd1 * p_pd1) / (1 << 64) * p6
            p_pd7 = ((p_pd1 * p_pd1) / (1 << 28)) * p7
            p_partial2 = (p8 * p_pd1) / (1 << 30)
            p_pd8 = (p_pd2 * p_partial2) / (1 << 29)
            p_pd9 = (p_pd5 * p_partial2) / (1 << 32)
            p_pd10 = (p_pd3 * p_partial2) / (1 << 37)
            p_pd11 = (p_pd7 * p_partial2) / (1 << 34)
            p_pd12 = (p_pd10 + p_pd11 + p_pd6 + p_pd9 + p_pd8 + p_pd4 + p_pd3 + p_pd5 + p_pd7)
            p_pd13 = (p_pd12 / (1 << 12)) * (p11 / (1 << 8))
            p_pd14 = (p_pd12 / (1 << 16)) * (p12 / (1 << 8))
            p_pd15 = p_pd13 + p_pd14
            p_pd16 = (p_pd15 * p_pd12 / (1 << 32)) * p13 / (1 << 8)
            press_partial = p_pd12 + p_pd15 + p_pd16
            press = press_partial / 100.0
            elev = int(self._config.get("altitude") or 0)
            if elev != 0:
                press = press / ((1 - (0.0065 * elev) / (temp + 0.0065 * elev + 273.15)) ** 5.257)
            event.data["values"] = {"Temperature": round(temp, 2), "Pressure": round(press, 2)}
            return True
        except Exception as e:
            logger.error(f"BMP3xx read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        chip_names = {0x50: "BMP38x", 0x60: "BMP390"}
        chip_str = chip_names.get(self._chip_id, f"0x{self._chip_id:02X}")
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", BMP3XX_ADDR_DEFAULT), "options": [
                {"value": 0x76, "label": "0x76"}, {"value": 0x77, "label": "0x77"},
            ]},
            {"name": "chip_info", "label": "Detected Sensor", "type": "info", "value": chip_str},
            {"name": "temp_os", "label": "Temperature Oversampling", "type": "select", "value": self._config.get("temp_os", 2), "options": [
                {"value": 0, "label": "Skipped"}, {"value": 1, "label": "x1"},
                {"value": 2, "label": "x2"}, {"value": 3, "label": "x4"},
                {"value": 4, "label": "x8"}, {"value": 5, "label": "x16"},
                {"value": 6, "label": "x32"},
            ]},
            {"name": "press_os", "label": "Pressure Oversampling", "type": "select", "value": self._config.get("press_os", 2), "options": [
                {"value": 0, "label": "Skipped"}, {"value": 1, "label": "x1"},
                {"value": 2, "label": "x2"}, {"value": 3, "label": "x4"},
                {"value": 4, "label": "x8"}, {"value": 5, "label": "x16"},
                {"value": 6, "label": "x32"},
            ]},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Pressure": 0.0}
