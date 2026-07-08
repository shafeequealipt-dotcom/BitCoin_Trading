"""Tests for OpenInterestTracker.

Issue #8 fix (2026-05-27) changed ``fetch_current`` to source its
``change_24h_pct`` from the DB-computed ~24h delta
(``AltDataRepository.get_latest_open_interest`` -> ``_compute_oi_delta_pct``,
23h lookback) — the SAME true delta the signal generator consumes — instead
of the old 1h-over-1h delta the API response carried (which was mislabeled
as 24h). Computing a real 24h delta therefore requires a prior snapshot at
least ~23h old in the table; the tests below seed that history explicitly so
they exercise the corrected path end-to-end, mirroring the canonical seeding
idiom in ``tests/test_altdata_repo_oi_delta.py``.
"""

from datetime import timedelta

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.utils import now_utc
from src.intelligence.altdata.open_interest import OpenInterestTracker


@pytest.fixture
def mock_bybit_oi(test_settings):
    client = MagicMock()
    client._settings = test_settings

    async def mock_call(method, **kwargs):
        if method == "get_open_interest":
            return {
                "list": [
                    {"openInterest": "15000", "timestamp": "1704110400000"},
                    {"openInterest": "14000", "timestamp": "1704106800000"},
                ]
            }
        return {"list": []}

    client.call = AsyncMock(side_effect=mock_call)
    return client


async def _seed_prior_oi(db, symbol: str, value: float, hours_ago: float) -> None:
    """Insert a prior OI snapshot at an explicit, sufficiently-old timestamp
    so the repo's ~23h-lookback delta has a real comparator. Mirrors the
    ``_insert`` helper in tests/test_altdata_repo_oi_delta.py."""
    when_iso = (now_utc() - timedelta(hours=hours_ago)).isoformat()
    await db.execute(
        "INSERT INTO open_interest (symbol, open_interest_value, timestamp) VALUES (?, ?, ?)",
        (symbol, value, when_iso),
    )


class TestOpenInterestTracker:
    @pytest.mark.asyncio
    async def test_fetch_current(self, mock_bybit_oi, test_db):
        # Seed a ~24h-old prior snapshot (14000) so fetch_current's freshly
        # saved current OI (15000) yields a real DB-computed 24h delta.
        await _seed_prior_oi(test_db, "BTCUSDT", 14000.0, hours_ago=24)

        tracker = OpenInterestTracker(mock_bybit_oi, test_db)
        results = await tracker.fetch_current(["BTCUSDT"])

        assert len(results) == 1
        assert results[0]["symbol"] == "BTCUSDT"
        assert results[0]["open_interest"] == 15000.0
        # Issue #8: 24h delta is now the DB-computed value (15000 vs the
        # 14000 snapshot ~24h ago = +7.14%), not the old 1h-from-API delta.
        assert results[0]["change_24h_pct"] == pytest.approx(7.1429, abs=0.01)
        assert results[0]["change_24h_pct"] > 5.0

    @pytest.mark.asyncio
    async def test_persisted_to_db(self, mock_bybit_oi, test_db):
        tracker = OpenInterestTracker(mock_bybit_oi, test_db)
        await tracker.fetch_current(["BTCUSDT"])

        rows = await test_db.fetch_all("SELECT * FROM open_interest")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_significant_changes(self, mock_bybit_oi, test_db):
        # get_significant_changes -> fetch_current(default_symbols); seed a
        # ~24h-old prior snapshot for each default symbol so each freshly
        # saved current OI produces a real (>threshold) 24h delta.
        for sym in mock_bybit_oi._settings.bybit.default_symbols:
            await _seed_prior_oi(test_db, sym, 14000.0, hours_ago=24)

        tracker = OpenInterestTracker(mock_bybit_oi, test_db)
        significant = await tracker.get_significant_changes(threshold_pct=5.0)
        assert len(significant) >= 1

    @pytest.mark.asyncio
    async def test_get_history(self, mock_bybit_oi, test_db):
        tracker = OpenInterestTracker(mock_bybit_oi, test_db)
        await tracker.fetch_current(["BTCUSDT"])
        history = await tracker.get_history("BTCUSDT", hours=24)
        assert len(history) >= 1
