"""Phase 6 follow-up — subprocess streaming + stall detection tests.

Verifies the new chunked-stdout reader and ``CLAUDE_PROC_STALL`` /
``CLAUDE_PROC_PREKILL`` diagnostics in ``ClaudeCodeClient``.

These tests use a small Python subprocess as the "fake claude CLI"
so we exercise the real Popen + non-blocking read path end-to-end.
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


# ---------------------------------------------------------------------------
# Fake-CLI factories
# ---------------------------------------------------------------------------


def _fake_cli_path(tmp_path, body: str) -> str:
    """Write a shebang-executable script that behaves like the claude CLI.

    Reads the prompt from stdin (sees EOF when the parent closes stdin),
    ignores the real CLI flags (``-p``, ``--output-format text``,
    ``--system-prompt X``), then runs ``body``. ``body`` is a textwrap-
    dedent-style block; leading whitespace common to all lines is
    stripped so callers can write the body indented for readability.
    """
    body_normalized = textwrap.dedent(body).strip()
    script = tmp_path / "fake_claude"
    header = (
        f"#!{sys.executable}\n"
        "import sys, time, os\n"
        "# Absorb the real claude CLI flags (-p, --output-format,\n"
        "# --system-prompt). We don't use them — just need to accept.\n"
        "prompt = sys.stdin.read()\n"
    )
    script.write_text(header + body_normalized + "\n")
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def make_client():
    """Build a ClaudeCodeClient that uses a fake CLI binary."""
    def _build(claude_path: str, timeout_seconds: int = 10):
        # Skip the heavy __init__ side effects (diagnostics, validate).
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
            client = ccc.ClaudeCodeClient(timeout_seconds=timeout_seconds)
        # Tighter stall thresholds for fast tests.
        # Legacy throttle controls the (now-DEBUG-level) CLAUDE_PROC_STALL
        # generic emission; named buckets (CLAUDE_PROC_STALL_<N>S) fire
        # at INFO/WARNING/ERROR depending on threshold position.
        # Phase 7 (post-Layer-1 fix) made the buckets settings-driven —
        # tests use small thresholds so a 1.2 s subprocess pause trips
        # the second bucket at WARNING level (proves graduated severity).
        client._STALL_LOG_EVERY_S = 0.5
        client._stall_warn_buckets = (0.5, 1.0, 2.0)
        # Tests skip the orphan-cleanup step which would otherwise try
        # to kill unrelated Python processes during the run.
        client._cleanup_orphaned_processes = lambda: None
        return client
    return _build


# ---------------------------------------------------------------------------
# Successful streaming
# ---------------------------------------------------------------------------


class TestStreamingHappyPath:
    def test_writes_stdout_and_exits_cleanly(
        self, tmp_path, make_client
    ) -> None:
        """A normal call returns the full stdout, no stall warning."""
        fake = _fake_cli_path(tmp_path, """
            sys.stdout.write("ok-response")
            sys.stdout.flush()
        """)
        client = make_client(fake)

        captured = []
        sink_id = logger.add(
            lambda msg: captured.append(str(msg)), level="WARNING",
        )
        try:
            result = client._subprocess_call("hello")
        finally:
            logger.remove(sink_id)

        assert "ok-response" in result
        joined = "\n".join(captured)
        assert "CLAUDE_PROC_STALL" not in joined


# ---------------------------------------------------------------------------
# Stall detection
# ---------------------------------------------------------------------------


class TestStallDetection:
    def test_stall_log_fires_when_stdout_silent(
        self, tmp_path, make_client
    ) -> None:
        """A subprocess that pauses for > the second stall bucket
        produces a graduated CLAUDE_PROC_STALL_<N>S sequence — the named
        buckets fire at INFO (1st), WARNING (2nd), ERROR (3rd) per the
        Phase 7 (post-Layer-1 fix) graduated-severity contract.

        The legacy generic ``CLAUDE_PROC_STALL`` (no suffix) tag is now
        DEBUG-level (Phase 7) and is verified separately at DEBUG capture.
        """
        # Body: write an early byte, sleep 1.2s with no output, then exit.
        # With _stall_warn_buckets=(0.5, 1.0, 2.0) set in make_client we
        # expect: bucket 0.5 fires at INFO, bucket 1.0 fires at WARNING.
        # Bucket 2.0 stays unfired because the subprocess wakes after 1.2s.
        fake = _fake_cli_path(tmp_path, """
            sys.stdout.write("early-byte")
            sys.stdout.flush()
            time.sleep(1.2)
            sys.stdout.write("late-byte")
            sys.stdout.flush()
        """)
        client = make_client(fake, timeout_seconds=5)

        # Capture INFO+ so we see the 0.5 s INFO bucket too, plus the
        # 1.0 s WARNING bucket. The legacy generic CLAUDE_PROC_STALL
        # (no suffix) is at DEBUG and is asserted via a separate sink.
        captured_info_plus: list[str] = []
        captured_debug_plus: list[str] = []
        sink_info = logger.add(
            lambda msg: captured_info_plus.append(str(msg)), level="INFO",
        )
        sink_debug = logger.add(
            lambda msg: captured_debug_plus.append(str(msg)), level="DEBUG",
        )
        try:
            result = client._subprocess_call("hello")
        finally:
            logger.remove(sink_info)
            logger.remove(sink_debug)

        assert "early-byte" in result
        assert "late-byte" in result

        joined_info = "\n".join(captured_info_plus)
        joined_debug = "\n".join(captured_debug_plus)

        # The named bucket events must appear in INFO+ capture. The first
        # bucket (0.5 s) fires at INFO; second (1.0 s) at WARNING.
        assert "CLAUDE_PROC_STALL_" in joined_info, (
            f"expected at least one named bucket stall event. "
            f"captured={joined_info[:500]}"
        )
        # Legacy generic tag at DEBUG — confirms the rate-limited
        # backward-compat emission is preserved at the demoted level.
        assert "CLAUDE_PROC_STALL |" in joined_debug, (
            f"expected legacy CLAUDE_PROC_STALL at DEBUG. "
            f"captured(debug)={joined_debug[:500]}"
        )
        # Verify legacy stall log shape (preserved across the demotion).
        m = re.search(
            r"CLAUDE_PROC_STALL \| pid=\d+ silence_s=(\d+) "
            r"stdout_so_far=(\d+) timeout_in_s=(\d+)",
            joined_debug,
        )
        assert m is not None, (
            f"stall log shape mismatch: {joined_debug[:500]}"
        )


# ---------------------------------------------------------------------------
# Timeout + pre-kill diagnostics
# ---------------------------------------------------------------------------


class TestPreKillDiagnostics:
    def test_full_timeout_captures_prekill_log(
        self, tmp_path, make_client
    ) -> None:
        """A subprocess that hangs past the timeout produces a
        ``CLAUDE_PROC_PREKILL`` warning before kill.
        """
        # Body: hang forever, ignoring SIGTERM-friendly cleanup.
        fake = _fake_cli_path(tmp_path, """
            time.sleep(60)
        """)
        client = make_client(fake, timeout_seconds=1)

        captured = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
        try:
            with pytest.raises(RuntimeError, match=r"timed out"):
                client._subprocess_call("hello")
        finally:
            logger.remove(sink_id)

        joined = "\n".join(captured)
        assert "CLAUDE_PROC_PREKILL" in joined, (
            f"expected pre-kill diagnostic. captured={joined[:500]}"
        )
        m = re.search(
            r"CLAUDE_PROC_PREKILL \| pid=\d+ wchan=(\S+) status='(.+?)'",
            joined,
        )
        assert m is not None, f"pre-kill log shape mismatch: {joined[:500]}"
        # Linux processes blocked on a syscall typically have a non-empty
        # wchan; ones in idle/zombie can show "<idle>" or 0. We accept
        # any non-empty value here — the important thing is the log
        # fired with the structured field.
        wchan = m.group(1)
        assert len(wchan) > 0
