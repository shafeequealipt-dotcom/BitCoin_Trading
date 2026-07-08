"""Issue I2 (F-17) — Orphan-positions architectural fix.

Phase 0 discovered 14 live orphan rows in the positions table despite
the 2026-05-11 cleanup callback registration at manager.py:2198. Phase
1 traced this to the callback reading ``transformer.current_mode`` at
close-dispatch time, which is brittle during boot / mid-switch /
SEGV recovery. The fix:

  1. Capture ``exchange_mode`` on TradeState at register_trade time
  2. on_trade_closed prefers TradeState.exchange_mode (with current_mode
     fallback) when building the close record
  3. _positions_table_cleanup_on_close reads from the record's
     exchange_mode (not transformer.current_mode)
  4. Emit POSITION_ROW_DELETED on success + POSITION_ROW_DELETE_FAIL
     on failure for visibility
  5. One-shot backfill script for the 14 legacy orphans

This suite covers items 1-4; item 5 is a standalone script tested by
operator-run dry-run.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.trade_coordinator import TradeCoordinator, TradeState


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


# ─── TradeState carries exchange_mode ────────────────────────────────────


def test_trade_state_has_exchange_mode_field() -> None:
    """TradeState dataclass exposes exchange_mode after I2."""
    s = TradeState(symbol="BTCUSDT", exchange_mode="bybit_demo")
    assert s.exchange_mode == "bybit_demo"


def test_trade_state_exchange_mode_defaults_to_empty() -> None:
    """Backwards-compat: default empty string preserves legacy callers
    that construct TradeState without the new kwarg."""
    s = TradeState(symbol="BTCUSDT")
    assert s.exchange_mode == ""


# ─── register_trade captures exchange_mode ───────────────────────────────


def test_register_trade_captures_mode_from_transformer() -> None:
    """register_trade pulls current_mode from transformer at entry time
    and stores it on TradeState."""
    coord = TradeCoordinator()
    xfm = MagicMock()
    xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)

    coord.register_trade(
        symbol="BTCUSDT",
        side="Buy",
        size=0.05,
        entry_price=80000.0,
    )
    state = coord._trades["BTCUSDT"]
    assert state.exchange_mode == "bybit_demo"


def test_register_trade_without_transformer_stores_empty_mode() -> None:
    """Boot-race: transformer not attached → empty string captured. The
    callback's fallback path is exercised at close time."""
    coord = TradeCoordinator()
    # No transformer attached
    coord.register_trade(
        symbol="ETHUSDT",
        side="Sell",
        size=0.1,
        entry_price=3000.0,
    )
    state = coord._trades["ETHUSDT"]
    assert state.exchange_mode == ""


# ─── on_trade_closed prefers TradeState.exchange_mode ────────────────────


def test_on_trade_closed_record_uses_state_exchange_mode() -> None:
    """When TradeState.exchange_mode is set, the close record carries
    it — even if the transformer's current_mode changed since entry."""
    coord = TradeCoordinator()
    xfm = MagicMock()
    xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=80000.0,
    )
    # Simulate transformer mode changing between open and close
    xfm.current_mode = ""  # transformer detaches / mid-switch
    # Capture the record emitted via the callback
    captured: list[dict] = []
    coord.register_close_callback(lambda r: captured.append(r))
    coord.on_trade_closed(
        symbol="BTCUSDT", pnl_pct=0.5, pnl_usd=20.0, was_win=True,
        closed_by="bybit_demo_sl_tp", exit_price=80400.0,
    )
    assert len(captured) == 1
    # TradeState.exchange_mode wins; record carries bybit_demo despite
    # transformer being empty at close time
    assert captured[0]["exchange_mode"] == "bybit_demo"


def test_on_trade_closed_falls_back_to_current_mode_when_state_mode_empty() -> None:
    """Legacy pre-I2 trades (registered before TradeState got the
    exchange_mode field) have empty state.exchange_mode. The close
    record falls back to current_mode."""
    coord = TradeCoordinator()
    # No transformer at register time (state.exchange_mode = "")
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=80000.0,
    )
    # Now attach transformer with a mode (e.g., post-restart attach)
    xfm = MagicMock()
    xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    captured: list[dict] = []
    coord.register_close_callback(lambda r: captured.append(r))
    coord.on_trade_closed(
        symbol="BTCUSDT", pnl_pct=0.5, pnl_usd=20.0, was_win=True,
        closed_by="exchange_sl_tp", exit_price=80400.0,
    )
    # Falls back to transformer at close
    assert captured[0]["exchange_mode"] == "bybit_demo"


# ─── Source-level pins ───────────────────────────────────────────────────


def test_manager_cleanup_callback_uses_record_mode() -> None:
    """Source-pin: the cleanup callback reads ``record.get('exchange_mode')``
    NOT transformer.current_mode. A regression that re-introduces the
    global-state read would silently re-leak."""
    src = open("src/workers/manager.py").read()
    # Locate the _positions_table_cleanup_on_close definition + body
    match = re.search(
        r"def _positions_table_cleanup_on_close.*?coordinator\.register_close_callback",
        src,
        re.DOTALL,
    )
    assert match is not None
    body = match.group(0)
    assert "record.get(\"exchange_mode\"" in body, (
        "Issue I2: cleanup callback must read record['exchange_mode'], "
        "not transformer.current_mode"
    )


def test_manager_cleanup_callback_uses_get_running_loop() -> None:
    """Source-pin: replace deprecated get_event_loop() with get_running_loop().
    This turns a silent failure (closed loop) into a visible CLOSE_CB_FAIL."""
    src = open("src/workers/manager.py").read()
    match = re.search(
        r"def _positions_table_cleanup_on_close.*?coordinator\.register_close_callback",
        src,
        re.DOTALL,
    )
    body = match.group(0)
    assert "get_running_loop" in body, (
        "Issue I2: cleanup callback must use asyncio.get_running_loop()"
    )


def test_manager_cleanup_callback_emits_position_row_deleted() -> None:
    """Source-pin: POSITION_ROW_DELETED success emission is registered."""
    src = open("src/workers/manager.py").read()
    assert "POSITION_ROW_DELETED" in src, (
        "Issue I2: POSITION_ROW_DELETED success emission missing"
    )


def test_manager_cleanup_callback_emits_position_row_delete_fail() -> None:
    """Source-pin: failure emission visible at WARNING level."""
    src = open("src/workers/manager.py").read()
    assert "POSITION_ROW_DELETE_FAIL" in src, (
        "Issue I2: POSITION_ROW_DELETE_FAIL must be emitted on failure"
    )


def test_manager_cleanup_callback_emits_position_row_delete_skip() -> None:
    """Source-pin: skip-path emission shows when mode-gating trips so
    operators can see silent skips that previously leaked."""
    src = open("src/workers/manager.py").read()
    assert "POSITION_ROW_DELETE_SKIP" in src, (
        "Issue I2: POSITION_ROW_DELETE_SKIP must be emitted on mode skip"
    )


def test_backfill_script_exists_and_imports() -> None:
    """The one-shot backfill script exists and is syntactically valid."""
    import ast
    src = open("scripts/backfill_orphan_positions.py").read()
    ast.parse(src)  # syntax check
    assert "POSITION_ORPHAN_BACKFILL_START" in src
    assert "POSITION_ORPHAN_DELETED" in src
    assert "POSITION_ORPHAN_BACKFILL_DONE" in src
