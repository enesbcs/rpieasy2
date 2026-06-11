from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event

logger = logging.getLogger("rpieasy2.controller.c033")


class C033ThingsboardHTTP(ControllerBase):
    CONTROLLER_ID = 33
    CONTROLLER_NAME = "Thingsboard HTTP"
    usesAccount = False
    usesPassword = True
    usesExtCreds = False
    usesHost = True
    usesPort = True
    usesID = False
    usesQueue = True
    usesTimeout = True
    usesTemplate = False
    usesMQTT = False
    defaultPort = 443

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._host: str = "demo.thingsboard.io"
        self._port: int = 443
        self._access_token: str = ""
        self._timeout: int = 5
        self._use_tls: bool = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "demo.thingsboard.io"))
        self._port = int(config.get("controllerport", config.get("port", 443)))
        self._access_token = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        self._use_tls = int(config.get("usetls", 0))
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._session or not self._access_token:
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))
        task_index = event.task_index if hasattr(event, "task_index") else -1
        task_name = config.get("TDN", config.get("name", f"Task{task_index + 1}"))

        body: dict[str, str] = {}
        for vn in value_names:
            vv = str(named_values.get(vn, ""))
            body[f"{task_name}-{vn}"] = vv

        if not body:
            return False

        scheme = "https" if self._use_tls else "http"
        url = f"{scheme}://{self._host}:{self._port}/api/v1/{self._access_token}/telemetry"

        logger.debug("C033: POST %s body=%s", url, body)

        try:
            async with self._session.post(
                url,
                data=json.dumps(body),
                headers={"Content-Type": "application/json"},
                ssl=False if self._use_tls == 15 else None,
            ) as resp:
                if resp.status < 100 or resp.status >= 300:
                    logger.warning("C033: HTTP %d from %s", resp.status, url)
                    return False
        except Exception as e:
            logger.error("C033: Request failed: %s", e)
            return False

        return True
