from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from rpieasy2.core.rpiconst import DEFAULT_I2C_SPEED, DEFAULT_SPI_SPEED


class GPIOManager(ABC):
    @abstractmethod
    def read(self, pin: int) -> int:
        ...

    @abstractmethod
    def write(self, pin: int, value: int) -> None:
        ...

    @abstractmethod
    def claim_output(self, pin: int) -> None:
        ...

    @abstractmethod
    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        ...

    @abstractmethod
    def watch(self, pin: int, edge: int, callback: Callable[[int, int, int], Any]) -> None:
        ...

    @abstractmethod
    def unwatch(self, pin: int) -> None:
        ...

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        return {}

    def pwm(self, pin: int, frequency: float, duty_cycle: float) -> None:
        pass

    def tone(self, pin: int, frequency: float, duration: float | None = None) -> None:
        pass

    def tone_stop(self, pin: int) -> None:
        pass

    def servo(self, pin: int, pulse_width: int) -> None:
        pass

    def set_mode(self, pin: int, mode: str) -> None:
        pass

    def read_boot_gpio_config(self) -> dict[int, str]:
        return {}

    @abstractmethod
    def close(self) -> None:
        ...


EDGE_RISING = 0
EDGE_FALLING = 1
EDGE_BOTH = 2


class I2CManager(ABC):
    @abstractmethod
    async def read_byte(self, addr: int) -> int:
        ...

    @abstractmethod
    async def write_byte(self, addr: int, value: int) -> None:
        ...

    @abstractmethod
    async def read_byte_data(self, addr: int, reg: int) -> int:
        ...

    @abstractmethod
    async def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        ...

    @abstractmethod
    async def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        ...

    @abstractmethod
    async def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        ...

    @abstractmethod
    async def read_bytes(self, addr: int, length: int) -> list[int]:
        ...

    @abstractmethod
    async def read_word_data(self, addr: int, reg: int) -> int:
        ...

    async def read_i2c_block_data16(self, addr: int, reg16: int, length: int) -> list[int]:
        raise NotImplementedError

    async def write_i2c_block_data16(self, addr: int, reg16: int, data: list[int]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def probe(self, addr: int) -> bool:
        ...

    @abstractmethod
    async def scan(self) -> list[int]:
        ...

    def set_frequency(self, freq_hz: int) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        ...


class SerialManager(ABC):
    @abstractmethod
    async def open(self, port: str, baud: int = 115200, timeout: float = 1.0) -> bool:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @abstractmethod
    async def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        ...

    @abstractmethod
    async def readline(self, timeout: float | None = None) -> bytes:
        ...

    @abstractmethod
    async def write(self, data: bytes) -> int:
        ...

    @abstractmethod
    async def write_then_read(self, data: bytes, read_size: int = 0, timeout: float = 1.0) -> bytes:
        ...

    @property
    @abstractmethod
    def is_open(self) -> bool:
        ...

    @property
    @abstractmethod
    def port(self) -> str:
        ...


class SPIManager(ABC):
    @abstractmethod
    def transfer(self, bus: int, device: int, data: list[int], speed_hz: int = DEFAULT_SPI_SPEED) -> list[int]:
        ...

    @abstractmethod
    def close(self) -> None:
        ...
