"""End-to-end functional verification of the 5 candidate-block integrity fixes.

Renders SKR-like and BSB-like candidate blocks through the REAL Settings
(Settings.load(), so the actual config flags/defaults drive the behavior) and
the production ClaudeStrategist._format_packages_for_prompt_full path, then
asserts every fix's observable marker is present. Read-only; writes/deletes no
data. Run: .venv/bin/python verify_candidate_block_integrity_e2e.py
"""

from types import SimpleNamespace

from src.brain.strategist import ClaudeStrategist
from src.config.settings import Settings
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)
from src.core.types import Signal, SignalType


class _FakeStructureCache:
    def __init__(self, data):
        self._data = data

    def get(self, symbol):
        return self._data.get(symbol)


class _FakeSignalWorker:
    def __init__(self, sig_map):
        self._m = sig_map

    def get_signal(self, symbol):
        return self._m.get(symbol)


class _FakeRegimeDetector:
    def __init__(self, reg_map):
        self._m = reg_map

    def get_coin_regime(self, symbol):
        return self._m.get(symbol)


class _FakeLayerManager:
    def __init__(self, scorer, votes):
        self._scorer = scorer
        self._votes = votes

    def get_scorer_components(self, symbol):
        return self._scorer.get(symbol)

    def get_strategy_votes(self, symbol):
        return self._votes.get(symbol)


def _structural(symbol, direction, structure, range_pos, confl):
    return SimpleNamespace(
        symbol=symbol, setup_quality="A", position_in_range=range_pos,
        smc_confluence=confl,
        market_structure=SimpleNamespace(structure=structure),
        nearest_fvg=SimpleNamespace(direction="bearish", midpoint=0.0100),
        nearest_ob=SimpleNamespace(direction="bearish", midpoint=0.0101),
        active_sweep_signal=None,
        mtf_confluence=SimpleNamespace(quality="maximum"),
        mtf_confluence_score=8, total_confluence_factors=8,
        volume_profile=SimpleNamespace(), poc_price=0.0094, fib_key_level=0.0099,
        session_context=SimpleNamespace(
            current_session="new_york", session_phase="early",
            manipulation_likely=False,
        ),
    )


def _votes_entry(buy_w, sell_w, opp_w, two_sided, voters):
    return {
        "votes": {n: {"vote": v, "confidence": c, "weight": w, "reasoning": ""}
                  for (n, v, c, w) in voters},
        "buy_weighted": buy_w, "sell_weighted": sell_w,
        "opposing_weighted": opp_w, "two_sided": two_sided,
        "consensus": "WEAK",
        "consensus_direction": "BUY" if buy_w >= sell_w else "SELL",
        "size_multiplier": 1.0, "last_updated": 0.0,
    }


def _pkg(symbol, price, xray_dir, structure, range_pos, confl, signals_dir,
         total, grade, quality):
    p = CoinPackage(
        symbol=symbol, qualified=True, opportunity_score=0.6,
        qualification_reasons=["xray"],
        price_data=PriceDataBlock(current=price, change_24h_pct=2.6,
                                  regime="ranging"),
        xray=XrayBlock(
            setup_type="bearish_fvg_ob", setup_score=int(total),
            setup_type_confidence=0.40, trade_direction=xray_dir,
            structural_levels=StructuralLevels(
                suggested_sl=price * 1.02, suggested_tp=price * 0.95,
                rr_ratio=2.76),
        ),
        strategies=StrategiesBlock(
            fired_count=23, ensemble_consensus="WEAK", total_score=total,
            scoring_regime="ranging",
        ),
        signals=SignalsBlock(confidence=0.5, direction=signals_dir),
        alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying",
                              oi_change_24h_pct=10.56, fear_greed=10),
        state_label=StateLabelBlock(primary="RANGE_FADE_SHORT", confidence=0.6),
    )
    p.interestingness_score = 0.6
    p.interestingness_breakdown = {"confluence": 0.20, "cleanness": 0.15}
    p.state_cleanness = 0.46
    p.confluence_count = 2
    return p


def _strat(settings):
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.settings = settings
    return s


