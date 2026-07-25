"""Brain Liveness Watchdog (2026-07-25) — detects a hung trading-brain process.

Runs inside ``trading-workers`` (a separate OS process from ``trading-brain``)
and periodically checks the heartbeat file that ``LayerManager._brain_review_loop``
writes at the top of every A/B cycle iteration (see
``src/core/brain_liveness.py`` for the full rationale and
``src/core/layer_manager.py::_write_brain_heartbeat`` for the write side).

Why cross-process instead of extending WorkerLivenessTracker: that tracker
lives entirely in-process (an in-memory dict), and trading-brain is a
different systemd unit with its own event loop — it has zero visibility
into trading-brain and vice versa. A shared heartbeat file (on the same
disk both processes already read/write ``data/*.json`` state to) is the
simplest correct bridge.

Design mirrors WorkerLivenessWatchdog (src/workers/worker_liveness_watchdog.py)
deliberately — same heartbeat-every-tick + rate-limited-alert-on-problem
shape, same log/alert conventions — so operators reading workers.log see one
consistent pattern rather than two different alerting styles.

Detect-and-alert only, no auto-restart: consistent with WorkerLivenessWatchdog,
which also only alerts. Granting this process the ability to `systemctl
restart` another service would be a new privilege escalation this file does
not introduce — an operator (or a future explicit change) restarts the
hung process.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.config.settings import Settings
from src.core.brain_liveness import (
    VERDICT_STALE,
    VERDICT_UNKNOWN,
    evaluate_brain_liveness,
)
from src.core.log_context import ctx, new_watchdog_id
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker_liveness")

_HEARTBEAT_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "brain_heartbeat.json"


class BrainLivenessWatchdog(BaseWorker):
    """Layer 1A always-on worker that monitors the trading-brain process.

    Args:
        settings: Application settings (BaseWorker plumbing only — the
            staleness threshold is passed explicitly so a malformed
            Settings object can't silently disable the watchdog).
        db: Database manager (required by BaseWorker; unused here, same
            as WorkerLivenessWatchdog).
        watchdog_interval_sec: How often this worker checks the heartbeat
            file (seconds). Default 60.
        staleness_threshold_sec: Precomputed via
            ``compute_staleness_threshold`` from the brain's own
            ``strategic_interval`` — see that function's docstring for
            why this must NOT be a hardcoded constant.
        alert_rate_limit_sec: Minimum seconds between Telegram alerts for
            a stale-brain condition, so a still-stuck process doesn't
            re-alert every tick. Default 3600.
        alert_manager: Optional AlertManager for Telegram dispatch.
            ``None`` -> log-only mode.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        *,
        watchdog_interval_sec: float = 60.0,
        staleness_threshold_sec: float = 6750.0,
        alert_rate_limit_sec: float = 3600.0,
        alert_manager=None,
    ) -> None:
        super().__init__(
            name="brain_liveness_watchdog",
            interval_seconds=float(watchdog_interval_sec),
            settings=settings,
            db=db,
        )
        self._threshold_s = float(staleness_threshold_sec)
        self._alert_rate_limit_s = float(alert_rate_limit_sec)
        self._alert_manager = alert_manager
        self._last_alert_ts: float = 0.0
        # Tracks the last-seen loop_iteration so a heartbeat that keeps
        # re-writing the SAME iteration number (impossible with the current
        # writer, but a cheap extra signal if that ever regresses) could be
        # distinguished from genuine progress in a future enhancement.
        self._last_seen_iteration: int | None = None

    async def tick(self) -> None:
        """One liveness probe: read the heartbeat file and classify."""
        new_watchdog_id()

        heartbeat_ts, iteration = self._read_heartbeat()
        result = evaluate_brain_liveness(
            heartbeat_timestamp=heartbeat_ts,
            now=time.time(),
            threshold_seconds=self._threshold_s,
        )

        log.info(
            f"BRAIN_LIVENESS_HEARTBEAT | verdict={result.verdict} "
            f"age_s={result.age_seconds if result.age_seconds is not None else 'NA'} "
            f"threshold_s={result.threshold_seconds:.0f} "
            f"loop_iteration={iteration if iteration is not None else 'NA'} | {ctx()}"
        )

        if result.verdict == VERDICT_STALE:
            self._emit_stale(result, iteration)
        elif result.verdict == VERDICT_UNKNOWN:
            # Only WARN if this isn't the expected brief window right after
            # boot (before the brain's first loop iteration has had a
            # chance to write). A cheap heuristic: unknown persisting past
            # one full staleness_threshold_sec worth of watchdog uptime is
            # no longer "just booted" — it means the heartbeat file was
            # never created at all (e.g. trading-brain isn't running, or a
            # code path never reaches the loop).
            uptime_s = (
                (now_utc() - self._start_time).total_seconds()
                if self._start_time else 0.0
            )
            if uptime_s > self._threshold_s:
                log.warning(
                    f"BRAIN_LIVENESS_UNKNOWN | reason={result.reason} "
                    f"watchdog_uptime_s={uptime_s:.0f} | {ctx()}"
                )

        self._last_seen_iteration = iteration

    # ─── Internal ───

    def _read_heartbeat(self) -> tuple[float | None, int | None]:
        """Read the heartbeat file. Returns (timestamp, loop_iteration).

        Best-effort: any read/parse failure returns (None, None), which
        ``evaluate_brain_liveness`` treats as VERDICT_UNKNOWN (fail-open —
        a transient read race during the writer's atomic rename must never
        itself trigger a stale-brain alarm).
        """
        try:
            if not _HEARTBEAT_FILE.exists():
                return None, None
            data = json.loads(_HEARTBEAT_FILE.read_text())
            ts = data.get("timestamp")
            it = data.get("loop_iteration")
            return (float(ts) if ts is not None else None,
                    int(it) if it is not None else None)
        except Exception as e:
            log.debug(f"BRAIN_HEARTBEAT_READ_FAIL | err='{str(e)[:100]}' | {ctx()}")
            return None, None

    def _emit_stale(self, result, iteration: int | None) -> None:
        log.warning(
            f"BRAIN_LIVENESS_STALE | age_s={result.age_seconds:.0f} "
            f"threshold_s={result.threshold_seconds:.0f} "
            f"loop_iteration={iteration if iteration is not None else 'NA'} | {ctx()}"
        )
        self._maybe_telegram_alert(
            detail=(
                f"trading-brain heartbeat is {result.age_seconds:.0f}s old "
                f"(threshold {result.threshold_seconds:.0f}s) — the process "
                f"may be hung (alive per systemd but not progressing). "
                f"Last known loop_iteration={iteration if iteration is not None else 'unknown'}. "
                f"Consider: sudo systemctl restart trading-brain"
            ),
        )

    def _maybe_telegram_alert(self, *, detail: str) -> None:
        """Send a rate-limited Telegram alert. No-ops when alert_manager is None."""
        if self._alert_manager is None:
            return
        now = time.time()
        if now - self._last_alert_ts < self._alert_rate_limit_s:
            return
        self._last_alert_ts = now
        import asyncio
        try:
            asyncio.create_task(
                self._alert_manager.send_error_alert(
                    component="brain_liveness",
                    error_message=f"BRAIN_LIVENESS_STALE: {detail}",
                ),
                name="brain_liveness_alert",
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(f"BRAIN_LIVENESS_ALERT_FAIL | err='{str(e)[:80]}' | {ctx()}")
