"""Multi-source sentiment aggregator: combines news, Reddit, and alt data."""

import time

from src.core.decorators import timed
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import SentimentLevel
from src.core.utils import clamp, now_utc, safe_divide
from src.database.connection import DatabaseManager
from src.database.repositories.altdata_repo import AltDataRepository
from src.database.repositories.news_repo import NewsRepository
from src.database.repositories.sentiment_repo import SentimentRepository
from src.intelligence.sentiment.scorer import SentimentScorer

log = get_logger("intelligence")

# Aggregation weights
WEIGHT_NEWS = 0.35
WEIGHT_REDDIT = 0.30
WEIGHT_FEAR_GREED = 0.20
WEIGHT_MOMENTUM = 0.15

# Phase 6 (session-stability): zero-coverage cache.
#
# The observability window showed 596/644 aggregations returning
# news_n=0 reddit_n=0 — one DB round-trip every 2 s for symbols with
# no meaningful news/reddit backing. Cache those "unknown" verdicts
# with a 30-min TTL so the next 359 re-queries for the same symbol
# return instantly. Small bounded dict per SentimentAggregator instance.
_ZERO_COVERAGE_TTL_SECONDS: float = 30 * 60  # 30 minutes


class SentimentAggregator:
    """Combines sentiment from multiple sources into a unified score per symbol.

    Args:
        db: Database manager.
        scorer: Sentiment scorer for level mapping.
        settings: Optional Settings reference. Phase 10 (post-Layer-1
            fix) reads ``settings.reddit.client_id`` to detect whether
            Reddit is intentionally disabled — when so, suppresses the
            per-coin ``SENT_UNKNOWN`` log spam (~192 events / 30 min
            against an empty Reddit credential set was the dominant log
            tag in production) and emits a single
            ``SENTIMENT_DEGRADED_MODE`` line at init instead.
    """

    def __init__(
        self,
        db: DatabaseManager,
        scorer: SentimentScorer,
        settings: "object | None" = None,
    ) -> None:
        self._db = db
        self._scorer = scorer
        self._news_repo = NewsRepository(db)
        self._sentiment_repo = SentimentRepository(db)
        self._altdata_repo = AltDataRepository(db)
        # Phase 6: per-symbol zero-coverage cache. Key → (expires_at, result_dict).
        self._unknown_cache: dict[str, tuple[float, dict]] = {}

        # Phase 10 (post-Layer-1 fix). Detect intentionally-disabled
        # Reddit so we can suppress the per-coin SENT_UNKNOWN spam. The
        # aggregator runs ~50 coins per cycle * 12 cycles per hour =
        # 600+ per-coin events per hour against an empty Reddit input
        # set when reddit is intentionally off — overwhelming
        # legitimate "no data this cycle for this coin" signals.
        self._reddit_intentionally_disabled = False
        # CALL_B Framing Fix Phase 5B (2026-05-06) — read the operator's
        # consumption_enabled flag. When False, the per-coin
        # SENT_DEGRADED_MODE INFO log is suppressed (the once-at-init
        # WARNING below stays). This is purely a log-noise reduction;
        # the cache and aggregation logic are untouched.
        self._consumption_enabled: bool = True
        if settings is None:
            self._reddit_intentionally_disabled = False
        else:
            try:
                reddit_cfg = getattr(settings, "reddit", None)
                client_id = getattr(reddit_cfg, "client_id", None) if reddit_cfg else None
                if not client_id:
                    self._reddit_intentionally_disabled = True
                    log.warning(
                        f"SENTIMENT_DEGRADED_MODE | reason=no_reddit "
                        f"source=fear_greed_only | {ctx()}"
                    )
                sent_cfg = getattr(settings, "sentiment", None)
                if sent_cfg is not None:
                    self._consumption_enabled = bool(
                        getattr(sent_cfg, "consumption_enabled", True)
                    )
                    if not self._consumption_enabled:
                        log.warning(
                            f"SENTIMENT_DEGRADED_MODE | reason=consumption_disabled "
                            f"source=operator_decision_2026-05-06 "
                            f"effect=per_coin_log_suppressed | {ctx()}"
                        )
            except Exception:
                # Defensive: never let settings introspection break the
                # aggregator init. Fall back to the verbose path.
                self._reddit_intentionally_disabled = False
                self._consumption_enabled = True

    @timed
    async def aggregate_for_symbol(self, symbol: str, hours: int = 24) -> dict:
        """Compute aggregated sentiment for a symbol from all sources.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            hours: Time window for analysis.

        Returns:
            Dict with overall_score, level, component scores, and details.
        """
        # Phase 6: zero-coverage cache short-circuit. If we've confirmed
        # within the TTL window that this symbol has no news and no reddit
        # data, return the cached "unknown" verdict without touching the
        # DB. Trimmed 596 of 644 lookups in the observability window.
        _cached = self._unknown_cache.get(symbol)
        if _cached is not None:
            _expires_at, _cached_result = _cached
            if _expires_at > time.monotonic():
                log.info(
                    f"SENT_UNKNOWN_CACHE_HIT | sym={symbol} | {ctx()}"
                )
                return dict(_cached_result)
            # Expired — drop it.
            self._unknown_cache.pop(symbol, None)

        # News sentiment
        news_articles = await self._news_repo.get_by_symbol(symbol, hours=hours)
        news_scores = [a.sentiment_score for a in news_articles]
        news_avg = sum(news_scores) / len(news_scores) if news_scores else 0.0

        # Reddit sentiment
        reddit_posts = await self._sentiment_repo.get_posts_by_symbol(symbol, hours=hours)
        reddit_scores = [p.sentiment_score for p in reddit_posts]
        reddit_avg = sum(reddit_scores) / len(reddit_scores) if reddit_scores else 0.0

        # Fear & Greed (normalized to -1..1 scale)
        fg = await self._altdata_repo.get_latest_fear_greed()
        fg_value = fg.value if fg else 50
        fg_normalized = (fg_value - 50) / 50.0  # 0-100 -> -1..1

        # Momentum: compare recent vs older sentiment
        recent_sentiment = await self._sentiment_repo.get_sentiment_for_symbol(symbol, limit=1)
        prev_score = recent_sentiment[0]["overall_score"] if recent_sentiment else 0.0
        current_avg = (news_avg + reddit_avg) / 2 if (news_scores or reddit_scores) else 0.0
        momentum = clamp(current_avg - prev_score, -1.0, 1.0)

        # Weighted aggregation — amplify F&G when extreme
        fg_weight = WEIGHT_FEAR_GREED
        if fg_value < 20 or fg_value > 80:
            fg_weight = 0.60  # Extreme F&G dominates the signal
        elif fg_value < 30 or fg_value > 70:
            fg_weight = 0.40

        # Rebalance weights to sum to ~1.0
        other_sum = WEIGHT_NEWS + WEIGHT_REDDIT + WEIGHT_MOMENTUM
        scale = (1.0 - fg_weight) / other_sum if other_sum > 0 else 0

        overall = (
            news_avg * WEIGHT_NEWS * scale
            + reddit_avg * WEIGHT_REDDIT * scale
            + fg_normalized * fg_weight
            + momentum * WEIGHT_MOMENTUM * scale
        )
        overall = clamp(overall, -1.0, 1.0)

        # F&G extremes: soft influence, NOT hard override
        # Phase 15 (P1-14): when the coin has zero news AND zero reddit
        # data, the only signal left is F&G (market-wide) + price
        # momentum (24h % change). The previous branch combined those
        # into an `overall` score that landed at 0.568 → "very_bullish"
        # for symbols with ZERO sentiment evidence — Claude was being
        # told to chase coins on momentum alone with no qualitative
        # backing. Per brief P1-14 the correct semantics is `neutral`
        # (or `unknown`) when the coin has no data. We log the F&G + 24h
        # change at INFO so the no-data state is diagnosable.
        has_own_data = len(news_scores) > 0 or len(reddit_scores) > 0
        if not has_own_data:
            overall = 0.0
            coin_24h = None
            try:
                ticker_row = await self._db.fetch_one(
                    "SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?",
                    (symbol,),
                )
                if ticker_row:
                    coin_24h = ticker_row.get("change_24h_pct")
            except Exception as e:
                log.warning(f"Suppressed: {e} (ticker_cache momentum lookup)")
            # Phase 6 (session-stability): explicitly mark this as UNKNOWN
            # (no qualitative data) rather than NEUTRAL so downstream
            # consumers (APEX, brain prompt) can distinguish "mixed news
            # averaged to zero" from "we have no news at all". The existing
            # SENT_NEUTRAL log is retained because downstream analysis
            # scripts grep for it; a dedicated SENT_UNKNOWN line lands
            # alongside so ops can split the two cases.
            #
            # Phase 10 (post-Layer-1 fix). When Reddit is INTENTIONALLY
            # disabled (no client_id in settings), the per-coin
            # SENT_UNKNOWN line is config noise — the operator already
            # knows reddit is off and the SENTIMENT_DEGRADED_MODE line
            # at init said so. Suppress per-coin in that case; the
            # per-coin line still fires when reddit IS configured but
            # transiently has no rows (genuine data gap = different
            # signal). SENT_NEUTRAL is preserved for downstream parsers.
            log.info(
                f"SENT_NEUTRAL | sym={symbol} rsn=no_news_no_reddit "
                f"fg={fg_value} change_24h={coin_24h} | {ctx()}"
            )
            # Phase 7 (output-quality, user Q4 = "categorical reason
            # refinement, no fallback"). Replace the single SENT_UNKNOWN
            # tag with categorical reasons so operators can grep one
            # tag per cause:
            #   SENT_DEGRADED_MODE — Reddit disabled by config (the
            #     dominant case today; ~98% of zero-coverage events).
            #   SENT_NO_DATA       — Reddit configured but no data found
            #     for this symbol (genuine pipeline gap).
            # The existing SENT_UNKNOWN tag is preserved as an alias for
            # back-compat with downstream parsers/dashboards. Behaviour
            # unchanged — `overall_score=0.0`, `level=UNKNOWN`.
            if self._reddit_intentionally_disabled:
                # CALL_B Framing Fix Phase 5B (2026-05-06) — suppress
                # the per-coin SENT_DEGRADED_MODE INFO when consumption
                # is also disabled. The init-time WARNING already
                # surfaces the degraded-mode state once per restart.
                if getattr(self, "_consumption_enabled", True):
                    log.info(
                        f"SENT_DEGRADED_MODE | sym={symbol} "
                        f"reason=reddit_disabled fg={fg_value} "
                        f"change_24h={coin_24h} | {ctx()}"
                    )
            else:
                log.info(
                    f"SENT_NO_DATA | sym={symbol} matched_articles=0 "
                    f"matched_reddit=0 fg={fg_value} "
                    f"change_24h={coin_24h} | {ctx()}"
                )
                # Back-compat alias.
                log.info(
                    f"SENT_UNKNOWN | sym={symbol} rsn=no_news_no_reddit "
                    f"fg={fg_value} change_24h={coin_24h} | {ctx()}"
                )
        elif fg_value < 15:
            # Extreme fear: blend, don't override
            fg_pull = -0.3
            overall = overall * 0.6 + fg_pull * 0.4
            log.info(
                "Sentiment F&G influence: F&G={fg} -> blended to {score:.2f} (not overridden)",
                fg=fg_value, score=overall,
            )
        elif fg_value > 85:
            fg_pull = 0.3
            overall = overall * 0.6 + fg_pull * 0.4
            log.info(
                "Sentiment F&G influence: F&G={fg} -> blended to {score:.2f} (not overridden)",
                fg=fg_value, score=overall,
            )

        # Phase 6: emit SentimentLevel.UNKNOWN when the symbol has no
        # qualitative signal at all — neither news nor reddit. F&G alone
        # is market-wide, not coin-specific, so it's insufficient to
        # justify NEUTRAL. Consumers that want to weight by data quality
        # can now treat UNKNOWN as a cue to skip / downweight.
        if not has_own_data:
            level = SentimentLevel.UNKNOWN
        else:
            level = self._scorer.score_to_level(overall)

        result = {
            "symbol": symbol,
            "overall_score": round(overall, 4),
            "level": level.value,
            "news_score": round(news_avg, 4),
            "news_count": len(news_articles),
            "reddit_score": round(reddit_avg, 4),
            "reddit_count": len(reddit_posts),
            "fear_greed_value": fg_value,
            "fear_greed_classification": fg.classification if fg else "N/A",
            "momentum": round(momentum, 4),
            "components_detail": {
                "news_weight": WEIGHT_NEWS,
                "reddit_weight": WEIGHT_REDDIT,
                "fear_greed_weight": WEIGHT_FEAR_GREED,
                "momentum_weight": WEIGHT_MOMENTUM,
            },
        }

        # Persist
        await self._sentiment_repo.save_aggregated_sentiment(result)

        log.info(f"SENT_AGG | sym={symbol} score={overall:.3f} level={level.value} news_n={len(news_articles)} reddit_n={len(reddit_posts)} fg={fg_value if 'fg_value' in dir() else '-'} | {ctx()}")

        # Populate zero-coverage cache so the next 30 min of re-queries
        # skip the DB round-trip. Only cache the "no data at all" case —
        # a mixed-news NEUTRAL should continue to re-aggregate in case
        # fresh news lands mid-window.
        if not has_own_data:
            self._unknown_cache[symbol] = (
                time.monotonic() + _ZERO_COVERAGE_TTL_SECONDS,
                dict(result),
            )

        return result

    @timed
    async def aggregate_all_symbols(
        self,
        symbols: list[str] | None = None,
        hours: int = 24,
    ) -> list[dict]:
        """Aggregate sentiment for all symbols.

        Args:
            symbols: List of trading pairs. Defaults to stored config.
            hours: Time window.

        Returns:
            List of aggregation result dicts.
        """
        if symbols is None:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

        results = []
        for symbol in symbols:
            try:
                result = await self.aggregate_for_symbol(symbol, hours)
                results.append(result)
            except Exception as e:
                log.warning("Failed to aggregate for {s}: {err}", s=symbol, err=str(e))

        return results

    @timed
    async def get_sentiment_shift(self, symbol: str, hours: int = 6) -> dict:
        """Compare current vs previous sentiment.

        Args:
            symbol: Trading pair.
            hours: How far back to compare.

        Returns:
            Dict with current_score, previous_score, shift, direction.
        """
        history = await self._sentiment_repo.get_sentiment_history(symbol, hours=hours)

        if len(history) < 2:
            current = history[0]["overall_score"] if history else 0.0
            return {
                "symbol": symbol,
                "current_score": current,
                "previous_score": 0.0,
                "shift": 0.0,
                "direction": "stable",
            }

        current = history[-1]["overall_score"]
        previous = history[0]["overall_score"]
        shift = current - previous

        if shift > 0.1:
            direction = "improving"
        elif shift < -0.1:
            direction = "worsening"
        else:
            direction = "stable"

        return {
            "symbol": symbol,
            "current_score": round(current, 4),
            "previous_score": round(previous, 4),
            "shift": round(shift, 4),
            "direction": direction,
        }

    @timed
    async def get_market_mood(self) -> dict:
        """Get overall crypto market mood.

        Returns:
            Dict with overall mood, Fear & Greed, avg sentiment, and top symbols.
        """
        all_results = await self.aggregate_all_symbols()
        fg = await self._altdata_repo.get_latest_fear_greed()

        if not all_results:
            return {
                "overall_mood": SentimentLevel.NEUTRAL.value,
                "fear_greed": fg.value if fg else 50,
                "avg_sentiment": 0.0,
                "most_bullish_symbol": None,
                "most_bearish_symbol": None,
                "total_news_count": 0,
                "total_reddit_posts": 0,
            }

        scores = [r["overall_score"] for r in all_results]
        avg = sum(scores) / len(scores)

        sorted_bull = sorted(all_results, key=lambda r: r["overall_score"], reverse=True)
        sorted_bear = sorted(all_results, key=lambda r: r["overall_score"])

        total_news = sum(r.get("news_count", 0) for r in all_results)
        total_reddit = sum(r.get("reddit_count", 0) for r in all_results)

        return {
            "overall_mood": self._scorer.score_to_level(avg).value,
            "fear_greed": fg.value if fg else 50,
            "avg_sentiment": round(avg, 4),
            "most_bullish_symbol": sorted_bull[0]["symbol"] if sorted_bull else None,
            "most_bearish_symbol": sorted_bear[0]["symbol"] if sorted_bear else None,
            "total_news_count": total_news,
            "total_reddit_posts": total_reddit,
        }
