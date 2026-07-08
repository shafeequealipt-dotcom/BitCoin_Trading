"""Layer 1 Defect 9 — coin_regime_history schema restore-loss fix.

Pre-fix: coin_regime_history schema (migrations.py v24) had columns
symbol/regime/confidence/adx/choppiness/timestamp. The INSERT only
wrote those five metrics, and the cold-start restore at
regime_worker:111-118 had to fabricate ``atr_percentile=0`` and
``volume_ratio=1.0`` because the SELECT could not surface them.

Fix: schema v36 ALTER TABLE adds ``volume_ratio`` and
``atr_percentile`` columns. INSERT writes all seven metrics. SELECT
projects all seven. Restore reads the persisted values and only
falls back to neutral defaults for pre-fix rows (NULL columns),
which it tags with ``partial_metrics`` in the REGIME_RESTORE_OK log.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_v36_adds_volume_ratio_and_atr_percentile_columns() -> None:
    """After migrations run, coin_regime_history must have the two new
    columns alongside the legacy five metrics."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    assert SCHEMA_VERSION >= 36, (
        "Defect 9 fix introduced schema v36; SCHEMA_VERSION must "
        "be at least 36 going forward."
    )

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)

            rows = await db.fetch_all(
                "PRAGMA table_info(coin_regime_history)"
            )
            cols = {r["name"] for r in rows}
            # Legacy columns must remain.
            for legacy in ("symbol", "regime", "confidence", "adx",
                           "choppiness", "timestamp"):
                assert legacy in cols, (
                    f"Legacy column {legacy!r} missing after v36."
                )
            # New columns required by the restore fix.
            for new in ("volume_ratio", "atr_percentile"):
                assert new in cols, (
                    f"Defect 9 v36 should add {new!r} to "
                    f"coin_regime_history."
                )
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_round_trip_preserves_all_seven_metrics() -> None:
    """INSERT with all seven metrics → SELECT must return all seven
    intact. This is the contract the restore path relies on."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)

            await db.execute(
                """INSERT INTO coin_regime_history
                   (symbol, regime, confidence, adx,
                    choppiness, volume_ratio, atr_percentile)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("BTCUSDT", "trending_up", 0.82, 28.5, 41.2, 1.34, 67.8),
            )

            row = await db.fetch_one(
                """SELECT symbol, regime, confidence, adx,
                          choppiness, volume_ratio, atr_percentile
                   FROM coin_regime_history WHERE symbol=?""",
                ("BTCUSDT",),
            )
            assert row is not None
            assert row["symbol"] == "BTCUSDT"
            assert row["regime"] == "trending_up"
            assert row["confidence"] == pytest.approx(0.82)
            assert row["adx"] == pytest.approx(28.5)
            assert row["choppiness"] == pytest.approx(41.2)
            # The two metrics that historically disappeared on restart.
            assert row["volume_ratio"] == pytest.approx(1.34)
            assert row["atr_percentile"] == pytest.approx(67.8)
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_pre_fix_row_returns_null_metrics_for_restore_path() -> None:
    """Rows written before the v36 fix have NULL volume_ratio and
    atr_percentile. The restore path must tolerate this and fall
    through to neutral defaults — but only for pre-fix rows."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = DatabaseManager(db_path)
        await db.connect()
        try:
            await run_migrations(db)

            # Write a "pre-fix" row using only the legacy column list,
            # leaving volume_ratio and atr_percentile NULL.
            await db.execute(
                """INSERT INTO coin_regime_history
                   (symbol, regime, confidence, adx, choppiness)
                   VALUES (?, ?, ?, ?, ?)""",
                ("ETHUSDT", "ranging", 0.61, 15.0, 58.0),
            )

            row = await db.fetch_one(
                """SELECT volume_ratio, atr_percentile
                   FROM coin_regime_history WHERE symbol=?""",
                ("ETHUSDT",),
            )
            assert row is not None
            assert row["volume_ratio"] is None
            assert row["atr_percentile"] is None
        finally:
            await db.disconnect()
