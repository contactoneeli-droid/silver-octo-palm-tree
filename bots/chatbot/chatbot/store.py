"""SQLite storage for conversations, requests (leads) and handoffs.

One database file per client, under ``CHATBOT_DATA_DIR`` (default ``data/``).
Only plain text turns are stored; tool calls stay inside a single request.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("CHATBOT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


@dataclass(frozen=True)
class Session:
    """Who we are talking to: the channel plus the channel's chat id."""

    channel: str  # "web" | "telegram" | "whatsapp"
    chat_id: str
    user_name: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
                """
            )

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

    def close(self) -> None:
        self._db.close()
