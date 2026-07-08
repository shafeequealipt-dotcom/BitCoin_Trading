"""Stale-entry preservation across ticks — Phase 3 Layer 1 restructure."""

from unittest.mock import MagicMock


def _wrapper(symbol: str, direction: str, score: float, consensus: str):
    raw = MagicMock()
    raw.symbol = symbol
    raw.direction = MagicMock()
    raw.direction.value = direction
    scored = MagicMock(raw_signal=raw, total_score=score)
    return MagicMock(
        scored_setup=scored,
        consensus_strength=consensus,
        size_multiplier=1.0,
        votes=[],
    )


def test_unprocessed_coin_preserved_across_ticks() -> None:
    """Coin processed in tick 1 but skipped in tick 2 keeps its entry.

    Mirrors the strategy_worker merge logic exactly — each tick
    `existing.update(new_consensus)` only touches the coins in
    `new_consensus`; coins absent from this tick keep their prior state.
    """
    from src.workers.strategy_worker import StrategyWorker

    w = StrategyWorker.__new__(StrategyWorker)

    # Tick 1: BTC processed
    tick1 = w._build_per_coin_consensus([_wrapper("BTCUSDT", "long", 90, "STRONG")])
    existing = {}
    existing.update(tick1)
    assert "BTCUSDT" in existing
    btc_ts1 = existing["BTCUSDT"]["last_updated"]

    # Tick 2: only ETH processed; BTC must remain
    tick2 = w._build_per_coin_consensus([_wrapper("ETHUSDT", "short", 80, "GOOD")])
    existing.update(tick2)
    assert "BTCUSDT" in existing  # preserved
    assert "ETHUSDT" in existing  # added
    assert existing["BTCUSDT"]["last_updated"] == btc_ts1  # not bumped

    # Tick 3: BTC reprocessed with new state — last_updated bumps
    import time
    time.sleep(0.01)  # ensure measurable diff
    tick3 = w._build_per_coin_consensus([_wrapper("BTCUSDT", "long", 95, "STRONG")])
    existing.update(tick3)
    assert existing["BTCUSDT"]["last_updated"] > btc_ts1
