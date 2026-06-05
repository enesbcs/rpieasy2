from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SPI3, SENSOR_TYPE_ULONG
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p111")

MFRC522_COMMAND_REG = 0x01
MFRC522_COM_ENABLE_REG = 0x02
MFRC522_FIFODATA_REG = 0x09
MFRC522_FIFOLEVEL_REG = 0x0A
MFRC522_BITFRAMING_REG = 0x0D
MFRC522_COMMAND = 0x01
MFRC522_SOFTRESET = 0x0F
MFRC522_IDLE_CMD = 0x00
MFRC522_CALC_CRC_CMD = 0x03
MFRC522_TRANSCEIVE_CMD = 0x1E
MFRC522_PCD_AUTHENT = 0x0E
MFRC522_PICC_REQIDL = 0x26
MFRC522_PICC_ANTICOLL1 = 0x93


class P111RC522(PluginBase):
    PLUGIN_ID = 111
    PLUGIN_NAME = "RFID - RC522"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SPI3,
        vtype=SENSOR_TYPE_ULONG,
        value_count=1,
        send_data_option=True,
        custom_vtype_var=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._cs_pin: int = -1
        self._rst_pin: int = -1
        self._tag: str = ""
        self._removal_timeout: int = 500
        self._last_tag_time: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._cs_pin = int(self._config.get("pin1") or -1)
        self._rst_pin = int(self._config.get("pin2") or -1)
        self._removal_timeout = int(self._config.get("removal_timeout") or 500)
        if self._cs_pin < 0 or not self._hw:
            return False
        try:
            if self._rst_pin > 0:
                self._hw.gpio.claim_output(self._rst_pin)
                self._hw.gpio.write(self._rst_pin, 1)
            await self._reset()
            return True
        except Exception as e:
            logger.error("RC522 init failed: %s", e)
            return False

    async def _reset(self) -> None:
        if not self._hw:
            return
        try:
            if self._rst_pin > 0:
                self._hw.gpio.write(self._rst_pin, 0)
                import asyncio
                await asyncio.sleep(0.05)
                self._hw.gpio.write(self._rst_pin, 1)
                await asyncio.sleep(0.05)
        except Exception:
            pass

    async def _spi_write(self, reg: int, data: int) -> None:
        if not self._hw:
            return
        try:
            self._hw.spi.xfer2([(reg << 1) & 0x7E, data])
        except Exception:
            pass

    async def _spi_read(self, reg: int) -> int:
        if not self._hw:
            return 0
        try:
            resp = self._hw.spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0x00])
            return resp[1] if len(resp) > 1 else 0
        except Exception:
            return 0

    async def _request_tag(self) -> str | None:
        if not self._hw:
            return None
        try:
            await self._spi_write(MFRC522_BITFRAMING_REG, 0x07)
            await self._spi_write(MFRC522_FIFODATA_REG, MFRC522_PICC_REQIDL)
            await self._spi_write(MFRC522_COMMAND_REG, MFRC522_TRANSCEIVE_CMD)
            import asyncio
            await asyncio.sleep(0.02)
            await self._spi_write(MFRC522_COMMAND_REG, MFRC522_IDLE_CMD)
            await self._spi_write(MFRC522_FIFODATA_REG, MFRC522_PICC_ANTICOLL1)
            await self._spi_write(MFRC522_COMMAND_REG, MFRC522_TRANSCEIVE_CMD)
            await asyncio.sleep(0.02)
            await self._spi_write(MFRC522_COMMAND_REG, MFRC522_IDLE_CMD)
            uid = ""
            for _ in range(5):
                b = await self._spi_read(MFRC522_FIFODATA_REG)
                uid += f"{b:02X}"
            return uid if uid != "0000000000" else None
        except Exception:
            return None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        tag = await self._request_tag()
        import asyncio
        now = asyncio.get_event_loop().time()
        if tag:
            self._tag = tag
            self._last_tag_time = now
        elif self._tag and (now - self._last_tag_time) * 1000 > self._removal_timeout:
            removal_val = int(self._config.get("removal_value") or 0)
            self._tag = f"{removal_val:08X}" if removal_val > 0 else ""
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Tag": self._tag}
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("removal_timeout", 500)
        self._config.setdefault("removal_value", "0")
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "pin1", "label": "GPIO CS PIN", "type": "number", "value": self._config.get("pin1", "")},
            {"name": "pin2", "label": "GPIO RST PIN (optional)", "type": "number", "value": self._config.get("pin2", -1)},
            {"name": "removal_timeout", "label": "Tag removal Time-out (mSec)", "type": "number", "value": self._config.get("removal_timeout", 500)},
            {"name": "removal_value", "label": "Value to set on Tag removal", "type": "number", "value": self._config.get("removal_value", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "CS PIN", "number": 1},
            {"label": "RST PIN (optional)", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Tag": ""}
