"""Brain-prompt-enrichment Phase 3.4 — direction performance line in CALL_B.

The pre-fix ``_build_position_prompt`` rendered regime, sentiment, today
PnL, then the open-positions header. The brain managing positions saw
no aggregate "today longs are NW/ML, shorts are NW/ML" framing — and
the dir_perf data WAS computed by ``PerformanceEnforcer._per_direction``,
just never bridged to the prompt.

These tests pin the new line's format and the day-bounded data source,
and verify the line is suppressed when the data would be misleading
(zero trades closed today, enforcer service unavailable, flag off).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.strategist import ClaudeStrategist


def _make_strategist(
    *,
    per_direction: dict | None = None,
    emit_flag: bool = True,
    include_enforcer: bool = True,
) -> ClaudeStrategist:
    """Minimal CALL_B harness for the direction-perf line.

    ``per_direction`` mimics ``PerformanceEnforcer._per_direction``:
    ``{"Buy": {"wins": N, "losses": M}, "Sell": {"wins": N, "losses": M}}``.
    When ``include_enforcer=False`` the enforcer service is unregistered
    (cold-start case).
    """
    if per_direction is None:
        per_direction = {
            "Buy": {"wins": 3, "losses": 1},
            "Sell": {"wins": 2, "losses": 3},
        }

    thesis_mgr = MagicMock()
    thesis_mgr.get_open_theses = AsyncMock(return_value=[])
    thesis_mgr.get_recent_lessons = AsyncMock(return_value=[])

    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=[])

    coordinator = MagicMock()
    coordinator.get_trade_plan = MagicMock(return_value=None)
    coordinator.get_trade_info = MagicMock(return_value={})
    coordinator.get_active_reentry_cooldowns = MagicMock(return_value=[])

    pnl_manager = SimpleNamespace(current_pnl_pct=0.0)
    regime_detector = MagicMock()
    regime_detector.get_coin_regime = MagicMock(return_value=None)

    urgent_queue = MagicMock()
    urgent_queue.has_concerns = False

    services = {
        "thesis_manager": thesis_mgr,
        "position_service": position_service,
        "trade_coordinator": coordinator,
        "pnl_manager": pnl_manager,
        "regime_detector": regime_detector,
        "urgent_queue": urgent_queue,
    }
    if include_enforcer:
        services["enforcer"] = SimpleNamespace(_per_direction=per_direction)

    settings = SimpleNamespace(
        brain=SimpleNamespace(
            use_packages=True,
            surface_briefing_fields=False,
            emit_direction_perf_in_callb=emit_flag,
        ),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
    )

    strat = ClaudeStrategist(
        claude_client=None,
        services=services,
        settings=settings,
    )
    strat.refresh_positions = AsyncMock(return_value=[])
    return strat


@pytest.mark.asyncio
async def test_dir_perf_line_renders_both_sides() -> None:
    """Default case: today has trades on both sides → line includes
    both counts and win rates."""
    strat = _make_strategist(
        per_direction={
            "Buy": {"wins": 3, "losses": 1},
            "Sell": {"wins": 2, "losses": 3},
        },
    )
    prompt = await strat._build_position_prompt()
    assert "## TODAY DIRECTION PERF:" in prompt
    assert "Longs 3W/1L (75% WR)" in prompt
    assert "Shorts 2W/3L (40% WR)" in prompt


@pytest.mark.asyncio
async def test_dir_perf_line_handles_one_sided_activity() -> None:
    """When only one side has closed any trades, that side renders
    normally and the other shows the explicit no-data marker — the
    brain reads "shorts is 0/0 with no data" rather than "shorts is
    losing 100%"."""
    strat = _make_strategist(
        per_direction={
            "Buy": {"wins": 4, "losses": 0},
            "Sell": {"wins": 0, "losses": 0},
        },
    )
    prompt = await strat._build_position_prompt()
    assert "Longs 4W/0L (100% WR)" in prompt
    assert "Shorts 0W/0L (no data)" in prompt


@pytest.mark.asyncio
async def test_dir_perf_line_suppressed_when_no_trades_today() -> None:
    """Zero trades on BOTH sides — the line would carry no signal and
    is suppressed entirely. (Brain reading "Longs 0/0 | Shorts 0/0"
    would be more misleading than absent.)"""
    strat = _make_strategist(
        per_direction={
            "Buy": {"wins": 0, "losses": 0},
            "Sell": {"wins": 0, "losses": 0},
        },
    )
    prompt = await strat._build_position_prompt()
    assert "TODAY DIRECTION PERF" not in prompt


@pytest.mark.asyncio
async def test_dir_perf_line_suppressed_when_enforcer_absent() -> None:
    """No enforcer service → no line. Graceful degradation, no crash."""
    strat = _make_strategist(include_enforcer=False)
    prompt = await strat._build_position_prompt()
    assert "TODAY DIRECTION PERF" not in prompt
    # Other CALL_B sections still render — sanity check that the
    # try/except did not skip the rest of the builder.
    assert "## MARKET REGIME" in prompt
    assert "## YOUR OPEN POSITIONS" in prompt


@pytest.mark.asyncio
async def test_dir_perf_flag_off_suppresses_line() -> None:
    """emit_direction_perf_in_callb=False drops the line without
    touching any other CALL_B section."""
    strat = _make_strategist(emit_flag=False)
    prompt = await strat._build_position_prompt()
    assert "TODAY DIRECTION PERF" not in prompt
    # TODAY PnL still renders — confirms the flag controls only the
    # dir-perf line, not the surrounding section.
    assert "## TODAY: PnL=" in prompt


@pytest.mark.asyncio
async def test_dir_perf_line_appears_immediately_after_pnl_line() -> None:
    """Layout guard: the dir-perf line sits between the TODAY PnL line
    and the open-positions header so the brain reads aggregates first,
    per-position details second."""
    strat = _make_strategist()
    prompt = await strat._build_position_prompt()
    pnl_idx = prompt.index("## TODAY: PnL=")
    dir_idx = prompt.index("## TODAY DIRECTION PERF:")
    pos_idx = prompt.index("## YOUR OPEN POSITIONS")
    assert pnl_idx < dir_idx < pos_idx
