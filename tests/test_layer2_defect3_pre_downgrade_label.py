"""Layer 2 Defect 3 — pre-downgrade signal label as queryable columns.

The forensic investigation found D3 is NOT a data-loss bug: every signal
event reaches save_signal(). The apparent 60% gap is the Phase 29 confidence
gate downgrading e.g. strong_buy at conf=0.43 → NEUTRAL before the write.

Pre-fix the pre-downgrade label lived only in the components JSON
(``original_signal_type``); Layer 4 label-quality analysis required parsing
JSON across every row.

Fix: schema v38 promotes both values to top-level columns:
- ``signal_type_pre_downgrade`` — pre-downgrade classifier label
- ``confidence_floor_failed`` — 0/1 flag for the downgrade event
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_v38_adds_pre_downgrade_columns() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import SCHEMA_VERSION, run_migrations

    assert SCHEMA_VERSION >= 38, "D3 introduced schema v38; SCHEMA_VERSION must be >= 38"
    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            rows = await db.fetch_all("PRAGMA table_info(signals)")
            cols = {r["name"] for r in rows}
            assert "signal_type_pre_downgrade" in cols
            assert "confidence_floor_failed" in cols
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_downgraded_signal_persists_pre_downgrade_columns() -> None:
    """A signal that was strong_buy pre-downgrade but persists as neutral
    after the confidence gate must carry both new columns: the original
    label string and the downgrade flag = 1."""
    from src.core.types import Signal, SignalType
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.altdata_repo import AltDataRepository

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            repo = AltDataRepository(db)
            sig = Signal(
                symbol="BTCUSDT",
                signal_type=SignalType.NEUTRAL,        # post-downgrade
                confidence=0.43,                       # below 0.60 strong floor
                source="multi_source_classifier",
                components={
                    "original_signal_type": "strong_buy",  # pre-downgrade
                    "confidence_floor_failed": True,        # downgrade event
                    "confidence_below_strong": True,
                    "confidence_below_buy": False,
                },
                reasoning="[downgraded conf<threshold] Multi-source ...",
                created_at=datetime.now(timezone.utc),
            )
            await repo.save_signal(sig)

            row = await db.fetch_one(
                "SELECT signal_type, signal_type_pre_downgrade, "
                "confidence_floor_failed FROM signals WHERE symbol=?",
                ("BTCUSDT",),
            )
            assert row is not None
            assert row["signal_type"] == "neutral"
            assert row["signal_type_pre_downgrade"] == "strong_buy"
            assert row["confidence_floor_failed"] == 1
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_non_downgraded_signal_persists_flag_zero() -> None:
    """A signal that was BUY pre- AND post-gate must carry
    confidence_floor_failed = 0 and matching pre-downgrade label."""
    from src.core.types import Signal, SignalType
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.altdata_repo import AltDataRepository

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            repo = AltDataRepository(db)
            sig = Signal(
                symbol="ETHUSDT",
                signal_type=SignalType.BUY,
                confidence=0.55,
                source="multi_source_classifier",
                components={
                    "original_signal_type": "buy",
                    "confidence_floor_failed": False,
                },
                reasoning="Multi-source dir=+0.5 active=[fg]",
                created_at=datetime.now(timezone.utc),
            )
            await repo.save_signal(sig)

            row = await db.fetch_one(
                "SELECT signal_type_pre_downgrade, confidence_floor_failed "
                "FROM signals WHERE symbol=?", ("ETHUSDT",),
            )
            assert row is not None
            assert row["signal_type_pre_downgrade"] == "buy"
            assert row["confidence_floor_failed"] == 0
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_legacy_components_with_missing_keys_persists_nulls() -> None:
    """If a future caller passes a Signal with components lacking the
    original_signal_type / confidence_floor_failed keys, the new columns
    must persist NULL (honest absence per Rule 5) rather than crashing
    or defaulting to bogus values."""
    from src.core.types import Signal, SignalType
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.database.repositories.altdata_repo import AltDataRepository

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "smoke.db"))
        await db.connect()
        try:
            await run_migrations(db)
            repo = AltDataRepository(db)
            sig = Signal(
                symbol="SOLUSDT",
                signal_type=SignalType.NEUTRAL,
                confidence=0.5,
                source="legacy",
                components={},
                reasoning="legacy without phase 4B fields",
                created_at=datetime.now(timezone.utc),
            )
            await repo.save_signal(sig)

            row = await db.fetch_one(
                "SELECT signal_type_pre_downgrade, confidence_floor_failed "
                "FROM signals WHERE symbol=?", ("SOLUSDT",),
            )
            assert row is not None
            assert row["signal_type_pre_downgrade"] is None
            assert row["confidence_floor_failed"] is None
        finally:
            await db.disconnect()
