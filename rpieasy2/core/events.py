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
        self._handlers: dict[str, list[AsyncEventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: AsyncEventHandler) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: AsyncEventHandler) -> None:
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    async def publish(self, event: Event) -> list[bool | None]:
        results: list[bool | None] = []
        for handler in self._handlers.get(event.type, []):
            try:
                result = await handler(event)
                results.append(result)
            except Exception as e:
                logger.error("EventBus %s handler error: %s", event.type, e)
                results.append(False)
        return results

    def clear(self) -> None:
        self._handlers.clear()


_EVENT_BUS = EventBus()


def get_event_bus() -> EventBus:
    return _EVENT_BUS
