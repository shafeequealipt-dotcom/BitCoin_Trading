"""T2-3 / F11 brain-vs-analysis disagreement visibility smoke tests.

Six-tier-fixes 2026-05-11. The disagreement predicate is a small bit
of inline logic in strategy_worker._execute_claude_trade just before
DIRECTION_DECISION emits. This test reproduces the predicate to lock
its semantics:

  fire = (
      analysis_dir in ("Buy", "Sell")
      AND analysis_dir != brain_dir
      AND direction == brain_dir
      AND not was_flipped
  )

Visibility-only design: a fire emits BRAIN_VS_ANALYSIS_DISAGREEMENT at
WARN. No enforcement, no behaviour change. Enforcement deferred
pending evidence collection.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _should_fire_inline(
    analysis_dir: str,
    brain_dir: str,
    final_dir: str,
    was_flipped: bool,
) -> bool:
    """Mirror of strategy_worker.py disagreement predicate."""
    return (
        analysis_dir in ("Buy", "Sell")
        and analysis_dir != brain_dir
        and final_dir == brain_dir
        and not was_flipped
    )


def test_disagreement_fires_when_analysis_opposes_brain_and_no_flip():
    """ADAUSDT scenario from WAVE 7: brain=Sell, analysis=BUY, no flip."""
    assert _should_fire_inline(
        analysis_dir="Buy", brain_dir="Sell", final_dir="Sell",
        was_flipped=False,
    ) is True


def test_disagreement_suppressed_when_flip_already_reconciled():
    """BLURUSDT scenario: brain=Buy, analysis=SELL, xray flipped to Sell.

    The flip resolves the disagreement; no need to surface it as a
    separate event since DIRECTION_DECISION already records flip_source.
    """
    assert _should_fire_inline(
        analysis_dir="Sell", brain_dir="Buy", final_dir="Sell",
        was_flipped=True,
    ) is False


def test_disagreement_suppressed_when_analysis_neutral():
    """Neutral analysis verdict is not a disagreement — just a no-opinion."""
    assert _should_fire_inline(
        analysis_dir="NEUTRAL", brain_dir="Buy", final_dir="Buy",
        was_flipped=False,
    ) is False


def test_disagreement_suppressed_when_analysis_agrees():
    """SKRUSDT scenario from WAVE 7: brain=Sell, analysis=SELL, agree."""
    assert _should_fire_inline(
        analysis_dir="Sell", brain_dir="Sell", final_dir="Sell",
        was_flipped=False,
    ) is False


def test_disagreement_suppressed_when_final_dir_changed_post_brain():
    """If somehow final_dir differs from brain_dir without was_flipped,

    treat that as already-resolved (some other unforeseen mechanism
    fired). Predicate is conservative — only fires when brain's
    direction is what actually trades.
    """
    assert _should_fire_inline(
        analysis_dir="Sell", brain_dir="Buy", final_dir="Sell",
        was_flipped=False,
    ) is False
