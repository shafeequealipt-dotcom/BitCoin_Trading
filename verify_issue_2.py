"""Self-verification for Issue #2 — interestingness sole ranker -> reserve slots.

Offline check against CURRENT code. Three parts:

  A. STATIC: both ranking sites (scanner cut + brain cap) now call
     reserve_slots_union and emit their split sentinels.
  B. UNION PROPERTY: a high-opportunity / low-interestingness coin that the OLD
     interestingness-first selection dropped is now RETAINED, and the candidate
     set size is preserved (the fix re-ranks, it does not shrink).
  C. NO-SHRINK: reserve_slots_union always fills min(n, len(items)).

Run: .venv/bin/python verify_issue_2.py
"""
from src.core.ranking import reserve_slots_union


def static_check():
    sc = open("src/workers/scanner_worker.py").read()
    st = open("src/brain/strategist.py").read()
    return {
        "scanner uses reserve_slots_union": "reserve_slots_union(" in sc,
        "scanner emits SCANNER_RESERVE_SLOTS": "SCANNER_RESERVE_SLOTS" in sc,
        "brain cap uses reserve_slots_union": "reserve_slots_union(" in st,
        "brain cap logs opp/int split": "from_opportunity=" in st and "from_interestingness=" in st,
    }


def union_property():
    # 15 candidates; cap to 10. Tuple = (sym, opportunity, interestingness).
    # HIGHOPP has the top opportunity but the lowest interestingness — exactly
    # the coin the old interestingness-first ranking silently dropped.
    cands = [(f"C{i}", 0.30 + i * 0.03, 0.90 - i * 0.03) for i in range(14)]
    cands.append(("HIGHOPP", 0.99, 0.01))
    k = 10

    # OLD behavior: sort by (interestingness, opportunity) DESC, take top k.
    old_sel = [r[0] for r in sorted(cands, key=lambda r: (r[2], r[1]), reverse=True)[:k]]
    # NEW behavior: reserve slots.
    picked, fo, fi = reserve_slots_union(
        cands, k, opp_key=lambda r: r[1], int_key=lambda r: r[2]
    )
    new_sel = [r[0] for r in picked]

    return {
        "OLD dropped HIGHOPP": "HIGHOPP" not in old_sel,
        "NEW retains HIGHOPP": "HIGHOPP" in new_sel,
        "set size preserved (==k)": len(new_sel) == k and len(old_sel) == k,
        "slots split across both scores": fo > 0 and fi > 0,
    }, old_sel, new_sel, fo, fi


def no_shrink():
    items = [(f"X{i}", i * 0.1, (20 - i) * 0.1) for i in range(20)]
    out = {}
    for n in (0, 1, 5, 20, 30):
        sel, _, _ = reserve_slots_union(items, n, opp_key=lambda r: r[1], int_key=lambda r: r[2])
        out[f"n={n}"] = len(sel) == min(max(n, 0), len(items))
    return out


def main():
    s = static_check()
    u, old_sel, new_sel, fo, fi = union_property()
    ns = no_shrink()

    print("ISSUE #2 VERIFICATION — reserve-slots ranker")
    print("  STATIC (both ranking sites):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  UNION PROPERTY (high-opportunity coin no longer dropped):")
    for k, v in u.items():
        print(f"    {k}: {v}")
    print(f"    OLD top-10 (by interestingness): {old_sel}")
    print(f"    NEW top-10 (reserve slots)     : {new_sel}  [from_opp={fo} from_int={fi}]")
    print("  NO-SHRINK (fills min(n, len)):")
    for k, v in ns.items():
        print(f"    {k}: {v}")

    ok = all(s.values()) and all(u.values()) and all(ns.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
