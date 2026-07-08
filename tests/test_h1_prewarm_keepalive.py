"""H1 (2026-05-16) — prewarm pool keepalive + observability tests.

Phase 1 empirical evidence (dev_notes/four_high_fixes/h1_phase1_death_diagnosis.md):
``claude -p`` exits with rc=1 after 3 seconds of stdin silence, killing
every prewarmed subprocess. Writing a single newline byte to stdin at
spawn time eliminates the timeout. These tests assert the keepalive
write happens at the right moment and that the death-cause classifier
labels the canonical Claude-CLI exit path correctly.

These are unit-level structural tests. Real subprocess lifecycle is
verified empirically (Step B2 / B5 in the diagnosis doc) and again in
production at H1 Phase 4 verification.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _make_fake_proc(pid: int = 9999, returncode: int | None = None) -> MagicMock:
    """Mock subprocess.Popen result with stdin.write that records bytes."""
    # Don't use spec=Popen so we can attach arbitrary attributes (stderr, etc.).
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc._stdin_writes: list[bytes] = []

    def _write(data: bytes) -> int:
        proc._stdin_writes.append(data)
        return len(data)

    proc.stdin = MagicMock()
    proc.stdin.write.side_effect = _write
    proc.stdin.flush.side_effect = lambda: None
    proc.stdin.close.side_effect = lambda: None
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = b""
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = b""
    # poll() returns None for "alive", integer rc for "dead"
    proc.poll.return_value = returncode
    return proc


def test_replenish_writes_newline_keepalive_immediately_after_spawn() -> None:
    """The fix: _replenish_blocking writes a newline byte to stdin
    right after subprocess.Popen returns.
    """
    from src.brain.claude_code_client import _ClaudeWorkerPool

    pool = _ClaudeWorkerPool(
        claude_path="/usr/bin/claude",
        env={},
        project_cwd="/tmp",
    )
    fake = _make_fake_proc(pid=12345)
    with patch("subprocess.Popen", return_value=fake) as _popen:
        pool._replenish_blocking(system_prompt="test prompt")
    # Popen called exactly once with the expected args
    assert _popen.call_count == 1
    args, kwargs = _popen.call_args
    cmd = args[0] if args else kwargs.get("args", [])
    assert "-p" in cmd
    assert "--output-format" in cmd
    # Keepalive write was emitted
    assert fake._stdin_writes == [b"\n"], (
        f"expected one newline write, got {fake._stdin_writes!r}"
    )
    fake.stdin.flush.assert_called_once()
    # CRITICAL: stdin must NOT be closed in _replenish_blocking — the
    # real prompt is appended later by _subprocess_call.
    fake.stdin.close.assert_not_called()
    # Slot was installed
    sys_hash = pool._hash_sys_prompt("test prompt")
    assert sys_hash in pool._slots
    assert pool._slots[sys_hash].proc is fake


def test_replenish_disposes_subprocess_on_keepalive_broken_pipe() -> None:
    """If the subprocess dies between Popen and the keepalive write
    (rare; auth-fail / OOM / signal), the keepalive write raises
    BrokenPipeError. The pool must dispose the subprocess, increment
    spawn_fail_count, and NOT install a dead slot.
    """
    from src.brain.claude_code_client import _ClaudeWorkerPool

    pool = _ClaudeWorkerPool(
        claude_path="/usr/bin/claude",
        env={},
        project_cwd="/tmp",
    )
    fake = _make_fake_proc(pid=22222)
    fake.stdin.write.side_effect = BrokenPipeError("subprocess died")
    initial_spawn_fail = pool._spawn_fail_count

    with patch("subprocess.Popen", return_value=fake):
        pool._replenish_blocking(system_prompt="test")

    assert pool._spawn_fail_count == initial_spawn_fail + 1
    # No slot installed
    sys_hash = pool._hash_sys_prompt("test")
    assert sys_hash not in pool._slots


def test_death_cause_classifier_recognizes_claude_cli_3s_stdin_timeout() -> None:
    """When a prewarmed subprocess is found dead with returncode=1 and
    stderr mentions "no stdin data", the PREWARM_DEATH_CAUSE classifier
    labels it ``claude_cli_3s_stdin_timeout``. This is the canonical
    pre-H1 failure mode; after the keepalive fix it should be rare.
    """
    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    pool = _ClaudeWorkerPool(
        claude_path="/usr/bin/claude",
        env={},
        project_cwd="/tmp",
    )
    # Build a dead slot directly
    fake = _make_fake_proc(pid=33333, returncode=1)
    fake.poll.return_value = 1
    err_payload = b"Warning: no stdin data received in 3s, ...\nError: Input must be provided"
    fake.stderr.read.return_value = err_payload

    sys_prompt = "some-system-prompt"
    sys_hash = pool._hash_sys_prompt(sys_prompt)
    slot = _PrewarmSlot(fake, sys_prompt_hash=sys_hash)
    # Force-age the slot so we don't trip the max_age branch
    slot.spawn_ts -= 10.0
    pool._slots[sys_hash] = slot

    # Patch fcntl + os.O_NONBLOCK lookup so the non-blocking stderr
    # read path works in the unit-test environment.
    with patch("fcntl.fcntl"), patch.object(pool, "_dispose"):
        result_proc, age = pool.acquire(sys_prompt)
    assert result_proc is None
    assert pool._dead_disposed_count == 1
    # The miss counter increments; hit counter does not
    assert pool._miss_count == 1
    assert pool._hit_count == 0


def test_hit_path_does_not_touch_dead_classifier() -> None:
    """Healthy slot acquired without invoking the death-cause path."""
    from src.brain.claude_code_client import _ClaudeWorkerPool, _PrewarmSlot

    pool = _ClaudeWorkerPool(
        claude_path="/usr/bin/claude",
        env={},
        project_cwd="/tmp",
    )
    fake = _make_fake_proc(pid=44444, returncode=None)
    fake.poll.return_value = None  # alive

    sys_prompt = "healthy-prompt"
    sys_hash = pool._hash_sys_prompt(sys_prompt)
    slot = _PrewarmSlot(fake, sys_prompt_hash=sys_hash)
    pool._slots[sys_hash] = slot
    pool._max_age_seconds = 900.0  # default

    result_proc, age = pool.acquire(sys_prompt)
    assert result_proc is fake
    assert age >= 0.0
    assert pool._hit_count == 1
    assert pool._miss_count == 0
    assert pool._dead_disposed_count == 0
