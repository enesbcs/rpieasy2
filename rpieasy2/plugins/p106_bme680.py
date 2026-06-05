from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD, SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_PRESSURE, SENSOR_V_TYPE_GAS
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p106")

BME680_ADDRS = [0x76, 0x77]
BME680_CHIP_ID = 0x61
BME680_REG_CHIP_ID = 0xD0
BME680_REG_CTRL_HUM = 0x72
BME680_REG_CTRL_MEAS = 0x74
BME680_REG_CONFIG = 0x75
BME680_REG_PRESS_MSB = 0x1F
BME680_REG_GAS_MSB = 0x2A
BME680_REG_GAS_STAT = 0x2D
BME680_REG_RES_HEAT0 = 0x5A
BME680_REG_GAS_WAIT0 = 0x64
BME680_REG_CTRL_GAS0 = 0x70


def s16(v: int) -> int:
    return v - 65536 if v > 32767 else v

def u16(v: tuple[int, int]) -> int:
    return v[0] | (v[1] << 8)


class P106BME680(PluginBase):
    PLUGIN_ID = 106
    PLUGIN_NAME = "Environment - BME68x"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x77
        self._cal: dict[str, int | float] = {}
        self._tfine: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or 0x77)
        if not self._hw:
            return False
        i2c = self._hw.i2c
        try:
            chip_id = await i2c.read_byte_data(self._addr, BME680_REG_CHIP_ID)
            if chip_id != BME680_CHIP_ID:
                logger.error("BME68x chip ID mismatch: 0x%02x", chip_id)
                return False
            await self._read_calibration()
            await i2c.write_byte_data(self._addr, BME680_REG_CTRL_HUM, 0x01)
            ctrl = (3 << 5) | (4 << 2) | 1  | 0x10
            await i2c.write_byte_data(self._addr, BME680_REG_CTRL_MEAS, ctrl)
            await i2c.write_byte_data(self._addr, BME680_REG_CONFIG, 0x00)
            await i2c.write_byte_data(self._addr, BME680_REG_RES_HEAT0, 0x64)
            await i2c.write_byte_data(self._addr, BME680_REG_GAS_WAIT0, 0x64)
            await i2c.write_byte_data(self._addr, BME680_REG_CTRL_GAS0, 0x00)
        except Exception as e:
            logger.error("BME68x init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x77)
        self._config.setdefault("altitude", 0)
        self._config.setdefault("gas_ohm", False)
        return True

    async def _read_calibration(self) -> None:
        i2c = self._hw.i2c
        cal: dict[str, int | float] = {}
        # Temperature cal (0x8A, 3 words)
        d = await i2c.read_i2c_block_data(self._addr, 0x8A, 6)
        cal["T1"] = u16((d[0], d[1]))
        cal["T2"] = s16(u16((d[2], d[3])))
        cal["T3"] = s16(u16((d[4], d[5])))
        # Pressure cal (0x8E, 9 words)
        d = await i2c.read_i2c_block_data(self._addr, 0x8E, 18)
        for i in range(9):
            v = u16((d[i*2], d[i*2+1]))
            cal[f"P{i+1}"] = s16(v) if i > 0 else v
        # Humidity cal (0xE1, 7 bytes)
        d = await i2c.read_i2c_block_data(self._addr, 0xE1, 7)
        cal["H1"] = d[0]  # unsigned byte
        cal["H2"] = s16(u16((d[1], d[2])))
        cal["H3"] = d[3]  # unsigned byte
        h4 = (d[4] << 4) | (d[5] & 0x0F)
        cal["H4"] = h4 - 1024 if h4 > 511 else h4
        h5 = (d[6] << 4) | ((d[5] >> 4) & 0x0F)
        cal["H5"] = h5 - 1024 if h5 > 511 else h5
        cal["H6"] = d[6] - 256 if d[6] > 127 else d[6]  # signed byte
        # Gas cal (0xEB, 1 byte range)
        # Range switching error? Let me check...
        # Gas range register is at 0x02 for BME680, not needed for basic calc
        d_gas = await i2c.read_i2c_block_data(self._addr, 0xEB, 1)
        cal["G1"] = d_gas[0]  # gas range index
        self._cal = cal

    async def _read_meas(self) -> dict[str, float]:
        i2c = self._hw.i2c
        cal = self._cal
        await i2c.write_byte_data(self._addr, BME680_REG_CTRL_MEAS,
                                  (3 << 5) | (4 << 2) | 1 | 0x10)
        await asyncio.sleep(0.08)
        d = await i2c.read_i2c_block_data(self._addr, BME680_REG_PRESS_MSB, 8)
        raw_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        raw_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        raw_h = (d[6] << 8) | d[7]
        # Temperature compensation
        var1 = (raw_t / 16384.0 - int(cal["T1"]) / 1024.0) * int(cal["T2"])
        var2 = ((raw_t / 131072.0 - int(cal["T1"]) / 8192.0) ** 2) * int(cal["T3"])
        tfine = var1 + var2
        self._tfine = tfine
        temp = tfine / 5120.0
        # Pressure compensation
        var1 = tfine / 2.0 - 64000.0
        var2 = var1 * var1 * int(cal["P6"]) / 32768.0
        var2 += var1 * int(cal["P5"]) * 2.0
        var2 = var2 / 4.0 + int(cal["P4"]) * 65536.0
        var1 = (int(cal["P3"]) * var1 * var1 / 524288.0 + int(cal["P2"]) * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * int(cal["P1"])
        press = 0.0
        if var1 != 0.0:
            p = 1048576.0 - raw_p
            p = (p - var2 / 4096.0) * 6250.0 / var1
            var3 = int(cal["P9"]) * p * p / 2147483648.0
            var4 = p * int(cal["P8"]) / 32768.0
            press = (p + var3 + var4 + int(cal["P7"])) / 16.0 / 100.0
        # Humidity compensation
        var1 = tfine - 76800.0
        var2 = (raw_h - (int(cal["H4"]) * 64.0 + int(cal["H5"]) / 16384.0 * var1))
        var3 = int(cal["H2"]) / 65536.0
        var4 = 1.0 + int(cal["H6"]) / 67108864.0 * var1 * (1.0 + int(cal["H3"]) / 67108864.0 * var1)
        hum = var2 * (var3 * var4)
        hum = hum * (1.0 - int(cal["H1"]) * hum / 524288.0)
        hum = max(0.0, min(100.0, hum))
        # Gas resistance
        gas_d = await i2c.read_i2c_block_data(self._addr, BME680_REG_GAS_MSB, 4)
        gas_adc = (gas_d[0] << 8) | gas_d[1]
        gas_valid = (gas_d[3] >> 5) & 0x01
        gas_range = gas_d[2] & 0x07
        gas = 0.0
        if gas_valid and gas_adc > 0:
            gas = gas_adc * 32.0 / (4.0 - gas_range)
            if gas > 1000000000.0:
                gas = 1000000000.0
        elev = int(self._config.get("altitude") or 0)
        if elev != 0:
            press = press / ((1.0 - elev / 44330.0) ** 5.255)
        return {"temp": temp, "hum": hum, "press": press, "gas": gas}

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            vals = await self._read_meas()
            gas_ohm = self._config.get("gas_ohm", False)
            gas_val = round(vals["gas"] / (1.0 if gas_ohm else 1000.0), 2)
            event.data["values"] = {
                "Temperature": round(vals["temp"], 2),
                "Humidity": round(vals["hum"], 1),
                "Pressure": round(vals["press"], 1),
                "Gas": gas_val,
            }
            return True
        except Exception as e:
            logger.error("BME68x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", 0x77), "options": [
                {"value": 0x76, "label": "0x76"},
                {"value": 0x77, "label": "0x77"},
            ]},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
            {"name": "gas_ohm", "label": "Present Gas in Ohm (not kOhm)", "type": "checkbox", "value": self._config.get("gas_ohm", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in BME680_ADDRS

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_PRESSURE, SENSOR_V_TYPE_GAS]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0, "Pressure": 0.0, "Gas": 0.0}
