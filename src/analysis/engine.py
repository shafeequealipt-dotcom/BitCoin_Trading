"""Main Technical Analysis Engine — orchestrates all indicators and patterns.

Produces a comprehensive market analysis report from OHLCV data.
Makes zero API calls — works purely with data from the database or passed in directly.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.indicators import trend, momentum, volatility, volume
from src.analysis.patterns.candlestick import CandlestickDetector
from src.analysis.patterns.chart_patterns import ChartPatternDetector
from src.core.exceptions import DataError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OHLCV, SignalType, TimeFrame
from src.core.utils import clamp, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository

log = get_logger("analysis")

FloatArray = NDArray[np.float64]

MIN_CANDLES = 50


def _nan_to_none(val):
    """Convert NaN/inf to None for JSON compatibility."""
    if val is None:
        return None
    if isinstance(val, float):
        if np.isnan(val) or np.isinf(val):
            return None
        return round(val, 6)
    if isinstance(val, np.floating):
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 6)
    return val


def _last_valid(arr: FloatArray):
    """Get the last non-NaN value from an array, or None."""
    for i in range(len(arr) - 1, -1, -1):
        if not np.isnan(arr[i]):
            return round(float(arr[i]), 6)
    return None


class TAEngine:
    """Technical Analysis Engine — runs all indicators and patterns.

    Args:
        db: Optional DatabaseManager for fetching OHLCV from the database.
        settings: Optional Settings — when provided, ``settings.ta``
            controls confidence smoothing. Without settings, smoothing
            is disabled (legacy alpha=1.0 behaviour preserved).
    """

    def __init__(
        self,
        db: DatabaseManager | None = None,
        settings: "object | None" = None,  # type: ignore[type-arg]
    ) -> None:
        self._db = db
        self._market_repo = MarketRepository(db) if db else None
        self._candle_detector = CandlestickDetector()
        self._chart_detector = ChartPatternDetector()
        self._settings = settings
        # XRAY phase-4 fix — per-symbol confidence history for EMA
        # smoothing in _compute_overall_signal. Bounded by universe size
        # (~50 entries). Survives across analyze() calls; cleared only
        # on engine restart.
        self._prev_confidence_by_symbol: dict[str, float] = {}
        log.info("TA Engine initialized")
        if bool(getattr(getattr(settings, "ta", None),
                        "volume_ratio_use_closed_candle", False)):
            log.info(
                "VOL_RATIO_CLOSED_CANDLE_SENTINEL | enabled=True | volume_sma_ratio "
                "computed on last CLOSED candle, forming bucket excluded "
                "(Item 4 entry-gaps 2026-05-26)"
            )

    async def analyze(
        self,
        candles: list[OHLCV] | None = None,
        symbol: str | None = None,
        timeframe: TimeFrame | None = None,
        limit: int = 200,
    ) -> dict:
        """Run full technical analysis.

        Either pass candles directly or provide symbol + timeframe to fetch from DB.

        Args:
            candles: OHLCV data (if provided, used directly).
            symbol: Trading pair (used with timeframe to fetch from DB).
            timeframe: Candlestick timeframe.
            limit: Number of candles to fetch.

        Returns:
            Comprehensive analysis dict with indicators, patterns, and overall signal.

        Raises:
            DataError: If insufficient data or no data source specified.
        """
        if candles is None:
            if symbol is None or timeframe is None:
                raise DataError("Provide either candles or symbol + timeframe")
            if self._market_repo is None:
                raise DataError("No database connection for fetching candles")
            candles = await self._market_repo.get_klines(symbol, timeframe.value, limit)

        if len(candles) < MIN_CANDLES:
            raise DataError(
                f"Need at least {MIN_CANDLES} candles, got {len(candles)}",
                details={"count": len(candles)},
            )

        sym = candles[0].symbol if candles else (symbol or "UNKNOWN")
        tf = candles[0].timeframe.value if candles else (timeframe.value if timeframe else "?")

        opens = np.array([c.open for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        volumes = np.array([c.volume for c in candles], dtype=np.float64)

        indicators = self._compute_all_indicators(opens, highs, lows, closes, volumes)
        candle_patterns = self._candle_detector.detect_all(opens, highs, lows, closes)
        chart_patterns = self._chart_detector.detect_all(highs, lows, closes)
        overall = self._compute_overall_signal(
            indicators, candle_patterns, chart_patterns, sym=sym,
        )

        sr = self._compute_support_resistance(highs, lows, closes)

        result = {
            "symbol": sym,
            "timeframe": tf,
            "candles_analyzed": len(candles),
            "current_price": _nan_to_none(closes[-1]),
            "timestamp": now_utc().isoformat(),
            "trend": indicators["trend"],
            "momentum": indicators["momentum"],
            "volatility": indicators["volatility"],
            "volume": indicators["volume"],
            "patterns": {
                "candlestick": candle_patterns,
                "chart": chart_patterns,
            },
            "support_resistance": sr,
            "overall": overall,
        }

        if overall["signal"] != "NEUTRAL" or abs(overall["score"]) > 0.3:
            log.debug(
                f"TA | sym={sym} tf={tf} result={overall['signal']} "
                f"score={overall['score']:.2f} conf={overall['confidence']:.2f} "
                f"conf_raw={overall.get('confidence_raw', overall['confidence']):.2f} | {ctx()}"
            )
        log.info(
            "Analysis complete for {s} {tf}: {sig} (score={sc:.2f}, conf={c:.2f}, conf_raw={cr:.2f})",
            s=sym, tf=tf, sig=overall["signal"], sc=overall["score"],
            c=overall["confidence"],
            cr=overall.get("confidence_raw", overall["confidence"]),
        )
        return result

    async def get_indicator(self, candles: list[OHLCV], indicator_name: str, **params) -> dict:
        """Run a single indicator by name.

        Args:
            candles: OHLCV data.
            indicator_name: e.g. "rsi", "macd", "bollinger_bands".
            **params: Indicator-specific parameters.

        Returns:
            Dict with name, value(s), and interpretation.
        """
        closes = np.array([c.close for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        vols = np.array([c.volume for c in candles], dtype=np.float64)

        indicator_map = {
            "sma": lambda: {"value": _last_valid(trend.sma(closes, params.get("period", 20)))},
            "ema": lambda: {"value": _last_valid(trend.ema(closes, params.get("period", 20)))},
            "rsi": lambda: {"value": _last_valid(momentum.rsi(closes, params.get("period", 14)))},
            "macd": lambda: self._single_macd(closes, params),
            "bollinger_bands": lambda: self._single_bb(closes, params),
            "atr": lambda: {"value": _last_valid(volatility.atr(highs, lows, closes, params.get("period", 14)))},
            "obv": lambda: {"value": _last_valid(volume.obv(closes, vols))},
            "vwap": lambda: {"value": _last_valid(volume.vwap(highs, lows, closes, vols))},
            "stochastic": lambda: self._single_stoch(highs, lows, closes, params),
            "adx": lambda: self._single_adx(highs, lows, closes, params),
        }

        if indicator_name not in indicator_map:
            return {"name": indicator_name, "error": f"Unknown indicator: {indicator_name}"}

        values = indicator_map[indicator_name]()
        return {"name": indicator_name, **values}

    async def get_support_resistance(self, candles: list[OHLCV], num_levels: int = 3) -> dict:
        """Find support and resistance levels.

        Args:
            candles: OHLCV data.
            num_levels: Number of levels to return.

        Returns:
            Dict with support_levels, resistance_levels, current_price.
        """
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        return self._compute_support_resistance(highs, lows, closes, num_levels)

    # --- Private helpers ---

    def _compute_all_indicators(self, opens, highs, lows, closes, volumes) -> dict:
        """Compute all indicators organized by category."""
        # Trend
        sma_20 = trend.sma(closes, 20)
        sma_50 = trend.sma(closes, 50)
        sma_200 = trend.sma(closes, 200)
        ema_12 = trend.ema(closes, 12)
        ema_26 = trend.ema(closes, 26)
        macd_l, macd_s, macd_h = trend.macd(closes)
        adx_v, pdi, mdi = trend.adx(highs, lows, closes)
        st_line, st_dir = trend.supertrend(highs, lows, closes)
        psar = trend.parabolic_sar(highs, lows, closes)

        trend_signals = self._count_trend_signals(closes, sma_20, sma_50, ema_12, macd_h, st_dir)

        trend_data = {
            "sma_20": _last_valid(sma_20),
            "sma_50": _last_valid(sma_50),
            "sma_200": _last_valid(sma_200),
            "ema_12": _last_valid(ema_12),
            "ema_26": _last_valid(ema_26),
            "macd": {
                "macd_line": _last_valid(macd_l),
                "signal_line": _last_valid(macd_s),
                "histogram": _last_valid(macd_h),
            },
            "adx": {
                "adx": _last_valid(adx_v),
                "plus_di": _last_valid(pdi),
                "minus_di": _last_valid(mdi),
            },
            "supertrend": {
                "value": _last_valid(st_line),
                "direction": _nan_to_none(st_dir[-1]) if len(st_dir) > 0 else None,
            },
            "parabolic_sar": _last_valid(psar),
            "trend_summary": trend_signals,
        }

        # Momentum
        rsi_14 = momentum.rsi(closes, 14)
        stoch_k, stoch_d = momentum.stochastic(highs, lows, closes)
        srsi_k, srsi_d = momentum.stochastic_rsi(closes)
        cci_20 = momentum.cci(highs, lows, closes)
        wr = momentum.williams_r(highs, lows, closes)
        roc_12 = momentum.roc(closes, 12)
        mom_10 = momentum.momentum_indicator(closes, 10)
        ao = momentum.awesome_oscillator(highs, lows)
        tsi_v, tsi_s = momentum.tsi(closes)

        rsi_val = _last_valid(rsi_14)
        mom_summary = "NEUTRAL"
        if rsi_val is not None:
            if rsi_val > 60:
                mom_summary = "BULLISH"
            elif rsi_val < 40:
                mom_summary = "BEARISH"

        momentum_data = {
            "rsi_14": rsi_val,
            "stochastic": {"k": _last_valid(stoch_k), "d": _last_valid(stoch_d)},
            "stochastic_rsi": {"k": _last_valid(srsi_k), "d": _last_valid(srsi_d)},
            "cci_20": _last_valid(cci_20),
            "williams_r": _last_valid(wr),
            "roc_12": _last_valid(roc_12),
            "momentum_10": _last_valid(mom_10),
            "awesome_oscillator": _last_valid(ao),
            "tsi": {"tsi": _last_valid(tsi_v), "signal": _last_valid(tsi_s)},
            "momentum_summary": mom_summary,
        }

        # Volatility
        bb_u, bb_m, bb_l, bb_bw = volatility.bollinger_bands(closes)
        atr_14 = volatility.atr(highs, lows, closes)
        natr_14 = volatility.natr(highs, lows, closes)
        kc_u, kc_m, kc_l = volatility.keltner_channels(highs, lows, closes)
        dc_u, dc_m, dc_l = volatility.donchian_channels(highs, lows)
        hv = volatility.historical_volatility(closes)
        ci = volatility.choppiness_index(highs, lows, closes)

        atr_val = _last_valid(atr_14)
        natr_val = _last_valid(natr_14)
        vol_summary = "MODERATE"
        if natr_val is not None:
            if natr_val > 3.0:
                vol_summary = "EXTREME"
            elif natr_val > 1.5:
                vol_summary = "HIGH"
            elif natr_val < 0.5:
                vol_summary = "LOW"

        volatility_data = {
            "bollinger": {
                "upper": _last_valid(bb_u),
                "middle": _last_valid(bb_m),
                "lower": _last_valid(bb_l),
                "bandwidth": _last_valid(bb_bw),
            },
            "atr_14": atr_val,
            "natr_14": natr_val,
            "keltner": {"upper": _last_valid(kc_u), "middle": _last_valid(kc_m), "lower": _last_valid(kc_l)},
            "donchian": {"upper": _last_valid(dc_u), "middle": _last_valid(dc_m), "lower": _last_valid(dc_l)},
            "historical_volatility": _last_valid(hv),
            "choppiness_index": _last_valid(ci),
            "volatility_summary": vol_summary,
        }

        # Volume
        obv_v = volume.obv(closes, volumes)
        vwap_v = volume.vwap(highs, lows, closes, volumes)
        mfi_v = volume.mfi(highs, lows, closes, volumes)
        cmf = volume.chaikin_money_flow(highs, lows, closes, volumes)
        ad = volume.accumulation_distribution(highs, lows, closes, volumes)
        # Item 4 (entry-gaps investigation, 2026-05-26): optionally compute the
        # volume ratio on the last CLOSED candle, excluding the still-forming
        # newest bucket whose partial volume biases the ratio low (the kline
        # fetch includes the forming bucket — market_service.get_klines has no
        # end-bound). Default off preserves legacy behaviour. Only the ratio is
        # affected; force_index and the other volume indicators are unchanged.
        _use_closed = bool(
            getattr(getattr(self._settings, "ta", None),
                    "volume_ratio_use_closed_candle", False)
        )
        _vols_for_ratio = volumes[:-1] if (_use_closed and len(volumes) > 1) else volumes
        if (_use_closed and len(volumes) > 1
                and not getattr(self, "_closed_candle_acted_logged", False)):
            self._closed_candle_acted_logged = True
            log.info(
                "VOL_RATIO_CLOSED_CANDLE_ACTIVE | volume ratio now excludes the "
                "forming bucket (first occurrence this process; Item 4 entry-gaps)"
            )
        vol_avg, vol_ratio = volume.volume_sma(_vols_for_ratio)
        fi = volume.force_index(closes, volumes)

        ratio_val = _last_valid(vol_ratio)
        vol_sum = "AVERAGE"
        if ratio_val is not None:
            if ratio_val > 2.0:
                vol_sum = "SPIKE"
            elif ratio_val > 1.3:
                vol_sum = "ABOVE_AVERAGE"
            elif ratio_val < 0.7:
                vol_sum = "BELOW_AVERAGE"
            elif ratio_val < 0.4:
                vol_sum = "LOW"

        volume_data = {
            "obv": _last_valid(obv_v),
            "vwap": _last_valid(vwap_v),
            "mfi_14": _last_valid(mfi_v),
            "chaikin_money_flow": _last_valid(cmf),
            "accumulation_distribution": _last_valid(ad),
            "volume_sma_ratio": ratio_val,
            "force_index": _last_valid(fi),
            "volume_summary": vol_sum,
        }

        return {
            "trend": trend_data,
            "momentum": momentum_data,
            "volatility": volatility_data,
            "volume": volume_data,
            "_raw": {
                "rsi": rsi_14,
                "macd_h": macd_h,
                "sma_50": sma_50,
                "ema_20": trend.ema(closes, 20),
                "adx": adx_v,
                "st_dir": st_dir,
                "bb_u": bb_u,
                "bb_l": bb_l,
                "vol_ratio": vol_ratio,
                "closes": closes,
            },
        }

    def _count_trend_signals(self, closes, sma_20, sma_50, ema_12, macd_h, st_dir) -> str:
        """Summarize trend direction from multiple indicators."""
        bull = 0
        bear = 0
        c = closes[-1]
        if not np.isnan(sma_50[-1]) and c > sma_50[-1]:
            bull += 1
        elif not np.isnan(sma_50[-1]):
            bear += 1
        if not np.isnan(ema_12[-1]) and c > ema_12[-1]:
            bull += 1
        elif not np.isnan(ema_12[-1]):
            bear += 1
        if not np.isnan(macd_h[-1]) and macd_h[-1] > 0:
            bull += 1
        elif not np.isnan(macd_h[-1]):
            bear += 1
        if not np.isnan(st_dir[-1]) and st_dir[-1] > 0:
            bull += 1
        elif not np.isnan(st_dir[-1]):
            bear += 1
        if bull > bear:
            return "BULLISH"
        elif bear > bull:
            return "BEARISH"
        return "NEUTRAL"

    def _compute_overall_signal(
        self,
        indicators: dict,
        candle_patterns: list,
        chart_patterns: list,
        sym: str = "UNKNOWN",
    ) -> dict:
        """Score all indicators and patterns into an overall signal.

        XRAY phase-4 fix: ``confidence`` is EMA-smoothed against the
        previous value for the same symbol so single-indicator flips
        (RSI crossing 50, MACD histogram zero-cross) do not produce the
        0.14 swings that pre-fix drove cycle-to-cycle Context flapping
        in the TradeScorer. Both ``confidence`` (smoothed) and
        ``confidence_raw`` (this-cycle ratio) are returned so operators
        can see the dampening in flight.

        ``alpha`` reads from ``settings.ta.confidence_ema_alpha`` when a
        Settings reference was wired at construction; falls back to 1.0
        (no smoothing — legacy behaviour) otherwise.
        """
        raw = indicators.get("_raw", {})
        score = 0.0
        bullish = 0
        bearish = 0
        neutral = 0
        reasons: list[str] = []

        # RSI
        rsi_val = _last_valid(raw.get("rsi", np.array([np.nan])))
        if rsi_val is not None:
            if rsi_val < 30:
                score += 1.0
                bullish += 1
                reasons.append(f"RSI oversold at {rsi_val:.1f}")
            elif rsi_val > 70:
                score -= 1.0
                bearish += 1
                reasons.append(f"RSI overbought at {rsi_val:.1f}")
            else:
                neutral += 1
                if rsi_val > 50:
                    reasons.append(f"RSI at {rsi_val:.1f} (bullish side)")
                else:
                    reasons.append(f"RSI at {rsi_val:.1f}")

        # MACD histogram
        macd_h_arr = raw.get("macd_h", np.array([np.nan]))
        macd_val = _last_valid(macd_h_arr)
        if macd_val is not None:
            if len(macd_h_arr) >= 2:
                prev = _last_valid(macd_h_arr[:-1])
                if prev is not None:
                    if macd_val > 0 and macd_val > prev:
                        score += 1.0
                        bullish += 1
                        reasons.append("MACD histogram positive and rising")
                    elif macd_val < 0 and macd_val < prev:
                        score -= 1.0
                        bearish += 1
                        reasons.append("MACD histogram negative and falling")
                    elif macd_val > 0:
                        score += 0.3
                        bullish += 1
                    elif macd_val < 0:
                        score -= 0.3
                        bearish += 1

        # Price vs SMA 50
        closes = raw.get("closes", np.array([]))
        sma_50_arr = raw.get("sma_50", np.array([np.nan]))
        if len(closes) > 0 and len(sma_50_arr) > 0:
            sma50_val = _last_valid(sma_50_arr)
            if sma50_val is not None:
                if closes[-1] > sma50_val:
                    score += 0.5
                    bullish += 1
                    reasons.append("Price above SMA 50 (uptrend)")
                else:
                    score -= 0.5
                    bearish += 1
                    reasons.append("Price below SMA 50 (downtrend)")

        # Price vs EMA 20
        ema_20_arr = raw.get("ema_20", np.array([np.nan]))
        if len(closes) > 0 and len(ema_20_arr) > 0:
            ema20_val = _last_valid(ema_20_arr)
            if ema20_val is not None:
                if closes[-1] > ema20_val:
                    score += 0.5
                    bullish += 1
                else:
                    score -= 0.5
                    bearish += 1

        # ADX strength
        adx_arr = raw.get("adx", np.array([np.nan]))
        adx_val = _last_valid(adx_arr)

        # Supertrend
        st_dir_arr = raw.get("st_dir", np.array([np.nan]))
        st_val = _last_valid(st_dir_arr)
        if st_val is not None:
            if st_val > 0:
                score += 1.0
                bullish += 1
                reasons.append("Supertrend bullish")
            else:
                score -= 1.0
                bearish += 1
                reasons.append("Supertrend bearish")

        # Bollinger position
        bb_u = raw.get("bb_u", np.array([np.nan]))
        bb_l = raw.get("bb_l", np.array([np.nan]))
        if len(closes) > 0:
            bb_u_val = _last_valid(bb_u)
            bb_l_val = _last_valid(bb_l)
            if bb_u_val is not None and bb_l_val is not None:
                bb_range = bb_u_val - bb_l_val
                if bb_range > 0:
                    pos = (closes[-1] - bb_l_val) / bb_range
                    if pos < 0.2:
                        score += 0.5
                        bullish += 1
                        reasons.append("Price near lower Bollinger Band (potential bounce)")
                    elif pos > 0.8:
                        score -= 0.5
                        bearish += 1

        # Volume
        vol_ratio_arr = raw.get("vol_ratio", np.array([np.nan]))
        vol_r = _last_valid(vol_ratio_arr)
        if vol_r is not None and vol_r > 2.0:
            score += 0.3 * np.sign(score) if score != 0 else 0
            reasons.append(f"Volume {vol_r:.1f}x above average (confirms move)")

        # Candlestick patterns
        for p in candle_patterns:
            if p["type"] == "bullish":
                score += 0.3
                bullish += 1
                reasons.append(f"Bullish {p['name']} pattern")
            elif p["type"] == "bearish":
                score -= 0.3
                bearish += 1
                reasons.append(f"Bearish {p['name']} pattern")

        # Chart patterns
        for p in chart_patterns:
            if p["type"] == "bullish":
                score += 0.5
                bullish += 1
                reasons.append(f"Bullish {p['name']} chart pattern")
            elif p["type"] == "bearish":
                score -= 0.5
                bearish += 1
                reasons.append(f"Bearish {p['name']} chart pattern")

        # ADX amplification/reduction
        if adx_val is not None:
            if adx_val > 25:
                score *= 1.2
            elif adx_val < 20:
                score *= 0.8

        # Normalize
        total = bullish + bearish + neutral
        raw_confidence = 0.0
        if total > 0:
            dominant = max(bullish, bearish)
            raw_confidence = dominant / total

        # XRAY phase-4 fix — EMA smoothing against per-symbol history.
        # alpha=1.0 disables smoothing (legacy behaviour); alpha=0.4 is
        # the empirically-chosen default that halves cycle-to-cycle
        # variance while preserving response within ~3 cycles.
        alpha = 1.0
        try:
            if self._settings is not None and hasattr(self._settings, "ta"):
                alpha = float(getattr(self._settings.ta, "confidence_ema_alpha", 1.0))
        except Exception:
            alpha = 1.0
        if alpha >= 1.0 - 1e-9:
            confidence = raw_confidence
        else:
            prev = self._prev_confidence_by_symbol.get(sym, raw_confidence)
            confidence = alpha * raw_confidence + (1.0 - alpha) * prev
        # Always store the smoothed value so the next cycle picks it up.
        self._prev_confidence_by_symbol[sym] = confidence

        score = clamp(score / 5.0, -1.0, 1.0)  # Normalize to [-1, 1]

        if score > 0.5:
            signal = "STRONG_BUY"
        elif score > 0.2:
            signal = "BUY"
        elif score < -0.5:
            signal = "STRONG_SELL"
        elif score < -0.2:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

        return {
            "signal": signal,
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "confidence_raw": round(raw_confidence, 4),
            "bullish_indicators": bullish,
            "bearish_indicators": bearish,
            "neutral_indicators": neutral,
            "key_reasons": reasons[:10],
        }

    def _compute_support_resistance(self, highs, lows, closes, num_levels: int = 3) -> dict:
        """Find support and resistance levels using local extrema."""
        peaks = ChartPatternDetector._find_local_maxima(highs, order=5)
        troughs = ChartPatternDetector._find_local_minima(lows, order=5)

        current = float(closes[-1])

        resistance = sorted(set(float(highs[p]) for p in peaks if highs[p] > current), key=lambda x: x)[:num_levels]
        support = sorted(set(float(lows[t]) for t in troughs if lows[t] < current), key=lambda x: -x)[:num_levels]

        return {
            "support_levels": [round(s, 2) for s in support],
            "resistance_levels": [round(r, 2) for r in resistance],
            "current_price": round(current, 2),
        }

    def _single_macd(self, closes, params):
        ml, sl, hist = trend.macd(closes, params.get("fast", 12), params.get("slow", 26), params.get("signal", 9))
        return {"macd_line": _last_valid(ml), "signal_line": _last_valid(sl), "histogram": _last_valid(hist)}

    def _single_bb(self, closes, params):
        u, m, l, bw = volatility.bollinger_bands(closes, params.get("period", 20), params.get("std_dev", 2.0))
        return {"upper": _last_valid(u), "middle": _last_valid(m), "lower": _last_valid(l), "bandwidth": _last_valid(bw)}

    def _single_stoch(self, highs, lows, closes, params):
        k, d = momentum.stochastic(highs, lows, closes, params.get("k_period", 14), params.get("d_period", 3))
        return {"k": _last_valid(k), "d": _last_valid(d)}

    def _single_adx(self, highs, lows, closes, params):
        a, p, m = trend.adx(highs, lows, closes, params.get("period", 14))
        return {"adx": _last_valid(a), "plus_di": _last_valid(p), "minus_di": _last_valid(m)}
