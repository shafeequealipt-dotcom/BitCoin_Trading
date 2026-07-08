"""Phase 3.7 + 3.8 — Mid-Hold Trade Management Fix: prompt enrichment.

Tests the static rendering helpers ``_render_thesis_invalidation_block``
and ``_render_thesis_events_block`` plus the event-consumption lifecycle
hooks ``_consume_callA_events`` / ``_consume_callB_events``.

The full _build_context_prompt / _build_position_prompt are exercised
by the live trial in Phase 3.9; here we cover the surfaces that can
be unit-tested without standing up the entire strategist service tree.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.brain.strategist import ClaudeStrategist as Strategist


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


# ════════════════════════════════════════════════════════════════════
# 1. _render_thesis_invalidation_block — static, brain_stated
# ════════════════════════════════════════════════════════════════════


def test_render_brain_stated_price_above() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
        "thesis_state": "VALID",
    }
    out = Strategist._render_thesis_invalidation_block(row)
    assert "THESIS_INVALIDATION:" in out
    assert "type=price_close_above" in out
    assert "value=245.3" in out
    assert "state=VALID" in out
    assert "source=brain_stated" in out


def test_render_brain_stated_none_type() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": "",
        "thesis_snapshot": "{}",
        "thesis_state": "VALID",
    }
    out = Strategist._render_thesis_invalidation_block(row)
    assert "type=none" in out
    assert "state=VALID" in out


def test_render_heuristic_fallback_with_ob_sell() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "heuristic_fallback",
        "thesis_invalidation": "",
        "thesis_snapshot": (
            '{"nearest_aligned_level": {"type": "ob", "side": "bearish",'
            ' "high": 245.30, "low": 244.10}}'
        ),
        "thesis_state": "DEGRADING",
    }
    out = Strategist._render_thesis_invalidation_block(row)
    assert "source=heuristic_fallback" in out
    assert "anchor=ob@245.3" in out
    assert "state=DEGRADING" in out


def test_render_heuristic_no_anchor() -> None:
    row = {
        "direction": "Sell",
        "thesis_source": "heuristic_fallback",
        "thesis_invalidation": "",
        "thesis_snapshot": '{"nearest_aligned_level": {"type": "none"}}',
        "thesis_state": "VALID",
    }
    out = Strategist._render_thesis_invalidation_block(row)
    assert "no_anchor" in out
    assert "state=VALID" in out


# ════════════════════════════════════════════════════════════════════
# 2. Flip annotation prefix (Phase 3.8 surface)
# ════════════════════════════════════════════════════════════════════


def test_render_with_flip_annotation_prefix() -> None:
    """The CALL_B Framing Fix Phase 1C-safe prefix appears when
    flip_annotation=True."""
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
        "thesis_state": "VALID",
    }
    out = Strategist._render_thesis_invalidation_block(row, flip_annotation=True)
    assert "THESIS_INVALIDATION_PRE_FLIP_INFORMATIONAL:" in out
    assert "THESIS_INVALIDATION:" not in out.replace(
        "THESIS_INVALIDATION_PRE_FLIP_INFORMATIONAL:", "",
    )


# ════════════════════════════════════════════════════════════════════
# 3. _render_thesis_events_block
# ════════════════════════════════════════════════════════════════════


def test_render_events_empty() -> None:
    out, ids = Strategist._render_thesis_events_block([])
    assert out == ""
    assert ids == []


def test_render_events_one() -> None:
    events = [
        {
            "id": 7,
            "symbol": "ETHUSDT",
            "event_type": "ensemble_flip",
            "payload": '{"consensus": "STRONG", "agreeing": 6.36}',
            "created_at": "2026-05-19T16:36:33",
        },
    ]
    out, ids = Strategist._render_thesis_events_block(events)
    assert ids == [7]
    assert "QUEUED_EVENTS:" in out
    assert "ensemble_flip" in out
    assert "16:36:33" in out


def test_render_events_multiple_joined() -> None:
    events = [
        {
            "id": 7, "symbol": "ETHUSDT", "event_type": "ensemble_flip",
            "payload": '{"a": 1}', "created_at": "2026-05-19T16:36:33",
        },
        {
            "id": 8, "symbol": "ETHUSDT", "event_type": "thesis_invalidation",
            "payload": '{"b": 2}', "created_at": "2026-05-19T16:40:00",
        },
    ]
    out, ids = Strategist._render_thesis_events_block(events)
    assert ids == [7, 8]
    assert "ensemble_flip" in out
    assert "thesis_invalidation" in out
    assert " | " in out  # joiner


# ════════════════════════════════════════════════════════════════════
# 4. mark_events_consumed lifecycle
# ════════════════════════════════════════════════════════════════════


def _make_minimal_strategist() -> Strategist:
    """Strategist bypassing __init__ (which logs boot sentinels)."""
    s = Strategist.__new__(Strategist)
    s.services = {}
    s._last_callA_event_ids = []
    s._last_callB_event_ids = []
    return s


@pytest.mark.asyncio
async def test_consume_callA_events_marks_and_resets(loguru_sink) -> None:
    s = _make_minimal_strategist()
    s._last_callA_event_ids = [10, 11, 12]
    mock_thesis = MagicMock()
    mock_thesis.mark_events_consumed = AsyncMock(return_value=3)
    s.services["thesis_manager"] = mock_thesis

    await s._consume_callA_events()

    mock_thesis.mark_events_consumed.assert_called_once_with(
        [10, 11, 12], "CALL_A",
    )
    assert s._last_callA_event_ids == []
    log = _records_with_tag(loguru_sink, "THESIS_SURFACED_IN_PROMPT ")[0][1]
    kv = _parse_kv(log)
    assert kv["consumer"] == "CALL_A"
    assert kv["events"] == "3"


@pytest.mark.asyncio
async def test_consume_callB_events_marks_and_resets(loguru_sink) -> None:
    s = _make_minimal_strategist()
    s._last_callB_event_ids = [20]
    mock_thesis = MagicMock()
    mock_thesis.mark_events_consumed = AsyncMock(return_value=1)
    s.services["thesis_manager"] = mock_thesis

    await s._consume_callB_events()

    mock_thesis.mark_events_consumed.assert_called_once_with([20], "CALL_B")
    assert s._last_callB_event_ids == []


@pytest.mark.asyncio
async def test_consume_with_empty_ids_is_noop() -> None:
    s = _make_minimal_strategist()
    mock_thesis = MagicMock()
    mock_thesis.mark_events_consumed = AsyncMock()
    s.services["thesis_manager"] = mock_thesis

    await s._consume_callA_events()

    mock_thesis.mark_events_consumed.assert_not_called()


@pytest.mark.asyncio
async def test_consume_failure_logs_and_clears(loguru_sink) -> None:
    s = _make_minimal_strategist()
    s._last_callA_event_ids = [99]
    mock_thesis = MagicMock()
    mock_thesis.mark_events_consumed = AsyncMock(side_effect=RuntimeError("DB down"))
    s.services["thesis_manager"] = mock_thesis

    await s._consume_callA_events()

    assert len(_records_with_tag(loguru_sink, "CALLA_EVENTS_CONSUME_FAIL ")) == 1
    # The ledger is cleared even on failure so the next cycle re-fetches
    # cleanly (events stay unseen at the DB level).
    assert s._last_callA_event_ids == []


# ════════════════════════════════════════════════════════════════════
# 5. Phase 3.8 — Flip-annotation regression guard
# ════════════════════════════════════════════════════════════════════
#
# Per the operator decision + the IMPLEMENT doc Risk 4 entry, the
# CALL_B Framing Fix Phase 1C (2026-05-06) intentionally hides the
# free-text thesis column from CALL_B prompts because flipped positions
# had pre-flip thesis text contradicting current state, which Claude
# misread as "thesis broken" and used to drive premature closes. Our
# new thesis_invalidation field must NOT regress that fix. The
# flip-annotation prefix is the explicit guard: when a position is
# APEX/XRAY-flipped, the rendering shows
# THESIS_INVALIDATION_PRE_FLIP_INFORMATIONAL so the brain treats the
# criterion as situational context, NOT as instruction.


def test_flip_annotation_for_flipped_position_uses_safe_prefix() -> None:
    """An APEX-flipped position renders with the PRE_FLIP prefix."""
    flipped_row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
        "thesis_state": "VALID",
        "apex_flipped": 1,
        "apex_original_direction": "Buy",
    }
    out = Strategist._render_thesis_invalidation_block(
        flipped_row, flip_annotation=True,
    )
    assert "THESIS_INVALIDATION_PRE_FLIP_INFORMATIONAL:" in out
    # Sanity: the criterion data is still present so the brain sees
    # what was set at entry; it's the framing that's different.
    assert "price_close_above" in out
    assert "245.3" in out


def test_flip_annotation_off_uses_standard_prefix() -> None:
    """A non-flipped position renders with the plain prefix (Phase 3.7 path)."""
    row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
        "thesis_state": "VALID",
    }
    out = Strategist._render_thesis_invalidation_block(row, flip_annotation=False)
    # The standard prefix lacks "_PRE_FLIP_INFORMATIONAL".
    assert "THESIS_INVALIDATION:" in out
    assert "PRE_FLIP_INFORMATIONAL" not in out


def test_flip_annotation_preserves_information_only_framing() -> None:
    """Per Rule 4 anti-pattern guard: even the flipped-form annotation
    is informational — it must NOT carry directive language like
    'close on invalidation'. The annotation merely marks the criterion
    as situational; the brain still decides."""
    flipped_row = {
        "direction": "Sell",
        "thesis_source": "brain_stated",
        "thesis_invalidation": '{"type": "price_close_above", "value": 245.30}',
        "thesis_snapshot": "{}",
        "thesis_state": "INVALIDATED",
    }
    out = Strategist._render_thesis_invalidation_block(
        flipped_row, flip_annotation=True,
    ).lower()
    forbidden = [
        "close if",
        "close on invalidation",
        "exit if",
        "must close",
        "must exit",
    ]
    for phrase in forbidden:
        assert phrase not in out, (
            f"flipped annotation contains directive phrase {phrase!r}"
        )
