"""SignalGenerator multi-source classification.

Verifies that ``_evaluate_signal()`` uses the multi-source weighted scoring.
Post Fix-3 (sentiment removal, 2026-06-10) the classifier is built from THREE
components — F&G (contrarian, direction-neutral by default), funding rate, and
OI change. Each is "active" only if abs(score) >= its min threshold and is
DROPPED from the weighted sum otherwise. Sentiment is no longer a component and
``_evaluate_signal`` no longer accepts a sentiment argument.

The key property (``test_zero_sentiment_can_classify_buy_via_funding``) is
unchanged: a coin with no sentiment data and F&G direction-neutral still
classifies BUY from its OWN funding/OI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.settings import (
    Settings,
    SignalGeneratorMultiSourceSettings,
    SignalGeneratorSettings,
)
from src.core.types import SignalType
from src.intelligence.signals.signal_generator import SignalGenerator


def _make_generator(
    cfg: SignalGeneratorMultiSourceSettings | None = None,
) -> SignalGenerator:
    """Build a SignalGenerator stub bypassing the heavy __init__ machinery."""
    settings = MagicMock()
    settings.signal_generator = SignalGeneratorSettings(
        multi_source=cfg or SignalGeneratorMultiSourceSettings(),
    )
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._aggregator = MagicMock()
    sg._db = MagicMock()
    sg._altdata_repo = MagicMock()
    sg._market_repo = MagicMock()
    sg._confidence = MagicMock()
    sg._ms_cfg = settings.signal_generator.multi_source
    return sg


def test_zero_sentiment_can_classify_buy_via_funding_under_neutrality() -> None:
    """No sentiment data + F&G direction-neutral → direction from the coin's OWN
    funding. Deeply negative funding (crowded-short → contrarian-bullish)
    classifies BUY via funding alone (active=[funding])."""
    sg = _make_generator()
    sig, reason = sg._evaluate_signal(
        fear_greed=15,        # extreme fear — DIRECTION-NEUTRAL (excluded)
        funding_rate=-0.012,  # deeply negative → funding_score = +1.0 (active)
        oi_change=0.0,        # neutral OI — INACTIVE
        symbol="BTCUSDT",
    )
    assert sig in (SignalType.BUY, SignalType.STRONG_BUY), (
        f"expected BUY via funding, got {sig.value} — reason: {reason}"
    )
    assert "active=[funding]" in reason


def test_fg_off_switch_restores_fg_and_funding_direction() -> None:
    """Off-switch (fg_direction_neutral=False) restores F&G in direction
    alongside funding (active=[fg,funding])."""
    sg = _make_generator(
        SignalGeneratorMultiSourceSettings(fg_direction_neutral=False)
    )
    sig, reason = sg._evaluate_signal(
        fear_greed=15, funding_rate=-0.012, oi_change=0.0, symbol="BTCUSDT",
    )
    assert sig in (SignalType.BUY, SignalType.STRONG_BUY)
    assert "active=[fg,funding]" in reason


def test_sentiment_keys_ignored_by_confidence_after_removal() -> None:
    """Fix 3 (sentiment removal): ConfidenceCalculator no longer reads
    news_sentiment/reddit_sentiment. Passing them (as a value, 0.0, None, or
    omitting them) yields IDENTICAL confidence — they are fully ignored."""
    from src.intelligence.signals.confidence import ConfidenceCalculator
    c = ConfidenceCalculator()
    base = dict(
        fear_greed=1.0, funding_rate=0.5, open_interest=0.5,
        data_age_hours=1.0, volume_surge_ratio=1.0,
    )
    without = c.calculate(dict(base))
    with_zero = c.calculate(dict(base, news_sentiment=0.0, reddit_sentiment=0.0))
    with_none = c.calculate(dict(base, news_sentiment=None, reddit_sentiment=None))
    with_value = c.calculate(dict(base, news_sentiment=0.9, reddit_sentiment=-0.9))
    assert without == with_zero == with_none == with_value


def test_fg_direction_neutral_excludes_fg_alone() -> None:
    """Issue 1 neutrality: with only F&G in range and no own funding/OI, the
    classifier is NEUTRAL — F&G no longer drives direction alone. Greed alone is
    also NEUTRAL (not a flip to sell)."""
    sg = _make_generator()
    for fg in (15, 85):
        sig, reason = sg._evaluate_signal(
            fear_greed=fg, funding_rate=0.0, oi_change=0.0, symbol="BTCUSDT",
        )
        assert sig == SignalType.NEUTRAL, f"fg={fg} should be neutral, got {sig.value}"


def test_strong_buy_when_funding_and_oi_align_bullish() -> None:
    """Funding + OI both bullish → STRONG_BUY (F&G direction-neutral by default,
    so it is excluded from the direction set)."""
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=10,        # excluded (direction-neutral)
        funding_rate=-0.01,   # funding_score = +1.0 (active)
        oi_change=10.0,       # oi_score = +1.0 (active)
        symbol="BTCUSDT",
    )
    assert sig == SignalType.STRONG_BUY


def test_strong_sell_when_funding_and_oi_align_bearish() -> None:
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=90,        # excluded
        funding_rate=0.012,   # funding_score = -1.0 (active)
        oi_change=-10.0,      # oi_score = -1.0 (active)
        symbol="BTCUSDT",
    )
    assert sig == SignalType.STRONG_SELL


def test_neutral_when_no_components_active() -> None:
    """All components inside their inactive band → NEUTRAL with no_active reason."""
    sg = _make_generator()
    sig, reason = sg._evaluate_signal(
        fear_greed=50,         # fg_score=0.0
        funding_rate=0.00001,  # below funding_min_active=0.10
        oi_change=0.05,        # below oi_min_active=0.10 (oi_score=0.01)
        symbol="BTCUSDT",
    )
    assert sig == SignalType.NEUTRAL
    assert "no active components" in reason


def test_neutral_when_components_cancel() -> None:
    """Two active components in opposite directions, weighted, net below the buy
    threshold → NEUTRAL. funding_score=+1.0 (w=0.20) vs oi_score=-1.0 (w=0.15):
    direction = (0.20 - 0.15)/0.35 = +0.143 < buy_threshold(0.18)."""
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=50,        # inactive
        funding_rate=-0.005,  # funding_score = +1.0 (active)
        oi_change=-5.0,       # oi_score = -1.0 (active)
        symbol="BTCUSDT",
    )
    assert sig == SignalType.NEUTRAL


def test_buy_threshold_calibrated() -> None:
    """direction_score = 0.30 (above buy_threshold=0.18, below strong=0.55) → BUY
    via OI alone."""
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=50,
        funding_rate=0.0,
        oi_change=1.5,        # oi_score = 0.30 → direction_score = 0.30
        symbol="BTCUSDT",
    )
    assert sig == SignalType.BUY


def test_strong_threshold_calibrated() -> None:
    """direction_score >= 0.55 → STRONG_BUY via OI alone."""
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=50,
        funding_rate=0.0,
        oi_change=10.0,       # oi_score = +1.0 → STRONG_BUY
        symbol="BTCUSDT",
    )
    assert sig == SignalType.STRONG_BUY


def test_inactive_components_dropped_from_weighted_sum() -> None:
    """Renormalisation: only-fg-active (via off-switch) behaves as fg-with-100%-
    weight → direction_score ≈ +1.0 → STRONG_BUY."""
    sg = _make_generator(
        SignalGeneratorMultiSourceSettings(fg_direction_neutral=False)
    )
    sig, reason = sg._evaluate_signal(
        fear_greed=15,
        funding_rate=0.0,
        oi_change=0.0,
        symbol="BTCUSDT",
    )
    assert sig == SignalType.STRONG_BUY
    assert "active=[fg]" in reason


def test_high_positive_funding_inverts_to_bearish() -> None:
    """Positive funding = crowded longs = bearish (funding_score is INVERTED)."""
    sg = _make_generator()
    sig, _ = sg._evaluate_signal(
        fear_greed=50,
        funding_rate=0.012,    # funding_score = -1.0 (clamped) → STRONG_SELL
        oi_change=0.0,
        symbol="BTCUSDT",
    )
    assert sig == SignalType.STRONG_SELL


def test_extreme_greed_classifies_sell_via_fg_alone_off_switch() -> None:
    """F&G=85 (extreme greed) alone → STRONG_SELL — only via the off-switch
    (fg_direction_neutral=False). By default F&G is direction-neutral."""
    sg = _make_generator(
        SignalGeneratorMultiSourceSettings(fg_direction_neutral=False)
    )
    sig, _ = sg._evaluate_signal(
        fear_greed=85,
        funding_rate=0.0,
        oi_change=0.0,
        symbol="BTCUSDT",
    )
    # fg_score = (50 - 85) / 30 = -1.17 → clamped -1.0 → STRONG_SELL
    assert sig == SignalType.STRONG_SELL


def test_custom_thresholds_via_config() -> None:
    """Operator override via config: a lowered buy_threshold makes a small OI
    score classify BUY where the default threshold would be NEUTRAL — validates
    the config plumbing reaches the classifier."""
    cfg = SignalGeneratorMultiSourceSettings(
        fg_min_active=0.10,
        funding_min_active=0.10,
        oi_min_active=0.05,    # allow a small OI score to be active
        fg_weight=0.25,
        funding_weight=0.20,
        oi_weight=0.15,
        strong_threshold=0.50,
        buy_threshold=0.10,    # lowered from 0.18 default
        fg_normalize_range=30.0,
        funding_normalize=0.005,
        oi_normalize_pct=5.0,
    )
    sg = _make_generator(cfg)
    # oi_change=0.6 → oi_score=0.12: above the custom buy=0.10 (BUY) but below
    # the default 0.18 (would be NEUTRAL) — proves the custom threshold is honored.
    sig, _ = sg._evaluate_signal(
        fear_greed=50,
        funding_rate=0.0,
        oi_change=0.6,
        symbol="ETHUSDT",
    )
    assert sig == SignalType.BUY


def test_legacy_constructor_signature_still_works() -> None:
    """SignalGenerator(aggregator, db) — back-compat for existing tests.

    The sentiment_* config fields remain on the dataclass (the schema is kept
    for a future re-enable) even though the classifier no longer uses them.
    """
    from src.intelligence.signals.signal_generator import SignalGenerator
    aggregator = MagicMock()
    db = MagicMock()
    sg = SignalGenerator(aggregator, db)
    assert sg._ms_cfg.sentiment_min_active == 0.05
    assert sg._ms_cfg.buy_threshold == 0.18


def test_settings_constructor_threads_through() -> None:
    """SignalGenerator(aggregator, db, settings=s) — production wiring."""
    from src.intelligence.signals.signal_generator import SignalGenerator
    settings = Settings.load()
    sg = SignalGenerator(MagicMock(), MagicMock(), settings=settings)
    assert sg._ms_cfg is settings.signal_generator.multi_source


def test_issue1_builder_loads_fg_direction_neutral_from_config():
    """Integration: the [signal_generator.multi_source] builder must CONSUME
    fg_direction_neutral from config (the off-switch)."""
    from src.config.settings import _build_signal_generator
    off = _build_signal_generator({"multi_source": {"fg_direction_neutral": False}})
    assert off.multi_source.fg_direction_neutral is False
    on = _build_signal_generator({"multi_source": {"fg_direction_neutral": True}})
    assert on.multi_source.fg_direction_neutral is True
    default = _build_signal_generator({"multi_source": {}})
    assert default.multi_source.fg_direction_neutral is True
