from datetime import datetime, timezone

import pytest
from conftest import HOUR, T0, FakeApi, FakeMarket, candles_from_closes

from trading.backtest import run_backtest
from trading.engine import Engine, PaperBroker, Portfolio
from trading.live import Runner
from trading.risk import RiskManager
from trading.store import Store
from trading.strategies import make_strategy


def make_engine(cfg, strategy=None, cash=1000.0, notes=None):
    strategy = strategy or make_strategy("sma_cross", {"fast": 2, "slow": 4})
    portfolio = Portfolio(cash=cash, initial_cash=cash)
    notify = notes.append if notes is not None else None
    return Engine(cfg, strategy, RiskManager(cfg.risk), PaperBroker(cfg.risk.fee_pct, cfg.risk.slippage_pct), portfolio, notify=notify)


def feed(engine, symbol, candles):
    trades = []
    for i in range(len(candles)):
        trades += engine.on_candle(symbol, candles[: i + 1])
    return trades


def test_open_on_buy_signal_then_stop_loss(cfg):
    cfg.risk.position_pct, cfg.risk.stop_loss_pct, cfg.risk.take_profit_pct = 10, 3, 50
    notes = []
    e = make_engine(cfg, notes=notes)
    # flat, then a cross up at 100 -> buy; then a candle whose low pierces the stop
    closes = [100] * 6 + [101, 102, 103]
    candles = candles_from_closes(closes)
    assert feed(e, "BTC/USDT", candles) == []
    pos = e.portfolio.positions["BTC/USDT"]
    entry = 101 * 1.0005  # slippage
    assert pos.entry == pytest.approx(entry) and pos.amount == pytest.approx(100 / 101)
    assert pos.stop == pytest.approx(entry * 0.97) and pos.take == pytest.approx(entry * 1.5)
    assert e.portfolio.cash == pytest.approx(1000 - pos.amount * entry * 1.001)

    from trading.data import Candle

    crash = Candle(candles[-1].ts + HOUR, 103, 103, 95, 96, 1)
    trades = e.on_candle("BTC/USDT", candles + [crash])
    assert len(trades) == 1 and trades[0].reason == "stop"
    t = trades[0]
    assert t.exit == pytest.approx(pos.stop * (1 - 0.0005))
    assert t.pnl < 0 and t.pnl_pct == pytest.approx(t.pnl / (pos.amount * pos.entry + pos.entry_fee) * 100)
    assert "BTC/USDT" not in e.portfolio.positions
    assert any("Cumpărat" in n for n in notes) and any("stop-loss" in n for n in notes)


def test_take_profit_and_sell_signal(cfg):
    cfg.risk.take_profit_pct = 2
    e = make_engine(cfg)
    candles = candles_from_closes([100] * 6 + [101, 104])
    trades = feed(e, "BTC/USDT", candles)
    assert [t.reason for t in trades] == ["take"] and trades[0].pnl > 0

    cfg.risk.take_profit_pct = 50
    e = make_engine(cfg)
    candles = candles_from_closes([100] * 6 + [101, 102, 103, 102, 101, 99, 98])
    trades = feed(e, "BTC/USDT", candles)
    assert [t.reason for t in trades] == ["signal"]


def test_max_positions_and_daily_loss_halt(cfg):
    cfg.risk.max_positions = 1
    e = make_engine(cfg)
    up = candles_from_closes([100] * 6 + [101, 102])
    feed(e, "BTC/USDT", up)
    feed(e, "ETH/USDT", up)
    assert list(e.portfolio.positions) == ["BTC/USDT"]

    cfg.risk.max_positions, cfg.risk.position_pct, cfg.risk.max_daily_loss_pct, cfg.risk.stop_loss_pct = 5, 50, 1, 3  # a 3% stop on half the equity loses ~1.5%
    notes = []
    e = make_engine(cfg, notes=notes)
    from trading.data import Candle

    base = candles_from_closes([100] * 6 + [101, 102])
    feed(e, "BTC/USDT", base)
    crash = Candle(base[-1].ts + HOUR, 102, 102, 90, 91, 1)
    e.on_candle("BTC/USDT", base + [crash])
    assert e.halted_day is not None and any("Limita zilnică" in n for n in notes)
    # A new buy signal the same day is ignored...
    again = base + [crash] + candles_from_closes([91, 91, 91, 91, 92, 93], start_ts=crash.ts + HOUR)
    e.on_candle("BTC/USDT", again)
    assert not e.portfolio.positions and not e.can_open(e.day)
    # ...but the next day trading resumes.
    next_day = candles_from_closes([93] * 6 + [94, 95], start_ts=T0 + 24 * HOUR)
    feed(e, "BTC/USDT", next_day)
    assert "BTC/USDT" in e.portfolio.positions


