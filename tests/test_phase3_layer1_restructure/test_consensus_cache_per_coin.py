"""Tests for per-coin ensemble consensus cache — Layer 1 restructure Phase 3."""

from unittest.mock import MagicMock

import pytest

from src.workers.strategy_worker import StrategyWorker


def _make_setup_wrapper(symbol: str, direction: str, score: float, consensus: str = "STRONG", votes: int = 5, size_mult: float = 1.0):
    """Build an EnsembleResult-like wrapper exposing the attrs the cache reads."""
    raw = MagicMock()
    raw.symbol = symbol
    raw.direction = MagicMock()
    raw.direction.value = direction
    scored = MagicMock(raw_signal=raw, total_score=score)
    wrapper = MagicMock(
        scored_setup=scored,
        consensus_strength=consensus,
        size_multiplier=size_mult,
        votes=[MagicMock() for _ in range(votes)],
    )
    return wrapper


class TestBuildPerCoinConsensus:
    def _stub_worker(self) -> StrategyWorker:
        # We construct the bare class shell — no DI needed because
        # _build_per_coin_consensus is pure and reads no instance state.
        w = StrategyWorker.__new__(StrategyWorker)
        return w

    def test_emits_per_symbol_keys(self) -> None:
        w = self._stub_worker()
        setups = [
            _make_setup_wrapper("BTCUSDT", "long", 90, "STRONG"),
            _make_setup_wrapper("ETHUSDT", "short", 80, "GOOD"),
        ]
        out = w._build_per_coin_consensus(setups)
        assert "BTCUSDT" in out and "ETHUSDT" in out
        assert out["BTCUSDT"]["consensus"] == "STRONG"
        assert out["BTCUSDT"]["direction"] == "long"
        assert out["ETHUSDT"]["consensus"] == "GOOD"
        assert out["ETHUSDT"]["direction"] == "short"

    def test_strongest_setup_per_symbol_wins(self) -> None:
        w = self._stub_worker()
        setups = [
            _make_setup_wrapper("BTCUSDT", "long", 60, "WEAK"),
            _make_setup_wrapper("BTCUSDT", "long", 95, "STRONG"),  # higher score
            _make_setup_wrapper("BTCUSDT", "long", 75, "GOOD"),
        ]
        out = w._build_per_coin_consensus(setups)
        assert out["BTCUSDT"]["consensus"] == "STRONG"

    def test_neutral_direction_when_unknown(self) -> None:
        w = self._stub_worker()
        s = _make_setup_wrapper("XX", "weird", 50, "GOOD")
        out = w._build_per_coin_consensus([s])
        assert out["XX"]["direction"] == "neutral"

    def test_includes_required_keys(self) -> None:
        w = self._stub_worker()
        setups = [_make_setup_wrapper("BTCUSDT", "long", 90, "STRONG", votes=4, size_mult=1.0)]
        out = w._build_per_coin_consensus(setups)
        entry = out["BTCUSDT"]
        for key in ("consensus", "consensus_score", "vote_count", "direction", "last_updated"):
            assert key in entry
        # Internal scoring seed must be stripped before publish.
        assert "_score_seed" not in entry
        assert entry["vote_count"] == 4
        assert entry["consensus_score"] == 1.0

    def test_lean_category_preserved(self) -> None:
        w = self._stub_worker()
        s = _make_setup_wrapper("ABC", "long", 50, "LEAN", size_mult=0.5)
        out = w._build_per_coin_consensus([s])
        assert out["ABC"]["consensus"] == "LEAN"


class TestLayerManagerAccessor:
    def test_get_strategy_consensus_returns_entry(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._strategy_consensus = {"BTCUSDT": {"consensus": "STRONG"}}
        assert lm.get_strategy_consensus("BTCUSDT") == {"consensus": "STRONG"}

    def test_get_strategy_consensus_missing_returns_none(self) -> None:
        from src.core.layer_manager import LayerManager
        lm = LayerManager.__new__(LayerManager)
        lm._strategy_consensus = {}
        assert lm.get_strategy_consensus("XX") is None
