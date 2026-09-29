"""Periodic checks: fetch each search, find listings not seen before, send them, handle errors and expiry."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ClientConfig
from .fetcher import FetchError, Fetcher
from .sources import Listing, ParseError, newest_first, parse
from .store import Store, iso, utc_now
from .store import parse as parse_dt
from .telegram import TelegramApi

log = logging.getLogger(__name__)

FAILURES_BEFORE_ALERT = 3
SEEN_RETENTION_DAYS = 120


def who(u: dict) -> str:
    return f"@{u['username']}" if u.get("username") else (u.get("name") or "")


class Watcher:
    def __init__(self, cfg: ClientConfig, store: Store, fetcher: Fetcher, api: TelegramApi):
        self.cfg = cfg
        self.store = store
        self.fetcher = fetcher
        self.api = api
        self.tz = ZoneInfo(cfg.timezone)

    # One search ----------------------------------------------------------------------

    def check(self, search: dict, now: datetime | None = None) -> tuple[list[Listing], bool]:
        """Returns (listings worth showing, first_run). The first run remembers everything on the page."""
        html = self.fetcher.get(newest_first(search["url"], search["source"]))
        listings = parse(search["source"], html)
        if not listings:
            raise ParseError("nu am găsit anunțuri în pagină (structura s-a schimbat sau pagina e blocată)")
        now = now or utc_now()
        seen = self.store.seen_keys(search["id"])
        if not seen:
            self.store.mark_seen(search["id"], [l.key for l in listings], now)
            return listings[: self.cfg.initial_results], True
        new = [l for l in listings if l.key not in seen]
        self.store.mark_seen(search["id"], [l.key for l in new], now)
        return new[: self.cfg.max_per_check], False

    def check_and_record(self, search: dict, now: datetime | None = None) -> tuple[list[Listing], bool]:
        """``check`` plus bookkeeping; records the failure and re-raises on error."""
        now = now or utc_now()
        try:
            result = self.check(search, now)
        except (FetchError, ParseError) as e:
            self.record_error(search, str(e), now)
            raise
        self.store.update_search(search["id"], last_checked_at=iso(now), last_error=None, error_count=0, error_notified=0)
        return result

    def record_error(self, search: dict, error: str, now: datetime) -> None:
        count = int(search.get("error_count") or 0) + 1
        fields = dict(last_checked_at=iso(now), last_error=error, error_count=count)
        if count >= FAILURES_BEFORE_ALERT and not search.get("error_notified") and self.cfg.owner_telegram_chat_id:
            self._send(
                self.cfg.owner_telegram_chat_id,
                f"⚠️ Căutarea «{search['name']}» (utilizator {search['user_id']}) a eșuat de {count} ori: {error}\nO verific mai rar până își revine.",
            )
            fields["error_notified"] = 1
        self.store.update_search(search["id"], **fields)
        log.warning("Căutarea %s a eșuat (a %s-a oară): %s", search["id"], count, error)

    def is_due(self, search: dict, now: datetime) -> bool:
        last = parse_dt(search.get("last_checked_at"))
        if not last:
            return True
        backoff = min(1 + int(search.get("error_count") or 0), 12)
        return now - last >= timedelta(minutes=self.cfg.poll_minutes * backoff)

    # All searches --------------------------------------------------------------------

    def run_pass(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utc_now()
        stats = {"checked": 0, "sent": 0, "errors": 0, "expired": self.expire_users(now)}
        for search in self.store.runnable_searches(now):
            if not self.is_due(search, now):
                continue
            stats["checked"] += 1
            try:
                listings, first = self.check_and_record(search, now)
            except (FetchError, ParseError):
                stats["errors"] += 1
                continue
            if listings:
                self.send_listings(search, listings, first)
                stats["sent"] += len(listings)
        self.store.prune_seen(now - timedelta(days=SEEN_RETENTION_DAYS))
        return stats

    def send_listings(self, search: dict, listings: list[Listing], first: bool) -> None:
        user_id, name = search["user_id"], search["name"]
        if first:
            self._send(user_id, f"Căutarea «{name}» e activă. Cele mai noi {len(listings)} anunțuri de acum:")
        if len(listings) <= 3:
            for listing in listings:
                self._send(user_id, f"🆕 {name}\n\n{listing.line()}", preview=True)
        else:
            body = "\n\n".join(f"{i}. {l.line()}" for i, l in enumerate(listings, 1))
            self._send(user_id, f"🆕 {len(listings)} anunțuri noi la «{name}»:\n\n{body}")

    def expire_users(self, now: datetime) -> int:
        expired = 0
        for u in self.store.users(status="active"):
            exp = parse_dt(u.get("expires_at"))
            if exp and exp <= now:
                self.store.update_user(u["user_id"], status="expired")
                self._send(u["user_id"], self.cfg.texts.expired)
                if self.cfg.owner_telegram_chat_id:
                    self._send(self.cfg.owner_telegram_chat_id, f"⏰ Abonamentul utilizatorului {u['user_id']} ({who(u)}) a expirat. Prelungește cu /aproba {u['user_id']} {self.cfg.subscription_days}")
                expired += 1
        return expired

    def _send(self, chat_id, text: str, preview: bool = False) -> None:
        try:
            self.api.send_message(chat_id, text, preview=preview)
        except Exception as e:  # blocked bot, network hiccup: never stop the pass
            log.warning("Nu pot trimite către %s: %s", chat_id, e)
