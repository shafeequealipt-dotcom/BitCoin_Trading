"""BybitDemoWSWorker — BaseWorker that owns the bybit_demo private-WS subscriber.

Tick is a periodic health-check + reconnect-if-stale loop. The actual
close events arrive via the underlying subscriber's pybit-thread
callbacks, not via tick. Tick only:

1. Performs initial connect on first tick.
2. Emits BYBIT_DEMO_WS_HEALTH per tick with throughput + dispatch
   counters from the subscriber's snapshot.
3. Triggers ``subscriber.reconnect()`` when ``is_stale()`` reports
   True (no message received within the staleness threshold).

P1 Phase 3b of the P1-P10 fix series. Lifecycle pattern matches
PriceWorker (BaseWorker subclass with periodic tick).
"""

from __future__ import annotations

import asyncio

from src.bybit_demo.bybit_demo_websocket_subscriber import (
    BybitDemoWebSocketSubscriber,
)
from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("bybit_demo")


class BybitDemoWSWorker(BaseWorker):
    """Tick-driven owner for the bybit_demo private-WS subscriber.

    Args:
        name: Worker identifier (default ``"bybit_demo_ws_worker"``).
        interval_seconds: Tick interval. Default 60s — long enough not
            to spam health logs, short enough to catch a staleness
            (120s threshold) within one tick.
        settings: Application settings.
        db: Database manager (passed through to the subscriber's
            BybitWebSocket instance).
        subscriber: The subscriber instance to own. Constructed by the
            worker manager after the coordinator is available.
    """

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        settings: Settings,
        db: DatabaseManager,
        subscriber: BybitDemoWebSocketSubscriber,
    ) -> None:
        super().__init__(
            name=name,
            interval_seconds=interval_seconds,
            settings=settings,
            db=db,
        )
        self._subscriber = subscriber
        # Track first-tick to defer the connect attempt until the worker
        # is actually running (so a connect failure surfaces through the
        # BaseWorker error-recovery loop, not synchronously at boot).
        self._first_tick_done: bool = False

    async def tick(self) -> None:
        """Health-check tick. First tick connects; subsequent ticks
        observe + reconnect if stale.
        """
        # ─── First tick: initial connect ──────────────────────────────
        if not self._first_tick_done:
            self._first_tick_done = True
            try:
                await self._subscriber.connect()
            except Exception as e:
                # Surface the connect failure but do not raise — pybit
                # auto-reconnect can still recover, and a reconnect tick
                # below will retry. Critical alert handled by the
                # BybitDemoAlertRelay (P10) when BYBIT_DEMO_WS_DEAD fires.
                log.error(
                    f"BYBIT_DEMO_WS_INITIAL_CONNECT_FAIL | err='{str(e)[:200]}' | "
                    f"polling_remains_active | {ctx()}"
                )
                # Reset _first_tick_done so the next tick retries the
                # connect rather than only doing the health-check path.
                self._first_tick_done = False
                return

        # ─── Subsequent ticks: health-check + reconnect on stale ──────
        snap = self._subscriber.get_health_snapshot()
        last_age = snap.get("last_msg_age_s")
        last_age_str = f"{last_age:.1f}" if last_age is not None else "n/a"
        msg_per_min = (snap["msg_total"] / self.interval) * 60.0 if self.interval > 0 else 0.0
        log.info(
            f"BYBIT_DEMO_WS_HEALTH | "
            f"connected={snap['connected']} "
            f"msgs_per_min={msg_per_min:.0f} "
            f"msg_total={snap['msg_total']} "
            f"by_topic={snap['msg_by_topic']} "
            f"dedup_count={snap['dedup_count']} "
            f"dispatch_fail_count={snap['dispatch_fail_count']} "
            f"last_msg_age_s={last_age_str} "
            f"window_s={self.interval:.0f} | {ctx()}"
        )

        if self._subscriber.is_stale():
            log.warning(
                f"BYBIT_DEMO_WS_STALE | last_msg_age_s={last_age_str} "
                f"triggering_reconnect | {ctx()}"
            )
            try:
                await self._subscriber.reconnect()
            except Exception as e:
                # reconnect() already logged BYBIT_DEMO_WS_DEAD on
                # failure; we just keep ticking so the next tick can
                # retry. Polling continues in parallel.
                log.error(
                    f"BYBIT_DEMO_WS_RECONNECT_FAIL | err='{str(e)[:200]}' | "
                    f"will_retry_next_tick | {ctx()}"
                )

    async def cleanup(self) -> None:
        """BaseWorker.cleanup hook — disconnect on shutdown."""
        try:
            await self._subscriber.disconnect()
        except Exception as e:
            log.warning(
                f"BYBIT_DEMO_WS_CLEANUP_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )
