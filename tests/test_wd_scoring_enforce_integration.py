"""Integration tests for the watchdog-side enforce-mode branches.

C1 Phase 1.5c (2026-05-21). The pure-function scoring tests in
``tests/test_wd_brain_scoring.py`` exercise ``compute_brain_close_score``
in isolation. These tests exercise the watchdog harness — the actual
intercept inside ``PositionWatchdog._execute_strategic_actions`` —
with mocked services so the three enforce-mode branches and the
log-only fall-through can be verified end-to-end before the operator
flips ``wd_brain_scoring_enforce`` in config.toml.

Each test:

1. Builds a real ``PositionWatchdog`` with mocked services via the
   shared ``_make_watchdog`` helper from
   ``tests/test_watchdog/test_position_watchdog.py``.
2. Stubs ``coordinator.drain_strategic_actions`` to return a single
   brain ``close`` action so the scoring intercept fires.
3. Stubs ``position_service.get_position`` to return a ``Position``
   whose state (entry, mark, SL, side) drives the composite into the
   target branch (execute / reject / reject_and_tighten).
4. Calls ``await wd._execute_strategic_actions()``.
5. Asserts the downstream calls: ``close_position`` invoked or not,
   ``_tighten_sl_breakeven_30pct`` invoked or not, and the
   ``_scoring_skip_close`` semantics held.

These are integration tests at the function level — no DB, no
network, no Claude. The asserts focus on the contract the
flag flip relies on: under enforce=True, sub-threshold brain
closes are blocked; under enforce=False (log-only) they fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import (
    AlertSettings,
    AltDataSettings,
    BrainSettings,
    BybitSettings,
    DatabaseSettings,
    FinnhubSettings,
    GeneralSettings,
    MCPSettings,
    RedditSettings,
    RiskSettings,
    Settings,
    WatchdogSettings,
    WorkerSettings,
)
from src.core.types import Order, OrderType, Position, Side
from src.workers.position_watchdog import PositionWatchdog


@dataclass
class _StubTradePlan:
    """Minimal TradePlan-shaped stub for the coordinator."""
    remaining_minutes: float = 35.0
    stop_loss_price: float = 0.0


def _make_settings(
    *,
    scoring_enabled: bool,
    enforce: bool,
    threshold: float = 6.0,
    min_hold_seconds: float = 0.0,
    tmp_path,
) -> Settings:
    """Build a Settings with the watchdog scoring fields wired explicitly.

    The min_hold_seconds default of 0.0 disables the 300s min-hold
    guardrail so the scoring intercept actually fires for the fresh
    positions in these tests. Production min-hold is preserved by the
    config.toml default (300.0) — these tests deliberately bypass it
    to exercise the scoring branch.
    """
    return Settings(
        general=GeneralSettings(mode="paper", log_dir=str(tmp_path / "logs")),
        bybit=BybitSettings(
            testnet=True, api_key="k", api_secret="s",
            default_symbols=["INJUSDT"],
        ),
        finnhub=FinnhubSettings(enabled=False),
        reddit=RedditSettings(enabled=False),
        altdata=AltDataSettings(),
        database=DatabaseSettings(path=str(tmp_path / "wd_test.db")),
        workers=WorkerSettings(max_consecutive_failures=3, restart_delay=1),
        brain=BrainSettings(enabled=True, api_key="sk-test"),
        risk=RiskSettings(),
        alerts=AlertSettings(telegram_enabled=False),
        watchdog=WatchdogSettings(
            enabled=True,
            check_interval_seconds=1,
            loss_warning_pct=1.0,
            trailing_loss_pct=0.5,
            sl_proximity_pct=30.0,
            rapid_move_pct=0.5,
            brain_trigger_loss_pct=1.5,
            brain_cooldown_seconds=5,
            partial_close_pct=50.0,
            max_brain_calls_per_hour=10,
            wd_brain_scoring_enabled=scoring_enabled,
            wd_brain_scoring_enforce=enforce,
            wd_brain_scoring_threshold=threshold,
            strategic_action_min_hold_seconds=min_hold_seconds,
        ),
        mcp=MCPSettings(),
    )


def _make_position(
    *,
    symbol: str = "INJUSDT",
    side: Side = Side.BUY,
    entry: float = 100.0,
    mark: float = 99.0,
    sl: float = 95.0,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        size=1.0,
        entry_price=entry,
        mark_price=mark,
        unrealized_pnl=(mark - entry) * (1 if side == Side.BUY else -1),
        leverage=5,
        stop_loss=sl,
    )


def _make_async_db() -> MagicMock:
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    db.fetch_one = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=None)
    db.executemany = AsyncMock(return_value=None)
    return db


def _make_coordinator(action: dict, age_seconds: float = 600.0) -> MagicMock:
    coord = MagicMock()
    coord.is_immune = MagicMock(return_value=(False, 0.0, ""))
    coord.get_maturity = MagicMock(return_value=(True, "mature", ""))
    coord.get_trade_plan = MagicMock(
        return_value=_StubTradePlan(remaining_minutes=35.0, stop_loss_price=95.0)
    )
    coord.get_age_seconds = MagicMock(return_value=age_seconds)
    coord.cleanup_stale = MagicMock(return_value=None)
    coord.update_peak_pnl = MagicMock(return_value=None)
    coord.queue_strategic_action = MagicMock(return_value=None)
    coord.set_close_reason = MagicMock(return_value=None)
    coord.peek_pending_actions = MagicMock(return_value=[action])
    coord.drain_strategic_actions = MagicMock(return_value=[action])
    coord.get_trade_info = MagicMock(return_value={})
    return coord


def _make_position_service(pos: Position) -> MagicMock:
    svc = MagicMock()
    svc.get_position = AsyncMock(return_value=pos)
    svc.get_positions = AsyncMock(return_value=[pos])
    svc.close_position = AsyncMock(return_value=Order(
        order_id="close_001", symbol=pos.symbol, side=Side.SELL,
        order_type=OrderType.MARKET, price=0.0, qty=pos.size,
    ))
    svc.set_stop_loss = AsyncMock(return_value=True)
    return svc


def _make_thesis_manager(entry_sl: float) -> MagicMock:
    tm = MagicMock()
    tm.get_open_thesis_for_symbol = AsyncMock(return_value={
        "stop_loss_price": entry_sl,
        "take_profit_price": 0.0,
        "leverage": 5,
        "thesis_state": "VALID",
    })
    return tm


def _build_wd(
    *,
    settings: Settings,
    position: Position,
    action: dict,
    age_seconds: float = 600.0,
    entry_sl: float = 95.0,
) -> PositionWatchdog:
    wd = PositionWatchdog(
        settings=settings,
        db=_make_async_db(),
        position_service=_make_position_service(position),
        market_service=MagicMock(),
        trade_coordinator=_make_coordinator(action, age_seconds=age_seconds),
        thesis_manager=_make_thesis_manager(entry_sl),
    )
    # Replace the tightening helper with a counted AsyncMock so the
    # test can assert it was called exactly once (or not at all) on
    # the reject_and_tighten path. The real helper delegates to
    # _push_sl_to_shadow which requires a wired sl_gateway; we are
    # asserting orchestration, not the push itself (the push helper
    # is already covered by its own tests).
    wd._tighten_sl_breakeven_30pct = AsyncMock(return_value=True)
    return wd


# ────────────────────────────────────────────────────────────────────
# Branch 1 — log-only mode (current production state)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_only_mode_does_not_block_brain_close(tmp_path):
    """enforce=False — scoring runs, would-be recommendation is logged,
    but the brain close fires regardless. Mirrors the current 2026-05-20
    log-only production behaviour."""
    settings = _make_settings(
        scoring_enabled=True, enforce=False, tmp_path=tmp_path,
    )
    # PnL -1.0% on a long → shallow_loser/moderate_loser bucket → composite
    # well below 6.0 → would_be=reject. But enforce=False, so close fires.
    pos = _make_position(side=Side.BUY, entry=100.0, mark=99.0, sl=95.0)
    action = {"symbol": "INJUSDT", "action": "close", "reason": "loss"}
    wd = _build_wd(settings=settings, position=pos, action=action)

    await wd._execute_strategic_actions()

    # Brain close fired despite the below-threshold score
    wd.position_service.close_position.assert_awaited_once()
    # Tightening was NOT invoked in log-only mode
    wd._tighten_sl_breakeven_30pct.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────
# Branch 2 — enforce + reject (0 <= composite < threshold)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_rejects_close_when_composite_below_threshold(tmp_path):
    """enforce=True with a composite in the [0, threshold) band — close
    is blocked, no tightening (the reject_and_tighten band is composite
    < 0). The position is held."""
    settings = _make_settings(
        scoring_enabled=True, enforce=True, tmp_path=tmp_path,
    )
    # Construct factor inputs that land in the reject band (composite in
    # [0, 6.0)):
    #   PnL -0.20% → shallow_loser (-3.0)
    #   time_remaining 35 min → deep (-2.0)
    #   age 600s → young (-1.0)
    #   velocity 0 → stationary (0.0)
    #   sl_consumption 50% → comfortable (-1.0)
    #   xray broken (+2.0)
    #   reasoning structural (+2.0)
    # Composite = -3 - 2 - 1 + 0 - 1 + 2 + 2 = -3.0 → reject_and_tighten.
    # To bump into reject band (>= 0) we need PnL > -0.5 AND a winning
    # factor configuration. Use PnL +0.5% (weak_winner +0.5) + the rest:
    #   weak_winner (+0.5) + deep (-2) + aged_losing (1.0, but pnl > 0
    #     so age_bucket is mature 0.0) + stationary (0) + comfortable
    #     (-1) + broken (+2) + structural (+2) = 1.5 → reject band.
    pos = _make_position(side=Side.BUY, entry=100.0, mark=100.5, sl=95.0)
    action = {
        "symbol": "INJUSDT",
        "action": "close",
        "reason": "structural setup broken on a regime reversal",
    }
    wd = _build_wd(settings=settings, position=pos, action=action)

    # Make structure_cache surface a broken xray so the scoring reaches
    # the reject band. The intercept walks structure_cache.get(symbol);
    # we feed it a stub.
    _xray = MagicMock()
    _xray.trade_direction = "short"   # opposite of Buy
    _xray.age_seconds = 5.0
    wd.structure_cache = MagicMock()
    wd.structure_cache.get = MagicMock(return_value=_xray)

    await wd._execute_strategic_actions()

    # Brain close was BLOCKED
    wd.position_service.close_position.assert_not_awaited()
    # Tightening was NOT called (this is the reject band, not reject_and_tighten)
    wd._tighten_sl_breakeven_30pct.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────
# Branch 3 — enforce + reject_and_tighten (composite < 0)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_blocks_close_and_tightens_sl_when_composite_negative(tmp_path):
    """enforce=True with composite < 0 — close blocked AND
    _tighten_sl_breakeven_30pct is invoked once. Matches the historical
    pattern: 25 of 28 scored events were in this band."""
    settings = _make_settings(
        scoring_enabled=True, enforce=True, tmp_path=tmp_path,
    )
    # Reproduce the INJUSDT 2026-05-20 11:16:08 historical event geometry:
    #   PnL -0.4362% → shallow_loser (-3.0)
    #   time_remaining ~37 min → deep (-2.0)
    #   age ~480s → young (-1.0)
    #   velocity 0 → stationary (0.0)
    #   sl_consumption ~44.9% → comfortable (-1.0)
    #   xray broken (+2.0)
    #   reasoning structural (+2.0)
    # Composite = -3.0 (matches the live event composite=-3.0)
    # → recommendation=reject_and_tighten.
    pos = _make_position(side=Side.BUY, entry=100.0, mark=99.55, sl=95.0)
    action = {
        "symbol": "INJUSDT",
        "action": "close",
        "reason": "structural setup broken on a regime reversal",
    }
    wd = _build_wd(
        settings=settings, position=pos, action=action, age_seconds=480.0,
    )
    _xray = MagicMock()
    _xray.trade_direction = "short"
    _xray.age_seconds = 5.0
    wd.structure_cache = MagicMock()
    wd.structure_cache.get = MagicMock(return_value=_xray)

    await wd._execute_strategic_actions()

    wd.position_service.close_position.assert_not_awaited()
    wd._tighten_sl_breakeven_30pct.assert_awaited_once()
    # Tightening was called with the position object
    _called_args = wd._tighten_sl_breakeven_30pct.await_args
    assert _called_args.args[0] is pos


# ────────────────────────────────────────────────────────────────────
# Branch 4 — enforce + execute (composite >= threshold)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforce_executes_close_when_composite_clears_threshold(tmp_path):
    """enforce=True with composite >= threshold — close fires normally.
    Reuses the high-conviction scenario from the pure-function tests
    (a strong winner with a broken XRAY)."""
    settings = _make_settings(
        scoring_enabled=True, enforce=True, threshold=4.0, tmp_path=tmp_path,
    )
    # PnL +1.5% (strong_winner +3.0) + mature age (0) + stationary (0)
    # + spacious SL (-2.0 — but we want above threshold)... actually
    # let's set up a strong-winner + imminent deadline + broken XRAY +
    # structural reasoning + tight SL = 3 + 1 + 0 + 0 + 0 + 2 + 2 = +8 >= 4.
    pos = _make_position(side=Side.BUY, entry=100.0, mark=101.5, sl=98.5)
    action = {
        "symbol": "INJUSDT",
        "action": "close",
        "reason": "structural setup invalidation regime reversal — locking profit",
    }
    wd = _build_wd(settings=settings, position=pos, action=action)
    # Tight SL bucket to keep composite positive; imminent deadline:
    wd.coordinator.get_trade_plan = MagicMock(
        return_value=_StubTradePlan(remaining_minutes=3.0, stop_loss_price=98.5)
    )
    _xray = MagicMock()
    _xray.trade_direction = "short"
    _xray.age_seconds = 5.0
    wd.structure_cache = MagicMock()
    wd.structure_cache.get = MagicMock(return_value=_xray)

    await wd._execute_strategic_actions()

    # The execute branch fires brain's close
    wd.position_service.close_position.assert_awaited_once()
    wd._tighten_sl_breakeven_30pct.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────
# Branch 5 — kill-switch (wd_brain_scoring_enabled=False)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scoring_disabled_skips_intercept_entirely(tmp_path):
    """wd_brain_scoring_enabled=False — the scoring path is bypassed
    completely; brain's close fires immediately (legacy behaviour
    pre-Issue 1). The kill switch is the operator's instant fallback if
    enforce mode ever needs to be disabled without flipping enforce
    alone."""
    settings = _make_settings(
        scoring_enabled=False, enforce=True, tmp_path=tmp_path,
    )
    pos = _make_position(side=Side.BUY, entry=100.0, mark=99.55, sl=95.0)
    action = {
        "symbol": "INJUSDT",
        "action": "close",
        "reason": "structural setup broken",
    }
    wd = _build_wd(settings=settings, position=pos, action=action)

    await wd._execute_strategic_actions()

    # Brain close fired regardless of what enforce mode would have decided
    wd.position_service.close_position.assert_awaited_once()
    wd._tighten_sl_breakeven_30pct.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────
# Branch 6 — kill-switch + enforce=False (the most permissive state)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scoring_disabled_and_enforce_false_still_passes_through(tmp_path):
    """Both kill switch off and log-only mode: brain close fires.
    The scoring intercept is fully short-circuited, including the
    WATCHDOG_CLOSE_SCORE_COMPUTED emission. WD_SCORING_PATH_REACHED
    still fires per IMPLEMENT_FIVE_ISSUES_FIX Rule 7."""
    settings = _make_settings(
        scoring_enabled=False, enforce=False, tmp_path=tmp_path,
    )
    pos = _make_position(side=Side.BUY, entry=100.0, mark=99.55, sl=95.0)
    action = {
        "symbol": "INJUSDT",
        "action": "close",
        "reason": "any reason",
    }
    wd = _build_wd(settings=settings, position=pos, action=action)

    await wd._execute_strategic_actions()

    wd.position_service.close_position.assert_awaited_once()
    wd._tighten_sl_breakeven_30pct.assert_not_awaited()
