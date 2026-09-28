"""Phase C: position sizing and portfolio risk limits.

Per TRADING_OVERHAUL_PLAN.md Phase C -- leverage/size stop being an LLM
guess (finding F10: higher "conviction" leverage produced BIGGER losses,
with leverage uncorrelated to being right) and become an OUTPUT of risk
sizing instead. Every function here is pure (no I/O, no global state) so it
is directly unit-testable and directly reusable by both a backtest overlay
and the live paper-trading loop -- one sizing implementation, not two that
can drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    """Fixed for the B1v2 paper trial -- see research/reports/paper_trial_plan.md
    for why these specific numbers, agreed before the trial starts."""
    risk_pct_per_trade: float = 0.5      # of current equity, at the stop
    max_total_open_risk_pct: float = 3.0  # sum of all open positions' risk-at-stop
    max_concurrent_positions: int = 10
    daily_loss_limit_pct: float = 5.0     # halt new entries for the rest of the day
    total_drawdown_limit_pct: float = 15.0  # halt new entries entirely (needs operator to resume)


@dataclass
class Position:
    symbol: str
    direction: int          # +1 long, -1 short
    entry_price: float
    notional_usd: float      # position size = risk_usd / stop_distance_pct * 100
    risk_usd: float          # $ at stake if the CURRENT stop is hit
    stop_price: float


def position_size(
    equity_usd: float, stop_distance_pct: float, risk_pct_per_trade: float,
) -> tuple[float, float]:
    """Fixed-fractional sizing: risk a fixed % of equity, sized so a stop-out
    loses exactly that amount regardless of the coin's own volatility --
    replaces the old "leverage = the LLM's confidence" approach (F10) with
    the same risk in dollar terms whether the stop is 1% away or 8% away.

    Returns (notional_usd, risk_usd). stop_distance_pct must be > 0.
    """
    if stop_distance_pct <= 0:
        raise ValueError("stop_distance_pct must be > 0")
    risk_usd = equity_usd * risk_pct_per_trade / 100
    notional_usd = risk_usd / (stop_distance_pct / 100)
    return notional_usd, risk_usd


def can_open(
    equity_usd: float,
    open_positions: list[Position],
    new_risk_usd: float,
    limits: RiskLimits,
    realized_pnl_today_usd: float,
    peak_equity_usd: float,
) -> tuple[bool, str]:
    """Every portfolio-level gate a new entry must clear. Returns
    (allowed, reason) -- reason is empty on allow, a short tag on block, so
    the caller can log exactly which limit fired (a repeat of the same
    "why blocked" observability gap the live bot's own gates needed, fixed
    directly here instead of retrofitted later)."""
    if len(open_positions) >= limits.max_concurrent_positions:
        return False, "max_concurrent_positions"

    total_open_risk = sum(p.risk_usd for p in open_positions) + new_risk_usd
    if total_open_risk / equity_usd * 100 > limits.max_total_open_risk_pct:
        return False, "max_total_open_risk_pct"

    if realized_pnl_today_usd < 0 and abs(realized_pnl_today_usd) / equity_usd * 100 >= limits.daily_loss_limit_pct:
        return False, "daily_loss_limit_pct"

    drawdown_pct = (peak_equity_usd - equity_usd) / peak_equity_usd * 100 if peak_equity_usd > 0 else 0
    if drawdown_pct >= limits.total_drawdown_limit_pct:
        return False, "total_drawdown_limit_pct"

    return True, ""
