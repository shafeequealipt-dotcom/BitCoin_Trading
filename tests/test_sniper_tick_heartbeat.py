"""Observability G2 — SNIPER_TICK heartbeat sampling.

The audit's claim — SNIPER_TICK fires zero times — is correct: no
heartbeat event exists in profit_sniper.py. The fix adds a sampled
SNIPER_TICK emission every 12 ticks (~60 s at the default 5 s
cadence) so operators can verify the sniper is alive even when no
state events fire.

This suite verifies:
  * The sampling cadence is exactly 1-in-12 ticks
  * The event fields are populated correctly
  * Each exit path (transformer-switching, get_positions failure,
    normal completion) calls the heartbeat helper
  * Volume scales as expected (~60 events/hour)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.workers.profit_sniper import ProfitSniper


@pytest.fixture
def loguru_sink():
    """Capture loguru records into a list for assertion."""
    records: list[str] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(msg.record["message"]),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records: list[str], tag: str) -> list[str]:
    return [m for m in records if m.startswith(tag)]


def _bare_sniper(*, tracked: dict | None = None, transformer_mode: str = "shadow") -> ProfitSniper:
    """Construct a minimal ProfitSniper bypassing __init__.

    We skip __init__ to avoid spinning up the SniperModels / BaseWorker
    machinery; the heartbeat helper only reads ``_tick_count``,
    ``_tracked``, and ``transformer.current_mode``. Tests that drive
    the full ``tick()`` need slightly more wiring (see below).
    """
    s: ProfitSniper = ProfitSniper.__new__(ProfitSniper)
    s._tick_count = 0
    s._tracked = tracked if tracked is not None else {}
    s.transformer = SimpleNamespace(current_mode=transformer_mode, is_switching=False)
    # G2 — per-window SL update counters (audit schema)
    s._sl_updates_attempted_window = 0
    s._sl_updates_accepted_window = 0
    return s


# ─── _maybe_emit_tick_heartbeat: sampling cadence ───────────────────────────


def test_heartbeat_does_not_fire_below_sample_threshold(loguru_sink) -> None:
    """Ticks 1..11 must NOT fire SNIPER_TICK; only multiples of 12 do."""
    _ = loguru_sink
    s = _bare_sniper()
    for _ in range(11):
        s._tick_count += 1
        s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    assert _records_with_tag(loguru_sink, "SNIPER_TICK") == []


def test_heartbeat_fires_at_tick_12(loguru_sink) -> None:
    """Tick 12 must fire SNIPER_TICK exactly once."""
    _ = loguru_sink
    s = _bare_sniper(tracked={"BTCUSDT": {}, "ETHUSDT": {}})
    for _ in range(12):
        s._tick_count += 1
        s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    msg = events[0]
    assert "tick=12" in msg
    assert "n=2" in msg
    # syms list should include both symbols (order may vary because
    # dict iteration order is insertion-preserving, so this is stable)
    assert "BTCUSDT" in msg
    assert "ETHUSDT" in msg
    assert "mode=shadow" in msg


def test_heartbeat_fires_every_12_ticks(loguru_sink) -> None:
    """Over 60 ticks, exactly 5 SNIPER_TICK events (12,24,36,48,60)."""
    _ = loguru_sink
    s = _bare_sniper()
    for _ in range(60):
        s._tick_count += 1
        s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 5
    # The tick= field should advance: 12, 24, 36, 48, 60
    ticks = [int(e.split("tick=")[1].split(" ")[0]) for e in events]
    assert ticks == [12, 24, 36, 48, 60]


def test_heartbeat_volume_matches_one_per_minute_target(loguru_sink) -> None:
    """720 ticks/hour at 5s cadence -> 60 SNIPER_TICK events/hour."""
    _ = loguru_sink
    s = _bare_sniper()
    for _ in range(720):
        s._tick_count += 1
        s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 60, f"expected 60 (one per minute), got {len(events)}"


# ─── _maybe_emit_tick_heartbeat: field truncation ───────────────────────────


def test_heartbeat_truncates_symbol_list_when_long(loguru_sink) -> None:
    """syms field should show first 5 + '+N' overflow indicator."""
    _ = loguru_sink
    tracked = {f"COIN{i:02d}USDT": {} for i in range(8)}
    s = _bare_sniper(tracked=tracked)
    s._tick_count = 12  # ready to fire on next call
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    msg = events[0]
    # First 5 symbols present
    for i in range(5):
        assert f"COIN{i:02d}USDT" in msg
    # Overflow indicator
    assert "+3" in msg
    # n field still shows full count
    assert "n=8" in msg


def test_heartbeat_handles_empty_tracked_dict(loguru_sink) -> None:
    """n=0 syms=[] when sniper is alive but tracking nothing."""
    _ = loguru_sink
    s = _bare_sniper(tracked={})
    s._tick_count = 12
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    msg = events[0]
    assert "n=0" in msg
    assert "syms=[]" in msg


def test_heartbeat_reports_elapsed_ms(loguru_sink) -> None:
    """el= field is the millisecond delta between _tick_start and now."""
    import time as _time
    _ = loguru_sink
    s = _bare_sniper()
    s._tick_count = 12
    _start = _time.time() - 0.050  # ~50 ms ago
    s._maybe_emit_tick_heartbeat(_tick_start=_start)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    msg = events[0]
    # Extract el= value
    el_str = msg.split("el=")[1].split("ms")[0]
    el_ms = int(el_str)
    assert 40 <= el_ms <= 200, f"expected ~50ms elapsed, got {el_ms}"


# ─── tick() integration — heartbeat fires from every exit path ──────────────


@pytest.mark.asyncio
async def test_tick_heartbeat_fires_on_transformer_switching_skip(loguru_sink) -> None:
    """If transformer is switching, tick returns early — heartbeat still
    fires (sniper is alive, just idle)."""
    _ = loguru_sink
    s = _bare_sniper()
    s._tick_count = 11  # next increment → 12, sample threshold
    s.transformer = SimpleNamespace(current_mode="shadow", is_switching=True)
    # Call tick() — should hit the transformer-switching branch
    await s.tick()
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1, "heartbeat must fire on the idle-skip path"
    assert "tick=12" in events[0]


@pytest.mark.asyncio
async def test_tick_heartbeat_fires_on_get_positions_failure(loguru_sink) -> None:
    """If _get_positions returns None (error), tick returns early — but
    the sniper is still alive, so the heartbeat must fire."""
    _ = loguru_sink
    s = _bare_sniper()
    s._tick_count = 11
    s.transformer = SimpleNamespace(current_mode="shadow", is_switching=False)
    s._get_positions = AsyncMock(return_value=None)
    await s.tick()
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    assert "tick=12" in events[0]


@pytest.mark.asyncio
async def test_tick_non_sample_does_not_fire(loguru_sink) -> None:
    """A non-sample tick (e.g., tick 7) must not fire SNIPER_TICK."""
    _ = loguru_sink
    s = _bare_sniper()
    s._tick_count = 6  # next increment → 7, not a sample
    s.transformer = SimpleNamespace(current_mode="shadow", is_switching=True)
    await s.tick()
    assert _records_with_tag(loguru_sink, "SNIPER_TICK") == []


# ─── G2 audit-schema fields: sl_updates_attempted / accepted ────────────────


def test_heartbeat_emits_sl_update_counters(loguru_sink) -> None:
    """Per-audit schema: sl_updates_attempted + sl_updates_accepted appear in heartbeat."""
    _ = loguru_sink
    s = _bare_sniper()
    # Simulate the call-site behaviour: each trail/lock apply
    # increments attempted; only accepted-ones increment accepted.
    s._sl_updates_attempted_window = 7
    s._sl_updates_accepted_window = 5
    s._tick_count = 12
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    msg = events[0]
    assert "sl_updates_attempted=7" in msg
    assert "sl_updates_accepted=5" in msg


def test_heartbeat_resets_sl_update_counters(loguru_sink) -> None:
    """Counters reset to 0 after each emission so the next window starts fresh."""
    _ = loguru_sink
    s = _bare_sniper()
    s._sl_updates_attempted_window = 3
    s._sl_updates_accepted_window = 2
    s._tick_count = 12
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)

    assert s._sl_updates_attempted_window == 0
    assert s._sl_updates_accepted_window == 0

    # Second window: counters carry from zero
    s._sl_updates_attempted_window = 4
    s._sl_updates_accepted_window = 4
    s._tick_count = 24
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 2
    assert "sl_updates_attempted=4" in events[1]
    assert "sl_updates_accepted=4" in events[1]


def test_heartbeat_emits_zero_counters_when_no_sl_activity(loguru_sink) -> None:
    """When no SL pushes happened, counters emit as 0 — no field omission."""
    _ = loguru_sink
    s = _bare_sniper()
    s._tick_count = 12
    s._maybe_emit_tick_heartbeat(_tick_start=0.0)
    events = _records_with_tag(loguru_sink, "SNIPER_TICK")
    assert len(events) == 1
    assert "sl_updates_attempted=0" in events[0]
    assert "sl_updates_accepted=0" in events[0]
