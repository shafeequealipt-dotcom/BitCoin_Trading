"""Unit tests for CRITICAL-4 (numeric-normalized alert dedup).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md CRITICAL-4.

Pre-fix: AlertThrottle.content_hash hashed the full message text.
Bybit retry alerts (e.g., the audit's KATUSDT 5x SET_SL_FAIL in 28s)
differ in numeric details (base_price 1017000 → 1017100 → ...) so they
produced different hashes, defeating the 5-min dedup window. CRITICAL
priority bypasses the rate gate too, so spam was unbounded.

Fix: new AlertThrottle.normalized_content_hash replaces every float and
integer with "#NUM" before hashing. Tag prefixes, symbol names, and
structural keys stay intact so genuinely-different alerts dedup
correctly. alert_manager._send switched to use the normalized hash.
"""

from __future__ import annotations

import pytest

from src.alerts.throttle import AlertThrottle


# ──────────────────────────────────────────────────────────────────────
# Group 1 — sanity: identical text → identical hash
# ──────────────────────────────────────────────────────────────────────


def test_identical_text_produces_identical_hash() -> None:
    """Baseline sanity: same input → same normalized hash."""
    a = "BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 err=...10001"
    assert (
        AlertThrottle.normalized_content_hash(a)
        == AlertThrottle.normalized_content_hash(a)
    )


# ──────────────────────────────────────────────────────────────────────
# Group 2 — the audit's KATUSDT retry case (the fix's reason for being)
# ──────────────────────────────────────────────────────────────────────


def test_katusdt_retry_pair_now_dedups() -> None:
    """The audit's failure case: KATUSDT 5x SET_SL_FAIL with base_price
    drifting per retry. Pre-fix these produced different content_hashes
    and dedup missed. Post-fix they produce the SAME normalized_hash."""
    msg_a = (
        "BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 "
        "err=BybitAPIError: API error (10001: StopLoss:1015000 set for "
        "Sell position should greater base_price:1017000??LastPr"
    )
    msg_b = (
        "BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 "
        "err=BybitAPIError: API error (10001: StopLoss:1015000 set for "
        "Sell position should greater base_price:1017100??LastPr"
    )
    assert (
        AlertThrottle.normalized_content_hash(msg_a)
        == AlertThrottle.normalized_content_hash(msg_b)
    )


def test_katusdt_5_retry_burst_collapses_to_one_hash() -> None:
    """All 5 audit-window KATUSDT retries should produce the same hash
    so the dedup TTL window catches them as a single event."""
    base_prices = [1017000, 1017100, 1017250, 1017300, 1017400]
    msgs = [
        f"BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569 "
        f"err=API error (10001: should greater base_price:{bp}??LastPr"
        for bp in base_prices
    ]
    hashes = {AlertThrottle.normalized_content_hash(m) for m in msgs}
    assert len(hashes) == 1, f"expected 1 normalized hash; got {len(hashes)}"


# ──────────────────────────────────────────────────────────────────────
# Group 3 — preserve distinction for genuinely different alerts
# ──────────────────────────────────────────────────────────────────────


def test_different_symbols_produce_different_hashes() -> None:
    """KATUSDT and ETHUSDT failures are distinct alerts even with same
    structure. The symbol name (non-numeric) must keep them separate."""
    msg_kat = "BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.010 err=10001"
    msg_eth = "BYBIT_DEMO_SET_SL_FAIL | sym=ETHUSDT sl=0.010 err=10001"
    assert (
        AlertThrottle.normalized_content_hash(msg_kat)
        != AlertThrottle.normalized_content_hash(msg_eth)
    )


def test_different_tags_produce_different_hashes() -> None:
    """SET_SL_FAIL and SET_TP_FAIL for the same symbol are different
    alert classes — must produce different hashes."""
    msg_sl = "BYBIT_DEMO_SET_SL_FAIL | sym=X sl=0.010 err=10001"
    msg_tp = "BYBIT_DEMO_SET_TP_FAIL | sym=X tp=0.010 err=10001"
    assert (
        AlertThrottle.normalized_content_hash(msg_sl)
        != AlertThrottle.normalized_content_hash(msg_tp)
    )


def test_different_error_text_produces_different_hashes() -> None:
    """Two alerts with same prefix but distinct non-numeric error text
    must produce different hashes (genuine distinction preserved)."""
    msg_a = "BYBIT_DEMO_AUTH_FAIL | err='invalid signature' code=10003"
    msg_b = "BYBIT_DEMO_AUTH_FAIL | err='timestamp window expired' code=10004"
    assert (
        AlertThrottle.normalized_content_hash(msg_a)
        != AlertThrottle.normalized_content_hash(msg_b)
    )


# ──────────────────────────────────────────────────────────────────────
# Group 4 — float ordering (regression: integer regex must not pre-eat
# the integer portion of a float)
# ──────────────────────────────────────────────────────────────────────


def test_float_normalization_handles_decimal_anchored_values() -> None:
    """`0.01015569` and `0.01015570` are floats. After normalization
    both should become `#NUM` (NOT `#NUM.#NUM` or `#NUM.0#NUM`).
    Regression guard against integer-first regex order."""
    norm_a = AlertThrottle.normalized_content_hash("sl=0.01015569 done")
    norm_b = AlertThrottle.normalized_content_hash("sl=0.01015570 done")
    assert norm_a == norm_b


def test_scientific_notation_normalized() -> None:
    """Scientific notation (e.g., 1.5e-9) should also normalize."""
    norm_a = AlertThrottle.normalized_content_hash("val=1.5e-9 done")
    norm_b = AlertThrottle.normalized_content_hash("val=2.7e-9 done")
    assert norm_a == norm_b


# ──────────────────────────────────────────────────────────────────────
# Group 5 — back-compat with raw content_hash
# ──────────────────────────────────────────────────────────────────────


def test_raw_content_hash_still_works() -> None:
    """The raw content_hash method is preserved for back-compat. It
    should hash the input verbatim with no normalization."""
    msg = "BYBIT_DEMO_SET_SL_FAIL | sym=KATUSDT sl=0.01015569"
    h = AlertThrottle.content_hash(msg)
    assert isinstance(h, str)
    assert len(h) == 16  # SHA256[:16]
    # Raw differs from normalized when the input has numeric values
    assert h != AlertThrottle.normalized_content_hash(msg)
