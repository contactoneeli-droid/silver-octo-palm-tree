"""Paper and live runs: poll closed candles, feed the engine, persist, report on Telegram."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import ClientConfig
from .data import timeframe_ms
from .engine import Engine
from .store import Store

log = logging.getLogger(__name__)

HELP = (
    "Comenzi:\n"
    "/status – capital, poziții, rezultat azi\n"
    "/pozitii – pozițiile deschise cu stop și țintă\n"
    "/tranzactii – ultimele 10 tranzacții\n"
    "/raport – raportul zilnic acum\n"
    "/pauza – nu mai deschide poziții (cele deschise rămân cu stop/țintă)\n"
    "/pornire – reia\n"
    "/inchide SIMBOL – închide o poziție la piață (ex. /inchide BTC/USDT)"
)


class Runner:
    def __init__(self, cfg: ClientConfig, engine: Engine, market, store: Store | None, api=None, mode: str = "paper"):
        self.cfg, self.engine, self.market, self.store, self.api, self.mode = cfg, engine, market, store, api, mode
        self.tz = ZoneInfo(cfg.timezone)
        self.step = timeframe_ms(cfg.timeframe)
        self.last_ts: dict[str, int] = (store.get("last_ts") if store else None) or {}
        self.report_day: str | None = store.get("report_day") if store else None
        self.lookback = engine.strategy.lookback + 5

    # Market loop -------------------------------------------------------------------

    def poll(self, now_ms: int | None = None) -> int:
        """Processes every newly closed candle. Returns how many candles were handled."""
        now_ms = now_ms or int(time.time() * 1000)
        handled = 0
        for symbol in self.cfg.symbols:
            try:
                candles = self.market.fetch_ohlcv(symbol, self.cfg.timeframe, limit=self.lookback)
            except Exception as e:  # network, exchange maintenance: try again next poll
                log.warning("Nu pot lua lumânări pentru %s: %s", symbol, e)
                continue
            closed = [c for c in candles if c.ts + self.step <= now_ms]
            if not closed or self.last_ts.get(symbol) == closed[-1].ts:
                continue
            last_seen = self.last_ts.get(symbol)
            if last_seen is None:
                # First run: prime on the latest closed candle only, do not replay history.
                self.engine.on_candle(symbol, closed)
            else:
                for i, c in enumerate(closed):
                    if c.ts > last_seen:
                        self.engine.on_candle(symbol, closed[: i + 1])
            self.last_ts[symbol] = closed[-1].ts
            handled += 1
        if handled and self.store:
            self.store.save(self.engine)
            self.store.set("last_ts", self.last_ts)
        return handled

    def maybe_daily_report(self, now: datetime | None = None) -> bool:
        local = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        if local.hour < self.cfg.report_hour or self.report_day == local.date().isoformat():
            return False
        self.report_day = local.date().isoformat()
        if self.store:
            self.store.set("report_day", self.report_day)
        self.notify("📊 Raport zilnic\n" + self.status_text())
        return True

    def run_forever(self) -> None:
        while True:
            try:
                self.poll()
                self.maybe_daily_report()
            except Exception:
                log.exception("Eroare în bucla de tranzacționare")
            time.sleep(self.cfg.poll_seconds)

    # Telegram -----------------------------------------------------------------------

    def notify(self, text: str) -> None:
        if self.api and self.cfg.owner_telegram_chat_id:
            try:
                self.api.send_message(self.cfg.owner_telegram_chat_id, text)
            except Exception as e:
                log.warning("Nu pot trimite pe Telegram: %s", e)
        else:
            log.info("%s", text)

    def status_text(self) -> str:
        e, p = self.engine, self.engine.portfolio
        equity = e.equity()
        total = equity - p.initial_cash
        today = e.today_pnl()
        flags = []
        if e.paused:
            flags.append("pe pauză")
        if e.halted_day == e.day and e.day is not None:
            flags.append("limită zilnică atinsă")
        today_trades = [t for t in p.trades if e.day and e._day(t.closed_ts) == e.day]
        lines = [
            f"{self.cfg.business_name} · {'SIMULARE' if self.mode == 'paper' else 'LIVE'} · {e.strategy.describe()}" + (f" · {', '.join(flags)}" if flags else ""),
            f"Capital: {equity:.2f} {self.cfg.quote} (numerar {p.cash:.2f}) · total {total:+.2f} ({total / p.initial_cash * 100 if p.initial_cash else 0:+.2f}%)",
            f"Azi: {today:+.2f} {self.cfg.quote} · {len(today_trades)} tranzacții închise",
            f"Poziții deschise: {len(p.positions)}/{self.cfg.risk.max_positions}",
        ]
        for pos in p.positions.values():
            price = e.prices.get(pos.symbol, pos.entry)
            lines.append(f"  • {pos.symbol}: {pos.amount:.6g} la {pos.entry:.4g}, acum {price:.4g} ({pos.unrealized(price):+.2f}); stop {pos.stop:.4g}, țintă {pos.take:.4g}")
        return "\n".join(lines)

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message or "text" not in message or not self.api:
            return
        chat_id, text = message["chat"]["id"], message["text"].strip()
        if str(chat_id) != str(self.cfg.owner_telegram_chat_id):
            self.api.send_message(chat_id, "Acest bot răspunde doar proprietarului.")
            return
        answer = self.command(text)
        if answer:
            self.api.send_message(chat_id, answer)

    def command(self, text: str) -> str | None:
        parts = text.split()
        cmd, args = parts[0].lower(), parts[1:]
        e, p = self.engine, self.engine.portfolio
        if cmd in ("/start", "/help", "/ajutor"):
            return HELP
        if cmd == "/status":
            return self.status_text()
        if cmd == "/pozitii":
            if not p.positions:
                return "Nicio poziție deschisă."
            return "\n".join(f"{pos.symbol}: {pos.amount:.6g} la {pos.entry:.4g}, stop {pos.stop:.4g}, țintă {pos.take:.4g}, nerealizat {pos.unrealized(e.prices.get(pos.symbol, pos.entry)):+.2f}" for pos in p.positions.values())
        if cmd == "/tranzactii":
            if not p.trades:
                return "Nicio tranzacție încă."
            return "Ultimele tranzacții:\n" + "\n".join(t.describe() for t in p.trades[-10:])
        if cmd == "/raport":
            return "📊 Raport\n" + self.status_text()
        if cmd == "/pauza":
            e.paused = True
            self._persist()
            return "Pauză: nu mai deschid poziții noi. Cele deschise rămân cu stop și țintă. /pornire ca să reiau."
        if cmd == "/pornire":
            e.paused = False
            self._persist()
            return "Am reluat tranzacționarea."
        if cmd == "/inchide":
            if not args:
                return "Folosește: /inchide SIMBOL (ex. /inchide BTC/USDT)"
            symbol = args[0].upper()
            trade = e.close_manual(symbol)
            self._persist()
            return f"Închis: {trade.describe()}" if trade else f"Nu am poziție deschisă pe {symbol}."
        return "Nu am înțeles. " + HELP

    def _persist(self) -> None:
        if self.store:
            self.store.save(self.engine)

    def start_telegram(self) -> None:
        if self.api:
            threading.Thread(target=self.api.poll, args=(self.handle_update,), name="telegram", daemon=True).start()
