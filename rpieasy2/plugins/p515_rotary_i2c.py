from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.hw.base import EDGE_BOTH
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL

logger = logging.getLogger("rpieasy2.plugin.p515")

_SEESAW_STATUS_BASE = 0x00
_SEESAW_GPIO_BASE = 0x01
_SEESAW_INTERRUPT_BASE = 0x0B
_SEESAW_NEOPIXEL_BASE = 0x0E
_SEESAW_ENCODER_BASE = 0x11
_SEESAW_HW_ID_CODE = 0x55
_SEESAW_PIN_BUTTON = 24


class _PixelBuf:
    def __init__(self, size: int, byteorder: str = "RGB"):
        self._size = size
        self._byteorder = byteorder.upper()
        if self._byteorder not in ("RGB", "GRB", "RGBW", "GRBW"):
            self._byteorder = "RGB"
        self._has_white = "W" in self._byteorder
        self._pixels: list[tuple[int, int, int]] = [(0, 0, 0)] * size
        self._brightness: float = 1.0

    def __len__(self) -> int:
        return self._size

    def __setitem__(self, index: int, val: tuple[int, int, int]) -> None:
        if isinstance(val, int):
            r = (val >> 16) & 0xFF
            g = (val >> 8) & 0xFF
            b = val & 0xFF
            val = (r, g, b)
        self._pixels[index] = val

    def __getitem__(self, index: int) -> tuple[int, int, int]:
        return self._pixels[index]

    def fill(self, color: tuple[int, int, int]) -> None:
        for i in range(self._size):
            self[i] = color

    @property
    def brightness(self) -> float:
        return self._brightness

    @brightness.setter
    def brightness(self, value: float) -> None:
        self._brightness = max(0.0, min(1.0, value))

    def _apply_brightness(self, r: int, g: int, b: int) -> tuple[int, int, int]:
        if self._brightness < 1.0:
            r = int(r * self._brightness)
            g = int(g * self._brightness)
            b = int(b * self._brightness)
        return (r, g, b)

    def _pack(self, r: int, g: int, b: int) -> list[int]:
        r, g, b = self._apply_brightness(r, g, b)
        order = self._byteorder.replace("W", "")
        mapping = {"R": r, "G": g, "B": b}
        return [mapping[c] for c in order]

    def transmit(self) -> list[int]:
        result: list[int] = []
        for i in range(self._size):
            r, g, b = self._pixels[i]
            result.extend(self._pack(r, g, b))
        return result


