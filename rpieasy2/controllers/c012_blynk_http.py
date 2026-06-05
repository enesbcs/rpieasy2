from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event

logger = logging.getLogger("rpieasy2.controller.c012")

BLYNK_DEFAULT_PORT = 80
BLYNK_DEFAULT_HOST = "blynk-cloud.com"


class C012BlynkHTTP(ControllerBase):
    CONTROLLER_ID = 12
    CONTROLLER_NAME = "Blynk HTTP"
    usesPassword = True
    usesHost = True
    usesPort = True
    usesID = True
    usesQueue = True
    usesTimeout = True
    usesMQTT = False
    usesAccount = False
    usesTemplate = False
    defaultPort = BLYNK_DEFAULT_PORT

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._host: str = BLYNK_DEFAULT_HOST
        self._port: int = BLYNK_DEFAULT_PORT
        self._auth_token: str = ""
        self._timeout: int = 5

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", BLYNK_DEFAULT_HOST))
        self._port = int(config.get("controllerport", config.get("port", BLYNK_DEFAULT_PORT)))
        self._auth_token = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        )
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._session or not self._auth_token:
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        pin = config.get("task_values", {}).get("idx", config.get("idx", "0"))

        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        pin_base = int(pin)
        for i, vname in enumerate(value_names):
            vval = named_values.get(vname, "")
            if not vval or vval == "0.0" or vval == "0":
                continue
            pin_id = pin_base + i
            url = f"http://{self._host}:{self._port}/{self._auth_token}/update/V{pin_id}?value={vval}"
            try:
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"Blynk HTTP error: {resp.status} for V{pin_id}")
                        return False
            except Exception as e:
                logger.error(f"Blynk HTTP send failed for V{pin_id}: {e}")
                return False
        return True
