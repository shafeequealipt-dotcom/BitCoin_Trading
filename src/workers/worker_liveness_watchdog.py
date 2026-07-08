"""Phase 11 (dead-workers fix) — periodic liveness watchdog worker.

Always-on Layer 1A worker that probes the WorkerLivenessTracker every
``watchdog_interval_sec`` seconds and emits structured warnings (plus
optional Telegram alerts) when a registered worker has not produced
its first tick within ``first_tick_grace_sec``, or has gone quiet for
``overdue_multiplier × expected_interval_s`` after its first tick.

Critical design constraints:

  * Self-contained dependencies. The watchdog must not import any
    service that the dead workers depend on (TACache, ShadowKlineReader,
    StructureEngine, MarketRepository) — its purpose is to detect when
    those are misbehaving, so depending on them would defeat the
    point. Tracker + LayerManager handle (for is_cycle_active) +
    optional AlertManager are the only collaborators.

  * Cycle-gate aware. The 5 cycle_gated workers
    (structure/signal/regime/strategy/scanner) are intentionally silent
    when ``LayerManager.is_cycle_active() = L2 AND L3`` is False.
    Alarming on those during normal L3=OFF operation would produce
    continuous alarm fatigue. This worker passes the live cycle_active
    value to the tracker so its is_alive() classification accounts
    for the gate.

  * Rate-limited Telegram alerts. Same worker can fail repeatedly;
    we want one alert per worker per ``alert_rate_limit_sec`` window
    (default 1 hour) so the operator's phone doesn't melt.

  * Heartbeat at INFO every tick. ``WORKER_LIVENESS_HEARTBEAT |
    total=N healthy=H dead=D overdue=O idle=I`` is unconditional so
    operators have a continuous trail in workers.log. The per-worker
    WARNING tags only fire when something is actually wrong.

Investigation: ``dev_notes/phase0_dead_workers_capture.md``,
``dev_notes/phase1_dead_workers_investigation/phase1_summary.md``.
"""

from __future__ import annotations

import time

from src.config.settings import Settings
from src.core.log_context import ctx, new_watchdog_id
from src.core.logging import get_logger
from src.core.worker_liveness import (
    STATUS_HEALTHY,
    STATUS_IDLE_CYCLE_GATE,
    STATUS_NEVER_TICKED,
    STATUS_OVERDUE,
    WorkerHealth,
    WorkerLivenessTracker,
)
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker_liveness")


