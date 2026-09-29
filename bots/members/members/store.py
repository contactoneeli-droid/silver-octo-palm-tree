"""SQLite storage: members, payments. One database per client, times in UTC ISO."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("MEMBERS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


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
                CREATE TABLE IF NOT EXISTS members (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT,
                    plan TEXT,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'none',   -- none | active | expired
                    trial_used INTEGER NOT NULL DEFAULT 0,
                    coupon TEXT,
                    awaiting TEXT,                          -- 'coupon' while we wait for a code
                    reminded_for TEXT,                      -- expires_at the last reminder was about
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    provider TEXT NOT NULL,                 -- stripe | manual | trial | owner
                    reference TEXT NOT NULL UNIQUE,
                    coupon TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    # Members ---------------------------------------------------------------------

    def upsert_member(self, user_id: int, username: str | None, name: str | None) -> dict:
        now = iso(utc_now())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO members(user_id, username, name, created_at, updated_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, name=excluded.name, updated_at=excluded.updated_at",
                (user_id, username, name, now, now),
            )
        return self.member(user_id)

    def member(self, user_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM members WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def update_member(self, user_id: int, **fields) -> None:
        fields["updated_at"] = iso(utc_now())
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE members SET {cols} WHERE user_id=?", (*fields.values(), user_id))

    def members(self, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM members", ()
        if status:
            sql, params = sql + " WHERE status=?", (status,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY expires_at", params).fetchall()
        return [dict(r) for r in rows]

    # Payments --------------------------------------------------------------------

    def payment_by_reference(self, reference: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM payments WHERE reference=?", (reference,)).fetchone()
        return dict(row) if row else None

    def add_payment(self, user_id: int, plan: str, amount_cents: int, currency: str, provider: str, reference: str, coupon: str | None) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO payments(user_id, plan, amount_cents, currency, provider, reference, coupon, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, plan, amount_cents, currency, provider, reference, coupon, iso(utc_now())),
            )
            return int(cur.lastrowid)

    def revenue_since(self, since: datetime) -> tuple[int, int]:
        """(payments count, cents) for real payments since ``since``."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM payments WHERE provider IN ('stripe','manual') AND created_at >= ?",
                (iso(since),),
            ).fetchone()
        return int(row[0]), int(row[1])

    def close(self) -> None:
        self._db.close()
