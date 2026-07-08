#!/usr/bin/env python3
"""QA-3 — real-project end-to-end pipeline / DI wiring check.

Wires the REAL project classes the way WorkerManager does — LayerManager,
KlineWorker (a real per-tick watch_list consumer), MarketScanner, the refresh
orchestrator, the scheduled worker, and the Telegram handler — through a
manager-style services dict and a real (temp) database, then drives a refresh
and traces the data flow end to end:

  DI: the LayerManager Call-A guard reads the same universe_refresh_state the
      manager registers; the scheduled worker and handler reach the same
      orchestrator.
  Data flow: a refresh swaps settings.universe AND MarketScanner._watch_list,
      and a REAL KlineWorker's per-tick read (self.settings.universe.watch_list)
      sees the new universe; the open position is kept; the state persists and
      boot-loads back.

Only market/exchange data is faked (no network); everything else is the real
code path. Run from the project root:  python verify_universe_pipeline.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import tempfile
from pathlib import Path

import src.core.universe_refresh as ur
import src.workers.universe_refresh_worker as wmod
from src.config.settings import Settings
from src.core.layer_manager import LayerManager
from src.core.universe_refresh import UniverseRefreshOrchestrator, UniverseRefreshState
from src.database.connection import DatabaseManager
from src.strategies.scanner import MarketScanner
from src.telegram.handlers.universe import UniverseHandler
from src.workers.kline_worker import KlineWorker
from src.workers.universe_refresh_worker import UniverseRefreshWorker
from verify_refresh_open_positions import (
    FakeMarket,
    FakeMarketRepo,
    FakePositionService,
    FakeRegime,
    _fast_warmup,
    _ticker,
    _trend_daily,
)

UTC = dt.timezone.utc


class FakeAlert:
    def __init__(self):
        self.sent = []

    async def send_custom(self, msg, *a, **k):
        self.sent.append(msg)
        return True


async def run() -> int:
    failures = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    ur._STATE_FILE = Path(tempfile.mkdtemp()) / "universe_state.json"

    # --- Real settings + real temp DB ---
    settings = Settings.load()
    _fast_warmup(settings)
    settings.universe.refresh.enabled = True
    db = DatabaseManager(
        str(Path(tempfile.mkdtemp()) / "t.db"),
        lock_wait_warn_ms=settings.database.db_lock_wait_threshold_ms,
        concurrency_model=settings.database.concurrency_model,
        reader_pool_size=settings.database.reader_pool_size,
    )
    await db.connect()

    # --- Fakes only for market/exchange data ---
    pos_coin = "ZZZOPENUSDT"
    trend = [f"TRND{i:02d}USDT" for i in range(30)]
    market = FakeMarket([_ticker(s) for s in trend], {s: _trend_daily(s) for s in trend})

    # --- Real project objects, wired like the manager ---
    scanner = MarketScanner(settings, market_service=market,
                            watch_list=set(settings.universe.watch_list))
    services = {
        "market": market, "bybit": None,
        "position_service": FakePositionService([pos_coin]),
        "regime_detector": FakeRegime(),
        "scanner": scanner,
        "alert_manager": FakeAlert(),
        "universe_refresh_state": UniverseRefreshState(),
    }
    orch = UniverseRefreshOrchestrator(settings, db, services)
    orch._market_repo = FakeMarketRepo()
    services["universe_refresh"] = orch
    layer_manager = LayerManager(settings, services)
    services["layer_manager"] = layer_manager
    kline_worker = KlineWorker(settings, db, market, scanner=scanner)
    sched = UniverseRefreshWorker(settings, db, services)
    handler = UniverseHandler(None, services)

    print("QA-3 real-project pipeline / DI wiring")

    # --- DI wiring ---
    check("LayerManager holds the shared services dict", layer_manager.services is services)
    check("scheduled worker holds the shared services dict", sched.services is services)
    check("handler reaches the same orchestrator", handler._orch() is orch)
    check("KlineWorker holds the shared settings object", kline_worker.settings is settings)

    # The exact predicate the LayerManager Call-A guard evaluates (line ~769).
    st = services["universe_refresh_state"]
    st.pause_call_a("pipeline")
    check("LayerManager guard sees PAUSED via the registered state",
          layer_manager.services.get("universe_refresh_state").is_call_a_paused() is True)
    st.resume_call_a()
    check("LayerManager guard sees RESUMED",
          layer_manager.services.get("universe_refresh_state").is_call_a_paused() is False)

    # --- Data flow: drive a real refresh and trace it to the real consumers ---
    before = list(settings.universe.watch_list)
    kw_before = list(kline_worker.settings.universe.watch_list)
    result = await orch.run_refresh("pipeline")
    check("refresh ok", result.get("status") == "ok")
    new = list(settings.universe.watch_list)
    check("settings.universe swapped", set(new) != set(before))
    check("MarketScanner._watch_list swapped consistently", scanner._watch_list == set(new))
    check("REAL KlineWorker per-tick read now sees the new universe",
          list(kline_worker.settings.universe.watch_list) == new and new != kw_before)
    check("open position force-kept in the live universe", pos_coin in new)
    check("state persisted", ur._STATE_FILE.exists() and pos_coin in ur._STATE_FILE.read_text())

    # --- Scheduled worker reaches the real orchestrator end to end ---
    wmod.now_utc = lambda: dt.datetime(2026, 6, 16, 23, 4, tzinfo=UTC)
    settings.universe.refresh.schedule_hours_utc = [23, 11]
    await sched.tick()
    check("scheduled worker fired the real orchestrator (slot committed)",
          sched._last_fired_slot == "2026-06-16-23")

    # --- Boot-load round-trip via the real loader ---
    from workers import _load_persisted_universe
    from src.core.logging import get_logger
    fresh = Settings._load_fresh()
    fresh.universe.refresh.enabled = True
    _load_persisted_universe(fresh, get_logger("verify"), state_path=str(ur._STATE_FILE))
    check("boot-load restores the persisted universe into a fresh settings",
          set(fresh.universe.watch_list) == set(new))

    try:
        await asyncio.wait_for(db.disconnect(), timeout=5.0)
    except Exception:
        pass

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — real LayerManager/KlineWorker/MarketScanner/orchestrator/"
          "scheduler/handler are correctly wired; a refresh flows through to the real consumers "
          "and persists/boot-loads.")
    return 0


if __name__ == "__main__":
    import os
    _rc = asyncio.run(run())
    # Force exit past the real DB reader-pool threads (non-daemon) so the
    # verification process terminates cleanly after all checks complete.
    sys.stdout.flush()
    os._exit(_rc)
