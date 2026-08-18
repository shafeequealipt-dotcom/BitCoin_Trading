"""Trade-Activity Watchdog (2026-08-18) — alerts when the bot is UP but not trading.

Closes the monitoring gap exposed by the 2026-08-16 ZenMux 403 outage: the LLM
provider rejected every completion for 2.5 days while systemd, every worker
heartbeat, and BrainLivenessWatchdog all reported perfect health. See
``src/core/trade_activity.py`` for the full incident write-up and why
heartbeat-based checks structurally cannot catch this.

Design deliberately mirrors BrainLivenessWatchdog (same BaseWorker shape,
same per-tick INFO heartbeat, same rate-limited Telegram alert, same
detect-and-alert-only posture — no auto-restart, no provider switching) so
operators reading workers.log see one consistent pattern.
"""

from __future__ import annotations

import time

from src.config.settings import Settings
from src.core.log_context import ctx, new_watchdog_id
from src.core.logging import get_logger
from src.core.trade_activity import (
    VERDICT_BRAIN_FAILING,
    VERDICT_NO_TRADES,
    evaluate_trade_activity,
)
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker_liveness")


class TradeActivityWatchdog(BaseWorker):
    """Layer 1A always-on worker that monitors whether trading is actually happening.

    Args:
        settings: Application settings (BaseWorker plumbing).
        db: Database manager — used to read the most recent trade ENTRY.
        services: The WorkerManager services dict. Read lazily on each tick
            (NOT captured at construction) because ``claude_client`` may be
            registered after this worker is built, and because a provider
            swap replaces the client object entirely.
        watchdog_interval_sec: Probe cadence. Default 60.
        max_consecutive_failures: Critical alert once the brain client's
            failure streak reaches this. <= 0 disables the check.
        no_trade_alert_hours: Warning once no trade has been ENTERED for this
            many hours. <= 0 disables the check.
        alert_rate_limit_sec: Minimum gap between Telegram alerts so an
            ongoing outage doesn't re-alert every tick. Default 3600.
        alert_manager: Optional AlertManager. None -> log-only mode.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        *,
        services: dict | None = None,
        watchdog_interval_sec: float = 60.0,
        max_consecutive_failures: int = 3,
        no_trade_alert_hours: float = 12.0,
        alert_rate_limit_sec: float = 3600.0,
        alert_manager=None,
    ) -> None:
        super().__init__(
            name="trade_activity_watchdog",
            interval_seconds=float(watchdog_interval_sec),
            settings=settings,
            db=db,
        )
        self._services = services if services is not None else {}
        self._max_failures = int(max_consecutive_failures)
        self._no_trade_threshold_s = float(no_trade_alert_hours) * 3600.0
        self._alert_rate_limit_s = float(alert_rate_limit_sec)
        self._alert_manager = alert_manager
        self._last_alert_ts: float = 0.0

    async def tick(self) -> None:
        """One activity probe: brain failure streak + time since last entry."""
        new_watchdog_id()

        failures = self._read_consecutive_failures()
        age_s = await self._seconds_since_last_trade_entry()

        result = evaluate_trade_activity(
            consecutive_failures=failures,
            seconds_since_last_trade=age_s,
            failure_threshold=self._max_failures,
            no_trade_threshold_seconds=self._no_trade_threshold_s,
        )

        _age_h = f"{age_s / 3600.0:.2f}" if age_s is not None else "NA"
        _fail = failures if failures is not None else "NA"
        log.info(
            f"TRADE_ACTIVITY_HEARTBEAT | verdict={result.verdict} "
            f"reason={result.reason} severity={result.severity} "
            f"consec_fail={_fail} fail_thr={self._max_failures} "
            f"since_last_trade_h={_age_h} "
            f"no_trade_thr_h={self._no_trade_threshold_s / 3600.0:.1f} | {ctx()}"
        )

        if result.verdict == VERDICT_BRAIN_FAILING:
            log.error(
                f"TRADE_ACTIVITY_BRAIN_FAILING | consec_fail={failures} "
                f"thr={self._max_failures} | the brain's LLM provider is "
                f"rejecting calls — the bot is UP but cannot trade | {ctx()}"
            )
            self._maybe_alert(
                subject="TRADE_ACTIVITY_BRAIN_FAILING",
                detail=(
                    f"The brain's LLM provider has failed {failures} calls in a "
                    f"row (threshold {self._max_failures}). The bot is running "
                    f"normally but CANNOT PLACE TRADES. Most likely: the provider "
                    f"revoked access, the free tier ended, or credit ran out. "
                    f"Check: grep STRAT_CALL_A_FAIL data/logs/brain.log | tail -3"
                ),
            )
        elif result.verdict == VERDICT_NO_TRADES:
            # Suppress during the boot window: a freshly-restarted process
            # legitimately has an old "last trade" until it places a new one,
            # and alerting on that would fire after every deploy.
            uptime_s = (
                (now_utc() - self._start_time).total_seconds()
                if self._start_time else 0.0
            )
            if uptime_s < self._no_trade_threshold_s:
                return
            log.warning(
                f"TRADE_ACTIVITY_NO_TRADES | since_last_trade_h={age_s / 3600.0:.2f} "
                f"thr_h={self._no_trade_threshold_s / 3600.0:.1f} | no trade ENTRY "
                f"in this window despite the brain reporting no API failures | {ctx()}"
            )
            self._maybe_alert(
                subject="TRADE_ACTIVITY_NO_TRADES",
                detail=(
                    f"No trade has been entered for {age_s / 3600.0:.1f} hours "
                    f"(threshold {self._no_trade_threshold_s / 3600.0:.1f}h), and the "
                    f"brain reports no API failures — so this is NOT a provider "
                    f"outage. Could be legitimately quiet markets, or entry gates "
                    f"blocking everything. Check: grep -c TRADE_SKIP data/logs/workers.log"
                ),
            )

    # ─── Internal ───

    def _read_consecutive_failures(self) -> int | None:
        """Read the brain client's failure streak.

        Handles both client shapes: GLMClient exposes ``get_stats()``;
        ClaudeClient (the OpenAI-compatible path) only carries the private
        ``_consecutive_failures`` attribute. Returns None when neither is
        available so the evaluator fails open rather than inventing a zero
        (a fabricated 0 would read as "healthy" and mask a real outage).
        """
        client = self._services.get("claude_client")
        if client is None:
            return None
        try:
            get_stats = getattr(client, "get_stats", None)
            if callable(get_stats):
                stats = get_stats() or {}
                val = stats.get("consecutive_failures")
                if val is not None:
                    return int(val)
            val = getattr(client, "_consecutive_failures", None)
            return int(val) if val is not None else None
        except Exception as e:
            log.debug(f"TRADE_ACTIVITY_STATS_FAIL | err='{str(e)[:100]}' | {ctx()}")
            return None

    async def _seconds_since_last_trade_entry(self) -> float | None:
        """Seconds since the most recent trade ENTRY, or None if unknown.

        Reads ``trade_thesis.opened_at`` rather than ``trade_log`` because a
        thesis row is written at ENTRY, whereas trade_log rows only appear on
        CLOSE — a position held open for hours is still proof the entry path
        works, and must not look like a dry spell.

        Uses ``julianday()`` on both sides rather than a string comparison:
        opened_at is stored in mixed formats across writers ("...T10:04:48"
        vs "... 10:04:48"), and since 'T' > ' ' in ASCII a raw string compare
        silently misorders same-day rows (the same trap documented on the
        recent-loss entry gate).
        """
        try:
            row = await self.db.fetch_one(
                "SELECT (julianday('now') - julianday(MAX(opened_at))) * 86400.0 "
                "AS age_s FROM trade_thesis WHERE opened_at IS NOT NULL"
            )
            if not row:
                return None
            age = (row or {}).get("age_s")
            return float(age) if age is not None else None
        except Exception as e:
            log.debug(f"TRADE_ACTIVITY_DB_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return None

    def _maybe_alert(self, *, subject: str, detail: str) -> None:
        """Rate-limited Telegram dispatch. No-ops without an AlertManager."""
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
                    component="trade_activity",
                    error_message=f"{subject}: {detail}",
                ),
                name="trade_activity_alert",
            )
        except Exception as e:  # pragma: no cover — defensive
            log.warning(f"TRADE_ACTIVITY_ALERT_FAIL | err='{str(e)[:80]}' | {ctx()}")
