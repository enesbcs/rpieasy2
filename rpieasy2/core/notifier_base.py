from __future__ import annotations

from typing import Any

from rpieasy2.core.events import Event, EventBus, get_event_bus
from rpieasy2.core.hw import HWManager


class NotifierBase:
    NOTIFIER_ID: int = 0
    NOTIFIER_NAME: str = "Unknown"

    def __init__(self):
        self._event_bus: EventBus = get_event_bus()
        self._hw: HWManager | None = None

    def set_hw_manager(self, hw: HWManager) -> None:
        self._hw = hw

    def subscribe(self) -> None:
        self._event_bus.subscribe("NOTIFIER_INIT", self._dispatch)
        self._event_bus.subscribe("NOTIFIER_SEND", self._dispatch)

    async def _dispatch(self, event: Event) -> bool | None:
        handler = getattr(self, f"on_{event.type.lower()}", None)
        if handler:
            return await handler(event)
        return None

    async def on_notifier_init(self, event: Event) -> bool | None:
        return True

    async def on_notifier_send(self, event: Event) -> bool | None:
        return True
