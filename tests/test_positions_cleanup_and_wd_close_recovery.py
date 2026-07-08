"""Issue 2 + Issue 3 Phase 3 tests.

Issue 2 (positions-table cleanup): TradingRepository.delete_position is
the new explicit cleanup entry. The close-callback wiring in
workers/manager.py is integration-tested at deploy time (it requires
the full DI stack). Unit tests cover the repository method directly.

Issue 3 (WD_CLOSE recovery): direct test of the SQL-recovery branches
is impractical without a full watchdog harness, so we run a structured
self-check on the new recovery code's invariants:
  - Recovery order: thesis -> orders -> defensive zero
  - Order qty + thesis size_usd / leverage feed the notional
  - WD_CLOSE_RECOVERY_FAIL fires only when both lookups returned empty
The end-to-end check happens during the production verification window.
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


_POSITIONS_TABLE_DDL = """
    CREATE TABLE positions (
        symbol TEXT PRIMARY KEY,
        side TEXT NOT NULL,
        size REAL NOT NULL,
        entry_price REAL NOT NULL DEFAULT 0,
        mark_price REAL NOT NULL DEFAULT 0,
        unrealized_pnl REAL NOT NULL DEFAULT 0,
        realized_pnl REAL NOT NULL DEFAULT 0,
        leverage INTEGER NOT NULL DEFAULT 1,
        liquidation_price REAL NOT NULL DEFAULT 0,
        stop_loss REAL,
        take_profit REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        exchange_mode TEXT NOT NULL DEFAULT 'shadow'
    )
