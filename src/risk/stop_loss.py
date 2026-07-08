"""Stop-loss and take-profit calculators: fixed %, ATR-based, S/R-based."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import Side
from src.core.utils import format_price, safe_divide

log = get_logger("risk")


class StopLossCalculator:
    """Calculates stop-loss and take-profit levels using multiple methods.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fixed_percentage(self, entry_price: float, side: Side,
                         sl_pct: float | None = None, tp_pct: float | None = None) -> dict:
        """Fixed percentage SL/TP from entry price."""
        sl_pct = sl_pct or self.settings.risk.default_stop_loss_pct
        tp_pct = tp_pct or self.settings.risk.default_take_profit_pct

        if side == Side.BUY:
            sl = entry_price * (1 - sl_pct / 100)
            tp = entry_price * (1 + tp_pct / 100)
        else:
            sl = entry_price * (1 + sl_pct / 100)
            tp = entry_price * (1 - tp_pct / 100)

        rr = safe_divide(tp_pct, sl_pct, 0)
        return {
            "method": "fixed_percentage", "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2), "sl_distance_pct": sl_pct,
            "tp_distance_pct": tp_pct, "risk_reward_ratio": round(rr, 2),
            "side": side.value,
        }

    def atr_based(self, entry_price: float, side: Side, atr_value: float,
                  sl_multiplier: float = 2.0, tp_multiplier: float = 3.0) -> dict:
        """ATR-based SL/TP placement."""
        if side == Side.BUY:
            sl = entry_price - (atr_value * sl_multiplier)
            tp = entry_price + (atr_value * tp_multiplier)
        else:
            sl = entry_price + (atr_value * sl_multiplier)
            tp = entry_price - (atr_value * tp_multiplier)

        sl_pct = abs(entry_price - sl) / entry_price * 100
        tp_pct = abs(tp - entry_price) / entry_price * 100
        rr = safe_divide(tp_pct, sl_pct, 0)

        return {
            "method": "atr_based", "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2), "atr_value": atr_value,
            "sl_multiplier": sl_multiplier, "tp_multiplier": tp_multiplier,
            "sl_distance_pct": round(sl_pct, 2), "tp_distance_pct": round(tp_pct, 2),
            "risk_reward_ratio": round(rr, 2), "side": side.value,
        }

    def support_resistance(self, entry_price: float, side: Side,
                           support_levels: list[float], resistance_levels: list[float],
                           buffer_pct: float = 0.5) -> dict:
        """SL/TP based on support/resistance levels."""
        sl = None
        tp = None

        if side == Side.BUY:
            below = [s for s in support_levels if s < entry_price]
            above = [r for r in resistance_levels if r > entry_price]
            if below:
                sl = max(below) * (1 - buffer_pct / 100)
            if above:
                tp = min(above) * (1 - buffer_pct / 100)
        else:
            above = [r for r in resistance_levels if r > entry_price]
            below = [s for s in support_levels if s < entry_price]
            if above:
                sl = min(above) * (1 + buffer_pct / 100)
            if below:
                tp = max(below) * (1 + buffer_pct / 100)

        # Fallback if levels not found
        if sl is None or tp is None:
            fallback = self.fixed_percentage(entry_price, side)
            sl = sl or fallback["stop_loss"]
            tp = tp or fallback["take_profit"]

        sl_pct = abs(entry_price - sl) / entry_price * 100
        tp_pct = abs(tp - entry_price) / entry_price * 100
        rr = safe_divide(tp_pct, sl_pct, 0)

        return {
            "method": "support_resistance", "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2), "sl_distance_pct": round(sl_pct, 2),
            "tp_distance_pct": round(tp_pct, 2), "risk_reward_ratio": round(rr, 2),
            "side": side.value, "buffer_pct": buffer_pct,
        }

    def trailing_stop(self, entry_price: float, side: Side, trail_pct: float = 2.0) -> dict:
        """Calculate initial trailing stop level."""
        trail_distance = entry_price * (trail_pct / 100)
        if side == Side.BUY:
            initial_stop = entry_price - trail_distance
        else:
            initial_stop = entry_price + trail_distance

        return {
            "method": "trailing_stop", "initial_stop": round(initial_stop, 2),
            "trail_pct": trail_pct, "trail_distance_usd": round(trail_distance, 2),
            "side": side.value,
        }

    def recommend(self, entry_price: float, side: Side, atr_value: float | None = None,
                  support_levels: list[float] | None = None,
                  resistance_levels: list[float] | None = None) -> dict:
        """Run all methods and recommend the best SL/TP combination."""
        methods = [self.fixed_percentage(entry_price, side)]

        if atr_value:
            methods.append(self.atr_based(entry_price, side, atr_value))
        if support_levels and resistance_levels:
            methods.append(self.support_resistance(entry_price, side, support_levels, resistance_levels))

        # Pick tightest SL that's at least 0.5% from entry
        valid_sls = []
        for m in methods:
            sl_pct = m.get("sl_distance_pct", 0)
            if sl_pct >= 0.5:
                valid_sls.append(m)

        if not valid_sls:
            valid_sls = methods

        # Tightest = smallest sl_distance_pct
        best = min(valid_sls, key=lambda m: m.get("sl_distance_pct", 100))

        # Ensure R:R >= 1.5
        if best.get("risk_reward_ratio", 0) < 1.5 and best.get("sl_distance_pct", 0) > 0:
            tp_needed_pct = best["sl_distance_pct"] * 1.5
            if side == Side.BUY:
                best["take_profit"] = round(entry_price * (1 + tp_needed_pct / 100), 2)
            else:
                best["take_profit"] = round(entry_price * (1 - tp_needed_pct / 100), 2)
            best["risk_reward_ratio"] = 1.5
            best["tp_distance_pct"] = round(tp_needed_pct, 2)

        return {
            "recommended_stop_loss": best["stop_loss"],
            "recommended_take_profit": best["take_profit"],
            "risk_reward_ratio": best.get("risk_reward_ratio", 0),
            "method_used": best["method"],
            "all_methods": methods,
            "reasoning": f"{best['method']} SL at ${format_price(best['stop_loss'])} ({best.get('sl_distance_pct', 0):.1f}%)",
        }
