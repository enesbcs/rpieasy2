from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p135")

SCD4X_ADDR = 0x62
SCD4X_CMD_MEAS = 0x21B1
SCD4X_CMD_READ = 0xEC05
SCD4X_CMD_STOP = 0x3F86
SCD4X_CMD_SET_ALT = 0x2427
SCD4X_CMD_GET_ALT = 0x2322
SCD4X_CMD_SET_TEMP_OFF = 0x241D
SCD4X_CMD_GET_TEMP_OFF = 0x2328
SCD4X_CMD_SET_ABC = 0x2416
SCD4X_CMD_GET_ABC = 0x2313


def _scd4x_crc(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


class P135SCD4x(PluginBase):
    PLUGIN_ID = 135
    PLUGIN_NAME = "Gases - CO2 SCD4x"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_TRIPLE,
        value_count=3,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
        i2c_max100khz=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = SCD4X_ADDR
        self._co2: int = 0
        self._temp: float = 0.0
        self._hum: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = SCD4X_ADDR
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            alt = int(self._config.get("altitude") or 0)
            if alt:
                cmd = struct.pack(">H", SCD4X_CMD_SET_ALT)
                alt_bytes = struct.pack(">H", alt)
                crc = _scd4x_crc(alt_bytes)
                await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]) + list(alt_bytes) + [crc])
                await asyncio.sleep(0.001)
            temp_off = float(self._config.get("temp_offset") or 0)
            if temp_off:
                off_raw = int(temp_off * 65535.0 / 175.0)
                cmd = struct.pack(">H", SCD4X_CMD_SET_TEMP_OFF)
                off_bytes = struct.pack(">H", off_raw)
                crc = _scd4x_crc(off_bytes)
                await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]) + list(off_bytes) + [crc])
                await asyncio.sleep(0.001)
            abc = self._config.get("auto_calibration", True)
            cmd = struct.pack(">H", SCD4X_CMD_SET_ABC)
            abc_bytes = struct.pack(">H", 1 if abc else 0)
            crc = _scd4x_crc(abc_bytes)
            await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]) + list(abc_bytes) + [crc])
            await asyncio.sleep(0.001)
        except Exception as e:
            logger.error("SCD4x init config failed: %s", e)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("sensor_type", 0)
        self._config.setdefault("altitude", 0)
        self._config.setdefault("temp_offset", "0")
        self._config.setdefault("low_power", False)
        self._config.setdefault("single_shot", False)
        self._config.setdefault("auto_calibration", True)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            cmd = struct.pack(">H", SCD4X_CMD_MEAS)
            await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]))
            await asyncio.sleep(5)
            cmd = struct.pack(">H", SCD4X_CMD_READ)
            await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]))
            await asyncio.sleep(0.01)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 9)
            if len(d) < 9:
                return False
            if _scd4x_crc(bytes(d[0:2])) != d[2]:
                logger.debug("SCD4x CRC failed on CO2")
                return False
            co2_raw = struct.unpack(">H", bytes(d[0:2]))[0]
            temp_raw = struct.unpack(">H", bytes(d[3:5]))[0]
            hum_raw = struct.unpack(">H", bytes(d[6:8]))[0]
            self._co2 = co2_raw
            self._temp = round(-45.0 + 175.0 * temp_raw / 65535.0, 2)
            self._hum = round(100.0 * hum_raw / 65535.0, 1)
            temp_off = float(self._config.get("temp_offset") or "0")
            self._temp += temp_off
            event.data["values"] = {"CO2": self._co2, "Humidity": self._hum, "Temperature": self._temp}
            return True
        except Exception as e:
            logger.error("SCD4x read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        if command.startswith("scd4x"):
            parts = command.split(",")
            if len(parts) > 1:
                sub = parts[1].strip()
                if sub == "setfrc" and len(parts) > 2 and self._hw:
                    frc = int(parts[2])
                    if 400 <= frc <= 2000:
                        try:
                            i2c = self._hw.i2c
                            stop_cmd = struct.pack(">H", SCD4X_CMD_STOP)
                            await i2c.write_i2c_block_data(self._addr, stop_cmd[0], list(stop_cmd[1:]))
                            await asyncio.sleep(0.5)
                            frc_bytes = struct.pack(">H", frc)
                            crc = _scd4x_crc(frc_bytes)
                            frc_cmd = struct.pack(">H", 0x362F)
                            await i2c.write_i2c_block_data(self._addr, frc_cmd[0], list(frc_cmd[1:]) + list(frc_bytes) + [crc])
                            await asyncio.sleep(0.5)
                            meas_cmd = struct.pack(">H", SCD4X_CMD_MEAS)
                            await i2c.write_i2c_block_data(self._addr, meas_cmd[0], list(meas_cmd[1:]))
                            self._config["auto_calibration"] = False
                            return True
                        except Exception:
                            pass
                if sub == "factoryreset" and self._hw:
                    try:
                        i2c = self._hw.i2c
                        fr_cmd = struct.pack(">H", 0x3632)
                        await i2c.write_i2c_block_data(self._addr, fr_cmd[0], list(fr_cmd[1:]))
                        await asyncio.sleep(1.2)
                        return True
                    except Exception:
                        pass
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "sensor_type", "label": "Sensor model", "type": "select", "value": self._config.get("sensor_type", 0), "options": [
                {"value": 0, "label": "SCD40"},
                {"value": 1, "label": "SCD41"},
            ]},
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
            {"name": "temp_offset", "label": "Temp offset (C)", "type": "text", "value": self._config.get("temp_offset", "0")},
            {"name": "low_power", "label": "Low-power measurement", "type": "checkbox", "value": self._config.get("low_power", False)},
            {"name": "single_shot", "label": "Single-shot measurements (SCD41 only)", "type": "checkbox", "value": self._config.get("single_shot", False)},
            {"name": "auto_calibration", "label": "Automatic Self Calibration", "type": "checkbox", "value": self._config.get("auto_calibration", True)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == SCD4X_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = SCD4X_ADDR
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_CO2, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_TEMP]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"CO2": 0, "Humidity": 0.0, "Temperature": 0.0}
