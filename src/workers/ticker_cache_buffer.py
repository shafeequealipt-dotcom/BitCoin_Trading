"""Ticker-cache write buffer (Issue 2 of cascade-fix series).

Background
----------
The pre-fix path scheduled one ``INSERT OR REPLACE INTO ticker_cache``
per WebSocket message via
``asyncio.run_coroutine_threadsafe(self.market_repo.save_ticker(...))``.
At 100-200 messages/sec each acquiring the global ``DatabaseManager``
``asyncio.Lock``, the writes queued behind themselves and behind any
other DB op holding the mutex.

Phase 0 baseline (2026-05-10) measured 35,290 ``DB_LOCK_WAIT`` events
in a 9-minute window with 99.7 % held by ``ticker_cache`` writes; max
wait was 63.6 seconds.

Design
------
``TickerCacheBuffer`` decouples WS writes from DB writes:

1. The WS callback (pybit thread) calls ``buffer.put(ticker)``. ``put``
   is synchronous, thread-safe via ``threading.Lock``, and updates an
   in-memory ``{symbol: latest_ticker}`` dict. **Latest-wins**: if the
   same symbol updates 50 times between flushes, only one row is
   written.
2. An async drainer task (started on the asyncio loop by
   ``buffer.start()``) wakes every ``flush_interval_ms`` (default 500),
   takes a snapshot of the dict, clears it, and writes the snapshot
   via ``MarketRepository.save_tickers_batch`` — a single
   ``executemany`` under the global lock.
3. Readers consult the buffer first via ``buffer.get(symbol)`` for
   sub-flush-interval freshness; on miss they fall back to the DB
   table (the existing path).

Result: DB writes drop from 100-200/sec to ≤ 2/sec (one per flush) at
the cost of up to ``flush_interval_ms`` of staleness for cross-process
readers. The in-process readers see fresher data than the DB had
before (the buffer holds data <500ms old; the DB previously had data
up to several seconds stale due to lock contention).

Crash recovery: buffer state is volatile, but the flush interval is
short (500 ms) so at most one flush of state is lost on crash. The
ticker_cache table was always a hot-replace cache (no historical
queries), so losing the last 500ms is acceptable — the next WS message
populates again within milliseconds.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import Ticker

if TYPE_CHECKING:
    from src.database.repositories.market_repo import MarketRepository

log = get_logger("worker")


# Default flush cadence — chosen so that:
#   - DB write rate ≤ 2/sec (vs 100-200/sec without buffering)
#   - reader staleness bounded at 500ms
#   - chunk size per flush stays reasonable (50 symbols × 1 flush =
#     50-row batch, well inside the kline batching pattern's 500-row
#     comfortable range)
_DEFAULT_FLUSH_INTERVAL_MS = 500

# Heartbeat cadence — emit one INFO line per N flushes summarizing
# total tickers written, average flush duration, max flush duration.
# 60 = ~30 s of operation at default cadence.
_HEARTBEAT_EVERY_N_FLUSHES = 60

# Issue 2.10 (2026-06-07): after this many CONSECUTIVE rejected ticks for a
# symbol, the new level is treated as a genuine sustained move (not a transient
# outlier) and accepted as the baseline — prevents a permanent stuck-rejection
# after a price gap (e.g. a reconnect that legitimately skipped a level).
_SPIKE_MAX_CONSECUTIVE = 3


class TickerCacheBuffer:
    """In-memory ticker write buffer with periodic batched flush.

    Args:
        repo: ``MarketRepository`` instance — the buffer drains into
            its ``save_tickers_batch`` method.
        flush_interval_ms: Drainer wake interval. Smaller = fresher DB
            but more flushes; larger = staler DB but fewer flushes.
            Default 500ms.

    Lifecycle:
        ``__init__`` creates state; ``start()`` launches the drainer
        task; ``stop()`` signals exit and awaits the drainer; ``put``
        is callable any time after ``__init__``; ``get`` is callable
        any time. ``put`` from a non-asyncio thread is safe; ``get``
        is also thread-safe.
    """

    def __init__(
        self,
        repo: MarketRepository,
        *,
        flush_interval_ms: int = _DEFAULT_FLUSH_INTERVAL_MS,
        spike_reject_pct: float = 0.0,
    ) -> None:
        if flush_interval_ms < 50:
            # Lower bound: anything below 50ms basically defeats the
            # batching purpose and saturates the DB lock with flushes.
            flush_interval_ms = 50
        self._repo = repo
        self._flush_interval_s = flush_interval_ms / 1000.0
        # Latest-wins per symbol. The dict itself is GIL-protected for
        # individual key updates (Python ≥3.7 dict is internally
        # thread-safe for atomic key writes), but the snapshot+clear
        # pair in flush() needs the explicit lock.
        self._pending: dict[str, Ticker] = {}
        self._lock = threading.Lock()
        self._drainer_task: asyncio.Task | None = None
        self._stop = False
        # Observability counters.
        self._flush_count: int = 0
        self._tickers_written: int = 0
        self._last_flush_at: float = time.monotonic()
        self._last_flush_duration_ms: float = 0.0
        self._max_flush_duration_ms: float = 0.0
        self._flush_err_count: int = 0
        # Issue 2.10: anomalous-tick rejection state. _last_price persists across
        # flushes (it is the accepted-price baseline, NOT the flushed pending
        # dict). 0.0 threshold = guard disabled (failure-safe default).
        self._spike_reject_pct: float = max(0.0, float(spike_reject_pct or 0.0))
        self._last_price: dict[str, float] = {}
        self._reject_streak: dict[str, int] = {}
        self._spike_reject_count: int = 0
        self._dropped_on_full_count: int = 0  # reserved — currently no
        # bound on _pending size. ticker_cache is keyed by symbol so
        # _pending size is bounded by the universe size (≤ 50).

    # ─── put / get ──────────────────────────────────────────────────

    def put(self, ticker: Ticker) -> None:
        """Latest-wins put. Safe to call from any thread.

        ``ticker.symbol`` is the dedup key — repeated puts for the same
        symbol overwrite each other so only the most-recent state
        flushes to disk. This is the entire reason the buffer reduces
        DB writes by 50-100x for a high-frequency WS stream.
        """
        with self._lock:
            # Issue 2.10 (2026-06-07): preventive anomalous-tick rejection. A
            # single tick whose price jumps more than spike_reject_pct from the
            # last accepted price is an outlier (bad print) and is HELD so it
            # cannot reach PnL/stop decisions. After _SPIKE_MAX_CONSECUTIVE
            # consecutive rejects the new level is a genuine sustained move and
            # is accepted as the baseline (never stuck-rejected after a gap).
            # 0.0 threshold disables the guard (legacy passthrough).
            if self._spike_reject_pct > 0.0:
                _sym = ticker.symbol
                _px = float(getattr(ticker, "last_price", 0.0) or 0.0)
                _last = self._last_price.get(_sym, 0.0)
                if _px > 0.0 and _last > 0.0:
                    _jump = abs(_px - _last) / _last
                    if _jump > self._spike_reject_pct:
                        _streak = self._reject_streak.get(_sym, 0) + 1
                        if _streak < _SPIKE_MAX_CONSECUTIVE:
                            self._reject_streak[_sym] = _streak
                            self._spike_reject_count += 1
                            log.warning(
                                f"PRICE_SPIKE_REJECT | sym={_sym} last={_last} "
                                f"new={_px} jump_pct={_jump * 100:.2f} "
                                f"thresh_pct={self._spike_reject_pct * 100:.1f} "
                                f"streak={_streak} | outlier tick held | {ctx()}"
                            )
                            return
                        log.warning(
                            f"PRICE_SPIKE_ACCEPT_AFTER_STREAK | sym={_sym} "
                            f"new={_px} prev={_last} streak={_streak} | sustained "
                            f"move accepted as new baseline | {ctx()}"
                        )
                if _px > 0.0:
                    self._reject_streak[_sym] = 0
                    self._last_price[_sym] = _px
            self._pending[ticker.symbol] = ticker

    def get(self, symbol: str) -> Ticker | None:
        """In-memory snapshot lookup. Returns the most-recent put for
        ``symbol`` that has not yet been flushed. Returns None if no
        such put exists (caller falls back to DB)."""
        with self._lock:
            return self._pending.get(symbol)

    def has_pending(self) -> bool:
        """Diagnostic helper — True if anything is awaiting flush."""
        with self._lock:
            return bool(self._pending)

    # ─── lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background drainer task. Idempotent."""
        if self._drainer_task is not None and not self._drainer_task.done():
            return
        self._stop = False
        self._drainer_task = asyncio.create_task(
            self._drainer(), name="ticker_cache_buffer_drainer",
        )
        log.info(
            # Issue 2.10 (2026-06-07) boot sentinel — confirm the anomalous-tick
            # rejection threshold loaded and whether the guard is active.
            f"TICKER_BUFFER_START | flush_interval_ms={self._flush_interval_s * 1000:.0f} "
            f"spike_reject_pct={self._spike_reject_pct:.4f} "
            f"spike_guard={'ON' if self._spike_reject_pct > 0.0 else 'OFF'} | {ctx()}"
        )

    async def stop(self) -> None:
        """Signal the drainer to exit, await it, then perform a final
        flush so no put is left orphaned."""
        self._stop = True
        if self._drainer_task is not None:
            try:
                # Give the drainer one extra interval to notice the
                # stop flag; if it doesn't, cancel.
                await asyncio.wait_for(
                    self._drainer_task,
                    timeout=self._flush_interval_s * 3,
                )
            except TimeoutError:
                self._drainer_task.cancel()
                try:
                    await self._drainer_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._drainer_task = None
        # Final flush — drain whatever remains.
        try:
            await self.flush()
        except Exception as e:
            log.warning(
                f"TICKER_BUFFER_FINAL_FLUSH_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )
        log.info(
            f"TICKER_BUFFER_STOP | total_flushes={self._flush_count} "
            f"total_written={self._tickers_written} | {ctx()}"
        )

    # ─── drainer ────────────────────────────────────────────────────

    async def _drainer(self) -> None:
        """Periodic flush loop. One flush per ``flush_interval``."""
        while not self._stop:
            try:
                await asyncio.sleep(self._flush_interval_s)
                await self.flush()
            except asyncio.CancelledError:
                # Cooperative cancel — exit the loop, let stop() do the
                # final flush.
                return
            except Exception as e:
                # Never let the drainer die on a single flush failure —
                # log loud (not silent) and continue.
                self._flush_err_count += 1
                log.warning(
                    f"TICKER_BUFFER_DRAIN_ERR | "
                    f"err='{str(e)[:120]}' "
                    f"cumulative_errs={self._flush_err_count} | {ctx()}"
                )

    async def flush(self) -> int:
        """Snapshot the pending dict and write it via
        ``MarketRepository.save_tickers_batch``.

        Returns:
            Number of tickers written. 0 if nothing was pending.
        """
        with self._lock:
            if not self._pending:
                return 0
            snapshot = list(self._pending.values())
            self._pending.clear()

        t0 = time.monotonic()
        try:
            await self._repo.save_tickers_batch(snapshot)
        except Exception:
            # On failure, the snapshot data is lost — but losing the
            # most recent 500ms of ticker_cache writes is acceptable
            # (the table is a hot-replace cache, not a historical
            # ledger). The next put repopulates within milliseconds.
            # Re-raise so the drainer logs the error.
            raise
        el_ms = (time.monotonic() - t0) * 1000.0

        # Update observability state.
        self._flush_count += 1
        self._tickers_written += len(snapshot)
        self._last_flush_at = time.monotonic()
        self._last_flush_duration_ms = el_ms
        if el_ms > self._max_flush_duration_ms:
            self._max_flush_duration_ms = el_ms

        if self._flush_count % _HEARTBEAT_EVERY_N_FLUSHES == 0:
            log.info(
                f"TICKER_BUFFER_HEARTBEAT | "
                f"flushes={self._flush_count} "
                f"written={self._tickers_written} "
                f"last_flush_n={len(snapshot)} "
                f"last_flush_ms={el_ms:.1f} "
                f"max_flush_ms={self._max_flush_duration_ms:.1f} "
                f"err_count={self._flush_err_count} | {ctx()}"
            )
        else:
            log.debug(
                f"TICKER_BATCH_FLUSH | n={len(snapshot)} "
                f"duration_ms={el_ms:.1f} | {ctx()}"
            )
        return len(snapshot)

    # ─── observability ──────────────────────────────────────────────

    def stats(self) -> dict:
        """Return a snapshot of internal counters for /metrics or
        ad-hoc inspection."""
        with self._lock:
            pending_n = len(self._pending)
        return {
            "pending_n": pending_n,
            "flush_count": self._flush_count,
            "tickers_written": self._tickers_written,
            "last_flush_at_mono": self._last_flush_at,
            "last_flush_duration_ms": self._last_flush_duration_ms,
            "max_flush_duration_ms": self._max_flush_duration_ms,
            "flush_err_count": self._flush_err_count,
            "flush_interval_ms": self._flush_interval_s * 1000.0,
            "spike_reject_count": self._spike_reject_count,
            "spike_reject_pct": self._spike_reject_pct,
        }
