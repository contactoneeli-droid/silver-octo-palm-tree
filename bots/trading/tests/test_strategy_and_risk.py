import pytest
from conftest import candles_from_closes

from trading.config import RiskConfig
from trading.data import load_csv, save_csv, synthetic, timeframe_ms
from trading.indicators import ema, highest, lowest, rsi, sma
from trading.risk import RiskManager
from trading.strategies import make_strategy


def test_indicators():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    e = ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and abs(e[3] - 3.0) < 1e-9
    up = rsi(list(range(1, 20)), 14)
    assert up[13] is None and up[14] == 100.0
    flat = rsi([5.0] * 20, 14)
    assert flat[-1] == 100.0  # no losses at all
    mixed = rsi([44, 44.3, 44.1, 43.6, 44.3, 44.8, 45.1, 45.4, 45.8, 46.1, 45.9, 46.0, 45.7, 46.3, 46.4, 46.1, 46.4, 46.2, 45.6], 14)
    assert 60 < mixed[-1] < 80
    assert highest([1, 3, 2, 5, 4], 3) == [None, None, 3, 5, 5] and lowest([1, 3, 2, 5, 4], 3) == [None, None, 1, 2, 2]


def test_sma_cross_signals():
    s = make_strategy("sma_cross", {"fast": 2, "slow": 4})
    # 10 flat, then rising: fast crosses above slow on the first up move
    candles = candles_from_closes([10] * 6 + [11, 12, 13, 12, 11, 9, 8])
    signals = [s.signal(candles[: i + 1]) for i in range(len(candles))]
    assert signals[:5] == [None] * 5
    assert "buy" in signals and "sell" in signals
    assert signals.index("buy") < signals.index("sell")
    with pytest.raises(ValueError):
        make_strategy("sma_cross", {"fast": 5, "slow": 5})
    with pytest.raises(ValueError):
        make_strategy("nope")


def test_rsi_and_breakout_signals():
    r = make_strategy("rsi", {"period": 3, "oversold": 30, "overbought": 70})
    falling_then_up = candles_from_closes([10, 9, 8, 7, 6, 5, 6.5, 8, 9.5, 11, 10.5, 9.5, 8.5])
    sig = [r.signal(falling_then_up[: i + 1]) for i in range(len(falling_then_up))]
    assert "buy" in sig and "sell" in sig and sig.index("buy") < sig.index("sell")

    b = make_strategy("breakout", {"period": 3})
    candles = candles_from_closes([10, 10, 10, 10, 12, 12, 12, 9])
    sig = [b.signal(candles[: i + 1]) for i in range(len(candles))]
    assert sig[4] == "buy" and sig[-1] == "sell" and sig[5] is None


def test_risk_sizing_and_limits():
    rm = RiskManager(RiskConfig(position_pct=10, stop_loss_pct=3, take_profit_pct=6, fee_pct=0.1, slippage_pct=0.05, min_notional=10, max_daily_loss_pct=5))
    assert rm.position_size(equity=1000, cash=1000, price=50) == pytest.approx(2.0)  # 100 budget
    assert rm.position_size(equity=1000, cash=50, price=50) == pytest.approx(50 / 1.0015 / 50)  # capped by cash
    assert rm.position_size(equity=50, cash=50, price=50) == 0.0  # 5 < min_notional
    assert rm.stop_take(100) == (97.0, 106.0)
    assert rm.daily_loss_exceeded(1000, 950) and not rm.daily_loss_exceeded(1000, 951)


def test_csv_roundtrip_and_synthetic(tmp_path):
    candles = synthetic(50, seed=1)
    assert len(candles) == 50 and all(c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in candles)
    assert candles[1].ts - candles[0].ts == timeframe_ms("1h")
    path = tmp_path / "x.csv"
    save_csv(path, candles)
    assert load_csv(path) == candles
    (tmp_path / "iso.csv").write_text("date,open,high,low,close\n2026-01-01T00:00:00Z,1,2,0.5,1.5\n2026-01-01T01:00:00Z,1.5,2,1,1.2\n")
    iso = load_csv(tmp_path / "iso.csv")
    assert iso[0].ts == 1_767_225_600_000 and iso[1].close == 1.2 and iso[0].volume == 0.0
    with pytest.raises(ValueError):
        timeframe_ms("7h")
