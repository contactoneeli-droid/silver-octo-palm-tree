"""SQLite persistence for paper/live runs: positions, trades, equity points and engine flags."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import date
from pathlib import Path

from .engine import Engine, Portfolio, Position, Trade

DATA_DIR = Path(os.environ.get("TRADING_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


class Store:
    def __init__(self, slug: str, mode: str, data_dir: Path = DATA_DIR):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / f"{slug}-{mode}.sqlite3"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, amount REAL, entry REAL, entry_fee REAL, stop REAL, take REAL, opened_ts INTEGER);
                CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, symbol TEXT, amount REAL, entry REAL, exit_price REAL, pnl REAL, pnl_pct REAL, opened_ts INTEGER, closed_ts INTEGER, reason TEXT);
                CREATE TABLE IF NOT EXISTS equity (ts INTEGER PRIMARY KEY, equity REAL);
                """
            )

    def get(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO state(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def save(self, engine: Engine) -> None:
        p = engine.portfolio
        with self._lock, self._db:
            self._db.execute("DELETE FROM positions")
            self._db.executemany(
                "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                [(s, pos.amount, pos.entry, pos.entry_fee, pos.stop, pos.take, pos.opened_ts) for s, pos in p.positions.items()],
            )
            saved = self._db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            self._db.executemany(
                "INSERT INTO trades(symbol, amount, entry, exit_price, pnl, pnl_pct, opened_ts, closed_ts, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                [(t.symbol, t.amount, t.entry, t.exit, t.pnl, t.pnl_pct, t.opened_ts, t.closed_ts, t.reason) for t in p.trades[saved:]],
            )
            if p.equity_curve:
                self._db.execute("INSERT OR REPLACE INTO equity VALUES (?,?)", p.equity_curve[-1])
            state = {
                "cash": p.cash, "initial_cash": p.initial_cash, "prices": engine.prices, "paused": engine.paused,
                "day": engine.day.isoformat() if engine.day else None, "day_start_equity": engine.day_start_equity,
                "halted_day": engine.halted_day.isoformat() if engine.halted_day else None,
            }
            self._db.execute("INSERT INTO state(key, value) VALUES ('engine', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(state),))

    def load(self, engine: Engine) -> bool:
        """Restores a previous run into ``engine``. Returns False when there is nothing saved."""
        state = self.get("engine")
        if not state:
            return False
        p = engine.portfolio
        p.cash, p.initial_cash = state["cash"], state["initial_cash"]
        engine.prices = {k: float(v) for k, v in (state.get("prices") or {}).items()}
        engine.paused = bool(state.get("paused"))
        engine.day = date.fromisoformat(state["day"]) if state.get("day") else None
        engine.day_start_equity = state.get("day_start_equity")
        engine.halted_day = date.fromisoformat(state["halted_day"]) if state.get("halted_day") else None
        with self._lock:
            p.positions = {r["symbol"]: Position(r["symbol"], r["amount"], r["entry"], r["entry_fee"], r["stop"], r["take"], r["opened_ts"]) for r in self._db.execute("SELECT * FROM positions")}
            p.trades = [Trade(r["symbol"], r["amount"], r["entry"], r["exit_price"], r["pnl"], r["pnl_pct"], r["opened_ts"], r["closed_ts"], r["reason"]) for r in self._db.execute("SELECT * FROM trades ORDER BY id")]
            p.equity_curve = [(r["ts"], r["equity"]) for r in self._db.execute("SELECT * FROM equity ORDER BY ts")]
        return True

    def close(self) -> None:
        self._db.close()
