"""The trading engine: one code path for backtest, paper and live. Long-only spot.

On every closed candle: check the stop-loss / take-profit of an open position, ask the
strategy for a signal, open or close accordingly, record equity, enforce the daily loss limit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .config import ClientConfig
from .data import Candle
from .risk import RiskManager
from .strategies import Strategy

log = logging.getLogger(__name__)


@dataclass
class Fill:
    price: float
    amount: float
    fee: float

    @property
    def cost(self) -> float:
        return self.price * self.amount


@dataclass
class Position:
    symbol: str
    amount: float
    entry: float
    entry_fee: float
    stop: float
    take: float
    opened_ts: int

    def unrealized(self, price: float) -> float:
        return (price - self.entry) * self.amount - self.entry_fee


@dataclass
class Trade:
    symbol: str
    amount: float
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    opened_ts: int
    closed_ts: int
    reason: str  # signal | stop | take | manual | end

    def describe(self) -> str:
        sign = "+" if self.pnl >= 0 else ""
        return f"{self.symbol}: {self.entry:.4g} → {self.exit:.4g}, {sign}{self.pnl:.2f} ({sign}{self.pnl_pct:.2f}%), {self.reason}"


@dataclass
class Portfolio:
    cash: float
    initial_cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.amount * prices.get(sym, p.entry) for sym, p in self.positions.items())


class Broker:
    def buy(self, symbol: str, amount: float, price_hint: float) -> Fill:
        raise NotImplementedError

    def sell(self, symbol: str, amount: float, price_hint: float) -> Fill:
        raise NotImplementedError


class PaperBroker(Broker):
    """Fills at the hinted price moved against us by the slippage, minus fees."""

    def __init__(self, fee_pct: float, slippage_pct: float):
        self.fee, self.slip = fee_pct / 100, slippage_pct / 100

    def buy(self, symbol, amount, price_hint):
        price = price_hint * (1 + self.slip)
        return Fill(price, amount, price * amount * self.fee)

    def sell(self, symbol, amount, price_hint):
        price = price_hint * (1 - self.slip)
        return Fill(price, amount, price * amount * self.fee)


class Engine:
    def __init__(self, cfg: ClientConfig, strategy: Strategy, risk: RiskManager, broker: Broker, portfolio: Portfolio, notify=None, tz=timezone.utc):
        self.cfg, self.strategy, self.risk, self.broker, self.portfolio = cfg, strategy, risk, broker, portfolio
        self.notify = notify or (lambda text: None)
        self.tz = tz
        self.prices: dict[str, float] = {}
        self.day: date | None = None
        self.day_start_equity: float | None = None
        self.halted_day: date | None = None
        self.paused = False

    # State ---------------------------------------------------------------------------

    def _day(self, ts: int) -> date:
        return datetime.fromtimestamp(ts / 1000, tz=self.tz).date()

    def equity(self) -> float:
        return self.portfolio.equity(self.prices)

    def can_open(self, day: date) -> bool:
        return not self.paused and self.halted_day != day and len(self.portfolio.positions) < self.cfg.risk.max_positions

    def today_pnl(self) -> float:
        return self.equity() - self.day_start_equity if self.day_start_equity is not None else 0.0

    # Candle ----------------------------------------------------------------------------

    def on_candle(self, symbol: str, candles: list[Candle]) -> list[Trade]:
        """``candles`` are closed candles for ``symbol``, oldest first; the last one is new. Returns trades closed now."""
        c = candles[-1]
        self.prices[symbol] = c.close
        day = self._day(c.ts)
        if day != self.day:
            self.day, self.day_start_equity = day, self.equity()
        closed: list[Trade] = []

        pos = self.portfolio.positions.get(symbol)
        if pos:
            if c.low <= pos.stop:
                closed.append(self._close(symbol, pos.stop, "stop", c.ts))
                pos = None
            elif c.high >= pos.take:
                closed.append(self._close(symbol, pos.take, "take", c.ts))
                pos = None

        signal = self.strategy.signal(candles)
        if signal == "sell" and pos:
            closed.append(self._close(symbol, c.close, "signal", c.ts))
        elif signal == "buy" and not pos and self.can_open(day):
            self._open(symbol, c.close, c.ts)

        equity = self.equity()
        self.portfolio.equity_curve.append((c.ts, equity))
        if self.day_start_equity and self.halted_day != day and self.risk.daily_loss_exceeded(self.day_start_equity, equity):
            self.halted_day = day
            self.notify(f"🛑 Limita zilnică de pierdere ({self.cfg.risk.max_daily_loss_pct}%) a fost atinsă: {equity - self.day_start_equity:.2f} {self.cfg.quote} azi. Nu mai deschid poziții până mâine.")
        return closed

    def _open(self, symbol: str, price: float, ts: int) -> None:
        amount = self.risk.position_size(self.equity(), self.portfolio.cash, price)
        if amount <= 0:
            return
        fill = self.broker.buy(symbol, amount, price)
        self.portfolio.cash -= fill.cost + fill.fee
        stop, take = self.risk.stop_take(fill.price)
        self.portfolio.positions[symbol] = Position(symbol, fill.amount, fill.price, fill.fee, stop, take, ts)
        self.notify(f"🟢 Cumpărat {fill.amount:.6g} {symbol} la {fill.price:.4g} ({fill.cost:.2f} {self.cfg.quote}); stop {stop:.4g}, țintă {take:.4g}.")

    def _close(self, symbol: str, price: float, reason: str, ts: int) -> Trade:
        pos = self.portfolio.positions.pop(symbol)
        fill = self.broker.sell(symbol, pos.amount, price)
        self.portfolio.cash += fill.cost - fill.fee
        invested = pos.amount * pos.entry + pos.entry_fee
        pnl = fill.cost - fill.fee - invested
        trade = Trade(symbol, pos.amount, pos.entry, fill.price, pnl, pnl / invested * 100 if invested else 0.0, pos.opened_ts, ts, reason)
        self.portfolio.trades.append(trade)
        icon = "🔴" if reason == "stop" else "✅" if reason == "take" else "⚪"
        label = {"stop": "stop-loss", "take": "take-profit", "signal": "semnal de vânzare", "manual": "închis manual", "end": "sfârșit de date"}[reason]
        self.notify(f"{icon} Vândut {symbol} la {fill.price:.4g} ({label}): {'+' if pnl >= 0 else ''}{pnl:.2f} {self.cfg.quote} ({trade.pnl_pct:+.2f}%).")
        return trade

    def close_manual(self, symbol: str, ts: int | None = None) -> Trade | None:
        if symbol not in self.portfolio.positions or symbol not in self.prices:
            return None
        return self._close(symbol, self.prices[symbol], "manual", ts or int(datetime.now(timezone.utc).timestamp() * 1000))
