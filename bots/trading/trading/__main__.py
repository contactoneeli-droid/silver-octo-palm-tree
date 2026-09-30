"""Run the trading bot for one client.

    python -m trading backtest --client demo-crypto --synthetic          # demo without network
    python -m trading backtest --client demo-crypto --days 365            # downloads candles via ccxt
    python -m trading backtest --client demo-crypto --csv data/BTC-USDT-1h.csv --symbol BTC/USDT
    python -m trading download --client demo-crypto --days 365            # saves CSVs under data/
    python -m trading paper --client demo-crypto                          # real prices, simulated money
    python -m trading live --client demo-crypto                           # real orders; needs TRADING_LIVE_CONFIRM=DA

Environment: TELEGRAM_BOT_TOKEN + OWNER_TELEGRAM_CHAT_ID (reports and commands),
EXCHANGE_API_KEY / EXCHANGE_API_SECRET [/ EXCHANGE_API_PASSWORD] (live), EXCHANGE_SANDBOX=1
(testnet where the exchange has one), TRADING_DATA_DIR.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .backtest import run_backtest
from .config import list_clients, load_client
from .data import load_csv, save_csv, synthetic, timeframe_ms
from .engine import Engine, PaperBroker, Portfolio
from .report import trades_csv
from .risk import RiskManager
from .strategies import make_strategy

DATA_DIR = Path(os.environ.get("TRADING_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _candles_for_backtest(cfg, args, log):
    if args.synthetic:
        return {sym: synthetic(n=args.candles, timeframe=cfg.timeframe, seed=7 + i) for i, sym in enumerate(cfg.symbols)}
    if args.csv:
        symbol = args.symbol or cfg.symbols[0]
        return {symbol: load_csv(args.csv)}
    from .exchange import CcxtMarket, make_exchange

    market = CcxtMarket(make_exchange(cfg.exchange))
    since = int(time.time() * 1000) - args.days * 86_400_000
    out = {}
    for sym in cfg.symbols:
        log.info("Descarc %s %s pe %s zile de pe %s...", sym, cfg.timeframe, args.days, cfg.exchange)
        out[sym] = market.fetch_history(sym, cfg.timeframe, since, timeframe_ms(cfg.timeframe))
        log.info("%s lumânări", len(out[sym]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading")
    parser.add_argument("mode", choices=["backtest", "download", "paper", "live"])
    parser.add_argument("--client", default=os.environ.get("TRADING_CLIENT", "demo-crypto"))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--csv", help="fișier CSV cu lumânări (timestamp, open, high, low, close[, volume])")
    parser.add_argument("--symbol", help="simbolul pentru --csv")
    parser.add_argument("--synthetic", action="store_true", help="date sintetice, fără rețea")
    parser.add_argument("--candles", type=int, default=3000, help="câte lumânări sintetice")
    parser.add_argument("--out", help="scrie tranzacțiile din backtest într-un CSV")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("trading")

    try:
        cfg = load_client(args.client)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        print(f"Clienți disponibili: {', '.join(list_clients()) or '(niciunul)'}", file=sys.stderr)
        return 2
    strategy = make_strategy(cfg.strategy.name, cfg.strategy.params)

    if args.mode == "backtest":
        report = run_backtest(cfg, strategy, _candles_for_backtest(cfg, args, log))
        print(report.summary())
        if args.out:
            trades_csv(report.trades, args.out)
            print(f"Tranzacțiile sunt în {args.out}")
        return 0

    if args.mode == "download":
        args.synthetic, args.csv = False, None
        for sym, candles in _candles_for_backtest(cfg, args, log).items():
            path = DATA_DIR / f"{sym.replace('/', '-')}-{cfg.timeframe}.csv"
            save_csv(path, candles)
            print(f"{sym}: {len(candles)} lumânări în {path}")
        return 0

    # paper / live ------------------------------------------------------------------
    from .exchange import CcxtBroker, CcxtMarket, make_exchange
    from .live import Runner
    from .store import Store

    live = args.mode == "live"
    if live and os.environ.get("TRADING_LIVE_CONFIRM") != "DA":
        print("Modul live pune ordine reale cu bani reali. Setează TRADING_LIVE_CONFIRM=DA ca să confirmi.", file=sys.stderr)
        return 2
    exchange = make_exchange(cfg.exchange, authenticated=live)
    broker = CcxtBroker(exchange, cfg.risk.fee_pct) if live else PaperBroker(cfg.risk.fee_pct, cfg.risk.slippage_pct)

    api = None
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token and cfg.owner_telegram_chat_id:
        from .telegram import TelegramApi

        api = TelegramApi(token)
    else:
        log.warning("Fără TELEGRAM_BOT_TOKEN + OWNER_TELEGRAM_CHAT_ID rapoartele merg doar în log.")

    store = Store(cfg.slug, args.mode)
    portfolio = Portfolio(cash=cfg.initial_cash, initial_cash=cfg.initial_cash)
    engine = Engine(cfg, strategy, RiskManager(cfg.risk), broker, portfolio, tz=ZoneInfo(cfg.timezone))
    runner = Runner(cfg, engine, CcxtMarket(exchange), store, api, mode=args.mode)
    engine.notify = runner.notify
    if store.load(engine):
        log.info("Am reluat starea salvată: %.2f %s numerar, %s poziții", portfolio.cash, cfg.quote, len(portfolio.positions))
    elif live:
        balance = exchange.fetch_balance().get("free", {}).get(cfg.quote)
        if balance:
            portfolio.cash = portfolio.initial_cash = float(balance)
            log.info("Sold disponibil pe exchange: %.2f %s", portfolio.cash, cfg.quote)

    runner.start_telegram()
    runner.notify(f"🤖 {cfg.business_name} pornit în modul {'LIVE' if live else 'SIMULARE'} pe {cfg.exchange}: {', '.join(cfg.symbols)} {cfg.timeframe}, {strategy.describe()}. {HELP_HINT}")
    runner.run_forever()
    return 0


HELP_HINT = "Scrie /status oricând."

if __name__ == "__main__":
    sys.exit(main())
