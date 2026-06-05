from __future__ import annotations

import asyncio
import glob
import logging
import os
from typing import Any, Callable

import lgpio as lg

from rpieasy2.core.hw.base import GPIOManager
from rpieasy2.core.rpiconst import RPI_GPIO_COUNT

logger = logging.getLogger("rpieasy2.hw.lgpio")

EDGE_RISING = lg.RISING_EDGE
EDGE_FALLING = lg.FALLING_EDGE
EDGE_BOTH = lg.BOTH_EDGES

def read_boot_gpio_config() -> dict[int, str]:
    result: dict[int, str] = {}
    for path in ("/boot/firmware/config.txt", "/boot/config.txt", "/boot/efi/config.txt"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("gpio="):
                        parts = line[5:].split("=", 1)
                        if len(parts) == 2:
                            try:
                                result[int(parts[0])] = parts[1]
                            except ValueError:
                                pass
            break
        except FileNotFoundError:
            continue
    return result



_PWM_PIN_CHANNELS = {
    12: [0, 0],
    13: [1, 1],
    18: [0, 2],
    19: [1, 3],
}


def _try_sysfs_pwm(pin: int, frequency: float, duty_cycle: float) -> bool:
    channels = _PWM_PIN_CHANNELS.get(pin)
    if channels is None:
        return False
    period_ns = int(1_000_000_000.0 / frequency)
    duty_ns = int(period_ns * max(0.0, min(100.0, duty_cycle)) / 100.0)
    if duty_ns >= period_ns:
        duty_ns = period_ns - 1
    for chip_dir in sorted(glob.glob("/sys/class/pwm/pwmchip*"), reverse=True):
        for ch in channels:
            pwm_path = f"{chip_dir}/pwm{ch}"
            try:
                if not os.path.exists(pwm_path):
                    with open(f"{chip_dir}/export", "w") as f:
                        f.write(str(ch))
                with open(f"{pwm_path}/period", "w") as f:
                    f.write(str(period_ns))
                with open(f"{pwm_path}/duty_cycle", "w") as f:
                    f.write(str(duty_ns))
                with open(f"{pwm_path}/enable", "w") as f:
                    f.write("1")
                return True
            except (OSError, IOError):
                continue
    return False


class LgpioGPIOManager(GPIOManager):
    def __init__(self):
        self._chip = lg.gpiochip_open(0)
        self._loop = asyncio.get_event_loop()
        self._watch_tasks: dict[int, asyncio.Task] = {}
        self._claimed_pins: set[int] = set()

    def read(self, pin: int) -> int:
        return lg.gpio_read(self._chip, pin)

    def write(self, pin: int, value: int) -> None:
        if pin not in self._claimed_pins:
            try:
                lg.gpio_claim_output(self._chip, pin)
                self._claimed_pins.add(pin)
            except lg.error:
                pass
        lg.gpio_write(self._chip, pin, value)

    def claim_output(self, pin: int) -> None:
        lg.gpio_claim_output(self._chip, pin)
        self._claimed_pins.add(pin)

    def claim_input(self, pin: int, pull_up: bool = False) -> None:
        flags = lg.SET_PULL_UP if pull_up else lg.SET_PULL_DOWN
        lg.gpio_claim_input(self._chip, pin, flags)
        self._claimed_pins.add(pin)

    def get_pin_states(self) -> dict[int, dict[str, str | int]]:
        states: dict[int, dict[str, str | int]] = {}
        for pin in list(self._claimed_pins):
            try:
                val = lg.gpio_read(self._chip, pin)
                if val >= 0:
                    states[pin] = {"value": val, "mode": "unknown"}
            except lg.error:
                pass
        return states

    def watch(self, pin: int, edge: int, callback: Callable[[int, int, int], Any]) -> None:
        queue: asyncio.Queue[tuple[int, int, int]] = asyncio.Queue()

        def _handler(chip: int, gpio: int, level: int, timestamp: int) -> None:
            asyncio.run_coroutine_threadsafe(queue.put((gpio, level, timestamp)), self._loop)

        lg.gpio_claim_alert(self._chip, pin, edge, _handler)
        self._claimed_pins.add(pin)

        async def _task() -> None:
            while True:
                gpio, level, ts = await queue.get()
                try:
                    callback(gpio, level, ts)
                except Exception:
                    pass

        self._watch_tasks[pin] = asyncio.create_task(_task)

    def unwatch(self, pin: int) -> None:
        task = self._watch_tasks.pop(pin, None)
        if task:
            task.cancel()
        self._claimed_pins.discard(pin)
        lg.gpio_free(self._chip, pin)

    def pwm(self, pin: int, frequency: float, duty_cycle: float) -> None:
        if pin in HARDWARE_PWM_PINS and _try_sysfs_pwm(pin, frequency, duty_cycle):
            return
        try:
            lg.gpio_claim_output(self._chip, pin)
        except Exception:
            pass
        self._claimed_pins.add(pin)
        duty = max(0, min(1000000, int(duty_cycle * 10000)))
        lg.tx_pwm(self._chip, pin, frequency, duty)

    def tone(self, pin: int, frequency: float, duration: float | None = None) -> None:
        lg.tx_tone(self._chip, pin, frequency)
        if duration is not None:
            asyncio.get_event_loop().call_later(duration, lambda: self.tone_stop(pin))

    def tone_stop(self, pin: int) -> None:
        lg.tx_tone(self._chip, pin, 0)

    def servo(self, pin: int, pulse_width: int) -> None:
        lg.tx_servo(self._chip, pin, max(500, min(2500, pulse_width)))

    def read_boot_gpio_config(self) -> dict[int, str]:
        return read_boot_gpio_config()

    def set_mode(self, pin: int, mode: str) -> None:
        from rpieasy2.core.rpiconst import PIN_MODE_INPUT, PIN_MODE_OUTPUT, PIN_MODE_INPUT_PULLUP, PIN_MODE_INPUT_PULLDOWN
        mode_low = mode.lower()
        lg.gpio_free(self._chip, pin)
        self._claimed_pins.discard(pin)
        if mode_low in ("input", "in"):
            lg.gpio_claim_input(self._chip, pin)
            self._claimed_pins.add(pin)
        elif mode_low in ("output", "out"):
            lg.gpio_claim_output(self._chip, pin)
            self._claimed_pins.add(pin)
        elif mode_low in ("input_pullup", "pullup", "pu"):
            lg.gpio_claim_input(self._chip, pin, lg.SET_PULL_UP)
            self._claimed_pins.add(pin)
        elif mode_low in ("input_pulldown", "pulldown", "pd"):
            lg.gpio_claim_input(self._chip, pin, lg.SET_PULL_DOWN)
            self._claimed_pins.add(pin)
        elif mode_low == "pwm":
            lg.gpio_claim_output(self._chip, pin)
            self._claimed_pins.add(pin)
        elif mode_low == "servo":
            lg.gpio_claim_output(self._chip, pin)
            self._claimed_pins.add(pin)

    def close(self) -> None:
        for task in self._watch_tasks.values():
            task.cancel()
        self._watch_tasks.clear()
        self._claimed_pins.clear()
        lg.gpiochip_close(self._chip)
