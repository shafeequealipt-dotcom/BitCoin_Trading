"""Fixtures for Phase 1 database tests."""

import pytest
import pytest_asyncio

from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations


@pytest_asyncio.fixture
async def test_db(tmp_path):
    db = DatabaseManager(str(tmp_path / "phase1_test.db"))
    await db.connect()
    await run_migrations(db)
    yield db
    await db.disconnect()
