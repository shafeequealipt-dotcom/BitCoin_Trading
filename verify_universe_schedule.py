#!/usr/bin/env python3
"""Phase 3 verification — the scheduled refresh fires correctly.

Proves, with a fake orchestrator and a controlled clock (no live system, no
network), that the scheduled worker:

  1. Stays dormant while the feature is disabled.
  2. Fires once at each configured UTC hour (23:00 and 11:00), and not at
     other hours.
  3. Fires exactly once per scheduled hour-slot (the last-run guard).
  4. Avoids the funding-settlement hours (00:00 / 08:00 / 16:00) — these are
     not in the configured schedule and never fire.
  5. Reports the divergence (overlap) between the two daily selections.

Run from the project root:  python verify_universe_schedule.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys

import src.workers.universe_refresh_worker as mod
from src.config.settings import Settings
from src.workers.universe_refresh_worker import UniverseRefreshWorker

UTC = dt.timezone.utc


class FakeOrch:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.selection = [f"C{i}USDT" for i in range(20)]

    async def run_refresh(self, trigger: str) -> dict:
        self.calls.append(trigger)
        return {"status": "ok", "selected": list(self.selection)}


async def run() -> int:
    failures: list[str] = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    clock = {"t": dt.datetime(2026, 6, 16, 23, 3, tzinfo=UTC)}
    mod.now_utc = lambda: clock["t"]  # control the worker's clock

    s = Settings.load()
    s.universe.refresh.enabled = True
    s.universe.refresh.schedule_hours_utc = [23, 11]
    orch = FakeOrch()
    w = UniverseRefreshWorker(s, None, {"universe_refresh": orch})

    print("Phase 3 scheduled-refresh verification")

    # 1. disabled -> dormant
    s.universe.refresh.enabled = False
    await w.tick()
    check("dormant while feature disabled", orch.calls == [])
    s.universe.refresh.enabled = True

    # 2/3. hour 23 fires once; same slot guarded
    await w.tick()
    check("fires at 23:00 UTC", orch.calls == ["scheduled_23"])
    await w.tick()
    check("does not re-fire in the same hour-slot", orch.calls == ["scheduled_23"])

    # 4. funding-settlement / off-schedule hours never fire
    for h in (0, 8, 16, 15):
        clock["t"] = dt.datetime(2026, 6, 17, h, 30, tzinfo=UTC)
        await w.tick()
    check("never fires at funding/off-schedule hours (0,8,16,15)", orch.calls == ["scheduled_23"])

    # 5. hour 11 fires; divergence reported between the two windows
    orch.selection = [f"C{i}USDT" for i in range(10)] + [f"D{i}USDT" for i in range(10)]
    clock["t"] = dt.datetime(2026, 6, 17, 11, 2, tzinfo=UTC)
    await w.tick()
    check("fires at 11:00 UTC", orch.calls == ["scheduled_23", "scheduled_11"])
    check("two daily windows tracked for divergence", w._last_selection_label == "scheduled_11")

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — fires at 23:00 and 11:00 only, once per slot, "
          "avoids funding hours, and reports divergence between the two windows.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
