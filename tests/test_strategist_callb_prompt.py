"""Phase 1A — Post-Execution Closure Fix.

Verifies that the CALL_B (position-management) prompt and its system prompt
no longer contain TIAS recency-bias coaching language. The lessons section
at strategist.py:3138-3158 was producing a closed-loop failure where Claude
read "X just lost -0.23% on time_decay" and immediately closed a fresh
position with the same symbol+direction.

These tests guard against:
  1. The system prompt re-adding "lessons from similar trades" guidance.
  2. The user prompt re-adding the "## RECENT LESSONS" header.
  3. The user prompt re-adding "Lesson:" injection lines.

CALL_A's own lessons section at strategist.py:1198-1211 is OUT OF SCOPE
per operator decision and is not asserted on here.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.strategist import POSITION_SYSTEM_PROMPT, ClaudeStrategist


# ─── Fixture helpers ────────────────────────────────────────────────────────


def _make_strategist_with_lessons(
    *,
    lessons: list[dict] | None = None,
    positions: list | None = None,
) -> ClaudeStrategist:
    """Build a minimal ClaudeStrategist whose CALL_B context will surface
    recency-bias coaching IF the deletion regressed.

    The mocked ``thesis_manager.get_recent_lessons`` always returns the
    supplied lessons list. The mocked ``trade_coordinator`` exposes the
    minimum surface ``_build_position_prompt`` calls (``get_trade_plan``,
    ``get_trade_info``, ``get_active_reentry_cooldowns``).
    """
    if lessons is None:
        lessons = [
            {
                "symbol": "RENDERUSDT",
                "direction": "Sell",
                "actual_pnl_pct": -0.23,
                "close_reason": "time_decay_p_win_low",
                "lesson": "Sell lost on time_decay with low p_win",
            },
            {
                "symbol": "ARBUSDT",
                "direction": "Buy",
                "actual_pnl_pct": +0.5,
                "close_reason": "shadow_sl_tp",
                "lesson": "Buy hit TP on momentum continuation",
            },
        ]
    if positions is None:
        positions = []

    thesis_mgr = MagicMock()
    thesis_mgr.get_open_theses = AsyncMock(return_value=[])
    thesis_mgr.get_recent_lessons = AsyncMock(return_value=lessons)

    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=positions)

    coordinator = MagicMock()
    coordinator.get_trade_plan = MagicMock(return_value=None)
    coordinator.get_trade_info = MagicMock(return_value={})
    # Issue 3 (2026-05-18) — brain prompt now reads
    # get_active_reentry_cooldowns; empty list = no cooldown lines rendered.
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

    settings = SimpleNamespace(
        brain=SimpleNamespace(use_packages=True, surface_briefing_fields=False),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
    )

    strat = ClaudeStrategist(
        claude_client=None,
        services=services,
        settings=settings,
    )
    # The builder calls ``self.refresh_positions`` (not in services); patch
    # to return the same empty list so the rest of the flow exercises.
    strat.refresh_positions = AsyncMock(return_value=positions)
    return strat


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_system_prompt_drops_lessons_from_similar_trades_guidance() -> None:
    """The CALL_B system prompt must not instruct Claude to consider
    'lessons from similar trades' once the data section is gone.

    Phase 1A removed the directive at line 160 of POSITION_SYSTEM_PROMPT
    so Claude is not told to weigh data that no longer reaches the prompt.
    """
    assert "lessons from similar trades" not in POSITION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_callb_prompt_has_no_recent_lessons_header() -> None:
    """The CALL_B user prompt must not include the '## RECENT LESSONS'
    section header, regardless of what ``thesis_manager.get_recent_lessons``
    returns.
    """
    strat = _make_strategist_with_lessons()
    prompt = await strat._build_position_prompt()
    assert "## RECENT LESSONS" not in prompt


@pytest.mark.asyncio
async def test_callb_prompt_has_no_lesson_keyword() -> None:
    """The CALL_B user prompt must not inject any 'Lesson:' free-text
    line. This catches a regression where the header gets renamed but
    the lesson body remains.
    """
    strat = _make_strategist_with_lessons()
    prompt = await strat._build_position_prompt()
    assert "Lesson:" not in prompt


@pytest.mark.asyncio
async def test_callb_prompt_keeps_market_regime_and_sentiment_headers() -> None:
    """Regression guard: removing the lessons block must not have
    accidentally deleted the surrounding sections (market regime,
    sentiment, today's PnL, open positions) that ``_build_position_prompt``
    is supposed to provide.
    """
    strat = _make_strategist_with_lessons()
    prompt = await strat._build_position_prompt()
    assert "## MARKET REGIME" in prompt
    assert "## SENTIMENT" in prompt
    assert "## YOUR OPEN POSITIONS" in prompt


# ─── Sub-phase 1B regression guards (CALL_B Framing Fix, 2026-05-06) ──


def test_system_prompt_drops_regime_reversed_close_rule() -> None:
    """Sub-phase 1B removed the rule:

      "If regime reversed against position direction and SL > 70%
       consumed: CLOSE."

    This was a dominant trade-killer for APEX/XRAY-flipped positions.
    """
    assert "regime reversed against position direction" not in POSITION_SYSTEM_PROMPT
    assert "SL > 70% consumed" not in POSITION_SYSTEM_PROMPT


def test_system_prompt_drops_thesis_broken_close_rule() -> None:
    """Sub-phase 1B removed the rule:

      "If thesis is broken (the reason for entry no longer holds): CLOSE."

    The original thesis text predates direction-flips; closing on it
    undoes the system's intentional flip-correction.
    """
    assert "thesis is broken" not in POSITION_SYSTEM_PROMPT


def test_system_prompt_states_aggressive_aim_up_front() -> None:
    """Sub-phase 1B leads with the operator's stated aim — mirrors
    CALL_A's framing fix. Catches a regression where the aim line gets
    moved or rewritten away from the lead position."""
    head = POSITION_SYSTEM_PROMPT.split("\n", 1)[0]
    assert "maximize the development" in head
    assert "Aggressive opportunity exploitation" in POSITION_SYSTEM_PROMPT


def test_system_prompt_forbids_regime_alignment_close() -> None:
    """Sub-phase 1B explicitly tells Claude not to close on regime
    alignment alone, original thesis, or recency bias. These are the
    three patterns the forensic data showed driving the closure
    pattern."""
    assert "regime alignment alone" in POSITION_SYSTEM_PROMPT
    assert "original thesis text" in POSITION_SYSTEM_PROMPT
    assert "recency bias" in POSITION_SYSTEM_PROMPT


def test_system_prompt_version_bumped() -> None:
    """The version sentinel rises whenever load-bearing changes land in
    POSITION_SYSTEM_PROMPT. v2 = post-Phase-1B reframing."""
    from src.brain.strategist import POSITION_SYSTEM_PROMPT_VERSION
    assert POSITION_SYSTEM_PROMPT_VERSION >= 2


# ─── Sub-phase 1C regression guard (CALL_B Framing Fix, 2026-05-06) ──


@pytest.mark.asyncio
async def test_callb_prompt_drops_per_position_thesis_line() -> None:
    """Sub-phase 1C removes the per-position 'Thesis: ...' line entirely
    from the CALL_B prompt body. The original thesis text was written for
    the pre-flip direction and contradicted the current state shown in
    the same block, which Claude read as 'thesis broken' to drive
    premature closes.

    The thesis_manager itself is unchanged (still saves the column on
    entry, still used by trade-history queries) — only the CALL_B
    rendering stops reading it.
    """
    # Open thesis with a recognizable text so a regression that re-adds
    # the line would surface the literal string.
    thesis_mgr = MagicMock()
    thesis_mgr.get_open_theses = AsyncMock(return_value=[
        {
            "symbol": "BTCUSDT",
            "direction": "Buy",
            "thesis": "REGRESSION_MARKER_THESIS_TEXT_NEEDLE_42",
            "stop_loss_price": 60000.0,
            "take_profit_price": 70000.0,
            "leverage": 3,
            "apex_flipped": 0,
        },
    ])
    thesis_mgr.get_recent_lessons = AsyncMock(return_value=[])

    pos = SimpleNamespace(
        symbol="BTCUSDT",
        side=SimpleNamespace(value="Buy"),
        entry_price=65000.0,
        mark_price=65500.0,
        size=0.1,
        leverage=3,
    )
    position_service = MagicMock()
    position_service.get_positions = AsyncMock(return_value=[pos])
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
    settings = SimpleNamespace(
        brain=SimpleNamespace(use_packages=True, surface_briefing_fields=False),
        stage2=SimpleNamespace(enable_zero_two_contract=False),
    )
    strat = ClaudeStrategist(
        claude_client=None,
        services=services,
        settings=settings,
    )
    strat.refresh_positions = AsyncMock(return_value=[pos])

    prompt = await strat._build_position_prompt()
    # The needle text from the thesis row must NOT appear in the prompt.
    assert "REGRESSION_MARKER_THESIS_TEXT_NEEDLE_42" not in prompt
    # The literal "Thesis:" label must NOT appear either (catches a
    # rename of the line that still injects the data).
    assert "  Thesis:" not in prompt
    # Position state IS still rendered.
    assert "BTCUSDT" in prompt
    assert "Entry:" in prompt
    assert "SL:" in prompt


# ─── Sub-phase 1D regression guard (CALL_B Framing Fix, 2026-05-06) ──


@pytest.mark.asyncio
async def test_callb_prompt_contains_aggressive_management_contract() -> None:
    """Sub-phase 1D inserts the CONTRACT — POSITION MANAGEMENT section
    directly above the per-position data so Claude reads it next to the
    rows it's reasoning about. The contract restates the system-prompt
    framing in operational terms and explicitly forbids the three
    closure patterns the forensic data flagged.
    """
    strat = _make_strategist_with_lessons(positions=[])
    prompt = await strat._build_position_prompt()
    # Section header
    assert "## CONTRACT — POSITION MANAGEMENT" in prompt
    # Aim
    assert "maximize their development" in prompt
    # The literal phrase from the plan that the operator decided is
    # required for FLIPPED-position management.
    assert "trust the current state shown above" in prompt
    # CLOSE criteria are present (positive cases)
    assert "structural change" in prompt
    assert "SL is approaching and recovery looks unlikely" in prompt
    # NEGATIVE close patterns explicitly forbidden
    assert "Regime alignment alone" in prompt
    assert "original thesis text" in prompt
    assert "Recency-bias" in prompt
