"""Self-verification for E25 — single fresh per-cycle scoring regime to the brain.

The scorer scored each coin under a fresh per-cycle regime; the brain rendered a
regime label re-read from the detector cache, which could have drifted — so the
label and the votes beside it could disagree. E25 tags the scoring regime onto
the per-coin consensus, carries it on the package, and the brain renders it.

Confirms:
  A. STATIC: StrategiesBlock.scoring_regime exists; the scorer threads
     coin_regimes/global_regime and writes scoring_regime; the scanner carries
     it; the brain prefers it (`_reg_str = _score_reg or _cache_reg_str`) and
     logs E25_REGIME_SNAPSHOT.
  B. REAL SCORER: _build_per_coin_consensus tags each coin with the regime it
     was scored under (per-coin override; global fallback) using the same
     snapshot the scoring loop used.
  C. CARRY: a CoinPackage round-trips scoring_regime on its StrategiesBlock.

Read-only / in-memory.
"""

from types import SimpleNamespace


def static_check():
    cp = open("src/core/coin_package.py").read()
    sw = open("src/workers/strategy_worker.py").read()
    sc = open("src/workers/scanner_worker.py").read()
    st = open("src/brain/strategist.py").read()
    return {
        "StrategiesBlock.scoring_regime field": "scoring_regime: str = \"\"" in cp,
        "scorer threads coin_regimes + writes scoring_regime":
            "coin_regimes=coin_regimes, global_regime=regime" in sw
            and '"scoring_regime": _scoring_regime' in sw,
        "scorer E25_SCORING_REGIME_TAGGED sentinel": "E25_SCORING_REGIME_TAGGED" in sw,
        "scanner carries scoring_regime onto package":
            'scoring_regime=str((consensus or {}).get("scoring_regime"' in sc,
        "brain prefers scoring regime over cache":
            "_reg_str = _score_reg or _cache_reg_str" in st,
        "brain E25_REGIME_SNAPSHOT sentinel": "E25_REGIME_SNAPSHOT" in st,
    }


def real_scorer_check():
    from src.workers.strategy_worker import StrategyWorker
    from src.strategies.models.regime_types import MarketRegime

    wk = StrategyWorker.__new__(StrategyWorker)  # method is self-independent

    def _sw(symbol, score):
        setup = SimpleNamespace(
            raw_signal=SimpleNamespace(symbol=symbol,
                                       direction=SimpleNamespace(value="Buy")),
            total_score=score,
        )
        return SimpleNamespace(scored_setup=setup, consensus_strength="STRONG",
                               size_multiplier=1.0, votes=[1, 2, 3])

    coin_regimes = {"BTCUSDT": SimpleNamespace(regime=MarketRegime.TRENDING_UP)}
    global_regime = SimpleNamespace(regime=MarketRegime.RANGING)

    out = wk._build_per_coin_consensus(
        [_sw("BTCUSDT", 10.0), _sw("ETHUSDT", 8.0)],  # ETH not in coin_regimes -> global
        coin_regimes=coin_regimes, global_regime=global_regime,
    )
    # BTC tagged with its per-coin override; ETH falls back to the global regime.
    return (out.get("BTCUSDT", {}).get("scoring_regime") == "trending_up"
            and out.get("ETHUSDT", {}).get("scoring_regime") == "ranging"), out


def carry_check():
    from src.core.coin_package import CoinPackage, StrategiesBlock
    pkg = CoinPackage(symbol="BTCUSDT", qualified=True, opportunity_score=0.5,
                      strategies=StrategiesBlock(ensemble_consensus="STRONG",
                                                 scoring_regime="trending_up"))
    return pkg.strategies.scoring_regime == "trending_up"


def main():
    s = static_check()
    scorer_ok, out = real_scorer_check()
    carry_ok = carry_check()

    print("E25 VERIFICATION — shared fresh scoring regime to the brain (completes #6/#9/#11)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  REAL SCORER tags scoring_regime (BTC per-coin=trending_up, ETH global=ranging): {scorer_ok}")
    print(f"    -> BTCUSDT={out.get('BTCUSDT', {}).get('scoring_regime')!r} "
          f"ETHUSDT={out.get('ETHUSDT', {}).get('scoring_regime')!r}")
    print(f"  CARRY round-trips on CoinPackage.strategies: {carry_ok}")

    ok = all(s.values()) and scorer_ok and carry_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
