"""Live simulation harness for the three-phase Telegram-stuck fix series.

For each fix (P1-1, P2-1 first-byte, P2-1 pool, P2-2 INFO/CRITICAL, P3-2),
this script drives the real production classes through a controlled
scenario that mimics live operating conditions, captures every log
emit via a loguru sink, and asserts the expected tag shape + values
fire. The intent is to validate that production behavior matches the
design intent without waiting for natural traffic to arrive in prod.

Run from project root:
    .venv/bin/python scripts/simulate_three_phase_fixes.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Make project root importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from loguru import logger

# Single shared log buffer for every scenario
_buf: list[str] = []
_sink_id = logger.add(
    lambda m: _buf.append(str(m)),
    level="DEBUG",
    format="{time:HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ Results harness                                                    ║
# ╚═══════════════════════════════════════════════════════════════════╝

class Scenario:
    """One scenario per fix. Captures pass/fail + evidence."""

    def __init__(self, phase: str, name: str) -> None:
        self.phase = phase
        self.name = name
        self.passes: list[str] = []
        self.fails: list[str] = []
        self.evidence: list[str] = []
        self.log_slice_start: int = 0

    def begin(self) -> None:
        self.log_slice_start = len(_buf)
        print(f"\n── {self.phase} :: {self.name} " + "─" * (60 - len(self.phase) - len(self.name) - 6))

    def assert_in_logs(self, pattern: str, regex: bool = False) -> None:
        joined = "\n".join(_buf[self.log_slice_start:])
        hit = (re.search(pattern, joined) if regex else (pattern in joined))
        msg = f"log contains {'/' + pattern + '/' if regex else repr(pattern)}"
        if hit:
            self.passes.append(msg)
            print(f"  [PASS] {msg}")
        else:
            self.fails.append(msg)
            print(f"  [FAIL] {msg}")
            # Show the last 15 captured lines for debugging
            tail = "\n          ".join(_buf[self.log_slice_start:][-15:])
            print(f"          last captured:\n          {tail}")

    def assert_not_in_logs(self, pattern: str) -> None:
        joined = "\n".join(_buf[self.log_slice_start:])
        msg = f"log does NOT contain {pattern!r}"
        if pattern in joined:
            self.fails.append(msg)
            print(f"  [FAIL] {msg}")
        else:
            self.passes.append(msg)
            print(f"  [PASS] {msg}")

    def note(self, msg: str) -> None:
        self.evidence.append(msg)
        print(f"  • {msg}")

    def summary(self) -> tuple[int, int]:
        return len(self.passes), len(self.fails)


_results: list[Scenario] = []


def run_scenario(phase: str, name: str):
    """Decorator returning the (sync or async) scenario callable."""
    def wrap(fn):
        s = Scenario(phase, name)
        _results.append(s)
        if asyncio.iscoroutinefunction(fn):
            async def inner():
                s.begin()
                await fn(s)
            return inner
        else:
            def inner():
                s.begin()
                fn(s)
            return inner
    return wrap


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ P1-1 — auto_vacuum + new DB_INCREMENTAL_VACUUM_OK tag             ║
# ╚═══════════════════════════════════════════════════════════════════╝

@run_scenario("P1-1", "mode=2 DB with populated freelist emits DB_INCREMENTAL_VACUUM_OK")
async def sim_p1_1_mode2(s: Scenario) -> None:
    from src.database.connection import DatabaseManager
    from src.workers.cleanup_worker import CleanupWorker

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        # Build a mode=2 DB with a non-empty freelist (mimics live state)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            conn.execute("VACUUM")
            conn.execute("CREATE TABLE filler(id INTEGER PRIMARY KEY, blob TEXT)")
            payload = "x" * 1024
            for _ in range(5000):
                conn.execute("INSERT INTO filler (blob) VALUES (?)", (payload,))
            conn.commit()
            conn.execute("DROP TABLE filler")
            conn.commit()
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            s.note(f"setup: freelist_count={free} (need > 0 to exercise reclaim path)")
            assert free > 0
        finally:
            conn.close()

        db = DatabaseManager(path)
        await db.connect()
        try:
            settings = MagicMock()
            settings.workers.max_consecutive_failures = 5
            settings.workers.restart_delay = 10
            cw = CleanupWorker(settings, db)
            t0 = time.monotonic()
            await cw.tick()
            elapsed = time.monotonic() - t0
            s.note(f"tick() elapsed: {elapsed*1000:.0f} ms")
        finally:
            await db.disconnect()

        s.assert_in_logs("DB_INCREMENTAL_VACUUM_OK")
        s.assert_in_logs("pages_freed=", regex=False)
        s.assert_in_logs("elapsed_ms=", regex=False)
        s.assert_in_logs("freelist_before=", regex=False)
        s.assert_in_logs("freelist_after=", regex=False)
        s.assert_in_logs("pages_cap=1000", regex=False)
        # The legacy tag must NOT fire
        s.assert_not_in_logs("VACUUM | mode=incremental")
        # Extract the actual pages_freed value
        joined = "\n".join(_buf[s.log_slice_start:])
        m = re.search(r"DB_INCREMENTAL_VACUUM_OK \| pages_freed=(\d+) elapsed_ms=(\d+)", joined)
        if m:
            s.note(f"observed: pages_freed={m.group(1)} elapsed_ms={m.group(2)}")
    finally:
        os.unlink(path)


@run_scenario("P1-1", "mode=0 DB emits DB_VACUUM_MIGRATION_REQUIRED warning, skips reclaim")
async def sim_p1_1_mode0(s: Scenario) -> None:
    from src.database.connection import DatabaseManager
    from src.workers.cleanup_worker import CleanupWorker

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        # Default mode=0 (NONE)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE marker(id INTEGER PRIMARY KEY)")
        conn.commit()
        mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
        conn.close()
        s.note(f"setup: PRAGMA auto_vacuum={mode} (mode=0 = NONE)")
        assert mode == 0

        db = DatabaseManager(path)
        await db.connect()
        try:
            settings = MagicMock()
            settings.workers.max_consecutive_failures = 5
            settings.workers.restart_delay = 10
            cw = CleanupWorker(settings, db)
            await cw.tick()
        finally:
            await db.disconnect()

        s.assert_in_logs("DB_AUTO_VACUUM_NOT_INCREMENTAL")  # boot-probe warning
        s.assert_in_logs("DB_VACUUM_MIGRATION_REQUIRED")    # cleanup-tick warning
        s.assert_not_in_logs("DB_INCREMENTAL_VACUUM_OK")    # MUST NOT fire on mode=0
    finally:
        os.unlink(path)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ P2-1 — first-byte deadline                                         ║
# ╚═══════════════════════════════════════════════════════════════════╝

def _fake_cli(tmp_path: Path, body: str) -> str:
    body_normalized = textwrap.dedent(body).strip()
    script = tmp_path / f"fake_claude_{os.getpid()}_{time.time_ns()}"
    header = (
        f"#!{sys.executable}\n"
        "import sys, time\nprompt = sys.stdin.read()\n"
    )
    script.write_text(header + body_normalized + "\n")
    script.chmod(0o755)
    return str(script)


def _build_real_claude_client(claude_path: str, *, first_byte: float, timeout_s: int):
    from src.brain import claude_code_client as ccc
    with patch.object(ccc.ClaudeCodeClient, "_log_diagnostics", lambda self: None), \
         patch.object(ccc.ClaudeCodeClient, "_validate_setup", lambda self: None), \
         patch.object(ccc.ClaudeCodeClient, "_find_claude", lambda self: claude_path), \
         patch.object(ccc.ClaudeCodeClient, "_build_env",
                      lambda self: {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}):
        cc = ccc.ClaudeCodeClient(
            timeout_seconds=timeout_s,
            first_byte_timeout_seconds=first_byte,
            prewarm_max_age_seconds=900.0,
            prewarm_stats_interval_seconds=0.05,
        )
    cc._stall_warn_buckets = (0.5, 1.0, 2.0)
    cc._cleanup_orphaned_processes = lambda: None
    return cc


@run_scenario("P2-1", "first-byte deadline fires when no stdout in N seconds")
def sim_p2_1_deadline(s: Scenario) -> None:
    # Fake CLI hangs forever; deadline must fire and bound the wait.
    tmp = Path(tempfile.mkdtemp())
    try:
        cli = _fake_cli(tmp, "time.sleep(60)")
        cc = _build_real_claude_client(cli, first_byte=1.0, timeout_s=20)
        s.note("setup: fake CLI sleeps 60s; first_byte_timeout=1.0s, total_timeout=20s")
        t0 = time.monotonic()
        crashed = False
        try:
            cc._subprocess_call("test prompt")
        except RuntimeError as e:
            crashed = "first-byte deadline" in str(e).lower()
            s.note(f"raised RuntimeError: {str(e)[:90]}...")
        elapsed = time.monotonic() - t0
        s.note(f"_subprocess_call duration: {elapsed:.2f}s (bounded close to first_byte_timeout)")
        s.assert_in_logs("CLAUDE_PROC_FIRST_BYTE_DEADLINE")
        s.assert_in_logs("BRAIN_FAILURE_CASCADE")
        s.assert_in_logs("kind=first_byte_deadline")
        s.passes.append("RuntimeError carried 'first-byte deadline' message") if crashed \
            else s.fails.append("RuntimeError did NOT carry 'first-byte deadline' message")
        print(f"  [{'PASS' if crashed else 'FAIL'}] RuntimeError message identification")
        # Elapsed should be close to first_byte_timeout (1s + small overhead) — NOT total_timeout (20s)
        if elapsed < 5.0:
            s.passes.append(f"call bounded at ~first_byte_timeout (~{elapsed:.1f}s), not total_timeout (20s)")
            print(f"  [PASS] call bounded at ~first_byte_timeout ({elapsed:.1f}s)")
        else:
            s.fails.append(f"call NOT bounded — took {elapsed:.1f}s")
            print(f"  [FAIL] call NOT bounded — took {elapsed:.1f}s")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


@run_scenario("P2-1", "first-byte deadline does NOT fire on healthy fast-output CLI")
def sim_p2_1_no_deadline(s: Scenario) -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        cli = _fake_cli(tmp, 'sys.stdout.write("ok"); sys.stdout.flush()')
        cc = _build_real_claude_client(cli, first_byte=0.5, timeout_s=10)
        s.note("setup: fake CLI writes 'ok' immediately; first_byte_timeout=0.5s")
        out = cc._subprocess_call("test prompt")
        s.note(f"got response: {out!r}")
        s.assert_not_in_logs("CLAUDE_PROC_FIRST_BYTE_DEADLINE")
        s.assert_not_in_logs("kind=first_byte_deadline")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ P2-1 — pool stats counters                                         ║
# ╚═══════════════════════════════════════════════════════════════════╝

@run_scenario("P2-1", "pool counters track hits/misses/stale_disposed + CLAUDE_POOL_STATS emits")
def sim_p2_1_pool(s: Scenario) -> None:
    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    class _FakeProc:
        def __init__(self, alive: bool) -> None:
            self.pid = 12345
            self._alive = alive
            self.stdin = self.stdout = self.stderr = None
        def poll(self):
            return None if self._alive else 0

    # Use a tight stats interval so the emit fires across acquires
    pool = _ClaudeWorkerPool(
        claude_path="/bin/true", env={}, project_cwd="/tmp",
        max_age_seconds=900.0, stats_interval_seconds=0.05,
    )

    # Scenario 1: empty -> miss
    proc, _ = pool.acquire("sys-prompt-A")
    s.note(f"miss scenario: acquire returned proc={proc}, hit={pool._hit_count}, miss={pool._miss_count}")
    assert proc is None

    # Scenario 2: inject a fresh slot -> hit
    slot = _PrewarmSlot(_FakeProc(alive=True), pool._hash_sys_prompt("sys-prompt-A"))
    with pool._lock:
        pool._slots[pool._hash_sys_prompt("sys-prompt-A")] = slot
    proc, _ = pool.acquire("sys-prompt-A")
    s.note(f"hit scenario: acquire returned proc={proc is not None}, hit={pool._hit_count}")
    assert proc is not None

    # Scenario 3: dead slot -> stale_disposed
    slot = _PrewarmSlot(_FakeProc(alive=False), pool._hash_sys_prompt("sys-prompt-A"))
    with pool._lock:
        pool._slots[pool._hash_sys_prompt("sys-prompt-A")] = slot
    proc, _ = pool.acquire("sys-prompt-A")
    s.note(f"stale scenario: acquire returned proc={proc}, miss={pool._miss_count}, stale_disposed={pool._stale_disposed_count}")
    assert proc is None

    # Wait past the stats interval and force one more acquire to trigger emit
    time.sleep(0.1)
    pool.acquire("sys-prompt-B")

    s.assert_in_logs("CLAUDE_POOL_STATS")
    s.assert_in_logs("hits=")
    s.assert_in_logs("misses=")
    s.assert_in_logs("stale_disposed=")
    s.assert_in_logs("spawn_failed=")
    s.assert_in_logs("hit_rate_pct=")
    s.assert_in_logs("slots_currently_held=")
    s.assert_in_logs("max_age_s=900")

    # Verify final counter state
    if pool._hit_count == 1 and pool._stale_disposed_count == 1 and pool._miss_count >= 3:
        s.passes.append(f"final counters: hits={pool._hit_count} miss={pool._miss_count} stale={pool._stale_disposed_count}")
        print(f"  [PASS] final counter state matches expectations")
    else:
        s.fails.append(f"counter mismatch: hits={pool._hit_count} miss={pool._miss_count} stale={pool._stale_disposed_count}")
        print(f"  [FAIL] counter state wrong")


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ P2-2 — INFO fire-and-forget + CRITICAL awaited                      ║
# ╚═══════════════════════════════════════════════════════════════════╝

def _build_alert_manager() -> object:
    from src.alerts.alert_manager import AlertManager
    settings = SimpleNamespace(
        alerts=SimpleNamespace(
            telegram_enabled=True, max_alerts_per_minute=10,
            trade_alerts=True, signal_alerts=True, error_alerts=True,
            bot_token="x", chat_id="-1001",
        ),
    )
    am = AlertManager(settings, MagicMock())
    am.enabled = True
    return am


@run_scenario("P2-2", "INFO send_custom returns in microseconds while bot.send_message takes seconds")
async def sim_p2_2_info_ff(s: Scenario) -> None:
    from src.core.types import AlertLevel
    am = _build_alert_manager()

    async def _slow_send(*a, **kw):
        await asyncio.sleep(0.5)  # simulate slow Telegram
        return True
    am.bot.send_message = AsyncMock(side_effect=_slow_send)

    t0 = time.monotonic()
    result = await am.send_custom("entry @ 100, SL @ 95, TP @ 110", AlertLevel.INFO)
    elapsed = time.monotonic() - t0
    s.note(f"send_custom returned True={result is True} in {elapsed*1000:.1f} ms (Telegram takes 500 ms)")
    if elapsed < 0.1:
        s.passes.append(f"INFO returned in {elapsed*1000:.1f} ms (< 100 ms)")
        print(f"  [PASS] INFO returned in {elapsed*1000:.1f} ms")
    else:
        s.fails.append(f"INFO BLOCKED — took {elapsed*1000:.0f} ms")
        print(f"  [FAIL] INFO blocked for {elapsed*1000:.0f} ms")

    s.assert_in_logs("ALERT_FIRE_AND_FORGET | kind=info bypass=Y")
    # Bot not yet awaited
    s.note(f"bot.send_message await_count BEFORE flush: {am.bot.send_message.await_count}")
    await am.flush_pending_info()
    s.note(f"bot.send_message await_count AFTER flush: {am.bot.send_message.await_count}")
    if am.bot.send_message.await_count == 1:
        s.passes.append("bot.send_message awaited exactly once after flush")
        print(f"  [PASS] bot.send_message awaited exactly once after flush")
    else:
        s.fails.append(f"bot.send_message await_count after flush: {am.bot.send_message.await_count}")
        print(f"  [FAIL] bot.send_message await_count after flush: {am.bot.send_message.await_count}")
    s.assert_in_logs("ALERT_SENT | level=info")


@run_scenario("P2-2", "CRITICAL send_custom blocks until bot returns (delivery guarantee)")
async def sim_p2_2_critical_blocks(s: Scenario) -> None:
    from src.core.types import AlertLevel
    am = _build_alert_manager()

    async def _slow_send(*a, **kw):
        await asyncio.sleep(0.3)
        return True
    am.bot.send_message = AsyncMock(side_effect=_slow_send)

    t0 = time.monotonic()
    result = await am.send_custom("EMERGENCY: all positions closed", AlertLevel.CRITICAL)
    elapsed = time.monotonic() - t0
    s.note(f"send_custom returned True={result is True} in {elapsed*1000:.0f} ms (Telegram takes 300 ms)")
    if elapsed >= 0.28:
        s.passes.append(f"CRITICAL awaited (~{elapsed*1000:.0f} ms ≈ 300 ms Telegram delay)")
        print(f"  [PASS] CRITICAL awaited ({elapsed*1000:.0f} ms)")
    else:
        s.fails.append(f"CRITICAL returned too fast ({elapsed*1000:.0f} ms < 280 ms)")
        print(f"  [FAIL] CRITICAL returned too fast")
    s.assert_in_logs("ALERT_AWAITED | kind=critical")
    s.assert_in_logs("ALERT_SENT | level=critical")
    s.assert_not_in_logs("ALERT_FIRE_AND_FORGET")  # CRITICAL must NOT take fire-and-forget path
    # No pending task for CRITICAL
    if not am._pending_info_tasks:
        s.passes.append("no pending info task scheduled for CRITICAL")
        print(f"  [PASS] no pending info task for CRITICAL")


@run_scenario("P2-2", "INFO delivery failure still emits ALERT_FAIL (not silently lost)")
async def sim_p2_2_info_failure(s: Scenario) -> None:
    from src.core.types import AlertLevel
    am = _build_alert_manager()
    am.bot.send_message = AsyncMock(return_value=False)  # bot reports failure
    am._reposition_dashboard = AsyncMock()  # short-circuit unrelated side-effect

    await am.send_custom("entry that will fail", AlertLevel.INFO)
    await am.flush_pending_info()
    s.assert_in_logs("ALERT_FAIL | level=info")
    s.assert_in_logs("send_returned_false")


@run_scenario("P2-2", "INFO dedup preserved under fire-and-forget (race-window closed)")
async def sim_p2_2_dedup(s: Scenario) -> None:
    from src.core.types import AlertLevel
    am = _build_alert_manager()
    am.bot.send_message = AsyncMock(return_value=True)
    msg = "trade_executed_BTCUSDT_Buy_50000_qty=0.01"

    r1 = await am.send_custom(msg, AlertLevel.INFO)
    r2 = await am.send_custom(msg, AlertLevel.INFO)  # immediately re-send same content
    await am.flush_pending_info()
    s.note(f"r1={r1} (expected True), r2={r2} (expected False — dedup)")
    if r1 is True and r2 is False and am.bot.send_message.await_count == 1:
        s.passes.append("dedup blocks the second identical INFO; bot called only once")
        print(f"  [PASS] dedup blocks second INFO; bot called once")
    else:
        s.fails.append(f"dedup broken: r1={r1} r2={r2} await_count={am.bot.send_message.await_count}")
        print(f"  [FAIL] dedup broken")


@run_scenario("P2-2", "Callers passing raw 'CRITICAL' string would crash — verify enum required")
async def sim_p2_2_enum_required(s: Scenario) -> None:
    """Documents that raw string priorities crash. After the follow-up
    fix (commit 79ec55d), all callers in src/ use AlertLevel enum. This
    scenario PROVES the AttributeError is real if someone passes a string."""
    from src.alerts.alert_manager import AlertManager
    from src.core.types import AlertLevel
    am = _build_alert_manager()
    am.bot.send_message = AsyncMock(return_value=True)
    am._reposition_dashboard = AsyncMock()
    # Pass raw "CRITICAL" string — should crash inside _send at `priority.value.lower()`
    crashed = False
    try:
        await am._send("test crash", "CRITICAL")  # type: ignore[arg-type]
    except AttributeError as e:
        crashed = True
        s.note(f"raw string priority correctly raised AttributeError: {str(e)[:80]}")
    if crashed:
        s.passes.append("raw-string priority confirmed to crash (caller bug, NOT alert_manager bug)")
        print(f"  [PASS] raw 'CRITICAL' string crashes _send (caller must pass AlertLevel enum)")
    else:
        s.fails.append("raw 'CRITICAL' string DID NOT crash — defensive coercion was added?")
        print(f"  [FAIL] raw string did not crash — investigate")


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ P3-2 — SL gateway rate-limit-aware skip in _push_sl_to_shadow      ║
# ╚═══════════════════════════════════════════════════════════════════╝

def _make_minimal_watchdog():
    """Construct a PositionWatchdog suitable for _push_sl_to_shadow testing.

    Reuses the project's test helper. Does NOT exercise tick() — we
    call _push_sl_to_shadow directly with controlled inputs.
    """
    sys.path.insert(0, str(_PROJECT_ROOT / "tests"))
    from test_watchdog.test_position_watchdog import _make_watchdog
    from src.config.settings import (
        AlertSettings, AltDataSettings, BrainSettings, BybitSettings,
        DatabaseSettings, FinnhubSettings, GeneralSettings, MCPSettings,
        RedditSettings, RiskSettings, Settings, WatchdogSettings,
        WorkerSettings,
    )
    tmpdir = tempfile.mkdtemp()
    settings = Settings(
        general=GeneralSettings(mode="paper", log_dir=os.path.join(tmpdir, "logs")),
        bybit=BybitSettings(testnet=True, api_key="k", api_secret="s",
                            default_symbols=["BTCUSDT", "ETHUSDT"]),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=os.path.join(tmpdir, "sim.db")),
        workers=WorkerSettings(max_consecutive_failures=3, restart_delay=1),
        brain=BrainSettings(enabled=True, api_key="sk-test"),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        watchdog=WatchdogSettings(enabled=True, check_interval_seconds=1),
        mcp=MCPSettings(),
    )
    return _make_watchdog(settings)


@run_scenario("P3-2", "trail_update / sentinel_deadline / sentinel_advisor / trail_activation skip when gateway rate-limited")
async def sim_p3_2_skip(s: Scenario) -> None:
    wd = _make_minimal_watchdog()
    # Inject a gateway that says "blocked for 7.3 s"
    gw = MagicMock()
    gw.next_eligible_in_seconds = MagicMock(return_value=7.3)
    gw.apply = AsyncMock(return_value=MagicMock(accepted=True))
    wd.sl_gateway = gw

    for source in ("trail_update", "sentinel_deadline", "sentinel_advisor", "trail_activation"):
        gw.apply.reset_mock()
        result = await wd._push_sl_to_shadow(
            symbol=f"SIM-{source.upper()}",
            new_sl=29000.0,
            plan=MagicMock(stop_loss_price=29500.0),
            current_shadow_sl=29500.0,
            direction="Sell",
            source=source,
        )
        s.note(f"src={source}: returned False={result is False}, gw.apply NOT called={not gw.apply.await_count}")
        if result is False and gw.apply.await_count == 0:
            s.passes.append(f"src={source}: skipped + apply not called")
            print(f"  [PASS] src={source}: skipped + apply not called")
        else:
            s.fails.append(f"src={source}: incorrect behavior")
            print(f"  [FAIL] src={source}: incorrect")

    s.assert_in_logs("SNIPER_RATE_LIMIT_AWARE_SKIP")
    for source in ("trail_update", "sentinel_deadline", "sentinel_advisor", "trail_activation"):
        s.assert_in_logs(f"src={source}")
    # Confirm remaining_s formatting
    s.assert_in_logs("next_eligible_in_s=7.3")


@run_scenario("P3-2", "When gateway is eligible, apply proceeds and SKIP does NOT fire")
async def sim_p3_2_eligible(s: Scenario) -> None:
    wd = _make_minimal_watchdog()
    gw = MagicMock()
    gw.next_eligible_in_seconds = MagicMock(return_value=0.0)  # eligible now
    gw.apply = AsyncMock(return_value=MagicMock(accepted=True))
    wd.sl_gateway = gw

    result = await wd._push_sl_to_shadow(
        symbol="SIM-ELIGIBLE",
        new_sl=29000.0,
        plan=MagicMock(stop_loss_price=29500.0),
        current_shadow_sl=29500.0,
        direction="Sell",
        source="trail_update",
    )
    s.note(f"result={result is True}, gw.apply called={gw.apply.await_count == 1}")
    s.assert_not_in_logs("SNIPER_RATE_LIMIT_AWARE_SKIP")
    if result is True and gw.apply.await_count == 1:
        s.passes.append("eligible window: apply called, no SKIP")
        print(f"  [PASS] eligible: apply called, no SKIP tag")


@run_scenario("P3-2", "Rate-limit-skipped call does NOT advance trail coalesce timestamp")
async def sim_p3_2_no_coalesce_drift(s: Scenario) -> None:
    wd = _make_minimal_watchdog()
    gw = MagicMock()
    gw.next_eligible_in_seconds = MagicMock(return_value=5.0)
    gw.apply = AsyncMock(return_value=MagicMock(accepted=True))
    wd.sl_gateway = gw

    await wd._push_sl_to_shadow(
        symbol="SIM-COALESCE",
        new_sl=100.0,
        plan=MagicMock(stop_loss_price=110.0),
        current_shadow_sl=110.0,
        direction="Sell",
        source="trail_update",
    )
    has_entry = "SIM-COALESCE" in getattr(wd, "_last_trail_push_at", {})
    s.note(f"_last_trail_push_at has SIM-COALESCE: {has_entry} (expected False — skipped before coalesce)")
    if not has_entry:
        s.passes.append("blocked call did NOT advance coalesce timestamps")
        print(f"  [PASS] coalesce-window invariant preserved")
    else:
        s.fails.append("blocked call DID advance coalesce — would silently delay legitimate retries")
        print(f"  [FAIL] coalesce-window violated")


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ Main                                                               ║
# ╚═══════════════════════════════════════════════════════════════════╝

async def main() -> int:
    print("=" * 76)
    print("LIVE SIMULATION OF THREE-PHASE TELEGRAM-STUCK FIX SERIES")
    print("=" * 76)

    # P1-1
    await sim_p1_1_mode2()
    await sim_p1_1_mode0()
    # P2-1
    sim_p2_1_deadline()
    sim_p2_1_no_deadline()
    sim_p2_1_pool()
    # P2-2
    await sim_p2_2_info_ff()
    await sim_p2_2_critical_blocks()
    await sim_p2_2_info_failure()
    await sim_p2_2_dedup()
    await sim_p2_2_enum_required()
    # P3-2
    await sim_p3_2_skip()
    await sim_p3_2_eligible()
    await sim_p3_2_no_coalesce_drift()

    # Summary
    print("\n" + "=" * 76)
    print("SIMULATION RESULTS")
    print("=" * 76)
    total_p = total_f = 0
    for r in _results:
        p, f = r.summary()
        total_p += p; total_f += f
        status = "PASS" if f == 0 else "FAIL"
        print(f"  [{status}] {r.phase} :: {r.name}  ({p} passes, {f} fails)")
    print("-" * 76)
    print(f"  TOTAL: {total_p} passes, {total_f} fails across {len(_results)} scenarios")
    print("=" * 76)
    return 0 if total_f == 0 else 1


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
