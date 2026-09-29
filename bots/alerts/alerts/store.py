"""SQLite storage: users, searches, seen listings. One database per client, UTC ISO times."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("ALERTS_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


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
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT,
                    status TEXT NOT NULL,          -- pending | active | expired | blocked
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_error TEXT,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    error_notified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS seen (
                    search_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    PRIMARY KEY (search_id, key)
                );
                """
            )

    # Users -----------------------------------------------------------------------

    def upsert_user(self, user_id: int, username: str | None, name: str | None, status: str = "pending", expires_at: datetime | None = None) -> dict:
        """Creates the user with ``status`` if new; otherwise only refreshes username/name."""
        now = iso(utc_now())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO users(user_id, username, name, status, expires_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, name=excluded.name, updated_at=excluded.updated_at",
                (user_id, username, name, status, iso(expires_at) if expires_at else None, now, now),
            )
        return self.user(user_id)

    def user(self, user_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def update_user(self, user_id: int, **fields) -> None:
        fields["updated_at"] = iso(utc_now())
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE users SET {cols} WHERE user_id=?", (*fields.values(), user_id))

    def users(self, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM users", ()
        if status:
            sql, params = sql + " WHERE status=?", (status,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created_at", params).fetchall()
        return [dict(r) for r in rows]

    # Searches --------------------------------------------------------------------

    def add_search(self, user_id: int, name: str, url: str, source: str) -> dict:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO searches(user_id, name, url, source, created_at) VALUES (?,?,?,?,?)",
                (user_id, name, url, source, iso(utc_now())),
            )
            search_id = int(cur.lastrowid)
        return self.search(search_id)

    def search(self, search_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM searches WHERE id=?", (search_id,)).fetchone()
        return dict(row) if row else None

    def user_searches(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM searches WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def runnable_searches(self, now: datetime) -> list[dict]:
        """Active searches of users whose access is active and not expired."""
        with self._lock:
            rows = self._db.execute(
                "SELECT s.* FROM searches s JOIN users u ON u.user_id = s.user_id"
                " WHERE s.active=1 AND u.status='active' AND (u.expires_at IS NULL OR u.expires_at > ?) ORDER BY s.id",
                (iso(now),),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_search(self, search_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE searches SET {cols} WHERE id=?", (*fields.values(), search_id))

    def delete_search(self, search_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM seen WHERE search_id=?", (search_id,))
            self._db.execute("DELETE FROM searches WHERE id=?", (search_id,))

    def counts(self) -> dict[str, int]:
        with self._lock:
            searches = self._db.execute("SELECT COUNT(*) FROM searches WHERE active=1").fetchone()[0]
            failing = self._db.execute("SELECT COUNT(*) FROM searches WHERE active=1 AND error_count>=3").fetchone()[0]
            seen = self._db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        return {"searches": int(searches), "failing": int(failing), "seen": int(seen)}

    # Seen listings -----------------------------------------------------------------

    def seen_keys(self, search_id: int) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT key FROM seen WHERE search_id=?", (search_id,)).fetchall()
        return {r[0] for r in rows}

    def mark_seen(self, search_id: int, keys: list[str], now: datetime) -> None:
        if not keys:
            return
        with self._lock, self._db:
            self._db.executemany("INSERT OR IGNORE INTO seen(search_id, key, first_seen) VALUES (?,?,?)", [(search_id, k, iso(now)) for k in keys])

    def prune_seen(self, before: datetime) -> int:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM seen WHERE first_seen < ?", (iso(before),))
            return cur.rowcount

    def close(self) -> None:
        self._db.close()
