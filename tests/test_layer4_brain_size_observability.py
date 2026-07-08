"""Layer 4 Commit 3 — per-trade brain sizing observability.

The Phase 4 trial needs to see whether the brain weighed the truthful
framing introduced in strategist._format_consensus_context. Each trade
must emit one L4_BRAIN_SIZE_DECISION log line carrying the brain's
chosen size, the live consensus context, and a reasoning excerpt.

These tests verify the log emit is wired into _execute_claude_trade
and that the format includes the fields the operator needs to group
by supporting_count bucket.
"""
from __future__ import annotations

import inspect


def test_l4_log_emit_is_in_execute_claude_trade() -> None:
    """The L4_BRAIN_SIZE_DECISION log emit must live inside the
    _execute_claude_trade flow so every brain-driven trade is captured.
    Refactor-resistant: locks the call site to the right function."""
    from src.workers.strategy_worker import StrategyWorker
    src = inspect.getsource(StrategyWorker._execute_claude_trade)
    assert "L4_BRAIN_SIZE_DECISION" in src, (
        "Layer 4 per-trade observability log must be emitted in "
        "_execute_claude_trade so the Phase 4 trial can audit "
        "brain-chosen size by consensus context"
    )


def test_l4_log_format_includes_required_fields() -> None:
    """The log line must carry every field the operator needs to
    group brain-chosen size by supporting_count bucket and verify
    the truthful framing is moving the brain's sizing."""
    from src.workers.strategy_worker import StrategyWorker
    src = inspect.getsource(StrategyWorker._execute_claude_trade)
    # Each required token must appear in the log construction
    required = (
        "sym=",
        "claude_size=",
        "final_size=",
        "consensus=",
        "supporting=",
        "opposing=",
        "regime=",
        "reasoning=",
    )
    for token in required:
        assert token in src, (
            f"L4_BRAIN_SIZE_DECISION log must include '{token}' so "
            f"the operator can bucket by consensus context"
        )


def test_l4_log_emit_is_non_fatal() -> None:
    """Per Rule 7 the observability must be loud-but-non-fatal: if
    the cache lookup or log formatting fails, the trade must still
    proceed. Lock the try/except wrapping."""
    from src.workers.strategy_worker import StrategyWorker
    src = inspect.getsource(StrategyWorker._execute_claude_trade)
    # The L4 block must be wrapped in try/except (the fail tag is the
    # marker that the wrapping exists at the right scope)
    assert "L4_BRAIN_SIZE_DECISION_FAIL" in src, (
        "L4 observability must wrap in try/except with a "
        "_FAIL tag at DEBUG so failure cannot crash the trade"
    )


def test_l4_log_emit_reads_from_ensemble_state_cache() -> None:
    """The supporting/opposing counts must come from the live
    EnsembleStateCache (the same source Layer 2 D6 register_trade
    uses). This guarantees the L4 log records the SAME consensus
    snapshot the brain actually saw."""
    from src.workers.strategy_worker import StrategyWorker
    src = inspect.getsource(StrategyWorker._execute_claude_trade)
    assert "ensemble_state_cache" in src, (
        "L4 observability must read from EnsembleStateCache so the "
        "supporting/opposing counts match what the brain's prompt saw"
    )
    assert "get_current_consensus" in src, (
        "L4 observability must call get_current_consensus to capture "
        "the live snapshot"
    )


def test_l4_log_emit_fires_before_order_placement() -> None:
    """The L4 log must be emitted BEFORE order placement so the
    observation captures the brain's intent even if the order
    subsequently fails. This is the same discipline the
    SIZE_DERIVATION event already follows."""
    from src.workers.strategy_worker import StrategyWorker
    src = inspect.getsource(StrategyWorker._execute_claude_trade)
    l4_pos = src.find("L4_BRAIN_SIZE_DECISION")
    # The XRAY-flip TP cap block follows the SIZE_DERIVATION region
    # and precedes order placement; the L4 log must sit between
    # SIZE_DERIVATION and XRAY-flip TP cap so it fires before any
    # downstream rejection path can drop the trade.
    xray_cap_pos = src.find("XRAY-flip TP cap")
    assert l4_pos > 0, "L4 log must be emitted in _execute_claude_trade"
    assert xray_cap_pos > 0, "XRAY-flip TP cap block must be present"
    assert l4_pos < xray_cap_pos, (
        "L4 log must fire BEFORE the XRAY-flip TP cap (i.e., before "
        "any downstream code that might drop the trade)"
    )
