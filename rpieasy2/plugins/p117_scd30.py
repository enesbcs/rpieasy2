from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p117")

SCD30_ADDR = 0x61
SCD30_CMD_START_CONT = 0x0010
SCD30_CMD_READ_MEAS = 0x0300
SCD30_CMD_SET_ALT = 0x5100
SCD30_CMD_GET_ALT = 0x5300
SCD30_CMD_SET_TEMP_OFFSET = 0x5400
SCD30_CMD_GET_TEMP_OFFSET = 0x5600
SCD30_CMD_SET_ABC = 0x5300
SCD30_CMD_GET_ABC = 0x5300
SCD30_CMD_SET_INTERVAL = 0x4600
SCD30_CMD_GET_INTERVAL = 0x4600


def _scd30_crc(data: bytes) -> int:
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


class P117SCD30(PluginBase):
    PLUGIN_ID = 117
    PLUGIN_NAME = "Gases - CO2 SCD30"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
        i2c_max100khz=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = SCD30_ADDR
        self._co2: int = 0
        self._hum: float = 0.0
        self._temp: float = 0.0
        self._crc_errors: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = SCD30_ADDR
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            alt = int(self._config.get("altitude") or 0)
            if alt:
                cmd = struct.pack(">HH", SCD30_CMD_SET_ALT, alt)
                await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[2:]))
            return True
        except Exception as e:
            logger.error("SCD30 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("altitude", 0)
        self._config.setdefault("temp_offset", "0")
        self._config.setdefault("measure_interval", 2)
        self._config.setdefault("auto_calibration", True)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            cmd = struct.pack(">H", SCD30_CMD_READ_MEAS)
            await i2c.write_i2c_block_data(self._addr, cmd[0], list(cmd[1:]))
            await asyncio.sleep(0.05)
            d = await i2c.read_i2c_block_data(self._addr, 0x00, 18)
            if len(d) < 18:
                return False
            co2_bytes = bytes(d[0:2])
            temp_bytes = bytes(d[6:8])
            hum_bytes = bytes(d[12:14])
            if _scd30_crc(d[0:2]) != d[2] or _scd30_crc(d[6:8]) != d[8] or _scd30_crc(d[12:14]) != d[14]:
                logger.debug("SCD30 CRC failed")
                self._crc_errors += 1
                if self._crc_errors >= 3:
                    self._crc_errors = 0
                    try:
                        await i2c.write_i2c_block_data(self._addr, 0x00, [0xD6])
                        await asyncio.sleep(2)
                        logger.info("SCD30 soft reset after CRC errors")
                    except Exception:
                        pass
                return False
            self._crc_errors = 0
            co2_raw = struct.unpack(">H", co2_bytes)[0]
            temp_raw = struct.unpack(">H", temp_bytes)[0]
            hum_raw = struct.unpack(">H", hum_bytes)[0]
            self._co2 = round(co2_raw)
            self._temp = round(temp_raw / 100.0 - 273.15, 2)
            self._hum = round(hum_raw / 100.0, 1)
            event.data["values"] = {"CO2": self._co2, "Humidity": self._hum, "Temperature": self._temp, "CO2raw": co2_raw}
            return True
        except Exception as e:
            logger.error("SCD30 read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0]
        try:
            if cmd == "scdgetabc":
                logger.info("SCD30 ABC: %s", self._config.get("auto_calibration", True))
                return True
            if cmd == "scdgetalt":
                logger.info("SCD30 Altitude: %s", self._config.get("altitude", 0))
                return True
            if cmd == "scdgettmp":
                logger.info("SCD30 Temp offset: %s", self._config.get("temp_offset", "0"))
                return True
            if cmd == "scdsetcalibration" and len(parts) > 1:
                val = parts[1].strip() == "1"
                self._config["auto_calibration"] = val
                return True
            if cmd == "scdsetfrc" and len(parts) > 1:
                val = int(parts[1].strip())
                if 400 <= val <= 2000:
                    self._config["auto_calibration"] = False
                    return True
            if cmd == "scdgetinterval":
                logger.info("SCD30 Interval: %s", self._config.get("measure_interval", 2))
                return True
            if cmd == "scdsetinterval" and len(parts) > 1:
                val = max(2, min(1800, int(parts[1].strip())))
                self._config["measure_interval"] = val
                return True
        except (ValueError, IndexError):
            pass
        return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "altitude", "label": "Altitude (m)", "type": "number", "value": self._config.get("altitude", 0)},
            {"name": "temp_offset", "label": "Temp offset (C)", "type": "text", "value": self._config.get("temp_offset", "0")},
            {"name": "measure_interval", "label": "Measurement Interval (sec)", "type": "number", "value": self._config.get("measure_interval", 2)},
            {"name": "auto_calibration", "label": "Automatic Self Calibration", "type": "checkbox", "value": self._config.get("auto_calibration", True)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == SCD30_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = SCD30_ADDR
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_CO2, SENSOR_V_TYPE_HUM, SENSOR_V_TYPE_TEMP, SENSOR_V_TYPE_CO2]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"CO2": 0, "Humidity": 0.0, "Temperature": 0.0, "CO2raw": 0}
