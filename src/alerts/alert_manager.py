"""Alert Manager: central hub for all notification routing, throttling, scheduling."""

import asyncio
from typing import TYPE_CHECKING

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import AlertLevel, BrainDecision, Order, Position, Side, Signal, WatchdogDecision
from src.database.connection import DatabaseManager
from src.alerts.formatter import AlertFormatter
from src.alerts.templates import AlertTemplates
from src.alerts.telegram_bot import TelegramBot
from src.alerts.throttle import AlertThrottle

if TYPE_CHECKING:
    from src.core.price_formatter import PriceFormatter

log = get_logger("alerts")


class AlertManager:
    """Central alert hub. All components call methods here to send alerts.

    Handles routing, throttling, deduplication, priority, and daily summary.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        price_formatter: "PriceFormatter | None" = None,
        services: dict | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        # Win-rate enhancement Phase E (2026-07-07) — optional shared
        # services dict, only passed by the production caller
        # (workers/manager.py). Lets the daily scorecard read live
        # in-memory counters (e.g. apex_gate.get_entry_quality_stats())
        # without a DB round-trip. None for the other three construction
        # sites (mcp/server.py, brain/__init__.py, core/container.py) —
        # _gather_summary_data guards on it being present.
        self._services = services
        self.bot = TelegramBot(settings)
        self.throttle = AlertThrottle(max_per_hour=settings.alerts.max_alerts_per_minute * 60)
        # Canonical price renderer (exact exchange tick size, magnitude
        # fallback). Threaded into the templates so alert prices match the
        # dashboard and the exchange. None is fully supported — templates
        # fall back to AlertFormatter's magnitude-aware formatting.
        self._price_formatter = price_formatter
        self.templates = AlertTemplates(price_formatter=price_formatter)
        self.enabled = settings.alerts.telegram_enabled
        self._daily_task: asyncio.Task | None = None
        # P2-2 (2026-05-13): pending fire-and-forget INFO send tasks.
        # Tracked so ``flush_pending_info`` (called from ``shutdown`` and
        # by tests) can await every in-flight delivery before exiting.
        # Tasks self-remove via the done-callback in ``_track_info_task``.
        self._pending_info_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        """Connect bot and verify. Disables gracefully on failure."""
        if not self.enabled:
            log.info("Alert system disabled in config")
            return
        ok = await self.bot.connect()
        if not ok:
            self.enabled = False
        log.info("Alert system initialized (enabled={e})", e=self.enabled)

    async def send_trade_alert(self, order: Order, account_balance: float | None = None) -> None:
        """Send alert when a trade is executed."""
        if not self.enabled or not self.settings.alerts.trade_alerts:
            return
        msg = self.templates.trade_executed(order, account_balance)
        await self._send(msg, AlertLevel.INFO)

    async def send_position_closed_alert(self, symbol: str, side: Side, entry_price: float, exit_price: float, pnl: float, pnl_pct: float) -> None:
        """Send alert when a position is closed."""
        if not self.enabled or not self.settings.alerts.trade_alerts:
            return
        msg = self.templates.position_closed(symbol, side, entry_price, exit_price, pnl, pnl_pct)
        await self._send(msg, AlertLevel.INFO)

    async def send_signal_alert(self, signal: Signal) -> None:
        """Send alert for high-confidence signals only."""
        if not self.enabled or not self.settings.alerts.signal_alerts:
            return
        if signal.confidence < 0.7:
            return
        msg = self.templates.signal_detected(signal)
        await self._send(msg, AlertLevel.INFO)

    async def send_brain_decision_alert(self, decision: BrainDecision, trigger: str, cost_usd: float) -> None:
        """Send alert for every Brain decision (including holds)."""
        if not self.enabled:
            return
        msg = self.templates.brain_decision(decision, trigger, cost_usd)
        await self._send(msg, AlertLevel.INFO)

    async def send_error_alert(self, component: str, error_message: str, severity: AlertLevel = AlertLevel.WARNING) -> None:
        """Send error alert. CRITICAL bypasses throttle."""
        if not self.enabled or not self.settings.alerts.error_alerts:
            return
        msg = self.templates.error_alert(component, error_message, severity)
        priority = AlertLevel.CRITICAL if severity == AlertLevel.CRITICAL else AlertLevel.WARNING
        await self._send(msg, priority)

    async def send_worker_crash_alert(self, worker_name: str, error: str, restart_count: int, max_restarts: int) -> None:
        """Send worker crash alert."""
        if not self.enabled:
            return
        msg = self.templates.worker_crash(worker_name, error, restart_count, max_restarts)
        priority = AlertLevel.CRITICAL if restart_count >= max_restarts else AlertLevel.WARNING
        await self._send(msg, priority)

    async def send_risk_warning(self, warning_type: str, details: dict) -> None:
        """Send risk warning (always CRITICAL priority)."""
        if not self.enabled:
            return
        msg = self.templates.risk_warning(warning_type, details)
        await self._send(msg, AlertLevel.CRITICAL)

    async def send_watchdog_alert(
        self,
        position: Position,
        current_price: float,
        pnl_pct: float,
        warnings: list[str],
        severity: AlertLevel,
    ) -> None:
        """Send position warning alert from the watchdog."""
        if not self.enabled:
            return
        msg = self.templates.watchdog_alert(position, current_price, pnl_pct, warnings, severity)
        await self._send(msg, severity)

    async def send_watchdog_decision(
        self,
        position: Position,
        decision: WatchdogDecision,
        cost_usd: float = 0.0,
    ) -> None:
        """Send watchdog Brain decision alert."""
        if not self.enabled:
            return
        msg = self.templates.watchdog_decision(position, decision, cost_usd)
        priority = AlertLevel.CRITICAL if decision.action in ("partial_close", "full_close") else AlertLevel.WARNING
        await self._send(msg, priority)

    async def send_price_alert(self, symbol: str, current_price: float, change_pct: float, timeframe_minutes: int) -> None:
        """Send price spike alert."""
        if not self.enabled:
            return
        msg = self.templates.price_alert(symbol, current_price, change_pct, timeframe_minutes)
        await self._send(msg, AlertLevel.WARNING)

    async def send_system_startup(self, mode: str, symbols: list[str], workers: int) -> None:
        """Send system startup notification (bypasses throttle)."""
        if not self.enabled:
            return
        msg = self.templates.system_startup(mode, symbols, workers)
        await self.bot.send_message(msg)

    async def send_system_shutdown(self, reason: str) -> None:
        """Send system shutdown notification (bypasses throttle)."""
        if not self.enabled:
            return
        msg = self.templates.system_shutdown(reason)
        await self.bot.send_message(msg)

    async def send_daily_summary(self) -> None:
        """Gather data and send daily summary."""
        if not self.enabled:
            return
        data = await self._gather_summary_data()
        msg = self.templates.daily_summary(data)
        await self.bot.send_message(msg)

    async def send_test_message(self) -> bool:
        """Send a test message to verify bot works."""
        msg = "\U0001f514 Trading Intelligence MCP \u2014 Test Alert\n\nIf you see this, alerts are working! \u2705"
        return await self.bot.send_message(msg)

    async def send_custom(self, message: str, priority: AlertLevel = AlertLevel.INFO) -> bool:
        """Send a pre-formatted custom message with throttle and dedup.

        Use this instead of _send() for custom-formatted messages from
        external callers (watchdog, layer_manager, strategy_worker).
        """
        if not self.enabled:
            return False
        return await self._send(message, priority)

    async def _send(self, message: str, priority: AlertLevel = AlertLevel.INFO) -> bool:
        """Core send method with throttle and dedup.

        Prefixes all messages with the trading mode indicator (#6).

        P2-2 (2026-05-13): INFO-level alerts are now dispatched
        fire-and-forget via ``asyncio.create_task`` so the critical
        trade path is never blocked by Telegram round-trip latency
        (observed up to 40 s on flood-control / network retry storms).
        CRITICAL and WARNING priorities still ``await`` the transport
        step so the operator's halt-notification guarantee is preserved.
        Failures on the fire-and-forget path are NOT silently lost —
        ALERT_FAIL still emits from ``_deliver_and_log``, plus a done-
        callback traps any unhandled exception via
        ``ALERT_FIRE_AND_FORGET_TASK_FAIL``.
        """
        # Mode indicator prefix — Transformer takes priority over trading_mode
        transformer = getattr(self, "_transformer", None)
        if transformer:
            message = f"{transformer.mode_label} {message}"
        else:
            trading_mode = getattr(self, "_trading_mode", None)
            if trading_mode:
                mode = getattr(trading_mode, "mode", None)
                if mode:
                    indicator = getattr(mode, "indicator", "")
                    label = getattr(mode, "label", "")
                    if indicator and label:
                        message = f"{indicator} {label} {message}"

        # CRITICAL-4 fix (2026-05-09) — use the numeric-normalized hash
        # so retry storms (e.g., audit's KATUSDT 5x SET_SL_FAIL with
        # different base_price values per retry) dedup correctly. Raw
        # content_hash kept as a separate static method for tests.
        h = AlertThrottle.normalized_content_hash(message)
        if self.throttle.is_duplicate(h):
            log.debug(f"ALERT_THROTTLE | type=dedup | {ctx()}")
            return False
        if not self.throttle.can_send(priority):
            log.debug(f"ALERT_THROTTLE | type=rate_limit level={priority.value} | {ctx()}")
            self.throttle.queue_alert({"message": message, "priority": priority.value})
            return False
        silent = priority == AlertLevel.INFO

        # P2-2 (2026-05-13): split by priority. INFO fires-and-forgets so
        # ``send_custom`` returns to the caller in microseconds even when
        # Telegram is slow. CRITICAL/WARNING keep awaited semantics.
        if priority == AlertLevel.INFO:
            # Pre-record dedup + rate-limit accounting BEFORE scheduling
            # the task. If we let ``_deliver_and_log`` record on success
            # then a second identical INFO call arriving before the
            # first task fires would skip dedup (race window). Pre-
            # recording closes the race at the cost of slightly
            # over-counting on send failures — but that's the
            # conservative direction (the operator gets at most one
            # alert per distinct content even if Telegram briefly
            # fails). The legacy CRITICAL/WARNING path keeps the
            # original post-success record because those paths are
            # serialized by the await.
            self.throttle.record_content(h)
            self.throttle.record_send()
            log.info(
                f"ALERT_FIRE_AND_FORGET | kind=info bypass=Y "
                f"len={len(message)} | {ctx()}"
            )
            task = asyncio.create_task(
                self._deliver_and_log(
                    message, priority, silent, h, record_throttle=False,
                ),
                name="alert-info-ff",
            )
            self._track_info_task(task)
            # Optimistic return — actual outcome surfaces via
            # ALERT_SENT/ALERT_FAIL emitted from inside the task.
            return True

        log.info(
            f"ALERT_AWAITED | kind={priority.value.lower()} "
            f"len={len(message)} | {ctx()}"
        )
        return await self._deliver_and_log(
            message, priority, silent, h, record_throttle=True,
        )

    def _track_info_task(self, task: asyncio.Task) -> None:
        """Add ``task`` to ``_pending_info_tasks`` and wire the done-callback.

        P2-2 (2026-05-13). The done-callback both removes the task from
        the pending set (so the set bounded by in-flight count) and
        surfaces any unhandled exception via the static
        ``_on_alert_task_done``.
        """
        self._pending_info_tasks.add(task)
        task.add_done_callback(self._pending_info_tasks.discard)
        task.add_done_callback(self._on_alert_task_done)

    async def _deliver_and_log(
        self,
        message: str,
        priority: AlertLevel,
        silent: bool,
        content_hash: str,
        *,
        record_throttle: bool = True,
    ) -> bool:
        """Perform the Telegram send + emit ALERT_SENT / ALERT_FAIL.

        P2-2 (2026-05-13) — extracted from ``_send`` so both the
        awaited (CRITICAL/WARNING) and fire-and-forget (INFO) paths
        share identical transport + logging behaviour. The only
        differences live in the priority-gate inside ``_send`` itself.
        Failures still emit ALERT_FAIL and trigger the dashboard
        reposition fallback — fire-and-forget never silently loses
        delivery status.

        Args:
            message: rendered Telegram payload (mode prefix already applied).
            priority: alert level (used only for log attribution here).
            silent: forwarded to ``bot.send_message``.
            content_hash: throttle dedup key.
            record_throttle: when ``True`` (CRITICAL/WARNING path), the
                throttle's content + send counters are bumped on
                successful delivery. When ``False`` (INFO fire-and-forget
                path), the throttle was already pre-recorded inside
                ``_send`` to avoid the dedup race; this helper must
                NOT double-record in that case.
        """
        # T6-4 / F7 fix (six-tier-fixes 2026-05-11) — capture the send_message
        # failure reason so ALERT_FAIL log lines surface root cause instead
        # of being singleton "alert failed" events with no context.
        _send_err: str = ""
        try:
            success = await self.bot.send_message(message, silent=silent)
        except Exception as _e:
            success = False
            _send_err = str(_e)[:160]
        if success:
            log.info(
                f"ALERT_SENT | level={priority.value} "
                f"len={len(message)} | {ctx()}"
            )
            if record_throttle:
                self.throttle.record_send()
                self.throttle.record_content(content_hash)
        else:
            log.error(
                f"ALERT_FAIL | level={priority.value} "
                f"len={len(message)} err='{_send_err or 'send_returned_false'}' "
                f"| {ctx()}"
            )
            # Reposition dashboard after alert — keeps it at bottom of chat
            await self._reposition_dashboard()
        return success

    async def flush_pending_info(self) -> None:
        """Wait for every in-flight fire-and-forget INFO send task to complete.

        P2-2 (2026-05-13). Two callers:

        - ``shutdown()`` — drains pending alerts before disconnecting
          the Telegram bot so the operator doesn't lose late info
          notifications on graceful service restart.
        - Tests — call this between an ``await send_custom`` (or any
          send_*-helper that funnels through ``_send`` on INFO) and an
          assertion on ``mock_bot.send_message`` so the fire-and-forget
          task has actually run.

        Safe to call when no tasks are pending — no-op in that case.
        """
        if not self._pending_info_tasks:
            return
        pending = list(self._pending_info_tasks)
        await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _on_alert_task_done(task: asyncio.Task) -> None:
        """Done-callback for fire-and-forget alert tasks.

        P2-2 (2026-05-13). ``_deliver_and_log`` already catches expected
        send-side failures and emits ALERT_FAIL. This callback exists
        for the asyncio safety-net case: an UNCAUGHT exception from
        somewhere inside the task. Without this hook, Python would
        log it once at process exit and the operator would have no
        attribution. Emits ``ALERT_FIRE_AND_FORGET_TASK_FAIL`` at ERROR
        so the failure shows up in real-time dashboards.
        """
        try:
            exc = task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return
        if exc is not None:
            log.error(
                f"ALERT_FIRE_AND_FORGET_TASK_FAIL | "
                f"exc_type={type(exc).__name__} "
                f"exc='{str(exc)[:160]}' | {ctx()}"
            )

    async def _reposition_dashboard(self) -> None:
        """After sending an alert, refresh the dashboard so it stays at the bottom."""
        try:
            if not getattr(self, "_telegram_bot_instance", False):
                return
            bot_data = getattr(self, "_app_bot_data", None)
            if not bot_data or not bot_data.get("dashboard_active"):
                return

            chat_id = bot_data.get("dashboard_chat_id")
            old_msg_id = bot_data.get("dashboard_msg_id")
            if not chat_id:
                return

            # Import here to avoid circular
            from src.telegram.handlers.dashboard_handler import (
                _delete_dashboard, _send_dashboard,
            )

            bot = self.bot.bot  # the actual telegram.Bot instance
            if bot and old_msg_id:
                await _delete_dashboard(bot, chat_id, old_msg_id)
                new_id = await _send_dashboard(bot, bot_data, chat_id)
                bot_data["dashboard_msg_id"] = new_id
        except Exception:
            pass  # never crash the alert pipeline for dashboard issues

    async def _gather_summary_data(self) -> dict:
        """Gather all data for the daily summary."""
        data: dict = {
            "total_pnl": 0, "total_pnl_pct": 0, "trades_count": 0,
            "wins": 0, "positions": [], "fear_greed": {},
            "brain_calls": 0, "brain_cost": 0,
            "workers_running": 0, "workers_total": 0,
            # Win-rate enhancement Phase E (2026-07-07).
            "expectancy_usd": 0.0, "fee_drag_est_usd": 0.0,
            "entry_quality_passed": None, "entry_quality_rejected": None,
            "entry_quality_by_reason": {},
        }
        try:
            from src.database.repositories.trading_repo import TradingRepository
            repo = TradingRepository(self.db)
            trades = await repo.get_trade_history(limit=50)
            data["trades_count"] = len(trades)
            data["wins"] = sum(1 for t in trades if t.pnl > 0)
            data["total_pnl"] = sum(t.pnl for t in trades)
            if trades:
                data["expectancy_usd"] = data["total_pnl"] / len(trades)
                # Round-trip taker-fee estimate — matches trade_coordinator's
                # _BYBIT_TAKER_FEE_PER_SIDE (0.055%/side, 0.11% round trip),
                # applied to each trade's entry notional. Estimate only (the
                # authoritative fee is booked exchange-side per trade); this
                # exists so the scorecard can show fee drag as a fraction of
                # gross PnL without a schema change.
                _rt_fee_pct = 0.0011
                data["fee_drag_est_usd"] = sum(
                    abs(t.entry_price * t.qty) * _rt_fee_pct for t in trades
                )
        except Exception:
            pass
        # Entry-quality filter counters (Phase A/E) — read from the live
        # TradeGate instance when the production caller passed services in.
        # None (not 0) when unavailable, so the template can render "N/A"
        # instead of a misleading zero.
        try:
            if self._services:
                _gate = self._services.get("apex_gate")
                if _gate is not None and hasattr(_gate, "get_entry_quality_stats"):
                    _eq = _gate.get_entry_quality_stats()
                    data["entry_quality_passed"] = _eq.get("passed", 0)
                    data["entry_quality_rejected"] = _eq.get("rejected_total", 0)
                    data["entry_quality_by_reason"] = _eq.get("rejected_by_reason", {})
        except Exception:
            pass
        return data

    async def start_daily_summary_scheduler(self) -> None:
        """Start background task for daily summary."""
        self._daily_task = asyncio.create_task(self._daily_loop())

    async def _daily_loop(self) -> None:
        """Send daily summary at [alerts].daily_summary_time (UTC, "HH:MM").

        Win-rate enhancement Phase E (2026-07-07): this scheduler previously
        had no caller anywhere in the codebase (dead code — config's
        daily_summary=true never actually fired a summary) and hardcoded
        20:00 UTC regardless of daily_summary_time. Now started from
        workers/manager.py and reads the configured time; malformed config
        falls back to the prior 20:00 default rather than crashing the loop.
        """
        from datetime import datetime, timedelta, timezone
        _time_str = str(getattr(self.settings.alerts, "daily_summary_time", "20:00") or "20:00")
        try:
            _hh, _mm = _time_str.split(":")
            hour, minute = int(_hh), int(_mm)
        except (ValueError, AttributeError):
            log.warning(
                f"DAILY_SUMMARY_TIME_INVALID | value='{_time_str}' "
                f"falling back to 20:00 UTC"
            )
            hour, minute = 20, 0
        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(days=1)
                wait = (target - now).total_seconds()
                await asyncio.sleep(wait)
                await self.send_daily_summary()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Daily summary error: {err}", err=str(e))
                await asyncio.sleep(3600)

    async def flush_queue(self) -> None:
        """Send queued alerts as a batch summary."""
        queued = self.throttle.get_queued()
        if not queued:
            return
        summary = f"\U0001f4ec <b>Queued Alerts ({len(queued)})</b>\n\n"
        for q in queued[:10]:
            summary += f"\u2022 {q.get('message', '')[:100]}\n"
        await self.bot.send_message(summary)

    async def shutdown(self) -> None:
        """Clean shutdown.

        P2-2 (2026-05-13) — also drains the fire-and-forget INFO send
        queue so the operator doesn't lose late alerts on service
        restart. ``flush_pending_info`` is safe to call when nothing
        is pending.
        """
        if self._daily_task:
            self._daily_task.cancel()
        await self.flush_pending_info()
        await self.flush_queue()
        await self.bot.disconnect()

    def get_stats(self) -> dict:
        """Combined stats."""
        return {
            "enabled": self.enabled,
            "bot": self.bot.get_stats(),
            "throttle": self.throttle.get_stats(),
        }
