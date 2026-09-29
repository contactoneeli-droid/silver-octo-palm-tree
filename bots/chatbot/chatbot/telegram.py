"""Telegram adapter: long polling with the Bot API, no extra dependencies."""

from __future__ import annotations

import logging
import time

import httpx

from .engine import BaseEngine
from .store import Session

log = logging.getLogger(__name__)


class TelegramApi:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.client = client or httpx.Client(timeout=40)

    def call(self, method: str, **params):
        r = self.client.post(f"{self.base}/{method}", json=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data}")
        return data["result"]

    def send_message(self, chat_id: int | str, text: str) -> None:
        # Telegram caps a message at 4096 characters.
        for i in range(0, len(text), 4000):
            self.call("sendMessage", chat_id=chat_id, text=text[i : i + 4000])

    def typing(self, chat_id: int | str) -> None:
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing")
        except Exception:  # cosmetic only
            pass


class TelegramBot:
    def __init__(self, api: TelegramApi, engine: BaseEngine):
        self.api = api
        self.engine = engine

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message or "text" not in message:
            return
        chat_id = message["chat"]["id"]
        sender = message.get("from", {})
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or None
        session = Session("telegram", str(chat_id), name)
        text = message["text"].strip()

        if text.startswith("/start"):
            self.engine.reset(session)
            self.api.send_message(chat_id, self.engine.greeting())
            return
        if text.startswith("/reset"):
            self.engine.reset(session)
            self.api.send_message(chat_id, "Am șters conversația. Cu ce te pot ajuta?")
            return

        self.api.typing(chat_id)
        reply = self.engine.reply(session, text)
        self.api.send_message(chat_id, reply.text)

    def run(self) -> None:
        offset = None
        me = self.api.call("getMe")
        log.info("Telegram: pornit ca @%s pentru %s", me.get("username"), self.engine.cfg.business_name)
        while True:
            try:
                updates = self.api.call("getUpdates", offset=offset, timeout=30, allowed_updates=["message"])
            except (httpx.HTTPError, RuntimeError) as e:
                log.error("getUpdates a eșuat: %s; reîncerc în 5s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.handle_update(update)
                except Exception:  # one bad update must not stop the bot
                    log.exception("Eroare la procesarea update-ului %s", update.get("update_id"))
