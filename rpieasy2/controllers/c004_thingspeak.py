from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.rpiconst import SENSOR_TYPE_STRING

logger = logging.getLogger("rpieasy2.controller.c004")

THINGSPEAK_HOST = "api.thingspeak.com"


class C004ThingSpeak(ControllerBase):
    CONTROLLER_ID = 4
    CONTROLLER_NAME = "ThingSpeak"
    usesAccount = True
    usesPassword = True
    usesHost = True
    usesPort = True
    usesID = True
    usesQueue = True
    usesMQTT = False
    usesTemplate = False
    usesTimeout = True
    defaultPort = 80

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._api_key: str = ""

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._api_key = config.get("controllerpassword", config.get("password", ""))
        host = config.get("controllerip", config.get("host", THINGSPEAK_HOST))
        port = int(config.get("controllerport", config.get("port", 80)))
        timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        use_tls = int(config.get("usetls", 0))
        scheme = "https" if use_tls else "http"
        if not config.get("controllerip"):
            host = THINGSPEAK_HOST
        self._session = aiohttp.ClientSession(
            base_url=f"{scheme}://{host}:{port}",
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._session or not self._api_key:
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        named_values = values.get("named_values", values)
        sensor_type = int(config.get("TDSF", 0))

        post_data = f"api_key={self._api_key}"

        if sensor_type == SENSOR_TYPE_STRING:
            first_val = next(iter(named_values.values()), "")
            post_data += f"&status={first_val}"
        else:
            for i, (vname, vval) in enumerate(named_values.items()):
                if i < 8:
                    post_data += f"&field{i + 1}={vval}"

        try:
            async with self._session.post("/update", data=post_data, headers={"Content-Type": "application/x-www-form-urlencoded"}) as resp:
                if resp.status != 200:
                    logger.warning(f"ThingSpeak error: {resp.status}")
                    return False
                return True
        except Exception as e:
            logger.error(f"ThingSpeak send failed: {e}")
            return False

    async def on_controller_send_udp(self, event: Event) -> bool | None:
        return False
