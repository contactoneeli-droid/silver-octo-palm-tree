"""SQLite storage for conversations, requests (leads), handoffs and appointments.

One database file per client, under ``CHATBOT_DATA_DIR`` (default ``data/``).
Only plain text turns are stored; tool calls stay inside a single request.
Appointment times are stored as UTC ISO strings so they sort correctly
across daylight-saving changes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CHATBOT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

ACTIVE_STATUSES = ("confirmed", "pending")


@dataclass(frozen=True)
class Session:
    """Who we are talking to: the channel plus the channel's chat id."""

    channel: str  # "web" | "telegram" | "whatsapp"
    chat_id: str
    user_name: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, slug: str, data_dir: Path = DATA_DIR):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / f"{slug}.sqlite3"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(channel, chat_id, id);
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    request TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    service TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    reminded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS appointments_start ON appointments(start_at);
                """
            )

    # Conversations ---------------------------------------------------------------

    def add_message(self, session: Session, role: str, content: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO messages(channel, chat_id, role, content, created_at) VALUES (?,?,?,?,?)",
                (session.channel, session.chat_id, role, content, _now()),
            )

    def history(self, session: Session, limit: int) -> list[dict[str, str]]:
        """Last ``limit`` turns, oldest first, as Claude ``messages`` entries."""
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content FROM messages WHERE channel=? AND chat_id=? ORDER BY id DESC LIMIT ?",
                (session.channel, session.chat_id, limit),
            ).fetchall()
        turns = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        # The API requires the first message to come from the user.
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        return turns

    def clear(self, session: Session) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM messages WHERE channel=? AND chat_id=?", (session.channel, session.chat_id))

    # Leads and handoffs ----------------------------------------------------------

    def add_lead(self, session: Session, name: str, contact: str, request: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO leads(channel, chat_id, name, contact, request, created_at) VALUES (?,?,?,?,?,?)",
                (session.channel, session.chat_id, name, contact, request, _now()),
            )
            return int(cur.lastrowid)

    def add_handoff(self, session: Session, reason: str, summary: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO handoffs(channel, chat_id, reason, summary, created_at) VALUES (?,?,?,?,?)",
                (session.channel, session.chat_id, reason, summary, _now()),
            )
            return int(cur.lastrowid)

    def leads(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # Appointments ----------------------------------------------------------------

    def add_appointment(
        self,
        session: Session,
        name: str,
        contact: str,
        service: str,
        start: datetime,
        end: datetime,
        status: str,
        notes: str = "",
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO appointments(channel, chat_id, name, contact, service, start_at, end_at, status, notes, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session.channel, session.chat_id, name, contact, service, to_utc_iso(start), to_utc_iso(end), status, notes, _now()),
            )
            return int(cur.lastrowid)

    def appointments_between(self, start: datetime, end: datetime, active_only: bool = True) -> list[dict]:
        """Active appointments overlapping [start, end), ordered by start."""
        sql = "SELECT * FROM appointments WHERE start_at < ? AND end_at > ?"
        params: list = [to_utc_iso(end), to_utc_iso(start)]
        if active_only:
            sql += f" AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
            params += list(ACTIVE_STATUSES)
        sql += " ORDER BY start_at, id"
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_appointment(self, appointment_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        return dict(row) if row else None

    def set_appointment_status(self, appointment_id: int, status: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id))

    def due_reminders(self, start: datetime, end: datetime) -> list[dict]:
        """Confirmed, not yet reminded appointments starting in [start, end)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM appointments WHERE status='confirmed' AND reminded=0 AND start_at >= ? AND start_at < ?"
                " ORDER BY start_at",
                (to_utc_iso(start), to_utc_iso(end)),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reminded(self, appointment_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE appointments SET reminded=1 WHERE id=?", (appointment_id,))

    def close(self) -> None:
        self._db.close()
