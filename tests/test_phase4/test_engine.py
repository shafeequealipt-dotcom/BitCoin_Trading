"""Tests for TAEngine: full analysis, signal scoring, edge cases."""

import numpy as np
import pytest

from src.analysis.engine import TAEngine
from src.core.exceptions import DataError
from src.core.types import OHLCV, TimeFrame


class TestTAEngineAnalysis:
    @pytest.mark.asyncio
    async def test_full_analysis_uptrend(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)

        assert result["symbol"] == "BTCUSDT"
        assert result["candles_analyzed"] == 200
        assert result["current_price"] is not None
        assert "trend" in result
        assert "momentum" in result
        assert "volatility" in result
        assert "volume" in result
        assert "patterns" in result
        assert "overall" in result
        assert "support_resistance" in result

    @pytest.mark.asyncio
    async def test_uptrend_produces_bullish(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)
        overall = result["overall"]
        assert overall["signal"] in ("BUY", "STRONG_BUY", "NEUTRAL")
        assert overall["score"] >= -0.5  # Should lean bullish or neutral, not strong sell

    @pytest.mark.asyncio
    async def test_downtrend_produces_bearish(self, downtrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=downtrend_candles)
        overall = result["overall"]
        assert overall["signal"] in ("SELL", "STRONG_SELL", "NEUTRAL")
        assert overall["score"] <= 0.5

    @pytest.mark.asyncio
    async def test_overall_has_reasons(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)
        overall = result["overall"]
        assert "key_reasons" in overall
        assert isinstance(overall["key_reasons"], list)
        assert len(overall["key_reasons"]) > 0

    @pytest.mark.asyncio
    async def test_confidence_range(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)
        conf = result["overall"]["confidence"]
        assert 0 <= conf <= 1.0

    @pytest.mark.asyncio
    async def test_score_range(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)
        score = result["overall"]["score"]
        assert -1.0 <= score <= 1.0


class TestTAEngineIndicatorAccess:
    @pytest.mark.asyncio
    async def test_get_single_rsi(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.get_indicator(uptrend_candles, "rsi", period=14)
        assert result["name"] == "rsi"
        assert result["value"] is not None
        assert 0 <= result["value"] <= 100

    @pytest.mark.asyncio
    async def test_get_single_macd(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.get_indicator(uptrend_candles, "macd")
        assert result["name"] == "macd"
        assert "macd_line" in result

    @pytest.mark.asyncio
    async def test_unknown_indicator(self, uptrend_candles):
        engine = TAEngine()
        result = await engine.get_indicator(uptrend_candles, "nonexistent")
        assert "error" in result


class TestTAEngineSupportResistance:
    @pytest.mark.asyncio
    async def test_support_resistance(self, uptrend_candles):
        engine = TAEngine()
        sr = await engine.get_support_resistance(uptrend_candles)
        assert "support_levels" in sr
        assert "resistance_levels" in sr
        assert "current_price" in sr
        assert isinstance(sr["support_levels"], list)
        assert isinstance(sr["resistance_levels"], list)


class TestTAEngineEdgeCases:
    @pytest.mark.asyncio
    async def test_too_few_candles(self):
        engine = TAEngine()
        candles = [
            OHLCV(symbol="TEST", timeframe=TimeFrame.H1,
                   timestamp=None, open=100, high=101, low=99, close=100.5, volume=100)
            for _ in range(10)
        ]
        with pytest.raises(DataError, match="at least"):
            await engine.analyze(candles=candles)

    @pytest.mark.asyncio
    async def test_no_data_source(self):
        engine = TAEngine()
        with pytest.raises(DataError, match="Provide either"):
            await engine.analyze()

    @pytest.mark.asyncio
    async def test_minimum_candles_works(self):
        """Exactly 50 candles should work."""
        np.random.seed(42)
        candles = []
        from datetime import datetime, timezone, timedelta
        base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(50):
            c = 70000 + np.random.normal(0, 100)
            candles.append(OHLCV(
                symbol="BTCUSDT", timeframe=TimeFrame.H1,
                timestamp=base_time + timedelta(hours=i),
                open=c - 10, high=c + 50, low=c - 50, close=c, volume=100,
            ))
        engine = TAEngine()
        result = await engine.analyze(candles=candles)
        assert result["candles_analyzed"] == 50

    @pytest.mark.asyncio
    async def test_output_no_nan(self, uptrend_candles):
        """Output should use None, not NaN, for JSON compatibility."""
        engine = TAEngine()
        result = await engine.analyze(candles=uptrend_candles)

        def check_no_nan(obj, path=""):
            if isinstance(obj, float):
                assert not np.isnan(obj), f"NaN found at {path}"
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "_raw":
                        continue
                    check_no_nan(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    check_no_nan(v, f"{path}[{i}]")

        check_no_nan(result)

    @pytest.mark.asyncio
    async def test_sideways_neutral(self, sideways_candles):
        """Sideways market should produce neutral or weak signal."""
        engine = TAEngine()
        result = await engine.analyze(candles=sideways_candles)
        score = result["overall"]["score"]
        assert -0.6 <= score <= 0.6  # Not a strong signal either way
