"""Observability G9 — STRAT_CALL_B_CTX lessons_in_db field.

The audit's TIAS_BRIDGE concern was learning-loop closure visibility.
Investigation showed:

  - Write side: TIAS_LESSON_BRIDGED fires per close (thesis_manager.py:456)
  - Read side: intentionally DISABLED in active CALL_B
    (_build_position_prompt at strategist.py:3402). The prior
    STRAT_CALL_B_LESSONS_INJECTED emission at L1414 lives in the
    legacy _build_context_prompt which is not called from
    create_position_plan.

G9 surfaces the actual DB-side lesson count in STRAT_CALL_B_CTX so
operators can see "TIAS writes N lessons; CALL_B intentionally reads
0 of them" without rebuilding the prompt or grep-correlating across
files. Pairs cleanly with the existing tias_coaching_removed sentinel.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.brain.strategist import ClaudeStrategist


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


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _make_strategist(*, db_lessons: list[dict] | None = None) -> ClaudeStrategist:
    """Build a strategist whose thesis_mgr.get_recent_lessons returns ``db_lessons``."""
    thesis_mgr = MagicMock()
    thesis_mgr.get_recent_lessons = AsyncMock(return_value=db_lessons or [])
    thesis_mgr.get_open_theses = AsyncMock(return_value=[])
    thesis_mgr.get_aggregated_stats = AsyncMock(return_value={"count": 0})

    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=[])

    services: dict = {
        "thesis_manager": thesis_mgr,
        "position_service": position_service,
    }

    settings = SimpleNamespace(
        brain=SimpleNamespace(
            use_packages=False,
            surface_briefing_fields=False,
        ),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
        price=SimpleNamespace(divergence_block_prompt_pct=1.0),
    )

    claude_mock = MagicMock()
    claude_mock.send_message = AsyncMock(return_value='{"position_actions": {}}')

    return ClaudeStrategist(
        claude_client=claude_mock, services=services, settings=settings,
    )


@pytest.mark.asyncio
async def test_call_b_ctx_carries_lessons_in_db_zero(loguru_sink) -> None:
    """When DB has zero lessons, STRAT_CALL_B_CTX shows lessons_in_db=0."""
    strat = _make_strategist(db_lessons=[])
    await strat._build_position_prompt()

    events = _records_with_tag(loguru_sink, "STRAT_CALL_B_CTX")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv.get("lessons_in_db") == "0"
    # tias_coaching_removed sentinel still present
    assert kv.get("tias_coaching_removed") == "True"
    # recency_lessons_count still hardcoded 0 (intentional)
    assert kv.get("recency_lessons_count") == "0"


@pytest.mark.asyncio
async def test_call_b_ctx_carries_lessons_in_db_nonzero(loguru_sink) -> None:
    """When DB has N lessons but CALL_B doesn't inject, lessons_in_db=N
    + recency_lessons_count=0 makes the disabled-by-design state visible."""
    lessons = [
        {"symbol": "BTCUSDT", "direction": "long", "actual_pnl_pct": 1.5,
         "close_reason": "take_profit", "lesson": "Strong momentum"},
        {"symbol": "ETHUSDT", "direction": "short", "actual_pnl_pct": -0.8,
         "close_reason": "stop_hit", "lesson": "Avoid reversals"},
        {"symbol": "ADAUSDT", "direction": "long", "actual_pnl_pct": 2.0,
         "close_reason": "take_profit", "lesson": "Trend continuation"},
    ]
    strat = _make_strategist(db_lessons=lessons)
    await strat._build_position_prompt()

    events = _records_with_tag(loguru_sink, "STRAT_CALL_B_CTX")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv.get("lessons_in_db") == "3"
    assert kv.get("tias_coaching_removed") == "True"
    assert kv.get("recency_lessons_count") == "0"


@pytest.mark.asyncio
async def test_call_b_ctx_handles_thesis_mgr_failure_gracefully(loguru_sink) -> None:
    """If thesis_mgr.get_recent_lessons raises, lessons_in_db defaults to 0."""
    thesis_mgr = MagicMock()
    thesis_mgr.get_recent_lessons = AsyncMock(side_effect=RuntimeError("DB locked"))
    thesis_mgr.get_open_theses = AsyncMock(return_value=[])
    thesis_mgr.get_aggregated_stats = AsyncMock(return_value={"count": 0})
    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=[])
    settings = SimpleNamespace(
        brain=SimpleNamespace(use_packages=False, surface_briefing_fields=False),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
        price=SimpleNamespace(divergence_block_prompt_pct=1.0),
    )
    claude_mock = MagicMock()
    claude_mock.send_message = AsyncMock(return_value='{}')
    strat = ClaudeStrategist(
        claude_client=claude_mock,
        services={"thesis_manager": thesis_mgr, "position_service": position_service},
        settings=settings,
    )
    # Should not raise — best-effort observability
    await strat._build_position_prompt()

    events = _records_with_tag(loguru_sink, "STRAT_CALL_B_CTX")
    assert len(events) == 1
    kv = _parse_kv(events[0][1])
    assert kv.get("lessons_in_db") == "0", "must default to 0 on query failure"
