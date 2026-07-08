"""Comprehensive tests for Position Watchdog worker."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.types import (
    AlertLevel,
    Order,
    OrderType,
    Position,
    Side,
    Ticker,
    WatchdogDecision,
)
from src.core.utils import generate_id, now_utc
from src.workers.position_watchdog import PositionWatchdog


def _make_async_db_mock() -> MagicMock:
    """Build a DB mock with every async method pre-wired as AsyncMock.

    Root cause of one of the original test failures: PositionWatchdog
    wraps ``db`` in a real ``MarketRepository`` which calls
    ``await self.db.fetch_all(...)`` / ``execute`` / ``executemany`` /
    ``fetch_one``. A plain ``MagicMock()`` returns MagicMock instances
    for those methods, and ``await mock_method()`` raises
    ``TypeError: object MagicMock can't be used in 'await' expression``.

    Pre-wiring the async surface here keeps every watchdog test off the
    same trap. Tests that need a deterministic return override
    ``mock_db.fetch_all.return_value = ...`` after construction.
    """
    mock_db = MagicMock()
    mock_db.fetch_all = AsyncMock(return_value=[])
    mock_db.fetch_one = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=None)
    mock_db.executemany = AsyncMock(return_value=None)
    return mock_db


def _make_mature_coordinator() -> MagicMock:
    """Build a TradeCoordinator mock that bypasses immunity / maturity gates.

    Root cause of the second wave of original test failures:
    PositionWatchdog gates monitoring with two coordinator-driven
    checks (lines 526-544 of position_watchdog.py):

    1. ``is_immune(symbol)`` — newly-opened positions get a 60-120s
       grace window during which the watchdog must NOT raise alerts
       (production safety: avoid spam on freshly-placed orders).
    2. ``get_maturity(symbol, pnl, sl_prox)`` — universal 120s "newborn"
       phase gate after the SL Hierarchy overhaul.

    Without an injected coordinator both fall to a per-symbol fallback
    that records ``open_time = now()`` on first sight and refuses to
    monitor for ``MINIMUM_HOLD_SECONDS`` (5 min default). That makes
    every "tick now and assert an alert fired" test fail because the
    position is immune for the entire test.

    The mature stub here returns:
      ``is_immune`` → ``(False, 0, "")``  (never immune)
      ``get_maturity`` → ``(True, "mature", "")`` (always monitorable)
      ``get_trade_plan`` → ``None``       (no plan; per-pos plan logic skipped)
      ``cleanup_stale`` / ``update_peak_pnl`` → no-op
    Tests that DO want to exercise immunity/maturity behavior pass
    their own coordinator via the ``coordinator=`` kwarg.
    """
    coord = MagicMock()
    coord.is_immune = MagicMock(return_value=(False, 0.0, ""))
    coord.get_maturity = MagicMock(return_value=(True, "mature", ""))
    coord.get_trade_plan = MagicMock(return_value=None)
    coord.cleanup_stale = MagicMock(return_value=None)
    coord.update_peak_pnl = MagicMock(return_value=None)
    coord.queue_strategic_action = MagicMock(return_value=None)
    coord.set_close_reason = MagicMock(return_value=None)
    coord.peek_pending_actions = MagicMock(return_value=[])
    coord.dequeue_strategic_actions = MagicMock(return_value=[])

    # Phase 1 of the price-source-divergence fix introduced the async
    # ``resolve_authoritative_pnl`` helper at ``trade_coordinator.py:406``;
    # every self-initiated close site in position_watchdog now awaits it
    # before calling ``on_trade_closed``. The default mock mirrors the
    # caller-supplied fallbacks back as ``local_fallback`` so existing
    # tests (which don't simulate Shadow) keep their original semantics.
    async def _mirror_fallback(
        *,
        symbol,
        position_service,
        fallback_pnl_usd,
        fallback_pnl_pct,
        fallback_exit_price=None,
        **_hints,  # absorb identity hints (order_id/qty/ws_exec_price/...)
    ):
        return (
            fallback_pnl_usd, fallback_pnl_pct,
            "local_fallback", fallback_exit_price,
        )
    coord.resolve_authoritative_pnl = AsyncMock(side_effect=_mirror_fallback)
    return coord


def _make_watchdog(
    settings,
    db=None,
    position_service=None,
    market_service=None,
    order_service=None,
    account_service=None,
    claude_client=None,
    cost_tracker=None,
    decision_parser=None,
    risk_manager=None,
    alert_manager=None,
    ta_engine=None,
    trade_coordinator=None,
):
    """Helper to construct a PositionWatchdog with mocked dependencies.

    Defaults are chosen so a barebones ``await wd.tick()`` actually
    monitors the supplied positions:

    * ``db`` → AsyncMock-pre-wired so ``MarketRepository`` works.
    * ``trade_coordinator`` → mature stub so positions are not gated
      by the 60-120s immunity window or the 300s minimum-hold fallback.

    Tests that want to exercise the gates explicitly override these.
    """
    return PositionWatchdog(
        settings=settings,
        db=db or _make_async_db_mock(),
        position_service=position_service or MagicMock(),
        market_service=market_service or MagicMock(),
        order_service=order_service,
        account_service=account_service,
        claude_client=claude_client,
        cost_tracker=cost_tracker,
        decision_parser=decision_parser,
        risk_manager=risk_manager,
        alert_manager=alert_manager,
        ta_engine=ta_engine,
        trade_coordinator=trade_coordinator if trade_coordinator is not None else _make_mature_coordinator(),
    )


# =============================================================================
# PnL Calculation Tests
# =============================================================================

class TestPnLCalculation:
    def test_long_position_loss(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=69000,
            unrealized_pnl=-10, leverage=2,
        )
        pnl = PositionWatchdog._calculate_pnl_pct(pos, 69000)
        assert pnl == pytest.approx(-1.4286, rel=1e-3)

    def test_short_position_loss(self):
        pos = Position(
            symbol="ETHUSDT", side=Side.SELL, size=0.1,
            entry_price=3500, mark_price=3600,
            unrealized_pnl=-10, leverage=2,
        )
        pnl = PositionWatchdog._calculate_pnl_pct(pos, 3600)
        assert pnl == pytest.approx(-2.857, rel=1e-3)

    def test_long_position_profit(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=71000,
            unrealized_pnl=10, leverage=2,
        )
        pnl = PositionWatchdog._calculate_pnl_pct(pos, 71000)
        assert pnl > 0

    def test_short_position_profit(self):
        pos = Position(
            symbol="ETHUSDT", side=Side.SELL, size=0.1,
            entry_price=3500, mark_price=3400,
            unrealized_pnl=10, leverage=2,
        )
        pnl = PositionWatchdog._calculate_pnl_pct(pos, 3400)
        assert pnl > 0

    def test_zero_entry_price(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=0, mark_price=70000,
        )
        pnl = PositionWatchdog._calculate_pnl_pct(pos, 70000)
        assert pnl == 0.0


# =============================================================================
# SL Proximity Tests
# =============================================================================

class TestSLProximity:
    def test_long_near_sl(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68500,
            stop_loss=68000,
        )
        # SL distance from entry = 70000 - 68000 = 2000
        # Current to SL = 68500 - 68000 = 500
        # Proximity = (1 - 500/2000) * 100 = 75%
        prox = PositionWatchdog._calculate_sl_proximity(pos, 68500)
        assert prox == pytest.approx(75.0)

    def test_short_near_sl(self):
        pos = Position(
            symbol="ETHUSDT", side=Side.SELL, size=0.1,
            entry_price=3500, mark_price=3680,
            stop_loss=3700,
        )
        # SL distance from entry = 3700 - 3500 = 200
        # Current to SL = 3700 - 3680 = 20
        # Proximity = (1 - 20/200) * 100 = 90%
        prox = PositionWatchdog._calculate_sl_proximity(pos, 3680)
        assert prox == pytest.approx(90.0)

    def test_no_stop_loss(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=69000,
            stop_loss=None,
        )
        prox = PositionWatchdog._calculate_sl_proximity(pos, 69000)
        assert prox is None

    def test_far_from_sl(self):
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=70000,
            stop_loss=68000,
        )
        prox = PositionWatchdog._calculate_sl_proximity(pos, 70000)
        assert prox == pytest.approx(0.0)


# =============================================================================
# Tick — No Positions / Profitable
# =============================================================================

class TestTickNoAction:
    @pytest.mark.asyncio
    async def test_no_positions_noop(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_alert_manager,
    ):
        mock_position_service.get_positions = AsyncMock(return_value=[])
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        await wd.tick()

        mock_market_service.get_ticker.assert_not_called()
        mock_alert_manager.send_watchdog_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_profitable_position_no_warnings(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_alert_manager, sample_profitable_position,
    ):
        mock_position_service.get_positions = AsyncMock(
            return_value=[sample_profitable_position],
        )
        # Price higher than entry = profitable LONG
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=71000, bid=70999, ask=71001,
            high_24h=72000, low_24h=69000, volume_24h=5000, change_24h_pct=1.5,
        ))
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        await wd.tick()

        mock_alert_manager.send_watchdog_alert.assert_not_called()


# =============================================================================
# Warning Detection Tests
# =============================================================================

class TestWarningDetection:
    @pytest.mark.asyncio
    async def test_loss_warning_triggered(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_alert_manager, sample_long_position,
    ):
        """Position down > 1% from entry should trigger WARNING."""
        mock_position_service.get_positions = AsyncMock(
            return_value=[sample_long_position],
        )
        # Entry 70000, current 69000 = -1.43% -> exceeds loss_warning_pct (1.0%)
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=69000, bid=68999, ask=69001,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.5,
        ))
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        await wd.tick()

        mock_alert_manager.send_watchdog_alert.assert_called_once()
        call_kwargs = mock_alert_manager.send_watchdog_alert.call_args
        assert call_kwargs.kwargs["position"] == sample_long_position
        assert call_kwargs.kwargs["pnl_pct"] < -1.0

    @pytest.mark.asyncio
    async def test_sl_proximity_critical(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_alert_manager,
    ):
        """Price within 30% of distance to SL should trigger CRITICAL."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68500,
            unrealized_pnl=-15, leverage=2,
            stop_loss=68000, take_profit=73000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])
        # Proximity = 75% (> 30% threshold)
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=68500, bid=68499, ask=68501,
            high_24h=71000, low_24h=68000, volume_24h=5000, change_24h_pct=-2.0,
        ))
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        await wd.tick()

        mock_alert_manager.send_watchdog_alert.assert_called_once()
        call_kwargs = mock_alert_manager.send_watchdog_alert.call_args
        assert call_kwargs.kwargs["severity"] == AlertLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_rapid_move_critical(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_alert_manager,
    ):
        """Large price drop between ticks should trigger CRITICAL."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=69000,
            unrealized_pnl=-10, leverage=2,
            stop_loss=68000, take_profit=73000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        # Seed initial price
        wd._last_prices["BTCUSDT"] = 69500

        # Price dropped from 69500 to 69000 = -0.72% (> 0.5% rapid_move_pct)
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=69000, bid=68999, ask=69001,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.5,
        ))
        await wd.tick()

        mock_alert_manager.send_watchdog_alert.assert_called_once()
        call_kwargs = mock_alert_manager.send_watchdog_alert.call_args
        assert call_kwargs.kwargs["severity"] == AlertLevel.CRITICAL


# =============================================================================
# Brain Trigger Tests
# =============================================================================

class TestBrainTrigger:
    @pytest.mark.asyncio
    async def test_brain_triggered_on_severe_loss(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_claude_client, cost_tracker, decision_parser,
        mock_alert_manager, mock_ta_engine, mock_account_service,
    ):
        """Brain called when loss exceeds brain_trigger_loss_pct (1.5%)."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68800,
            unrealized_pnl=-12, leverage=2,
            stop_loss=67000, take_profit=73000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])
        # Entry 70000, current 68800 = -1.71% (> 1.5% brain trigger)
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=68800, bid=68799, ask=68801,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.7,
        ))

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            claude_client=mock_claude_client,
            cost_tracker=cost_tracker,
            decision_parser=decision_parser,
            alert_manager=mock_alert_manager,
            ta_engine=mock_ta_engine,
            account_service=mock_account_service,
        )
        await wd.tick()

        mock_claude_client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_brain_cooldown_prevents_spam(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_claude_client, cost_tracker, decision_parser,
        mock_alert_manager, mock_account_service,
    ):
        """Same symbol not called twice within cooldown period."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68800,
            unrealized_pnl=-12, leverage=2,
            stop_loss=67000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=68800, bid=68799, ask=68801,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.7,
        ))

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            claude_client=mock_claude_client,
            cost_tracker=cost_tracker,
            decision_parser=decision_parser,
            alert_manager=mock_alert_manager,
            account_service=mock_account_service,
        )

        # First tick triggers brain
        await wd.tick()
        assert mock_claude_client.send_message.call_count == 1

        # Second tick within cooldown (5s) should NOT trigger brain again
        await wd.tick()
        assert mock_claude_client.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_max_brain_calls_per_hour(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_claude_client, cost_tracker, decision_parser,
        mock_alert_manager, mock_account_service,
    ):
        """Rate limit enforced after max_brain_calls_per_hour reached."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68800,
            unrealized_pnl=-12, leverage=2,
            stop_loss=67000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=68800, bid=68799, ask=68801,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.7,
        ))

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            claude_client=mock_claude_client,
            cost_tracker=cost_tracker,
            decision_parser=decision_parser,
            alert_manager=mock_alert_manager,
            account_service=mock_account_service,
        )

        # Simulate hourly limit already hit
        wd._brain_calls_this_hour = watchdog_settings.watchdog.max_brain_calls_per_hour

        await wd.tick()
        mock_claude_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_budget_exceeded_skips_brain(
        self, watchdog_settings, mock_position_service, mock_market_service,
        mock_claude_client, decision_parser,
        mock_alert_manager, mock_account_service,
    ):
        """Brain not called when daily budget is exhausted."""
        from src.brain.cost_tracker import CostTracker
        tracker = CostTracker(daily_budget_usd=0.001)
        # Exhaust the budget
        tracker.record_call(100000, 50000)

        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=68800,
            unrealized_pnl=-12, leverage=2,
            stop_loss=67000,
        )
        mock_position_service.get_positions = AsyncMock(return_value=[pos])
        mock_market_service.get_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT", last_price=68800, bid=68799, ask=68801,
            high_24h=71000, low_24h=68500, volume_24h=5000, change_24h_pct=-1.7,
        ))

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
            claude_client=mock_claude_client,
            cost_tracker=tracker,
            decision_parser=decision_parser,
            alert_manager=mock_alert_manager,
            account_service=mock_account_service,
        )
        await wd.tick()

        mock_claude_client.send_message.assert_not_called()


