"""Issue I3 (F-28) — WD_PNL_MISMATCH blocks corrupted commits.

The pre-I3 watchdog's WD_PNL_MISMATCH ERROR was purely advisory.
Post-I3 the watchdog refuses to commit a close record when:
  * pnl_pct == 0 AND entry_price > 0 (the integrity-violation
    condition), AND
  * price_source is NOT one of the authoritative tags

The block re-attempts on the next tick (Bybit's closed-pnl indexer
typically populates in 1-10s). After _PNL_MISMATCH_RETRY_LIMIT
consecutive blocks the watchdog commits anyway via
WD_PNL_MISMATCH_FORCED so the trade is never permanently silenced.

Coverage:
  * Source-pin: block emission registered
  * Source-pin: force-commit after retry exhaustion
  * Source-pin: authoritative source bypasses the block
  * State: _pnl_mismatch_retries counter exists + has correct type
"""

from __future__ import annotations

import re


def _read_watchdog() -> str:
    return open("src/workers/position_watchdog.py").read()


def test_pnl_mismatch_blocked_event_registered() -> None:
    """The new WD_PNL_MISMATCH_BLOCKED emission is in source."""
    src = _read_watchdog()
    assert "WD_PNL_MISMATCH_BLOCKED" in src, (
        "Issue I3: WD_PNL_MISMATCH_BLOCKED emission must exist"
    )


def test_pnl_mismatch_forced_event_registered() -> None:
    """The force-commit-after-retry-exhausted emission is in source."""
    src = _read_watchdog()
    assert "WD_PNL_MISMATCH_FORCED" in src, (
        "Issue I3: WD_PNL_MISMATCH_FORCED emission must exist for "
        "the retry-exhausted force-commit path"
    )


def test_authoritative_source_set_includes_exchange_and_ws() -> None:
    """The block-bypass set covers the documented authoritative sources."""
    src = _read_watchdog()
    # The set is defined locally for the check; assert all three tags present
    assert "exchange_authoritative" in src, "must list exchange_authoritative"
    assert "bybit_ws_authoritative" in src, "must list bybit_ws_authoritative"
    assert "shadow_authoritative" in src, "must list shadow_authoritative"
    # Ensure they're co-located in the same frozenset (block-bypass logic)
    match = re.search(
        r"_AUTHORITATIVE_SOURCES\s*=\s*frozenset\s*\(\s*\{[^}]+\}",
        src, re.DOTALL,
    )
    assert match is not None, (
        "Issue I3: _AUTHORITATIVE_SOURCES frozenset must define the "
        "block-bypass tags as a literal set in _detect_and_record_closes"
    )
    body = match.group(0)
    assert "exchange_authoritative" in body
    assert "bybit_ws_authoritative" in body
    assert "shadow_authoritative" in body


def test_pnl_mismatch_retry_limit_constant_exists() -> None:
    """The retry limit is exposed as a module constant for ops to tune."""
    src = _read_watchdog()
    assert re.search(
        r"^_PNL_MISMATCH_RETRY_LIMIT\s*:\s*int\s*=\s*\d+",
        src, re.MULTILINE,
    ), "Issue I3: _PNL_MISMATCH_RETRY_LIMIT constant must be declared"


def test_pnl_mismatch_retries_state_initialised_in_init() -> None:
    """__init__ declares the per-symbol retry counter dict."""
    src = _read_watchdog()
    assert "self._pnl_mismatch_retries: dict[str, int]" in src, (
        "Issue I3: self._pnl_mismatch_retries must be initialised in __init__"
    )


def test_block_path_skips_on_trade_closed() -> None:
    """The block branch uses `continue` to skip the on_trade_closed call."""
    src = _read_watchdog()
    # Locate the WD_PNL_MISMATCH_BLOCKED emission + immediate continue
    m = re.search(
        r"WD_PNL_MISMATCH_BLOCKED.*?continue",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "Issue I3: WD_PNL_MISMATCH_BLOCKED branch must `continue` "
        "(skip the on_trade_closed call) to actually block the commit"
    )


def test_block_preserves_existing_wd_pnl_mismatch_emission() -> None:
    """The original advisory WD_PNL_MISMATCH log still fires.
    Rule 3 forbids removing the integrity check."""
    src = _read_watchdog()
    # The original log.error( ... WD_PNL_MISMATCH ... ) is still there
    assert re.search(
        r"log\.error\([^)]*WD_PNL_MISMATCH",
        src,
    ), "Issue I3: the original WD_PNL_MISMATCH ERROR log must remain"
