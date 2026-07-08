"""Pre-trade validation: comprehensive checks before any order is placed."""

from src.config.settings import Settings
from src.config.constants import SUPPORTED_SYMBOLS
from src.core.logging import get_logger
from src.core.types import AccountInfo, Position, Side
from src.core.utils import safe_divide
from src.trading.models.instrument import InstrumentInfo

log = get_logger("risk")


class TradeValidator:
    """Validates proposed trades against all risk rules.

    Hard limits cannot be overridden by any configuration.

    Args:
        settings: Application settings.
    """

    ABSOLUTE_MAX_LEVERAGE = 10
    ABSOLUTE_MAX_DAILY_LOSS_PCT = 10.0
    ABSOLUTE_MAX_POSITION_PCT = 25.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_order(self, symbol: str, side: Side, qty: float, price: float | None,
                       stop_loss: float | None, take_profit: float | None, leverage: int,
                       account: AccountInfo, positions: list[Position],
                       instrument: InstrumentInfo | None = None) -> tuple[bool, list[str]]:
        """Run all pre-trade validation checks.

        Returns:
            Tuple of (is_valid, list_of_issues). Empty list = valid.
        """
        issues: list[str] = []
        entry_price = price or 0

        # 1. Symbol
        if symbol not in SUPPORTED_SYMBOLS:
            issues.append(f"Unsupported symbol: {symbol}")

        # 2. Quantity
        if qty <= 0:
            issues.append("Quantity must be positive")
        if instrument:
            if qty < instrument.min_qty:
                issues.append(f"Qty {qty} below min {instrument.min_qty}")
            if qty > instrument.max_qty:
                issues.append(f"Qty {qty} above max {instrument.max_qty}")

        # 3. Notional
        if instrument and entry_price > 0 and instrument.min_notional > 0:
            notional = qty * entry_price
            if notional < instrument.min_notional:
                issues.append(f"Order value ${notional:.2f} below minimum ${instrument.min_notional}")

        # 4. Price
        if price is not None and price <= 0:
            issues.append("Price must be positive")

        # 5. Stop-loss mandatory
        if self.settings.risk.mandatory_stop_loss and stop_loss is None:
            issues.append("Stop-loss is mandatory for all trades")

        # 6. Stop-loss sanity
        if stop_loss is not None and entry_price > 0:
            sl_dist_pct = abs(entry_price - stop_loss) / entry_price * 100
            if side == Side.BUY and stop_loss >= entry_price:
                issues.append("Stop-loss must be below entry for BUY orders")
            elif side == Side.SELL and stop_loss <= entry_price:
                issues.append("Stop-loss must be above entry for SELL orders")
            elif sl_dist_pct < 0.1:
                issues.append(f"Stop-loss too close ({sl_dist_pct:.2f}%) — will get stopped by noise")
            elif sl_dist_pct > 20:
                issues.append(f"Stop-loss too far ({sl_dist_pct:.1f}%) — excessive risk")

        # 7. Take-profit sanity
        if take_profit is not None and entry_price > 0:
            if side == Side.BUY and take_profit <= entry_price:
                issues.append("Take-profit must be above entry for BUY orders")
            elif side == Side.SELL and take_profit >= entry_price:
                issues.append("Take-profit must be below entry for SELL orders")

        # 8. Leverage
        if leverage < 1:
            issues.append("Leverage must be >= 1")
        max_lev = min(self.settings.risk.max_leverage, self.ABSOLUTE_MAX_LEVERAGE)
        if leverage > max_lev:
            issues.append(f"Leverage {leverage}x exceeds max {max_lev}x")

        # 9. Position size
        if entry_price > 0 and account.total_equity > 0:
            pos_pct = (qty * entry_price) / account.total_equity * 100
            max_pos = min(self.settings.risk.max_position_size_pct, self.ABSOLUTE_MAX_POSITION_PCT)
            if pos_pct > max_pos:
                issues.append(f"Position size {pos_pct:.1f}% exceeds max {max_pos}%")

        # 10. Position count
        if len(positions) >= self.settings.risk.max_open_positions:
            existing = [p for p in positions if p.symbol == symbol and p.side == side]
            if not existing:
                issues.append(f"Max open positions ({self.settings.risk.max_open_positions}) reached")

        # 11. Exposure
        if entry_price > 0 and account.total_equity > 0:
            current_exp = sum(abs(p.size * p.mark_price) for p in positions)
            new_exp = current_exp + (qty * entry_price)
            exp_pct = new_exp / account.total_equity * 100
            if exp_pct > self.settings.risk.max_total_exposure_pct:
                issues.append(f"Total exposure {exp_pct:.1f}% would exceed max {self.settings.risk.max_total_exposure_pct}%")

        # 12. Balance
        if entry_price > 0:
            margin = (qty * entry_price) / max(leverage, 1)
            if margin > account.available_balance:
                issues.append(f"Insufficient balance: need ${margin:.2f}, have ${account.available_balance:.2f}")

        # 13. Duplicate
        for p in positions:
            if p.symbol == symbol and p.side == side and p.size > 0:
                issues.append(f"Already have a {side.value} position in {symbol}")
                break

        return len(issues) == 0, issues

    def validate_risk_params(self, settings: Settings) -> list[str]:
        """Validate risk settings themselves are sane."""
        issues = []
        r = settings.risk

        if r.max_position_size_pct < 0.1 or r.max_position_size_pct > self.ABSOLUTE_MAX_POSITION_PCT:
            issues.append(f"max_position_size_pct {r.max_position_size_pct} out of range [0.1, {self.ABSOLUTE_MAX_POSITION_PCT}]")
        if r.daily_loss_limit_pct < 0.5 or r.daily_loss_limit_pct > self.ABSOLUTE_MAX_DAILY_LOSS_PCT:
            issues.append(f"daily_loss_limit_pct {r.daily_loss_limit_pct} out of range [0.5, {self.ABSOLUTE_MAX_DAILY_LOSS_PCT}]")
        if r.max_leverage < 1 or r.max_leverage > self.ABSOLUTE_MAX_LEVERAGE:
            issues.append(f"max_leverage {r.max_leverage} out of range [1, {self.ABSOLUTE_MAX_LEVERAGE}]")
        if r.default_stop_loss_pct < 0.1 or r.default_stop_loss_pct > 20:
            issues.append(f"default_stop_loss_pct {r.default_stop_loss_pct} out of range [0.1, 20]")
        if r.default_take_profit_pct < 0.1 or r.default_take_profit_pct > 50:
            issues.append(f"default_take_profit_pct {r.default_take_profit_pct} out of range [0.1, 50]")
        if r.max_open_positions < 1 or r.max_open_positions > 20:
            issues.append(f"max_open_positions {r.max_open_positions} out of range [1, 20]")

        return issues

    def calculate_required_margin(self, qty: float, price: float, leverage: int) -> float:
        """Calculate margin required for a position."""
        return safe_divide(qty * price, max(leverage, 1), 0)

    def calculate_risk_reward(self, entry_price: float, stop_loss: float, take_profit: float, side: Side) -> float:
        """Calculate risk/reward ratio."""
        if side == Side.BUY:
            risk = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit
        return safe_divide(reward, risk, 0) if risk > 0 else 0
