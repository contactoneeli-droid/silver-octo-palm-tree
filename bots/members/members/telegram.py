"""Minimal Telegram Bot API client (httpx), with inline keyboards and group admin calls."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


def inline_keyboard(rows: list[list[dict]]) -> dict:
    """rows of buttons: {"text": ..., "callback_data": ...} or {"text": ..., "url": ...}"""
    return {"inline_keyboard": rows}


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

    def send_message(self, chat_id: int | str, text: str, reply_markup: dict | None = None) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup, disable_web_page_preview=True)

    def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)
        except RuntimeError as e:  # stale queries are harmless
            log.debug("answerCallbackQuery: %s", e)

    def create_invite_link(self, chat_id: int, name: str, expire_at: int) -> str:
        """One-person, time-limited invite link to the paid group."""
        result = self.call("createChatInviteLink", chat_id=chat_id, name=name[:32], member_limit=1, expire_date=expire_at)
        return result["invite_link"]

    def remove_from_group(self, chat_id: int, user_id: int) -> None:
        """Kick without a permanent ban, so the member can come back after paying."""
        self.call("banChatMember", chat_id=chat_id, user_id=user_id)
        self.call("unbanChatMember", chat_id=chat_id, user_id=user_id, only_if_banned=True)

    def get_me(self) -> dict:
        return self.call("getMe")

    def poll(self, handler, allowed_updates=("message", "callback_query")) -> None:
        offset = None
        while True:
            try:
                updates = self.call("getUpdates", offset=offset, timeout=30, allowed_updates=list(allowed_updates))
            except (httpx.HTTPError, RuntimeError) as e:
                log.error("getUpdates a eșuat: %s; reîncerc în 5s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handler(update)
                except Exception:  # one bad update must not stop the bot
                    log.exception("Eroare la update %s", update.get("update_id"))
