"""Position sizing and the limits that keep a bad day from becoming a bad month."""

from __future__ import annotations

from .config import RiskConfig


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def position_size(self, equity: float, cash: float, price: float) -> float:
        """Amount (base units) to buy: position_pct of equity, capped by cash after fees/slippage; 0 if too small."""
        costs = 1 + (self.cfg.fee_pct + self.cfg.slippage_pct) / 100
        budget = min(equity * self.cfg.position_pct / 100, cash / costs)
        if budget < self.cfg.min_notional or price <= 0:
            return 0.0
        return budget / price

    def stop_take(self, entry: float) -> tuple[float, float]:
        return entry * (1 - self.cfg.stop_loss_pct / 100), entry * (1 + self.cfg.take_profit_pct / 100)

    def daily_loss_exceeded(self, day_start_equity: float, equity: float) -> bool:
        return day_start_equity > 0 and equity <= day_start_equity * (1 - self.cfg.max_daily_loss_pct / 100)
