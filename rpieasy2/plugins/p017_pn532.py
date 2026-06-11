from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p017")

PN532_ADDR = 0x24

PN532_PREAMBLE = 0x00
PN532_STARTCODE1 = 0x00
PN532_STARTCODE2 = 0xFF
PN532_HOSTTOPN532 = 0xD4
PN532_PN532TOHOST = 0xD5

PN532_COMMAND_GETFIRMWAREVERSION = 0x02
PN532_COMMAND_SAMCONFIGURATION = 0x14
PN532_COMMAND_INLISTPASSIVETARGET = 0x4A
PN532_MIFARE_ISO14443A = 0x00


class P017PN532(PluginBase):
    PLUGIN_ID = 17
    PLUGIN_NAME = "RFID - PN532"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )
    I2C_ADDRESSES = [0x24]

    def __init__(self):
        super().__init__()
        self._addr: int = PN532_ADDR
        self._config: dict[str, Any] = {}
        self._last_tag: str = ""
        self._bg_task: asyncio.Task | None = None
        self._bg_running: bool = False

    async def _write_cmd(self, cmd: bytes) -> None:
        i2c = self._hw.i2c
        frame = bytes([PN532_PREAMBLE, PN532_STARTCODE1, PN532_STARTCODE2,
                       len(cmd) + 1, ~(len(cmd) + 1) & 0xFF,
                       PN532_HOSTTOPN532]) + cmd
        chk = sum(frame[5:]) & 0xFF
        frame += bytes([~chk & 0xFF, 0x00])
        await i2c.write_i2c_block_data(self._addr, 0x00, list(frame))

    async def _read_ack(self) -> bool:
        await asyncio.sleep(0.005)
        return True

    async def _read_response(self, length: int = 32) -> bytes:
        i2c = self._hw.i2c
        d = await i2c.read_i2c_block_data(self._addr, 0x00, length)
        return bytes(d)

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = PN532_ADDR
        if not self._hw:
            return False
        try:
            await self._write_cmd(bytes([PN532_COMMAND_GETFIRMWAREVERSION]))
            await self._read_ack()
            resp = await self._read_response(16)
            if len(resp) < 8 or resp[6] != 0xD5:
                logger.warning("PN532 not responding correctly")
                return False
            ver = resp[7] if len(resp) > 7 else 0
            rev = resp[8] if len(resp) > 8 else 0
            logger.info("PN532 firmware v%s.%s initialized", ver, rev)
            await self._write_cmd(bytes([PN532_COMMAND_SAMCONFIGURATION, 0x01, 0x14, 0x01]))
            await self._read_ack()
            self._bg_running = True
            self._bg_task = asyncio.ensure_future(self._bg_reader(event.task_index))
            return True
        except Exception as e:
            logger.error("PN532 init failed: %s", e)
            return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("auto_tag_removal", True)
        self._config.setdefault("event_on_tag_removal", False)
        return True

    async def _bg_reader(self, task_index: int) -> None:
        while self._bg_running and self._hw:
            try:
                await self._write_cmd(bytes([PN532_COMMAND_INLISTPASSIVETARGET, 0x01, PN532_MIFARE_ISO14443A]))
                await self._read_ack()
                resp = await self._read_response(32)
                if len(resp) > 10 and resp[7] == 0x01:
                    uid_len = resp[9] & 0x0F if len(resp) > 9 else 0
                    uid_start = 12 if len(resp) > 12 else 0
                    uid = resp[uid_start:uid_start + uid_len]
                    tag_hex = uid.hex().upper()
                    if tag_hex != self._last_tag:
                        self._last_tag = tag_hex
                        await get_event_bus().publish(Event(
                            type="PLUGIN_READ", task_index=task_index,
                            data={"values": {"Tag": tag_hex}}
                        ))
                elif int(self._config.get("auto_tag_removal", 1)):
                    if self._last_tag:
                        self._last_tag = ""
                        await get_event_bus().publish(Event(
                            type="PLUGIN_READ", task_index=task_index,
                            data={"values": {"Tag": ""}}
                        ))
            except Exception:
                pass
            await asyncio.sleep(0.3)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            await self._write_cmd(bytes([PN532_COMMAND_INLISTPASSIVETARGET, 0x01, PN532_MIFARE_ISO14443A]))
            await self._read_ack()
            resp = await self._read_response(32)
            if len(resp) > 10 and resp[7] == 0x01:
                uid_len = resp[9] & 0x0F if len(resp) > 9 else 0
                uid_start = 12 if len(resp) > 12 else 0
                uid = resp[uid_start:uid_start + uid_len]
                tag_hex = uid.hex().upper()
                self._last_tag = tag_hex
                event.data["values"] = {"Tag": tag_hex}
            else:
                event.data["values"] = {"Tag": self._last_tag if int(self._config.get("auto_tag_removal", 1)) else ""}
            return True
        except Exception as e:
            logger.error("PN532 read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "auto_tag_removal", "label": "Auto tag removal", "type": "checkbox", "value": self._config.get("auto_tag_removal", True)},
            {"name": "event_on_tag_removal", "label": "Event on tag removal", "type": "checkbox", "value": self._config.get("event_on_tag_removal", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return event.data.get("address", 0) == PN532_ADDR

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = PN532_ADDR
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Tag"]
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        self._bg_running = False
        if self._bg_task:
            self._bg_task.cancel()
            self._bg_task = None
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Tag": ""}
