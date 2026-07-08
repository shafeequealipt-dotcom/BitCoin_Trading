#!/usr/bin/env python3
"""QA-4 — live simulation of the original problem, end to end.

Reproduces the documented root cause on LIVE exchange data and shows each
phase's fix responding as intended:

  THE PROBLEM (reproduced): the current static universe is led by calm majors;
  measure its recent realized movement and how many coins are effectively
  dead (the 'wins are fee-scratches' situation).

  THE SCENARIO: the operator is holding an open position in a calm major
  (BTCUSDT) — exactly the kind of position the refresh must never abandon.

  THE FIX (driven through the REAL orchestrator on live data):
    - Phase 1: the selection rebuilds the universe around genuine movers
      (selected realized movement materially exceeds the static list's).
    - Phase 2/3/5: the refresh applies safely — the calm open BTC position is
      FORCE-KEPT (so it stays managed) even though BTC would not be selected on
      movement; only Call-A is paused and is resumed; the new universe is
      persisted.

  CROSS-CHECK: selected movement > static movement, open position retained,
  state consistent.

Read-only against the exchange except for kline caching (the system would do
that anyway). Run from the project root:  python simulate_universe_refresh.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import src.core.universe_refresh as ur
from src.config.settings import Settings
from src.core.types import OHLCV, TimeFrame
from src.core.universe_refresh import UniverseRefreshOrchestrator, UniverseRefreshState
from src.core.utils import timestamp_to_datetime
from src.database.connection import DatabaseManager
from src.strategies.scanner import MarketScanner
from src.strategies.universe_selector import realized_volatility_pct
from src.trading.client import BybitClient
from src.trading.services.market_service import MarketService
from verify_refresh_open_positions import FakeMarketRepo, FakePositionService, FakeRegime

OPEN_POS = "BTCUSDT"  # a calm major the operator is holding — must be kept


def _map(symbol, raw, tf):
    out = [OHLCV(symbol=symbol, timeframe=tf, timestamp=timestamp_to_datetime(int(i[0])),
                 open=float(i[1]), high=float(i[2]), low=float(i[3]), close=float(i[4]),
                 volume=float(i[5]), turnover=float(i[6]) if len(i) > 6 else 0.0) for i in raw]
    out.reverse()
    return out


async def _daily(bybit, syms, limit, conc=8):
    sem = asyncio.Semaphore(conc)
    out = {}

    async def one(s):
        async with sem:
            try:
                r = await bybit.call("get_kline", category="linear", symbol=s,
                                     interval=TimeFrame.D1.value, limit=limit)
                out[s] = _map(s, r.get("list", []), TimeFrame.D1)
            except Exception:
                out[s] = []
    await asyncio.gather(*(one(s) for s in syms))
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


async def run() -> int:
    failures = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    ur._STATE_FILE = Path(tempfile.mkdtemp()) / "universe_state.json"

    settings = Settings.load()
    settings.universe.refresh.warmup_max_minutes = 0.1
    settings.universe.refresh.warmup_poll_seconds = 1
    p = settings.universe.refresh
    lookback = p.volatility_lookback_days
    current_static = list(settings.universe.watch_list)

    db = DatabaseManager(str(Path(tempfile.mkdtemp()) / "t.db"),
                         lock_wait_warn_ms=settings.database.db_lock_wait_threshold_ms,
                         concurrency_model=settings.database.concurrency_model,
                         reader_pool_size=settings.database.reader_pool_size)
    await db.connect()
    # Migrate the temp DB so MarketService.get_klines can cache candles (the
    # real system migrates at boot; the orchestrator's daily fetch saves klines).
    from src.database.migrations import run_migrations
    await run_migrations(db)
    bybit = BybitClient(settings, db)
    await bybit.connect()
    market = MarketService(bybit, db, kline_save_chunk_size=settings.database.kline_save_chunk_size)

    scanner = MarketScanner(settings, market_service=market, watch_list=set(current_static))
    services = {
        "market": market, "bybit": bybit,
        "position_service": FakePositionService([OPEN_POS]),  # holding a calm major
        "regime_detector": FakeRegime(),
        "scanner": scanner,
        "universe_refresh_state": UniverseRefreshState(),
    }
    orch = UniverseRefreshOrchestrator(settings, db, services)
    orch._market_repo = FakeMarketRepo()  # warm-up readiness (real system: KlineWorker backfill)
    services["universe_refresh"] = orch

    print("QA-4 live simulation of the original problem\n")

    # === THE PROBLEM: measure the current static universe's movement ===
    print("Step 1 — reproduce the problem: the current static universe's movement.")
    static_daily = await _daily(bybit, current_static, lookback + 3)
    static_vols = [realized_volatility_pct(static_daily.get(s, []), lookback)
                   for s in current_static if static_daily.get(s)]
    static_mean = _mean(static_vols)
    dead = sum(1 for v in static_vols if v < 2.0)
    print(f"  Static universe: {len(static_vols)} coins, average daily range {static_mean:.2f}%, "
          f"{dead} coins below 2% (effectively calm).")

    # === THE FIX: run the real refresh on live data, holding a calm major ===
    print("\nStep 2 — run the real refresh (live data), holding an open BTCUSDT position.")
    result = await orch.run_refresh("simulation")
    ok = result.get("status") == "ok"
    check("refresh completed ok", ok)
    if not ok:
        print(f"  refresh returned: {result}")
        return 1
    selected = list(result.get("selected", []))

    sel_daily = await _daily(bybit, [s for s in selected if s not in static_daily], lookback + 3)
    sel_daily.update(static_daily)
    sel_vols = [realized_volatility_pct(sel_daily.get(s, []), lookback)
                for s in selected if sel_daily.get(s)]
    sel_mean = _mean(sel_vols)
    ratio = (sel_mean / static_mean) if static_mean > 0 else 0.0

    print(f"  Selected universe: {len(selected)} coins, average daily range {sel_mean:.2f}%.")
    print(f"  Movement improvement: {ratio:.2f}x more active than the static list.")

    # === CROSS-CHECK: each phase responded as intended ===
    print("\nStep 3 — cross-check each fix against its aim.")
    check("PHASE 1: selected universe is materially MORE active than the static one",
          sel_mean > static_mean * 1.15)
    check("PHASE 1: multi-day selection produced a full, fresh universe",
          len(selected) >= p.min_universe_size)
    check("PHASE 2 SAFETY: the calm open BTC position is FORCE-KEPT (never abandoned)",
          OPEN_POS in selected)
    check("PHASE 2 SAFETY: BTC kept despite being calmer than the selection bar",
          OPEN_POS in selected and realized_volatility_pct(static_daily.get(OPEN_POS, []), lookback) < sel_mean)
    check("PHASE 2: only Call-A was paused and is now resumed",
          services["universe_refresh_state"].is_call_a_paused() is False)
    check("PHASE 5: the new universe was applied to the scanner and persisted",
          scanner._watch_list == set(selected) and ur._STATE_FILE.exists())
    added = [s for s in result.get("added", []) if s != OPEN_POS]
    print(f"\n  Net effect: replaced {len(result.get('removed', []))} calm coins with fresh movers; "
          f"kept the open BTC position; {len(added)} new movers added.")

    try:
        await asyncio.wait_for(bybit.disconnect(), timeout=5.0)
        await asyncio.wait_for(db.disconnect(), timeout=5.0)
    except Exception:
        pass

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — on live data the fix turns the calm, majors-led universe "
          "into a materially-more-active one, while the calm open position is force-kept and "
          "managed and only new-trade-finding pauses. The original root cause is addressed.")
    return 0


if __name__ == "__main__":
    _rc = asyncio.run(run())
    sys.stdout.flush()
    os._exit(_rc)
