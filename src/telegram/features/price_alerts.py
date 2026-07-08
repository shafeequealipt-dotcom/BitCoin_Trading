"""Custom Price Alert Engine: checks user-defined price alerts."""

import time

from src.core.logging import get_logger
from src.core.utils import generate_id, now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.telegram_repo import TelegramRepository
from src.telegram.models.telegram_types import PriceAlert

log = get_logger("telegram")

# Phase conn-pool/p5-3 (db-concurrency-refactor 2026-05-14): periodic
# re-probe interval (seconds) for the in-memory active-alerts count. The
# count is normally maintained by create/trigger/cancel; the periodic
# re-probe self-heals against any drift (e.g. if a row is inserted from
# outside the engine path).
_ACTIVE_COUNT_REPROBE_S = 1800.0  # 30 min


class PriceAlertEngine:
    """Manages custom price alerts set by users.

    Phase conn-pool/p5-3 (2026-05-14): caches the active-alert COUNT in
    memory so the price_alert_worker can skip the per-10 s DB poll when
    no alerts exist. ``price_alerts`` has had 0 rows across all observed
    sessions; the audit identified the poll as the top cascade-holder
    SQL. Under the pooled engine the read no longer blocks other workers,
    but eliminating the poll entirely removes one source of writer-side
    background acquisitions and cleans up the log signal.

    The count is updated by ``create_alert`` / ``check_alerts`` (which
    calls ``repo.trigger_alert``) / ``cancel_alert``. A periodic re-probe
    every ``_ACTIVE_COUNT_REPROBE_S`` seconds self-heals against drift.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.repo = TelegramRepository(db)
        # ``None`` means "not yet probed"; ``_has_active()`` will read
        # the DB exactly once on first access. Subsequent reads return
        # from the cached count.
        self._active_count: int | None = None
        self._last_probe_monotonic: float = 0.0

    async def _ensure_active_count(self) -> int:
        """Return the cached active-alerts count, lazily probing the DB
        on first access and periodically re-probing every
        ``_ACTIVE_COUNT_REPROBE_S`` seconds for self-healing.
        """
        now = time.monotonic()
        stale = self._active_count is None or (
            now - self._last_probe_monotonic >= _ACTIVE_COUNT_REPROBE_S
        )
        if stale:
            rows = await self.repo.get_active_alerts()
            self._active_count = len(rows)
            self._last_probe_monotonic = now
        return self._active_count  # type: ignore[return-value]

    async def has_active(self) -> bool:
        """Cheap check (mostly in-memory) used by ``price_alert_worker``
        to gate the per-tick DB poll. Returns True if the cached count
        is > 0 or if we have not yet probed.
        """
        return (await self._ensure_active_count()) > 0

    async def create_alert(
        self, chat_id: int, symbol: str, condition: str, target: float, current: float,
    ) -> PriceAlert:
        """Create a new price alert."""
        alert = PriceAlert(
            id=generate_id("pa"),
            chat_id=chat_id,
            symbol=symbol,
            condition=condition,
            target_price=target,
            current_price_at_set=current,
            created_at=now_utc(),
        )
        await self.repo.save_alert(alert)
        # Keep the cache fresh after a write.
        await self._ensure_active_count()
        self._active_count = (self._active_count or 0) + 1
        log.info(
            "Price alert created: {sym} {cond} {target}",
            sym=symbol, cond=condition, target=target,
        )
        return alert

    async def check_alerts(self, prices: dict[str, float]) -> list[PriceAlert]:
        """Check all active alerts against current prices.

        Args:
            prices: {symbol: current_price}

        Returns:
            List of triggered alerts.
        """
        triggered: list[PriceAlert] = []
        active = await self.repo.get_active_alerts()

        for row in active:
            symbol = row["symbol"]
            if symbol not in prices:
                continue

            alert = PriceAlert(
                id=row["id"],
                chat_id=row["chat_id"],
                symbol=symbol,
                condition=row["condition"],
                target_price=row["target_price"],
                current_price_at_set=row.get("current_price_at_set", 0),
            )

            if alert.check(prices[symbol]):
                alert.triggered = True
                alert.triggered_at = now_utc()
                await self.repo.trigger_alert(alert.id)
                # Triggered alerts leave the "active" (untriggered) set.
                if self._active_count is not None and self._active_count > 0:
                    self._active_count -= 1
                triggered.append(alert)

        return triggered

    async def get_user_alerts(self, chat_id: int) -> list[dict]:
        """Get all active alerts for a user."""
        return await self.repo.get_active_alerts(chat_id)

    async def cancel_alert(self, alert_id: str) -> None:
        """Cancel (delete) an alert."""
        await self.repo.delete_alert(alert_id)
        if self._active_count is not None and self._active_count > 0:
            self._active_count -= 1
