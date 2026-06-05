from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p083")

SGP30_ADDR = 0x58
SGP30_IAQ_CMD = [0x20, 0x03]


class P083SGP30(PluginBase):
    PLUGIN_ID = 83
    PLUGIN_NAME = "Gases - SGP30 TVOC/eCO2"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = SGP30_ADDR
        self._tvoc: int = 0
        self._eco2: int = 0
        self._initialized = False
        self._new_values = False
        self._init_time: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = SGP30_ADDR
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, SGP30_IAQ_CMD[0], SGP30_IAQ_CMD[1:])
            eco2_base = int(self._config.get("eco2_baseline") or 0)
            tvoc_base = int(self._config.get("tvoc_baseline") or 0)
            if eco2_base != 0 and tvoc_base != 0:
                bs = [(eco2_base >> 8) & 0xFF, eco2_base & 0xFF, (tvoc_base >> 8) & 0xFF, tvoc_base & 0xFF]
                await i2c.write_i2c_block_data(self._addr, 0x20, [0x2E] + bs)
            self._initialized = True
            self._init_time = asyncio.get_event_loop().time()
            return True
        except Exception as e:
            logger.error("SGP30 init failed: %s", e)
            return False

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        if not self._initialized or not self._hw:
            return None
        try:
            i2c = self._hw.i2c
            await i2c.write_i2c_block_data(self._addr, SGP30_IAQ_CMD[0], SGP30_IAQ_CMD[1:])
            await asyncio.sleep(0.012)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
            self._tvoc = (d[2] << 8) | d[3]
            self._eco2 = (d[0] << 8) | d[1]
            elapsed = (asyncio.get_event_loop().time() - self._init_time) * 1000
            if elapsed > 15000 or (self._tvoc != 0 and self._eco2 != 400):
                self._new_values = True
            if self._new_values and elapsed > 60000:
                try:
                    await i2c.write_i2c_block_data(self._addr, 0x20, [0x15])
                    await asyncio.sleep(0.012)
                    d = await i2c.read_i2c_block_data(self._addr, 0x00, 6)
                    eco2_base = (d[0] << 8) | d[1]
                    tvoc_base = (d[2] << 8) | d[3]
                    if eco2_base != 0 and tvoc_base != 0:
                        self._config["eco2_baseline"] = eco2_base
                        self._config["tvoc_baseline"] = tvoc_base
                except Exception:
                    pass
        except Exception as e:
            logger.error("SGP30 read error: %s", e)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._new_values:
            return False
        self._new_values = False
        event.data["values"] = {"TVOC": self._tvoc, "eCO2": self._eco2}
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        status = "Initialized" if self._initialized else "-"
        event.data["form"] = [
            {"name": "status", "label": "Sensor State", "type": "text", "value": status, "readonly": True},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == SGP30_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = SGP30_ADDR
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_CO2]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"TVOC": 0, "eCO2": 0}
