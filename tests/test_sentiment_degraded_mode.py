"""Phase 10 (post-Layer-1 fix) — SentimentAggregator degraded mode tests.

When ``settings.reddit.client_id`` is empty, Reddit is intentionally
disabled and the aggregator should:
  1. Emit one ``SENTIMENT_DEGRADED_MODE`` line at init (WARNING).
  2. Suppress the per-coin ``SENT_UNKNOWN`` line for the no-data path.
     The ``SENT_NEUTRAL`` line is preserved (downstream parsers grep it).

When Reddit IS configured but transiently has no rows (real data gap),
the per-coin ``SENT_UNKNOWN`` still fires — different signal.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_10_reddit_disable.md``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.intelligence.sentiment.aggregator import SentimentAggregator
from src.intelligence.sentiment.scorer import SentimentScorer


def test_disabled_when_no_settings() -> None:
    """No settings reference → keep verbose path (back-compat)."""
    db = MagicMock()
    agg = SentimentAggregator(db, SentimentScorer())
    assert agg._reddit_intentionally_disabled is False


def test_disabled_when_settings_has_empty_client_id() -> None:
    """Empty client_id → degraded mode flag set."""
    db = MagicMock()
    settings = SimpleNamespace(reddit=SimpleNamespace(client_id=""))
    agg = SentimentAggregator(db, SentimentScorer(), settings)
    assert agg._reddit_intentionally_disabled is True


def test_disabled_when_settings_reddit_missing() -> None:
    """settings has no reddit attribute at all → degraded mode."""
    db = MagicMock()
    settings = SimpleNamespace()
    agg = SentimentAggregator(db, SentimentScorer(), settings)
    assert agg._reddit_intentionally_disabled is True


def test_enabled_when_settings_has_client_id() -> None:
    """Real client_id → verbose mode (Reddit configured)."""
    db = MagicMock()
    settings = SimpleNamespace(reddit=SimpleNamespace(client_id="real_id"))
    agg = SentimentAggregator(db, SentimentScorer(), settings)
    assert agg._reddit_intentionally_disabled is False


def test_disabled_handles_settings_introspection_failure() -> None:
    """Defensive: any exception in settings introspection → fall back to verbose."""
    db = MagicMock()

    class _Bomb:
        @property
        def reddit(self):
            raise RuntimeError("settings exploded")

    agg = SentimentAggregator(db, SentimentScorer(), _Bomb())
    # Defensive default = verbose path so we never silently lose
    # observability if settings introspection breaks.
    assert agg._reddit_intentionally_disabled is False


def test_manager_reddit_disabled_log_is_warning() -> None:
    """src/workers/manager.py emits REDDIT_DISABLED at WARNING."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src" / "workers" / "manager.py").read_text()
    # Look for the WARNING-level structured tag.
    assert 'log.warning(\n                    "REDDIT_DISABLED' in src or \
           'log.warning("REDDIT_DISABLED' in src, (
        "REDDIT_DISABLED log must be at WARNING level — INFO buries the "
        "disable notice in normal traffic."
    )
    assert "reason=no_credentials" in src
    assert "impact=sentiment_degraded" in src


def test_manager_passes_settings_to_aggregator() -> None:
    """src/workers/manager.py constructs SentimentAggregator(db, scorer, settings)."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src" / "workers" / "manager.py").read_text()
    assert "SentimentAggregator(db, scorer, settings)" in src, (
        "Aggregator construction must pass settings — without it the "
        "degraded-mode flag is never set in production."
    )
