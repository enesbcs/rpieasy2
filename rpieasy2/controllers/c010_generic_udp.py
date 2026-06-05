from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c010")

UDP_DEFAULT_PORT = 514


class C010GenericUDP(ControllerBase):
    CONTROLLER_ID = 10
    CONTROLLER_NAME = "Generic UDP"
    usesHost = True
    usesPort = True
    usesTemplate = True
    usesID = False
    usesQueue = True
    usesTimeout = True
    usesMQTT = False
    usesAccount = False
    usesPassword = False
    defaultPort = UDP_DEFAULT_PORT

    def __init__(self):
        super().__init__()
        self._transport: asyncio.DatagramTransport | None = None
        self._host: str = ""
        self._port: int = UDP_DEFAULT_PORT

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", UDP_DEFAULT_PORT)))
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        ctrl_config = config.get("controller", {})

        url_template = ctrl_config.get("controllerpublish", ctrl_config.get("topic", "%sysname%_%tskname%_%valname%=%value%"))
        url_template = resolve_controller_template(url_template, task_index=event.task_index,
                                                    task_config=config, task_values=values,
                                                    controller_config=ctrl_config)

        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        loop = asyncio.get_running_loop()
        try:
            for vname in value_names:
                vval = named_values.get(vname, "")
                msg = url_template.replace("%valname%", vname).replace("%value%", str(vval))
                try:
                    transport, _ = await loop.create_datagram_endpoint(
                        lambda: asyncio.DatagramProtocol(),
                        remote_addr=(self._host, self._port),
                    )
                    transport.sendto(msg.encode())
                    transport.close()
                except Exception as e:
                    logger.error(f"Generic UDP send failed: {e}")
                    return False
        finally:
            pass
        return True
