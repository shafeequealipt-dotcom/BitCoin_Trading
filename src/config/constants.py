"""System-wide constants: supported symbols, rate limits, table names, etc.

Most constants are module-level frozen values. SUPPORTED_SYMBOLS is a dynamic
registry updated by the scanner every 5 minutes with the top coins by score.
"""

import threading
from dataclasses import dataclass
from typing import Iterable


# =============================================================================
# Dynamic symbol registry — replaces the old frozenset
# =============================================================================

class SymbolRegistry:
    """Dynamic symbol set updated by the scanner at runtime.

    Drop-in replacement for the legacy ``frozenset[str]`` constant.
    Supports the full set-algebra contract callers depended on:

    * Membership: ``"BTCUSDT" in registry``
    * Iteration: ``for sym in registry``  (sorted, deterministic)
    * Conversion: ``frozenset(registry)``, ``list(registry)``,
      ``sorted(registry)``
    * Cardinality: ``len(registry)``
    * Set algebra (returns ``frozenset[str]``):
      - ``registry - other``  (``__sub__``)
      - ``registry | other``  (``__or__``)
      - ``registry & other``  (``__and__``)
      - ``registry ^ other``  (``__xor__``)
    * Subset / superset: ``registry <= other``, ``registry >= other``
    * Equality vs ``set``/``frozenset``: ``registry == frozenset({...})``

    The set ops return frozenset SNAPSHOTS (point-in-time copies) rather
    than living views so callers can use them safely after a scanner
    update mutates the registry's underlying state. This is the same
    semantics frozenset offers and is what the legacy code depended on
    when the value was a literal frozenset.

    Pre-seeded with the well-known symbols that production code expects
    to be present at boot (BTC/ETH plus every key in ``MIN_ORDER_QTY``).
    The scanner calls ``update()`` after each scan cycle to refresh the
    set with the top coins by opportunity score; BTC/ETH are always
    preserved (HR-2 reference pairs).
    """

    # ── construction ────────────────────────────────────────────────
    def __init__(self, initial: Iterable[str] | None = None) -> None:
        self._symbols: set[str] = set(initial) if initial else {"BTCUSDT", "ETHUSDT"}
        self._lock = threading.Lock()

    def update(self, symbols: Iterable[str]) -> None:
        """Replace the active symbol set (called by scanner each cycle).

        BTC/ETH reference pairs are always preserved per blueprint HR-2.
        """
        with self._lock:
            self._symbols = set(symbols) | {"BTCUSDT", "ETHUSDT"}

    def add(self, symbol: str) -> None:
        """Add a single symbol to the registry (used at boot to seed
        well-known symbols from order-qty tables)."""
        with self._lock:
            self._symbols.add(symbol)

    def snapshot(self) -> frozenset[str]:
        """Return a point-in-time frozen copy of the current symbol set."""
        with self._lock:
            return frozenset(self._symbols)

    # ── membership / iteration / cardinality ────────────────────────
    def __contains__(self, symbol: object) -> bool:
        if not isinstance(symbol, str):
            return False
        with self._lock:
            return symbol in self._symbols

    def __iter__(self):
        # Sorted iteration so log lines and tests get deterministic order.
        with self._lock:
            return iter(sorted(self._symbols))

    def __len__(self) -> int:
        with self._lock:
            return len(self._symbols)

    def __bool__(self) -> bool:
        return len(self) > 0

    def __repr__(self) -> str:
        with self._lock:
            return f"SymbolRegistry({sorted(self._symbols)})"

    # ── set algebra (returns frozenset snapshots) ──────────────────
    @staticmethod
    def _coerce(other: object) -> frozenset[str]:
        """Coerce any iterable of strings to a frozenset for set ops."""
        if isinstance(other, SymbolRegistry):
            return other.snapshot()
        if isinstance(other, (set, frozenset)):
            return frozenset(other)
        if isinstance(other, Iterable):
            return frozenset(other)
        raise TypeError(
            f"unsupported operand type(s) for set algebra: "
            f"'SymbolRegistry' and '{type(other).__name__}'"
        )

    def __sub__(self, other: object) -> frozenset[str]:
        return self.snapshot() - self._coerce(other)

    def __rsub__(self, other: object) -> frozenset[str]:
        return self._coerce(other) - self.snapshot()

    def __or__(self, other: object) -> frozenset[str]:
        return self.snapshot() | self._coerce(other)

    __ror__ = __or__  # symmetric

    def __and__(self, other: object) -> frozenset[str]:
        return self.snapshot() & self._coerce(other)

    __rand__ = __and__  # symmetric

    def __xor__(self, other: object) -> frozenset[str]:
        return self.snapshot() ^ self._coerce(other)

    __rxor__ = __xor__

    def __le__(self, other: object) -> bool:
        return self.snapshot() <= self._coerce(other)

    def __ge__(self, other: object) -> bool:
        return self.snapshot() >= self._coerce(other)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SymbolRegistry):
            return self.snapshot() == other.snapshot()
        try:
            return self.snapshot() == self._coerce(other)
        except TypeError:
            return NotImplemented

    def __hash__(self) -> None:
        # Mutable container — explicitly unhashable. Prevents accidental
        # use as a dict key (callers should use snapshot() first).
        raise TypeError("SymbolRegistry is mutable; call .snapshot() to hash")


