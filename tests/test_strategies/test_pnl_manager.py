"""Tests for DailyPnLManager."""

import pytest

from src.core.utils import now_utc
from src.strategies.pnl_manager import DailyPnLManager


def _today() -> str:
    """Today's UTC date in the canonical YYYY-MM-DD form the manager uses.

    Tests must seed ``today_date`` with TODAY (not a hard-coded past
    date). ``DailyPnLManager._check_new_day`` runs at the top of
    ``on_trade_closed`` / ``update``; if ``today_date`` differs from
    today it resets EVERY field including ``starting_equity = 0``.
    A reset starting_equity makes ``current_pnl_pct`` zero forever and
    masks the target_hit / halted thresholds the tests are validating.
    """
    return now_utc().strftime("%Y-%m-%d")


class TestPnLModes:
    def _make_manager(self, settings, pnl_pct):
        mgr = DailyPnLManager(settings)
        mgr.starting_equity = 10000
        mgr.realized_pnl = pnl_pct * 100  # 10000 * pct/100
        mgr.today_date = _today()
        mgr._recalculate()
        return mgr

    def test_target_hit_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, 5.5)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "TARGET_HIT"
        assert mode["max_leverage"] == 2
        assert mode["max_positions"] == 1

    def test_protect_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, 3.5)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "PROTECT"

    def test_good_day_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, 1.5)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "GOOD_DAY"

    def test_normal_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, 0.0)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "NORMAL"
        # NORMAL ≡ "full aggression" per pnl_manager.py:249. Production
        # tuned ``max_positions`` to 10 (was 5 in the original draft);
        # ``max_leverage`` stays at 5. Test asserts the live contract.
        assert mode["max_leverage"] == 5
        assert mode["max_positions"] == 10

    def test_caution_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, -2.0)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "CAUTION"

    def test_survival_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, -4.0)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "SURVIVAL"
        # SURVIVAL allows ``max_leverage = 3`` per pnl_manager.py:265.
        # The original test assertion of 2 was from an older draft;
        # the live contract uses 3 with quality-gate enabled.
        assert mode["max_leverage"] == 3
        assert mode.get("quality_gate") is True

    def test_halted_mode(self, strategy_settings):
        mgr = self._make_manager(strategy_settings, -6.0)
        mode = mgr.get_current_mode()
        assert mode["mode"] == "HALTED"
        assert mode["max_positions"] == 0


class TestCanTrade:
    def test_can_trade_normal(self, strategy_settings):
        mgr = DailyPnLManager(strategy_settings)
        mgr.starting_equity = 10000
        mgr.today_date = _today()
        mgr._recalculate()
        can, reason = mgr.can_trade()
        assert can is True

    def test_cannot_trade_halted(self, strategy_settings):
        mgr = DailyPnLManager(strategy_settings)
        mgr.starting_equity = 10000
        mgr.realized_pnl = -600
        mgr.today_date = _today()
        mgr._recalculate()
        can, reason = mgr.can_trade()
        assert can is False
        assert "halted" in reason.lower()


class TestOnTradeClosed:
    @pytest.mark.asyncio
    async def test_updates_realized_pnl(self, strategy_settings):
        mgr = DailyPnLManager(strategy_settings)
        mgr.starting_equity = 10000
        mgr.today_date = _today()
        await mgr.on_trade_closed(50.0)
        assert mgr.realized_pnl == 50.0

    @pytest.mark.asyncio
    async def test_target_hit_flag(self, strategy_settings):
        mgr = DailyPnLManager(strategy_settings)
        mgr.starting_equity = 10000
        mgr.today_date = _today()
        await mgr.on_trade_closed(600.0)  # 6% of equity
        assert mgr.target_hit is True

    @pytest.mark.asyncio
    async def test_halted_flag(self, strategy_settings):
        mgr = DailyPnLManager(strategy_settings)
        mgr.starting_equity = 10000
        mgr.today_date = _today()
        await mgr.on_trade_closed(-600.0)  # -6% of equity
        assert mgr.halted is True
