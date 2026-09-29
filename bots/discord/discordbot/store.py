"""SQLite per client: warnings, XP, tickets, paid roles and payments. Times are UTC ISO."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DISCORD_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, slug: str, data_dir: Path = DATA_DIR):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / f"{slug}.sqlite3"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, moderator_id INTEGER,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL, cleared INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS xp (
                    user_id INTEGER PRIMARY KEY, xp INTEGER NOT NULL DEFAULT 0,
                    messages INTEGER NOT NULL DEFAULT 0, last_award TEXT
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, channel_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS paid_roles (
                    user_id INTEGER NOT NULL, code TEXT NOT NULL, expires_at TEXT NOT NULL,
                    reminded_for TEXT, PRIMARY KEY (user_id, code)
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, code TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, provider TEXT NOT NULL,
                    reference TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                """
            )

    # Warnings ------------------------------------------------------------------------

    def add_warning(self, user_id: int, moderator_id: int | None, reason: str) -> int:
        """Adds a warning and returns how many active warnings the user has now."""
        with self._lock, self._db:
            self._db.execute("INSERT INTO warnings(user_id, moderator_id, reason, created_at) VALUES (?,?,?,?)", (user_id, moderator_id, reason, iso(utc_now())))
            return int(self._db.execute("SELECT COUNT(*) FROM warnings WHERE user_id=? AND cleared=0", (user_id,)).fetchone()[0])

    def warnings(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM warnings WHERE user_id=? AND cleared=0 ORDER BY id", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def clear_warnings(self, user_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE warnings SET cleared=1 WHERE user_id=?", (user_id,))

    # XP ------------------------------------------------------------------------------

    def xp(self, user_id: int) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM xp WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else {"user_id": user_id, "xp": 0, "messages": 0, "last_award": None}

    def add_xp(self, user_id: int, amount: int, now: datetime, count_message: bool = True) -> int:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO xp(user_id, xp, messages, last_award) VALUES (?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET xp = xp + excluded.xp, messages = messages + excluded.messages, last_award = excluded.last_award",
                (user_id, amount, 1 if count_message else 0, iso(now)),
            )
            return int(self._db.execute("SELECT xp FROM xp WHERE user_id=?", (user_id,)).fetchone()[0])

    def top(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM xp ORDER BY xp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def rank(self, user_id: int) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) + 1 FROM xp WHERE xp > (SELECT COALESCE((SELECT xp FROM xp WHERE user_id=?), 0))", (user_id,)).fetchone()
        return int(row[0])

    # Tickets -------------------------------------------------------------------------

    def open_ticket(self, user_id: int) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO tickets(user_id, created_at) VALUES (?,?)", (user_id, iso(utc_now())))
            return int(cur.lastrowid)

    def set_ticket_channel(self, ticket_id: int, channel_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tickets SET channel_id=? WHERE id=?", (channel_id, ticket_id))

    def open_tickets(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM tickets WHERE user_id=? AND status='open'", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def ticket_by_channel(self, channel_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (channel_id,)).fetchone()
        return dict(row) if row else None

    def close_ticket(self, ticket_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tickets SET status='closed', closed_at=? WHERE id=?", (iso(utc_now()), ticket_id))

    # Paid roles ----------------------------------------------------------------------

    def payment_exists(self, reference: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM payments WHERE reference=?", (reference,)).fetchone() is not None

    def add_payment(self, user_id: int, code: str, amount_cents: int, currency: str, provider: str, reference: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO payments(user_id, code, amount_cents, currency, provider, reference, created_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, code, amount_cents, currency, provider, reference, iso(utc_now())),
            )

    def paid_role(self, user_id: int, code: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM paid_roles WHERE user_id=? AND code=?", (user_id, code)).fetchone()
        return dict(row) if row else None

    def set_paid_role(self, user_id: int, code: str, expires_at: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO paid_roles(user_id, code, expires_at, reminded_for) VALUES (?,?,?,NULL)"
                " ON CONFLICT(user_id, code) DO UPDATE SET expires_at=excluded.expires_at, reminded_for=NULL",
                (user_id, code, iso(expires_at)),
            )

    def paid_roles(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM paid_roles ORDER BY expires_at").fetchall()
        return [dict(r) for r in rows]

    def user_paid_roles(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM paid_roles WHERE user_id=? ORDER BY expires_at", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def mark_reminded(self, user_id: int, code: str, expires_at: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE paid_roles SET reminded_for=? WHERE user_id=? AND code=?", (expires_at, user_id, code))

    def remove_paid_role(self, user_id: int, code: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM paid_roles WHERE user_id=? AND code=?", (user_id, code))

    def revenue_since(self, since: datetime) -> tuple[int, int]:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM payments WHERE provider='stripe' AND created_at >= ?", (iso(since),)).fetchone()
        return int(row[0]), int(row[1])

    def close(self) -> None:
        self._db.close()
