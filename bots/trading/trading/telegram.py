"""Minimal Telegram Bot API client (httpx) with long polling."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)
CHUNK = 4000


class TelegramApi:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.client = client or httpx.Client(timeout=40)

    def call(self, method: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        r = self.client.post(f"{self.base}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description', data)}")
        return data["result"]

    def send_message(self, chat_id: int | str, text: str) -> None:
        for i in range(0, max(len(text), 1), CHUNK):
            self.call("sendMessage", chat_id=chat_id, text=text[i : i + CHUNK], disable_web_page_preview=True)

    def get_me(self) -> dict:
        return self.call("getMe")

    def poll(self, handler) -> None:
        offset = None
        while True:
            try:
                updates = self.call("getUpdates", offset=offset, timeout=30, allowed_updates=["message"])
            except (httpx.HTTPError, RuntimeError) as e:
                log.error("getUpdates a eșuat: %s; reîncerc în 5s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handler(update)
                except Exception:
                    log.exception("Eroare la update %s", update.get("update_id"))
