"""Enhanced Ring Buffer for Mode4 ProfitSniper — institutional-grade data pipeline.

Replaces the old PriceRingBuffer with a richer 14-field BufferPoint captured every
5 seconds. Designed to feed 5 new mathematical models (Hurst, Momentum Decay,
ATR Extension, Volume Profile, EV Shift) while maintaining backward compatibility
with the existing 5 models in sniper_models.py.

Components:
  BufferPoint        — dataclass storing one tick's enriched data
  EnhancedRingBuffer — fixed-size circular buffer (720 entries = 60 min)
  PositionProfitState — per-position peak/drawdown tracking
"""

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# Component A: BufferPoint — enriched tick data (14 fields)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class BufferPoint:
    """Single data point captured every 5 seconds for one position."""

    # ── Category A: Raw Market Data ──
    timestamp: float = 0.0          # Unix epoch
    price: float = 0.0              # Mid-price if bid/ask available, else last_price
    bid: float = 0.0                # Best bid
    ask: float = 0.0                # Best ask
    spread: float = 0.0             # ask - bid
    volume_delta: float = 0.0       # Volume traded in this 5s window
    buy_volume_est: float = 0.0     # Estimated buyer-initiated volume (Lee-Ready)
    sell_volume_est: float = 0.0    # Estimated seller-initiated volume (Lee-Ready)

    # ── Category B: Position Context ──
    pnl_pct: float = 0.0            # Position PnL % at this moment
    peak_pnl_pct: float = 0.0       # Highest PnL % seen so far
    drawdown_from_peak: float = 0.0  # Current PnL - peak PnL (always ≤ 0 when in profit)
    distance_from_entry_atr: float = 0.0  # |price - entry| in ATR units

    # ── Category C: Volatility Context ──
    atr_current: float = 0.0        # 14-period ATR on 5m candles
    cumulative_volume: float = 0.0  # Raw 24h volume (for delta computation)


# ═══════════════════════════════════════════════════════════════════════════════
# Component B: EnhancedRingBuffer — fixed-size circular buffer
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedRingBuffer:
    """Fixed-size circular buffer storing enriched tick data for a single position.

    Each entry is a BufferPoint captured every 5 seconds.
    Default: 720 entries = 60 minutes of data.
    Models require minimum 100 entries (8+ min) for valid computation.

    Provides numpy array getters (cached per-tick) for model computation,
    plus backward-compatible methods matching the old PriceRingBuffer API.
    """

    def __init__(self, symbol: str, max_size: int = 720, min_ready: int = 100):
        self.symbol = symbol
        self._max_size = max_size
        self._min_ready = min_ready
        self._buffer: deque[BufferPoint] = deque(maxlen=max_size)
        self._last_cumulative_volume: float = 0.0
        self._arrays_cache: dict | None = None

    # ── Core Operations ──

    def add_point(self, point: BufferPoint) -> None:
        """Add a new data point. Oldest auto-drops if full."""
        self._buffer.append(point)
        self._arrays_cache = None

    def append(self, entry) -> None:
        """Backward-compatible append. Accepts BufferPoint or old dict format."""
        if isinstance(entry, BufferPoint):
            self.add_point(entry)
        elif isinstance(entry, dict):
            # Old format: {ts, price, bid, ask, volume_24h}
            point = BufferPoint(
                timestamp=entry.get("ts", 0.0),
                price=entry.get("price", 0.0),
                bid=entry.get("bid", 0.0),
                ask=entry.get("ask", 0.0),
                spread=(entry.get("ask", 0) - entry.get("bid", 0))
                if entry.get("ask", 0) > 0 and entry.get("bid", 0) > 0
                else 0.0,
                cumulative_volume=entry.get("volume_24h", 0.0),
            )
            self.add_point(point)

    def is_ready(self, min_points: int | None = None) -> bool:
        """Do we have enough data for models?"""
        threshold = min_points if min_points is not None else self._min_ready
        return len(self._buffer) >= threshold

    def size(self) -> int:
        """Current number of points."""
        return len(self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)

    def time_span_seconds(self) -> float:
        """Time span covered by the buffer."""
        if len(self._buffer) < 2:
            return 0.0
        return self._buffer[-1].timestamp - self._buffer[0].timestamp

    def get_age_seconds(self) -> float:
        """Backward-compatible alias for time_span_seconds."""
        return self.time_span_seconds()

    def clear(self) -> None:
        """Clear all data."""
        self._buffer.clear()
        self._last_cumulative_volume = 0.0
        self._arrays_cache = None

    # ── Numpy Array Getters (cached per tick) ──

    def _ensure_cache(self) -> dict:
        if self._arrays_cache is None:
            self._arrays_cache = {}
        return self._arrays_cache

    def get_prices(self, n: int | None = None) -> list[float]:
        """Return prices as list (backward-compatible with old models).
        If n is given, return last n prices."""
        if n is not None:
            return [p.price for p in list(self._buffer)[-n:]]
        cache = self._ensure_cache()
        if "prices_list" not in cache:
            cache["prices_list"] = [p.price for p in self._buffer]
        return cache["prices_list"]

    def get_prices_np(self) -> np.ndarray:
        """Return prices as numpy array (for new models)."""
        cache = self._ensure_cache()
        if "prices_np" not in cache:
            cache["prices_np"] = np.array([p.price for p in self._buffer])
        return cache["prices_np"]

    def get_pnl_series(self) -> np.ndarray:
        """Return PnL % values as numpy array."""
        cache = self._ensure_cache()
        if "pnl" not in cache:
            cache["pnl"] = np.array([p.pnl_pct for p in self._buffer])
        return cache["pnl"]

    def get_volumes(self) -> np.ndarray:
        """Return volume deltas as numpy array."""
        cache = self._ensure_cache()
        if "volumes" not in cache:
            cache["volumes"] = np.array([p.volume_delta for p in self._buffer])
        return cache["volumes"]

    def get_buy_volumes(self) -> np.ndarray:
        """Return estimated buy volumes as numpy array."""
        return np.array([p.buy_volume_est for p in self._buffer])

    def get_sell_volumes(self) -> np.ndarray:
        """Return estimated sell volumes as numpy array."""
        return np.array([p.sell_volume_est for p in self._buffer])

    def get_timestamps(self, n: int | None = None) -> list[float]:
        """Return timestamps as list (backward-compatible)."""
        if n is not None:
            return [p.timestamp for p in list(self._buffer)[-n:]]
        cache = self._ensure_cache()
        if "timestamps_list" not in cache:
            cache["timestamps_list"] = [p.timestamp for p in self._buffer]
        return cache["timestamps_list"]

    def get_drawdowns(self) -> np.ndarray:
        """Return drawdown-from-peak values as numpy array."""
        return np.array([p.drawdown_from_peak for p in self._buffer])

    def get_atr_distances(self) -> np.ndarray:
        """Return ATR-normalized distances from entry as numpy array."""
        return np.array([p.distance_from_entry_atr for p in self._buffer])

    # ── Backward-Compatible Getters (for sniper_models.py) ──

    def get_latest(self) -> dict | None:
        """Return the most recent point as a dict (old format)."""
        if not self._buffer:
            return None
        p = self._buffer[-1]
        return {
            "ts": p.timestamp,
            "price": p.price,
            "bid": p.bid,
            "ask": p.ask,
            "volume_24h": p.cumulative_volume,
        }

    def get_all(self) -> list[dict]:
        """Return all points as list of dicts (old format)."""
        return [
            {
                "ts": p.timestamp,
                "price": p.price,
                "bid": p.bid,
                "ask": p.ask,
                "volume_24h": p.cumulative_volume,
            }
            for p in self._buffer
        ]

    def get_volume_data(self, n: int | None = None) -> list[float]:
        """Return cumulative volume values (backward-compatible)."""
        if n is not None:
            return [p.cumulative_volume for p in list(self._buffer)[-n:]]
        return [p.cumulative_volume for p in self._buffer]

    def get_spread_data(self, n: int | None = None) -> list[float]:
        """Return spread percentages (backward-compatible)."""
        data = list(self._buffer)[-n:] if n is not None else list(self._buffer)
        result = []
        for p in data:
            if p.price > 0 and p.bid > 0 and p.ask > 0:
                result.append((p.ask - p.bid) / p.price * 100)
            else:
                result.append(0.0)
        return result

    def get_slice(self, n: int) -> list[BufferPoint]:
        """Return the last N BufferPoints."""
        buf = list(self._buffer)
        return buf[-n:] if n < len(buf) else buf

    # ── Volume Delta Management ──

    def compute_volume_delta(self, current_cumulative: float) -> float:
        """Compute volume traded in this 5s window from cumulative delta."""
        if self._last_cumulative_volume == 0:
            self._last_cumulative_volume = current_cumulative
            return 0.0
        delta = current_cumulative - self._last_cumulative_volume
        self._last_cumulative_volume = current_cumulative
        return max(delta, 0.0)  # Can go negative on 24h rolling boundary

    # ── Lee-Ready Buy/Sell Volume Estimation ──

    @staticmethod
    def estimate_buy_sell_volume(
        price: float, bid: float, ask: float, total_volume: float,
    ) -> tuple[float, float]:
        """Classify total_volume into buy/sell using Lee-Ready (1991) tick rule.

        If price is at ask → buyer-initiated. At bid → seller-initiated.
        Between → proportional split. Industry standard (Bloomberg, FactSet).
        """
        if total_volume <= 0:
            return 0.0, 0.0
        if ask <= bid or bid <= 0 or ask <= 0:
            return total_volume * 0.5, total_volume * 0.5

        buy_ratio = max(0.0, min(1.0, (price - bid) / (ask - bid)))
        return total_volume * buy_ratio, total_volume * (1.0 - buy_ratio)


