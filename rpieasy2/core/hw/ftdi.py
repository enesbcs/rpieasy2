from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Callable

from rpieasy2.core.hw.base import GPIOManager, I2CManager, SPIManager
from rpieasy2.core.rpiconst import DEFAULT_FTDI_SPI_SPEED, DEFAULT_SPI_SPEED

logger = logging.getLogger("rpieasy2.hw.ftdi")

_inuse_urls: set[str] = set()
_device_info_cache: dict[str, dict[str, Any]] = {}


def _mark_ftdi_inuse(url: str) -> None:
    _inuse_urls.add(url)


def _mark_ftdi_free(url: str) -> None:
    _inuse_urls.discard(url)


def _cache_device_info(url: str, port_width: int, has_mpsse: bool, ic_name: str,
                       port_count: int, dev_version: int,
                       vid: int | None = None, pid: int | None = None,
                       iface_idx: int = 0) -> None:
    _device_info_cache[url] = {
        "port_width": port_width,
        "has_mpsse": has_mpsse,
        "ic_name": ic_name,
        "port_count": port_count,
        "dev_version": dev_version,
        "vid": vid,
        "pid": pid,
        "iface_idx": iface_idx,
    }


def make_device_id(vid, pid, ic_name, port_count, dev_version, iface_idx) -> str:
    raw = f"{int(vid):04x}:{int(pid):04x}:{ic_name}:{port_count}:{dev_version}:{iface_idx}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    logger.debug("make_device_id: raw=%s -> device_id=%s", raw, digest)
    return digest


def url_identifier(url: str) -> str:
    suffix = url.replace("ftdi://ftdi:", "")
    parts = suffix.split("/")
    if len(parts) >= 2:
        addr = parts[0].split(":")[-1] if ":" in parts[0] else parts[0]
        return f"{addr}/{parts[1]}"
    return suffix


def _chip_name_from_url(url: str) -> str:
    lower = url.lower()
    if "232h" in lower or "ft232" in lower:
        return "FT232H"
    if "2232" in lower or "ft2232" in lower:
        return "FT2232H"
    if "4232" in lower or "ft4232" in lower:
        return "FT4232H"
    return ""


def _port_width_from_url(url: str) -> int:
    lower = url.lower()
    if "232h" in lower or "ft232" in lower:
        return 16
    if "2232" in lower or "ft2232" in lower:
        return 8
    if "4232" in lower or "ft4232" in lower:
        return 8
    return 8


def _has_mpsse_from_url(url: str) -> bool:
    lower = url.lower()
    return "232h" in lower or "ft232" in lower or "2232" in lower or "ft2232" in lower or "4232" in lower or "ft4232" in lower


_CHIP_PROPERTIES: dict[str, tuple[int, int]] = {
    "FT232H": (1, 0x0900),
    "FT2232H": (2, 0x0700),
    "FT4232H": (4, 0x0800),
    "FT230X": (1, 0x1000),
    "FT231X": (1, 0x1000),
    "FT234X": (1, 0x1000),
}


def _ic_name_props(ic_name: str) -> tuple[int, int]:
    return _CHIP_PROPERTIES.get(ic_name, (0, 0))


def _cache_url_info(url: str) -> None:
    ic_name = _chip_name_from_url(url)
    pc, dv = _ic_name_props(ic_name)
    _cache_device_info(url, _port_width_from_url(url), _has_mpsse_from_url(url),
                       ic_name, pc, dv)


def _update_cache_from_ftdi(url: str, ftdi) -> None:
    if not getattr(ftdi, 'is_connected', False):
        _cache_url_info(url)
        return
    try:
        vid = ftdi._usb_dev.idVendor
        pid = ftdi._usb_dev.idProduct
        ic_name = ftdi.ic_name.upper()
        port_count = ftdi.device_port_count
        dev_version = ftdi.device_version
        iface_idx = ftdi.port_index
        port_width = _port_width_from_url(url)
        has_mpsse = _has_mpsse_from_url(url)
        _cache_device_info(url, port_width, has_mpsse, ic_name,
                           port_count, dev_version, vid, pid, iface_idx)
        logger.debug("Cached real FTDI info for %s: vid=0x%04x pid=0x%04x ic_name=%s "
                     "port_count=%d dev_version=0x%04x iface_idx=%d",
                     url, vid, pid, ic_name, port_count, dev_version, iface_idx)
    except Exception as e:
        logger.warning("Failed to read real FTDI device info from opened instance: %s", e)
        _cache_url_info(url)


