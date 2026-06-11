from __future__ import annotations

import asyncio
import logging

import smbus2

from rpieasy2.core.hw.base import I2CManager
from rpieasy2.core.rpiconst import DEFAULT_I2C_BUS

logger = logging.getLogger("rpieasy2.hw.i2c")


class Smbus2I2CManager(I2CManager):
    def __init__(self, bus: int | None = None):
        if bus is not None:
            self._bus = smbus2.SMBus(bus)
            return
        for b in [1, 0, 2, 3, 4]:
            try:
                self._bus = smbus2.SMBus(b)
                return
            except FileNotFoundError:
                continue
            except PermissionError:
                self._bus = smbus2.SMBus(b)
                return
        raise FileNotFoundError("No I2C bus found (tried 0-4)")

    async def read_byte(self, addr: int) -> int:
        return await asyncio.to_thread(self._bus.read_byte, addr)

    async def write_byte(self, addr: int, value: int) -> None:
        await asyncio.to_thread(self._bus.write_byte, addr, value)

    async def read_byte_data(self, addr: int, reg: int) -> int:
        return await asyncio.to_thread(self._bus.read_byte_data, addr, reg)

    async def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        await asyncio.to_thread(self._bus.write_byte_data, addr, reg, value)

    async def read_bytes(self, addr: int, length: int) -> list[int]:
        msg = smbus2.i2c_msg.read(addr, length)
        await asyncio.to_thread(self._bus.i2c_rdwr, msg)
        return list(msg)

    async def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        return await asyncio.to_thread(self._bus.read_i2c_block_data, addr, reg, length)

    async def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        await asyncio.to_thread(self._bus.write_i2c_block_data, addr, reg, data)

    async def read_word_data(self, addr: int, reg: int) -> int:
        return await asyncio.to_thread(self._bus.read_word_data, addr, reg)

    async def read_i2c_block_data16(self, addr: int, reg16: int, length: int) -> list[int]:
        reg_hi = (reg16 >> 8) & 0xFF
        reg_lo = reg16 & 0xFF
        write_msg = smbus2.i2c_msg.write(addr, [reg_hi, reg_lo])
        read_msg = smbus2.i2c_msg.read(addr, length)

        def _do() -> list[int]:
            self._bus.i2c_rdwr(write_msg, read_msg)
            return list(read_msg)

        return await asyncio.to_thread(_do)

    async def write_i2c_block_data16(self, addr: int, reg16: int, data: list[int]) -> None:
        reg_hi = (reg16 >> 8) & 0xFF
        reg_lo = reg16 & 0xFF
        payload = [reg_hi, reg_lo] + data
        msg = smbus2.i2c_msg.write(addr, payload)

        def _do() -> None:
            self._bus.i2c_rdwr(msg)

        await asyncio.to_thread(_do)

    async def probe(self, addr: int) -> bool:
        try:
            await asyncio.to_thread(self._bus.write_quick, addr)
            return True
        except OSError:
            return False

    async def scan(self) -> list[int]:
        found: list[int] = []
        for addr in range(0x03, 0x78):
            if await self.probe(addr):
                found.append(addr)
        return found

    def set_frequency(self, freq_hz: int) -> None:
        pass

    async def close(self) -> None:
        await asyncio.to_thread(self._bus.close)