class WorkerLivenessWatchdog(BaseWorker):
    """Layer 1A always-on worker that monitors per-worker liveness.

    Args:
        settings: Application settings (only used for BaseWorker plumbing
            — interval / max_restarts come from constructor kwargs so the
            watchdog isn't blocked by a malformed Settings).
        db: Database manager (required by BaseWorker; the watchdog does
            not touch it).
        tracker: The liveness tracker to read from. Production wires the
            WorkerManager-owned singleton.
        watchdog_interval_sec: How often the watchdog probes (seconds).
            Default 30 — must be < ``first_tick_grace_sec`` so a
            fresh-boot dead worker is detected within
            ``first_tick_grace_sec + watchdog_interval_sec``.
        first_tick_grace_sec: Grace window from ``WM_START`` before
            ``WORKER_NEVER_TICKED`` fires. Default 90 — covers the
            slowest first-tick latency observed in production
            (scanner_worker @ 352 s in the 06:18 boot — 90 s only
            covers the OTHER four workers; the operator can raise
            this for environments where scanner reliably needs
            > 90 s, or accept that scanner_worker emits one
            NEVER_TICKED alarm at boot before its first sweet-spot).
        overdue_multiplier: Multiplier on ``expected_interval_s``;
            when ``last_tick_age_s`` exceeds this, the worker is
            ``WORKER_TICK_OVERDUE``. Default 2.0.
        alert_rate_limit_sec: Minimum seconds between Telegram
            alerts for the same worker name. Default 3600.
        alert_manager: Optional AlertManager for Telegram dispatch.
            ``None`` → log-only mode (still emits structured warnings,
            but no alert).
    """

    # Utility worker, no layer_tier — matches cleanup_worker convention.
    # cycle_gated stays at the BaseWorker default of False so the
    # watchdog runs irrespective of the L3 gate (it MUST run when L3
    # is off — that's the state it needs to differentiate from).

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        *,
        tracker: WorkerLivenessTracker,
        watchdog_interval_sec: float = 30.0,
        first_tick_grace_sec: float = 90.0,
        overdue_multiplier: float = 2.0,
        alert_rate_limit_sec: float = 3600.0,
        alert_manager=None,
    ) -> None:
        super().__init__(
            name="worker_liveness_watchdog",
            interval_seconds=float(watchdog_interval_sec),
            settings=settings,
            db=db,
        )
        self._tracker = tracker
        self._grace_s = float(first_tick_grace_sec)
        self._overdue_mult = float(overdue_multiplier)
        self._alert_rate_limit_s = float(alert_rate_limit_sec)
        self._alert_manager = alert_manager
        # name → unix ts of last alert sent. Used to rate-limit per-
        # worker so a stuck worker doesn't trigger 120 alerts per hour.
        self._last_alert_ts: dict[str, float] = {}

    async def tick(self) -> None:
        """One liveness probe: classify every worker and emit accordingly."""
        # Each tick gets a wid for log correlation across the heartbeat
        # event and any per-worker WARNING events.
        new_watchdog_id()

        cycle_active = self._is_cycle_active()
        snapshots = self._tracker.snapshot_with_cycle(cycle_active=cycle_active)

        counts = {
            "total": len(snapshots),
            "healthy": 0,
            "never_ticked": 0,
            "overdue": 0,
            "idle_cycle_gate": 0,
        }

        for h in snapshots:
            if h.status == STATUS_HEALTHY:
                counts["healthy"] += 1
            elif h.status == STATUS_NEVER_TICKED:
                counts["never_ticked"] += 1
                self._emit_never_ticked(h, cycle_active=cycle_active)
            elif h.status == STATUS_OVERDUE:
                counts["overdue"] += 1
                self._emit_overdue(h, cycle_active=cycle_active)
            elif h.status == STATUS_IDLE_CYCLE_GATE:
                counts["idle_cycle_gate"] += 1

        log.info(
            f"WORKER_LIVENESS_HEARTBEAT | "
            f"total={counts['total']} "
            f"healthy={counts['healthy']} "
            f"never_ticked={counts['never_ticked']} "
            f"overdue={counts['overdue']} "
            f"idle_cycle_gate={counts['idle_cycle_gate']} "
            f"cycle_active={cycle_active} | {ctx()}"
        )

    # ─── Internal ───

    def _is_cycle_active(self) -> bool:
        """Return ``LayerManager.is_cycle_active()`` or True if not wired.

        Defaulting to True when LayerManager is not yet attached means
        the watchdog will alarm on cycle_gated workers during the brief
        boot window before WorkerManager wires _layer_manager. That's
        the right default — a wiring oversight is a real bug, and the
        boot window is short (<5s).
        """
        lm = getattr(self, "_layer_manager", None)
        if lm is None or not hasattr(lm, "is_cycle_active"):
            return True
        try:
            return bool(lm.is_cycle_active())
        except Exception:
            # Conservative default if the LM probe somehow raises:
            # report cycle_active=True so we don't silently mute alarms
            # by mistakenly thinking the cycle is off.
            return True

    def _emit_never_ticked(
        self, h: WorkerHealth, *, cycle_active: bool,
    ) -> None:
        # T6-2 / F6 fix (six-tier-fixes 2026-05-11) — downgrade the log
        # level when this fires for cycle-gated workers waiting for the
        # cold-start M5 sweet-spot (cycle_active is False AND
        # cycle_gated is True). That's expected boot-window behavior
        # per the cold-start gate fix (memory:
        # project_cold_start_resume_fix.md) and a WARN-level emission
        # was confusing operator dashboards. Genuine never-ticked
        # failures (cycle_active=True but worker still has tick_count=0)
        # remain at WARN so they surface in alerts.
        if h.cycle_gated and not cycle_active:
            log.info(
                f"WORKER_NEVER_TICKED | name={h.name} "
                f"tier={h.tier} cycle_gated={h.cycle_gated} "
                f"elapsed_since_start_s={h.elapsed_since_start_s:.0f} "
                f"sweet_spot_fires={h.sweet_spot_fires} "
                f"cycle_active={cycle_active} severity=expected_cold_start "
                f"| {ctx()}"
            )
        else:
            log.warning(
                f"WORKER_NEVER_TICKED | name={h.name} "
                f"tier={h.tier} cycle_gated={h.cycle_gated} "
                f"elapsed_since_start_s={h.elapsed_since_start_s:.0f} "
                f"sweet_spot_fires={h.sweet_spot_fires} "
                f"cycle_active={cycle_active} | {ctx()}"
            )
        self._maybe_telegram_alert(
            name=h.name,
            severity_text="WORKER_NEVER_TICKED",
            detail=(
                f"{h.name} (tier={h.tier}) registered "
                f"{h.elapsed_since_start_s:.0f}s ago with "
                f"{h.sweet_spot_fires} sweet-spot fires but no "
                f"successful tick. cycle_active={cycle_active}, "
                f"cycle_gated={h.cycle_gated}."
            ),
        )

    def _emit_overdue(
        self, h: WorkerHealth, *, cycle_active: bool,
    ) -> None:
        last_age = h.last_tick_age_s if h.last_tick_age_s is not None else -1.0
        log.warning(
            f"WORKER_TICK_OVERDUE | name={h.name} "
            f"tier={h.tier} cycle_gated={h.cycle_gated} "
            f"last_tick_age_s={last_age:.0f} "
            f"expected_interval_s={h.expected_interval_s} "
            f"tick_count={h.tick_count} "
            f"cycle_active={cycle_active} | {ctx()}"
        )
        self._maybe_telegram_alert(
            name=h.name,
            severity_text="WORKER_TICK_OVERDUE",
            detail=(
                f"{h.name} last ticked {last_age:.0f}s ago; "
                f"expected every {h.expected_interval_s}s. "
                f"tick_count={h.tick_count}. "
                f"cycle_active={cycle_active}."
            ),
        )

    def _maybe_telegram_alert(
        self, *, name: str, severity_text: str, detail: str,
    ) -> None:
        """Send a Telegram alert if the rate-limit window has elapsed.

        No-ops when ``_alert_manager`` is None (log-only mode).
        """
        if self._alert_manager is None:
            return
        now = time.time()
        last = self._last_alert_ts.get(name, 0.0)
        if now - last < self._alert_rate_limit_s:
            return
        self._last_alert_ts[name] = now
        # AlertManager.send_error_alert is async; schedule fire-and-
        # forget so the watchdog tick doesn't block on Telegram I/O.
        # Failure to enqueue is logged but does not raise.
        import asyncio
        try:
            asyncio.create_task(
                self._alert_manager.send_error_alert(
                    component="worker_liveness",
                    error_message=f"{severity_text}: {detail}",
                ),
                name=f"worker_liveness_alert_{name}",
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(
                f"WORKER_LIVENESS_ALERT_FAIL | name={name} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )
