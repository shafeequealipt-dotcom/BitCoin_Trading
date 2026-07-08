"""Shared type definitions: enums and dataclasses used across the entire system.

All enums inherit from (str, Enum) for JSON serialization compatibility.
All dataclasses include to_dict() and from_dict() for serialization.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# =============================================================================
# Enums
# =============================================================================

class Side(str, Enum):
    """Order/position side."""
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    """Supported order types."""
    MARKET = "Market"
    LIMIT = "Limit"
    STOP_MARKET = "StopMarket"
    STOP_LIMIT = "StopLimit"
    TAKE_PROFIT = "TakeProfit"


class OrderStatus(str, Enum):
    """Order lifecycle status."""
    NEW = "New"
    PARTIALLY_FILLED = "PartiallyFilled"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"


class TimeFrame(str, Enum):
    """Candlestick timeframes (values match Bybit API interval strings)."""
    M1 = "1"
    M5 = "5"
    M15 = "15"
    M30 = "30"
    H1 = "60"
    H4 = "240"
    D1 = "D"
    W1 = "W"


class SignalType(str, Enum):
    """Trading signal strength."""
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


class SentimentLevel(str, Enum):
    """Aggregated sentiment classification.

    ``UNKNOWN`` is distinct from ``NEUTRAL``: NEUTRAL means "real data
    exists and it averaged out near zero"; UNKNOWN means "no news / no
    reddit / no F&G signal was available for this symbol." Downstream
    consumers (APEX assembler, strategist prompts) should treat UNKNOWN
    as missing data rather than a genuine neutral stance, so the brain
    stops chasing coins with zero qualitative backing.
    """
    VERY_BULLISH = "very_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    VERY_BEARISH = "very_bearish"
    UNKNOWN = "unknown"


class TradingMode(str, Enum):
    """System trading mode."""
    PAPER = "paper"
    LIVE = "live"


class WorkerStatus(str, Enum):
    """Background worker state."""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    RESTARTING = "restarting"


class WorkerTier(str, Enum):
    """Layer 1 restructure Phase 4 — architectural sub-layer of a worker.

    Tags each worker with its Layer 1 sub-layer so cycle gating, log
    routing, and operational dashboards can group workers by role.
    The tier reflects the architectural concern, NOT the toggle layer
    that ``LayerManager.is_layer_active(N)`` answers — those diverge
    after Phase 8's renumbering (e.g. profit_sniper stays LAYER4 even
    when toggle layer 5 controls it).

    Values:
        LAYER1A: always-running data fetchers (kline, price, altdata, news).
        LAYER1B: cycle-triggered analyzers (structure, signal, regime).
        LAYER1C: strategy pipeline (Stage 1's 4 internal layers).
        LAYER1D: selector + package builder (scanner_worker).
        LAYER4:  position monitoring (profit_sniper, position_watchdog,
                 recovery_planner). Stays LAYER4 across Phase 8 renumber.
        LAYER5:  reserved (post-Phase-8 monitoring sub-tier).
        UTILITY: support workers (cleanup, discovery, alerts, etc.).
    """
    LAYER1A = "layer1a"
    LAYER1B = "layer1b"
    LAYER1C = "layer1c"
    LAYER1D = "layer1d"
    LAYER4 = "layer4"
    LAYER5 = "layer5"
    UTILITY = "utility"


class AlertLevel(str, Enum):
    """Alert severity level."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# =============================================================================
# Serialization helpers
# =============================================================================

def _serialize_value(val: Any) -> Any:
    """Serialize a single value for to_dict()."""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    if hasattr(val, "to_dict"):
        return val.to_dict()
    return val


def _deserialize_value(val: Any, field_type: type) -> Any:
    """Deserialize a single value for from_dict()."""
    origin = getattr(field_type, "__origin__", None)

    # Handle Optional (Union[X, None])
    if origin is type(int | str):  # UnionType
        args = field_type.__args__
        non_none = [a for a in args if a is not type(None)]
        if val is None:
            return None
        if non_none:
            return _deserialize_value(val, non_none[0])

    # Handle list[X]
    if origin is list:
        inner = field_type.__args__[0] if hasattr(field_type, "__args__") else str
        return [_deserialize_value(v, inner) for v in val]

    # Handle dict[K, V]
    if origin is dict:
        return val

    # Handle datetime
    if field_type is datetime:
        if isinstance(val, str):
            return datetime.fromisoformat(val)
        return val

    # Handle Enum subclasses
    if isinstance(field_type, type) and issubclass(field_type, Enum):
        if isinstance(val, field_type):
            return val
        return field_type(val)

    return val


# =============================================================================
# Base mixin for serialization
# =============================================================================

class SerializableMixin:
    """Mixin providing to_dict() and from_dict() for dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize this dataclass to a plain dict."""
        result: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            val = getattr(self, f.name)
            result[f.name] = _serialize_value(val)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SerializableMixin":
        """Reconstruct this dataclass from a plain dict."""
        hints = {f.name: f.type for f in fields(cls)}  # type: ignore[arg-type]
        resolved = {}
        # Get actual type annotations from the class
        annotations = {}
        for klass in reversed(cls.__mro__):
            annotations.update(getattr(klass, "__annotations__", {}))
        for f in fields(cls):  # type: ignore[arg-type]
            if f.name not in data:
                continue
            ft = annotations.get(f.name, str)
            resolved[f.name] = _deserialize_value(data[f.name], ft)
        return cls(**resolved)


# =============================================================================
# Dataclasses
# =============================================================================

@dataclass
class OHLCV(SerializableMixin):
    """Single candlestick bar."""
    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float = 0.0


@dataclass
class Ticker(SerializableMixin):
    """Real-time ticker snapshot."""
    symbol: str
    last_price: float
    bid: float
    ask: float
    high_24h: float
    low_24h: float
    volume_24h: float
    change_24h_pct: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Order(SerializableMixin):
    """A trading order."""
    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    price: float
    qty: float
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Position(SerializableMixin):
    """An open position."""
    symbol: str
    side: Side
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    liquidation_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PositionsQueryResult:
    """Discriminated result for ``get_positions_with_confirmation``.

    Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14): exchange adapters
    historically converted every API failure to ``return []`` — which is
    byte-for-byte identical to "Bybit confirms zero positions." The
    position watchdog interpreted both states the same way, triggering
    phantom-close events for live positions whenever a timestamp /
    network / auth failure occurred.

    The discriminated result preserves the semantic distinction:

      * ``confirmed=True``  → ``positions`` reflects exchange ground
        truth (may be empty if the exchange genuinely has no open
        positions).
      * ``confirmed=False`` → the adapter could not confirm exchange
        state. ``positions`` is empty by convention but **MUST NOT be
        interpreted as "no positions"**. Callers (typically the
        position watchdog) preserve their last-known state until a
        subsequent confirmed query updates them.

    Callers that don't need the confirmation flag continue to use the
    legacy ``get_positions() -> list[Position]`` method, which delegates
    to this new method and returns ``positions`` (empty on either
    state). The legacy contract is preserved.
    """

    confirmed: bool
    positions: tuple[Position, ...] = ()
    reason: str = ""  # populated when confirmed=False; e.g. "timestamp_fail"


@dataclass(frozen=True)
class BalanceQueryResult:
    """Discriminated result for ``get_wallet_balance_with_confirmation``.

    Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14): same architectural
    pattern as ``PositionsQueryResult`` applied to wallet-balance
    queries. The audited window observed 2 op=balance TIMESTAMP_FAIL
    events; the adapter converted each to ``_empty_account_info()``
    (zero equity) which downstream sizing / capital-tier / brain
    consumers misinterpreted as "account is empty."

      * ``confirmed=True``  → ``account`` reflects exchange ground truth.
      * ``confirmed=False`` → exchange did not confirm; callers
        preserve their last-known balance until a subsequent confirmed
        query.

    ``account`` is populated even when ``confirmed=False`` (with the
    zero sentinel) so callers that ignore the flag get the same legacy
    behavior. Callers that check the flag get the architectural
    upgrade.
    """

    confirmed: bool
    account: "AccountInfo | None" = None
    reason: str = ""


@dataclass
class NewsArticle(SerializableMixin):
    """A financial news article."""
    id: str
    headline: str
    source: str
    url: str
    summary: str
    sentiment_score: float  # -1.0 to 1.0
    symbols: list[str] = field(default_factory=list)
    category: str = ""
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RedditPost(SerializableMixin):
    """A Reddit post with sentiment data."""
    id: str
    subreddit: str
    title: str
    score: int
    num_comments: int
    upvote_ratio: float
    sentiment_score: float  # -1.0 to 1.0
    symbols_mentioned: list[str] = field(default_factory=list)
    permalink: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Signal(SerializableMixin):
    """A trading signal from any analysis source."""
    symbol: str
    signal_type: SignalType
    confidence: float  # 0.0 to 1.0
    source: str
    components: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TradeRecord(SerializableMixin):
    """Historical record of a completed trade."""
    trade_id: str
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    strategy: str = ""
    signal_confidence: float = 0.0
    notes: str = ""
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exit_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AccountInfo(SerializableMixin):
    """Account balance and margin snapshot."""
    total_equity: float
    available_balance: float
    used_margin: float
    unrealized_pnl: float
    margin_level_pct: float = 0.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FearGreedData(SerializableMixin):
    """Crypto Fear & Greed Index reading."""
    value: int  # 0-100
    classification: str  # e.g. "Extreme Fear", "Greed"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FundingRate(SerializableMixin):
    """Perpetual contract funding rate."""
    symbol: str
    funding_rate: float
    next_funding_time: datetime
    predicted_rate: float = 0.0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BrainDecision(SerializableMixin):
    """A decision made by the Claude Brain."""
    id: str
    action: str  # "buy", "sell", "hold", "close"
    symbol: str
    confidence: float  # 0.0 to 1.0
    order_type: OrderType = OrderType.MARKET
    reasoning: str = ""
    risk_notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WatchdogDecision(SerializableMixin):
    """A decision from the Position Watchdog's Claude review."""
    id: str
    action: str  # "hold", "tighten_stop", "partial_close", "full_close"
    symbol: str
    confidence: float  # 0.0 to 1.0
    new_stop_loss: float | None = None
    reasoning: str = ""
    risk_notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
