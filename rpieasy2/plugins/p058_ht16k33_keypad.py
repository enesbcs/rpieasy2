from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p058")

HT16K33_KEY_ADDR = 0x70

HT16K33_KEY_REG_KEYS = 0x40
HT16K33_KEY_REG_INT = 0x60


class P058HT16K33KeyPad(PluginBase):
    PLUGIN_ID = 58
    PLUGIN_NAME = "Keypad - HT16K33"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
    )
    I2C_ADDRESSES = [0x70, 0x71, 0x72, 0x73]

    def __init__(self):
        super().__init__()
        self._addr: int = HT16K33_KEY_ADDR
        self._config: dict[str, Any] = {}
        self._last_value: int = -1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(self._config.get("address", HT16K33_KEY_ADDR))
        except (ValueError, TypeError):
            self._addr = HT16K33_KEY_ADDR
        self._last_value = -1
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", HT16K33_KEY_ADDR)
        return True

    async def _scan_keys(self) -> int:
        if not self._hw:
            return 0
        try:
            i2c = self._hw.i2c
            d = await i2c.read_i2c_block_data(self._addr, HT16K33_KEY_REG_KEYS, 16)
            for col in range(3):
                val = d[col * 2]
                if val:
                    for row in range(13):
                        if val & (1 << row):
                            return col * 16 + row + 1
            return 0
        except Exception:
            return 0

    async def on_plugin_read(self, event: Event) -> bool | None:
        scancode = await self._scan_keys()
        event.data["values"] = {"ScanCode": scancode}
        return True

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        scancode = await self._scan_keys()
        if scancode != self._last_value:
            self._last_value = scancode
            await get_event_bus().publish(Event(
                type="PLUGIN_READ", task_index=event.task_index,
                data={"values": {"ScanCode": scancode}},
            ))
        return None

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        addr_opts = [{"value": a, "label": f"0x{a:02X}"} for a in range(0x70, 0x74)]
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", HT16K33_KEY_ADDR), "options": addr_opts},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._addr = int(self._config.get("address", HT16K33_KEY_ADDR))
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        addr = event.data.get("address", 0)
        return 0x70 <= addr <= 0x73

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"ScanCode": 0}
