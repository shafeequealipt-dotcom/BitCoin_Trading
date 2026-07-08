"""Phase 7 (output-quality) — sentiment categorical reason tests.

Verifies the no-data branch in SentimentAggregator emits the right
categorical tag depending on whether Reddit is intentionally disabled
or genuinely missing data. Behaviour unchanged — `overall_score=0.0`,
`level=UNKNOWN` in both cases. Only the log tag differs.

Per user Q4 = "Categorical reason refinement only, no fallback":
  Reddit disabled by config        → SENT_DEGRADED_MODE
  Reddit configured but empty      → SENT_NO_DATA + SENT_UNKNOWN (alias)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.intelligence.sentiment import aggregator as agg_mod
from src.intelligence.sentiment.aggregator import SentimentAggregator

pytestmark = pytest.mark.asyncio


def _make_aggregator(reddit_disabled: bool) -> SentimentAggregator:
    """Build an aggregator stub bypassing __init__ heavy machinery.

    Uses AsyncMock for ALL repo methods aggregate_for_symbol calls so
    no real I/O happens. Repos are referenced by name only.
    """
    a = SentimentAggregator.__new__(SentimentAggregator)
    # NewsRepository.get_by_symbol → list of articles
    a._news_repo = MagicMock()
    a._news_repo.get_by_symbol = AsyncMock(return_value=[])
    # SentimentRepository — async methods used by aggregate_for_symbol.
    a._sentiment_repo = MagicMock()
    a._sentiment_repo.get_posts_by_symbol = AsyncMock(return_value=[])
    a._sentiment_repo.get_sentiment_for_symbol = AsyncMock(return_value=[])
    a._sentiment_repo.save_aggregated_sentiment = AsyncMock()
    # AltDataRepository — F&G fetch.
    a._altdata_repo = MagicMock()
    fg = MagicMock()
    fg.value = 47
    a._altdata_repo.get_latest_fear_greed = AsyncMock(return_value=fg)
    a._reddit_intentionally_disabled = reddit_disabled
    a._unknown_cache = {}
    # DatabaseManager — ticker_cache lookup is async via fetch_one.
    a._db = MagicMock()
    a._db.fetch_one = AsyncMock(return_value=None)
    a._scorer = MagicMock()
    return a


async def test_reddit_disabled_emits_degraded_mode_tag(monkeypatch) -> None:
    """Reddit intentionally disabled → SENT_DEGRADED_MODE log + no SENT_NO_DATA."""
    a = _make_aggregator(reddit_disabled=True)
    captured: list[str] = []
    monkeypatch.setattr(
        agg_mod, "log",
        MagicMock(
            info=lambda m, *args, **kw: captured.append(str(m)),
            warning=lambda m, *args, **kw: captured.append(str(m)),
            debug=lambda m, *args, **kw: None,
        ),
    )
    result = await a.aggregate_for_symbol("BTCUSDT")
    # Behaviour unchanged.
    assert result["overall_score"] == 0.0

    # The right tag fired.
    assert any("SENT_DEGRADED_MODE" in m for m in captured), (
        f"expected SENT_DEGRADED_MODE, got: {captured}"
    )
    # The "real data gap" tag did NOT fire (reddit was intentionally off,
    # not a genuine data problem).
    assert not any("SENT_NO_DATA" in m for m in captured), (
        f"unexpected SENT_NO_DATA when reddit_disabled=True: {captured}"
    )


async def test_reddit_configured_but_empty_emits_no_data_tag(monkeypatch) -> None:
    """Reddit configured + empty → SENT_NO_DATA + SENT_UNKNOWN (alias for back-compat).

    Distinct from the disabled case: this is a real data gap that
    operators may want to investigate.
    """
    a = _make_aggregator(reddit_disabled=False)
    captured: list[str] = []
    monkeypatch.setattr(
        agg_mod, "log",
        MagicMock(
            info=lambda m, *args, **kw: captured.append(str(m)),
            warning=lambda m, *args, **kw: captured.append(str(m)),
            debug=lambda m, *args, **kw: None,
        ),
    )
    result = await a.aggregate_for_symbol("BTCUSDT")
    assert result["overall_score"] == 0.0

    # Genuine data-gap tag fired.
    assert any("SENT_NO_DATA" in m and "BTCUSDT" in m for m in captured), (
        f"expected SENT_NO_DATA: {captured}"
    )
    # Back-compat alias also fired.
    assert any("SENT_UNKNOWN" in m for m in captured), (
        f"expected SENT_UNKNOWN alias: {captured}"
    )
    # The disabled-mode tag did NOT fire.
    assert not any("SENT_DEGRADED_MODE" in m for m in captured), (
        f"unexpected SENT_DEGRADED_MODE when reddit configured: {captured}"
    )


async def test_sent_neutral_alias_preserved(monkeypatch) -> None:
    """SENT_NEUTRAL tag is preserved across both branches for downstream parsers."""
    captured: list[str] = []

    a = _make_aggregator(reddit_disabled=True)
    monkeypatch.setattr(
        agg_mod, "log",
        MagicMock(
            info=lambda m, *args, **kw: captured.append(str(m)),
            warning=lambda m, *args, **kw: captured.append(str(m)),
            debug=lambda m, *args, **kw: None,
        ),
    )
    await a.aggregate_for_symbol("BTCUSDT")
    assert any("SENT_NEUTRAL" in m for m in captured), (
        f"expected SENT_NEUTRAL preserved: {captured}"
    )
