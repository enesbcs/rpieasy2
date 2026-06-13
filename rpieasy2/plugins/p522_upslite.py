from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p522")

UPSLITE_ADDR = 0x36
UPSLITE_REG_VOLTAGE = 0x02
UPSLITE_REG_CAPACITY = 0x04
UPSLITE_REG_CMD = 0x06
UPSLITE_REG_RESET = 0xFE


class P522UPSLite(PluginBase):
    PLUGIN_ID = 522
    PLUGIN_NAME = "Energy (DC) - UPS-Lite - MAX17040"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_DUAL, value_count=2, formula_option=True, send_data_option=True, timer_option=True, plugin_stats=True)
    I2C_ADDRESSES = [0x36]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._valuecount = 2

    async def on_plugin_init(self, event: Event) -> bool | None:
        if event is not None:
            self._config = event.data.get("task_config", {})
        vc = self._config.get("valuecount", 2)
        self._valuecount = int(vc) if vc is not None else 2
        if self._hw:
            try:
                await self._quick_start()
            except Exception as e:
                logger.error("UPSLite QuickStart failed: %s", e)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("indicator0", -1)
        self._config.setdefault("indicator1", 1)
        self._config.setdefault("valuecount", 2)
        return True

    async def _read_word_be(self, reg: int) -> int:
        raw = await self._hw.i2c.read_i2c_block_data(UPSLITE_ADDR, reg, 2)
        return (raw[0] << 8) | raw[1]

    async def _read_voltage(self) -> float:
        val = await self._read_word_be(UPSLITE_REG_VOLTAGE)
        return val * 1.25 / 1000.0 / 16.0

    async def _read_capacity(self) -> float:
        val = await self._read_word_be(UPSLITE_REG_CAPACITY)
        return val / 256.0

    async def _quick_start(self) -> None:
        await self._hw.i2c.write_i2c_block_data(UPSLITE_ADDR, UPSLITE_REG_CMD, [0x40, 0x00])

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            vals: dict[str, Any] = {}
            ind0 = self._config.get("indicator0", -1)
            ind0 = int(ind0) if ind0 is not None else -1
            ind1 = self._config.get("indicator1", 1)
            ind1 = int(ind1) if ind1 is not None else 1

            for v_idx, ind in [(0, ind0), (1, ind1)]:
                if ind == 2:
                    val = await self._read_voltage()
                    if val is not None:
                        vals[f"V{ind}"] = round(val, 4)
                elif ind == 4:
                    val = await self._read_capacity()
                    if val is not None:
                        vals[f"C{ind}"] = round(val, 2)

            if ind0 == -1 and ind1 == 1:
                val = await self._read_voltage()
                if val is not None:
                    vals["Voltage"] = round(val, 4)
                cap = await self._read_capacity()
                if cap is not None:
                    vals["Capacity"] = round(cap, 2)

            event.data["values"] = vals
            return True
        except Exception as e:
            logger.error("UPSLite read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        ind_opts = [
            {"value": -1, "label": "None"},
            {"value": 2, "label": "Voltage"},
            {"value": 4, "label": "Capacity"},
        ]
        event.data["form"] = [
            {"name": "indicator0", "label": "Indicator1", "type": "select", "value": self._config.get("indicator0", -1), "options": ind_opts},
            {"name": "indicator1", "label": "Indicator2", "type": "select", "value": self._config.get("indicator1", 1), "options": ind_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        vc = 2
        for v in range(2):
            ind = self._config.get(f"indicator{v}", -1)
            ind = int(ind) if ind is not None else -1
            if ind > 0 and vc != v + 1:
                vc = v + 1
        self._valuecount = vc
        if vc == 1:
            self.DEVICE_PROPERTIES.vtype = SENSOR_TYPE_SINGLE
            self.DEVICE_PROPERTIES.value_count = 1
        else:
            self.DEVICE_PROPERTIES.vtype = SENSOR_TYPE_DUAL
            self.DEVICE_PROPERTIES.value_count = 2
        self._config["valuecount"] = vc
        await self.on_plugin_init(None)
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Voltage": 0.0, "Capacity": 0.0}
