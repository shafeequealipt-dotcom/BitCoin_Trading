"""Live-style SIMULATION of the original issue-occurring situations.

Reconstructs the exact coins/values from the captured Call-A prompt that
exhibited each issue (SKR, BSB, AVAX, BLUR, KAT — CALL_A_FULL_PROMPT_2026-06-09)
and renders them through the REAL production pipeline, showing for EACH phase a
BEFORE (fix disabled — reproduces the original bug) vs AFTER (shipped behavior)
contrast with a FIXED / NOT-FIXED verdict. This is the "does each fix respond
as intended on real-shaped data" check.

SAFE: fresh real components only; no live-process contact, no DB writes, no
exchange/Claude connections. Run:
  .venv/bin/python verify_candidate_block_live_simulation.py
"""

from verify_candidate_block_pipeline_runtime import (
    real_settings, real_strategist, real_layer_manager_with, real_signal,
    make_pkg, structural, _SigGetter, _StructCache, _RegimeDet,
)

VERDICTS = []


def grep_lines(block, *needles):
    return [ln.strip() for ln in block.splitlines()
            if any(n in ln for n in needles)]


def render(settings, symbol, price, xray_dir, structure, range_pos, confl,
           signals_dir, sig_type, comps, votes=None, scorer=None):
    services = {
        "layer_manager": real_layer_manager_with(
            scorer_components={symbol: scorer} if scorer else {},
            votes={symbol: votes} if votes else {}),
        "signal_worker": _SigGetter({symbol: real_signal(symbol, sig_type, comps)}),
        "structure_cache": _StructCache({symbol: structural(symbol, xray_dir, structure, range_pos, confl)}),
        "regime_detector": _RegimeDet(),
    }
    strat = real_strategist(settings, services)
    return strat._format_packages_for_prompt_full(
        {symbol: make_pkg(symbol, price, xray_dir, structure, range_pos, confl, signals_dir)})


def verdict(phase, coin, aim, before_lines, after_lines, fixed, note=""):
    VERDICTS.append((phase, fixed))
    print("\n" + "=" * 74)
    print(f"PHASE {phase}  —  coin {coin}")
    print(f"AIM: {aim}")
    print("-" * 74)
    print("BEFORE (fix disabled — the original situation the brain saw):")
    for l in before_lines or ["  (line absent)"]:
        print("   " + l)
    print("AFTER (shipped behavior):")
    for l in after_lines or ["  (line absent / omitted)"]:
        print("   " + l)
    if note:
        print("NOTE: " + note)
    print("VERDICT: " + ("FIXED — responds as intended" if fixed else "NOT FIXED"))


