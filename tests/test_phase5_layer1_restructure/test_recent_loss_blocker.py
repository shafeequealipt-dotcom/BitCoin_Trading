"""Phase 5 audit fix: recent-loss blocker uses set-membership lookup.

Regression guard for the gap caught in the post-Phase-9 audit:
``_check_blockers`` originally tried to call a non-existent
``recorder.had_recent_loss`` method. The fix introduces
``trade_recorder.recent_loss_symbols`` (one DB query per tick) and
threads the set through ``_qualifies`` → ``_check_blockers`` so the
membership test is O(1) per coin.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import ScannerQualitativeSettings
from src.workers.scanner_worker import ScannerWorker


def _stub_worker(qualitative=None) -> ScannerWorker:
    w = ScannerWorker.__new__(ScannerWorker)
    settings = MagicMock()
    settings.scanner.qualitative = qualitative or ScannerQualitativeSettings()
    w.settings = settings
    w.services = {"altdata_worker": None}
    return w


class TestCheckBlockersRecentLoss:
    def test_recent_loss_set_membership_blocks(self) -> None:
        w = _stub_worker()
        blockers = w._check_blockers(
            "BTCUSDT", structure=None, consensus=None,
            recent_loss_set={"BTCUSDT", "ETHUSDT"},
        )
        assert any("recent_loss_within_" in b for b in blockers)

    def test_symbol_not_in_set_passes(self) -> None:
        w = _stub_worker()
        blockers = w._check_blockers(
            "SOLUSDT", structure=None, consensus=None,
            recent_loss_set={"BTCUSDT"},
        )
        assert not any("recent_loss" in b for b in blockers)

    def test_none_set_disables_check(self) -> None:
        w = _stub_worker()
        blockers = w._check_blockers(
            "BTCUSDT", structure=None, consensus=None, recent_loss_set=None,
        )
        # No recent-loss blocker — None means "feature disabled / not yet wired".
        assert not any("recent_loss" in b for b in blockers)


class TestRecentLossSymbolsQuery:
    @pytest.mark.asyncio
    async def test_zero_hours_returns_empty(self) -> None:
        from src.core.trade_recorder import recent_loss_symbols
        db = MagicMock()
        db.fetch_all = AsyncMock()
        result = await recent_loss_symbols(db, hours=0)
        assert result == set()
        db.fetch_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_distinct_symbols(self) -> None:
        from src.core.trade_recorder import recent_loss_symbols
        db = MagicMock()
        db.fetch_all = AsyncMock(return_value=[
            {"symbol": "BTCUSDT"},
            {"symbol": "ETHUSDT"},
        ])
        result = await recent_loss_symbols(db, hours=1)
        assert result == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self) -> None:
        """Recorder hiccup must NOT break the scanner cycle."""
        from src.core.trade_recorder import recent_loss_symbols
        db = MagicMock()
        db.fetch_all = AsyncMock(side_effect=RuntimeError("boom"))
        result = await recent_loss_symbols(db, hours=1)
        assert result == set()
