from __future__ import annotations

import logging

import spidev

from rpieasy2.core.hw.base import SPIManager
from rpieasy2.core.rpiconst import DEFAULT_SPI_SPEED

logger = logging.getLogger("rpieasy2.hw.spi")


class SpidevSPIManager(SPIManager):
    def __init__(self):
        self._devices: dict[str, spidev.SpiDev] = {}

    def _get_device(self, bus: int, device: int, speed_hz: int) -> spidev.SpiDev:
        key = f"{bus}:{device}"
        if key not in self._devices:
            spi = spidev.SpiDev()
            spi.open(bus, device)
            spi.max_speed_hz = speed_hz
            self._devices[key] = spi
        return self._devices[key]

    def transfer(self, bus: int, device: int, data: list[int], speed_hz: int = DEFAULT_SPI_SPEED) -> list[int]:
        spi = self._get_device(bus, device, speed_hz)
        return list(spi.xfer2(data))

    def close(self) -> None:
        for spi in self._devices.values():
            spi.close()
        self._devices.clear()
