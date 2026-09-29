# Bot de trading

Execută o strategie simplă pe un exchange de crypto (prin ccxt: Binance, Bybit, Kraken, OKX...),
cu **backtest** pe date istorice, **simulare** cu prețuri reale și bani fictivi, și abia apoi **live**.
Are stop-loss, take-profit, limită de pierdere zilnică și rapoarte pe Telegram. Long-only, spot.

> Niciun bot nu garantează profit. Backtest-ul arată cum s-ar fi comportat strategia în trecut,
> nu ce va face în viitor. Clientul pornește live doar cu bani pe care își permite să îi piardă,
> cu chei API fără drept de retragere.

## Ce face

- **Strategii** (`strategy.name` + `params`): `sma_cross` (încrucișare de medii mobile),
  `rsi` (revenire din supravânzare/supracumpărare), `breakout` (ieșire din canalul ultimelor N lumânări).
  Una nouă = o clasă în `strategies.py` cu metoda `signal(candles) -> "buy" | "sell" | None`.
- **Risc**: `position_pct` din capital per poziție, `stop_loss_pct`, `take_profit_pct`,
  `max_positions`, `max_daily_loss_pct` (sub acest prag pe zi botul nu mai deschide poziții până
  a doua zi), comision și alunecare presupuse în simulare.
- **Backtest**: același motor ca în live, rulat pe lumânări istorice; raport cu randament, buy & hold
  pe aceeași perioadă, număr de tranzacții, procent câștigătoare, profit factor, câștig/pierdere
  medie, drawdown maxim; export CSV al tranzacțiilor.
- **Simulare (paper)**: ia prețurile reale de pe exchange la fiecare lumânare închisă și execută
  fictiv, cu starea salvată în SQLite (supraviețuiește repornirii).
- **Live**: ordine la piață reale prin ccxt. Pornește doar cu `TRADING_LIVE_CONFIRM=DA`.
- **Telegram** (proprietar): notificare la fiecare cumpărare/vânzare, raport zilnic la `report_hour`,
  `/status`, `/pozitii`, `/tranzactii`, `/raport`, `/pauza`, `/pornire`, `/inchide SIMBOL`.

## Pornire

```bash
cd bots/trading
pip install -r requirements-dev.txt

# 1. Demo fără rețea, pe date sintetice
python -m trading backtest --client demo-crypto --synthetic

# 2. Backtest pe date reale (descărcate de pe exchange, fără cont)
python -m trading backtest --client demo-crypto --days 365 --out tranzactii.csv
python -m trading download --client demo-crypto --days 730        # salvează CSV în data/
python -m trading backtest --client demo-crypto --csv data/BTC-USDT-1h.csv --symbol BTC/USDT

# 3. Simulare cu prețuri reale (rapoarte pe Telegram dacă setezi token + chat id)
cp .env.example .env && export $(grep -v '^#' .env | xargs)
python -m trading paper --client demo-crypto

# 4. Live, după săptămâni de simulare bună
EXCHANGE_API_KEY=... EXCHANGE_API_SECRET=... TRADING_LIVE_CONFIRM=DA python -m trading live --client demo-crypto
```

`clients/<slug>/config.yaml`: exchange, simboluri, interval, strategie, risc, capital inițial,
ora raportului. Vezi `clients/demo-crypto/config.yaml`.

## Teste

```bash
pytest
```

Testele acoperă indicatorii, semnalele strategiilor, mărimea pozițiilor, intrarea/ieșirea
(semnal, stop, țintă), limita zilnică, backtest-ul cu raport, bucla de simulare cu persistență,
raportul zilnic și comenzile Telegram. Nu ating exchange-uri reale.

## Limite cunoscute

- Long-only, spot; fără short, fără levier, fără futures.
- Ordine la piață; fără trailing stop sau ordine limită.
- Un proces per client, un exchange per proces.
- Modul live a fost scris după API-ul ccxt, dar nu a putut fi rulat pe un exchange din mediul de
  dezvoltare (fără rețea către exchange-uri). Prima pornire live se face pe sume mici sau pe
  testnet (`EXCHANGE_SANDBOX=1`, unde există).
