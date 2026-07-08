"""Pipeline-4 — Live R3 trace through REAL DatabaseManager + real SQLite.

Builds a temp SQLite with the trade_log schema, inserts realistic rows,
and runs the production _derive_wr_aware_override_threshold helper
against that database. Verifies cold-start fallback and live WR
derivation behaviour through the actual production data path.
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.settings import Settings
from src.database.connection import DatabaseManager
from src.database.migrations import MIGRATIONS
from src.workers.strategy_worker import StrategyWorker


class _Probe:
    """Minimal object exposing the attributes _derive_wr_aware_override_threshold reads."""
    def __init__(self, db, settings):
        self.services = {"db": db}
        self.settings = settings


_SEED_COUNTER = [0]  # mutable counter to guarantee unique trade_id across calls


async def _seed_trade_log(db: DatabaseManager, buy_wins: int, buy_losses: int,
                          sell_wins: int, sell_losses: int) -> None:
    """Insert canned trade_log rows with realistic pnl_usd shapes."""
    rows: list[tuple] = []
    for i in range(buy_wins):
        _SEED_COUNTER[0] += 1
        rows.append((
            f"trade-{_SEED_COUNTER[0]}", "TESTUSDT", "Buy", 1.0, 1.02, 100.0, 3, 2.0, 5.0,
            "test", "test thesis", "tp", 10.0, "2026-05-10T00:00:00", "2026-05-10T00:10:00",
        ))
    for i in range(buy_losses):
        _SEED_COUNTER[0] += 1
        rows.append((
            f"trade-{_SEED_COUNTER[0]}", "TESTUSDT", "Buy", 1.0, 0.98, 100.0, 3, -2.0, -5.0,
            "test", "test thesis", "sl", 10.0, "2026-05-10T01:00:00", "2026-05-10T01:10:00",
        ))
    for i in range(sell_wins):
        _SEED_COUNTER[0] += 1
        rows.append((
            f"trade-{_SEED_COUNTER[0]}", "TESTUSDT", "Sell", 1.0, 0.98, 100.0, 3, 2.0, 5.0,
            "test", "test thesis", "tp", 10.0, "2026-05-10T02:00:00", "2026-05-10T02:10:00",
        ))
    for i in range(sell_losses):
        _SEED_COUNTER[0] += 1
        rows.append((
            f"trade-{_SEED_COUNTER[0]}", "TESTUSDT", "Sell", 1.0, 1.02, 100.0, 3, -2.0, -5.0,
            "test", "test thesis", "sl", 10.0, "2026-05-10T03:00:00", "2026-05-10T03:10:00",
        ))
    for r in rows:
        await db.execute(
            "INSERT INTO trade_log (trade_id, symbol, direction, entry_price, "
            "exit_price, size_usd, leverage, pnl_pct, pnl_usd, strategy, thesis, "
            "close_reason, hold_minutes, opened_at, closed_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            r,
        )


async def main() -> None:
    print("=== R3 LIVE PIPELINE — _derive_wr_aware_override_threshold ===")
    print("=== against REAL DatabaseManager + REAL SQLite trade_log table ===\n")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "audit_R3.db")
        db = DatabaseManager(db_path=db_path)
        await db.connect()
        try:
            # Apply real migrations so trade_log table exists with the
            # production schema.
            for sql in MIGRATIONS:
                await db.execute(sql)

            settings = Settings()
            probe = _Probe(db, settings)
            probe._derive_wr_aware_override_threshold = (
                StrategyWorker._derive_wr_aware_override_threshold.__get__(probe)
            )

            # ============================================================
            print("--- Scenario A: empty trade_log (cold-start fallback path) ---")
            t, m = await probe._derive_wr_aware_override_threshold("Buy")
            print(f"  threshold={t}, source={m['source']}, buy_n={m['buy_n']}, sell_n={m['sell_n']}")
            assert t == 10.0 and m["source"] == "cold_start"
            print("  PASS — cold-start fallback to legacy 10.0\n")

            # ============================================================
            print("--- Scenario B: < window_min trades for one direction ---")
            await _seed_trade_log(db, buy_wins=15, buy_losses=10, sell_wins=0, sell_losses=0)  # 25 Buy total, 0 Sell
            t, m = await probe._derive_wr_aware_override_threshold("Buy")
            print(f"  Buy=25 trades (< window_min 30) -> threshold={t}, source={m['source']}, "
                  f"buy_wr={m['buy_wr']}, buy_n={m['buy_n']}")
            assert m["source"] == "cold_start" and t == 10.0
            print("  PASS — below window_min => cold-start fallback\n")

            # ============================================================
            print("--- Scenario C: live WR derivation (Buy 70% WR, > window_min) ---")
            # Already have 25 Buys (15W/10L = 60%). Add 10 more Buys all wins to push to 70%.
            await _seed_trade_log(db, buy_wins=10, buy_losses=0, sell_wins=0, sell_losses=0)
            # Now 25 wins / 35 total = 71.4% Buy WR.
            t, m = await probe._derive_wr_aware_override_threshold("Buy")
            print(f"  Buy=35 (71.4% WR) -> threshold={t}, source={m['source']}, "
                  f"buy_wr={m['buy_wr']}, buy_n={m['buy_n']}")
            # 10.0 * (1 - 0.714) ~= 2.86, clamped at floor 2.0
            assert m["source"] == "wr", f"Expected source=wr, got {m['source']}"
            assert 2.0 <= t <= 3.5, f"Expected ~2.86, got {t}"
            print(f"  PASS — WR-aware: high Buy WR ({m['buy_wr']}%) lowered threshold to {t}\n")

            # ============================================================
            print("--- Scenario D: symmetric Sell WR test (Sell 60%) ---")
            await _seed_trade_log(db, buy_wins=0, buy_losses=0, sell_wins=18, sell_losses=12)
            t, m = await probe._derive_wr_aware_override_threshold("Sell")
            print(f"  Sell=30 (60% WR) -> threshold={t}, source={m['source']}, sell_wr={m['sell_wr']}")
            # 10.0 * (1 - 0.6) = 4.0
            assert m["source"] == "wr"
            assert 3.5 <= t <= 4.5, f"Expected ~4.0, got {t}"
            print(f"  PASS — symmetric formula applies to Sell side\n")

        finally:
            await db.disconnect()

    print("=== R3 LIVE PIPELINE: GREEN — real production code path exercises real DatabaseManager.fetch_all ===")


if __name__ == "__main__":
    asyncio.run(main())
