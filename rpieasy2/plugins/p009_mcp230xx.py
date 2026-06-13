from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.hw.base import EDGE_BOTH
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p009")

MCP23017_BASE_ADDR = 0x20
MCP23008_BASE_ADDR = 0x20

MCP23017_REG_IODIRA = 0x00
MCP23017_REG_IODIRB = 0x01
MCP23017_REG_GPIOA = 0x12
MCP23017_REG_GPIOB = 0x13
MCP23017_REG_GPPUA = 0x0C
MCP23017_REG_GPPUB = 0x0D
MCP23017_REG_GPINTENA = 0x04
MCP23017_REG_GPINTENB = 0x05
MCP23017_REG_INTCONA = 0x08
MCP23017_REG_INTCONB = 0x09
MCP23017_REG_DEFVALA = 0x06
MCP23017_REG_DEFVALB = 0x07
MCP23017_REG_IOCON = 0x0A

MCP23008_REG_IODIR = 0x00
MCP23008_REG_GPIO = 0x09
MCP23008_REG_GPPU = 0x06
MCP23008_REG_GPINTEN = 0x02
MCP23008_REG_INTCON = 0x05
MCP23008_REG_DEFVAL = 0x03
MCP23008_REG_IOCON = 0x05

MCP23017_PINS = 16
MCP23008_PINS = 8

PIN_MODE_INPUT = 0
PIN_MODE_INPUT_PULLUP = 1
PIN_MODE_OUTPUT = 2


