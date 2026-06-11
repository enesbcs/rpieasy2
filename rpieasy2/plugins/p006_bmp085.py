from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_BARO
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p006")

BMP085_ADDR = 0x77
BMP085_REG_CAL_AC1 = 0xAA
BMP085_REG_CAL_AC2 = 0xAC
BMP085_REG_CAL_AC3 = 0xAE
BMP085_REG_CAL_AC4 = 0xB0
BMP085_REG_CAL_AC5 = 0xB2
BMP085_REG_CAL_AC6 = 0xB4
BMP085_REG_CAL_B1 = 0xB6
BMP085_REG_CAL_B2 = 0xB8
BMP085_REG_CAL_MB = 0xBA
BMP085_REG_CAL_MC = 0xBC
BMP085_REG_CAL_MD = 0xBE
BMP085_REG_CONTROL = 0xF4
BMP085_REG_TEMPDATA = 0xF6
BMP085_REG_PRESSDATA = 0xF6
BMP085_CMD_READTEMP = 0x2E
BMP085_CMD_READPRESS = 0x34


class P006BMP085(PluginBase):
    PLUGIN_ID = 6
    PLUGIN_NAME = "Environment - BMP085"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_BARO, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x77]

    def __init__(self):
        super().__init__()
        self._addr: int = BMP085_ADDR
        self._cal: dict[str, int] = {}
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", BMP085_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            chip_id = await i2c.read_byte_data(self._addr, 0xD0)
            if chip_id != 0x55:
                logger.warning("BMP085 chip ID mismatch: 0x%02x", chip_id)
                return False
            await self._read_cal()
        except Exception as e:
            logger.error(f"BMP085 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", BMP085_ADDR)
        self._config.setdefault("oversampling", "3")
        self._config.setdefault("altitude", 0)
        return True

    async def _read_cal(self) -> None:
        if not self._hw: return
        i2c = self._hw.i2c
        def s16(v): return v - 65536 if v > 32767 else v
        self._cal["AC1"] = s16((await i2c.read_i2c_block_data(self._addr, BMP085_REG_CAL_AC1, 2))[0] << 8 | (await i2c.read_i2c_block_data(self._addr, BMP085_REG_CAL_AC1, 2))[1])
        d = await i2c.read_i2c_block_data(self._addr, 0xAA, 22)
        self._cal["AC1"] = s16(d[0] << 8 | d[1])
        self._cal["AC2"] = s16(d[2] << 8 | d[3])
        self._cal["AC3"] = s16(d[4] << 8 | d[5])
        self._cal["AC4"] = d[6] << 8 | d[7]
        self._cal["AC5"] = d[8] << 8 | d[9]
        self._cal["AC6"] = d[10] << 8 | d[11]
        self._cal["B1"] = s16(d[12] << 8 | d[13])
        self._cal["B2"] = s16(d[14] << 8 | d[15])
        self._cal["MB"] = s16(d[16] << 8 | d[17])
        self._cal["MC"] = s16(d[18] << 8 | d[19])
        self._cal["MD"] = s16(d[20] << 8 | d[21])

    async def _read_raw_temp(self) -> int:
        if not self._hw: return 0
        i2c = self._hw.i2c
        await i2c.write_byte_data(self._addr, BMP085_REG_CONTROL, BMP085_CMD_READTEMP)
        await asyncio.sleep(0.005)
        d = await i2c.read_i2c_block_data(self._addr, BMP085_REG_TEMPDATA, 2)
        return d[0] << 8 | d[1]

    async def _read_raw_pressure(self) -> int:
        if not self._hw: return 0
        i2c = self._hw.i2c
        oss = int(self._config.get("oversampling") or 3)
        await i2c.write_byte_data(self._addr, BMP085_REG_CONTROL, BMP085_CMD_READPRESS + (oss << 6))
        delays = [5, 8, 14, 26]
        await asyncio.sleep(delays[oss if oss < 4 else 3] / 1000.0)
        d = await i2c.read_i2c_block_data(self._addr, BMP085_REG_PRESSDATA, 3)
        raw = (d[0] << 16) | (d[1] << 8) | d[2]
        raw >>= (8 - oss)
        return raw

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            ut = await self._read_raw_temp()
            up = await self._read_raw_pressure()
            ac6 = self._cal["AC6"]
            ac5 = self._cal["AC5"]
            mc = self._cal["MC"]
            md = self._cal["MD"]
            x1 = (ut - ac6) * ac5 / 32768.0
            x2 = mc * 2048.0 / (x1 + md)
            b5 = x1 + x2
            temp = (b5 + 8) / 16.0 / 10.0
            b6 = b5 - 4000
            b2 = self._cal["B2"]
            ac2 = self._cal["AC2"]
            x1 = (b2 * (b6 * b6 >> 12)) >> 11
            x2 = (ac2 * b6) >> 11
            x3 = x1 + x2
            oss = int(self._config.get("oversampling") or 3)
            b3 = (((self._cal["AC1"] * 4 + x3) << oss) + 2) / 4
            x1 = (self._cal["AC3"] * b6) >> 13
            x2 = (self._cal["B1"] * (b6 * b6 >> 12)) >> 16
            x3 = ((x1 + x2) + 2) >> 2
            b4 = (self._cal["AC4"] * (x3 + 32768)) >> 15
            b7 = (up - b3) * (50000 >> oss)
            if b7 < 0x80000000:
                p = (b7 * 2) / b4
            else:
                p = (b7 / b4) * 2
            x1 = (p >> 8) * (p >> 8)
            x1 = (x1 * 3038) >> 16
            x2 = (-7357 * p) >> 16
            p = p + ((x1 + x2 + 3791) >> 4)
            press = p / 100.0
            elev = int(self._config.get("altitude") or 0)
            if elev != 0:
                press = press / ((1 - (0.0065 * elev) / (temp + 0.0065 * elev + 273.15)) ** 5.257)
            event.data["values"] = {"Temperature": round(temp, 2), "Pressure": round(press, 2)}
            return True
        except Exception as e:
            logger.error(f"BMP085 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", BMP085_ADDR), "options": [
                {"value": 0x77, "label": "0x77"},
            ]},
            {"name": "oversampling", "label": "Oversampling", "type": "select", "value": self._config.get("oversampling", "3"), "options": [
                {"value": "0", "label": "Low Power (1)"},
                {"value": "1", "label": "Standard (2)"},
                {"value": "2", "label": "High Res (4)"},
                {"value": "3", "label": "Ultra High Res (8)"},
            ]},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Pressure": 0.0}
