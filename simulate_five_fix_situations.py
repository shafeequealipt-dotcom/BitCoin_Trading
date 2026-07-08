"""Five-Fix Follow-Up — live-situation simulation (2026-06-11).

Recreates each of the five ORIGINAL problem situations with the same data the
evidence captured (the polluted ALGO components line, the six-cycle frozen
signal, the RUNE noise-death stop, the RUNE/SKR inverted-OI buys, the HYPE
size inflation) and replays them through the REAL fixed code paths — the real
renderer, the real three-window blend, the real classifier, the live scaling
helper, the real optimizer-plus-gate chain — printing BEFORE (the recorded
losing behaviour) against AFTER (what the fixed system does on the same
situation), and asserting each responds as fixed.

Situation data sources, cited inline: the 2026-06-10 capture
(RECENT_6_CALLA_PROMPTS / CALL_A_PROMPTS 09:30-14:00), the losing-window
trade_log, the live SIZE_DERIVATION breadcrumbs, and five REAL ALGOUSDT
5-minute open-interest snapshots fetched from the demo exchange during the
2026-06-11 pipeline verification. Read-only; never rewrites data.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

from src.config.settings import Settings, SignalGeneratorMultiSourceSettings
from src.core.types import Signal, SignalType
from src.intelligence.signals.signal_generator import SignalGenerator

PASS: list[str] = []
FAIL: list[str] = []


def assert_fixed(name: str, before: str, after: str, ok: bool) -> None:
    (PASS if ok else FAIL).append(name)
    print(f"\n--- {name} ---")
    print(f"  BEFORE (problem): {before}")
    print(f"  AFTER  (fixed):   {after}")
    print(f"  => {'RESPONDS AS FIXED' if ok else 'NOT FIXED'}")


def _sg(cfg: SignalGeneratorMultiSourceSettings | None = None) -> SignalGenerator:
    sg = SignalGenerator.__new__(SignalGenerator)
    sg._ms_cfg = cfg or SignalGeneratorMultiSourceSettings()
    return sg


async def _blend(sg, oi24, p24, oi1h, oi15m, p_short):
    async def _pc(symbol, bars):
        return p_short
    sg._compute_recent_price_change_pct = _pc
    return await sg._blend_oi_windows("SIM", oi24, p24, oi1h, oi15m)


# ── SITUATION 1 — the polluted ALGO Components line (Fix 1) ─────────────────
# Captured 2026-06-10 (RECENT_6 line 351): "Components: oi_change_pct=-2.6095,
# confidence_floor_failed=1.0000, confidence_below_buy=1.0000,
# funding_rate=-0.0000, confidence_below_strong=0.0000, fear_greed=9" — the
# brain read internal bookkeeping flags as market inputs, in shifting
# positions, because bool passed the numeric type check.
def situation_1_polluted_components(settings) -> None:
    from src.brain.strategist import ClaudeStrategist
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StrategiesBlock, XrayBlock,
    )

    # The EXACT captured ALGO shape: downgrade fired (flags True), the same
    # market values, plus the post-Fix-2 window keys the live dict now carries.
    algo_sig = Signal(
        symbol="ALGOUSDT", signal_type=SignalType.NEUTRAL, confidence=0.34,
        source="intelligence_aggregator",
        components={
            "fear_greed": 9,
            "funding_rate": -0.0000,
            "oi_change_15m_pct": -0.31,
            "oi_change_1h_pct": 0.73,
            "oi_change_24h_pct": -2.6095,
            "original_signal_type": "sell",
            "confidence_floor_failed": True,
            "confidence_below_buy": True,
            "confidence_below_strong": False,
        },
    )

    class _SW:
        def get_signal(self, sym):
            return algo_sig

    strategist = ClaudeStrategist(None, {"signal_worker": _SW()}, settings)
    pkg = CoinPackage(
        symbol="ALGOUSDT", qualified=True, opportunity_score=0.6,
        qualification_reasons=["sim"],
        price_data=PriceDataBlock(current=0.1812, change_24h_pct=-2.88),
        xray=XrayBlock(), strategies=StrategiesBlock(),
        signals=SignalsBlock(), alt_data=AltDataBlock(),
        interestingness_score=0.75,
    )
    out = strategist._format_packages_for_prompt_full({"ALGOUSDT": pkg})
    comp = next((ln for ln in out.splitlines() if "Components:" in ln), "")
    clean = (
        all(k not in out for k in (
            "confidence_floor_failed", "confidence_below_buy",
            "confidence_below_strong", "original_signal_type",
        ))
        and "oi_change_24h_pct=-2.6095" in comp
        and "fear_greed=9 (global, direction-inactive)" in out
        and algo_sig.components["confidence_floor_failed"] is True  # dict intact
    )

    # Defense in depth: even with the exclusion flag rolled back, the captured
    # bool-as-1.0000 leak can never recur (the type guard is unconditional).
    s_off = copy.deepcopy(settings)
    s_off.brain.components_diagnostics_excluded = False
    strategist_off = ClaudeStrategist(None, {"signal_worker": _SW()}, s_off)
    out_off = strategist_off._format_packages_for_prompt_full({"ALGOUSDT": pkg})
    no_bool_leak = "confidence_floor_failed" not in out_off

    assert_fixed(
        "Situation 1 — ALGO candidate block polluted with classifier diagnostics",
        "Components: oi_change_pct=-2.6095, confidence_floor_failed=1.0000, "
        "confidence_below_buy=1.0000, funding_rate=-0.0000, ... (captured 2026-06-10)",
        f"{comp.strip()} | diagnostics absent={clean} | "
        f"flag-off bool leak still impossible={no_bool_leak}",
        clean and no_bool_leak,
    )


# ── SITUATION 2 — the six-cycle frozen signal (Fix 2) ───────────────────────
# Captured: ALGO oi_change_pct=-2.6095 IDENTICAL across six 5-minute cycles
# (~30 min) because the stored series was an hourly plateau — the short
# deltas read exactly 0.0 inside a plateau and the 24h delta pinned.
# The AFTER side uses REAL ALGOUSDT 5-minute snapshots fetched from the demo
# exchange on 2026-06-11 (pipeline verification run) — genuinely moving data.
def situation_2_frozen_six_cycles() -> None:
    # Six cycles of PRE-FIX inputs: hourly-plateau storage = frozen 24h delta,
    # zero short deltas. Pre-fix read = 24h-only (the revert config).
    pre_cfg = SignalGeneratorMultiSourceSettings(
        oi_blend_weight_15m=0.0, oi_blend_weight_short=0.0, oi_blend_weight_long=1.0,
    )
    sg_pre = _sg(pre_cfg)
    pre_scores = []
    for _cycle in range(6):
        b, _ = asyncio.run(_blend(sg_pre, oi24=-2.6095, p24=-2.88, oi1h=0.0, oi15m=0.0, p_short=-0.6))
        pre_scores.append(round(b, 4))

    # Six cycles of POST-FIX inputs: real demo 5-minute snapshots
    # (2026-06-11: 69,715,646 -> 69,672,670 -> 69,752,627 -> 69,803,401 ->
    # 69,770,985 -> 69,748,287) yield moving 15m deltas each cycle; the 1h
    # delta drifts; the 24h figure stays the slow context it always was.
    snaps = [69715646.0, 69672670.0, 69752627.0, 69803401.3, 69770984.8, 69748287.1]
    sg_post = _sg()  # live defaults: 15m=0.4 / 1h=0.6 / 24h context
    post_scores = []
    for i in range(6):
        cur = snaps[i]
        prior_15m = snaps[i - 3] if i >= 3 else snaps[0]
        oi15m = (cur - prior_15m) / prior_15m * 100.0
        oi1h = (cur - snaps[0]) / snaps[0] * 100.0
        b, _ = asyncio.run(_blend(sg_post, oi24=-2.6095, p24=-2.88,
                                  oi1h=oi1h, oi15m=oi15m, p_short=-0.6))
        post_scores.append(round(b, 4))

    frozen_before = len(set(pre_scores)) == 1
    moves_after = len(set(post_scores)) >= 4
    assert_fixed(
        "Situation 2 — signal pinned identical across six 5-minute cycles",
        f"24h-only on plateau data: blended scores {pre_scores} (IDENTICAL — the brain saw the same photo six times)",
        f"15m/1h drivers on real 5-min snapshots: {post_scores} ({len(set(post_scores))} distinct — moves with the market)",
        frozen_before and moves_after,
    )

    # The aim behind the fix: catch the turn WITHIN the hour. Shorts pile in
    # NOW (15m OI rising on a falling 15m price) while the 24h read is quiet.
    sg_live = _sg()
    turn, dbg = asyncio.run(_blend(sg_live, oi24=0.4, p24=0.2, oi1h=2.5, oi15m=3.2, p_short=-0.9))
    assert_fixed(
        "Situation 2b — intra-session turn invisible to the 24h read",
        "24h read quiet (+0.4% OI, +0.2% price) — old signal saw nothing for hours",
        f"15m OI +3.2% on 15m price -0.9% -> blended {turn:+.3f} "
        f"(bearish NOW, cond_15m={dbg['cond_15m']} cond_1h={dbg['cond_1h']})",
        turn < -0.10 and dbg["cond_15m"] == "inv",
    )


# ── SITUATION 3 — the RUNE noise-death stop (Fix 3) ─────────────────────────
# Losing window 2026-06-10: RUNE buys stopped within minutes at fractional
# moves (entry 0.392, hold 0.7 min, the 1.5% constant stop INSIDE the coin's
# noise band — 65% of the window's closes were stop-hits). The losing-window
# replay classified RUNE high-volatility, recommended stop 2.0%.
def situation_3_rune_noise_death(settings) -> None:
    from src.workers.strategy_worker import compute_volatility_scaled_stop

    entry = 0.392                      # captured RUNE entry
    wiggle_low = entry * (1 - 0.018)   # the -1.8% first-minutes noise excursion
    vss = settings.risk.volatility_stop_scaling
    brain_sl = entry * (1 - vss.reference_stop_pct / 100.0)

    new_sl, new_size, target_pct, final_pct = compute_volatility_scaled_stop(
        sl=brain_sl, current_price=entry, direction="Buy", size_usd=100.0,
        recommended_sl_pct=2.0,        # the replay's high-class recommendation
        reference_stop_pct=vss.reference_stop_pct, max_cap_pct=vss.max_cap_pct,
    )
    before_dies = wiggle_low <= brain_sl       # 1.5% stop inside the wiggle
    after_survives = wiggle_low > new_sl       # scaled stop outside it
    risk_before = 100.0 * vss.reference_stop_pct / 100.0
    risk_after = new_size * final_pct / 100.0
    assert_fixed(
        "Situation 3 — RUNE stopped by ordinary noise in its first minutes",
        f"entry {entry}, constant 1.5% stop {brain_sl:.5f}, -1.8% wiggle to {wiggle_low:.5f} "
        f"-> STOPPED in 0.7 min (captured), risk ${risk_before:.2f}",
        f"vol-scaled stop {final_pct:.2f}% = {new_sl:.5f}, the same wiggle SURVIVES; "
        f"size 100->{new_size:.1f} so risk ${risk_after:.2f} == ${risk_before:.2f}",
        before_dies and after_survives and abs(risk_after - risk_before) < 1e-9,
    )

    # And the brain now SEES the floor instead of being silently widened.
    from src.brain.strategist import ClaudeStrategist
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
        StrategiesBlock, XrayBlock,
    )

    class _SW:
        def get_signal(self, sym):
            return None

    strategist = ClaudeStrategist(None, {"signal_worker": _SW()}, settings)
    pkg = CoinPackage(
        symbol="RUNEUSDT", qualified=True, opportunity_score=0.6,
        qualification_reasons=["sim"],
        price_data=PriceDataBlock(current=entry, change_24h_pct=-1.0),
        xray=XrayBlock(), strategies=StrategiesBlock(),
        signals=SignalsBlock(), alt_data=AltDataBlock(),
        interestingness_score=0.75,
    )
    out = strategist._format_packages_for_prompt_full(
        {"RUNEUSDT": pkg}, vol_floors={"RUNEUSDT": target_pct},
    )
    assert_fixed(
        "Situation 3b — the brain was never told the coin's real noise band",
        "prompt said the flat constant: 'SL minimum 1.5% from entry. Tighter is rejected.'",
        f"candidate now carries 'Vol stop floor: {target_pct:.2f}%' "
        f"(rendered={'Vol stop floor' in out}); system prompt rule 4 is volatility-aware",
        f"Vol stop floor: {target_pct:.2f}%" in out,
    )


# ── SITUATION 4 — RUNE/SKR inverted-OI buys through the LIVE blend (Fix 4) ──
# Captured root: RUNE OI +5.8% with price FALLING read as a buy; SKR
# +10.56% on a short structure read strong_buy. Replayed here through the
# CURRENT three-window production path (blend -> classifier), shorts piling
# in across the short windows exactly as the situation described.
def situation_4_inverted_oi_live_path() -> None:
    sg = _sg()
    cases = [
        ("RUNE", dict(oi24=5.8, p24=-2.0, oi1h=3.0, oi15m=1.2, p_short=-1.2)),
        ("SKR", dict(oi24=10.56, p24=-3.5, oi1h=6.0, oi15m=4.0, p_short=-2.0)),
    ]
    results = []
    for name, c in cases:
        blended, dbg = asyncio.run(_blend(sg, **c))
        stype, _ = sg._evaluate_signal(
            fear_greed=9, funding_rate=-0.0001, oi_change=c["oi24"],
            price_change=c["p24"], oi_score=blended, symbol=name,
        )
        results.append((name, blended, stype.value, dbg["cond_15m"], dbg["cond_1h"]))
    ok = all(
        b < 0 and t in ("sell", "strong_sell") and c15 == "inv" and c1h == "inv"
        for _, b, t, c15, c1h in results
    )
    assert_fixed(
        "Situation 4 — rising OI on a falling price manufactured BUYs (RUNE/SKR)",
        "RUNE +5.8% OI on falling price -> buy; SKR +10.56% on short structure -> strong_buy (the wrong-side root)",
        " ; ".join(
            f"{n}: blended {b:+.3f} -> {t} (cond_15m={c15} cond_1h={c1h})"
            for n, b, t, c15, c1h in results
        ),
        ok,
    )


# ── SITUATION 5 — the HYPE size inflation through the REAL chain (Fix 5) ────
# Captured live 2026-06-10: SIZE_DERIVATION | sym=HYPEUSDT claude=$700
# apex=$1200 gate_c0=$1050 final=$1050 — the brain chose 700 dollars and
# 1050 executed. Replayed through the real optimizer + real gate, both
# switch states.
def situation_5_hype_inflation(settings) -> None:
    from src.apex.optimizer import TradeOptimizer, OptimizedTrade
    from src.apex.gate import TradeGate

    def _hype():
        return OptimizedTrade(
            symbol="HYPEUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
            tp_mode="fixed", position_size_usd=1200.0, leverage=2,
            entry_timing="immediate", add_on_pullback=False,
            reasoning="sim", confidence=1.0, original_size=700.0,
        )

    async def _chain(apex_cfg) -> tuple[float, float]:
        opt = TradeOptimizer(None, None, apex_cfg)
        t = _hype()
        opt._apply_constraints(t)
        apex_size = float(t.position_size_usd)
        gate = TradeGate(
            {"fund_manager": SimpleNamespace(
                _account_state=SimpleNamespace(available=10000.0))},
            apex_cfg,
        )
        gd = {"symbol": "HYPEUSDT", "direction": "Buy",
              "size_usd": apex_size, "leverage": int(t.leverage),
              "_xray_confidence": 0.7, "_setup_score": 60.0,
              "_expected_rr": 2.0, "_claude_original_size_usd": 700.0,
              "original_size": 700.0, "entry_price": 38.0}
        validated = await gate.validate(gd)
        return apex_size, float(validated["size_usd"])

    pre_cfg = copy.copy(settings.apex)
    pre_cfg.apex_size_override_enabled = True   # the pre-fix behaviour
    apex_pre, final_pre = asyncio.run(_chain(pre_cfg))
    apex_post, final_post = asyncio.run(_chain(settings.apex))  # switch off (live default)

    assert_fixed(
        "Situation 5 — the brain's $700 executed as $1050 (system-side multiplier)",
        f"switch ON replays the capture: brain $700 -> apex ${apex_pre:.0f} -> "
        f"gate CHECK0 ${final_pre:.0f} executed (matches the live SIZE_DERIVATION)",
        f"switch OFF (live default): brain $700 -> apex ${apex_post:.0f} -> "
        f"final ${final_post:.0f} — the amount the brain selects is the amount that trades",
        abs(apex_pre - 1200.0) < 1e-6 and abs(final_pre - 1050.0) < 1e-6
        and abs(apex_post - 700.0) < 1e-6 and abs(final_post - 700.0) < 1e-6,
    )


def main() -> int:
    print("=== Five-Fix Follow-Up — live-situation simulation "
          "(original problems replayed through the fixed system) ===")
    settings = Settings.load()
    situation_1_polluted_components(settings)
    situation_2_frozen_six_cycles()
    situation_3_rune_noise_death(settings)
    situation_4_inverted_oi_live_path()
    situation_5_hype_inflation(settings)
    print(f"\n==== RESULT: {len(PASS)} situations fixed, {len(FAIL)} not fixed ====")
    if FAIL:
        for f in FAIL:
            print(f"  NOT FIXED: {f}")
        return 1
    print("ALL ORIGINAL SITUATIONS NOW RESPOND AS FIXED.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
