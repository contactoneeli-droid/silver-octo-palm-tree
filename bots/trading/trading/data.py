"""Candles: the dataclass, CSV load/save, timeframe helpers and a synthetic generator for demos."""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TIMEFRAME_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000}


@dataclass(frozen=True)
class Candle:
    ts: int  # open time, ms since epoch, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.ts / 1000, tz=timezone.utc)


def timeframe_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"Interval necunoscut '{timeframe}'; folosește unul din {', '.join(TIMEFRAME_MS)}")
    return TIMEFRAME_MS[timeframe]


def _parse_ts(value: str) -> int:
    value = value.strip()
    if value.replace(".", "", 1).isdigit():
        n = float(value)
        return int(n if n > 1e11 else n * 1000)  # seconds vs milliseconds
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def load_csv(path: str | Path) -> list[Candle]:
    """CSV with a header containing timestamp/time/date, open, high, low, close[, volume]."""
    candles = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = {k.lower().strip(): k for k in reader.fieldnames or []}
        ts_key = next((fields[k] for k in ("timestamp", "time", "date", "datetime", "open_time") if k in fields), None)
        if not ts_key or not {"open", "high", "low", "close"} <= set(fields):
            raise ValueError(f"{path}: coloane necesare: timestamp/time/date, open, high, low, close")
        for row in reader:
            candles.append(Candle(_parse_ts(row[ts_key]), float(row[fields["open"]]), float(row[fields["high"]]), float(row[fields["low"]]), float(row[fields["close"]]), float(row[fields["volume"]]) if "volume" in fields and row[fields["volume"]] else 0.0))
    candles.sort(key=lambda c: c.ts)
    return candles


def save_csv(path: str | Path, candles: list[Candle]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts, c.open, c.high, c.low, c.close, c.volume])


def synthetic(n: int = 2000, timeframe: str = "1h", start_price: float = 30_000, seed: int = 7, drift: float = 0.0002, vol: float = 0.01, start_ts: int | None = None) -> list[Candle]:
    """A random walk with drift and a few regime changes, for demos and tests. Deterministic per seed."""
    rng = random.Random(seed)
    step = timeframe_ms(timeframe)
    ts = start_ts if start_ts is not None else int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    price = start_price
    out = []
    regime = drift
    for i in range(n):
        if i % 300 == 0:
            regime = drift * rng.choice([-2, -1, 1, 2, 3])
        change = rng.gauss(regime, vol)
        o = price
        c = max(o * (1 + change), 1e-6)
        wick = abs(rng.gauss(0, vol / 2))
        h = max(o, c) * (1 + wick)
        lo = min(o, c) * (1 - wick)
        out.append(Candle(ts + i * step, round(o, 2), round(h, 2), round(lo, 2), round(c, 2), round(rng.uniform(10, 100), 3)))
        price = c
    return out
