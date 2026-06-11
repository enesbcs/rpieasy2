from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_CUSTOM0, SENSOR_TYPE_SINGLE

logger = logging.getLogger("rpieasy2.plugin.p513")


class _StepperMotor:

    def __init__(self, gpio, pins: list[int], rpm: int):
        self._gpio = gpio
        self.P1 = pins[0]
        self.P2 = pins[1]
        self.P3 = pins[2]
        self.P4 = pins[3]
        self.deg_per_step = 5.625 / 64
        self.steps_per_rev = int(360 / self.deg_per_step)
        self.step_angle = 0.0
        self.step_angle2 = 0.0
        self.moving = False
        self.setspeed(rpm)
        self.stop()

    def setspeed(self, rpm: int) -> None:
        self._rpm = rpm
        self._T = (60.0 / self._rpm) / self.steps_per_rev if self._rpm > 0 else 0.01

    def stop(self) -> None:
        self._gpio.write(self.P1, 0)
        self._gpio.write(self.P2, 0)
        self._gpio.write(self.P3, 0)
        self._gpio.write(self.P4, 0)
        self.moving = False

    def move_to(self, angle: float) -> None:
        target_step_angle = 8 * (int(angle / self.deg_per_step) / 8)
        steps = target_step_angle - self.step_angle2
        steps = int(steps % self.steps_per_rev)
        if steps > self.steps_per_rev / 2:
            steps -= int(self.steps_per_rev)
            self._move_acw_sync(-steps // 8)
        else:
            self._move_cw_sync(steps // 8)
        self.step_angle = angle
        self.step_angle2 = target_step_angle

    def move_cw(self, angle: float) -> None:
        target = int(angle / self.deg_per_step) // 8
        steps = int(target % self.steps_per_rev)
        self._move_cw_sync(steps)
        self.step_angle += angle
        self.step_angle2 += target

    def move_acw(self, angle: float) -> None:
        target = int(angle / self.deg_per_step) // 8
        steps = int(target % self.steps_per_rev)
        self._move_acw_sync(steps)
        self.step_angle -= angle
        self.step_angle2 -= target

    def _move_cw_sync(self, big_steps: int) -> None:
        self.stop()
        big_steps = int(big_steps)
        self.moving = True
        for _ in range(big_steps):
            self._gpio.write(self.P4, 1)
            self._sleep(self._T)
            self._gpio.write(self.P2, 0)
            self._sleep(self._T)
            self._gpio.write(self.P3, 1)
            self._sleep(self._T)
            self._gpio.write(self.P1, 0)
            self._sleep(self._T)
            self._gpio.write(self.P2, 1)
            self._sleep(self._T)
            self._gpio.write(self.P4, 0)
            self._sleep(self._T)
            self._gpio.write(self.P1, 1)
            self._sleep(self._T)
            self._gpio.write(self.P3, 0)
            self._sleep(self._T)
            if not self.moving:
                break
        self.moving = False

    def _move_acw_sync(self, big_steps: int) -> None:
        self.stop()
        big_steps = int(big_steps)
        self.moving = True
        for _ in range(big_steps):
            self._gpio.write(self.P3, 0)
            self._sleep(self._T)
            self._gpio.write(self.P1, 1)
            self._sleep(self._T)
            self._gpio.write(self.P4, 0)
            self._sleep(self._T)
            self._gpio.write(self.P2, 1)
            self._sleep(self._T)
            self._gpio.write(self.P1, 0)
            self._sleep(self._T)
            self._gpio.write(self.P3, 1)
            self._sleep(self._T)
            self._gpio.write(self.P2, 0)
            self._sleep(self._T)
            self._gpio.write(self.P4, 1)
            self._sleep(self._T)
            if not self.moving:
                break
        self.moving = False

    @staticmethod
    def _sleep(secs: float) -> None:
        import time
        time.sleep(secs)


class P513Stepper(PluginBase):
    PLUGIN_ID = 513
    PLUGIN_NAME = "Output - Stepper driver"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_CUSTOM0,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=1,
        send_data_option=False,
        timer_option=False,
        formula_option=False,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._motor: _StepperMotor | None = None
        self._angle: int = 0
        self._panning: bool = False
        self._pan_task: asyncio.Task | None = None
        self._pan_start: int = 20
        self._pan_stop: int = 340
        self._pan_delay: int = 3

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._angle = 0
        self._panning = False
        self._pan_task = None
        try:
            p1 = int(self._config.get("pin1", -1))
        except (ValueError, TypeError):
            p1 = -1
        try:
            p2 = int(self._config.get("pin2", -1))
        except (ValueError, TypeError):
            p2 = -1
        try:
            p3 = int(self._config.get("pin3", -1))
        except (ValueError, TypeError):
            p3 = -1
        try:
            p4 = int(self._config.get("pin4", -1))
        except (ValueError, TypeError):
            p4 = -1
        if p1 > 0 and p2 > 0 and p3 > 0 and p4 > 0 and self._hw:
            try:
                speed = int(self._config.get("speed", 10))
            except (ValueError, TypeError):
                speed = 10
            try:
                for pin in (p1, p2, p3, p4):
                    self._hw.gpio.claim_output(pin)
                self._motor = _StepperMotor(self._hw.gpio, [p1, p2, p3, p4], speed)
                try:
                    restored = int(self._config.get("angle", 0))
                except (ValueError, TypeError):
                    restored = 0
                if restored > 0:
                    self._motor.move_to(float(restored))
                self._angle = int(self._motor.step_angle)
                logger.info("Stepper initialized on pins %d,%d,%d,%d at %d RPM", p1, p2, p3, p4, speed)
            except Exception as e:
                logger.error("Stepper init failed: %s", e)
                self._motor = None
        else:
            logger.warning("Stepper: all 4 pins must be set")
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        self._panning = False
        if self._motor:
            try:
                self._motor.stop()
            except Exception:
                pass
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"Angle": str(self._angle)}
        event.data["named_values"] = {"Angle": str(self._angle)}
        event.data["value_names"] = ["Angle"]
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        sv = event.data.get("values", {})
        if sv:
            raw = next(iter(sv.values()), "-360")
            try:
                aval = int(float(raw))
            except (ValueError, TypeError):
                aval = -360
            if -360 < aval < 360:
                self._move_to(aval)
                event.data["values"] = {"Angle": str(self._angle)}
                event.data["named_values"] = {"Angle": str(self._angle)}
                event.data["value_names"] = ["Angle"]
                return True
        command = (event.string1 or "").strip()
        if command:
            self._handle_command(command)
            return True
        return False

    def _handle_command(self, cmd: str) -> None:
        parts = cmd.split(",")
        if not parts or parts[0].strip().lower() != "motor":
            return
        subcmd = ""
        try:
            subcmd = parts[2].strip().lower()
        except IndexError:
            return
        try:
            angle = int(float(parts[3].strip()))
        except (ValueError, IndexError):
            angle = -360
        try:
            speed = int(parts[4].strip())
        except (ValueError, IndexError):
            speed = 0
        if subcmd == "pos":
            if angle == -360:
                try:
                    astr = parts[3].strip().lower()
                except IndexError:
                    return
                dir_map = {"home": 0, "n": 0, "last": self._angle,
                           "e": 90, "s": 180, "w": 270,
                           "ne": 45, "se": 135, "nw": 315, "sw": 225}
                angle = dir_map.get(astr, -360)
            if angle > -360:
                if speed > 0 and self._motor:
                    self._motor.setspeed(speed)
                self._move_to(angle)
        elif subcmd == "left":
            if angle > -360 and self._motor:
                if speed > 0:
                    self._motor.setspeed(speed)
                self._motor.move_acw(float(angle))
                self._angle = int(self._motor.step_angle)
        elif subcmd == "right":
            if angle > -360 and self._motor:
                if speed > 0:
                    self._motor.setspeed(speed)
                self._motor.move_cw(float(angle))
                self._angle = int(self._motor.step_angle)
        elif subcmd == "off":
            self._panning = False
            if self._motor:
                self._motor.stop()
        elif subcmd == "panstop":
            self._panning = False
        elif subcmd == "setzero":
            self._panning = False
            if self._motor:
                self._motor.stop()
                self._motor.step_angle = 0
                self._motor.step_angle2 = 0
            self._angle = 0
        elif subcmd == "pan":
            if angle > -360:
                self._pan_start = angle
                self._pan_stop = speed
                try:
                    self._pan_delay = int(parts[5].strip())
                except (ValueError, IndexError):
                    pass
                try:
                    speed = int(parts[6].strip())
                except (ValueError, IndexError):
                    speed = 0
                if speed == 0:
                    speed = int(self._config.get("speed", 10))
                self._start_pan(speed)

    def _move_to(self, angle: int) -> None:
        if not self._motor:
            return
        self._motor.move_to(float(angle))
        self._angle = int(self._motor.step_angle)

    def _start_pan(self, speed: int) -> None:
        if self._panning:
            self._panning = False
        if self._pan_task and not self._pan_task.done():
            self._pan_task.cancel()
        self._pan_task = asyncio.create_task(self._background_pan(speed))

    async def _background_pan(self, speed: int) -> None:
        if not self._motor:
            return
        self._panning = True
        direction = False
        angle = abs(self._pan_start - self._pan_stop)
        self._motor.setspeed(speed)
        if self._pan_stop < self._pan_start:
            self._motor.move_to(float(self._pan_stop))
        else:
            self._motor.move_to(float(self._pan_start))
        self._angle = int(self._motor.step_angle)
        await asyncio.sleep(1)
        while self._panning:
            try:
                if direction:
                    self._motor.move_acw(float(angle))
                else:
                    self._motor.move_cw(float(angle))
            except Exception:
                self._panning = False
            self._motor.stop()
            self._angle = int(self._motor.step_angle)
            direction = not direction
            await asyncio.sleep(self._pan_delay)

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        try:
            speed_val = int(self._config.get("speed", 10))
        except (ValueError, TypeError):
            speed_val = 10
        event.data["form"] = [
            {"name": "pin1", "label": "GPIO Pin 1 (IN1)", "type": "number",
             "value": self._config.get("pin1", "")},
            {"name": "pin2", "label": "GPIO Pin 2 (IN2)", "type": "number",
             "value": self._config.get("pin2", "")},
            {"name": "pin3", "label": "GPIO Pin 3 (IN3)", "type": "number",
             "value": self._config.get("pin3", "")},
            {"name": "pin4", "label": "GPIO Pin 4 (IN4)", "type": "number",
             "value": self._config.get("pin4", "")},
            {"name": "speed", "label": "Speed (RPM, 1-25)", "type": "number",
             "value": speed_val, "min": 1, "max": 25},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["pin1"] = ""
        self._config["pin2"] = ""
        self._config["pin3"] = ""
        self._config["pin4"] = ""
        self._config["speed"] = 10
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Angle"]
        return True
