"""Tests for ScannerWorker watch_list filter (Layer 1 universe alignment, Phase 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.core.types import Side, Ticker
from src.strategies.scanner import MarketScanner


def _make_ticker(symbol: str, price: float = 100.0, volume: float = 100_000_000.0,
                 change_pct: float = 5.0) -> Ticker:
    """Build a Ticker that passes the scanner's hard disqualifiers."""
    return Ticker(
        symbol=symbol,
        last_price=price,
        bid=price * 0.9999,
        ask=price * 1.0001,
        high_24h=price * 1.05,
        low_24h=price * 0.95,
        volume_24h=volume,
        change_24h_pct=change_pct,
    )


def _make_position(symbol: str) -> MagicMock:
    """Build a position mock with the .symbol attribute the scanner reads."""
    p = MagicMock()
    p.symbol = symbol
    p.side = Side.BUY
    p.size = 1.0
    return p


@pytest.fixture
def settings(sample_config_toml, sample_env_file):
    Settings.reset()
    s = Settings._load_fresh(sample_config_toml, sample_env_file)
    # Make sure we're scoring on mainnet path (not testnet shortcut)
    s.bybit.testnet = False
    yield s
    Settings.reset()


@pytest.fixture
def market_service():
    """A mock MarketService whose get_all_linear_tickers is overridden per test."""
    return MagicMock()


@pytest.fixture
def position_service_empty():
    """Position service with no open positions."""
    svc = MagicMock()
    svc.get_positions = AsyncMock(return_value=[])
    return svc


class TestWatchListFilter:
    async def test_filters_input_to_watch_list(self, settings, market_service, position_service_empty):
        """All 5 tickers, watch_list = 3, no positions → input set = 3."""
        all_tickers = [_make_ticker(s) for s in
                       ["BTCUSDT", "ETHUSDT", "SOLUSDT", "JUNKUSDT", "ANOTHERUSDT"]]
        market_service.get_all_linear_tickers = AsyncMock(return_value=all_tickers)

        watch = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
        scanner = MarketScanner(settings, market_service, watch_list=watch)
        scanner._position_service = position_service_empty

        result = await scanner.scan_market()
        scored_symbols = {c["symbol"] for c in result}
        # Top-N (settings.scanner.max_coins) is bounded by what we filtered to.
        assert scored_symbols.issubset(watch)
        # JUNK / ANOTHER must not appear (they were filtered out before scoring)
        assert "JUNKUSDT" not in scored_symbols
        assert "ANOTHERUSDT" not in scored_symbols

    async def test_protected_symbols_outside_watch_list_included(
        self, settings, market_service,
    ):
        """HR-2: open-position coin OUTSIDE watch_list still gets scored + can survive."""
        all_tickers = [_make_ticker(s) for s in
                       ["BTCUSDT", "ETHUSDT", "POSCOINUSDT", "JUNKUSDT"]]
        market_service.get_all_linear_tickers = AsyncMock(return_value=all_tickers)

        watch = {"BTCUSDT", "ETHUSDT"}
        position_svc = MagicMock()
        position_svc.get_positions = AsyncMock(
            return_value=[_make_position("POSCOINUSDT")]
        )

        scanner = MarketScanner(settings, market_service, watch_list=watch)
        scanner._position_service = position_svc

        result = await scanner.scan_market()
        scored_symbols = {c["symbol"] for c in result}

        # POSCOIN was scored even though it's outside watch_list (HR-2).
        assert "POSCOINUSDT" in scored_symbols
        # JUNK was filtered (not in watch ∪ positions).
        assert "JUNKUSDT" not in scored_symbols

    async def test_empty_watch_list_falls_back_to_legacy(self, settings, market_service, position_service_empty):
        """No watch_list → score all tickers (backward compatibility)."""
        all_tickers = [_make_ticker(s) for s in
                       ["BTCUSDT", "ETHUSDT", "ANYTHINGUSDT", "ELSEUSDT"]]
        market_service.get_all_linear_tickers = AsyncMock(return_value=all_tickers)

        scanner = MarketScanner(settings, market_service)  # no watch_list
        scanner._position_service = position_service_empty

        result = await scanner.scan_market()
        scored_symbols = {c["symbol"] for c in result}
        # All 4 tickers were eligible — all 4 in result (top-N >> 4 in test)
        assert scored_symbols == {"BTCUSDT", "ETHUSDT", "ANYTHINGUSDT", "ELSEUSDT"}

    async def test_top_n_bounded_to_filtered_set(self, settings, market_service, position_service_empty):
        """Top-N never exceeds the filtered input set size."""
        # 100 tickers in Bybit's universe
        all_tickers = [_make_ticker(f"COIN{i:03d}USDT") for i in range(100)]
        market_service.get_all_linear_tickers = AsyncMock(return_value=all_tickers)

        # watch_list of 5
        watch = {"COIN001USDT", "COIN002USDT", "COIN003USDT", "COIN004USDT", "COIN005USDT"}
        scanner = MarketScanner(settings, market_service, watch_list=watch)
        scanner._position_service = position_service_empty

        result = await scanner.scan_market()
        # Result should be at most 5 (the watch_list size), even though
        # max_coins might be larger.
        assert len(result) <= 5
        assert all(c["symbol"] in watch for c in result)

    async def test_scan_market_does_not_double_fetch_positions(
        self, settings, market_service,
    ):
        """scan_market fetches positions ONCE and passes to _update_universe."""
        all_tickers = [_make_ticker(s) for s in ["BTCUSDT", "ETHUSDT"]]
        market_service.get_all_linear_tickers = AsyncMock(return_value=all_tickers)

        position_svc = MagicMock()
        position_svc.get_positions = AsyncMock(return_value=[])

        scanner = MarketScanner(
            settings, market_service,
            watch_list={"BTCUSDT", "ETHUSDT"},
        )
        scanner._position_service = position_svc

        await scanner.scan_market()

        # Position service must be called exactly once per scan_market —
        # the filter pre-fetch, NOT a second call inside _update_universe.
        assert position_svc.get_positions.call_count == 1


class TestUpdateUniverseDirect:
    async def test_update_universe_with_protected_skips_fetch(
        self, settings, market_service,
    ):
        """When _update_universe is called with protected_symbols, no fetch."""
        position_svc = MagicMock()
        position_svc.get_positions = AsyncMock(return_value=[])

        scanner = MarketScanner(settings, market_service)
        scanner._position_service = position_svc

        results = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
        await scanner._update_universe(results, protected_symbols={"POSITIONUSDT"})

        # No fetch happened — caller already provided the set.
        assert position_svc.get_positions.call_count == 0
        # POSITION coin was added to the universe even though not in results.
        assert "POSITIONUSDT" in scanner._active_universe

    async def test_update_universe_without_protected_fetches(
        self, settings, market_service,
    ):
        """Legacy direct caller without protected_symbols → method fetches itself."""
        position_svc = MagicMock()
        position_svc.get_positions = AsyncMock(return_value=[_make_position("POSCOINUSDT")])

        scanner = MarketScanner(settings, market_service)
        scanner._position_service = position_svc

        results = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
        await scanner._update_universe(results)  # no protected_symbols kwarg

        # Method fetched on its own — backward compatibility preserved.
        assert position_svc.get_positions.call_count == 1
        assert "POSCOINUSDT" in scanner._active_universe
