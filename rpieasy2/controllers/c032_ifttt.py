from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event

logger = logging.getLogger("rpieasy2.controller.c032")

_IFTTT_MAX_VALUES = 3


class C032IFTTT(ControllerBase):
    CONTROLLER_ID = 32
    CONTROLLER_NAME = "IFTTT Webhooks"
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
        self._host: str = "maker.ifttt.com"
        self._port: int = 443
        self._api_key: str = ""
        self._timeout: int = 5
        self._use_tls: bool = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "maker.ifttt.com"))
        self._port = int(config.get("controllerport", config.get("port", 443)))
        self._api_key = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        self._use_tls = int(config.get("usetls", 0))
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._session or not self._api_key:
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))
        task_index = event.task_index if hasattr(event, "task_index") else -1
        task_name = config.get("TDN", config.get("name", f"Task{task_index + 1}"))

        body = {"key": self._api_key}
        for i in range(min(len(value_names), _IFTTT_MAX_VALUES)):
            vn = value_names[i]
            vv = str(named_values.get(vn, ""))
            body[f"value{i + 1}"] = vv

        data = urlencode(body).encode("utf-8")
        scheme = "https" if self._use_tls else "http"
        url = f"{scheme}://{self._host}:{self._port}/trigger/{task_name}/with/key/{self._api_key}"

        logger.debug("C032: POST %s body=%s", url, body)

        try:
            async with self._session.post(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                ssl=False if self._use_tls == 15 else None,
            ) as resp:
                if resp.status < 100 or resp.status >= 300:
                    logger.warning("C032: HTTP %d from %s", resp.status, url)
                    return False
        except Exception as e:
            logger.error("C032: Request failed: %s", e)
            return False

        return True
