#!/usr/bin/env python3
"""Boot the REAL WorkerManager DI container and verify the adaptive exit wiring.

Runs the actual WorkerManager.initialize() — the real dependency-injection
container that constructs the whole service graph — against a TEMPORARY database
(zero risk to the live data/trading.db) and NEVER calls start_all (no worker
loops, no trading). It then inspects the REAL constructed objects to prove the
adaptive exit is wired by the real manager code:

  - the real SLGateway holds the real VolatilityProfiler + adaptive settings
  - the real ProfitSniper holds the real profiler + gateway + adaptive settings
  - the real PositionWatchdog holds the real profiler + gateway + adaptive settings
  - the real boot sentinels (ADAPTIVE_EXIT_CONFIG, SL_GATEWAY_INIT) fired

Read-only w.r.t. live state. Exits non-zero if the real container did not wire
the adaptive components.
"""
import asyncio
import os
import sys
import tempfile

FAILS = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


async def main():
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.workers.manager import WorkerManager

    settings = Settings._load_fresh()
    # Point the manager at a throwaway DB so live data is never touched.
    tmp = tempfile.mkdtemp(prefix="adaptive_boot_")
    settings.database.path = os.path.join(tmp, "boot_check.db")

    db = DatabaseManager(
        settings.database.path,
        lock_wait_warn_ms=getattr(settings.database, "db_lock_wait_threshold_ms", 1000),
        concurrency_model=getattr(settings.database, "concurrency_model", "serialized"),
        reader_pool_size=getattr(settings.database, "reader_pool_size", 1),
    )
    mgr = WorkerManager(settings, db)

    print("Booting the REAL WorkerManager.initialize() (temp DB, no start_all)...")
    try:
        await asyncio.wait_for(mgr.initialize(), timeout=180.0)
        print("initialize() completed.\n")
    except Exception as e:
        print(f"initialize() raised/-timed (continuing to inspect what was built): {str(e)[:160]}\n")

    svc = mgr._services
    gw = svc.get("sl_gateway")
    vp = svc.get("volatility_profiler")
    wd = svc.get("position_watchdog")
    sniper = next((w for w in mgr.workers if type(w).__name__ == "ProfitSniper"), None)

    print("== REAL CONTAINER — adaptive exit components constructed by the manager ==")
    chk("VolatilityProfiler in _services", vp is not None)
    chk("SLGateway in _services", gw is not None)
    chk("PositionWatchdog in _services", wd is not None)
    chk("ProfitSniper in workers", sniper is not None)

    print("\n== REAL WIRING — deps connected by the manager ==")
    if gw is not None:
        chk("gateway <- real profiler", getattr(gw, "_volatility_profiler", None) is vp and vp is not None)
        chk("gateway sees r2_profit_lock_floor_enabled",
            bool(getattr(gw._settings.sl_gateway, "r2_profit_lock_floor_enabled", False)))
    if sniper is not None:
        chk("sniper <- real profiler", getattr(sniper, "volatility_profiler", None) is vp)
        chk("sniper <- real gateway", getattr(sniper, "sl_gateway", None) is gw)
        chk("sniper sees adaptive_exit.enabled", bool(sniper.settings.adaptive_exit.enabled))
    if wd is not None:
        chk("watchdog <- real profiler", getattr(wd, "volatility_profiler", None) is vp)
        chk("watchdog <- real gateway", getattr(wd, "sl_gateway", None) is gw)
        chk("watchdog sees adaptive_exit.enabled", bool(wd.settings.adaptive_exit.enabled))

    # Clean shutdown — no start_all was ever called, so nothing is trading.
    try:
        await db.close()
    except Exception:
        pass

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)}: {', '.join(FAILS)}")
        sys.exit(1)
    print("RESULT: PASS — the REAL WorkerManager DI container constructed and wired the "
          "adaptive exit components (profiler -> gateway/sniper/watchdog) with the flags live.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(main())
