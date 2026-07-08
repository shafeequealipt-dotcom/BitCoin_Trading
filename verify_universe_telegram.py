#!/usr/bin/env python3
"""Phase 4 verification — the manual Telegram refresh button.

Proves, with fakes (no live system, no network, no Telegram), that the manual
button:

  1. Asks for confirmation (inline keyboard) when the feature is enabled.
  2. Refuses when the feature is disabled.
  3. On confirm, acknowledges start and runs the SAME orchestration, posting
     staged plain-prose status (started -> selected -> warm-up -> done) and
     keeping the open position force-kept and managed.
  4. Cancel changes nothing.
  5. The overlap guard refuses a manual refresh while one is already running.

Reuses the Phase 2 fakes. Run from the project root:
  python verify_universe_telegram.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import src.core.universe_refresh as ur
from src.config.settings import Settings
from src.telegram.handlers.universe import UniverseHandler
from verify_refresh_open_positions import (
    FakePositionService,
    _fast_warmup,
    _mk_orchestrator,
)


class FakeMsg:
    def __init__(self) -> None:
        self.replies: list[tuple] = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self) -> None:
        self.message = FakeMsg()
        self.callback_query = FakeQuery()


class FakeAlert:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_custom(self, msg, *a, **k):
        self.sent.append(msg)
        return True


async def run() -> int:
    failures: list[str] = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    ur._STATE_FILE = Path(tempfile.mkdtemp()) / "universe_state.json"

    settings = Settings.load()
    _fast_warmup(settings)
    settings.universe.refresh.enabled = True
    orch, services, scanner, pos_coin = _mk_orchestrator(
        settings, FakePositionService(["ZZZOPENUSDT"])
    )
    services["universe_refresh"] = orch  # the manager registers this in production
    services["alert_manager"] = FakeAlert()
    h = UniverseHandler(None, services)

    print("Phase 4 manual Telegram refresh verification")

    up = FakeUpdate()
    await h.refresh_prompt(up, None)
    check("prompt shows a confirmation keyboard when enabled",
          bool(up.message.replies) and up.message.replies[0][1] is not None)

    settings.universe.refresh.enabled = False
    up2 = FakeUpdate()
    await h.refresh_prompt(up2, None)
    check("prompt blocked (no keyboard) when feature disabled",
          bool(up2.message.replies) and up2.message.replies[0][1] is None
          and "disabled" in up2.message.replies[0][0].lower())
    settings.universe.refresh.enabled = True

    upx = FakeUpdate()
    await h.refresh_cancel(upx, None)
    check("cancel reports nothing changed", any("cancelled" in e.lower() for e in upx.callback_query.edits))

    # End-to-end staged run via _run (deterministic, awaited — no background
    # task), BEFORE the confirm test so the single-flight state is clean.
    stages: list[str] = []

    async def notify(stage, message):
        stages.append(stage)

    before = list(settings.universe.watch_list)
    await h._run(orch, notify)
    check("staged status: started", "started" in stages)
    check("staged status: selected", "selected" in stages)
    check("staged status: warm-up", "warmup" in stages)
    check("staged status: done", "done" in stages)
    check("open position kept after a manual refresh", pos_coin in settings.universe.watch_list)
    check("universe changed to movers", set(settings.universe.watch_list) != set(before))

    # Confirm acknowledges start and schedules the background run; drain it.
    upc = FakeUpdate()
    await h.refresh_confirm(upc, None)
    check("confirm acknowledges start", any("starting" in e.lower() for e in upc.callback_query.edits))
    state = services["universe_refresh_state"]
    for _ in range(100):
        if not state.is_running():
            break
        await asyncio.sleep(0.05)
    check("background refresh completed (single-flight released)", state.is_running() is False)

    state.begin()  # simulate an in-flight refresh
    upo = FakeUpdate()
    await h.refresh_confirm(upo, None)
    check("overlap guard: confirm refuses while a refresh is running",
          any("already in progress" in e.lower() for e in upo.callback_query.edits))
    state.end()

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — manual button confirms, runs the same orchestration "
          "with staged plain-prose status, keeps open positions, and guards against overlap.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
