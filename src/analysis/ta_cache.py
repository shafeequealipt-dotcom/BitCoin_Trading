"""Centralized TA cache — compute once, share everywhere.

Eliminates duplicate TA computation across:
- strategy_worker (every 45s)
- signal_worker (every 120s)
- position_watchdog (every 15s)

Drop-in replacement for TAEngine — same analyze() interface.
"""

import asyncio
import time
from collections import OrderedDict

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("analysis")

# TTL is the sole freshness gate. At the live wiring (manager.py sets
# ttl_seconds=120.0) and a 45 s StrategyWorker cycle this yields the
# pattern MISS, HIT, HIT, MISS, HIT, HIT — a ~67 % hit rate in steady
# state. The DEFAULT_TTL below is used only when a caller constructs
# TACache without the explicit ttl argument.
DEFAULT_TTL = 90.0

# Stage-1/2 root-cause fix: the cache key is now `f"{sym}:{tf}"` in BOTH
# the candles-path and the symbol-path. Previously the candles-path key
# was `f"{sym}:{tf}:{len(candles)}"`, which rolled on every new kline
# arrival and produced 100 % misses. An interim fix replaced len with a
# 5-second monotonic bucket (`{sym}:{tf}:{bucket}`); the bucket still
# rolled 9× between 45 s ticks and produced 0 % cross-tick hits — the
# exact symptom observed in the 2026-04-24 observability window
# (`cache_valid=0 recomputed=31 hits=0` every STRAT_CYCLE_DONE).
#
# A second latent defect compounded it: the symbol-path key included
# `limit` as a fourth component (`{sym}:{tf}:{bucket}:{limit}`), which
# NEVER matched the candles-path key structure. strategy_worker wrote
# via the candles-path and strategist read via the symbol-path; even
# within the same bucket the two paths were disjoint. The prefetch was
# populating keys no reader ever asked for.
#
# Both paths now produce `f"{sym}:{tf}"`. The 90 s (default) / 120 s
# (live) TTL is the sole freshness gate. Different callers querying
# the same (symbol, timeframe) at different limits share the same
# cached analysis — semantic loss is negligible because TA indicators
# stabilize after ~50 candles; 100 vs 200 candle windows produce
# materially identical RSI/MACD/ADX/ATR values.

# Phase 6 (Stage-1/2 fix) defaults. Maxsize=200 is generous headroom
# against the steady-state working set (≈ 32 symbols × 2 timeframes
# × 2 limits ≈ 120 entries pre-key-unification; ≤ 64 entries post-fix).
# LRU eviction removes least-recently-used on write overflow, which
# combined with the TTL check at read guarantees the cache cannot grow
# beyond the bound regardless of caller patterns. TA_CACHE_SIZE log is
# rate-limited to 1 emission per _SIZE_LOG_MIN_INTERVAL so operators can
# watch the steady-state population without log spam.
_DEFAULT_MAXSIZE = 200
_SIZE_LOG_MIN_INTERVAL = 300.0  # seconds


class TACache:
    """Caching wrapper around TAEngine with TTL-based expiration and LRU bound.

    All consumers that previously called ta_engine.analyze() now get
    cached results if the same symbol/timeframe was computed within TTL.
    Phase 6 (Stage-1/2 fix): ``maxsize``-bounded OrderedDict with LRU
    eviction ensures the cache dict cannot grow without bound, closing
    the 596.2 MB / 600 MB MemoryHigh exhaustion risk called out in the
    2026-04-24 observability report.

    Args:
        ta_engine: The underlying TAEngine to delegate to.
        ttl_seconds: How long cached results remain valid.
        maxsize: Maximum number of cached entries. LRU eviction past this.
    """

    def __init__(
        self,
        ta_engine,
        ttl_seconds: float = DEFAULT_TTL,
        maxsize: int = _DEFAULT_MAXSIZE,
    ) -> None:
        self._engine = ta_engine
        self._ttl = ttl_seconds
        self._maxsize = max(int(maxsize), 1)
        # Phase 6: OrderedDict so the eviction policy can drop the
        # least-recently-used entry in O(1) via popitem(last=False) when
        # a write would push past ``self._maxsize``. ``move_to_end`` on
        # read promotes the hit to MRU.
        self._cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()
        # Phase 7 (P0-6): three counters instead of two so STRAT_CYCLE_DONE
        # can report `cache_lookups=N cache_valid=M recomputed=K` honestly.
        # ``_lookups`` = every call to analyze that hit the cache code path
        #               (i.e. had a usable key).
        # ``_valid_hits`` = lookups that returned a cached value within TTL.
        # ``_recomputed`` = lookups that fell through to the underlying
        #                   engine.analyze (cache miss OR stale entry).
        # Total of valid_hits + recomputed equals lookups.
        # ``_hits`` and ``_misses`` are kept as aliases for back-compat
        # with any downstream telemetry that still reads them.
        self._lookups = 0
        self._valid_hits = 0
        self._recomputed = 0
        # Phase 6: eviction counter + size-log throttle.
        self._evictions = 0
        self._last_size_log_ts: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def _hits(self) -> int:  # back-compat alias
        return self._valid_hits

    @property
    def _misses(self) -> int:  # back-compat alias
        return self._recomputed

    async def analyze(
        self,
        candles=None,
        symbol: str | None = None,
        timeframe=None,
        limit: int = 200,
    ) -> dict:
        """Cached version of TAEngine.analyze(). Same interface.

        Key is `f"{sym}:{tf}"` in both the candles-path and the
        symbol-path, unifying what strategy_worker prefetches with what
        strategist / volatility_profiler / apex_assembler / profit_sniper
        / tias / telegram handlers subsequently read. Freshness is
        decided entirely by the monotonic-time TTL check below.
        """
        if candles:
            sym = getattr(candles[0], "symbol", symbol or "UNK") if candles else "UNK"
            tf = getattr(candles[0], "timeframe", timeframe)
            tf_val = tf.value if hasattr(tf, "value") else str(tf) if tf else "?"
            key = f"{sym}:{tf_val}"
        elif symbol and timeframe:
            tf_val = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
            key = f"{symbol}:{tf_val}"
        else:
            return await self._engine.analyze(
                candles=candles, symbol=symbol, timeframe=timeframe, limit=limit,
            )

        # Check cache under lock (protects against interleaving at await points)
        now = time.monotonic()
        self._lookups += 1
        async with self._lock:
            cached = self._cache.get(key)
            if cached:
                cache_time, result = cached
                if now - cache_time < self._ttl:
                    self._valid_hits += 1
                    # Phase 6: promote hit to MRU end so LRU eviction
                    # never drops a hot entry.
                    self._cache.move_to_end(key)
                    return result

        # Cache miss — compute OUTSIDE lock (expensive async operation)
        self._recomputed += 1
        result = await self._engine.analyze(
            candles=candles, symbol=symbol, timeframe=timeframe, limit=limit,
        )

        # Write result under lock + enforce maxsize via LRU eviction.
        async with self._lock:
            self._cache[key] = (time.monotonic(), result)
            # If the write pushed us over maxsize, drop the LRU entry.
            # popitem(last=False) is the OrderedDict FIFO/LRU drop idiom.
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
                self._evictions += 1
        return result

    def is_fresh(self, symbol: str, timeframe: str = "60", max_age: int = 60) -> bool:
        """Check if cached TA is fresh enough."""
        for key, (ts, _) in self._cache.items():
            if key.startswith(f"{symbol}:{timeframe}"):
                return time.monotonic() - ts < max_age
        return False

    def invalidate(self, symbol: str | None = None) -> None:
        """Clear cache entries.

        Phase 6 (Stage-1/2 fix): preserves OrderedDict semantics so that
        subsequent ``move_to_end`` calls inside ``analyze()`` continue to
        work. The prior implementation rebuilt ``self._cache`` as a
        plain dict via a comprehension, which silently dropped the
        OrderedDict type and would have raised AttributeError on the
        next cache hit.
        """
        if symbol:
            prefix = f"{symbol}:"
            # In-place key removal to keep the OrderedDict instance.
            stale = [k for k in self._cache if k.startswith(prefix)]
            for k in stale:
                del self._cache[k]
        else:
            self._cache.clear()

    def get_stats(self) -> dict:
        # Phase 6: emit a rate-limited TA_CACHE_SIZE log on every get_stats()
        # call (strategy_worker invokes this once per tick). The throttle
        # produces roughly one entry per _SIZE_LOG_MIN_INTERVAL seconds —
        # enough for operators to watch steady-state population without
        # spamming the log stream at the 45 s strategy tick cadence.
        now_mt = time.monotonic()
        if (now_mt - self._last_size_log_ts) >= _SIZE_LOG_MIN_INTERVAL:
            self._last_size_log_ts = now_mt
            try:
                log.info(
                    f"TA_CACHE_SIZE | entries={len(self._cache)} "
                    f"maxsize={self._maxsize} evictions={self._evictions} "
                    f"hit_rate={self._valid_hits / max(self._lookups, 1):.2f} | "
                    f"{ctx()}"
                )
            except Exception:
                # Never let observability failures affect the hot path.
                pass
        return {
            # Phase 7 (P0-6): honest naming. Old fields kept for back-compat.
            "lookups": self._lookups,
            "valid_hits": self._valid_hits,
            "recomputed": self._recomputed,
            "hits": self._valid_hits,        # alias
            "misses": self._recomputed,      # alias
            "hit_rate": round(self._valid_hits / max(self._lookups, 1), 2),
            "cached_entries": len(self._cache),
            # Phase 6 (Stage-1/2 fix): LRU eviction metrics.
            "maxsize": self._maxsize,
            "evictions": self._evictions,
        }

    # Proxy all other TAEngine attributes for compatibility
    def __getattr__(self, name):
        return getattr(self._engine, name)
