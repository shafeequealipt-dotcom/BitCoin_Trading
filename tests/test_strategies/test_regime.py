"""Tests for RegimeDetector."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import RegimeSettings, _build_regime
from src.strategies.models.regime_types import REGIME_ACTIVE_CATEGORIES, MarketRegime, RegimeState
from src.strategies.regime import RegimeDetector


class TestRegimeTypes:
    def test_all_regimes_have_active_categories(self):
        for regime in MarketRegime:
            assert regime in REGIME_ACTIVE_CATEGORIES

    def test_regime_state_to_dict(self, sample_regime):
        d = sample_regime.to_dict()
        assert d["regime"] == "trending_up"
        assert d["confidence"] == 0.75
        assert d["adx"] == 30
        assert "scalping" in d["active_strategy_categories"]

    def test_regime_state_from_dict(self):
        data = {
            "regime": "volatile",
            "confidence": 0.8,
            "adx": 18,
            "atr_percentile": 180,
            "choppiness": 40,
            "volume_ratio": 2.5,
            "trend_direction": -1,
        }
        state = RegimeState.from_dict(data)
        assert state.regime == MarketRegime.VOLATILE
        assert state.confidence == 0.8
        assert state.trend_direction == -1

    def test_dead_regime_limited_categories(self):
        cats = REGIME_ACTIVE_CATEGORIES[MarketRegime.DEAD]
        assert "funding_arb" in cats
        assert "microstructure" in cats
        assert "momentum" not in cats

    def test_trending_up_has_most_categories(self):
        up_cats = REGIME_ACTIVE_CATEGORIES[MarketRegime.TRENDING_UP]
        dead_cats = REGIME_ACTIVE_CATEGORIES[MarketRegime.DEAD]
        assert len(up_cats) > len(dead_cats)


class TestRegimeThresholds:
    """Verify the regime classifier thresholds match the values calibrated
    to close the ELSE-fallback gap (see dev_notes/regime_investigation/).

    Pre-tune, 73.9% of regime emissions fell through the `else: RANGING`
    branch with conf=0.40 — uninformative for downstream consumers. The
    new thresholds widen each explicit branch so the fallback shrinks.
    """

    def test_default_dataclass_thresholds(self):
        cfg = RegimeSettings()
        assert cfg.trending_adx_threshold == 20.0
        assert cfg.ranging_adx_threshold == 20.0
        assert cfg.ranging_choppiness_threshold == 50.0
        assert cfg.volatile_atr_percentile == 70.0
        assert cfg.dead_adx_threshold == 12.0
        assert cfg.dead_volume_ratio == 0.5
        assert cfg.hysteresis_count == 2

    def test_build_regime_missing_keys_uses_new_defaults(self):
        cfg = _build_regime({})
        assert cfg.trending_adx_threshold == 20.0
        assert cfg.ranging_choppiness_threshold == 50.0
        assert cfg.volatile_atr_percentile == 70.0
        assert cfg.dead_adx_threshold == 12.0

    def test_build_regime_explicit_values_override_defaults(self):
        cfg = _build_regime({
            "trending_adx_threshold": 30.0,
            "ranging_choppiness_threshold": 65.0,
            "volatile_atr_percentile": 120.0,
            "dead_adx_threshold": 8.0,
        })
        assert cfg.trending_adx_threshold == 30.0
        assert cfg.ranging_choppiness_threshold == 65.0
        assert cfg.volatile_atr_percentile == 120.0
        assert cfg.dead_adx_threshold == 8.0


class TestRegimeClassifierBranches:
    """Behavior tests for the classifier — verify each explicit branch
    fires at the calibrated threshold boundary instead of falling through
    to the ELSE = RANGING default.

    Uses mocked TAEngine + MarketRepository so the test is deterministic
    and does not require live market data.
    """

    def _build_detector(self) -> RegimeDetector:
        settings = MagicMock()
        settings.regime = RegimeSettings()
        ta_engine = MagicMock()
        ta_engine.analyze = AsyncMock()
        market_repo = MagicMock()
        # 60 dummy OHLCV tuples — passes the `len(klines) >= 50` guard.
        market_repo.get_klines = AsyncMock(
            return_value=[(0, 0, 0, 0, 0, 0)] * 60,
        )
        return RegimeDetector(settings, ta_engine, market_repo)

    def _ta_payload(
        self,
        *,
        adx: float,
        plus_di: float,
        minus_di: float,
        choppiness: float,
        natr: float,
        volume_ratio: float,
    ) -> dict:
        return {
            "trend": {"adx": {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}},
            "volatility": {
                "choppiness_index": choppiness,
                "atr_14": 1.0,
                "natr_14": natr,
            },
            "volume": {"volume_sma_ratio": volume_ratio},
        }

    @pytest.mark.asyncio
    async def test_trending_up_fires_at_adx_22_post_b1a(self):
        """ADX = 22 (above new 20 threshold, below old 25). With Plus-DI
        > Minus-DI and choppiness < 45, the trending_up branch must fire.
        Pre-tune this fell to ELSE = RANGING.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=22, plus_di=28, minus_di=18, choppiness=35, natr=0.5, volume_ratio=1.0,
        )
        state = await det.detect("BTCUSDT")
        assert state.regime == MarketRegime.TRENDING_UP
        assert state.confidence == pytest.approx(22 / 50, abs=0.01)

    @pytest.mark.asyncio
    async def test_trending_down_fires_at_adx_22_post_b1a(self):
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=22, plus_di=18, minus_di=28, choppiness=35, natr=0.5, volume_ratio=1.0,
        )
        state = await det.detect("ETHUSDT")
        assert state.regime == MarketRegime.TRENDING_DOWN
        assert state.trend_direction == -1

    @pytest.mark.asyncio
    async def test_strict_ranging_fires_at_chop_55_post_b1a(self):
        """ADX < 20, choppiness = 55 (above new 50 threshold, below old 60).
        Strict ranging branch must fire instead of ELSE fallback.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=15, plus_di=12, minus_di=13, choppiness=55, natr=0.4, volume_ratio=1.0,
        )
        state = await det.detect("SOLUSDT")
        assert state.regime == MarketRegime.RANGING
        # conf = min(chop / 80, 1.0) = 55/80 = 0.6875 — distinct from ELSE conf=0.4
        assert state.confidence == pytest.approx(55 / 80, abs=0.01)

    @pytest.mark.asyncio
    async def test_volatile_fires_on_high_atr_percentile_post_b1a(self):
        """NATR=0.8 -> atr_percentile=80, above new 70 threshold. Volatile
        branch must fire. Pre-tune (threshold 150) this was unreachable
        from the NATR-derived value.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=10, plus_di=8, minus_di=9, choppiness=40, natr=0.8, volume_ratio=1.0,
        )
        state = await det.detect("DOGEUSDT")
        assert state.regime == MarketRegime.VOLATILE

    @pytest.mark.asyncio
    async def test_dead_fires_at_adx_10_post_b1a(self):
        """ADX = 10 (below new 12 threshold), low volume, low ATR.
        Dead branch must fire.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=10, plus_di=8, minus_di=9, choppiness=40, natr=0.3, volume_ratio=0.3,
        )
        state = await det.detect("ARBUSDT")
        assert state.regime == MarketRegime.DEAD
        assert state.confidence == 0.8

    @pytest.mark.asyncio
    async def test_transitional_coin_gets_computed_confidence(self):
        """Issue #6 tiling fix (2026-05-27): a transitional coin — ADX = 14
        (above dead threshold 12, below trending 20), choppiness = 45 (below
        ranging threshold 50), volume normal — used to fall through to a
        FABRICATED ``RANGING`` at a flat 0.40 confidence.

        The (ADX, choppiness) plane is now fully tiled. With no directional
        trend strength (ADX below the trending threshold) the coin is still
        labeled RANGING, but its confidence is COMPUTED from choppiness
        (chop/100 = 0.45), never the old flat constant — so the brain receives
        a signal-bearing reading instead of a fabricated one.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=14, plus_di=15, minus_di=14, choppiness=45, natr=0.4, volume_ratio=0.8,
        )
        state = await det.detect("BNBUSDT")
        assert state.regime == MarketRegime.RANGING
        # Computed from choppiness (45/100), NOT the old fabricated flat 0.40.
        assert state.confidence == pytest.approx(0.45, abs=0.001)
        assert state.confidence != pytest.approx(0.40, abs=0.001)

    @pytest.mark.asyncio
    async def test_no_regression_on_clearly_trending(self):
        """ADX = 32 (above both old and new trending threshold). Trending
        still fires — no regression on clearly-trending coins.
        """
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=32, plus_di=35, minus_di=15, choppiness=30, natr=0.5, volume_ratio=1.1,
        )
        state = await det.detect("INJUSDT")
        assert state.regime == MarketRegime.TRENDING_UP
        assert state.trend_direction == 1
        assert state.confidence == pytest.approx(32 / 50, abs=0.01)

    @pytest.mark.asyncio
    async def test_choppy_highvol_is_ranging_not_volatile_phase0a(self):
        """Per-coin-authority Phase 0a: a low-ADX, high-choppiness coin whose
        own NATR ranks high (atr_percentile high) must classify RANGING, not
        VOLATILE. This is the live BTC case (ADX~18, chop~66) that the old
        VOLATILE-before-RANGING ordering mislabelled volatile, silencing
        mean-reversion. Structure now wins over volatility magnitude.
        """
        det = self._build_detector()
        # natr=0.9 -> the (dummy-kline) fallback sets atr_percentile=90 (>70),
        # which would have triggered VOLATILE under the old ordering.
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=18, plus_di=12, minus_di=13, choppiness=66, natr=0.9, volume_ratio=1.0,
        )
        state = await det.detect("BTCUSDT")
        assert state.regime == MarketRegime.RANGING
        assert state.trend_direction == 0

    @pytest.mark.asyncio
    async def test_volatile_asserts_no_direction_phase0d(self):
        """Per-coin-authority Phase 0d: a genuinely VOLATILE coin (not ranging,
        not trending, not dead) must carry trend_direction == 0 — no spurious
        DI-derived lean.
        """
        det = self._build_detector()
        # adx 10 (< ranging 20 but chop 40 < ranging 50 -> not ranging;
        # volume 1.0 -> not dead), natr 0.8 -> atr_percentile 80 (> 70) -> VOLATILE.
        det.ta_engine.analyze.return_value = self._ta_payload(
            adx=10, plus_di=20, minus_di=8, choppiness=40, natr=0.8, volume_ratio=1.0,
        )
        state = await det.detect("DOGEUSDT")
        assert state.regime == MarketRegime.VOLATILE
        assert state.trend_direction == 0


