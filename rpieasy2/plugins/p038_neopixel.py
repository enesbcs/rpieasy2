from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUMMY, SENSOR_TYPE_NONE
from rpi_ws281x import Color, PixelStrip

logger = logging.getLogger("rpieasy2.plugin.p038")

PWM_PINS: dict[int, int] = {12: 0, 18: 0, 13: 1, 19: 1}


class P038NeoPixel(PluginBase):
    PLUGIN_ID = 38
    PLUGIN_NAME = "Output - NeoPixel Basic"
    PLUGIN_VALUES = 0
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUMMY,
        vtype=SENSOR_TYPE_NONE,
        value_count=0,
        send_data_option=False,
        timer_option=False,
        timer_optional=False,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._led: PixelStrip | None = None
        self._pixel_num: int = 0
        self._brightness: int = 100
        self._pin: int = -1

    @staticmethod
    def _safe_int(val: Any, default: int) -> int:
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    async def _clearall(self) -> None:
        if self._led is None:
            return
        try:
            for i in range(self._pixel_num):
                self._led.setPixelColor(i, Color(0, 0, 0))
            self._led.show()
        except Exception:
            pass

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._pin = self._safe_int(self._config.get("pin"), -1)
        self._pixel_num = max(self._safe_int(self._config.get("led_count"), 1), 1)
        self._brightness = max(0, min(255, self._safe_int(self._config.get("brightness"), 100)))
        wchannel = PWM_PINS.get(self._pin, -1)
        if wchannel < 0:
            logger.error(
                "NeoPixel: invalid pin %s (need PWM-capable: 12, 13, 18, 19)", self._pin
            )
            return False
        try:
            self._led = PixelStrip(
                num=self._pixel_num,
                pin=self._pin,
                channel=wchannel,
                brightness=self._brightness,
            )
            self._led.begin()
            await self._clearall()
            return True
        except Exception as e:
            logger.error("NeoPixel init failed: %s", e)
            self._led = None
            return False

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._clearall()
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("pin", -1)
        self._config.setdefault("led_count", 1)
        self._config.setdefault("brightness", 100)
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        if self._led is None:
            return False
        command = event.data.get("command", "").strip().lower()
        if not command:
            return False
        parts = command.split(",")
        cmd = parts[0]
        if cmd == "neopixel":
            return await self._neopixel(parts)
        if cmd == "neopixelall":
            return await self._neopixelall(parts)
        if cmd == "neopixelline":
            return await self._neopixelline(parts)
        return False

    async def _neopixel(self, parts: list[str]) -> bool:
        if len(parts) < 5:
            return False
        try:
            pin = int(parts[1].strip()) - 1
            r = int(parts[2].strip())
            g = int(parts[3].strip())
            b = int(parts[4].strip())
        except (ValueError, IndexError):
            return False
        if pin < 0 or pin >= self._pixel_num:
            return False
        br = int(parts[5].strip()) if len(parts) > 5 and parts[5].strip() else -1
        try:
            if br >= 0:
                self._led.setBrightness(br)
            else:
                self._led.setBrightness(self._brightness)
            self._led.setPixelColor(pin, Color(r, g, b))
            self._led.show()
            return True
        except Exception as e:
            logger.error("NeoPixel error: %s", e)
            return False

    async def _neopixelall(self, parts: list[str]) -> bool:
        if len(parts) < 4:
            return False
        try:
            r = int(parts[1].strip())
            g = int(parts[2].strip())
            b = int(parts[3].strip())
        except (ValueError, IndexError):
            return False
        br = int(parts[4].strip()) if len(parts) > 4 and parts[4].strip() else -1
        try:
            if br >= 0:
                self._led.setBrightness(br)
            else:
                self._led.setBrightness(self._brightness)
            col = Color(r, g, b)
            for i in range(self._pixel_num):
                self._led.setPixelColor(i, col)
            self._led.show()
            return True
        except Exception as e:
            logger.error("NeoPixel error: %s", e)
            return False

    async def _neopixelline(self, parts: list[str]) -> bool:
        if len(parts) < 6:
            return False
        try:
            pin1 = int(parts[1].strip()) - 1
            pin2 = int(parts[2].strip()) - 1
            r = int(parts[3].strip())
            g = int(parts[4].strip())
            b = int(parts[5].strip())
        except (ValueError, IndexError):
            return False
        if pin1 < 0:
            pin1 = 0
        if pin2 >= self._pixel_num:
            pin2 = self._pixel_num - 1
        if pin2 < pin1:
            pin2 = pin1
        br = int(parts[6].strip()) if len(parts) > 6 and parts[6].strip() else -1
        try:
            if br >= 0:
                self._led.setBrightness(br)
            else:
                self._led.setBrightness(self._brightness)
            col = Color(r, g, b)
            for i in range(pin1, pin2 + 1):
                self._led.setPixelColor(i, col)
            self._led.show()
            return True
        except Exception as e:
            logger.error("NeoPixel error: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        pin_opts = [{"value": p, "label": f"GPIO {p} (PWM{ch})"} for p, ch in sorted(PWM_PINS.items())]
        event.data["form"] = [
            {
                "name": "pin",
                "label": "GPIO Pin (PWM-capable)",
                "type": "select",
                "value": int(self._config.get("pin") or -1),
                "options": pin_opts,
            },
            {
                "name": "led_count",
                "label": "LED Count",
                "type": "number",
                "value": self._config.get("led_count", 1),
            },
            {
                "name": "brightness",
                "label": "Initial Brightness (0-255)",
                "type": "number",
                "value": self._config.get("brightness", 100),
            },
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        led_count = self._safe_int(self._config.get("led_count"), 1)
        if led_count < 1:
            led_count = 1
        self._config["led_count"] = led_count
        brightness = self._safe_int(self._config.get("brightness"), 100)
        self._config["brightness"] = max(0, min(255, brightness))
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {}
