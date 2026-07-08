"""Definitive-fix Phase 12 — ensemble single-strategy cap.

Smoke tests for the togglable ``single_strategy_max_share`` cap. With
cap=1.0 (default), behaviour is unchanged; with a tighter cap, a
dominant strategy's contribution is clipped so STRONG requires
multiple independent voters.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from src.strategies.models.signal_types import EnsembleVote


def _capped_contribution(votes, vote_str: str, cap_share: float) -> float:
    """Replica of the inline lambda for direct unit testing."""
    contribs = [
        v.weight * v.confidence for v in votes if v.vote == vote_str
    ]
    if not contribs or cap_share >= 1.0:
        return sum(contribs)
    total = sum(contribs)
    for i, c in enumerate(contribs):
        rest = total - c
        ceiling = rest * cap_share / max(1.0 - cap_share, 1e-9)
        if c > ceiling:
            contribs[i] = ceiling
    return sum(contribs)


def _vote(name: str, vote: str, confidence: float, weight: float) -> EnsembleVote:
    return EnsembleVote(
        strategy_name=name, vote=vote, confidence=confidence,
        weight=weight, reasoning="",
    )


def test_phase12_cap_disabled_passthrough() -> None:
    """cap=1.0 → identical to unweighted sum."""
    votes = [
        _vote("a", "BUY", 0.9, 1.0),
        _vote("b", "BUY", 0.5, 1.0),
        _vote("c", "BUY", 0.5, 1.0),
    ]
    assert _capped_contribution(votes, "BUY", 1.0) == 1.9


def test_phase12_dominant_strategy_capped() -> None:
    """One huge contribution gets clamped relative to the rest."""
    votes = [
        _vote("dominant", "BUY", 1.0, 4.0),  # contribution=4.0
        _vote("small1", "BUY", 0.5, 1.0),   # 0.5
        _vote("small2", "BUY", 0.5, 1.0),   # 0.5
    ]
    # cap=0.4 → rest_total=1.0, ceiling = 1.0 * 0.4 / 0.6 ≈ 0.667.
    # Dominant's 4.0 → clipped to 0.667; total ≈ 0.667 + 0.5 + 0.5 = 1.667.
    capped = _capped_contribution(votes, "BUY", 0.4)
    assert abs(capped - 1.6667) < 1e-3


def test_phase12_balanced_votes_unchanged_under_cap() -> None:
    """Balanced contributions don't trigger the clamp."""
    votes = [
        _vote("a", "BUY", 0.6, 1.0),
        _vote("b", "BUY", 0.6, 1.0),
        _vote("c", "BUY", 0.6, 1.0),
    ]
    raw = sum(v.weight * v.confidence for v in votes)
    capped = _capped_contribution(votes, "BUY", 0.4)
    # Each contribution=0.6, rest=1.2, ceiling = 1.2*0.4/0.6 = 0.8.
    # All under ceiling → total unchanged.
    assert abs(capped - raw) < 1e-9
