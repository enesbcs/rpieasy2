from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TEMP_HUM
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p014")

SI70XX_ADDR = 0x40
CMD_MEASURE_TEMP = 0xF3
CMD_MEASURE_HUMI = 0xF5


class P014SI70xx(PluginBase):
    PLUGIN_ID = 14
    PLUGIN_NAME = "Environment - SI70xx/HTU21D"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TEMP_HUM, value_count=2, formula_option=True, send_data_option=True, i2c_no_device_check=True, plugin_stats=True, output_data_type=OutputDataType.ALL)

    def __init__(self):
        super().__init__()
        self._addr: int = SI70XX_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", SI70XX_ADDR)
        if not self._hw: return False
        try:
            resolution = int(self._config.get("resolution") or 0)
            res_cmds = [0x00, 0x80, 0x01, 0x81]
            cmd = res_cmds[resolution] if resolution < 4 else 0x00
            await self._hw.i2c.write_i2c_block_data(self._addr, 0xE6, [cmd])
        except Exception:
            pass
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _read_temp(self) -> float:
        if not self._hw: return 0.0
        i2c = self._hw.i2c
        await i2c.write_byte(self._addr, CMD_MEASURE_TEMP)
        await asyncio.sleep(0.05)
        d = await i2c.read_bytes(self._addr, 3)
        return (175.72 * ((d[0] << 8) | d[1]) / 65536.0) - 46.85

    async def _read_humi(self) -> float:
        if not self._hw: return 0.0
        i2c = self._hw.i2c
        await i2c.write_byte(self._addr, CMD_MEASURE_HUMI)
        await asyncio.sleep(0.05)
        d = await i2c.read_bytes(self._addr, 3)
        return max(0, min(100, (125.0 * ((d[0] << 8) | d[1]) / 65536.0) - 6.0))

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            event.data["values"] = {"Temperature": round(await self._read_temp(), 2), "Humidity": round(await self._read_humi(), 2)}
            return True
        except Exception as e:
            logger.error(f"SI70xx read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", SI70XX_ADDR), "options": [
                {"value": 0x40, "label": "0x40"}, {"value": 0x41, "label": "0x41"},
            ]},
            {"name": "resolution", "label": "Resolution", "type": "select", "value": self._config.get("resolution", 0), "options": [
                {"value": 0, "label": "Temp14 / RH12"},
                {"value": 1, "label": "Temp13 / RH10"},
                {"value": 2, "label": "Temp12 / RH8"},
                {"value": 3, "label": "Temp11 / RH11"},
            ]},
            {"name": "filter_power", "label": "ADC Filter Power (0-4)", "type": "number", "value": self._config.get("filter_power", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
