"""Unit tests for `src.core.flip_tp_capper.compute_capped_flip_tp`.

Pure-function tests — no fixtures, no mocks, no I/O. Each test
constructs the inputs explicitly so the math behind each cap branch
is unambiguous.

TP-Volume-Closure fix Phase 1C — 2026-05-07.
"""

from __future__ import annotations

from src.analysis.volatility_profile import CoinVolatilityProfile
from src.config.settings import FlipTPSettings
from src.core.flip_tp_capper import (
    METHOD_DISABLED,
    METHOD_FALLBACK,
    METHOD_HARD_CEILING,
    METHOD_STRUCTURAL_KEPT,
    METHOD_VOLATILITY_CAPPED,
    compute_capped_flip_tp,
)


def _profile(**overrides) -> CoinVolatilityProfile:
    """Build a CoinVolatilityProfile with sensible defaults; override
    via kwargs. Mirrors the live `_BASE_PARAMS` dict for class=high."""
    base = dict(
        symbol="OPUSDT",
        atr_pct_5m=0.46,
        atr_pct_1h=0.50,
        volatility_class="high",
        recommended_tp_pct=3.90,
        recommended_sl_pct=1.80,
        recommended_hold_min=54,
        recommended_strategy="trend_follow",
        regime="trending_up",
        regime_confidence=0.70,
    )
    base.update(overrides)
    return CoinVolatilityProfile(**base)


def _settings(**overrides) -> FlipTPSettings:
    base = dict(
        enabled=True,
        hard_ceiling_pct=5.0,
        fallback_tp_distance_pct=2.0,
        structural_buffer_multiplier=1.0,
    )
    base.update(overrides)
    return FlipTPSettings(**base)


# ---------------------------------------------------------------------------
# Test 1 — structural within vol-aware → keep structural unchanged
# ---------------------------------------------------------------------------


def test_structural_within_vol_aware_kept_unchanged() -> None:
    """1.5% structural target with 3.9% vol-aware cap → keep structural.

    Vol-aware is the soft cap; if structural is already inside it, the
    flip path keeps the structural target so the trade still benefits
    from the structural intent of the XRAY direction recheck.
    """
    price = 0.148
    structural_tp = price * (1 - 0.015)   # 1.5% below = $0.14578 (Sell)

    final_tp, method, telem = compute_capped_flip_tp(
        symbol="OPUSDT",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(),
        settings=_settings(),
    )

    assert method == METHOD_STRUCTURAL_KEPT
    assert final_tp == structural_tp
    assert abs(telem["chosen_dist_pct"] - 1.5) < 0.001
    assert abs(telem["vol_aware_pct"] - 3.9) < 0.001


# ---------------------------------------------------------------------------
# Test 2 — structural exceeds vol-aware * multiplier → cap at vol-aware
# ---------------------------------------------------------------------------


def test_structural_exceeds_vol_aware_caps_at_vol_aware() -> None:
    """15% structural target with 3.9% vol-aware → cap at 3.9%.

    This is the headline case from the live failure: GALAUSDT/ICPUSDT
    flips with structural targets 15-20% from price get capped to the
    volatility-class-aware distance and execute instead of being
    rejected by the SLTPValidator.
    """
    price = 3.00
    structural_tp = price * (1 - 0.20)    # 20% below (Sell short target)

    final_tp, method, telem = compute_capped_flip_tp(
        symbol="ICPUSDT",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(volatility_class="high", recommended_tp_pct=3.90),
        settings=_settings(),
    )

    assert method == METHOD_VOLATILITY_CAPPED
    expected_tp = price * (1.0 - 0.039)   # 3.9% below
    assert abs(final_tp - expected_tp) < 1e-6
    assert abs(telem["chosen_dist_pct"] - 3.9) < 0.001
    assert abs(telem["structural_dist_pct"] - 20.0) < 0.001


# ---------------------------------------------------------------------------
# Test 3 — vol-aware * multiplier exceeds hard ceiling → cap at hard ceiling
# ---------------------------------------------------------------------------


