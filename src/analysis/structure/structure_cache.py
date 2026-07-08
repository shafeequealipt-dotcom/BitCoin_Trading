"""X-RAY structural analysis cache — compute once, share everywhere.

Mirrors TACache pattern: monotonic TTL, dict-based, hit/miss stats.
Stores StructuralAnalysis per symbol, consumed by scorer, strategist,
SL/TP validator, and other downstream components.
"""

import time

from src.analysis.structure.models.structure_types import StructuralAnalysis
from src.core.logging import get_logger

log = get_logger("xray")

DEFAULT_TTL = 300.0  # 5 minutes — accommodates batched full-market scanning


class HigherTFStructureCache:
    """Issue #5 (2026-05-31): per-(symbol, timeframe) TTL cache of the cheap
    higher-timeframe structural views (TFStructureView for H4/D1) that feed the
    MTF confluence scorer. Mirrors the TACache/StructureCache monotonic-TTL
    pattern. TTL is passed per ``get`` so each timeframe can align to its kline
    fetch cooldown (H4 ~300s, D1 ~3600s) — this keeps the per-tick recompute
    cost bounded (D1 is recomputed ~once/hour, not every tick)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[float, object]] = {}

    def get(self, symbol: str, tf: str, ttl_s: float):
        """Return the cached view if younger than ``ttl_s``, else None."""
        cached = self._cache.get((symbol, tf))
        if cached:
            ts, view = cached
            if time.monotonic() - ts < ttl_s:
                return view
        return None

    def set(self, symbol: str, tf: str, view) -> None:
        self._cache[(symbol, tf)] = (time.monotonic(), view)

    def size(self) -> int:
        return len(self._cache)


class StructureCache:
    """TTL cache for X-RAY structural analysis results.

    Args:
        ttl_seconds: How long cached results remain valid.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, StructuralAnalysis]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, symbol: str) -> StructuralAnalysis | None:
        """Get cached analysis for a symbol if still fresh.

        Returns:
            StructuralAnalysis or None if expired/missing.
        """
        cached = self._cache.get(symbol)
        if cached:
            cache_time, result = cached
            if time.monotonic() - cache_time < self._ttl:
                self._hits += 1
                return result
        self._misses += 1
        return None

    def set(self, symbol: str, analysis: StructuralAnalysis) -> None:
        """Store analysis result with current timestamp."""
        self._cache[symbol] = (time.monotonic(), analysis)

    def get_all(self) -> dict[str, StructuralAnalysis]:
        """Return a snapshot copy of all non-expired entries keyed by symbol.

        Returns a new dict — safe to iterate while the cache is being updated.
        """
        now = time.monotonic()
        return dict(
            (sym, analysis)
            for sym, (ts, analysis) in self._cache.items()
            if now - ts < self._ttl
        )

    def get_top_setups(self, n: int = 8) -> list[StructuralAnalysis]:
        """Return the top N analyses, selected by setup_score, then ORDERED by
        score x confidence so a structureless (zero-confidence) coin cannot
        lead the brain's X-RAY shortlist.

        E19 (2026-05-28): the shortlist was ranked by setup_score alone, so a
        high-score zero-confidence coin could top the brain's X-RAY block even
        after #7 made the producer coherent and E17/E18 guarded APEX. Membership
        is still selected by setup_score (preserved — the same N coins), but the
        returned ORDER is by ``setup_score * setup_type_confidence`` so genuinely
        strong, confident structures lead and a zero-confidence coin (key 0)
        sorts to the bottom. Completes the #7/E17/E18 X-RAY confidence thread.

        Args:
            n: Maximum number of results.

        Returns:
            List of StructuralAnalysis: the top-N by score, ordered by
            score x confidence (descending).
        """
        fresh = self.get_all()
        # Membership: the top N by setup_score (unchanged — preserved).
        top_n = sorted(
            fresh.values(),
            key=lambda a: a.setup_score,
            reverse=True,
        )[:n]

        def _conviction(a: StructuralAnalysis) -> float:
            return float(a.setup_score) * max(
                float(getattr(a, "setup_type_confidence", 0.0) or 0.0), 0.0
            )

        # Order: by conviction = score x confidence (E19). A zero-confidence
        # coin gets key 0 and sorts last, so it can never lead the shortlist.
        reranked = sorted(top_n, key=_conviction, reverse=True)
        # Observability: only when the re-rank actually changes the lead coin —
        # i.e. E19 just demoted a structureless high-score coin from the top.
        if top_n and reranked and top_n[0].symbol != reranked[0].symbol:
            def _conf(a: StructuralAnalysis) -> float:
                # Same None-guard the conviction key uses, so the log line can
                # never raise on a duck-typed None confidence.
                return float(getattr(a, "setup_type_confidence", 0.0) or 0.0)
            log.info(
                f"E19_XRAY_RERANK | by_score={top_n[0].symbol}"
                f"(score={top_n[0].setup_score},conf={_conf(top_n[0]):.2f}) "
                f"by_conviction={reranked[0].symbol}"
                f"(score={reranked[0].setup_score},conf={_conf(reranked[0]):.2f})"
            )
        return reranked

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def size(self) -> int:
        """Return number of cached items (including expired)."""
        return len(self._cache)

    def invalidate(self, symbol: str | None = None) -> None:
        """Clear cache entries for a symbol, or all if None."""
        if symbol:
            self._cache.pop(symbol, None)
        else:
            self._cache.clear()

    def set_ranked_setups(self, setups: list, skip_list: list[str]) -> None:
        """Store scanner-ranked setups and skip list."""
        self._ranked_setups = setups
        self._skip_list = skip_list

    def get_ranked_setups(self) -> list:
        """Return scanner-ranked setups (or empty list)."""
        return getattr(self, "_ranked_setups", [])

    def get_skip_list(self) -> list[str]:
        """Return scanner skip list (or empty list)."""
        return getattr(self, "_skip_list", [])

    def get_stats(self) -> dict:
        """Return cache hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1), 2),
            "cached_entries": len(self._cache),
        }

    def get_oldest_entry_age_seconds(self) -> float:
        """Return the age (in seconds) of the oldest entry, or 0.0 if empty.

        Used by ``structure_worker``'s ``XRAY_CACHE_HEALTH`` diagnostic so
        operators can detect batch-cursor stalls (a healthy worker
        re-analyzes every coin within ``ttl_seconds``; a stalled one
        leaves an entry untouched indefinitely).
        """
        if not self._cache:
            return 0.0
        now = time.monotonic()
        return now - min(ts for ts, _ in self._cache.values())

    def get_freshness_breakdown(
        self, fresh_within_seconds: float | None = None,
    ) -> dict[str, int]:
        """Return per-bucket counts of cached entries by age.

        Definitive-fix Phase 1 — surfaces "how many of the universe are
        actually within the freshness window the scanner expects?" so
        operators don't have to infer it from ``oldest_age_s`` alone.

        Args:
            fresh_within_seconds: The "fresh" threshold in seconds.
                Defaults to ``self._ttl`` so it tracks the cache's own
                expiry contract. Pass a smaller value (e.g. 300 when
                TTL is 600) to count strictly-fresh entries.

        Returns:
            Dict with keys:
              ``total``  — total entries in the cache (incl. expired)
              ``fresh``  — entries with age < ``fresh_within_seconds``
              ``stale``  — entries with age >= ``fresh_within_seconds``
        """
        threshold = float(
            fresh_within_seconds if fresh_within_seconds is not None
            else self._ttl
        )
        now = time.monotonic()
        fresh = 0
        stale = 0
        for ts, _ in self._cache.values():
            if (now - ts) < threshold:
                fresh += 1
            else:
                stale += 1
        return {
            "total": fresh + stale,
            "fresh": fresh,
            "stale": stale,
        }
