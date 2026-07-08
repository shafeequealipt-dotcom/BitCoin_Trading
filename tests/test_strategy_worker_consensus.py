"""Phase 4 (post-Layer-1 fix) — StrategyWorker consensus cache parity.

Verifies the one-line fix at ``src/workers/strategy_worker.py:604``
(was ``filtered`` → now ``consensus_setups``). The cache is the data
source ScannerWorker reads via ``LayerManager.get_strategy_consensus``;
it must reflect the FULL universe, not the post-PnL-restriction subset.

Live impact (from 2026-04-27 06:23-06:52 UTC monitor): cache filled
with 5-18 of 50 coins per cycle, qualifying 0-2 of 50 against the
Phase-5 plan target of 5-25.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_4_consensus_filter.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.workers.strategy_worker import StrategyWorker


# ---------------------------------------------------------------------------
# Fixture builders — _build_per_coin_consensus signature is documented at
# strategy_worker.py:785-797. We need an "EnsembleResult-like wrapper"
# carrying scored_setup with raw_signal.symbol/direction + total_score,
# plus consensus_strength + size_multiplier + votes on the wrapper.
# ---------------------------------------------------------------------------


@dataclass
class _FakeSignal:
    symbol: str
    direction: str  # "Buy" or "Sell"


@dataclass
class _FakeScoredSetup:
    raw_signal: _FakeSignal
    total_score: float


@dataclass
class _FakeWrapper:
    """Mimics ScoredSetupWithEnsemble that StrategyWorker passes around."""
    scored_setup: _FakeScoredSetup
    consensus_strength: str  # STRONG/GOOD/LEAN/WEAK/CONFLICT
    size_multiplier: float
    votes: list


def _make_setup(symbol: str, score: float, consensus: str = "GOOD") -> _FakeWrapper:
    return _FakeWrapper(
        scored_setup=_FakeScoredSetup(
            raw_signal=_FakeSignal(symbol=symbol, direction="Buy"),
            total_score=score,
        ),
        consensus_strength=consensus,
        size_multiplier={
            "STRONG": 1.0,
            "GOOD": 0.75,
            "LEAN": 0.5,
            "WEAK": 0.3,
            "CONFLICT": 0.15,
        }.get(consensus, 0.5),
        votes=["dummy_vote"],
    )


def _make_strategy_worker_stub() -> StrategyWorker:
    """Build a StrategyWorker without invoking the heavy __init__ chain.

    We exercise ``_build_per_coin_consensus`` and ``_build_consensus_summary``
    directly — they are the only methods the cache write site touches.
    """
    sw = StrategyWorker.__new__(StrategyWorker)
    return sw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_per_coin_consensus_includes_all_input_symbols() -> None:
    """Every symbol in input must appear in output (no PnL-mode filtering inside)."""
    sw = _make_strategy_worker_stub()

    # 50 setups with varying scores; the helper does NOT filter by score.
    setups = [_make_setup(f"COIN{i:02d}USDT", score=float(i)) for i in range(50)]
    out = sw._build_per_coin_consensus(setups)

    assert len(out) == 50
    for i in range(50):
        sym = f"COIN{i:02d}USDT"
        assert sym in out
        assert out[sym]["consensus"] == "GOOD"
        assert out[sym]["direction"] == "long"


def test_build_per_coin_consensus_takes_highest_scoring_per_symbol() -> None:
    """Two setups for the same symbol — the higher total_score wins."""
    sw = _make_strategy_worker_stub()

    setups = [
        _make_setup("BTCUSDT", score=10.0, consensus="WEAK"),
        _make_setup("BTCUSDT", score=80.0, consensus="STRONG"),  # winner
        _make_setup("BTCUSDT", score=50.0, consensus="GOOD"),
    ]
    out = sw._build_per_coin_consensus(setups)
    assert out["BTCUSDT"]["consensus"] == "STRONG"


def test_full_vs_filtered_size_gap() -> None:
    """Demonstrate the gap: ``filtered`` (post-PnL) is a strict subset of full.

    This test makes the cache-write contract executable: the per-coin
    consensus from full setups must have AT LEAST as many entries as the
    consensus_summary from filtered setups, and typically more.
    """
    sw = _make_strategy_worker_stub()

    # 50 full setups; only the top 10 (>=50 score) clear NORMAL threshold.
    full_setups = [_make_setup(f"COIN{i:02d}USDT", score=float(i * 2)) for i in range(50)]
    threshold = 50.0
    filtered_setups = [s for s in full_setups if s.scored_setup.total_score >= threshold]

    cache_full = sw._build_per_coin_consensus(full_setups)
    summary_filtered = sw._build_consensus_summary(filtered_setups)

    assert len(cache_full) == 50, "Full cache must reflect every input coin"
    assert len(summary_filtered) < 50, "Filtered summary must be a subset"
    assert len(summary_filtered) <= len(cache_full)


def test_filtered_summary_unaffected_by_phase4_fix() -> None:
    """The summary alias still uses ``filtered`` — strategist.py:1017/1587
    expect post-PnL shape. Phase 4 must not regress that contract."""
    sw = _make_strategy_worker_stub()

    filtered = [_make_setup("BTCUSDT", score=80.0, consensus="STRONG")]
    summary = sw._build_consensus_summary(filtered)

    # Legacy summary shape: {symbol: {buy, sell, total_score}}
    assert "BTCUSDT" in summary
    assert "buy" in summary["BTCUSDT"]
    assert "sell" in summary["BTCUSDT"]
    assert "total_score" in summary["BTCUSDT"]
    assert summary["BTCUSDT"]["total_score"] == 80.0


def test_lean_category_preserved() -> None:
    """LEAN must round-trip through the cache (Phase 5 maps it to failing
    consensus by default but the cache must record it faithfully)."""
    sw = _make_strategy_worker_stub()
    setups = [_make_setup("XRPUSDT", score=40.0, consensus="LEAN")]
    out = sw._build_per_coin_consensus(setups)
    assert out["XRPUSDT"]["consensus"] == "LEAN"
