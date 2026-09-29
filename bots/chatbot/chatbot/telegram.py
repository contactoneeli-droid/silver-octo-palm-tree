"""Telegram adapter: long polling with the Bot API, no extra dependencies.

Customers talk to the bot; the owner (``owner_telegram_chat_id``) gets a few
commands on top: /azi, /maine, /programari AAAA-LL-ZZ, /confirma N, /anuleaza N.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import httpx

from .booking import BookingError, fmt_date
from .engine import BaseEngine
from .store import Session

log = logging.getLogger(__name__)

OWNER_HELP = (
    "Comenzi pentru proprietar:\n"
    "/azi – programările de azi\n"
    "/maine – programările de mâine\n"
    "/programari AAAA-LL-ZZ – programările dintr-o zi\n"
    "/confirma N – confirmă programarea N\n"
    "/anuleaza N – anulează programarea N"
)


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
    def __init__(self, api: TelegramApi, engine: BaseEngine, owner_chat_id: str | None = None):
        self.api = api
        self.engine = engine
        self.owner_chat_id = str(owner_chat_id) if owner_chat_id else None

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message or "text" not in message:
            return
        chat_id = message["chat"]["id"]
        sender = message.get("from", {})
        name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p) or None
        session = Session("telegram", str(chat_id), name)
        text = message["text"].strip()

        if self.owner_chat_id and str(chat_id) == self.owner_chat_id and text.startswith("/"):
            handled = self.owner_command(text)
            if handled is not None:
                self.api.send_message(chat_id, handled)
                return

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

    def owner_command(self, text: str) -> str | None:
        """Returns the answer for an owner command, or None if it is not one."""
        cal = self.engine.calendar
        parts = text.split()
        cmd, args = parts[0].lower().lstrip("/"), parts[1:]
        if cmd == "help" or cmd == "ajutor":
            return OWNER_HELP if cal else "Programările prin chat nu sunt activate."
        if cmd not in {"azi", "maine", "programari", "confirma", "anuleaza"}:
            return None
        if cal is None:
            return "Programările prin chat nu sunt activate pentru această afacere."
        try:
            if cmd in {"azi", "maine", "programari"}:
                if cmd == "azi":
                    day = cal.now().date()
                elif cmd == "maine":
                    day = cal.now().date() + timedelta(days=1)
                else:
                    if not args:
                        return "Folosește: /programari AAAA-LL-ZZ"
                    day = cal.parse_date(args[0])
                appts = cal.day_schedule(day)
                if not appts:
                    return f"Nicio programare în {fmt_date(day)}."
                return f"Programări în {fmt_date(day)}:\n" + "\n".join(a.describe() for a in appts)
            if not args or not args[0].isdigit():
                return f"Folosește: /{cmd} N (numărul programării)"
            appointment_id = int(args[0])
            if cmd == "confirma":
                appt = cal.confirm(appointment_id)
                return f"Confirmată: {appt.describe()}"
            appt = cal.cancel(appointment_id, None, None)
            return f"Anulată: {appt.describe()}"
        except BookingError as e:
            return str(e)

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
