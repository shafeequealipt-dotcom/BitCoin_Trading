"""Phase 7 (post-Layer-1 fix) — CLAUDE_PROC_STALL graduated levels.

The bucket-based stall detector previously fired all three named events
(``CLAUDE_PROC_STALL_60S`` / ``_120S`` / ``_240S``) at WARNING level.
Production logs show every successful brain call hits the 60 s bucket
because Claude subprocess startup latency is ~60-90 s — operators
learned to ignore stall WARNINGs entirely, defeating the point of
having one.

This commit introduces graduated severity:
  - 60 s: INFO (informational; first stall window is normal)
  - 120 s: WARNING (something is wrong)
  - 240 s+: ERROR (approaching SIGKILL territory)

The legacy tag ``CLAUDE_PROC_STALL`` (no suffix) was demoted from
WARNING to DEBUG — the named buckets carry the same information.

These tests exercise the level-classification logic by source
inspection. Running the actual stall code-path requires spawning a real
subprocess, which is heavy for negligible coverage gain.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_7_stall_threshold.md``.
"""

from __future__ import annotations

import re
from pathlib import Path


CLAUDE_CLIENT = (
    Path(__file__).parent.parent / "src" / "brain" / "claude_code_client.py"
)


def test_60s_bucket_logs_at_info() -> None:
    """First stall bucket (60 s) is informational, not alarming."""
    src = CLAUDE_CLIENT.read_text()
    # The classification is `if threshold <= 60.0: log_fn = log.info`.
    assert "log_fn = log.info" in src, (
        "60 s bucket must log at INFO — production logs show this fires "
        "on every successful brain call (Claude startup latency is ~60-90 s)."
    )


def test_120s_bucket_logs_at_warning() -> None:
    """Second stall bucket (120 s) signals something is wrong."""
    src = CLAUDE_CLIENT.read_text()
    assert "log_fn = log.warning" in src


def test_240s_bucket_logs_at_error() -> None:
    """Third stall bucket (240 s+) is approaching SIGKILL — escalate to ERROR."""
    src = CLAUDE_CLIENT.read_text()
    assert "log_fn = log.error" in src


def test_legacy_stall_log_demoted_to_debug() -> None:
    """The legacy CLAUDE_PROC_STALL (no suffix) emission must be DEBUG.

    The named buckets carry the same information at proper graduation;
    the legacy tag at WARNING was effectively dead-code (operators
    learned to ignore it). DEBUG keeps it available for forensic
    deep-dives without polluting the steady-state stream.
    """
    src = CLAUDE_CLIENT.read_text()
    # Find the legacy emission block — the one that uses
    # _STALL_LOG_EVERY_S throttling and the no-suffix CLAUDE_PROC_STALL tag.
    pattern = re.compile(
        r'log\.\w+\(\s*\n?\s*f"CLAUDE_PROC_STALL \|',
        re.DOTALL,
    )
    matches = list(pattern.finditer(src))
    # There should be exactly one legacy emission (the named ones are
    # CLAUDE_PROC_STALL_60S etc. with the int interpolated).
    assert len(matches) == 1, (
        f"Expected exactly 1 legacy CLAUDE_PROC_STALL emission, got "
        f"{len(matches)}. The Phase 7 demotion contract is broken."
    )
    legacy_emit = matches[0].group(0)
    assert "log.debug" in legacy_emit, (
        f"Legacy CLAUDE_PROC_STALL must emit at DEBUG, got: {legacy_emit!r}"
    )


def test_buckets_are_settings_driven() -> None:
    """Stall thresholds must be reachable from
    ``settings.brain.stall_warn_buckets_seconds``.

    Hard-coded constants would prevent operator tuning without a code
    deploy. Phase 7 threads the value via the ClaudeCodeClient
    constructor kwarg ``stall_warn_buckets_seconds``; WorkerManager
    forwards ``settings.brain.stall_warn_buckets_seconds`` into that
    kwarg.
    """
    src = CLAUDE_CLIENT.read_text()
    # Constructor kwarg present.
    assert "stall_warn_buckets_seconds" in src
    # Instance field set from constructor input.
    assert "self._stall_warn_buckets" in src

    manager_src = (
        Path(__file__).parent.parent / "src" / "workers" / "manager.py"
    ).read_text()
    # WorkerManager wires the settings value into the constructor.
    assert "stall_warn_buckets_seconds=" in manager_src
    assert "_brain_cfg.stall_warn_buckets_seconds" in manager_src


def test_client_stall_buckets_default_when_none() -> None:
    """If no kwarg passed, falls back to (60, 120, 240) — backwards-compat
    for callers (e.g. core/container.py) that don't yet pass the kwarg."""
    from src.brain.claude_code_client import ClaudeCodeClient
    from unittest.mock import patch

    with patch.object(
        ClaudeCodeClient, "_log_diagnostics", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_validate_setup", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_find_claude", lambda self: "/bin/true"
    ), patch.object(
        ClaudeCodeClient, "_build_env",
        lambda self: {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    ):
        client = ClaudeCodeClient()
    assert client._stall_warn_buckets == (60.0, 120.0, 240.0)


def test_client_stall_buckets_accepts_custom_tuple() -> None:
    """Constructor kwarg overrides the default."""
    from src.brain.claude_code_client import ClaudeCodeClient
    from unittest.mock import patch

    with patch.object(
        ClaudeCodeClient, "_log_diagnostics", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_validate_setup", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_find_claude", lambda self: "/bin/true"
    ), patch.object(
        ClaudeCodeClient, "_build_env",
        lambda self: {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    ):
        client = ClaudeCodeClient(
            stall_warn_buckets_seconds=(5.0, 10.0, 20.0),
        )
    assert client._stall_warn_buckets == (5.0, 10.0, 20.0)


def test_client_stall_buckets_malformed_falls_back() -> None:
    """Malformed input must not crash construction — fall back to default."""
    from src.brain.claude_code_client import ClaudeCodeClient
    from unittest.mock import patch

    with patch.object(
        ClaudeCodeClient, "_log_diagnostics", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_validate_setup", lambda self: None
    ), patch.object(
        ClaudeCodeClient, "_find_claude", lambda self: "/bin/true"
    ), patch.object(
        ClaudeCodeClient, "_build_env",
        lambda self: {"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    ):
        # Empty tuple → fallback.
        client_empty = ClaudeCodeClient(stall_warn_buckets_seconds=())
        assert client_empty._stall_warn_buckets == (60.0, 120.0, 240.0)
        # Non-numeric → fallback.
        client_bad = ClaudeCodeClient(
            stall_warn_buckets_seconds=("a", "b", "c"),  # type: ignore[arg-type]
        )
        assert client_bad._stall_warn_buckets == (60.0, 120.0, 240.0)


def test_brain_settings_default_buckets_present() -> None:
    """Sanity: BrainSettings must still expose stall_warn_buckets_seconds."""
    from src.config.settings import BrainSettings
    bs = BrainSettings()
    assert hasattr(bs, "stall_warn_buckets_seconds")
    assert tuple(bs.stall_warn_buckets_seconds) == (60, 120, 240)
