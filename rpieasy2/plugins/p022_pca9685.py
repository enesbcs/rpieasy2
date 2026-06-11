from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_NONE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p022")

PCA9685_ADDR = 0x40
PCA9685_REG_MODE1 = 0x00
PCA9685_REG_MODE2 = 0x01
PCA9685_REG_LED0_ON_L = 0x06
PCA9685_REG_PRESCALE = 0xFE

PCA9685_OSCILLATOR = 25000000


def _calc_prescale(freq: int) -> int:
    return max(3, min(0xFF, int(round(PCA9685_OSCILLATOR / (4096 * freq)) - 1)))


class P022PCA9685(PluginBase):
    PLUGIN_ID = 22
    PLUGIN_NAME = "Extra IO - PCA9685"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_NONE,
        ports=1,
        custom=True,
        exit_task_before_save=False,
    )
    I2C_ADDRESSES = [0x40 + i for i in range(62)]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = PCA9685_ADDR

    async def _write_reg(self, reg: int, val: int) -> None:
        await self._hw.i2c.write_byte_data(self._addr, reg, val)

    async def _read_reg(self, reg: int) -> int:
        return await self._hw.i2c.read_byte_data(self._addr, reg)

    async def _init_pca(self) -> None:
        mode1 = await self._read_reg(PCA9685_REG_MODE1)
        await self._write_reg(PCA9685_REG_MODE1, mode1 | 0x10)
        freq = int(self._config.get("frequency", 1000))
        prescale = _calc_prescale(freq)
        await self._write_reg(PCA9685_REG_MODE1, (mode1 & 0x7F) | 0x10)
        await self._write_reg(PCA9685_REG_PRESCALE, prescale)
        await self._write_reg(PCA9685_REG_MODE1, mode1 & 0xEF)
        await self._write_reg(PCA9685_REG_MODE1, (mode1 & 0xEF) | 0x80)
        mode2 = int(self._config.get("mode2", 0x10))
        await self._write_reg(PCA9685_REG_MODE2, mode2)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", PCA9685_ADDR))
        except (ValueError, TypeError):
            self._addr = PCA9685_ADDR
        if not self._hw:
            return False
        try:
            await self._init_pca()
            return True
        except Exception as e:
            logger.error("PCA9685 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", PCA9685_ADDR)
        self._config.setdefault("mode2", 0x10)
        self._config.setdefault("frequency", 1000)
        self._config.setdefault("range", 4095)
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "").lower()
        params = event.data.get("params", {})
        if command in ("pcapwm", "pwm"):
            pin = int(params.get("pin", 0))
            duty = int(params.get("duty", 0))
            if 0 <= pin <= 15:
                rng = int(self._config.get("range", 4095))
                off = min(4095, int(duty * 4095 / rng) if rng > 0 else 0)
                reg = PCA9685_REG_LED0_ON_L + pin * 4
                await self._hw.i2c.write_i2c_block_data(self._addr, reg, [0x00, 0x00, off & 0xFF, (off >> 8) & 0xFF])
                return True
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": 0x40 + i, "label": f"0x{0x40 + i:02X}"} for i in range(62)]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", PCA9685_ADDR), "options": addr_opts},
            {"name": "mode2", "label": "MODE2", "type": "number", "value": self._config.get("mode2", 0x10)},
            {"name": "frequency", "label": "Frequency (24-1526)", "type": "number", "value": self._config.get("frequency", 1000)},
            {"name": "range", "label": "Range (1-10000)", "type": "number", "value": self._config.get("range", 4095)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", PCA9685_ADDR))
        if self._hw:
            try:
                await self._init_pca()
            except Exception as e:
                logger.error("PCA9685 re-init failed: %s", e)
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x40 <= addr <= 0x7F

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"PWM": 0}
