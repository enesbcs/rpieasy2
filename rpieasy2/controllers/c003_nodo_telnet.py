from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event

logger = logging.getLogger("rpieasy2.controller.c003")

NODO_DEFAULT_PORT = 23


class C003NodoTelnet(ControllerBase):
    CONTROLLER_ID = 3
    CONTROLLER_NAME = "Nodo Telnet"
    usesAccount = False
    usesPassword = True
    usesID = True
    usesHost = True
    usesPort = True
    usesQueue = True
    usesMQTT = False
    usesTemplate = False
    defaultPort = NODO_DEFAULT_PORT
    needsNetwork = True

    def __init__(self):
        super().__init__()
        self._host: str = ""
        self._port: int = NODO_DEFAULT_PORT
        self._password: str = ""
        self._timeout: int = 5

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", NODO_DEFAULT_PORT)))
        self._password = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(1, int(config.get("clienttimeout", 5)) // 100) if int(config.get("clienttimeout", 100)) > 0 else 5
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        idx = config.get("task_values", {}).get("idx", config.get("idx", "0"))

        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        for i, vname in enumerate(value_names):
            val = named_values.get(vname, "")
            msg = f"variableset {idx},{val}\n"
            try:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(self._host, self._port),
                        timeout=self._timeout,
                    )
                except Exception:
                    logger.error(f"Nodo Telnet: cannot connect to {self._host}:{self._port}")
                    return False

                try:
                    writer.write(b" \n")
                    await writer.drain()
                    auth_data = await asyncio.wait_for(
                        reader.readuntil(b"password:"), timeout=self._timeout
                    )
                    if self._password and b"password" in auth_data.lower():
                        writer.write(f"{self._password}\n".encode())
                        await writer.drain()
                except asyncio.TimeoutError:
                    pass

                writer.write(msg.encode())
                await writer.drain()
                await asyncio.sleep(0.1)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Nodo Telnet send failed: {e}")
                return False
        return True
