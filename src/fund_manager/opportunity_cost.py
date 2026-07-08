"""M13 — Opportunity cost analysis.

Evaluates whether deploying capital to a trade is the best use of that
capital compared to holding cash (earning 0%):

  EV = amount * expected_return_pct * probability
       - amount * (1 - probability) * stop_loss_pct

  If EV < 0, the trade is a negative expected-value proposition
  and the capital is better left idle.
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState  # noqa: F401

log = get_logger("fund_manager")

# Minimum EV threshold — trades must exceed this to be considered "best use"
_MIN_EV_THRESHOLD = 0.0

# Default stop-loss percentage if not provided
_DEFAULT_STOP_LOSS_PCT = 0.02  # 2%


class OpportunityCostCalculator:
    """Analyzes whether a trade is the best use of capital."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}

    # ------------------------------------------------------------------
    # Core EV calculation
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_ev(
        amount: float,
        expected_return_pct: float,
        probability: float,
        stop_loss_pct: float = _DEFAULT_STOP_LOSS_PCT,
    ) -> float:
        """Calculate the expected value of a trade.

        Args:
            amount: Capital to be deployed in USD.
            expected_return_pct: Expected return as a decimal (e.g. 0.03 for 3%).
            probability: Probability of success (0.0 to 1.0).
            stop_loss_pct: Stop-loss as a decimal (e.g. 0.02 for 2%).

        Returns:
            Expected value in USD.
        """
        gain_if_win = amount * expected_return_pct * probability
        loss_if_lose = amount * (1.0 - probability) * stop_loss_pct
        return gain_if_win - loss_if_lose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_best_use(
        self,
        symbol: str,
        amount: float,
        expected_return_pct: float,
        probability: float,
        stop_loss_pct: float = _DEFAULT_STOP_LOSS_PCT,
    ) -> dict:
        """Evaluate whether deploying capital to this trade is the best use.

        Args:
            symbol: Trading pair symbol.
            amount: Capital to deploy in USD.
            expected_return_pct: Expected return percentage as decimal.
            probability: Probability of success (0.0-1.0).
            stop_loss_pct: Stop-loss percentage as decimal.

        Returns:
            Dict with:
              is_best: bool — True if EV > 0 and trade is worthwhile
              expected_value: float — EV in USD
              better_option: str — Description of the better alternative
              ev_per_dollar: float — EV normalized per dollar risked
        """
        ev = self.calculate_ev(amount, expected_return_pct, probability, stop_loss_pct)
        ev_per_dollar = ev / amount if amount > 0 else 0.0

        # Determine if this is the best use
        is_best = ev > _MIN_EV_THRESHOLD

        # Suggest better option if EV is negative
        if is_best:
            better_option = "none"
        elif ev < -amount * 0.01:
            better_option = "hold_cash — EV is significantly negative"
        else:
            better_option = "hold_cash — marginal or negative EV"

        # Check if there might be competing positions with better EV
        alternative_note = ""
        try:
            coordinator = self._services.get("trade_coordinator")
            if coordinator is not None and not is_best:
                active_count = len(coordinator.active_trades)
                if active_count > 0:
                    alternative_note = (
                        f" ({active_count} active trades already using capital)"
                    )
        except Exception:
            pass

        result = {
            "is_best": is_best,
            "expected_value": round(ev, 4),
            "better_option": better_option + alternative_note,
            "ev_per_dollar": round(ev_per_dollar, 6),
            "inputs": {
                "symbol": symbol,
                "amount": amount,
                "expected_return_pct": expected_return_pct,
                "probability": probability,
                "stop_loss_pct": stop_loss_pct,
            },
        }

        log.info(
            "OpportunityCost: symbol={sym}, amount={amt:.2f}, "
            "EV={ev:.2f}, is_best={best}",
            sym=symbol, amt=amount, ev=ev, best=is_best,
        )

        return result
