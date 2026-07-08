"""Tests for PriceWorker — including HR-1/HR-2/HR-3 universe-integration."""

import pytest
from unittest.mock import AsyncMock

from src.workers.price_worker import PriceWorker


class TestPriceWorker:
    @pytest.mark.asyncio
    async def test_connects_on_first_tick(
        self, mock_settings, test_db, mock_bybit_ws, mock_scanner,
    ):
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=mock_scanner)
        await worker.tick()
        mock_bybit_ws.connect_public.assert_called_once()
        mock_bybit_ws.subscribe_ticker.assert_called_once()
        assert worker._connected is True

    @pytest.mark.asyncio
    async def test_reconnects_on_disconnect(
        self, mock_settings, test_db, mock_bybit_ws, mock_scanner,
    ):
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=mock_scanner)
        await worker.tick()  # Connect
        assert worker._connected is True

        # Simulate disconnect
        mock_bybit_ws.is_running = False
        await worker.tick()  # Detect disconnect
        assert worker._connected is False

    @pytest.mark.asyncio
    async def test_cleanup_disconnects(
        self, mock_settings, test_db, mock_bybit_ws, mock_scanner,
    ):
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=mock_scanner)
        await worker.tick()
        await worker.cleanup()
        mock_bybit_ws.disconnect.assert_called_once()
        assert worker._connected is False


class TestPriceWorkerUniverseIntegration:
    """Verifies the corrected Layer 1 contract for PriceWorker.

    PriceWorker stays on ``BaseWorker`` (continuous WS) but reads from
    ``settings.universe.watch_list`` instead of the scanner under the
    corrected architecture. Phase 7 deleted the ``_on_universe_change``
    rotation handler and the master callback dispatcher.
    """

    @pytest.mark.asyncio
    async def test_init_tracked_symbols_seeded_from_watch_list(
        self, mock_settings, test_db, mock_bybit_ws,
    ):
        """HR-5: _tracked_symbols pre-seeded from settings.universe.watch_list.

        This avoids a spurious first-tick reconnect log line; matches the
        same pattern KlineWorker uses.
        """
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=None)
        assert worker._tracked_symbols == list(mock_settings.universe.watch_list)
        assert len(worker._tracked_symbols) >= 10

    @pytest.mark.asyncio
    async def test_tick_subscribes_to_watch_list(
        self, mock_settings, test_db, mock_bybit_ws,
    ):
        """HR-1: tick subscribes the WS to the full watch_list."""
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=None)
        # Force initial state: not yet connected.
        worker._connected = False
        await worker.tick()
        mock_bybit_ws.connect_public.assert_called_once()
        mock_bybit_ws.subscribe_ticker.assert_called_once()
        sub_args = mock_bybit_ws.subscribe_ticker.call_args
        # First positional arg should be the symbol list.
        subscribed_syms = sub_args.args[0]
        assert set(subscribed_syms) == set(mock_settings.universe.watch_list)

    @pytest.mark.asyncio
    async def test_empty_watch_list_skips_tick(
        self, mock_settings, test_db, mock_bybit_ws,
    ):
        """HR-3: defensive guard against empty watch_list — no WS subscribe."""
        mock_settings.universe.watch_list = []
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=None)
        await worker.tick()
        mock_bybit_ws.connect_public.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_universe_change_method_removed_phase7(
        self, mock_settings, test_db, mock_bybit_ws,
    ):
        """Phase 7 deleted ``_on_universe_change``. PriceWorker now stays
        subscribed to the same ``watch_list`` for the lifetime of the
        process; the operator updates watch_list (config restart) to
        change the subscription set."""
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=None)
        assert not hasattr(worker, "_on_universe_change")

    @pytest.mark.asyncio
    async def test_get_ws_quote_accessor_for_phase6(
        self, mock_settings, test_db, mock_bybit_ws,
    ):
        """Phase 6: PriceWorker exposes ``get_ws_quote(coin, max_age_s)`` so
        APEX / consumers can read fresh WS prices without a REST hop."""
        worker = PriceWorker(mock_settings, test_db, mock_bybit_ws, scanner=None)
        assert hasattr(worker, "get_ws_quote")
        assert callable(worker.get_ws_quote)
        # Empty cache returns None.
        assert worker.get_ws_quote("BTCUSDT") is None