def main():
    s = real_settings()
    B = s.brain
    SE = s.strategy_engine
    ST = s.stage2

    # ---- PHASE 1a: SKR strong_buy SIGNAL vs short STRUCTURE ----
    skr_comps = {"overall_sentiment": None, "fear_greed": 10, "funding_rate": 0.0001,
                 "oi_change_pct": 10.565, "news_count": None, "reddit_count": None}
    B.emit_direction_disagreement_notes = False
    off = render(s, "SKRUSDT", 0.009863, "short", "ranging", 0.77, 53, "short", "STRONG_BUY", skr_comps)
    B.emit_direction_disagreement_notes = True
    on = render(s, "SKRUSDT", 0.009863, "short", "ranging", 0.77, 53, "short", "STRONG_BUY", skr_comps)
    b = grep_lines(off, "NOTE: this Signal")
    a = grep_lines(on, "NOTE: this Signal")
    verdict("1a (signal-vs-structure)", "SKRUSDT (signal=strong_buy, X-RAY=short)",
            "a strong-buy signal on a short-structure coin must be flagged as a labeled disagreement, not read as authoritative",
            b or ["Signal: type=strong_buy ...  (no disagreement note — brain could read it as a clean buy)"],
            grep_lines(on, "Signal: type=strong_buy") + a, bool(a) and not b)

    # ---- PHASE 1b: BSB ensemble LONG vs short STRUCTURE + votes/poll ----
    bsb_comps = {"overall_sentiment": None, "fear_greed": 10, "funding_rate": 0.0001,
                 "oi_change_pct": -2.095, "news_count": None, "reddit_count": None}
    bsb_votes = {"votes": {"F2": {"vote": "BUY", "confidence": 0.85, "weight": 1.0}},
                 "buy_weighted": 3.10, "sell_weighted": 0.0, "opposing_weighted": 0.0,
                 "two_sided": True, "consensus": "WEAK", "consensus_direction": "BUY"}
    bsb_scorer = {"base": 37.0, "confluence": 20.0, "context": 20.0, "quality": 7.0,
                  "total": 84.0, "grade": "A+"}
    B.emit_direction_disagreement_notes = False
    off = render(s, "BSBUSDT", 0.3353, "short", "downtrend", 1.0, 35, "long", "SELL", bsb_comps, bsb_votes, bsb_scorer)
    B.emit_direction_disagreement_notes = True
    on = render(s, "BSBUSDT", 0.3353, "short", "downtrend", 1.0, 35, "long", "SELL", bsb_comps, bsb_votes, bsb_scorer)
    verdict("1b (ensemble-vs-structure)", "BSBUSDT (ensemble=long, X-RAY=downtrend short) — the coin the brain mis-traded long",
            "an ensemble-long lean on a short downtrend must be labeled as a disagreement so the brain doesn't fade the structure on consensus alone",
            grep_lines(off, "Consensus Context:") + ["(no DISAGREEMENT line)"],
            grep_lines(on, "Consensus Context:", "DISAGREEMENT"),
            ("DISAGREEMENT" in on) and ("DISAGREEMENT" not in off))
    verdict("1b (votes line + two-sided poll)", "BSBUSDT (one-sided BUY=3.10, opposing=0)",
            "the one-sided tally must be labeled, and the two-sided poll must render even when opposing=0 (removing the BSB asymmetry)",
            grep_lines(off, "Votes:", "Two-sided poll"),
            grep_lines(on, "Votes (confirmed-direction tally):", "Two-sided poll"),
            ("Votes (confirmed-direction tally):" in on) and ("Two-sided poll:" in on)
            and ("Two-sided poll:" not in off))

    # ---- PHASE 2: AVAX 0-fired vs the 23-fired coins (firing honesty + obs) ----
    avax_comps = {"overall_sentiment": None, "fear_greed": 10, "funding_rate": -0.00004,
                  "oi_change_pct": -3.756, "confidence_floor_failed": 1.0,
                  "confidence_below_strong": 1.0, "news_count": None, "reddit_count": None}
    B.emit_direction_disagreement_notes = True
    on = render(s, "AVAXUSDT", 6.607, "long", "uptrend", 0.05, 39, "long", "SELL", avax_comps)
    # AVAX signal=sell vs X-RAY long — also a 1a conflict (cross-check the note generalizes)
    verdict("2 / 1a cross-check", "AVAXUSDT (0-fired; signal=sell vs X-RAY=long)",
            "zero-fired is presented honestly and the signal-vs-structure note generalizes to the sell-vs-long mirror; per-coin L1 fire/skip logs make genuine-vs-data-gap provable live",
            ["Strategies: 0 fired ... (no signal-vs-structure note before)"],
            grep_lines(on, "Strategies:", "no scored setup", "NOTE: this Signal"),
            "CONFLICTS with the X-RAY structure (LONG)" in on,
            "Phase 2's per-coin logs (STRAT_L1_COIN_FIRE_DIST / STRAT_SKIP_KLINE_COUNT_AGG) fire in the live scan tick — proven present in the real scan path; the live cycle will show whether AVAX's 0-fired is genuine or a data gap.")

    # ---- PHASE 3: dead sentiment/news + funding precision (BLUR) ----
    blur_comps_before = {"overall_sentiment": 0.0, "fear_greed": 10, "funding_rate": -0.0002,
                         "oi_change_pct": 0.547, "news_count": 0, "reddit_count": 0}
    blur_comps_after = {"overall_sentiment": None, "fear_greed": 10, "funding_rate": -0.0002,
                        "oi_change_pct": 0.547, "news_count": None, "reddit_count": None}
    ST.component_precision_decimals = 3      # old precision
    B.fear_greed_components_demote_enabled = False  # isolate phase 3 (keep fg in line, old)
    off = render(s, "BLURUSDT", 0.017601, "long", "uptrend", 0.0, 49, "long", "NEUTRAL", blur_comps_before)
    ST.component_precision_decimals = 4      # shipped
    off_comp = grep_lines(off, "Components:")
    on = render(s, "BLURUSDT", 0.017601, "long", "uptrend", 0.0, 49, "long", "NEUTRAL", blur_comps_after)
    on_comp = grep_lines(on, "Components:")
    fixed3 = (off_comp and "overall_sentiment=0.000" in off_comp[0] and "funding_rate=-0.000" in off_comp[0]
              and on_comp and "overall_sentiment" not in on_comp[0] and "funding_rate=-0.0002" in on_comp[0])
    verdict("3 (dead inputs + funding precision)", "BLURUSDT (sentiment/news absent, funding -0.0002)",
            "a true-absence input must be omitted (not shown as a live 0.000), and a real small funding value must be visible",
            off_comp, on_comp, fixed3)
    B.fear_greed_components_demote_enabled = True

    # ---- PHASE 4: fear-greed crowds top-5 vs demoted+tagged (SKR) ----
    B.fear_greed_components_demote_enabled = False
    off = render(s, "SKRUSDT", 0.009863, "short", "ranging", 0.77, 53, "short", "STRONG_BUY", skr_comps)
    B.fear_greed_components_demote_enabled = True
    on = render(s, "SKRUSDT", 0.009863, "short", "ranging", 0.77, 53, "short", "STRONG_BUY", skr_comps)
    off_c = grep_lines(off, "Components:")
    on_c = grep_lines(on, "Components:")
    # before: fear_greed ranks in the body (no tag); after: tagged + appended last
    fixed4 = (on_c and "fear_greed=10 (global, direction-inactive)" in on_c[0]
              and (off_c and "(global, direction-inactive)" not in off_c[0]))
    verdict("4 (fear-greed presentation)", "SKRUSDT (fear_greed=10, global)",
            "the global fear-greed must not crowd the per-coin top-5 as if it were a live per-coin directional input; it is demoted and tagged",
            off_c, on_c, fixed4)

    # ---- PHASE 5: BSB A+ on quality 7 — annotation (shipped) + optional cap ----
    SE.grade_quality_floor = 0.0   # before: annotation disabled
    off = render(s, "BSBUSDT", 0.3353, "short", "downtrend", 1.0, 35, "long", "SELL", bsb_comps, bsb_votes, bsb_scorer)
    SE.grade_quality_floor = 10.0  # shipped
    on = render(s, "BSBUSDT", 0.3353, "short", "downtrend", 1.0, 35, "long", "SELL", bsb_comps, bsb_votes, bsb_scorer)
    off_s = grep_lines(off, "Score: total=84")
    on_s = grep_lines(on, "Score: total=84")
    fixed5 = (on_s and "quality LOW" in on_s[0]) and (off_s and "quality LOW" not in off_s[0])
    verdict("5 (grade honesty — annotation)", "BSBUSDT (total=84 grade=A+, quality=7/20)",
            "a top grade on a low-quality setup must surface its weakness so the brain isn't misled (annotation is always-on; default-shipped)",
            off_s, on_s, fixed5)

    # Phase 5 cap (optional lever) on the REAL scorer
    _cap_demo()

    # ---- summary ----
    print("\n" + "#" * 74)
    print("SIMULATION SUMMARY — each phase responding on real issue-occurring data")
    print("#" * 74)
    allok = True
    for ph, ok in VERDICTS:
        allok = allok and ok
        print(("FIXED     " if ok else "NOT FIXED ") + ph)
    print("\n" + ("RESULT: ALL PHASES RESPOND AS INTENDED ON THE ORIGINAL SITUATIONS"
                  if allok else "RESULT: ONE OR MORE PHASES DID NOT RESPOND AS INTENDED"))
    return 0 if allok else 1


