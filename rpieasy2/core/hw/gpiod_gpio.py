from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

import gpiod

from rpieasy2.core.hw.base import GPIOManager

logger = logging.getLogger("rpieasy2.hw.gpiod")

EDGE_RISING = 0
EDGE_FALLING = 1
EDGE_BOTH = 2

try:
    from gpiod.line import Direction, Bias, Edge, Value
    _GPIO_NEW_API = True
except ImportError:
    Direction = gpiod.LineDirection
    Bias = gpiod.LineBias
    Edge = gpiod.EdgeDetection
    Value = type("Value", (), {"ACTIVE": 1, "INACTIVE": 0})
    _GPIO_NEW_API = False


def _value_to_gpiod(val: int):
    if _GPIO_NEW_API:
        return Value.ACTIVE if val else Value.INACTIVE
    return val


def _make_request(chip, config: dict, consumer: str):
    if _GPIO_NEW_API:
        return chip.request_lines(config=config, consumer=consumer)
    return chip.request_lines(config=gpiod.LineConfig(config), consumer=consumer)


def _make_settings(*, direction, bias=None, edge_detection=None, output_value=None):
    if _GPIO_NEW_API:
        kwargs = {"direction": direction}
        if bias is not None:
            kwargs["bias"] = bias
        if edge_detection is not None:
            kwargs["edge_detection"] = edge_detection
        if output_value is not None:
            kwargs["output_value"] = output_value
        return gpiod.LineSettings(**kwargs)
    s = gpiod.LineSettings()
    s.direction = direction
    if bias is not None:
        s.bias = bias
    if edge_detection is not None:
        s.edge_detection = edge_detection
    return s


def _parse_pin(pin: int) -> tuple[str, int]:
    if pin < 1000:
        return "/dev/gpiochip0", pin
    chip_id = pin // 1000
    offset = pin % 1000
    return f"/dev/gpiochip{chip_id}", offset


