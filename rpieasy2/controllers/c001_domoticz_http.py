from __future__ import annotations

import logging
from typing import Any

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.rpiconst import DEFAULT_DOMOTICZ_PORT, SENSOR_TYPE_DIMMER, SENSOR_TYPE_SWITCH

logger = logging.getLogger("rpieasy2.controller.c001")


def _map_rssi(value: int) -> int:
    if value <= 0:
        return 0
    if value <= 1:
        return 1
    if value <= 2:
        return 2
    if value <= 4:
        return 3
    if value <= 8:
        return 4
    if value <= 13:
        return 5
    if value <= 20:
        return 6
    if value <= 30:
        return 7
    if value <= 42:
        return 8
    if value <= 55:
        return 9
    if value <= 75:
        return 10
    return 11


async def _domoticz_rssi() -> int:
    from rpieasy2.core.util import get_wifi_rssi
    rssi = get_wifi_rssi()
    if rssi is not None:
        return _map_rssi(abs(rssi) // 10)
    return 0


class C001DomoticzHTTP(ControllerBase):
    CONTROLLER_ID = 1
    CONTROLLER_NAME = "Domoticz HTTP"
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesID = True
    defaultPort = 8080
    usesMQTT = False

    async def on_controller_send(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("task_config", {})
        values: dict[str, Any] = event.data.get("values", {})
        ctrl_config = config.get("controller", {})
        host = ctrl_config.get("controllerip", ctrl_config.get("host", "127.0.0.1"))
        port = int(ctrl_config.get("controllerport", ctrl_config.get("port", DEFAULT_DOMOTICZ_PORT)))
        username = ctrl_config.get("controlleruser", ctrl_config.get("username", ""))
        password = ctrl_config.get("controllerpassword", ctrl_config.get("password", ""))
        idx = config.get("task_values", {}).get("idx", config.get("idx", "0"))
        use_tls = int(ctrl_config.get("usetls", 0))

        if not idx or idx == "0":
            logger.error("Domoticz HTTP: IDX cannot be zero!")
            return False

        sensor_type = int(config.get("TDSF", 0))
        named_values = values.get("named_values", values)
        first_val = next(iter(named_values.values()), "")

        rssi = await _domoticz_rssi()

        if sensor_type in (SENSOR_TYPE_SWITCH, SENSOR_TYPE_DIMMER):
            try:
                fval = float(first_val)
            except (ValueError, TypeError):
                fval = 0.0
            if fval == 0.0:
                switchcmd = "Off"
            elif sensor_type == SENSOR_TYPE_SWITCH:
                switchcmd = "On"
            else:
                switchcmd = f"Set%20Level&level={fval}"

            params = {
                "type": "command",
                "param": "switchlight",
                "idx": idx,
                "switchcmd": switchcmd,
                "rssi": rssi,
            }
        else:
            svalue = ";".join(str(v) for v in named_values.values())
            params = {
                "type": "command",
                "param": "udevice",
                "idx": idx,
                "nvalue": 0,
                "svalue": svalue,
                "rssi": rssi,
            }

        scheme = "https" if use_tls else "http"
        url = f"{scheme}://{host}:{port}/json.htm"
        try:
            auth = aiohttp.BasicAuth(username, password) if username else None
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, auth=auth, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Domoticz HTTP error: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"Domoticz HTTP send failed: {e}")
            return False
        return True