# =============================================================================
# Stop-Loss Validation Tests (SAFETY CRITICAL)
# =============================================================================

class TestTightenStopValidation:
    @pytest.mark.asyncio
    async def test_tighten_stop_long_valid(
        self, watchdog_settings, mock_position_service, sample_long_position,
    ):
        """LONG: new SL 69000 > current SL 68000 = ACCEPTED."""
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="BTCUSDT",
            confidence=0.8, new_stop_loss=69000,
        )
        result = await wd._execute_tighten_stop(sample_long_position, decision)

        assert result["executed"] is True
        mock_position_service.set_stop_loss.assert_called_once_with("BTCUSDT", 69000)

    @pytest.mark.asyncio
    async def test_tighten_stop_long_rejected(
        self, watchdog_settings, mock_position_service, sample_long_position,
    ):
        """LONG: new SL 67000 < current SL 68000 = REJECTED (would widen)."""
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="BTCUSDT",
            confidence=0.8, new_stop_loss=67000,
        )
        result = await wd._execute_tighten_stop(sample_long_position, decision)

        assert result["executed"] is False
        assert "not tighter" in result["error"]
        mock_position_service.set_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_tighten_stop_short_valid(
        self, watchdog_settings, mock_position_service, sample_short_position,
    ):
        """SHORT: new SL 3650 < current SL 3700 = ACCEPTED."""
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="ETHUSDT",
            confidence=0.8, new_stop_loss=3650,
        )
        result = await wd._execute_tighten_stop(sample_short_position, decision)

        assert result["executed"] is True
        mock_position_service.set_stop_loss.assert_called_once_with("ETHUSDT", 3650)

    @pytest.mark.asyncio
    async def test_tighten_stop_short_rejected(
        self, watchdog_settings, mock_position_service, sample_short_position,
    ):
        """SHORT: new SL 3800 > current SL 3700 = REJECTED (would widen)."""
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="ETHUSDT",
            confidence=0.8, new_stop_loss=3800,
        )
        result = await wd._execute_tighten_stop(sample_short_position, decision)

        assert result["executed"] is False
        assert "not tighter" in result["error"]
        mock_position_service.set_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_tighten_stop_no_existing_sl(
        self, watchdog_settings, mock_position_service,
    ):
        """No current SL — any new SL should be accepted."""
        pos = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=69000,
            stop_loss=None,
        )
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="BTCUSDT",
            confidence=0.8, new_stop_loss=68000,
        )
        result = await wd._execute_tighten_stop(pos, decision)

        assert result["executed"] is True
        mock_position_service.set_stop_loss.assert_called_once_with("BTCUSDT", 68000)

    @pytest.mark.asyncio
    async def test_tighten_stop_no_new_sl_provided(
        self, watchdog_settings, mock_position_service, sample_long_position,
    ):
        """tighten_stop with no new_stop_loss = rejected."""
        wd = _make_watchdog(
            watchdog_settings, position_service=mock_position_service,
        )
        decision = WatchdogDecision(
            id="test", action="tighten_stop", symbol="BTCUSDT",
            confidence=0.8, new_stop_loss=None,
        )
        result = await wd._execute_tighten_stop(sample_long_position, decision)

        assert result["executed"] is False
        assert "no new_stop_loss" in result["error"]


