"""T2-2 APEX fallback SL fix tests (2026-05-12).

Pre-fix bug (F69, verified BLURUSDT 2026-05-12 12:36):
  - Claude directive: BLURUSDT Sell entry=0.0268, SL=0.0328
  - SL distance from price = (0.0328 - 0.0268) / 0.0268 = 22.6%
  - APEX_TIMEOUT_REGIME → fallback returned the directive UNCHANGED
    (is_fallback=True passes Claude's prices straight through)
  - SLTPValidator with default max_distance_pct=10% rejected the SL as
    "nonsensical" → SLTP_VALIDATE_SKIP → trade silently dropped
  - 1 of 3 strategist trades for cycle 19 lost

Fix: when ``coin_data`` is provided to ``_fallback``, validate Claude's
original SL/TP distance from current price. If beyond the safe
validator cap (9% to leave headroom), substitute a volatility-aware
percentage (recommended_sl_pct × 1.5, capped at 9%) and set
``is_fallback=False`` so layer_manager._apply_apex_optimization
applies the corrected percentages via the live ticker price (which
scales correctly for low-priced coins).

Tests use minimal CoinData stubs to avoid the full APEX assembler
dependency.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@dataclass
class _StubCoinData:
    """Minimal volatility profile stand-in for _fallback's coin_data kwarg.
    Real CoinData has many more fields; the fallback only reads these 3.
    """
    current_price: float
    recommended_sl_pct: float
    recommended_tp_pct: float


def _make_optimizer():
    """Build a TradeOptimizer with stub deps. Only `_fallback` is exercised."""
    from unittest.mock import MagicMock

    from src.apex.optimizer import TradeOptimizer
    settings = MagicMock()
    settings.max_leverage = 10
    settings.min_tp_pct = 0.3
    settings.min_regime_trades_for_fallback = 10
    # Real signature: __init__(qwen_client, assembler, settings)
    return TradeOptimizer(MagicMock(), MagicMock(), settings)


# ── T2-2 unit tests: BLUR replication and validator-cap clamp ────────


def test_t2_2_blurusdt_replication_clamps_sl_to_safe_pct():
    """T2-2 bug-replication: BLUR Sell @ 0.0268 with SL=0.0328 (22.6%)
    must be clamped to a percentage that the SLTPValidator will accept
    (under 10%). Recommended sl_pct=1.5 → override = 1.5*1.5 = 2.25%."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=0.0268,
        recommended_sl_pct=1.5,
        recommended_tp_pct=2.5,
    )
    directive = {
        "symbol": "BLURUSDT",
        "direction": "Sell",
        "stop_loss_price": 0.0328,    # 22.6% from price — invalid
        "take_profit_price": 0.0250,  # 6.7% from price — valid
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "timeout_regime: test", coin_data=coin_data)
    # SL was clamped → is_fallback=False so layer_manager applies the
    # corrected percentage via current price.
    assert fb.is_fallback is False, (
        "T2-2: SL out-of-range should trigger pct override (is_fallback=False)"
    )
    assert fb.sl_pct == 2.25, (
        f"T2-2: sl_pct should be 1.5 * 1.5 = 2.25; got {fb.sl_pct}"
    )
    # TP was within range → preserved unchanged (no override)
    assert fb.tp_pct == 1.5  # placeholder value (TP wasn't overridden, sl_pct was)


