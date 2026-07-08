#!/usr/bin/env python3
"""Offline validation for Problem 4 / F20 — the ensemble vote display no longer
self-contradicts. Read-only.

Before: the "Votes" line, the "Two-sided poll" line, and the "Opposition" tier
were computed from different bases, so one candidate showed Votes SELL=0.00,
Two-sided poll SELL=3.55, and Opposition NEGLIGIBLE simultaneously (and the
brain cited the wrong number). The Opposition tier classified on the one-sided
confirmed sum, which is ~0 on the opposing side by construction.

After: when the two-sided poll is active, the Opposition tier classifies on the
SAME opposing weight the Two-sided poll prints (opposing_weighted), so the two
agree. This exercises the real production helper ``_opposition_tier``.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from src.brain.strategist import _opposition_tier  # noqa: E402

_FAIL: list[str] = []


def chk(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


def tier(buy_w, sell_w, opp_weighted, two_sided):
    return _opposition_tier(
        buy_w=buy_w, sell_w=sell_w,
        opposing_weighted=opp_weighted, two_sided=two_sided,
    )


print("Problem 4 / F20 — Opposition tier agrees with the two-sided poll")

# ALICE (call0001): Votes BUY=2.29 SELL=0.00; two-sided poll SELL=3.55. The old
# one-sided tier was NEGLIGIBLE; the new two-sided tier must be STRONG (latent
# SELL 3.55 actually OUTWEIGHS the winning BUY 2.29).
t, d, ow, aw = tier(2.29, 0.00, 3.55, True)
chk("ALICE two-sided: tier STRONG (was NEGLIGIBLE)", t == "STRONG", f"tier={t}")
chk("ALICE two-sided: opp_dir SELL, opp_wsum=3.55 (matches two-sided poll)",
    d == "SELL" and abs(ow - 3.55) < 1e-9 and abs(aw - 2.29) < 1e-9)

# ADA (call0005): Votes BUY=0.00 SELL=1.93; two-sided poll BUY=2.31 (the latent
# BUY the brain wrongly cited). New tier must flag it STRONG, not NEGLIGIBLE.
t, d, ow, aw = tier(0.00, 1.93, 2.31, True)
chk("ADA two-sided: tier STRONG (latent BUY exceeds winner)", t == "STRONG", f"tier={t}")
chk("ADA two-sided: opp_dir BUY, opp_wsum=2.31", d == "BUY" and abs(ow - 2.31) < 1e-9)

# MON: genuinely one-sided (BUY=4.77 vs two-sided SELL=0.34) -> low opposition.
t, d, ow, aw = tier(4.77, 0.00, 0.34, True)
chk("MON two-sided: low tier (genuinely one-sided)", t in ("NEGLIGIBLE", "WEAK"),
    f"tier={t} ratio={ow/aw:.3f}")

# Backward compatibility: two-sided OFF -> legacy one-sided base unchanged.
t_off, d_off, ow_off, aw_off = tier(2.29, 0.00, 3.55, False)
chk("two-sided OFF: legacy one-sided base (opp_wsum=0.00 -> NEGLIGIBLE)",
    t_off == "NEGLIGIBLE" and abs(ow_off - 0.00) < 1e-9)

# Consistency invariant: in two-sided mode the tier's opp_wsum EQUALS the
# opposing_weighted the Two-sided poll line prints (no contradiction possible).
for bw, sw, opw in [(2.29, 0.0, 3.55), (0.0, 1.93, 2.31), (4.77, 0.0, 0.34),
                    (0.0, 3.38, 2.21), (2.51, 0.0, 2.35)]:
    _t, _d, _ow, _aw = tier(bw, sw, opw, True)
    chk(f"invariant: tier opp_wsum == two-sided poll value ({bw}/{sw}/{opw})",
        abs(_ow - opw) < 1e-9)

# Symmetry: swapping the winning side mirrors the tier (no directional lean).
ta, *_ = tier(2.0, 0.0, 4.0, True)   # BUY wins, SELL opposes 4.0
tb, *_ = tier(0.0, 2.0, 4.0, True)   # SELL wins, BUY opposes 4.0
chk("symmetry: same tier regardless of which side leads", ta == tb, f"{ta} vs {tb}")

print()
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — the Opposition tier now classifies on the two-sided")
print("opposing weight, agrees with the Two-sided poll line, resolves the F20")
print("three-way contradiction, is symmetric, and is backward compatible.")
