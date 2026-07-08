"""Phase 4 of the 1D briefing rewrite — NaN-defense (Risk R2).

Risk R2 in the rollout document:

    "State labeler / interestingness ranker silently NaN's on edge
    inputs → all-coins-tied at 0.0 → no ranking signal."

This test confirms the ranker never returns NaN on adversarial inputs:
NaN floats, infinities, missing optionals, garbage strings. If any of
these escape ``_safe_clamp``, ranking degenerates and Phase 9 cutover
must roll back. This test is the regression guard.
"""

import math

from src.workers.scanner.interestingness import compute_interestingness
from src.workers.scanner.state_labeler import LABEL_NO_TRADEABLE_STATE


def _is_finite_score(score: float) -> bool:
    return isinstance(score, float) and math.isfinite(score) and 0.0 <= score <= 1.0


def test_nan_float_inputs_yield_finite_score() -> None:
    res = compute_interestingness(
        setup_type_confidence=float("nan"),
        setup_score=float("nan"),
        rr_ratio=float("nan"),
        regime_confidence=float("nan"),
        funding_rate=float("nan"),
    )
    assert _is_finite_score(res.score)
    for k, v in res.breakdown.items():
        assert math.isfinite(v), f"breakdown.{k} = {v} is not finite"


def test_inf_float_inputs_yield_finite_score() -> None:
    res = compute_interestingness(
        setup_type_confidence=float("inf"),
        setup_score=float("inf"),
        rr_ratio=float("inf"),
        regime_confidence=float("-inf"),
        funding_rate=float("inf"),
    )
    assert _is_finite_score(res.score)


def test_garbage_string_inputs_yield_finite_score() -> None:
    res = compute_interestingness(
        setup_type="???",
        regime="???",
        consensus="???",
        consensus_direction="???",
        trade_direction="upward",
        mtf_quality="???",
        mtf_h1_bias="???",
        primary_label="UNKNOWN_LABEL",
    )
    assert _is_finite_score(res.score)


def test_all_defaults_yield_finite_score() -> None:
    """No kwargs at all — pure-default invocation."""
    res = compute_interestingness()
    assert _is_finite_score(res.score)
    # With nothing fired, primary defaults to NO_TRADEABLE_STATE which
    # carries a 0.05 base weight; combined with mid-defaults, score
    # should be small but non-zero (typically ~0.03-0.05).
    assert res.score < 0.20


def test_empty_secondary_labels_does_not_crash() -> None:
    res = compute_interestingness(
        primary_label=LABEL_NO_TRADEABLE_STATE,
        secondary_labels=[],
    )
    assert _is_finite_score(res.score)


def test_position_in_range_outside_unit_interval_handled() -> None:
    """position_in_range should saturate via the extremity bands;
    out-of-range values are still finite."""
    res = compute_interestingness(position_in_range=2.5)
    assert _is_finite_score(res.score)
    res2 = compute_interestingness(position_in_range=-1.0)
    assert _is_finite_score(res2.score)


def test_breakdown_keys_match_documented_set() -> None:
    """Contract: breakdown carries exactly the seven component keys."""
    res = compute_interestingness()
    expected = {
        "cleanness", "confluence", "extremity", "label_strength",
        "structural_quality", "mtf_alignment", "open_position_floor",
    }
    assert set(res.breakdown.keys()) == expected