def main():
    settings = Settings.load()
    # The full votes block only renders when briefing fields are surfaced.
    settings.brain.surface_briefing_fields = True

    results = []

    # --- SKR-like: signal strong_buy (OI-driven) vs short structure (Issue 1a,
    #     3, 4). ---
    skr_sig = Signal(
        symbol="SKRUSDT", signal_type=SignalType.STRONG_BUY, confidence=0.69,
        source="intelligence_aggregator",
        components={
            "overall_sentiment": None,         # Issue 3: UNKNOWN -> absent
            "fear_greed": 10,                  # Issue 4: global, demoted+tagged
            "funding_rate": 0.0001,            # Issue 3: visible at 4dp
            "oi_change_pct": 10.565,           # the real driver
            "news_count": None, "reddit_count": None,
        },
    )
    skr = _strat(settings)
    skr.services = {
        "structure_cache": _FakeStructureCache(
            {"SKRUSDT": _structural("SKRUSDT", "short", "ranging", 0.77, 53)}),
        "signal_worker": _FakeSignalWorker({"SKRUSDT": skr_sig}),
        "regime_detector": _FakeRegimeDetector({"SKRUSDT": None}),
        "layer_manager": _FakeLayerManager(
            {"SKRUSDT": {"base": 38.0, "confluence": 15.0, "context": 5.0,
                         "quality": 12.8, "total": 70.8, "grade": "A"}},
            {"SKRUSDT": _votes_entry(0.0, 1.78, 0.99, True,
                                     [("A4_ema", "SELL", 0.65, 1.0)])}),
    }
    # SKR ensemble lean (pkg.signals.direction) is SHORT (votes sell) — it
    # AGREES with the short structure, so no 1b note here; SKR's contradiction
    # is the strong_buy SIGNAL (1a). Quality 12.8 (>= floor) -> no I5 note.
    skr_pkg = _pkg("SKRUSDT", 0.009863, "short", "ranging", 0.77, 53, "short",
                   70.8, "A", 12.8)
    skr_out = skr._format_packages_for_prompt_full({"SKRUSDT": skr_pkg})

    # --- BSB-like: votes/ensemble long vs downtrend short (Issue 1b), grade A+
    #     on quality 7 (Issue 5). ---
    bsb_sig = Signal(
        symbol="BSBUSDT", signal_type=SignalType.SELL, confidence=0.45,
        source="intelligence_aggregator",
        components={"overall_sentiment": None, "fear_greed": 10,
                    "funding_rate": 0.0001, "oi_change_pct": -2.095,
                    "news_count": None, "reddit_count": None},
    )
    bsb = _strat(settings)
    bsb.services = {
        "structure_cache": _FakeStructureCache(
            {"BSBUSDT": _structural("BSBUSDT", "short", "downtrend", 1.00, 35)}),
        "signal_worker": _FakeSignalWorker({"BSBUSDT": bsb_sig}),
        "regime_detector": _FakeRegimeDetector({"BSBUSDT": None}),
        "layer_manager": _FakeLayerManager(
            {"BSBUSDT": {"base": 37.0, "confluence": 20.0, "context": 20.0,
                         "quality": 7.0, "total": 84.0, "grade": "A+"}},
            {"BSBUSDT": _votes_entry(3.10, 0.0, 0.0, True,
                                     [("F2_multi_tf", "BUY", 0.85, 1.0)])}),
    }
    # ensemble lean shown in Consensus Context comes from pkg.signals.direction
    bsb_pkg = _pkg("BSBUSDT", 0.3353, "short", "downtrend", 1.00, 35, "long",
                   84.0, "A+", 7.0)
    bsb_out = bsb._format_packages_for_prompt_full({"BSBUSDT": bsb_pkg})

    print("=" * 70)
    print("SKR-LIKE RENDERED BLOCK")
    print("=" * 70)
    print(skr_out)
    print("=" * 70)
    print("BSB-LIKE RENDERED BLOCK")
    print("=" * 70)
    print(bsb_out)

    def check(name, cond):
        results.append((name, cond))

    # Issue 1a — signal-vs-structure note on SKR
    check("I1a signal-vs-xray NOTE present (SKR)",
          "this Signal is an independent" in skr_out
          and "CONFLICTS with the X-RAY structure (SHORT)" in skr_out)
    # Issue 1b — ensemble-vs-structure DISAGREEMENT on BSB (ensemble long vs short)
    check("I1b ensemble DISAGREEMENT line present (BSB)",
          "DISAGREEMENT" in bsb_out and "ensemble leans LONG" in bsb_out
          and "X-RAY structure is SHORT" in bsb_out)
    # Issue 1b — votes line relabeled + two-sided poll renders at opp=0 (BSB)
    check("I1b votes relabeled confirmed-direction tally (BSB)",
          "Votes (confirmed-direction tally):" in bsb_out)
    check("I1b two-sided poll renders with opp=0 (BSB)",
          "Two-sided poll:" in bsb_out
          and "the opposite side was polled and no" in bsb_out)
    check("I1b two-sided poll shows latent opposition (SKR opp=0.99)",
          "Two-sided poll:" in skr_out and "asked the OTHER" in skr_out)
    # Issue 3 — absent sentiment/news omitted, funding visible at 4dp (SKR)
    check("I3 overall_sentiment omitted (SKR)", "overall_sentiment" not in skr_out)
    check("I3 news_count omitted (SKR)", "news_count" not in skr_out)
    check("I3 funding visible at 4 decimals (SKR)", "funding_rate=0.0001" in skr_out)
    # Issue 4 — fear-greed demoted out of ranking and tagged (SKR)
    check("I4 fear-greed tagged global/direction-inactive (SKR)",
          "fear_greed=10 (global, direction-inactive)" in skr_out)
    check("I4 real per-coin component leads fear-greed in line (SKR)",
          ("oi_change_pct" in skr_out)
          and (skr_out.index("oi_change_pct") < skr_out.index("fear_greed=10 (global")))
    # Issue 5 — grade annotation on low quality (BSB quality 7 < floor 10)
    check("I5 quality-LOW annotation present (BSB A+ quality 7)",
          "quality LOW" in bsb_out
          and "driven by base/confluence/context" in bsb_out)
    # Issue 5 — cap is OFF by default: BSB grade still A+ (not capped)
    check("I5 cap OFF by default — grade unchanged A+ (BSB)",
          "grade=A+" in bsb_out)

    print("\n" + "=" * 70)
    print("MARKER CHECKS")
    print("=" * 70)
    allok = True
    for name, cond in results:
        allok = allok and cond
        print(("PASS " if cond else "FAIL ") + name)
    print("\nRESULT: " + ("ALL FIXES RENDER CORRECTLY" if allok
                           else "ONE OR MORE FIXES NOT RENDERING"))
    return 0 if allok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
