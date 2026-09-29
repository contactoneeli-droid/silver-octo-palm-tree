"""ccxt adapters: market data for paper mode, real orders for live mode."""

from __future__ import annotations

import logging
import os

import ccxt

from .data import Candle
from .engine import Broker, Fill

log = logging.getLogger(__name__)


def make_exchange(name: str, authenticated: bool = False):
    if not hasattr(ccxt, name):
        raise ValueError(f"Exchange necunoscut '{name}' pentru ccxt")
    params = {"enableRateLimit": True}
    if authenticated:
        key, secret = os.environ.get("EXCHANGE_API_KEY"), os.environ.get("EXCHANGE_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Lipsesc EXCHANGE_API_KEY / EXCHANGE_API_SECRET pentru modul live")
        params.update(apiKey=key, secret=secret)
        if os.environ.get("EXCHANGE_API_PASSWORD"):
            params["password"] = os.environ["EXCHANGE_API_PASSWORD"]
    exchange = getattr(ccxt, name)(params)
    if os.environ.get("EXCHANGE_SANDBOX") == "1":
        exchange.set_sandbox_mode(True)
    return exchange


class CcxtMarket:
    def __init__(self, exchange):
        self.exchange = exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200, since: int | None = None) -> list[Candle]:
        rows = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)) for r in rows]

    def fetch_history(self, symbol: str, timeframe: str, since: int, step_ms: int, limit: int = 1000) -> list[Candle]:
        """Pages through fetch_ohlcv from ``since`` until now."""
        out: list[Candle] = []
        cursor = since
        while True:
            batch = self.fetch_ohlcv(symbol, timeframe, limit=limit, since=cursor)
            if not batch:
                break
            out.extend(c for c in batch if not out or c.ts > out[-1].ts)
            if len(batch) < limit:
                break
            cursor = batch[-1].ts + step_ms
        return out


class CcxtBroker(Broker):
    """Market orders on the real exchange. Fees come from the order when the exchange reports them."""

    def __init__(self, exchange, fee_pct: float):
        self.exchange = exchange
        self.fee_pct = fee_pct
        self.exchange.load_markets()

    def _fill(self, symbol: str, order: dict, fallback_price: float) -> Fill:
        if order.get("id") and (order.get("average") is None or order.get("filled") in (None, 0)):
            order = self.exchange.fetch_order(order["id"], symbol)
        price = float(order.get("average") or order.get("price") or fallback_price)
        amount = float(order.get("filled") or order.get("amount"))
        fee = 0.0
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if f and f.get("cost"):
                cost = float(f["cost"])
                fee += cost * price if f.get("currency") and f["currency"] == symbol.split("/")[0] else cost
        return Fill(price, amount, fee or price * amount * self.fee_pct / 100)

    def buy(self, symbol, amount, price_hint):
        amount = float(self.exchange.amount_to_precision(symbol, amount))
        order = self.exchange.create_order(symbol, "market", "buy", amount)
        fill = self._fill(symbol, order, price_hint)
        log.info("LIVE cumpărat %s %s la %s", fill.amount, symbol, fill.price)
        return fill

    def sell(self, symbol, amount, price_hint):
        amount = float(self.exchange.amount_to_precision(symbol, amount))
        order = self.exchange.create_order(symbol, "market", "sell", amount)
        fill = self._fill(symbol, order, price_hint)
        log.info("LIVE vândut %s %s la %s", fill.amount, symbol, fill.price)
        return fill
