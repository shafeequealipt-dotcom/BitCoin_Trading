"""M22: Fee Optimizer.

Ensures trades are large enough to be profitable after accounting for
exchange commissions. Prevents micro-trades where fees eat all profit.
"""

from src.core.logging import get_logger

log = get_logger("fund_manager")

# ── Fee structure ────────────────────────────────────────────────────────────
# Bybit taker fee: 0.06% per side
COMMISSION_PCT_PER_SIDE = 0.06
# Total round-trip commission (entry + exit)
ROUND_TRIP_COMMISSION_PCT = COMMISSION_PCT_PER_SIDE * 2  # 0.12%

# Minimum trade amount in USD (below this, fees dominate)
MIN_TRADE_USD = 50.0

# Expected profit must exceed fees by this factor
MIN_PROFIT_FEE_RATIO = 3.0


class FeeOptimizer:
    """Trade fee analysis and minimum profitability enforcement."""

    def __init__(self, settings=None) -> None:
        self.settings = settings

    def min_profitable_trade(self, symbol: str) -> float:
        """Return the minimum trade size in USD for this symbol.

        Below this amount, round-trip commissions (0.12%) make the trade
        unprofitable for typical moves.

        Args:
            symbol: Trading pair (currently unused; same fee for all pairs).

        Returns:
            Minimum trade amount in USD.
        """
        # With 0.12% round-trip fees, a $50 trade costs $0.06 in fees
        # That means you need at least $0.06 profit just to break even
        min_amount = MIN_TRADE_USD

        log.debug(
            "Min profitable trade for {symbol}: {amount:.2f} USD "
            "(round-trip fee: {fee}%)",
            symbol=symbol,
            amount=min_amount,
            fee=ROUND_TRIP_COMMISSION_PCT,
        )

        return min_amount

    def is_worth_trading(self, amount_usd: float, expected_pnl_pct: float) -> bool:
        """Check if a trade's expected profit justifies the fees.

        The expected profit must be at least 3x the round-trip fee to be
        considered worthwhile.

        Args:
            amount_usd: Proposed trade size in USD.
            expected_pnl_pct: Expected PnL as a percentage (e.g. 2.0 = 2%).

        Returns:
            True if the trade is worth executing.
        """
        # Check minimum trade size
        if amount_usd < MIN_TRADE_USD:
            log.info(
                "Trade too small: {amount:.2f} USD < minimum {min:.2f} USD",
                amount=amount_usd,
                min=MIN_TRADE_USD,
            )
            return False

        # Calculate fee cost
        fee_cost_pct = ROUND_TRIP_COMMISSION_PCT
        expected_profit_pct = abs(expected_pnl_pct)

        # Expected profit must be at least 3x the fee
        required_profit_pct = fee_cost_pct * MIN_PROFIT_FEE_RATIO

        if expected_profit_pct < required_profit_pct:
            log.info(
                "Trade not worth it: expected {expected:.3f}% profit < "
                "required {required:.3f}% (3x fees of {fees:.3f}%)",
                expected=expected_profit_pct,
                required=required_profit_pct,
                fees=fee_cost_pct,
            )
            return False

        # Calculate actual USD values
        expected_profit_usd = amount_usd * (expected_profit_pct / 100.0)
        fee_cost_usd = amount_usd * (fee_cost_pct / 100.0)

        log.debug(
            "Trade worth it: expected profit {profit:.2f} USD "
            "vs fees {fees:.2f} USD (ratio: {ratio:.1f}x)",
            profit=expected_profit_usd,
            fees=fee_cost_usd,
            ratio=expected_profit_usd / fee_cost_usd if fee_cost_usd > 0 else 0,
        )

        return True

    def get_fee_cost(self, amount_usd: float) -> float:
        """Calculate the round-trip fee cost for a given trade size.

        Args:
            amount_usd: Trade size in USD.

        Returns:
            Total fee cost in USD.
        """
        return amount_usd * (ROUND_TRIP_COMMISSION_PCT / 100.0)

    def get_breakeven_move_pct(self) -> float:
        """Return the minimum price move needed to break even after fees.

        Returns:
            Breakeven move as a percentage.
        """
        return ROUND_TRIP_COMMISSION_PCT
