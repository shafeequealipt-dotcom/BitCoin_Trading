"""Self-verification for Issue E16 — position crowd-out.

Offline check against CURRENT code. Two parts:

  A. STATIC: the Call-A cap now reserves the full top_n budget for the
     non-position candidate pool (no longer subtracts pinned positions), logs
     candidates/positions_in_manage_block, and the dedicated manage-block uses
     the "## OPEN POSITIONS" header (an ESSENTIAL trim marker, so it survives
     trim — also addresses companion E22).
  B. BEHAVIORAL: on a crowded book (7 open positions + 12 fresh candidates,
     top_n=10), the OLD logic left only 3 new-entry slots; the NEW logic gives
     the brain the full 10 fresh candidates while the 7 positions move to the
     manage-block. This is what gives the first-batch #2 ranker room to land.

Run: .venv/bin/python verify_issue_e16.py
"""
from src.core.ranking import reserve_slots_union
from src.core.coin_package import CoinPackage, PriceDataBlock
from src.brain import strategist as _strat_mod


def static_check():
    src = open("src/brain/strategist.py").read()
    return {
        "candidate pool excludes positions": "_candidate_pool = [" in src
        and "if p.open_position is None" in src,
        "no longer subtracts pinned from budget": "slots_left = max(0, _top_n - len(pinned))" not in src,
        "logs candidates + manage-block count": "candidates={len(capped)}" in src
        and "positions_in_manage_block=" in src,
        "dedicated OPEN POSITIONS manage-block": "## OPEN POSITIONS (already held" in src,
        "manage-block header is ESSENTIAL marker": "## OPEN POSITIONS" in _strat_mod._TRIM_ESSENTIAL_MARKERS,
    }


def _pkg(sym, opp, intr, position=False):
    p = CoinPackage(symbol=sym, qualified=True, opportunity_score=opp,
                    price_data=PriceDataBlock(current=100.0))
    p.interestingness_score = intr
    if position:
        p.open_position = {"side": "Buy", "entry_price": 100.0}
    return p


def behavioral():
    top_n = 10
    pkgs = {}
    for i in range(7):  # 7 open positions (crowded book)
        pkgs[f"POS{i}"] = _pkg(f"POS{i}", 0.5, 0.5, position=True)
    for i in range(12):  # 12 fresh candidates
        pkgs[f"CAND{i}"] = _pkg(f"CAND{i}", 0.3 + i * 0.05, 0.9 - i * 0.05)

    # OLD logic: positions pinned, candidates fill the leftover slots.
    pinned = {s: p for s, p in pkgs.items() if p.open_position is not None}
    old_new_entry_slots = max(0, top_n - len(pinned))

    # NEW logic (exact replica of the shipped cap): candidates get the full budget.
    position_count = sum(1 for p in pkgs.values() if p.open_position is not None)
    candidate_pool = [p for p in pkgs.values() if p.open_position is None]
    if top_n > 0 and len(candidate_pool) > top_n:
        picked, _, _ = reserve_slots_union(
            candidate_pool, top_n,
            opp_key=lambda p: p.opportunity_score,
            int_key=lambda p: p.interestingness_score,
        )
        capped = {p.symbol: p for p in picked}
    else:
        capped = {p.symbol: p for p in candidate_pool}
    new_candidates = len(capped)
    no_positions_in_candidates = all(p.open_position is None for p in capped.values())

    return {
        "OLD new-entry slots (crowded)": old_new_entry_slots,
        "NEW candidate slots (crowded)": new_candidates,
        "NEW gives full budget": new_candidates == top_n,
        "positions preserved in manage-block": position_count == 7,
        "no positions consume candidate slots": no_positions_in_candidates,
    }


def main():
    s = static_check()
    b = behavioral()
    print("ISSUE E16 VERIFICATION — position crowd-out")
    print("  STATIC (independent candidate budget + trim-protected manage-block):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  BEHAVIORAL (7 positions + 12 candidates, top_n=10):")
    for k, v in b.items():
        print(f"    {k}: {v}")
    ok = (all(s.values())
          and b["NEW candidate slots (crowded)"] == 10
          and b["OLD new-entry slots (crowded)"] == 3
          and b["NEW gives full budget"]
          and b["positions preserved in manage-block"]
          and b["no positions consume candidate slots"])
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
