"""Backtest / period metrics and their text form for the console and Telegram."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .engine import Trade


@dataclass
class Report:
    initial_cash: float
    final_equity: float
    trades: list[Trade]
    equity_curve: list[tuple[int, float]]
    buy_hold_pct: float
    quote: str = "USDT"
    strategy: str = ""
    symbols: list[str] = field(default_factory=list)

    @property
    def return_pct(self) -> float:
        return (self.final_equity / self.initial_cash - 1) * 100 if self.initial_cash else 0.0

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / len(self.trades) * 100 if self.trades else 0.0

    @property
    def profit_factor(self) -> float | None:
        gross_loss = -sum(t.pnl for t in self.losses)
        gross_win = sum(t.pnl for t in self.wins)
        if gross_loss == 0:
            return None if gross_win == 0 else float("inf")
        return gross_win / gross_loss

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = None, 0.0
        for _, eq in self.equity_curve:
            peak = eq if peak is None or eq > peak else peak
            if peak:
                worst = min(worst, (eq - peak) / peak * 100)
        return worst

    @property
    def period(self) -> tuple[datetime, datetime] | None:
        if not self.equity_curve:
            return None
        return (datetime.fromtimestamp(self.equity_curve[0][0] / 1000, tz=timezone.utc), datetime.fromtimestamp(self.equity_curve[-1][0] / 1000, tz=timezone.utc))

    def summary(self) -> str:
        pf = self.profit_factor
        pf_text = "n/a" if pf is None else "∞" if pf == float("inf") else f"{pf:.2f}"
        period = self.period
        when = f"{period[0]:%d.%m.%Y} → {period[1]:%d.%m.%Y}" if period else "fără date"
        avg_win = sum(t.pnl_pct for t in self.wins) / len(self.wins) if self.wins else 0.0
        avg_loss = sum(t.pnl_pct for t in self.losses) / len(self.losses) if self.losses else 0.0
        reasons = {}
        for t in self.trades:
            reasons[t.reason] = reasons.get(t.reason, 0) + 1
        lines = [
            f"📈 Backtest {', '.join(self.symbols)} · {self.strategy}",
            f"Perioadă: {when}",
            f"Capital: {self.initial_cash:.2f} → {self.final_equity:.2f} {self.quote} ({self.return_pct:+.2f}%)",
            f"Buy & hold în aceeași perioadă: {self.buy_hold_pct:+.2f}%",
            f"Tranzacții: {len(self.trades)} · câștigătoare {self.win_rate:.0f}% · profit factor {pf_text}",
            f"Câștig mediu {avg_win:+.2f}% · pierdere medie {avg_loss:+.2f}%",
            f"Scădere maximă (drawdown): {self.max_drawdown_pct:.2f}%",
        ]
        if reasons:
            lines.append("Închideri: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
        return "\n".join(lines)


def trades_csv(trades: list[Trade], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "opened", "closed", "amount", "entry", "exit", "pnl", "pnl_pct", "reason"])
        for t in trades:
            w.writerow([t.symbol, datetime.fromtimestamp(t.opened_ts / 1000, tz=timezone.utc).isoformat(), datetime.fromtimestamp(t.closed_ts / 1000, tz=timezone.utc).isoformat(), f"{t.amount:.8f}", t.entry, t.exit, f"{t.pnl:.4f}", f"{t.pnl_pct:.4f}", t.reason])