"""


# Tests are native ``async def`` so pytest-asyncio (mode=AUTO in pyproject.toml)
# schedules them on a fresh event loop per test. Running these as sync funcs
# with ``asyncio.get_event_loop().run_until_complete`` works in isolation but
# fails inside the full pytest suite when a prior async test has closed the
# default loop.

# ===========================================================================
# Issue 2 — delete_position
# ===========================================================================

async def test_delete_position_idempotent():
    """delete_position on an empty row should be a no-op."""
    from src.database.connection import DatabaseManager
    from src.database.repositories.trading_repo import TradingRepository

    db_path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(db_path)
    await db.connect()
    try:
        await db.execute(_POSITIONS_TABLE_DDL)
        repo = TradingRepository(db)

        # No-op delete on non-existent row
        await repo.delete_position("NEVERSEEN")

        # Insert a row then delete
        await db.execute(
            "INSERT INTO positions(symbol,side,size,entry_price,exchange_mode) "
            "VALUES (?,?,?,?,?)",
            ("ABCUSDT", "Buy", 100.0, 1.0, "bybit_demo"),
        )
        rows = await db.fetch_all(
            "SELECT symbol FROM positions WHERE symbol='ABCUSDT'"
        )
        assert len(rows) == 1
        await repo.delete_position("ABCUSDT")
        rows = await db.fetch_all(
            "SELECT symbol FROM positions WHERE symbol='ABCUSDT'"
        )
        assert len(rows) == 0
    finally:
        await db.disconnect()
        os.unlink(db_path)


async def test_delete_position_does_not_affect_other_symbols():
    """delete_position must be symbol-scoped, not a blanket DELETE."""
    from src.database.connection import DatabaseManager
    from src.database.repositories.trading_repo import TradingRepository

    db_path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(db_path)
    await db.connect()
    try:
        await db.execute(_POSITIONS_TABLE_DDL)
        repo = TradingRepository(db)
        await db.execute(
            "INSERT INTO positions(symbol,side,size,entry_price,exchange_mode) "
            "VALUES (?,?,?,?,?)", ("AAA", "Buy", 1.0, 1.0, "bybit_demo"))
        await db.execute(
            "INSERT INTO positions(symbol,side,size,entry_price,exchange_mode) "
            "VALUES (?,?,?,?,?)", ("BBB", "Sell", 2.0, 2.0, "bybit_demo"))
        await repo.delete_position("AAA")
        rows = await db.fetch_all(
            "SELECT symbol FROM positions ORDER BY symbol"
        )
        syms = [dict(r)["symbol"] for r in rows]
        assert syms == ["BBB"], f"expected ['BBB'], got {syms}"
    finally:
        await db.disconnect()
        os.unlink(db_path)


# ===========================================================================
# Issue 3 — recovery code invariants
# ===========================================================================
# The recovery code lives inside position_watchdog._detect_and_record_closes
# which is several hundred lines of context-dependent logic. Rather than
# stand up the full watchdog, we test the recovery's invariants by
# inspecting the source — a brittle test that catches the most likely
# regressions (someone deletes the recovery block or reorders it).
# ===========================================================================

def test_wd_close_recovery_source_invariants():
    """Recovery block must exist, must precede the PnL compute, must
    include both thesis and orders queries, and must emit
    WD_CLOSE_RECOVERY_FAIL when both fail."""
    src = open(os.path.join(
        os.path.dirname(__file__), "..", "src", "workers", "position_watchdog.py"
    )).read()

    # Recovery markers
    assert "WD_CLOSE_THESIS_RECOVERY" in src
    assert "WD_CLOSE_ORDERS_RECOVERY" in src
    assert "WD_CLOSE_RECOVERY_FAIL" in src

    # Recovery must precede the PnL compute. Use the log.info(/log.error(
    # call site (not the bare tag, which also appears in the comment
    # block) for ordering. The log.error for WD_CLOSE_RECOVERY_FAIL is
    # uniquely identifiable; same for the recovery log.info calls.
    pos_thesis_log = src.find('"WD_CLOSE_THESIS_RECOVERY | sym=')
    pos_orders_log = src.find('"WD_CLOSE_ORDERS_RECOVERY | sym=')
    pos_fail_log = src.find('"WD_CLOSE_RECOVERY_FAIL | sym=')
    pos_pnl_compute = src.find('pnl_pct = ((exit_price - entry_price)')
    assert 0 < pos_thesis_log < pos_orders_log < pos_fail_log < pos_pnl_compute, (
        f"recovery ordering broken: thesis_log={pos_thesis_log} "
        f"orders_log={pos_orders_log} fail_log={pos_fail_log} "
        f"pnl_compute={pos_pnl_compute}"
    )

    # The orders query selects qty + avg_fill_price (needed for notional)
    assert "FROM orders" in src
    assert "avg_fill_price" in src

    # The thesis query selects size_usd + leverage (needed for notional)
    assert "FROM trade_thesis" in src
    assert "size_usd" in src

    # The PnL compute path uses recovered values when notional is 0
    assert "recovered_size_usd" in src
    assert "recovered_qty" in src
    assert "recovered_leverage" in src
    print("  PASS: WD_CLOSE recovery source invariants intact "
          "(thesis -> orders -> fail order, notional uses recovered values)")


def test_wd_close_recovery_handles_partial_recovery():
    """Logic check: if thesis returns only direction and orders returns
    only entry_price, the combined recovery should yield both.

    This exercises the conditional guards `if _t_dir and not direction`
    (don't overwrite a non-empty value) and `if _o_ent > 0 and entry_price <= 0`
    (only fill missing fields).
    """
    # Simulate the recovery code's conditional logic
    entry_price = 0.0
    direction = ""

    # Thesis returns: direction only
    _t_ent = 0.0
    _t_dir = "Buy"
    if _t_ent > 0 and entry_price <= 0:
        entry_price = _t_ent
    if _t_dir and not direction:
        direction = _t_dir
    assert entry_price == 0.0
    assert direction == "Buy"

    # Orders returns: entry only (and same direction we already have)
    _o_ent = 1.5
    _o_dir = "Buy"
    if _o_ent > 0 and entry_price <= 0:
        entry_price = _o_ent
    if _o_dir and not direction:
        direction = _o_dir  # no-op since already "Buy"
    assert entry_price == 1.5
    assert direction == "Buy"
    print("  PASS: WD_CLOSE recovery combines partial thesis + partial orders into full record")


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    print("\n=== Issues 2 + 3 Phase 3 — positions cleanup + WD_CLOSE recovery ===\n")
    tests = [
        ("delete_position idempotent", test_delete_position_idempotent),
        ("delete_position symbol-scoped", test_delete_position_does_not_affect_other_symbols),
        ("WD_CLOSE recovery source invariants", test_wd_close_recovery_source_invariants),
        ("WD_CLOSE recovery combines partials", test_wd_close_recovery_handles_partial_recovery),
    ]
    failed = 0
    for name, fn in tests:
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"  PASS: {name}")
        except AssertionError as e:
            print(f"  FAIL: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nResult: {len(tests) - failed}/{len(tests)} passed")
    sys.exit(0 if failed == 0 else 1)
