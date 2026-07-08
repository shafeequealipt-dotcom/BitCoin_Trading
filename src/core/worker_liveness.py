"""Phase 11 (dead-workers fix) — per-worker liveness tracker.

Distinct from ``src/workers/health.py::WorkerHealthMonitor`` (which holds
register/snapshot helpers for the global /health command) and from
``src/core/health_monitor.py::SystemHealthMonitor`` (which probes
event-loop lag and process-level metrics). This module tracks the
information needed to answer one specific question:

    "For each registered worker, has it ticked since startup, and if
    yes, when was its most recent successful tick?"

The 09:58 dead-workers incident remained invisible for 7 hours because
the answer to that question wasn't surfaced anywhere — the workers
emitted ``WM_START`` and ``SWEET_SPOT_FIRED`` but no operator had a
way to detect "registered but never ticked". This tracker plus the
companion ``WorkerLivenessWatchdog`` close that gap.

Cycle-gate awareness:
    Layer 1B/1C/1D workers (``cycle_gated=True``) are intentionally
    silent when ``LayerManager.is_cycle_active()`` returns False (Layer
    2 OR Layer 3 OFF). The watchdog passes the live cycle_active value
    into ``is_alive`` so a cycle-gated worker that hasn't ticked while
    the cycle is OFF reports ``status="idle_cycle_gate"`` — NOT
    ``"never_ticked"``. False positives during normal L3=OFF operation
    would be alarm fatigue.

Threading model:
    All record_* methods are called on the single asyncio event loop;
    the underlying ``dict`` mutations are atomic at the Python bytecode
    level. No lock is needed. Snapshot methods produce immutable copies
    so callers cannot mutate live state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.core.logging import get_logger

log = get_logger("worker_liveness")

# Cycle-gated worker statuses ────────────────────────────────────────────
STATUS_HEALTHY = "healthy"
STATUS_NEVER_TICKED = "never_ticked"
STATUS_OVERDUE = "overdue"
STATUS_IDLE_CYCLE_GATE = "idle_cycle_gate"
STATUS_NO_DATA = "no_data"


@dataclass(frozen=True)
class WorkerHealth:
    """Immutable snapshot of one worker's liveness state.

    Returned by ``WorkerLivenessTracker.snapshot()``. Consumers (the
    watchdog, ``/health`` Telegram handler, future Prometheus exporter)
    read these fields; nothing mutates them.

    Attributes:
        name: Worker name (matches BaseWorker.name).
        tier: Layer tier tag (``"LAYER1A"``, ``"LAYER1B"``, ...) or None
            for non-tier-tagged workers (telegram_bot_worker, watchdogs).
        cycle_gated: Whether this worker skips ticks when
            ``LayerManager.is_cycle_active()`` is False. True for the
            5 cycle_gated workers (structure/signal/regime/strategy/
            scanner); False for everyone else.
        expected_interval_s: Worker's configured tick interval. Used to
            compute the "overdue" threshold via ``overdue_multiplier``.
            ``None`` for sweet-spot workers whose effective interval
            depends on the schedule (treated as window_minutes * 60).
        wm_start_ts: Unix timestamp at which ``WorkerManager._run_worker``
            registered the worker (just before ``await worker.start()``).
        first_tick_ts: Timestamp of the FIRST successful tick after
            registration, or ``None`` if no tick has happened yet.
        last_tick_ts: Timestamp of the most recent successful tick, or
            ``None`` if no tick has happened yet.
        tick_count: Total successful ticks since registration.
        sweet_spot_fires: Total ``SWEET_SPOT_FIRED`` events recorded for
            this worker (only meaningful for SweetSpotWorker subclasses;
            zero for fixed-interval BaseWorker).
        elapsed_since_start_s: Wall-clock seconds since ``wm_start_ts``.
        last_tick_age_s: Seconds since ``last_tick_ts`` if any tick has
            happened, else ``None``.
        status: One of ``STATUS_HEALTHY``, ``STATUS_NEVER_TICKED``,
            ``STATUS_OVERDUE``, ``STATUS_IDLE_CYCLE_GATE``,
            ``STATUS_NO_DATA``.
        status_reason: Human-readable description of the status,
            suitable for /health rendering.
    """
    name: str
    tier: str | None
    cycle_gated: bool
    expected_interval_s: float | None
    wm_start_ts: float
    first_tick_ts: float | None
    last_tick_ts: float | None
    tick_count: int
    sweet_spot_fires: int
    elapsed_since_start_s: float
    last_tick_age_s: float | None
    status: str
    status_reason: str


class WorkerLivenessTracker:
    """Tracks per-worker liveness state for the watchdog and /health.

    See module docstring for the design rationale and threading model.
    """

    def __init__(self) -> None:
        # name → mutable state record. Each record is a dict so individual
        # field updates don't require copying the whole structure.
        self._workers: dict[str, dict] = {}

    # ─── Registration ───

    def register(
        self,
        name: str,
        *,
        expected_interval_s: float | None,
        cycle_gated: bool,
        tier: str | None,
    ) -> None:
        """Register a worker. Idempotent on repeat calls (re-registers).

        Called from ``WorkerManager._run_worker`` immediately before the
        ``WM_START`` log so ``elapsed_since_start_s`` math is consistent
        with what the operator sees in workers.log.

        Args:
            name: Worker name (BaseWorker.name).
            expected_interval_s: Tick interval. Pass ``None`` for
                sweet-spot workers whose interval is window-driven.
            cycle_gated: Whether this worker honours the L2-AND-L3
                cycle gate (i.e. ``BaseWorker.cycle_gated``).
            tier: Layer tier tag (``"LAYER1A"`` etc.) or ``None``.
        """
        now = time.time()
        self._workers[name] = {
            "name": name,
            "tier": tier,
            "cycle_gated": cycle_gated,
            "expected_interval_s": expected_interval_s,
            "wm_start_ts": now,
            "first_tick_ts": None,
            "last_tick_ts": None,
            "tick_count": 0,
            "sweet_spot_fires": 0,
        }

    def deregister(self, name: str) -> None:
        """Remove a worker. Used when workers stop permanently."""
        self._workers.pop(name, None)

    # ─── Recording ───

    def record_tick(self, name: str) -> None:
        """Record a successful tick. Fast (no I/O, no logging).

        Called from ``BaseWorker.start`` (and ``SweetSpotWorker.start``)
        AFTER the tick body returns successfully and AFTER the existing
        ``WORKER_FIRST_TICK`` / ``LAYER1*_TICK_DONE`` logs so the
        ordering between observability events is deterministic.
        """
        rec = self._workers.get(name)
        if rec is None:
            # Untracked worker — silently ignore. The caller is
            # presumed to be a new BaseWorker subclass that hasn't
            # been wired through the manager yet; logging a warning
            # here would cause a flood until wiring is fixed.
            return
        now = time.time()
        if rec["first_tick_ts"] is None:
            rec["first_tick_ts"] = now
        rec["last_tick_ts"] = now
        rec["tick_count"] += 1

    def record_sweet_spot(self, name: str) -> None:
        """Record a ``SWEET_SPOT_FIRED`` event.

        Called from ``SweetSpotScheduler.wait_for_sweet_spot`` after
        the existing ``SWEET_SPOT_FIRED`` log. Lets the watchdog
        annotate ``WORKER_NEVER_TICKED`` events with how many fires
        the worker missed (useful diagnostic when comparing the dead
        workers' ``sweet_spot_fires=N`` vs ``tick_count=0``).
        """
        rec = self._workers.get(name)
        if rec is None:
            return
        rec["sweet_spot_fires"] += 1

    # ─── Snapshot / queries ───

    def snapshot(self) -> list[WorkerHealth]:
        """Return immutable snapshots of every registered worker.

        Note: ``status`` and ``status_reason`` use a pessimistic default
        because this overload doesn't know whether the cycle is active.
        Callers that need cycle-gate-aware status should use
        :meth:`is_alive` per worker.
        """
        return [
            self._build_health(rec, cycle_active=True)
            for rec in self._workers.values()
        ]

    def snapshot_with_cycle(self, cycle_active: bool) -> list[WorkerHealth]:
        """Return cycle-gate-aware snapshots.

        Equivalent to calling :meth:`snapshot` then re-classifying each
        cycle_gated worker by ``is_alive(name, cycle_active=...)``.
        """
        return [
            self._build_health(rec, cycle_active=cycle_active)
            for rec in self._workers.values()
        ]

    def is_alive(
        self,
        name: str,
        *,
        cycle_active: bool,
        grace_s: float = 90.0,
        overdue_mult: float = 2.0,
    ) -> tuple[bool, str]:
        """Return ``(alive, status)`` for one worker.

        Args:
            name: Worker name.
            cycle_active: Live value of
                ``LayerManager.is_cycle_active()`` at probe time.
            grace_s: Grace period from registration before
                ``never_ticked`` fires (default 90 s — matches the
                config default for the watchdog). For sweet-spot
                workers whose ``expected_interval_s`` exceeds this
                value, the EFFECTIVE grace is widened to
                ``expected_interval_s + grace_s`` so the watchdog
                doesn't false-alarm during the boot window while a
                sweet-spot worker waits for its first scheduled fire.
                The 06:18 reference boot had structure_worker first
                ticking at 158 s, scanner_worker at 352 s — both
                healthy but a naive grace=90 would have alarmed on
                every one of them.
            overdue_mult: Multiplier on ``expected_interval_s``; when
                ``last_tick_age_s`` exceeds this, the worker is
                ``overdue``.

        Returns:
            ``(alive, status)`` where ``alive`` is True for healthy and
            for cycle-gate-idle workers (the latter is intentionally
            silent), and False for ``never_ticked`` and ``overdue``.
            ``status`` is one of the ``STATUS_*`` constants.
        """
        rec = self._workers.get(name)
        if rec is None:
            return False, STATUS_NO_DATA

        now = time.time()
        elapsed = now - rec["wm_start_ts"]
        first = rec["first_tick_ts"]
        last = rec["last_tick_ts"]
        cycle_gated = rec["cycle_gated"]
        expected = rec["expected_interval_s"]

        # Effective grace: SweetSpotWorker workers (interval ≥ ~300 s)
        # need at least one full window plus the user-configured grace
        # before NEVER_TICKED is meaningful. Otherwise a healthy
        # 5-min worker that waits ~240 s for its first sweet-spot
        # would emit a false alarm at every boot.
        if expected is not None and expected > grace_s:
            effective_grace = expected + grace_s
        else:
            effective_grace = grace_s

        # Cycle-gate aware: cycle_gated worker without a first tick is
        # only alarming if the cycle is active. When the cycle is OFF
        # (operator hasn't toggled L3 yet, or has explicitly stopped
        # trading), 1B/1C/1D workers SHOULD be silent — that's their
        # design. Do not alarm.
        if cycle_gated and not cycle_active and first is None:
            return True, STATUS_IDLE_CYCLE_GATE

        # First tick not yet observed beyond the (effective) grace
        # window → genuine never-ticked. Either the worker is hung
        # (not the cycle-gate case we ruled out above) or the cycle
        # was active long enough for the grace to elapse without
        # producing a tick.
        if first is None and elapsed > effective_grace:
            return False, STATUS_NEVER_TICKED

        # No tick yet, still within (effective) grace.
        if first is None:
            return True, STATUS_HEALTHY

        # First tick observed; check overdue against expected interval.
        # Sweet-spot workers without an explicit interval default to
        # 5 min × overdue_mult so we don't false-alarm on the
        # window-driven cadence.
        if last is not None and expected is not None:
            tick_age = now - last
            if tick_age > expected * overdue_mult:
                # Cycle-gated workers can legitimately go quiet when the
                # cycle flips off after a tick. Mirror the never_ticked
                # logic: don't alarm if currently gated.
                if cycle_gated and not cycle_active:
                    return True, STATUS_IDLE_CYCLE_GATE
                return False, STATUS_OVERDUE

        return True, STATUS_HEALTHY

    # ─── Internal ───

    def _build_health(self, rec: dict, *, cycle_active: bool) -> WorkerHealth:
        now = time.time()
        elapsed = now - rec["wm_start_ts"]
        last = rec["last_tick_ts"]
        last_age = (now - last) if last is not None else None
        alive, status = self.is_alive(
            rec["name"], cycle_active=cycle_active,
        )
        reason = self._reason_for(status, rec, elapsed, last_age, cycle_active)
        return WorkerHealth(
            name=rec["name"],
            tier=rec["tier"],
            cycle_gated=rec["cycle_gated"],
            expected_interval_s=rec["expected_interval_s"],
            wm_start_ts=rec["wm_start_ts"],
            first_tick_ts=rec["first_tick_ts"],
            last_tick_ts=rec["last_tick_ts"],
            tick_count=rec["tick_count"],
            sweet_spot_fires=rec["sweet_spot_fires"],
            elapsed_since_start_s=elapsed,
            last_tick_age_s=last_age,
            status=status,
            status_reason=reason,
        )

    @staticmethod
    def _reason_for(
        status: str,
        rec: dict,
        elapsed: float,
        last_age: float | None,
        cycle_active: bool,
    ) -> str:
        if status == STATUS_HEALTHY:
            if rec["tick_count"] == 0:
                return f"within grace ({elapsed:.0f}s since start)"
            return f"last tick {last_age:.0f}s ago, {rec['tick_count']} total"
        if status == STATUS_NEVER_TICKED:
            return (
                f"started {elapsed:.0f}s ago, "
                f"sweet_spot_fires={rec['sweet_spot_fires']}, "
                f"no successful tick"
            )
        if status == STATUS_OVERDUE:
            return (
                f"last tick {last_age:.0f}s ago, "
                f"expected_interval_s={rec['expected_interval_s']:.0f}"
            )
        if status == STATUS_IDLE_CYCLE_GATE:
            return (
                f"cycle_gated worker, cycle_active={cycle_active} "
                f"(L2 AND L3 must be ON to tick)"
            )
        return "no data"


# ─── Module-level singleton ───
#
# WorkerManager constructs the canonical instance and stores it in
# ``self._services["worker_liveness"]`` for downstream injection. Tests
# build their own instances; that's why this is a class, not a global.
_default_tracker: WorkerLivenessTracker | None = None


def get_default_tracker() -> WorkerLivenessTracker:
    """Return the process-wide singleton, lazily constructed.

    Used by callers that don't have a direct handle (e.g. the
    sweet-spot scheduler, which is constructed before the tracker is
    wired). Production code SHOULD prefer dependency injection where
    possible to keep tests clean.
    """
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = WorkerLivenessTracker()
    return _default_tracker


def set_default_tracker(tracker: WorkerLivenessTracker | None) -> None:
    """Replace the process-wide singleton.

    Tests use ``set_default_tracker(None)`` to reset between cases.
    Production wires the WorkerManager-owned tracker via
    ``set_default_tracker(self._worker_liveness)`` once at construction.
    """
    global _default_tracker
    _default_tracker = tracker
