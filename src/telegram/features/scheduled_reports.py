"""Scheduled Reports: configurable auto-report engine."""

import time

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.telegram_repo import TelegramRepository
from src.telegram.models.telegram_types import ScheduledReport

log = get_logger("telegram")

# Phase conn-pool/p5-3: periodic re-probe interval (seconds) for the
# in-memory active-reports count. Self-heals against drift.
_ACTIVE_COUNT_REPROBE_S = 1800.0  # 30 min


class ScheduledReportEngine:
    """Configurable auto-report engine.

    Phase conn-pool/p5-3 (2026-05-14): caches the active-reports COUNT in
    memory so the scheduled_report_worker can skip the per-300 s DB poll
    when no reports are configured. ``scheduled_reports`` has had 0 rows
    across all observed sessions; eliminating the poll removes one source
    of writer-side background acquisitions and cleans up the log signal.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self.repo = TelegramRepository(db)
        self._active_count: int | None = None
        self._last_probe_monotonic: float = 0.0

    async def _ensure_active_count(self) -> int:
        """Cached active-reports count. Lazily probes on first access;
        re-probes every ``_ACTIVE_COUNT_REPROBE_S`` for self-healing.
        """
        now = time.monotonic()
        stale = self._active_count is None or (
            now - self._last_probe_monotonic >= _ACTIVE_COUNT_REPROBE_S
        )
        if stale:
            rows = await self.repo.get_active_reports()
            self._active_count = len(rows)
            self._last_probe_monotonic = now
        return self._active_count  # type: ignore[return-value]

    async def has_active(self) -> bool:
        """Cheap check (mostly in-memory) used by ``scheduled_report_worker``
        to gate the per-300 s DB poll.
        """
        return (await self._ensure_active_count()) > 0

    async def create_report(self, chat_id: int, report_type: str, schedule: str) -> ScheduledReport:
        report = ScheduledReport(
            id=generate_id("rpt"), chat_id=chat_id,
            report_type=report_type, schedule=schedule,
            created_at=now_utc(),
        )
        await self.repo.save_report_config(report)
        # Refresh cache after a write.
        await self._ensure_active_count()
        self._active_count = (self._active_count or 0) + 1
        return report

    async def get_due_reports(self) -> list[dict]:
        return await self.repo.get_active_reports()
