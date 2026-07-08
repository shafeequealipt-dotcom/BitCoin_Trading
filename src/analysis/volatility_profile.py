"""Per-coin volatility profiling — adaptive TP/SL/hold parameters per coin's ATR.

Each coin gets a volatility class (dead/low/medium/high/extreme) based on its
normalised ATR on 5-minute candles.  Recommended trade parameters (TP%, SL%,
hold time, strategy type) are computed from the class and the coin's individual
regime, then cached for 60 seconds.

Usage:
    profiler = VolatilityProfiler(ta_cache, regime_detector, settings)
    profile = await profiler.get_profile("BTCUSDT")
    # profile.recommended_tp_pct, profile.recommended_sl_pct, etc.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.config.settings import VolatilityProfileSettings
from src.core.logging import get_logger
from src.core.types import TimeFrame

log = get_logger("volatility_profile")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoinVolatilityProfile:
    """Immutable snapshot of a coin's volatility characteristics and
    recommended trade parameters."""

    symbol: str

    # Core ATR metrics
    atr_pct_5m: float          # NATR on 5-min candles (primary classifier)
    atr_pct_1h: float          # NATR on 1-hour candles (secondary context)

    # Classification
    volatility_class: str      # "dead" / "low" / "medium" / "high" / "extreme"

    # Recommended trade parameters
    recommended_tp_pct: float
    recommended_sl_pct: float
    recommended_hold_min: int
    recommended_strategy: str  # "scalp" / "mean_revert" / "breakout" / "momentum" / "trend_follow"

    # Per-coin regime context
    regime: str                # e.g. "trending_up", "ranging", "dead"
    regime_confidence: float

    # Cache metadata
    timestamp: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Base parameters per volatility class
# ---------------------------------------------------------------------------

_BASE_PARAMS: dict[str, tuple[float, float, int, str]] = {
    #              tp%   sl%   hold  strategy
    "dead":     (0.30, 0.20,  10,  "scalp"),
    "low":      (0.50, 0.35,  20,  "mean_revert"),
    "medium":   (1.50, 1.00,  30,  "breakout"),
    "high":     (3.00, 2.00,  45,  "momentum"),
    "extreme":  (5.00, 3.00,  60,  "trend_follow"),
}

# Regime multipliers: (tp_mult, sl_mult, hold_mult)
_REGIME_MODS: dict[str, tuple[float, float, float]] = {
    "trending_up":   (1.3, 0.9, 1.2),
    "trending_down": (1.3, 0.9, 1.2),
    "ranging":       (0.7, 0.8, 0.8),
    "volatile":      (1.1, 1.2, 0.9),
    "dead":          (0.6, 0.7, 0.5),
}


# ---------------------------------------------------------------------------
# Profiler service
# ---------------------------------------------------------------------------

class VolatilityProfiler:
    """Computes and caches per-coin volatility profiles.

    Args:
        ta_cache: TACache instance (already registered, 30s TTL).
        regime_detector: RegimeDetector instance (may be None at init, late-wired).
        settings: VolatilityProfileSettings with thresholds and limits.
    """

    def __init__(
        self,
        ta_cache,
        regime_detector=None,
        settings: VolatilityProfileSettings | None = None,
    ) -> None:
        self._ta_cache = ta_cache
        self._regime_detector = regime_detector
        self._settings = settings or VolatilityProfileSettings()

        # Per-symbol cache: {symbol: CoinVolatilityProfile}
        self._cache: dict[str, CoinVolatilityProfile] = {}
        self._cache_ttl = self._settings.cache_ttl_seconds

        # Deterministic per-symbol jitter in [-15, +15] seconds. Splitting
        # the 30-coin universe across a 30 s window avoids the thundering-
        # herd miss-storm we saw at every TTL boundary (31 compute events
        # in the same minute). Hash-based so the same symbol always gets
        # the same offset — stable across restarts.
        self._symbol_jitter: dict[str, int] = {}

        # Stats
        self._hits = 0
        self._misses = 0

        # Rate-limited VOL_PROFILE_HIT log: without this, a fully-warm cache
        # looks indistinguishable from "profiler never called" in the logs.
        # One line per symbol per 60s is enough to prove the hot path runs.
        self._last_hit_log: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _ttl_for(self, symbol: str) -> float:
        """Per-symbol effective TTL = base_ttl + deterministic_jitter.

        Phase 5 (P0-4): jitter range comes from
        ``VolatilityProfileSettings.jitter_range_seconds`` (default 30) so
        operators can tune it independently of base TTL. With base 120 s
        and range 30, expirations spread across [90, 150] s — a 60 s
        window — eliminating the 30-coin thundering herd at the boundary.
        Hash-based, so the same coin always gets the same offset.
        """
        j = self._symbol_jitter.get(symbol)
        if j is None:
            jitter_range = int(getattr(self._settings, "jitter_range_seconds", 15))
            span = max(1, 2 * jitter_range + 1)
            j = (hash(symbol) & 0x7FFFFFFF) % span - jitter_range
            self._symbol_jitter[symbol] = j
        return max(1.0, self._cache_ttl + j)

    async def get_profile(self, symbol: str) -> CoinVolatilityProfile:
        """Return a cached or freshly computed volatility profile for *symbol*."""
        cached = self._cache.get(symbol)
        if cached and (time.monotonic() - cached.timestamp) < self._ttl_for(symbol):
            self._hits += 1
            _now = time.monotonic()
            if _now - self._last_hit_log.get(symbol, 0.0) >= 60.0:
                self._last_hit_log[symbol] = _now
                log.debug(
                    "VOL_PROFILE_HIT | sym={sym} class={cls} atr_pct={atr:.2f}%",
                    sym=symbol, cls=cached.volatility_class, atr=cached.atr_pct_5m,
                )
            return cached

        self._misses += 1
        profile = await self._compute(symbol)
        self._cache[symbol] = profile
        return profile

    async def get_all_profiles(
        self, symbols: list[str],
    ) -> dict[str, CoinVolatilityProfile]:
        """Batch-fetch profiles for multiple symbols."""
        result: dict[str, CoinVolatilityProfile] = {}
        for sym in symbols:
            try:
                result[sym] = await self.get_profile(sym)
            except Exception:
                pass
        return result

    def get_stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1) * 100, 1),
            "cached_symbols": len(self._cache),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _compute(self, symbol: str) -> CoinVolatilityProfile:
        """Compute a fresh profile by querying TACache and RegimeDetector."""

        atr_pct_5m = 0.0
        atr_pct_1h = 0.0

        # --- Fetch 5-minute ATR (primary classifier) ---
        try:
            ta_5m = await self._ta_cache.analyze(
                symbol=symbol, timeframe=TimeFrame.M5, limit=100,
            )
            vol_5m = ta_5m.get("volatility", {}) if ta_5m else {}
            atr_pct_5m = vol_5m.get("natr_14") or 0.0

            # If NATR not available, compute from absolute ATR
            if atr_pct_5m == 0.0:
                atr_abs = vol_5m.get("atr_14") or 0.0
                price = ta_5m.get("price") or ta_5m.get("close") or 0.0
                if not price:
                    # Try extracting close from the last candle
                    overall = ta_5m.get("overall", {})
                    price = overall.get("close") or overall.get("price") or 0.0
                if atr_abs > 0 and price > 0:
                    atr_pct_5m = (atr_abs / price) * 100
        except Exception as e:
            log.debug("VOL_PROFILE_5M_FAIL | sym={sym} err='{err}'", sym=symbol, err=str(e)[:80])

        # --- Fetch 1-hour ATR (secondary context) ---
        try:
            ta_1h = await self._ta_cache.analyze(
                symbol=symbol, timeframe=TimeFrame.H1, limit=100,
            )
            vol_1h = ta_1h.get("volatility", {}) if ta_1h else {}
            atr_pct_1h = vol_1h.get("natr_14") or 0.0

            if atr_pct_1h == 0.0:
                atr_abs = vol_1h.get("atr_14") or 0.0
                price = ta_1h.get("price") or ta_1h.get("close") or 0.0
                if not price:
                    overall = ta_1h.get("overall", {})
                    price = overall.get("close") or overall.get("price") or 0.0
                if atr_abs > 0 and price > 0:
                    atr_pct_1h = (atr_abs / price) * 100
        except Exception as e:
            log.debug("VOL_PROFILE_1H_FAIL | sym={sym} err='{err}'", sym=symbol, err=str(e)[:80])

        # --- Per-coin regime ---
        regime_str = "unknown"
        regime_conf = 0.0
        if self._regime_detector:
            try:
                cr = self._regime_detector.get_coin_regime(symbol)
                if cr:
                    regime_str = cr.regime.value
                    regime_conf = cr.confidence
            except Exception:
                pass

        # --- Classify ---
        vol_class = self._classify(atr_pct_5m)

        # --- Compute recommended parameters ---
        tp_pct, sl_pct, hold_min, strategy = self._compute_params(
            vol_class, regime_str, atr_pct_1h,
        )

        profile = CoinVolatilityProfile(
            symbol=symbol,
            atr_pct_5m=round(atr_pct_5m, 4),
            atr_pct_1h=round(atr_pct_1h, 4),
            volatility_class=vol_class,
            recommended_tp_pct=tp_pct,
            recommended_sl_pct=sl_pct,
            recommended_hold_min=hold_min,
            recommended_strategy=strategy,
            regime=regime_str,
            regime_confidence=round(regime_conf, 2),
        )

        log.info(
            "VOL_PROFILE | sym={sym} class={cls} atr_pct={atr:.2f}% regime={rgm} "
            "| tp={tp:.2f}% sl={sl:.2f}% hold={hold}min strategy={strat}",
            sym=symbol, cls=vol_class, atr=atr_pct_5m, rgm=regime_str,
            tp=tp_pct, sl=sl_pct, hold=hold_min, strat=strategy,
        )

        return profile

    def _classify(self, atr_pct_5m: float) -> str:
        """Map 5-min ATR percentage to a volatility class."""
        s = self._settings
        if atr_pct_5m < s.dead_threshold:
            return "dead"
        if atr_pct_5m < s.low_threshold:
            return "low"
        if atr_pct_5m < s.medium_threshold:
            return "medium"
        if atr_pct_5m < s.high_threshold:
            return "high"
        return "extreme"

    def _compute_params(
        self, vol_class: str, regime: str, atr_pct_1h: float,
    ) -> tuple[float, float, int, str]:
        """Return (tp_pct, sl_pct, hold_min, strategy) for given class + regime."""

        base_tp, base_sl, base_hold, strategy = _BASE_PARAMS.get(
            vol_class, _BASE_PARAMS["medium"],
        )

        # Apply regime modifiers
        tp_m, sl_m, hold_m = _REGIME_MODS.get(regime, (1.0, 1.0, 1.0))
        tp = base_tp * tp_m
        sl = base_sl * sl_m
        hold = int(base_hold * hold_m)

        # Adjust strategy for trending regimes
        if regime == "trending_up":
            strategy = "trend_follow" if vol_class in ("high", "extreme") else "momentum"
        elif regime == "trending_down":
            strategy = "trend_follow" if vol_class in ("high", "extreme") else "momentum"
        elif regime == "ranging" and vol_class in ("dead", "low"):
            strategy = "scalp"

        # Enforce floors and caps from settings
        s = self._settings
        tp = max(tp, s.min_tp_pct)
        sl = max(sl, s.min_sl_pct)
        tp = min(tp, s.max_tp_pct)
        sl = min(sl, s.max_sl_pct)
        hold = max(hold, 5)  # minimum 5 minutes

        return round(tp, 2), round(sl, 2), hold, strategy
