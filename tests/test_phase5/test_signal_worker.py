"""Tests for SignalWorker — including HR-1/HR-2/HR-3 universe-integration."""

import pytest
from unittest.mock import AsyncMock

from src.workers.signal_worker import SignalWorker


def _wire(worker, scanner):
    """Helper to install the scanner the way manager.py does (late-wire)."""
    worker._scanner = scanner
    return worker


class TestSignalWorker:
    @pytest.mark.asyncio
    async def test_tick_generates_signals(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        await worker.tick()
        # Corrected Layer 1: tick reads settings.universe.watch_list directly,
        # so generate_signal fires once per watch_list symbol.
        assert mock_signal_generator.generate_signal.call_count == len(
            mock_settings.universe.watch_list
        )

    @pytest.mark.asyncio
    async def test_handles_ta_failure(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """If TA fails for a symbol, signal generation should still work."""
        mock_ta_engine.analyze = AsyncMock(side_effect=Exception("TA error"))
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        await worker.tick()
        # Signals should still be generated (using sentiment only); one per
        # watch_list symbol.
        assert mock_signal_generator.generate_signal.call_count == len(
            mock_settings.universe.watch_list
        )

    @pytest.mark.asyncio
    async def test_handles_signal_gen_failure(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """If signal generation fails for one symbol, others continue."""
        call_count = 0

        async def flaky_gen(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == "BTCUSDT":
                raise Exception("gen error")
            from src.core.types import Signal, SignalType
            return Signal(symbol=symbol, signal_type=SignalType.NEUTRAL,
                          confidence=0.5, source="test")

        mock_signal_generator.generate_signal = AsyncMock(side_effect=flaky_gen)
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        await worker.tick()
        # Corrected Layer 1: every coin in watch_list is attempted.
        assert call_count == len(mock_settings.universe.watch_list)


class TestSignalWorkerUniverseIntegration:
    """Verifies the corrected Layer 1 contract.

    The original tests verified the old "scanner is sole universe source"
    contract. The corrected Layer 1 architecture replaced that with
    "settings.universe.watch_list is sole universe source" and Phase 7
    deleted the rotation-callback handlers, so the tests below were
    updated to match.
    """

    @pytest.mark.asyncio
    async def test_tick_reads_watch_list(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """HR-1: tick reads settings.universe.watch_list directly."""
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        await worker.tick()
        # Generates a signal for every watch_list symbol.
        assert mock_signal_generator.generate_signal.call_count == len(mock_settings.universe.watch_list)

    @pytest.mark.asyncio
    async def test_empty_watch_list_skips_tick(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """HR-3: defensive guard against empty watch_list."""
        mock_settings.universe.watch_list = []
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        await worker.tick()
        assert mock_signal_generator.generate_signal.call_count == 0

    @pytest.mark.asyncio
    async def test_on_universe_change_method_removed_phase7(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """Phase 7 deleted ``_on_universe_change`` — rotation handlers gone."""
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        assert not hasattr(worker, "_on_universe_change")

    @pytest.mark.asyncio
    async def test_get_signal_accessor_for_phase6(
        self, mock_settings, test_db,
        mock_ta_engine, mock_aggregator, mock_signal_generator,
    ):
        """Phase 6: SignalWorker exposes ``get_signal(coin)`` so ScannerWorker
        can read the most recent SignalResult per coin without re-running
        signal generation."""
        worker = SignalWorker(
            mock_settings, test_db,
            mock_ta_engine, mock_aggregator, mock_signal_generator,
        )
        assert hasattr(worker, "get_signal")
        assert callable(worker.get_signal)
        # Empty cache returns None.
        assert worker.get_signal("BTCUSDT") is None
