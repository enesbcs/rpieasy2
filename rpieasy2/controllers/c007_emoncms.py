from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.rpiconst import SENSOR_TYPE_STRING
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c007")

EMONCMS_DEFAULT_URL = "/emoncms/input/post.json"
EMONCMS_PORT = 80


class C007Emoncms(ControllerBase):
    CONTROLLER_ID = 7
    CONTROLLER_NAME = "Emoncms"
    usesAccount = False
    usesPassword = True
    usesHost = True
    usesPort = True
    usesTemplate = True
    usesID = True
    usesMQTT = False
    usesTimeout = True
    defaultPort = EMONCMS_PORT

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._api_key: str = ""

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._api_key = config.get("controllerpassword", config.get("password", ""))
        host = config.get("controllerip", config.get("host", "127.0.0.1"))
        port = int(config.get("controllerport", config.get("port", EMONCMS_PORT)))
        timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        use_tls = int(config.get("usetls", 0))
        scheme = "https" if use_tls else "http"
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
        ctrl_config = config.get("controller", {})
        unit = config.get("unit", 1)
        sensor_type = int(config.get("TDSF", 0))

        if sensor_type == SENSOR_TYPE_STRING:
            logger.error("Emoncms: SENSOR_TYPE_STRING not supported")
            return False

        url_path = ctrl_config.get("controllerpublish", ctrl_config.get("topic", EMONCMS_DEFAULT_URL))
        url_path = resolve_controller_template(url_path, task_index=event.task_index,
                                                task_config=config, task_values=values,
                                                controller_config=ctrl_config)
        named_values = values.get("named_values", values)
        idx = int(config.get("task_values", {}).get("idx", config.get("idx", "0")))

        json_fields = ",".join(
            f"field{idx + i}:{v}" for i, v in enumerate(named_values.values())
        )

        params = {
            "node": str(unit),
            "json": "{" + json_fields + "}",
            "apikey": self._api_key,
        }

        try:
            async with self._session.get(url_path, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"Emoncms error: {resp.status}")
                    return False
                return True
        except Exception as e:
            logger.error(f"Emoncms send failed: {e}")
            return False
