"""J4 (2026-05-14) — Claude CLI subprocess pool observability tests.

The audit's CLAUDE_PROC_STALL events fire 60-240s into every call. The
J4 investigation (dev_notes/seven_fixes/) confirmed the stalls are
API latency, not subprocess overhead. The subprocess pool already
exists but had 0% hit rate over 18 audit-window calls and the
diagnostic emissions did not distinguish ageing from process death,
leaving the operator with no signal on which mitigation to pursue.

This module pins the J4 observability additions:

  * CLAUDE_PREWARM_HIT fires on every prewarm acquisition (master-
    prompt-mandated alias to the richer CLAUDE_PROC_POOL_ACQUIRE).
  * CLAUDE_PIPELINE_NEXT fires synchronously when the next prewarm is
    scheduled so the operator sees the pipeline decision in the log
    timeline.
  * CLAUDE_PREWARM_DISPOSED differentiates age_expired vs dead.
  * CLAUDE_POOL_STATS carries the new age_disposed and dead_disposed
    counters.

Source pins at the bottom defend against silent removal of any of
these events in future refactors.
"""

from __future__ import annotations

import re

import pytest


def _read(path: str) -> str:
    return open(path, encoding="utf-8").read()


_CLIENT = (
    "/home/inshadaliqbal786/trading-intelligence-mcp/"
    "src/brain/claude_code_client.py"
)


def test_pool_has_split_disposal_counters() -> None:
    """J4 added age_disposed_count and dead_disposed_count alongside
    the legacy stale_disposed_count so operators can diagnose
    ageing vs dead workers separately."""
    src = _read(_CLIENT)
    assert "self._age_disposed_count: int = 0" in src
    assert "self._dead_disposed_count: int = 0" in src
    assert "self._stale_disposed_count" in src


def test_pool_stats_emits_new_counters() -> None:
    """The CLAUDE_POOL_STATS line now includes age_disposed and
    dead_disposed fields."""
    src = _read(_CLIENT)
    # Locate the CLAUDE_POOL_STATS block and verify both new field
    # tokens are interpolated within it.
    stats_block = re.search(
        r"f\"CLAUDE_POOL_STATS \|.*?\| \{ctx\(\)\}\"",
        src,
        re.DOTALL,
    )
    assert stats_block is not None, "CLAUDE_POOL_STATS log line missing"
    assert "age_disposed=" in stats_block.group(0)
    assert "dead_disposed=" in stats_block.group(0)


def test_disposal_emits_reason() -> None:
    """CLAUDE_PREWARM_DISPOSED fires with reason=age_expired or
    reason=dead on each stale-or-dead worker disposal."""
    src = _read(_CLIENT)
    assert "CLAUDE_PREWARM_DISPOSED" in src
    assert 'reason=age_expired' in src or 'reason={_reason}' in src
    # The reason is computed in the conditional branch
    assert '_reason = "dead"' in src
    assert '_reason = "age_expired"' in src


def test_prewarm_hit_event_present() -> None:
    """Master-prompt-mandated CLAUDE_PREWARM_HIT fires on every
    prewarm acquisition alongside the richer CLAUDE_PROC_POOL_ACQUIRE."""
    src = _read(_CLIENT)
    assert "CLAUDE_PREWARM_HIT" in src
    # Must appear in the prewarm_proc-not-None branch
    hit_idx = src.find("CLAUDE_PREWARM_HIT")
    acquire_idx = src.find("CLAUDE_PROC_POOL_ACQUIRE")
    # CLAUDE_PREWARM_HIT lives after CLAUDE_PROC_POOL_ACQUIRE in the
    # same conditional branch — both fire on the same condition.
    assert 0 < acquire_idx < hit_idx


def test_pipeline_next_event_present() -> None:
    """Master-prompt-mandated CLAUDE_PIPELINE_NEXT fires when the next
    prewarm is scheduled so the operator sees the pipeline decision."""
    src = _read(_CLIENT)
    assert "CLAUDE_PIPELINE_NEXT" in src
    # Must appear adjacent to the replenish_async call
    pipeline_idx = src.find("CLAUDE_PIPELINE_NEXT")
    replenish_idx = src.find("self._proc_pool.replenish_async(system_prompt)")
    # CLAUDE_PIPELINE_NEXT fires immediately after replenish_async is
    # scheduled (the replenish itself is async; the log records the
    # dispatch decision).
    assert 0 < replenish_idx < pipeline_idx


# --- Pool counter regression with mock subprocess ------------------


class _DeadProc:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self._poll_result: int | None = 1  # exited

    def poll(self) -> int | None:
        return self._poll_result


class _LiveProc(_DeadProc):
    def __init__(self) -> None:
        super().__init__()
        self._poll_result = None  # still alive


@pytest.mark.asyncio
async def test_dead_worker_increments_dead_disposed() -> None:
    """When acquire encounters a dead worker, dead_disposed
    increments and age_disposed does NOT."""
    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    pool = _ClaudeWorkerPool(
        claude_path="/nonexistent",
        env={},
        project_cwd="/tmp",
        max_age_seconds=900.0,
        stats_interval_seconds=0.0,
    )
    slot = _PrewarmSlot(_DeadProc(), "sys_hash_abc")
    pool._slots["sys_hash_abc"] = slot
    # Patch _hash_sys_prompt to return our test key
    object.__setattr__(pool, "_hash_sys_prompt", staticmethod(lambda _: "sys_hash_abc"))

    proc, age = pool.acquire("any_system_prompt")
    assert proc is None
    assert pool._dead_disposed_count == 1
    assert pool._age_disposed_count == 0
    assert pool._stale_disposed_count == 1  # legacy combined counter


@pytest.mark.asyncio
async def test_aged_worker_increments_age_disposed() -> None:
    """When acquire encounters a live but age-expired worker,
    age_disposed increments and dead_disposed does NOT."""
    import time

    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    pool = _ClaudeWorkerPool(
        claude_path="/nonexistent",
        env={},
        project_cwd="/tmp",
        max_age_seconds=0.001,  # immediately expires
        stats_interval_seconds=0.0,
    )
    slot = _PrewarmSlot(_LiveProc(), "sys_hash_def")
    pool._slots["sys_hash_def"] = slot
    object.__setattr__(pool, "_hash_sys_prompt", staticmethod(lambda _: "sys_hash_def"))
    time.sleep(0.01)  # ensure age > max_age

    proc, age = pool.acquire("any_system_prompt")
    assert proc is None
    assert pool._age_disposed_count == 1
    assert pool._dead_disposed_count == 0
    assert pool._stale_disposed_count == 1


@pytest.mark.asyncio
async def test_live_fresh_worker_returns_hit() -> None:
    """When acquire encounters a live, fresh worker, hit_count
    increments and no disposal counter changes."""
    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    pool = _ClaudeWorkerPool(
        claude_path="/nonexistent",
        env={},
        project_cwd="/tmp",
        max_age_seconds=900.0,
        stats_interval_seconds=0.0,
    )
    live = _LiveProc()
    slot = _PrewarmSlot(live, "sys_hash_xyz")
    pool._slots["sys_hash_xyz"] = slot
    object.__setattr__(pool, "_hash_sys_prompt", staticmethod(lambda _: "sys_hash_xyz"))

    proc, age = pool.acquire("any_system_prompt")
    assert proc is live
    assert pool._hit_count == 1
    assert pool._miss_count == 0
    assert pool._age_disposed_count == 0
    assert pool._dead_disposed_count == 0