class TestRegimeUnknown:
    """Per-coin-authority Phase 0b/0c — the explicit UNKNOWN state."""

    def _detector_with_klines(self, n: int) -> RegimeDetector:
        settings = MagicMock()
        settings.regime = RegimeSettings()
        ta_engine = MagicMock()
        ta_engine.analyze = AsyncMock()
        market_repo = MagicMock()
        market_repo.get_klines = AsyncMock(return_value=[(0, 0, 0, 0, 0, 0)] * n)
        return RegimeDetector(settings, ta_engine, market_repo)

    @pytest.mark.asyncio
    async def test_insufficient_klines_returns_unknown_not_ranging(self):
        det = self._detector_with_klines(10)  # < 50
        state = await det.detect("NEWUSDT")
        assert state.regime == MarketRegime.UNKNOWN
        assert state.confidence == 0.0
        # _last_regime is populated (non-None) so get_last_regime() won't force a re-detect.
        assert det.get_last_regime() is state

    @pytest.mark.asyncio
    async def test_missing_core_field_returns_unknown(self):
        det = self._detector_with_klines(60)
        # TA payload with choppiness_index genuinely absent (None).
        det.ta_engine.analyze.return_value = {
            "trend": {"adx": {"adx": 25, "plus_di": 20, "minus_di": 10}},
            "volatility": {"atr_14": 1.0, "natr_14": 0.5},  # no choppiness_index
            "volume": {"volume_sma_ratio": 1.0},
        }
        state = await det.detect("GAPUSDT")
        assert state.regime == MarketRegime.UNKNOWN

    @pytest.mark.asyncio
    async def test_detect_per_coin_failure_emits_unknown_not_omit(self):
        det = self._detector_with_klines(60)
        det.ta_engine.analyze = AsyncMock(side_effect=RuntimeError("ta boom"))
        results = await det.detect_per_coin(["FAILUSDT"])
        assert "FAILUSDT" in results  # not omitted
        assert results["FAILUSDT"].regime == MarketRegime.UNKNOWN

    def test_unknown_has_broad_nonempty_roster(self):
        from src.strategies.models.regime_types import REGIME_ACTIVE_CATEGORIES
        cats = REGIME_ACTIVE_CATEGORIES[MarketRegime.UNKNOWN]
        assert "kickstart" in cats  # never empty
        assert "momentum" in cats and "mean_reversion" in cats  # broad


