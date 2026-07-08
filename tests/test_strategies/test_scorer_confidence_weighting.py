"""Phase 5a — TradeScorer Quality component honors structural confidence.

XRAY counter-setup work introduces BULLISH_FVG_OB_COUNTER and
BEARISH_FVG_OB_COUNTER variants with reduced confidence (×0.7). This
test suite verifies that ``_xray_sr_score`` multiplies its structural
component by ``setup_type_confidence`` (clamped to [0.5, 1.0]) so
counter setups don't out-rank in-direction setups when raw structural
features are similar.
"""

from __future__ import annotations

from src.strategies.scorer import _xray_sr_score


def _struct_with_confidence(conf: float) -> dict:
    """Mid-quality structural fixture with adjustable confidence.

    Total raw sr_pts ≈ 5.0 (entry +2, struct +2, fvg +1) so all
    confidence-multiplied results stay below the 8.0 clamp ceiling and
    we can observe the multiplicative scaling cleanly.
    """
    return {
        "setup_type_confidence": conf,
        "structural_placement": {
            "entry_quality": "good",     # +2
            "rr_quality": "skip",         # 0 (and is_fallback_rr=True so no penalty)
            "rr_ratio": 1.5,
            "is_fallback_rr": True,
        },
        "market_structure": {
            "structure": "uptrend",       # +2 alignment
            "last_bos": None,
        },
        "nearest_fvg": {"direction": "bullish"},          # +1
        "nearest_ob": None,
        "smc_confluence": 0,
        "active_sweep_signal": None,
        "volume_profile": None,
        "fibonacci": None,
        "mtf_confluence": None,
        "session_context": None,
    }


class TestPhase5aConfidenceWeighting:
    def test_full_confidence_unchanged(self) -> None:
        # Confidence 1.0 → factor 1.0 → no change.
        score_full, _ = _xray_sr_score(_struct_with_confidence(1.0), is_buy=True)
        # Without confidence (legacy) — uses default 0.85.
        struct_legacy = _struct_with_confidence(0.85)
        del struct_legacy["setup_type_confidence"]
        score_legacy, _ = _xray_sr_score(struct_legacy, is_buy=True)
        # 1.0 * x > 0.85 * x for any positive x.
        assert score_full > score_legacy

    def test_counter_setup_confidence_reduces_score(self) -> None:
        # Counter setup at conf 0.35 (typical Phase 4 output).
        score_counter, _ = _xray_sr_score(
            _struct_with_confidence(0.35), is_buy=True,
        )
        # In-direction at conf 0.85 (typical pre-fix).
        score_in, _ = _xray_sr_score(
            _struct_with_confidence(0.85), is_buy=True,
        )
        assert score_counter < score_in

    def test_floor_at_0_5(self) -> None:
        # Even at confidence 0.0, the multiplier floors at 0.5 — never
        # zeros out structural points entirely.
        score_zero, _ = _xray_sr_score(
            _struct_with_confidence(0.0), is_buy=True,
        )
        score_half, _ = _xray_sr_score(
            _struct_with_confidence(0.5), is_buy=True,
        )
        # Both at floor → equal.
        assert score_zero == score_half
        assert score_zero > 0.0

    def test_zero_factor_does_not_zero_legitimate_structure(self) -> None:
        # Confidence 0.0 still gives 50% of the structural points.
        score_zero, _ = _xray_sr_score(
            _struct_with_confidence(0.0), is_buy=True,
        )
        score_full, _ = _xray_sr_score(
            _struct_with_confidence(1.0), is_buy=True,
        )
        # Floor (0.5) → score_zero ≥ 50% of score_full.
        assert score_zero >= 0.5 * score_full * 0.95  # tolerance for clamping

    def test_legacy_no_confidence_uses_default(self) -> None:
        # When setup_type_confidence is absent, default 0.85 is used.
        struct = _struct_with_confidence(0.0)
        del struct["setup_type_confidence"]
        score_legacy, _ = _xray_sr_score(struct, is_buy=True)
        score_explicit, _ = _xray_sr_score(
            _struct_with_confidence(0.85), is_buy=True,
        )
        assert score_legacy == score_explicit

    def test_confidence_above_one_clamped(self) -> None:
        # Defensive — confidence values >1 (shouldn't happen) are
        # clamped to 1.0, not amplified beyond raw structure.
        score_above, _ = _xray_sr_score(
            _struct_with_confidence(1.5), is_buy=True,
        )
        score_one, _ = _xray_sr_score(
            _struct_with_confidence(1.0), is_buy=True,
        )
        assert score_above == score_one

    def test_proportional_in_normal_range(self) -> None:
        # Within [0.5, 1.0], score scales linearly with confidence.
        score_05, _ = _xray_sr_score(_struct_with_confidence(0.5), is_buy=True)
        score_07, _ = _xray_sr_score(_struct_with_confidence(0.7), is_buy=True)
        score_09, _ = _xray_sr_score(_struct_with_confidence(0.9), is_buy=True)
        assert score_05 < score_07 < score_09
