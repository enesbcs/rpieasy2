from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p011")

I2C_SLAVE = 0x0703
I2C_BUS = 1

CMD_DIGITAL_WRITE = 1
CMD_DIGITAL_READ = 2
CMD_ANALOG_WRITE = 3
CMD_ANALOG_READ = 4

ANALOG_DIVERSION = 1


class P011PME(PluginBase):
    PLUGIN_ID = 11
    PLUGIN_NAME = "Extra IO - ProMini Extender"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=1,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )
    I2C_ADDRESSES = [0x3f, 0x4f, 0x5f, 0x6f, 0x7f]

    PORT_TYPE_DIGITAL = 0
    PORT_TYPE_ANALOG = 1
    PORT_TYPE_SWITCH = 2

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0x7f
        self._port_type: int = 0
        self._port_num: int = 0
        self._last_switch_state: int = -1
        self._fd: int | None = None
        self._busy: bool = False
        self._iid: int = -1
        self._enddelay: float = 0.1
        self._sketch: int = 0
        self._i2c_lock = asyncio.Lock()
        self._pulse_tasks: set[asyncio.Task] = set()

    # --- raw I2C helpers (synchronous, run in threads) ---

    def _raw_connect(self, address: int) -> bool:
        try:
            self._fd = os.open(f"/dev/i2c-{I2C_BUS}", os.O_RDWR)
            fcntl.ioctl(self._fd, I2C_SLAVE, address)
        except Exception as e:
            logger.error("PME: cannot open I2C bus %d at 0x%02x: %s", I2C_BUS, address, e)
            self._fd = None
            return False
        return True

    def _raw_write(self, data: bytes) -> None:
        if self._fd is None:
            return
        try:
            os.write(self._fd, data)
        except Exception as e:
            logger.error("PME: raw write error: %s", e)

    def _raw_read(self, size: int) -> bytes:
        if self._fd is None:
            return b""
        try:
            return os.read(self._fd, size)
        except Exception as e:
            logger.error("PME: raw read error: %s", e)
            return b""

    # --- transmission lock (mimics old TwoWire) ---

    def _begin_transmission(self, oid: int = 0) -> int:
        if self._busy:
            return 0
        self._busy = True
        self._iid = int(str(int(time.time())) + str(oid))
        return self._iid

    def _end_transmission(self, iid: int) -> None:
        if self._iid == iid and self._busy:
            time.sleep(self._enddelay)
        self._busy = False
        self._iid = -1

    # --- PME protocol operations (synchronous, run in threads) ---

    def _sync_pme_write_retry(self, fnc: int, port: int, value: int) -> None:
        data = bytes([fnc, port & 0xFF, value & 0xFF, (value >> 8) & 0xFF])
        iid = 0
        for _ in range(10):
            iid = self._begin_transmission(port)
            if iid != 0:
                break
            time.sleep(0.01)
        if iid != 0:
            self._raw_write(data)
            self._end_transmission(iid)

    def _sync_pme_read(self, dtype: int, port: int) -> int:
        fnc = CMD_DIGITAL_READ if dtype == self.PORT_TYPE_DIGITAL else CMD_ANALOG_READ
        p = port
        if dtype == self.PORT_TYPE_ANALOG and port > 19:
            p = port - 20
        cmd = bytes([fnc, p & 0xFF, 0, 0])
        iid = 0
        for _ in range(10):
            iid = self._begin_transmission(port)
            if iid != 0:
                break
            time.sleep(0.01)
        if iid == 0:
            return -1
        self._raw_write(cmd)
        if dtype == self.PORT_TYPE_DIGITAL:
            time.sleep(0.001)
        else:
            time.sleep(0.01)
        buf = self._raw_read(4)
        self._end_transmission(iid)
        if len(buf) >= 4:
            if buf[2] == 0xFF and buf[3] == 0xFF:
                if self._sketch == 0:
                    logger.error("ProMini I2C frozen, restart it manually!")
                return -1
            if dtype == self.PORT_TYPE_DIGITAL:
                result = buf[0]
                if result not in (0, 1):
                    return -1
                return result
            else:
                return buf[1] << 8 | buf[0]
        return -1

    def _sync_check_sketch(self) -> int:
        pn = 0x10
        iid = 0
        for _ in range(10):
            iid = self._begin_transmission(pn)
            if iid != 0:
                break
            time.sleep(0.01)
        if iid == 0:
            return 0
        self._raw_write(bytes([pn, 0, 0, 0]))
        time.sleep(0.01)
        data = self._raw_read(4)
        self._end_transmission(iid)
        if len(data) > 3:
            if data[0] == pn and data[1] == 0xFE and data[2] != 0 and data[2] != 0xFF:
                return int(data[3])
        return 0

    def _sync_init(self, address: int) -> bool:
        if not self._raw_connect(address):
            return False
        if self._sketch < 1:
            self._sketch = self._sync_check_sketch()
        if self._sketch == 0:
            self._enddelay = 0.1
            logger.info("PME: no sketch version detected, using safe slow timing")
        else:
            self._enddelay = 0.001
            logger.info("PME: sketch v%d detected", self._sketch)
        return True

    # --- async wrappers ---

    async def _pme_write(self, fnc: int, port: int, value: int) -> None:
        async with self._i2c_lock:
            await asyncio.to_thread(self._sync_pme_write_retry, fnc, port, value)

    async def _pme_read(self, dtype: int, port: int) -> int:
        async with self._i2c_lock:
            return await asyncio.to_thread(self._sync_pme_read, dtype, port)

    # --- plugin lifecycle ---

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", 0x7f))
        except (ValueError, TypeError):
            self._addr = 0x7f
        try:
            self._port_type = int(self._config.get("port_type", 0))
        except (ValueError, TypeError):
            self._port_type = 0
        try:
            self._port_num = int(self._config.get("port_num", 0))
        except (ValueError, TypeError):
            self._port_num = 0
        success = await asyncio.to_thread(self._sync_init, self._addr)
        if not success:
            return False
        self._last_switch_state = -1
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", 0x7f)
        self._config.setdefault("port_type", 0)
        self._config.setdefault("port_num", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._port_type == self.PORT_TYPE_SWITCH:
            return False
        value = await self._pme_read(self._port_type, self._port_num)
        if value >= 0:
            event.data["values"] = {"Value": float(value)}
            return True
        return False

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if self._port_type != self.PORT_TYPE_SWITCH:
            return None
        new_value = await self._pme_read(self.PORT_TYPE_DIGITAL, self._port_num)
        if new_value != self._last_switch_state and new_value >= 0:
            self._last_switch_state = new_value
            event.data["values"] = {"Value": float(new_value)}
            return True
        return None

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0] if parts else ""

        if cmd == "extgpio" and len(parts) >= 3:
            try:
                pin = int(parts[1])
                val = int(parts[2])
            except ValueError:
                return False
            if val not in (0, 1):
                return False
            logger.debug("EXTGPIO %d set to %d", pin, val)
            await self._pme_write(CMD_DIGITAL_WRITE, pin, val)
            return True

        if cmd == "extpwm" and len(parts) >= 3:
            try:
                pin = int(parts[1])
                prop = int(parts[2])
            except ValueError:
                return False
            logger.debug("EXTPWM %d: %d", pin, prop)
            await self._pme_write(CMD_ANALOG_WRITE, pin, prop)
            return True

        if cmd in ("extpulse", "extlongpulse") and len(parts) >= 3:
            try:
                pin = int(parts[1])
                val = int(parts[2])
            except ValueError:
                return False
            if val not in (0, 1):
                return False
            duration = int(parts[3]) if len(parts) > 3 else 100
            factor = 1000 if cmd == "extlongpulse" else 1
            dur_ms = duration * factor
            logger.debug("EXTGPIO %d: Pulse started for %d ms", pin, dur_ms)
            await self._pme_write(CMD_DIGITAL_WRITE, pin, val)
            if dur_ms <= 10:
                await asyncio.sleep(dur_ms / 1000.0)
                await self._pme_write(CMD_DIGITAL_WRITE, pin, 1 - val)
            else:
                task = asyncio.create_task(self._delayed_pulse(pin, 1 - val, dur_ms / 1000.0))
                self._pulse_tasks.add(task)
                task.add_done_callback(self._pulse_tasks.discard)
            logger.debug("EXTGPIO %d: Pulse ended", pin)
            return True

        return False

    async def _delayed_pulse(self, pin: int, value: int, delay_sec: float) -> None:
        await asyncio.sleep(delay_sec)
        await self._pme_write(CMD_DIGITAL_WRITE, pin, value)

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        event.data["vtype"] = SENSOR_TYPE_SINGLE
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Value"]
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [
            {"value": a, "label": f"0x{a:02X}"}
            for a in [0x3f, 0x4f, 0x5f, 0x6f, 0x7f]
        ]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select",
             "value": self._config.get("address", 0x7f), "options": addr_opts},
            {"name": "port_type", "label": "Port Type", "type": "select",
             "value": self._config.get("port_type", 0), "options": [
                {"value": 0, "label": "Digital"},
                {"value": 1, "label": "Analog"},
                {"value": 2, "label": "Input (switch)"},
            ]},
            {"name": "port_num", "label": "Port number", "type": "number",
             "value": self._config.get("port_num", 0)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        try:
            self._port_type = int(self._config.get("port_type", 0))
        except (ValueError, TypeError):
            self._port_type = 0
        try:
            self._port_num = int(self._config.get("port_num", 0))
        except (ValueError, TypeError):
            self._port_num = 0
        try:
            self._addr = int(self._config.get("address", 0x7f))
        except (ValueError, TypeError):
            self._addr = 0x7f
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return addr in [0x3f, 0x4f, 0x5f, 0x6f, 0x7f]

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        for task in list(self._pulse_tasks):
            task.cancel()
        self._pulse_tasks.clear()
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Value": 0.0}