# Pre-seeded with the symbols every code path expects at boot. The
# scanner replaces the set on each cycle, but the seed guards against:
#   * RiskValidator / OrderService rejecting trades on known symbols
#     before the first scanner tick.
#   * Decision parser dropping Claude trades on known symbols at boot.
# The seed list is the union of the order-qty tables defined below and
# BTC/ETH reference pairs (HR-2). It is computed AFTER the order-qty
# dicts so the registry stays in lock-step with whatever those tables
# advertise.
SUPPORTED_SYMBOLS: SymbolRegistry = SymbolRegistry({"BTCUSDT", "ETHUSDT"})

# Symbols that exist in SUPPORTED_SYMBOLS but don't have reliable ticker data
# on Bybit testnet. Excluded from testnet scanning to prevent log flooding.
TESTNET_EXCLUDED_SYMBOLS: frozenset[str] = frozenset({
    "AVAXUSDT",
    "MATICUSDT",
})

SUPPORTED_TIMEFRAMES: frozenset[str] = frozenset({
    "1", "5", "15", "30", "60", "240", "D", "W",
})

# =============================================================================
# Order quantity limits (in base currency units)
# =============================================================================

MIN_ORDER_QTY: dict[str, float] = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.1,
    "XRPUSDT": 1.0,
    "DOGEUSDT": 10.0,
    "ADAUSDT": 1.0,
    "AVAXUSDT": 0.1,
    "DOTUSDT": 0.1,
    "LINKUSDT": 0.1,
    "MATICUSDT": 1.0,
}

# Quantity step sizes for rounding on Bybit testnet
TESTNET_QTY_STEPS: dict[str, float] = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.1,
    "XRPUSDT": 0.1,
    "DOGEUSDT": 1.0,
    "ADAUSDT": 1.0,
    "DOTUSDT": 0.1,
    "LINKUSDT": 0.1,
    "MATICUSDT": 0.1,
    "AVAXUSDT": 0.1,
}

MAX_ORDER_QTY: dict[str, float] = {
    "BTCUSDT": 100.0,
    "ETHUSDT": 1000.0,
    "SOLUSDT": 10000.0,
    "XRPUSDT": 1000000.0,
    "DOGEUSDT": 10000000.0,
    "ADAUSDT": 1000000.0,
    "AVAXUSDT": 100000.0,
    "DOTUSDT": 100000.0,
    "LINKUSDT": 100000.0,
    "MATICUSDT": 1000000.0,
}

# Seed the dynamic registry with every key from the order-qty tables.
# This is the boot-time invariant downstream code depends on: any
# symbol in ``MIN_ORDER_QTY`` / ``MAX_ORDER_QTY`` is also a member of
# ``SUPPORTED_SYMBOLS`` from the moment the module loads, so order
# validation and risk checks work before the scanner has had its
# first tick.
for _seed_symbol in (set(MIN_ORDER_QTY.keys()) | set(MAX_ORDER_QTY.keys())):
    SUPPORTED_SYMBOLS.add(_seed_symbol)
