#!/usr/bin/env python3
"""Read-only verification for upstream brain-data fixes #2-#5 (2026-05-31).

Exercises real objects (no DB writes, no network) and asserts the post-fix
behaviour for each issue. Mirrors the project's verify_issue_*.py style.

    .venv/bin/python verify_upstream_issues_2_5.py
"""
from __future__ import annotations

from types import SimpleNamespace

_pass = 0
_fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    mark = "PASS" if cond else "FAIL"
    if cond:
        _pass += 1
    else:
        _fail += 1
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))


# ───────────────────────── #4 SMC kind->direction ─────────────────────────
def verify_issue_4() -> None:
    from src.analysis.structure.models.structure_types import FairValueGap, OrderBlock
    # Real dataclasses expose .direction, not .kind — the renderer must read it.
    fvg = FairValueGap(direction="bullish", midpoint=733.65)
    ob = OrderBlock(direction="bearish", midpoint=715.80)
    check("#4 FairValueGap has no 'kind'", not hasattr(fvg, "kind"))
    fvg_render = f"fvg={getattr(fvg, 'direction', None) or 'n/a'}@{fvg.midpoint}"
    ob_render = f"ob={getattr(ob, 'direction', None) or 'n/a'}@{ob.midpoint}"
    check("#4 fvg renders polarity (not n/a)", fvg_render == "fvg=bullish@733.65", fvg_render)
    check("#4 ob renders polarity (not n/a)", ob_render == "ob=bearish@715.8", ob_render)


# ───────────────────── #3B regime missing-volume ──────────────────────────
def verify_issue_3b() -> None:
    from src.strategies.models.regime_types import MarketRegime, RegimeState

    # missing volume serializes None + flag False, round-trips as unknown
    rs = RegimeState(
        regime=MarketRegime.RANGING, confidence=0.5, adx=15.0, atr_percentile=40.0,
        choppiness=45.0, volume_ratio=1.0, volume_ratio_known=False, trend_direction=0,
    )
    d = rs.to_dict()
    check("#3B missing volume serializes None", d["volume_ratio"] is None)
    check("#3B round-trips as unknown", RegimeState.from_dict(d).volume_ratio_known is False)
    # A dict carrying a real volume_ratio and NO explicit flag -> derived known.
    check("#3B present value (no explicit flag) is known",
          RegimeState.from_dict({"regime": "ranging", "volume_ratio": 0.8}).volume_ratio_known is True)
    check("#3B unknown() marks volume not known", RegimeState.unknown().volume_ratio_known is False)


