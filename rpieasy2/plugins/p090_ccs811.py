from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p090")

CCS811_ADDR_0 = 0x5A
CCS811_ADDR_1 = 0x5B

CCS811_REG_STATUS = 0x00
CCS811_REG_MEAS_MODE = 0x01
CCS811_REG_ALG_RESULT = 0x02
CCS811_REG_ENV_DATA = 0x05
CCS811_REG_HW_ID = 0x20


class P090CCS811(PluginBase):
    PLUGIN_ID = 90
    PLUGIN_NAME = "Gases - CCS811 TVOC/eCO2"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        formula_option=True,
        value_count=2,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x5A, 0x5B]

    def __init__(self):
        super().__init__()
        self._addr: int = CCS811_ADDR_0
        self._config: dict[str, Any] = {}
        self._tvoc: int = 0
        self._eco2: int = 0
        self._ready = False
        self._new_data = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", CCS811_ADDR_0))
        except (ValueError, TypeError):
            self._addr = CCS811_ADDR_0
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            hw_id = await i2c.read_byte_data(self._addr, CCS811_REG_HW_ID)
            if hw_id != 0x81:
                logger.error("CCS811 HW_ID mismatch: 0x%02X", hw_id)
                return False
            await i2c.write_byte_data(self._addr, CCS811_REG_MEAS_MODE, 0x00)
            await asyncio.sleep(0.001)
            await i2c.write_byte_data(self._addr, CCS811_REG_MEAS_MODE, 0x10)
            await asyncio.sleep(0.001)
            try:
                freq = int(self._config.get("read_interval", 1))
            except (ValueError, TypeError):
                freq = 1
            drive_mode = min(max(freq, 1), 3)
            await i2c.write_byte_data(self._addr, CCS811_REG_MEAS_MODE, drive_mode)
            self._ready = True
            self._new_data = False
            return True
        except Exception as e:
            logger.error("CCS811 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", CCS811_ADDR_0)
        self._config.setdefault("read_interval", 1)
        self._config.setdefault("compensation_enable", False)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._ready:
            return False
        try:
            i2c = self._hw.i2c
            try:
                comp_enable = int(self._config.get("compensation_enable", 0))
            except (ValueError, TypeError):
                comp_enable = 0
            if comp_enable:
                try:
                    temp = float(self._config.get("comp_temp", 25.0))
                except (ValueError, TypeError):
                    temp = 25.0
                try:
                    hum = float(self._config.get("comp_hum", 50.0))
                except (ValueError, TypeError):
                    hum = 50.0
                temp_raw = int((temp + 25) * 512)
                hum_raw = int(hum * 512)
                env_data = [(hum_raw >> 8) & 0xFF, hum_raw & 0xFF,
                            (temp_raw >> 8) & 0xFF, temp_raw & 0xFF]
                await i2c.write_i2c_block_data(self._addr, CCS811_REG_ENV_DATA, env_data)
                await asyncio.sleep(0.001)

            status = await i2c.read_byte_data(self._addr, CCS811_REG_STATUS)
            if not (status & 0x08):
                return False
            d = await i2c.read_i2c_block_data(self._addr, CCS811_REG_ALG_RESULT, 4)
            self._eco2 = (d[0] << 8) | d[1]
            self._tvoc = (d[2] << 8) | d[3]
            event.data["values"] = {"TVOC": self._tvoc, "eCO2": self._eco2}
            return True
        except Exception as e:
            logger.error("CCS811 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        freq_opts = [
            {"value": 1, "label": "1 second"},
            {"value": 2, "label": "10 seconds"},
            {"value": 3, "label": "60 seconds"},
        ]
        addr_opts = [
            {"value": 0x5A, "label": "0x5A (ADDR LOW)"},
            {"value": 0x5B, "label": "0x5B (ADDR HIGH)"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", CCS811_ADDR_0), "options": addr_opts},
            {"name": "read_interval", "label": "Take reading every", "type": "select", "value": self._config.get("read_interval", 1), "options": freq_opts},
            {"name": "compensation_enable", "label": "Enable temp/humid compensation", "type": "checkbox", "value": self._config.get("compensation_enable", False)},
            {"name": "comp_temp", "label": "Compensation Temperature (C)", "type": "number", "value": self._config.get("comp_temp", "25")},
            {"name": "comp_hum", "label": "Compensation Humidity (%)", "type": "number", "value": self._config.get("comp_hum", "50")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) in [CCS811_ADDR_0, CCS811_ADDR_1]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"TVOC": 0, "eCO2": 0}