def test_vol_aware_exceeds_hard_ceiling_caps_at_ceiling() -> None:
    """If a high-multiplier on a high-vol profile exceeds the hard
    ceiling, the ceiling wins. Method reflects the ceiling-clamp path."""
    price = 100.0
    structural_tp = price * (1 + 0.20)    # 20% above (Buy long target)

    # 6% vol-aware * 1.5 mult = 9% > 5% hard ceiling → ceiling wins.
    final_tp, method, telem = compute_capped_flip_tp(
        symbol="EXTREMEUSDT",
        direction="Buy",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(volatility_class="extreme", recommended_tp_pct=6.0),
        settings=_settings(structural_buffer_multiplier=1.5, hard_ceiling_pct=5.0),
    )

    assert method == METHOD_HARD_CEILING
    expected_tp = price * (1.0 + 0.05)
    assert abs(final_tp - expected_tp) < 1e-6
    assert abs(telem["chosen_dist_pct"] - 5.0) < 0.001
    assert abs(telem["chosen_cap_pct"] - 5.0) < 0.001
    assert abs(telem["hard_ceiling_pct"] - 5.0) < 0.001


# ---------------------------------------------------------------------------
# Test 4 — no vol_profile → use configured fallback
# ---------------------------------------------------------------------------


def test_missing_vol_profile_uses_fallback() -> None:
    """When the volatility profile is None (new symbol, profiler
    degraded), the configured fallback is the cap. Default 2%."""
    price = 0.5
    structural_tp = price * (1 - 0.15)    # 15% below (Sell)

    final_tp, method, telem = compute_capped_flip_tp(
        symbol="NEWCOIN",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=None,
        settings=_settings(fallback_tp_distance_pct=2.0),
    )

    assert method == METHOD_FALLBACK
    expected_tp = price * (1.0 - 0.02)
    assert abs(final_tp - expected_tp) < 1e-6
    assert abs(telem["chosen_dist_pct"] - 2.0) < 0.001
    assert telem["vol_aware_pct"] == 0.0


# ---------------------------------------------------------------------------
# Test 5 — settings.enabled=False → no-op (disabled)
# ---------------------------------------------------------------------------


def test_disabled_returns_structural_unchanged() -> None:
    """Master switch off → identical to pre-fix behavior."""
    price = 100.0
    structural_tp = price * (1 - 0.20)    # 20% below; would otherwise be capped

    final_tp, method, _ = compute_capped_flip_tp(
        symbol="ANY",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(),
        settings=_settings(enabled=False),
    )

    assert method == METHOD_DISABLED
    assert final_tp == structural_tp


# ---------------------------------------------------------------------------
# Test 6 — direction-of-cap is correct for Buy and Sell
# ---------------------------------------------------------------------------


def test_buy_and_sell_directions_cap_in_correct_direction() -> None:
    """Buy puts the capped TP above price; Sell puts it below."""
    price = 50.0
    high_structural_tp = price * (1 + 0.30)   # 30% above (Buy)
    low_structural_tp = price * (1 - 0.30)    # 30% below (Sell)
    settings = _settings()
    profile = _profile(recommended_tp_pct=3.0)  # cap at 3%

    buy_tp, buy_method, _ = compute_capped_flip_tp(
        symbol="X",
        direction="Buy",
        current_price=price,
        structural_tp=high_structural_tp,
        vol_profile=profile,
        settings=settings,
    )
    sell_tp, sell_method, _ = compute_capped_flip_tp(
        symbol="X",
        direction="Sell",
        current_price=price,
        structural_tp=low_structural_tp,
        vol_profile=profile,
        settings=settings,
    )

    assert buy_method == METHOD_VOLATILITY_CAPPED
    assert sell_method == METHOD_VOLATILITY_CAPPED
    assert buy_tp > price       # Buy TP must be above price
    assert sell_tp < price      # Sell TP must be below price
    assert abs(buy_tp - price * 1.03) < 1e-6
    assert abs(sell_tp - price * 0.97) < 1e-6


# ---------------------------------------------------------------------------
# Test 7 — boundary: structural exactly at cap → kept (not capped)
# ---------------------------------------------------------------------------


def test_structural_exactly_at_cap_is_kept() -> None:
    """When structural equals the cap, structural is kept (no penalty).

    `<=` boundary semantic means a 3.9% structural with a 3.9% cap is
    counted as "within" and preserved.
    """
    price = 100.0
    structural_tp = price * (1 - 0.039)   # exactly 3.9% below
    final_tp, method, telem = compute_capped_flip_tp(
        symbol="X",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(recommended_tp_pct=3.9),
        settings=_settings(),
    )

    assert method == METHOD_STRUCTURAL_KEPT
    assert final_tp == structural_tp
    assert abs(telem["structural_dist_pct"] - 3.9) < 0.001
    assert abs(telem["chosen_cap_pct"] - 3.9) < 0.001


