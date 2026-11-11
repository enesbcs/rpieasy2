from __future__ import annotations

import logging
import time as time_mod
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SINGLE, SENSOR_TYPE_SWITCH, SENSOR_V_TYPE_SWITCH
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
        self._longpress_fired: bool = False
        self._safe_pending_state: int | None = None
        self._safe_pending_since: float = 0.0

    def _value_name(self) -> str:
        name = (
            self._config.get("TDVN1")
            or self._config.get("value_name")
            or self._config.get("valuename")
        )
        return str(name) if name else "State"

    def _long_press_mode(self) -> int:
        raw = self._config.get("long_press_mode", self._config.get("lp_mode"))
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        return 3 if bool(self._config.get("long_press", False)) else 0

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
        value_name = self._value_name()
        event.data["values"] = {value_name: val}
        event.data["named_values"] = {value_name: val}
        event.data["value_names"] = [value_name]
        logger.debug("on_plugin_read: pin=%s val=%s name=%s", self._pin, val, value_name)
        return True

    async def _publish_change(
        self,
        value: int,
        raw: int,
        *,
        long_press: bool = False,
        double_click: bool = False,
    ) -> None:
        event = Event(
            type="PLUGIN_GPIO_CHANGE",
            task_index=self._task_index,
            data={
                "state": value,
                "event_value": value,
                "raw_state": raw,
                "value_name": self._value_name(),
                "long_press": long_press,
                "double_click": double_click,
                "safe_button": bool(self._config.get("safe_button", False)),
            },
        )
        await get_event_bus().publish(event)

    def _long_press_value(self, raw: int) -> int:
        # ESPEasy applies only inverse-logic to the raw pin state here; the
        # push-button type does not change the long-press event value.
        send_state = raw
        if bool(self._config.get("inverse_logic", False)):
            send_state = 1 if send_state == 0 else 0
        return 11 if send_state else 10

    async def _check_long_press(
        self,
        raw: int,
        reported_state: int,
        mode: int,
        min_interval_ms: int,
        now: float,
    ) -> None:
        if not self._is_pressing or self._longpress_fired or mode == 0:
            return
        # ESPEasy SWITCH_LONGPRESS_LOW/HIGH/BOTH
        if mode == 1 and raw != 0:
            return
        if mode == 2 and raw != 1:
            return
        if (now - self._press_start) * 1000.0 < min_interval_ms:
            return
        self._longpress_fired = True
        # ESPEasy reports state + 10, so state 0 -> 10 and state 1 -> 11.
        value = self._long_press_value(raw)
        await self._publish_change(value, raw, long_press=True)

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if self._pin < 0 or not self._hw:
            return None
        now = time_mod.time()
        debounce_ms = int(self._config.get("debounce") or 20)
        raw = self._hw.gpio.read(self._pin)
        btn_type = int(self._config.get("button_type") or 0)
        state = self._apply_button_type(raw)

        double_click = bool(self._config.get("double_click", False))
        safe_button = bool(self._config.get("safe_button", False))
        dc_max_ms = max(1000, int(self._config.get("dc_max_interval") or 1000))
        lp_mode = self._long_press_mode()
        lp_min_ms = max(500, int(self._config.get("lp_min_interval") or 500))

        # ESPEasy safe button waits one extra 100ms cycle before accepting a
        # change, but long-press/double-click handling must still run.
        changed = state != self._last_state
        if safe_button and changed:
            if self._safe_pending_state != state:
                self._safe_pending_state = state
                self._safe_pending_since = now
                return None
            if (now - self._safe_pending_since) * 1000.0 < max(debounce_ms, 100):
                return None
        self._safe_pending_state = None

        if now < self._debounce_until:
            await self._check_long_press(raw, state, lp_mode, lp_min_ms, now)
            return None

        # Active state is based on the raw pin level for push buttons, before
        # inverse-logic is applied to the reported value.
        is_active = (raw == 0) if btn_type == 1 else (raw == 1)

        if changed:
            self._debounce_until = now + debounce_ms / 1000.0
            self._last_state = state
            self._longpress_fired = False
            self._press_start = now
            self._is_pressing = is_active

            skipped = False
            if double_click and is_active:
                if (
                    self._click_count
                    and (now - self._last_click_time) * 1000.0 > dc_max_ms
                ):
                    self._click_count = 0
                self._click_count += 1
                self._last_click_time = now
                if self._click_count >= 2:
                    self._click_count = 0
                    await self._publish_change(3, raw, double_click=True)
                    skipped = True

            if not skipped and not double_click:
                await self._publish_change(state, raw)
            return None

        # State unchanged: long press fires at the threshold while held.
        await self._check_long_press(raw, state, lp_mode, lp_min_ms, now)

        # Emit the deferred single click once the double-click window expires.
        if (
            double_click
            and self._click_count == 1
            and (now - self._last_click_time) * 1000.0 > dc_max_ms
        ):
            self._click_count = 0
            if not self._longpress_fired:
                await self._publish_change(state, raw)
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
            {"name": "dc_max_interval", "label": "Double-click Max Interval (ms)", "type": "number", "value": self._config.get("dc_max_interval", 1000)},
            {"name": "long_press", "label": "Long Press Detection (legacy checkbox)", "type": "checkbox", "value": self._config.get("long_press", False)},
            {"name": "long_press_mode", "label": "Longpress event", "type": "select", "value": self._long_press_mode(), "options": [
                {"value": 0, "label": "Disabled"},
                {"value": 1, "label": "Active only on LOW (EVENT= 10 [NORMAL] or 11 [INVERSED])"},
                {"value": 2, "label": "Active only on HIGH (EVENT= 11 [NORMAL] or 10 [INVERSED])"},
                {"value": 3, "label": "Active on LOW & HIGH (EVENT= 10 or 11)"},
            ]},
            {"name": "lp_min_interval", "label": "Long Press Min Interval (ms)", "type": "number", "value": self._config.get("lp_min_interval", 500)},
            {"name": "safe_button", "label": "Use Safe Button (slower)", "type": "checkbox", "value": self._config.get("safe_button", False)},
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
        if self._last_task_values:
            return dict(self._last_task_values)
        return {self._value_name(): 0}
