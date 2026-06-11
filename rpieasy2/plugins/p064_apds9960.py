from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p064")

APDS9960_ADDR = 0x39

APDS9960_REG_ENABLE = 0x80
APDS9960_REG_ATIME = 0x9C
APDS9960_REG_WTIME = 0xA0
APDS9960_REG_PPULSE = 0xA6
APDS9960_REG_CONTROL = 0xA7
APDS9960_REG_CONFIG1 = 0xA5
APDS9960_REG_CONFIG2 = 0xA8
APDS9960_REG_ID = 0x8C
APDS9960_REG_STATUS = 0x8F
APDS9960_REG_CDATAL = 0x94
APDS9960_REG_RDATAL = 0x96
APDS9960_REG_GDATAL = 0x98
APDS9960_REG_BDATAL = 0x9A
APDS9960_REG_PDATA = 0x9C
APDS9960_REG_GCONF1 = 0xFC
APDS9960_REG_GCONF2 = 0xFD
APDS9960_REG_GPULSE = 0xAB
APDS9960_REG_GFLVL = 0xAE
APDS9960_REG_GSTATUS = 0xAC
APDS9960_REG_GFIFO_U = 0xB0
APDS9960_REG_GFIFO_D = 0xB1
APDS9960_REG_GFIFO_L = 0xB2
APDS9960_REG_GFIFO_R = 0xB3

PGAIN_1X = 0
PGAIN_2X = 1
PGAIN_4X = 2
PGAIN_8X = 3

LED_DRIVE_100MA = 0
LED_DRIVE_50MA = 1
LED_DRIVE_25MA = 2
LED_DRIVE_12_5MA = 3

LED_BOOST_100 = 0
LED_BOOST_150 = 1
LED_BOOST_200 = 2
LED_BOOST_300 = 3

DIR_NONE = -1
DIR_UP = 1
DIR_DOWN = 2
DIR_LEFT = 3
DIR_RIGHT = 4
DIR_NEAR = 5
DIR_FAR = 6

GESTURE_NAMES = {
    DIR_UP: "UP",
    DIR_DOWN: "DOWN",
    DIR_LEFT: "LEFT",
    DIR_RIGHT: "RIGHT",
    DIR_NEAR: "NEAR",
    DIR_FAR: "FAR",
}

MODE_GPL = 0
MODE_RGB = 1


