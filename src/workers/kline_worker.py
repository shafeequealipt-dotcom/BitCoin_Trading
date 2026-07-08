"""Kline worker: fetches historical candlestick data via Bybit REST API.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on the FULL ``config.universe.watch_list`` (50 coins), not the
  30-coin active_universe filter. Workers maintain warm data for the broad
  pool; the cycle's 30-coin focus is selected by ScannerWorker.
- Fires at the configured sweet spot (default 0:30 within each 5-min window)
  via ``SweetSpotWorker``, replacing the fixed 45-s polling cadence. The
  sweet spot lands ~30 s after each M5 candle close so writes are final
  before downstream workers read at 0:45+.
"""

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame, WorkerTier
from src.database.connection import DatabaseManager
from src.trading.services.market_service import MarketService
from src.workers.base_worker import SweetSpotWorker

log = get_logger("worker")

# Optimized timeframe tiers: (timeframe, min_seconds_between_fetches)
# Under sweet-spot scheduling we fire once per 5-min window. M5 finalizes
# every 5 min so a per-tick fetch is exactly right; H1/H4/D1 cooldowns
# preserve the longer cadence so we don't over-fetch.
TIMEFRAME_SCHEDULE = {
    TimeFrame.M5: 60,
    TimeFrame.H1: 60,
    TimeFrame.H4: 300,
    TimeFrame.D1: 3600,
}

# Phase 2 (corrected-Layer-1): post-tick freshness watchdog threshold.
# After fetching, any of the 50 watch_list symbols whose newest M5 kline
# in trading.db is older than this is logged with KLINE_FRESHNESS_WARN.
# 600 s = 2 missed M5 closes, the operator-tunable signal that a coin's
# data is degrading even though the worker itself is healthy.
_KLINE_FRESHNESS_THRESHOLD_S = 600.0

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999. Cap the IN-clause
# to 500 symbols (the timeframe placeholder leaves 499 slots — well above
# the 50-symbol watch_list). Larger universes naturally sub-sample.
# Used by both KLINE_WRITE_LAG and KLINE_FRESHNESS_WARN scans below.
_LAG_QUERY_MAX_SYMBOLS = 500

# Issue I4 (F-27 DB lock cascade, 2026-05-14) — chunk size for the
# kline staleness scan. The pre-I4 scan ran one fetch_all with up to
# 500 symbols in the IN clause; that single query was the audited
# 22:35:48 cascade's 13.9s holder. 100 symbols per chunk keeps each
# per-batch DB-lock hold well under the 1000 ms DB_LOCK_WAIT_WARN
# threshold while still amortising round-trips.
_STALENESS_SCAN_CHUNK: int = 100


class KlineWorker(SweetSpotWorker):
    """Fetches klines for the full watch_list on a sweet-spot schedule.

    Reads ``config.universe.watch_list`` (50 coins) every tick. Fires at
    ``settings.workers.sweet_spots.kline_worker`` (default ``"0:30"``) within
    every ``settings.workers.sweet_spots.window_minutes`` window (default 5).

    Args:
        settings: Application settings.
        db: Database manager.
        market_service: MarketService for fetching klines.
        scanner: Retained as an optional injection for backward-compatibility
            with code that still references ``kline_worker._scanner``. The
            worker no longer reads from it; kept None-safe so callers don't
            crash. Slated for removal in Phase 7.
    """

    # Layer 1 restructure — sub-layer assignment via the canonical
    # WorkerTier enum. BaseWorker derives the ``LAYER1A`` log tag and
    # the ``layer1a`` cycle-tracker key from this single source.
    worker_tier = WorkerTier.LAYER1A

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        market_service: MarketService, scanner=None,
    ) -> None:
        super().__init__(
            name="kline_worker",
            sweet_spot=settings.workers.sweet_spots.kline_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.market_service = market_service
        self._scanner = scanner  # legacy injection; not read by tick()
        # Tracked symbols come from settings.universe.watch_list each tick.
        # Initial seed avoids a one-tick warmup gap on first call.
        self._tracked_symbols: list[str] = list(settings.universe.watch_list)
        self._last_fetch: dict[str, float] = {}
        # Phase 6 (P0-5): per-symbol fetch counts for the most recent tick.
        # When the aggregate falls short of expectation, this lets us name
        # the offending symbol(s) in KLINE_GAP rather than report a single
        # opaque shortfall number.
        self._last_tick_per_symbol: dict[str, int] = {}
        # Phase 6 (P0-5): on a CRITICAL fetch (zero klines fetched), open
        # a 30 s circuit breaker. Strategy worker reads this monotonic
        # deadline at tick start and skips its cycle so it never runs TA
        # on klines that didn't refresh.
        self._circuit_breaker_until: float = 0.0
        # Phase 3 (post-Layer-1 fix): per-symbol consecutive-failure
        # counter. Used to escalate to ``KLINE_STRAGGLER`` when a single
        # coin is failing repeatedly while the universe overall is
        # healthy. Reset on first success.
        self._consecutive_fails: dict[str, int] = {}
        self._fail_streak_started: dict[str, float] = {}
        self._STRAGGLER_THRESHOLD = 3
        # Phase 1 (D-3 fix): WAL checkpoint scheduler state. The
        # ``wal_autocheckpoint=2000`` pragma is opportunistic and only
        # fires when no readers hold a snapshot; under continuous
        # multi-worker load that condition is rare and the -wal file
        # was observed pinned at the 100 MiB ``journal_size_limit``
        # cap. Triggering ``PRAGMA wal_checkpoint(PASSIVE)`` every
        # ``wal_checkpoint_every_n_kline_ticks`` ticks gives SQLite an
        # explicit truncation opportunity. Three or more consecutive
        # PASSIVE checkpoints reporting ``busy != 0`` escalates the
        # next call to TRUNCATE which briefly blocks writers but
        # reclaims WAL space when readers persistently pin frames.
        self._tick_count: int = 0
        self._consecutive_busy_checkpoints: int = 0

    @staticmethod
    def _classify_fetch_quality(total: int, expected: int) -> tuple[str, str]:
        """Map fetch-vs-expected into a log level + reason code.

        Per brief P0-5 Fix A:
            total == expected      -> ("INFO", "ok")
            total < expected*0.9   -> ("WARNING", "short_10pct")
            total < expected*0.5   -> ("ERROR", "short_50pct")
            total == 0             -> ("CRITICAL", "zero_fetch")
        Order matters: zero is the most-specific case so it is checked
        BEFORE the percentage thresholds.
        """
        if expected <= 0:
            return ("INFO", "ok")
        if total == 0:
            return ("CRITICAL", "zero_fetch")
        ratio = total / expected
        if ratio < 0.5:
            return ("ERROR", "short_50pct")
        if ratio < 0.9:
            return ("WARNING", "short_10pct")
        return ("INFO", "ok")

    def is_circuit_open(self) -> bool:
        """Public helper used by strategy_worker to gate TA on a fetch collapse."""
        return time.monotonic() < self._circuit_breaker_until

    async def tick(self) -> None:
        """Fetch klines for the full watch_list on the configured sweet spot.

        Universe handling (corrected Layer 1, HR-1 / HR-5 / HR-6): the
        single source of truth is ``config.universe.watch_list`` (50 coins).
        The scanner is no longer consulted here — that decoupling is the
        whole point of the corrected architecture. UniverseSettings.__post_init__
        enforces ``len(watch_list) >= 10`` and rejects malformed entries at
        startup, so an empty/invalid watch_list causes ConfigError before
        workers ever start (no runtime guard needed here).
        """
        universe = list(self.settings.universe.watch_list)
        # Defensive: should never be empty given UniverseSettings validation,
        # but log + skip rather than divide-by-zero downstream.
        if not universe:
            log.warning(
                f"KLINE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return
        self._tracked_symbols = universe

        now = time.time()
        # Phase 3 (post-Layer-1 fix): tick wall-clock for the el= field.
        t0_mono = time.monotonic()
        total_fetched = 0
        errors_this_tick = 0
        skipped_cooldown = 0
        # Phase 2 (corrected-Layer-1): per-timeframe split for the new
        # KLINE_TICK_SUMMARY line, so operators can see which timeframes
        # ran on a given tick (e.g. expect M5+H1 every tick, H4 only at
        # window-aligned 5-min boundaries, D1 only hourly).
        tf_fetched: dict[str, int] = {tf.value: 0 for tf in TIMEFRAME_SCHEDULE}
        per_symbol_fetched: dict[str, int] = {s: 0 for s in self._tracked_symbols}
        per_symbol_expected: dict[str, int] = {s: 0 for s in self._tracked_symbols}

        for symbol in self._tracked_symbols:
            for timeframe, min_interval in TIMEFRAME_SCHEDULE.items():
                cache_key = f"{symbol}:{timeframe.value}"
                last = self._last_fetch.get(cache_key, 0)

                if now - last < min_interval:
                    skipped_cooldown += 1
                    continue

                # Each scheduled (symbol, tf) fetch contributes 200 klines to
                # the expected count. Tracking per-symbol expected makes
                # the per-symbol gap report mathematically honest.
                per_symbol_expected[symbol] += 200

                try:
                    klines = await self.market_service.get_klines(
                        symbol, timeframe, limit=200,
                    )
                    n = len(klines)
                    total_fetched += n
                    tf_fetched[timeframe.value] = tf_fetched.get(timeframe.value, 0) + n
                    per_symbol_fetched[symbol] = per_symbol_fetched.get(symbol, 0) + n
                    self._last_fetch[cache_key] = now
                    # Phase 6 (output-quality): record kline-cache write
                    # timestamp so downstream workers can compute end-to-end
                    # freshness. Cache key = "klines:<symbol>:<timeframe>".
                    if n > 0:
                        try:
                            from src.core.cache_freshness import record_write
                            record_write("klines", f"{symbol}:{timeframe.value}")
                        except Exception:  # pragma: no cover — defensive
                            pass
                    # Phase 3 (post-Layer-1 fix): a successful fetch resets
                    # the per-symbol failure streak. A symbol with N>0
                    # successful klines on any timeframe in this tick is
                    # not a straggler.
                    if n > 0 and symbol in self._consecutive_fails:
                        del self._consecutive_fails[symbol]
                        self._fail_streak_started.pop(symbol, None)
                    # Phase 2 (post-Layer-1 fix): the prior ``await asyncio.sleep(0.1)``
                    # here was the dominant source of the chronic 12-20 s tick
                    # latency. With ~30 symbols × up to 4 timeframes per tick,
                    # the 0.1 s sleeps summed to ~12 s of pure idle time on
                    # peak ticks. Bybit-side rate limiting is already enforced
                    # by ``BybitClient.call`` via
                    # ``@rate_limit(calls_per_second=10.0)`` (see
                    # ``src/trading/client.py:146``), so this artificial
                    # throttle was redundant. ``sleep(0)`` still yields the
                    # event loop on each iteration without idle-spinning.
                    await asyncio.sleep(0)
                except Exception as e:
                    # Phase 3 (post-Layer-1 fix): per-symbol failure log
                    # promoted from DEBUG to WARNING and structured for
                    # grep-ability. Pre-fix, KLINE_WRITE_LAG showed which
                    # symbols were stale but not why — the failures were
                    # silent at the default INFO level.
                    errors_this_tick += 1
                    log.warning(
                        f"KLINE_FETCH_FAIL | sym={symbol} tf={timeframe.value} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )

        # Snapshot for the next tick / external readers.
        self._last_tick_per_symbol = dict(per_symbol_fetched)

        # Phase 3 (post-Layer-1 fix): track per-symbol consecutive fails.
        # A symbol that expected klines this tick but got zero from any
        # timeframe is counted as a fail. After ``_STRAGGLER_THRESHOLD``
        # consecutive fails, emit ``KLINE_STRAGGLER`` so operators can
        # identify persistently broken coins (universe-pruning candidates).
        for sym, exp in per_symbol_expected.items():
            if exp <= 0:
                continue
            got = per_symbol_fetched.get(sym, 0)
            if got > 0:
                # Reset handled at success site already; defensive cleanup.
                self._consecutive_fails.pop(sym, None)
                self._fail_streak_started.pop(sym, None)
                continue
            self._consecutive_fails[sym] = self._consecutive_fails.get(sym, 0) + 1
            if sym not in self._fail_streak_started:
                self._fail_streak_started[sym] = now
            if self._consecutive_fails[sym] >= self._STRAGGLER_THRESHOLD:
                duration_s = now - self._fail_streak_started[sym]
                log.warning(
                    f"KLINE_STRAGGLER | sym={sym} "
                    f"consecutive_fails={self._consecutive_fails[sym]} "
                    f"duration={duration_s:.0f}s | {ctx()}"
                )

        # Phase 6 (P0-5 Fix A): log level + reason driven by fetch quality.
        # The pre-existing single INFO line was masking 97% data-loss events
        # because it never escalated. Now total < 50% expected → ERROR,
        # total == 0 → CRITICAL with circuit breaker.
        # Phase 3 (post-Layer-1 fix): added ``errors=`` and ``el=`` fields.
        expected_total = sum(per_symbol_expected.values())
        level, reason = self._classify_fetch_quality(total_fetched, expected_total)
        el_ms = (time.monotonic() - t0_mono) * 1000
        _emit = getattr(log, level.lower(), log.info)
        _emit(
            f"KLINE_FETCH | klines={total_fetched} expected={expected_total} "
            f"symbols={len(self._tracked_symbols)} quality={reason} "
            f"errors={errors_this_tick} el={el_ms:.0f}ms | {ctx()}"
        )

        # Per-symbol gap report — only when something is actually short.
        if level in ("WARNING", "ERROR", "CRITICAL"):
            for sym, exp in per_symbol_expected.items():
                if exp <= 0:
                    continue
                got = per_symbol_fetched.get(sym, 0)
                if got >= exp:
                    continue
                stale_since = now - self._last_fetch.get(
                    f"{sym}:{TimeFrame.M5.value}", 0,
                )
                log.warning(
                    f"KLINE_GAP | sym={sym} expected={exp} got={got} "
                    f"stale_since={stale_since:.0f}s | {ctx()}"
                )

        # CRITICAL → open the circuit breaker so strategy_worker pauses TA.
        if level == "CRITICAL":
            self._circuit_breaker_until = time.monotonic() + 30.0
            log.critical(
                f"KLINE_CIRCUIT_BREAKER | open_until=+30s reason={reason} | {ctx()}"
            )

        # Phase 2 (corrected-Layer-1): single grouped SELECT feeds BOTH
        # post-tick diagnostics — KLINE_WRITE_LAG (candle-aware staleness,
        # 360 s threshold) and KLINE_FRESHNESS_WARN (longer 600 s threshold
        # + missing-row reporting). Pre-correction, these ran as two
        # separate queries each holding DatabaseManager._lock, doubling
        # the lock-hold time per tick. SQLite's SQLITE_MAX_VARIABLE_NUMBER
        # default is 999; the IN-clause is capped at _LAG_QUERY_MAX_SYMBOLS
        # = 500 (1 timeframe + 499 symbol slots) so larger universes
        # naturally sub-sample rather than crash the tick.
        #
        # Thresholds:
        #   _LAG_THRESHOLD_S          = candle_period + 60 s (= 360 s for M5)
        #   _KLINE_FRESHNESS_THRESHOLD_S = 600 s (2 missed M5 closes)
        try:
            _scan_syms = self._tracked_symbols[:_LAG_QUERY_MAX_SYMBOLS]
            if _scan_syms:
                # Issue I4 (F-27 DB lock cascade, 2026-05-14) — chunk
                # the staleness scan to release the DB lock between
                # batches. Pre-I4 a single fetch_all with up to 500
                # symbols in the IN clause held the DB lock for the
                # full query duration; the audited 22:35:48 cascade
                # peaked at 13,905 ms because this fetch_all was the
                # holder. Chunking lets sniper / watchdog / scanner
                # interleave their DB ops between batches, keeping
                # tick latency stable under load. _STALENESS_SCAN_CHUNK
                # is sized so each batch completes in well under the
                # 1000 ms DB_LOCK_WAIT_WARN threshold — at typical
                # latency ~10 ms/100 symbols, 100 keeps the per-batch
                # hold to ~10-50 ms.
                kline_rows: list[dict[str, Any]] = []
                _chunk_size = _STALENESS_SCAN_CHUNK
                for _chunk_start in range(0, len(_scan_syms), _chunk_size):
                    _chunk = _scan_syms[_chunk_start:_chunk_start + _chunk_size]
                    _ph = ",".join("?" for _ in _chunk)
                    _chunk_rows = await self.db.fetch_all(
                        f"""
                        SELECT symbol, MAX(timestamp) AS newest_ts
                        FROM klines
                        WHERE timeframe = ? AND symbol IN ({_ph})
                        GROUP BY symbol
                        """,
                        (TimeFrame.M5.value, *_chunk),
                    )
                    kline_rows.extend(_chunk_rows)
                    # Yield to the event loop so workers waiting on the
                    # DB lock get a chance to run between our chunks.
                    # Issue I4 — this is the lock-pressure-release
                    # mechanism that prevents the 14-second steady-
                    # state cascades observed in the audit window.
                    if _chunk_start + _chunk_size < len(_scan_syms):
                        log.debug(
                            f"DB_WRITE_DEFERRED | op=kline_staleness_scan "
                            f"chunk={_chunk_start // _chunk_size + 1} "
                            f"chunk_size={len(_chunk)} "
                            f"remaining={len(_scan_syms) - _chunk_start - _chunk_size} "
                            f"| {ctx()}"
                        )
                        await asyncio.sleep(0)
                now_dt = datetime.now(timezone.utc)
                _M5_PERIOD_S = 300
                _LAG_BUFFER_S = 60
                _LAG_THRESHOLD_S = _M5_PERIOD_S + _LAG_BUFFER_S
                _lag_stale: list[tuple[str, float]] = []
                _seen_syms: set[str] = set()
                for r in kline_rows:
                    sym = r["symbol"]
                    _seen_syms.add(sym)
                    ts_str = r.get("newest_ts")
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except Exception:
                        continue
                    age_s = (now_dt - ts).total_seconds()
                    # KLINE_WRITE_LAG: candle-aware threshold (360 s).
                    if age_s > _LAG_THRESHOLD_S:
                        _lag_stale.append((sym, age_s))
                    # KLINE_FRESHNESS_WARN: longer threshold (600 s).
                    # Operators correlate this with universe-pruning decisions.
                    if age_s > _KLINE_FRESHNESS_THRESHOLD_S:
                        log.warning(
                            f"KLINE_FRESHNESS_WARN | sym={sym} "
                            f"age_s={age_s:.0f} "
                            f"threshold_s={_KLINE_FRESHNESS_THRESHOLD_S:.0f} | {ctx()}"
                        )
                # Aggregate KLINE_WRITE_LAG line — top-5 stragglers.
                if _lag_stale:
                    _lag_stale.sort(key=lambda x: -x[1])
                    top = ",".join(f"{s}={a:.0f}s" for s, a in _lag_stale[:5])
                    log.warning(
                        f"KLINE_WRITE_LAG | stale_count={len(_lag_stale)} "
                        f"threshold_s={_LAG_THRESHOLD_S} "
                        f"candle_period_s={_M5_PERIOD_S} "
                        f"top5=[{top}] | {ctx()}"
                    )
                # Symbols in watch_list with NO M5 row at all — these are
                # worse-than-late stragglers (kline_worker has never
                # successfully fetched them or trading.db retention
                # purged them).
                for sym in _scan_syms:
                    if sym not in _seen_syms:
                        log.warning(
                            f"KLINE_FRESHNESS_WARN | sym={sym} "
                            f"age_s=inf reason=no_klines_in_db "
                            f"threshold_s={_KLINE_FRESHNESS_THRESHOLD_S:.0f} | {ctx()}"
                        )
        except Exception as e:
            # Phase 12.1 (lifecycle-logging-audit Gap 1.2-G1): promoted from
            # DEBUG to WARNING + structured ctx() suffix. Exception in the
            # post-tick freshness scan SQL means freshness reporting is
            # silently broken — operationally important to surface.
            log.warning(
                f"KLINE_FRESHNESS_SKIP | err='{str(e)[:120]}' | {ctx()}"
            )

        # Phase 1 (D-3 fix): scheduled WAL checkpoint. Fires once every
        # ``wal_checkpoint_every_n_kline_ticks`` (default 50) ticks AFTER
        # all writes for this tick are committed and the freshness scan
        # has completed. PASSIVE never blocks; it only truncates what is
        # safe right now. If PASSIVE returns ``busy != 0`` this many
        # times in a row, escalate the NEXT call to TRUNCATE which
        # briefly blocks writers but reclaims WAL space.
        self._tick_count += 1
        await self._maybe_run_wal_checkpoint()

        # Phase 2 (corrected-Layer-1): structured tick summary. Replaces the
        # legacy free-text "Kline worker: fetched N klines for S symbols"
        # line. Includes universe size, total fetched, skipped (cooldown),
        # per-timeframe split, elapsed wall-clock, and sweet-spot drift.
        tf_split = ",".join(
            f"{tf}:{n}" for tf, n in tf_fetched.items()
        )
        log.info(
            f"KLINE_TICK_SUMMARY | universe={len(self._tracked_symbols)} "
            f"fetched={total_fetched} saved={total_fetched} "
            f"skipped={skipped_cooldown} tf_split={{{tf_split}}} "
            f"errors={errors_this_tick} el={el_ms:.0f}ms "
            f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

    async def _maybe_run_wal_checkpoint(self) -> None:
        """Run ``PRAGMA wal_checkpoint`` on a configurable cadence.

        Phase 1 (D-3 fix). Called at the end of every kline_worker tick
        AFTER writes/scans complete. Skips silently unless the per-tick
        counter has reached the configured cadence. PASSIVE is the
        default mode; if too many consecutive PASSIVE checkpoints come
        back ``busy != 0``, the next scheduled call escalates to
        TRUNCATE.

        Errors are logged but never re-raised — a failed checkpoint
        must not break the kline_worker tick.
        """
        cadence = self.settings.database.wal_checkpoint_every_n_kline_ticks
        if cadence < 1 or self._tick_count % cadence != 0:
            return

        truncate_after = (
            self.settings.database.wal_checkpoint_truncate_after_busy_count
        )
        mode = (
            "TRUNCATE"
            if self._consecutive_busy_checkpoints >= truncate_after
            else "PASSIVE"
        )
        wal_path = f"{self.settings.database.path}-wal"
        try:
            wal_size_before = (
                os.path.getsize(wal_path) if os.path.exists(wal_path) else -1
            )
        except OSError:
            wal_size_before = -1

        try:
            result = await self.db.checkpoint(mode=mode)
        except Exception as e:
            log.warning(
                f"WAL_CHECKPOINT_ERR | mode={mode} err='{str(e)[:120]}' | {ctx()}"
            )
            return

        try:
            wal_size_after = (
                os.path.getsize(wal_path) if os.path.exists(wal_path) else -1
            )
        except OSError:
            wal_size_after = -1

        busy = int(result.get("busy", 0))
        log_pages = int(result.get("log_pages", -1))
        ckpt_pages = int(result.get("ckpt_pages", -1))

        if busy != 0:
            self._consecutive_busy_checkpoints += 1
        else:
            self._consecutive_busy_checkpoints = 0

        # Emit a single structured line per scheduled checkpoint so
        # operators can grep for the cadence and reclamation history.
        log.info(
            f"WAL_CHECKPOINT_SCHEDULED | mode={mode} busy={busy} "
            f"log_pages={log_pages} ckpt_pages={ckpt_pages} "
            f"wal_before={wal_size_before} wal_after={wal_size_after} "
            f"tick={self._tick_count} consecutive_busy="
            f"{self._consecutive_busy_checkpoints} | {ctx()}"
        )

        # Escalation announcement: emitted when the next scheduled call
        # will use TRUNCATE because consecutive busy hit the threshold.
        if (
            mode == "PASSIVE"
            and self._consecutive_busy_checkpoints >= truncate_after
        ):
            log.warning(
                f"WAL_CHECKPOINT_ESCALATE | reason=busy_count="
                f"{self._consecutive_busy_checkpoints} "
                f"threshold={truncate_after} next_mode=TRUNCATE | {ctx()}"
            )