class TestBreadthBrake:
    """Per-coin-authority Phase 5 — the breadth RISK/SIZING brake (sizing only)."""

    def _det(self) -> RegimeDetector:
        settings = MagicMock()
        settings.regime = RegimeSettings()
        return RegimeDetector(settings, MagicMock(), MagicMock())

    def _coins(self, **counts) -> dict:
        m: dict = {}
        i = 0
        for name, n in counts.items():
            rgm = getattr(MarketRegime, name)
            for _ in range(n):
                s = RegimeState.unknown()
                s.regime = rgm
                m[f"{name}{i}"] = s
                i += 1
        return m

    def test_lopsided_down_brakes_size(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=10, TRENDING_UP=2)
        mult, info = d.breadth_sizing()
        assert mult < 1.0
        assert info["lopsided"] > 0.65

    def test_balanced_no_brake(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=6, TRENDING_UP=6)
        mult, _ = d.breadth_sizing()
        assert mult == 1.0

    def test_ranging_heavy_no_brake(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=2, TRENDING_UP=1, RANGING=9)
        mult, _ = d.breadth_sizing()
        assert mult == 1.0  # few trending -> not directionally lopsided

    def test_too_few_classified_no_brake(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=5)
        mult, info = d.breadth_sizing()
        assert mult == 1.0 and info["classified"] == 5

    def test_unknown_excluded_from_denominator(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=10, TRENDING_UP=2, UNKNOWN=20)
        mult, info = d.breadth_sizing()
        assert info["classified"] == 12  # the 20 UNKNOWN are excluded
        assert mult < 1.0

    def test_disabled_flag_no_brake(self):
        d = self._det()
        d.settings.regime.breadth_brake_enabled = False
        d._per_coin_regimes = self._coins(TRENDING_DOWN=12)
        mult, _ = d.breadth_sizing()
        assert mult == 1.0

    def test_floor_is_lower_bound(self):
        d = self._det()
        d._per_coin_regimes = self._coins(TRENDING_DOWN=20)  # fully one-sided
        mult, _ = d.breadth_sizing()
        assert mult == pytest.approx(d.settings.regime.breadth_brake_floor, abs=1e-9)


