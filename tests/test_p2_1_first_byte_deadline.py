"""P2-1 (2026-05-13) — Claude CLI first-byte deadline tests.

Verifies the new ``first_byte_timeout_seconds`` deadline added to
``ClaudeCodeClient._stream_subprocess_io``. Uses a small Python
subprocess as the "fake claude CLI" so we exercise the real Popen +
non-blocking read path end-to-end, matching the existing
test_brain_subprocess_streaming.py convention.

Three cases:

1. ``test_first_byte_deadline_fires_when_no_stdout_arrives`` — the
   fake CLI sleeps past the deadline before writing. The deadline
   trips, ``CLAUDE_PROC_FIRST_BYTE_DEADLINE`` is emitted at WARNING
   with the new fields, and ``RuntimeError`` propagates with the
   "first-byte deadline" message that ``send_message`` uses to emit
   ``CLAUDE_RETRY_ON_FIRST_BYTE_TIMEOUT``.

2. ``test_first_byte_deadline_does_not_fire_when_output_arrives_early`` —
   the fake CLI writes immediately; deadline never trips even when
   the value is small.

3. ``test_first_byte_deadline_disabled_when_zero`` — passing 0
   restores the legacy total-timeout-only behaviour.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import time
from unittest.mock import patch

import pytest
from loguru import logger

from src.brain import claude_code_client as ccc


def _fake_cli_path(tmp_path, body: str) -> str:
    """Same shape as the existing test_brain_subprocess_streaming helper."""
    body_normalized = textwrap.dedent(body).strip()
    script = tmp_path / "fake_claude_p2_1"
    header = (
        f"#!{sys.executable}\n"
        "import sys, time, os\n"
        "prompt = sys.stdin.read()\n"
    )
    script.write_text(header + body_normalized + "\n")
    script.chmod(0o755)
    return str(script)


def _build_client(claude_path: str, *, timeout_seconds: int, first_byte: float):
    """Construct ClaudeCodeClient with side-effects mocked, matching the
    project's existing fake-CLI test pattern."""
    with patch.object(
        ccc.ClaudeCodeClient, "_log_diagnostics", lambda self: None
    ), patch.object(
        ccc.ClaudeCodeClient, "_validate_setup", lambda self: None
    ), patch.object(
        ccc.ClaudeCodeClient, "_find_claude", lambda self: claude_path
    ), patch.object(
        ccc.ClaudeCodeClient, "_build_env",
        lambda self: {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    ):
        client = ccc.ClaudeCodeClient(
            timeout_seconds=timeout_seconds,
            first_byte_timeout_seconds=first_byte,
        )
    client._stall_warn_buckets = (0.5, 1.0, 2.0)
    client._cleanup_orphaned_processes = lambda: None
    return client


def test_first_byte_deadline_fires_when_no_stdout_arrives(tmp_path) -> None:
    """Subprocess that produces no stdout for 1.5 s must trip a 0.5 s deadline."""
    fake = _fake_cli_path(tmp_path, """
        time.sleep(1.5)
        sys.stdout.write("too-late")
        sys.stdout.flush()
    """)
    # total timeout 10 s so we know any timeout we see came from
    # the new first-byte path, not the legacy total-timeout path.
    client = _build_client(fake, timeout_seconds=10, first_byte=0.5)

    captured: list[str] = []
    sink_id = logger.add(
        lambda msg: captured.append(str(msg)), level="WARNING",
    )
    try:
        with pytest.raises(RuntimeError, match=r"first-byte deadline"):
            client._subprocess_call("hello")
    finally:
        logger.remove(sink_id)

    joined = "\n".join(captured)
    assert "CLAUDE_PROC_FIRST_BYTE_DEADLINE" in joined, (
        f"expected first-byte deadline emit. captured={joined[:500]}"
    )
    # Shape check on the new tag's fields per Rule 6.
    m = re.search(
        r"CLAUDE_PROC_FIRST_BYTE_DEADLINE \| pid=\d+ "
        r"elapsed_s=\d+ deadline_s=\d+",
        joined,
    )
    assert m is not None, f"new tag shape mismatch: {joined[:500]}"
    # The cascade emit should classify this as first_byte_deadline.
    assert "BRAIN_FAILURE_CASCADE" in joined
    assert "kind=first_byte_deadline" in joined, (
        f"cascade emit must distinguish first-byte vs total timeout: {joined[:500]}"
    )


def test_first_byte_deadline_does_not_fire_when_output_arrives_early(
    tmp_path,
) -> None:
    """Immediate stdout output keeps the deadline silent even with a tight setting."""
    fake = _fake_cli_path(tmp_path, """
        sys.stdout.write("on-time-output")
        sys.stdout.flush()
    """)
    client = _build_client(fake, timeout_seconds=5, first_byte=0.2)

    captured_warn: list[str] = []
    sink_id = logger.add(
        lambda msg: captured_warn.append(str(msg)), level="WARNING",
    )
    try:
        result = client._subprocess_call("hello")
    finally:
        logger.remove(sink_id)

    assert "on-time-output" in result
    joined = "\n".join(captured_warn)
    assert "CLAUDE_PROC_FIRST_BYTE_DEADLINE" not in joined, (
        f"deadline tripped on a healthy call: {joined[:300]}"
    )
    assert "BRAIN_FAILURE_CASCADE" not in joined


def test_first_byte_deadline_disabled_when_zero(tmp_path) -> None:
    """first_byte=0 disables the new path; the legacy total-timeout still works."""
    # Body: write nothing, sleep past first_byte-equivalent. With first_byte=0
    # disabled, the call should still succeed via the late write.
    fake = _fake_cli_path(tmp_path, """
        time.sleep(0.6)
        sys.stdout.write("after-disabled-deadline")
        sys.stdout.flush()
    """)
    client = _build_client(fake, timeout_seconds=5, first_byte=0.0)
    assert client._first_byte_timeout == 0.0

    captured: list[str] = []
    sink_id = logger.add(
        lambda msg: captured.append(str(msg)), level="WARNING",
    )
    try:
        result = client._subprocess_call("hello")
    finally:
        logger.remove(sink_id)

    assert "after-disabled-deadline" in result
    joined = "\n".join(captured)
    assert "CLAUDE_PROC_FIRST_BYTE_DEADLINE" not in joined, (
        f"deadline fired despite first_byte=0: {joined[:300]}"
    )
