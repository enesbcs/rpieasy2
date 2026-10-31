from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("rpieasy2.events")

PLUGIN_INIT = "PLUGIN_INIT"
PLUGIN_EXIT = "PLUGIN_EXIT"
PLUGIN_READ = "PLUGIN_READ"
PLUGIN_WRITE = "PLUGIN_WRITE"
PLUGIN_ONCE_A_SECOND = "PLUGIN_ONCE_A_SECOND"
PLUGIN_TEN_PER_SECOND = "PLUGIN_TEN_PER_SECOND"
PLUGIN_FIFTY_PER_SECOND = "PLUGIN_FIFTY_PER_SECOND"
PLUGIN_DEVICE_ADD = "PLUGIN_DEVICE_ADD"
PLUGIN_GET_DEVICE_VALUE_NAMES = "PLUGIN_GET_DEVICE_VALUE_NAMES"
PLUGIN_GET_DEVICEGPIONAMES = "PLUGIN_GET_DEVICEGPIONAMES"
PLUGIN_WEBFORM_LOAD = "PLUGIN_WEBFORM_LOAD"
PLUGIN_WEBFORM_SAVE = "PLUGIN_WEBFORM_SAVE"
PLUGIN_WEBFORM_SHOW_CONFIG = "PLUGIN_WEBFORM_SHOW_CONFIG"
PLUGIN_WEBFORM_SHOW_VALUES = "PLUGIN_WEBFORM_SHOW_VALUES"
PLUGIN_SERIAL_IN = "PLUGIN_SERIAL_IN"
PLUGIN_UDP_IN = "PLUGIN_UDP_IN"
PLUGIN_GPIO_CHANGE = "PLUGIN_GPIO_CHANGE"
PLUGIN_GET_DEVICEVALUECOUNT = "PLUGIN_GET_DEVICEVALUECOUNT"
PLUGIN_GET_DEVICEVTYPE = "PLUGIN_GET_DEVICEVTYPE"
PLUGIN_GET_DISCOVERY_VTYPES = "PLUGIN_GET_DISCOVERY_VTYPES"
PLUGIN_SET_DEFAULTS = "PLUGIN_SET_DEFAULTS"
PLUGIN_TASKTIMER_IN = "PLUGIN_TASKTIMER_IN"
PLUGIN_CLOCK_IN = "PLUGIN_CLOCK_IN"
PLUGIN_GET_CONFIG_VALUE = "PLUGIN_GET_CONFIG_VALUE"
PLUGIN_WEBFORM_LOAD_OUTPUT_SELECTOR = "PLUGIN_WEBFORM_LOAD_OUTPUT_SELECTOR"

CONTROLLER_ADD = "CONTROLLER_ADD"
CONTROLLER_INIT = "CONTROLLER_INIT"
CONTROLLER_SEND = "CONTROLLER_SEND"
CONTROLLER_SUBSCRIBE = "CONTROLLER_SUBSCRIBE"
CONTROLLER_SEND_UDP = "CONTROLLER_SEND_UDP"
CONTROLLER_PROCESS_QUEUE = "CONTROLLER_PROCESS_QUEUE"
CONTROLLER_CONFIG_CHANGED = "CONTROLLER_CONFIG_CHANGED"
CPLUGIN_PROTOCOL_TEMPLATE = "CPLUGIN_PROTOCOL_TEMPLATE"
CPLUGIN_TASK_CHANGE_NOTIFICATION = "CPLUGIN_TASK_CHANGE_NOTIFICATION"

NOTIFIER_ADD = "NOTIFIER_ADD"
NOTIFIER_INIT = "NOTIFIER_INIT"
NOTIFIER_SEND = "NOTIFIER_SEND"

TASK_CONFIG_CHANGED = "TASK_CONFIG_CHANGED"


@dataclass
class Event:
    type: str
    task_index: int = -1
    controller_index: int = -1
    notifier_index: int = -1
    data: dict[str, Any] = field(default_factory=dict)
    parsed_json: dict[str, Any] = field(default_factory=dict)
    string1: str = ""
    string2: str = ""
    string3: str = ""
    string4: str = ""
    string5: str = ""
    custom1: str = ""
    custom2: str = ""
    custom3: str = ""
    protocol: int = 0
    index: int = 0
    sensor_type: int = -1


AsyncEventHandler = Callable[["Event"], Awaitable[bool | None]]


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[tuple[int, int, AsyncEventHandler]]] = defaultdict(list)
        self._seq = 0

    def subscribe(self, event_type: str, handler: AsyncEventHandler, priority: int = 0) -> None:
        # Handlers run in ascending priority order; equal priorities keep
        # subscription order. Plugins/data producers use the default (0), while
        # consumers that must observe the values produced during the event (e.g.
        # the webserver, rules engine and controllers listening on PLUGIN_READ)
        # subscribe with a higher priority so they always run last, even when a
        # plugin instance is (re)created at runtime and subscribes afterwards.
        self._seq += 1
        self._handlers[event_type].append((priority, self._seq, handler))
        self._handlers[event_type].sort(key=lambda item: (item[0], item[1]))

    def unsubscribe(self, event_type: str, handler: AsyncEventHandler) -> None:
        handlers = self._handlers.get(event_type)
        if not handlers:
            return
        remaining = [item for item in handlers if item[2] != handler]
        if remaining:
            self._handlers[event_type] = remaining
        else:
            self._handlers.pop(event_type, None)

    async def publish(self, event: Event) -> list[bool | None]:
        results: list[bool | None] = []
        for _, _, handler in list(self._handlers.get(event.type, [])):
            try:
                result = await handler(event)
                results.append(result)
            except Exception as e:
                logger.error("EventBus %s handler error: %s", event.type, e)
                results.append(False)
        return results

    def clear(self) -> None:
        self._handlers.clear()
        self._seq = 0


_EVENT_BUS = EventBus()


def get_event_bus() -> EventBus:
    return _EVENT_BUS
