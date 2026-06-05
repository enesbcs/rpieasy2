from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Any

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event

logger = logging.getLogger("rpieasy2.controller.c017")

ZABBIX_DEFAULT_PORT = 10051

ZBXD_HEADER = b"ZBXD\x01"


def _build_zabbix_payload(host: str, items: list[dict[str, Any]]) -> bytes:
    data = {
        "request": "sender data",
        "data": [
            {
                "host": host,
                "key": item["key"],
                "value": item["value"],
            }
            for item in items
        ],
    }
    json_data = json.dumps(data, separators=(",", ":")).encode()
    length = len(json_data)
    header = ZBXD_HEADER + struct.pack("<Q", length)
    return header + json_data


class C017Zabbix(ControllerBase):
    CONTROLLER_ID = 17
    CONTROLLER_NAME = "Zabbix"
    usesHost = True
    usesPort = True
    usesPassword = False
    usesID = False
    usesQueue = True
    usesTimeout = True
    usesTemplate = False
    usesMQTT = False
    usesAccount = False
    defaultPort = ZABBIX_DEFAULT_PORT

    def __init__(self):
        super().__init__()
        self._host: str = ""
        self._port: int = ZABBIX_DEFAULT_PORT
        self._zabbix_host: str = ""
        self._timeout: int = 5

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", ZABBIX_DEFAULT_PORT)))
        sys_cfg = get_config().data.get("system", {})
        self._zabbix_host = sys_cfg.get("name", "RPIEasy2")
        self._timeout = max(1, int(config.get("clienttimeout", 1000)) // 100)
        return True

    async def on_controller_send(self, event: Event) -> bool | None:
        config = event.data.get("task_config", {})
        values = event.data.get("values", {})

        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        if not value_names:
            return True

        items = []
        for vname in value_names:
            vval = named_values.get(vname, "")
            try:
                num_val = float(vval)
                items.append({"key": vname, "value": num_val})
            except (ValueError, TypeError):
                items.append({"key": vname, "value": vval})

        payload = _build_zabbix_payload(self._zabbix_host, items)

        try:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._timeout,
                )
            except Exception:
                logger.error(f"Zabbix: cannot connect to {self._host}:{self._port}")
                return False

            try:
                writer.write(payload)
                await writer.drain()
                response = await asyncio.wait_for(
                    reader.readexactly(len(ZBXD_HEADER) + 8),
                    timeout=self._timeout,
                )
                if response[:len(ZBXD_HEADER)] == ZBXD_HEADER:
                    data_len = struct.unpack("<Q", response[len(ZBXD_HEADER):])[0]
                    if data_len > 0:
                        json_resp = await asyncio.wait_for(
                            reader.readexactly(data_len),
                            timeout=self._timeout,
                        )
                        resp_data = json.loads(json_resp)
                        if resp_data.get("response") != "success":
                            logger.warning(f"Zabbix server error: {resp_data}")
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Zabbix send failed: {e}")
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.error(f"Zabbix send failed: {e}")
            return False
        return True
