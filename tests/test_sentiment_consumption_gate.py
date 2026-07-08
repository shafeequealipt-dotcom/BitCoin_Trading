"""Sentiment consumption gate — post Fix-3 (sentiment removal, 2026-06-10).

Fix 3 severed sentiment from the signal entirely: the direction classifier
``_evaluate_signal`` no longer takes a sentiment argument and the confidence
calculator no longer reads news/reddit sentiment. The ``consumption_enabled``
flag now governs only whether the per-coin sentiment is FETCHED in
``signal_worker`` (default False = no fetch, no SENT_UNKNOWN_CACHE_HIT spam);
it can no longer change the classifier output. These tests assert that new
contract and that the flag still defaults to disabled.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import (
    SentimentSettings,
    SignalGeneratorMultiSourceSettings,
    SignalGeneratorSettings,
)
from src.core.types import SignalType
from src.intelligence.signals.signal_generator import SignalGenerator


def _make_generator(
    *,
    consumption_enabled: bool,
    cfg: SignalGeneratorMultiSourceSettings | None = None,
) -> SignalGenerator:
    """SignalGenerator stub with mocked deps + a real Settings shim
    carrying the operator-toggleable consumption flag.
    """
    settings = MagicMock()
    settings.signal_generator = SignalGeneratorSettings(
        multi_source=cfg or SignalGeneratorMultiSourceSettings(),
    )
    settings.sentiment = SentimentSettings(consumption_enabled=consumption_enabled)
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._aggregator = MagicMock()
    sg._db = MagicMock()
    sg._altdata_repo = MagicMock()
    sg._market_repo = MagicMock()
    sg._confidence = MagicMock()
    sg._ms_cfg = settings.signal_generator.multi_source
    sg._sentiment_consumption_enabled = bool(
        settings.sentiment.consumption_enabled
    )
    return sg


def test_classifier_has_no_sentiment_parameter() -> None:
    """Fix 3 (sentiment removal, 2026-06-10): sentiment is fully severed from
    the direction classifier — _evaluate_signal no longer accepts a sentiment
    argument at all. The consumption gate now governs only whether the per-coin
    sentiment is FETCHED (signal_worker), never the classifier.
    """
    import inspect
    sg = _make_generator(consumption_enabled=False)
    params = list(inspect.signature(sg._evaluate_signal).parameters)
    assert "sentiment" not in params, f"classifier still takes sentiment: {params}"


def test_classifier_decides_on_fg_funding_oi_only() -> None:
    """With sentiment gone, direction comes from funding/OI (and F&G via the
    off-switch). A deeply negative funding alone classifies BUY, and no
    'sentiment' token appears in the reason regardless of the gate state.
    """
    for enabled in (False, True):
        sg = _make_generator(consumption_enabled=enabled)
        sig, reason = sg._evaluate_signal(
            fear_greed=50, funding_rate=-0.012, oi_change=0.0, symbol="BTCUSDT",
        )
        assert sig in (SignalType.BUY, SignalType.STRONG_BUY)
        assert "active=[funding]" in reason
        assert "sentiment" not in reason


def test_default_settings_disable_consumption() -> None:
    """SentimentSettings default value matches the operator's decision —
    disabled by default. Catches a regression where the field flips
    silently to True.
    """
    cfg = SentimentSettings()
    assert cfg.consumption_enabled is False