class P064APDS9960(PluginBase):
    PLUGIN_ID = 64
    PLUGIN_NAME = "Gesture - APDS9960"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES = [0x39]

    def __init__(self):
        super().__init__()
        self._addr: int = APDS9960_ADDR
        self._config: dict[str, Any] = {}
        self._gesture_available = False

    async def _read_byte(self, reg: int) -> int:
        return await self._hw.i2c.read_byte_data(self._addr, reg)

    async def _write_byte(self, reg: int, val: int) -> None:
        await self._hw.i2c.write_byte_data(self._addr, reg, val)

    async def _read_word(self, reg: int) -> int:
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 2)
        return d[0] | (d[1] << 8) if len(d) >= 2 else 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = APDS9960_ADDR
        if not self._hw:
            return False
        try:
            await self._write_byte(APDS9960_REG_ENABLE, 0x00)
            await asyncio.sleep(0.01)
            await self._write_byte(APDS9960_REG_ENABLE, 0x01)
            await asyncio.sleep(0.01)
            try:
                ggain = int(self._config.get("ggain", PGAIN_4X))
            except (ValueError, TypeError):
                ggain = PGAIN_4X
            try:
                gldrive = int(self._config.get("gldrive", LED_DRIVE_100MA))
            except (ValueError, TypeError):
                gldrive = LED_DRIVE_100MA
            try:
                pgain = int(self._config.get("pgain", PGAIN_4X))
            except (ValueError, TypeError):
                pgain = PGAIN_4X
            try:
                again = int(self._config.get("again", PGAIN_4X))
            except (ValueError, TypeError):
                again = PGAIN_4X
            try:
                ldrive = int(self._config.get("ldrive", LED_DRIVE_100MA))
            except (ValueError, TypeError):
                ldrive = LED_DRIVE_100MA
            try:
                lboost = int(self._config.get("lboost", LED_BOOST_300))
            except (ValueError, TypeError):
                lboost = LED_BOOST_300
            control = (pgain & 0x03) | ((again & 0x03) << 2) | ((ldrive & 0x03) << 6)
            await self._write_byte(APDS9960_REG_CONTROL, control)
            await self._write_byte(APDS9960_REG_PPULSE, 0x08)
            await self._write_byte(APDS9960_REG_ATIME, 0xDB)
            await self._write_byte(APDS9960_REG_WTIME, 0x00)
            await self._write_byte(APDS9960_REG_CONFIG1, 0x60)
            await self._write_byte(APDS9960_REG_CONFIG2, 0x01)
            await self._write_byte(APDS9960_REG_GCONF2, lboost & 0x03)
            await self._write_byte(APDS9960_REG_GPULSE, 0xC9)
            await self._write_byte(APDS9960_REG_GCONF1, (ggain & 0x03) << 2 | (gldrive & 0x03))
            enable = 0x01 | 0x02 | 0x04 | 0x08
            await self._write_byte(APDS9960_REG_ENABLE, enable)
            await asyncio.sleep(0.01)
            return True
        except Exception as e:
            logger.error("APDS9960 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("ggain", PGAIN_4X)
        self._config.setdefault("gldrive", LED_DRIVE_100MA)
        self._config.setdefault("lboost", LED_BOOST_300)
        self._config.setdefault("pgain", PGAIN_4X)
        self._config.setdefault("again", PGAIN_4X)
        self._config.setdefault("ldrive", LED_DRIVE_100MA)
        self._config.setdefault("mode", MODE_GPL)
        self._config.setdefault("gesture_event", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            mode = int(self._config.get("mode", MODE_GPL))
            if mode == MODE_GPL:
                proximity = await self._read_byte(APDS9960_REG_PDATA)
                ambient = await self._read_word(APDS9960_REG_CDATAL)
                gesture = await self._read_byte(APDS9960_REG_GSTATUS)
                event.data["values"] = {
                    "Gesture": gesture,
                    "Proximity": proximity,
                    "Light": ambient,
                }
            else:
                r = await self._read_word(APDS9960_REG_RDATAL)
                g = await self._read_word(APDS9960_REG_GDATAL)
                b = await self._read_word(APDS9960_REG_BDATAL)
                event.data["values"] = {"R": r, "G": g, "B": b}
            return True
        except Exception as e:
            logger.error("APDS9960 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        mode_opts = [
            {"value": MODE_GPL, "label": "Gesture/Proximity/Ambient Light Sensor"},
            {"value": MODE_RGB, "label": "R/G/B Colors"},
        ]
        gain_opts = [
            {"value": 0, "label": "1x"},
            {"value": 1, "label": "2x"},
            {"value": 2, "label": "4x"},
            {"value": 3, "label": "8x"},
        ]
        drive_opts = [
            {"value": 0, "label": "100 mA"},
            {"value": 1, "label": "50 mA"},
            {"value": 2, "label": "25 mA"},
            {"value": 3, "label": "12.5 mA"},
        ]
        boost_opts = [
            {"value": 0, "label": "100%"},
            {"value": 1, "label": "150%"},
            {"value": 2, "label": "200%"},
            {"value": 3, "label": "300%"},
        ]
        form = [
            {"name": "mode", "label": "Plugin Mode", "type": "select", "value": self._config.get("mode", MODE_GPL), "options": mode_opts},
            {"name": "ggain", "label": "Gesture Gain", "type": "select", "value": self._config.get("ggain", PGAIN_4X), "options": gain_opts},
            {"name": "gldrive", "label": "Gesture LED Drive", "type": "select", "value": self._config.get("gldrive", LED_DRIVE_100MA), "options": drive_opts},
            {"name": "lboost", "label": "Gesture LED Boost", "type": "select", "value": self._config.get("lboost", LED_BOOST_300), "options": boost_opts},
            {"name": "pgain", "label": "Proximity Gain", "type": "select", "value": self._config.get("pgain", PGAIN_4X), "options": gain_opts},
            {"name": "again", "label": "Ambient Light Sensor Gain", "type": "select", "value": self._config.get("again", PGAIN_4X), "options": gain_opts},
            {"name": "ldrive", "label": "Proximity & ALS LED Drive", "type": "select", "value": self._config.get("ldrive", LED_DRIVE_100MA), "options": drive_opts},
            {"name": "gesture_event", "label": "Separate Gesture events", "type": "checkbox", "value": self._config.get("gesture_event", 0)},
        ]
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == APDS9960_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = APDS9960_ADDR
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        mode = int(task_config.get("mode", MODE_GPL))
        if mode == MODE_GPL:
            return {"Gesture": 0, "Proximity": 0, "Light": 0}
        return {"R": 0, "G": 0, "B": 0}
