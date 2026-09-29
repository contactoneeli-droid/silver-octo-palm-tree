"""Strategies decide on the last closed candle: "buy", "sell" or None. Long-only, one position per symbol."""

from __future__ import annotations

from .data import Candle
from .indicators import highest, lowest, rsi, sma


class Strategy:
    name = "base"
    lookback = 2  # candles needed before a signal can be computed

    def __init__(self, **params):
        self.params = params

    def signal(self, candles: list[Candle]) -> str | None:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in self.params.items())})"


class SmaCross(Strategy):
    """Buy when the fast SMA crosses above the slow one, sell when it crosses below."""

    name = "sma_cross"

    def __init__(self, fast: int = 20, slow: int = 50):
        if fast >= slow:
            raise ValueError("sma_cross: fast trebuie să fie mai mic decât slow")
        super().__init__(fast=fast, slow=slow)
        self.fast, self.slow = fast, slow
        self.lookback = slow + 1

    def signal(self, candles):
        if len(candles) < self.lookback:
            return None
        closes = [c.close for c in candles]
        f, s = sma(closes, self.fast), sma(closes, self.slow)
        if None in (f[-1], f[-2], s[-1], s[-2]):
            return None
        if f[-2] <= s[-2] and f[-1] > s[-1]:
            return "buy"
        if f[-2] >= s[-2] and f[-1] < s[-1]:
            return "sell"
        return None


class RsiReversion(Strategy):
    """Buy when RSI climbs back above the oversold line, sell when it drops back below the overbought line."""

    name = "rsi"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__(period=period, oversold=oversold, overbought=overbought)
        self.period, self.oversold, self.overbought = period, oversold, overbought
        self.lookback = period + 3

    def signal(self, candles):
        if len(candles) < self.lookback:
            return None
        r = rsi([c.close for c in candles], self.period)
        if r[-1] is None or r[-2] is None:
            return None
        if r[-2] < self.oversold <= r[-1]:
            return "buy"
        if r[-2] > self.overbought >= r[-1]:
            return "sell"
        return None


class Breakout(Strategy):
    """Buy on a close above the highest high of the previous ``period`` candles, sell on a close below their lowest low."""

    name = "breakout"

    def __init__(self, period: int = 20):
        super().__init__(period=period)
        self.period = period
        self.lookback = period + 1

    def signal(self, candles):
        if len(candles) < self.lookback:
            return None
        prev = candles[-self.period - 1 : -1]
        last = candles[-1]
        if last.close > max(c.high for c in prev):
            return "buy"
        if last.close < min(c.low for c in prev):
            return "sell"
        return None


STRATEGIES = {cls.name: cls for cls in (SmaCross, RsiReversion, Breakout)}


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"Strategie necunoscută '{name}'; disponibile: {', '.join(STRATEGIES)}")
    return STRATEGIES[name](**(params or {}))