# =============================================================================
# Partial Close Tests
# =============================================================================

class TestPartialClose:
    @pytest.mark.asyncio
    async def test_partial_close_correct_qty(
        self, watchdog_settings, mock_position_service,
        mock_risk_manager, sample_long_position,
    ):
        """50% of 0.01 size = 0.005 qty closed."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            risk_manager=mock_risk_manager,
        )
        decision = WatchdogDecision(
            id="test", action="partial_close", symbol="BTCUSDT",
            confidence=0.8,
        )
        result = await wd._execute_partial_close(sample_long_position, decision)

        assert result["executed"] is True
        assert result["close_pct"] == 50.0
        mock_position_service.reduce_position.assert_called_once_with(
            "BTCUSDT", 0.005,
        )

    @pytest.mark.asyncio
    async def test_partial_close_updates_risk_manager(
        self, watchdog_settings, mock_position_service,
        mock_risk_manager, sample_long_position,
    ):
        """Risk manager should be updated with estimated PnL."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            risk_manager=mock_risk_manager,
        )
        decision = WatchdogDecision(
            id="test", action="partial_close", symbol="BTCUSDT",
            confidence=0.8,
        )
        await wd._execute_partial_close(sample_long_position, decision)

        mock_risk_manager.on_trade_closed.assert_called_once()
        # 50% of unrealized_pnl (-10) = -5.0
        called_pnl = mock_risk_manager.on_trade_closed.call_args[0][0]
        assert called_pnl == pytest.approx(-5.0)


# =============================================================================
# Full Close Tests
# =============================================================================

class TestFullClose:
    @pytest.mark.asyncio
    async def test_full_close_calls_close_position(
        self, watchdog_settings, mock_position_service,
        mock_risk_manager, sample_long_position,
    ):
        """Full close delegates to position_service.close_position."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            risk_manager=mock_risk_manager,
        )
        decision = WatchdogDecision(
            id="test", action="full_close", symbol="BTCUSDT",
            confidence=0.9,
        )
        result = await wd._execute_full_close(sample_long_position, decision)

        assert result["executed"] is True
        # Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1): watchdog now
        # passes close_trigger= so BYBIT_DEMO_POSITION_CLOSE log carries
        # the source-specific reason. Per the per-call-site mapping,
        # _execute_full_close uses "wd_full_close".
        mock_position_service.close_position.assert_called_once_with(
            "BTCUSDT", close_trigger="wd_full_close",
        )

    @pytest.mark.asyncio
    async def test_full_close_updates_risk_manager(
        self, watchdog_settings, mock_position_service,
        mock_risk_manager, sample_long_position,
    ):
        """Risk manager updated with full unrealized PnL."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            risk_manager=mock_risk_manager,
        )
        decision = WatchdogDecision(
            id="test", action="full_close", symbol="BTCUSDT",
            confidence=0.9,
        )
        await wd._execute_full_close(sample_long_position, decision)

        mock_risk_manager.on_trade_closed.assert_called_once_with(-10)


