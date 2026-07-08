"""Position size calculation algorithms: fixed %, ATR-based, Kelly criterion."""

import math

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import Side
from src.core.utils import safe_divide

log = get_logger("risk")


class PositionSizer:
    """Calculates optimal position sizes using multiple strategies.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.max_pos_pct = settings.risk.max_position_size_pct

    def fixed_percentage(self, account_equity: float, risk_pct: float, entry_price: float,
                         stop_loss_price: float, symbol_step_size: float = 0.001) -> dict:
        """Fixed percentage risk model — risk a set % of equity per trade.

        Args:
            account_equity: Total account equity.
            risk_pct: Percentage of equity to risk (e.g. 2.0).
            entry_price: Planned entry price.
            stop_loss_price: Stop-loss price.
            symbol_step_size: Minimum qty increment.

        Returns:
            Position sizing result dict.
        """
        risk_amount = account_equity * (risk_pct / 100)
        price_distance = abs(entry_price - stop_loss_price)
        price_distance_pct = safe_divide(price_distance, entry_price, 0) * 100

        if price_distance <= 0 or entry_price <= 0:
            return {"method": "fixed_percentage", "qty": 0, "qty_usd": 0,
                    "risk_amount_usd": risk_amount, "reason": "Invalid stop distance",
                    "capped": False, "risk_pct": risk_pct, "entry_price": entry_price,
                    "stop_loss_price": stop_loss_price, "stop_distance_pct": 0,
                    "position_pct_of_equity": 0}

        position_size_usd = risk_amount / (price_distance / entry_price)
        max_usd = account_equity * (self.max_pos_pct / 100)
        capped = position_size_usd > max_usd
        if capped:
            position_size_usd = max_usd

        qty = position_size_usd / entry_price
        if symbol_step_size > 0:
            qty = math.floor(qty / symbol_step_size) * symbol_step_size
            qty = round(qty, 8)

        return {
            "method": "fixed_percentage", "qty": qty,
            "qty_usd": round(qty * entry_price, 2),
            "risk_amount_usd": round(risk_amount, 2), "risk_pct": risk_pct,
            "entry_price": entry_price, "stop_loss_price": stop_loss_price,
            "stop_distance_pct": round(price_distance_pct, 2),
            "position_pct_of_equity": round(safe_divide(qty * entry_price, account_equity, 0) * 100, 2),
            "capped": capped,
            "reason": f"Risk ${risk_amount:.2f} on a {price_distance_pct:.1f}% stop = {qty} qty",
        }

    def atr_based(self, account_equity: float, risk_pct: float, entry_price: float,
                  atr_value: float, atr_multiplier: float = 2.0, side: Side = Side.BUY,
                  symbol_step_size: float = 0.001) -> dict:
        """ATR-based volatility-adjusted sizing.

        Args:
            account_equity: Total equity.
            risk_pct: Risk percentage.
            entry_price: Entry price.
            atr_value: Average True Range value.
            atr_multiplier: ATR multiplier for stop distance.
            side: Trade direction.
            symbol_step_size: Qty step.

        Returns:
            Sizing result with calculated stop-loss.
        """
        stop_distance = atr_value * atr_multiplier
        if side == Side.BUY:
            stop_loss = entry_price - stop_distance
        else:
            stop_loss = entry_price + stop_distance

        result = self.fixed_percentage(account_equity, risk_pct, entry_price, stop_loss, symbol_step_size)
        result["method"] = "atr_based"
        result["atr_value"] = atr_value
        result["atr_multiplier"] = atr_multiplier
        result["calculated_stop_loss"] = round(stop_loss, 2)
        return result

    def kelly_criterion(self, account_equity: float, win_rate: float, avg_win: float,
                        avg_loss: float, fraction: float = 0.25) -> dict:
        """Kelly Criterion for optimal bet sizing (uses fractional Kelly for safety).

        Args:
            account_equity: Total equity.
            win_rate: Historical win rate (0-1).
            avg_win: Average winning trade amount.
            avg_loss: Average losing trade amount.
            fraction: Kelly fraction (0.25 = quarter Kelly).

        Returns:
            Kelly sizing result.
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return {
                "method": "kelly_criterion", "full_kelly_pct": 0, "fraction_used": fraction,
                "adjusted_kelly_pct": 0, "position_size_usd": 0,
                "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
                "win_loss_ratio": 0, "capped": False,
                "reason": "Insufficient data for Kelly — using minimum size",
            }

        wl_ratio = avg_win / avg_loss
        kelly_pct = win_rate - ((1 - win_rate) / wl_ratio)
        adjusted = kelly_pct * fraction

        if adjusted <= 0:
            return {
                "method": "kelly_criterion", "full_kelly_pct": round(kelly_pct * 100, 2),
                "fraction_used": fraction, "adjusted_kelly_pct": 0,
                "position_size_usd": 0, "win_rate": win_rate,
                "avg_win": avg_win, "avg_loss": avg_loss,
                "win_loss_ratio": round(wl_ratio, 2), "capped": False,
                "reason": "Kelly is negative — edge is insufficient, do not trade",
            }

        pos_usd = account_equity * adjusted
        max_usd = account_equity * (self.max_pos_pct / 100)
        capped = pos_usd > max_usd
        if capped:
            pos_usd = max_usd

        return {
            "method": "kelly_criterion", "full_kelly_pct": round(kelly_pct * 100, 2),
            "fraction_used": fraction, "adjusted_kelly_pct": round(adjusted * 100, 2),
            "position_size_usd": round(pos_usd, 2), "win_rate": win_rate,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "win_loss_ratio": round(wl_ratio, 2), "capped": capped,
            "reason": f"Kelly suggests {kelly_pct*100:.1f}% — using {fraction} Kelly for safety",
        }

    def fixed_usd(self, amount_usd: float, entry_price: float, symbol_step_size: float = 0.001) -> dict:
        """Fixed USD amount sizing."""
        if entry_price <= 0:
            return {"method": "fixed_usd", "qty": 0, "qty_usd": 0, "entry_price": entry_price}
        qty = amount_usd / entry_price
        if symbol_step_size > 0:
            qty = math.floor(qty / symbol_step_size) * symbol_step_size
            qty = round(qty, 8)
        return {"method": "fixed_usd", "qty": qty, "qty_usd": round(qty * entry_price, 2), "entry_price": entry_price}

    def recommend(self, account_equity: float, risk_pct: float, entry_price: float,
                  stop_loss_price: float | None = None, atr_value: float | None = None,
                  win_rate: float | None = None, avg_win: float | None = None,
                  avg_loss: float | None = None, symbol_step_size: float = 0.001,
                  side: Side = Side.BUY) -> dict:
        """Run all applicable methods and recommend the most conservative."""
        methods = []

        if stop_loss_price:
            methods.append(self.fixed_percentage(account_equity, risk_pct, entry_price, stop_loss_price, symbol_step_size))
        if atr_value:
            methods.append(self.atr_based(account_equity, risk_pct, entry_price, atr_value, 2.0, side, symbol_step_size))
        if win_rate and avg_win and avg_loss:
            methods.append(self.kelly_criterion(account_equity, win_rate, avg_win, avg_loss))

        if not methods:
            default_sl = entry_price * (1 - self.settings.risk.default_stop_loss_pct / 100) if side == Side.BUY else entry_price * (1 + self.settings.risk.default_stop_loss_pct / 100)
            methods.append(self.fixed_percentage(account_equity, risk_pct, entry_price, default_sl, symbol_step_size))

        valid = [m for m in methods if m.get("qty", 0) > 0]
        if not valid:
            return {"recommended_qty": 0, "recommended_usd": 0, "method_used": "none",
                    "all_methods": methods, "reasoning": "No valid sizing method produced a result"}

        best = min(valid, key=lambda m: m.get("qty_usd", m.get("position_size_usd", float("inf"))))
        return {
            "recommended_qty": best.get("qty", 0),
            "recommended_usd": best.get("qty_usd", best.get("position_size_usd", 0)),
            "method_used": best["method"],
            "all_methods": methods,
            "reasoning": best.get("reason", ""),
        }
