from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.rpiconst import BUILD, get_local_ip

logger = logging.getLogger("rpieasy2.controller.c009")

FHEM_DEFAULT_PORT = 8383


class C009FHEMHTTP(ControllerBase):
    CONTROLLER_ID = 9
    CONTROLLER_NAME = "FHEM HTTP"
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesHost = True
    usesPort = True
    usesID = False
    usesTemplate = False
    usesQueue = True
    usesTimeout = True
    usesMQTT = False
    defaultPort = FHEM_DEFAULT_PORT

    def __init__(self):
        super().__init__()
        self._session: aiohttp.ClientSession | None = None
        self._host: str = ""
        self._port: int = FHEM_DEFAULT_PORT
        self._username: str = ""
        self._password: str = ""
        self._timeout: int = 5
        self._use_tls: bool = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", FHEM_DEFAULT_PORT)))
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

        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))
        task_name = config.get("TDN", config.get("name", "rpieasy2_task"))
        sensor_type = config.get("TDSF", 1)

        sys_cfg = get_config().data.get("system", {})
        sys_name = sys_cfg.get("name", "RPIEasy2")
        sys_unit = sys_cfg.get("unit", 1)

        payload = {
            "module": "ESPEasy",
            "version": "1.04",
            "data": {
                "ESP": {
                    "name": sys_name,
                    "unit": int(sys_unit),
                    "version": 0,
                    "build": BUILD,
                    "build_notes": "",
                    "build_git": "",
                    "node_type_id": 0,
                    "sleep": 0,
                    "ip": get_local_ip(),
                },
                "SENSOR": {},
            },
        }

        sensor_obj = payload["data"]["SENSOR"]
        for x, vname in enumerate(value_names):
            sensor_obj[str(x)] = {
                "deviceName": task_name,
                "valueName": vname,
                "type": int(sensor_type),
                "value": str(named_values.get(vname, "")),
            }

        auth = None
        if self._username:
            auth = aiohttp.BasicAuth(self._username, self._password)

        scheme = "https" if self._use_tls else "http"
        try:
            async with self._session.post(
                f"{scheme}://{self._host}:{self._port}/ESPEasy",
                json=payload,
                auth=auth,
            ) as resp:
                if resp.status not in (200, 201, 202, 204):
                    logger.warning(f"FHEM HTTP POST error: {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"FHEM HTTP POST failed: {e}")
            return False
        return True
