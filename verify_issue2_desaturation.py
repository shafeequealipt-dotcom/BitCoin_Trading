#!/usr/bin/env python3
"""Issue 2 verification — X-RAY setup_score de-saturation.

Proves, without touching live data:
  1. The real config loads the modifier scale (<1.0) and grade thresholds.
  2. The SAME spectrum of setups that ALL pile at A+ / score 100 under the legacy
     scale=1.0 now SPREADS across A+/A/B/C under the tuned scale — the saturation
     is gone and the coin filter can rank on real relative quality.
  3. Relative ordering is preserved exactly (a stronger setup never scores below a
     weaker one).
  4. The directional-RR de-grading is intact: a spent downtrend short (rr_short
     ~0.1) is still forced to SKIP regardless of the scale.

Read-only. Exit 0 = pass.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.analysis.structure.structure_engine import StructureEngine
from src.analysis.structure.models.structure_types import (
    MarketStructureResult, StructuralPlacement,
)


def _placement(rr_long: float, rr_short: float) -> StructuralPlacement:
    rr_best = max(rr_long, rr_short)
    return StructuralPlacement(
        structural_sl=90.0, structural_tp=110.0, rr_ratio=rr_best,
        rr_quality="excellent" if rr_best >= 3 else "good",
        rr_long=rr_long, rr_short=rr_short, rr_best=rr_best,
        is_fallback_rr=False,
    )


def _ms(structure: str, strength: str, bos: str | None) -> MarketStructureResult:
    return MarketStructureResult(
        structure=structure, strength=strength,
        last_bos=SimpleNamespace(direction=bos) if bos else None,
        last_choch=None,
    )


def _mtf(quality: str, score: int):
    return SimpleNamespace(quality=quality, score=score)


# A spectrum of LONG setups from strongest to weakest. Each is a dict of the
# inputs _compute_setup_score reads. Under the legacy scale=1.0 the top several
# all overflow and clamp to A+/100; the fix must spread them.
SETUPS = [
    ("strongest", dict(pos=0.08, struct="uptrend", strength="strong", bos="bullish",
                       rr_long=4.0, smc=95, mtf=_mtf("maximum", 9), vp="below_poc",
                       fib=True)),
    ("strong",    dict(pos=0.12, struct="uptrend", strength="strong", bos="bullish",
                       rr_long=3.2, smc=80, mtf=_mtf("good", 6), vp="below_poc",
                       fib=True)),
    ("good",      dict(pos=0.25, struct="uptrend", strength="medium", bos=None,
                       rr_long=2.4, smc=60, mtf=_mtf("good", 5), vp=None, fib=True)),
    ("moderate",  dict(pos=0.25, struct="ranging", strength="medium", bos=None,
                       rr_long=2.1, smc=45, mtf=_mtf("weak", 3), vp=None, fib=False)),
    ("modest",    dict(pos=0.40, struct="ranging", strength="weak", bos=None,
                       rr_long=2.0, smc=35, mtf=None, vp=None, fib=False)),
    ("weak",      dict(pos=0.50, struct="ranging", strength="weak", bos=None,
                       rr_long=1.8, smc=25, mtf=None, vp=None, fib=False)),
]


def grade_spectrum(engine: StructureEngine) -> list[tuple[str, int, str]]:
    out = []
    for name, s in SETUPS:
        vp = SimpleNamespace(current_vs_poc=s["vp"]) if s["vp"] else None
        fib = SimpleNamespace(confluence_with=True) if s["fib"] else None
        score, quality = engine._compute_setup_score(
            position_in_range=s["pos"],
            market_structure=_ms(s["struct"], s["strength"], s["bos"]),
            structural_placement=_placement(s["rr_long"], rr_short=0.5),
            suggested_direction="long",
            smc_confluence=s["smc"],
            volume_profile=vp, fibonacci=fib, mtf_confluence=s["mtf"],
            symbol=name,
        )
        out.append((name, score, quality))
    return out


def main() -> int:
    fails: list[str] = []
    s = Settings.load()
    print("== Config ==")
    print(f"  setup_score_modifier_scale = {s.structure.setup_score_modifier_scale}")
    print(f"  grades A+>={s.structure.setup_grade_a_plus_min} "
          f"A>={s.structure.setup_grade_a_min} B>={s.structure.setup_grade_b_min} "
          f"C>={s.structure.setup_grade_c_min}")
    if float(s.structure.setup_score_modifier_scale) >= 1.0:
        fails.append("modifier_scale must be <1.0 to de-saturate")

    # Tuned engine (live config) vs legacy engine (scale=1.0).
    tuned = StructureEngine(settings=s.structure)
    import dataclasses
    legacy_settings = dataclasses.replace(s.structure, setup_score_modifier_scale=1.0)
    legacy = StructureEngine(settings=legacy_settings)

    leg = grade_spectrum(legacy)
    tun = grade_spectrum(tuned)

    print("\n== Legacy scale=1.0 (saturated) vs tuned (spread) ==")
    print("  setup        legacy        tuned")
    for (n, ls, lq), (_, ts, tq) in zip(leg, tun):
        print(f"  {n:<11}  {ls:3d} {lq:<4}     {ts:3d} {tq}")

    leg_grades = {q for _, _, q in leg}
    tun_grades = {q for _, _, q in tun}
    leg_aplus = sum(1 for _, _, q in leg if q == "A+")
    tun_aplus = sum(1 for _, _, q in tun if q == "A+")
    print(f"\n  legacy distinct grades = {sorted(leg_grades)} (A+ count {leg_aplus})")
    print(f"  tuned  distinct grades = {sorted(tun_grades)} (A+ count {tun_aplus})")

    # The tuned spread must use more distinct grades than the saturated legacy,
    # and must reduce the A+ pile.
    if len(tun_grades) <= len(leg_grades):
        fails.append("tuned scale must spread into MORE distinct grades than legacy")
    if tun_aplus >= leg_aplus and leg_aplus > 1:
        fails.append("tuned scale must reduce the A+ pile")

    # Ordering preserved (monotonic non-increasing strongest->weakest).
    scores = [ts for _, ts, _ in tun]
    if scores != sorted(scores, reverse=True):
        fails.append(f"relative ordering must be preserved: {scores}")

    # Directional de-grading intact: spent downtrend short -> SKIP under tuned.
    score, quality = tuned._compute_setup_score(
        position_in_range=0.00, market_structure=_ms("downtrend", "strong", "bearish"),
        structural_placement=_placement(rr_long=18.0, rr_short=0.12),
        suggested_direction="short", smc_confluence=80,
        mtf_confluence=_mtf("maximum", 9), symbol="SPENT",
    )
    print(f"\n== Directional de-grading (spent short rr=0.12) -> {quality} score={score} ==")
    if quality != "SKIP":
        fails.append(f"spent short must stay SKIP under the scale, got {quality}")

    print("\n== RESULT ==")
    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: setup_score de-saturates (grades spread), ordering preserved, "
          "spent-short de-grading intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