_FTDI_PRODUCT_IDS: dict[str, int] = {
    "232h": 0x6014, "ft232h": 0x6014,
    "2232h": 0x6010, "ft2232h": 0x6010,
    "4232h": 0x6011, "ft4232h": 0x6011,
    "232r": 0x6001, "ft232r": 0x6001,
    "230x": 0x6015, "ft230x": 0x6015,
}

def _vid_pid_from_url(url: str) -> tuple[int | None, int | None]:
    import re
    m = re.match(r"ftdi://([^:]+):([^:]+):", url)
    if m:
        vid_str, pid_str = m.group(1), m.group(2)
        try:
            return int(vid_str, 16), int(pid_str, 16)
        except ValueError:
            pass
        lower_pid = pid_str.lower()
        if lower_pid in _FTDI_PRODUCT_IDS:
            return 0x0403, _FTDI_PRODUCT_IDS[lower_pid]
    return None, None


def _interface_from_url(url: str) -> int:
    idx = url.rfind("/")
    if idx >= 0:
        try:
            return int(url[idx + 1:])
        except ValueError:
            pass
    return 0


def list_ftdi_devices() -> list[dict[str, Any]]:
    try:
        from pyftdi.ftdi import Ftdi
    except ImportError:
        return []
    result: list[dict[str, Any]] = []
    try:
        urls = _get_ftdi_urls(Ftdi)
        if not urls:
            return result
    except Exception as e:
        logger.debug("Failed to list FTDI devices: %s", e)
        return result
    for url in urls:
        try:
            port_width = _port_width_from_url(url)
            has_mpsse = _has_mpsse_from_url(url)
            description = url_identifier(url)
            ic_name = _chip_name_from_url(url)
            vid = pid = None
            iface_idx = _interface_from_url(url)
            port_count = 0
            dev_version = 0
            cached = _device_info_cache.get(url)
            if cached:
                port_width = cached["port_width"]
                has_mpsse = cached["has_mpsse"]
                ic_name = cached["ic_name"]
                port_count = cached["port_count"]
                dev_version = cached["dev_version"]
                cached_vid = cached.get("vid")
                if cached_vid is not None:
                    vid = cached_vid
                    pid = cached["pid"]
                    iface_idx = cached.get("iface_idx", iface_idx)
                    logger.debug("list_ftdi_devices: %s using cached identifiers: "
                                 "vid=0x%04x pid=0x%04x iface=%d",
                                 url, vid, pid, iface_idx)
                else:
                    logger.debug("list_ftdi_devices: %s using cached data: "
                                 "ic_name=%s port_count=%d dev_version=0x%04x",
                                 url, ic_name, port_count, dev_version)
            if vid is None:
                try:
                    desc, iface = Ftdi.get_identifiers(url)
                    vid = getattr(desc, "vid", None)
                    pid = getattr(desc, "pid", None)
                    iface_idx = iface
                except Exception:
                    pass
                if vid is None or pid is None:
                    vid, pid = _vid_pid_from_url(url)
            if not cached:
                pc, dv = _ic_name_props(ic_name)
                port_count = pc
                dev_version = dv
                logger.debug("list_ftdi_devices: %s using URL defaults "
                             "(ic_name=%s port_count=%d dev_version=0x%04x)",
                             url, ic_name, port_count, dev_version)
            device_id = ""
            if vid is not None and pid is not None and ic_name:
                device_id = make_device_id(vid, pid, ic_name, port_count, dev_version, iface_idx)
            entry: dict[str, Any] = {
                "url": url,
                "description": description,
                "port_width": port_width,
                "has_mpsse": has_mpsse,
            }
            if device_id:
                entry["device_id"] = device_id
            result.append(entry)
        except Exception as e:
            logger.debug("Failed to query FTDI device %s: %s", url, e)
    return result