# =============================================================================
# Hold Decision Tests
# =============================================================================

class TestHoldDecision:
    @pytest.mark.asyncio
    async def test_hold_no_execution(
        self, watchdog_settings, mock_position_service,
        mock_risk_manager, sample_long_position,
    ):
        """Hold action should not execute any trades."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            risk_manager=mock_risk_manager,
        )
        decision = WatchdogDecision(
            id="test", action="hold", symbol="BTCUSDT",
            confidence=0.6, reasoning="temporary dip",
        )
        result = await wd._execute_decision(sample_long_position, decision)

        assert result["executed"] is False
        assert result["action"] == "hold"
        mock_position_service.close_position.assert_not_called()
        mock_position_service.set_stop_loss.assert_not_called()
        mock_position_service.reduce_position.assert_not_called()


# =============================================================================
# Error Isolation Tests
# =============================================================================

class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_one_position_error_doesnt_block_others(
        self, watchdog_settings, mock_market_service, mock_alert_manager,
    ):
        """Error monitoring BTCUSDT must not prevent monitoring ETHUSDT.

        Production semantics: PositionWatchdog calls ``get_ticker`` once
        for the maturity check at the top of the per-position loop and
        a second time inside ``_monitor_position`` for the actual
        analysis. So a healthy 2-position tick performs ~4 ticker
        fetches; the per-position try/except keeps a failure on one
        symbol from starving the others.
        """
        pos1 = Position(
            symbol="BTCUSDT", side=Side.BUY, size=0.01,
            entry_price=70000, mark_price=69000,
            unrealized_pnl=-10, leverage=2, stop_loss=68000,
        )
        pos2 = Position(
            symbol="ETHUSDT", side=Side.SELL, size=0.1,
            entry_price=3500, mark_price=3600,
            unrealized_pnl=-10, leverage=2, stop_loss=3700,
        )

        mock_pos_service = MagicMock()
        mock_pos_service.get_positions = AsyncMock(return_value=[pos1, pos2])

        eth_ticker = Ticker(
            symbol="ETHUSDT", last_price=3600, bid=3599, ask=3601,
            high_24h=3650, low_24h=3450, volume_24h=10000, change_24h_pct=-2.0,
        )

        # BTCUSDT maturity-check ticker call raises; ETHUSDT calls
        # (maturity + _monitor_position) succeed. Use a callable
        # ``side_effect`` so the same Ticker is returned for every
        # ETHUSDT call regardless of how many fetches the production
        # code performs (forward-compat against ticker-cache changes).
        async def _ticker_side_effect(symbol: str):
            if symbol == "BTCUSDT":
                raise Exception("API error for BTCUSDT")
            return eth_ticker

        mock_market_service.get_ticker = AsyncMock(side_effect=_ticker_side_effect)

        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_pos_service,
            market_service=mock_market_service,
            alert_manager=mock_alert_manager,
        )
        await wd.tick()

        # Isolation invariant: BTCUSDT's failure did not stop the loop.
        # The watchdog should have attempted at least one fetch for each
        # of the two positions (maturity check fires per-position).
        symbols_attempted = {
            call.args[0] for call in mock_market_service.get_ticker.call_args_list
        }
        assert symbols_attempted == {"BTCUSDT", "ETHUSDT"}, (
            f"watchdog stopped after BTCUSDT failure — only saw "
            f"{symbols_attempted}; ETHUSDT must still be monitored"
        )
        # ETHUSDT should still have been processed and triggered an alert
        # (it's down 2.86% from entry which exceeds loss_warning_pct)
        mock_alert_manager.send_watchdog_alert.assert_called_once()


# =============================================================================
# Stale Tracking Cleanup Tests
# =============================================================================

class TestStaleCleanup:
    @pytest.mark.asyncio
    async def test_stale_symbols_cleaned_up(
        self, watchdog_settings, mock_position_service, mock_market_service,
    ):
        """Tracking dicts should be pruned when positions close."""
        wd = _make_watchdog(
            watchdog_settings,
            position_service=mock_position_service,
            market_service=mock_market_service,
        )
        # Pre-seed tracking data for a symbol that is no longer open
        wd._position_peaks["DOGEUSDT"] = 5.0
        wd._last_prices["DOGEUSDT"] = 0.15
        wd._last_pnls["DOGEUSDT"] = -2.0
        wd._last_brain_call["DOGEUSDT"] = time.monotonic()

        # Return no positions
        mock_position_service.get_positions = AsyncMock(return_value=[])

        await wd.tick()

        assert "DOGEUSDT" not in wd._position_peaks
        assert "DOGEUSDT" not in wd._last_prices
        assert "DOGEUSDT" not in wd._last_pnls
        assert "DOGEUSDT" not in wd._last_brain_call

    @pytest.mark.asyncio
    async def test_cleanup_method_clears_all(self, watchdog_settings):
        """cleanup() should clear all tracking state."""
        wd = _make_watchdog(watchdog_settings)
        wd._position_peaks["BTCUSDT"] = 100.0
        wd._last_prices["BTCUSDT"] = 70000.0
        wd._last_pnls["BTCUSDT"] = -1.5
        wd._last_brain_call["BTCUSDT"] = time.monotonic()

        await wd.cleanup()

        assert len(wd._position_peaks) == 0
        assert len(wd._last_prices) == 0
        assert len(wd._last_pnls) == 0
        assert len(wd._last_brain_call) == 0


# =============================================================================
# Decision Parser Integration Tests
# =============================================================================

class TestWatchdogDecisionParser:
    def test_parse_hold_response(self, decision_parser):
        text = '{"action": "hold", "symbol": "BTCUSDT", "confidence": 0.6, "reasoning": "dip"}'
        d = decision_parser.parse_watchdog_decision(text)
        assert d.action == "hold"
        assert d.confidence == pytest.approx(0.6)

    def test_parse_tighten_stop_response(self, decision_parser):
        text = '{"action": "tighten_stop", "symbol": "BTCUSDT", "confidence": 0.8, "new_stop_loss": 69500}'
        d = decision_parser.parse_watchdog_decision(text)
        assert d.action == "tighten_stop"
        assert d.new_stop_loss == 69500.0

    def test_parse_full_close_response(self, decision_parser):
        text = '{"action": "full_close", "symbol": "BTCUSDT", "confidence": 0.95, "reasoning": "trend reversed"}'
        d = decision_parser.parse_watchdog_decision(text)
        assert d.action == "full_close"
        assert d.confidence == pytest.approx(0.95)

    def test_parse_invalid_action_defaults_hold(self, decision_parser):
        text = '{"action": "panic_sell", "symbol": "BTCUSDT", "confidence": 0.5}'
        d = decision_parser.parse_watchdog_decision(text)
        assert d.action == "hold"

    def test_parse_markdown_fenced_json(self, decision_parser):
        text = '```json\n{"action": "partial_close", "symbol": "BTCUSDT", "confidence": 0.7}\n```'
        d = decision_parser.parse_watchdog_decision(text)
        assert d.action == "partial_close"