class GpiodGPIOManager(GPIOManager):
    def __init__(self):
        self._chips: dict[str, gpiod.Chip] = {}
        self._requests: dict[tuple[str, int], gpiod.LineRequest] = {}
        self._claimed_pins: set[int] = set()
        self._loop = asyncio.get_event_loop()
        self._watch_fds: dict[int, int] = {}
        self._pwm_threads: dict[int, _PwmThread] = {}
        self._pin_modes: dict[int, str] = {}

    def set_pin_mode(self, pin: int, mode: str) -> None:
        self._pin_modes[pin] = mode

    def get_pin_mode(self, pin: int) -> str:
        return self._pin_modes.get(pin, "none")

    def apply_pin_modes(self) -> None:
        for pin, mode in list(self._pin_modes.items()):
            try:
                if mode == "output":
                    self.claim_output(pin)
                elif mode == "input":
                    self.claim_input(pin, pull_up=False)
                elif mode == "input_pullup":
                    self.claim_input(pin, pull_up=True)
                elif mode == "input_pulldown":
                    self._get_request(pin, "input_pulldown")
                    self._claimed_pins.add(pin)
            except Exception as e:
                logger.warning("apply_pin_modes: pin %d mode %s failed: %s", pin, mode, e)

    def _get_chip(self, chip_path: str) -> gpiod.Chip:
        if chip_path not in self._chips:
            self._chips[chip_path] = gpiod.Chip(chip_path)
        return self._chips[chip_path]

    def _get_request(self, pin: int, direction: str = "output",
                     pull_up: bool = False) -> gpiod.LineRequest:
        saved_mode = self._pin_modes.get(pin, "none")
        effective_dir = direction
        effective_pull = pull_up
        if saved_mode != "none" and direction != "output":
            if saved_mode == "input_pullup":
                effective_dir = "input_pullup"
                effective_pull = True
            elif saved_mode == "input_pulldown":
                effective_dir = "input_pulldown"
                effective_pull = False
            else:
                effective_dir = "input"
                effective_pull = False
        key = (pin, effective_dir)
        if key in self._requests:
            return self._requests[key]

        self._release_pin(pin)

        chip_path, offset = _parse_pin(pin)
        chip = self._get_chip(chip_path)

        if effective_dir == "output":
            settings = _make_settings(direction=Direction.OUTPUT)
        else:
            bias = None
            if effective_pull:
                bias = Bias.PULL_UP
            elif effective_dir == "input_pulldown":
                bias = Bias.PULL_DOWN
            settings = _make_settings(direction=Direction.INPUT, bias=bias)

        req = _make_request(chip, {offset: settings}, "rpieasy2")
        self._requests[key] = req
        logger.debug("_get_request: pin=%d direction=%s saved_mode=%s effective_dir=%s", pin, direction, saved_mode, effective_dir)
        return req

    def _release_pin(self, pin: int) -> None:
        for key in list(self._requests.keys()):
            if key[0] == pin:
                del self._requests[key]

    def read(self, pin: int) -> int:
        try:
            chip_path, offset = _parse_pin(pin)
            if not _GPIO_NEW_API:
                chip = self._get_chip(chip_path)
                val = chip.get_values([offset])
                return int(val[0]) if val else 0
            saved_mode = self._pin_modes.get(pin, "none")
            if saved_mode in ("input_pullup", "input_pulldown", "input", "output"):
                req_key = (pin, saved_mode)
            else:
                req_key = (pin, "input")
            req = self._requests.get(req_key)
            if req is not None:
                val = req.get_value(offset)
                return val.value
            chip = self._get_chip(chip_path)
            settings = _make_settings(direction=Direction.INPUT)
            req = _make_request(chip, {offset: settings}, "rpieasy2_read")
            val = req.get_value(offset)
            return val.value
        except Exception as e:
            logger.warning("read failed on pin %d: %s", pin, e)
            return 0

    def write(self, pin: int, value: int) -> None:
        try:
            req = self._get_request(pin, "output")
            _, offset = _parse_pin(pin)
            req.set_value(offset, _value_to_gpiod(value))
            self._claimed_pins.add(pin)
        except Exception as e:
            logger.warning("gpiod write failed on pin %d: %s", pin, e)

    def claim_output(self, pin: int) -> None:
        self._get_request(pin, "output")
        self._claimed_pins.add(pin)

    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        direction = "input_pullup" if pull_up else "input"
        self._get_request(pin, direction)
        self._claimed_pins.add(pin)

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        states: dict[int, dict[str, str | int]] = {}
        for pin in list(self._claimed_pins):
            try:
                val = self.read(pin)
                states[pin] = {"value": val, "mode": "unknown"}
            except Exception:
                pass
        return states

    def watch(self, pin: int, edge: int,
              callback: Callable[[int, int, int], Any]) -> None:
        chip_path, offset = _parse_pin(pin)
        chip = self._get_chip(chip_path)

        edge_detection = None
        if edge == EDGE_RISING:
            edge_detection = Edge.RISING
        elif edge == EDGE_FALLING:
            edge_detection = Edge.FALLING
        else:
            edge_detection = Edge.BOTH
        settings = _make_settings(direction=Direction.INPUT, edge_detection=edge_detection)

        req = _make_request(chip, {offset: settings}, "rpieasy2_watch")
        self._requests[(pin, "watch")] = req
        self._claimed_pins.add(pin)

        def _on_fd_readable() -> None:
            try:
                for ev in req.read_edge_events():
                    callback(pin, ev.event_type,
                             int(ev.timestamp_ns / 1000))
            except Exception:
                pass

        fd = req.fd
        self._loop.add_reader(fd, _on_fd_readable)
        self._watch_fds[pin] = fd

    def unwatch(self, pin: int) -> None:
        fd = self._watch_fds.pop(pin, None)
        if fd is not None:
            try:
                self._loop.remove_reader(fd)
            except Exception:
                pass
        self._claimed_pins.discard(pin)
        self._release_pin(pin)

    def pwm(self, pin: int, frequency: float, duty_cycle: float) -> None:
        existing = self._pwm_threads.get(pin)
        if existing:
            existing.stop()

        try:
            self.claim_output(pin)
        except Exception:
            pass

        t = _PwmThread(self, pin, frequency, duty_cycle)
        self._pwm_threads[pin] = t
        t.start()

    def tone(self, pin: int, frequency: float,
             duration: float | None = None) -> None:
        self.pwm(pin, frequency, 50.0)
        if duration is not None:
            def _stop():
                self.tone_stop(pin)
            self._loop.call_later(duration, _stop)

    def tone_stop(self, pin: int) -> None:
        t = self._pwm_threads.pop(pin, None)
        if t:
            t.stop()

    def servo(self, pin: int, pulse_width: int) -> None:
        duty = (pulse_width / 20000.0) * 100.0
        duty = max(2.5, min(12.5, duty))
        self.pwm(pin, 50, duty)

    def set_mode(self, pin: int, mode: str) -> None:
        from rpieasy2.core.rpiconst import (
            PIN_MODE_INPUT, PIN_MODE_OUTPUT,
            PIN_MODE_INPUT_PULLUP, PIN_MODE_INPUT_PULLDOWN,
        )
        mode_low = mode.lower()
        self._release_pin(pin)
        self._claimed_pins.discard(pin)
        if mode_low in ("input", "in"):
            self.claim_input(pin, pull_up=False)
        elif mode_low in ("output", "out"):
            self.claim_output(pin)
        elif mode_low in ("input_pullup", "pullup", "pu"):
            self.claim_input(pin, pull_up=True)
        elif mode_low in ("input_pulldown", "pulldown", "pd"):
            self._get_request(pin, "input_pulldown")
            self._claimed_pins.add(pin)
        elif mode_low in ("pwm", "servo"):
            self.claim_output(pin)

    def close(self) -> None:
        for pin, fd in list(self._watch_fds.items()):
            try:
                self._loop.remove_reader(fd)
            except Exception:
                pass
        self._watch_fds.clear()
        for t in self._pwm_threads.values():
            t.stop()
        self._pwm_threads.clear()
        self._requests.clear()
        self._claimed_pins.clear()
        for chip in self._chips.values():
            try:
                chip.close()
            except Exception:
                pass
        self._chips.clear()


class _PwmThread(threading.Thread):
    def __init__(self, manager: GpiodGPIOManager, pin: int,
                 frequency: float, duty_cycle: float):
        super().__init__(daemon=True)
        self._manager = manager
        self._pin = pin
        self._frequency = frequency
        self._duty_cycle = max(0.0, min(100.0, duty_cycle))
        self._running = False

    def run(self) -> None:
        self._running = True
        period = 1.0 / self._frequency
        high = period * self._duty_cycle / 100.0
        low = period - high
        _, offset = _parse_pin(self._pin)

        while self._running:
            try:
                chip_path, _ = _parse_pin(self._pin)
                chip = self._manager._get_chip(chip_path)
                settings = _make_settings(direction=Direction.OUTPUT)
                req = _make_request(chip, {offset: settings}, "rpieasy2_pwm")
                break
            except Exception:
                time.sleep(0.1)

        if not self._running:
            return

        try:
            while self._running:
                if high > 0:
                    req.set_value(offset, _value_to_gpiod(1))
                    time.sleep(high)
                if low > 0 and self._running:
                    req.set_value(offset, _value_to_gpiod(0))
                    time.sleep(low)
        finally:
            try:
                req.set_value(offset, _value_to_gpiod(0))
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
