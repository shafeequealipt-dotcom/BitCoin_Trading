"""Tests for system-wide constants."""

from src.config.constants import (
    API_RATE_LIMITS,
    DATABASE_TABLES,
    FEAR_GREED_RANGES,
    MAX_ORDER_QTY,
    MCP_TOOLS,
    MIN_ORDER_QTY,
    SENTIMENT_THRESHOLDS,
    SUPPORTED_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
    WORKER_NAMES,
    RateLimitConfig,
)


class TestSupportedSymbols:
    def test_contains_major_pairs(self):
        assert "BTCUSDT" in SUPPORTED_SYMBOLS
        assert "ETHUSDT" in SUPPORTED_SYMBOLS
        assert "SOLUSDT" in SUPPORTED_SYMBOLS

    def test_supports_legacy_frozenset_contract(self):
        """The registry replaced the legacy ``frozenset[str]`` constant.

        Test the CONTRACT, not the type — every operation legacy callers
        relied on (``in``, ``iter``, ``len``, ``-``, ``|``, ``&``,
        equality, ``frozenset(...)`` conversion) must continue to work.
        """
        # Membership.
        assert "BTCUSDT" in SUPPORTED_SYMBOLS
        assert "DEFINITELY_NOT_A_SYMBOL" not in SUPPORTED_SYMBOLS

        # Iteration + cardinality.
        symbols = list(SUPPORTED_SYMBOLS)
        assert len(symbols) == len(SUPPORTED_SYMBOLS)
        assert symbols == sorted(symbols)  # deterministic order

        # Conversion to frozenset (snapshot).
        snap = frozenset(SUPPORTED_SYMBOLS)
        assert isinstance(snap, frozenset)
        assert "BTCUSDT" in snap

        # Set algebra returns frozenset snapshots.
        diff = SUPPORTED_SYMBOLS - frozenset({"BTCUSDT"})
        assert isinstance(diff, frozenset)
        assert "BTCUSDT" not in diff
        union = SUPPORTED_SYMBOLS | frozenset({"FAKEUSDT"})
        assert "FAKEUSDT" in union and "BTCUSDT" in union


class TestSupportedTimeframes:
    def test_contains_common_timeframes(self):
        assert "1" in SUPPORTED_TIMEFRAMES
        assert "60" in SUPPORTED_TIMEFRAMES
        assert "D" in SUPPORTED_TIMEFRAMES

    def test_is_frozenset(self):
        assert isinstance(SUPPORTED_TIMEFRAMES, frozenset)


class TestOrderQtyLimits:
    def test_min_qty_keys_match_symbols(self):
        for symbol in MIN_ORDER_QTY:
            assert symbol in SUPPORTED_SYMBOLS

    def test_max_qty_keys_match_symbols(self):
        for symbol in MAX_ORDER_QTY:
            assert symbol in SUPPORTED_SYMBOLS

    def test_min_less_than_max(self):
        for symbol in MIN_ORDER_QTY:
            assert MIN_ORDER_QTY[symbol] < MAX_ORDER_QTY[symbol]


class TestRateLimits:
    def test_all_apis_present(self):
        expected = {"bybit_rest", "bybit_order", "finnhub", "reddit", "coingecko", "telegram", "claude"}
        assert expected == set(API_RATE_LIMITS.keys())

    def test_rate_limit_config_frozen(self):
        config = RateLimitConfig(requests_per_second=5.0, burst_size=5)
        assert config.requests_per_second == 5.0


class TestSentimentThresholds:
    def test_has_expected_keys(self):
        assert "very_bullish" in SENTIMENT_THRESHOLDS
        assert "very_bearish" in SENTIMENT_THRESHOLDS


class TestFearGreedRanges:
    def test_covers_full_range(self):
        all_values = set()
        for low, high in FEAR_GREED_RANGES.values():
            all_values.update(range(low, high + 1))
        assert 0 in all_values
        assert 100 in all_values


class TestWorkerNames:
    def test_has_required_workers(self):
        assert "market_data_worker" in WORKER_NAMES
        assert "news_worker" in WORKER_NAMES

    def test_is_frozenset(self):
        assert isinstance(WORKER_NAMES, frozenset)


class TestMCPTools:
    def test_has_trading_tools(self):
        assert "PLACE_ORDER" in MCP_TOOLS
        assert "GET_POSITIONS" in MCP_TOOLS

    def test_values_are_strings(self):
        for key, val in MCP_TOOLS.items():
            assert isinstance(val, str)


class TestDatabaseTables:
    def test_has_core_tables(self):
        assert "OHLCV" in DATABASE_TABLES
        assert "ORDERS" in DATABASE_TABLES
        assert "TRADES" in DATABASE_TABLES

    def test_values_are_lowercase(self):
        for key, val in DATABASE_TABLES.items():
            assert val == val.lower()
