"""M19 — Strategic small-loss harvesting.

Identifies positions with small unrealised losses (-0.3% to -0.8%)
that are old enough and whose thesis appears broken, so they can be
closed early rather than letting them drift to the full stop-loss.

Criteria for harvesting:
  1. PnL between -0.3% and -0.8%
  2. Position age >= threshold (varies by category)
  3. Not improving (not anti-fragile or mean-reverting)
"""

from __future__ import annotations

import time

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState  # noqa: F401

log = get_logger("fund_manager")

# ── Harvest thresholds ─────────────────────────────────────────────
_MIN_LOSS_PCT = -0.8  # Maximum loss for harvesting (more negative = bigger loss)
_MAX_LOSS_PCT = -0.3  # Minimum loss for harvesting (must be at least -0.3%)

# Minimum age in minutes before harvesting, by category
_MIN_AGE_MINUTES: dict[str, int] = {
    "scalping": 15,
    "momentum": 30,
    "mean_reversion": 45,
    "funding_arb": 120,
    "sentiment": 30,
    "advanced": 30,
    "predatory": 20,
    "microstructure": 20,
    "time_based": 30,
    "cross_market": 30,
    "ai_enhanced": 30,
}
_DEFAULT_MIN_AGE = 30  # minutes

# Categories that should NOT be harvested (they expect temporary drawdowns)
_EXEMPT_CATEGORIES: set[str] = {"mean_reversion", "funding_arb"}


class LossHarvester:
    """Identifies positions suitable for strategic small-loss harvesting."""

    def __init__(self, settings=None, db=None, services: dict | None = None) -> None:
        self._settings = settings
        self._db = db
        self._services = services or {}

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    @staticmethod
    def should_harvest(
        pnl_pct: float,
        age_minutes: float,
        strategy_category: str = "default",
    ) -> bool:
        """Determine whether a position should be harvested.

        Args:
            pnl_pct: Current unrealised PnL percentage (negative = loss).
            age_minutes: How long the position has been open in minutes.
            strategy_category: The strategy's category.

        Returns:
            True if the position should be closed for a small loss.
        """
        # Exempt categories
        if strategy_category in _EXEMPT_CATEGORIES:
            return False

        # Must be in the harvest loss range
        if not (_MIN_LOSS_PCT <= pnl_pct <= _MAX_LOSS_PCT):
            return False

        # Must be old enough
        min_age = _MIN_AGE_MINUTES.get(strategy_category, _DEFAULT_MIN_AGE)
        if age_minutes < min_age:
            return False

        return True

    # ------------------------------------------------------------------
    # Candidate discovery
    # ------------------------------------------------------------------

    async def get_harvest_candidates(self) -> list[dict]:
        """Find positions that are candidates for loss harvesting.

        Returns:
            List of dicts with position details and harvest reasoning.
            Each dict has keys: symbol, side, pnl_pct, age_minutes,
            strategy_category, entry_price, mark_price, reason.
        """
        candidates: list[dict] = []

        try:
            position_svc = self._services.get("position")
            coordinator = self._services.get("trade_coordinator")

            if position_svc is None:
                log.debug("LossHarvester: position service unavailable")
                return candidates

            positions = await position_svc.get_positions()
            now = time.time()

            for pos in positions:
                # Calculate PnL percentage
                if pos.entry_price <= 0:
                    continue

                side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                if side_val.lower() in ("buy", "long"):
                    pnl_pct = ((pos.mark_price - pos.entry_price) / pos.entry_price) * 100
                else:
                    pnl_pct = ((pos.entry_price - pos.mark_price) / pos.entry_price) * 100

                # Skip if not in loss range
                if not (_MIN_LOSS_PCT <= pnl_pct <= _MAX_LOSS_PCT):
                    continue

                # Get strategy category from trade coordinator
                strategy_category = "default"
                age_minutes = 0.0

                if coordinator is not None:
                    try:
                        trade_state = coordinator.get_state(pos.symbol)
                        if trade_state is not None:
                            strategy_category = trade_state.strategy_category
                            age_minutes = (now - trade_state.opened_at) / 60.0
                    except Exception:
                        pass

                # If no coordinator data, estimate age from position update time
                if age_minutes <= 0 and hasattr(pos, "updated_at") and pos.updated_at:
                    try:
                        age_seconds = (
                            time.time()
                            - pos.updated_at.timestamp()
                        )
                        age_minutes = max(0.0, age_seconds / 60.0)
                    except Exception:
                        age_minutes = 0.0

                # Check harvest criteria
                if not self.should_harvest(pnl_pct, age_minutes, strategy_category):
                    continue

                candidate = {
                    "symbol": pos.symbol,
                    "side": side_val,
                    "pnl_pct": round(pnl_pct, 3),
                    "age_minutes": round(age_minutes, 1),
                    "strategy_category": strategy_category,
                    "entry_price": pos.entry_price,
                    "mark_price": pos.mark_price,
                    "reason": (
                        f"Small loss ({pnl_pct:.2f}%) persisting for "
                        f"{age_minutes:.0f}min — thesis likely broken"
                    ),
                }
                candidates.append(candidate)

                log.info(
                    "LossHarvester: candidate {sym} pnl={pnl:.2f}% "
                    "age={age:.0f}min cat={cat}",
                    sym=pos.symbol, pnl=pnl_pct,
                    age=age_minutes, cat=strategy_category,
                )

        except Exception:
            log.warning("LossHarvester: error scanning for harvest candidates")

        return candidates
