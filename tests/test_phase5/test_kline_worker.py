"""Tests for KlineWorker — including HR-1/HR-2/HR-3 universe-integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.types import TimeFrame
from src.workers.kline_worker import KlineWorker


class TestKlineWorker:
    @pytest.mark.asyncio
    async def test_tick_fetches_klines(
        self, mock_settings, test_db, mock_market_service, mock_scanner,
    ):
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=mock_scanner)
        await worker.tick()
        assert mock_market_service.get_klines.called

    @pytest.mark.asyncio
    async def test_tiered_fetching(
        self, mock_settings, test_db, mock_market_service, mock_scanner,
    ):
        """M1/M5 fetched every tick, H4/D1 less frequently."""
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=mock_scanner)

        # First tick — all tiers with every_n=1 should be fetched
        await worker.tick()
        first_call_count = mock_market_service.get_klines.call_count

        mock_market_service.get_klines.reset_mock()

        # Second tick — only every_n=1 tiers
        await worker.tick()
        second_call_count = mock_market_service.get_klines.call_count

        # Second tick should fetch same or fewer (M15/H1 fetched at tick 5)
        assert second_call_count <= first_call_count

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(
        self, mock_settings, test_db, mock_market_service, mock_scanner,
    ):
        mock_market_service.get_klines.side_effect = Exception("API timeout")
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=mock_scanner)
        # Should not raise — errors are caught per-symbol
        await worker.tick()


class TestKlineWorkerUniverseIntegration:
    """Verifies the corrected Layer 1 contract.

    The original tests in this class verified the old "scanner is sole
    universe source" contract. The corrected Layer 1 architecture
    (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md) replaced that with
    "settings.universe.watch_list is sole universe source" and Phase 7
    deleted the rotation-callback handlers, so the tests below were
    updated to match.
    """

    @pytest.mark.asyncio
    async def test_init_seeds_tracked_symbols_from_watch_list(
        self, mock_settings, test_db, mock_market_service,
    ):
        """HR-5: __init__ pre-seeds _tracked_symbols from settings.universe.watch_list."""
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=None)
        assert worker._tracked_symbols == list(mock_settings.universe.watch_list)
        assert len(worker._tracked_symbols) >= 10

    @pytest.mark.asyncio
    async def test_tick_reads_watch_list_not_scanner(
        self, mock_settings, test_db, mock_market_service,
    ):
        """HR-1: tick reads settings.universe.watch_list directly; scanner unused."""
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=None)
        await worker.tick()
        # With a populated watch_list, fetches DO happen (scanner=None doesn't block)
        assert mock_market_service.get_klines.called
        # All fetched symbols must be from the watch_list
        called_symbols = {c.args[0] for c in mock_market_service.get_klines.call_args_list}
        assert called_symbols.issubset(set(mock_settings.universe.watch_list))

    @pytest.mark.asyncio
    async def test_empty_watch_list_skips_tick(
        self, mock_settings, test_db, mock_market_service,
    ):
        """HR-3: defensive guard against empty watch_list (UniverseSettings
        validation makes this unreachable in production, but the runtime
        guard remains for safety)."""
        mock_settings.universe.watch_list = []
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=None)
        await worker.tick()
        assert not mock_market_service.get_klines.called

    @pytest.mark.asyncio
    async def test_on_universe_change_method_removed_phase7(
        self, mock_settings, test_db, mock_market_service,
    ):
        """Phase 7 deleted ``_on_universe_change`` — the master callback
        dispatcher in manager.py was also removed. Workers operate on the
        full watch_list and no longer react to scanner rotations."""
        worker = KlineWorker(mock_settings, test_db, mock_market_service, scanner=None)
        assert not hasattr(worker, "_on_universe_change")

    @pytest.mark.asyncio
    async def test_get_score_accessor_for_phase6(
        self, mock_settings, test_db, mock_market_service,
    ):
        """Phase 6: KlineWorker exposes is_circuit_open() (legacy accessor
        used by strategy_worker, retained under the corrected architecture)."""
        worker = KlineWorker(mock_settings, test_db, mock_market_service)
        assert hasattr(worker, "is_circuit_open")
        assert callable(worker.is_circuit_open)
        assert worker.is_circuit_open() is False  # not opened yet
