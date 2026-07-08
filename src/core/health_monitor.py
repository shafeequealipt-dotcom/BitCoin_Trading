"""System-level health probe (Phase 11 of the logging overhaul).

Distinct from `src/workers/health.py::WorkerHealthMonitor`, which aggregates
per-worker tick/error counters via `BaseWorker.get_status()`. This module
probes the *runtime process itself* — event-loop responsiveness, asyncio task
count, process memory, CPU — so degradations that starve every worker at once
(event loop congestion, memory leaks, runaway tasks) are visible as dedicated
`SYSTEM_HEALTH` log lines instead of being inferred from downstream symptoms.

Lifecycle: WorkerManager instantiates one `SystemHealthMonitor` and launches
`start_all()`'s periodic health loop (every 60 s). Each `check()` emits one
`SYSTEM_HEALTH` info line and, if applicable, threshold warnings:
  * `EVENT_LOOP_LAG`  — `asyncio.sleep(0)` took > 100 ms
  * `MEMORY_HIGH`     — RSS > 1 GB

`psutil` is imported lazily. If unavailable, memory/CPU fields report `-1`
and the probe still logs loop lag + task count (the primary degradation
signals).
"""

from __future__ import annotations

import asyncio
import os
import time

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("core")


class SystemHealthMonitor:
    """Lightweight event-loop / process health probe.

    Designed to run inside the existing asyncio event loop (not a thread), so
    the loop-lag measurement reflects the same scheduler that every trading
    coroutine uses. `check()` is cheap (~ns for sleep(0); one psutil call for
    memory/CPU). Safe to schedule every 60 s without backpressure.
    """

    # Thresholds for alarm-level logs (tunable in settings if needed later)
    LAG_WARN_MS: float = 100.0
    MEMORY_WARN_MB: float = 1000.0
    # Phase 5 (P0-4): when loop lag crosses this severe-tier threshold,
    # we additionally enumerate the top blocking tasks so operators can
    # name the offender instead of grepping for it.
    LAG_SEVERE_MS: float = 500.0

    def __init__(self) -> None:
        self._pid: int = os.getpid()
        self._psutil_process = None
        try:
            import psutil  # type: ignore[import-not-found]
            self._psutil_process = psutil.Process(self._pid)
        except Exception as _e:
            # Graceful degrade — matches dashboard_handler.py's psutil-usage pattern.
            # loop_lag + task-count still work via asyncio only.
            log.warning(
                f"SYSTEM_HEALTH_INIT | psutil_unavailable err='{str(_e)[:80]}' "
                f"| memory/cpu fields will report -1 | {ctx()}"
            )

    async def check(self) -> None:
        """Run one health check and emit SYSTEM_HEALTH.

        Safe against all exceptions — a malfunction here must never propagate
        out and disturb the main event loop.
        """
        # ── Event-loop lag ──
        # asyncio.sleep(0) yields control back to the scheduler. If the loop
        # is healthy this returns in microseconds; > 100 ms means other
        # coroutines are hogging the loop (the exact symptom watchdog ticks
        # and brain cycles see as inflation).
        _t0 = time.time()
        try:
            await asyncio.sleep(0)
        except Exception:
            pass
        lag_ms = (time.time() - _t0) * 1000

        # ── Active asyncio tasks ──
        try:
            tasks = [t for t in asyncio.all_tasks() if not t.done()]
            active_tasks = len(tasks)
        except Exception:
            active_tasks = -1

        # ── Memory + CPU (psutil — may be unavailable) ──
        mem_mb = -1.0
        cpu_pct = -1.0
        if self._psutil_process is not None:
            try:
                mem_info = self._psutil_process.memory_info()
                mem_mb = mem_info.rss / 1024 / 1024
            except Exception:
                pass
            try:
                # interval=0 returns cached percentage without blocking;
                # first call may report 0 until the second call.
                cpu_pct = self._psutil_process.cpu_percent(interval=0)
            except Exception:
                pass

        log.info(
            f"SYSTEM_HEALTH | loop_lag={lag_ms:.1f}ms tasks={active_tasks} "
            f"mem={mem_mb:.0f}MB cpu={cpu_pct:.0f}% pid={self._pid} | {ctx()}"
        )

        if lag_ms > self.LAG_WARN_MS:
            log.warning(
                f"EVENT_LOOP_LAG | lag={lag_ms:.0f}ms (>{self.LAG_WARN_MS:.0f}ms) "
                f"tasks={active_tasks} — event loop congested | {ctx()}"
            )

        # Phase 5 (P0-4): on severe lag, name the top blocking tasks so
        # operators don't have to guess. Best-effort — failures are
        # swallowed because this is observability, not control flow.
        if lag_ms > self.LAG_SEVERE_MS:
            try:
                top = self._top_blocking_tasks(n=3)
                log.warning(
                    f"EVENT_LOOP_BLOCKER | lag={lag_ms:.0f}ms "
                    f"top_tasks=[{','.join(top) or 'unknown'}] | {ctx()}"
                )
            except Exception as e:
                log.debug(
                    "EVENT_LOOP_BLOCKER enumeration failed: {err}", err=str(e),
                )

        if mem_mb > self.MEMORY_WARN_MB:
            log.warning(
                f"MEMORY_HIGH | mem={mem_mb:.0f}MB (>{self.MEMORY_WARN_MB:.0f}MB) "
                f"pid={self._pid} — possible memory leak | {ctx()}"
            )

    def _top_blocking_tasks(self, n: int = 3) -> list[str]:
        """Return the names of the top-N currently-stacked asyncio tasks.

        Best-effort identification of which coroutine is hogging the loop
        when ``EVENT_LOOP_LAG`` fires above the severe threshold. We use
        ``task.get_stack()`` length as a proxy for "currently doing work"
        because there's no portable API to ask "which task is running".
        Tasks that are merely awaiting will have shallow stacks; tasks
        running CPU-bound code (or blocking syscalls) are deeper.
        """
        try:
            tasks = [t for t in asyncio.all_tasks() if not t.done()]
        except Exception:
            return []
        scored: list[tuple[int, str]] = []
        for t in tasks:
            try:
                depth = len(t.get_stack(limit=64))
            except Exception:
                depth = 0
            try:
                name = t.get_name()
            except Exception:
                name = repr(t)[:40]
            # Skip the health monitor's own task — it's always running.
            if name and "health" not in name.lower():
                scored.append((depth, name))
        scored.sort(reverse=True)
        return [name for _depth, name in scored[:n]]