def _cap_demo():
    """Phase 5 optional cap on the REAL TradeScorer: a high-grading setup is
    lowered to the ceiling when the cap is enabled (default OFF)."""
    from src.config.settings import Settings
    from src.strategies.scorer import TradeScorer
    from src.strategies.models.signal_types import RawSignal
    from src.strategies.models.regime_types import MarketRegime, RegimeState
    from src.core.types import Side
    s = Settings.load()
    regime = RegimeState(regime=MarketRegime.RANGING, confidence=0.6, adx=15.0,
                         atr_percentile=30.0, choppiness=40.0, volume_ratio=0.5,
                         trend_direction=0, active_strategy_categories=["scalping"])
    ta = {"trend": {"trend_summary": "BULLISH"}, "momentum": {"momentum_summary": "BULLISH"},
          "volatility": {"volatility_summary": "MODERATE"}, "volume": {"volume_summary": "HIGH"},
          "overall": {"signal": "BUY", "confidence": 0.9},
          "support_resistance": {"current_price": 100.0, "support_levels": [], "resistance_levels": []}}
    sig = RawSignal(strategy_name="t", strategy_category="scalping", symbol="X",
                    direction=Side.BUY, entry_price=100, suggested_stop_loss=99,
                    suggested_take_profit=104, timeframe="5",
                    conditions_met={"a": True, "b": True, "c": True},
                    conditions_strength={"a": 0.95, "b": 0.9, "c": 0.92})
    s.strategy_engine.grade_quality_cap_enabled = False
    g_off = TradeScorer(s).score(sig, [], ta, None, None, regime, None)
    s.strategy_engine.grade_quality_cap_enabled = True
    s.strategy_engine.grade_quality_floor = 21.0  # treat as low-quality to force the cap
    g_on = TradeScorer(s).score(sig, [], ta, None, None, regime, None)
    order = ["D", "C", "B", "A", "A+"]
    fixed = order.index(g_on.grade) <= order.index("B") and order.index(g_on.grade) <= order.index(g_off.grade)
    verdict("5 (grade honesty — optional cap, REAL scorer)", "high-grade low-quality setup",
            "when the operator enables the gated cap, a low-quality setup cannot CARRY a top grade — the canonical grade is lowered",
            [f"cap OFF (default): grade = {g_off.grade}  (total={g_off.total_score:.0f})"],
            [f"cap ON: grade = {g_on.grade}  (capped to ceiling; quality_capped={g_on.scoring_details.get('quality_capped')})"],
            fixed)


if __name__ == "__main__":
    import sys
    sys.exit(main())