class TestRegimeMissingVolume:
    """Issue #3B (2026-05-31): a genuinely-missing volume_sma_ratio must be
    distinguishable from a real one — not masked to a healthy 1.0. The detector
    carries `volume_ratio_known`; missing volume must NOT force DEAD and must NOT
    crash (it falls through to the ADX/choppiness tiling)."""

    def _build_detector(self) -> RegimeDetector:
        settings = MagicMock()
        settings.regime = RegimeSettings()
        ta_engine = MagicMock()
        ta_engine.analyze = AsyncMock()
        market_repo = MagicMock()
        market_repo.get_klines = AsyncMock(return_value=[(0, 0, 0, 0, 0, 0)] * 60)
        return RegimeDetector(settings, ta_engine, market_repo)

    def _payload(self, *, adx, plus_di, minus_di, choppiness, natr, volume):
        # `volume` is the dict placed under the "volume" key — pass {} to model a
        # genuinely-absent volume_sma_ratio (the missing-data case).
        return {
            "trend": {"adx": {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}},
            "volatility": {"choppiness_index": choppiness, "atr_14": 1.0, "natr_14": natr},
            "volume": volume,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("vr", [0.0, 0.06, 0.5, 1.0, 2.0])
    async def test_present_volume_marks_known(self, vr):
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._payload(
            adx=22, plus_di=28, minus_di=18, choppiness=35, natr=0.5,
            volume={"volume_sma_ratio": vr},
        )
        state = await det.detect("BTCUSDT")
        assert state.volume_ratio_known is True
        assert state.volume_ratio == pytest.approx(vr)

    @pytest.mark.asyncio
    async def test_missing_volume_marks_not_known_and_neutral(self):
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._payload(
            adx=22, plus_di=28, minus_di=18, choppiness=35, natr=0.5, volume={},
        )
        state = await det.detect("BTCUSDT")
        assert state.volume_ratio_known is False
        # neutral placeholder kept for arithmetic; classification unaffected here
        assert state.volume_ratio == pytest.approx(1.0)
        assert state.regime == MarketRegime.TRENDING_UP

    @pytest.mark.asyncio
    async def test_missing_volume_does_not_force_dead(self):
        # ADX below dead threshold (12) with low ATR — the only thing that could
        # make this DEAD is a low volume ratio. With volume MISSING it must not be
        # forced DEAD; it falls to the tiling (RANGING) and must not crash.
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._payload(
            adx=10, plus_di=11, minus_di=10, choppiness=40, natr=0.1, volume={},
        )
        state = await det.detect("ARBUSDT")
        assert state.regime != MarketRegime.DEAD
        assert state.volume_ratio_known is False

    @pytest.mark.asyncio
    async def test_real_low_volume_still_dead(self):
        # The SAME ADX/ATR but with a REAL low volume ratio must still classify
        # DEAD — proving we only declined to use volume when it was absent.
        det = self._build_detector()
        det.ta_engine.analyze.return_value = self._payload(
            adx=10, plus_di=11, minus_di=10, choppiness=40, natr=0.1,
            volume={"volume_sma_ratio": 0.06},
        )
        state = await det.detect("ARBUSDT")
        assert state.regime == MarketRegime.DEAD
        assert state.volume_ratio_known is True

    def test_to_dict_serializes_unknown_as_none(self):
        s = RegimeState(
            regime=MarketRegime.RANGING, confidence=0.5, adx=15.0,
            atr_percentile=40.0, choppiness=45.0, volume_ratio=1.0,
            volume_ratio_known=False, trend_direction=0,
        )
        d = s.to_dict()
        assert d["volume_ratio"] is None
        assert d["volume_ratio_known"] is False
        # round-trips back to unknown
        assert RegimeState.from_dict(d).volume_ratio_known is False

    def test_from_dict_present_value_is_known(self):
        s = RegimeState.from_dict({
            "regime": "ranging", "confidence": 0.5, "adx": 15,
            "atr_percentile": 40, "choppiness": 45, "volume_ratio": 0.8,
            "trend_direction": 0,
        })
        assert s.volume_ratio_known is True
        assert s.volume_ratio == pytest.approx(0.8)

    def test_from_dict_null_value_is_unknown(self):
        s = RegimeState.from_dict({
            "regime": "ranging", "confidence": 0.5, "adx": 15,
            "atr_percentile": 40, "choppiness": 45, "volume_ratio": None,
            "trend_direction": 0,
        })
        assert s.volume_ratio_known is False
        assert s.volume_ratio == pytest.approx(1.0)

    def test_unknown_factory_marks_volume_not_known(self):
        assert RegimeState.unknown().volume_ratio_known is False
