"""P2-1 (2026-05-13) — prewarm pool tuning + CLAUDE_POOL_STATS observability.

Verifies the changes to ``_ClaudeWorkerPool``:

1. Default ``max_age_seconds`` is now 900 s (was 60 s).
2. ``acquire`` correctly increments ``hits / misses / stale_disposed``
   under realistic patterns.
3. ``CLAUDE_POOL_STATS`` emits once per ``stats_interval_seconds`` with
   the new fields (hits, misses, stale_disposed, hit_rate_pct,
   max_age_s).
4. ``stats_interval_seconds <= 0`` disables periodic emission.

These tests do NOT spawn real Claude CLI subprocesses — they construct
the pool directly and feed it lightweight stub procs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

import pytest
from loguru import logger

from src.brain.claude_code_client import (
    _ClaudeWorkerPool,
    _PrewarmSlot,
)


class _FakeProc:
    """Stand-in for subprocess.Popen that ``acquire`` only needs to
    answer alive/dead. We never actually run it."""

    def __init__(self, pid: int = 1234, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive
        self.stdin: Any = None
        self.stdout: Any = None
        self.stderr: Any = None
        self.returncode: int | None = None if alive else 0

    def poll(self) -> int | None:
        return None if self._alive else 0


def _build_pool(
    *,
    max_age_seconds: float = 900.0,
    stats_interval_seconds: float = 300.0,
) -> _ClaudeWorkerPool:
    """Construct a pool with throwaway claude_path + env."""
    return _ClaudeWorkerPool(
        claude_path="/bin/true",
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        project_cwd="/tmp",
        max_age_seconds=max_age_seconds,
        stats_interval_seconds=stats_interval_seconds,
    )


def _install_fresh_slot(
    pool: _ClaudeWorkerPool, sys_prompt: str, proc: _FakeProc,
) -> None:
    """Insert a freshly-aged slot directly into the pool's internal dict.

    We mimic what ``_replenish_blocking`` would do without spawning a
    real subprocess.
    """
    sys_hash = pool._hash_sys_prompt(sys_prompt)
    slot = _PrewarmSlot(proc=proc, sys_prompt_hash=sys_hash)  # type: ignore[arg-type]
    with pool._lock:
        pool._slots[sys_hash] = slot


def test_default_max_age_is_900_seconds() -> None:
    """The legacy 60 s default was producing 0% hit rate. P2-1 raises it to 900 s."""
    pool = _ClaudeWorkerPool(
        claude_path="/bin/true",
        env={},
        project_cwd="/tmp",
    )
    assert pool._max_age_seconds == 900.0


def test_acquire_hit_increments_hit_counter() -> None:
    """A successful acquire bumps hits, not misses."""
    pool = _build_pool()
    _install_fresh_slot(pool, "test-prompt", _FakeProc(pid=1001, alive=True))
    proc, age = pool.acquire("test-prompt")
    assert proc is not None
    assert proc.pid == 1001
    assert age >= 0.0
    assert pool._hit_count == 1
    assert pool._miss_count == 0
    assert pool._stale_disposed_count == 0


def test_acquire_empty_increments_miss_counter() -> None:
    """No matching slot → miss, not stale_disposed."""
    pool = _build_pool()
    proc, age = pool.acquire("never-prewarmed")
    assert proc is None
    assert age == 0.0
    assert pool._hit_count == 0
    assert pool._miss_count == 1
    assert pool._stale_disposed_count == 0


def test_acquire_dead_proc_increments_stale_disposed() -> None:
    """Dead slot → disposed + counted as both miss and stale_disposed."""
    pool = _build_pool()
    _install_fresh_slot(pool, "dead-prompt", _FakeProc(pid=2002, alive=False))
    proc, age = pool.acquire("dead-prompt")
    assert proc is None
    assert pool._hit_count == 0
    assert pool._miss_count == 1
    assert pool._stale_disposed_count == 1


def test_acquire_stale_proc_increments_stale_disposed() -> None:
    """A slot older than ``max_age_seconds`` is disposed without use."""
    pool = _build_pool(max_age_seconds=0.001)  # 1 ms freshness window
    _install_fresh_slot(pool, "stale-prompt", _FakeProc(pid=3003, alive=True))
    time.sleep(0.05)  # age past the 1 ms window
    proc, _age = pool.acquire("stale-prompt")
    assert proc is None
    assert pool._hit_count == 0
    assert pool._miss_count == 1
    assert pool._stale_disposed_count == 1


def test_stats_emit_fires_once_per_interval() -> None:
    """CLAUDE_POOL_STATS appears in the log stream after the interval elapses."""
    pool = _build_pool(stats_interval_seconds=0.05)  # 50 ms cadence
    captured: list[str] = []
    sink_id = logger.add(
        lambda msg: captured.append(str(msg)), level="INFO",
    )
    try:
        pool.acquire("x")  # miss; under interval -> no emit yet
        assert not [m for m in captured if "CLAUDE_POOL_STATS" in m]
        time.sleep(0.1)
        pool.acquire("x")  # miss; interval has elapsed -> emit
    finally:
        logger.remove(sink_id)

    stats_lines = [m for m in captured if "CLAUDE_POOL_STATS" in m]
    assert stats_lines, (
        f"expected CLAUDE_POOL_STATS after stats_interval elapsed: "
        f"captured={captured[:5]}"
    )
    line = stats_lines[-1]
    for field in (
        "hits=", "misses=", "stale_disposed=",
        "spawn_failed=", "hit_rate_pct=",
        "slots_currently_held=", "max_age_s=",
    ):
        assert field in line, f"missing field {field!r} in {line}"


def test_stats_emit_disabled_when_interval_zero() -> None:
    """stats_interval_seconds <= 0 disables periodic emission."""
    pool = _build_pool(stats_interval_seconds=0.0)
    captured: list[str] = []
    sink_id = logger.add(
        lambda msg: captured.append(str(msg)), level="INFO",
    )
    try:
        for _ in range(5):
            pool.acquire("y")
            time.sleep(0.01)
    finally:
        logger.remove(sink_id)
    assert not [m for m in captured if "CLAUDE_POOL_STATS" in m], (
        f"emit fired despite interval=0: {captured[:3]}"
    )
