from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp

from rpieasy2.core.events import Event
from rpieasy2.core.notifier_base import NotifierBase
from rpieasy2.core.system_vars import resolve_special_chars

logger = logging.getLogger("rpieasy2.notifier.n006")


class N006Telegram(NotifierBase):
    NOTIFIER_ID = 6
    NOTIFIER_NAME = "Telegram"

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    async def on_notifier_init(self, event: Event) -> bool | None:
        self._config = event.data.get("notifier_config", {})
        token = self._config.get("password", "")
        chat_id = self._config.get("chatid", "")

        if chat_id or not token:
            return True

        server = self._config.get("server", "api.telegram.org")
        port = int(self._config.get("port", 443))
        url = f"https://{server}:{port}/bot{token}/getUpdates"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=False) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("result", [])
                        if results:
                            msg = results[0].get("message", {})
                            found = msg.get("from", {}).get("id")
                            if found:
                                self._config["chatid"] = str(found)
                                logger.info("Telegram: auto-discovered chat_id=%s", found)
        except Exception as e:
            logger.debug("Telegram: chat_id auto-discover failed: %s", e)

        return True

    async def on_notifier_send(self, event: Event) -> bool | None:
        config = self._config
        token = config.get("password", "")
        chat_id = config.get("chatid", "")
        server = config.get("server", "api.telegram.org")
        port = int(config.get("port", 443))

        if not token or not chat_id:
            logger.warning("Telegram: missing token or chat_id")
            return False

        message = event.data.get("message", "")
        body_template = config.get("body", "")
        text = resolve_special_chars(body_template or message)

        payload = urlencode({
            "chat_id": chat_id,
            "parse_mode": "HTML",
            "text": text,
        }).encode("utf-8")

        url = f"https://{server}:{port}/bot{token}/sendMessage"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False,
                ) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("Telegram: HTTP %d", resp.status)
                        return False
        except Exception as e:
            logger.error("Telegram: request failed: %s", e)
            return False

        return True
