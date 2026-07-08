"""Direction-aware stop-loss geometry helpers.

J7 (2026-05-14) — six separate places in the codebase had near-identical
direction-aware tightness checks (``new_sl > current_sl`` for LONG vs
``new_sl < current_sl`` for SHORT). The audit observation OBS-12
interpreted a misleading log message as evidence that the check was
direction-blind. The investigation in
``dev_notes/seven_fixes/j7_*`` confirmed the actual six implementations
are correct, but the duplication is itself a defect: any future
contributor copying the wrong half of the pattern would silently
re-introduce the direction-blind bug.

This module is the single source of truth for tightness comparison so
every SL adjustment site shares one tested code path.

The semantics are simple but easy to get backwards under stress:

  LONG / Buy position
    - Entry is BELOW current price (long-from-support).
    - Stop is BELOW the entry initially.
    - A "tighter" stop is a HIGHER price (closer to mark from below)
      because it reduces downside if mark stalls and retreats.

  SHORT / Sell position
    - Entry is ABOVE current price (short-from-resistance).
    - Stop is ABOVE the entry initially.
    - A "tighter" stop is a LOWER price (closer to mark from above)
      because it reduces upside risk if mark stalls and rallies.

The helper accepts both the ``Side`` enum (``Side.BUY`` / ``Side.SELL``)
and the raw string variants (``"Buy"`` / ``"Sell"`` / ``"Long"`` /
``"Short"``) the codebase has historically used. Empty / unknown sides
return ``False`` rather than raising — every caller treats a False
result as "skip this update" which is the safe default.

Investigation: ``dev_notes/seven_fixes/`` agent reports for J7.
"""

from __future__ import annotations

from typing import Any

_LONG_TOKENS = frozenset({"buy", "long"})


def is_long_side(side: Any) -> bool:
    """Return True when ``side`` denotes a long/buy position.

    Tolerates the three representations the codebase has historically
    used:

      * ``Side`` enum (``Side.BUY`` / ``Side.SELL``)
      * Raw enum value string (``"Buy"`` / ``"Sell"``)
      * Legacy long-form string (``"Long"`` / ``"Short"``)
      * Upper / lower case variants

    Unknown / empty values return False so the caller treats them as
    SHORT (the safer default for a missing direction — a SHORT update
    that turns out to be a LONG is silently a no-op for tighten checks;
    the inverse can wrongly allow a looser stop). Callers that need to
    distinguish "missing direction" from "explicit SHORT" should check
    the input themselves before calling.

    Args:
        side: A ``Side`` enum, string, or any object exposing a
            ``.value`` attribute that resolves to one of the recognized
            tokens.

    Returns:
        True iff side is one of ``{Side.BUY, "Buy", "Long",
        "BUY", "LONG", "buy", "long"}``.
    """
    if side is None:
        return False
    # Side enum: .value is "Buy" / "Sell"
    _val = getattr(side, "value", None)
    if isinstance(_val, str):
        return _val.strip().lower() in _LONG_TOKENS
    if isinstance(side, str):
        return side.strip().lower() in _LONG_TOKENS
    return False


def is_tighter_sl(
    side: Any,
    current_sl: float,
    requested_sl: float,
) -> bool:
    """Return True when ``requested_sl`` is strictly tighter than
    ``current_sl`` given the position ``side``.

    Tightness is direction-aware:

      * LONG → tighter means HIGHER price (closer to mark from below).
      * SHORT → tighter means LOWER price (closer to mark from above).

    Equal prices are treated as NOT tighter (strict greater/less-than)
    so an idempotent re-push does not get re-emitted as a tightening.

    A non-positive ``current_sl`` (no current SL set, e.g. 0 or
    sentinel-missing) is treated as "any positive requested SL is
    tighter" because installing a first stop is by definition a
    tightening relative to no stop at all. Callers that prefer to
    treat missing-SL as a separate case should check
    ``current_sl <= 0`` before calling.

    Args:
        side: Position side; accepts the same forms as
            :func:`is_long_side`.
        current_sl: The currently-installed stop-loss price. Pass 0 or
            negative to indicate "no stop currently set".
        requested_sl: The new stop-loss price under consideration.

    Returns:
        True iff installing ``requested_sl`` would tighten the stop
        for this side.
    """
    if requested_sl <= 0:
        return False
    if current_sl <= 0:
        # Installing the first stop is strictly a tightening from
        # "unbounded loss" to a bounded one — treat as tighter.
        return True
    if is_long_side(side):
        return requested_sl > current_sl
    return requested_sl < current_sl
