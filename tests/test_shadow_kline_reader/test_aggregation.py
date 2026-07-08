"""Pure aggregation tests for ShadowKlineReader.

Uses ``temp_shadow_db`` fixture (360 mins of seed data per symbol → exactly
6 H1 buckets). All tests run against a real on-disk SQLite file with the
same schema as production shadow.db, but populated with deterministic data
so bucket-math is verifiable.
"""

import pytest

from src.analysis.structure.shadow_kline_reader import ShadowKlineReader
from src.core.types import OHLCV, TimeFrame


@pytest.fixture
async def reader(temp_shadow_db):
    """A connected ShadowKlineReader bound to the temp DB."""
    r = ShadowKlineReader(temp_shadow_db)
    await r.connect()
    yield r
    await r.close()


class TestH1Aggregation:
    async def test_h1_bucket_count_from_360_minutes(self, reader):
        """360 minutes of 1-min candles → 6 H1 buckets."""
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert len(candles) == 6

    async def test_returns_ohlcv_objects(self, reader):
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert all(isinstance(c, OHLCV) for c in candles)

    async def test_chronological_order(self, reader):
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        timestamps = [c.timestamp for c in candles]
        assert timestamps == sorted(timestamps)

    async def test_timeframe_enum_set(self, reader):
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert all(c.timeframe == TimeFrame.H1 for c in candles)

    async def test_open_is_first_minute_open(self, reader):
        # Bucket 0 spans minutes 0-59. Open of minute 0 = base_price + 0*0.1 = 100.0.
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert candles[0].open == pytest.approx(100.0)

    async def test_close_is_last_minute_close(self, reader):
        # Bucket 0 spans minutes 0-59. Close of minute 59 = base + 59*0.1 + 0.1 = 106.0.
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert candles[0].close == pytest.approx(106.0)

    async def test_high_is_max_in_bucket(self, reader):
        # High of minute 59 = base + 59*0.1 + 0.5 = 106.4 (highest in bucket 0).
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert candles[0].high == pytest.approx(106.4)

    async def test_low_is_min_in_bucket(self, reader):
        # Low of minute 0 = base - 0.5 = 99.5 (lowest in bucket 0).
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert candles[0].low == pytest.approx(99.5)

    async def test_volume_is_sum_of_minutes(self, reader):
        # Bucket 0 volume = sum_{m=0..59} (1.0 + (m % 10) * 0.1)
        # = sum of 6 cycles of (1.0 + 0.0..0.9) = 6 * sum(1.0..1.9) = 6 * 14.5 = 87.0
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        expected_volume = sum(1.0 + (m % 10) * 0.1 for m in range(60))
        assert candles[0].volume == pytest.approx(expected_volume)

    async def test_turnover_is_sum_of_per_minute_turnover(self, reader):
        # Turnover per minute = volume * open. Sum across the bucket.
        candles = await reader.get_klines("BTCUSDT", "60", 200)
        expected_turnover = sum(
            (1.0 + (m % 10) * 0.1) * (100.0 + m * 0.1) for m in range(60)
        )
        assert candles[0].turnover == pytest.approx(expected_turnover)

    async def test_per_symbol_isolation(self, reader):
        """ETHUSDT and BTCUSDT have different price scales — confirm no cross-symbol bleed."""
        btc = await reader.get_klines("BTCUSDT", "60", 200)
        eth = await reader.get_klines("ETHUSDT", "60", 200)
        assert btc[0].open == pytest.approx(100.0)
        assert eth[0].open == pytest.approx(1100.0)


class TestEdgeCases:
    async def test_unknown_symbol_returns_empty_list(self, reader):
        candles = await reader.get_klines("DOESNOTEXIST", "60", 200)
        assert candles == []

    async def test_unknown_timeframe_defaults_to_h1(self, reader):
        # "FOO" is not in TF_MS so the default 3_600_000 (1h) is used —
        # same buckets as "60" → 6 buckets.
        candles = await reader.get_klines("BTCUSDT", "FOO", 200)
        assert len(candles) == 6

    async def test_limit_caps_returned_buckets(self, reader):
        """limit=3 should return the most-recent 3 buckets only."""
        candles = await reader.get_klines("BTCUSDT", "60", 3)
        assert len(candles) == 3
        # And they should be the LAST 3 (latest timestamps)
        all_candles = await reader.get_klines("BTCUSDT", "60", 200)
        assert candles == all_candles[-3:]
