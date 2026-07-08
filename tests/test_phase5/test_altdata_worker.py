"""Tests for AltDataWorker — including HR-1/HR-2/HR-3 universe-integration."""

import pytest
from unittest.mock import AsyncMock

from src.workers.altdata_worker import AltDataWorker


def _wire(worker, scanner):
    """Helper to install the scanner the way manager.py does (late-wire)."""
    worker._scanner = scanner
    return worker


class TestAltDataWorker:
    @pytest.mark.asyncio
    async def test_tick_fetches_all_sources(
        self, mock_settings, test_db,
        mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        mock_scanner,
    ):
        worker = AltDataWorker(
            mock_settings, test_db,
            mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        )
        _wire(worker, mock_scanner)
        await worker.tick()
        mock_fear_greed.fetch_current.assert_called_once()
        mock_funding_tracker.fetch_current_rates.assert_called_once()
        mock_oi_tracker.fetch_current.assert_called_once()
        mock_onchain.get_global_metrics.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_source_failure_continues(
        self, mock_settings, test_db,
        mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        mock_scanner,
    ):
        """If Fear & Greed fails, funding rates should still work."""
        mock_fear_greed.fetch_current = AsyncMock(side_effect=Exception("FG down"))
        worker = AltDataWorker(
            mock_settings, test_db,
            mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        )
        _wire(worker, mock_scanner)
        # Should not raise
        await worker.tick()
        mock_funding_tracker.fetch_current_rates.assert_called_once()

    @pytest.mark.asyncio
    async def test_works_with_partial_sources(
        self, mock_settings, test_db, mock_fear_greed, mock_scanner,
    ):
        """Worker works even with only some sources available."""
        worker = AltDataWorker(
            mock_settings, test_db,
            mock_fear_greed, None, None, None,
        )
        _wire(worker, mock_scanner)
        await worker.tick()
        mock_fear_greed.fetch_current.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_sources_warns(self, mock_settings, test_db, mock_scanner):
        worker = AltDataWorker(mock_settings, test_db, None, None, None, None)
        _wire(worker, mock_scanner)
        await worker.tick()  # Should not crash, just log warning


class TestAltDataWorkerUniverseIntegration:
    """Verifies the corrected Layer 1 contract.

    The original Phase-3 assertions in this test class verified the old
    "scanner is sole universe source" contract. The corrected Layer 1
    architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md) replaced
    that with "settings.universe.watch_list is sole universe source" and
    deleted the rotation-callback handlers, so the tests below were
    updated to match.
    """

    @pytest.mark.asyncio
    async def test_init_symbols_seeded_from_watch_list(self, mock_settings, test_db):
        """HR-5: AltDataWorker seeds self.symbols from settings.universe.watch_list."""
        worker = AltDataWorker(mock_settings, test_db, None, None, None, None)
        assert worker.symbols == list(mock_settings.universe.watch_list)
        assert len(worker.symbols) >= 10  # UniverseSettings.__post_init__ enforces

    @pytest.mark.asyncio
    async def test_tick_uses_watch_list_not_scanner(
        self, mock_settings, test_db,
        mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
    ):
        """HR-1: tick reads settings.universe.watch_list, not scanner."""
        worker = AltDataWorker(
            mock_settings, test_db,
            mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        )
        # No scanner injection — confirm tick still runs.
        await worker.tick()
        # Funding fires every wake-up; OI/F&G have monotonic deadlines.
        mock_funding_tracker.fetch_current_rates.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_watch_list_skips_tick(
        self, mock_settings, test_db,
        mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
    ):
        """HR-3: defensive guard against empty watch_list (UniverseSettings
        validation makes this unreachable in production, but the runtime
        guard remains for safety)."""
        mock_settings.universe.watch_list = []
        worker = AltDataWorker(
            mock_settings, test_db,
            mock_fear_greed, mock_funding_tracker, mock_oi_tracker, mock_onchain,
        )
        await worker.tick()
        mock_fear_greed.fetch_current.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_universe_change_method_removed_phase7(self, mock_settings, test_db):
        """Phase 7 deleted ``_on_universe_change`` — the master callback
        dispatcher in manager.py was also removed. Workers operate on the
        full watch_list and no longer react to scanner rotations."""
        worker = AltDataWorker(mock_settings, test_db, None, None, None, None)
        assert not hasattr(worker, "_on_universe_change")
