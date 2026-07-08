#!/usr/bin/env python3
"""Issue 1 verification — the CALL_A latency CLI flags wire end-to-end.

Proves, without any live claude call:
  1. The real config.toml [brain] loads claude_cli_effort/bare/exclude into
     BrainSettings.
  2. A ClaudeCodeClient built from those values produces the exact
     --effort/--bare/--exclude flag list at the spawn sites, and hands the SAME
     list to its prewarm pool (so a warm worker matches the decision call).
  3. The OFF config (effort="" + both bools False) yields an EMPTY flag list —
     byte-identical to the pre-Issue-1 invocation (the revert path).
  4. The CLAUDE_CLI_FLAGS_CONFIG boot sentinel fires.

Read-only. No data is written. Exit code 0 = pass.
"""
from __future__ import annotations

import sys

from src.config.settings import Settings
from src.brain import claude_code_client as ccc


def build_client(effort: str, bare: bool, exclude: bool, sink: list[str]):
    orig_info, orig_warn = ccc.log.info, ccc.log.warning

    def _spy(real):
        def _f(msg, *a, **k):
            sink.append(str(msg))
            return real(msg, *a, **k)
        return _f

    ccc.log.info = _spy(orig_info)
    ccc.log.warning = _spy(orig_warn)  # capture the effort-invalid warning too
    try:
        return ccc.ClaudeCodeClient(
            model="claude-opus-4-7",
            effort=effort,
            bare=bare,
            exclude_dynamic_system_prompt=exclude,
        )
    finally:
        ccc.log.info, ccc.log.warning = orig_info, orig_warn


def main() -> int:
    failures: list[str] = []

    # 1. Real config loads the activation.
    s = Settings.load()
    b = s.brain
    print("== Loaded config.toml [brain] ==")
    print(f"  claude_cli_effort = {b.claude_cli_effort!r}")
    print(f"  claude_cli_bare = {b.claude_cli_bare!r}")
    print(f"  claude_cli_exclude_dynamic_system_prompt = "
          f"{b.claude_cli_exclude_dynamic_system_prompt!r}")
    if b.claude_cli_effort != "medium":
        failures.append(f"effort expected 'medium', got {b.claude_cli_effort!r}")
    # Both bool flags are intentionally OFF on CLI 2.1.167: --bare skips OAuth login
    # (breaks the call), and --exclude-dynamic-system-prompt-sections is a no-op when
    # --system-prompt is supplied (which the brain always does). The only active
    # lever is --effort. The plumbing for both bools stays (harmless when off).
    if b.claude_cli_bare is not False:
        failures.append(f"bare expected False (breaks auth on 2.1.167), got "
                        f"{b.claude_cli_bare!r}")
    if b.claude_cli_exclude_dynamic_system_prompt is not False:
        failures.append("exclude_dynamic expected False (no-op with --system-prompt)")

    # 2. Client from the live values produces the expected flag list + matches pool.
    sink: list[str] = []
    client = build_client(
        b.claude_cli_effort, b.claude_cli_bare,
        b.claude_cli_exclude_dynamic_system_prompt, sink,
    )
    expected = ["--effort", "medium"]
    print("\n== Activated client flag list ==")
    print(f"  client._extra_cli_flags = {client._extra_cli_flags}")
    print(f"  pool._extra_flags       = {client._proc_pool._extra_flags}")
    if client._extra_cli_flags != expected:
        failures.append(f"client flags {client._extra_cli_flags} != {expected}")
    if client._proc_pool._extra_flags != expected:
        failures.append("pool flags differ from client flags (warm worker mismatch)")

    # 3. The boot sentinel fired with the right content.
    sentinel = [ln for ln in sink if "CLAUDE_CLI_FLAGS_CONFIG" in ln]
    print("\n== Boot sentinel ==")
    print(f"  {sentinel[0] if sentinel else '(MISSING)'}")
    if not sentinel:
        failures.append("CLAUDE_CLI_FLAGS_CONFIG sentinel not emitted")
    elif "effort=medium" not in sentinel[0]:
        failures.append("sentinel missing effort=medium")

    # 4. OFF config => empty flags => byte-identical revert.
    sink2: list[str] = []
    off = build_client("", False, False, sink2)
    print("\n== Reverted (off) client flag list ==")
    print(f"  off._extra_cli_flags = {off._extra_cli_flags}")
    if off._extra_cli_flags != []:
        failures.append(f"OFF flags expected [], got {off._extra_cli_flags}")
    off_sentinel = [ln for ln in sink2 if "CLAUDE_CLI_FLAGS_CONFIG" in ln]
    if off_sentinel and "(none)" not in off_sentinel[0]:
        failures.append("OFF sentinel should show flags='(none)'")

    # 5. Invalid effort value is rejected (a config typo must not break brain calls).
    sink3: list[str] = []
    bad = build_client("turbo", False, False, sink3)
    print("\n== Invalid effort guard ==")
    print(f"  effort='turbo' -> flags = {bad._extra_cli_flags}")
    if bad._extra_cli_flags != []:
        failures.append(f"invalid effort must be dropped, got {bad._extra_cli_flags}")
    if not any("CLAUDE_CLI_EFFORT_INVALID" in ln for ln in sink3):
        failures.append("invalid effort should emit CLAUDE_CLI_EFFORT_INVALID warning")

    print("\n== RESULT ==")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: latency flags wire end-to-end; revert path is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