class _Seesaw:
    def __init__(self, i2c, addr: int):
        self._i2c = i2c
        self._addr = addr
        self.hw_id: int = 0
        self._options: int = 0
        self.has_encoder: bool = False
        self.has_neopixel: bool = False
        self.has_gpio: bool = False

    async def init(self) -> bool:
        try:
            ver = await self._read16(_SEESAW_STATUS_BASE, 0x00, 4)
            if len(ver) >= 4:
                self.hw_id = ver[2]
            else:
                return False
            if self.hw_id != _SEESAW_HW_ID_CODE:
                logger.error("Seesaw HW ID mismatch: expected 0x%02x, got 0x%02x", _SEESAW_HW_ID_CODE, self.hw_id)
                return False
            opts = await self._read16(_SEESAW_STATUS_BASE, 0x02, 4)
            if len(opts) >= 4:
                self._options = (opts[0] << 24) | (opts[1] << 16) | (opts[2] << 8) | opts[3]
            self.has_encoder = bool(self._options & (1 << 6))
            self.has_neopixel = bool(self._options & (1 << 5))
            self.has_gpio = bool(self._options & (1 << 4))
            logger.info("Seesaw initialized: hw_id=0x%02x options=0x%08x enc=%s neo=%s gpio=%s",
                        self.hw_id, self._options, self.has_encoder, self.has_neopixel, self.has_gpio)
            return True
        except Exception as e:
            logger.error("Seesaw init error: %s", e)
            return False

    async def _read16(self, reg_hi: int, reg_lo: int, length: int) -> list[int]:
        reg16 = (reg_hi << 8) | reg_lo
        return await self._i2c.read_i2c_block_data16(self._addr, reg16, length)

    async def _write16(self, reg_hi: int, reg_lo: int, data: list[int]) -> None:
        reg16 = (reg_hi << 8) | reg_lo
        await self._i2c.write_i2c_block_data16(self._addr, reg16, data)

    async def sw_reset(self) -> None:
        try:
            await self._write16(_SEESAW_STATUS_BASE, 0x7F, [0xFF])
            await asyncio.sleep(0.5)
        except Exception:
            pass

    async def get_options(self) -> int:
        opts = await self._read16(_SEESAW_STATUS_BASE, 0x02, 4)
        if len(opts) >= 4:
            self._options = (opts[0] << 24) | (opts[1] << 16) | (opts[2] << 8) | opts[3]
        return self._options

    async def encoder_position(self) -> int:
        data = await self._read16(_SEESAW_ENCODER_BASE, 0x04, 4)
        if len(data) >= 4:
            return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        return 0

    async def set_encoder_position(self, pos: int) -> None:
        await self._write16(_SEESAW_ENCODER_BASE, 0x04, [
            (pos >> 24) & 0xFF,
            (pos >> 16) & 0xFF,
            (pos >> 8) & 0xFF,
            pos & 0xFF,
        ])

    async def enable_encoder_interrupt(self) -> None:
        await self._write16(_SEESAW_ENCODER_BASE, 0x05, [0x01])

    async def pin_mode(self, pin: int, mode: int) -> None:
        await self._write16(_SEESAW_GPIO_BASE, 0x03, [pin, mode])

    async def digital_read(self, pin: int) -> int:
        mask = 1 << pin
        data = await self._read16(_SEESAW_GPIO_BASE, 0x00, 4)
        if len(data) >= 4:
            bulk = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
            return 1 if (bulk & mask) else 0
        return 0

    async def set_GPIO_interrupts(self, pins: list[int], enabled: bool) -> None:
        mask = 0
        for p in pins:
            mask |= 1 << p
        if enabled:
            await self._write16(_SEESAW_INTERRUPT_BASE, 0x04, [
                (mask >> 24) & 0xFF,
                (mask >> 16) & 0xFF,
                (mask >> 8) & 0xFF,
                mask & 0xFF,
            ])
        else:
            await self._write16(_SEESAW_INTERRUPT_BASE, 0x05, [
                (mask >> 24) & 0xFF,
                (mask >> 16) & 0xFF,
                (mask >> 8) & 0xFF,
                mask & 0xFF,
            ])

    async def read_GPIO_interrupt_flag(self) -> int:
        data = await self._read16(_SEESAW_INTERRUPT_BASE, 0x00, 4)
        if len(data) >= 4:
            return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        return 0


class _SeesawNeopixel(_PixelBuf):
    def __init__(self, seesaw: _Seesaw, count: int, pin: int = 15, byteorder: str = "RGB"):
        super().__init__(count, byteorder)
        self._seesaw = seesaw
        self._pin = pin
        self._auto_write = True

    async def show(self) -> None:
        data = self.transmit()
        if self._pin == 15:
            await self._seesaw._write16(_SEESAW_NEOPIXEL_BASE, 0x02, [len(data)] + data)
        else:
            await self._seesaw._write16(_SEESAW_NEOPIXEL_BASE, 0x01, [self._pin, len(data)] + data)

    @property
    def auto_write(self) -> bool:
        return self._auto_write

    @auto_write.setter
    def auto_write(self, value: bool) -> None:
        self._auto_write = value


