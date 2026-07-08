"""M3: Capital Reserves — Three-Pool System.

Splits trading capital into three pools:
  - Active (70%): Normal trading operations
  - A+ Reserve (20%): Released only for highest-quality setups
  - Emergency (10%): Contrarian bets during extreme conditions
"""

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import (
    AccountState,
    CapitalPool,
    RiskWeather,
)

log = get_logger("fund_manager")

# Pool allocation percentages
ACTIVE_PCT = 70.0
APLUS_RESERVE_PCT = 20.0
EMERGENCY_PCT = 10.0

# Thresholds for pool routing
APLUS_MIN_SCORE = 80
EMERGENCY_MIN_SCORE = 85


class CapitalReserves:
    """Three-pool capital management system.

    Allocates trading capital across active, A+ reserve, and emergency pools.
    Routes trade setups to the appropriate pool based on quality and conditions.
    """

    def __init__(self, settings=None) -> None:
        self.settings = settings

    def update_pools(self, state: AccountState) -> None:
        """Split trading_capital into three pools and update state.

        Args:
            state: AccountState with trading_capital already calculated.
        """
        capital = state.trading_capital

        state.active_pool = capital * (ACTIVE_PCT / 100.0)
        state.aplus_reserve = capital * (APLUS_RESERVE_PCT / 100.0)
        state.emergency_reserve = capital * (EMERGENCY_PCT / 100.0)

        # Phase 12.9 (lifecycle-logging-audit Gap 9.11-G1): deleted prose
        # duplicate. The downstream FUND_POOLS structured tag (per the
        # workers/fund_manager_worker.py emission, 6,264 firings in
        # current rotation) carries the same fields with grep-friendly
        # `active=` / `aplus=` / `emergency=` keys.

    def get_pool_for_setup(
        self,
        grade: str,
        score: float,
        weather_level: RiskWeather,
        state: AccountState,
    ) -> tuple[CapitalPool, float]:
        """Determine which capital pool to use for a trade setup.

        Returns the pool designation and the total capital available
        from that pool.

        Args:
            grade: Trade quality grade (e.g. "A+", "A", "B").
            score: Setup score (0-100).
            weather_level: Current risk weather level.
            state: Current AccountState with pool balances.

        Returns:
            Tuple of (CapitalPool, available_amount_usd).
        """
        grade_upper = grade.upper().strip()

        # A+ grade with high score → unlock A+ reserve (active + aplus combined)
        if grade_upper == "A+" and score >= APLUS_MIN_SCORE:
            available = state.active_pool + state.aplus_reserve
            log.info(
                "A+ setup detected (score={score}): unlocking reserve pool, "
                "available={available:.2f}",
                score=score,
                available=available,
            )
            return CapitalPool.RESERVE_APLUS, available

        # HURRICANE weather with very high score → contrarian emergency pool
        if weather_level == RiskWeather.HURRICANE and score >= EMERGENCY_MIN_SCORE:
            available = state.active_pool + state.aplus_reserve + state.emergency_reserve
            log.info(
                "HURRICANE contrarian setup (score={score}): unlocking emergency pool, "
                "available={available:.2f}",
                score=score,
                available=available,
            )
            return CapitalPool.EMERGENCY, available

        # Default: active pool only
        available = state.active_pool
        log.debug(
            "Standard setup: using active pool, available={available:.2f}",
            available=available,
        )
        return CapitalPool.ACTIVE, available
