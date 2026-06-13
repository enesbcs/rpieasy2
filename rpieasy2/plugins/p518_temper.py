from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_USB, SENSOR_TYPE_TEMP_HUM

logger = logging.getLogger("rpieasy2.plugin.p518")

try:
    from rpieasy2.lib.temper import Temper as _TemperCls
    _TEMPER: _TemperCls | None = _TemperCls()
except Exception:
    logger.warning("temper library not available")
    _TEMPER = None


class P518Temper(PluginBase):
    PLUGIN_ID = 518
    PLUGIN_NAME = "Environment - USB Temper"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_USB,
        vtype=SENSOR_TYPE_TEMP_HUM,
        value_count=2,
        send_data_option=True,
        timer_option=True,
        formula_option=True,
    )

    SENSOR_TYPES = {
        0: ("Internal temperature", "internal temperature", ""),
        1: ("External temperature", "external temperature", ""),
        2: ("Internal temp + humidity", "internal temperature", "internal humidity"),
        3: ("External temp + humidity", "external temperature", "external humidity"),
    }

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._temp: float = 0.0
        self._hum: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        if _TEMPER is None:
            logger.error("temper library not initialized")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("device_id", "")
        self._config.setdefault("sensor_type", 0)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if _TEMPER is None:
            return False
        try:
            device_id = self._config.get("device_id", "")
            if not device_id:
                return False
            try:
                parts = device_id.split(":")
                target_bus = int(parts[0])
                target_dev = int(parts[1])
            except (IndexError, ValueError, TypeError):
                logger.error("Invalid device_id format: %s", device_id)
                return False
            try:
                sensor_type = int(self._config.get("sensor_type", 0))
            except (ValueError, TypeError):
                sensor_type = 0
            sensor_info = self.SENSOR_TYPES.get(sensor_type)
            if sensor_info is None:
                sensor_type = 0
                sensor_info = self.SENSOR_TYPES[0]
            temp_key = sensor_info[1]
            hum_key = sensor_info[2]
            results = _TEMPER.read()
            found = None
            for r in results:
                try:
                    rb = int(r.get("busnum", 0))
                    rd = int(r.get("devnum", 0))
                except (ValueError, TypeError):
                    continue
                if rb == target_bus and rd == target_dev:
                    found = r
                    break
            if found is None:
                return False
            if temp_key:
                try:
                    self._temp = float(found.get(temp_key, 0.0))
                except (ValueError, TypeError):
                    self._temp = 0.0
            if hum_key:
                try:
                    self._hum = float(found.get(hum_key, 0.0))
                except (ValueError, TypeError):
                    self._hum = 0.0
            event.data["values"] = {"Temperature": self._temp, "Humidity": self._hum}
            return True
        except Exception as e:
            logger.error("Temper read error: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        cur_id = self._config.get("device_id", "")
        try:
            try:
                stype = int(self._config.get("sensor_type", 0))
            except (ValueError, TypeError):
                stype = 0
            if _TEMPER is None:
                raise RuntimeError("temper library not available")
            results = _TEMPER.read()
            dev_opts: list[dict[str, Any]] = []
            for r in results:
                try:
                    bus = int(r.get("busnum", 0))
                    dev = int(r.get("devnum", 0))
                except (ValueError, TypeError):
                    continue
                dev_id = f"{bus}:{dev}"
                label = f"Bus {bus:03d} Dev {dev:03d}"
                fw = r.get("firmware", "")
                if fw:
                    label += f" - {fw}"
                label += f" ({r.get('vendorid', 0):04x}:{r.get('productid', 0):04x})"
                dev_opts.append({"value": dev_id, "label": label})
            if not dev_opts:
                dev_opts = [{"value": "", "label": "No Temper devices found"}]
            elif cur_id and not any(p["value"] == cur_id for p in dev_opts):
                dev_opts.append({"value": cur_id, "label": cur_id})
            form.append({"name": "device_id", "label": "Device", "type": "select",
                         "value": cur_id, "options": dev_opts})
            sensor_opts = [
                {"value": 0, "label": "Internal temperature"},
                {"value": 1, "label": "External temperature"},
                {"value": 2, "label": "Internal temp + humidity"},
                {"value": 3, "label": "External temp + humidity"},
            ]
            form.append({"name": "sensor_type", "label": "Sensor type", "type": "select",
                         "value": str(stype), "options": sensor_opts})
        except Exception:
            form.append({"name": "device_id", "label": "Device", "type": "text",
                         "value": cur_id})
            form.append({"name": "sensor_type", "label": "Sensor type", "type": "number",
                         "value": str(self._config.get("sensor_type", 0))})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Temperature": 0.0, "Humidity": 0.0}
