from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM_BARO, SENSOR_TYPE_TEMP_EMPTY_BARO, SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_PRESSURE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p028")

BME280_ADDR = 0x76
BME280_REG_ID = 0xD0
BME280_REG_CTRL_HUM = 0xF2
BME280_REG_CTRL_MEAS = 0xF4
BME280_REG_CONFIG = 0xF5
BME280_REG_PRESS = 0xF7
BME280_REG_TEMP = 0xFA
BME280_REG_HUM = 0xFD

MODE_SLEEP = 0x00
MODE_FORCED = 0x01

OVERSAMPLING_OPTS = [
    {"value": 0, "label": "Skipped"},
    {"value": 1, "label": "x1"},
    {"value": 2, "label": "x2"},
    {"value": 3, "label": "x4"},
    {"value": 4, "label": "x8"},
    {"value": 5, "label": "x16"},
]


class P028BME280(PluginBase):
    PLUGIN_ID = 28
    PLUGIN_NAME = "Environment - BMx280"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM_BARO, value_count=3, formula_option=True, send_data_option=True, error_state_values=True, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._addr: int = BME280_ADDR
        self._cal: dict[str, int] = {}
        self._config: dict[str, Any] = {}
        self._has_humidity: bool = True

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", BME280_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            chip_id = await i2c.read_byte_data(self._addr, BME280_REG_ID)
            if chip_id == 0x60:
                self._has_humidity = True
                logger.info("BME280 detected (ID 0x60)")
            elif chip_id in (0x56, 0x57, 0x58):
                self._has_humidity = False
                logger.info("BMP280 detected (ID 0x%02x, no humidity)", chip_id)
            else:
                logger.warning("Unknown BMx280 chip ID: 0x%02x, assuming BME280", chip_id)
                self._has_humidity = True
            await self._read_cal()
            to = int(self._config.get("oversampling_temp") or 1)
            po = int(self._config.get("oversampling_press") or 1)
            ho = int(self._config.get("oversampling_hum") or 1)
            if self._has_humidity:
                await i2c.write_byte_data(self._addr, BME280_REG_CTRL_HUM, ho & 0x07)
            ctrl = ((to & 0x07) << 5) | ((po & 0x07) << 2) | MODE_SLEEP
            await i2c.write_byte_data(self._addr, BME280_REG_CTRL_MEAS, ctrl)
            await i2c.write_byte_data(self._addr, BME280_REG_CONFIG, 0x00)
        except Exception as e:
            logger.error(f"BMx280 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _read_cal(self) -> None:
        if not self._hw: return
        i2c = self._hw.i2c
        d = await i2c.read_i2c_block_data(self._addr, 0x88, 26)
        def s16(v): return v - 65536 if v > 32767 else v
        self._cal["T1"] = d[0] | (d[1] << 8)
        self._cal["T2"] = s16(d[2] | (d[3] << 8))
        self._cal["T3"] = s16(d[4] | (d[5] << 8))
        self._cal["P1"] = d[6] | (d[7] << 8)
        self._cal["P2"] = s16(d[8] | (d[9] << 8))
        self._cal["P3"] = s16(d[10] | (d[11] << 8))
        self._cal["P4"] = s16(d[12] | (d[13] << 8))
        self._cal["P5"] = s16(d[14] | (d[15] << 8))
        self._cal["P6"] = s16(d[16] | (d[17] << 8))
        self._cal["P7"] = s16(d[18] | (d[19] << 8))
        self._cal["P8"] = s16(d[20] | (d[21] << 8))
        self._cal["P9"] = s16(d[22] | (d[23] << 8))
        hd = await i2c.read_i2c_block_data(self._addr, 0xE1, 7)
        self._cal["H1"] = d[25]
        self._cal["H2"] = s16(hd[0] | (hd[1] << 8))
        self._cal["H3"] = hd[2]
        v = (hd[3] << 4) | (hd[4] & 0x0F)
        self._cal["H4"] = v - 1024 if v > 511 else v
        v = (hd[5] << 4) | ((hd[4] >> 4) & 0x0F)
        self._cal["H5"] = v - 1024 if v > 511 else v
        self._cal["H6"] = hd[6] - 256 if hd[6] > 127 else hd[6]

    async def _raw(self, reg: int, n: int) -> int:
        if not self._hw: return 0
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, n)
        v = 0
        for b in d: v = (v << 8) | b
        if n == 3:
            v >>= 4
        return v

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            to = int(self._config.get("oversampling_temp") or 1)
            po = int(self._config.get("oversampling_press") or 1)
            ho = int(self._config.get("oversampling_hum") or 1)
            ctrl = ((to & 0x07) << 5) | ((po & 0x07) << 2) | MODE_FORCED
            await i2c.write_byte_data(self._addr, BME280_REG_CTRL_MEAS, ctrl)
            t_measure = 1.25 + (2.3 * to) + (2.3 * po + 0.575)
            if self._has_humidity:
                t_measure += 2.3 * ho + 0.575
            await asyncio.sleep(t_measure / 1000.0)
            rt = await self._raw(BME280_REG_TEMP, 3)
            rp = await self._raw(BME280_REG_PRESS, 3)
            t1, t2, t3 = self._cal["T1"], self._cal["T2"], self._cal["T3"]
            v1 = (rt / 16384.0 - t1 / 1024.0) * t2
            v2 = ((rt / 131072.0 - t1 / 8192.0) ** 2) * t3
            tf = v1 + v2
            temp = tf / 5120.0
            p1, p2, p3 = self._cal["P1"], self._cal["P2"], self._cal["P3"]
            p4, p5, p6 = self._cal["P4"], self._cal["P5"], self._cal["P6"]
            p7, p8, p9 = self._cal["P7"], self._cal["P8"], self._cal["P9"]
            v1 = tf / 2.0 - 64000.0
            v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
            v2 = v2 / 4.0 + p4 * 65536.0
            v1 = (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0
            v1 = (1.0 + v1 / 32768.0) * p1
            if v1 != 0:
                p = 1048576.0 - rp
                p = (p - v2 / 4096.0) * 6250.0 / v1
                v1 = p9 * p * p / 2147483648.0
                v2 = p * p8 / 32768.0
                press = (p + v1 + v2 + p7) / 16.0 / 100.0
            else: press = 0.0
            elev = int(self._config.get("altitude") or 0)
            if elev != 0:
                press = press / ((1 - (0.0065 * elev) / (temp + 0.0065 * elev + 273.15)) ** 5.257)
            temp_off = float(self._config.get("temperature_offset") or 0) / 10.0
            temp += temp_off
            vals: dict[str, float] = {"Temperature": round(temp, 2), "Pressure": round(press, 2)}
            if self._has_humidity:
                rh = await self._raw(BME280_REG_HUM, 2)
                h1, h2, h3 = self._cal["H1"], self._cal["H2"], self._cal["H3"]
                h4, h5, h6 = self._cal["H4"], self._cal["H5"], self._cal["H6"]
                v = tf - 76800.0
                h = (rh - (h4 * 64.0 + h5 / 16384.0 * v)) * (h2 / 65536.0 * (1.0 + h6 / 67108864.0 * v * (1.0 + h3 / 67108864.0 * v)))
                h = h * (1.0 - h1 * h / 524288.0)
                vals["Humidity"] = round(max(0, min(100, h)), 2)
            event.data["values"] = vals
            return True
        except Exception as e:
            logger.error(f"BMx280 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", BME280_ADDR), "options": [
                {"value": 0x76, "label": "0x76"}, {"value": 0x77, "label": "0x77"},
            ]},
            {"name": "oversampling_temp", "label": "Temperature Oversampling", "type": "select", "value": self._config.get("oversampling_temp", 1), "options": OVERSAMPLING_OPTS},
            {"name": "oversampling_press", "label": "Pressure Oversampling", "type": "select", "value": self._config.get("oversampling_press", 1), "options": OVERSAMPLING_OPTS},
            {"name": "oversampling_hum", "label": "Humidity Oversampling", "type": "select", "value": self._config.get("oversampling_hum", 1), "options": OVERSAMPLING_OPTS},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
            {"name": "temperature_offset", "label": "Temperature Offset (x0.1C)", "type": "number", "value": self._config.get("temperature_offset", 0)},
            {"name": "error_state", "label": "Error State Output", "type": "select", "value": self._config.get("error_state", 0), "options": [
                {"value": 0, "label": "NaN"},
                {"value": 1, "label": "-127"},
                {"value": 2, "label": "0"},
                {"value": 3, "label": "125"},
                {"value": 4, "label": "Ignore"},
            ]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        if self._has_humidity:
            event.data["vtype"] = SENSOR_TYPE_TEMP_HUM_BARO
        else:
            event.data["vtype"] = SENSOR_TYPE_TEMP_EMPTY_BARO
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        if self._has_humidity:
            event.data["vtypes"] = [SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_PRESSURE]
        else:
            event.data["vtypes"] = [SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_PRESSURE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0, "Pressure": 0.0}
