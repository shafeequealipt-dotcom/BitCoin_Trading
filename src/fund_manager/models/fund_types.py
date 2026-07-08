"""Data models for the Intelligent Fund Manager."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AccountLevel(str, Enum):
    ROOKIE = "rookie"
    PROVEN = "proven"
    VETERAN = "veteran"
    ELITE = "elite"
    MASTER = "master"


class RiskWeather(str, Enum):
    CLEAR = "clear"
    CLOUDY = "cloudy"
    STORMY = "stormy"
    HURRICANE = "hurricane"
    NUCLEAR = "nuclear"


class MarketEmotion(str, Enum):
    PANIC = "panic"
    FEAR = "fear"
    NEUTRAL = "neutral"
    OPTIMISM = "optimism"
    GREED = "greed"
    EUPHORIA = "euphoria"


class CapitalPool(str, Enum):
    ACTIVE = "active"
    RESERVE_APLUS = "reserve_aplus"
    EMERGENCY = "emergency"


class TimeHorizon(str, Enum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


@dataclass
class AccountState:
    total_equity: float = 0.0
    starting_balance: float = 0.0
    level: AccountLevel = AccountLevel.ROOKIE
    unlock_pct: float = 20.0
    trading_capital: float = 0.0
    active_pool: float = 0.0
    aplus_reserve: float = 0.0
    emergency_reserve: float = 0.0
    locked_profits: float = 0.0
    profit_floor: float = 0.0
    in_use: float = 0.0
    # H3 (2026-05-16) — naive position notional sum (size * entry_price,
    # no leverage divisor). Kept ALONGSIDE the canonical leverage-aware
    # ``in_use`` so any caller that explicitly wants raw notional exposure
    # has it without re-deriving the wrong value. Updated in
    # ``FundManager.update_state``.
    in_use_notional: float = 0.0
    available: float = 0.0
    growth_multiplier: float = 1.0
    level_thresholds: dict = field(default_factory=lambda: {
        "proven": 1.5, "veteran": 2.0, "elite": 3.0, "master": 5.0,
    })


@dataclass
class SizingDecision:
    symbol: str
    raw_amount_usd: float = 0.0
    quality_multiplier: float = 1.0
    streak_multiplier: float = 1.0
    pnl_multiplier: float = 1.0
    volatility_multiplier: float = 1.0
    consensus_multiplier: float = 1.0
    correlation_multiplier: float = 1.0
    time_multiplier: float = 1.0
    weather_multiplier: float = 1.0
    emotion_multiplier: float = 1.0
    momentum_multiplier: float = 1.0
    velocity_multiplier: float = 1.0
    final_amount_usd: float = 0.0
    final_leverage: int = 1
    capital_pool_used: CapitalPool = CapitalPool.ACTIVE
    time_horizon: TimeHorizon = TimeHorizon.FAST
    max_loss_usd: float = 0.0
    reasoning: str = ""
    all_multipliers: dict = field(default_factory=dict)

    @property
    def combined_multiplier(self) -> float:
        return (
            self.quality_multiplier * self.streak_multiplier
            * self.pnl_multiplier * self.volatility_multiplier
            * self.consensus_multiplier * self.correlation_multiplier
            * self.time_multiplier * self.weather_multiplier
            * self.emotion_multiplier * self.momentum_multiplier
            * self.velocity_multiplier
        )


@dataclass
class RiskWeatherReport:
    level: RiskWeather = RiskWeather.CLEAR
    score: float = 0.0
    allocation_multiplier: float = 1.0
    max_leverage_override: int = 5
    components: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    updated_at: Optional[datetime] = None


@dataclass
class EcosystemHealth:
    score: int = 50
    diversity_score: int = 0
    concentration_score: int = 0
    correlation_score: int = 0
    win_distribution_score: int = 0
    active_strategies: int = 0
    dominant_strategy_pct: float = 0.0
    avg_correlation: float = 0.0
    profitable_strategies_pct: float = 0.0
    health_status: str = "unknown"
    recommendations: list = field(default_factory=list)


@dataclass
class RecoveryPlan:
    active: bool = False
    deficit_usd: float = 0.0
    target_daily_recovery: float = 0.0
    recovered_so_far: float = 0.0
    days_in_recovery: int = 0
    allowed_strategies: list = field(default_factory=list)
    max_trade_size_pct: float = 3.0
    max_sl_pct: float = 1.5
    target_tp_pct: float = 2.0
    progress_pct: float = 0.0


@dataclass
class CapitalVelocity:
    current_velocity: float = 0.0
    target_velocity: float = 1.5
    status: str = "healthy"
    recommendation: str = ""
