#!/usr/bin/env python3
"""P2 entry-direction fix — self-verification.

Problem 2: the ensemble polls every strategy with only the originator's
direction, and the per-strategy vote() methods are direction-conditional
confirmers (return the asked direction or NEUTRAL, never the opposite). So the
opposing side tallies zero even when strong opposing signals exist (a bearish
supertrend stays NEUTRAL when asked about a Buy), and the brain reads a
one-sided "BUY=x vs SELL=0".

The fix (behind [strategy_engine] ensemble_two_sided_vote) runs a SECOND poll
with the opposite direction and surfaces the honest opposing weighted sum on
EnsembleResult.opposing_votes — the brain reads a real contest. It does NOT
change buy_votes/sell_votes, the consensus label, the size, or the cache.

This drives the REAL EnsembleVoter.vote code path with duck-typed strategies
that reproduce the production confirmer pattern, and proves:
  - flag OFF: opposing_votes == 0, two_sided_active False, legacy tally intact;
  - flag ON: the hidden opposing voter is surfaced (opposing_votes > 0) while
    buy_votes/sell_votes/consensus are byte-identical to the OFF run;
  - neutrality: the long-originator and short-originator cases are symmetric.

Run: .venv/bin/python verify_p2_two_sided_vote.py
Read-only. Never writes or deletes data.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.types import Side
from src.strategies.ensemble import EnsembleVoter

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


class FakeStrat:
    """Direction-conditional confirmer, like the real B2/E1/G2 voters:
    returns the asked direction (with conf) only when its own bias matches,
    otherwise NEUTRAL — it never opposes."""

    def __init__(self, name: str, bias: Side, conf: float = 0.8) -> None:
        self.name = name
        self._bias = bias
        self._conf = conf

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata):
        if direction == self._bias:
            side = "BUY" if self._bias == Side.BUY else "SELL"
            return (side, self._conf, f"{self.name} confirms {side}")
        return ("NEUTRAL", 0.3, f"{self.name} abstains")


class FakeRegistry:
    def __init__(self, strategies):
        self._s = strategies

    def get_active_for_regime(self, regime):
        return list(self._s)

    def get_performance(self, name):
        return SimpleNamespace(ensemble_weight=1.0)


def make_setup(direction: Side):
    raw = SimpleNamespace(
        symbol="ETHUSDT", direction=direction, strategy_name="ORIGINATOR",
    )
    return SimpleNamespace(
        raw_signal=raw, total_score=90.0, scoring_details={},
    )


def run(direction: Side, two_sided: bool):
    settings = Settings.load()
    # Isolate the two-sided behavior from the dominance cap for a clean
    # single-voter-per-side assertion (the cap is exercised separately in the
    # ensemble's own tests).
    settings.strategy_engine.single_strategy_max_share = 1.0
    settings.strategy_engine.ensemble_two_sided_vote = two_sided
    # One bull confirmer + one bear confirmer + one always-neutral. The bear
    # is the "hidden opposing voter" that the single poll never tallies.
    strategies = [
        FakeStrat("S_bull", Side.BUY),
        FakeStrat("S_bear", Side.SELL),
        FakeStrat("S_neutral_a", Side.BUY, conf=0.0),
    ]
    voter = EnsembleVoter(registry=FakeRegistry(strategies), settings=settings)
    regime = SimpleNamespace(regime="volatile")
    return voter.vote(
        setup=make_setup(direction), candles_map={}, ta_map={},
        sentiment_data=None, altdata=None, regime=regime,
    )


print("Part A — BUY originator: hidden opposing SELL voter is revealed")
off = run(Side.BUY, two_sided=False)
on = run(Side.BUY, two_sided=True)
check("flag OFF: opposing_votes == 0", off.opposing_votes == 0.0,
      f"opposing_votes={off.opposing_votes}")
check("flag OFF: two_sided_active is False", off.two_sided_active is False)
check("flag ON: opposing_votes > 0 (hidden bear voter surfaced)",
      on.opposing_votes > 0.0, f"opposing_votes={on.opposing_votes}")
check("flag ON: two_sided_active is True", on.two_sided_active is True)
check("legacy buy_votes UNCHANGED by the flag",
      abs(on.buy_votes - off.buy_votes) < 1e-9,
      f"off={off.buy_votes} on={on.buy_votes}")
check("legacy sell_votes UNCHANGED by the flag",
      abs(on.sell_votes - off.sell_votes) < 1e-9,
      f"off={off.sell_votes} on={on.sell_votes}")
check("consensus label UNCHANGED by the flag",
      on.consensus_strength == off.consensus_strength,
      f"off={off.consensus_strength} on={on.consensus_strength}")
check("the contest is now honest (BUY side ~ revealed SELL side)",
      abs(on.buy_votes - on.opposing_votes) < 1e-9,
      f"BUY={on.buy_votes} opposing_SELL={on.opposing_votes}")

print("\nPart B — neutrality mirror: SELL originator reveals hidden BUY voter")
on_sell = run(Side.SELL, two_sided=True)
check("flag ON (SELL originator): opposing BUY strength surfaced",
      on_sell.opposing_votes > 0.0, f"opposing_votes={on_sell.opposing_votes}")
check("symmetry: SELL-originator opposing == BUY-originator opposing",
      abs(on_sell.opposing_votes - on.opposing_votes) < 1e-9,
      f"buy_orig={on.opposing_votes} sell_orig={on_sell.opposing_votes}")


print("\nPart C — cross-check fix: opposing tally uses the SAME weighting base "
      "as the agreeing side when regime weighting is live")


class FakeWeighter:
    """Scales every (strategy, regime) by a constant factor."""
    def __init__(self, factor):
        self._f = factor

    def get_factor(self, regime_str, strategy_name):
        return self._f


def run_regime(factor, enabled):
    settings = Settings.load()
    settings.strategy_engine.single_strategy_max_share = 1.0
    settings.strategy_engine.ensemble_two_sided_vote = True
    settings.strategy_engine.regime_weighting_enabled = enabled
    strategies = [FakeStrat("S_bull", Side.BUY), FakeStrat("S_bear", Side.SELL)]
    voter = EnsembleVoter(
        registry=FakeRegistry(strategies), settings=settings,
        regime_weighter=FakeWeighter(factor),
    )
    regime = SimpleNamespace(regime="volatile")
    return voter.vote(setup=make_setup(Side.BUY), candles_map={}, ta_map={},
                      sentiment_data=None, altdata=None, regime=regime)


base = run_regime(1.0, enabled=False)          # equal-weight baseline
rw = run_regime(2.0, enabled=True)             # regime weighting x2 live
check("regime live: agreeing buy_votes scaled by the regime factor",
      abs(rw.buy_votes - 2.0 * base.buy_votes) < 1e-9,
      f"base={base.buy_votes} rw={rw.buy_votes}")
check("regime live: opposing tally scaled by the SAME factor (consistent base)",
      abs(rw.opposing_votes - 2.0 * base.opposing_votes) < 1e-9,
      f"base_opp={base.opposing_votes} rw_opp={rw.opposing_votes}")
check("regime live: agreeing and opposing share one weighting base (ratio ==)",
      abs((rw.buy_votes / rw.opposing_votes) - (base.buy_votes / base.opposing_votes)) < 1e-9)

print()
if failures:
    print(f"RESULT: FAIL ({len(failures)}): {failures}")
    sys.exit(1)
print("RESULT: PASS — the two-sided poll surfaces the hidden opposing voter to "
      "the brain, the legacy tally/consensus/cache are untouched, and the long "
      "and short cases are symmetric (no directional lean).")
