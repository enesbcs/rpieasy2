from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.config import get_config
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUAL, SENSOR_TYPE_DUAL
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p067")


class P067HX711(PluginBase):
    PLUGIN_ID = 67
    PLUGIN_NAME = "Weight - HX711 Load Cell"
    PLUGIN_VALUES = 2
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUAL,
        vtype=SENSOR_TYPE_DUAL,
        value_count=2,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._pin_sck: int = -1
        self._pin_dt: int = -1
        self._weight_a: float = 0.0
        self._weight_b: float = 0.0
        self._sample_count = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin_sck = int(self._config.get("pin1") or -1)
        self._pin_dt = int(self._config.get("pin2") or -1)
        if self._pin_sck < 0 or self._pin_dt < 0 or not self._hw:
            return False
        try:
            self._hw.gpio.claim_output(self._pin_sck)
            self._hw.gpio.claim_input(self._pin_dt)
            self._hw.gpio.write(self._pin_sck, 0)
        except Exception as e:
            logger.error("HX711 pin init failed: %s", e)
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

    async def _read_raw(self, gain: int = 128) -> int | None:
        dt = self._pin_dt
        sck = self._pin_sck
        if not self._hw:
            return None
        try:
            timeout = 0
            while self._hw.gpio.read(dt) != 0:
                timeout += 1
                if timeout > 100000:
                    return None
                await asyncio.sleep(0.00001)
            val = 0
            for _ in range(24):
                self._hw.gpio.write(sck, 1)
                val = (val << 1) | self._hw.gpio.read(dt)
                self._hw.gpio.write(sck, 0)
            pulses = 1 if gain == 128 else (3 if gain == 64 else 2)
            for _ in range(pulses):
                self._hw.gpio.write(sck, 1)
                self._hw.gpio.write(sck, 0)
            if val & 0x800000:
                val |= ~0xFFFFFF
            return val
        except Exception:
            return None

    def _apply_cal(self, raw: int, offset: float, cal_enabled: bool,
                   adc1: int, out1: float, adc2: int, out2: float) -> float:
        w = (raw - offset) / (128.0 * 256.0)
        if cal_enabled and adc2 != adc1:
            scale = (out2 - out1) / (adc2 - adc1)
            w = out1 + (raw - adc1) * scale
        return w

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        if self._pin_sck < 0:
            return None
        mode_a = int(self._config.get("mode_a") or 1)
        os_a = self._config.get("os_a", False)
        self._sample_count += 1

        os_a = int(self._config.get("os_a") or 1)
        raw_a = None
        for _ in range(max(1, os_a)):
            r = await self._read_raw(gain=128 if mode_a == 2 else 64)
            if r is not None:
                raw_a = r if raw_a is None else raw_a + r
        if raw_a is not None and os_a > 1:
            raw_a //= os_a
        if raw_a is not None:
            offset_a = float(self._config.get("offset_a") or 0)
            cal_a = self._config.get("cal_a", False)
            adc1_a = int(self._config.get("adc1_a") or 0)
            out1_a = float(self._config.get("out1_a") or 0)
            adc2_a = int(self._config.get("adc2_a") or 0)
            out2_a = float(self._config.get("out2_a") or 0)
            self._weight_a = self._apply_cal(raw_a, offset_a, cal_a,
                                              adc1_a, out1_a, adc2_a, out2_a)

        os_b = int(self._config.get("os_b") or 1)
        raw_b = None
        for _ in range(max(1, os_b)):
            r = await self._read_raw(gain=32)
            if r is not None:
                raw_b = r if raw_b is None else raw_b + r
        if raw_b is not None and os_b > 1:
            raw_b //= os_b
        if raw_b is not None:
            offset_b = float(self._config.get("offset_b") or 0)
            cal_b = self._config.get("cal_b", False)
            adc1_b = int(self._config.get("adc1_b") or 0)
            out1_b = float(self._config.get("out1_b") or 0)
            adc2_b = int(self._config.get("adc2_b") or 0)
            out2_b = float(self._config.get("out2_b") or 0)
            self._weight_b = self._apply_cal(raw_b, offset_b, cal_b,
                                              adc1_b, out1_b, adc2_b, out2_b)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if self._sample_count < 1:
            return False
        event.data["values"] = {
            "WeightChanA": round(self._weight_a, 3),
            "WeightChanB": round(self._weight_b, 3),
        }
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        if parts[0] == "tare" and len(parts) > 1:
            ch = parts[1].strip()
            if ch == "a":
                self._config["offset_a"] = str(self._weight_a)
                return True
            if ch == "b":
                self._config["offset_b"] = str(self._weight_b)
                return True
        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("mode_a", 1)
        self._config.setdefault("mode_b", 1)
        self._config.setdefault("offset_a", "0")
        self._config.setdefault("offset_b", "0")
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "pin1", "label": "GPIO SCK", "type": "number", "value": self._config.get("pin1", "")},
            {"name": "pin2", "label": "GPIO DT", "type": "number", "value": self._config.get("pin2", "")},
            {"name": "mode_a", "label": "Channel A Mode", "type": "select", "value": self._config.get("mode_a", 1), "options": [
                {"value": 0, "label": "Off"},
                {"value": 1, "label": "Gain 128"},
                {"value": 2, "label": "Gain 64"},
            ]},
            {"name": "os_a", "label": "Channel A Oversampling", "type": "checkbox", "value": self._config.get("os_a", False)},
            {"name": "offset_a", "label": "Channel A Offset", "type": "text", "value": self._config.get("offset_a", "0")},
            {"name": "cal_a", "label": "Channel A Calibration", "type": "checkbox", "value": self._config.get("cal_a", False)},
            {"name": "adc1_a", "label": "Ch A ADC Point 1", "type": "number", "value": self._config.get("adc1_a", 0)},
            {"name": "out1_a", "label": "Ch A Out Point 1", "type": "text", "value": self._config.get("out1_a", "0")},
            {"name": "adc2_a", "label": "Ch A ADC Point 2", "type": "number", "value": self._config.get("adc2_a", 0)},
            {"name": "out2_a", "label": "Ch A Out Point 2", "type": "text", "value": self._config.get("out2_a", "0")},
            {"name": "mode_b", "label": "Channel B Mode", "type": "select", "value": self._config.get("mode_b", 0), "options": [
                {"value": 0, "label": "Off"},
                {"value": 1, "label": "Gain 32"},
            ]},
            {"name": "os_b", "label": "Channel B Oversampling", "type": "checkbox", "value": self._config.get("os_b", False)},
            {"name": "offset_b", "label": "Channel B Offset", "type": "text", "value": self._config.get("offset_b", "0")},
            {"name": "cal_b", "label": "Channel B Calibration", "type": "checkbox", "value": self._config.get("cal_b", False)},
            {"name": "adc1_b", "label": "Ch B ADC Point 1", "type": "number", "value": self._config.get("adc1_b", 0)},
            {"name": "out1_b", "label": "Ch B Out Point 1", "type": "text", "value": self._config.get("out1_b", "0")},
            {"name": "adc2_b", "label": "Ch B ADC Point 2", "type": "number", "value": self._config.get("adc2_b", 0)},
            {"name": "out2_b", "label": "Ch B Out Point 2", "type": "text", "value": self._config.get("out2_b", "0")},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "GPIO SCK", "number": 1},
            {"label": "GPIO DT", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_WEIGHT, SENSOR_V_TYPE_WEIGHT]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"WeightChanA": 0.0, "WeightChanB": 0.0}
