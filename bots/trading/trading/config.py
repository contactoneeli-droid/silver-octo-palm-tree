"""Per-client configuration: exchange, symbols, strategy, risk limits, reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CLIENTS_DIR = Path(os.environ.get("TRADING_CLIENTS_DIR", Path(__file__).resolve().parent.parent / "clients"))


@dataclass
class RiskConfig:
    position_pct: float = 10.0  # % of equity per position
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0
    max_positions: int = 2
    max_daily_loss_pct: float = 5.0  # stop opening positions for the rest of the day
    fee_pct: float = 0.1
    slippage_pct: float = 0.05
    min_notional: float = 10.0


@dataclass
class StrategyConfig:
    name: str = "sma_cross"
    params: dict = field(default_factory=dict)


@dataclass
class ClientConfig:
    slug: str
    business_name: str
    exchange: str = "binance"
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1h"
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    initial_cash: float = 1000.0
    quote: str = "USDT"
    report_hour: int = 20
    timezone: str = "Europe/Bucharest"
    poll_seconds: int = 60
    owner_telegram_chat_id: str | None = None
    root: Path | None = None


def _section(cls, raw: dict | None):
    raw = dict(raw or {})
    unknown = set(raw) - set(cls.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{cls.__name__}: chei necunoscute {sorted(unknown)}")
    return cls(**raw)


def list_clients(clients_dir: Path = CLIENTS_DIR) -> list[str]:
    if not clients_dir.is_dir():
        return []
    return sorted(p.name for p in clients_dir.iterdir() if (p / "config.yaml").is_file())


def load_client(slug: str, clients_dir: Path = CLIENTS_DIR) -> ClientConfig:
    root = clients_dir / slug
    path = root / "config.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Nu există clientul '{slug}' (lipsește {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "business_name" not in raw:
        raise ValueError(f"{path}: lipsește 'business_name'")
    symbols = [str(s).upper() for s in (raw.get("symbols") or ["BTC/USDT"])]
    if any("/" not in s for s in symbols):
        raise ValueError(f"{path}: simbolurile se scriu ca BAZĂ/COTAȚIE, ex. BTC/USDT")
    strategy = _section(StrategyConfig, raw.get("strategy"))
    risk = _section(RiskConfig, raw.get("risk"))
    if not 0 < risk.position_pct <= 100 or risk.stop_loss_pct <= 0 or risk.take_profit_pct <= 0:
        raise ValueError(f"{path}: risk.position_pct în (0,100], stop_loss_pct și take_profit_pct > 0")
    owner = os.environ.get("OWNER_TELEGRAM_CHAT_ID") or raw.get("owner_telegram_chat_id")
    return ClientConfig(
        slug=slug,
        business_name=raw["business_name"],
        exchange=str(raw.get("exchange", "binance")).lower(),
        symbols=symbols,
        timeframe=str(raw.get("timeframe", "1h")),
        strategy=strategy,
        risk=risk,
        initial_cash=float(raw.get("initial_cash", 1000)),
        quote=str(raw.get("quote") or symbols[0].split("/")[1]).upper(),
        report_hour=int(raw.get("report_hour", 20)),
        timezone=raw.get("timezone", "Europe/Bucharest"),
        poll_seconds=int(raw.get("poll_seconds", 60)),
        owner_telegram_chat_id=str(owner) if owner else None,
        root=root,
    )
