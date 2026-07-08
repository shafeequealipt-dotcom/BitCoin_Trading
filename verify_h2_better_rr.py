"""H2 self-verification — let the data choose the better-reward side or skip.

Drives the live rich-block renderer with the observed ALGO case (rr_long=0.31 vs
rr_short=1.95, short ~6x better) and asserts:

  - The brain now SEES both directions' risk-reward in the live prompt
    ("RR by direction: long=0.31 short=1.95 better=SHORT (...x)").
  - The inline instruction tells it to take the better side or SKIP, and that a
    side with no trade history is still tradeable.
  - The systemic RISK-REWARD CHECK framing is present in BOTH trade prompts.
  - No flip switch or structural-suppression veto is enabled by this change
    (direction stays the brain's decision at prompt time).

Run: .venv/bin/python verify_h2_better_rr.py
"""

from __future__ import annotations

from types import SimpleNamespace

from src.brain.strategist import (
    ClaudeStrategist,
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
)
from src.core.coin_package import StrategiesBlock, SignalsBlock, AltDataBlock

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name, ok, detail):
    results.append((name, PASS if ok else FAIL, detail))


class _StructureCache:
    def get(self, symbol):
        return SimpleNamespace(
            setup_quality="B",
            market_structure=SimpleNamespace(structure="bearish"),
            position_in_range=0.55, smc_confluence=2,
            nearest_fvg=None, nearest_ob=None, active_sweep_signal=None,
            mtf_confluence=None, volume_profile=None, session_context=None,
            # the H2 source: both-direction risk-reward (ALGO case)
            structural_placement=SimpleNamespace(
                rr_long=0.31, rr_short=1.95,
                is_long_invalid=False, is_short_invalid=False,
            ),
        )


def _pkg():
    return SimpleNamespace(
        symbol="ALGOUSDT",
        opportunity_score=10.0, interestingness_score=10.0,
        qualified=True, open_position=None, state_label=None,
        xray=SimpleNamespace(
            setup_type="TREND_PULLBACK_LONG", trade_direction="Buy",
            setup_type_confidence=0.45, setup_score=50.0,
            structural_levels=SimpleNamespace(
                suggested_sl=0.90, suggested_tp=1.10, rr_ratio=0.31,
            ),
        ),
        signals=SignalsBlock(confidence=0.4, direction="neutral"),
        alt_data=AltDataBlock(),
        price_data=SimpleNamespace(regime="trending_up"),
        strategies=StrategiesBlock(fired_count=3, ensemble_consensus="LEAN", total_score=10.0),
        blockers_observed=[], missing_fields=[], stale_fields=[],
        completeness=1.0, qualification_reasons=[],
    )


def main() -> int:
    settings = SimpleNamespace(
        brain=SimpleNamespace(surface_briefing_fields=False),
        scanner=None, stage2=None,
        strategy_engine=SimpleNamespace(
            brain_prompt_l4_consensus_context_enabled=False),
    )
    services = {
        "structure_cache": _StructureCache(),
        "regime_detector": None, "signal_worker": None, "layer_manager": None,
    }
    strat = ClaudeStrategist(claude_client=None, services=services, settings=settings)
    out = strat._format_packages_for_prompt_full({"ALGOUSDT": _pkg()})

    check("H2 both-direction RR surfaced in live prompt",
          "RR by direction: long=0.31 short=1.95 better=SHORT" in out,
          f"present={'RR by direction: long=0.31 short=1.95 better=SHORT' in out}")
    check("H2 better-side-or-skip instruction present",
          "take the better-reward side or SKIP" in out,
          "inline take-better-or-skip instruction rendered")
    check("H2 no-history-not-a-reason clause present",
          "lack of trade history on a side is NOT a reason to avoid it" in out,
          "no-history clause rendered")
    check("H2 systemic RR check in live prompt",
          "RISK-REWARD CHECK (both sides)" in TRADE_SYSTEM_PROMPT_ZERO_TWO,
          "RISK-REWARD CHECK present in ZERO_TWO")
    check("H2 systemic RR check in fallback prompt",
          "RISK-REWARD CHECK (both sides)" in TRADE_SYSTEM_PROMPT,
          "RISK-REWARD CHECK present in fallback")
    check("H2 does not mention enabling a flip/suppression switch",
          "xray_dir_flip" not in out and "apex_dir_flip" not in out
          and "suppression_enabled" not in out,
          "no flip/suppression switch referenced in rendered prompt")

    print("\nH2 BETTER-REWARD DIRECTION — SELF-VERIFICATION\n")
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
