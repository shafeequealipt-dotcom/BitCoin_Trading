"""Phase 2 of the 1D briefing rewrite — vote distribution cache shape.

Single question this test answers: "After ``EnsembleResult.vote_distribution_dict``
is called, does the cache shape match the contract documented at
``layer_manager.get_strategy_votes`` so the Phase 4 ranker can read it
without surprises?"

Verifies (one fixture, two assertions):

1. ``EnsembleResult.vote_distribution_dict()`` produces ``{strategy_name:
   {vote, confidence, weight, reasoning}}`` with reasoning truncated to
   the configured cap.
2. ``StrategyWorker._build_per_coin_votes`` selects the highest
   ``total_score`` setup per symbol and exposes the aggregate fields the
   accessor contract requires (``buy_weighted``, ``sell_weighted``,
   ``neutral_weighted``, ``consensus``, ``consensus_direction``,
   ``size_multiplier``, ``last_updated``).

No DB, no sleep. Pure construction + introspection.
"""

from datetime import datetime, timezone

from src.core.types import Side
from src.strategies.models.signal_types import (
    EnsembleResult,
    EnsembleVote,
    RawSignal,
    ScoredSetup,
)


def _scored_setup(symbol: str, total_score: float) -> ScoredSetup:
    raw = RawSignal(
        strategy_name="origin_strategy",
        strategy_category="momentum",
        symbol=symbol,
        direction=Side.BUY,
        entry_price=1.0,
        suggested_stop_loss=0.95,
        suggested_take_profit=1.10,
        timeframe="5m",
        created_at=datetime.now(timezone.utc),
    )
    return ScoredSetup(
        raw_signal=raw,
        base_score=20.0,
        confluence_score=20.0,
        context_score=20.0,
        quality_score=20.0,
        total_score=total_score,
        grade="A",
        scoring_details={"setup_type_confidence": 0.85},
    )


def test_vote_distribution_dict_shape_and_truncation() -> None:
    """EnsembleResult.vote_distribution_dict() returns the expected shape."""
    votes = [
        EnsembleVote(
            strategy_name="a3_bb_squeeze_scalp",
            vote="BUY",
            confidence=0.85,
            weight=1.0,
            reasoning="bullish " * 30,  # 240 chars — must be truncated
        ),
        EnsembleVote(
            strategy_name="m2_momentum",
            vote="BUY",
            confidence=0.72,
            weight=0.9,
            reasoning="momentum aligned",
        ),
        EnsembleVote(
            strategy_name="f1_funding_fade",
            vote="SELL",
            confidence=0.55,
            weight=0.6,
            reasoning="funding extreme",
        ),
    ]
    setup = _scored_setup("BTCUSDT", total_score=85.0)
    result = EnsembleResult(
        scored_setup=setup,
        votes=votes,
        buy_votes=1.642,  # 0.85*1.0 + 0.72*0.9
        sell_votes=0.33,
        neutral_votes=0.0,
        consensus_strength="STRONG",
        consensus_direction="BUY",
        passed=True,
        size_multiplier=1.0,
    )

    dist = result.vote_distribution_dict()
    # All three voting strategies present.
    assert set(dist.keys()) == {
        "a3_bb_squeeze_scalp", "m2_momentum", "f1_funding_fade",
    }
    # Inner shape contract.
    inner = dist["a3_bb_squeeze_scalp"]
    assert set(inner.keys()) == {"vote", "confidence", "weight", "reasoning"}
    assert inner["vote"] == "BUY"
    assert inner["confidence"] == 0.85
    assert inner["weight"] == 1.0
    # Truncation policy: long reasoning capped at 140 chars.
    assert len(inner["reasoning"]) == 140
    # Untruncated reasoning passes through unchanged.
    assert dist["m2_momentum"]["reasoning"] == "momentum aligned"


def test_build_per_coin_votes_selects_highest_score_and_aggregates() -> None:
    """StrategyWorker._build_per_coin_votes picks the strongest setup per coin.

    Constructs two EnsembleResult wrappers for the same symbol with
    different total_scores; verifies the higher-score wrapper's votes
    populate the cache and the aggregate fields match.
    """
    # Lazy import to avoid the heavy strategy_worker module at collect-time.
    from src.workers.strategy_worker import StrategyWorker

    weaker_setup = _scored_setup("ETHUSDT", total_score=50.0)
    stronger_setup = _scored_setup("ETHUSDT", total_score=85.0)

    weak_votes = [
        EnsembleVote(
            strategy_name="weak_voter", vote="BUY",
            confidence=0.4, weight=0.5, reasoning="weak",
        ),
    ]
    strong_votes = [
        EnsembleVote(
            strategy_name="strong_voter_a", vote="BUY",
            confidence=0.85, weight=1.0, reasoning="strong A",
        ),
        EnsembleVote(
            strategy_name="strong_voter_b", vote="SELL",
            confidence=0.55, weight=0.6, reasoning="strong B",
        ),
    ]
    weaker = EnsembleResult(
        scored_setup=weaker_setup, votes=weak_votes,
        buy_votes=0.2, sell_votes=0.0, neutral_votes=0.0,
        consensus_strength="WEAK", consensus_direction="BUY",
        size_multiplier=0.30,
    )
    stronger = EnsembleResult(
        scored_setup=stronger_setup, votes=strong_votes,
        buy_votes=0.85, sell_votes=0.33, neutral_votes=0.0,
        consensus_strength="GOOD", consensus_direction="BUY",
        size_multiplier=0.75,
    )

    # Call the bound method directly — no need to fully construct a worker.
    out = StrategyWorker._build_per_coin_votes(
        StrategyWorker.__new__(StrategyWorker),  # bypass __init__
        [weaker, stronger],
    )
    assert "ETHUSDT" in out
    entry = out["ETHUSDT"]
    # Highest total_score (85.0 — stronger) wins; weaker's votes discarded.
    assert set(entry["votes"].keys()) == {"strong_voter_a", "strong_voter_b"}
    # Aggregate fields populated from the winning wrapper.
    assert entry["consensus"] == "GOOD"
    assert entry["consensus_direction"] == "BUY"
    assert entry["buy_weighted"] == 0.85
    assert entry["sell_weighted"] == 0.33
    assert entry["neutral_weighted"] == 0.0
    assert entry["size_multiplier"] == 0.75
    assert isinstance(entry["last_updated"], float)
    # Internal scoring seed must be stripped before publishing.
    assert "_score_seed" not in entry
