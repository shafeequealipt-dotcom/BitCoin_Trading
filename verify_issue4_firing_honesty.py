"""Verify Issue 4 — honest, consistent strategy-firing display (CALL_A exploit/fetch).

Read-only. Drives the REAL ClaudeStrategist._format_packages_for_prompt_full
(the live full-block path) with a 0-fired package and a fake layer_manager
whose get_strategy_votes returns a real two-sided lean, and asserts:

  1. When fired=0 but the two-sided poll leans (SELL=0.80, 27 voters), the
     prompt surfaces the lean ("two-sided strategy poll DID lean SELL=...") so
     the brain is NOT told the old misleading "genuine no-signal".
  2. When the votes read is STALE (older than consensus_freshness_seconds), the
     line carries the "weigh cautiously" staleness note.
  3. When there is NO poll lean at all (no votes entry), the message falls back
     to the honest "no scored setup AND no directional poll lean".

Also documents the live-log proof that the Layer funnel is lossless
(signals == scored == consensus), so 0-fired is genuinely "no scored setup",
not a silent drop.

No protected tables are touched; nothing is mutated.
"""
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, ".")

from src.brain.strategist import ClaudeStrategist
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    XrayBlock,
)


class _FakeStructureCache:
    def __init__(self, data):
        self._d = data

    def get(self, s):
        return self._d.get(s)


class _FakeSignalWorker:
    def get_signal(self, s):
        return None


class _FakeRegimeDetector:
    def get_coin_regime(self, s):
        return None


class _FakeLayerManager:
    def __init__(self, votes_entry):
        self._votes = votes_entry

    def get_strategy_votes(self, symbol):
        return self._votes

    def get_scorer_components(self, symbol):
        return None


def _structural(symbol):
    return SimpleNamespace(
        symbol=symbol,
        setup_quality="SKIP",
        position_in_range=0.00,
        smc_confluence=70,
        market_structure=SimpleNamespace(structure="downtrend"),
        nearest_fvg=SimpleNamespace(direction="bearish", midpoint=0.067),
        nearest_ob=SimpleNamespace(direction="bearish", midpoint=0.068),
        active_sweep_signal=None,
        mtf_confluence=SimpleNamespace(quality="good"),
        mtf_confluence_score=70,
        total_confluence_factors=3,
        volume_profile=SimpleNamespace(),
        poc_price=0.089,
        fib_key_level=0.082,
        session_context=SimpleNamespace(
            current_session="ny", session_phase="mid", manipulation_likely=False,
        ),
    )


def _strategist(votes_entry, freshness=360):
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = {
        "structure_cache": _FakeStructureCache({"HYPERUSDT": _structural("HYPERUSDT")}),
        "signal_worker": _FakeSignalWorker(),
        "regime_detector": _FakeRegimeDetector(),
        "layer_manager": _FakeLayerManager(votes_entry),
    }
    s.settings = SimpleNamespace(
        brain=SimpleNamespace(
            surface_briefing_fields=False,
            consensus_freshness_seconds=freshness,
        ),
        scanner=SimpleNamespace(
            briefing=SimpleNamespace(prompt_floor_interestingness=0.20),
        ),
    )
    return s


def _pkg():
    # The HYPER-like 0-fired coin: no scored setup reached the ensemble.
    return CoinPackage(
        symbol="HYPERUSDT",
        qualified=True,
        opportunity_score=0.47,
        qualification_reasons=["xray=bearish_structural_break"],
        price_data=PriceDataBlock(current=0.0672, change_24h_pct=-17.6, regime="trending_down"),
        xray=XrayBlock(
            setup_type="bearish_structural_break", setup_score=30,
            setup_type_confidence=0.70, trade_direction="short",
        ),
        strategies=StrategiesBlock(
            fired_count=0, ensemble_consensus="NONE", total_score=0.0,
        ),
        signals=SignalsBlock(confidence=0.35, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying", fear_greed=12),
        state_label=StateLabelBlock(primary="TREND_PULLBACK_SHORT", confidence=0.5),
    )


def _votes(sell_w, buy_w, voters, age_s):
    vd = {f"S{i}": {"vote": "SELL", "confidence": 0.6, "weight": 1.0} for i in range(voters)}
    return {
        "votes": vd,
        "buy_weighted": buy_w,
        "sell_weighted": sell_w,
        "opposing_weighted": buy_w,
        "two_sided": True,
        "consensus": "WEAK",
        "last_updated": time.time() - age_s,
    }


def main():
    failures = []

    # 1. Fresh lean is surfaced (not "genuine no-signal").
    s1 = _strategist(_votes(sell_w=0.80, buy_w=0.10, voters=27, age_s=5))
    out1 = s1._format_packages_for_prompt_full({"HYPERUSDT": _pkg()})
    ok1 = ("two-sided strategy poll DID lean SELL=0.80" in out1
           and "27 voters" in out1
           and "genuine no-signal" not in out1)
    print(f"1. fresh poll lean surfaced: {'PASS' if ok1 else 'FAIL'}")
    if not ok1:
        failures.append("fresh lean not surfaced")
        print("   ---- relevant lines ----")
        for ln in out1.splitlines():
            if "Strategies:" in ln or "poll" in ln or "no-signal" in ln:
                print("   " + ln.strip())

    # 2. Stale lean carries the cautiously note.
    s2 = _strategist(_votes(sell_w=0.80, buy_w=0.10, voters=27, age_s=1000), freshness=360)
    out2 = s2._format_packages_for_prompt_full({"HYPERUSDT": _pkg()})
    ok2 = "weigh cautiously" in out2 and "old" in out2
    print(f"2. stale read flagged: {'PASS' if ok2 else 'FAIL'}")
    if not ok2:
        failures.append("stale note missing")

    # 3. No poll lean at all -> honest fallback (no fabricated lean).
    s3 = _strategist(None)
    out3 = s3._format_packages_for_prompt_full({"HYPERUSDT": _pkg()})
    ok3 = ("no scored setup AND no directional poll lean" in out3
           and "DID lean" not in out3)
    print(f"3. no-lean honest fallback: {'PASS' if ok3 else 'FAIL'}")
    if not ok3:
        failures.append("no-lean fallback wrong")

    print()
    if failures:
        print(f"ISSUE 4 VERIFY: FAIL — {failures}")
        sys.exit(1)
    print("ISSUE 4 VERIFY: PASS — 0-fired lines surface the genuine two-sided "
          "poll lean, flag staleness, and fall back honestly (3/3). Live logs "
          "separately confirm signals==scored==consensus (lossless funnel).")


if __name__ == "__main__":
    main()
