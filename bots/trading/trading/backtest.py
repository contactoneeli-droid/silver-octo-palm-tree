"""Replay historical candles through the engine, symbol by symbol in time order."""

from __future__ import annotations

from datetime import timezone

from .config import ClientConfig
from .data import Candle
from .engine import Engine, PaperBroker, Portfolio
from .report import Report
from .risk import RiskManager
from .strategies import Strategy


def run_backtest(cfg: ClientConfig, strategy: Strategy, candles_by_symbol: dict[str, list[Candle]], initial_cash: float | None = None, notify=None) -> Report:
    initial = initial_cash if initial_cash is not None else cfg.initial_cash
    portfolio = Portfolio(cash=initial, initial_cash=initial)
    engine = Engine(cfg, strategy, RiskManager(cfg.risk), PaperBroker(cfg.risk.fee_pct, cfg.risk.slippage_pct), portfolio, notify=notify, tz=timezone.utc)

    window = strategy.lookback + 2
    index = {sym: 0 for sym in candles_by_symbol}
    timestamps = sorted({c.ts for cs in candles_by_symbol.values() for c in cs})
    for ts in timestamps:
        for sym, cs in candles_by_symbol.items():
            i = index[sym]
            if i < len(cs) and cs[i].ts == ts:
                engine.on_candle(sym, cs[max(0, i - window + 1) : i + 1])
                index[sym] = i + 1

    last_ts = timestamps[-1] if timestamps else 0
    for sym in list(portfolio.positions):
        engine._close(sym, engine.prices[sym], "end", last_ts)
    if timestamps:
        portfolio.equity_curve.append((last_ts, engine.equity()))

    holds = [(cs[-1].close / cs[0].close - 1) * 100 for cs in candles_by_symbol.values() if len(cs) > 1 and cs[0].close]
    return Report(
        initial_cash=initial,
        final_equity=engine.equity(),
        trades=list(portfolio.trades),
        equity_curve=list(portfolio.equity_curve),
        buy_hold_pct=sum(holds) / len(holds) if holds else 0.0,
        quote=cfg.quote,
        strategy=strategy.describe(),
        symbols=list(candles_by_symbol),
    )
