from __future__ import annotations

import logging
import time as time_mod
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SINGLE, SENSOR_TYPE_SWITCH
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p001")

class P001Switch(PluginBase):
    PLUGIN_ID = 1
    PLUGIN_NAME = "Switch input - Switch"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_SINGLE, vtype=SENSOR_TYPE_SWITCH, value_count=1, inverse_logic_option=True, timer_optional=True, send_data_option=True)

    def __init__(self):
        super().__init__()
        self._pin: int = -1
        self._config: dict[str, Any] = {}
        self._last_state: int = -1
        self._debounce_until: float = 0.0
        self._click_count: int = 0
        self._last_click_time: float = 0.0
        self._press_start: float = 0.0
        self._is_pressing: bool = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin = int(self._config.get("pin") or -1)
        if self._pin > 0 and self._hw:
            try:
                boot_gpio = self._hw.gpio.read_boot_gpio_config()
                pull_up = boot_gpio.get(self._pin, "") == "ip,pu"
                self._hw.gpio.claim_input(self._pin, pull_up=pull_up)
                raw = self._hw.gpio.read(self._pin)
                boot_state = int(self._config.get("boot_state") or 0)
                if boot_state == 1:
                    self._last_state = 1
                else:
                    self._last_state = raw
            except Exception as e:
                logger.error(f"Switch pin init failed: {e}")
                try:
                    cfg = get_config()
                    task = cfg.get_task(event.task_index) if event.task_index >= 0 else None
                    if task is not None:
                        task["enabled"] = False
                        task["TDE"] = False
                        cfg.set_task(event.task_index, task)
                        cfg.save()
                        await get_event_bus().publish(Event(type="TASK_CONFIG_CHANGED", task_index=self._task_index, data={"task_config": task}))
                except Exception:
                    logger.exception("Failed to disable task after GPIO init error")
                return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    def _apply_button_type(self, raw: int) -> int:
        btn_type = int(self._config.get("button_type") or 0)
        inverse = bool(self._config.get("inverse_logic", False))
        if btn_type == 1:
            raw = 1 if raw == 0 else 0
        elif btn_type == 2:
            raw = raw
        if inverse:
            raw = 1 if raw == 0 else 0
        return raw

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._pin < 0:
            logger.debug("on_plugin_read SKIP: pin=%s", self._pin)
            return False
        val = self._last_state if self._last_state >= 0 else 0
        event.data["values"] = {"State": val}
        event.data["named_values"] = {"State": val}
        event.data["value_names"] = ["State"]
        logger.debug("on_plugin_read: pin=%s val=%s", self._pin, val)
        return True

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if self._pin < 0 or not self._hw:
            return None
        now = time_mod.time()
        debounce_ms = int(self._config.get("debounce") or 20)
        if now < self._debounce_until:
            return None
        raw = self._hw.gpio.read(self._pin)
        btn_type = int(self._config.get("button_type") or 0)
        inverse = bool(self._config.get("inverse_logic", False))
        state = self._apply_button_type(raw)
        changed = state != self._last_state
        if changed:
            self._debounce_until = now + debounce_ms / 1000.0
            self._last_state = state
            double_click = bool(self._config.get("double_click", False))
            safe_button = bool(self._config.get("safe_button", False))
            long_press = bool(self._config.get("long_press", False))
            dc_max_ms = int(self._config.get("dc_max_interval") or 250)
            lp_min_ms = int(self._config.get("lp_min_interval") or 300)

            is_active = (state == 1) if btn_type != 1 else (state == 0)

            if safe_button:
                if is_active:
                    self._click_count += 1
                    self._last_click_time = now
                    if self._click_count >= 2:
                        self._click_count = 0
                        ev = Event(type="PLUGIN_GPIO_CHANGE", task_index=self._task_index, data={"state": state})
                        await get_event_bus().publish(ev)
                return None

            if long_press:
                if is_active and not self._is_pressing:
                    self._is_pressing = True
                    self._press_start = now
                elif not is_active and self._is_pressing:
                    self._is_pressing = False
                    press_dur = (now - self._press_start) * 1000.0
                    if press_dur >= lp_min_ms:
                        ev = Event(type="PLUGIN_GPIO_CHANGE", task_index=self._task_index, data={"state": state, "long_press": True})
                        await get_event_bus().publish(ev)
                        return None

            if double_click:
                if is_active:
                    self._click_count += 1
                    self._last_click_time = now
                    if self._click_count >= 2:
                        self._click_count = 0
                        ev = Event(type="PLUGIN_GPIO_CHANGE", task_index=self._task_index, data={"state": state, "double_click": True})
                        await get_event_bus().publish(ev)
                else:
                    if self._click_count == 1 and (now - self._last_click_time) * 1000.0 > dc_max_ms:
                        self._click_count = 0
                        ev = Event(type="PLUGIN_GPIO_CHANGE", task_index=self._task_index, data={"state": state})
                        await get_event_bus().publish(ev)
                return None

            ev = Event(type="PLUGIN_GPIO_CHANGE", task_index=self._task_index, data={"state": state})
            await get_event_bus().publish(ev)
        return None

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "inverse_logic", "label": "Inversed Logic", "type": "checkbox", "value": self._config.get("inverse_logic", False)},
            {"name": "pin", "label": "GPIO Pin", "type": "number", "value": self._config.get("pin", "")},
            {"name": "switch_type", "label": "Switch Type", "type": "select", "value": self._config.get("switch_type", 0), "options": [{"value": 0, "label": "Switch"}, {"value": 1, "label": "Dimmer"}]},
            {"name": "dimmer_value", "label": "Dimmer Value (0-255)", "type": "number", "value": self._config.get("dimmer_value", 0)},
            {"name": "button_type", "label": "Button Type", "type": "select", "value": self._config.get("button_type", 0), "options": [{"value": 0, "label": "Normal Switch"}, {"value": 1, "label": "Push Button Active Low"}, {"value": 2, "label": "Push Button Active High"}]},
            {"name": "boot_state", "label": "Boot State", "type": "select", "value": self._config.get("boot_state", 0), "options": [{"value": 0, "label": "Same as before"}, {"value": 1, "label": "High"}]},
            {"name": "debounce", "label": "Debounce Interval (ms)", "type": "number", "value": self._config.get("debounce", 40)},
            {"name": "double_click", "label": "Double-click Detection", "type": "checkbox", "value": self._config.get("double_click", False)},
            {"name": "dc_max_interval", "label": "Double-click Max Interval (ms)", "type": "number", "value": self._config.get("dc_max_interval", 250)},
            {"name": "long_press", "label": "Long Press Detection", "type": "checkbox", "value": self._config.get("long_press", False)},
            {"name": "lp_min_interval", "label": "Long Press Min Interval (ms)", "type": "number", "value": self._config.get("lp_min_interval", 300)},
            {"name": "safe_button", "label": "Safe Button", "type": "checkbox", "value": self._config.get("safe_button", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [{"label": "GPIO Pin (bidirectional)", "number": 1}]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"State": 0}
