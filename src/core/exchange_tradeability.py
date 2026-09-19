"""Exchange tradeability — which symbols the ACTIVE exchange can fill.

Incident (2026-09-09..19): 26 of 55 brain directives (47%) targeted symbols
the Shadow exchange could not fill. The trading bot ranks its universe by
volatility over all ~580 Bybit perpetuals; Shadow selects its coins once at
startup (top ~100 by 24h turnover) and only those have a price feed, so
every other symbol died at placement with "Symbol not tracked" — after
passing the R:R, volume, ATR, min-move, recent-loss and breaker gates. The
two symbol sets had no shared contract: nothing in the bot consulted
Shadow's list, and Shadow's own ``FORCE_INCLUDE`` (11 hand-typed symbols
"to keep the universes in sync") cannot follow a universe that churns ~13
coins per refresh.

This module is that contract's bot-side half: a small provider of "what can
the active exchange fill", used by the universe refresh (choose only
fillable coins) and by the execution path (skip early with a clear reason).

Design rules, all deliberate:

* **Fail OPEN.** ``None`` means "cannot say" — Shadow down, an older Shadow
  without ``/api/coins``, a mode where the concept does not apply. Callers
  must then behave exactly as before. A transient outage must never halt
  trading or empty the universe.
* **Mode-aware.** Only Shadow has a restricted symbol set. Live Bybit and
  Bybit-demo list everything, so those modes return ``None``.
* **Cached, stale-tolerant.** Shadow's list changes only when Shadow
  restarts, so a short TTL cache keeps the per-directive check off the
  network, and a failed refresh keeps serving the last good list instead of
  flipping to "unknown".
"""

from __future__ import annotations

import time
from typing import Any, Callable

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("exchange_tradeability")

DEFAULT_TTL_SECONDS = 300.0
# After a FAILED fetch, retry sooner than the TTL so recovery (Shadow coming
# back, or restarting with /api/coins) is noticed quickly — but still bounded,
# so an outage costs one attempt per interval, never one per directive.
DEFAULT_FAILURE_RETRY_SECONDS = 30.0


class ExchangeTradeability:
    """Answers "can the active exchange fill this symbol?".

    Args:
        shadow_order: The raw ``ShadowOrderService`` (NOT the Transformer's
            order proxy, which does not forward extra methods). ``None`` if
            the Shadow adapters could not be created.
        transformer: Provides ``is_shadow`` so the answer follows the live
            exchange mode. ``None`` is treated as "assume Shadow" (matches
            the direct-services fallback in ``WorkerManager``).
        ttl_seconds: How long a fetched list is served before re-fetching.
        failure_retry_seconds: Wait before retrying after a failed fetch.
        clock: Injectable monotonic clock (tests).
    """

    def __init__(
        self,
        shadow_order: Any,
        transformer: Any = None,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        failure_retry_seconds: float = DEFAULT_FAILURE_RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._shadow_order = shadow_order
        self._transformer = transformer
        self._ttl = float(ttl_seconds)
        self._failure_retry = float(failure_retry_seconds)
        self._clock = clock
        self._cached: set[str] | None = None
        # Monotonic time before which we will not hit the network again.
        # Tracked independently of ``_cached`` so that an outage with NO
        # history (nothing cached to serve) is still rate-limited.
        self._next_attempt_at: float = 0.0
        self._warned_unavailable = False

    def _applies(self) -> bool:
        """True when the active exchange is Shadow (the only restricted one)."""
        if self._shadow_order is None:
            return False
        if self._transformer is None:
            return True
        try:
            return bool(self._transformer.is_shadow)
        except Exception:
            # Cannot tell which exchange is active -> do not restrict.
            return False

    async def get_tradeable(self) -> set[str] | None:
        """The fillable symbol set, or ``None`` when unknown / not applicable."""
        if not self._applies():
            return None

        now = self._clock()
        if now < self._next_attempt_at:
            return self._cached

        fresh: set[str] | None = None
        try:
            fresh = await self._shadow_order.get_tradeable_symbols()
        except Exception as e:  # never let a lookup break trading
            log.warning(
                f"TRADEABILITY_FETCH_ERR | err_type={type(e).__name__} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )

        if fresh:
            if self._cached is None or fresh != self._cached:
                log.info(
                    f"TRADEABILITY_REFRESHED | n={len(fresh)} "
                    f"prev={len(self._cached) if self._cached else 0} | {ctx()}"
                )
            self._cached = fresh
            self._next_attempt_at = now + self._ttl
            self._warned_unavailable = False
            return self._cached

        # Refresh failed. Keep serving the last good list (Shadow's set only
        # changes on a Shadow restart); with no history, report "unknown".
        # Back off so an outage costs one attempt per retry interval, not one
        # per directive.
        self._next_attempt_at = now + self._failure_retry
        if not self._warned_unavailable:
            self._warned_unavailable = True
            log.warning(
                f"TRADEABILITY_UNAVAILABLE | serving="
                f"{'last_good' if self._cached else 'none_fail_open'} "
                f"n={len(self._cached) if self._cached else 0} | Shadow "
                f"/api/coins unreachable or absent (old Shadow?) | {ctx()}"
            )
        return self._cached

    async def is_tradeable(self, symbol: str) -> bool | None:
        """``True``/``False`` when known; ``None`` when unknown (fail open)."""
        tradeable = await self.get_tradeable()
        if tradeable is None:
            return None
        return symbol in tradeable
