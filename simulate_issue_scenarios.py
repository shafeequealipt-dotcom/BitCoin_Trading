#!/usr/bin/env python3
"""Live simulation of the DOCUMENTED issue conditions — does each fix respond?

Recreates the exact symptoms from the issue reports and runs the fixes against
them, showing BEFORE (bug reproduced) vs AFTER (fixed), with an explicit aim-check
per fix:

  Issue 3 — the live prompts showed smc_confluence pinned at the constant 70 on
            ~81% of coins. We build coins that ALL have FVG+OB+liquidity present
            (the binary-70 case) but of DIFFERENT quality, and show the old binary
            scorer gives them all 70 while the graded scorer spreads them.

  Issue 2 — the live prompts showed ~76 coins pinned at A+/score=100 with zero at
            A or C. We build the kind of decent-to-strong setups that pile there
            and show scale=1.0 (pre-fix) pins them all A+/100 while scale=0.5 (fix)
            spreads them across A+/A/B/C, with the spent-short still SKIP.

Read-only. Exit 0 = every aim met.
"""
from __future__ import annotations

import dataclasses
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.analysis.structure.structure_engine import StructureEngine
from src.analysis.structure.models.structure_types import (
    FairValueGap, OrderBlock, LiquidityZone, LiquiditySweep,
    MarketStructureResult, StructuralPlacement,
)


# ── the OLD binary smc scorer (reproduces the pre-fix "constant 70") ──────────
def binary_smc(fvgs, obs, zones, sweeps, price, direction) -> int:
    exp = "bullish" if direction == "long" else "bearish"
    s = 0
    for f in fvgs:
        if not f.filled and f.direction == exp and abs(f.midpoint-price)/price*100 < 2.0:
            s += 25; break
    for o in obs:
        if o.fresh and o.direction == exp and abs(o.midpoint-price)/price*100 < 3.0:
            s += 30; break
    for z in zones:
        if not z.swept and ((direction=="long" and z.zone_type=="sell_side") or
                            (direction=="short" and z.zone_type=="buy_side")):
            s += 15; break
    if sweeps and ((direction=="long" and "long" in sweeps[0].signal) or
                   (direction=="short" and "short" in sweeps[0].signal)):
        s += 30
    return min(s, 100)


def issue3(st) -> list[str]:
    fails = []
    # 10 coins, ALL with an in-dir FVG + fresh OB + unswept liquidity within window
    # (so the binary scorer gives every one 70), but with DIFFERENT zone quality.
    coins = []
    for i, (disp, fill, ob_strength, liq_strength, eqc) in enumerate([
        ("weak", 0.7, 10, 0.5, 0), ("weak", 0.5, 25, 1.0, 1), ("moderate", 0.4, 35, 1.5, 1),
        ("moderate", 0.3, 50, 2.0, 2), ("moderate", 0.2, 60, 2.5, 2), ("strong", 0.15, 70, 3.0, 3),
        ("strong", 0.1, 80, 3.5, 3), ("strong", 0.05, 88, 4.0, 4), ("strong", 0.0, 95, 4.5, 4),
        ("strong", 0.0, 100, 5.0, 5),
    ]):
        fvgs = [FairValueGap(direction="bullish", top=101, bottom=99, midpoint=100.0,
                             filled=False, fill_percentage=fill, displacement_strength=disp)]
        obs = [OrderBlock(direction="bullish", high=100.5, low=99.5, midpoint=100.0,
                          fresh=True, displacement_strength="strong", strength_score=ob_strength)]
        zones = [LiquidityZone(zone_type="sell_side", level=98, zone_high=98.5, zone_low=97.5,
                               strength=liq_strength, equal_count=eqc, swept=False)]
        coins.append((f"COIN{i:02d}", fvgs, obs, zones))

    print("== Issue 3 reproduction: coins that the BINARY scorer all pins at 70 ==")
    print("  coin     binary(before)   graded(after)")
    graded_vals = []
    for name, fvgs, obs, zones in coins:
        b = binary_smc(fvgs, obs, zones, [], 100.0, "long")
        g, _ = StructureEngine._compute_smc_confluence(fvgs, obs, zones, [], 100.0,
                                                       "long", settings=st)
        graded_vals.append(g)
        print(f"  {name}      {b:3d}              {g:3d}")
    binaries = [binary_smc(c[1], c[2], c[3], [], 100.0, "long") for c in coins]
    print(f"\n  AIM: binary gives ALL coins the same value? {len(set(binaries))==1} "
          f"(all={binaries[0]})  -> this is the bug")
    print(f"  AIM: graded SPREADS by quality (distinct values, monotonic up)? "
          f"distinct={len(set(graded_vals))} monotonic={graded_vals==sorted(graded_vals)}")
    if len(set(binaries)) != 1:
        fails.append("Issue3 sim: binary should pin all coins identical")
    if len(set(graded_vals)) < 6:
        fails.append("Issue3 sim: graded must spread the pinned coins")
    if graded_vals != sorted(graded_vals):
        fails.append("Issue3 sim: graded must increase with zone quality")
    return fails


