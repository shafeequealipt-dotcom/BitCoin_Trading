"""Self-verification for E19 — rank the X-RAY shortlist by score x confidence.

The X-RAY shortlist (top N sent to the brain) was selected AND ordered by
setup_score alone, so a high-score zero-confidence (structureless) coin could
top the brain's X-RAY block. E19 keeps the top-N membership selected by score
(operator decision: membership preserved) but ORDERS the returned N by
setup_score x setup_type_confidence, so a zero-confidence coin sorts to the
bottom and cannot lead.

Confirms (against the REAL StructureCache.get_top_setups):
  A. STATIC: the method orders by conviction (score x confidence), selects
     membership by score, and emits the E19_XRAY_RERANK sentinel on lead change.
  B. BEHAVIORAL: a structureless high-score coin (score 90, conf 0.0) no longer
     leads — a confident coin (score 80, conf 0.9) does; the structureless coin
     sorts last; and the top-N membership is preserved (selected by score).

Read-only / in-memory.
"""

from types import SimpleNamespace


def static_check():
    s = open("src/analysis/structure/structure_cache.py").read()
    return {
        "orders by conviction (score x confidence)":
            "setup_type_confidence" in s and "_conviction" in s
            and "float(a.setup_score) * max(" in s,
        "membership selected by setup_score (top_n)":
            "top_n = sorted(" in s and "key=lambda a: a.setup_score," in s,
        "E19_XRAY_RERANK sentinel present": "E19_XRAY_RERANK" in s,
    }


def behavioral_check():
    from src.analysis.structure.structure_cache import StructureCache

    cache = StructureCache(ttl_seconds=3600)
    # Duck-typed analyses (get_top_setups reads symbol/setup_score/setup_type_confidence).
    cache.set("STRUCT", SimpleNamespace(symbol="STRUCT", setup_score=90, setup_type_confidence=0.0))  # structureless high score
    cache.set("CONF", SimpleNamespace(symbol="CONF", setup_score=80, setup_type_confidence=0.9))    # confident
    cache.set("MID", SimpleNamespace(symbol="MID", setup_score=70, setup_type_confidence=0.5))      # middling
    cache.set("LOW", SimpleNamespace(symbol="LOW", setup_score=10, setup_type_confidence=0.9))      # low score, excluded at n=3

    # n=8 (>=4): all four returned (membership preserved), ordered by conviction.
    top_all = cache.get_top_setups(n=8)
    syms_all = [a.symbol for a in top_all]
    lead_is_confident = syms_all[0] == "CONF"          # was STRUCT under score-only
    structureless_last_or_low = syms_all.index("STRUCT") > syms_all.index("CONF")
    membership_all = set(syms_all) == {"STRUCT", "CONF", "MID", "LOW"}

    # n=3: membership = top-3 BY SCORE (STRUCT 90, CONF 80, MID 70); LOW excluded.
    top3 = cache.get_top_setups(n=3)
    syms3 = [a.symbol for a in top3]
    membership_by_score = set(syms3) == {"STRUCT", "CONF", "MID"}   # LOW (score 10) not selected
    order_by_conviction = syms3[0] == "CONF" and syms3[-1] == "STRUCT"  # conf leads, structureless last

    return {
        "n>=4: confident coin leads (was structureless)": lead_is_confident,
        "structureless coin sorts below the confident one": structureless_last_or_low,
        "n>=4: full membership preserved": membership_all,
        "n=3: membership = top-3 by score (LOW excluded)": membership_by_score,
        "n=3: order = conviction (CONF leads, STRUCT last)": order_by_conviction,
    }, syms_all, syms3


def main():
    s = static_check()
    b, syms_all, syms3 = behavioral_check()
    print("E19 VERIFICATION — rank X-RAY shortlist by score x confidence (completes #7/E17/E18)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  BEHAVIORAL (real StructureCache.get_top_setups):")
    print(f"    n=8 order: {syms_all}")
    print(f"    n=3 order: {syms3}")
    for k, v in b.items():
        print(f"    {k}: {v}")
    ok = all(s.values()) and all(b.values())
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
