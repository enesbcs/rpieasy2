from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c011")

_VARS_PER_TASK = 4


def _delete_not_needed_values(text: str, value_count: int) -> str:
    for i in range(1, _VARS_PER_TASK + 1):
        start_token = f"%{i}%"
        end_token = f"%/{i}%"
        if i < value_count:
            text = text.replace(start_token, "").replace(end_token, "")
        else:
            while True:
                s = text.find(start_token)
                if s == -1:
                    break
                e = text.find(end_token, s)
                if e == -1:
                    break
                text = text[:s] + text[e + len(end_token):]
    return text


def _replace_c011_tokens(text: str, named_values: dict, value_names: list[str],
                         task_config: dict, task_index: int, url_encode: bool) -> str:
    def _enc(v: str) -> str:
        return quote(v, safe='') if url_encode else v

    for i in range(_VARS_PER_TASK):
        vn = value_names[i] if i < len(value_names) else ""
        vv = str(named_values.get(vn, "")) if vn else ""
        text = text.replace(f"%vname{i+1}%", vn)
        text = text.replace(f"%val{i+1}%", _enc(vv))

    first_vname = value_names[0] if value_names else ""
    first_vval = str(named_values.get(first_vname, "")) if first_vname else ""
    text = text.replace("%value%", _enc(first_vval))

    return text


def _parse_headers(header_text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


class C011GenericHTTPAdvanced(ControllerBase):
    CONTROLLER_ID = 11
    CONTROLLER_NAME = "Generic HTTP Advanced"
    usesMQTT = False
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesHost = True
    usesPort = True
    usesTemplate = True
    usesID = False
    usesQueue = True
    usesTimeout = True
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
        self._httpmethod: str = "GET"
        self._httpuri: str = ""
        self._httpheader: str = ""
        self._httpbody: str = ""
        self._sendbinary: bool = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._host = config.get("controllerip", config.get("host", "127.0.0.1"))
        self._port = int(config.get("controllerport", config.get("port", 80)))
        self._username = config.get("controlleruser", config.get("username", ""))
        self._password = config.get("controllerpassword", config.get("password", ""))
        self._timeout = max(5, int(config.get("clienttimeout", 1000)) // 100)
        self._use_tls = int(config.get("usetls", 0))
        self._httpmethod = config.get("httpmethod", "GET").upper()
        self._httpuri = config.get("httpuri", "")
        self._httpheader = config.get("httpheader", "")
        self._httpbody = config.get("httpbody", "")
        self._sendbinary = config.get("sendbinary", False)
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
        value_count = len(value_names)
        task_index = event.task_index if hasattr(event, "task_index") else -1

        uri = self._httpuri
        header = self._httpheader
        body = self._httpbody

        uri = _delete_not_needed_values(uri, value_count)
        header = _delete_not_needed_values(header, value_count)
        body = _delete_not_needed_values(body, value_count)

        uri = _replace_c011_tokens(uri, named_values, value_names, config, task_index, url_encode=True)
        header = _replace_c011_tokens(header, named_values, value_names, config, task_index, url_encode=False)
        body = _replace_c011_tokens(body, named_values, value_names, config, task_index,
                                     url_encode=not self._sendbinary)

        uri = resolve_controller_template(uri, task_index=task_index, task_config=config,
                                           task_values=values)
        header = resolve_controller_template(header, task_index=task_index, task_config=config,
                                              task_values=values)
        body = resolve_controller_template(body, task_index=task_index, task_config=config,
                                            task_values=values)

        auth = None
        if self._username:
            auth = aiohttp.BasicAuth(self._username, self._password)

        scheme = "https" if self._use_tls else "http"
        url = f"{scheme}://{self._host}:{self._port}{uri}"
        headers = _parse_headers(header)
        ssl_ctx = False if self._use_tls == 15 else None

        try:
            method = self._httpmethod.upper()
            logger.debug("C011: %s %s headers=%s body=%s", method, url, headers, body)

            kw: dict[str, Any] = {"headers": headers, "ssl": ssl_ctx}
            if auth:
                kw["auth"] = auth

            if method == "GET":
                async with self._session.get(url, **kw) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("C011: HTTP %d for %s %s", resp.status, method, url)
                        return False
            elif method == "POST":
                kw["data"] = body
                async with self._session.post(url, **kw) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("C011: HTTP %d for %s %s", resp.status, method, url)
                        return False
            elif method == "PUT":
                kw["data"] = body
                async with self._session.put(url, **kw) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("C011: HTTP %d for %s %s", resp.status, method, url)
                        return False
            elif method == "PATCH":
                kw["data"] = body
                async with self._session.patch(url, **kw) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("C011: HTTP %d for %s %s", resp.status, method, url)
                        return False
            elif method == "HEAD":
                async with self._session.head(url, **kw) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("C011: HTTP %d for %s %s", resp.status, method, url)
                        return False
            else:
                logger.error("C011: Unsupported HTTP method: %s", method)
                return False
        except Exception as e:
            logger.error("C011: Request failed: %s", e)
            return False

        return True
