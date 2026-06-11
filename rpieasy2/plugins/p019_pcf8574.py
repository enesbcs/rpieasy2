from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.hw.base import EDGE_FALLING
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p019")

PCF8574_BASE_ADDR = 0x20
PCF8574A_BASE_ADDR = 0x38

PIN_MODE_INPUT = 0
PIN_MODE_OUTPUT = 2


class P019PCF8574(PluginBase):
    PLUGIN_ID = 19
    PLUGIN_NAME = "Switch input - PCF8574"
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
    I2C_ADDRESSES = [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27,
                     0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F]

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._int_pin: int = -1
        self._shadow: int = 0xFF
        self._pin_bit: int = 0
        self._i2c_addr: int = 0
        self._pin_mode: int = PIN_MODE_INPUT
        self._last_input_val: int = -1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._int_pin = int(self._config.get("int_pin", -1))
        except (ValueError, TypeError):
            self._int_pin = -1
        try:
            addr = int(self._config.get("address", PCF8574_BASE_ADDR))
        except (ValueError, TypeError):
            addr = PCF8574_BASE_ADDR
        try:
            port = int(self._config.get("port", 0))
        except (ValueError, TypeError):
            port = 0
        self._pin_bit = 1 << (port % 8)
        try:
            self._pin_mode = int(self._config.get("pin_mode", PIN_MODE_INPUT))
        except (ValueError, TypeError):
            self._pin_mode = PIN_MODE_INPUT
        offset = port // 8
        self._i2c_addr = addr + offset
        if self._i2c_addr >= 0x38:
            self._i2c_addr = 0x20 + (self._i2c_addr - 0x38)
        if self._pin_mode == PIN_MODE_OUTPUT:
            self._shadow = 0xFF & ~self._pin_bit
            if self._hw:
                await self._hw.i2c.write_byte_data(self._i2c_addr, 0x00, self._shadow)
        else:
            self._shadow = 0xFF
            if self._hw:
                r = await self._hw.i2c.read_byte_data(self._i2c_addr, 0x00)
                if self._int_pin > 0 and self._hw:
                    self._hw.gpio.claim_input(self._int_pin)
                    self._hw.gpio.watch(self._int_pin, EDGE_FALLING, self._int_cb)
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._hw and self._int_pin > 0:
            try:
                self._hw.gpio.unwatch(self._int_pin)
            except Exception:
                pass
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", PCF8574_BASE_ADDR)
        self._config.setdefault("port", 0)
        self._config.setdefault("int_pin", -1)
        self._config.setdefault("pin_mode", PIN_MODE_INPUT)
        return True

    def _int_cb(self, gpio: int, level: int, ts: int) -> None:
        asyncio.ensure_future(self._on_int())

    async def _on_int(self) -> None:
        try:
            val = await self._hw.i2c.read_byte_data(self._i2c_addr, 0x00)
            try:
                pin = int(self._config.get("port", 0)) % 8
            except (ValueError, TypeError):
                pin = 0
            cur = (val >> pin) & 1
            if cur != self._last_input_val:
                self._last_input_val = cur
                await get_event_bus().publish(Event(
                    type="PLUGIN_READ", task_index=self._task_index,
                    data={"values": {"State": cur}},
                ))
        except Exception:
            pass

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            if self._pin_mode == PIN_MODE_OUTPUT:
                try:
                    val = (self._shadow >> (int(self._config.get("port", 0)) % 8)) & 1
                except (ValueError, TypeError):
                    val = (self._shadow >> 0) & 1
                event.data["values"] = {"State": val}
                return True
            state = await self._hw.i2c.read_byte_data(self._i2c_addr, 0x00)
            try:
                pin = int(self._config.get("port", 0)) % 8
            except (ValueError, TypeError):
                pin = 0
            value = (state >> pin) & 1
            event.data["values"] = {"State": value}
            return True
        except Exception as e:
            logger.error("PCF8574 read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = event.data.get("command", "").strip().lower()
        parts = command.split(",")
        if not parts:
            return False
        cmd = parts[0]
        if cmd == "pcfgpio" and len(parts) >= 3:
            return await self._pcfgpio(parts[1], parts[2])
        if cmd == "pcfpulse" and len(parts) >= 3:
            return await self._pcfpulse(parts[1], parts[2], parts[3] if len(parts) > 3 else "100")
        if cmd == "pcflongpulse" and len(parts) >= 3:
            return await self._pcflongpulse(parts[1], parts[2], parts[3] if len(parts) > 3 else "2")
        if self._pin_mode == PIN_MODE_OUTPUT:
            val = 1 if command in ("1", "on") else 0
            return await self._write_pin(val)
        return False

    async def _resolve_pin(self, pin_str: str) -> tuple[int, int] | None:
        try:
            epin = int(pin_str.strip())
        except ValueError:
            return None
        i2c_addr = PCF8574_BASE_ADDR + (epin - 1) // 8
        local_pin = (epin - 1) % 8
        return i2c_addr, local_pin

    async def _write_i2c_pin(self, i2c_addr: int, local_pin: int, val: int) -> bool:
        try:
            raw = await self._hw.i2c.read_byte_data(i2c_addr, 0x00)
        except Exception:
            raw = 0xFF
        if val:
            raw |= (1 << local_pin)
        else:
            raw &= ~(1 << local_pin)
        await self._hw.i2c.write_byte_data(i2c_addr, 0x00, raw)
        return True

    async def _write_pin(self, val: int) -> bool:
        if not self._hw:
            return False
        if val:
            self._shadow |= self._pin_bit
        else:
            self._shadow &= ~self._pin_bit
        await self._hw.i2c.write_byte_data(self._i2c_addr, 0x00, self._shadow)
        return True

    async def _pcfgpio(self, pin_str: str, val_str: str) -> bool:
        try:
            epin = int(pin_str.strip())
            val = 1 if int(val_str.strip()) else 0
        except ValueError:
            return False
        resolved = await self._resolve_pin(pin_str)
        if resolved is None:
            return False
        i2c_addr, local_pin = resolved
        return await self._write_i2c_pin(i2c_addr, local_pin, val)

    async def _pcfpulse(self, pin_str: str, val_str: str, dur_str: str) -> bool:
        try:
            epin = int(pin_str.strip())
            val = 1 if int(val_str.strip()) else 0
            dur_ms = float(dur_str.strip())
        except ValueError:
            return False
        resolved = await self._resolve_pin(pin_str)
        if resolved is None:
            return False
        i2c_addr, local_pin = resolved
        await self._write_i2c_pin(i2c_addr, local_pin, val)
        await asyncio.sleep(dur_ms / 1000)
        await self._write_i2c_pin(i2c_addr, local_pin, 1 - val)
        return True

    async def _pcflongpulse(self, pin_str: str, val_str: str, dur_str: str) -> bool:
        try:
            epin = int(pin_str.strip())
            val = 1 if int(val_str.strip()) else 0
            dur_s = float(dur_str.strip())
        except ValueError:
            return False
        resolved = await self._resolve_pin(pin_str)
        if resolved is None:
            return False
        i2c_addr, local_pin = resolved
        await self._write_i2c_pin(i2c_addr, local_pin, val)
        asyncio.ensure_future(self._delayed_write(i2c_addr, local_pin, 1 - val, dur_s))
        return True

    async def _delayed_write(self, i2c_addr: int, local_pin: int, val: int, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        await self._write_i2c_pin(i2c_addr, local_pin, val)

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = []
        for a in range(0x20, 0x28):
            addr_opts.append({"value": a, "label": f"0x{a:02X}"})
        for a in range(0x38, 0x40):
            addr_opts.append({"value": a, "label": f"0x{a:02X} (PCF8574A)"})
        port_opts = [{"value": i + 1, "label": f"P{i}"} for i in range(8)]
        mode_opts = [
            {"value": 0, "label": "Input"},
            {"value": 2, "label": "Output"},
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", PCF8574_BASE_ADDR), "options": addr_opts},
            {"name": "port", "label": "Port", "type": "select", "value": self._config.get("port", 0), "options": port_opts},
            {"name": "pin_mode", "label": "Type", "type": "select", "value": self._config.get("pin_mode", PIN_MODE_INPUT), "options": mode_opts},
            {"name": "int_pin", "label": "Interrupt Pin", "type": "number", "value": self._config.get("int_pin", -1)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return (0x20 <= addr <= 0x27) or (0x38 <= addr <= 0x3F)

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        try:
            addr = int(self._config.get("address", PCF8574_BASE_ADDR))
        except (ValueError, TypeError):
            addr = PCF8574_BASE_ADDR
        try:
            port = int(self._config.get("port", 0))
        except (ValueError, TypeError):
            port = 0
        if addr >= 0x38:
            addr = 0x20 + (addr - 0x38)
        event.data["address"] = addr + offset
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"State": 0}
