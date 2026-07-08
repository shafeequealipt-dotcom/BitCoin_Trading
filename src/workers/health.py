"""Worker health monitoring: tracks status of all workers and provides system health."""

from src.core.logging import get_logger
from src.core.types import WorkerStatus
from src.core.utils import now_utc

log = get_logger("worker")


class WorkerHealthMonitor:
    """Tracks health of all registered workers.

    Provides system-wide health status and per-worker detail.
    """

    def __init__(self) -> None:
        self.workers: dict = {}
        self.start_time = now_utc()

    def register(self, worker) -> None:
        """Add a worker to monitoring.

        Args:
            worker: BaseWorker instance.
        """
        self.workers[worker.name] = worker

    def get_system_health(self) -> dict:
        """Get comprehensive health report for all workers.

        Returns:
            Dict with status, uptime, per-worker details, and aggregates.
        """
        uptime = (now_utc() - self.start_time).total_seconds()
        worker_statuses = {}
        running = 0
        stopped = 0
        error = 0
        total_ticks = 0
        total_errors = 0

        for name, w in self.workers.items():
            status = w.get_status()
            worker_statuses[name] = status
            total_ticks += status["total_ticks"]
            total_errors += status["error_count"]

            ws = w.status
            if ws == WorkerStatus.RUNNING:
                running += 1
            elif ws == WorkerStatus.STOPPED:
                stopped += 1
            elif ws in (WorkerStatus.ERROR, WorkerStatus.RESTARTING):
                error += 1

        total = len(self.workers)
        if total == 0:
            sys_status = "healthy"
        elif running == total:
            sys_status = "healthy"
        elif stopped > total / 2:
            sys_status = "critical"
        else:
            sys_status = "degraded"

        error_rate = (total_errors / total_ticks * 100) if total_ticks > 0 else 0.0

        return {
            "status": sys_status,
            "uptime_seconds": round(uptime, 1),
            "workers": worker_statuses,
            "total_workers": total,
            "running_workers": running,
            "stopped_workers": stopped,
            "error_workers": error,
            "total_ticks": total_ticks,
            "total_errors": total_errors,
            "error_rate_pct": round(error_rate, 2),
        }

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if all workers are running.
        """
        return all(w.status == WorkerStatus.RUNNING for w in self.workers.values())

    def get_worker_status(self, name: str) -> dict:
        """Get status for a specific worker.

        Args:
            name: Worker name.

        Returns:
            Worker status dict, or error dict if not found.
        """
        w = self.workers.get(name)
        if w is None:
            return {"error": f"Worker '{name}' not found"}
        return w.get_status()
