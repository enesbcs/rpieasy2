from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from rpieasy2.core.events import Event
from rpieasy2.core.notifier_base import NotifierBase
from rpieasy2.core.system_vars import resolve_special_chars

logger = logging.getLogger("rpieasy2.notifier.n008")


class N008MSTeams(NotifierBase):
    NOTIFIER_ID = 8
    NOTIFIER_NAME = "MS Teams"

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    async def on_notifier_init(self, event: Event) -> bool | None:
        self._config = event.data.get("notifier_config", {})
        return True

    async def on_notifier_send(self, event: Event) -> bool | None:
        config = self._config
        fullurl = config.get("fullurl", "")

        if not fullurl:
            logger.warning("MS Teams: missing webhook URL")
            return False

        message = event.data.get("message", "")
        body_template = config.get("body", "")
        text = resolve_special_chars(body_template or message)

        payload = json.dumps({"text": text}).encode("utf-8")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    fullurl,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status < 100 or resp.status >= 300:
                        logger.warning("MS Teams: HTTP %d", resp.status)
                        return False
        except Exception as e:
            logger.error("MS Teams: request failed: %s", e)
            return False

        return True
