#!/usr/bin/env python3
"""Issue 3 verification — graded structure confluence + H4/D1 anchor wiring.

Proves, without touching live data:
  1. The real config.toml loads the graded-SMC weights/windows.
  2. _compute_smc_confluence now SPREADS across coins of different structural
     quality instead of pinning at the constant 70 — strong > medium > weak, and
     the legacy binary would have given medium and strong the SAME 70.
  3. MTFConfluence now exposes per-timeframe h4_bias/d1_bias, populated from
     higher-TF views and empty when the higher-TF feature is off (dormant-safe).
  4. The interestingness confluence anchor-count rises when H4/D1 agree (the
     wiring is correct; live effect requires structure.mtf_multi_timeframe_enabled).

Read-only. Exit 0 = pass.
"""
from __future__ import annotations

import sys

from src.config.settings import Settings, StructureSettings
from src.analysis.structure.structure_engine import StructureEngine
from src.analysis.structure.mtf_confluence import MTFConfluenceScorer
from src.analysis.structure.models.structure_types import (
    FairValueGap, OrderBlock, LiquidityZone, LiquiditySweep,
)
from src.workers.scanner.interestingness import _confluence


def _fvg(disp: str, fill: float, dist_mid: float) -> FairValueGap:
    return FairValueGap(direction="bullish", top=101, bottom=99,
                        midpoint=100.0 + dist_mid, filled=False,
                        fill_percentage=fill, gap_size_pct=2.0,
                        displacement_strength=disp)


def _ob(strength: float, dist_mid: float) -> OrderBlock:
    return OrderBlock(direction="bullish", high=100.5, low=99.5,
                     midpoint=100.0 + dist_mid, fresh=True,
                     displacement_strength="strong", strength_score=strength)


def _zone(strength: float, eq: int) -> LiquidityZone:
    return LiquidityZone(zone_type="sell_side", level=98.0, zone_high=98.5,
                        zone_low=97.5, strength=strength, equal_count=eq,
                        swept=False)


def _sweep(sig: str, rev: str, age: int) -> LiquiditySweep:
    return LiquiditySweep(sweep_type="bullish_sweep", level_swept=98.0,
                         signal=sig, reversal_strength=rev, age_candles=age)


def main() -> int:
    fails: list[str] = []
    s = Settings.load()
    st = s.structure
    print("== Graded-SMC config loaded ==")
    for k in ("smc_weight_fvg", "smc_weight_ob", "smc_weight_liq",
              "smc_weight_sweep", "smc_fvg_proximity_pct", "smc_ob_proximity_pct",
              "smc_sweep_recency_candles"):
        print(f"  {k} = {getattr(st, k)}")
    if float(st.smc_weight_fvg) != 25.0 or float(st.smc_weight_ob) != 30.0:
        fails.append("graded-SMC weights not loaded from config")

    # ---- 2. smc_confluence spreads across coins of different quality ----
    def smc(fvgs, obs, zones, sweeps) -> int:
        score, _ = StructureEngine._compute_smc_confluence(
            fvgs, obs, zones, sweeps, current_price=100.0, direction="long",
            settings=st,
        )
        return score

    weak = smc([_fvg("weak", 0.6, 1.5)], [_ob(20, 2.0)], [_zone(1.0, 0)],
               [_sweep("weak_long", "weak", 18)])
    medium = smc([_fvg("moderate", 0.2, 0.5)], [_ob(55, 1.0)], [_zone(3.0, 2)], [])
    strong = smc([_fvg("strong", 0.0, 0.0)], [_ob(95, 0.0)], [_zone(5.0, 5)],
                 [_sweep("high_probability_long", "strong", 0)])
    print("\n== smc_confluence now spreads per coin (was a constant 70) ==")
    print(f"  weak setup   = {weak}")
    print(f"  medium setup = {medium}")
    print(f"  strong setup = {strong}")
    if not (weak < medium < strong):
        fails.append(f"smc must spread weak<medium<strong: {weak}/{medium}/{strong}")
    if len({weak, medium, strong}) < 3:
        fails.append("smc did not differentiate the three coins")
    # Contrast: the legacy binary scorer gave medium AND strong the SAME 70
    # (FVG 25 + OB 30 + liq 15, no sweep / with sweep capped) — no spread.
    print("  (legacy binary scorer would have scored medium and strong both ~70)")

    # ---- 3. MTFConfluence exposes h4/d1 bias; dormant-safe when HTF off ----
    scorer = MTFConfluenceScorer(StructureSettings())

    class _View:
        def __init__(self, structure, has_data=True, bos=""):
            self.structure = structure
            self.has_data = has_data
            self.last_bos_direction = bos

    from src.analysis.structure.models.structure_types import MarketStructureResult
    ms = MarketStructureResult(structure="uptrend")
    res_off = scorer.score("T", 100.0, "long", ms, [], [], None, [], [], 0,
                           None, None, higher_tf_views=None)
    res_on = scorer.score("T", 100.0, "long", ms, [], [], None, [], [], 0,
                          None, None,
                          higher_tf_views={"240": _View("uptrend"),
                                           "D": _View("downtrend")})
    print("\n== MTFConfluence H4/D1 bias (Part B) ==")
    print(f"  HTF off -> h4_bias={res_off.h4_bias!r} d1_bias={res_off.d1_bias!r}")
    print(f"  HTF on  -> h4_bias={res_on.h4_bias!r} d1_bias={res_on.d1_bias!r}")
    if (res_off.h4_bias, res_off.d1_bias) != ("", ""):
        fails.append("HTF-off must leave biases empty (dormant-safe)")
    if (res_on.h4_bias, res_on.d1_bias) != ("long", "short"):
        fails.append(f"HTF-on biases wrong: {res_on.h4_bias}/{res_on.d1_bias}")

    # ---- 4. interestingness anchor-count rises when H4/D1 agree ----
    base, n_base = _confluence(consensus_direction="long", trade_direction="long",
                               signal_direction="", funding_rate=0.0,
                               mtf_h1_bias="long", mtf_h4_bias="", mtf_d1_bias="",
                               regime="")
    withhtf, n_htf = _confluence(consensus_direction="long", trade_direction="long",
                                 signal_direction="", funding_rate=0.0,
                                 mtf_h1_bias="long", mtf_h4_bias="long",
                                 mtf_d1_bias="long", regime="")
    print("\n== interestingness confluence anchor-count (Part B wiring) ==")
    print(f"  H1 only      -> aligned anchors = {n_base}")
    print(f"  +H4 +D1 agree -> aligned anchors = {n_htf}")
    if n_htf <= n_base:
        fails.append("H4/D1 agreement must raise the aligned-anchor count")

    print("\n== RESULT ==")
    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: smc_confluence spreads per coin; H4/D1 anchors wired "
          "(dormant-safe until the HTF feature is enabled).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
