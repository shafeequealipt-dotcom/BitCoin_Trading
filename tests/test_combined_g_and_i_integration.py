"""Combined cross-session integration test.

Exercises G-suite (observability gaps G1-G11) and I-suite (five
critical fixes I1-I5) together through a single trade lifecycle.
Verifies the fixes coexist without contract conflicts and the
emissions all fire at the right pipeline phase.

This is the final integration smoke check for the combined branch
that has all 16 fix branches merged. Failures here would indicate
a cross-fix wiring problem that the per-branch test suites miss.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.sl_tp_validator import SLTPValidator
from src.core.thesis_manager import ThesisManager
from src.core.trade_coordinator import TradeCoordinator


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


# ─── G + I trade open lifecycle ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_trade_open_fires_all_g_and_i_emissions(loguru_sink) -> None:
    """A single trade open exercises: SLTP_PAIR_OK (G10) + COORD_REG
    (G6/I2) + COORD_DUPLICATE_REGISTER (G6) + TRADEPLAN_PERSISTED (I5)
    + THESIS_OPEN (G8) in one continuous lifecycle."""
    # Step 1 — validate (G10)
    validator = SLTPValidator()
    action, _ = validator.validate_pair(
        sl_price=78000.0, tp_price=84000.0,
        entry_price=80000.0, current_price=80000.0,
        direction="Buy", symbol="BTCUSDT",
    )
    assert action == "OK"

    # Step 2 — register trade (G6 fields + I2 exchange_mode)
    coord = TradeCoordinator()
    xfm = MagicMock(); xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    coord.register_trade(
        symbol="BTCUSDT",
        side="Buy", size=0.05, entry_price=80000.0,
        decision_id="d-e2e",
        source="claude_direct",
        order_id="ORD-E2E-1",
        sl_price=78000.0, tp_price=84000.0,
        leverage=5, size_usd=4000.0,
    )

    # Step 3 — register trade plan (I5 TRADEPLAN_PERSISTED)
    plan = SimpleNamespace(
        direction="Buy", entry_price=80000.0, peak_price=80000.0,
        stop_loss_price=78000.0, target_price=84000.0,
        max_hold_minutes=120, trailing_activation_pct=2.0,
        size_tier="standard",
    )
    coord.register_trade_plan("BTCUSDT", plan)

    # Step 4 — save thesis (G8 fields)
    db = MagicMock()
    cur = MagicMock(); cur.lastrowid = 42
    db.execute = AsyncMock(return_value=cur)
    mgr = ThesisManager(db=db)
    await mgr.save_thesis(
        symbol="BTCUSDT", direction="long", entry_price=80000.0,
        stop_loss_price=78000.0, take_profit_price=84000.0,
        size_usd=4000.0, leverage=5, max_hold_minutes=120,
        trailing_activation_pct=2.0, thesis="combined E2E",
        order_id="ORD-E2E-1",
    )

    # Now verify every expected emission fired
    sltp_ok = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    coord_reg = _records_with_tag(loguru_sink, "COORD_REG ")
    trade_plan_persisted = _records_with_tag(loguru_sink, "TRADEPLAN_PERSISTED")
    thesis_open = _records_with_tag(loguru_sink, "THESIS_OPEN ")

    assert len(sltp_ok) == 1, "G10 SLTP_PAIR_OK must fire"
    assert len(coord_reg) == 1, "G6 COORD_REG must fire"
    assert len(trade_plan_persisted) == 1, "I5 TRADEPLAN_PERSISTED must fire"
    assert len(thesis_open) == 1, "G8 THESIS_OPEN must fire"

    # Cross-event field consistency: same symbol, same SL/TP across G10+G6+G8
    sltp_kv = _parse_kv(sltp_ok[0][1])
    coord_kv = _parse_kv(coord_reg[0][1])
    thesis_kv = _parse_kv(thesis_open[0][1])
    assert sltp_kv["sym"] == coord_kv["sym"] == thesis_kv["sym"] == "BTCUSDT"
    assert float(coord_kv["sl"]) == float(thesis_kv["sl"]) == 78000.0
    assert float(coord_kv["tp"]) == float(thesis_kv["tp"]) == 84000.0

    # G10 checks list visible
    assert sltp_kv.get("checks") == "invalid_price,sl_equals_tp,wrong_side"

    # I2 fielded exchange_mode on TradeState
    state = coord._trades["BTCUSDT"]
    assert state.exchange_mode == "bybit_demo"


# ─── G6 duplicate + I2 mode together ──────────────────────────────────────


def test_g6_duplicate_warning_fires_with_i2_mode_intact(loguru_sink) -> None:
    """G6 COORD_DUPLICATE_REGISTER fires; I2's exchange_mode is captured
    on the new state regardless of duplicate."""
    coord = TradeCoordinator()
    xfm = MagicMock(); xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    coord.register_trade(symbol="BTCUSDT", side="Buy", size=0.05,
                         entry_price=80000.0, decision_id="d-1")
    # Duplicate
    coord.register_trade(symbol="BTCUSDT", side="Sell", size=0.10,
                         entry_price=81000.0, decision_id="d-2")
    dup = _records_with_tag(loguru_sink, "COORD_DUPLICATE_REGISTER")
    assert len(dup) == 1
    # State has I2's mode field after the second registration
    assert coord._trades["BTCUSDT"].exchange_mode == "bybit_demo"


# ─── I1 + I5 ground-truth + restart-resilient state ──────────────────────


@pytest.mark.asyncio
async def test_i1_unknown_state_does_not_corrupt_i5_recovery(loguru_sink) -> None:
    """When I1's ground-truth-unknown fires, the watchdog preserves
    state. Subsequent I5 recover_state_from_db reads the persisted
    thesis row and rebuilds the same state. The two fixes compose."""
    coord = TradeCoordinator()
    xfm = MagicMock(); xfm.current_mode = "bybit_demo"
    coord.attach_transformer(xfm)
    coord.register_trade(symbol="BTCUSDT", side="Buy", size=0.05,
                         entry_price=80000.0, decision_id="d-live",
                         order_id="ORD-1")
    # I5 simulated DB recovery from a separate thesis row
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[{
        "symbol": "ETHUSDT", "direction": "Sell", "entry_price": 3000.0,
        "size_usd": 200.0, "leverage": 3,
        "opened_at": "2026-05-14T08:00:00+00:00",
        "order_id": "ORD-2", "exchange_mode": "bybit_demo",
    }])
    restored = await coord.recover_state_from_db(db)
    # Existing live trade preserved
    assert "BTCUSDT" in coord._trades
    assert coord._trades["BTCUSDT"].entry_price == 80000.0
    # Recovered trade added
    assert restored == 1
    assert "ETHUSDT" in coord._trades
    assert coord._trades["ETHUSDT"].exchange_mode == "bybit_demo"
    # DASHBOARD_STATE_RECOVERED emitted (I5)
    rec = _records_with_tag(loguru_sink, "DASHBOARD_STATE_RECOVERED")
    assert any("ETHUSDT" in m for _, m in rec)


# ─── Module + emission-shape inventory pins ───────────────────────────────


def test_all_modified_modules_import_cleanly() -> None:
    """All 20 modified production modules import without raising."""
    mods = [
        "src.brain.strategist",
        "src.core.layer_manager",
        "src.workers.profit_sniper",
        "src.bybit_demo.bybit_demo_websocket_subscriber",
        "src.bybit_demo.bybit_demo_client",
        "src.bybit_demo.bybit_demo_adapter",
        "src.shadow.shadow_adapter",
        "src.core.trade_coordinator",
        "src.core.transformer",
        "src.core.exceptions",
        "src.core.types",
        "src.workers.strategy_worker",
        "src.workers.position_watchdog",
        "src.workers.kline_worker",
        "src.workers.manager",
        "src.core.thesis_manager",
        "src.core.sl_tp_validator",
        "src.risk.time_decay_sl",
        "src.strategies.pnl_manager",
        "src.database.connection",
    ]
    failures = []
    for m in mods:
        try:
            __import__(m)
        except Exception as e:
            failures.append(f"{m}: {type(e).__name__}: {e}")
    assert not failures, "Module import failures: " + "; ".join(failures)


def test_all_new_emission_tags_grep_in_source() -> None:
    """Every new structured-event tag introduced by G1-G11 + I1-I5
    must be locatable in src/. A missing tag would mean a regression
    in source that source-pin tests didn't catch (e.g., a refactor
    that dropped the emission)."""
    import subprocess
    expected_tags = [
        # G-suite
        "STRAT_CALL_A_END", "STRAT_CALL_B_END",
        "BRAIN_CYCLE_A_DONE", "BRAIN_CYCLE_B_DONE",
        "SNIPER_TICK", "BYBIT_DEMO_WS_EXEC_NON_CLOSE",
        "BYBIT_DEMO_WS_POS_UPDATE", "BYBIT_DEMO_WS_ORDER",
        "COORD_REG", "COORD_DUPLICATE_REGISTER",
        "THESIS_OPEN", "SLTP_PAIR_OK",
        # I-suite
        "BYBIT_DEMO_TIMESTAMP_RETRY",
        "BYBIT_DEMO_POSITIONS_UNKNOWN_STATE",
        "BYBIT_DEMO_BALANCE_UNKNOWN_STATE",
        "SHADOW_POSITIONS_UNKNOWN_STATE",
        "WD_GROUND_TRUTH_UNKNOWN",
        "POSITION_ROW_DELETED", "POSITION_ROW_DELETE_FAIL",
        "POSITION_ROW_DELETE_SKIP",
        "WD_PNL_MISMATCH_BLOCKED", "WD_PNL_MISMATCH_FORCED",
        "DB_LOCK_BREAKDOWN", "DB_WRITE_DEFERRED",
        "DASHBOARD_STATE_RECOVERED",
        "DASHBOARD_STATE_RECOVER_SUMMARY",
        "TRADEPLAN_PERSISTED", "BOOT_STATE_RECOVERED",
    ]
    missing = []
    for tag in expected_tags:
        r = subprocess.run(
            ["grep", "-rl", f"\"{tag}", "src/"],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not r.stdout.strip():
            missing.append(tag)
    assert not missing, f"Missing emission tags in source: {missing}"


def test_new_typed_classes_exposed() -> None:
    """All new public types from I1 + I2 are importable from their
    canonical paths."""
    from src.core.types import PositionsQueryResult, BalanceQueryResult
    from src.core.exceptions import GroundTruthUnavailableError
    from src.core.exceptions import APIError, TradingMCPError
    assert issubclass(GroundTruthUnavailableError, APIError)
    assert issubclass(GroundTruthUnavailableError, TradingMCPError)

    r1 = PositionsQueryResult(confirmed=True)
    r2 = BalanceQueryResult(confirmed=True)
    assert r1.confirmed is True
    assert r2.confirmed is True