del _seed_symbol

# =============================================================================
# API rate limits (requests per second unless noted)
# =============================================================================


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limit configuration for an API."""
    requests_per_second: float
    burst_size: int = 1


API_RATE_LIMITS: dict[str, RateLimitConfig] = {
    "bybit_rest": RateLimitConfig(requests_per_second=10.0, burst_size=10),
    "bybit_order": RateLimitConfig(requests_per_second=5.0, burst_size=5),
    "finnhub": RateLimitConfig(requests_per_second=1.0, burst_size=1),  # 60/min
    "reddit": RateLimitConfig(requests_per_second=1.0, burst_size=1),   # 60/min
    "coingecko": RateLimitConfig(requests_per_second=0.17, burst_size=1),  # ~10/min
    "telegram": RateLimitConfig(requests_per_second=1.0, burst_size=5),
    "claude": RateLimitConfig(requests_per_second=0.5, burst_size=2),
}

# =============================================================================
# Sentiment analysis thresholds
# =============================================================================

SENTIMENT_THRESHOLDS: dict[str, float] = {
    "very_bullish": 0.6,
    "bullish": 0.2,
    "neutral_low": -0.2,
    "neutral_high": 0.2,
    "bearish": -0.2,
    "very_bearish": -0.6,
}

# Fear & Greed Index ranges
FEAR_GREED_RANGES: dict[str, tuple[int, int]] = {
    "extreme_fear": (0, 24),
    "fear": (25, 44),
    "neutral": (45, 55),
    "greed": (56, 75),
    "extreme_greed": (76, 100),
}

# =============================================================================
# Worker registry
# =============================================================================

WORKER_NAMES: frozenset[str] = frozenset({
    "market_data_worker",
    "news_worker",
    "reddit_worker",
    "altdata_worker",
    "health_check_worker",
    "position_watchdog",
    "scanner_worker",
    "regime_worker",
    "strategy_worker",
    "discovery_worker",
    "live_monitor_worker",
    "backtest_worker",
    "trial_monitor_worker",
    "allocation_worker",
    "optimization_worker",
    "telegram_bot_worker",
    "price_alert_worker",
})

# =============================================================================
# MCP tool name constants
# =============================================================================

MCP_TOOLS: dict[str, str] = {
    # Market data
    "GET_PRICE": "get_price",
    "GET_OHLCV": "get_ohlcv",
    "GET_TICKER": "get_ticker",
    "GET_ORDERBOOK": "get_orderbook",
    # Trading
    "PLACE_ORDER": "place_order",
    "CANCEL_ORDER": "cancel_order",
    "GET_POSITIONS": "get_positions",
    "GET_ORDERS": "get_orders",
    "CLOSE_POSITION": "close_position",
    # Account
    "GET_BALANCE": "get_balance",
    "GET_ACCOUNT": "get_account",
    # Intelligence
    "GET_NEWS": "get_news",
    "GET_SENTIMENT": "get_sentiment",
    "GET_SIGNALS": "get_signals",
    "GET_FEAR_GREED": "get_fear_greed",
    "GET_FUNDING_RATES": "get_funding_rates",
    # Brain
    "BRAIN_ANALYZE": "brain_analyze",
    "BRAIN_STATUS": "brain_status",
    # System
    "SYSTEM_STATUS": "system_status",
    "WORKER_STATUS": "worker_status",
}

# =============================================================================
# Database table names
# =============================================================================

DATABASE_TABLES: dict[str, str] = {
    # Market data layer
    "OHLCV": "ohlcv",
    "TICKERS": "tickers",
    # Intelligence layer
    "NEWS": "news_articles",
    "REDDIT_POSTS": "reddit_posts",
    "SIGNALS": "signals",
    "FEAR_GREED": "fear_greed",
    "FUNDING_RATES": "funding_rates",
    # Trading layer
    "ORDERS": "orders",
    "POSITIONS": "positions",
    "TRADES": "trade_records",
    "ACCOUNT_SNAPSHOTS": "account_snapshots",
    # Learning layer
    "BRAIN_DECISIONS": "brain_decisions",
    "PERFORMANCE_METRICS": "performance_metrics",
    "PATTERNS": "patterns",
}