class P009MCP230xx(PluginBase):
    PLUGIN_ID = 9
    PLUGIN_NAME = "Extra IO - MCP23017/MCP23008"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        inverse_logic_option=True,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )
    I2C_ADDRESSES = [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._i2c_addr: int = 0
        self._local_pin: int = 0
        self._bank: int = 0
        self._is_23008: bool = False
        self._int_pin: int = -1
        self._last_value: int = -1

    async def _iodir_reg(self) -> int:
        return MCP23017_REG_IODIRA + self._bank if not self._is_23008 else MCP23008_REG_IODIR

    async def _gpio_reg(self) -> int:
        return MCP23017_REG_GPIOA + self._bank if not self._is_23008 else MCP23008_REG_GPIO

    async def _gppu_reg(self) -> int:
        return MCP23017_REG_GPPUA + self._bank if not self._is_23008 else MCP23008_REG_GPPU

    async def _gpinten_reg(self) -> int:
        return MCP23017_REG_GPINTENA + self._bank if not self._is_23008 else MCP23008_REG_GPINTEN

    async def _setup_pin(self) -> None:
        i2c = self._hw.i2c
        try:
            pin_mode = int(self._config.get("pin_mode", PIN_MODE_INPUT))
        except (ValueError, TypeError):
            pin_mode = PIN_MODE_INPUT
        iodir = await i2c.read_byte_data(self._i2c_addr, await self._iodir_reg())
        if pin_mode == PIN_MODE_OUTPUT:
            iodir &= ~(1 << self._local_pin)
            await i2c.write_byte_data(self._i2c_addr, await self._iodir_reg(), iodir)
            await i2c.write_byte_data(self._i2c_addr, await self._gpio_reg(), 0)
        else:
            iodir |= (1 << self._local_pin)
            await i2c.write_byte_data(self._i2c_addr, await self._iodir_reg(), iodir)
            if pin_mode == PIN_MODE_INPUT_PULLUP:
                gppu = await i2c.read_byte_data(self._i2c_addr, await self._gppu_reg())
                gppu |= (1 << self._local_pin)
                await i2c.write_byte_data(self._i2c_addr, await self._gppu_reg(), gppu)

    async def _setup_interrupt(self, task_index: int) -> None:
        if self._int_pin > 0 and self._hw and self._config.get("pin_mode", PIN_MODE_INPUT) != PIN_MODE_OUTPUT:
            gpint = await self._hw.i2c.read_byte_data(self._i2c_addr, await self._gpinten_reg())
            gpint |= (1 << self._local_pin)
            await self._hw.i2c.write_byte_data(self._i2c_addr, await self._gpinten_reg(), gpint)
            self._hw.gpio.claim_input(self._int_pin)
            self._hw.gpio.watch(self._int_pin, EDGE_BOTH, self._int_cb)

    def _int_cb(self, gpio: int, level: int, ts: int) -> None:
        asyncio.ensure_future(self._on_int(level))

    async def _on_int(self, level: int) -> None:
        try:
            cur = 1 if level else 0
            if cur == self._last_value:
                return
            self._last_value = cur
            await get_event_bus().publish(Event(
                type="PLUGIN_READ", task_index=self._task_index,
                data={"values": {"State": cur}},
            ))
        except Exception:
            pass

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        if not self._hw:
            return False
        try:
            self._is_23008 = int(self._config.get("chip_type", 0)) == 1
        except (ValueError, TypeError):
            self._is_23008 = False
        try:
            addr = int(self._config.get("address", MCP23017_BASE_ADDR))
        except (ValueError, TypeError):
            addr = MCP23017_BASE_ADDR
        try:
            port = int(self._config.get("port", 0))
        except (ValueError, TypeError):
            port = 0
        pins_per_chip = MCP23008_PINS if self._is_23008 else MCP23017_PINS
        chip_offset = (port - 1) // pins_per_chip if port > 0 else 0
        local_pin_num = ((port - 1) % pins_per_chip) if port > 0 else 0
        self._i2c_addr = addr + chip_offset
        self._local_pin = local_pin_num % 8
        self._bank = local_pin_num // 8
        try:
            self._int_pin = int(self._config.get("int_pin", -1))
        except (ValueError, TypeError):
            self._int_pin = -1
        try:
            if not self._is_23008:
                await self._hw.i2c.write_byte_data(self._i2c_addr, MCP23017_REG_IOCON, 0x02)
            await self._setup_pin()
            await self._setup_interrupt(event.task_index)
            return True
        except Exception as e:
            logger.error("MCP230xx init failed: %s", e)
            return False

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._hw and self._int_pin > 0:
            try:
                self._hw.gpio.unwatch(self._int_pin)
            except Exception:
                pass
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", MCP23017_BASE_ADDR)
        self._config.setdefault("port", 0)
        self._config.setdefault("pin_mode", PIN_MODE_INPUT)
        self._config.setdefault("chip_type", 0)
        self._config.setdefault("int_pin", -1)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            gpio = await self._hw.i2c.read_byte_data(self._i2c_addr, await self._gpio_reg())
            val = (gpio >> self._local_pin) & 1
            event.data["values"] = {"State": val}
            return True
        except Exception as e:
            logger.error("MCP230xx read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "").strip().lower()
        if command:
            parts = command.split(",")
            cmd = parts[0]
            if cmd == "mcpgpio" and len(parts) >= 3:
                return await self._mcpgpio(parts[1], parts[2])
            if cmd == "mcppulse" and len(parts) >= 3:
                dur = parts[3] if len(parts) > 3 else "100"
                return await self._mcppulse(parts[1], parts[2], dur)
        pin_mode = int(self._config.get("pin_mode", PIN_MODE_INPUT))
        if pin_mode != PIN_MODE_OUTPUT:
            return False
        values = event.data.get("values", {})
        if values:
            val = 1 if str(list(values.values())[0]).lower() in ("1", "on", "true") else 0
        elif command in ("1", "on", "0", "off"):
            val = 1 if command in ("1", "on") else 0
        else:
            return False
        return await self._write_output(val)

    async def _resolve_epin(self, epin: int) -> tuple[int, int] | None:
        pins_per = MCP23008_PINS if self._is_23008 else MCP23017_PINS
        addr = MCP23017_BASE_ADDR + (epin - 1) // pins_per
        local = ((epin - 1) % pins_per) if epin > 0 else 0
        return addr, local

    async def _write_remote(self, i2c_addr: int, local: int, val: int) -> bool:
        try:
            bank = local // 8
            lpin = local % 8
            if not self._is_23008:
                iodir = await self._hw.i2c.read_byte_data(i2c_addr, MCP23017_REG_IODIRA + bank)
                if iodir & (1 << lpin):
                    iodir &= ~(1 << lpin)
                    await self._hw.i2c.write_byte_data(i2c_addr, MCP23017_REG_IODIRA + bank, iodir)
                gpio_reg = MCP23017_REG_GPIOA + bank
            else:
                iodir = await self._hw.i2c.read_byte_data(i2c_addr, MCP23008_REG_IODIR)
                if iodir & (1 << lpin):
                    iodir &= ~(1 << lpin)
                    await self._hw.i2c.write_byte_data(i2c_addr, MCP23008_REG_IODIR, iodir)
                gpio_reg = MCP23008_REG_GPIO
            gpio = await self._hw.i2c.read_byte_data(i2c_addr, gpio_reg)
            if val:
                gpio |= (1 << lpin)
            else:
                gpio &= ~(1 << lpin)
            await self._hw.i2c.write_byte_data(i2c_addr, gpio_reg, gpio)
            return True
        except Exception as e:
            logger.error("MCP write failed: %s", e)
            return False

    async def _write_output(self, val: int) -> bool:
        try:
            gpio = await self._hw.i2c.read_byte_data(self._i2c_addr, await self._gpio_reg())
            if val:
                gpio |= (1 << self._local_pin)
            else:
                gpio &= ~(1 << self._local_pin)
            await self._hw.i2c.write_byte_data(self._i2c_addr, await self._gpio_reg(), gpio)
            return True
        except Exception as e:
            logger.error("MCP output failed: %s", e)
            return False

    async def _mcpgpio(self, pin_str: str, val_str: str) -> bool:
        try:
            epin = int(pin_str.strip())
            val = 1 if int(val_str.strip()) else 0
        except ValueError:
            return False
        resolved = await self._resolve_epin(epin)
        if resolved is None:
            return False
        i2c_addr, local = resolved
        return await self._write_remote(i2c_addr, local, val)

    async def _mcppulse(self, pin_str: str, val_str: str, dur_str: str) -> bool:
        try:
            epin = int(pin_str.strip())
            val = 1 if int(val_str.strip()) else 0
            dur_ms = float(dur_str.strip())
        except ValueError:
            return False
        resolved = await self._resolve_epin(epin)
        if resolved is None:
            return False
        i2c_addr, local = resolved
        await self._write_remote(i2c_addr, local, val)
        await asyncio.sleep(dur_ms / 1000)
        await self._write_remote(i2c_addr, local, 1 - val)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x20, 0x28)]
        try:
            chip = int(self._config.get("chip_type", 0))
        except (ValueError, TypeError):
            chip = 0
        max_pins = MCP23008_PINS if chip else MCP23017_PINS
        port_opts = []
        for i in range(max_pins):
            if chip:
                port_opts.append({"value": i + 1, "label": f"GP{i}"})
            else:
                bank = 'A' if i < 8 else 'B'
                port_opts.append({"value": i + 1, "label": f"P{bank}{i % 8}"})
        mode_opts = [
            {"value": 0, "label": "Input"},
            {"value": 1, "label": "Input-Pullup"},
            {"value": 2, "label": "Output"},
        ]
        chip_opts = [
            {"value": 0, "label": "MCP23017"},
            {"value": 1, "label": "MCP23008"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MCP23017_BASE_ADDR), "options": addr_opts},
            {"name": "port", "label": "Port", "type": "select", "value": self._config.get("port", 0), "options": port_opts},
            {"name": "pin_mode", "label": "Type", "type": "select", "value": self._config.get("pin_mode", PIN_MODE_INPUT), "options": mode_opts},
            {"name": "chip_type", "label": "Chip", "type": "select", "value": self._config.get("chip_type", 0), "options": chip_opts},
            {"name": "int_pin", "label": "Interrupt Pin", "type": "number", "value": self._config.get("int_pin", -1)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x20 <= addr <= 0x27

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        try:
            addr = int(self._config.get("address", MCP23017_BASE_ADDR))
        except (ValueError, TypeError):
            addr = MCP23017_BASE_ADDR
        try:
            port = int(self._config.get("port", 0))
        except (ValueError, TypeError):
            port = 0
        try:
            pins_per = MCP23008_PINS if int(self._config.get("chip_type", 0)) else MCP23017_PINS
        except (ValueError, TypeError):
            pins_per = MCP23017_PINS
        chip_offset = (port - 1) // pins_per if port > 0 else 0
        event.data["address"] = addr + chip_offset
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"State": 0}
