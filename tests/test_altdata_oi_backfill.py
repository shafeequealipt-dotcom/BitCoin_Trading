"""OI cold-start backfill — OpenInterestTracker.backfill_history + repo helpers.

A fresh deployment has no OI snapshot ≥23h old, so the 24h delta
(``AltDataRepository._compute_oi_delta_pct``) reads 0.0 until ~23h of live
5-minute fetches accrue. ``backfill_history`` seeds Bybit's historical OI
series on first boot so the 24h/1h/15m windows read true values from cycle 1.

These tests pin the contract:

  1. ``save_open_interest_at`` stores an explicit historical timestamp.
  2. ``has_open_interest_older_than`` reflects whether the 24h lookback is satisfiable.
  3. ``backfill_history`` seeds enough history that the 24h delta is non-zero.
  4. ``backfill_history`` is idempotent — a symbol already spanning 24h is
     skipped and no second API call is made.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import (
    OI_LOOKBACK_24H_HOURS,
    AltDataRepository,
)
from src.intelligence.altdata.open_interest import OpenInterestTracker


@pytest.fixture()
async def db(tmp_path):
    """aiosqlite-backed DB with the production OI schema."""
    db_path = tmp_path / "oi_backfill.db"
    mgr = DatabaseManager(str(db_path))
    await mgr.connect()
    await mgr.execute(
        """
        CREATE TABLE open_interest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            open_interest_value REAL NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
    )
    yield mgr
    await mgr.disconnect()


class _FakeSettings:
    class bybit:
        default_symbols = ["BTCUSDT"]


class _FakeBybitClient:
    """Minimal stand-in returning a synthetic 15-min OI series spanning ~50h.

    Newest-first (Bybit returns descending), one point every 15 minutes so the
    oldest point is ~49.75h old — comfortably past the 23h lookback.
    """

    def __init__(self, base_oi: float = 1000.0, points: int = 200) -> None:
        self._settings = _FakeSettings()
        self._base = base_oi
        self._points = points
        self.calls = 0
        self.intervals: list = []  # records intervalTime of each call

    async def call(self, method, **kwargs):
        assert method == "get_open_interest"
        self.calls += 1
        self.intervals.append(kwargs.get("intervalTime"))
        limit = int(kwargs.get("limit", 50))
        now_ms = int(now_utc().timestamp() * 1000)
        step_ms = 15 * 60 * 1000
        lst = [
            {
                "openInterest": str(self._base * (1.0 + 0.002 * i)),
                "timestamp": str(now_ms - i * step_ms),
            }
            for i in range(min(limit, self._points))
        ]
        return {"list": lst}


@pytest.mark.asyncio
async def test_save_open_interest_at_stores_explicit_timestamp(db) -> None:
    repo = AltDataRepository(db)
    ts = (now_utc() - timedelta(hours=24)).isoformat()
    await repo.save_open_interest_at("BTCUSDT", 12345.0, ts)
    rows = await repo.get_open_interest("BTCUSDT", hours=48)
    assert len(rows) == 1
    assert rows[0]["open_interest_value"] == 12345.0


@pytest.mark.asyncio
async def test_has_open_interest_older_than(db) -> None:
    repo = AltDataRepository(db)
    assert await repo.has_open_interest_older_than("BTCUSDT", OI_LOOKBACK_24H_HOURS) is False
    # A recent-only snapshot does NOT satisfy the 24h lookback.
    await repo.save_open_interest_at("BTCUSDT", 1.0, now_utc().isoformat())
    assert await repo.has_open_interest_older_than("BTCUSDT", OI_LOOKBACK_24H_HOURS) is False
    # A ≥23h-old snapshot does.
    await repo.save_open_interest_at(
        "BTCUSDT", 1.0, (now_utc() - timedelta(hours=30)).isoformat(),
    )
    assert await repo.has_open_interest_older_than("BTCUSDT", OI_LOOKBACK_24H_HOURS) is True


@pytest.mark.asyncio
async def test_backfill_seeds_nonzero_24h_delta(db) -> None:
    repo = AltDataRepository(db)
    tracker = OpenInterestTracker(_FakeBybitClient(base_oi=1000.0), db)

    # Fresh DB → 24h lookback unsatisfiable.
    assert await repo.has_open_interest_older_than("BTCUSDT", OI_LOOKBACK_24H_HOURS) is False

    summary = await tracker.backfill_history(["BTCUSDT"])
    assert summary["seeded"] == 1
    assert summary["rows"] == 200

    # A ≥23h anchor now exists and the 24h delta is a true non-zero value.
    assert await repo.has_open_interest_older_than("BTCUSDT", OI_LOOKBACK_24H_HOURS) is True
    latest = await repo.get_latest_open_interest("BTCUSDT")
    assert latest is not None
    assert latest["change_24h_pct"] != 0.0


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db) -> None:
    client = _FakeBybitClient()
    tracker = OpenInterestTracker(client, db)

    first = await tracker.backfill_history(["BTCUSDT"])
    assert first["seeded"] == 1 and client.calls == 1

    # Second boot: symbol already spans 24h → skipped, no second API call.
    second = await tracker.backfill_history(["BTCUSDT"])
    assert second["skipped"] == 1 and second["seeded"] == 0
    assert client.calls == 1


@pytest.mark.asyncio
async def test_integration_backfill_then_live_fetch_yields_deltas(db) -> None:
    """Integration of the worker's first-tick sequence: backfill seeds history,
    then the live ``fetch_current`` saves the current snapshot and the enriched
    read returns true non-zero 24h/1h/15m deltas — the exact path ``tick()``
    runs (backfill before the OI fetch) once the guard fires on boot.
    """
    repo = AltDataRepository(db)
    client = _FakeBybitClient(base_oi=1000.0)
    tracker = OpenInterestTracker(client, db)

    await tracker.backfill_history(["BTCUSDT"])     # step 1: seed (15min page)
    fetched = await tracker.fetch_current(["BTCUSDT"])  # step 2: live fetch (5min)

    # The two phases used the correct exchange granularities.
    assert "15min" in client.intervals          # backfill = 50h page
    assert client.intervals[-1] == "5min"        # fetch_current = fresh window
    assert len(fetched) == 1

    # All three windows now resolve to true non-zero values off seeded history.
    latest = await repo.get_latest_open_interest("BTCUSDT")
    assert latest is not None
    assert latest["change_24h_pct"] != 0.0
    assert latest["change_1h_pct"] != 0.0
    assert latest["change_15m_pct"] != 0.0