def resolve_ftdi_configs(saved_configs: list[dict]) -> list[dict]:
    import time
    live_devices: list[dict] = []
    for attempt in range(3):
        live_devices = list_ftdi_devices()
        if live_devices:
            break
        if attempt < 2:
            logger.debug("FTDI scan returned no devices, retrying... (attempt %d/3)", attempt + 1)
            time.sleep(1.0)
    live_by_id: dict[str, dict] = {}
    for d in live_devices:
        did = d.get("device_id")
        if did:
            live_by_id[did] = d
    resolved: list[dict] = []
    for cfg in saved_configs:
        saved_id = cfg.get("device_id", "")
        if saved_id and saved_id in live_by_id:
            logger.info("FTDI config: stored device_id=%s matches live device at %s", saved_id, live_by_id[saved_id]["url"])
            cfg = dict(cfg)
            cfg["url"] = live_by_id[saved_id]["url"]
        elif saved_id:
            live_ids = list(live_by_id.keys()) if live_by_id else ["(none found)"]
            logger.warning("FTDI config: stored device_id=%s not found in live devices (live: %s), keeping saved URL %s",
                          saved_id, live_ids, cfg.get("url", ""))
        else:
            logger.warning("FTDI config: no device_id stored for entry (url=%s), cannot match — re-save config from Hardware page",
                          cfg.get("url", ""))
        resolved.append(cfg)
    return resolved


def _get_ftdi_urls(ftdi_cls) -> list[str]:
    from io import StringIO
    from contextlib import redirect_stdout
    import re
    buffer = StringIO()
    try:
        with redirect_stdout(buffer):
            ftdi_cls.show_devices()
        output = buffer.getvalue()
    except Exception:
        return []
    urls = re.findall(r'ftdi://[^\s]+', output)
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        u = u.rstrip('/')
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def ftdi_pins_reserved(mpsse_channels: list[str | None], channel_index: int, port_width: int) -> set[int]:
    reserved: set[int] = set()
    base = 0
    for ci, mode in enumerate(mpsse_channels):
        if ci == channel_index:
            if mode == "i2c":
                reserved.update(range(base, base + 3))
            elif mode == "spi":
                reserved.update(range(base, base + 4))
            break
        if mode == "i2c":
            base += 3
        elif mode == "spi":
            base += 4
        else:
            base += port_width
    return reserved


def all_reserved_pins(mpsse_channels: list[str | None], port_width: int) -> set[int]:
    reserved: set[int] = set()
    base = 0
    for mode in mpsse_channels:
        if mode == "i2c":
            reserved.update(range(base, base + 3))
            base += port_width
        elif mode == "spi":
            reserved.update(range(base, base + 4))
            base += port_width
        else:
            base += port_width
    return reserved


class FtdiMultiGPIOManager(GPIOManager):
    def __init__(self, device_configs: list[dict]):
        self._devices: list[FtdiGPIOManager] = []
        self._port_widths: list[int] = []
        for dev_cfg in device_configs:
            url = dev_cfg.get("url", "ftdi://ftdi:232h/1")
            gpio_pins_raw = dev_cfg.get("gpio_pins", {})
            gpio_pins = {int(k): v for k, v in gpio_pins_raw.items()} if gpio_pins_raw else None
            self._devices.append(FtdiGPIOManager(url, gpio_pins=gpio_pins))
            self._port_widths.append(dev_cfg.get("port_width", 16))

    def _resolve(self, pin: int) -> tuple[int, int]:
        offset = 0
        for di, pw in enumerate(self._port_widths):
            if pin < offset + pw:
                return di, pin - offset
            offset += pw
        raise IndexError(f"Pin {pin} out of range (max {offset - 1})")

    def read(self, pin: int) -> int:
        di, lp = self._resolve(pin)
        return self._devices[di].read(lp)

    def write(self, pin: int, value: int) -> None:
        di, lp = self._resolve(pin)
        self._devices[di].write(lp, value)

    def claim_output(self, pin: int) -> None:
        di, lp = self._resolve(pin)
        self._devices[di].claim_output(lp)

    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        di, lp = self._resolve(pin)
        self._devices[di].claim_input(lp, pull_up=pull_up)

    def watch(self, pin: int, edge: int, callback: Callable[[int, int, int], Any]) -> None:
        logger.warning("FTDI GPIO watch not supported in multi-device mode")

    def unwatch(self, pin: int) -> None:
        pass

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        result: dict[int, dict[str, str | int]] = {}
        offset = 0
        for di, pw in enumerate(self._port_widths):
            sub = self._devices[di].get_pin_states()
            for local_pin, state in sub.items():
                result[offset + local_pin] = state
            offset += pw
        return result

    def close(self) -> None:
        for d in self._devices:
            d.close()


