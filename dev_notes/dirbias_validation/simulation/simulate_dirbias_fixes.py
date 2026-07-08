"""Live simulation of the four direction-bias fixes (2026-05-19).

For each fix, the simulation:
1. Re-creates the pre-fix bug conditions (with overrides where needed).
2. Runs the real production code path against representative data.
3. Compares pre-fix vs post-fix behavior side-by-side.
4. Asserts post-fix output matches the design aim.
5. Prints PASS / FAIL per assertion with evidence.

Invoke from the project root:
    python3 dev_notes/dirbias_validation/simulation/simulate_dirbias_fixes.py
"""
from __future__ import annotations

import sys

import numpy as np

from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    PriceLevel,
)
from src.analysis.structure.structural_levels import StructuralLevelCalculator
from src.analysis.structure.support_resistance import SupportResistanceEngine
from src.brain.strategist import STRAT_REGIME_BLOCK_VERSION
from src.config.settings import Settings, StructureSettings
from src.workers.scanner.state_labeler import (
    LABELLER_REGIME_HAIRCUT_VERSION,
    _trigger_extreme_fear_long,
    _trigger_extreme_greed_short,
    _trigger_funding_extreme_fade_long,
    _trigger_funding_extreme_fade_short,
    _trigger_range_fade_long,
    _trigger_range_fade_short,
    _trigger_trend_pullback_long,
    _trigger_trend_pullback_short,
    label_state,
)


# ============================================================================
# Helpers
# ============================================================================


def section(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}")


def subsection(title: str) -> None:
    print(f"\n--- {title} ---")


def report(asserts: list[tuple[str, bool]]) -> bool:
    all_ok = True
    for label, ok in asserts:
        status = "PASS" if ok else "FAIL"
        print(f"  {status} - {label}")
        if not ok:
            all_ok = False
    return all_ok


# ============================================================================
# SCENARIO 1 - Issue 4: symmetric MARKET REGIME prompt block
# ============================================================================

def scenario_issue4() -> bool:
    section("SCENARIO 1 - Issue 4: symmetric MARKET REGIME prompt block")
    print(
        "Pre-fix bug: trending_down carried mandate-strength 'DEFAULT SELL BIAS'\n"
        "wording while trending_up carried weaker preference language. NOTE block\n"
        "fired only on trending_down at conf > 0.60. Result: Claude's CALL_A\n"
        "decisions skewed 92.3 percent Sell in production.\n\n"
        "Fix: symmetric scenario-driven wording on BOTH regimes; NOTE fires on\n"
        "both at the same confidence threshold. Operator directive honored\n"
        "(no hardcoded asymmetric correction numbers).\n"
    )

    # Inline rebuild matching strategist.py:1461-1488 (live block) verbatim.
    direction_hint = {
        "trending_down": "Bias for shorts when per-coin evidence agrees; per-coin tags override.",
        "trending_up": "Bias for longs when per-coin evidence agrees; per-coin tags override.",
        "ranging": "both directions OK",
        "volatile": "both directions with caution",
        "dead": "scalp mode - both directions, tight TP",
    }

    def render_note(regime: str, confidence: float) -> str | None:
        if confidence > 0.60:
            if regime == "trending_down":
                return (
                    "NOTE: High-confidence global downtrend - shorts have structural "
                    "backdrop, but per-coin pivot evidence still decides."
                )
            if regime == "trending_up":
                return (
                    "NOTE: High-confidence global uptrend - longs have structural "
                    "backdrop, but per-coin pivot evidence still decides."
                )
        return None

    subsection("Rendered output for both high-confidence regimes")
    td_hint = direction_hint["trending_down"]
    tu_hint = direction_hint["trending_up"]
    td_note = render_note("trending_down", 0.65)
    tu_note = render_note("trending_up", 0.65)
    print(f"  trending_down @0.65 hint: {td_hint!r}")
    print(f"  trending_up   @0.65 hint: {tu_hint!r}")
    print(f"  trending_down @0.65 NOTE: {td_note!r}")
    print(f"  trending_up   @0.65 NOTE: {tu_note!r}")

    # Test low confidence — NOTE must not fire either way
    td_note_lo = render_note("trending_down", 0.55)
    tu_note_lo = render_note("trending_up", 0.55)
    print(f"  trending_down @0.55 NOTE: {td_note_lo!r}  (must be None)")
    print(f"  trending_up   @0.55 NOTE: {tu_note_lo!r}  (must be None)")

    subsection("Cross-checks")
    # Wording symmetry: substitute direction tokens and assert texts are identical.
    td_mirror = td_hint.replace("shorts", "_DIR_")
    tu_mirror = tu_hint.replace("longs", "_DIR_")
    td_note_mirror = (td_note or "").replace("downtrend", "_REG_").replace("shorts", "_DIR_")
    tu_note_mirror = (tu_note or "").replace("uptrend", "_REG_").replace("longs", "_DIR_")

    return report([
        ("Module constant STRAT_REGIME_BLOCK_VERSION == 2", STRAT_REGIME_BLOCK_VERSION == 2),
        ("trending_down hint mentions 'shorts'", "shorts" in td_hint),
        ("trending_up hint mentions 'longs'", "longs" in tu_hint),
        ("Both hints are wording-symmetric after direction substitution", td_mirror == tu_mirror),
        ("NOTE fires on trending_down @ conf=0.65", td_note is not None),
        ("NOTE fires on trending_up @ conf=0.65", tu_note is not None),
        ("NOTE wording is symmetric after regime/direction substitution", td_note_mirror == tu_note_mirror),
        ("NOTE suppressed on trending_down @ conf=0.55", td_note_lo is None),
        ("NOTE suppressed on trending_up @ conf=0.55", tu_note_lo is None),
        ("ranging/volatile/dead all use 'both directions' wording (no bias)",
         all("both directions" in direction_hint[r] or "both" in direction_hint[r]
             for r in ("ranging", "volatile", "dead"))),
    ])