def test_t2_2_in_range_original_sl_preserved_as_fallback():
    """T2-2: Claude's SL within validator cap (e.g. 5%) is preserved
    via is_fallback=True (legacy pass-through behaviour)."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=100.0,
        recommended_sl_pct=2.0,
        recommended_tp_pct=3.0,
    )
    directive = {
        "symbol": "BTCUSDT",
        "direction": "Buy",
        "stop_loss_price": 95.0,   # 5% from price — valid
        "take_profit_price": 105.0,  # 5% from price — valid
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    assert fb.is_fallback is True, (
        "T2-2: in-range SL/TP should preserve legacy is_fallback=True"
    )


def test_t2_2_no_coin_data_preserves_legacy_behaviour():
    """T2-2 backward-compat: legacy callers pass coin_data=None and
    receive the unchanged pre-fix behaviour (is_fallback=True regardless
    of original SL distance)."""
    opt = _make_optimizer()
    directive = {
        "symbol": "BLURUSDT",
        "direction": "Sell",
        "stop_loss_price": 0.0328,   # 22.6% from a hypothetical price
        "take_profit_price": 0.0250,
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=None)
    assert fb.is_fallback is True, (
        "T2-2: coin_data=None must preserve legacy is_fallback=True"
    )


def test_t2_2_zero_recommended_sl_falls_back_to_floor():
    """T2-2: when recommended_sl_pct is 0 (cold-start coin, no profile),
    the fallback uses the absolute floor (1.5%)."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=0.0268,
        recommended_sl_pct=0.0,
        recommended_tp_pct=0.0,
    )
    directive = {
        "symbol": "NEWCOIN",
        "direction": "Sell",
        "stop_loss_price": 0.04,  # 49% from price — way invalid
        "take_profit_price": 0.02,
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    assert fb.is_fallback is False
    assert fb.sl_pct == 1.5, (
        f"T2-2: zero recommended_sl_pct should use floor 1.5; got {fb.sl_pct}"
    )


def test_t2_2_pathological_recommended_sl_clamped_to_safe_max():
    """T2-2: even if recommended_sl_pct * 1.5 exceeds the safe validator
    cap (9%), the override is clamped DOWN so the fix can never
    reintroduce the bug it set out to fix."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=100.0,
        recommended_sl_pct=10.0,  # absurd — 10% × 1.5 = 15%
        recommended_tp_pct=2.0,
    )
    directive = {
        "symbol": "PATHOLOGICAL",
        "direction": "Sell",
        "stop_loss_price": 130.0,  # 30% — invalid
        "take_profit_price": 90.0,
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    assert fb.is_fallback is False
    assert fb.sl_pct == 9.0, (
        f"T2-2: override must be clamped to safe_max 9.0; got {fb.sl_pct}"
    )


def test_t2_2_buy_direction_handled_symmetrically():
    """T2-2: Buy direction with low-priced coin — validate the same
    clamp logic applies regardless of direction (the validator's
    distance check is direction-agnostic)."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=0.05,
        recommended_sl_pct=2.0,
        recommended_tp_pct=3.0,
    )
    directive = {
        "symbol": "LOWCOIN",
        "direction": "Buy",
        "stop_loss_price": 0.04,   # 20% below price — invalid
        "take_profit_price": 0.052,  # 4% above — valid
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    assert fb.is_fallback is False
    assert fb.sl_pct == 3.0  # 2.0 * 1.5


def test_t2_2_zero_current_price_skips_clamp():
    """T2-2 defensive: when coin_data.current_price is 0 (cold profile,
    feed lag), the clamp logic is skipped and legacy is_fallback=True
    is preserved. The trade may still be dropped by the validator at
    the next layer, but the fallback path itself does not introduce
    a divide-by-zero."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=0.0,
        recommended_sl_pct=2.0,
        recommended_tp_pct=3.0,
    )
    directive = {
        "symbol": "X",
        "direction": "Sell",
        "stop_loss_price": 100.0,
        "take_profit_price": 80.0,
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    assert fb.is_fallback is True


def test_t2_2_zero_original_sl_skips_sl_clamp():
    """T2-2 defensive: when Claude's directive has stop_loss_price=0
    (rare — strategist is supposed to always provide it), no SL clamp
    is applied. Equivalent to the pre-fix behaviour for that case."""
    opt = _make_optimizer()
    coin_data = _StubCoinData(
        current_price=100.0,
        recommended_sl_pct=2.0,
        recommended_tp_pct=3.0,
    )
    directive = {
        "symbol": "X",
        "direction": "Sell",
        "stop_loss_price": 0,
        "take_profit_price": 130.0,  # 30% — TP invalid → tp clamp fires
        "size_usd": 600,
        "leverage": 3,
    }
    fb = opt._fallback(directive, "test", coin_data=coin_data)
    # SL preserved (0); TP clamped → is_fallback=False
    assert fb.is_fallback is False
    assert fb.tp_pct == 4.5  # recommended_tp_pct (3.0) * 1.5
    # sl_pct stays at default placeholder when SL skip path taken
    assert fb.sl_pct == 2.0


# ── T2-2 contract test: defaults locked ──────────────────────────────


def test_t2_2_constants_locked_against_silent_drift():
    """Locks the T2-2 constants so a future config touch can't reintroduce
    the bug by raising the safe-max above the validator's cap."""
    from src.apex.optimizer import TradeOptimizer
    # safe_max must stay strictly UNDER the SLTPValidator default 10.0%
    assert TradeOptimizer._APEX_FB_VALIDATOR_SAFE_MAX_PCT < 10.0
    assert TradeOptimizer._APEX_FB_VALIDATOR_SAFE_MAX_PCT == 9.0
    # SL absolute floor must be > 0 (a 0 floor would mean "no stop")
    assert TradeOptimizer._APEX_FB_SL_PCT_FLOOR > 0.0
    # The 1.5x multiplier preserves the operator's aggressive aim
    # (wider than the regime baseline, not tighter)
    assert TradeOptimizer._APEX_FB_REC_PCT_MULT >= 1.0
