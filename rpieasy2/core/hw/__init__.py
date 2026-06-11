from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.hw.base import GPIOManager, I2CManager, SPIManager, SerialManager
from rpieasy2.core.rpiconst import DEFAULT_SPI_SPEED

logger = logging.getLogger("rpieasy2.hw")

_GPIO_ERROR_LOGGED: set[int] = set()


class _StubGPIO(GPIOManager):
    def __init__(self):
        self._log = logging.getLogger("rpieasy2.hw.stub")

    def _warn_once(self, pin: int, msg: str) -> None:
        # Log a single warning per-pin to avoid spamming logs when running
        # on non-RPi hosts or without native libraries installed.
        try:
            if pin not in _GPIO_ERROR_LOGGED:
                self._log.warning(msg, pin)
                _GPIO_ERROR_LOGGED.add(pin)
        except Exception:
            # Be defensive: don't let logging issues break the stub
            self._log.warning(msg, pin)

    def read(self, pin: int) -> int:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot read pin %d")
        return 0

    def write(self, pin: int, value: int) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot write pin %d")

    def claim_output(self, pin: int) -> None:
        # claiming isn't meaningful in the stub
        return None

    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        return None

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        return {}

    def watch(self, pin: int, edge: int, callback) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot watch pin %d")

    def unwatch(self, pin: int) -> None:
        return None

    def pwm(self, pin: int, frequency: float, duty_cycle: float) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot PWM pin %d")

    def tone(self, pin: int, frequency: float, duration: float | None = None) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot tone pin %d")

    def tone_stop(self, pin: int) -> None:
        return None

    def servo(self, pin: int, pulse_width: int) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot servo pin %d")

    def set_mode(self, pin: int, mode: str) -> None:
        self._warn_once(pin, "GPIO not available (install lgpio), cannot set mode pin %d")

    def close(self) -> None:
        return None


class _StubI2C(I2CManager):
    def __init__(self):
        self._log = logging.getLogger("rpieasy2.hw.stub")
    async def read_byte(self, addr: int) -> int:
        self._log.warning("I2C not available (install smbus2)")
        return 0
    async def write_byte(self, addr: int, value: int) -> None:
        self._log.warning("I2C not available (install smbus2)")
    async def read_byte_data(self, addr: int, reg: int) -> int:
        self._log.warning("I2C not available (install smbus2)")
        return 0
    async def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self._log.warning("I2C not available (install smbus2)")
    async def read_bytes(self, addr: int, length: int) -> list[int]:
        self._log.warning("I2C not available (install smbus2)")
        return [0] * length
    async def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        self._log.warning("I2C not available (install smbus2)")
        return [0] * length
    async def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        self._log.warning("I2C not available (install smbus2)")
    async def read_word_data(self, addr: int, reg: int) -> int:
        self._log.warning("I2C not available (install smbus2)")
        return 0
    async def probe(self, addr: int) -> bool:
        self._log.warning("I2C not available (install smbus2)")
        return False

    async def scan(self) -> list[int]:
        self._log.warning("I2C not available (install smbus2)")
        return []

    async def read_i2c_block_data16(self, addr: int, reg16: int, length: int) -> list[int]:
        self._log.warning("I2C not available (install smbus2)")
        return [0] * length

    async def write_i2c_block_data16(self, addr: int, reg16: int, data: list[int]) -> None:
        self._log.warning("I2C not available (install smbus2)")

    def set_frequency(self, freq_hz: int) -> None:
        pass

    async def close(self) -> None:
        pass


class _StubSPI(SPIManager):
    def __init__(self):
        self._log = logging.getLogger("rpieasy2.hw.stub")
    def transfer(self, bus: int, device: int, data: list[int], speed_hz: int = DEFAULT_SPI_SPEED) -> list[int]:
        self._log.warning("SPI not available (install spidev)")
        return [0] * len(data)
    def close(self) -> None:
        pass