class FtdiGPIOManager(GPIOManager):
    def __init__(self, url: str = "ftdi://ftdi:232h/1", gpio_pins: dict[int, str] | None = None,
                 ftdi_instance=None, mpsse_gpio=None):
        self._url = url
        self._warned = False
        self._gpio = None
        self._ftdi_dev = None
        self._mpsse_mode = False
        self._owns_gpio = True
        self._is_i2c_gpio = False
        if mpsse_gpio is not None:
            self._gpio = mpsse_gpio
            self._mpsse_mode = True
            self._owns_gpio = False
            self._is_i2c_gpio = hasattr(mpsse_gpio, '_controller')
            try:
                self._ftdi_dev = getattr(mpsse_gpio, '_ftdi', None)
            except Exception:
                self._ftdi_dev = None
            logger.debug("FTDI GPIO using shared GPIO (I2cGpioPort=%s)", self._is_i2c_gpio)
            if url not in _device_info_cache or _device_info_cache[url].get("vid") is None:
                _cache_url_info(url)
            return
        if ftdi_instance is not None:
            self._ftdi_dev = ftdi_instance
            self._mpsse_mode = True
            _mark_ftdi_inuse(url)
            if url not in _device_info_cache or _device_info_cache[url].get("vid") is None:
                _cache_url_info(url)
            try:
                from pyftdi.gpio import GpioMpsseController
                gpio = GpioMpsseController()
                gpio.configure(ftdi_instance)
                self._gpio = gpio
                logger.debug("FTDI GPIO using GpioMpsseController (MPSSE mode)")
                return
            except Exception as e:
                logger.warning("FTDI GpioMpsseController init failed: %s", e)
        from pyftdi.gpio import GpioController
        direction = 0x00
        if gpio_pins:
            mask = 0
            for pnum, pdir in gpio_pins.items():
                if pdir == "input":
                    mask |= 1 << int(pnum)
            direction = mask
        try:
            gpio = GpioController()
            gpio.open_from_url(url, direction=direction)
            self._gpio = gpio
            _mark_ftdi_inuse(url)
            ftdi_dev = getattr(gpio, '_ftdi', None)
            if ftdi_dev is not None:
                _update_cache_from_ftdi(url, ftdi_dev)
            else:
                _cache_url_info(url)
        except Exception as e:
            logger.warning("FTDI device %s not available: %s", url, e)

    def _check(self) -> bool:
        if self._gpio is not None:
            return True
        if not self._warned:
            logger.warning("FTDI device %s not available, ignoring GPIO access", self._url)
            self._warned = True
        return False

    def _i2c_gpio_read(self, pin: int) -> int:
        try:
            self._gpio.set_direction(1 << pin, 0)
            val = (self._gpio.read() >> pin) & 1
            return val
        except Exception as e:
            logger.error("FTDI I2cGpio read pin %d failed: %s", pin, e)
            return 0

    def _i2c_gpio_write(self, pin: int, value: int) -> None:
        try:
            self._gpio.set_direction(1 << pin, 1 << pin)
            self._gpio.write(value << pin)
            logger.debug("FTDI I2cGpio write pin=%d value=%d OK", pin, value)
        except Exception as e:
            logger.error("FTDI I2cGpio write pin=%d failed: %s", pin, e)

    def read(self, pin: int) -> int:
        if not self._check():
            logger.debug("FTDI read pin %d: no GPIO controller, returning 0", pin)
            return 0
        if self._is_i2c_gpio:
            return self._i2c_gpio_read(pin)
        try:
            val = (self._gpio.read() >> pin) & 1
            return val
        except Exception as e:
            logger.error("FTDI read pin %d failed: %s", pin, e)
            return 0

    def write(self, pin: int, value: int) -> None:
        if self._is_i2c_gpio:
            self._i2c_gpio_write(pin, value)
            return
        mask = 1 << pin
        if self._ftdi_dev is not None and self._mpsse_mode:
            try:
                if pin < 8:
                    gpio_read_cmd = bytearray([0x80])
                    self._ftdi_dev.write_data(gpio_read_cmd)
                    raw = self._ftdi_dev.read_data(1)
                    current = raw[0] if raw else 0
                    new_val = (current | mask) if value else (current & ~mask)
                    dir_byte = 0x00
                    if self._gpio is not None:
                        dir_byte = self._gpio._direction & 0xFF if hasattr(self._gpio, '_direction') else 0x00
                    dir_byte |= mask
                    cmd = bytearray([0x50, new_val & 0xFF, dir_byte])
                else:
                    gpio_read_cmd = bytearray([0x82])
                    self._ftdi_dev.write_data(gpio_read_cmd)
                    raw = self._ftdi_dev.read_data(1)
                    current = raw[0] if raw else 0
                    new_val = (current | (mask >> 8)) if value else (current & ~(mask >> 8))
                    dir_byte = 0x00
                    if self._gpio is not None:
                        dir_byte = (self._gpio._direction >> 8) & 0xFF if hasattr(self._gpio, '_direction') else 0x00
                    dir_byte |= (mask >> 8)
                    cmd = bytearray([0x52, new_val & 0xFF, dir_byte])
                if cmd:
                    self._ftdi_dev.write_data(cmd)
                logger.debug("FTDI MPSSE write pin=%d value=%d current=0x%02x dir=0x%02x OK",
                             pin, value, current if 'current' in dir() else 0, dir_byte)
                return
            except Exception as e:
                logger.error("FTDI MPSSE write pin=%d failed: %s", pin, e)
        if not self._check():
            logger.debug("FTDI write pin=%d value=%d skipped: no GPIO controller", pin, value)
            return
        try:
            current = self._gpio.read()
            self._gpio.write(current)
            self._gpio.set_direction(mask, mask)
            new_val = (current | mask) if value else (current & ~mask)
            self._gpio.write(new_val)
            logger.debug("FTDI write pin=%d value=%d current=0x%02x OK", pin, value, current)
        except Exception as e:
            logger.error("FTDI write pin=%d value=%d failed: %s", pin, value, e)

    def claim_output(self, pin: int) -> None:
        if not self._check():
            logger.debug("FTDI claim_output pin %d skipped: no GPIO controller", pin)
            return
        if self._is_i2c_gpio:
            try:
                self._gpio.set_direction(1 << pin, 1 << pin)
                logger.debug("FTDI I2cGpio claim_output pin %d OK", pin)
                return
            except Exception as e:
                logger.warning("FTDI I2cGpio claim_output pin %d failed: %s", pin, e)
                return
        mask = 1 << pin
        if self._ftdi_dev is not None and self._mpsse_mode and pin < 8:
            try:
                gpio_read_cmd = bytearray([0x80])
                self._ftdi_dev.write_data(gpio_read_cmd)
                raw = self._ftdi_dev.read_data(1)
                current = raw[0] if raw else 0
                self._ftdi_dev.write_data(bytearray([0x82]))
                raw_h = self._ftdi_dev.read_data(1)
                _ = raw_h[0] if raw_h else 0
                dir_byte = 0x00
                if self._gpio is not None:
                    dir_byte = self._gpio._direction & 0xFF if hasattr(self._gpio, '_direction') else 0x00
                dir_byte |= mask
                cmd = bytearray([0x50, current & 0xFF, dir_byte])
                self._ftdi_dev.write_data(cmd)
                logger.debug("FTDI claim_output pin %d using raw MPSSE dir=0x%02x OK", pin, dir_byte)
                return
            except Exception as e:
                logger.warning("FTDI claim_output raw MPSSE pin %d failed: %s", pin, e)
        try:
            current = self._gpio.read()
            self._gpio.write(current)
            self._gpio.set_direction(1 << pin, 1 << pin)
            logger.debug("FTDI claim_output pin %d OK", pin)
        except Exception as e:
            logger.warning("FTDI claim_output pin %d failed: %s", pin, e)

    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        if not self._check():
            return
        if self._is_i2c_gpio:
            try:
                self._gpio.set_direction(1 << pin, 0)
                logger.debug("FTDI I2cGpio claim_input pin %d OK", pin)
                return
            except Exception as e:
                logger.warning("FTDI I2cGpio claim_input pin %d failed: %s", pin, e)
                return
        try:
            current = self._gpio.read()
            self._gpio.write(current)
            self._gpio.set_direction(1 << pin, 0)
            logger.debug("FTDI claim_input pin %d OK", pin)
        except Exception as e:
            logger.warning("FTDI claim_input pin %d failed: %s", pin, e)

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        result: dict[int, dict[str, str | int]] = {}
        if self._is_i2c_gpio:
            try:
                ctrl = self._gpio._controller
                with ctrl._lock:
                    value = ctrl._read_raw(ctrl._wide_port)
                for pin in range(16):
                    result[pin] = {"value": (value >> pin) & 1}
            except Exception:
                pass
            return result
        gpio = self._gpio
        needs_close = False
        if gpio is None:
            try:
                from pyftdi.gpio import GpioController
                gpio = GpioController()
                gpio.open_from_url(self._url, direction=0x0000)
                needs_close = True
            except Exception:
                return result
        try:
            value = gpio.read()
            for pin in range(16):
                result[pin] = {"value": (value >> pin) & 1}
        except Exception:
            pass
        finally:
            if needs_close:
                try:
                    gpio.close()
                except Exception:
                    pass
        return result

    def watch(self, pin: int, edge: int, callback: Callable[[int, int, int], Any]) -> None:
        logger.warning("FTDI GPIO watch not supported")

    def unwatch(self, pin: int) -> None:
        pass

    def close(self) -> None:
        if self._gpio is not None and self._owns_gpio:
            try:
                self._gpio.close()
            except Exception:
                pass
        _mark_ftdi_free(self._url)


