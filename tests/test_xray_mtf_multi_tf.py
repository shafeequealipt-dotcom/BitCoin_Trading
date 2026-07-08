"""Issue #5 (2026-05-31): wire higher timeframes (H4+D1) into X-RAY MTF.

Covers the cross-timeframe blend math, alignment rules, graceful fallback,
the per-TF cache TTL, the cheap direction-only engine read, and — critically —
that the flag-off / no-HTF path is byte-identical to the legacy H1-only score.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.analysis.structure.mtf_confluence import MTFConfluenceScorer
from src.analysis.structure.structure_cache import HigherTFStructureCache
from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    TFStructureView,
)
from src.config.settings import StructureSettings


def _scorer(weight: float = 0.25) -> MTFConfluenceScorer:
    s = StructureSettings()
    s.mtf_htf_weight = weight
    return MTFConfluenceScorer(s)


def _view(structure="uptrend", bos="", has_data=True, tf="D") -> TFStructureView:
    return TFStructureView(
        timeframe=tf, structure=structure, last_bos_direction=bos, has_data=has_data,
    )


def _score(scorer, direction="long", structure="ranging", higher_tf_views=None):
    return scorer.score(
        symbol="X", current_price=100.0, direction=direction,
        market_structure=MarketStructureResult(structure=structure),
        supports=[], resistances=[], placement=None,
        fvgs=[], order_blocks=[], smc_confluence=0,
        fibonacci=None, volume_profile=None,
        higher_tf_views=higher_tf_views,
    )


class TestBlendMath:
    def test_none_views_returns_factor_score_unchanged(self):
        sc = _scorer()
        assert sc._blend_higher_tf("long", 8, None) == (8, 0.0, [], {})

    def test_empty_views_returns_factor_score_unchanged(self):
        sc = _scorer()
        assert sc._blend_higher_tf("long", 8, {}) == (8, 0.0, [], {})

    def test_full_alignment_lifts_score(self):
        sc = _scorer(weight=0.25)
        views = {"D": _view("uptrend", tf="D"), "240": _view("uptrend", tf="240")}
        blended, agree, missing, analyses = sc._blend_higher_tf("long", 8, views)
        assert agree == pytest.approx(1.0)
        assert blended == 10  # round(8 * 1.25)
        assert missing == []
        assert analyses == {"htf_d1": 1.0, "htf_h4": 1.0}

    def test_full_conflict_cuts_score(self):
        sc = _scorer(weight=0.25)
        views = {"D": _view("downtrend", tf="D"), "240": _view("downtrend", tf="240")}
        blended, agree, missing, _ = sc._blend_higher_tf("long", 8, views)
        assert agree == pytest.approx(-1.0)
        assert blended == 6  # round(8 * 0.75)
        assert "d1_conflict" in missing and "h4_conflict" in missing

    def test_neutral_views_are_noop(self):
        sc = _scorer(weight=0.25)
        views = {"D": _view("ranging", tf="D"), "240": _view("ranging", tf="240")}
        blended, agree, _, _ = sc._blend_higher_tf("long", 8, views)
        assert agree == pytest.approx(0.0)
        assert blended == 8

    def test_d1_outweighs_h4(self):
        # D1 (w=1.0) downtrend vs H4 (w=0.7) uptrend, dir long -> net negative.
        sc = _scorer()
        views = {"D": _view("downtrend", tf="D"), "240": _view("uptrend", tf="240")}
        _, agree, _, _ = sc._blend_higher_tf("long", 8, views)
        assert agree == pytest.approx((1.0 * -1 + 0.7 * 1) / 1.7)
        assert agree < 0

    def test_influence_is_bounded(self):
        import math
        sc = _scorer(weight=0.25)
        for fs in range(0, 11):
            for d_struct in ("uptrend", "downtrend", "ranging"):
                views = {"D": _view(d_struct, tf="D")}
                blended, _, _, _ = sc._blend_higher_tf("long", fs, views)
                assert abs(blended - fs) <= math.ceil(fs * 0.25)
                assert 0 <= blended <= 10

    def test_missing_data_excluded_and_marked(self):
        sc = _scorer()
        views = {"D": _view(has_data=False, tf="D"), "240": _view("uptrend", tf="240")}
        blended, agree, missing, analyses = sc._blend_higher_tf("long", 8, views)
        # only H4 counts -> agreement +1 on it
        assert agree == pytest.approx(1.0)
        assert "d1_data_missing" in missing
        assert "htf_d1" not in analyses and analyses["htf_h4"] == 1.0

    def test_all_missing_is_noop(self):
        sc = _scorer()
        views = {"D": _view(has_data=False, tf="D"), "240": _view(has_data=False, tf="240")}
        blended, agree, missing, _ = sc._blend_higher_tf("long", 7, views)
        assert (blended, agree) == (7, 0.0)
        assert "d1_data_missing" in missing and "h4_data_missing" in missing


class TestTfAlignment:
    def test_structure_alignment(self):
        sc = _scorer()
        assert sc._tf_alignment("long", _view("uptrend")) == 1.0
        assert sc._tf_alignment("long", _view("downtrend")) == -1.0
        assert sc._tf_alignment("short", _view("downtrend")) == 1.0
        assert sc._tf_alignment("short", _view("uptrend")) == -1.0

    def test_ranging_uses_bos_weakly(self):
        sc = _scorer()
        assert sc._tf_alignment("long", _view("ranging", bos="bullish")) == 0.5
        assert sc._tf_alignment("long", _view("ranging", bos="bearish")) == -0.5
        assert sc._tf_alignment("long", _view("ranging", bos="")) == 0.0

    def test_neutral_direction_is_zero(self):
        sc = _scorer()
        assert sc._tf_alignment("", _view("uptrend")) == 0.0


class TestScoreIntegration:
    def test_score_none_views_matches_baseline(self):
        sc = _scorer()
        base = _score(sc, higher_tf_views=None)
        empty = _score(sc, higher_tf_views={})
        assert base.score == empty.score
        assert base.quality == empty.quality

    def test_score_with_aligned_views_does_not_crash_and_marks_tf(self):
        sc = _scorer()
        views = {"D": _view("uptrend", tf="D"), "240": _view("uptrend", tf="240")}
        res = _score(sc, direction="long", structure="uptrend", higher_tf_views=views)
        assert 0 <= res.score <= 10
        # per-TF analysis surfaced additively in timeframe_analyses
        assert res.timeframe_analyses.get("htf_d1") == 1.0

    def test_score_with_missing_higher_tf_falls_back(self):
        sc = _scorer()
        views = {"D": _view(has_data=False, tf="D"), "240": _view(has_data=False, tf="240")}
        baseline = _score(sc, direction="long", structure="uptrend", higher_tf_views=None)
        res = _score(sc, direction="long", structure="uptrend", higher_tf_views=views)
        assert res.score == baseline.score  # no usable HTF -> identical
        assert "d1_data_missing" in res.missing_factors


class TestHigherTFCache:
    def test_set_get_within_ttl(self):
        c = HigherTFStructureCache()
        v = _view("uptrend", tf="D")
        c.set("BTCUSDT", "D", v)
        assert c.get("BTCUSDT", "D", 3600) is v
        assert c.size() == 1

    def test_expires_after_ttl(self):
        c = HigherTFStructureCache()
        with patch("src.analysis.structure.structure_cache.time.monotonic", return_value=1000.0):
            c.set("BTCUSDT", "D", _view(tf="D"))
        with patch("src.analysis.structure.structure_cache.time.monotonic", return_value=1000.0 + 3601):
            assert c.get("BTCUSDT", "D", 3600) is None

    def test_per_tf_keys_independent(self):
        c = HigherTFStructureCache()
        c.set("BTCUSDT", "D", _view(tf="D"))
        assert c.get("BTCUSDT", "240", 300) is None  # different TF, not cached


class TestEngineDirectionOnly:
    def _engine(self):
        from src.analysis.structure.structure_engine import StructureEngine
        return StructureEngine(StructureSettings())

    def _candles(self, n, base=100.0, step=0.0):
        from src.core.types import OHLCV
        out = []
        for i in range(n):
            px = base + step * i
            out.append(OHLCV(
                symbol="X", timeframe="D", timestamp=i * 86400000,
                open=px, high=px + 1, low=px - 1, close=px,
                volume=1000.0, turnover=1000.0 * px,
            ))
        return out

    def test_thin_candles_returns_no_data(self):
        eng = self._engine()
        view = eng.analyze_direction_only("X", self._candles(10), timeframe="D")
        assert view.has_data is False
        assert view.timeframe == "D"

    def test_empty_candles_returns_no_data(self):
        eng = self._engine()
        assert eng.analyze_direction_only("X", [], timeframe="240").has_data is False

    def test_adequate_candles_returns_view(self):
        eng = self._engine()
        view = eng.analyze_direction_only("X", self._candles(80, step=1.0), timeframe="D")
        assert view.has_data is True
        assert view.timeframe == "D"
        assert view.structure in ("uptrend", "downtrend", "ranging", "unknown")
        assert view.current_price > 0


class TestStructureWorkerMTF:
    """Worker-level MTF wiring (the highest-risk surface): flag gate, real
    refresh/build glue, and per-symbol cache gating that bounds re-fetch."""

    def _worker(self, enabled: bool):
        from unittest.mock import MagicMock
        from src.config.settings import Settings
        from src.analysis.structure.structure_engine import StructureEngine
        from src.analysis.structure.structure_cache import StructureCache
        from src.workers.structure_worker import StructureWorker
        s = Settings.load()
        s.structure.mtf_multi_timeframe_enabled = enabled
        eng = StructureEngine(s.structure)
        w = StructureWorker(settings=s, db=MagicMock(), engine=eng,
                            cache=StructureCache(), scanner=None, shadow_kline_reader=None)
        return w

    def _candles(self, tf="240", n=80):
        from src.core.types import OHLCV
        return [OHLCV(symbol="BTCUSDT", timeframe=tf, timestamp=i, open=100+i, high=101+i,
                      low=99+i, close=100+i, volume=1e3, turnover=1e5) for i in range(n)]

    def test_flag_off_build_returns_none_no_fetch(self):
        # Flag OFF: the tick guard never calls _refresh; _build_htf_views is None.
        w = self._worker(False)
        assert w._mtf_enabled is False
        assert w._build_htf_views("BTCUSDT") is None

    @pytest.mark.asyncio
    async def test_flag_on_refresh_then_build(self):
        from unittest.mock import AsyncMock, MagicMock
        w = self._worker(True)
        w._market_repo = MagicMock()
        w._market_repo.get_klines_batch = AsyncMock(return_value={"BTCUSDT": self._candles()})
        await w._refresh_htf_views(["BTCUSDT"])
        # one batched query per higher TF (240, D)
        assert w._market_repo.get_klines_batch.await_count == 2
        views = w._build_htf_views("BTCUSDT")
        assert views and "240" in views and "D" in views
        assert views["240"].has_data is True

    @pytest.mark.asyncio
    async def test_per_symbol_cache_gating_avoids_refetch(self):
        from unittest.mock import AsyncMock, MagicMock
        w = self._worker(True)
        w._market_repo = MagicMock()
        w._market_repo.get_klines_batch = AsyncMock(return_value={"BTCUSDT": self._candles()})
        await w._refresh_htf_views(["BTCUSDT"])
        n_after_first = w._market_repo.get_klines_batch.await_count
        await w._refresh_htf_views(["BTCUSDT"])  # cache fresh -> no refetch
        assert w._market_repo.get_klines_batch.await_count == n_after_first


class TestMtfConfigLoads:
    """Regression for the _build_structure bug: list/default_factory fields
    (mtf_timeframes, swing_lookbacks) must actually load from config, not be
    silently dropped by a hasattr() filter."""

    def test_mtf_timeframes_loads_from_config(self):
        from src.config.settings import _build_structure
        st = _build_structure({"mtf_timeframes": ["240"], "mtf_htf_weight": 0.4})
        assert st.mtf_timeframes == ["240"]
        assert st.mtf_htf_weight == 0.4

    def test_swing_lookbacks_loads_from_config(self):
        from src.config.settings import _build_structure
        assert _build_structure({"swing_lookbacks": [2, 4, 6]}).swing_lookbacks == [2, 4, 6]

    def test_scalar_fields_still_load(self):
        from src.config.settings import _build_structure
        st = _build_structure({"mtf_d1_cache_ttl_seconds": 7200, "batch_size": 30})
        assert st.mtf_d1_cache_ttl_seconds == 7200 and st.batch_size == 30
