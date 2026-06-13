from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p523")

MCP342X_ADDR_DEFAULT = 0x68
MCP342X_GAIN_MAP = {1: 0b00, 2: 0b01, 4: 0b10, 8: 0b11}
MCP342X_RES_MAP = {12: 0b0000, 14: 0b0100, 16: 0b1000, 18: 0b1100}
MCP342X_CHAN_MAP = {0: 0b0000000, 1: 0b0100000, 2: 0b1000000, 3: 0b1100000}
MCP342X_CONV_TIME = {12: 1.0 / 240, 14: 1.0 / 60, 16: 1.0 / 15, 18: 1.0 / 3.75}
MCP342X_RES_LSB = {12: 1e-3, 14: 250e-6, 16: 62.5e-6, 18: 15.625e-6}
MCP342X_NR_MASK = 0b10000000

MCP342X_DEVICES = ["MCP3422", "MCP3423", "MCP3424", "MCP3426", "MCP3427", "MCP3428"]


class P523MCP342x(PluginBase):
    PLUGIN_ID = 523
    PLUGIN_NAME = "Analog input - MCP342x"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, formula_option=True, send_data_option=True, timer_option=True, timer_optional=True, plugin_stats=True)
    I2C_ADDRESSES = [0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = MCP342X_ADDR_DEFAULT
        self._config_reg = 0
        self._gain = 1
        self._resolution = 12
        self._device = "MCP3424"

    async def on_plugin_init(self, event: Event) -> bool | None:
        if event is not None:
            self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address")
        if self._addr is None:
            self._addr = MCP342X_ADDR_DEFAULT
        self._device = self._config.get("device_type", "MCP3424")
        self._gain = self._config.get("gain")
        if self._gain is None:
            self._gain = 1
        self._resolution = self._config.get("resolution")
        if self._resolution is None:
            self._resolution = 12
        self._build_config()
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MCP342X_ADDR_DEFAULT)
        self._config.setdefault("device_type", "MCP3424")
        self._config.setdefault("gain", 1)
        self._config.setdefault("resolution", 12)
        self._config.setdefault("channel", 0)
        return True

    def _build_config(self) -> None:
        gain = int(self._gain) if self._gain is not None else 1
        resolution = int(self._resolution) if self._resolution is not None else 12
        chan = int(self._config.get("channel", 0)) if self._config.get("channel") is not None else 0
        c = 0
        c |= MCP342X_GAIN_MAP.get(gain, 0)
        c |= MCP342X_RES_MAP.get(resolution, 0)
        c |= MCP342X_CHAN_MAP.get(chan, 0)
        self._config_reg = c

    async def _convert_and_read(self, channel: int, samples: int = 3) -> float:
        i2c = self._hw.i2c
        chan = int(channel) if channel is not None else 0
        self._config_reg &= 0b00011111
        self._config_reg |= MCP342X_CHAN_MAP.get(chan, 0)
        config = self._config_reg | MCP342X_NR_MASK
        resolution = int(self._resolution) if self._resolution is not None else 12
        bytes_to_read = 4 if resolution == 18 else 3
        conv_time = MCP342X_CONV_TIME.get(resolution, 1.0 / 240)

        for _ in range(samples):
            await i2c.write_byte(self._addr, config)
            await asyncio.sleep(0.95 * conv_time)
            d = await i2c.read_i2c_block_data(self._addr, config, bytes_to_read)
            if len(d) < bytes_to_read:
                continue
            if d[-1] & MCP342X_NR_MASK == 0:
                count = 0
                for i in range(bytes_to_read - 1):
                    count <<= 8
                    count |= d[i]
                res = resolution
                sign_mask = 1 << (res - 1)
                count_mask = sign_mask - 1
                if count & sign_mask:
                    count = -(~count & count_mask) - 1
                lsb = MCP342X_RES_LSB.get(resolution, 1e-3)
                gain_val = int(self._gain) if self._gain is not None else 1
                return (count * lsb) / gain_val
        return -1.0

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            chan = self._config.get("channel", 0)
            chan = int(chan) if chan is not None else 0
            samples = self._config.get("samples", 3)
            samples = int(samples) if samples is not None else 3
            val = await self._convert_and_read(chan, samples)
            if val != -1.0:
                event.data["values"] = {"Analog": round(val, 6)}
                return True
            return False
        except Exception as e:
            logger.error("MCP342x read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        dev_opts = [{"value": d, "label": d} for d in MCP342X_DEVICES]
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x68, 0x70)]
        gain_opts = [
            {"value": 2 / 3, "label": "2/3"},
            {"value": 1, "label": "1"},
            {"value": 2, "label": "2"},
            {"value": 4, "label": "4"},
            {"value": 8, "label": "8"},
            {"value": 16, "label": "16"},
        ]
        chan_opts = [{"value": i, "label": f"CH{i + 1}"} for i in range(4)]
        event.data["form"] = [
            {"name": "device_type", "label": "Type", "type": "select", "value": self._config.get("device_type", "MCP3424"), "options": dev_opts},
            {"name": "address", "label": "Address", "type": "select", "value": self._config.get("address", MCP342X_ADDR_DEFAULT), "options": addr_opts},
            {"name": "gain", "label": "Gain", "type": "select", "value": self._config.get("gain", 1), "options": gain_opts},
            {"name": "channel", "label": "Channel", "type": "select", "value": self._config.get("channel", 0), "options": chan_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._device = self._config.get("device_type", "MCP3424")
        self._gain = self._config.get("gain")
        if self._gain is None:
            self._gain = 1
        self._resolution = self._config.get("resolution")
        if self._resolution is None:
            self._resolution = 12
        self._build_config()
        await self.on_plugin_init(None)
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Analog": 0.0}