# ---------------------------------------------------------------------------
# Test 8 — structural_buffer_multiplier > 1 enlarges the soft cap
# ---------------------------------------------------------------------------


def test_buffer_multiplier_extends_vol_aware_cap() -> None:
    """multiplier=1.5 lets a structural target up to 1.5x vol-aware
    pass through unmodified, exercising the operator-tunable knob.
    """
    price = 100.0
    structural_tp = price * (1 - 0.045)   # 4.5% — between 3% (raw) and 4.5% cap
    final_tp, method, telem = compute_capped_flip_tp(
        symbol="X",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(recommended_tp_pct=3.0),
        settings=_settings(structural_buffer_multiplier=1.5),
    )

    # raw vol_aware=3.0, mult=1.5 → vol_aware_capped_pct=4.5%, hard ceiling 5%.
    # structural_dist_pct=4.5 → exactly at cap → kept.
    assert method == METHOD_STRUCTURAL_KEPT
    assert final_tp == structural_tp
    assert abs(telem["vol_aware_capped_pct"] - 4.5) < 0.001


# ---------------------------------------------------------------------------
# Test 9 — vol_profile present but recommended_tp_pct == 0 → fallback path
# ---------------------------------------------------------------------------


def test_vol_profile_with_zero_tp_pct_falls_back() -> None:
    """A degraded volatility profile that returns 0% recommended TP is
    treated identically to a missing profile — fall back to the
    configured fallback_tp_distance_pct.

    Why this can happen: the profiler's _compute path can theoretically
    produce a 0 if all per-class params and regime mods land on a 0
    multiplier, OR if a future operator zeroes a class to disable
    trading on it without removing it from the dict. Either way the
    cap must not divide by zero or produce a 0% TP.
    """
    price = 100.0
    structural_tp = price * (1 - 0.15)    # 15% below — cap should kick in
    final_tp, method, telem = compute_capped_flip_tp(
        symbol="DEAD",
        direction="Sell",
        current_price=price,
        structural_tp=structural_tp,
        vol_profile=_profile(volatility_class="dead", recommended_tp_pct=0.0),
        settings=_settings(fallback_tp_distance_pct=2.0),
    )

    assert method == METHOD_FALLBACK
    expected_tp = price * (1 - 0.02)
    assert abs(final_tp - expected_tp) < 1e-6
    assert abs(telem["chosen_dist_pct"] - 2.0) < 0.001
    # vol_aware_pct telemetry is the raw 0.0 from the profile so log
    # readers can see "profile was present but useless".
    assert telem["vol_aware_pct"] == 0.0


# ---------------------------------------------------------------------------
# Test 10 — invalid prices (current_price <= 0 or structural_tp <= 0)
# ---------------------------------------------------------------------------


def test_invalid_prices_return_unchanged_no_div_by_zero() -> None:
    """Defensive: when current_price or structural_tp is zero/negative,
    the helper returns structural unchanged without dividing by zero.
    The downstream SLTPValidator at sl_tp_validator.py:108-117 catches
    the same condition with `if current_price <= 0 or tp_price <= 0:
    return "SKIP"`, so the trade is rejected anyway — but the helper
    must not crash before the validator gets a chance to see it.
    """
    profile = _profile()
    settings = _settings()

    # Zero current_price
    final_tp, method, _ = compute_capped_flip_tp(
        symbol="X", direction="Sell", current_price=0.0,
        structural_tp=99.0, vol_profile=profile, settings=settings,
    )
    assert final_tp == 99.0
    assert method == METHOD_STRUCTURAL_KEPT

    # Zero structural_tp
    final_tp, method, _ = compute_capped_flip_tp(
        symbol="X", direction="Sell", current_price=100.0,
        structural_tp=0.0, vol_profile=profile, settings=settings,
    )
    assert final_tp == 0.0
    assert method == METHOD_STRUCTURAL_KEPT

    # Negative current_price
    final_tp, method, _ = compute_capped_flip_tp(
        symbol="X", direction="Sell", current_price=-1.0,
        structural_tp=99.0, vol_profile=profile, settings=settings,
    )
    assert final_tp == 99.0
    assert method == METHOD_STRUCTURAL_KEPT
