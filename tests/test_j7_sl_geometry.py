"""J7 (2026-05-14) — direction-aware SL geometry helper tests.

Pins the contract for ``src.core.sl_geometry``:

  * ``is_long_side`` accepts Side enum, string variants, and unknown
    values (returns False on unknown for safe-default behaviour).
  * ``is_tighter_sl`` is direction-aware (LONG higher = tighter,
    SHORT lower = tighter).
  * Equal SLs return False (strict inequality; no idempotent re-push).
  * Missing current SL (<= 0) treats any positive requested SL as
    tighter (first-install is structurally a tightening).
  * Non-positive requested SL is never tighter.
"""

from __future__ import annotations

import pytest

from src.core.sl_geometry import is_long_side, is_tighter_sl
from src.core.types import Side


# --- is_long_side --------------------------------------------------


@pytest.mark.parametrize(
    "side,expected",
    [
        (Side.BUY, True),
        (Side.SELL, False),
        ("Buy", True),
        ("Sell", False),
        ("Long", True),
        ("Short", False),
        ("BUY", True),
        ("SELL", False),
        ("buy", True),
        ("sell", False),
        ("  Buy  ", True),
        ("", False),
        (None, False),
        (123, False),
        ("Unknown", False),
    ],
)
def test_is_long_side(side, expected) -> None:
    assert is_long_side(side) is expected


# --- is_tighter_sl: long ---------------------------------------------


def test_long_higher_sl_is_tighter() -> None:
    """LONG: a HIGHER stop is closer to mark (from below) — tighter."""
    assert is_tighter_sl(Side.BUY, current_sl=100.0, requested_sl=101.0) is True


def test_long_lower_sl_is_not_tighter() -> None:
    """LONG: a LOWER stop widens the loss potential — NOT tighter."""
    assert is_tighter_sl(Side.BUY, current_sl=100.0, requested_sl=99.0) is False


def test_long_equal_sl_is_not_tighter() -> None:
    """Strict inequality — same price is not tighter (idempotency
    guard)."""
    assert is_tighter_sl(Side.BUY, current_sl=100.0, requested_sl=100.0) is False


# --- is_tighter_sl: short --------------------------------------------


def test_short_lower_sl_is_tighter() -> None:
    """SHORT: a LOWER stop is closer to mark (from above) — tighter.

    Pins the audit's ATOMUSDT case at 20:41:48:
        entry=2.0373 (Sell), cur_sl=2.0629, req_sl=2.05767.
        2.05767 < 2.0629 → tighter for a SHORT.
    """
    assert is_tighter_sl(Side.SELL, current_sl=2.0629, requested_sl=2.05767) is True


def test_short_higher_sl_is_not_tighter() -> None:
    """SHORT: a HIGHER stop widens the risk — NOT tighter."""
    assert is_tighter_sl(Side.SELL, current_sl=2.0629, requested_sl=2.07) is False


def test_short_equal_sl_is_not_tighter() -> None:
    assert is_tighter_sl(Side.SELL, current_sl=2.0629, requested_sl=2.0629) is False


# --- is_tighter_sl: missing current SL -------------------------------


def test_first_install_long_is_tighter() -> None:
    """No current stop installed (0) — any positive long stop is
    tighter (first-install is a structural tightening from
    unbounded loss)."""
    assert is_tighter_sl(Side.BUY, current_sl=0.0, requested_sl=99.0) is True


def test_first_install_short_is_tighter() -> None:
    assert is_tighter_sl(Side.SELL, current_sl=0.0, requested_sl=101.0) is True


def test_negative_current_treats_as_missing() -> None:
    """Some upstream paths use -1.0 / negative as missing-SL sentinel.
    Helper treats <= 0 as missing."""
    assert is_tighter_sl(Side.BUY, current_sl=-1.0, requested_sl=99.0) is True


# --- is_tighter_sl: invalid requested -------------------------------


def test_zero_requested_is_never_tighter() -> None:
    """Requested SL of 0 means "remove the stop" — never tighter."""
    assert is_tighter_sl(Side.BUY, current_sl=100.0, requested_sl=0.0) is False
    assert is_tighter_sl(Side.SELL, current_sl=100.0, requested_sl=0.0) is False


def test_negative_requested_is_never_tighter() -> None:
    assert is_tighter_sl(Side.BUY, current_sl=100.0, requested_sl=-5.0) is False


# --- string forms work consistently with enum -----------------------


def test_string_side_matches_enum() -> None:
    """Pin: a string side and the equivalent enum produce identical
    decisions. Defends against caller drift."""
    assert is_tighter_sl(Side.BUY, 100.0, 101.0) == is_tighter_sl("Buy", 100.0, 101.0)
    assert is_tighter_sl(Side.SELL, 100.0, 99.0) == is_tighter_sl("Sell", 100.0, 99.0)
    assert is_tighter_sl(Side.BUY, 100.0, 99.0) == is_tighter_sl("Long", 100.0, 99.0)
    assert is_tighter_sl(Side.SELL, 100.0, 99.0) == is_tighter_sl("Short", 100.0, 99.0)


# --- source pin: sentinel uses the helper ---------------------------


def test_sentinel_uses_helper() -> None:
    """Source-pin: the sentinel path in position_watchdog.py imports
    and calls the shared helper. A future refactor that re-introduces
    open-coded direction branching here surfaces immediately."""
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/workers/position_watchdog.py", encoding="utf-8",
    ).read()
    assert "from src.core.sl_geometry import is_long_side, is_tighter_sl" in src
    assert "is_tighter_sl(pos.side, current_sl, new_sl)" in src
    # The legacy "— not tighter" message was misleading; J7 replaced
    # it with reason=not_tighter / reason=micro_profit_block.
    assert "SENTINEL_TIGHTNESS_DIRECTION_AWARE" in src
    assert "reason=micro_profit_block" in src or "_skip_reason = \"micro_profit_block\"" in src
