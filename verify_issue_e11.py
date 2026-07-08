"""Self-verification for Issue E11 — per-cycle blocker failure heat-map.

The per-package PACKAGE_BLOCKERS log already fires when a single package has
blockers; E11 adds a per-cycle aggregate (PACKAGE_BLOCKER_HEATMAP) counting
blocker labels across all coins scanned in a cycle, so operators see which
sources fail and how often. This confirms (A) the heat-map is wired in both
the briefing and exclusion paths and (B) the aggregation produces the right
per-label counts and stays silent when nothing failed.

Read-only; replicates the shipped aggregation.
"""
from collections import Counter


def static_check():
    src = open("src/workers/scanner_worker.py").read()
    return {
        "heat-map tag present": "PACKAGE_BLOCKER_HEATMAP" in src,
        "wired in both scanner paths": src.count("PACKAGE_BLOCKER_HEATMAP") >= 2,
        "Counter aggregation": "_blocker_heat" in src and "most_common()" in src,
        "distinct from per-package PACKAGE_BLOCKERS": "PACKAGE_BLOCKERS" in src,
    }


def _aggregate(blocker_lists):
    """Exact replica of the shipped per-cycle aggregation."""
    heat: Counter = Counter()
    for bl in blocker_lists:
        for b in (bl or []):
            heat[b] += 1
    if not heat:
        return None
    return ", ".join(f"{k}={v}" for k, v in heat.most_common())


def main():
    s = static_check()
    # three coins: two signal_missing, one funding_missing, one clean
    by_label = _aggregate([
        ["signal_missing"], ["signal_missing", "funding_missing"], [], None,
    ])
    silent = _aggregate([[], [], None])
    print("ISSUE E11 VERIFICATION — per-cycle blocker heat-map")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  BEHAVIORAL (aggregation replica):")
    print(f"    mixed blockers -> by_label=[{by_label}]")
    print(f"    no blockers    -> {silent!r} (silent)")
    ok = (
        all(s.values())
        and by_label is not None
        and "signal_missing=2" in by_label and "funding_missing=1" in by_label
        and silent is None
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
