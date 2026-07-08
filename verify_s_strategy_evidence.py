"""S1-S5 self-verification — correct, complete, reconciled strategy evidence.

Drives the LIVE rich-block renderer (_format_packages_for_prompt_full, used when
[stage2].enable_full_layer_block=true) with synthetic packages and asserts:

  S1: a coin with 0 fired / ensemble NONE never renders a blank — it gets a
      truthful "genuine no-signal" line, or a "data gap" line when the package
      provenance shows incomplete strategy inputs.
  S5: a strong-ADX-on-thin-volume coin (INJ-style ADX=36 vol_ratio=0.03) gets a
      low-participation caveat; a healthy-volume trend does NOT.
  S2/S3: the STRATEGY HINTS framing is rebalanced (no blanket "often wrong") and
      reconciled (one-line hints do not override candidate ensembles).
  S4: the system prompt carries explicit Signal-vs-ensemble precedence guidance.

Run: .venv/bin/python verify_s_strategy_evidence.py   (in-memory stubs only)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.brain.strategist import ClaudeStrategist, TRADE_SYSTEM_PROMPT_ZERO_TWO
from src.core.coin_package import StrategiesBlock, SignalsBlock, AltDataBlock

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, PASS if ok else FAIL, detail))


def _pkg(symbol, *, fired, ens, blockers=None, score=10.0):
    return SimpleNamespace(
        symbol=symbol,
        opportunity_score=score,
        interestingness_score=score,
        qualified=True,
        open_position=None,
        state_label=None,
        xray=None,  # block 2 skips gracefully
        signals=SignalsBlock(confidence=0.38, direction="neutral"),
        alt_data=AltDataBlock(),
        price_data=SimpleNamespace(regime="ranging"),
        strategies=StrategiesBlock(
            fired_count=fired, ensemble_consensus=ens, total_score=score,
        ),
        blockers_observed=list(blockers or []),
        missing_fields=[],
        stale_fields=[],
        completeness=1.0,
        qualification_reasons=[],
    )


class _RegimeDetector:
    """get_coin_regime returns INJ as strong-ADX-on-thin-volume; others healthy."""
    def get_coin_regime(self, symbol: str):
        if symbol == "INJUSDT":
            adx, volr = 36.3, 0.03
        else:
            adx, volr = 20.0, 0.80
        return SimpleNamespace(
            regime=SimpleNamespace(value="trending_up"),
            confidence=0.73, adx=adx, atr_percentile=50.0, choppiness=40.0,
            volume_ratio=volr, trend_direction=1,
            active_strategy_categories=["momentum"],
        )


def main() -> int:
    settings = SimpleNamespace(
        brain=SimpleNamespace(surface_briefing_fields=False),
        scanner=None,
        stage2=None,
        strategy_engine=SimpleNamespace(
            brain_prompt_l4_consensus_context_enabled=True,
        ),
    )
    services = {
        "regime_detector": _RegimeDetector(),
        "signal_worker": None,
        "structure_cache": None,
        "layer_manager": None,
    }
    strat = ClaudeStrategist(claude_client=None, services=services, settings=settings)

    packages = {
        "ENAUSDT": _pkg("ENAUSDT", fired=0, ens="NONE"),  # genuine no-signal
        "SANDUSDT": _pkg("SANDUSDT", fired=0, ens="NONE",
                         blockers=["kline_stale"]),         # data gap
        "INJUSDT": _pkg("INJUSDT", fired=5, ens="GOOD"),    # S5 caveat
        "DYDXUSDT": _pkg("DYDXUSDT", fired=4, ens="GOOD"),  # healthy, no caveat
    }
    out = strat._format_packages_for_prompt_full(packages)

    # S1 — genuine no-signal labeled (not blank).
    check("S1 genuine no-signal labeled",
          "genuine no-signal" in out,
          f"'genuine no-signal' present={'genuine no-signal' in out}")

    # S1 — data gap labeled when provenance shows incomplete inputs.
    check("S1 data-gap labeled when inputs incomplete",
          "data gap, not a confirmed flat" in out,
          f"'data gap' present={'data gap, not a confirmed flat' in out}")

    # S1 — a coin that DID fire gets no empty-evidence line.
    # (the no-signal lines only attach to ENA/SAND, so exactly the two above)
    n_nosig = out.count("No strategy fired an entry") + out.count(
        "No strategy signal this cycle")
    check("S1 only the two empty coins are labeled",
          n_nosig == 2, f"empty-evidence lines={n_nosig} (expect 2)")

    # S5 — INJ strong-ADX-thin-volume caveat present exactly once.
    n_caveat = out.count("strong ADX")
    check("S5 thin-volume caveat fires for INJ only",
          n_caveat == 1, f"caveat count={n_caveat} (expect 1, INJ only)")

    # S2/S3 — framing rebalanced + reconciled (check the live module source).
    src = Path("src/brain/strategist.py").read_text()
    check("S2 blanket 'often wrong' framing removed",
          "often wrong" not in src,
          f"'often wrong' still present={'often wrong' in src}")
    check("S2/S3 neutral + reconciled hints header present",
          "do not blindly follow them and do not blindly dismiss them" in src
          and "do not override it" in src,
          "neutral 'follow/dismiss' + 'do not override' phrasing present")

    # S4 — Signal-vs-ensemble precedence guidance in the live system prompt.
    check("S4 Signal-vs-ensemble precedence guidance present",
          "Neither is automatically authoritative over the other"
          in TRADE_SYSTEM_PROMPT_ZERO_TWO,
          "RULE 9 precedence text present in ZERO_TWO prompt")

    print("\nS1-S5 STRATEGY EVIDENCE — SELF-VERIFICATION\n")
    n_pass = 0
    for name, status, detail in results:
        print(f"  [{status}] {name}")
        print(f"         {detail}")
        if status == PASS:
            n_pass += 1
    print(f"\n  {n_pass}/{len(results)} checks passed\n")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
