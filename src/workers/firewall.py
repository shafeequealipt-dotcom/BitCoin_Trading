"""SENTINEL Exit Firewall — blocks strategic review from force-closing positions.

Claude's strategic review can still: open trades, tighten stops, set exits.
It CANNOT: close positions or take_profit positions.

The data proves it: 26/31 wins from natural SL/TP (84% win rate, +$115),
ALL 8 strategic review closes were losses (0% win rate, -$22).
"""

from src.core.logging import get_logger
from src.core.log_context import ctx

log = get_logger("sentinel")

# Actions that strategic review is NOT allowed to perform on existing positions.
# Positions are managed by Watchdog, ProfitSniper, and SL/TP.
_BLOCKED_ACTIONS = frozenset({"close", "take_profit"})


def should_allow_strategic_action(
    action: str, symbol: str, reason: str,
) -> tuple[bool, str]:
    """Determine if a strategic review action should pass through the firewall.

    Args:
        action: The action type from PositionAction
                ("close", "take_profit", "tighten_stop", "set_exit", "hold").
        symbol: The position symbol.
        reason: Claude's reason for the action.

    Returns:
        (allowed, explanation) tuple.
    """
    if action in _BLOCKED_ACTIONS:
        log.warning(
            f"SENTINEL_FIREWALL_BLOCK | sym={symbol} act={action} "
            f"rsn='{reason[:80]}' | Exit managed by Watchdog/ProfitSniper/SL_TP | {ctx()}"
        )
        return (False, f"SENTINEL: strategic review cannot {action} — only SL/TP/timer exits allowed")

    return (True, "allowed")
