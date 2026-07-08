"""Signal generator: combines all intelligence into trading signals.

Uses contrarian logic: fear = buying opportunity, extreme greed = caution.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.core.decorators import timed
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import Signal, SignalType
from src.core.utils import clamp, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository
from src.database.repositories.market_repo import MarketRepository
from src.intelligence.sentiment.aggregator import SentimentAggregator
from src.intelligence.signals.confidence import ConfidenceCalculator
from src.intelligence.signals.signal_models import (
    CONFIDENCE_THRESHOLDS,
)

if TYPE_CHECKING:
    from src.config.settings import Settings

log = get_logger("intelligence")


class SignalGenerator:
    """Generates trading signals from aggregated intelligence.

    Combines sentiment, Fear & Greed, funding rates, and OI into signals
    using a multi-source weighted classifier (Phase 1 output-quality fix).

    Args:
        aggregator: Sentiment aggregator.
        db: Database manager.
        settings: Optional Application settings; when omitted (legacy
            constructor signature) the multi-source classifier loads
            its dataclass defaults. Production paths in
            ``WorkerManager`` and ``mcp/server.py`` pass the live
            ``Settings`` so operators can tune via
            ``[signal_generator.multi_source]`` in ``config.toml``.
    """

    def __init__(
        self,
        aggregator: SentimentAggregator,
        db: DatabaseManager,
        settings: "Settings | None" = None,
    ) -> None:
        self._aggregator = aggregator
        self._db = db
        self._altdata_repo = AltDataRepository(db)
        # Phase 3 (Stage-1/2 fix): MarketRepository for ticker + M5 kline
        # access powering the redefined _volume_factor (trading volume
        # surge ratio) and the freshness computation.
        self._market_repo = MarketRepository(db)
        self._confidence = ConfidenceCalculator()
        # Phase 1 (output-quality): multi-source classification settings.
        # Falls back to defaults when settings is None so existing test
        # constructors ``SignalGenerator(aggregator, db)`` keep working.
        if settings is not None and hasattr(settings, "signal_generator"):
            self._ms_cfg = settings.signal_generator.multi_source
        else:
            from src.config.settings import SignalGeneratorMultiSourceSettings
            self._ms_cfg = SignalGeneratorMultiSourceSettings()
        # CALL_B Framing Fix Phase 5B (2026-05-06) — sentiment consumption
        # gate. When False, the sentiment branch in `_evaluate_signal`
        # is forced inactive (regardless of `sentiment_min_active`)
        # because Reddit is config-disabled and Finnhub free tier has
        # no altcoin coverage; the source isn't actually contributing
        # signal in the production deployment. Default False matches
        # the dataclass default; opt back in by toggling
        # `[sentiment].consumption_enabled = true` in config.toml.
        #
        # Layer 1 Defect 5 (2026-05-21) — the dead-code fallback below
        # historically defaulted to True (and the inner getattr fallback
        # also to True), contradicting both the documented intent above
        # and the settings dataclass default at settings.py:1825. The
        # contradiction was harmless in production because the settings
        # path always wins, but it made the codebase lie about its own
        # behavior. Aligning the fallbacks to False — the single
        # authoritative default — makes the resolution intentional and
        # consistent at every site.
        self._sentiment_consumption_enabled: bool = False
        if settings is not None and hasattr(settings, "sentiment"):
            self._sentiment_consumption_enabled = bool(
                getattr(settings.sentiment, "consumption_enabled", False)
            )
        log.info(
            f"BOOT_SENTIMENT_CONSUMPTION | "
            f"enabled={self._sentiment_consumption_enabled} "
            f"source={'settings' if settings is not None else 'fallback_default'} "
            f"| {ctx()}"
        )
        if not self._sentiment_consumption_enabled:
            log.warning(
                f"SENT_CONSUMPTION_DISABLED | reason=operator_decision_2026-05-06 "
                f"effect=signal_generator_skip_sentiment_branch | {ctx()}"
            )

        # Layer 1 Defect 4 boot log — single sentiment normalizer ladder
        # used by both the confidence calculator and the classifier.
        # Pre-fix two ladders disagreed in magnitude AND fg/funding
        # sign convention; the unified ladder uses the contrarian
        # signs (high fear → bullish, crowded long → bearish) with
        # config-tunable divisors from
        # [signal_generator.multi_source] in config.toml.
        log.info(
            f"BOOT_SENT_NORM_OK | "
            f"fg_div={self._ms_cfg.fg_normalize_range} "
            f"funding_div={self._ms_cfg.funding_normalize} "
            f"oi_div={self._ms_cfg.oi_normalize_pct} "
            f"fg_weight={self._ms_cfg.fg_weight} "
            f"fg_direction_neutral={getattr(self._ms_cfg, 'fg_direction_neutral', True)} "
            f"oi_price_window_h={getattr(self._ms_cfg, 'oi_price_window_hours', 24.0)} "
            f"oi_price_dead_band={getattr(self._ms_cfg, 'oi_price_dead_band_pct', 0.0)} "
            f"oi_blend_15m={getattr(self._ms_cfg, 'oi_blend_weight_15m', 0.4)} "
            f"oi_blend_short={getattr(self._ms_cfg, 'oi_blend_weight_short', 0.6)} "
            f"oi_blend_long={getattr(self._ms_cfg, 'oi_blend_weight_long', 0.0)} "
            f"oi_15m_window_h={getattr(self._ms_cfg, 'oi_15m_window_hours', 0.25)} "
            f"oi_short_window_h={getattr(self._ms_cfg, 'oi_short_window_hours', 1.0)} "
            f"sign_convention=contrarian | {ctx()}"
        )

    @timed
    async def generate_signal(self, symbol: str) -> Signal:
        """Generate a trading signal for a symbol.

        Combines:
        - Aggregated sentiment (news + reddit + fear&greed)
        - Funding rate (extreme = contrarian signal)
        - Open interest direction

        Args:
            symbol: Trading pair.

        Returns:
            Signal dataclass with type, confidence, and reasoning.
        """
        # Fix 3 (sentiment removal, 2026-06-10): the per-coin sentiment input
        # read zero on ~94% of coins, was already direction-inactive, and its
        # aggregate_for_symbol call was the origin of the SENT_UNKNOWN_CACHE_HIT
        # spam. It is now fully severed from the signal — no fetch here, no
        # direction contribution, no confidence contribution, no prompt field.
        # Fear-greed (fetched immediately below) is a SEPARATE market-wide value
        # and is UNTOUCHED.

        # Get Fear & Greed
        fg = await self._altdata_repo.get_latest_fear_greed()
        fg_value = fg.value if fg else 50

        # Get funding rate
        fr = await self._altdata_repo.get_latest_funding_rate(symbol)
        funding_rate = fr.funding_rate if fr else 0.0

        # Get latest OI
        oi = await self._altdata_repo.get_latest_open_interest(symbol)
        oi_change = oi.get("change_24h_pct", 0.0) if oi and isinstance(oi, dict) else 0.0

        # Fix 1 (price-conditioned OI, 2026-06-10): fetch the same-window (24h)
        # price change so the OI direction component reads correct futures
        # semantics — rising OI on a FALLING price is shorts piling in (bearish),
        # not longs accumulating (bullish). Reuses the market repo the generator
        # already holds. Defensive like the other external fetches here (volume
        # surge, blend windows): a ticker fetch failure degrades to 0.0 (no
        # conditioning this cycle), never crashes signal generation for the coin.
        price_change_24h = 0.0
        try:
            _ticker = await self._market_repo.get_ticker(symbol)
            if _ticker is not None:
                price_change_24h = float(
                    getattr(_ticker, "change_24h_pct", 0.0) or 0.0
                )
        except Exception as e:
            log.debug(
                "SIG_TICKER_FETCH_FAIL | sym={s} err='{err}'",
                s=symbol, err=str(e)[:80],
            )

        # Fix 2 (fresh signal inputs, 2026-06-10; Five-Fix Follow-Up adds the
        # 15m window): the 15-minute and 1-hour OI windows drive direction so
        # the signal moves at the cadence the system trades; the 24h read is
        # context-only by default. Each window is price-conditioned (Fix 1)
        # against its OWN matching price window and normalized BEFORE the blend.
        oi_change_1h = (
            float(oi.get("change_1h_pct", 0.0)) if oi and isinstance(oi, dict) else 0.0
        )
        oi_change_15m = (
            float(oi.get("change_15m_pct", 0.0)) if oi and isinstance(oi, dict) else 0.0
        )
        s_oi_blended, _oiw = await self._blend_oi_windows(
            symbol, oi_change, price_change_24h, oi_change_1h, oi_change_15m,
        )
        _s_1h_str = "na" if _oiw["s_1h"] is None else f"{_oiw['s_1h']:+.3f}"
        _s_15m_str = "na" if _oiw["s_15m"] is None else f"{_oiw['s_15m']:+.3f}"
        # cond_* are the annotation-only Fix-1 inversion tags (inv/pass/na) —
        # operator-approved observability so a price-conditioning flip is
        # explicit instead of inferred from sign comparison.
        log.info(
            f"SIG_OI_WINDOWS | sym={symbol} oi_24h={oi_change:+.2f} "
            f"oi_1h={oi_change_1h:+.2f} oi_15m={oi_change_15m:+.2f} "
            f"price_24h={price_change_24h:+.2f} "
            f"price_1h={_oiw['price_1h']:+.2f} price_15m={_oiw['price_15m']:+.2f} "
            f"s_24h={_oiw['s_24h']:+.3f} s_1h={_s_1h_str} s_15m={_s_15m_str} "
            f"s_blended={s_oi_blended:+.3f} "
            f"cond_24h={_oiw['cond_24h']} cond_1h={_oiw['cond_1h']} "
            f"cond_15m={_oiw['cond_15m']} | {ctx()}"
        )

        # Phase 1 (output-quality): per-coin input presence log so
        # operators can grep "which inputs reached classification" when
        # the distribution is unhealthy. Fires before classification so
        # SIG_GEN_INPUT and SIG_CLASSIFY pair on the same coin.
        ms = self._ms_cfg
        fg_active = abs((50 - fg_value) / ms.fg_normalize_range) >= ms.fg_min_active
        fund_active = abs(-funding_rate / ms.funding_normalize) >= ms.funding_min_active
        # Five-Fix Follow-Up — audit remediation (2026-06-11): oi_active is
        # gated on the BLENDED score — the same value _evaluate_signal's
        # active set tests — so this log truthfully shows which inputs reach
        # classification. Pre-fix it keyed on the raw 24h magnitude, which
        # Fix 2 demoted to context-only: a coin with a quiet 24h but active
        # 15m/1h windows logged oi_active=False while the classifier counted
        # OI active. Log-only alignment; no behavior change.
        oi_active = abs(s_oi_blended) >= ms.oi_min_active
        log.info(
            f"SIG_GEN_INPUT | sym={symbol} "
            f"fg_active={fg_active} "
            f"fund_active={fund_active} oi_active={oi_active} "
            f"fg={fg_value} "
            f"funding={funding_rate:+.5f} oi_change_24h={oi_change:+.2f} "
            f"oi_blended={s_oi_blended:+.3f} "
            f"price_chg_24h={price_change_24h:+.2f} | {ctx()}"
        )

        # Determine signal type using multi-source weighted classifier.
        signal_type, reasoning = self._evaluate_signal(
            fg_value, funding_rate, oi_change,
            price_change=price_change_24h, oi_score=s_oi_blended, symbol=symbol,
        )

        # Calculate confidence — Layer 1 Defect 4 reconciliation.
        # Pre-fix this block used a HARDCODED normalizer ladder
        # (fg/50, funding*100, oi/20) with NO sign inversion, while
        # the SIG_GEN_INPUT activity check above AND the
        # `_evaluate_signal` classifier ladder use the config-driven
        # ms.* divisors WITH sign inversion (high fear → bullish,
        # crowded long → bearish). Confidence and classifier therefore
        # disagreed both in magnitude AND in direction-sign for fg and
        # funding, so the confidence-vs-direction agreement factor
        # was computed against a contradicted view of the same inputs.
        # Aligning to the classifier ladder (config-driven, contrarian
        # signs) makes confidence and classification read the same
        # numbers. The BOOT_SENT_NORM_OK log line below confirms the
        # ladder identity at boot.
        fg_normalized = clamp(
            (50.0 - fg_value) / ms.fg_normalize_range, -1.0, 1.0,
        )
        fr_normalized = clamp(
            -funding_rate / ms.funding_normalize, -1.0, 1.0,
        )
        # Fix 1 + Fix 2: confidence reads the SAME blended, price-conditioned OI
        # the classifier uses (ladder identity preserved across the windowing).
        oi_normalized = s_oi_blended
        log.debug(
            f"SENT_NORM_VALUES | sym={symbol} "
            f"fg_norm={fg_normalized:+.3f} "
            f"fr_norm={fr_normalized:+.3f} "
            f"oi_norm={oi_normalized:+.3f} | {ctx()}"
        )

        # Phase 3 (Stage-1/2 fix): compute REAL data age in hours so the
        # freshness component of confidence stops being a dead constant.
        # Prior to this fix `data_age_hours` was hardcoded to 1.0, which
        # pinned _freshness_factor at 1.0 for every signal — 15 % of the
        # confidence formula was dead weight.
        data_age_hours = self._compute_data_age_hours(fg, fr, oi)

        # Phase 3 (Stage-1/2 fix): compute REAL trading-volume surge ratio
        # so _volume_factor reflects market activity instead of sentiment-
        # source availability (news_count + reddit_count). The old proxy
        # floored at 0.3 for 29 of 32 coins because Finnhub lacks altcoin
        # coverage and Reddit is globally disabled. news_count and
        # reddit_count are still carried in components so downstream logs
        # and tests that read them continue to work; they're just not
        # used to compute the volume factor any more.
        volume_surge_ratio = await self._compute_volume_surge_ratio(symbol)

        # Fix 3 (sentiment removal, 2026-06-10): sentiment is severed from the
        # confidence inputs too. The confidence components carry only the genuine
        # live inputs (fear-greed, funding, open interest) plus the freshness and
        # volume factors. confidence.py is None-safe and no longer iterates the
        # dropped news_sentiment/reddit_sentiment keys.
        components = {
            "fear_greed": fg_normalized,
            "funding_rate": fr_normalized,
            "open_interest": oi_normalized,
            "data_age_hours": data_age_hours,
            "volume_surge_ratio": volume_surge_ratio,
        }

        confidence = self._confidence.calculate(components)

        # Phase 29 (Y-28): enforce CONFIDENCE_THRESHOLDS as a hard gate.
        # The pre-Phase29 generator emitted STRONG_BUY at confidence
        # 0.29 because the type was chosen from sentiment ALONE while
        # the confidence gate was never enforced. Downgrade per the
        # ladder: STRONG_* requires conf >= 0.60; BUY/SELL require
        # >= 0.40; below that, fall back to NEUTRAL.
        #
        # CALL_B Framing Fix Phase 4B (2026-05-06) — make the downgrade
        # NON-DESTRUCTIVE. The pre-fix path overwrote `signal_type` so
        # downstream consumers (ScannerWorker, ClaudeStrategist via
        # _signal_cache) only saw the downgraded form. The fix preserves
        # the original classification in `components.original_signal_type`
        # alongside `components.confidence_floor_failed` so consumers can
        # opt into the pre-downgrade strength when their context warrants
        # it (e.g., XRAY's RR-asymmetry comparison, briefing pack
        # interestingness ranker). Back-compat preserved: the existing
        # `signal.signal_type` field still surfaces the downgraded form
        # so any consumer that reads only that field is unaffected.
        _orig_type = signal_type
        try:
            t_strong = float(CONFIDENCE_THRESHOLDS.get("strong_buy", 0.60))
            t_buy = float(CONFIDENCE_THRESHOLDS.get("buy", 0.40))
        except Exception:
            t_strong, t_buy = 0.60, 0.40
        _conf_below_strong = False
        _conf_below_buy = False
        if signal_type in (SignalType.STRONG_BUY, SignalType.STRONG_SELL):
            if confidence < t_strong:
                _conf_below_strong = True
                if confidence >= t_buy:
                    signal_type = (
                        SignalType.BUY if signal_type == SignalType.STRONG_BUY
                        else SignalType.SELL
                    )
                else:
                    _conf_below_buy = True
                    signal_type = SignalType.NEUTRAL
        elif signal_type in (SignalType.BUY, SignalType.SELL):
            if confidence < t_buy:
                _conf_below_buy = True
                signal_type = SignalType.NEUTRAL
        _was_downgraded = signal_type != _orig_type
        if _was_downgraded:
            log.info(
                f"SIG_DOWNGRADE | sym={symbol} from={_orig_type.value} "
                f"to={signal_type.value} conf={confidence:.2f} "
                f"strong_min={t_strong:.2f} buy_min={t_buy:.2f} | {ctx()}"
            )
            reasoning = f"[downgraded conf<threshold] {reasoning}"

        signal = Signal(
            symbol=symbol,
            signal_type=signal_type,
            confidence=confidence,
            source="intelligence_aggregator",
            components={
                # Fix 3 (sentiment removal, 2026-06-10): overall_sentiment,
                # news_count and reddit_count are no longer carried — they read
                # zero on ~94% of coins and the candidate block's Components line
                # already omitted them. Fear-greed, funding and OI remain as the
                # genuine per-coin inputs the brain sees.
                # Five-Fix Follow-Up — Fix 2 (2026-06-10): the brain now sees
                # the fresh driver windows (15m, 1h) beside the 24h context.
                # oi_change_pct was renamed oi_change_24h_pct so each window's
                # label states its measurement honestly.
                "fear_greed": fg_value,
                "funding_rate": funding_rate,
                "oi_change_15m_pct": oi_change_15m,
                "oi_change_1h_pct": oi_change_1h,
                "oi_change_24h_pct": oi_change,
                # Phase 4B (2026-05-06) — non-destructive downgrade meta.
                # Always set so downstream code can branch without a
                # presence check; original_signal_type matches signal_type
                # when no downgrade fired.
                "original_signal_type": _orig_type.value,
                "confidence_floor_failed": _was_downgraded,
                "confidence_below_strong": _conf_below_strong,
                "confidence_below_buy": _conf_below_buy,
            },
            reasoning=reasoning,
            created_at=now_utc(),
        )

        await self._altdata_repo.save_signal(signal)

        log.info(
            f"SIG_GEN | sym={symbol} type={signal_type.value} "
            f"conf={confidence:.2f} vol_surge={volume_surge_ratio:.2f} "
            f"age_h={data_age_hours:.2f} rsn='{reasoning[:80]}' | {ctx()}"
        )
        return signal

    def _compute_data_age_hours(self, fg, fr, oi) -> float:
        """Compute the OLDEST-input age in hours across confidence inputs.

        Phase 3 (Stage-1/2 fix): replaces the hardcoded ``1.0`` that
        pinned ``_freshness_factor`` at 1.0 forever. Uses the min (oldest)
        timestamp so a single stale input degrades freshness — consistent
        with "the confidence is as strong as the weakest-link data".

        Sources checked, in order: F&G (``fg.timestamp``), funding rate
        (``fr.fetched_at``), open interest (``oi['timestamp']``). Sentiment
        inputs are NOT checked here — their own timestamps live deep inside
        the aggregator; the per-symbol altdata timestamps are the
        lowest-overhead signal.

        Returns 24.0 (max degradation) if no timestamp is available.
        """
        now = now_utc()

        def _as_aware(dt: datetime) -> datetime:
            """Coerce any datetime to tz-aware UTC.

            Rows fetched from SQLite come back naive when they were
            stored with ``datetime.isoformat()`` omitting a tz suffix
            (e.g. ``save_open_interest`` persists ``now_utc()`` but the
            persisted string is naive on read). Mixing naive and aware
            datetimes in ``min()`` raises TypeError on Python 3.10+,
            so normalise up front.
            """
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        timestamps: list[datetime] = []
        try:
            if fg is not None:
                ts = getattr(fg, "timestamp", None)
                if isinstance(ts, datetime):
                    timestamps.append(_as_aware(ts))
        except Exception:
            pass
        try:
            if fr is not None:
                ts = getattr(fr, "fetched_at", None)
                if isinstance(ts, datetime):
                    timestamps.append(_as_aware(ts))
        except Exception:
            pass
        try:
            if oi is not None and isinstance(oi, dict):
                raw = oi.get("timestamp")
                if isinstance(raw, datetime):
                    timestamps.append(_as_aware(raw))
                elif isinstance(raw, str):
                    try:
                        timestamps.append(_as_aware(datetime.fromisoformat(raw)))
                    except Exception:
                        pass
        except Exception:
            pass

        if not timestamps:
            return 24.0

        # Oldest timestamp (smallest datetime) → largest age.
        oldest = min(timestamps)
        age_seconds = max((now - oldest).total_seconds(), 0.0)
        return age_seconds / 3600.0

    async def _compute_volume_surge_ratio(self, symbol: str) -> float:
        """Return the last 5-min volume / 20-period average volume ratio.

        Phase 3 (Stage-1/2 fix): replaces the dead ``news_count +
        reddit_count`` volume proxy with a measurement of actual market
        activity. A ratio >= 2.5 means the coin is trading at 2.5× its
        recent typical 5-min volume — a strong conviction cue. Ratios
        below 0.5 indicate a volume contraction.

        Returns 1.0 (neutral) when insufficient data is available so the
        downstream _volume_factor defaults to the 'normal activity'
        bucket rather than floor-clamping every under-covered coin.
        """
        try:
            klines = await self._market_repo.get_klines(symbol, "5", 21)
            if len(klines) < 21:
                return 1.0
            current_vol = float(klines[-1].volume or 0.0)
            prior = [float(k.volume or 0.0) for k in klines[-21:-1]]
            avg_vol = sum(prior) / len(prior) if prior else 0.0
            if avg_vol <= 0:
                return 1.0
            return current_vol / avg_vol
        except Exception as e:
            log.debug(
                "SIG_VOL_SURGE_FAIL | sym={s} err='{err}'",
                s=symbol, err=str(e)[:80],
            )
            return 1.0

    @timed
    async def generate_all_signals(self, symbols: list[str] | None = None) -> list[Signal]:
        """Generate signals for all configured symbols.

        Args:
            symbols: Trading pairs. Defaults to standard set.

        Returns:
            List of Signal dataclasses.
        """
        if symbols is None:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

        signals = []
        for symbol in symbols:
            try:
                signal = await self.generate_signal(symbol)
                signals.append(signal)
            except Exception as e:
                log.warning("Failed to generate signal for {s}: {err}", s=symbol, err=str(e))

        return signals

    @timed
    async def get_latest_signal(self, symbol: str) -> Signal | None:
        """Get the most recent signal for a symbol from the database.

        Args:
            symbol: Trading pair.

        Returns:
            Signal or None.
        """
        return await self._altdata_repo.get_latest_signal(symbol)

    def _condition_oi_score(
        self, oi_score: float, oi_change: float, price_change: float,
    ) -> float:
        """Price-condition the OI direction score (Fix 1, 2026-06-10).

        Correct futures semantics: rising OI is bullish only if price is ALSO
        rising (longs accumulating); rising OI on a FALLING price is shorts
        piling in — bearish. So when the OI change and the same-window price
        change have OPPOSITE signs (beyond an optional dead-band on the price
        move), invert the SIGN of the OI score; the magnitude is preserved.
        Same-sign moves (or a price move inside the dead-band, or no OI signal)
        leave the score unchanged.

        This corrects an inverted computation; it is NOT a flip to a standing
        short bias — the result is two-sided, driven by each coin's own price:

            OI up,   price up   -> longs accumulating  -> unchanged (bullish)
            OI up,   price down -> shorts piling in     -> inverted  (bearish)
            OI down, price up   -> short covering        -> inverted  (weak bull)
            OI down, price down -> long liquidation       -> unchanged (weak bear)

        Window-agnostic: Fix 2 reuses this per OI window with the matching
        price-change window.
        """
        cfg = self._ms_cfg
        dead_band = float(getattr(cfg, "oi_price_dead_band_pct", 0.0))
        # No usable OI signal, or price move inside the dead-band -> unchanged.
        if oi_change == 0.0 or abs(price_change) <= dead_band:
            return oi_score
        # Opposite signs -> invert (rising OI + falling price = bearish, etc.).
        if (oi_change > 0.0) != (price_change > 0.0):
            return -oi_score
        return oi_score

    async def _compute_recent_price_change_pct(
        self, symbol: str, lookback_bars: int,
    ) -> float:
        """Percent price change over the last ``lookback_bars`` 5-minute klines.

        Fix 2: supplies the SHORT-window price change that price-conditions the
        fresh short OI window (the matching half of the Fix-1 condition). Reads
        the local kline DB (no API call). Returns 0.0 on insufficient data so a
        cold start degrades to no-conditioning rather than a spurious read.
        """
        try:
            klines = await self._market_repo.get_klines(symbol, "5", lookback_bars + 1)
            if len(klines) < lookback_bars + 1:
                return 0.0
            now_close = float(klines[-1].close or 0.0)
            past_close = float(klines[-1 - lookback_bars].close or 0.0)
            if past_close <= 0:
                return 0.0
            return (now_close - past_close) / past_close * 100.0
        except Exception as e:
            log.debug(
                "SIG_PRICE_CHG_FAIL | sym={s} err='{err}'",
                s=symbol, err=str(e)[:80],
            )
            return 0.0

    @staticmethod
    def _cond_tag(s_raw: float, s_cond: float, usable: bool) -> str:
        """Annotation-only inversion tag for one OI window (Five-Fix Follow-Up
        Fix 2 / Phase 4 observability, operator-approved 2026-06-10).

        Derived purely from values the blend already computed — the score math
        is untouched. ``inv`` = the Fix-1 price-conditioning flipped this
        window's sign (magnitude preserved, so the flipped score is the exact
        negation); ``pass`` = conditioning left it unchanged; ``na`` = the
        window had no usable data this cycle.
        """
        if not usable:
            return "na"
        return "inv" if (s_raw != 0.0 and s_cond == -s_raw) else "pass"

    async def _blend_oi_windows(
        self,
        symbol: str,
        oi_change_24h: float,
        price_change_24h: float,
        oi_change_1h: float,
        oi_change_15m: float,
    ) -> tuple[float, dict]:
        """Blend the fresh short OI windows into the directional read (Fix 2;
        Five-Fix Follow-Up 2026-06-10 adds the 15m window and demotes 24h to
        context-only by default).

        The 15-minute and 1-hour windows are the DIRECTIONAL DRIVERS
        (operator-approved weights 0.4 / 0.6); the 24h window participates
        only if its weight is set above zero (default 0.0 — context-only:
        still computed, rendered and logged). Each window is normalized then
        PRICE-CONDITIONED (Fix 1) against its OWN matching price window
        BEFORE the blend, so both halves of the bullish-or-bearish condition
        describe the same move. Weights renormalize over the windows that
        have usable data; when NO driver window has data (cold start / flat
        OI), falls back to the 24h conditioned score at FULL strength —
        never damps the only real signal.

        Returns ``(blended_score, dbg)`` where ``dbg`` carries the
        per-window scores (None = unusable), matched price changes and the
        annotation-only ``cond_*`` inversion tags for SIG_OI_WINDOWS.
        """
        cfg = self._ms_cfg
        # 24h window — always computed (context render + cold-start fallback).
        s_24h_raw = clamp(float(oi_change_24h) / cfg.oi_normalize_pct, -1.0, 1.0)
        s_24h = self._condition_oi_score(
            s_24h_raw, float(oi_change_24h), float(price_change_24h),
        )
        w_15m = float(getattr(cfg, "oi_blend_weight_15m", 0.4))
        w_1h = float(getattr(cfg, "oi_blend_weight_short", 0.6))
        w_24h = float(getattr(cfg, "oi_blend_weight_long", 0.0))

        # 1h driver window.
        s_1h = None
        s_1h_raw = 0.0
        price_1h = 0.0
        if oi_change_1h != 0.0 and w_1h > 0.0:
            bars_1h = max(
                1,
                round(float(getattr(cfg, "oi_short_window_hours", 1.0)) * 60.0 / 5.0),
            )
            price_1h = await self._compute_recent_price_change_pct(symbol, bars_1h)
            s_1h_raw = clamp(float(oi_change_1h) / cfg.oi_normalize_pct, -1.0, 1.0)
            s_1h = self._condition_oi_score(
                s_1h_raw, float(oi_change_1h), float(price_1h),
            )

        # 15m driver window (Five-Fix Follow-Up Fix 2).
        s_15m = None
        s_15m_raw = 0.0
        price_15m = 0.0
        if oi_change_15m != 0.0 and w_15m > 0.0:
            bars_15m = max(
                1,
                round(float(getattr(cfg, "oi_15m_window_hours", 0.25)) * 60.0 / 5.0),
            )
            price_15m = await self._compute_recent_price_change_pct(symbol, bars_15m)
            s_15m_raw = clamp(float(oi_change_15m) / cfg.oi_normalize_pct, -1.0, 1.0)
            s_15m = self._condition_oi_score(
                s_15m_raw, float(oi_change_15m), float(price_15m),
            )

        # Weighted blend over the usable windows; cold-start ladder falls back
        # to the 24h conditioned score at full strength when no window blends.
        parts: list[tuple[float, float]] = []
        if s_15m is not None:
            parts.append((w_15m, s_15m))
        if s_1h is not None:
            parts.append((w_1h, s_1h))
        if w_24h > 0.0 and oi_change_24h != 0.0:
            parts.append((w_24h, s_24h))
        if parts:
            wsum = sum(w for w, _ in parts)
            blended = (
                sum(w * s for w, s in parts) / wsum if wsum > 0.0 else s_24h
            )
        else:
            blended = s_24h

        dbg = {
            "s_24h": s_24h,
            "s_1h": s_1h,
            "s_15m": s_15m,
            "price_1h": price_1h,
            "price_15m": price_15m,
            "cond_24h": self._cond_tag(
                s_24h_raw, s_24h, usable=(oi_change_24h != 0.0),
            ),
            "cond_1h": self._cond_tag(s_1h_raw, s_1h or 0.0, usable=s_1h is not None),
            "cond_15m": self._cond_tag(
                s_15m_raw, s_15m or 0.0, usable=s_15m is not None,
            ),
        }
        return clamp(blended, -1.0, 1.0), dbg

    def _evaluate_signal(
        self,
        fear_greed: int,
        funding_rate: float,
        oi_change: float,
        price_change: float = 0.0,
        oi_score: float | None = None,
        symbol: str = "",
    ) -> tuple[SignalType, str]:
        """Multi-source weighted classification of signal type.

        Phase 1 (output-quality fix). Replaces the pre-fix 9-rule cascade
        that used sentiment as a HARD gate (every BUY/SELL rule required
        ``abs(sentiment) > 0.2``). With sentiment=0.0 in 97.9% of coins
        (Reddit disabled + Finnhub free tier no altcoin coverage +
        ``aggregator.py:165`` zero-coverage rule), all signals fell
        through to NEUTRAL by design.

        Post-fix: compute a weighted ``direction_score`` across four
        components (sentiment, F&G contrarian, funding rate, OI change).
        Each component is "active" only if abs(score) >= its
        ``component_min_active`` threshold; INACTIVE components are
        DROPPED from the weighted sum (they don't pull toward NEUTRAL).
        Weights are renormalised over the active set so a coin with
        only F&G + funding active is classified on those alone.

        Component score conventions (all in [-1, +1]):
            fg_score        : (50 - fear_greed) / fg_normalize_range.
                              CONTRARIAN — F&G low → bullish (+1), F&G
                              high → bearish (-1).
            funding_score   : -funding_rate / funding_normalize.
                              INVERTED — high positive funding =
                              crowded longs = bearish.
            oi_score        : oi_change / oi_normalize_pct, clamped, then
                              PRICE-CONDITIONED (Fix 1): rising OI on a
                              falling price flips to bearish (shorts piling
                              in); rising OI on a rising price stays bullish
                              (longs accumulating). Magnitude preserved.

        Mapping:
            direction_score >= +strong_threshold → STRONG_BUY
            direction_score >= +buy_threshold    → BUY
            direction_score <= -strong_threshold → STRONG_SELL
            direction_score <= -buy_threshold    → SELL
            else                                 → NEUTRAL
            (no active components)               → NEUTRAL

        Args:
            fear_greed: Fear & Greed index in [0, 100].
            funding_rate: Current funding rate (e.g. 0.01 = 1%).
            oi_change: 24h open-interest change in percent. Used ONLY when
                ``oi_score`` is None (the back-compat single-window path);
                ignored when ``oi_score`` is provided (the production path).
            price_change: Same-window (24h) price change in percent, used to
                price-condition the OI score (Fix 1). Default 0.0 = no
                conditioning (back-compat for callers without a price source).
                Like ``oi_change``, only consulted when ``oi_score`` is None.
            oi_score: Pre-computed, blended, per-window price-conditioned OI
                direction score in [-1, +1] (Fix 2, from
                ``_blend_oi_windows`` — 15m/1h drivers, 24h context). When
                provided (production always provides it), it supersedes the
                ``oi_change``/``price_change`` single-window conditioning.
                Default None = back-compat path for tests/legacy callers.
            symbol: Trading pair (used for ``SIG_CLASSIFY`` log only).

        Returns:
            Tuple of (SignalType, reasoning string).
        """
        cfg = self._ms_cfg

        # 1. Compute three component scores in [-1, +1]. (Sentiment removed —
        #    Fix 3 2026-06-10 — so fg/funding/oi are the only inputs.)
        s_fg = clamp(
            (50.0 - float(fear_greed)) / cfg.fg_normalize_range, -1.0, 1.0,
        )
        s_funding = clamp(
            -float(funding_rate) / cfg.funding_normalize, -1.0, 1.0,
        )
        # Fix 2: production passes a pre-blended, already-price-conditioned OI
        # score (oi_score, from _blend_oi_windows). Tests / back-compat callers
        # pass only oi_change + price_change and the single-window Fix-1
        # conditioning runs here.
        if oi_score is not None:
            s_oi = clamp(float(oi_score), -1.0, 1.0)
        else:
            _s_oi_raw = clamp(float(oi_change) / cfg.oi_normalize_pct, -1.0, 1.0)
            s_oi = self._condition_oi_score(_s_oi_raw, float(oi_change), float(price_change))
            if _s_oi_raw != s_oi:
                # Fix 1 observability: the conditioning fired (sign flipped). A
                # rising-OI-on-falling-price buy is now correctly bearish.
                log.info(
                    f"SIG_OI_CONDITIONED | sym={symbol or '-'} "
                    f"oi_change={oi_change:+.2f} price_change={price_change:+.2f} "
                    f"raw_oi={_s_oi_raw:+.3f} conditioned_oi={s_oi:+.3f} | {ctx()}"
                )

        # 2. Mark each component active iff abs(score) >= its threshold.
        #    A score of 0 (or near-zero) means "no usable signal" and is
        #    excluded from the weighted sum — it does NOT pull toward
        #    NEUTRAL by occupying weight.
        # Fix 3 (sentiment removal, 2026-06-10): the sentiment component is gone
        # from the active/scores/weights sets entirely (it was already
        # force-inactive via the consumption gate). Direction is now built from
        # fg (when not neutral), funding, and OI alone.
        # Issue 1 (2026-06-08) — Fear-and-Greed direction neutrality. When
        # fg_direction_neutral is set, F&G is EXCLUDED from the direction set so
        # it contributes nothing to direction (the contrarian (50 - fg) term was
        # pinning the classifier to ~100% buy). F&G is still computed (s_fg) and
        # still flows to the confidence components, so it informs conviction but
        # not direction. This is neutrality, not a flip. When False, the prior
        # contrarian-buy participation is restored (off-switch).
        _fg_active = (
            (not getattr(cfg, "fg_direction_neutral", True))
            and abs(s_fg) >= cfg.fg_min_active
        )
        active = {
            "fg": _fg_active,
            "funding": abs(s_funding) >= cfg.funding_min_active,
            "oi": abs(s_oi) >= cfg.oi_min_active,
        }
        scores = {
            "fg": s_fg,
            "funding": s_funding,
            "oi": s_oi,
        }
        weights = {
            "fg": cfg.fg_weight,
            "funding": cfg.funding_weight,
            "oi": cfg.oi_weight,
        }

        # 3. Weighted sum over active components, renormalised.
        active_weight_sum = sum(
            weights[c] for c in active if active[c]
        )
        if active_weight_sum <= 0.0:
            direction_score = 0.0
            signal_type = SignalType.NEUTRAL
            reason = (
                f"Multi-source: no active components "
                f"(fg={s_fg:+.2f}, "
                f"fund={s_funding:+.2f}, oi={s_oi:+.2f})"
            )
        else:
            direction_score = sum(
                weights[c] * scores[c]
                for c in active if active[c]
            ) / active_weight_sum
            # Map to SignalType.
            if direction_score >= cfg.strong_threshold:
                signal_type = SignalType.STRONG_BUY
            elif direction_score >= cfg.buy_threshold:
                signal_type = SignalType.BUY
            elif direction_score <= -cfg.strong_threshold:
                signal_type = SignalType.STRONG_SELL
            elif direction_score <= -cfg.buy_threshold:
                signal_type = SignalType.SELL
            else:
                signal_type = SignalType.NEUTRAL
            active_labels = [c for c in active if active[c]]
            reason = (
                f"Multi-source dir={direction_score:+.3f} "
                f"active=[{','.join(active_labels)}] "
                f"(fg={s_fg:+.2f}, "
                f"fund={s_funding:+.2f}, oi={s_oi:+.2f})"
            )

        # 4. Per-coin observability — operators see exactly which
        #    components fired and why a signal classified as it did.
        #    Cheap (one log per coin per 5-min cycle).
        log.info(
            f"SIG_CLASSIFY | sym={symbol or '-'} "
            f"components=[fg:{s_fg:+.2f},"
            f"fund:{s_funding:+.2f},oi:{s_oi:+.2f}] "
            f"active=[fg:{active['fg']},"
            f"fund:{active['funding']},oi:{active['oi']}] "
            f"direction_score={direction_score:+.3f} "
            f"type={signal_type.value} | {ctx()}"
        )
        return signal_type, reason
