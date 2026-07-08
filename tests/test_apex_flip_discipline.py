"""Definitive-fix Phase 9 — APEX flip discipline.

Two surgical tests for the new ``_enforce_flip_confidence`` helper:
1. Ranging regime + low confidence → flip blocked.
2. Ranging regime + high confidence → flip allowed.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.apex.optimizer import TradeOptimizer
from src.config.settings import APEXSettings


@dataclass
class _Optimized:
    direction: str
    confidence: float
    was_flipped: bool = True
    position_size_usd: float = 600.0
    original_size: float = 600.0


def _make_optimizer(min_flip_conf: float = 0.90) -> TradeOptimizer:
    """Build an optimizer with a uniform flip threshold across all
    direction pairs. Use this when the test is exercising the gate
    contract independent of the asymmetric-threshold feature added by
    the PRIMARY Sell-Bias Fix (2026-05-11).
    """
    cfg = APEXSettings(
        apex_min_flip_confidence=min_flip_conf,
        apex_min_flip_confidence_buy_to_sell=min_flip_conf,
        apex_min_flip_confidence_sell_to_buy=min_flip_conf,
    )
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    return opt


def test_phase9_low_conf_flip_in_ranging_blocked() -> None:
    """conf=0.70 < 0.90 → flip is reverted."""
    opt = _make_optimizer(min_flip_conf=0.90)
    optimized = _Optimized(direction="Buy", confidence=0.70)
    revert, reason = opt._enforce_flip_confidence(optimized, "Sell", "ranging")
    assert revert is True
    assert "0.70" in reason and "0.90" in reason


def test_phase9_high_conf_flip_in_ranging_allowed() -> None:
    """conf=0.95 >= 0.90 → flip is allowed."""
    opt = _make_optimizer(min_flip_conf=0.90)
    optimized = _Optimized(direction="Buy", confidence=0.95)
    revert, _ = opt._enforce_flip_confidence(optimized, "Sell", "ranging")
    assert revert is False


def test_phase9_no_flip_does_nothing() -> None:
    """Same direction in/out → not a flip → no revert."""
    opt = _make_optimizer(min_flip_conf=0.90)
    optimized = _Optimized(direction="Sell", confidence=0.10)
    revert, _ = opt._enforce_flip_confidence(optimized, "Sell", "ranging")
    assert revert is False


def test_phase9_trending_regime_skipped() -> None:
    """Trending regime is governed by the pre-call lock; this gate is a no-op."""
    opt = _make_optimizer(min_flip_conf=0.90)
    optimized = _Optimized(direction="Buy", confidence=0.50)
    revert, _ = opt._enforce_flip_confidence(optimized, "Sell", "trending_up")
    assert revert is False


# ─── PRIMARY Sell-Bias Fix (2026-05-11) — asymmetric flip thresholds ──────
# These tests cover the new ``_resolve_flip_threshold`` helper and the
# direction-pair behaviour of ``_enforce_flip_confidence``. Defaults
# encode the operator-chosen HEAVY tune: Buy→Sell needs 0.95, Sell→Buy
# keeps 0.70. Backed by P.1.8 flip-vs-unflip performance data.


def test_asymmetric_defaults_match_operator_choice() -> None:
    cfg = APEXSettings()
    assert cfg.apex_min_flip_confidence_buy_to_sell == 0.95
    assert cfg.apex_min_flip_confidence_sell_to_buy == 0.70
    # Legacy field preserved for back-compat tests / configs.
    assert cfg.apex_min_flip_confidence == 0.70


def test_buy_to_sell_uses_higher_threshold() -> None:
    """Buy→Sell flip at conf=0.85 is BELOW the 0.95 threshold — reverted."""
    cfg = APEXSettings(
        apex_min_flip_confidence_buy_to_sell=0.95,
        apex_min_flip_confidence_sell_to_buy=0.70,
    )
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    optimized = _Optimized(direction="Sell", confidence=0.85)
    revert, reason = opt._enforce_flip_confidence(optimized, "Buy", "ranging")
    assert revert is True
    assert "0.95" in reason  # the threshold appears in the reason


def test_sell_to_buy_uses_lower_threshold() -> None:
    """Sell→Buy flip at conf=0.75 is ABOVE the 0.70 threshold — allowed.

    Same confidence value (0.75) would still be BELOW the 0.95 floor
    for Buy→Sell — asymmetry verified.
    """
    cfg = APEXSettings(
        apex_min_flip_confidence_buy_to_sell=0.95,
        apex_min_flip_confidence_sell_to_buy=0.70,
    )
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    optimized = _Optimized(direction="Buy", confidence=0.75)
    revert, _ = opt._enforce_flip_confidence(optimized, "Sell", "ranging")
    assert revert is False


def test_resolve_threshold_buy_to_sell() -> None:
    """Direct test of the resolver helper."""
    cfg = APEXSettings(
        apex_min_flip_confidence=0.70,
        apex_min_flip_confidence_buy_to_sell=0.95,
        apex_min_flip_confidence_sell_to_buy=0.70,
    )
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    assert opt._resolve_flip_threshold("Buy", "Sell") == 0.95
    assert opt._resolve_flip_threshold("Sell", "Buy") == 0.70
    # Defensive: unknown direction labels fall back to the legacy floor.
    assert opt._resolve_flip_threshold("Long", "Short") == 0.70


# ─── Post-Execution Closure Fix Phase 2 (2026-05-05) ──────────────────────
# Three tests for ``_apply_flip_resize_policy`` — the helper that decides
# whether Qwen's flip-direction resize is honored (smaller) or capped
# (larger) versus Claude's original size. Replaces the prior unconditional
# force-back-to-original behavior that gave lower-conviction flips
# full-conviction risk allocation.


def test_phase2_flip_with_smaller_qwen_size_accepted() -> None:
    """Qwen's smaller size on a flip is honored (de-risks the lower-
    conviction direction change). No mutation should occur.
    """
    opt = _make_optimizer()
    optimized = _Optimized(
        direction="Sell", confidence=0.95,
        was_flipped=True, position_size_usd=200.0, original_size=400.0,
    )
    opt._apply_flip_resize_policy(
        optimized, claude_direction="Buy",
        regime="ranging", symbol="ARBUSDT",
    )
    assert optimized.position_size_usd == 200.0  # unchanged


def test_phase2_flip_with_larger_qwen_size_capped() -> None:
    """Qwen attempting to UPSIZE on a flip is capped back to Claude's
    original size. The lower-conviction direction change should not
    inherit higher risk allocation.
    """
    opt = _make_optimizer()
    optimized = _Optimized(
        direction="Sell", confidence=0.95,
        was_flipped=True, position_size_usd=600.0, original_size=400.0,
    )
    opt._apply_flip_resize_policy(
        optimized, claude_direction="Buy",
        regime="ranging", symbol="ARBUSDT",
    )
    assert optimized.position_size_usd == 400.0  # capped to original


def test_phase2_flip_with_equal_qwen_size_unchanged() -> None:
    """Qwen sizing equal to original is a no-op — neither accepted nor
    capped. The within-tolerance branch short-circuits cleanly.
    """
    opt = _make_optimizer()
    optimized = _Optimized(
        direction="Sell", confidence=0.95,
        was_flipped=True, position_size_usd=400.0, original_size=400.0,
    )
    opt._apply_flip_resize_policy(
        optimized, claude_direction="Buy",
        regime="ranging", symbol="ARBUSDT",
    )
    assert optimized.position_size_usd == 400.0


def test_phase2_zero_original_size_is_no_op() -> None:
    """Defensive: when original_size is 0 (missing/uninitialized), the
    helper returns silently without touching position_size_usd. The
    inline guard at lines 282-283 of optimizer.py preserves this.
    """
    opt = _make_optimizer()
    optimized = _Optimized(
        direction="Sell", confidence=0.95,
        was_flipped=True, position_size_usd=200.0, original_size=0.0,
    )
    opt._apply_flip_resize_policy(
        optimized, claude_direction="Buy",
        regime="ranging", symbol="ARBUSDT",
    )
    assert optimized.position_size_usd == 200.0  # unchanged
