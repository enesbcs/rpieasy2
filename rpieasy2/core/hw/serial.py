from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.hw.base import SerialManager

logger = logging.getLogger("rpieasy2.hw.serial")


class PyserialSerialManager(SerialManager):
    def __init__(self):
        self._ser: Any = None
        self._port: str = ""
        self._lock = asyncio.Lock()

    async def open(self, port: str, baud: int = 115200, timeout: float = 1.0) -> bool:
        try:
            import serial
            self._ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)
            self._port = port
            logger.debug(f"Serial port {port} opened at {baud} baud")
            return True
        except Exception as e:
            logger.error(f"Failed to open serial port {port}: {e}")
            self._ser = None
            return False

    async def close(self) -> None:
        async with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                self._port = ""

    async def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        async with self._lock:
            if not self._ser or not self._ser.is_open:
                return b""
            old_to = self._ser.timeout
            if timeout is not None:
                self._ser.timeout = timeout
            try:
                data = self._ser.read(size)
            except Exception:
                data = b""
            if timeout is not None:
                self._ser.timeout = old_to
            return data

    async def readline(self, timeout: float | None = None) -> bytes:
        async with self._lock:
            if not self._ser or not self._ser.is_open:
                return b""
            old_to = self._ser.timeout
            if timeout is not None:
                self._ser.timeout = timeout
            try:
                data = self._ser.readline()
            except Exception:
                data = b""
            if timeout is not None:
                self._ser.timeout = old_to
            return data

    async def write(self, data: bytes) -> int:
        async with self._lock:
            if not self._ser or not self._ser.is_open:
                return 0
            try:
                return self._ser.write(data)
            except Exception as e:
                logger.error(f"Serial write failed: {e}")
                return 0

    async def write_then_read(self, data: bytes, read_size: int = 0, timeout: float = 1.0) -> bytes:
        async with self._lock:
            if not self._ser or not self._ser.is_open:
                return b""
            old_to = self._ser.timeout
            self._ser.timeout = timeout
            try:
                self._ser.flushInput()
                self._ser.write(data)
                if read_size > 0:
                    result = self._ser.read(read_size)
                else:
                    result = b""
            except Exception as e:
                logger.error(f"Serial write_then_read failed: {e}")
                result = b""
            self._ser.timeout = old_to
            return result

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def port(self) -> str:
        return self._port