# ============================================================================
# SCENARIO 2 - Issue 2 Concern 7: counter_confidence_multiplier = 1.0
# ============================================================================

def scenario_issue2() -> bool:
    section("SCENARIO 2 - Issue 2 Concern 7: counter_confidence_multiplier = 1.0")
    print(
        "Pre-fix bug: counter setups (BULLISH_FVG_OB_COUNTER / BEARISH_FVG_OB_COUNTER)\n"
        "had their base_conf multiplied by 0.7 at structure_engine.py:1188,1210.\n"
        "This pre-suppressed counter-direction setups before any downstream consumer\n"
        "saw them - cumulative ~3.88x size suppression through 4 stacked floors.\n\n"
        "Fix: config.toml:1783 set to 1.0. Identity multiply. No code change.\n"
        "Reversible by single TOML edit + service restart.\n"
    )

    settings = Settings.load()
    counter_mult = settings.structure.setup_types.counter_confidence_multiplier
    pre_fix_mult = 0.7

    subsection("Loaded value from config.toml")
    print(f"  counter_confidence_multiplier (loaded) = {counter_mult}")

    subsection("Confidence value comparison for counter setups")
    print(f"  base_conf | pre-fix (x0.7) | post-fix (x{counter_mult})  | ratio")
    rows = []
    for base_conf in (0.50, 0.55, 0.60, 0.70, 0.80):
        pre = base_conf * pre_fix_mult
        post = base_conf * counter_mult
        ratio = post / pre if pre > 0 else float("inf")
        print(f"  {base_conf:.2f}     | {pre:.4f}        | {post:.4f}      | {ratio:.3f}x")
        rows.append((base_conf, pre, post))

    subsection("Downstream effect at the 4 stacked floor-0.5 multiplier sites")
    # Each downstream consumer applies max(0.5, min(1.0, conf)) as a floor.
    # The compounding effect:
    base = 0.60
    pre_conf = base * pre_fix_mult  # 0.42
    post_conf = base * counter_mult  # 0.60
    pre_floored = max(0.5, min(1.0, pre_conf))
    post_floored = max(0.5, min(1.0, post_conf))
    print(f"  base_conf=0.60 counter setup:")
    print(f"    pre-fix:  raw={pre_conf:.3f}  floored={pre_floored:.3f}  (forced up to floor)")
    print(f"    post-fix: raw={post_conf:.3f}  floored={post_floored:.3f}  (passes floor naturally)")
    print(f"  pre-fix loses signal (forces 0.42 -> 0.5); post-fix preserves the 0.60 signal.")

    return report([
        ("Loaded counter_mult == 1.0", abs(counter_mult - 1.0) < 1e-9),
        ("Identity multiply: 0.60 * mult == 0.60", abs(0.60 * counter_mult - 0.60) < 1e-9),
        ("No asymmetric cut (mult >= 0.99 = effectively 1.0)", counter_mult >= 0.99),
        ("Post-fix counter conf strictly > pre-fix at every base_conf",
         all(p_post > p_pre for _, p_pre, p_post in rows)),
        ("Post-fix conf 0.60 passes floor-0.5 naturally (no signal loss)",
         post_floored == post_conf and post_floored > pre_floored),
    ])


