from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_BARO
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p032")

MS5611_ADDR_DEFAULT = 0x77
MS5611_CMD_RESET = 0x1E
MS5611_CMD_ADC_READ = 0x00
MS5611_CMD_ADC_CONV = 0x40
MS5611_CMD_ADC_D1 = 0x00
MS5611_CMD_ADC_D2 = 0x10
MS5611_CMD_ADC_256 = 0x00
MS5611_CMD_ADC_512 = 0x02
MS5611_CMD_ADC_1024 = 0x04
MS5611_CMD_ADC_2048 = 0x06
MS5611_CMD_ADC_4096 = 0x08
MS5611_CMD_PROM_RD = 0xA0


class P032MS5611(PluginBase):
    PLUGIN_ID = 32
    PLUGIN_NAME = "Environment - MS5611"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_BARO, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x76, 0x77]

    def __init__(self):
        super().__init__()
        self._addr: int = MS5611_ADDR_DEFAULT
        self._prom: list[int] = [0] * 8
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", MS5611_ADDR_DEFAULT)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MS5611_ADDR_DEFAULT)
        self._config.setdefault("altitude", 0)
        self._config.setdefault("oversampling", "4")
        return True

    async def _read_prom(self) -> None:
        if not self._hw: return
        i2c = self._hw.i2c
        await i2c.write_byte_data(self._addr, 0x00, MS5611_CMD_RESET)
        await asyncio.sleep(0.003)
        for i in range(8):
            d = await i2c.read_i2c_block_data(self._addr, MS5611_CMD_PROM_RD + 2 * i, 2)
            self._prom[i] = d[0] << 8 | d[1]

    async def _read_adc(self, cmd: int) -> int:
        if not self._hw: return 0
        i2c = self._hw.i2c
        await i2c.write_byte_data(self._addr, 0x00, MS5611_CMD_ADC_CONV + cmd)
        os_delays = {0: 0.0009, 2: 0.003, 4: 0.004, 6: 0.006, 8: 0.010}
        delay = os_delays.get(cmd & 0x0F, 0.010)
        await asyncio.sleep(delay)
        d = await i2c.read_i2c_block_data(self._addr, MS5611_CMD_ADC_READ, 3)
        return (d[0] << 16) | (d[1] << 8) | d[2]

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            await self._read_prom()
            oss = int(self._config.get("oversampling") or 4)
            os_cmd = {1: MS5611_CMD_ADC_256, 2: MS5611_CMD_ADC_512, 3: MS5611_CMD_ADC_1024, 4: MS5611_CMD_ADC_2048, 5: MS5611_CMD_ADC_4096}
            os_val = os_cmd.get(oss, MS5611_CMD_ADC_4096)
            d2 = await self._read_adc(MS5611_CMD_ADC_D2 + os_val)
            d1 = await self._read_adc(MS5611_CMD_ADC_D1 + os_val)
            dt = d2 - self._prom[5] * (1 << 8)
            offset = self._prom[2] * (1 << 16) + dt * self._prom[4] / (1 << 7)
            sens = self._prom[1] * (1 << 15) + dt * self._prom[3] / (1 << 8)
            temp = (2000 + (dt * self._prom[6]) / (1 << 23)) / 100.0
            t2 = 0.0
            off2 = 0.0
            sens2 = 0.0
            if temp < 20.0:
                temp_c = temp * 100.0
                t2 = dt * dt / (1 << 31)
                temp_20 = temp_c - 2000
                off2 = 5.0 * temp_20 * temp_20 / 2.0
                sens2 = 5.0 * temp_20 * temp_20 / 4.0
                if temp_c < -1500:
                    temp_15 = temp_c + 1500
                    off2 += 7.0 * temp_15 * temp_15
                    sens2 += 11.0 * temp_15 * temp_15 / 2.0
            temp_c = temp * 100.0 - t2
            offset_c = offset - off2
            sens_c = sens - sens2
            press = ((d1 * sens_c) / (1 << 21) - offset_c) / (1 << 15)
            press = press / 100.0
            temp = temp_c / 100.0
            elev = int(self._config.get("altitude") or 0)
            if elev != 0:
                press = press / ((1 - (0.0065 * elev) / (temp + 0.0065 * elev + 273.15)) ** 5.257)
            event.data["values"] = {"Temperature": round(temp, 2), "Pressure": round(press, 2)}
            return True
        except Exception as e:
            logger.error(f"MS5611 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MS5611_ADDR_DEFAULT), "options": [
                {"value": 0x76, "label": "0x76"}, {"value": 0x77, "label": "0x77"},
            ]},
            {"name": "oversampling", "label": "Oversampling", "type": "select", "value": self._config.get("oversampling", "4"), "options": [
                {"value": "1", "label": "256 (1x)"},
                {"value": "2", "label": "512 (2x)"},
                {"value": "3", "label": "1024 (4x)"},
                {"value": "4", "label": "2048 (8x)"},
                {"value": "5", "label": "4096 (16x)"},
            ]},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Pressure": 0.0}
