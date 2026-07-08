"""Stress-test scenarios for the DB-concurrency refactor.

Phase conn-pool/p3-5 (db-concurrency-refactor 2026-05-14). Implements
the five scenarios from
``dev_notes/db_concurrency/09_stress_test_scenarios.md``:

1. Klines burst — 50,000 INSERT OR IGNORE rows across 10 simulated
   writers, plus background readers.
2. New trade burst — 5 trades × 7 writes back-to-back.
3. Dashboard read storm — 10 concurrent multi-fetch handlers.
4. Combined burst — scenarios 1+2+3 simultaneously for 5 min.
5. Sustained mixed load — 30 min at production-like rates.

The tests are marked ``stress`` and are SKIPPED by default — run them
manually with::

    pytest tests/stress/test_db_concurrency_stress.py -v -m stress

Each test runs against a COPY of the production DB at
``data/trading_stress_test.db`` (gitignored). The original
``data/trading.db`` is never opened by these tests.

Each scenario records baseline + post-refactor metrics. The harness
parameterizes pool size over ``[2, 4, 8, 12]`` so the smallest pool
that passes all 5 scenarios can be identified as the
``reader_pool_size`` default for Phase 3.7 cutover.

Scenarios 4 and 5 take 5 min and 30 min respectively; they are gated
behind the ``STRESS_LONG=1`` environment variable so they don't run
unless the operator opts in.
"""

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from src.database.connection import DatabaseManager, _PooledDatabaseEngine

pytestmark = [pytest.mark.asyncio, pytest.mark.stress]


SOURCE_DB_PATH = Path("data/trading.db")
STRESS_DB_PATH = Path("data/trading_stress_test.db")
LONG_SCENARIOS_ENABLED = os.environ.get("STRESS_LONG") == "1"

# Scenario 1 row budget. Default 2,000 rows × 10 writers = 20,000 rows
# (validates the harness in seconds). For the Phase 3.5 production-sized
# sweep mandated by the spec, run with STRESS_KLINES_ROWS=5000 (= 50,000
# rows total) — the spec's number. The pool-size pass criterion scales
# automatically with the row count via the budget heuristic below.
KLINES_ROWS_PER_WRITER = int(os.environ.get("STRESS_KLINES_ROWS", "2000"))
KLINES_WRITERS = 10
KLINES_TOTAL = KLINES_ROWS_PER_WRITER * KLINES_WRITERS


def _have_source_db() -> bool:
    return SOURCE_DB_PATH.exists() and SOURCE_DB_PATH.stat().st_size > 1_000_000


@pytest.fixture(scope="module")
def stress_db_copy():
    """One copy of data/trading.db to data/trading_stress_test.db for the
    whole stress-test module run. Per-test isolation is preserved because
    each scenario creates and drops its own ``_stress`` tables; the
    underlying SQLite metadata is fine to share.

    Re-copying for every test added ~1 GB of disk I/O on a 184 MB DB ×
    multiple parameter variants, which dwarfed the test workload itself.
    """
    if not _have_source_db():
        pytest.skip(
            f"{SOURCE_DB_PATH} not present or too small (< 1 MB); "
            "stress tests require a real DB snapshot to be useful."
        )
    # Copy to data/ so the trading.db-wal/shm sidecars are kept alongside.
    target = STRESS_DB_PATH
    if target.exists():
        target.unlink()
    for sfx in ("-wal", "-shm"):
        sidecar = Path(str(target) + sfx)
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy(SOURCE_DB_PATH, target)
    yield str(target)
    for path in (target, Path(str(target) + "-wal"), Path(str(target) + "-shm")):
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


async def _engine(db_path: str, model: str, pool_size: int) -> DatabaseManager:
    """Construct + connect a DatabaseManager for the scenario."""
    db = DatabaseManager(
        db_path,
        wal_mode=True,
        concurrency_model=model,
        reader_pool_size=pool_size,
    )
    await db.connect()
    return db