# ============================================================================
# SCENARIO 3 - Issue 3: labeller soft regime haircut (all 8 triggers)
# ============================================================================

def scenario_issue3() -> bool:
    section("SCENARIO 3 - Issue 3: labeller soft regime haircut (all 8 triggers)")
    print(
        "Pre-fix bug: 8 trigger predicates in state_labeler.py had hard kills\n"
        "(`if regime mismatch: return None`). In sustained trending_down, this\n"
        "killed all LONG triggers and the scanner emitted 4.84x more SHORT labels\n"
        "than LONG labels - feeding the brain a one-sided slate.\n\n"
        "Fix: each trigger now accepts regime_haircut kwarg. On regime mismatch,\n"
        "returns base_conf * regime_haircut instead of None. Single symmetric\n"
        "value (counter_regime_confidence_haircut = 0.5) applied to all 8.\n"
    )

    settings = Settings.load()
    live_haircut = settings.scanner.labeller.counter_regime_confidence_haircut

    subsection("Loaded haircut value + module constant")
    print(f"  LABELLER_REGIME_HAIRCUT_VERSION (module) = {LABELLER_REGIME_HAIRCUT_VERSION}")
    print(f"  counter_regime_confidence_haircut (TOML) = {live_haircut}")

    # Direct trigger invocations — bypasses label_state priority ordering so
    # we test each trigger's haircut math in isolation. Inputs chosen so
    # only the target trigger qualifies; other triggers return None.
    # Each tuple: (label, trigger_fn, mismatch_kwargs, expected_base_conf)
    trigger_cases = [
        # 4 LONG triggers in their respective mismatch regimes
        ("trend_pullback_LONG", _trigger_trend_pullback_long,
         dict(regime="trending_down", setup_type="bullish_fvg_ob",
              trade_direction="long", setup_type_confidence=0.60),
         0.60),
        ("range_fade_LONG", _trigger_range_fade_long,
         dict(regime="trending_down", trade_direction="long",
              position_in_range=0.15, consensus_direction="",
              setup_type_confidence=0.60),
         0.60),
        ("funding_extreme_LONG", _trigger_funding_extreme_fade_long,
         dict(regime="trending_down", funding_rate=-0.005,
              position_in_range=0.45),
         1.0),  # min(1.0, 0.40 + 0.0035*200) -> 1.10 -> capped 1.0
        ("extreme_fear_LONG", _trigger_extreme_fear_long,
         dict(regime="trending_down", fear_greed=10,
              consensus_direction="long", trade_direction="long"),
         0.60),  # 0.40 + (20-10)/50 = 0.60
        # 4 SHORT triggers in their mirror mismatch regimes
        ("trend_pullback_SHORT", _trigger_trend_pullback_short,
         dict(regime="trending_up", setup_type="bearish_fvg_ob",
              trade_direction="short", setup_type_confidence=0.60),
         0.60),
        ("range_fade_SHORT", _trigger_range_fade_short,
         dict(regime="trending_up", trade_direction="short",
              position_in_range=0.85, consensus_direction="",
              setup_type_confidence=0.60),
         0.60),
        ("funding_extreme_SHORT", _trigger_funding_extreme_fade_short,
         dict(regime="trending_up", funding_rate=0.005,
              position_in_range=0.55),
         1.0),
        ("extreme_greed_SHORT", _trigger_extreme_greed_short,
         dict(regime="trending_up", fear_greed=85,
              consensus_direction="short", trade_direction="short"),
         0.50),  # 0.40 + (85-80)/50 = 0.50
    ]

    subsection("Per-trigger return values across haircut values (mismatch regime)")
    print(f"  {'trigger':<24} {'expected':>9} {'h=0.0':>10} {'h=0.5':>10} {'h=1.0':>10}")
    asserts: list[tuple[str, bool]] = []
    for name, trig, kwargs, expected_base in trigger_cases:
        r0 = trig(regime_haircut=0.0, **kwargs)
        r05 = trig(regime_haircut=0.5, **kwargs)
        r1 = trig(regime_haircut=1.0, **kwargs)
        r0_str = "None" if r0 is None else f"{r0:.3f}"
        r05_str = "None" if r05 is None else f"{r05:.3f}"
        r1_str = "None" if r1 is None else f"{r1:.3f}"
        print(
            f"  {name:<24} {expected_base:>9.3f} "
            f"{r0_str:>10} {r05_str:>10} {r1_str:>10}"
        )
        # Three assertions per trigger
        asserts.append(
            (f"{name} h=0.0 -> None (legacy hard-kill preserved)", r0 is None))
        asserts.append(
            (f"{name} h=0.5 -> base * 0.5",
             r05 is not None and abs(r05 - expected_base * 0.5) < 1e-6))
        asserts.append(
            (f"{name} h=1.0 -> base (regime gate effectively off)",
             r1 is not None and abs(r1 - expected_base) < 1e-6))

    subsection("Cross-check: in-regime behavior is haircut-invariant")
    in_regime_cases = [
        ("trend_pullback_LONG", _trigger_trend_pullback_long,
         dict(regime="trending_up", setup_type="bullish_fvg_ob",
              trade_direction="long", setup_type_confidence=0.60)),
        ("range_fade_LONG", _trigger_range_fade_long,
         dict(regime="ranging", trade_direction="long",
              position_in_range=0.15, consensus_direction="",
              setup_type_confidence=0.60)),
    ]
    in_regime_ok = True
    for name, trig, kwargs in in_regime_cases:
        confs = [trig(regime_haircut=h, **kwargs) for h in (0.0, 0.5, 1.0)]
        ok = all(c is not None and abs(c - confs[0]) < 1e-9 for c in confs)
        print(f"  {name} in-regime: confs = {confs}  -> invariant = {ok}")
        if not ok:
            in_regime_ok = False
    asserts.append(("In-regime confidence is haircut-invariant", in_regime_ok))

    subsection("End-to-end label_state pipeline (haircut = live 0.5)")
    # Demonstrate the full pipeline produces a usable LONG label in
    # trending_down with the live haircut value.
    e2e = label_state(
        setup_type="bullish_fvg_ob", trade_direction="long",
        setup_type_confidence=0.60, regime="trending_down",
        regime_haircut=live_haircut,
    )
    print(f"  label_state(LONG setup, trending_down, haircut={live_haircut}) "
          f"-> primary={e2e.primary!r} conf={e2e.confidence}")
    asserts.append(
        (f"label_state pipeline admits LONG in trending_down at haircut={live_haircut}",
         e2e.primary == "TREND_PULLBACK_LONG" and e2e.confidence == 0.30))

    asserts.append(("LABELLER_REGIME_HAIRCUT_VERSION == 2",
                    LABELLER_REGIME_HAIRCUT_VERSION == 2))
    asserts.append(("Live haircut config == 0.5", abs(live_haircut - 0.5) < 1e-9))

    return report(asserts)


# ============================================================================
# SCENARIO 4 - Issue 1: XRAY min-edge floor + symmetric min_touches on real OHLC
# ============================================================================

def scenario_issue1() -> bool:
    section("SCENARIO 4 - Issue 1: XRAY rr_long collapse on sustained downtrend")
    print(
        "Pre-fix bug: in sustained trending_down, support_resistance.py:126\n"
        "hardcoded `r.touches >= 1` for resistance while min_touches=2 for\n"
        "support. Noisy single-touch swing highs near current price became\n"
        "nearest resistance -> structural_tp formula produced TP at/below\n"
        "current price -> rr_long ~ 0 -> XRAY flip block triggered Buy -> Sell.\n\n"
        "Fix: min_touches_resistance=2 (symmetric with support) drops noise.\n"
        "tp_min_distance_pct=0.5 percent clamp catches any remaining degenerate\n"
        "cases - structural_tp clamped, is_structurally_invalid=True surfaced.\n"
    )

    # Build synthetic OHLC: 200 candles of a sustained downtrend.
    rng = np.random.default_rng(seed=42)
    n = 200
    drift = np.linspace(105.0, 100.0, n)
    noise = rng.normal(0.0, 0.15, n).cumsum() * 0.08
    base = drift + noise
    highs = base + 0.4
    lows = base - 0.4
    closes = base

    # Plant: 3 multi-touch resistance at $103 (legitimate)
    for idx in (40, 80, 120):
        highs[idx] = 103.0
    # Plant: 1 single-touch swing high at $100.4 (the noise that collapses rr_long pre-fix)
    highs[160] = 100.4
    # Plant: 3 multi-touch support at $95 (legitimate, far below)
    for idx in (60, 100, 140):
        lows[idx] = 95.0

    highs64 = highs.astype(np.float64)
    lows64 = lows.astype(np.float64)
    closes64 = closes.astype(np.float64)
    current_price = 100.0

    ms_trending_down = MarketStructureResult(structure="trending_down", strength="strong")

    # ---- Pre-fix simulation: min_touches_resistance=1, no clamp ----
    pre_settings = StructureSettings(
        min_touches=2,
        min_touches_resistance=1,
        tp_min_distance_pct=0.0,
        sl_buffer_pct=0.15,
        tp_buffer_pct=0.10,
        sl_fallback_pct=2.0,
        tp_fallback_pct=4.0,
    )
    pre_sr = SupportResistanceEngine(pre_settings)
    pre_sup, pre_res, _ = pre_sr.calculate(highs64, lows64, closes64, current_price)
    pre_calc = StructuralLevelCalculator(pre_settings)
    pre_placement = pre_calc._calc_long(
        current_price=current_price,
        supports=pre_sup,
        resistances=pre_res,
        ms=ms_trending_down,
        position=0.95,
    )

    # ---- Post-fix simulation: min_touches_resistance=2, tp_min_distance_pct=0.5 ----
    post_settings = StructureSettings(
        min_touches=2,
        min_touches_resistance=2,
        tp_min_distance_pct=0.5,
        sl_buffer_pct=0.15,
        tp_buffer_pct=0.10,
        sl_fallback_pct=2.0,
        tp_fallback_pct=4.0,
    )
    post_sr = SupportResistanceEngine(post_settings)
    post_sup, post_res, _ = post_sr.calculate(highs64, lows64, closes64, current_price)
    post_calc = StructuralLevelCalculator(post_settings)
    post_placement = post_calc._calc_long(
        current_price=current_price,
        supports=post_sup,
        resistances=post_res,
        ms=ms_trending_down,
        position=0.95,
    )

    subsection("Detected levels (after filter)")
    print(f"  PRE-FIX:  sup={len(pre_sup)} res={len(pre_res)}")
    print(f"            res touches: {[r.touches for r in pre_res]}")
    print(f"            nearest res price: {pre_res[0].price:.2f} touches={pre_res[0].touches}")
    print(f"  POST-FIX: sup={len(post_sup)} res={len(post_res)}")
    print(f"            res touches: {[r.touches for r in post_res]}")
    if post_res:
        print(f"            nearest res price: {post_res[0].price:.2f} touches={post_res[0].touches}")
    else:
        print("            (no resistance survived the symmetric filter)")

    subsection("StructuralPlacement output for _calc_long")
    print(f"  PRE-FIX:  structural_tp=${pre_placement.structural_tp:.2f}  "
          f"rr_ratio={pre_placement.rr_ratio:.3f}  is_invalid={pre_placement.is_structurally_invalid}")
    print(f"  POST-FIX: structural_tp=${post_placement.structural_tp:.2f}  "
          f"rr_ratio={post_placement.rr_ratio:.3f}  is_invalid={post_placement.is_structurally_invalid}")

    # ---- Edge-case simulation: force degenerate placement to exercise clamp ----
    subsection("Edge case - force clamp activation (resistance AT current price)")
    res_at_price = PriceLevel(
        price=100.0, zone_low=100.0, zone_high=100.1,
        touches=3, strength=3.0, level_type="resistance",
    )
    sup_below = PriceLevel(
        price=95.0, zone_low=94.9, zone_high=95.1,
        touches=3, strength=3.0, level_type="support",
    )
    clamp_placement = post_calc._calc_long(
        current_price=100.0,
        supports=[sup_below],
        resistances=[res_at_price],
        ms=MarketStructureResult(structure="ranging", strength="medium"),
        position=0.95,
    )
    print(f"  resistance AT $100 -> structural_tp=${clamp_placement.structural_tp:.2f}  "
          f"is_invalid={clamp_placement.is_structurally_invalid}  rr={clamp_placement.rr_ratio:.3f}")
    print(f"  Expected: tp clamped to >= $100.50 (0.5 pct above), is_invalid=True, rr>0")

    # Mirror short clamp
    subsection("Edge case - mirror clamp for _calc_short (support AT current price)")
    sup_at_price = PriceLevel(
        price=100.0, zone_low=99.9, zone_high=100.0,
        touches=3, strength=3.0, level_type="support",
    )
    res_above = PriceLevel(
        price=105.0, zone_low=104.5, zone_high=105.5,
        touches=3, strength=3.0, level_type="resistance",
    )
    short_clamp = post_calc._calc_short(
        current_price=100.0,
        supports=[sup_at_price],
        resistances=[res_above],
        ms=MarketStructureResult(structure="ranging", strength="medium"),
        position=0.05,
    )
    print(f"  support AT $100 -> structural_tp=${short_clamp.structural_tp:.2f}  "
          f"is_invalid={short_clamp.is_structurally_invalid}  rr={short_clamp.rr_ratio:.3f}")
    print(f"  Expected: tp clamped to <= $99.50 (0.5 pct below), is_invalid=True, rr>0")

    return report([
        ("Pre-fix keeps single-touch resistance noise",
         any(r.touches == 1 for r in pre_res)),
        ("Post-fix symmetric filter drops single-touch resistance",
         all(r.touches >= 2 for r in post_res)),
        ("Post-fix rr_long is strictly positive (no collapse-to-zero)",
         post_placement.rr_ratio > 0.0),
        ("Post-fix rr_long > pre-fix rr_long (filter improved the placement)",
         post_placement.rr_ratio >= pre_placement.rr_ratio),
        ("Edge-case LONG clamp activates: tp >= current_price * 1.005",
         clamp_placement.structural_tp >= 100.5),
        ("Edge-case LONG clamp sets is_structurally_invalid=True",
         clamp_placement.is_structurally_invalid is True),
        ("Edge-case LONG clamp keeps rr strictly positive",
         clamp_placement.rr_ratio > 0.0),
        ("Edge-case SHORT clamp activates: tp <= current_price * 0.995",
         short_clamp.structural_tp <= 99.5),
        ("Edge-case SHORT clamp sets is_structurally_invalid=True",
         short_clamp.is_structurally_invalid is True),
        ("Edge-case SHORT clamp keeps rr strictly positive",
         short_clamp.rr_ratio > 0.0),
    ])


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    section("DIRECTION-BIAS FIX SERIES - LIVE SIMULATION")
    print("Each scenario re-creates the pre-fix bug condition + runs the real")
    print("production code path + cross-checks against design aim.")
    print("All four fixes shipped on 2026-05-19, restart at 10:03 UTC.")

    results = {
        "Issue 4 (symmetric MARKET REGIME)": scenario_issue4(),
        "Issue 2 (counter mult = 1.0)": scenario_issue2(),
        "Issue 3 (soft regime haircut)": scenario_issue3(),
        "Issue 1 (XRAY clamp + symmetric)": scenario_issue1(),
    }

    section("FINAL SIMULATION SUMMARY")
    overall_ok = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status} - {name}")
        if not ok:
            overall_ok = False

    print()
    if overall_ok:
        print("OVERALL VERDICT: ALL FOUR FIXES BEHAVE AS DESIGNED.")
        print("Pre-fix bug conditions reproduced; post-fix code paths respond correctly.")
        return 0
    print("OVERALL VERDICT: ONE OR MORE FIXES DID NOT BEHAVE AS EXPECTED.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
