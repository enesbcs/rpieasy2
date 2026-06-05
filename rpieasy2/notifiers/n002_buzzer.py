from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.notifier_base import NotifierBase


class N002Buzzer(NotifierBase):
    NOTIFIER_ID = 2
    NOTIFIER_NAME = "Buzzer"

    def __init__(self):
        super().__init__()
        self._pin: int = 0
        self._config: dict[str, Any] = {}

    async def on_notifier_init(self, event: Event) -> bool | None:
        self._config = event.data.get("notifier_config", {})
        pin_num = self._config.get("pin", 0)
        if pin_num > 0 and self._hw:
            try:
                self._hw.gpio.claim_output(pin_num)
                self._pin = pin_num
            except Exception as e:
                logger.error(f"Buzzer pin init failed: {e}")
                return False
        return True

    async def on_notifier_send(self, event: Event) -> bool | None:
        if not self._pin or not self._hw:
            logger.warning("Buzzer not configured")
            return False

        try:
            self._hw.gpio.write(self._pin, 1)
            await asyncio.sleep(0.5)
            self._hw.gpio.write(self._pin, 0)
            return True
        except Exception as e:
            logger.error(f"Buzzer error: {e}")
            return False
        return True
