from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.hw.base import EDGE_FALLING
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_DUAL

logger = logging.getLogger("rpieasy2.plugin.p516")

REG_GCONF = 0x00
REG_INTCONF = 0x01
REG_ESTATUS = 0x02
REG_CVALB4 = 0x03
REG_CVALB3 = 0x04
REG_CVALB2 = 0x05
REG_CVALB1 = 0x06
REG_CMAXB4 = 0x07
REG_CMAXB3 = 0x08
REG_CMAXB2 = 0x09
REG_CMAXB1 = 0x0A
REG_CMINB4 = 0x0B
REG_CMINB3 = 0x0C
REG_CMINB2 = 0x0D
REG_CMINB1 = 0x0E
REG_ISTEPB4 = 0x0F
REG_ISTEPB3 = 0x10
REG_ISTEPB2 = 0x11
REG_ISTEPB1 = 0x12
REG_DPPERIOD = 0x13
REG_ADDRESS = 0x14
REG_IDCODE = 0x70
REG_VERSION = 0x71
REG_I2CADDRESS = 0x72
REG_EEPROMS = 0x81

WRAP_ENABLE = 0x01
WRAP_DISABLE = 0x00
DIRE_LEFT = 0x02
DIRE_RIGHT = 0x00
IPUP_ENABLE = 0x04
IPUP_DISABLE = 0x00
RMOD_X4 = 0x10
RMOD_X2 = 0x08
RMOD_X1 = 0x00

RESET = 0x80

PUSHR = 0x01
PUSHP = 0x02
PUSHD = 0x04
PUSHL = 0x08
RINC = 0x10
RDEC = 0x20
RMAX = 0x40
RMIN = 0x80


class _i2cEncoderMiniLib:
    def __init__(self, i2c, addr: int):
        self._i2c = i2c
        self._addr = addr
        self.stat = 0
        self.gconf = 0

    async def begin(self, conf: int) -> None:
        await self._write8(REG_GCONF, conf & 0xFF)
        self.gconf = conf

    async def reset(self) -> None:
        await self._write8(REG_GCONF, RESET)

    async def updateStatus(self) -> bool:
        self.stat = await self._read8(REG_ESTATUS)
        return self.stat != 0

    def readStatus(self, status: int) -> bool:
        return bool(self.stat & status)

    def readStatusRaw(self) -> int:
        return self.stat

    async def readCounter32(self) -> int:
        return await self._read32(REG_CVALB4)

    async def readCounter16(self) -> int:
        return await self._read16(REG_CVALB2)

    async def readCounter8(self) -> int:
        return await self._read8(REG_CVALB1)

    async def readMax(self) -> int:
        return await self._read32(REG_CMAXB4)

    async def readMin(self) -> int:
        return await self._read32(REG_CMINB4)

    async def readStep(self) -> int:
        return await self._read16(REG_ISTEPB4)

    async def readDoublePushPeriod(self) -> int:
        return await self._read8(REG_DPPERIOD)

    async def readIDCode(self) -> int:
        return await self._read8(REG_IDCODE)

    async def readVersion(self) -> int:
        return await self._read8(REG_VERSION)

    async def readEEPROM(self, add: int) -> int:
        return await self._read8(add)

    async def writeInterruptConfig(self, interrupt: int) -> None:
        await self._write8(REG_INTCONF, interrupt)

    async def autoconfigInterrupt(self) -> None:
        reg = 0
        reg |= RINC | RDEC
        reg |= PUSHP | PUSHR
        await self._write8(REG_INTCONF, reg)

    async def writeCounter(self, value: int) -> None:
        await self._write32(REG_CVALB4, value)

    async def writeMax(self, max_val: int) -> None:
        await self._write32(REG_CMAXB4, max_val)

    async def writeMin(self, min_val: int) -> None:
        await self._write32(REG_CMINB4, min_val)

    async def writeStep(self, step: int) -> None:
        await self._write32(REG_ISTEPB4, step)

    async def writeDoublePushPeriod(self, dperiod: int) -> None:
        await self._write8(REG_DPPERIOD, dperiod)

    async def writeEEPROM(self, add: int, data: int) -> None:
        await self._write8(add, data)
        await asyncio.sleep(0.001)

    async def _write8(self, reg: int, value: int) -> None:
        await self._i2c.write_byte_data(self._addr, reg, value)

    async def _write24(self, reg: int, value: int) -> None:
        s = _to_be_bytes(value, 4)
        await self._i2c.write_i2c_block_data(self._addr, reg, list(s[1:4]))

    async def _write32(self, reg: int, value: int) -> None:
        s = _to_be_bytes(value, 4)
        await self._i2c.write_i2c_block_data(self._addr, reg, list(s))

    async def _read8(self, reg: int) -> int:
        return await self._i2c.read_byte_data(self._addr, reg)

    async def _read16(self, reg: int) -> int:
        data = await self._i2c.read_i2c_block_data(self._addr, reg, 2)
        return _from_be_bytes(data, 2)

    async def _read32(self, reg: int) -> int:
        data = await self._i2c.read_i2c_block_data(self._addr, reg, 4)
        if data[0] == 0 and data[1] == 255 and data[1:] == data[2:]:
            raise Exception("Read error")
        if data[0] == 128:
            data[0] = 0
        return _from_be_bytes(data, 4)


def _to_be_bytes(value: int, length: int) -> bytes:
    return value.to_bytes(length, byteorder="big", signed=True)


def _from_be_bytes(data: list[int], length: int) -> int:
    return int.from_bytes(bytes(data[:length]), byteorder="big", signed=True)


DuppaI2CAddresses = [0x20, 0x21, 0x22, 0x23, 0x30, 0x31, 0x32, 0x33]


