from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_SERIAL,
    SENSOR_TYPE_QUAD,
    SENSOR_V_TYPE_CURRENT,
)
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p525")

P525_NR_OUTPUT_VALUES = 4

# HDHK multi channel AC current sensors come in an 8 channel and a 16 channel
# variant. Both expose one holding register per channel, but at different base
# addresses (0x28 / 40 for the 8ch model, 0x08 / 8 for the 16ch model).
P525_MODEL_8CH = 8
P525_MODEL_16CH = 16
P525_MODELS = {
    P525_MODEL_8CH: "HDHK-8 (8 channel, 0x28..)",
    P525_MODEL_16CH: "HDHK-16 (16 channel, 0x08..)",
}
P525_START_REG = {
    P525_MODEL_8CH: 40,
    P525_MODEL_16CH: 8,
}
P525_CHANNELS = "ABCDEFGHIJKLMNOP"

# "None" selection: an output slot that is not bound to a physical channel
P525_CHANNEL_NONE = -1

try:
    import serial as _pyserial
    _SERIAL_ERRORS: tuple = (_pyserial.SerialException, OSError)
except ImportError:  # pragma: no cover - pyserial is a hard requirement for this plugin
    _SERIAL_ERRORS = (OSError,)


def _modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class P525HDHK(PluginBase):
    PLUGIN_ID = 525
    PLUGIN_NAME = "Energy (AC) - HDHK Modbus AC current sensor"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
        mqtt_state_class=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._port: str = "/dev/ttyUSB0"
        self._baud: int = 9600
        self._addr: int = 1
        self._de_pin: int = -1
        self._model: int = P525_MODEL_8CH
        self._channels: int = P525_MODEL_8CH
        # Physical channel index (0..15) or P525_CHANNEL_NONE for each output slot
        self._query_config: list[int] = [0, 1, 2, 3]
        self._product: str = ""
        # Serial access lock: prevents closing the port while a read is in flight
        # (closing sets the underlying fd to None and an in-flight os.write raises
        # "'NoneType' object cannot be interpreted as an integer")
        self._io_lock = asyncio.Lock()

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _channel_name(channel: int) -> str:
        if 0 <= channel < len(P525_CHANNELS):
            return P525_CHANNELS[channel]
        return str(channel)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = str(self._config.get("serial_port", "/dev/ttyUSB0") or "/dev/ttyUSB0")
        self._baud = self._safe_int(self._config.get("baudrate", 9600), 9600)
        self._addr = self._safe_int(self._config.get("modbus_addr", 1), 1)
        self._de_pin = self._safe_int(self._config.get("de_pin", -1), -1)
        self._model = self._safe_int(self._config.get("model", P525_MODEL_8CH), P525_MODEL_8CH)
        if self._model not in P525_START_REG:
            self._model = P525_MODEL_8CH
        self._channels = self._model
        for i in range(P525_NR_OUTPUT_VALUES):
            self._query_config[i] = self._safe_int(
                self._config.get(f"channel{i + 1}"), [0, 1, 2, 3][i]
            )
        ok = await self._setup_serial()
        if ok:
            await self._identify()
        return ok

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=pyserial.EIGHTBITS,
                parity=pyserial.PARITY_NONE,
                stopbits=pyserial.STOPBITS_ONE,
                timeout=1,
            )
            return True
        except Exception as e:
            logger.error("HDHK serial init failed: %s", e)
            self._serial = None
            return False

    async def _close_serial(self) -> None:
        async with self._io_lock:
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

    async def _modbus_read(self, reg: int, count: int) -> list[int] | None:
        if not self._serial:
            return None
        req = struct.pack(">BBHH", self._addr, 0x03, reg, count)
        crc = _modbus_crc(req)
        req += struct.pack("<H", crc)
        async with self._io_lock:
            port = self._serial
            if port is None:
                return None
            try:
                if self._de_pin >= 0 and self._hw:
                    self._hw.gpio.write(self._de_pin, 1)
                await asyncio.to_thread(port.write, req)
                if self._de_pin >= 0 and self._hw:
                    await asyncio.sleep(0.001)
                    self._hw.gpio.write(self._de_pin, 0)
                resp = await asyncio.to_thread(port.read, 256)
            except _SERIAL_ERRORS as e:
                logger.warning("HDHK serial error: %s", e)
                return None
            except Exception as e:
                logger.debug("HDHK modbus error: %s", e)
                return None
        if len(resp) < 5:
            logger.debug("HDHK: short response (%d bytes)", len(resp))
            return None
        if resp[1] & 0x80:
            logger.warning("HDHK: exception response, code %d", resp[2] if len(resp) > 2 else -1)
            return None
        data_len = resp[2]
        if len(resp) < 3 + data_len + 2:
            return None
        expected_crc = _modbus_crc(resp[:3 + data_len])
        recv_crc = struct.unpack("<H", resp[3 + data_len:5 + data_len])[0]
        if expected_crc != recv_crc:
            logger.warning("HDHK CRC mismatch")
            return None
        return [struct.unpack(">H", resp[3 + 2 * i:5 + 2 * i])[0] for i in range(count)]

    async def _read_channel(self, channel: int) -> float | None:
        start = P525_START_REG.get(self._model, 40)
        words = await self._modbus_read(start + channel, 1)
        if not words:
            return None
        return round(words[0] * 0.01, 2)

    async def _identify(self) -> None:
        # Register 0: firmware version, 1/2: current range, 4: production date
        try:
            fw = await self._modbus_read(0, 1)
            if not fw or not fw[0]:
                self._product = ""
                return
            r1 = await self._modbus_read(1, 1)
            r2 = await self._modbus_read(2, 1)
            date = await self._modbus_read(4, 1)
            d = f"{date[0]:04X}" if date else "0000"
            self._product = (
                f"HD{r1[0] if r1 else 0}A{r2[0] if r2 else 0}A "
                f"(20{d[0:2]}/{d[2:4]}) v{fw[0]}"
            )
            logger.info("HDHK %s detected on %s (addr %d)", self._product, self._port, self._addr)
        except Exception as e:
            self._product = ""
            logger.debug("HDHK identify failed: %s", e)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._serial:
            return False
        out: dict[str, float] = {}
        for i, channel in enumerate(self._query_config):
            if channel is None or channel < 0:
                continue
            value = await self._read_channel(channel)
            if value is None:
                logger.warning(
                    "HDHK: no valid response for channel %s at address %d",
                    self._channel_name(channel), self._addr,
                )
                continue
            out[f"Amper_{self._channel_name(channel)}"] = value
        if not out:
            return False
        event.data["values"] = out
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyUSB0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("modbus_addr", 1)
        self._config.setdefault("model", P525_MODEL_8CH)
        self._config.setdefault("de_pin", -1)
        self._config.setdefault("channel1", 0)
        self._config.setdefault("channel2", 1)
        self._config.setdefault("channel3", 2)
        self._config.setdefault("channel4", 3)
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        port = str(self._config.get("serial_port", "/dev/ttyUSB0"))
        try:
            import serial.tools.list_ports
            ports_found = serial.tools.list_ports.comports()
            port_options = [{"value": p.device, "label": p.device} for p in ports_found]
            if not port_options:
                port_options = [{"value": "", "label": "No serial ports found"}]
            elif port and not any(p["value"] == port for p in port_options):
                port_options.append({"value": port, "label": port})
        except Exception:
            port_options = [{"value": port or "", "label": port or "/dev/ttyUSB0"}]
        form.append({"name": "serial_port", "label": "Serial Device", "type": "select",
                     "value": port, "options": port_options})
        form.append({"name": "baudrate", "label": "Baud Rate", "type": "number",
                     "value": self._config.get("baudrate", 9600)})
        form.append({"name": "modbus_addr", "label": "Modbus Address", "type": "number",
                     "value": self._config.get("modbus_addr", 1), "min": 1, "max": 247})
        form.append({"name": "model", "label": "Sensor Model", "type": "select",
                     "value": self._config.get("model", P525_MODEL_8CH),
                     "options": [{"value": k, "label": v} for k, v in P525_MODELS.items()]})
        form.append({"name": "de_pin", "label": "DE Pin (RS485)", "type": "number",
                     "value": self._config.get("de_pin", -1)})
        model = self._safe_int(self._config.get("model", P525_MODEL_8CH), P525_MODEL_8CH)
        if model not in P525_START_REG:
            model = P525_MODEL_8CH
        channel_options = [{"value": P525_CHANNEL_NONE, "label": "None"}]
        channel_options += [
            {"value": ch, "label": self._channel_name(ch)} for ch in range(model)
        ]
        for i in range(P525_NR_OUTPUT_VALUES):
            form.append({
                "name": f"channel{i + 1}",
                "label": f"Value {i + 1} Channel",
                "type": "select",
                "value": self._config.get(f"channel{i + 1}", [0, 1, 2, 3][i]),
                "options": channel_options,
            })
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = self._safe_int(self._config.get("modbus_addr"), 1)
        self._baud = self._safe_int(self._config.get("baudrate"), 9600)
        self._model = self._safe_int(self._config.get("model"), P525_MODEL_8CH)
        if self._model not in P525_START_REG:
            self._model = P525_MODEL_8CH
        self._channels = self._model
        for i in range(P525_NR_OUTPUT_VALUES):
            self._query_config[i] = self._safe_int(
                self._config.get(f"channel{i + 1}"), [0, 1, 2, 3][i]
            )
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin", "number": 2},
            {"label": "DE Pin", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_CURRENT] * P525_NR_OUTPUT_VALUES
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Amper_A": 0.0, "Amper_B": 0.0, "Amper_C": 0.0, "Amper_D": 0.0}
