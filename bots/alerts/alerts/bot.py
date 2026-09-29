"""Telegram conversation: users manage their searches, the owner manages users."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from .config import ClientConfig
from .fetcher import FetchError
from .sources import ParseError, default_name, detect_source, source_name
from .store import Store, iso, utc_now
from .store import parse as parse_dt
from .telegram import TelegramApi
from .watcher import Watcher, who

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")

HELP = (
    "Comenzi:\n"
    "trimite un link de căutare (sau /adauga LINK nume) – pornești o alertă\n"
    "/lista – căutările tale\n"
    "/sterge N – ștergi căutarea N\n"
    "/pauza N – oprești temporar căutarea N; /pornire N o repornește\n"
    "/verifica N – verifici acum dacă a apărut ceva nou\n"
    "/status – până când ai acces"
)
OWNER_HELP = (
    "\n\nComenzi pentru proprietar:\n"
    "/utilizatori – toți utilizatorii și starea lor\n"
    "/aproba ID [ZILE] – activezi sau prelungești un utilizator (implicit {days} zile)\n"
    "/blocheaza ID – oprești un utilizator\n"
    "/stats – cifre\n"
    "/ruleaza – verifici acum toate căutările"
)


def full_name(user: dict) -> str:
    return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)


class AlertsBot:
    def __init__(self, cfg: ClientConfig, store: Store, api: TelegramApi, watcher: Watcher):
        self.cfg = cfg
        self.store = store
        self.api = api
        self.watcher = watcher

    # Dispatch ------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message or "text" not in message or message["chat"]["type"] != "private":
            return
        user = message["from"]
        user_id, text = user["id"], message["text"].strip()
        member = self.store.user(user_id)
        member = self.register(user) if member is None else self.store.upsert_user(user_id, user.get("username"), full_name(user))

        if self.is_owner(user_id) and text.startswith("/"):
            answer = self.owner_command(text)
            if answer is not None:
                self.send(user_id, answer)
                return

        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("/start", "/help", "/ajutor"):
            self.send(user_id, self.cfg.texts.welcome + "\n\n" + HELP + (OWNER_HELP.format(days=self.cfg.subscription_days) if self.is_owner(user_id) else ""))
            if member["status"] == "pending":
                self.send(user_id, self.cfg.texts.pending)
            return
        if cmd == "/status":
            self.send(user_id, self.status_text(member))
            return
        if not self.has_access(member):
            self.send(user_id, self.status_text(member))
            return

        if cmd == "/adauga" or URL_RE.search(text):
            answer = self.add_search(user_id, arg if cmd == "/adauga" else text)
        elif cmd == "/lista":
            answer = self.list_text(user_id)
        elif cmd in ("/sterge", "/pauza", "/pornire", "/verifica"):
            answer = self.search_command(user_id, cmd, arg)
        else:
            answer = "Nu am înțeles. " + HELP
        if answer:
            self.send(user_id, answer)

    def send(self, chat_id, text: str, preview: bool = False) -> None:
        try:
            self.api.send_message(chat_id, text, preview=preview)
        except Exception as e:
            log.warning("Nu pot trimite către %s: %s", chat_id, e)

    # Access --------------------------------------------------------------------------

    def is_owner(self, user_id: int) -> bool:
        return self.cfg.owner_telegram_chat_id is not None and str(user_id) == self.cfg.owner_telegram_chat_id

    def register(self, user: dict) -> dict:
        if self.cfg.access == "open" or self.is_owner(user["id"]):
            status, expires = "active", None
        elif self.cfg.trial_days > 0:
            status, expires = "active", utc_now() + timedelta(days=self.cfg.trial_days)
        else:
            status, expires = "pending", None
        member = self.store.upsert_user(user["id"], user.get("username"), full_name(user), status=status, expires_at=expires)
        if self.cfg.owner_telegram_chat_id and not self.is_owner(user["id"]):
            state = "în așteptare" if status == "pending" else f"probă {self.cfg.trial_days} zile" if expires else "activ"
            self.send(self.cfg.owner_telegram_chat_id, f"👤 Utilizator nou: {who(member)} (id {user['id']}), {state}.\nActivează sau prelungește cu /aproba {user['id']} {self.cfg.subscription_days}")
        return member

    def has_access(self, member: dict) -> bool:
        exp = parse_dt(member.get("expires_at"))
        return member.get("status") == "active" and (exp is None or exp > utc_now())

    def status_text(self, member: dict) -> str:
        status, exp = member.get("status"), parse_dt(member.get("expires_at"))
        if status == "active" and exp and exp <= utc_now():
            status = "expired"
        if status == "active":
            n = len(self.store.user_searches(member["user_id"]))
            until = f"până pe {self.fmt_date(exp)}" if exp else "fără limită de timp"
            return f"Ai acces {until}. Căutări active: {n} din {self.cfg.max_searches_per_user}."
        if status == "pending":
            return self.cfg.texts.pending
        if status == "expired":
            return self.cfg.texts.expired
        return "Contul tău e oprit."

    def fmt_date(self, dt: datetime) -> str:
        return dt.astimezone(self.watcher.tz).strftime("%d.%m.%Y")

    # Searches ------------------------------------------------------------------------

    def add_search(self, user_id: int, text: str) -> str:
        m = URL_RE.search(text)
        if not m:
            return "Trimite-mi linkul complet al căutării (începe cu https://)."
        url = m.group(0).rstrip(".,;)»\"'")
        source = detect_source(url)
        if not source or source not in self.cfg.sources:
            allowed = ", ".join(source_name(s) for s in self.cfg.sources)
            return f"Deocamdată știu să citesc doar {allowed}. Fă căutarea acolo și trimite-mi linkul."
        searches = self.store.user_searches(user_id)
        if any(s["url"] == url for s in searches):
            return "Ai deja această căutare. Vezi /lista."
        if len(searches) >= self.cfg.max_searches_per_user:
            return f"Poți avea cel mult {self.cfg.max_searches_per_user} căutări. Șterge una cu /sterge N."
        name = (text[m.end():].strip() or text[: m.start()].strip() or default_name(url, source))[:60]
        search = self.store.add_search(user_id, name, url, source)
        try:
            listings, first = self.watcher.check_and_record(search)
        except (FetchError, ParseError) as e:
            return f"Am salvat căutarea «{name}», dar nu am putut citi pagina acum ({e}). Reîncerc automat la fiecare {self.cfg.poll_minutes} minute."
        self.watcher.send_listings(search, listings, first)
        return f"De acum te anunț la fiecare anunț nou, la cel mult {self.cfg.poll_minutes} minute după ce apare."

    def list_text(self, user_id: int) -> str:
        searches = self.store.user_searches(user_id)
        if not searches:
            return "Nu ai nicio căutare. Trimite-mi un link de căutare de pe " + ", ".join(source_name(s) for s in self.cfg.sources) + "."
        lines = []
        for i, s in enumerate(searches, 1):
            state = "activă" if s["active"] else "pe pauză"
            if s["active"] and int(s.get("error_count") or 0) >= 3:
                state = "cu probleme la citire"
            checked = parse_dt(s.get("last_checked_at"))
            when = f", verificată la {checked.astimezone(self.watcher.tz).strftime('%H:%M')}" if checked else ""
            lines.append(f"{i}. {s['name']} ({source_name(s['source'])}), {state}{when}\n{s['url']}")
        return "Căutările tale:\n\n" + "\n\n".join(lines)

    def search_command(self, user_id: int, cmd: str, arg: str) -> str | None:
        searches = self.store.user_searches(user_id)
        if cmd in ("/pauza", "/pornire") and arg.lower() == "toate":
            for s in searches:
                self.store.update_search(s["id"], active=1 if cmd == "/pornire" else 0)
            return "Am repornit toate căutările." if cmd == "/pornire" else "Am pus toate căutările pe pauză."
        if not arg.isdigit() or not 1 <= int(arg) <= len(searches):
            return f"Scrie numărul căutării din /lista, de exemplu: {cmd} 1"
        search = searches[int(arg) - 1]
        name = search["name"]
        if cmd == "/sterge":
            self.store.delete_search(search["id"])
            return f"Am șters căutarea «{name}»."
        if cmd == "/pauza":
            self.store.update_search(search["id"], active=0)
            return f"Căutarea «{name}» e pe pauză. O repornești cu /pornire {arg}."
        if cmd == "/pornire":
            self.store.update_search(search["id"], active=1)
            return f"Căutarea «{name}» e din nou activă."
        try:
            listings, first = self.watcher.check_and_record(search)
        except (FetchError, ParseError) as e:
            return f"Nu am putut citi pagina: {e}"
        if listings:
            self.watcher.send_listings(search, listings, first)
            return None
        return f"Nimic nou la «{name}» de la ultima verificare."

    # Owner ---------------------------------------------------------------------------

    def owner_command(self, text: str) -> str | None:
        parts = text.split()
        cmd, args = parts[0].lower().lstrip("/"), parts[1:]
        if cmd == "utilizatori":
            users = self.store.users()
            if not users:
                return "Niciun utilizator încă."
            lines = []
            for u in users:
                exp = parse_dt(u.get("expires_at"))
                state = {"active": "activ", "pending": "în așteptare", "expired": "expirat", "blocked": "blocat"}.get(u["status"], u["status"])
                if u["status"] == "active" and exp:
                    state += f" până pe {self.fmt_date(exp)}"
                lines.append(f"{u['user_id']} {who(u)}: {state}, {len(self.store.user_searches(u['user_id']))} căutări")
            return "Utilizatori:\n" + "\n".join(lines[:100])
        if cmd == "aproba":
            if not args or not args[0].isdigit() or (len(args) > 1 and not args[1].isdigit()):
                return "Folosește: /aproba ID [ZILE]"
            user_id = int(args[0])
            days = int(args[1]) if len(args) > 1 else self.cfg.subscription_days
            member = self.store.user(user_id)
            if not member:
                return f"Nu cunosc utilizatorul {user_id}. Trebuie să scrie botului întâi."
            now = utc_now()
            current = parse_dt(member.get("expires_at"))
            base = current if member["status"] == "active" and current and current > now else now
            expires = base + timedelta(days=days)
            self.store.update_user(user_id, status="active", expires_at=iso(expires))
            if member["status"] != "active" or not self.has_access(member):
                self.send(user_id, self.cfg.texts.activated)
            self.send(user_id, f"Ai acces până pe {self.fmt_date(expires)}.")
            return f"Activat: {user_id} ({who(member)}) până pe {self.fmt_date(expires)}."
        if cmd == "blocheaza":
            if not args or not args[0].isdigit():
                return "Folosește: /blocheaza ID"
            user_id = int(args[0])
            if not self.store.user(user_id):
                return f"Nu cunosc utilizatorul {user_id}."
            self.store.update_user(user_id, status="blocked")
            self.send(user_id, "Accesul tău la alerte a fost oprit.")
            return f"Blocat: {user_id}."
        if cmd == "stats":
            users = self.store.users()
            by = {s: sum(1 for u in users if u["status"] == s) for s in ("active", "pending", "expired", "blocked")}
            c = self.store.counts()
            return (
                f"📊 {self.cfg.business_name}\n"
                f"Utilizatori: {by['active']} activi, {by['pending']} în așteptare, {by['expired']} expirați, {by['blocked']} blocați\n"
                f"Căutări active: {c['searches']} (cu probleme: {c['failing']})\n"
                f"Anunțuri ținute minte: {c['seen']}"
            )
        if cmd == "ruleaza":
            s = self.watcher.run_pass()
            return f"Verificate {s['checked']} căutări, trimise {s['sent']} anunțuri, {s['errors']} erori."
        return None