class P516RotaryDuppa(PluginBase):
    PLUGIN_ID = 516
    PLUGIN_NAME = "Input - Duppa I2C Rotary Encoder"
    PLUGIN_VALUES = 2
    I2C_ADDRESSES = DuppaI2CAddresses
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
        self._enc: _i2cEncoderMiniLib | None = None
        self._i2c_addr: int = 0
        self._step: int = 1
        self._pos_min: int = 0
        self._pos_max: int = 100
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
        addr_str = self._config.get("i2c_addr", "0x20")
        try:
            self._i2c_addr = int(str(addr_str), 16) if isinstance(addr_str, str) else int(addr_str)
        except (ValueError, TypeError):
            self._i2c_addr = 0x20
        try:
            self._step = int(self._config.get("step", 1))
        except (ValueError, TypeError):
            self._step = 1
        try:
            self._pos_min = int(self._config.get("pos_min", 0))
        except (ValueError, TypeError):
            self._pos_min = 0
        try:
            self._pos_max = int(self._config.get("pos_max", 100))
        except (ValueError, TypeError):
            self._pos_max = 100
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
        self._interrupt_ok = False

        if not self._hw or not self._hw.i2c:
            logger.warning("I2C not available")
            return True
        if self._i2c_addr <= 0:
            logger.warning("No I2C address configured")
            return True

        self._enc = _i2cEncoderMiniLib(self._hw.i2c, self._i2c_addr)
        try:
            config = WRAP_DISABLE | DIRE_RIGHT | IPUP_ENABLE | RMOD_X1
            await self._enc.begin(config)
            await self._enc.writeMax(self._pos_max)
            await self._enc.writeMin(self._pos_min)
            await self._enc.writeStep(self._step)
            await self._enc.writeCounter(self._counter)
            await self._enc.autoconfigInterrupt()
            eid = await self._enc.readIDCode()
            ever = await self._enc.readVersion()
            if eid == 0 and ever == 0:
                logger.info("No Duppa encoder at 0x%02x (ID=0x00 Ver=0x00)", self._i2c_addr)
                self._enc = None
                return True
            logger.info("Duppa encoder found at 0x%02x ID=0x%02x Ver=0x%02x", self._i2c_addr, eid, ever)
        except Exception as e:
            logger.error("Duppa encoder init failed at 0x%02x: %s", self._i2c_addr, e)
            self._enc = None
            return True

        if self._int_pin > 0 and self._hw.gpio:
            try:
                self._hw.gpio.claim_input(self._int_pin)
                self._hw.gpio.watch(self._int_pin, EDGE_FALLING, self._int_callback)
                self._interrupt_ok = True
            except Exception as e:
                logger.warning("GPIO interrupt on pin %d not available, falling back to timer: %s", self._int_pin, e)
                self._interrupt_ok = False

        self._initialized = True
        logger.info("Duppa rotary I2C initialized at 0x%02x step=%d", self._i2c_addr, self._step)
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._int_pin > 0 and self._hw and self._hw.gpio:
            try:
                self._hw.gpio.unwatch(self._int_pin)
            except Exception:
                pass
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._initialized or not self._enc:
            return False
        await self._read_sensor()
        event.data["values"] = {"Counter": str(self._counter), "Button": str(self._button)}
        event.data["named_values"] = {"Counter": str(self._counter), "Button": str(self._button)}
        event.data["value_names"] = ["Counter", "Button"]
        return True

    async def _read_sensor(self) -> None:
        if not self._enc:
            return
        try:
            await self._enc.updateStatus()
            self._button = 1 if self._enc.readStatus(PUSHP) else 0
            cpos = await self._enc.readCounter32()
            if cpos < self._pos_min:
                cpos = self._pos_min
            if cpos > self._pos_max:
                cpos = self._pos_max
            self._counter = cpos
        except Exception as e:
            logger.debug("Encoder read error: %s", e)

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
        sv = event.data.get("values", {})
        if sv:
            try:
                val = int(next(iter(sv.values())))
            except (ValueError, TypeError):
                return False
            if self._enc:
                await self._enc.writeCounter(val)
                self._counter = val
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
            {"name": "int_pin", "label": "Rotary interrupt pin", "type": "number",
             "value": self._config.get("int_pin", -1)},
            {"type": "text", "name": "_int_note",
             "label": "Add one RPI INPUT pin to handle input changes immediately",
             "value": ""},
            {"name": "i2c_addr", "label": "I2C address", "type": "select",
             "value": current_addr_str, "options": i2c_opts},
            {"name": "step", "label": "Step", "type": "select",
             "value": self._config.get("step", 1), "options": [
                 {"value": "1", "label": "1"},
                 {"value": "2", "label": "2"},
                 {"value": "3", "label": "3"},
                 {"value": "4", "label": "4"},
             ]},
            {"name": "pos_min", "label": "Limit min.", "type": "number",
             "value": self._config.get("pos_min", 0), "min": -65535, "max": 65535},
            {"name": "pos_max", "label": "Limit max.", "type": "number",
             "value": self._config.get("pos_max", 100), "min": -65535, "max": 65535},
        ]
        if self._enc:
            try:
                eid = await self._enc.readIDCode()
                ever = await self._enc.readVersion()
                form.append({"type": "text", "name": "_found",
                             "label": f"Rotary found. ID: {eid} Version: {ever}",
                             "value": ""})
            except Exception:
                pass
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["i2c_addr"] = "0x20"
        self._config["step"] = 1
        self._config["pos_min"] = 0
        self._config["pos_max"] = 100
        self._config["int_pin"] = -1
        return True
