from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p169")

AS3935_ADDR = 0x03

AS3935_REG_AFE_GB = 0x00
AS3935_REG_DISTANCE = 0x07
AS3935_REG_INT = 0x03
AS3935_REG_STAT = 0x02
AS3935_REG_TUNING = 0x08
AS3935_REG_LCO_FDIV = 0x03
AS3935_REG_MASK_THRESH = 0x02
AS3935_REG_WDTH = 0x01
AS3935_REG_SLEEP = 0x03

AS3935_INT_LIGHTNING = 0x08
AS3935_INT_DISTURBER = 0x04
AS3935_INT_NOISE = 0x01


class P169AS3935(PluginBase):
    PLUGIN_ID = 169
    PLUGIN_NAME = "Environment - AS3935 Lightning Detector"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_TRIPLE,
        formula_option=True,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        i2c_no_device_check=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x03]

    def __init__(self):
        super().__init__()
        self._addr: int = AS3935_ADDR
        self._config: dict[str, Any] = {}
        self._total = 0
        self._last_dist = 0
        self._last_energy = 0

    async def _read_reg(self, reg: int) -> int:
        return await self._hw.i2c.read_byte_data(self._addr, reg)

    async def _write_reg(self, reg: int, val: int) -> None:
        await self._hw.i2c.write_byte_data(self._addr, reg, val)

    async def _read_distance(self) -> int:
        v = await self._read_reg(AS3935_REG_DISTANCE)
        return v & 0x3F

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = AS3935_ADDR
        if not self._hw:
            return False
        try:
            await self._write_reg(AS3935_REG_AFE_GB, 0x1E)
            await self._write_reg(AS3935_REG_WDTH, 0x22)
            await asyncio.sleep(0.002)
            await self._write_reg(AS3935_REG_LCO_FDIV, 0x3C)
            await asyncio.sleep(0.002)
            await self._write_reg(AS3935_REG_MASK_THRESH, 0x3A)
            await self._write_reg(AS3935_REG_LCO_FDIV, 0x3C)
            await asyncio.sleep(0.002)
            return True
        except Exception as e:
            logger.error("AS3935 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("indoor", True)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            irq = await self._read_reg(AS3935_REG_INT)
            if irq & AS3935_INT_LIGHTNING:
                dist = await self._read_distance()
                energy_raw = await self._read_reg(0x05)
                energy_mid = await self._read_reg(0x04)
                energy_lo = await self._read_reg(0x06)
                energy = (energy_raw << 16) | (energy_mid << 8) | energy_lo
                self._last_dist = dist
                self._last_energy = energy
                self._total += 1
            event.data["values"] = {
                "DistanceNear": max(0, self._last_dist - 1),
                "DistanceFar": self._last_dist,
                "Lightning": self._last_energy,
                "Total": self._total,
            }
            return True
        except Exception as e:
            logger.error("AS3935 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "indoor", "label": "Indoor mode", "type": "checkbox", "value": self._config.get("indoor", True)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == AS3935_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = AS3935_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"DistanceNear": 0, "DistanceFar": 0, "Lightning": 0, "Total": 0}