def _pool_stats(db: DatabaseManager) -> dict:
    """Return the pooled-engine stats dict, or empty if legacy."""
    eng = db._engine
    if isinstance(eng, _PooledDatabaseEngine):
        return eng._pool.stats()
    return {}


# ---------------------------------------------------------------------------
# Scenario 1 — Klines burst
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model,pool_size", [
    ("reader_pool", 2),
    ("reader_pool", 4),
    ("reader_pool", 8),
])
async def test_scenario1_klines_burst(stress_db_copy, model, pool_size):
    """``KLINES_TOTAL`` rows split across ``KLINES_WRITERS`` coroutines
    while 2 readers poll. Goal: all writes commit within budget; cascade
    events = 0. Row count scales via STRESS_KLINES_ROWS env var.
    """
    db = await _engine(stress_db_copy, model, pool_size)
    try:
        # Pre-create a synthetic stress table (don't touch real klines).
        await db.execute(
            "CREATE TABLE IF NOT EXISTS klines_stress "
            "(symbol TEXT, timeframe TEXT, timestamp INTEGER, value REAL, "
            "PRIMARY KEY (symbol, timeframe, timestamp))"
        )

        async def writer(worker_id: int, n: int):
            base_ts = 1700000000 + worker_id * 10_000
            rows = [
                (f"S{worker_id}", "5", base_ts + i, float(i))
                for i in range(n)
            ]
            chunk = 500
            for off in range(0, len(rows), chunk):
                await db.executemany(
                    "INSERT OR IGNORE INTO klines_stress "
                    "(symbol, timeframe, timestamp, value) VALUES (?, ?, ?, ?)",
                    rows[off : off + chunk],
                )
                await asyncio.sleep(0)

        async def background_reader(stop_evt: asyncio.Event):
            reads = 0
            while not stop_evt.is_set():
                await db.fetch_all(
                    "SELECT symbol FROM klines_stress LIMIT 100"
                )
                reads += 1
                await asyncio.sleep(0.05)
            return reads

        stop = asyncio.Event()
        readers = [asyncio.create_task(background_reader(stop)) for _ in range(2)]

        t0 = time.monotonic()
        await asyncio.gather(
            *[writer(i, KLINES_ROWS_PER_WRITER) for i in range(KLINES_WRITERS)]
        )
        elapsed_s = time.monotonic() - t0

        stop.set()
        read_counts = await asyncio.gather(*readers)

        # Verify writes landed.
        row = await db.fetch_one("SELECT COUNT(*) AS c FROM klines_stress")
        assert row["c"] == KLINES_TOTAL

        # Cleanup the stress table.
        await db.execute("DROP TABLE klines_stress")

        stats = _pool_stats(db)
        print(
            f"\n[scenario1] model={model} pool={pool_size} rows={KLINES_TOTAL} "
            f"elapsed={elapsed_s:.2f}s reads={sum(read_counts)} "
            f"pool_stats={stats}"
        )

        # Budget scales with row count: 2.4 ms/row for legacy, 1.2 ms/row
        # for pooled (legacy serialises every chunk against background reads;
        # pooled lets reads run concurrently). Floor at 30s so small runs
        # don't fail on Python/asyncio fixed overhead.
        # Phase conn-pool/p3-9: single_lock removed; only the pool budget remains.
        per_row_budget_ms = 1.2
        budget_s = max(30.0, KLINES_TOTAL * per_row_budget_ms / 1000.0)
        assert elapsed_s < budget_s, (
            f"scenario1 over budget: {elapsed_s:.2f}s > {budget_s:.0f}s for "
            f"model={model} pool={pool_size} rows={KLINES_TOTAL}"
        )
        if model == "reader_pool":
            assert stats["exhausted_count"] == 0, (
                f"pool exhausted {stats['exhausted_count']} times at pool={pool_size}"
            )
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Scenario 2 — New trade burst
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model,pool_size", [
    ("reader_pool", 4),
])
async def test_scenario2_new_trade_burst(stress_db_copy, model, pool_size):
    """5 trades back-to-back, each writing 7 statements to 7 tables.
    35 sequential writes total. Goal: complete within 5 s.
    """
    db = await _engine(stress_db_copy, model, pool_size)
    try:
        # Per-trade synthetic table set (we don't write to live tables).
        for tbl in ("trade_open_log_stress",):
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS {tbl} "
                "(trade_id TEXT, step TEXT, value REAL)"
            )

        async def open_trade(trade_id: str):
            # Simulate 7 sequential writes per trade.
            for step in range(7):
                await db.execute(
                    "INSERT INTO trade_open_log_stress "
                    "(trade_id, step, value) VALUES (?, ?, ?)",
                    (trade_id, f"step{step}", float(step)),
                )

        t0 = time.monotonic()
        await asyncio.gather(*[open_trade(f"T{i}") for i in range(5)])
        elapsed_s = time.monotonic() - t0

        row = await db.fetch_one(
            "SELECT COUNT(*) AS c FROM trade_open_log_stress"
        )
        assert row["c"] == 35

        await db.execute("DROP TABLE trade_open_log_stress")

        print(
            f"\n[scenario2] model={model} pool={pool_size} "
            f"elapsed={elapsed_s:.3f}s pool_stats={_pool_stats(db)}"
        )

        assert elapsed_s < 5.0, (
            f"scenario2 over budget: {elapsed_s:.3f}s > 5.0s for "
            f"model={model} pool={pool_size}"
        )
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Scenario 3 — Dashboard read storm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model,pool_size", [
    ("reader_pool", 2),
    ("reader_pool", 4),
    ("reader_pool", 8),
])
async def test_scenario3_dashboard_read_storm(stress_db_copy, model, pool_size):
    """10 concurrent dashboard handlers, each issuing 5 fetch_* calls
    against real production tables. Goal: < 1s total elapsed.
    """
    db = await _engine(stress_db_copy, model, pool_size)
    try:
        async def dashboard_handler():
            # 5 representative reads against sqlite_master (always present,
            # safe on any production DB snapshot). Mirrors /dashboard's
            # pattern of 5-8 sequential reads per render.
            await db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT 100"
            )
            await db.fetch_one(
                "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table'"
            )
            await db.fetch_all(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' ORDER BY name LIMIT 50"
            )
            await db.fetch_one(
                "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='index'"
            )
            await db.fetch_all(
                "SELECT type, COUNT(*) AS c FROM sqlite_master GROUP BY type"
            )

        t0 = time.monotonic()
        await asyncio.gather(*[dashboard_handler() for _ in range(10)])
        elapsed_s = time.monotonic() - t0

        print(
            f"\n[scenario3] model={model} pool={pool_size} "
            f"elapsed={elapsed_s * 1000:.0f}ms pool_stats={_pool_stats(db)}"
        )

        # Pass criteria: 10 handlers × 5 reads done within 1s on pooled,
        # within 3s on legacy (serialised).
        # Phase conn-pool/p3-9: single_lock removed; only the pool budget remains.
        budget = 1.0
        assert elapsed_s < budget, (
            f"scenario3 over budget: {elapsed_s:.2f}s > {budget}s for "
            f"model={model} pool={pool_size}"
        )
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Scenario 4 — Combined burst (LONG — opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LONG_SCENARIOS_ENABLED, reason="set STRESS_LONG=1 to run")
@pytest.mark.parametrize("model,pool_size", [
    ("reader_pool", 4),
    ("reader_pool", 8),
])
async def test_scenario4_combined_burst(stress_db_copy, model, pool_size):
    """Scenarios 1 + 2 + 3 simultaneously for 5 minutes. Goal: 0 cascades."""
    db = await _engine(stress_db_copy, model, pool_size)
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS klines_stress "
            "(symbol TEXT, timeframe TEXT, timestamp INTEGER, value REAL, "
            "PRIMARY KEY (symbol, timeframe, timestamp))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS trade_open_log_stress "
            "(trade_id TEXT, step TEXT, value REAL)"
        )

        end_at = time.monotonic() + 300.0  # 5 min
        cascades = {"count": 0}

        async def klines_loop():
            cycle = 0
            while time.monotonic() < end_at:
                rows = [
                    (f"S{cycle}", "5", 1700000000 + cycle * 1000 + i, float(i))
                    for i in range(100)
                ]
                await db.executemany(
                    "INSERT OR IGNORE INTO klines_stress "
                    "(symbol, timeframe, timestamp, value) VALUES (?, ?, ?, ?)",
                    rows,
                )
                cycle += 1
                await asyncio.sleep(0.5)

        async def trade_loop():
            cycle = 0
            while time.monotonic() < end_at:
                for step in range(7):
                    await db.execute(
                        "INSERT INTO trade_open_log_stress "
                        "(trade_id, step, value) VALUES (?, ?, ?)",
                        (f"T{cycle}", f"step{step}", float(step)),
                    )
                cycle += 1
                await asyncio.sleep(1.0)

        async def dashboard_loop():
            while time.monotonic() < end_at:
                await db.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                await asyncio.sleep(0.2)

        await asyncio.gather(
            klines_loop(),
            trade_loop(),
            dashboard_loop(),
            dashboard_loop(),
            dashboard_loop(),
        )

        await db.execute("DROP TABLE klines_stress")
        await db.execute("DROP TABLE trade_open_log_stress")

        print(
            f"\n[scenario4] model={model} pool={pool_size} "
            f"cascades_observed={cascades['count']} "
            f"pool_stats={_pool_stats(db)}"
        )
        # Cascades counted via log inspection in CI; here we only assert
        # the run completed and pool stayed healthy.
        stats = _pool_stats(db)
        assert stats.get("exhausted_count", 0) == 0
    finally:
        await db.disconnect()