# ═══════════════════════════════════════════════════════════════════════════════
# Component C: PositionProfitState — per-position peak/drawdown tracking
# ═══════════════════════════════════════════════════════════════════════════════

class PositionProfitState:
    """Tracks profit lifecycle for a single position.

    Created when position opens, destroyed when it closes.
    Provides peak PnL tracking, drawdown computation, and age metrics.
    """

    __slots__ = (
        "symbol", "entry_price", "direction", "atr_at_entry", "opened_at",
        "peak_pnl_pct", "peak_price", "peak_timestamp",
        "ticks_in_profit", "ticks_total",
        # Loss-Cutting Phase 7 (2026-05-31) — worst-point tracking for the
        # final-phase history-aware recovery (the trough is the loss-side
        # mirror of the peak; trough_price is the price at the worst PnL).
        "trough_pnl_pct", "trough_price",
    )

    def __init__(
        self,
        symbol: str,
        entry_price: float,
        direction: str,
        atr_at_entry: float = 0.0,
        opened_at: float = 0.0,
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.direction = direction          # "Buy" or "Sell"
        self.atr_at_entry = atr_at_entry
        self.opened_at = opened_at or time.time()

        self.peak_pnl_pct: float = 0.0
        self.peak_price: float = entry_price
        self.peak_timestamp: float = self.opened_at

        self.ticks_in_profit: int = 0
        self.ticks_total: int = 0

        # Loss-Cutting Phase 7 — the trough (worst PnL) and the price at it.
        self.trough_pnl_pct: float = 0.0
        self.trough_price: float = entry_price

    def update(self, current_pnl_pct: float, current_price: float, now: float) -> None:
        """Update state with latest tick. Called every 5 seconds."""
        self.ticks_total += 1
        if current_pnl_pct > 0:
            self.ticks_in_profit += 1
        if current_pnl_pct > self.peak_pnl_pct:
            self.peak_pnl_pct = current_pnl_pct
            self.peak_price = current_price
            self.peak_timestamp = now
        # Loss-Cutting Phase 7 — track the worst PnL and the price at it (the
        # loss-side mirror of the peak), used by the final-phase recovery.
        if current_pnl_pct < self.trough_pnl_pct:
            self.trough_pnl_pct = current_pnl_pct
            self.trough_price = current_price

    @property
    def time_since_peak_seconds(self) -> float:
        return time.time() - self.peak_timestamp

    @property
    def age_seconds(self) -> float:
        return time.time() - self.opened_at

    @property
    def profit_ratio(self) -> float:
        """Fraction of ticks spent in profit (0.0 to 1.0)."""
        return self.ticks_in_profit / max(self.ticks_total, 1)
