"""Owner notifications: new requests and handoffs go to the owner's Telegram chat."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class Notifier:
    def notify(self, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class LogNotifier(Notifier):
    """Used when no owner chat is configured: the notification only goes to the log."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def notify(self, text: str) -> None:
        self.sent.append(text)
        log.info("Notificare (fără destinatar configurat):\n%s", text)


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.client = client or httpx.Client(timeout=15)

    def notify(self, text: str) -> None:
        try:
            r = self.client.post(self.url, json={"chat_id": self.chat_id, "text": text})
            r.raise_for_status()
        except httpx.HTTPError as e:
            # A failed notification must never break the customer's conversation.
            log.error("Notificarea către proprietar a eșuat: %s", e)
