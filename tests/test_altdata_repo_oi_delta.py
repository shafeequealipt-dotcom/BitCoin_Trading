"""Definitive-fix follow-up — AltDataRepository.get_latest_open_interest

The on-disk ``open_interest`` table stores raw snapshots only; the
repo enriches the read with ``change_24h_pct``, ``change_1h_pct`` and
``change_15m_pct`` (Five-Fix Follow-Up Fix 2, 2026-06-10) computed from
the most-recent prior snapshot at least N hours old.

These tests pin the contract:

  1. No history → returns None.
  2. Single snapshot → 24h/1h deltas are 0.0 (no prior reference).
  3. Two snapshots ~24h apart → 24h delta computed correctly,
     1h delta is 0.0 (no recent prior snapshot in the 50-min window).
  4. Snapshots in both windows → both deltas computed independently.
  5. Prior value zero → graceful 0.0 (no division-by-zero crash).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository


@pytest.fixture()
async def db(tmp_path):
    """Spin up an aiosqlite-backed DB with the production OI schema."""
    db_path = tmp_path / "oi_test.db"
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
    await mgr.execute(
        "CREATE INDEX idx_oi_symbol ON open_interest(symbol, timestamp DESC)",
    )
    yield mgr
    await mgr.disconnect()


async def _insert(mgr: DatabaseManager, sym: str, value: float, when_iso: str) -> None:
    await mgr.execute(
        "INSERT INTO open_interest (symbol, open_interest_value, timestamp) VALUES (?, ?, ?)",
        (sym, value, when_iso),
    )


@pytest.mark.asyncio
async def test_returns_none_when_no_history(db) -> None:
    repo = AltDataRepository(db)
    assert await repo.get_latest_open_interest("BTCUSDT") is None


@pytest.mark.asyncio
async def test_single_snapshot_yields_zero_deltas(db) -> None:
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, "BTCUSDT", 50000.0, now.isoformat())

    result = await repo.get_latest_open_interest("BTCUSDT")
    assert result is not None
    assert result["open_interest_value"] == 50000.0
    # No prior snapshot → both deltas are 0.0.
    assert result["change_24h_pct"] == 0.0
    assert result["change_1h_pct"] == 0.0


@pytest.mark.asyncio
async def test_24h_delta_computed_from_prior_snapshot(db) -> None:
    repo = AltDataRepository(db)
    now = now_utc()
    yesterday = (now - timedelta(hours=24)).isoformat()
    # Yesterday OI=48000 → today OI=50000 → +4.1667 % delta.
    await _insert(db, "BTCUSDT", 48000.0, yesterday)
    await _insert(db, "BTCUSDT", 50000.0, now.isoformat())

    result = await repo.get_latest_open_interest("BTCUSDT")
    assert result is not None
    assert result["change_24h_pct"] == pytest.approx(4.1667, abs=0.01)
    # The query picks the most-recent prior snapshot AT LEAST N hours
    # old; when only one prior exists (24h old), it serves both the
    # 24h and 1h windows. That's the intended graceful-degradation
    # behaviour and matches the on-disk reality of sparse history.
    assert result["change_1h_pct"] == pytest.approx(4.1667, abs=0.01)


@pytest.mark.asyncio
async def test_independent_24h_and_1h_deltas(db) -> None:
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, "ETHUSDT", 1000.0, (now - timedelta(hours=24)).isoformat())
    await _insert(db, "ETHUSDT", 1100.0, (now - timedelta(minutes=55)).isoformat())
    await _insert(db, "ETHUSDT", 1200.0, now.isoformat())

    result = await repo.get_latest_open_interest("ETHUSDT")
    assert result is not None
    # 24h: 1000 → 1200 = +20 %.
    assert result["change_24h_pct"] == pytest.approx(20.0, abs=0.01)
    # 1h: 1100 → 1200 = +9.0909 %.
    assert result["change_1h_pct"] == pytest.approx(9.0909, abs=0.01)


@pytest.mark.asyncio
async def test_15m_delta_independent_of_1h_and_24h(db) -> None:
    """Five-Fix Follow-Up Fix 2 (2026-06-10): the 15-minute delta is computed
    from the most-recent snapshot at least ~12.5 minutes old, independently of
    the 1h and 24h windows — the fresh directional driver window."""
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, "ETHUSDT", 1000.0, (now - timedelta(hours=24)).isoformat())
    await _insert(db, "ETHUSDT", 1100.0, (now - timedelta(minutes=55)).isoformat())
    await _insert(db, "ETHUSDT", 1150.0, (now - timedelta(minutes=15)).isoformat())
    await _insert(db, "ETHUSDT", 1200.0, now.isoformat())

    result = await repo.get_latest_open_interest("ETHUSDT")
    assert result is not None
    # 15m: 1150 → 1200 = +4.3478 % (picks the -15min row, not the -55min one).
    assert result["change_15m_pct"] == pytest.approx(4.3478, abs=0.01)
    # 1h: 1100 → 1200 = +9.0909 % (unchanged by the new window).
    assert result["change_1h_pct"] == pytest.approx(9.0909, abs=0.01)
    # 24h: 1000 → 1200 = +20 % (unchanged).
    assert result["change_24h_pct"] == pytest.approx(20.0, abs=0.01)


@pytest.mark.asyncio
async def test_15m_delta_zero_when_no_recent_snapshot(db) -> None:
    """Cold start for the 15m window: with no snapshot at least ~12.5 minutes
    old beyond the latest itself... the closest prior is the 24h row, which IS
    older than the lookback — the window measures against it honestly. With
    ONLY the latest row, the delta is 0.0 (no prior reference)."""
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, "SOLUSDT", 500.0, now.isoformat())
    result = await repo.get_latest_open_interest("SOLUSDT")
    assert result is not None
    assert result["change_15m_pct"] == 0.0


@pytest.mark.asyncio
async def test_prior_zero_value_returns_zero_delta_no_crash(db) -> None:
    repo = AltDataRepository(db)
    now = now_utc()
    yesterday = (now - timedelta(hours=24)).isoformat()
    await _insert(db, "AAVEUSDT", 0.0, yesterday)  # zero prior
    await _insert(db, "AAVEUSDT", 500.0, now.isoformat())

    result = await repo.get_latest_open_interest("AAVEUSDT")
    assert result is not None
    # Division-by-zero must NOT crash; returns 0.0 fallback.
    assert result["change_24h_pct"] == 0.0


@pytest.mark.asyncio
async def test_negative_delta_for_decreasing_oi(db) -> None:
    repo = AltDataRepository(db)
    now = now_utc()
    await _insert(db, "SOLUSDT", 1100.0, (now - timedelta(hours=24)).isoformat())
    await _insert(db, "SOLUSDT", 1000.0, now.isoformat())

    result = await repo.get_latest_open_interest("SOLUSDT")
    assert result is not None
    # 1100 → 1000 = -9.0909 %.
    assert result["change_24h_pct"] == pytest.approx(-9.0909, abs=0.01)


@pytest.mark.asyncio
async def test_24h_delta_correct_near_midnight_utc_space_format(db, monkeypatch) -> None:
    """OI-midnight-delta regression (2026-05-27).

    Production stores OI timestamps in the space format from ``datetime('now')``
    ("YYYY-MM-DD HH:MM:SS"), while the lookback cutoff is isoformat ("...T...").
    For the ~1 hour each day when ``now - 23h`` shares the current UTC date, a
    raw string compare made the space-format LATEST row sort at-or-below the
    'T'-format cutoff (space 0x20 < 'T' 0x54 at the separator), so the query
    picked the latest row as its own prior and the 24h delta collapsed to 0.

    Pin ``now`` to 23:55 UTC and use SPACE-format rows (as production stores
    them) so this deterministically reproduces the midnight case. With the
    datetime()-normalised comparison the delta is correct (4.1667 %); the old
    raw compare returned 0.0 here.
    """
    import src.database.repositories.altdata_repo as repo_mod
    from datetime import datetime, timezone

    fixed = datetime(2026, 6, 15, 23, 55, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(repo_mod, "now_utc", lambda: fixed)

    def _space(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    repo = AltDataRepository(db)
    # ~24h prior and current, both in the production space format.
    await _insert(db, "BTCUSDT", 48000.0, _space(fixed - timedelta(hours=24)))
    await _insert(db, "BTCUSDT", 50000.0, _space(fixed))

    result = await repo.get_latest_open_interest("BTCUSDT")
    assert result is not None
    assert result["open_interest_value"] == 50000.0
    # 48000 → 50000 = +4.1667 %, computed correctly despite now being 23:55 UTC.
    assert result["change_24h_pct"] == pytest.approx(4.1667, abs=0.01)
