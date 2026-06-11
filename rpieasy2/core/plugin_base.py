from __future__ import annotations

from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event, EventBus, get_event_bus
from rpieasy2.core.formula import FormulaError
from rpieasy2.core.hw import HWManager


class PluginBase:
    PLUGIN_ID: int = 0
    PLUGIN_NAME: str = "Unknown"
    PLUGIN_VALUES: int = 1
    DEVICE_PROPERTIES: DeviceProperties = DeviceProperties()

    def __init__(self):
        self._event_bus: EventBus = get_event_bus()
        self._hw: HWManager | None = None
        self._task_index: int = -1

    def set_hw_manager(self, hw: HWManager) -> None:
        self._hw = hw

    def subscribe(self, task_index: int) -> None:
        self._task_index = task_index
        for et in ("PLUGIN_INIT", "PLUGIN_EXIT", "PLUGIN_READ", "PLUGIN_WRITE",
                   "PLUGIN_ONCE_A_SECOND", "PLUGIN_TEN_PER_SECOND", "PLUGIN_FIFTY_PER_SECOND",
                   "PLUGIN_DEVICE_ADD",
                   "PLUGIN_GET_DEVICE_VALUE_NAMES", "PLUGIN_GET_DEVICEGPIONAMES",
                   "PLUGIN_GET_DEVICEVALUECOUNT", "PLUGIN_GET_DEVICEVTYPE",
                   "PLUGIN_GET_DISCOVERY_VTYPES",
                   "PLUGIN_SET_DEFAULTS",
                   "PLUGIN_TASKTIMER_IN", "PLUGIN_CLOCK_IN",
                   "PLUGIN_GET_CONFIG_VALUE",
                    "PLUGIN_WEBFORM_LOAD", "PLUGIN_WEBFORM_SAVE",
                    "PLUGIN_WEBFORM_SHOW_CONFIG", "PLUGIN_WEBFORM_SHOW_VALUES",
                    "PLUGIN_WEBFORM_LOAD_OUTPUT_SELECTOR"):
            self._event_bus.subscribe(et, self._dispatch)

    async def _dispatch(self, event: Event) -> bool | None:
        if event.task_index >= 0 and event.task_index != self._task_index:
            return None
        if event.type == "PLUGIN_READ":
            tc = getattr(self, "_config", None)
            if tc and tc.get("data_feed_source", 0) != 0:
                return None
        handler = getattr(self, f"on_{event.type.lower()}", None)
        if handler:
            collect_timing = False
            if event.type == "PLUGIN_READ":
                from rpieasy2.core.config import get_config
                collect_timing = get_config().data.get("system", {}).get("collect_timing_statistics", False)
            if collect_timing:
                import time
                t0 = time.perf_counter()
            result = await handler(event)
            if event.type == "PLUGIN_READ" and self.DEVICE_PROPERTIES.plugin_stats:
                values = event.data.get("values", {})
                if values:
                    from rpieasy2.core.plugin_stats import get_plugin_stats, init_plugin_stats
                    stats = get_plugin_stats(event.task_index)
                    if stats is None:
                        stats = init_plugin_stats(event.task_index)
                    stats.push(values)
            if event.type == "PLUGIN_READ" and self.DEVICE_PROPERTIES.formula_option:
                self._apply_formulas(event)
            if collect_timing:
                elapsed = time.perf_counter() - t0
                from rpieasy2.core.timing_stats import get_timing_stats, init_timing_stats
                ts = get_timing_stats(event.task_index)
                if ts is None:
                    ts = init_timing_stats(event.task_index)
                ts.push(elapsed)
            return result
        return None

    def _apply_formulas(self, event: Event) -> None:
        values = event.data.get("values", {})
        if not values:
            return
        try:
            from rpieasy2.core.formula import evaluate as eval_formula
        except ImportError:
            return
        tc = getattr(self, "_config", None)
        if tc is None:
            return
        changed = False
        for vi_str, raw_val in list(values.items()):
            try:
                vi = int(vi_str) if not isinstance(vi_str, int) else vi_str
            except (ValueError, TypeError):
                continue
            key = f"TDF{vi}"
            formula_str = tc.get(key, "")
            if not formula_str:
                continue
            try:
                raw_float = float(raw_val)
                result = eval_formula(formula_str, raw_float)
                values[vi_str] = result
                changed = True
            except (ValueError, TypeError, FormulaError):
                pass
        if changed:
            event.data["values"] = values

    async def on_plugin_init(self, event: Event) -> bool | None:
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        return True

    async def on_plugin_once_a_second(self, event: Event) -> bool | None:
        return None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        return None

    async def on_plugin_device_add(self, event: Event) -> bool | None:
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        sensor_type = event.sensor_type
        num_values = event.data.get("num_values", self.PLUGIN_VALUES)
        from rpieasy2.core.rpiconst import get_discovery_vtypes
        vtypes = get_discovery_vtypes(sensor_type if sensor_type > 0 else self.DEVICE_PROPERTIES.vtype, num_values)
        event.data["vtypes"] = vtypes
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return None

    async def on_plugin_tasktimer_in(self, event: Event) -> bool | None:
        return None

    async def on_plugin_clock_in(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_config_value(self, event: Event) -> bool | None:
        return None

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        return True

    async def on_plugin_webform_show_config(self, event: Event) -> bool | None:
        return True

    async def on_plugin_webform_show_values(self, event: Event) -> bool | None:
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}

    def unsubscribe(self) -> None:
        self._event_bus.clear()
