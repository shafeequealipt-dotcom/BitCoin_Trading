"""SENTINEL Exit Firewall — blocks strategic review from force-closing positions.

Claude's strategic review can still: open trades, tighten stops, set exits.
It CANNOT: close positions or take_profit positions.

The data proves it: 26/31 wins from natural SL/TP (84% win rate, +$115),
ALL 8 strategic review closes were losses (0% win rate, -$22).

Trusted sources bypass the firewall. "call_b" (position review cycle) and
"call_a_urgent" (urgent watchdog-driven position actions from Call A) are
both Claude-decided — they review positions with full context (PnL, regime,
SL consumed, thesis validity) and their close decisions are respected.
Any other source (default "strategic_review") remains subject to _BLOCKED_ACTIONS.

T1-1 / F18 phantom-close defense (six-tier-fixes 2026-05-11): when the
caller passes ``active_symbols``, close / take_profit actions on symbols
that are NOT currently in the active-trades set are rejected at this
layer with a structured ``PHANTOM_CLOSE_REJECTED`` log line — even when
the source is in ``_TRUSTED_SOURCES``. The trusted-source bypass is
preserved for every other action (tighten_stop, set_exit, hold). This
satisfies the prompt's "three layers must have the guard" constraint
together with the coordinator and layer_manager checks.
"""

from src.core.logging import get_logger
from src.core.log_context import ctx

log = get_logger("sentinel")

# Actions that strategic review is NOT allowed to perform on existing positions.
# Positions are managed by Watchdog, ProfitSniper, and SL/TP.
_BLOCKED_ACTIONS = frozenset({"close", "take_profit"})

# Sources that bypass _BLOCKED_ACTIONS entirely. These are Claude-decided
# position management paths with full context; their close/take_profit
# decisions are trusted and propagated to the coordinator queue.
_TRUSTED_SOURCES = frozenset({"call_b", "call_a_urgent"})


def should_allow_strategic_action(
    action: str,
    symbol: str,
    reason: str,
    source: str = "strategic_review",
    active_symbols: frozenset[str] | None = None,
) -> tuple[bool, str]:
    """Determine if a strategic review action should pass through the firewall.

    Args:
        action: The action type from PositionAction
                ("close", "take_profit", "tighten_stop", "set_exit", "hold").
        symbol: The position symbol.
        reason: Claude's reason for the action.
        source: Origin tag. Trusted sources pass the firewall unconditionally:
                "call_b"         — Call B position review (intelligent manager).
                "call_a_urgent"  — Call A urgent position actions (watchdog-driven).
                Any other value (default "strategic_review") is subject to the
                _BLOCKED_ACTIONS guard. Legacy callers that omit source keep
                the pre-change behavior.
        active_symbols: Optional frozenset of symbols that currently have
                an active TradeState in the coordinator. When supplied,
                close / take_profit actions on symbols NOT in this set
                are rejected with ``PHANTOM_CLOSE_REJECTED`` regardless
                of source. When None (legacy callers), the precondition
                is skipped — preserving prior behaviour for paths that
                have not yet plumbed the active-symbol context.

    Returns:
        (allowed, explanation) tuple.
    """
    # T1-1 / F18 phantom-close defense — applies BEFORE the trusted-source
    # bypass so call_b / call_a_urgent cannot close a position that is no
    # longer in the active set. Tighten_stop and set_exit continue to flow
    # through the trusted-source path unchanged.
    if (
        active_symbols is not None
        and action in _BLOCKED_ACTIONS
        and symbol not in active_symbols
    ):
        log.warning(
            f"PHANTOM_CLOSE_REJECTED | layer=firewall sym={symbol} "
            f"act={action} src={source} rsn='{reason[:80]}' | {ctx()}"
        )
        return (
            False,
            f"PHANTOM_CLOSE_REJECTED: {symbol} not in active trades",
        )

    if source in _TRUSTED_SOURCES:
        log.info(
            f"SENTINEL_FIREWALL_ALLOW | sym={symbol} act={action} "
            f"src={source} rsn='{reason[:80]}' | {ctx()}"
        )
        return (True, f"allowed: trusted source {source}")

    if action in _BLOCKED_ACTIONS:
        log.warning(
            f"SENTINEL_FIREWALL_BLOCK | sym={symbol} act={action} "
            f"src={source} rsn='{reason[:80]}' | "
            f"Exit managed by Watchdog/ProfitSniper/SL_TP | {ctx()}"
        )
        return (False, f"SENTINEL: strategic review cannot {action} — only SL/TP/timer exits allowed")

    return (True, "allowed")
