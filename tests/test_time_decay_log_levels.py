"""Observability G11 — TIME_DECAY noise reduction (WARNING → INFO).

Phase 0 baseline showed TIME_DECAY events dominated the WARNING tier:

  TIME_DECAY_MAE_MONOTONIC_HOLD  296/1.5h at WARNING
  TIME_DECAY_MAE_GUARD           254/1.5h at WARNING
  TIME_DECAY_AGE_GUARD           100/1.5h at WARNING

All three are normal-operation gate decisions (position too young, MAE
hasn't reached threshold, regression attempt rejected by monotonic
hold). None are exceptional conditions that warrant WARNING-tier tail.

G11 downgrades all three to INFO. The events still fire — only the
severity classification changes — so the WARNING tail surfaces real
warnings while the INFO tier carries full decay diagnostics.

This suite pins the level at INFO so a future refactor that re-raises
the level (or removes the event) fails the test.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.risk.time_decay_sl import TimeDecaySLCalculator


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def test_time_decay_age_guard_emits_at_info(loguru_sink) -> None:
    """TIME_DECAY_AGE_GUARD must emit at INFO, not WARNING."""
    # Read the source and grep for the literal log.info / log.warning
    # surrounding the AGE_GUARD tag. This pins the level at the source
    # without needing to spin up the full TimeDecaySLCalculator pipeline.
    src = open("src/risk/time_decay_sl.py").read()
    # Find the AGE_GUARD emission block
    match = re.search(
        r"log\.(info|warning)\(\s*\n\s*f\"TIME_DECAY_AGE_GUARD",
        src,
    )
    assert match is not None, "AGE_GUARD emission not found"
    assert match.group(1) == "info", (
        f"TIME_DECAY_AGE_GUARD must emit at INFO post-G11, got {match.group(1)}"
    )


def test_time_decay_mae_guard_emits_at_info() -> None:
    """TIME_DECAY_MAE_GUARD must emit at INFO, not WARNING."""
    src = open("src/risk/time_decay_sl.py").read()
    match = re.search(
        r"log\.(info|warning)\(\s*\n\s*f\"TIME_DECAY_MAE_GUARD",
        src,
    )
    assert match is not None
    assert match.group(1) == "info", (
        f"TIME_DECAY_MAE_GUARD must emit at INFO post-G11, got {match.group(1)}"
    )


def test_time_decay_mae_monotonic_hold_emits_at_info() -> None:
    """TIME_DECAY_MAE_MONOTONIC_HOLD must emit at INFO, not WARNING."""
    src = open("src/risk/time_decay_sl.py").read()
    match = re.search(
        r"log\.(info|warning)\(\s*\n\s*f\"TIME_DECAY_MAE_MONOTONIC_HOLD",
        src,
    )
    assert match is not None
    assert match.group(1) == "info", (
        f"TIME_DECAY_MAE_MONOTONIC_HOLD must emit at INFO post-G11, got {match.group(1)}"
    )


def test_assign_mae_monotonic_still_blocks_regression() -> None:
    """The event downgrade must NOT change the hold logic.

    Regression attempts must still be rejected: when candidate > prior,
    the function returns False (no MAE update) and emits the log.
    Pre-G11 the level was WARNING; post-G11 it's INFO. Both must still
    cause the state.mae_pct to remain unchanged.
    """
    # Build a minimal TimeDecaySLCalculator instance — the helper is bound to
    # the instance but doesn't require the full service graph.
    cfg = SimpleNamespace(
        min_age_seconds=0.0,
        mae_to_sl_ratio_threshold=0.0,
    )
    tdsl: TimeDecaySLCalculator = TimeDecaySLCalculator.__new__(TimeDecaySLCalculator)
    tdsl.cfg = cfg

    state = SimpleNamespace(
        symbol="BTCUSDT",
        mae_pct=-1.5,        # current MAE (deeper / more negative)
        tick_count=10,
    )
    # Caller attempts candidate=-0.5 which is a regression (less adverse).
    result = tdsl._assign_mae_monotonic(state, candidate=-0.5, source="test")
    assert result is False, "regression must be rejected"
    assert state.mae_pct == -1.5, "mae_pct must NOT update on regression"


def test_assign_mae_monotonic_accepts_deeper_mae() -> None:
    """A deeper MAE (more negative) must still be accepted post-G11."""
    cfg = SimpleNamespace(min_age_seconds=0.0, mae_to_sl_ratio_threshold=0.0)
    tdsl: TimeDecaySLCalculator = TimeDecaySLCalculator.__new__(TimeDecaySLCalculator)
    tdsl.cfg = cfg

    state = SimpleNamespace(symbol="BTCUSDT", mae_pct=-1.0, tick_count=5)
    result = tdsl._assign_mae_monotonic(state, candidate=-2.0, source="test")
    assert result is True, "deeper MAE must be accepted"
    assert state.mae_pct == -2.0, "mae_pct must update to candidate"