class _StubSerial(SerialManager):
    def __init__(self):
        self._log = logging.getLogger("rpieasy2.hw.stub")
    async def open(self, port: str, baud: int = 115200, timeout: float = 1.0) -> bool:
        self._log.warning("Serial not available (install pyserial), cannot open %s", port)
        return False
    async def close(self) -> None:
        pass
    async def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        return b""
    async def readline(self, timeout: float | None = None) -> bytes:
        return b""
    async def write(self, data: bytes) -> int:
        return 0
    async def write_then_read(self, data: bytes, read_size: int = 0, timeout: float = 1.0) -> bytes:
        return b""
    @property
    def is_open(self) -> bool:
        return False
    @property
    def port(self) -> str:
        return ""


class HWManager:
    def __init__(self, gpio: GPIOManager, i2c: I2CManager, spi: SPIManager, serial: SerialManager):
        self.gpio = gpio
        self.i2c = i2c
        self.spi = spi
        self.serial = serial

    async def close(self) -> None:
        self.gpio.close()
        await self.i2c.close()
        self.spi.close()
        await self.serial.close()


_HAS_NATIVE = False


def has_native_hw() -> bool:
    return _HAS_NATIVE


def _native_gpio() -> GPIOManager:
    global _HAS_NATIVE
    try:
        from rpieasy2.core.hw.lgpio_gpio import LgpioGPIOManager
        inst = LgpioGPIOManager()
        _HAS_NATIVE = True
        return inst
    except Exception:
        logger.warning("lgpio not available, GPIO operations will be stubs (install: pip install lgpio)")
        return _StubGPIO()


def _native_i2c() -> I2CManager:
    global _HAS_NATIVE
    try:
        from rpieasy2.core.hw.smbus2_i2c import Smbus2I2CManager
        inst = Smbus2I2CManager()
        _HAS_NATIVE = True
        return inst
    except PermissionError:
        logger.warning("smbus2: no permission to access I2C bus (add user to i2c group)")
        return _StubI2C()
    except FileNotFoundError:
        logger.warning("smbus2: no I2C bus found (enable in config.txt: dtparam=i2c_arm=on, then reboot)")
        return _StubI2C()
    except Exception as e:
        logger.warning("smbus2 not available: %s: %s", type(e).__name__, e)
        return _StubI2C()


def _native_spi() -> SPIManager:
    global _HAS_NATIVE
    try:
        from rpieasy2.core.hw.spidev_spi import SpidevSPIManager
        inst = SpidevSPIManager()
        _HAS_NATIVE = True
        return inst
    except Exception:
        logger.warning("spidev not available, SPI operations will be stubs (install: pip install spidev)")
        return _StubSPI()


def _native_serial() -> SerialManager:
    try:
        from rpieasy2.core.hw.serial import PyserialSerialManager
        inst = PyserialSerialManager()
        return inst
    except ImportError:
        logger.warning("pyserial not available, serial operations will be stubs (install: pip install pyserial)")
        return _StubSerial()


def _find_ftdi_url(dev_id: str, fallback_url: str) -> str:
    import time
    if not dev_id:
        return fallback_url
    try:
        from rpieasy2.core.hw.ftdi import list_ftdi_devices
        for attempt in range(3):
            for ld in list_ftdi_devices():
                if ld.get("device_id") == dev_id:
                    logger.info("Resolved FTDI device %s to URL %s", dev_id, ld["url"])
                    return ld["url"]
            if attempt < 2:
                time.sleep(1.0)
    except Exception:
        pass
    return fallback_url


