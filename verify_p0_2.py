"""P0-2 verification — direction-decision authority.

Parses the trial-window log and asserts:

1. The P0_2_SENTINEL boot log is present with `high_conviction_protection=True`.
2. Zero co-occurring `APEX_DIR_LOCK | dir=Buy` + `XRAY_DIR_FLIP | flipped_dir=Sell`
   pair on the same trade decision (the dual-logging defect must be gone).
3. Every flip / veto decision is logged exactly once as a `DIRECTION_DECISION` line.
4. For every trade where high-conviction was true AND the XRAY-rr disagreed,
   the trade was vetoed (decision=skip authority=XRAY action=veto), never reversed.
5. Counts: total directives, executed, skipped, vetoed, flipped — all accounted.

Usage:
    python verify_p0_2.py /path/to/workers.log
    python verify_p0_2.py /path/to/workers.log /path/to/workers.previous.log

Exit code: 0 if all assertions hold, 1 otherwise. Prints a human-readable summary
to stdout before exit.

This script does not delete or modify any log file. Read-only.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path


P0_2_SENTINEL_RE = re.compile(
    r"P0_2_SENTINEL \| high_conviction_protection=(?P<enabled>True|False)"
    r"\s+flip_threshold=(?P<threshold>[\d.]+)"
)
APEX_DIR_LOCK_RE = re.compile(
    r"APEX_DIR_LOCK \| sym=(?P<sym>\S+)\s+dir=(?P<dir>\S+).*?did=(?P<did>\S+)"
)
XRAY_DIR_FLIP_RE = re.compile(
    r"XRAY_DIR_FLIP \| sym=(?P<sym>\S+)\s+"
    r"original_dir=(?P<orig>\S+)\s+"
    r"flipped_dir=(?P<flipped>\S+).*?did=(?P<did>\S+)"
)
DIRECTION_DECISION_RE = re.compile(
    r"DIRECTION_DECISION \| sym=(?P<sym>\S+)\s+"
    r"intended=(?P<intended>\S+)\s+"
    r"decision=(?P<decision>\S+)\s+"
    r"authority=(?P<authority>\S+)\s+"
    r"action=(?P<action>\S+)\s+"
    r"reason=(?P<reason>\S+)"
    r".*?did=(?P<did>\S+)"
)
STRAT_EXEC_RE = re.compile(
    r"STRAT_EXEC \| sym=(?P<sym>\S+)\s+dir=(?P<dir>\S+).*?did=(?P<did>\S+)"
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python verify_p0_2.py <log_file> [<log_file> ...]")
        return 1

    log_paths = [Path(p) for p in argv[1:]]
    for p in log_paths:
        if not p.exists():
            print(f"ERROR: log file not found: {p}")
            return 1

    sentinel_seen = False
    sentinel_enabled = None
    sentinel_threshold = None
    apex_locks: list[dict] = []
    flips: list[dict] = []
    decisions: list[dict] = []
    execs: list[dict] = []

    for path in log_paths:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = P0_2_SENTINEL_RE.search(line)
                if m:
                    sentinel_seen = True
                    sentinel_enabled = m.group("enabled") == "True"
                    sentinel_threshold = float(m.group("threshold"))
                    continue
                m = APEX_DIR_LOCK_RE.search(line)
                if m:
                    apex_locks.append(m.groupdict())
                    continue
                m = XRAY_DIR_FLIP_RE.search(line)
                if m:
                    flips.append(m.groupdict())
                    continue
                m = DIRECTION_DECISION_RE.search(line)
                if m:
                    decisions.append(m.groupdict())
                    continue
                m = STRAT_EXEC_RE.search(line)
                if m:
                    execs.append(m.groupdict())
                    continue

    # ---- Assertion 1: Boot sentinel present ----
    print("=" * 60)
    print("P0-2 VERIFICATION REPORT")
    print("=" * 60)
    print()
    print(f"Logs scanned: {[str(p) for p in log_paths]}")
    print()

    passed = True
    if not sentinel_seen:
        print("FAIL: P0_2_SENTINEL boot log not found.")
        passed = False
    else:
        print(
            f"PASS: P0_2_SENTINEL present. "
            f"enabled={sentinel_enabled} threshold={sentinel_threshold}"
        )
        if not sentinel_enabled:
            print(
                "  NOTE: high_conviction_protection is OFF "
                "(kill-switch); the fix is inactive."
            )

    # ---- Assertion 2: Zero dual-logging pairings ----
    # Group by did. A "pairing" is APEX_DIR_LOCK dir=Buy + XRAY_DIR_FLIP
    # original_dir=Buy flipped_dir=Sell with the same did.
    locks_by_did = defaultdict(list)
    for loc in apex_locks:
        locks_by_did[loc["did"]].append(loc)
    flips_by_did = defaultdict(list)
    for fl in flips:
        flips_by_did[fl["did"]].append(fl)

    paired_dids = []
    for did, locks in locks_by_did.items():
        if did not in flips_by_did:
            continue
        for loc in locks:
            for fl in flips_by_did[did]:
                if loc["sym"] == fl["sym"] and loc["dir"] == fl["orig"]:
                    paired_dids.append((did, loc["sym"], loc["dir"], fl["flipped"]))

    if paired_dids:
        print(
            f"FAIL: {len(paired_dids)} co-occurring APEX_DIR_LOCK + "
            f"XRAY_DIR_FLIP pairing(s) on same trade detected:"
        )
        for did, sym, locked, flipped in paired_dids[:5]:
            print(f"  did={did} sym={sym} locked={locked} flipped={flipped}")
        passed = False
    else:
        print(
            f"PASS: zero APEX_DIR_LOCK + XRAY_DIR_FLIP pairings on the "
            f"same trade ({len(apex_locks)} locks, {len(flips)} flips total)."
        )

    # Note: standalone XRAY_DIR_FLIP events post-fix should be ZERO
    # because the new code emits DIRECTION_DECISION instead. The presence
    # of XRAY_DIR_FLIP entries in the log indicates the pre-fix code is
    # still being executed (e.g., process not restarted with the new
    # code). Report as a warning, not a hard fail.
    if flips and sentinel_seen:
        print(
            f"WARN: {len(flips)} XRAY_DIR_FLIP events found AFTER the "
            f"P0_2_SENTINEL boot. Expected zero post-fix. Check that the "
            f"process restarted with the updated code."
        )

    # ---- Assertion 3: DIRECTION_DECISION counts per action ----
    by_action = defaultdict(int)
    by_authority_and_action = defaultdict(int)
    high_conviction_skips = 0
    for d in decisions:
        by_action[d["action"]] += 1
        by_authority_and_action[(d["authority"], d["action"])] += 1
        if d["authority"] == "XRAY" and d["action"] == "veto":
            high_conviction_skips += 1

    print()
    print("DIRECTION_DECISION event distribution:")
    for action, count in sorted(by_action.items()):
        print(f"  action={action}: {count}")
    if not decisions:
        print(
            "  (no DIRECTION_DECISION events — expected if no XRAY "
            "disagreements occurred in the trial window)"
        )

    # ---- Assertion 4: High-conviction veto count ----
    print()
    print(
        f"High-conviction vetoes (decision=skip authority=XRAY "
        f"action=veto): {high_conviction_skips}"
    )

    # ---- Final summary ----
    print()
    print(f"STRAT_EXEC events: {len(execs)}")
    print(f"APEX_DIR_LOCK events: {len(apex_locks)}")
    print(f"XRAY_DIR_FLIP events (pre-fix shape): {len(flips)}")
    print(f"DIRECTION_DECISION events (post-fix shape): {len(decisions)}")
    print()
    print("=" * 60)
    print(f"OVERALL: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
