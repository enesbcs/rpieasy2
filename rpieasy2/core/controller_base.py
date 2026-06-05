from __future__ import annotations

import time
from typing import Any

from rpieasy2.core.events import Event, EventBus, get_event_bus
from rpieasy2.core.hw import HWManager


class ControllerQueue:
    def __init__(self, min_interval: float = 1.0, max_depth: int = 100, max_retries: int = 3):
        self.min_interval = min_interval
        self.max_depth = max_depth
        self.max_retries = max_retries
        self._entries: list[dict] = []
        self._last_process: float = 0.0

    def enqueue(self, entry: dict) -> bool:
        if len(self._entries) >= self.max_depth:
            return False
        entry.setdefault("retries", 0)
        entry["last_attempt"] = time.time()
        self._entries.append(entry)
        return True

    def get_pending(self) -> list[dict]:
        now = time.time()
        if now - self._last_process < self.min_interval:
            return []
        self._last_process = now
        pending = [e for e in self._entries if e.get("retries", 0) < self.max_retries]
        self._entries = [e for e in self._entries if e.get("retries", 0) >= self.max_retries]
        return pending

    def mark_sent(self, entry: dict) -> None:
        pass

    def mark_failed(self, entry: dict) -> None:
        entry["retries"] = entry.get("retries", 0) + 1
        if entry["retries"] < self.max_retries:
            self._entries.append(entry)

    @property
    def size(self) -> int:
        return len(self._entries)


class ControllerBase:
    CONTROLLER_ID: int = 0
    CONTROLLER_NAME: str = "Unknown"
    CONTROLLER_HAS_MQTT: bool = False

    usesMQTT: bool = False
    usesAccount: bool = False
    usesPassword: bool = False
    usesTemplate: bool = False
    usesID: bool = False
    Custom: bool = False
    usesHost: bool = True
    usesPort: bool = True
    usesQueue: bool = True
    usesCheckReply: bool = True
    usesTimeout: bool = True
    usesSampleSets: bool = False
    usesExtCreds: bool = False
    needsNetwork: bool = True
    allowsExpire: bool = True
    allowLocalSystemTime: bool = False
    mqttAutoDiscover: bool = False
    defaultPort: int = 80

    def __init__(self):
        self._event_bus: EventBus = get_event_bus()
        self._hw: HWManager | None = None
        self._send_queue: ControllerQueue | None = None
        self._controller_index: int = -1

    def set_hw_manager(self, hw: HWManager) -> None:
        self._hw = hw

    def _init_queue_from_config(self, config: dict[str, Any]) -> None:
        min_interval = float(config.get("minimumsendinterval", config.get("queue_min_interval", 1))) / 1000.0
        max_depth = int(config.get("maxqueuedepth", config.get("queue_max_depth", 10)))
        max_retries = int(config.get("maxretries", config.get("queue_max_retries", 10)))
        self._send_queue = ControllerQueue(min_interval, max_depth, max_retries)

    def enqueue_send(self, data: dict) -> bool:
        if self._send_queue is None:
            return False
        return self._send_queue.enqueue(data)

    def subscribe(self) -> None:
        self._event_bus.subscribe("CONTROLLER_INIT", self._handle_controller_init_wrapper)
        self._event_bus.subscribe("CONTROLLER_SEND", self._dispatch)
        self._event_bus.subscribe("CONTROLLER_SUBSCRIBE", self._dispatch)
        self._event_bus.subscribe("CONTROLLER_SEND_UDP", self._dispatch)
        self._event_bus.subscribe("CONTROLLER_PROCESS_QUEUE", self._dispatch)

    async def _handle_controller_init_wrapper(self, event: Event) -> bool | None:
        self._controller_index = event.controller_index
        return await self.on_controller_init(event)

    async def _dispatch(self, event: Event) -> bool | None:
        if self._controller_index >= 0:
            try:
                from rpieasy2.core.config import get_config
                cfg = get_config()
                ctrls = cfg.data.get("controllers", [])
                if self._controller_index < len(ctrls):
                    ctrl_cfg = ctrls[self._controller_index]
                    if not ctrl_cfg.get("controllerenabled", ctrl_cfg.get("enabled", True)):
                        return None
            except Exception:
                pass
        handler = getattr(self, f"on_{event.type.lower()}", None)
        if handler:
            return await handler(event)
        return None

    async def on_controller_init(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("controller_config", {})
        self._init_queue_from_config(config)
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        return True

    async def on_controller_subscribe(self, event: Event) -> bool | None:
        return True

    async def on_controller_send_udp(self, event: Event) -> bool | None:
        return True

    async def on_controller_process_queue(self, event: Event) -> bool | None:
        if self._send_queue is None:
            return True
        pending = self._send_queue.get_pending()
        if not pending:
            return True
        for entry in pending:
            send_event = Event(
                type="CONTROLLER_SEND",
                controller_index=event.controller_index,
                data=entry.get("data", {}),
            )
            result = await self.on_controller_send(send_event)
            if result:
                self._send_queue.mark_sent(entry)
            else:
                self._send_queue.mark_failed(entry)
        return True
