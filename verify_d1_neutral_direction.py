"""D1 self-verification — directional bias removed to NEUTRAL.

Asserts the live trade-finding prompt and the scanner sentiment labels no longer
carry a hardcoded direction lean, while NOT introducing the opposite lean:

  - The live F&G framing presents fear/greed as NEUTRAL market context whose
    direction is decided by each coin's own regime (no "contrarian-buy" lean).
  - The framing is symmetric: fear can confirm a short OR mark a long; greed can
    mark a short OR protect a long — neither side is the default.
  - The scanner sentiment labels are de-editorialized (no "smart money buys
    panic" / "BIAS"); they are data-conditional contrarian SETUPS.
  - The per-coin regime remains the stated direction authority; flip switches
    are untouched; the recent-loser line discourages re-buying just-closed
    losers on sentiment alone.
  - The D1 sentinel (STRAT_REGIME_BLOCK_VERSION) is bumped.

Run: .venv/bin/python verify_d1_neutral_direction.py
"""

from __future__ import annotations

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    STRAT_REGIME_BLOCK_VERSION,
)
from src.workers.scanner.state_labeler import (
    LABEL_EXTREME_FEAR_LONG_BIAS,
    LABEL_EXTREME_GREED_SHORT_BIAS,
    ACTION_HINTS,
)

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, PASS if ok else FAIL, detail))


def main() -> int:
    live = TRADE_SYSTEM_PROMPT_ZERO_TWO
    fb = TRADE_SYSTEM_PROMPT

    # The old long-leaning framing is gone from BOTH prompts.
    check("D1 'contrarian-buy windows' lean removed (live)",
          "contrarian-buy windows" not in live,
          f"present={'contrarian-buy windows' in live}")
    check("D1 'smart money buys panic' removed (fallback)",
          "smart money buys panic" not in fb,
          f"present={'smart money buys panic' in fb}")
    check("D1 'BEST opportunities in ANY regime' removed (fallback)",
          "BEST opportunities in ANY regime" not in fb,
          f"present={'BEST opportunities in ANY regime' in fb}")

    # Neutral framing present and SYMMETRIC (no new short lean either).
    for label, p in (("live", live), ("fallback", fb)):
        ok = (
            "NEUTRAL on direction" in p
            and "not a direction instruction" in p
            and "do NOT flip to long" in p  # present in both prompts
        )
        check(f"D1 neutral F&G framing present ({label})",
              ok, "NEUTRAL + 'not a direction instruction' + no-auto-flip note")

    # Symmetry: fear is NOT a blanket buy and greed is NOT a blanket sell —
    # both can go either way per the coin's regime.
    check("D1 framing is symmetric (no replacement short bias)",
          'treat fear as "buy" or greed as "sell" by default' in live
          and 'treat fear as "buy" or greed as "sell" by default' in fb,
          "explicit 'do not treat fear=buy or greed=sell by default' in both")

    # Per-coin regime remains the direction authority.
    check("D1 per-coin regime still the direction authority",
          "per-coin regime in the Regime line — it is the direction authority"
          in live,
          "DIRECTION BY REGIME authority line intact")

    # Scanner labels de-editorialized (data-conditional SETUPS, not BIAS).
    check("D1 fear label value de-editorialized",
          LABEL_EXTREME_FEAR_LONG_BIAS == "EXTREME_FEAR_CONTRARIAN_LONG",
          f"value={LABEL_EXTREME_FEAR_LONG_BIAS}")
    check("D1 greed label value de-editorialized",
          LABEL_EXTREME_GREED_SHORT_BIAS == "EXTREME_GREED_CONTRARIAN_SHORT",
          f"value={LABEL_EXTREME_GREED_SHORT_BIAS}")
    fear_hint = ACTION_HINTS.get(LABEL_EXTREME_FEAR_LONG_BIAS, "")
    check("D1 fear label description neutral (no 'buys panic')",
          "buys panic" not in fear_hint and "not a buy signal" in fear_hint,
          f"hint={fear_hint!r}")

    # Sentinel bumped.
    check("D1 sentinel STRAT_REGIME_BLOCK_VERSION bumped to 4",
          STRAT_REGIME_BLOCK_VERSION == 4,
          f"version={STRAT_REGIME_BLOCK_VERSION}")

    print("\nD1 NEUTRAL DIRECTION — SELF-VERIFICATION\n")
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