def test_backtest_report_metrics(cfg):
    cfg.symbols = ["BTC/USDT"]
    closes = [100] * 6 + [101, 102, 103, 104, 105, 104, 103, 101, 100] * 3
    report = run_backtest(cfg, make_strategy("sma_cross", {"fast": 2, "slow": 4}), {"BTC/USDT": candles_from_closes(closes)})
    assert report.initial_cash == 1000 and len(report.trades) >= 2
    assert report.final_equity == pytest.approx(1000 + sum(t.pnl for t in report.trades))
    assert report.buy_hold_pct == pytest.approx(0.0)
    assert 0 <= report.win_rate <= 100 and report.max_drawdown_pct <= 0
    assert all(t.reason in ("signal", "stop", "take", "end") for t in report.trades)
    text = report.summary()
    assert "Backtest BTC/USDT" in text and "Buy & hold" in text and "drawdown" in text
    # Open position at the end of data is closed with reason "end".
    tail = run_backtest(cfg, make_strategy("sma_cross", {"fast": 2, "slow": 4}), {"BTC/USDT": candles_from_closes([100] * 6 + [101, 102])})
    assert [t.reason for t in tail.trades] == ["end"]


def test_runner_polls_closed_candles_persists_and_reports(cfg, tmp_path):
    cfg.symbols, cfg.report_hour = ["BTC/USDT"], 20
    series = candles_from_closes([100] * 6 + [101, 102, 103, 104])
    market = FakeMarket({"BTC/USDT": series})
    api = FakeApi()
    store = Store(cfg.slug, "paper", tmp_path)
    e = make_engine(cfg)
    runner = Runner(cfg, e, market, store, api, mode="paper")
    e.notify = runner.notify

    market.advance("BTC/USDT", 6)
    now = series[5].ts + HOUR  # candle 5 just closed
    assert runner.poll(now) == 1 and runner.poll(now) == 0  # same candle twice: ignored
    market.advance("BTC/USDT", 4)
    assert runner.poll(series[-1].ts + HOUR) == 1
    assert "BTC/USDT" in e.portfolio.positions
    assert any("Cumpărat" in t for t in api.texts())

    # Restart: state comes back from SQLite.
    e2 = make_engine(cfg)
    assert store.load(e2) and e2.portfolio.cash == pytest.approx(e.portfolio.cash)
    assert e2.portfolio.positions["BTC/USDT"].entry == e.portfolio.positions["BTC/USDT"].entry
    runner2 = Runner(cfg, e2, market, store, api, mode="paper")
    assert runner2.last_ts == {"BTC/USDT": series[-1].ts}

    # Daily report once per day after report_hour (Europe/Bucharest = UTC+2 in winter).
    before = datetime(2026, 1, 2, 17, 0, tzinfo=timezone.utc)
    assert not runner.maybe_daily_report(before)
    at = datetime(2026, 1, 2, 18, 30, tzinfo=timezone.utc)
    assert runner.maybe_daily_report(at) and not runner.maybe_daily_report(at)
    assert api.texts()[-1].startswith("📊 Raport zilnic") and "SIMULARE" in api.texts()[-1]

    # Telegram commands, owner only.
    runner.handle_update({"message": {"chat": {"id": 999}, "text": "/status"}})
    assert "doar proprietarului" in api.texts()[-1]
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/pozitii"}})
    assert "BTC/USDT" in api.texts()[-1] and "stop" in api.texts()[-1]
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/pauza"}})
    assert e.paused and store.get("engine")["paused"] is True
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/inchide btc/usdt"}})
    assert "Închis" in api.texts()[-1] and not e.portfolio.positions and e.portfolio.trades[-1].reason == "manual"
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/tranzactii"}})
    assert "manual" in api.texts()[-1]
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/pornire"}})
    assert not e.paused
    runner.handle_update({"message": {"chat": {"id": 1}, "text": "/altceva"}})
    assert "Nu am înțeles" in api.texts()[-1]
    store.close()
