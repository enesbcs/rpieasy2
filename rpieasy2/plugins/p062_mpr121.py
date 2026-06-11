from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p062")

MPR121_SOFT_RESET = 0x80
MPR121_ELECTRODE_CFG = 0x5E
MPR121_TOUCH_STATUS = 0x00

MPR121_MHD_RISING = 0x2B
MPR121_NHD_RISING = 0x2C
MPR121_NCL_RISING = 0x2D
MPR121_FDL_RISING = 0x2E
MPR121_MHD_FALLING = 0x2F
MPR121_NHD_FALLING = 0x30
MPR121_NCL_FALLING = 0x31
MPR121_FDL_FALLING = 0x32
MPR121_FILTER_CFG = 0x5D
MPR121_DEBOUNCE = 0x5B

DEFAULT_SETTINGS = [
    (MPR121_MHD_RISING, 0x01),
    (MPR121_NHD_RISING, 0x01),
    (MPR121_NCL_RISING, 0x00),
    (MPR121_FDL_RISING, 0x00),
    (MPR121_MHD_FALLING, 0x01),
    (MPR121_NHD_FALLING, 0x01),
    (MPR121_NCL_FALLING, 0xFF),
    (MPR121_FDL_FALLING, 0x02),
]

TOUCH_THRESHOLD_DEFAULT = 0x0F
RELEASE_THRESHOLD_DEFAULT = 0x0A


class P062MPR121(PluginBase):
    PLUGIN_ID = 62
    PLUGIN_NAME = "Keypad - MPR121 Touch"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        custom_vtype_var=True,
    )
    I2C_ADDRESSES = [0x5A, 0x5B, 0x5C, 0x5D]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x5A
        self._i2c = None
        self._int_pin: int = -1
        self._scancode_mode: bool = False
        self._initialized: bool = False
        self._last_touch: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("i2c_addr", 0x5A))
        except (ValueError, TypeError):
            self._addr = 0x5A
        try:
            self._int_pin = int(self._config.get("int_pin", -1))
        except (ValueError, TypeError):
            self._int_pin = -1
        self._scancode_mode = bool(self._config.get("scancode_mode", False))
        if not self._hw:
            return False
        self._i2c = self._hw.i2c
        if not self._i2c:
            return False
        self._initialized = await self._init_device()
        if self._initialized and self._int_pin >= 0:
            self._hw.gpio.claim_input(self._int_pin)
        return self._initialized

    async def _write_reg(self, reg: int, value: int) -> None:
        if not self._i2c:
            return
        try:
            await self._i2c.write_byte_data(self._addr, reg, value)
        except Exception as e:
            logger.debug("MPR121 write reg 0x%02X failed: %s", reg, e)

    async def _read_word(self, reg: int) -> int:
        if not self._i2c:
            return 0
        try:
            return await self._i2c.read_word_data(self._addr, reg)
        except Exception as e:
            logger.debug("MPR121 read word failed: %s", e)
            return 0

    async def _init_device(self) -> bool:
        try:
            await self._i2c.write_byte_data(self._addr, MPR121_SOFT_RESET, 0x63)
            await asyncio.sleep(0.01)
            await self._i2c.write_byte_data(self._addr, MPR121_ELECTRODE_CFG, 0x00)
            await asyncio.sleep(0.01)
        except Exception as e:
            logger.error("MPR121 reset failed: %s", e)
            return False
        for reg, val in DEFAULT_SETTINGS:
            await self._write_reg(reg, val)
        await self._write_reg(MPR121_FILTER_CFG, 0x04)
        await self._write_reg(MPR121_DEBOUNCE, 0x00)
        for ele in range(12):
            base = 0x41 + ele * 2
            await self._write_reg(base, TOUCH_THRESHOLD_DEFAULT)
            await self._write_reg(base + 1, RELEASE_THRESHOLD_DEFAULT)
        await self._write_reg(MPR121_ELECTRODE_CFG, 0x0C)
        return True

    async def _read_touch(self) -> int:
        return await self._read_word(MPR121_TOUCH_STATUS) & 0x0FFF

    @staticmethod
    def _key_index(scancode: int) -> int:
        for i in range(12):
            if scancode & (1 << i):
                return i
        return 0

    async def _handle_touch(self) -> bool | None:
        touch = await self._read_touch()
        if touch == self._last_touch:
            return None
        self._last_touch = touch
        return True

    async def _get_value(self) -> int:
        touch = self._last_touch
        if touch == 0:
            return 0
        if self._scancode_mode:
            return touch
        return self._key_index(touch)

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if not self._initialized:
            return None
        changed = await self._handle_touch()
        if changed:
            val = await self._get_value()
            event.data["values"] = {"ScanCode": float(val)}
            return True
        return None

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._initialized:
            return False
        changed = await self._handle_touch()
        val = await self._get_value()
        event.data["values"] = {"ScanCode": float(val)}
        return changed or val > 0

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("i2c_addr", 0x5A)
        self._config.setdefault("int_pin", -1)
        self._config.setdefault("scancode_mode", False)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "i2c_addr", "label": "I2C Address", "type": "select",
             "value": self._config.get("i2c_addr", 0x5A),
             "options": [
                 {"value": a, "label": hex(a)} for a in self.I2C_ADDRESSES
             ]},
            {"name": "int_pin", "label": "Interrupt Pin", "type": "number",
             "value": self._config.get("int_pin", -1)},
            {"name": "scancode_mode", "label": "ScanCode (raw bitmask)", "type": "checkbox",
             "value": self._config.get("scancode_mode", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.par1 in self.I2C_ADDRESSES

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "Interrupt Pin", "number": 1},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"ScanCode": 0.0}
