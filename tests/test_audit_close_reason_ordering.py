"""Issue 2.11 (2026-06-07) — close-reason-ordering audit.

The Issue 2.11 fix records the *truthful* exit reason on the
TradeCoordinator BEFORE the position is actually closed, so the
``pop_close_reason`` consumers (the WS subscriber + the watchdog poll)
return the real reason instead of the generic ``"{mode}_sl_tp"`` fallback.

For that fix to do anything, ``set_close_reason`` MUST be invoked *before*
``position_service.close_position`` — if the order were reversed, the close
event could be consumed (and the generic fallback booked) before the real
reason was ever stored. No existing test pins that ordering. These two
cases close the gap on the two Issue 2.11 paths:

  1. Sniper full-close — ``ProfitSniper._execute_full_close`` records the
     reason at ``src/workers/profit_sniper.py:4700`` then closes at
     ``:4705``.
  2. Watchdog time-decay force-close — ``PositionWatchdog._handle_time_decay``
     records the win-prob reason at
     ``src/workers/position_watchdog.py:1812`` then closes at ``:1816``.

The ordering is asserted by attaching both mocks to a single parent ``Mock``
via ``attach_mock`` and inspecting ``parent.mock_calls`` (which records calls
across children in invocation order).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, Mock

from src.risk.layer4_protection import ProtectionResult
from src.workers.profit_sniper import ProfitSniper


# ═════════════════════════════════════════════════════════════════════════
# Case 1 — Sniper full-close (Issue 2.11 path, profit_sniper.py:4699-4708)
# ═════════════════════════════════════════════════════════════════════════


def _make_sniper_with_coordinator():
    """Build a minimal ProfitSniper wired with a MagicMock trade_coordinator
    whose set_close_reason / remove_trade_plan / on_trade_closed are plain
    mocks and resolve_authoritative_pnl is an AsyncMock returning the
    4-tuple the production code unpacks. position_service.close_position is
    an AsyncMock. Mirrors _make_sniper in
    tests/test_layer4_protection/test_sniper_integration.py but adds the
    coordinator so the set_close_reason call fires."""
    sw = ProfitSniper.__new__(ProfitSniper)
    sw.settings = MagicMock()
    sw.position_service = MagicMock()
    sw.position_service.close_position = AsyncMock()
    sw.event_buffer = None

    coord = MagicMock()
    coord.set_close_reason = MagicMock()
    coord.remove_trade_plan = MagicMock()
    coord.on_trade_closed = MagicMock()
    coord.resolve_authoritative_pnl = AsyncMock(
        return_value=(-12.3, -0.4, "mark_price", 99.5),
    )
    sw.trade_coordinator = coord

    # Service that ALLOWS the close (protected=False) so _execute_full_close
    # proceeds to the set_close_reason → close_position sequence.
    svc = MagicMock()
    svc.is_protected = AsyncMock(return_value=ProtectionResult(
        protected=False, reason="no_protection", evidence={},
    ))
    sw.layer4_protection = svc
    return sw, coord


def _pos() -> MagicMock:
    p = MagicMock()
    p.symbol = "ETHUSDT"
    p.side = "Buy"
    p.size = 10
    p.unrealized_pnl = -12.3
    return p


def test_sniper_full_close_sets_reason_before_close():
    """Issue 2.11 (profit_sniper.py:4700 set_close_reason →
    :4705 close_position): set_close_reason must be called once with
    (symbol, closed_by) and STRICTLY BEFORE close_position."""
    sw, coord = _make_sniper_with_coordinator()
    pos = _pos()

    # Attach both side-effecting calls to one parent so mock_calls records
    # the relative invocation order across the two children.
    parent = Mock()
    parent.attach_mock(coord.set_close_reason, "set_close_reason")
    parent.attach_mock(sw.position_service.close_position, "close_position")

    result = asyncio.run(sw._execute_full_close(
        symbol="ETHUSDT",
        pos=pos,
        score_data={"exploit_score": 90, "pnl_pct": -0.4},
        closed_by="loss_spike_force",
    ))
    assert result is True, "allowed close should succeed"

    # 1. set_close_reason called once with the exact reason.
    coord.set_close_reason.assert_called_once_with("ETHUSDT", "loss_spike_force")
    # 2. close_position was actually called.
    sw.position_service.close_position.assert_awaited_once()

    # 3. Ordering: set_close_reason precedes close_position in the parent's
    #    recorded call sequence.
    names = [c[0] for c in parent.mock_calls]
    assert "set_close_reason" in names and "close_position" in names
    assert names.index("set_close_reason") < names.index("close_position"), (
        f"set_close_reason must precede close_position; order was {names}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Case 2 — Watchdog time-decay force-close
#          (position_watchdog.py:1811-1818)
# ═════════════════════════════════════════════════════════════════════════


def test_watchdog_time_decay_sets_reason_before_close():
    """Issue 2.11 (position_watchdog.py:1812 set_close_reason →
    :1816 close_position): on the win-prob force-close branch
    (outcome == -1.0), the watchdog must call coordinator.set_close_reason
    with the force reason ('win_prob_force_close') STRICTLY BEFORE
    position_service.close_position.

    Drives _handle_time_decay into the force-close branch by pre-seeding a
    TimeDecayState (skips the first-tick lazy-init early-return) with a
    p_win in the (near_certain_loser_p_win, p_win_force_close) band, past
    grace + min_age, with MAE/SL ratio above the gate, regime supporting
    (so _update_p_win does not push p_win below the band), and a
    layer4_protection stub reporting structural_invalidation=True (so the
    structural guard yields and the win_prob_force_close label is stamped)."""
    from dataclasses import dataclass
    from src.config.settings import Settings
    from src.core.trade_plan import TradePlan
    from src.core.types import Position, Side
    from src.workers.position_watchdog import PositionWatchdog

    async def run():
        settings = Settings._load_fresh()

        pos_svc = MagicMock()
        pos_svc.close_position = AsyncMock(return_value=True)
        pos_svc.set_stop_loss = AsyncMock(return_value=True)

        @dataclass
        class FakeProfile:
            atr_pct_5m: float = 0.5
            volatility_class: str = "medium"
        vp = MagicMock()
        vp.get_profile = AsyncMock(return_value=FakeProfile())

        class FakeEnum:
            def __init__(self, v):
                self.value = v

        @dataclass
        class FakeRegime:
            regime: object
            confidence: float
        rd = MagicMock()
        # Buy position + "trending_up" → regime_still_supports=True → the
        # _update_p_win regime bonus (×1.05) keeps p_win inside the band.
        rd.get_coin_regime = MagicMock(
            return_value=FakeRegime(FakeEnum("trending_up"), 0.5),
        )

        # MagicMock coordinator so we can assert set_close_reason ordering.
        coord = MagicMock()
        coord.is_symbol_in_any_cooldown = MagicMock(return_value=False)
        coord.set_close_reason = MagicMock()
        coord.remove_trade_plan = MagicMock()
        coord.on_trade_closed = MagicMock()
        coord.get_trade_plan = MagicMock(return_value=None)
        coord.get_trade_info = MagicMock(return_value={})
        coord._trades = {}
        coord.resolve_authoritative_pnl = AsyncMock(
            return_value=(-30.0, -1.5, "mark_price", 0.97),
        )

        # layer4_protection stub: structural_invalidation=True so the
        # structural guard yields and the force-close sentinel labels the
        # reason "win_prob_force_close" (rather than the near-certain bucket).
        l4 = MagicMock()
        l4.compute_structural_invalidation = MagicMock(
            return_value=(True, "regime_inv:test"),
        )
        l4.record_struct_guard_verdict = MagicMock()

        wd = PositionWatchdog(
            settings=settings, db=MagicMock(),
            position_service=pos_svc, market_service=None,
            volatility_profiler=vp, regime_detector=rd,
            trade_coordinator=coord, layer4_protection=l4,
        )
        assert wd._time_decay is not None, "time-decay calculator must be built"

        # The loaded config (TimeDecayConfig is frozen) already carries the
        # defaults this path relies on; assert them so a future config change
        # that would break the force-close labelling fails loudly here rather
        # than silently mislabelling the close reason.
        cfg = wd._time_decay.cfg
        assert cfg.p_win_force_close == 0.15
        assert cfg.near_certain_loser_p_win == 0.10
        assert cfg.winprob_age_aware_band_enabled is False
        assert cfg.structural_invalidation_required is True
        assert cfg.smooth_p_win_enabled is False
        assert cfg.slow_bleed_cumulative_force_close_enabled is False
        assert cfg.min_age_seconds == 300.0
        assert cfg.mae_to_sl_ratio_threshold == 0.5

        plan = TradePlan(
            symbol="FORCEUSDT", direction="Buy", entry_price=1.0,
            target_price=1.05, stop_loss_price=0.98,
            max_hold_minutes=30,
        )
        plan.opened_at = time.time() - 700  # past grace + min_age (300s)

        # Pre-seed state so _handle_time_decay skips lazy-init (which would
        # otherwise return False on the first tick) and runs the calculator.
        state = wd._time_decay.create_state(
            symbol="FORCEUSDT", direction="Buy", entry_price=1.0,
            original_sl_pct=2.0, max_hold_seconds=1800,
            atr_5m_pct=0.5, regime_confidence=0.5,
            volatility_class="medium",
        )
        # p_win in (0.10, 0.15): a ×1.05 regime bonus → 0.126, still < force
        # threshold (0.15) and > near-certain (0.10) → win_prob_force_close.
        state.p_win = 0.12
        # MAE/SL ratio = 1.5 / 2.0 = 0.75 (above the 0.50 gate).
        state.mae_pct = -1.5
        # last == prev == current → no tick-over-tick deepening → no ATR
        # penalty in _update_p_win (keeps p_win inside the band).
        state.last_pnl_pct = -1.5
        state.prev_pnl_pct = -1.5
        wd._td_states["FORCEUSDT"] = state

        pos = Position(
            symbol="FORCEUSDT", side=Side.BUY, size=100, entry_price=1.0,
            mark_price=0.985, unrealized_pnl=-30.0,
            stop_loss=0.98, take_profit=1.05,
        )

        parent = Mock()
        parent.attach_mock(coord.set_close_reason, "set_close_reason")
        parent.attach_mock(pos_svc.close_position, "close_position")

        closed = await wd._handle_time_decay(
            pos, plan, pnl_pct=-1.5, current_price=0.985,
        )

        assert closed is True, (
            "force-close branch must return True (position closed)"
        )
        # set_close_reason called with the force reason.
        coord.set_close_reason.assert_called_once_with(
            "FORCEUSDT", "win_prob_force_close",
        )
        # close_position was invoked.
        pos_svc.close_position.assert_awaited_once()

        # Ordering: set_close_reason BEFORE close_position.
        names = [c[0] for c in parent.mock_calls]
        assert "set_close_reason" in names and "close_position" in names
        assert names.index("set_close_reason") < names.index("close_position"), (
            f"set_close_reason must precede close_position; order was {names}"
        )

    asyncio.run(run())
