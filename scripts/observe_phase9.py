"""Daily observation harness — Layer 1 restructure Phase 9.

Reads ``cycle_metrics`` (populated hourly by Phase 1's CycleTracker) and
prints a one-screen summary against the verification-checklist
thresholds. Run on a cron — daily output goes to operator inbox.

Thresholds (per blueprint Section 13 + plan Phase 9):
    Layer 1A p95 < 5 s
    Layer 1B p95 < 15 s
    Layer 1C p95 < 10 s
    Layer 1D p95 < 500 ms
    Total cycle p95 < 30 s
    Avg qualified ∈ [5, 25]
    Avg packages ∈ [10, 15]

Usage::

    python3 scripts/observe_phase9.py [hours]

``hours`` defaults to 24. The script aggregates the last N hours of
cycle_metrics into a single summary line per metric.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading.db"


def _fmt_pass(value: float, threshold: float, *, less_is_better: bool = True) -> str:
    if less_is_better:
        return "PASS" if value < threshold else "FAIL"
    return "PASS" if value > threshold else "FAIL"


def main(hours: int = 24) -> int:
    """Pull cycle_metrics summary for the last ``hours``. Print + exit."""
    if not DB_PATH.exists():
        print(f"[observe] DB missing: {DB_PATH}", file=sys.stderr)
        return 1

    cutoff_ts = int(datetime.now(timezone.utc).timestamp()) - hours * 3600

    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*), "
                "AVG(layer1a_p95_ms), AVG(layer1b_p95_ms), AVG(layer1c_p95_ms), "
                "AVG(layer1d_p95_ms), AVG(total_p95_ms), "
                "AVG(qualified_pct_avg), AVG(packages_count_avg) "
                "FROM cycle_metrics WHERE hour_ts >= ?",
                (cutoff_ts,),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError as e:
            print(f"[observe] cycle_metrics not yet populated: {e}", file=sys.stderr)
            return 1

    rows, l1a, l1b, l1c, l1d, total, qualified, packages = row
    if rows == 0:
        print(f"[observe] No cycle_metrics rows in last {hours}h.")
        return 0

    # Convert to seconds for human-friendly thresholds.
    l1a_s = (l1a or 0) / 1000.0
    l1b_s = (l1b or 0) / 1000.0
    l1c_s = (l1c or 0) / 1000.0
    l1d_ms = l1d or 0
    total_s = (total or 0) / 1000.0

    print(f"=== Phase 9 Observation Summary (last {hours}h, {rows} hourly buckets) ===")
    print(f"Layer 1A p95 avg: {l1a_s:5.2f}s  (target <5s)   [{_fmt_pass(l1a_s, 5)}]")
    print(f"Layer 1B p95 avg: {l1b_s:5.2f}s  (target <15s)  [{_fmt_pass(l1b_s, 15)}]")
    print(f"Layer 1C p95 avg: {l1c_s:5.2f}s  (target <10s)  [{_fmt_pass(l1c_s, 10)}]")
    print(f"Layer 1D p95 avg: {l1d_ms:5.0f}ms (target <500ms) [{_fmt_pass(l1d_ms, 500)}]")
    print(f"Total p95 avg:    {total_s:5.2f}s  (target <30s)  [{_fmt_pass(total_s, 30)}]")
    qualified = qualified or 0
    print(f"Avg qualified:    {qualified:5.1f}    (target 5-25)  "
          f"[{ 'PASS' if 5 <= qualified <= 25 else 'FAIL' }]")
    packages = packages or 0
    print(f"Avg packages:     {packages:5.1f}    (target 10-15) "
          f"[{ 'PASS' if 10 <= packages <= 15 else 'FAIL' }]")
    return 0


if __name__ == "__main__":
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    raise SystemExit(main(h))
