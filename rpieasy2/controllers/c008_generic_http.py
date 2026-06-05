from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c008")


class C008GenericHTTP(ControllerBase):
    CONTROLLER_ID = 8
    CONTROLLER_NAME = "Generic HTTP"
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesHost = True
    usesPort = True
    usesTemplate = True
    usesID = True
    usesQueue = True
    usesTimeout = True
    usesMQTT = False
    defaultPort = 80

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._host: str = ""
        self._port: int = 80
        self._username: str = ""
        self._password: str = ""
        self._timeout: int = 5
        self._use_tls: bool = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", 80)))
        self._username = config.get("controlleruser", config.get("username", ""))
        self._password = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        self._use_tls = int(config.get("usetls", 0))
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._session:
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        ctrl_config = config.get("controller", {})

        url_template = ctrl_config.get("controllerpublish", ctrl_config.get("topic", "/"))
        url_template = resolve_controller_template(url_template, task_index=event.task_index,
                                                    task_config=config, task_values=values,
                                                    controller_config=ctrl_config)
        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        auth = None
        if self._username:
            auth = aiohttp.BasicAuth(self._username, self._password)

        scheme = "https" if self._use_tls else "http"
        for i, vname in enumerate(value_names):
            vval = named_values.get(vname, "")
            url = url_template.replace("%valname%", vname).replace("%value%", str(vval))
            try:
                async with self._session.get(
                    f"{scheme}://{self._host}:{self._port}{url}",
                    auth=auth,
                ) as resp:
                    if resp.status not in (200, 201, 202, 204):
                        logger.warning(f"Generic HTTP error: {resp.status}")
                        return False
            except Exception as e:
                logger.error(f"Generic HTTP send failed: {e}")
                return False
        return True
