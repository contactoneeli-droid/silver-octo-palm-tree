import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trading.config import load_client  # noqa: E402
from trading.data import Candle  # noqa: E402

HOUR = 3_600_000
T0 = 1_767_225_600_000  # 2026-01-01 00:00 UTC


def candles_from_closes(closes, start_ts=T0, step=HOUR, wick=0.0):
    """Flat-ish candles whose close follows ``closes``; high/low = close ± wick fraction."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + wick), min(prev, c) * (1 - wick)
        out.append(Candle(start_ts + i * step, prev, hi, lo, c, 1.0))
        prev = c
    return out


class FakeMarket:
    """Serves candles up to a movable cursor, like an exchange whose time advances."""

    def __init__(self, series: dict):
        self.series = series
        self.cursor = {s: 0 for s in series}

    def advance(self, symbol, n=1):
        self.cursor[symbol] = min(self.cursor[symbol] + n, len(self.series[symbol]))

    def fetch_ohlcv(self, symbol, timeframe, limit=200, since=None):
        upto = self.cursor[symbol]
        return self.series[symbol][max(0, upto - limit) : upto]


class FakeApi:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))

    def texts(self):
        return [t for _, t in self.sent]


@pytest.fixture
def cfg():
    c = load_client("demo-crypto", ROOT / "clients")
    c.owner_telegram_chat_id = "1"
    return c
