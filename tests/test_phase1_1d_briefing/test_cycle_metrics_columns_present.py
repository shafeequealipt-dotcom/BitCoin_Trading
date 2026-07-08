"""Phase 1 of the 1D briefing rewrite — verify cycle_metrics schema.

Single question this test answers: "After running migrations against a
fresh sqlite file, are the 4 new briefing columns present with the
expected types?" If yes, Phase 4 can populate them via the CycleTracker
flush without DB-side surprises.
"""

import tempfile

import pytest

from src.database.connection import DatabaseManager
from src.database.migrations import SCHEMA_VERSION, run_migrations


_BRIEFING_COLUMNS = {
    "interestingness_p50": "REAL",
    "interestingness_p95": "REAL",
    "state_label_distribution_json": "TEXT",
    "briefing_packages_count": "INTEGER",
}


@pytest.mark.asyncio
async def test_cycle_metrics_briefing_columns_present() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = DatabaseManager(tmp.name, wal_mode=True)
    await db.connect()
    try:
        await run_migrations(db)

        # Verify the schema version was bumped to (at least) 26 — Phase 1
        # of the 1D briefing rewrite. Higher values are accepted to keep
        # the test forward-compatible across later phases.
        row = await db.fetch_one("SELECT MAX(version) AS v FROM schema_version")
        assert row is not None and int(row["v"]) >= 26, (
            f"SCHEMA_VERSION must be >= 26 after Phase 1; got {row}"
        )
        assert SCHEMA_VERSION >= 26

        # Verify each briefing column exists with the expected type.
        # PRAGMA returns rows of (cid, name, type, notnull, dflt_value, pk).
        rows = await db.fetch_all("PRAGMA table_info(cycle_metrics)")
        present = {r["name"]: (r["type"] or "").upper() for r in rows}
        for col, expected_type in _BRIEFING_COLUMNS.items():
            assert col in present, f"missing column {col} in cycle_metrics"
            assert present[col] == expected_type, (
                f"{col} type expected {expected_type}, got {present[col]}"
            )
    finally:
        await db.disconnect()
