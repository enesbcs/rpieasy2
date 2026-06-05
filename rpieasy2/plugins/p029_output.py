from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SINGLE, SENSOR_TYPE_SWITCH, SENSOR_V_TYPE_SWITCH, SENSOR_V_TYPE_CAN_SET
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p029")


class P029Output(PluginBase):
    PLUGIN_ID = 29
    PLUGIN_NAME = "Output - Domoticz MQTT Helper"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SINGLE,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
    )

    def __init__(self):
        super().__init__()
        self._pin: int = -1
        self._config: dict[str, Any] = {}
        self._state: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin = int(self._config.get("pin") or -1)
        if self._pin > 0 and self._hw:
            try:
                self._hw.gpio.claim_output(self._pin)
                init_val = int(self._config.get("init_state") or 0)
                inverted = self._config.get("inverted", False)
                self._state = 1 - init_val if inverted else init_val
                self._hw.gpio.write(self._pin, self._state)
            except Exception as e:
                logger.error(f"Output pin init failed: {e}")
                # disable task to avoid repeated failures
                try:
                    cfg = get_config()
                    task = cfg.get_task(event.task_index) if event.task_index >= 0 else None
                    if task is not None:
                        task["enabled"] = False
                        task["TDE"] = False
                        cfg.set_task(event.task_index, task)
                        cfg.save()
                        await get_event_bus().publish(Event(type="TASK_CONFIG_CHANGED", task_index=event.task_index, data={"task_config": task}))
                except Exception:
                    logger.exception("Failed to disable task after GPIO init error")
                return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._pin < 0:
            return False
        if self._hw:
            try:
                self._state = self._hw.gpio.read(self._pin)
            except Exception:
                pass
        val = self._state
        event.data["values"] = {"Output": val}
        event.data["named_values"] = {"Output": val}
        event.data["value_names"] = ["Output"]
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        if self._pin < 0 or not self._hw:
            return False
        try:
            # Normalize incoming value: either numeric string in string1 or parsed JSON
            if event.parsed_json and "value" in event.parsed_json:
                val = int(event.parsed_json.get("value"))
            else:
                val = int(event.string1)
            inverted = self._config.get("inverted", False)
            self._state = 1 - val if inverted else val
            self._hw.gpio.write(self._pin, self._state)
            # Report named values so other subsystems can resolve by name
            event.data["values"] = {"Output": val}
            event.data["named_values"] = {"Output": val}
            event.data["value_names"] = ["Output"]
            return True
        except (ValueError, TypeError):
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "inverted", "label": "Invert On/Off value", "type": "checkbox", "value": self._config.get("inverted", False)},
            {"name": "pin", "label": "GPIO Pin", "type": "number", "value": self._config.get("pin", "")},
            {"name": "init_state", "label": "Init State", "type": "select", "value": self._config.get("init_state", 0), "options": [{"value": 0, "label": "Off"}, {"value": 1, "label": "On"}]},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [{"label": "GPIO Pin (output)", "number": 1}]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH | SENSOR_V_TYPE_CAN_SET]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Output": 0}
