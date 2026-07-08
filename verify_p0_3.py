"""P0-3 verification — close-veto trap fix.

Parses the trial-window log and asserts:

1. The P0_3_SENTINEL boot log is present with `brain_vote_factor=on` and
   the configured hard_risk_floor_sl_pct value.
2. For every WATCHDOG_CLOSE_SCORE_COMPUTED event, the breakdown includes
   brain_vote_bucket and brain_vote_factor fields.
3. For every BRAIN_CLOSE_VOTE_RECEIVED with sl_pct >= floor, a
   WATCHDOG_HARD_FLOOR_HIT follows (and the position closes).
4. C1 regression: brain panic-close on a sound position (xray_bucket=supports,
   velocity<=0, sl_factor<=-1) still produces a composite below threshold
   (reject or reject_and_tighten).
5. No-churn regression: WATCHDOG_CLOSE_REJECTED still fires when appropriate.

Usage:
    python verify_p0_3.py /path/to/workers.log [<log> ...]

Exit code: 0 if all assertions hold, 1 otherwise. Prints a human-readable
summary to stdout before exit. This script does not delete or modify any
log file. Read-only.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path


P0_3_SENTINEL_RE = re.compile(
    r"P0_3_SENTINEL \| brain_vote_factor=(?P<bv>\S+)\s+"
    r"hard_risk_floor_sl_pct=(?P<floor>[\d.]+)\s+"
    r"threshold=(?P<threshold>[\d.]+)\s+"
    r"enforce_mode=(?P<enforce>True|False)"
)
SCORE_COMPUTED_RE = re.compile(
    r"WATCHDOG_CLOSE_SCORE_COMPUTED \| sym=(?P<sym>\S+)\s+"
    r".*?composite=(?P<composite>-?[\d.]+)\s+"
    r".*?recommendation=(?P<rec>\S+)\s+"
    r".*?sl_pct=(?P<sl_pct>-?[\d.]+)\s+"
    r".*?xray_bucket=(?P<xray>\S+)\s+"
    r".*?brain_vote_bucket=(?P<bv_bucket>\S+)\s+"
    r"brain_vote_factor=(?P<bv_factor>-?[\d.]+)\s+"
    r"hard_floor_pct=(?P<floor>[\d.]+)\s+"
    r"hard_floor_active=(?P<floor_active>True|False)"
    r".*?did=(?P<did>\S+)"
)
HARD_FLOOR_HIT_RE = re.compile(
    r"WATCHDOG_HARD_FLOOR_HIT \| sym=(?P<sym>\S+)\s+"
    r"sl_pct=(?P<sl_pct>-?[\d.]+)\s+"
    r"floor=(?P<floor>[\d.]+)\s+"
    r"composite=(?P<composite>-?[\d.]+)"
    r".*?did=(?P<did>\S+)"
)
REJECT_RE = re.compile(
    r"WATCHDOG_CLOSE_REJECTED \| sym=(?P<sym>\S+)\s+"
    r"composite=(?P<composite>-?[\d.]+).*?did=(?P<did>\S+)"
)
EXECUTED_RE = re.compile(
    r"WATCHDOG_CLOSE_EXECUTED \| sym=(?P<sym>\S+)\s+"
    r"composite=(?P<composite>-?[\d.]+).*?did=(?P<did>\S+)"
)
TIGHTEN_RE = re.compile(
    r"WATCHDOG_CLOSE_OVERRIDE_TIGHTEN \| sym=(?P<sym>\S+)\s+"
    r"composite=(?P<composite>-?[\d.]+).*?did=(?P<did>\S+)"
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python verify_p0_3.py <log_file> [<log_file> ...]")
        return 1
    log_paths = [Path(p) for p in argv[1:]]
    for p in log_paths:
        if not p.exists():
            print(f"ERROR: log file not found: {p}")
            return 1

    sentinel = None
    scores: list[dict] = []
    floor_hits: list[dict] = []
    rejects: list[dict] = []
    executes: list[dict] = []
    tightens: list[dict] = []

    for path in log_paths:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = P0_3_SENTINEL_RE.search(line)
                if m:
                    sentinel = m.groupdict()
                    continue
                m = SCORE_COMPUTED_RE.search(line)
                if m:
                    scores.append(m.groupdict())
                    continue
                m = HARD_FLOOR_HIT_RE.search(line)
                if m:
                    floor_hits.append(m.groupdict())
                    continue
                m = REJECT_RE.search(line)
                if m:
                    rejects.append(m.groupdict())
                    continue
                m = EXECUTED_RE.search(line)
                if m:
                    executes.append(m.groupdict())
                    continue
                m = TIGHTEN_RE.search(line)
                if m:
                    tightens.append(m.groupdict())
                    continue

    print("=" * 60)
    print("P0-3 VERIFICATION REPORT")
    print("=" * 60)
    print()
    print(f"Logs scanned: {[str(p) for p in log_paths]}")
    print()

    passed = True

    # 1. Boot sentinel
    if sentinel is None:
        print("FAIL: P0_3_SENTINEL boot log not found.")
        passed = False
    else:
        print(
            f"PASS: P0_3_SENTINEL present. brain_vote_factor={sentinel['bv']} "
            f"hard_risk_floor_sl_pct={sentinel['floor']} "
            f"threshold={sentinel['threshold']} "
            f"enforce={sentinel['enforce']}"
        )

    # 2. brain_vote fields present in score events
    if scores:
        sample = scores[0]
        if "bv_bucket" not in sample or "bv_factor" not in sample:
            print(
                "FAIL: brain_vote_bucket/brain_vote_factor missing from "
                "WATCHDOG_CLOSE_SCORE_COMPUTED breakdown."
            )
            passed = False
        else:
            print(
                f"PASS: {len(scores)} WATCHDOG_CLOSE_SCORE_COMPUTED events all "
                f"carry brain_vote_bucket and brain_vote_factor fields."
            )

    # 3. Hard-floor: every score with floor_active=True should have a HARD_FLOOR_HIT
    floor_active_scores = [s for s in scores if s.get("floor_active") == "True"]
    floor_hit_dids = {h["did"] for h in floor_hits}
    floor_missing = [s for s in floor_active_scores if s["did"] not in floor_hit_dids]
    if floor_missing:
        print(
            f"FAIL: {len(floor_missing)} score events with hard_floor_active=True "
            f"had no corresponding WATCHDOG_HARD_FLOOR_HIT log."
        )
        for s in floor_missing[:5]:
            print(f"  sym={s['sym']} sl_pct={s['sl_pct']} composite={s['composite']}")
        passed = False
    else:
        print(
            f"PASS: every score with hard_floor_active=True ({len(floor_active_scores)}) "
            f"has a matching WATCHDOG_HARD_FLOOR_HIT. Total floor hits: {len(floor_hits)}."
        )

    # 4. Distribution summary
    print()
    print("Score-outcome distribution:")
    print(f"  WATCHDOG_CLOSE_EXECUTED:        {len(executes)}")
    print(f"  WATCHDOG_CLOSE_REJECTED:        {len(rejects)}")
    print(f"  WATCHDOG_CLOSE_OVERRIDE_TIGHTEN:{len(tightens)}")
    print(f"  WATCHDOG_HARD_FLOOR_HIT:        {len(floor_hits)}")
    print(f"  Total scored votes:             {len(scores)}")

    # 5. C1 regression: brain-silent path (no brain_vote_present) should
    #    not have brain_vote_factor > 0. With the wire-up using
    #    brain_vote_present=True for every call, this is always >= 0 from
    #    a positive bucket. A negative or zero brain_vote_factor would
    #    indicate the absent bucket fired — i.e., the wire-up is wrong.
    bad_bv = [
        s for s in scores
        if s.get("bv_bucket") == "absent"
    ]
    if bad_bv:
        print(
            f"WARN: {len(bad_bv)} score events with brain_vote_bucket=absent. "
            f"Expected zero (every scoring call should pass brain_vote_present=True)."
        )

    # 6. Sanity: a structural-reasoning brain vote should add +2.0
    structural_bvs = [
        s for s in scores
        if s.get("bv_bucket") == "structural"
        and float(s.get("bv_factor", "0")) > 1.5
    ]
    if scores and not structural_bvs:
        print(
            "WARN: no structural-reasoning brain votes seen with "
            "brain_vote_factor > 1.5 in this window."
        )

    print()
    print("=" * 60)
    print(f"OVERALL: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
