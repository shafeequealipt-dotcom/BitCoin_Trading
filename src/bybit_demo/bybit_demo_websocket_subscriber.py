"""Bybit demo private-WebSocket subscriber.

Owns the lifecycle, message handlers, and idempotency dedup for the
Bybit-demo private push stream. Bridges pybit-thread events back to
the project's asyncio loop via ``asyncio.run_coroutine_threadsafe``
and dispatches close-event data to ``TradeCoordinator.on_trade_closed``.

This module is the consumer side of the BybitWebSocket demo extension
(see ``src/trading/websocket.py``). It is constructed and owned by
``BybitDemoWSWorker`` (``src/workers/bybit_demo_ws_worker.py``); the
worker handles tick-driven health checks and reconnection.

P1 Phase 3b of the P1-P10 fix series. Replaces the watchdog poll-only
detection model with push notifications from Bybit's matching engine.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

from src.config.settings import Settings
from src.core.exceptions import MarketDataError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.trading.websocket import BybitWebSocket

if TYPE_CHECKING:
    from src.core.trade_coordinator import TradeCoordinator
    from src.database.connection import DatabaseManager

# Loguru file-only logger routed to workers.log via get_logger("bybit_demo").
log = get_logger("bybit_demo")

# Per-handler dedup window. Bybit can emit duplicate execution events
# under rare conditions (e.g., transient TCP retry). Five seconds is
# long enough to absorb a re-emit and short enough to not suppress
# legitimate re-entries (cooldown gate enforces minimum 180s post-close
# before a new same-symbol trade — far longer than this window).
_DEDUP_TTL_SECONDS: float = 5.0

# Health-check threshold. If no message received for this long during
# expected market activity, treat the connection as stale and trigger a
# safety-net reconnect (pybit's auto-reconnect should usually handle
# transport drops first; this is the secondary catch).
#
# T5-4 / T5-5 / F19 + Phase5 F-1 fix (six-tier-fixes 2026-05-11) —
# the previous value of 120 s caused a spurious reconnect every 2-4 min
# during trade-quiet windows (92 reconnects per 8 h session today),
# because the private-channel WS produces NO payloads when the account
# is between trades. _last_msg_received_mono is only updated on payload
# receipt, so quiet periods register as "stale" even though TCP +
# pybit's own keepalive prove the socket is healthy. Bumping to 600 s
# (10 min) eliminates the spurious cycle while preserving the safety-
# net for genuinely dead connections: TCP keepalive at the OS layer and
# pybit's library-level auto-reconnect both still catch transport drops
# in seconds. The threshold here only adds an explicit secondary check.
_STALE_MESSAGE_THRESHOLD_SECONDS: float = 600.0


class BybitDemoWebSocketSubscriber:
    """Owns the bybit_demo private-WS subscription + close-event dispatch.

    Lifecycle:
        1. ``connect()`` opens the private socket via BybitWebSocket and
           subscribes to position / execution / order topics.
        2. pybit pushes events to ``_handle_execution`` /
           ``_handle_position`` / ``_handle_order`` running in pybit's
           thread.
        3. Each handler defers async work back to ``self._loop`` via
           ``asyncio.run_coroutine_threadsafe`` so the project's
           coordinator (and downstream async DB callbacks) execute in
           the right event loop.
        4. ``disconnect()`` closes the socket on shutdown.

    Idempotency strategy (three layers; this class implements layer 1):
        - L1 (this class): ``_processed_closes`` TTL dedup keyed by
          ``(symbol, orderId)`` for ``_DEDUP_TTL_SECONDS`` to suppress
          Bybit-side duplicate execution events.
        - L2 (TradeCoordinator): atomic ``_trades.pop(symbol, None)``
          first-writer-wins; subsequent calls warn and return.
        - L3 (PositionWatchdog): ``is_symbol_in_any_cooldown`` cooldown
          gate (Issue 3, 2026-05-18) skips poll-side processing of
          WS-handled closes; previously ``is_symbol_cooled_down``.

    Args:
        settings: Application settings (must include ``bybit_demo``).
        db: Database manager (passed to the underlying BybitWebSocket).
        coordinator: TradeCoordinator instance for ``on_trade_closed`` calls.
        loop: The project's asyncio event loop (for thread-safe dispatch).
    """

    def __init__(
        self,
        settings: Settings,
        db: "DatabaseManager",
        coordinator: "TradeCoordinator",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._db = db
        self._coordinator = coordinator
        self._loop = loop
        # Underlying WebSocket primitive — owned, not shared with
        # PriceWorker's BybitWebSocket instance (so demo private and
        # live public streams are isolated).
        self._ws = BybitWebSocket(settings, db)
        # Connection state. Set True after connect() completes; reset
        # on disconnect / reconnect.
        self._connected: bool = False
        # Idempotency dedup. Keys are "{symbol}|{orderId}", values are
        # monotonic timestamps (seconds). Pruned in _is_duplicate_close
        # to bound memory.
        self._processed_closes: dict[str, float] = {}
        # Health observability — last-message-received timestamp +
        # per-topic message counters.
        self._last_msg_received_mono: float = 0.0
        self._msg_count_total: int = 0
        self._msg_count_by_topic: dict[str, int] = {
            "execution": 0,
            "position": 0,
            "order": 0,
        }
        # Dispatch failure counter (for health log). Increments when
        # run_coroutine_threadsafe rejects (e.g., loop closed).
        self._dispatch_fail_count: int = 0
        # Dedup-suppress counter (for health log). Increments when
        # _is_duplicate_close returns True.
        self._dedup_count: int = 0
        # Phantom-loss fix (2026-06-05) Commit 1: multi-fill net
        # accumulator. A laddered SL/TP exit arrives as several reducing
        # execution legs sharing one orderId; we sum execPnl/execFee per
        # (symbol, orderId) so the flatting leg books the WHOLE-close net,
        # not just the final tranche. Keyed (symbol, orderId) ->
        # {"pnl", "fee", "ts"}; TTL-pruned (same window as the dedup gate)
        # so an abandoned ladder cannot leak. Read-and-cleared on the
        # flatting fill via _drain_close_accum. See PHANTOM_LOSS_FIX_DESIGN.
        self._close_pnl_accum: dict[tuple[str, str], dict[str, float]] = {}

    # ─── Connection lifecycle ─────────────────────────────────────────

    async def connect(self) -> None:
        """Open private WS to bybit_demo cluster + subscribe to 3 topics.

        Idempotent: calling on an already-connected subscriber is a no-op
        with a debug log.

        Raises:
            MarketDataError: When the underlying BybitWebSocket fails to
                connect (credentials missing, transport error, etc.).
        """
        if self._connected:
            log.debug(f"BYBIT_DEMO_WS_CONNECT_NOOP | already_connected | {ctx()}")
            return
        await self._ws.connect_private(demo=True)
        # Subscribe to all three private topics. Subscription order is
        # not significant — pybit batches subscriptions internally.
        self._ws.subscribe_executions(self._handle_execution)
        self._ws.subscribe_positions(self._handle_position)
        self._ws.subscribe_orders(self._handle_order)
        self._connected = True
        self._last_msg_received_mono = time.monotonic()
        log.info(
            f"BYBIT_DEMO_WS_CONN | demo_url=wss://stream-demo.bybit.com/v5/private "
            f"topics=position,execution,order | {ctx()}"
        )

    async def disconnect(self) -> None:
        """Close the private WS connection (called from worker.cleanup)."""
        if not self._connected and self._ws._private_ws is None:
            return
        try:
            await self._ws.disconnect()
        except Exception as e:
            log.warning(f"BYBIT_DEMO_WS_DISCONNECT_ERR | err='{str(e)[:120]}' | {ctx()}")
        finally:
            self._connected = False
            log.info(f"BYBIT_DEMO_WS_DISC | rsn=requested | {ctx()}")

    async def reconnect(self) -> None:
        """Safety-net reconnect when pybit's auto-reconnect has not recovered.

        pybit handles transient transport drops internally and re-subscribes
        from its own registry. This method is the secondary path called by
        the worker's tick health-check when ``is_stale()`` reports True
        (e.g., pybit gave up after 10 retries). It tears down + reconnects
        + re-subscribes from scratch.
        """
        log.warning(f"BYBIT_DEMO_WS_RECONNECT_START | rsn=stale_or_disconnected | {ctx()}")
        try:
            await self.disconnect()
        except Exception as e:
            log.warning(f"BYBIT_DEMO_WS_RECONNECT_DISCONNECT_FAIL | err='{str(e)[:120]}' | {ctx()}")
        try:
            await self.connect()
            log.info(f"BYBIT_DEMO_WS_RECONNECT_OK | {ctx()}")
        except Exception as e:
            log.error(
                f"BYBIT_DEMO_WS_DEAD | err='{str(e)[:200]}' | "
                f"reconnect_failed_polling_remains_active | {ctx()}"
            )
            raise

    # ─── Health observation ───────────────────────────────────────────

    def is_stale(self) -> bool:
        """True when no message has been received in the threshold window.

        Used by the worker's tick to decide whether a reconnect is needed.
        ``last_msg_received_mono = 0.0`` (initial state before any message)
        also counts as stale once the threshold has elapsed since boot.
        """
        if not self._connected:
            return True
        elapsed = time.monotonic() - self._last_msg_received_mono
        return elapsed > _STALE_MESSAGE_THRESHOLD_SECONDS

    def get_health_snapshot(self) -> dict[str, Any]:
        """Return per-tick observability snapshot for the worker's HEALTH log.

        Returns counts since the last snapshot AND resets them so the next
        snapshot reports throughput over the just-ended window.
        """
        snap = {
            "connected": self._connected,
            "msg_total": self._msg_count_total,
            "msg_by_topic": dict(self._msg_count_by_topic),
            "dedup_count": self._dedup_count,
            "dispatch_fail_count": self._dispatch_fail_count,
            "last_msg_age_s": (
                time.monotonic() - self._last_msg_received_mono
                if self._last_msg_received_mono > 0
                else None
            ),
        }
        # Reset window counters
        self._msg_count_total = 0
        for k in self._msg_count_by_topic:
            self._msg_count_by_topic[k] = 0
        self._dedup_count = 0
        self._dispatch_fail_count = 0
        return snap

    # ─── Message handlers (run in pybit thread) ───────────────────────

    def _handle_execution(self, message: dict[str, Any]) -> None:
        """Pybit execution-stream callback. Runs in pybit's thread.

        Parses the fill event, applies dedup, and dispatches a close-event
        coroutine onto the project loop. Per-fill events with
        ``closedSize > 0`` AND ``leavesQty == 0`` indicate a fully-flatting
        close (stop-loss / take-profit / manual close); only these trigger
        ``coordinator.on_trade_closed``.

        Partial fills (``leavesQty > 0``) are logged informationally but
        do NOT trigger on_trade_closed — the position is still open.
        """
        self._mark_message_received("execution")
        try:
            fills = self._extract_data_list(message)
        except Exception as e:
            log.warning(f"BYBIT_DEMO_WS_EXEC_PARSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return
        for fill in fills:
            self._handle_one_execution(fill)

    def _handle_position(self, message: dict[str, Any]) -> None:
        """Pybit position-stream callback. Runs in pybit's thread.

        Logs position state changes for observability. Does NOT trigger
        ``coordinator.on_trade_closed`` directly — the execution-stream
        handler is the canonical close source. Position events are
        secondary confirmation that a position fully flatted.

        Observability G4 — Bybit's position WS sends an event on every
        state change (size, margin, SL/TP, position status). Before
        G4 only the flat (size==0) case was logged. The audit
        (2026-05-13) noted F-26 ground-truth divergence (system
        thought 2 positions open, Bybit had 5) would have been
        detected immediately if non-flat updates were visible. The
        new BYBIT_DEMO_WS_POS_UPDATE event carries the full snapshot
        per state change. BYBIT_DEMO_WS_POS_FLAT continues firing
        unchanged when size==0 as the lifecycle-end marker.
        """
        self._mark_message_received("position")
        try:
            positions = self._extract_data_list(message)
        except Exception as e:
            log.warning(f"BYBIT_DEMO_WS_POS_PARSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return
        for pos in positions:
            sym = pos.get("symbol", "")
            if not sym:
                continue
            size_str = pos.get("size", "0")
            try:
                size = float(size_str)
            except (TypeError, ValueError):
                size = 0.0

            # G4 — non-flat snapshot. Bybit sends this on size /
            # entryPrice / leverage / SL / TP / status changes.
            # ``unrealisedPnl`` and ``markPrice`` are also in the
            # payload and surface here for divergence-detection
            # correlation. Best-effort field reads — any missing
            # field defaults to a sentinel rather than raising, so
            # malformed messages still emit the part they have.
            if size != 0.0:
                _side = pos.get("side", "")
                _entry = pos.get("entryPrice", "") or pos.get("avgPrice", "")
                _sl = pos.get("stopLoss", "")
                _tp = pos.get("takeProfit", "")
                _lev = pos.get("leverage", "")
                _status = pos.get("positionStatus", "")
                _upnl = pos.get("unrealisedPnl", "") or pos.get("unrealizedPnl", "")
                _mark = pos.get("markPrice", "")
                log.info(
                    f"BYBIT_DEMO_WS_POS_UPDATE | sym={sym} side={_side} "
                    f"qty={size} entry_price={_entry} mark_price={_mark} "
                    f"unrealized_pnl={_upnl} sl_price={_sl} tp_price={_tp} "
                    f"lev={_lev} status={_status} | {ctx()}"
                )
            else:
                log.info(
                    f"BYBIT_DEMO_WS_POS_FLAT | sym={sym} | {ctx()}"
                )

    def _handle_order(self, message: dict[str, Any]) -> None:
        """Pybit order-stream callback. Runs in pybit's thread.

        Logs order lifecycle transitions for observability (orderId
        tracking + correlation with execution events). Does NOT trigger
        coordinator close — that's exclusively the execution stream.

        Observability G5 — Bybit's order WS sends transition events
        for ``Created``, ``New``, ``PartiallyFilled``, ``Filled``,
        ``Cancelled``, ``Rejected``, ``Untriggered``, ``Triggered``,
        ``Deactivated``. Before G5, only the three terminal states
        emitted at DEBUG (invisible to INFO log greps). After G5, all
        observable transitions emit at INFO with the full field set
        (side, qty, price, SL/TP, link_id) so operators can trace
        order lifecycle end-to-end and correlate ORD_SEND timing with
        eventual fill timing.
        """
        self._mark_message_received("order")
        try:
            orders = self._extract_data_list(message)
        except Exception as e:
            log.warning(f"BYBIT_DEMO_WS_ORDER_PARSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return
        for order in orders:
            sym = order.get("symbol", "")
            oid = order.get("orderId", "")
            status = order.get("orderStatus", "")
            if not (sym and oid and status):
                continue
            _side = order.get("side", "")
            _qty = order.get("qty", "")
            _price = order.get("price", "") or order.get("avgPrice", "")
            _sl = order.get("stopLoss", "")
            _tp = order.get("takeProfit", "")
            _otype = order.get("orderType", "")
            _link = order.get("orderLinkId", "")
            log.info(
                f"BYBIT_DEMO_WS_ORDER | sym={sym} oid={oid[:12]} "
                f"status={status} side={_side} qty={_qty} price={_price} "
                f"sl_price={_sl} tp_price={_tp} order_type={_otype} "
                f"link_id={_link[:24]} | {ctx()}"
            )

    # ─── Helpers ──────────────────────────────────────────────────────

    def _mark_message_received(self, topic: str) -> None:
        """Atomic counter bumps (single dict-set + int-add are GIL-safe)."""
        self._last_msg_received_mono = time.monotonic()
        self._msg_count_total += 1
        if topic in self._msg_count_by_topic:
            self._msg_count_by_topic[topic] += 1

    @staticmethod
    def _extract_data_list(message: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalise pybit's ``{topic, data: [...]}`` envelope to a list.

        Tolerates both wrapped (``message["data"]``) and direct payload
        shapes — same convention as PriceWorker._handle_ticker_update.
        """
        data = message.get("data", message)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    def _handle_one_execution(self, fill: dict[str, Any]) -> None:
        """Process a single execution event from the data list."""
        symbol = fill.get("symbol", "")
        order_id = fill.get("orderId", "")
        if not symbol or not order_id:
            return

        try:
            closed_size = float(fill.get("closedSize", "0") or 0)
            leaves_qty = float(fill.get("leavesQty", "0") or 0)
            exec_price = float(fill.get("execPrice", "0") or 0)
            exec_qty = float(fill.get("execQty", "0") or 0)
            exec_fee = float(fill.get("execFee", "0") or 0)
            # Phantom-loss fix Commit 1: execPnl is Bybit's own signed NET
            # realized PnL for this fill (fees included) — the authoritative
            # per-close figure that was being received and discarded.
            # execTime (ms epoch) is the correct freshness floor for the
            # REST reconcile (never synthesize a wall clock). Both default
            # to 0 when absent (demo presence unconfirmed — see
            # PHANTOM_LOSS_FIX_DESIGN section 13); a 0 execPnl falls back to
            # reconstruction downstream.
            exec_pnl = float(fill.get("execPnl", "0") or 0)
            exec_time = int(float(fill.get("execTime", "0") or 0))
        except (TypeError, ValueError) as e:
            log.warning(
                f"BYBIT_DEMO_WS_EXEC_FIELD_PARSE_FAIL | sym={symbol} "
                f"oid={order_id[:12]} err='{str(e)[:80]}' | {ctx()}"
            )
            return

        # Phantom-loss fix Commit 1: accumulate the net for EVERY reducing
        # leg (closed_size > 0), BEFORE the early-returns below, so partial
        # tranches contribute even though only the flatting leg dispatches.
        # Opening/modify fills (closed_size <= 0) are not closes — skipped.
        if closed_size > 0 and order_id:
            self._accumulate_close_leg(symbol, order_id, exec_pnl, exec_fee)

        # Only fully-flatting closes trigger on_trade_closed. Partial
        # fills (leaves_qty > 0) leave the position open; the next
        # execution event will arrive when the rest fills.
        if closed_size <= 0:
            # Observability G3 — promoted from DEBUG to INFO so
            # opening fills, reductions, and modifications are visible
            # to log greps. Audit (2026-05-13) noted only
            # BYBIT_DEMO_WS_CLOSE_EVENT was visible at INFO; non-close
            # executions were silent. Adds side / exec_price /
            # exec_qty / exec_fee / exec_type so the emission carries
            # the same shape as BYBIT_DEMO_WS_CLOSE_EVENT for log
            # consumers.
            _side = fill.get("side", "")
            _exec_type = fill.get("execType", "")
            # ``partial=N`` mirrors the CLOSE_EVENT field shape so
            # downstream parsers can iterate all three WS execution
            # emissions (CLOSE_EVENT, EXEC_PARTIAL, EXEC_NON_CLOSE)
            # with a single field-extraction template.
            log.info(
                f"BYBIT_DEMO_WS_EXEC_NON_CLOSE | sym={symbol} oid={order_id[:12]} "
                f"side={_side} exec_price={exec_price} exec_qty={exec_qty} "
                f"exec_fee={exec_fee} closed_size={closed_size} "
                f"exec_type={_exec_type} partial=N | {ctx()}"
            )
            return
        if leaves_qty > 0:
            log.info(
                f"BYBIT_DEMO_WS_EXEC_PARTIAL | sym={symbol} oid={order_id[:12]} "
                f"closed_size={closed_size} leaves={leaves_qty} | {ctx()}"
            )
            return

        # L1 dedup gate
        if self._is_duplicate_close(symbol, order_id):
            self._dedup_count += 1
            log.info(
                f"BYBIT_DEMO_WS_DEDUP | sym={symbol} oid={order_id[:12]} "
                f"reason=duplicate_within_{int(_DEDUP_TTL_SECONDS)}s | {ctx()}"
            )
            return

        stop_order_type = fill.get("stopOrderType", "") or ""
        side = fill.get("side", "")
        # Map stopOrderType to closed_by reason. P2 will refine these
        # to mode-aware labels; for now we use unambiguous, non-mode
        # specific tags.
        if stop_order_type in ("StopLoss", "Stop"):
            closed_by = "bybit_sl_hit"
        elif stop_order_type == "TakeProfit":
            closed_by = "bybit_tp_hit"
        else:
            # No stop-order context — likely a system-initiated close
            # (close_position call) or a manual close via UI. The
            # coordinator's pop_close_reason holds the system-set reason
            # if available; falls back to a neutral label.
            pop_reason = (
                self._coordinator.pop_close_reason(symbol)
                if self._coordinator
                else ""
            )
            closed_by = pop_reason or "bybit_external"

        # Issue 4 fix (2026-05-11): check for a pending partial-close
        # intent set by reduce_position BEFORE the order went out. If
        # present, this execution event is a reduceOnly partial — route
        # it to coordinator.on_partial_close, which records the partial
        # without popping the trade state. The pre-fix bug treated the
        # reduceOnly fill as a full close because order leaves_qty=0
        # always after a market fill (regardless of position residual).
        # The pending entry carries the qty we INTENDED to close; we
        # use the WS-reported closed_size as the authoritative qty in
        # case Bybit clamped or rounded.
        partial_pending = None
        if self._coordinator is not None and hasattr(
            self._coordinator, "pop_partial_close_pending"
        ):
            partial_pending = self._coordinator.pop_partial_close_pending(symbol)

        # Phantom-loss fix Commit 1: drain the per-orderId net accumulator
        # (includes this flatting leg) so the logged net is the whole close,
        # not just the last tranche. Falls back to this leg's own values if
        # the accumulator was empty (final-only delivery). net_exec_pnl /
        # net_exec_fee / exec_time are surfaced for the pre-flip live
        # capture; they are threaded to the coordinator in a later commit.
        net_exec_pnl, net_exec_fee = self._drain_close_accum(
            symbol, order_id, exec_pnl, exec_fee
        )

        log.info(
            f"BYBIT_DEMO_WS_CLOSE_EVENT | sym={symbol} oid={order_id[:12]} "
            f"side={side} exec_price={exec_price} exec_qty={exec_qty} "
            f"exec_fee={exec_fee} closed_size={closed_size} "
            f"exec_pnl={exec_pnl} net_exec_pnl={net_exec_pnl} "
            f"net_exec_fee={net_exec_fee} exec_time={exec_time} "
            f"closed_by={closed_by} "
            f"partial={'Y' if partial_pending else 'N'} | {ctx()}"
        )

        if partial_pending is not None:
            # Partial path — coordinator.on_partial_close keeps the trade
            # state alive (decrements state.size) so the eventual final
            # close fires against the residual.
            self._dispatch_partial_close(
                symbol=symbol,
                closed_qty=float(closed_size),
                exec_price=exec_price,
                closed_by=str(partial_pending.get("by") or "mode4_partial"),
                exec_fee=exec_fee,
                order_id=order_id,
            )
            return

        # Dispatch to coordinator. We pass exec_price (Bybit's
        # authoritative WS fill price) as the exit_price kwarg and rely on
        # the coordinator's sentinel-zero contract: pnl_pct=0 + valid
        # exit_price triggers the back-derive at trade_coordinator.py
        # lines 695-727 (CRITICAL-1 fix), which computes pnl_pct from
        # state.entry_price + close_price + state.side, then back-derives
        # pnl_usd from pnl_pct + state.size at lines 731-740. was_win is
        # flipped inside the coordinator from the back-derived pnl_pct, so
        # the False placeholder we pass here is overwritten before the
        # close-callback fan-out fires.
        #
        # When state is unknown (manual close) the coordinator's existing
        # double-close warn handles it cleanly.
        self._dispatch_close(
            symbol=symbol,
            exit_price=exec_price,
            closed_by=closed_by,
            exec_fee=net_exec_fee,
            order_id=order_id,
            exec_pnl=net_exec_pnl,
            ws_exec_qty=closed_size,
            ws_close_ts=exec_time,
        )

    def _is_duplicate_close(self, symbol: str, order_id: str) -> bool:
        """L1 idempotency gate. Returns True if this (symbol, oid) was
        processed within the dedup TTL window.

        Side effect: prunes stale entries to bound memory. With <100
        closes/hour and 5s TTL, the dict stays under 1 entry on average
        — pruning is cheap insurance.
        """
        key = f"{symbol}|{order_id}"
        now = time.monotonic()
        # Prune anything older than the TTL.
        stale = [k for k, ts in self._processed_closes.items() if now - ts > _DEDUP_TTL_SECONDS]
        for k in stale:
            self._processed_closes.pop(k, None)
        if key in self._processed_closes:
            return True
        self._processed_closes[key] = now
        return False

    def _accumulate_close_leg(
        self, symbol: str, order_id: str, exec_pnl: float, exec_fee: float
    ) -> None:
        """Phantom-loss fix Commit 1: sum execPnl/execFee for one reducing
        leg of a (symbol, orderId) close. TTL-pruned (dedup window) so an
        abandoned/partial ladder cannot leak. Read+cleared by
        _drain_close_accum on the flatting leg.
        """
        now = time.monotonic()
        stale = [
            k for k, v in self._close_pnl_accum.items()
            if now - v.get("ts", now) > _DEDUP_TTL_SECONDS
        ]
        for k in stale:
            del self._close_pnl_accum[k]
        acc = self._close_pnl_accum.get((symbol, order_id))
        if acc is None:
            acc = {"pnl": 0.0, "fee": 0.0, "ts": now}
            self._close_pnl_accum[(symbol, order_id)] = acc
        acc["pnl"] += exec_pnl
        acc["fee"] += exec_fee
        acc["ts"] = now

    def _drain_close_accum(
        self, symbol: str, order_id: str, fallback_pnl: float, fallback_fee: float
    ) -> tuple[float, float]:
        """Phantom-loss fix Commit 1: pop the summed (execPnl, execFee) for
        this (symbol, orderId). Returns the per-leg fallbacks when the key
        is absent (final-only delivery) so there is never an undercount.
        """
        acc = self._close_pnl_accum.pop((symbol, order_id), None)
        if acc is None:
            return fallback_pnl, fallback_fee
        return acc["pnl"], acc["fee"]

    def _dispatch_close(
        self,
        *,
        symbol: str,
        exit_price: float,
        closed_by: str,
        exec_fee: float,
        order_id: str,
        exec_pnl: float = 0.0,
        ws_exec_qty: float = 0.0,
        ws_close_ts: float = 0.0,
    ) -> None:
        """Dispatch coordinator.on_trade_closed onto the project loop.

        Runs from pybit's thread. ``asyncio.run_coroutine_threadsafe``
        schedules the coroutine on ``self._loop``; the future is
        fire-and-forget (we do not block the pybit thread on its
        completion). A dispatch failure (e.g., loop closed during
        shutdown) is logged and counted but does not raise.
        """
        try:
            asyncio.run_coroutine_threadsafe(
                self._call_coordinator_close(
                    symbol=symbol,
                    exit_price=exit_price,
                    closed_by=closed_by,
                    exec_fee=exec_fee,
                    order_id=order_id,
                    exec_pnl=exec_pnl,
                    ws_exec_qty=ws_exec_qty,
                    ws_close_ts=ws_close_ts,
                ),
                self._loop,
            )
        except Exception as e:
            self._dispatch_fail_count += 1
            log.error(
                f"BYBIT_DEMO_WS_DISPATCH_FAIL | sym={symbol} oid={order_id[:12]} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )

    def _dispatch_partial_close(
        self,
        *,
        symbol: str,
        closed_qty: float,
        exec_price: float,
        closed_by: str,
        exec_fee: float,
        order_id: str,
    ) -> None:
        """Issue 4 fix (2026-05-11) — dispatch a partial close.

        Mirrors :meth:`_dispatch_close` but routes to
        ``coordinator.on_partial_close`` instead of
        ``coordinator.on_trade_closed``. Fire-and-forget on the project
        loop; dispatch failures are logged and counted.
        """
        try:
            asyncio.run_coroutine_threadsafe(
                self._call_coordinator_partial_close(
                    symbol=symbol,
                    closed_qty=closed_qty,
                    exec_price=exec_price,
                    closed_by=closed_by,
                    exec_fee=exec_fee,
                    order_id=order_id,
                ),
                self._loop,
            )
        except Exception as e:
            self._dispatch_fail_count += 1
            log.error(
                f"BYBIT_DEMO_WS_PARTIAL_DISPATCH_FAIL | sym={symbol} "
                f"oid={order_id[:12]} err='{str(e)[:120]}' | {ctx()}"
            )

    async def _call_coordinator_partial_close(
        self,
        *,
        symbol: str,
        closed_qty: float,
        exec_price: float,
        closed_by: str,
        exec_fee: float,
        order_id: str,
    ) -> None:
        """Issue 4 fix — call coordinator.on_partial_close in the project loop.

        on_partial_close builds a record with size=closed_qty and PnL
        computed on the closed-portion notional, fires the partial-close
        callback list (a strict subset of the full close-callback list:
        only trade_history + trade_log writers in the first ship),
        decrements state.size, and does NOT pop the trade state. The
        eventual final close (whether SL/TP hit on the residual, or
        another partial that brings size to 0) routes through the
        existing on_trade_closed path against the reduced state.size.
        """
        try:
            self._coordinator.on_partial_close(
                symbol=symbol,
                closed_qty=closed_qty,
                exec_price=exec_price,
                closed_by=closed_by,
                price_source="bybit_ws_authoritative",
            )
        except Exception as e:
            log.error(
                f"BYBIT_DEMO_WS_PARTIAL_COORD_FAIL | sym={symbol} "
                f"oid={order_id[:12]} err='{str(e)[:200]}' | {ctx()}"
            )

    async def _call_coordinator_close(
        self,
        *,
        symbol: str,
        exit_price: float,
        closed_by: str,
        exec_fee: float,
        order_id: str,
        exec_pnl: float = 0.0,
        ws_exec_qty: float = 0.0,
        ws_close_ts: float = 0.0,
    ) -> None:
        """Call coordinator.on_trade_closed in the project loop.

        on_trade_closed is sync; the await here is just to schedule
        on the loop. pnl_pct=0 / pnl_usd=0 / was_win=False are
        sentinel placeholders — the coordinator back-derives pnl_pct
        from state.entry_price + exit_price + state.side at
        trade_coordinator.py:695-727 (CRITICAL-1 fix), then back-derives
        pnl_usd from pnl_pct + state.size at lines 731-740, then flips
        was_win from the back-derived value before the close-callback
        fan-out fires. The exec_fee is not yet threaded through (P3
        will widen the coordinator signature to accept fee-inclusive
        net PnL); for P1 we pass the gross exit and let the
        coordinator's back-derive produce gross PnL too.

        For unregistered trades (manual close, race) the coordinator's
        existing double-close guard at trade_coordinator.py:670-675
        warns and returns; for registered trades the back-derived
        pnl_pct determines win/loss downstream consistently.
        """
        try:
            # PnL-truth fix (2026-05-26): resolve the exchange's real net
            # closedPnl before booking, instead of passing a zero sentinel
            # that made the coordinator back-derive a gross, fee-free
            # number. close_with_authoritative_pnl resolves via the
            # coordinator's transformer (Bybit /v5/position/closed-pnl) and
            # falls back to the prior gross back-derive if the exchange has
            # no data yet. exec_fee is retained in the signature for the
            # caller but is now subsumed by the net closedPnl figure.
            # Phantom-loss fix Commit 3: thread the in-hand WS net fill so the
            # coordinator books the real net AND runs the staleness gate with
            # a trusted reference. close_pnl_source (default 'legacy') is read
            # defensively so this works before/after the settings field lands
            # (commit 5).
            _close_mode = getattr(
                getattr(self._settings, "bybit_demo", None),
                "close_pnl_source",
                "legacy",
            )
            await self._coordinator.close_with_authoritative_pnl(
                symbol=symbol,
                exit_price=exit_price,
                closed_by=closed_by,
                exec_pnl=exec_pnl,
                exec_fee=exec_fee,
                ws_order_id=order_id,
                ws_exec_qty=ws_exec_qty,
                ws_close_ts=ws_close_ts,
                close_pnl_source=_close_mode,
            )
        except Exception as e:
            log.error(
                f"BYBIT_DEMO_WS_COORD_CALL_FAIL | sym={symbol} oid={order_id[:12]} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )
