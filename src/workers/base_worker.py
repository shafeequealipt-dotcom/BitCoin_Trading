"""Abstract base class for all background workers.

Handles the run loop, error recovery with exponential backoff,
heartbeat logging, and lifecycle management.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from src.config.settings import Settings
from src.core.exceptions import WorkerCrashError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import WorkerStatus, WorkerTier
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.workers.sweet_spot_scheduler import SweetSpotScheduler

# Phase 5 (Stage-1/2 fix): BASE_WORKER_TICK_SLOW threshold (seconds).
# A tick longer than this holds the shared asyncio event loop and will
# delay every other worker's next tick (notably position_watchdog at 10s
# cadence). The threshold is intentionally lower than the WD_POLL_LAG
# emission at 2× the configured watchdog interval (20s default) so this
# log fires FIRST when a worker is the source of event-loop congestion.
_BASE_WORKER_TICK_SLOW_SECONDS = 2.0

# Phase 10 (post-Layer-1 fix): per-worker overrides for the slow-tick
# threshold. Some workers legitimately have longer ticks — kline_worker
# fans out across the whole universe; strategy_worker prefetches +
# scores. Pre-fix, both tripped BASE_WORKER_TICK_SLOW every tick at the
# global 2 s threshold, drowning out the cases where the threshold
# actually identified a problem. Operators can grep these names to see
# which workers expect higher latencies — this is documentation, not
# config drift.
# Phase 11 (dead-workers fix). Per-worker rate limit on the
# LAYER1{B,C,D}_TICK_SKIP INFO emit so a long L3=OFF window doesn't
# flood workers.log. Intermediate skips still log at DEBUG so a
# DEBUG-level run sees them all (e.g. for forensic analysis).
# 600 s = 10 min; with 5 cycle_gated workers on 5-min cadence that's
# at most 30 INFO events per hour from this tag.
_SKIP_INFO_RATE_LIMIT_S: float = 600.0

_TICK_SLOW_PER_WORKER: dict[str, float] = {
    "kline_worker": 8.0,        # ~30 syms × ~1.5 tfs × API + DB writes
    "strategy_worker": 10.0,    # prefetch + TA + brain prep
    "structure_worker": 6.0,    # X-RAY computation per session
    "regime_worker": 4.0,       # per-coin regime + global
    # Phase 9 (post-Layer-1 fix): altdata fans out to 3-4 REST batch
    # calls (Bybit funding, Bybit OI, AlternativeMe F&G, optional
    # on-chain). Each batch is 2-5 s in steady state; combined wall
    # clock under asyncio.gather is bounded by the slowest. Live
    # observation 2026-04-27 showed 5-9 s ticks against the 2 s global
    # threshold — every tick was logged as slow despite the worker's
    # 300 s interval (1.7% of cadence consumed). 12 s comfortably
    # covers the observed envelope while still alerting if a feed
    # genuinely hangs.
    "altdata_worker": 12.0,
}

log = get_logger("worker")


def _liveness_record_tick(name: str) -> None:
    """Notify the WorkerLivenessTracker singleton of a successful tick.

    Phase 11 (dead-workers fix). Indirected through this helper so the
    BaseWorker / SweetSpotWorker run loops don't grow new top-level
    imports of the tracker — every successful tick goes through here,
    silently no-ops if the tracker is unavailable (e.g. during early
    boot before the tracker is constructed). The default tracker is
    a module-level singleton; tests can override via
    ``worker_liveness.set_default_tracker``.
    """
    try:
        from src.core.worker_liveness import get_default_tracker
        get_default_tracker().record_tick(name)
    except Exception:  # pragma: no cover — defensive
        # Liveness recording must never break the tick path. If the
        # tracker import or call fails, the worker continues; the
        # watchdog will eventually surface the absence of records as a
        # NEVER_TICKED warning and the operator will investigate.
        pass


class BaseWorker(ABC):
    """Abstract base for all background workers.

    Subclasses implement tick() — one cycle of the worker's job.
    BaseWorker handles scheduling, error recovery, heartbeat, and lifecycle.

    Layer 1 restructure Phase 1 — subclasses may set ``layer_tier_tag``
    (one of ``"LAYER1A"``, ``"LAYER1B"``, ``"LAYER1C"``, ``"LAYER1D"``).
    When set, BaseWorker emits ``{tier}_TICK_DONE`` after each successful
    tick with elapsed_ms. This is additive to any per-worker
    ``*_TICK_SUMMARY`` lines that already exist; the new tag standardizes
    the start/done markers across the four sub-layers so cross-layer
    aggregation is grep-friendly.

    Args:
        name: Worker identifier (e.g. "price_worker").
        interval_seconds: Seconds between ticks.
        settings: Application settings.
        db: Database manager.
    """

    # Layer 1 restructure Phases 1 + 4 — canonical sub-layer assignment.
    # ``worker_tier`` (WorkerTier | None) is the source of truth; the
    # log-emission string ``layer_tier_tag`` is derived from it via
    # the property below so a worker subclass cannot drift from the
    # enum's string form. Subclasses set ``worker_tier`` directly:
    #     worker_tier = WorkerTier.LAYER1A
    # Utility workers (cleanup, discovery, etc.) leave this None and
    # neither the cycle gate nor cycle tracker engages.
    worker_tier: WorkerTier | None = None

    # Layer 1 restructure Phase 4 — when True, the run loop checks
    # ``layer_manager.is_cycle_active()`` before each tick and emits a
    # ``LAYER1{B,C,D}_TICK_SKIP`` debug line + skips the tick when the
    # cycle is inactive (trading toggled off). LAYER1A workers leave
    # this False so data fetchers always run.
    cycle_gated: bool = False

    @property
    def layer_tier_tag(self) -> str | None:
        """Uppercase log-emission tag derived from ``worker_tier``.

        Returns ``"LAYER1A"`` / ``"LAYER1B"`` / ... for tier-tagged
        workers, or None for utility workers. Centralizes the
        enum-to-string conversion so log lines and cycle-tracker
        keys cannot drift from each other.
        """
        if self.worker_tier is None:
            return None
        return self.worker_tier.value.upper()

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        settings: Settings,
        db: DatabaseManager,
    ) -> None:
        self.name = name
        self.interval = interval_seconds
        self.settings = settings
        self.db = db
        # Phase 14 Gap J1 (output-quality obs): per-instance worker ID.
        # Distinguishes two restarts of the same worker (same `name`,
        # different `wid`). Used in WM_START / WM_STOP / WM_CRASH logs
        # by WorkerManager so operators can tell from the logs whether
        # a worker has been restarted vs is the original boot instance.
        # 8-char hex is enough — per-process uniqueness, not global.
        import uuid as _uuid
        self.wid = _uuid.uuid4().hex[:8]
        self.status = WorkerStatus.STOPPED
        self.running = False
        self.restart_count = 0
        self.max_restarts = settings.workers.max_consecutive_failures
        self.restart_delay = float(settings.workers.restart_delay)
        self.last_tick_time: datetime | None = None
        self.last_error: str | None = None
        self.total_ticks = 0
        self.error_count = 0
        # Layer 1 restructure Phase 4 — late-bound LayerManager handle
        # for the cycle_gated check inside the run loop. WorkerManager
        # sets this after instantiation. None when not wired —
        # gated workers fall through (don't skip) so a wiring oversight
        # doesn't silently halt all analysis.
        self._layer_manager = None
        # Layer 1 restructure Phase 1 — late-bound CycleTracker handle.
        # WorkerManager wires this for every layer_tier_tag-tagged
        # worker so the run loop can record start/end cycle markers
        # without each worker subclass duplicating the boilerplate.
        # 1A workers leave this None — they have no "cycle" semantics.
        self._cycle_tracker = None
        self._heartbeat_interval = 300  # 5 minutes
        self._last_heartbeat: float = 0.0
        self._start_time: datetime | None = None
        # Phase 10 (post-Layer-1 fix): one-shot WORKER_FIRST_TICK milestone.
        # Lets operators see boot-to-first-tick latency per worker; useful
        # for diagnosing slow startup chains (e.g. universe wiring races).
        self._first_tick_logged: bool = False
        # Phase 11 (dead-workers fix): one-shot WORKER_TICK_START milestone.
        # Fires once, right BEFORE the first ``await self.tick()``. The
        # presence/absence of this marker is the canonical diagnostic
        # for "did the run loop reach the tick body?" If WM_START fires
        # but WORKER_TICK_START never does, the issue is upstream (cycle
        # gate, scheduler) — not in tick(). Confirms a future regression
        # of the same shape as the 2026-04-27 silent-skip is observable.
        self._tick_start_logged: bool = False
        # Phase 11: per-worker rate-limited cycle-skip log timestamp.
        # The pre-fix DEBUG-level skip log was filtered out at the
        # configured INFO log level, leaving operators no visibility
        # into "worker is silent because L3 is OFF". The skip now emits
        # at INFO with a 10-min per-worker rate-limit so workers.log
        # has a continuous record without flooding (5 cycle_gated workers
        # × 12 fires/hour = 60 events/hour at DEBUG; with rate-limit at
        # 10 min that becomes 5 × 6 = 30 INFO events/hour).
        self._last_skip_info_ts: float = 0.0
        # Resolved slow-tick threshold (per-worker override beats the
        # module-level default).
        self._tick_slow_threshold_s = _TICK_SLOW_PER_WORKER.get(
            name, _BASE_WORKER_TICK_SLOW_SECONDS,
        )

    async def start(self) -> None:
        """Start the worker run loop.

        Runs tick() at configured intervals with error recovery and auto-restart.

        Raises:
            WorkerCrashError: If max restart attempts exceeded.
        """
        self.running = True
        self.status = WorkerStatus.RUNNING
        self._start_time = now_utc()
        log.info(
            "Worker '{name}' started (interval={interval}s)",
            name=self.name, interval=self.interval,
        )

        while self.running:
            try:
                # Layer 1 restructure Phase 4 — cycle gate. 1B/1C/1D
                # workers (cycle_gated=True) skip ticks when trading
                # toggle is off so we don't burn cycles analyzing data
                # nobody will act on. LAYER1A workers (cycle_gated=False)
                # always run.
                if (
                    self.cycle_gated and self._layer_manager
                    and hasattr(self._layer_manager, "is_cycle_active")
                    and not self._layer_manager.is_cycle_active()
                ):
                    if self.layer_tier_tag:
                        # Phase 11 (dead-workers fix): rate-limited INFO
                        # promotion. Pre-fix this was DEBUG-only, which
                        # left operators no visibility into "5 workers
                        # silent because L3 is OFF" at INFO log level.
                        # _SKIP_INFO_RATE_LIMIT_S = 600 (10 min) bounds
                        # the noise; intermediate skips still emit at
                        # DEBUG so a DEBUG-level run sees them all.
                        _now_skip = time.monotonic()
                        if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                            self._last_skip_info_ts = _now_skip
                            log.info(
                                f"{self.layer_tier_tag}_TICK_SKIP | "
                                f"sub={self.name} reason=cycle_inactive "
                                f"rate_limited=true | {ctx()}"
                            )
                        else:
                            log.debug(
                                f"{self.layer_tier_tag}_TICK_SKIP | "
                                f"sub={self.name} reason=cycle_inactive | {ctx()}"
                            )
                    await asyncio.sleep(self.interval)
                    continue
                # Layer 1 restructure Phase 4 — cold-start boundary gate.
                # Blueprint HR-8 (LAYER1_RESTRUCTURE_BLUEPRINT.md §18.8)
                # mandates that the first analytical cycle after a cold
                # start (workers boot, or trading toggled off→on) waits
                # for the next 5-min boundary so all four sub-layers see
                # fresh data simultaneously. The original Phase 4 only
                # emitted CYCLE_RESUME_WAIT/CYCLE_RESUME log markers; the
                # implicit assumption was that the natural sweet-spot
                # ordering (kline 0:30 → ... → scanner 4:00) would
                # produce the right sequence. That assumption fails for
                # partial-window boots where ``scanner_worker`` (4:00)
                # is the only offset still ahead of ``now`` in the boot
                # window — it fires alone before any upstream cache
                # exists, producing fail_no_xray=50.
                #
                # ``LayerManager._cold_start_resume_done`` is set False
                # the moment a boundary wait is scheduled and flipped
                # back to True the moment the wait completes (CYCLE_RESUME
                # log line). We consult it via ``getattr`` so test
                # fixtures using ``LayerManager.__new__`` keep working.
                # Default True = fail-open: a wiring oversight does not
                # silently halt analysis.
                if (
                    self.cycle_gated and self._layer_manager
                    and not getattr(
                        self._layer_manager,
                        "_cold_start_resume_done",
                        True,
                    )
                ):
                    if self.layer_tier_tag:
                        _now_skip = time.monotonic()
                        if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                            self._last_skip_info_ts = _now_skip
                            log.info(
                                f"{self.layer_tier_tag}_TICK_SKIP | "
                                f"sub={self.name} "
                                f"reason=cold_start_boundary_pending "
                                f"rate_limited=true | {ctx()}"
                            )
                        else:
                            log.debug(
                                f"{self.layer_tier_tag}_TICK_SKIP | "
                                f"sub={self.name} "
                                f"reason=cold_start_boundary_pending | "
                                f"{ctx()}"
                            )
                    await asyncio.sleep(self.interval)
                    continue
                # Phase 5 (Stage-1/2 fix): per-tick wall-clock measurement
                # so ``BASE_WORKER_TICK_SLOW`` names the specific worker
                # holding the event loop when ``WD_POLL_LAG`` fires. Cost
                # is two time.monotonic() calls per tick — negligible.
                _tick_start = time.monotonic()
                # Layer 1 restructure Phase 1 — start a cycle marker for
                # 1B/1C/1D workers so CYCLE_COMPLETE rolls up real
                # latencies instead of zeros. ScannerWorker (1D) keeps
                # its own start/end inside tick() so it can stamp
                # qualified/selected/packages onto the summary; 1B and
                # 1C delegate timing to this base loop.
                _cycle_id = self._maybe_start_cycle(_tick_start)
                # Phase 11 (dead-workers fix): one-shot WORKER_TICK_START.
                # Emit BEFORE await self.tick() so the diagnostic
                # "did the run loop reach tick()?" answer is loud and
                # structured. Logged once per worker per process; cheap.
                if not self._tick_start_logged:
                    self._tick_start_logged = True
                    log.info(
                        f"WORKER_TICK_START | name={self.name} "
                        f"tier={self.layer_tier_tag} | {ctx()}"
                    )
                await self.tick()
                _tick_el = time.monotonic() - _tick_start
                self._maybe_end_cycle(_cycle_id)
                # Phase 10: one-shot first-tick milestone with boot-to-first-tick
                # latency. Cheap; only fires once per worker per process.
                if not self._first_tick_logged:
                    self._first_tick_logged = True
                    if self._start_time:
                        boot_to_tick_ms = (
                            now_utc() - self._start_time
                        ).total_seconds() * 1000.0
                    else:
                        boot_to_tick_ms = 0.0
                    log.info(
                        f"WORKER_FIRST_TICK | name={self.name} "
                        f"el_to_first_tick_ms={boot_to_tick_ms:.0f} "
                        f"first_tick_el_ms={_tick_el * 1000:.0f} | {ctx()}"
                    )
                if _tick_el > self._tick_slow_threshold_s:
                    log.warning(
                        f"BASE_WORKER_TICK_SLOW | name={self.name} "
                        f"el={_tick_el * 1000:.0f}ms "
                        f"threshold_ms={self._tick_slow_threshold_s * 1000:.0f} "
                        f"interval_s={self.interval:.1f} | {ctx()}"
                    )
                # Layer 1 restructure Phase 1 — emit standardized tick-done
                # marker keyed by sub-layer. Additive; existing per-worker
                # *_TICK_SUMMARY lines stay. See src/core/log_tags.py.
                if self.layer_tier_tag:
                    log.info(
                        f"{self.layer_tier_tag}_TICK_DONE | sub={self.name} "
                        f"elapsed_ms={_tick_el * 1000:.0f} "
                        f"interval_s={self.interval:.1f} | {ctx()}"
                    )
                # Phase 11 (dead-workers fix). Notify the liveness
                # tracker AFTER the existing observability has fired so
                # the watchdog's heartbeat ordering is deterministic and
                # WORKER_FIRST_TICK / LAYER1*_TICK_DONE remain the
                # canonical "tick succeeded" signals in workers.log.
                # Module-level singleton get is a single dict lookup — no
                # measurable overhead per tick.
                _liveness_record_tick(self.name)
                self.last_tick_time = now_utc()
                self.total_ticks += 1
                self.restart_count = 0
                self._maybe_log_heartbeat()
            except Exception as e:
                self.error_count += 1
                self.restart_count += 1
                self.last_error = str(e)
                self.status = WorkerStatus.ERROR
                # Phase 11 (dead-workers fix). Structured WORKER_TICK_FAIL
                # tag complements the existing free-text WARNING below so
                # operators can grep one tag for tick failures across
                # all workers without picking up unrelated warnings.
                log.warning(
                    f"WORKER_TICK_FAIL | name={self.name} "
                    f"tier={self.layer_tier_tag} "
                    f"err_type={type(e).__name__} "
                    f"err='{str(e)[:120]}' "
                    f"restart_count={self.restart_count} | {ctx()}"
                )
                log.error(
                    "Worker '{name}' tick failed ({rc}/{max}): {err}",
                    name=self.name, rc=self.restart_count,
                    max=self.max_restarts, err=str(e),
                )

                if self.restart_count >= self.max_restarts:
                    log.critical(
                        "Worker '{name}' exceeded max restarts ({max}). Stopping permanently.",
                        name=self.name, max=self.max_restarts,
                    )
                    self.running = False
                    self.status = WorkerStatus.STOPPED
                    raise WorkerCrashError(
                        f"Worker '{self.name}' crashed after {self.max_restarts} attempts: {e}"
                    )

                backoff = min(self.restart_delay * (2 ** (self.restart_count - 1)), 60.0)
                self.status = WorkerStatus.RESTARTING
                log.warning(
                    "Worker '{name}' restarting in {backoff:.1f}s (attempt {rc})",
                    name=self.name, backoff=backoff, rc=self.restart_count,
                )
                await asyncio.sleep(backoff)
                self.status = WorkerStatus.RUNNING
                continue

            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self.running = False
        self.status = WorkerStatus.STOPPED
        await self.cleanup()
        log.info(
            "Worker '{name}' stopped (total_ticks={ticks}, errors={errs})",
            name=self.name, ticks=self.total_ticks, errs=self.error_count,
        )

    @abstractmethod
    async def tick(self) -> None:
        """One cycle of the worker's job. Subclasses implement this."""
        ...

    async def cleanup(self) -> None:
        """Resource cleanup on stop. Override in subclasses if needed."""
        pass

    def _maybe_log_heartbeat(self) -> None:
        """Log a heartbeat if enough time has passed since the last one."""
        now = time.monotonic()
        if now - self._last_heartbeat >= self._heartbeat_interval:
            self._last_heartbeat = now
            log.info(
                "[HEARTBEAT] Worker '{name}' alive | ticks={ticks} | errors={errs} | last_tick={lt}",
                name=self.name, ticks=self.total_ticks, errs=self.error_count,
                lt=self.last_tick_time.isoformat() if self.last_tick_time else "N/A",
            )

    # ─── Phase 1 cycle-tracker helpers ─────────────────────────────────
    # 1A workers (cycle_gated=False) skip cycle tracking — they have no
    # cycle semantics. 1D (ScannerWorker) drives cycle start/end
    # explicitly inside its tick because it stamps qualified/selected/
    # packages onto the summary; the base loop must NOT double-start
    # for 1D so we exclude that tier here. Stored as a frozenset of the
    # canonical ``WorkerTier`` enum members so a string-vs-enum drift
    # at any subclass declaration triggers an immediate type error.
    _CYCLE_TRACKED_TIERS: frozenset[WorkerTier] = frozenset(
        {WorkerTier.LAYER1B, WorkerTier.LAYER1C}
    )

    def _maybe_start_cycle(self, t_start_monotonic: float) -> str | None:
        """Start a CycleTracker entry for this tick if applicable.

        Returns the cycle_id when a cycle was started, else None.
        Failure-tolerant: any exception inside the tracker degrades to
        None and is logged at DEBUG so a recorder bug never starves the
        production tick body.
        """
        if (
            self._cycle_tracker is None
            or self.worker_tier not in self._CYCLE_TRACKED_TIERS
        ):
            return None
        try:
            # Enum's .value is already lowercase ("layer1b") which
            # matches CycleTracker._CYCLE_LAYERS validation.
            return self._cycle_tracker.start_cycle(self.worker_tier.value)
        except Exception as e:
            log.debug(
                f"CYCLE_TRACKER_START_FAIL | sub={self.name} "
                f"err='{str(e)[:80]}'"
            )
            return None

    def _maybe_end_cycle(self, cycle_id: str | None) -> None:
        """Close a CycleTracker entry started by ``_maybe_start_cycle``.

        Tolerates all errors so a tracker bug cannot break the tick
        post-condition (last_tick_time update, restart_count reset).
        """
        if cycle_id is None or self._cycle_tracker is None or self.worker_tier is None:
            return
        try:
            self._cycle_tracker.end_cycle(self.worker_tier.value, cycle_id)
        except Exception as e:
            log.debug(
                f"CYCLE_TRACKER_END_FAIL | sub={self.name} "
                f"err='{str(e)[:80]}'"
            )

    def get_status(self) -> dict:
        """Return current worker status as a dict."""
        uptime = 0.0
        if self._start_time:
            uptime = (now_utc() - self._start_time).total_seconds()
        return {
            "name": self.name,
            "status": self.status.value,
            "running": self.running,
            "restart_count": self.restart_count,
            "total_ticks": self.total_ticks,
            "error_count": self.error_count,
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
            "last_error": self.last_error,
            "uptime_seconds": round(uptime, 1),
        }


class SweetSpotWorker(BaseWorker):
    """BaseWorker variant whose tick fires at a configured MM:SS offset within each window.

    Replaces the fixed-interval ``await asyncio.sleep(self.interval)`` at the
    end of ``BaseWorker.start()`` with ``await scheduler.wait_for_sweet_spot()``
    placed at the START of each cycle (so the worker waits FIRST then ticks).
    All other behavior (error recovery, heartbeat, lifecycle, tick-slow log,
    ``WORKER_FIRST_TICK``, restart cap) is identical to ``BaseWorker``.

    Used by the 7 data workers in the corrected Layer 1 architecture
    (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8). Workers that should
    keep firing on a fixed interval (NewsWorker, RedditWorker,
    CleanupWorker, PriceWorker, PositionWatchdog, ProfitSniper, etc.)
    continue to subclass ``BaseWorker`` directly.

    Args:
        name: Worker identifier (e.g. ``"kline_worker"``).
        sweet_spot: ``"MM:SS"`` offset within the window, validated upstream
            by ``SweetSpotsSettings``.
        settings: Application settings.
        db: Database manager.
        window_minutes: Window length in minutes (default 5; must match
            settings.workers.sweet_spots.window_minutes).
    """

    def __init__(
        self,
        name: str,
        sweet_spot: str,
        settings: Settings,
        db: DatabaseManager,
        *,
        window_minutes: int = 5,
    ) -> None:
        # Pass window_minutes * 60 as the legacy ``interval_seconds`` so any
        # downstream code that introspects ``self.interval`` still gets a
        # sensible value. Scheduling is driven by the scheduler below — the
        # interval is documentation, not behavior.
        super().__init__(
            name=name,
            interval_seconds=float(window_minutes * 60),
            settings=settings,
            db=db,
        )
        self._scheduler = SweetSpotScheduler(
            worker_name=name,
            offset=sweet_spot,
            window_minutes=window_minutes,
        )
        # Drift of the most recent sweet-spot fire — exposed for per-worker
        # tick summaries (e.g. ``KLINE_TICK_SUMMARY ... drift_ms=X``).
        self._last_drift_ms: float = 0.0

    async def start(self) -> None:
        """Sweet-spot variant of the run loop.

        Differs from ``BaseWorker.start`` in exactly one place: the trailing
        ``await asyncio.sleep(self.interval)`` is replaced with
        ``await self._scheduler.wait_for_sweet_spot()`` placed BEFORE the
        first tick (not after it). This guarantees the chain ordering
        applies from boot — kline doesn't tick at boot before structure;
        instead both wait for their respective sweet spots in the SAME
        first window after startup.

        Raises:
            WorkerCrashError: If max restart attempts exceeded (same as
                ``BaseWorker.start``).
        """
        self.running = True
        self.status = WorkerStatus.RUNNING
        self._start_time = now_utc()
        log.info(
            "Worker '{name}' started (sweet_spot={offset} window_min={w})",
            name=self.name,
            offset=self._scheduler.offset_str,
            w=self._scheduler.window_minutes,
        )

        while self.running:
            # Wait for the next sweet spot BEFORE ticking. This is the only
            # behavioral difference from BaseWorker.start — every other line
            # below mirrors the parent's loop body.
            try:
                self._last_drift_ms = await self._scheduler.wait_for_sweet_spot()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Scheduler failure is unexpected; sleep briefly and retry.
                log.warning(
                    f"SWEET_SPOT_WAIT_FAIL | worker={self.name} "
                    f"err={str(e)[:120]} | {ctx()}"
                )
                await asyncio.sleep(1.0)
                continue

            if not self.running:
                break

            # Layer 1 restructure Phase 4 — cycle gate (sweet-spot variant).
            # The scheduler still fires at the configured spot; we skip
            # the actual tick body when the cycle is inactive. Skipping
            # here (not before wait_for_sweet_spot) means the next fire
            # remains anchored to wall-clock — no schedule drift.
            if (
                self.cycle_gated and self._layer_manager
                and hasattr(self._layer_manager, "is_cycle_active")
                and not self._layer_manager.is_cycle_active()
            ):
                if self.layer_tier_tag:
                    # Phase 11 (dead-workers fix): rate-limited INFO
                    # promotion mirrors BaseWorker.start. See that
                    # docstring for rationale.
                    _now_skip = time.monotonic()
                    if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                        self._last_skip_info_ts = _now_skip
                        log.info(
                            f"{self.layer_tier_tag}_TICK_SKIP | "
                            f"sub={self.name} reason=cycle_inactive "
                            f"drift_ms={self._last_drift_ms:.0f} "
                            f"rate_limited=true | {ctx()}"
                        )
                    else:
                        log.debug(
                            f"{self.layer_tier_tag}_TICK_SKIP | "
                            f"sub={self.name} reason=cycle_inactive "
                            f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
                        )
                continue

            # Layer 1 restructure Phase 4 — cold-start boundary gate
            # (sweet-spot variant). Mirrors the gate in BaseWorker.start;
            # see that block for the full rationale. Tested live: on
            # the 02:51:33 → 02:51:45 restart, scanner_worker fired its
            # first tick at 02:54:00 with an empty XRAY cache because
            # the original Phase-4 wait was observation-only. This gate
            # closes that gap by skipping every cycle_gated tick until
            # ``CYCLE_RESUME`` flips ``_cold_start_resume_done`` back
            # to True. ``getattr`` default True is the fail-open path
            # for tests that build LM via ``__new__``.
            if (
                self.cycle_gated and self._layer_manager
                and not getattr(
                    self._layer_manager,
                    "_cold_start_resume_done",
                    True,
                )
            ):
                if self.layer_tier_tag:
                    _now_skip = time.monotonic()
                    if _now_skip - self._last_skip_info_ts >= _SKIP_INFO_RATE_LIMIT_S:
                        self._last_skip_info_ts = _now_skip
                        log.info(
                            f"{self.layer_tier_tag}_TICK_SKIP | "
                            f"sub={self.name} "
                            f"reason=cold_start_boundary_pending "
                            f"drift_ms={self._last_drift_ms:.0f} "
                            f"rate_limited=true | {ctx()}"
                        )
                    else:
                        log.debug(
                            f"{self.layer_tier_tag}_TICK_SKIP | "
                            f"sub={self.name} "
                            f"reason=cold_start_boundary_pending "
                            f"drift_ms={self._last_drift_ms:.0f} | "
                            f"{ctx()}"
                        )
                continue

            try:
                _tick_start = time.monotonic()
                # Layer 1 restructure Phase 1 — see BaseWorker.start
                # equivalent. Sweet-spot variant uses identical wrapper
                # so 1B/1C ticks (which use SweetSpotWorker) feed
                # CycleTracker latencies for the CYCLE_COMPLETE rollup.
                _cycle_id = self._maybe_start_cycle(_tick_start)
                # Phase 11 (dead-workers fix): one-shot WORKER_TICK_START
                # mirrors BaseWorker.start. See that docstring for the
                # diagnostic rationale.
                if not self._tick_start_logged:
                    self._tick_start_logged = True
                    log.info(
                        f"WORKER_TICK_START | name={self.name} "
                        f"tier={self.layer_tier_tag} "
                        f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
                    )
                await self.tick()
                _tick_el = time.monotonic() - _tick_start
                self._maybe_end_cycle(_cycle_id)

                if not self._first_tick_logged:
                    self._first_tick_logged = True
                    if self._start_time:
                        boot_to_tick_ms = (
                            now_utc() - self._start_time
                        ).total_seconds() * 1000.0
                    else:
                        boot_to_tick_ms = 0.0
                    log.info(
                        f"WORKER_FIRST_TICK | name={self.name} "
                        f"el_to_first_tick_ms={boot_to_tick_ms:.0f} "
                        f"first_tick_el_ms={_tick_el * 1000:.0f} "
                        f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
                    )

                if _tick_el > self._tick_slow_threshold_s:
                    log.warning(
                        f"BASE_WORKER_TICK_SLOW | name={self.name} "
                        f"el={_tick_el * 1000:.0f}ms "
                        f"threshold_ms={self._tick_slow_threshold_s * 1000:.0f} "
                        f"interval_s={self.interval:.1f} | {ctx()}"
                    )
                # Layer 1 restructure Phase 1 — sweet-spot variant emits the
                # tier-tagged tick-done marker with drift_ms (the SweetSpot
                # scheduler's drift) so operators can correlate timing
                # tightness with tick latency.
                if self.layer_tier_tag:
                    log.info(
                        f"{self.layer_tier_tag}_TICK_DONE | sub={self.name} "
                        f"elapsed_ms={_tick_el * 1000:.0f} "
                        f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
                    )

                # Phase 11 (dead-workers fix). See note in BaseWorker.start.
                _liveness_record_tick(self.name)
                self.last_tick_time = now_utc()
                self.total_ticks += 1
                self.restart_count = 0
                self._maybe_log_heartbeat()
            except Exception as e:
                self.error_count += 1
                self.restart_count += 1
                self.last_error = str(e)
                self.status = WorkerStatus.ERROR
                # Phase 11 (dead-workers fix). Structured WORKER_TICK_FAIL
                # tag complements the existing free-text WARNING below so
                # operators can grep one tag for tick failures across
                # all workers without picking up unrelated warnings.
                log.warning(
                    f"WORKER_TICK_FAIL | name={self.name} "
                    f"tier={self.layer_tier_tag} "
                    f"err_type={type(e).__name__} "
                    f"err='{str(e)[:120]}' "
                    f"restart_count={self.restart_count} | {ctx()}"
                )
                log.error(
                    "Worker '{name}' tick failed ({rc}/{max}): {err}",
                    name=self.name, rc=self.restart_count,
                    max=self.max_restarts, err=str(e),
                )

                if self.restart_count >= self.max_restarts:
                    log.critical(
                        "Worker '{name}' exceeded max restarts ({max}). Stopping permanently.",
                        name=self.name, max=self.max_restarts,
                    )
                    self.running = False
                    self.status = WorkerStatus.STOPPED
                    raise WorkerCrashError(
                        f"Worker '{self.name}' crashed after {self.max_restarts} attempts: {e}"
                    )

                backoff = min(self.restart_delay * (2 ** (self.restart_count - 1)), 60.0)
                self.status = WorkerStatus.RESTARTING
                log.warning(
                    "Worker '{name}' restarting in {backoff:.1f}s (attempt {rc})",
                    name=self.name, backoff=backoff, rc=self.restart_count,
                )
                await asyncio.sleep(backoff)
                self.status = WorkerStatus.RUNNING
                # After backoff we continue to wait for the NEXT sweet spot
                # via the loop top — failed tick does not fire again immediately.
                continue

    def get_scheduler_stats(self) -> dict:
        """Expose drift/fire stats for periodic chain-health observability."""
        return self._scheduler.get_stats()