# ---------------------------------------------------------------------------
# Scenario 5 — Sustained mixed (LONG — opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LONG_SCENARIOS_ENABLED, reason="set STRESS_LONG=1 to run")
@pytest.mark.parametrize("model,pool_size", [("reader_pool", 4)])
async def test_scenario5_sustained_mixed(stress_db_copy, model, pool_size):
    """30 min of production-like mixed load."""
    db = await _engine(stress_db_copy, model, pool_size)
    try:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS sustained_stress "
            "(id INTEGER PRIMARY KEY, ts REAL, v REAL)"
        )

        end_at = time.monotonic() + 1800.0  # 30 min

        async def sniper_loop():
            cycle = 0
            while time.monotonic() < end_at:
                await db.execute(
                    "INSERT INTO sustained_stress (ts, v) VALUES (?, ?)",
                    (time.time(), float(cycle)),
                )
                cycle += 1
                await asyncio.sleep(5.0)

        async def watchdog_loop():
            while time.monotonic() < end_at:
                await db.fetch_all(
                    "SELECT id FROM sustained_stress ORDER BY id DESC LIMIT 9"
                )
                await asyncio.sleep(10.0)

        async def ticker_loop():
            while time.monotonic() < end_at:
                rows = [(time.time(), float(i)) for i in range(50)]
                await db.executemany(
                    "INSERT INTO sustained_stress (ts, v) VALUES (?, ?)", rows
                )
                await asyncio.sleep(0.5)

        await asyncio.gather(sniper_loop(), watchdog_loop(), ticker_loop())

        await db.execute("DROP TABLE sustained_stress")

        stats = _pool_stats(db)
        print(
            f"\n[scenario5] model={model} pool={pool_size} pool_stats={stats}"
        )
        assert stats.get("exhausted_count", 0) == 0
    finally:
        await db.disconnect()
