"""T2-1 Claude CLI subprocess pre-spawn pool tests.

The Claude CLI in ``-p`` mode is single-shot — it processes one prompt
then exits. The pool pre-spawns ONE worker per system_prompt key so the
spawn + CLI bootup latency (~1-5 s) is hidden behind the previous
call's API wait time. The actual API latency (60-240 s for first
stdout token, the dominant cost) is unchanged — that's network +
inference time.

These tests use a tiny shell-script stand-in for the Claude binary
that immediately exits, so we can verify pool lifecycle without
requiring real Claude credentials.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# A trivial "Claude CLI" stand-in that:
#   1. Reads stdin until EOF
#   2. Echoes the input wrapped in a marker
#   3. Exits
#
# Behaves like ``-p`` mode (single-shot, exit on EOF) so the pool's
# acquire/replenish lifecycle can be exercised without the real
# Anthropic dependency.
_FAKE_CLAUDE_SCRIPT = """#!/bin/bash
exec cat
"""


@pytest.fixture
def fake_claude_path(tmp_path):
    """Create a writable fake Claude binary for the test."""
    p = tmp_path / "fake_claude"
    p.write_text(_FAKE_CLAUDE_SCRIPT)
    p.chmod(0o755)
    return str(p)


def _make_pool(claude_path: str, max_age_seconds: float = 60.0):
    from src.brain.claude_code_client import _ClaudeWorkerPool
    pool = _ClaudeWorkerPool(
        claude_path=claude_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        project_cwd=os.getcwd(),
        max_age_seconds=max_age_seconds,
    )
    # F28 (2026-06-05): these tests validate the slot mechanics (replenish /
    # acquire / dispose), which are now gated on the warm-pool canary having
    # confirmed the CLI is healthy. Mark reuse healthy so the mechanics run
    # without a live canary call (canary gating itself is covered by
    # verify_warm_pool_safety.py).
    pool._reuse_healthy = True
    return pool


# ── T2-1 unit tests: hash + acquire + replenish + dispose ────────────


def test_t2_1_hash_sys_prompt_distinguishes_distinct_prompts():
    """SHA-256 prefix must distinguish the 2 production system prompts."""
    from src.brain.claude_code_client import _ClaudeWorkerPool
    h1 = _ClaudeWorkerPool._hash_sys_prompt("STRATEGIST_PROMPT_A")
    h2 = _ClaudeWorkerPool._hash_sys_prompt("POSITION_PROMPT_B")
    h3 = _ClaudeWorkerPool._hash_sys_prompt("")
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3
    assert len(h1) == 16  # 16-char prefix per implementation
    # Determinism
    assert h1 == _ClaudeWorkerPool._hash_sys_prompt("STRATEGIST_PROMPT_A")


def test_t2_1_empty_pool_returns_none(fake_claude_path):
    """acquire on a never-replenished pool returns (None, 0.0)."""
    pool = _make_pool(fake_claude_path)
    proc, age = pool.acquire("test_sys_prompt")
    assert proc is None
    assert age == 0.0
    pool.shutdown()


def test_t2_1_replenish_then_acquire(fake_claude_path):
    """After replenish_async completes, acquire returns the primed worker."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    # Wait for the daemon thread to spawn (typically <100ms)
    deadline = time.time() + 5.0
    proc = None
    while time.time() < deadline:
        proc, age = pool.acquire("sys_prompt_X")
        if proc is not None:
            break
        time.sleep(0.05)
    assert proc is not None, "replenish_async did not spawn within 5s"
    assert age >= 0.0
    # Cleanup: the test doesn't actually use the worker, so dispose it
    try:
        proc.stdin.close()
        proc.wait(timeout=2.0)
    except Exception:
        pass
    pool.shutdown()


def test_t2_1_acquire_pops_slot(fake_claude_path):
    """acquire removes the slot, so a second acquire returns None."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pool.known_pids():
            break
        time.sleep(0.05)
    proc1, _ = pool.acquire("sys_prompt_X")
    assert proc1 is not None
    proc2, _ = pool.acquire("sys_prompt_X")
    assert proc2 is None
    try:
        proc1.stdin.close()
        proc1.wait(timeout=2.0)
    except Exception:
        pass
    pool.shutdown()


def test_t2_1_per_sys_prompt_isolation(fake_claude_path):
    """A worker for system_prompt A must NOT be returned for B."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_A")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pool.known_pids():
            break
        time.sleep(0.05)
    # Acquire for B — should miss
    proc, _ = pool.acquire("sys_prompt_B")
    assert proc is None
    # Acquire for A — should hit
    proc, _ = pool.acquire("sys_prompt_A")
    assert proc is not None
    try:
        proc.stdin.close()
        proc.wait(timeout=2.0)
    except Exception:
        pass
    pool.shutdown()


def test_t2_1_stale_worker_disposed_not_returned(fake_claude_path):
    """A worker older than max_age_seconds is disposed without use."""
    pool = _make_pool(fake_claude_path, max_age_seconds=0.5)
    pool.replenish_async("sys_prompt_X")
    deadline = time.time() + 5.0
    primed_pid = None
    while time.time() < deadline:
        pids = pool.known_pids()
        if pids:
            primed_pid = next(iter(pids))
            break
        time.sleep(0.05)
    assert primed_pid is not None, "replenish_async did not spawn"
    # Wait until past max_age_seconds
    time.sleep(0.7)
    proc, age = pool.acquire("sys_prompt_X")
    assert proc is None  # Stale → disposed → returned None
    pool.shutdown()


def test_t2_1_known_pids_tracks_alive_workers(fake_claude_path):
    """known_pids returns all currently-pre-spawned, still-alive PIDs."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    pool.replenish_async("sys_prompt_Y")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if len(pool.known_pids()) >= 2:
            break
        time.sleep(0.05)
    pids = pool.known_pids()
    assert len(pids) >= 2
    pool.shutdown()


def test_t2_1_shutdown_disposes_all(fake_claude_path):
    """shutdown disposes all pre-spawned workers."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    pool.replenish_async("sys_prompt_Y")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if len(pool.known_pids()) >= 2:
            break
        time.sleep(0.05)
    assert len(pool.known_pids()) >= 2
    pool.shutdown()
    # Brief settle for SIGTERM/wait
    time.sleep(0.3)
    assert pool.known_pids() == set()


def test_t2_1_replenish_idempotent_for_fresh_slot(fake_claude_path):
    """A second replenish_async for an already-fresh slot is a no-op
    (does not spawn a duplicate worker)."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pool.known_pids():
            break
        time.sleep(0.05)
    pids_before = pool.known_pids()
    assert len(pids_before) == 1
    # Second replenish_async should NOT spawn a duplicate
    pool.replenish_async("sys_prompt_X")
    time.sleep(0.5)
    pids_after = pool.known_pids()
    assert pids_after == pids_before
    pool.shutdown()


# ── T2-1 contract test: pool integration with ClaudeCodeClient ───────


def test_t2_1_known_pids_set_supports_orphan_cleanup_exclusion(fake_claude_path):
    """known_pids returns a Python set containing ints, suitable for the
    orphan-cleanup ``pid in pool_pids`` check."""
    pool = _make_pool(fake_claude_path)
    pool.replenish_async("sys_prompt_X")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if pool.known_pids():
            break
        time.sleep(0.05)
    pids = pool.known_pids()
    assert isinstance(pids, set)
    for pid in pids:
        assert isinstance(pid, int)
        # `pid in set` is the exact check used by _cleanup_orphaned_processes
        assert pid in pids
    pool.shutdown()