def create_hw_manager(use_ftdi: bool = False, ftdi_config: dict[str, Any] | None = None,
                      ftdi_devices: list[dict[str, Any]] | None = None) -> HWManager:
    if use_ftdi:
        try:
            from rpieasy2.core.hw.ftdi import FtdiGPIOManager, FtdiI2CManager, FtdiSPIManager, FtdiMultiGPIOManager
            logger.info("Using FTDI hardware backend")
            devices = ftdi_devices or ([ftdi_config] if ftdi_config else [])
            if not devices:
                devices = [{"url": "ftdi://ftdi:232h/1", "port_width": 16, "mpsse_channels": [], "gpio_pins": {}}]
            if len(devices) > 1:
                gpio_inst = FtdiMultiGPIOManager(devices)
                i2c_inst = _StubI2C()
                spi_inst = _StubSPI()
                for dev in devices:
                    mpsse = dev.get("mpsse_channels", [])
                    if "i2c" in mpsse or "spi" in mpsse:
                        pass
            else:
                dev = devices[0]
                url = dev.get("url", "ftdi://ftdi:232h/1")
                gpio_pins_raw = dev.get("gpio_pins", {})
                gpio_pins = {int(k): v for k, v in gpio_pins_raw.items()} if gpio_pins_raw else None
                mpsse_channels = dev.get("mpsse_channels", [])
                has_i2c = "i2c" in mpsse_channels
                has_spi = "spi" in mpsse_channels
                i2c_freq = int(dev.get("i2c_frequency", 100000))
                i2c_inst = FtdiI2CManager(url, frequency=i2c_freq) if has_i2c else _StubI2C()
                spi_freq = int(dev.get("spi_frequency", 6000000))
                spi_inst = FtdiSPIManager(url, frequency=spi_freq) if has_spi else _StubSPI()
                dev_id = dev.get("device_id", "")
                need_retry = False
                if has_i2c and i2c_inst.get_gpio() is None and i2c_inst.get_ftdi() is None:
                    need_retry = True
                elif has_spi and spi_inst.get_gpio() is None and spi_inst.get_ftdi() is None:
                    need_retry = True
                elif not has_i2c and not has_spi:
                    need_retry = True
                if need_retry:
                    new_url = _find_ftdi_url(dev_id, url)
                    if new_url != url:
                        logger.info("FTDI device URL changed from %s to %s, retrying", url, new_url)
                        url = new_url
                        i2c_inst = FtdiI2CManager(url, frequency=i2c_freq) if has_i2c else _StubI2C()
                        spi_inst = FtdiSPIManager(url, frequency=spi_freq) if has_spi else _StubSPI()
                mpsse_gpio = None
                if has_i2c and hasattr(i2c_inst, 'get_gpio'):
                    mpsse_gpio = i2c_inst.get_gpio()
                if mpsse_gpio is None and has_spi and hasattr(spi_inst, 'get_gpio'):
                    mpsse_gpio = spi_inst.get_gpio()
                if mpsse_gpio is not None:
                    gpio_inst = FtdiGPIOManager(url, gpio_pins=gpio_pins, mpsse_gpio=mpsse_gpio)
                else:
                    ftdi_inst = None
                    if has_i2c and hasattr(i2c_inst, 'get_ftdi'):
                        ftdi_inst = i2c_inst.get_ftdi()
                    if ftdi_inst is None and has_spi and hasattr(spi_inst, 'get_ftdi'):
                        ftdi_inst = spi_inst.get_ftdi()
                    gpio_inst = FtdiGPIOManager(url, gpio_pins=gpio_pins, ftdi_instance=ftdi_inst) if gpio_pins else FtdiGPIOManager(url, ftdi_instance=ftdi_inst)
            return HWManager(
                gpio=gpio_inst,
                i2c=i2c_inst,
                spi=spi_inst,
                serial=_StubSerial(),
            )
        except ImportError:
            logger.warning("FTDI libraries not installed, falling back to native (install: pip install pyftdi)")
    return HWManager(gpio=_native_gpio(), i2c=_native_i2c(), spi=_native_spi(), serial=_native_serial())


def create_native_i2c() -> I2CManager:
    from rpieasy2.core.hw.smbus2_i2c import Smbus2I2CManager
    return Smbus2I2CManager()


def is_stub_i2c(i2c: I2CManager) -> bool:
    return isinstance(i2c, _StubI2C)


async def scan_i2c_bus() -> list[int]:
    i2c = create_native_i2c()
    try:
        return await i2c.scan()
    finally:
        await i2c.close()


def detect_ftdi() -> bool:
    try:
        from pyftdi.ftdi import Ftdi
        return len(Ftdi().list_devices()) > 0
    except Exception:
        return False
