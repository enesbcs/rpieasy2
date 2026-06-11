from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p143")

ADA_ENCODER_ADDR = 0x36
M5STACK_ENCODER_ADDR = 0x40
DFROBOT_ENCODER_ADDR = 0x54

ADA_ENCODER_REG_ID = 0x00
ADA_ENCODER_REG_POSITION = 0x03
ADA_ENCODER_REG_BUTTON = 0x08

M5STACK_REG_ID = 0x00
M5STACK_REG_POSITION = 0x20

DFROBOT_REG_ID = 0x00
DFROBOT_REG_POSITION_H = 0x03

ENCODER_ADA = 0
ENCODER_M5 = 1
ENCODER_DFR = 2


class P143I2CRotary(PluginBase):
    PLUGIN_ID = 143
    PLUGIN_NAME = "Switch input - I2C Rotary encoders"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        formula_option=True,
        value_count=2,
        send_data_option=True,
    )
    I2C_ADDRESSES = [0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x40, 0x54, 0x55, 0x56, 0x57]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = ADA_ENCODER_ADDR
        self._counter: int = 0
        self._prev_counter: int = 0
        self._button_state: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", ADA_ENCODER_ADDR))
        except (ValueError, TypeError):
            self._addr = ADA_ENCODER_ADDR
        try:
            self._counter = int(self._config.get("initial_position", 0))
        except (ValueError, TypeError):
            self._counter = 0
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", ADA_ENCODER_ADDR)
        self._config.setdefault("encoder_type", ENCODER_ADA)
        self._config.setdefault("initial_position", 0)
        self._config.setdefault("min_position", 0)
        self._config.setdefault("max_position", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            enc_type = int(self._config.get("encoder_type", ENCODER_ADA))
            if enc_type == ENCODER_ADA:
                d = await i2c.read_i2c_block_data(self._addr, ADA_ENCODER_REG_POSITION, 5)
                raw_pos = (d[0] << 16) | (d[1] << 8) | d[2]
                btn_state = d[4] & 1
                self._counter = raw_pos
                self._button_state = btn_state
            elif enc_type == ENCODER_M5:
                d = await i2c.read_i2c_block_data(self._addr, M5STACK_REG_POSITION, 4)
                self._counter = (d[0] << 8) | d[1]
                self._button_state = d[2] & 1
            else:
                d = await i2c.read_i2c_block_data(self._addr, DFROBOT_REG_POSITION_H, 2)
                self._counter = (d[0] << 8) | d[1]
                btn = await i2c.read_byte_data(self._addr, 0x02)
                self._button_state = btn & 1
            event.data["values"] = {"Counter": self._counter, "State": self._button_state}
            return True
        except Exception as e:
            logger.error("I2C Rotary read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        enc_opts = [
            {"value": ENCODER_ADA, "label": "Adafruit I2C Encoder"},
            {"value": ENCODER_M5, "label": "M5Stack Encoder"},
            {"value": ENCODER_DFR, "label": "DFRobot Encoder"},
        ]
        event.data["form"] = [
            {"name": "encoder_type", "label": "Encoder type", "type": "select", "value": self._config.get("encoder_type", ENCODER_ADA), "options": enc_opts},
            {"name": "address", "label": "I2C Address", "type": "number", "value": self._config.get("address", ADA_ENCODER_ADDR)},
            {"name": "initial_position", "label": "Initial encoder position", "type": "number", "value": self._config.get("initial_position", 0)},
            {"name": "min_position", "label": "Lowest encoder position", "type": "number", "value": self._config.get("min_position", 0)},
            {"name": "max_position", "label": "Highest encoder position", "type": "number", "value": self._config.get("max_position", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        try:
            self._addr = int(self._config.get("address", ADA_ENCODER_ADDR))
        except (ValueError, TypeError):
            self._addr = ADA_ENCODER_ADDR
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == self._addr

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Counter": 0, "State": 0}