class FtdiI2CManager(I2CManager):
    def __init__(self, url: str = "ftdi://ftdi:232h/1", frequency: int = 100000):
        self._url = url
        self._warned = False
        self._i2c = None
        self._frequency = frequency
        logger.debug("FtdiI2CManager.__init__: url=%s, frequency=%d, _inuse_urls contains url=%s",
                     url, frequency, url in _inuse_urls)
        try:
            from pyftdi.ftdi import Ftdi
            from pyftdi.i2c import I2cController
            i2c = I2cController()
            i2c.configure(url, frequency=frequency)
            self._i2c = i2c
            _mark_ftdi_inuse(url)
            ftdi_dev = getattr(i2c, '_ftdi', None)
            if ftdi_dev is not None:
                _update_cache_from_ftdi(url, ftdi_dev)
            else:
                _cache_url_info(url)
            logger.debug("FtdiI2CManager.__init__: I2cController configured OK, url=%s", url)
        except Exception as e:
            logger.warning("FTDI I2C device %s not available: %s", url, e)

    def set_frequency(self, freq_hz: int) -> None:
        if freq_hz == self._frequency or self._i2c is None:
            self._frequency = freq_hz
            return
        try:
            self._i2c._frequency = freq_hz
            self._frequency = freq_hz
            logger.debug("FTDI I2C frequency set to %d Hz", freq_hz)
        except Exception as e:
            logger.warning("FTDI I2C set_frequency failed: %s", e)

    def _check(self) -> bool:
        if self._i2c is not None:
            return True
        if not self._warned:
            logger.warning("FTDI I2C device %s not available (self._i2c is None)", self._url)
            self._warned = True
        return False

    def _port(self, addr: int):
        if not self._check():
            return None
        if addr is None:
            logger.error("FTDI I2C _port called with addr=None", exc_info=True)
            return None
        return self._i2c.get_port(addr)

    async def read_byte(self, addr: int) -> int:
        port = self._port(addr)
        if port is None:
            return 0
        try:
            val = await asyncio.to_thread(lambda: port.read(1)[0])
            logger.debug("FTDI I2C read_byte(0x%02x) = 0x%02x", addr, val)
            return val
        except Exception as e:
            logger.error("FTDI I2C read_byte(0x%02x) failed: %s", addr, e)
            return 0

    async def write_byte(self, addr: int, value: int) -> None:
        port = self._port(addr)
        if port is None:
            return
        try:
            await asyncio.to_thread(port.write, [value])
        except Exception as e:
            logger.error("FTDI I2C write_byte(0x%02x, 0x%02x) failed: %s", addr, value, e)

    async def read_byte_data(self, addr: int, reg: int) -> int:
        port = self._port(addr)
        if port is None:
            return 0
        await asyncio.to_thread(port.write, [reg])
        return await asyncio.to_thread(lambda: port.read(1)[0])

    async def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        port = self._port(addr)
        if port is None:
            return
        await asyncio.to_thread(port.write, [reg, value])

    async def read_bytes(self, addr: int, length: int) -> list[int]:
        port = self._port(addr)
        if port is None:
            return [0] * length
        return await asyncio.to_thread(lambda: list(port.read(length)))

    async def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        port = self._port(addr)
        if port is None:
            return [0] * length
        await asyncio.to_thread(port.write, [reg])
        return await asyncio.to_thread(lambda: list(port.read(length)))

    async def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        port = self._port(addr)
        if port is None:
            return
        await asyncio.to_thread(port.write, [reg] + data)

    async def read_word_data(self, addr: int, reg: int) -> int:
        port = self._port(addr)
        if port is None:
            return 0
        await asyncio.to_thread(port.write, [reg])
        buf = await asyncio.to_thread(port.read, 2)
        return buf[0] | (buf[1] << 8)

    async def probe(self, addr: int) -> bool:
        if not self._check():
            logger.debug("FTDI I2C probe(0x%02x): _check failed", addr)
            return False
        port = self._i2c.get_port(addr)
        try:
            result = await asyncio.to_thread(port.poll, write=True)
            logger.debug("FTDI I2C probe(0x%02x) = %s", addr, result)
            return result
        except Exception as e:
            logger.debug("FTDI I2C probe(0x%02x) exception: %s", addr, e)
            return False

    async def scan(self) -> list[int]:
        if not self._check():
            return []
        found: list[int] = []
        for addr in range(0x03, 0x78):
            if await self.probe(addr):
                found.append(addr)
        return found

    async def close(self) -> None:
        if self._i2c is not None:
            try:
                await asyncio.to_thread(self._i2c.close)
            except Exception as e:
                logger.warning("FTDI I2C close error: %s", e)
        _mark_ftdi_free(self._url)

    def get_gpio(self):
        if self._i2c is not None:
            return self._i2c.get_gpio()
        return None

    def get_ftdi(self):
        if self._i2c is not None:
            return getattr(self._i2c, '_ftdi', None)
        return None


