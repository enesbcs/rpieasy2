from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.notifier_base import NotifierBase
from rpieasy2.core.rpiconst import DEFAULT_SMTP_PORT

logger = logging.getLogger("rpieasy2.notifier.n001")


class N001Email(NotifierBase):
    NOTIFIER_ID = 1
    NOTIFIER_NAME = "Email (SMTP)"

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}

    async def on_notifier_init(self, event: Event) -> bool | None:
        self._config = event.data.get("notifier_config", {})
        return True

    async def on_notifier_send(self, event: Event) -> bool | None:
        config = self._config
        smtp_server = config.get("server", "smtp.gmail.com")
        smtp_port = config.get("port", DEFAULT_SMTP_PORT)
        username = config.get("username", "")
        password = config.get("password", "")
        use_tls = config.get("tls", True)
        sender = config.get("sender", username)
        recipients = config.get("recipients", "")
        subject = event.string1 or "RPiEasy Notification"
        body = event.string2 or ""

        if not recipients:
            logger.warning("No email recipients configured")
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipients

        def _send() -> bool:
            try:
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    if use_tls:
                        server.starttls()
                    if username:
                        server.login(username, password)
                    server.send_message(msg)
                logger.info(f"Email sent to {recipients}")
                return True
            except Exception as e:
                logger.error(f"Email send failed: {e}")
                return False

        return await asyncio.to_thread(_send)
