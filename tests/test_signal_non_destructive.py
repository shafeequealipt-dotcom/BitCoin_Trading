"""CALL_B Framing Fix Phase 4B (2026-05-06) — non-destructive SIG_DOWNGRADE.

The pre-fix path overwrote `Signal.signal_type` so downstream consumers
(ScannerWorker, ClaudeStrategist via _signal_cache) only saw the
downgraded form. The fix preserves the original classification in
`components.original_signal_type` alongside `components.confidence_floor_failed`
so consumers can opt into the pre-downgrade strength.

Two surgical tests verify the new contract:
  1. Downgrade present: STRONG_BUY at conf=0.30 → BUY (downgraded once)
     OR NEUTRAL (downgraded twice, conf<buy_min). Components carry the
     original_signal_type=STRONG_BUY plus confidence_floor_failed=True.
  2. No downgrade: BUY at conf=0.50 stays BUY. Components carry the
     original_signal_type=BUY and confidence_floor_failed=False (i.e.,
     consumers can always read the field without a presence check).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import (
    SignalGeneratorMultiSourceSettings,
    SignalGeneratorSettings,
)
from src.core.types import SignalType
from src.intelligence.signals.signal_generator import SignalGenerator


def _make_generator(
    confidence: float,
    signal_type_pre_downgrade: SignalType,
    cfg: SignalGeneratorMultiSourceSettings | None = None,
) -> SignalGenerator:
    """SignalGenerator stub with mocked deps.

    `signal_type_pre_downgrade` is what `_evaluate_signal` would return;
    `confidence` is what `_confidence.calculate` would return. The
    downgrade ladder is then applied per generator code.
    """
    settings = MagicMock()
    settings.signal_generator = SignalGeneratorSettings(
        multi_source=cfg or SignalGeneratorMultiSourceSettings(),
    )
    sg = SignalGenerator.__new__(SignalGenerator)

    aggregator = MagicMock()
    aggregator.aggregate_for_symbol = AsyncMock(return_value={
        "overall_score": 0.0,
        "news_count": 0,
        "reddit_count": 0,
        "news_score": 0.0,
        "reddit_score": 0.0,
    })

    altdata_repo = MagicMock()
    altdata_repo.get_latest_fear_greed = AsyncMock(return_value=None)
    altdata_repo.get_latest_funding_rate = AsyncMock(return_value=None)
    altdata_repo.get_latest_open_interest = AsyncMock(return_value=None)
    altdata_repo.save_signal = AsyncMock(return_value=None)

    market_repo = MagicMock()
    # Fix 1 (2026-06-10): generate_signal now awaits market_repo.get_ticker for
    # the 24h price-conditioning of the OI component — model it as async (None =
    # no ticker -> no conditioning, leaving the downgrade behaviour under test
    # unchanged). get_klines (volume surge / blend) stays a MagicMock; those
    # paths are defensively try-wrapped.
    market_repo.get_ticker = AsyncMock(return_value=None)

    confidence_calc = MagicMock()
    confidence_calc.calculate = MagicMock(return_value=confidence)

    sg._aggregator = aggregator
    sg._db = MagicMock()
    sg._altdata_repo = altdata_repo
    sg._market_repo = market_repo
    sg._confidence = confidence_calc
    sg._ms_cfg = settings.signal_generator.multi_source

    # Stub _evaluate_signal to return the pre-downgrade type so the
    # downgrade-ladder block in generate_signal exercises with the
    # confidence we control.
    sg._evaluate_signal = MagicMock(  # type: ignore[method-assign]
        return_value=(signal_type_pre_downgrade, "test_reasoning"),
    )
    # Stub the freshness / volume helpers.
    sg._compute_data_age_hours = MagicMock(return_value=0.5)

    async def _vol(_sym: str) -> float:
        return 1.0
    sg._compute_volume_surge_ratio = _vol  # type: ignore[method-assign]

    return sg


@pytest.mark.asyncio
async def test_downgrade_preserves_original_signal_type_in_components() -> None:
    """STRONG_BUY at conf=0.30 → downgraded twice (below strong_min=0.60
    AND below buy_min=0.40) → final type=NEUTRAL. The original
    STRONG_BUY classification must survive in
    components.original_signal_type with confidence_floor_failed=True.
    """
    sg = _make_generator(
        confidence=0.30,
        signal_type_pre_downgrade=SignalType.STRONG_BUY,
    )
    signal = await sg.generate_signal("BTCUSDT")
    assert signal.signal_type == SignalType.NEUTRAL
    assert signal.components["original_signal_type"] == SignalType.STRONG_BUY.value
    assert signal.components["confidence_floor_failed"] is True
    assert signal.components["confidence_below_strong"] is True
    assert signal.components["confidence_below_buy"] is True


@pytest.mark.asyncio
async def test_downgrade_one_step_strong_to_buy() -> None:
    """STRONG_BUY at conf=0.50 → downgrade ONCE to BUY (above buy_min,
    below strong_min). `confidence_below_strong=True`, but
    `confidence_below_buy=False`."""
    sg = _make_generator(
        confidence=0.50,
        signal_type_pre_downgrade=SignalType.STRONG_BUY,
    )
    signal = await sg.generate_signal("BTCUSDT")
    assert signal.signal_type == SignalType.BUY
    assert signal.components["original_signal_type"] == SignalType.STRONG_BUY.value
    assert signal.components["confidence_floor_failed"] is True
    assert signal.components["confidence_below_strong"] is True
    assert signal.components["confidence_below_buy"] is False


@pytest.mark.asyncio
async def test_no_downgrade_when_confidence_above_thresholds() -> None:
    """BUY at conf=0.50 (>= buy_min=0.40) → no downgrade. The new
    components keys are STILL set so consumers can branch without a
    presence check.
    """
    sg = _make_generator(
        confidence=0.50,
        signal_type_pre_downgrade=SignalType.BUY,
    )
    signal = await sg.generate_signal("BTCUSDT")
    assert signal.signal_type == SignalType.BUY
    assert signal.components["original_signal_type"] == SignalType.BUY.value
    assert signal.components["confidence_floor_failed"] is False
    assert signal.components["confidence_below_strong"] is False
    assert signal.components["confidence_below_buy"] is False


# ── Layer 1 Defect 5 — sentiment-consumption default resolution ────


def test_sentiment_consumption_fallback_default_matches_settings_default() -> None:
    """Layer 1 Defect 5 regression guard: when no ``settings`` is passed
    (legacy constructor signature), the SignalGenerator's
    ``_sentiment_consumption_enabled`` must equal the SentimentSettings
    dataclass default. Historically the fallback was True while the
    dataclass default was False — the lie was harmless in production
    (settings always wins) but the codebase contradicted itself.
    """
    from src.config.settings import SentimentSettings
    db = MagicMock()
    aggregator = MagicMock()
    sg = SignalGenerator(aggregator=aggregator, db=db)
    assert (
        sg._sentiment_consumption_enabled
        == SentimentSettings().consumption_enabled
    )


def test_sentiment_consumption_reads_from_settings_when_provided() -> None:
    """When settings.sentiment is provided, the SignalGenerator must use
    its ``consumption_enabled`` value — both True and False."""
    db = MagicMock()
    aggregator = MagicMock()
    settings_on = MagicMock()
    settings_on.sentiment = MagicMock()
    settings_on.sentiment.consumption_enabled = True
    sg_on = SignalGenerator(
        aggregator=aggregator, db=db, settings=settings_on,
    )
    assert sg_on._sentiment_consumption_enabled is True

    settings_off = MagicMock()
    settings_off.sentiment = MagicMock()
    settings_off.sentiment.consumption_enabled = False
    sg_off = SignalGenerator(
        aggregator=aggregator, db=db, settings=settings_off,
    )
    assert sg_off._sentiment_consumption_enabled is False