class FtdiSPIManager(SPIManager):
    def __init__(self, url: str = "ftdi://ftdi:232h/1", frequency: int = DEFAULT_FTDI_SPI_SPEED):
        self._url = url
        self._warned = False
        self._spi = None
        self._frequency = frequency
        try:
            from pyftdi.spi import SpiController
            spi = SpiController()
            spi.configure(url)
            self._spi = spi
            _mark_ftdi_inuse(url)
            ftdi_dev = getattr(spi, '_ftdi', None)
            if ftdi_dev is not None:
                _update_cache_from_ftdi(url, ftdi_dev)
            else:
                _cache_url_info(url)
        except Exception as e:
            logger.warning("FTDI SPI device %s not available: %s", url, e)

    def _check(self) -> bool:
        if self._spi is not None:
            return True
        if not self._warned:
            logger.warning("FTDI SPI device %s not available", self._url)
            self._warned = True
        return False

    def transfer(self, bus: int, device: int, data: list[int], speed_hz: int | None = None) -> list[int]:
        if not self._check():
            return [0] * len(data)
        freq = speed_hz if speed_hz is not None else self._frequency
        port = self._spi.get_port(cs=device, freq=freq, mode=0)
        return list(port.exchange(list(data)))

    def close(self) -> None:
        if self._spi is not None:
            try:
                self._spi.close()
            except Exception as e:
                logger.warning("FTDI SPI close error: %s", e)
        _mark_ftdi_free(self._url)

    def get_gpio(self):
        if self._spi is not None:
            return self._spi.get_gpio()
        return None

    def get_ftdi(self):
        if self._spi is not None:
            return getattr(self._spi, '_ftdi', None)
        return None