class P515RotaryI2C(PluginBase):
    PLUGIN_ID = 515
    PLUGIN_NAME = "I2C - Adafruit Rotary Encoder"
    PLUGIN_VALUES = 2
    I2C_ADDRESSES = [0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D]
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_DUAL,
        value_count=2,
        send_data_option=True,
        timer_option=True,
        formula_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._seesaw: _Seesaw | None = None
        self._neopixel: _SeesawNeopixel | None = None
        self._i2c_addr: int = 0
        self._step: int = 1
        self._pos_min: int = -360
        self._pos_max: int = 360
        self._counter: int = 0
        self._button: int = 0
        self._initialized: bool = False
        self._int_pin: int = -1
        self._int_flag: bool = False
        self._interrupt_ok: bool = False

    def _int_callback(self, gpio: int, level: int, ts: int) -> None:
        self._int_flag = True

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        addr_str = self._config.get("i2c_addr", 0x36)
        try:
            self._i2c_addr = int(str(addr_str), 16) if isinstance(addr_str, str) else int(addr_str)
        except (ValueError, TypeError):
            self._i2c_addr = 0x36
        try:
            self._step = int(self._config.get("step", 1))
        except (ValueError, TypeError):
            self._step = 1
        try:
            self._pos_min = int(self._config.get("pos_min", -360))
        except (ValueError, TypeError):
            self._pos_min = -360
        try:
            self._pos_max = int(self._config.get("pos_max", 360))
        except (ValueError, TypeError):
            self._pos_max = 360
        try:
            self._int_pin = int(self._config.get("int_pin", -1))
        except (ValueError, TypeError):
            self._int_pin = -1
        try:
            self._counter = int(self._config.get("counter", 0))
        except (ValueError, TypeError):
            self._counter = 0
        self._button = 0
        self._int_flag = False
        if not self._hw or not self._hw.i2c:
            logger.warning("I2C not available")
            return True
        self._seesaw = _Seesaw(self._hw.i2c, self._i2c_addr)
        ok = await self._seesaw.init()
        if not ok:
            logger.error("Seesaw init failed at 0x%02x", self._i2c_addr)
            self._seesaw = None
            return True
        if self._seesaw.has_gpio:
            await self._seesaw.pin_mode(_SEESAW_PIN_BUTTON, 5)
            await self._seesaw.set_GPIO_interrupts([_SEESAW_PIN_BUTTON], True)
        if self._seesaw.has_encoder:
            await self._seesaw.set_encoder_position(self._counter)
            await self._seesaw.enable_encoder_interrupt()
        if self._seesaw.has_neopixel:
            self._neopixel = _SeesawNeopixel(self._seesaw, 1)
            try:
                rv = self._config.get("neo_r", 0)
                gv = self._config.get("neo_g", 0)
                bv = self._config.get("neo_b", 0)
                r = int(rv) if rv is not None else 0
                g = int(gv) if gv is not None else 0
                b = int(bv) if bv is not None else 0
                self._neopixel[0] = (r, g, b)
            except Exception:
                self._neopixel[0] = (0, 0, 0)
            await self._neopixel.show()
        if self._int_pin > 0 and self._hw.gpio:
            try:
                self._hw.gpio.claim_input(self._int_pin)
                self._hw.gpio.watch(self._int_pin, EDGE_BOTH, self._int_callback)
                self._interrupt_ok = True
            except Exception as e:
                logger.warning("GPIO interrupt on pin %d not available, falling back to timer: %s", self._int_pin, e)
                self._interrupt_ok = False
        self._initialized = True
        logger.info("Rotary I2C initialized at 0x%02x step=%d", self._i2c_addr, self._step)
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._int_pin > 0 and self._hw and self._hw.gpio:
            try:
                self._hw.gpio.unwatch(self._int_pin)
            except Exception:
                pass
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._initialized or not self._seesaw:
            return False
        await self._read_sensor()
        event.data["values"] = {"Counter": str(self._counter), "Button": str(self._button)}
        event.data["named_values"] = {"Counter": str(self._counter), "Button": str(self._button)}
        event.data["value_names"] = ["Counter", "Button"]
        return True

    async def _read_sensor(self) -> None:
        if self._seesaw.has_encoder:
            try:
                pos = await self._seesaw.encoder_position()
                self._counter = pos * self._step
                if self._counter < self._pos_min:
                    self._counter = self._pos_min
                    await self._seesaw.set_encoder_position(self._pos_min // self._step)
                elif self._counter > self._pos_max:
                    self._counter = self._pos_max
                    await self._seesaw.set_encoder_position(self._pos_max // self._step)
            except Exception as e:
                logger.debug("Encoder read error: %s", e)
        if self._seesaw.has_gpio:
            try:
                self._button = 1 - (await self._seesaw.digital_read(_SEESAW_PIN_BUTTON))
            except Exception as e:
                logger.debug("Button read error: %s", e)

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if not self._initialized:
            return None
        if self._interrupt_ok and self._int_flag:
            self._int_flag = False
            await self._read_sensor()
        elif not self._interrupt_ok:
            await self._read_sensor()
        return None

    async def on_plugin_write(self, event: Event) -> bool | None:
        cmd = (event.string1 or "").strip().lower()
        if cmd.startswith("rotarypixel"):
            parts = cmd.split(",")
            if len(parts) >= 4 and self._neopixel:
                try:
                    r = int(parts[2].strip())
                    g = int(parts[3].strip())
                    b = int(parts[4].strip()) if len(parts) > 4 else 0
                    bright = int(parts[5].strip()) if len(parts) > 5 else 100
                    self._neopixel.brightness = bright / 100.0
                    self._neopixel[0] = (r, g, b)
                    await self._neopixel.show()
                    return True
                except (ValueError, IndexError):
                    pass
        sv = event.data.get("values", {})
        if sv:
            try:
                val = int(next(iter(sv.values())))
            except (ValueError, TypeError):
                return False
            if self._seesaw:
                await self._seesaw.set_encoder_position(val)
                self._counter = val * self._step
            return True
        return False

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Counter", "Button"]
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("addr", 0)
        event.data["found"] = addr in self.I2C_ADDRESSES
        return True

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["addr"] = self._i2c_addr
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        i2c_opts = [{"value": hex(a), "label": f"0x{a:02X}"} for a in self.I2C_ADDRESSES]
        current_addr_str = hex(self._i2c_addr)
        form = [
            {"name": "i2c_addr", "label": "I2C Address", "type": "select",
             "value": current_addr_str, "options": i2c_opts},
            {"name": "step", "label": "Step (pulses per detent)", "type": "number",
             "value": self._config.get("step", 1), "min": 1, "max": 4},
            {"name": "pos_min", "label": "Min position", "type": "number",
             "value": self._config.get("pos_min", -360)},
            {"name": "pos_max", "label": "Max position", "type": "number",
             "value": self._config.get("pos_max", 360)},
            {"name": "int_pin", "label": "Interrupt GPIO pin (0=disable)", "type": "number",
             "value": self._config.get("int_pin", -1)},
        ]
        if self._seesaw and self._seesaw.has_neopixel:
            neo_r = int(self._config.get("neo_r", 0))
            neo_g = int(self._config.get("neo_g", 0))
            neo_b = int(self._config.get("neo_b", 0))
            form.append({"type": "text", "name": "_neo_header",
                         "label": "NeoPixel Color",
                         "value": "Set default NeoPixel color (0-255)"})
            form.append({"name": "neo_r", "label": "Red", "type": "number",
                         "value": neo_r, "min": 0, "max": 255})
            form.append({"name": "neo_g", "label": "Green", "type": "number",
                         "value": neo_g, "min": 0, "max": 255})
            form.append({"name": "neo_b", "label": "Blue", "type": "number",
                         "value": neo_b, "min": 0, "max": 255})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["i2c_addr"] = "0x36"
        self._config["step"] = 1
        self._config["pos_min"] = -360
        self._config["pos_max"] = 360
        self._config["int_pin"] = -1
        self._config["neo_r"] = 0
        self._config["neo_g"] = 0
        self._config["neo_b"] = 0
        return True