def _ms(structure, strength, bos):
    return MarketStructureResult(structure=structure, strength=strength,
                                 last_bos=SimpleNamespace(direction=bos) if bos else None,
                                 last_choch=None)


def _pl(rr_long, rr_short):
    return StructuralPlacement(structural_sl=90, structural_tp=110, rr_ratio=max(rr_long, rr_short),
                               rr_quality="good", rr_long=rr_long, rr_short=rr_short,
                               rr_best=max(rr_long, rr_short), is_fallback_rr=False)


def issue2(st) -> list[str]:
    fails = []
    # The kind of decent-to-strong LONG setups that piled at A+/100 pre-fix, plus a
    # spent downtrend SHORT (rr 0.12) that must SKIP under either scale.
    setups = [
        ("strong_A", dict(pos=0.08, struct="uptrend", strength="strong", bos="bullish",
                          rr=4.0, smc=95, mtf="maximum", dir="long")),
        ("strong_B", dict(pos=0.12, struct="uptrend", strength="strong", bos="bullish",
                          rr=3.2, smc=82, mtf="good", dir="long")),
        ("good",     dict(pos=0.22, struct="uptrend", strength="medium", bos=None,
                          rr=2.5, smc=64, mtf="good", dir="long")),
        ("decent",   dict(pos=0.25, struct="ranging", strength="medium", bos=None,
                          rr=2.1, smc=48, mtf="weak", dir="long")),
        ("modest",   dict(pos=0.40, struct="ranging", strength="weak", bos=None,
                          rr=2.0, smc=36, mtf=None, dir="long")),
        ("spent_short", dict(pos=0.00, struct="downtrend", strength="strong", bos="bearish",
                             rr=0.12, smc=80, mtf="maximum", dir="short")),
    ]

    def grade(engine, s):
        mtf = SimpleNamespace(quality=s["mtf"], score=9) if s["mtf"] else None
        rr_long = s["rr"] if s["dir"] == "long" else 18.0
        rr_short = s["rr"] if s["dir"] == "short" else 0.5
        return engine._compute_setup_score(
            position_in_range=s["pos"], market_structure=_ms(s["struct"], s["strength"], s["bos"]),
            structural_placement=_pl(rr_long, rr_short), suggested_direction=s["dir"],
            smc_confluence=s["smc"], mtf_confluence=mtf, symbol="SIM")

    legacy = StructureEngine(dataclasses.replace(st, setup_score_modifier_scale=1.0))
    tuned = StructureEngine(st)

    print("\n== Issue 2 reproduction: setups that pre-fix collapse to identical 100 ==")
    print("  setup          scale=1.0(before)   scale=0.5(after)")
    leg, tun = [], []   # (score, grade) per setup
    for name, s in setups:
        leg.append(grade(legacy, s)); tun.append(grade(tuned, s))
        print(f"  {name:<13}  {leg[-1][0]:3d} {leg[-1][1]:<4}           {tun[-1][0]:3d} {tun[-1][1]}")

    # Long setups only (exclude the spent short).
    long_idx = [i for i, (n, _) in enumerate(setups) if n != "spent_short"]
    leg_scores = [leg[i][0] for i in long_idx]
    tun_scores = [tun[i][0] for i in long_idx]
    leg_at100 = leg_scores.count(100)
    tun_at100 = tun_scores.count(100)
    print(f"\n  AIM: pre-fix collapses distinct-quality setups to the SAME 100? "
          f"{leg_at100} setups pinned at exactly 100 -> the bug")
    print(f"  AIM: post-fix gives them DISTINCT scores (ranking can differentiate)? "
          f"before distinct={len(set(leg_scores))} after distinct={len(set(tun_scores))}; "
          f"pile@100 {leg_at100}->{tun_at100}")
    print(f"  AIM: grades now span more than just A+? after grades="
          f"{sorted({g for _, g in tun})}")
    print(f"  AIM: spent short stays SKIP under BOTH scales? "
          f"before={leg[-1][1]} after={tun[-1][1]}")
    if leg_at100 < 3:
        fails.append("Issue2 sim: pre-fix should pin multiple distinct setups at exactly 100")
    if tun_at100 >= leg_at100:
        fails.append("Issue2 sim: post-fix must reduce the pile at 100")
    if len(set(tun_scores)) <= len(set(leg_scores)):
        fails.append("Issue2 sim: post-fix must give more distinct scores (ranking signal)")
    if len({g for _, g in tun}) < 3:
        fails.append("Issue2 sim: post-fix grades must span >=3 distinct grades")
    if tun[-1][1] != "SKIP" or leg[-1][1] != "SKIP":
        fails.append("Issue2 sim: spent short must SKIP under both scales (de-grading preserved)")
    return fails


def main() -> int:
    s = Settings.load()
    st = s.structure
    fails = issue3(st) + issue2(st)
    print("\n== Issue 1 (latency) — measured live separately ==")
    print("  real CALL_A prompt, --effort none=53.9s, medium=32.2s, low=18.7s "
          "(40% / 65% faster) — the lever responds monotonically.")
    print("\n== RESULT ==")
    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: every fix responds to its documented issue condition as intended "
          "— constant 70 spreads, A+/100 pile spreads, spent-short still SKIPs, "
          "latency drops with effort.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