# ─────────── #2 regime full consistency + #3A vol display (shared) ─────────
def verify_issue_2_and_3a() -> None:
    from src.brain.strategist import ClaudeStrategist
    from src.core.coin_package import (
        AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock, StateLabelBlock,
        StrategiesBlock, StructuralLevels, XrayBlock,
    )
    from src.strategies.models.regime_types import MarketRegime, RegimeState

    class _Cache:
        def __init__(self, m): self._m = m
        def get_scorer_components(self, s): return None
        def get(self, s): return self._m.get(s)

    class _Rd:
        def __init__(self, m): self._m = m
        def get_coin_regime(self, s): return self._m.get(s)

    class _Lm:
        def __init__(self, m): self._m = m
        def get_score_breakdown(self, s): return self._m.get(s)

    analysis = SimpleNamespace(
        symbol="ETHUSDT", setup_quality="C", position_in_range=0.96, smc_confluence=70,
        market_structure=SimpleNamespace(structure="uptrend"),
        nearest_fvg=SimpleNamespace(direction="bullish", midpoint=2029.99),
        nearest_ob=SimpleNamespace(direction="bullish", midpoint=2022.38),
        active_sweep_signal=None, mtf_confluence=SimpleNamespace(quality="good"),
        mtf_confluence_score=72, total_confluence_factors=4, volume_profile=SimpleNamespace(),
        poc_price=2016.1, fib_key_level=2024.16,
        session_context=SimpleNamespace(current_session="asian", session_phase="late",
                                        manipulation_likely=False),
    )
    # live cache says DEAD (drifted); scored under RANGING with its own metrics
    cache_rs = RegimeState(regime=MarketRegime.DEAD, confidence=0.80, adx=10.9,
                           atr_percentile=1.0, choppiness=38.0, volume_ratio=0.50,
                           trend_direction=0)
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = {"structure_cache": _Cache({"ETHUSDT": analysis}),
                  "signal_worker": SimpleNamespace(get_scorer_components=lambda x: None),
                  "regime_detector": _Rd({"ETHUSDT": cache_rs}),
                  "layer_manager": _Lm({})}
    s.settings = SimpleNamespace(
        brain=SimpleNamespace(surface_briefing_fields=False),
        scanner=SimpleNamespace(briefing=SimpleNamespace(prompt_floor_interestingness=0.20)),
    )

    def pkg(**strat):
        return CoinPackage(
            symbol="ETHUSDT", qualified=True, opportunity_score=0.4,
            qualification_reasons=["xray=bullish_fvg_ob"],
            price_data=PriceDataBlock(current=2027.99, change_24h_pct=0.7, regime="dead"),
            xray=XrayBlock(setup_type="bullish_fvg_ob", setup_score=49,
                           setup_type_confidence=0.70, trade_direction="long",
                           structural_levels=StructuralLevels(suggested_sl=2011.91,
                                                              suggested_tp=2039.32, rr_ratio=0.70)),
            strategies=StrategiesBlock(fired_count=23, ensemble_consensus="GOOD",
                                       total_score=79.8, **strat),
            signals=SignalsBlock(confidence=0.38, direction="long"),
            alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying",
                                  oi_change_24h_pct=3.9, fear_greed=28),
            state_label=StateLabelBlock(primary="TREND_PULLBACK_LONG", confidence=0.6),
        )

    out = s._format_packages_for_prompt_full({"ETHUSDT": pkg(
        scoring_regime="ranging", scoring_regime_confidence=0.55, scoring_regime_adx=22.0,
        scoring_regime_atr_percentile=30.0, scoring_regime_choppiness=42.0,
        scoring_regime_volume_ratio=0.062, scoring_regime_volume_ratio_known=True,
        scoring_regime_trend_direction=0)})
    check("#2 candidate Regime shows SCORING word", "Regime: ranging " in out)
    check("#2 shows SCORED metric ADX=22.0 (not cache 10.9)",
          "ADX=22.0" in out and "ADX=10.9" not in out)
    check("#2 drift note when live!=scored", "(live conditions now read dead)" in out)
    check("#3A vol_ratio at precision (0.062 not 0.00)",
          "vol_ratio=0.062" in out and "vol_ratio=0.00 " not in out)
    check("#4 SMC line shows fvg=bullish (live render path)", "fvg=bullish@" in out)

    out_missing = s._format_packages_for_prompt_full({"ETHUSDT": pkg(
        scoring_regime="ranging", scoring_regime_adx=15.0, scoring_regime_volume_ratio=1.0,
        scoring_regime_volume_ratio_known=False)})
    check("#3A missing volume renders n/a", "vol_ratio=n/a" in out_missing)


# ───────────────────────── #5 MTF higher-TF blend ─────────────────────────
def verify_issue_5() -> None:
    from src.analysis.structure.mtf_confluence import MTFConfluenceScorer
    from src.analysis.structure.models.structure_types import TFStructureView
    from src.config.settings import Settings, StructureSettings

    sc = MTFConfluenceScorer(StructureSettings())
    check("#5 flag default OFF", Settings.load().structure.mtf_multi_timeframe_enabled is False)
    check("#5 None views -> factor score unchanged (regression-safe)",
          sc._blend_higher_tf("long", 8, None) == (8, 0.0, [], {}))
    up = {"D": TFStructureView(timeframe="D", structure="uptrend", has_data=True),
          "240": TFStructureView(timeframe="240", structure="uptrend", has_data=True)}
    check("#5 full alignment lifts (8 -> 10)", sc._blend_higher_tf("long", 8, up)[0] == 10)
    dn = {"D": TFStructureView(timeframe="D", structure="downtrend", has_data=True),
          "240": TFStructureView(timeframe="240", structure="downtrend", has_data=True)}
    check("#5 full conflict cuts (8 -> 6)", sc._blend_higher_tf("long", 8, dn)[0] == 6)
    miss = {"D": TFStructureView(timeframe="D", has_data=False)}
    blended, agree, missing, _ = sc._blend_higher_tf("long", 7, miss)
    check("#5 missing HTF -> unchanged + marked", blended == 7 and "d1_data_missing" in missing)


if __name__ == "__main__":
    for fn in (verify_issue_4, verify_issue_3b, verify_issue_2_and_3a, verify_issue_5):
        print(f"\n=== {fn.__name__} ===")
        fn()
    print(f"\n{'='*40}\nTOTAL: {_pass} passed, {_fail} failed")
    raise SystemExit(1 if _fail else 0)
