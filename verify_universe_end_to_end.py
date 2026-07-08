#!/usr/bin/env python3
"""Phase 5 verification — end-to-end persistence round-trip + boot-load safety.

Proves the final wiring holds together (no live system, no network):

  1. A refresh persists the new universe to the state file.
  2. At boot, when the feature is ENABLED, that persisted universe is loaded
     back into settings (so the dynamic universe survives a restart), using
     the REAL boot-load function from workers.py.
  3. When the feature is DISABLED, boot-load is a no-op even if the file
     exists (disabling reverts to the config.toml seed on next boot).
  4. An invalid/too-small persisted list is ignored (config seed kept).

Run from the project root:  python verify_universe_end_to_end.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import src.core.universe_refresh as ur
from src.config.settings import Settings
from src.core.logging import get_logger
from verify_refresh_open_positions import (
    FakePositionService,
    _fast_warmup,
    _mk_orchestrator,
)
from workers import _load_persisted_universe

log = get_logger("verify")


async def run() -> int:
    failures: list[str] = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    tmp = Path(tempfile.mkdtemp()) / "universe_state.json"
    ur._STATE_FILE = tmp  # orchestrator persists here

    print("Phase 5 end-to-end verification")

    # 1. Run a refresh -> it persists the new universe.
    settings = Settings.load()
    _fast_warmup(settings)
    settings.universe.refresh.enabled = True
    orch, services, scanner, pos_coin = _mk_orchestrator(
        settings, FakePositionService(["ZZZOPENUSDT"])
    )
    result = await orch.run_refresh("e2e")
    persisted = set(result.get("selected", []))
    check("refresh succeeded", result.get("status") == "ok")
    check("state file written", tmp.exists())
    saved = json.loads(tmp.read_text()).get("watch_list", []) if tmp.exists() else []
    check("persisted list matches the selected universe", set(saved) == persisted and len(saved) >= 10)
    check("open position is in the persisted universe", pos_coin in saved)

    # 2. Fresh boot WITH the feature enabled -> persisted universe loaded.
    fresh = Settings._load_fresh()
    fresh.universe.refresh.enabled = True
    seed = list(fresh.universe.watch_list)
    _load_persisted_universe(fresh, log, state_path=str(tmp))
    check("boot-load restored the persisted universe", set(fresh.universe.watch_list) == set(saved))
    check("boot-load changed it from the config seed", set(fresh.universe.watch_list) != set(seed))

    # 3. Fresh boot with the feature DISABLED -> no-op (config seed kept).
    fresh2 = Settings._load_fresh()
    fresh2.universe.refresh.enabled = False
    seed2 = list(fresh2.universe.watch_list)
    _load_persisted_universe(fresh2, log, state_path=str(tmp))
    check("boot-load is a no-op when feature disabled", list(fresh2.universe.watch_list) == seed2)

    # 4. Invalid / too-small persisted list -> ignored.
    bad = Path(tempfile.mkdtemp()) / "bad.json"
    bad.write_text(json.dumps({"watch_list": ["BTCUSDT", "ETHUSDT"]}))  # < 10
    fresh3 = Settings._load_fresh()
    fresh3.universe.refresh.enabled = True
    seed3 = list(fresh3.universe.watch_list)
    _load_persisted_universe(fresh3, log, state_path=str(bad))
    check("boot-load ignores an invalid/too-small persisted list", list(fresh3.universe.watch_list) == seed3)

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — refresh persists, boot-load restores when enabled, "
          "is a safe no-op when disabled, and ignores invalid state.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
