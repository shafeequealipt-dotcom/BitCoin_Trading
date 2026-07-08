"""Price worker: real-time price streaming via Bybit WebSocket.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Subscribes to all 50 coins in ``config.universe.watch_list``.
- Stays on the existing fixed-interval ``BaseWorker`` tick (default 45 s)
  because the WebSocket stream is continuous — the tick body is a
  connection health/reconnect loop, not a data fetch. Sweet-spot
  scheduling would slow failover detection without benefit.
"""

import asyncio
import time as _time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import Ticker, WorkerTier
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.trading.websocket import BybitWebSocket
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class PriceWorker(BaseWorker):
    """Streams real-time prices via Bybit WebSocket.

    Unlike polling workers, this one maintains a persistent WebSocket
    connection. tick() manages the connection, callbacks process data.

    Args:
        settings: Application settings.
        db: Database manager.
        ws: Bybit WebSocket client.
        scanner: Retained as None-safe legacy injection (not read by tick);
            slated for removal in a future cleanup phase.
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1A

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        ws: BybitWebSocket, scanner=None,
        ticker_buffer=None,
    ) -> None:
        super().__init__(
            name="price_worker",
            interval_seconds=float(settings.workers.market_data_interval),
            settings=settings,
            db=db,
        )
        self.ws = ws
        self.market_repo = MarketRepository(db)
        self._scanner = scanner  # legacy injection; not read by tick()
        # Issue 2 of cascade-fix series (2026-05-10): when a
        # TickerCacheBuffer is injected, the WS callback no longer
        # schedules a per-message save_ticker via
        # asyncio.run_coroutine_threadsafe — it puts into the buffer
        # instead. The buffer's drainer batches writes via
        # MarketRepository.save_tickers_batch, dropping DB write rate
        # from ~180/sec to ~2/sec (one per 500ms flush). When None
        # (legacy callers / tests), the per-message path is preserved
        # for backward compat.
        self._ticker_buffer = ticker_buffer
        # Pre-seed tracked symbols from settings.universe.watch_list so the
        # first tick's same-universe check avoids a spurious reconnect log.
        # Same pattern as KlineWorker's __init__ seed.
        self._tracked_symbols: list[str] = list(settings.universe.watch_list)
        self._connected = False
        self._dropped_count: int = 0
        # Phase 6: in-memory last-quote cache. Populated on every WS tick
        # so APEX / assembler / any caller can consult the fresh WS price
        # without a REST hop. {sym: (last_price, monotonic_ts)}. Dict
        # assignment is GIL-atomic for a single key so no lock is needed.
        self._ws_quotes: dict[str, tuple[float, float]] = {}
        # Phase 12 (post-Layer-1 fix): per-tick WS message counter so the
        # ``PRICE_WS_HEALTH`` heartbeat can report msg/min throughput,
        # not just connectivity. Reset to 0 each time the heartbeat
        # emits (every tick — `interval_seconds` from settings, default
        # 45s).
        self._ws_msg_count: int = 0
        self._ws_health_last_emit: float = _time.monotonic()
        # Phase 3 of the price-source-divergence fix (2026-05-03):
        # captured reference to the asyncio event loop so the WS
        # callback (which runs on a pybit thread-pool thread without
        # an asyncio loop) can use ``asyncio.run_coroutine_threadsafe``
        # to schedule the ``save_ticker`` persistence on the main loop.
        # See ``_handle_ticker_update`` and ``_on_save_ticker_done``
        # below for the full bridge. The loop is captured in ``tick()``
        # because ``__init__`` runs before the asyncio loop is started.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Phase 3: per-tick counter for WS-driven ``save_ticker`` failures
        # so operators can detect persistence regressions without
        # grep'ing every PRICE_WS_PERSIST_FAIL line. Reset alongside
        # ``_ws_msg_count`` on the heartbeat emission.
        self._ws_persist_fail_count: int = 0
        # Phase 12.1 (lifecycle-logging-audit Gap 1.1-G2): per-tick counters
        # for invalid-price skips and pre-loop persistence skips. Both were
        # previously emitted as per-event DEBUG (invisible at default INFO
        # sink). Rolled into PRICE_WS_HEALTH so the operator sees the rate
        # without per-event noise.
        self._invalid_skips_count: int = 0
        self._persist_noloop_count: int = 0

    async def tick(self) -> None:
        """Maintain WebSocket connection and subscriptions.

        Corrected Layer 1 (HR-1 / HR-5): universe is the full
        ``settings.universe.watch_list`` (50 coins). PriceWorker stays
        on its existing fixed-interval (default 45 s) tick because the
        WebSocket stream is continuous — the tick body is a connection
        health/reconnect loop, not a data fetch. Sweet-spot scheduling
        would slow down failover detection without benefit.

        UniverseSettings.__post_init__ validates watch_list at startup
        so this read is always non-empty under normal config.
        """
        # Phase 3 (price-source fix 2026-05-03): capture the running event
        # loop on the first tick so the WS callback (which fires on a
        # pybit thread-pool thread without an asyncio loop) can use
        # ``asyncio.run_coroutine_threadsafe`` to schedule the
        # ``save_ticker`` coroutine. ``__init__`` runs before the loop
        # is up so the capture must happen here, not in the constructor.
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                # Defensive — ``tick()`` runs inside ``BaseWorker.run``
                # which is itself an asyncio coroutine, so the loop
                # MUST be available here. If somehow it isn't, log
                # loud and continue; the WS callback will see
                # ``self._loop is None`` and skip persistence (logging
                # ``PRICE_WS_PERSIST_NOLOOP``) rather than crash.
                log.error(
                    f"PRICE_WS_LOOP_CAPTURE_FAIL | reason=no_running_loop | {ctx()}"
                )

        # Issue 2 of cascade-fix series (2026-05-10): start the
        # TickerCacheBuffer drainer once we have an event loop. The
        # buffer is constructed before the loop exists (in
        # WorkerManager._setup); start() schedules the drainer task on
        # the running loop. Idempotent — safe to re-call on every tick.
        if self._ticker_buffer is not None:
            await self._ticker_buffer.start()

        universe = list(self.settings.universe.watch_list)
        if not universe:
            log.warning(
                f"PRICE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return

        if set(universe) != set(self._tracked_symbols):
            self._tracked_symbols = universe
            # Reconnect WebSocket with new symbols. pybit has no
            # unsubscribe primitive — full reconnect is the only mechanism
            # to drop the old subscription set, and the
            # ``_handle_ticker_update`` callback will only populate
            # ``_ws_quotes`` for the new set going forward.
            if self._connected:
                self._connected = False

        if not self._connected:
            await self.ws.connect_public()
            self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)
            self._connected = True
            # Phase 3 (P0-2 Fix C): emit a sample of subscribed symbols so
            # operators can verify the active universe is fully covered. The
            # earlier APEX_PRICE_FALLBACK observations on coins like SOONUSDT
            # / DOTUSDT / RIVERUSDT pointed at universe-vs-subscription gaps
            # — surfacing the actual list makes the gap visible at the next
            # WS reconnect rather than discovered later via fallback.
            _sample = ",".join(self._tracked_symbols[:10])
            _suffix = "..." if len(self._tracked_symbols) > 10 else ""
            log.info(
                f"PRICE_WS_CONN | symbols={len(self._tracked_symbols)} "
                f"sample=[{_sample}{_suffix}] | {ctx()}"
            )
        else:
            # Connection health check — if ws dropped, reconnect next tick.
            # Reconnect re-runs the full subscribe_ticker(self._tracked_symbols)
            # block above, so every active-universe symbol is re-subscribed
            # together (no per-symbol re-subscribe drift).
            if not self.ws.is_running:
                log.warning(f"PRICE_WS_DISC | rsn=ws_not_running | {ctx()}")
                self._connected = False

        # Phase 12 (post-Layer-1 fix): periodic WS health heartbeat with
        # message-per-minute throughput. Pre-fix, the only WS observability
        # was reconnect / disconnect events — operators couldn't tell a
        # quiet-but-healthy stream from a hung-but-still-connected stream.
        # Emit once per tick (every interval_seconds; default 45 s) and
        # reset the counter so the next emission reports throughput over
        # the just-ended interval.
        now_mono = _time.monotonic()
        elapsed_s = max(now_mono - self._ws_health_last_emit, 0.001)
        msgs_per_min = (self._ws_msg_count / elapsed_s) * 60.0
        # Phase 3 of the price-source-divergence fix added
        # ``persist_fails_in_window`` so operators can detect a regression
        # in the WS->ticker_cache write path immediately. In steady state
        # this should be zero; non-zero means save_ticker is failing
        # (DB lock, disk full, schema drift, etc.) and warrants
        # investigation.
        log.info(
            f"PRICE_WS_HEALTH | "
            f"status={'connected' if self._connected and self.ws.is_running else 'disconnected'} "
            f"msgs_per_min={msgs_per_min:.0f} "
            f"msgs_in_window={self._ws_msg_count} "
            f"persist_fails_in_window={self._ws_persist_fail_count} "
            f"invalid_skips_in_window={self._invalid_skips_count} "
            f"persist_noloop_in_window={self._persist_noloop_count} "
            f"window_s={elapsed_s:.1f} "
            f"subscribed={len(self._tracked_symbols)} "
            f"quotes_cached={len(self._ws_quotes)} | {ctx()}"
        )
        self._ws_msg_count = 0
        self._ws_persist_fail_count = 0
        self._invalid_skips_count = 0
        self._persist_noloop_count = 0
        self._ws_health_last_emit = now_mono

    def _handle_ticker_update(self, data: dict) -> None:
        """Process incoming ticker data from WebSocket callback.

        Args:
            data: Raw WebSocket message.
        """
        try:
            tick_data = data.get("data", data)
            if isinstance(tick_data, list):
                tick_data = tick_data[0] if tick_data else {}

            symbol = tick_data.get("symbol", "")
            if not symbol:
                return

            def _sf(val, default=0.0):
                """Safe float — handles None, empty strings."""
                if not val and val != 0:
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            last_price = _sf(tick_data.get("lastPrice"))
            if last_price <= 0:
                # Phase 12.1 (lifecycle-logging-audit Gap 1.1-G2): rolled
                # into PRICE_WS_HEALTH `invalid_skips_in_window=N` so the
                # rate is visible without per-event DEBUG (which was
                # invisible at default INFO sink anyway).
                self._invalid_skips_count += 1
                return  # Skip update with zero/invalid price

            # Phase 6: update in-memory quote cache for APEX / assembler.
            self._ws_quotes[symbol] = (last_price, _time.monotonic())
            # Phase 12 (post-Layer-1 fix): bump WS message counter for
            # the periodic PRICE_WS_HEALTH heartbeat. GIL-atomic int
            # increment; safe under the WS callback's thread.
            self._ws_msg_count += 1

            ticker = Ticker(
                symbol=symbol,
                last_price=last_price,
                bid=_sf(tick_data.get("bid1Price")),
                ask=_sf(tick_data.get("ask1Price")),
                high_24h=_sf(tick_data.get("highPrice24h")),
                low_24h=_sf(tick_data.get("lowPrice24h")),
                volume_24h=_sf(tick_data.get("volume24h")),
                change_24h_pct=_sf(tick_data.get("price24hPcnt")) * 100,
                timestamp=now_utc(),
            )

            # Issue 2 of cascade-fix series (2026-05-10): when a
            # TickerCacheBuffer is wired, the WS callback puts into the
            # buffer instead of scheduling a per-message save_ticker
            # via run_coroutine_threadsafe. ``buffer.put`` is sync,
            # thread-safe, and O(1) — no event loop required. The
            # buffer's drainer batches writes via
            # MarketRepository.save_tickers_batch every flush_interval
            # (default 500ms), dropping DB write rate from ~180/sec to
            # ~2/sec. Phase 0 baseline measured 99.7% of DB_LOCK_WAIT
            # events held by ticker_cache writes — this batching is
            # the cascade fix.
            #
            # When the buffer is None (legacy callers / tests), the
            # original Phase 3 (price-source-divergence fix) per-message
            # path is preserved for backward compat.
            if self._ticker_buffer is not None:
                # Synchronous, thread-safe — no asyncio loop needed.
                self._ticker_buffer.put(ticker)
            else:
                # Legacy per-message path. Same Phase 3 logic as before:
                # bridge the WS thread-pool callback to the asyncio
                # event loop via ``asyncio.run_coroutine_threadsafe``;
                # done-callback re-raises any exception so persistence
                # failures fire ``PRICE_WS_PERSIST_FAIL`` instead of
                # being silently lost.
                loop = self._loop
                if loop is None:
                    # Phase 12.1 (lifecycle-logging-audit Gap 1.1-G2):
                    # rolled into PRICE_WS_HEALTH
                    # `persist_noloop_in_window=N` so the rate is
                    # visible without per-event DEBUG.
                    self._persist_noloop_count += 1
                elif loop.is_closed():
                    # Shutdown race: loop closed but WS callback still
                    # firing on its thread. Skip silently — exiting.
                    pass
                else:
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            self.market_repo.save_ticker(ticker), loop,
                        )
                        future.add_done_callback(self._on_save_ticker_done)
                    except Exception as e:
                        # run_coroutine_threadsafe can raise if the loop
                        # is in an invalid state. Never silently
                        # swallowed — this is a real failure operators
                        # must see.
                        self._ws_persist_fail_count += 1
                        log.error(
                            f"PRICE_WS_PERSIST_SCHEDULE_FAIL | sym={symbol} "
                            f"err='{str(e)[:120]}' | {ctx()}"
                        )

        except Exception as e:
            self._dropped_count += 1
            # Phase 9 Gap A4 (output-quality obs): per-drop structured tag
            # with cumulative count gives operators per-symbol visibility.
            # Phase 12.1 (lifecycle-logging-audit Gap 1.1-G1): removed the
            # 3 prose duplicates (`log.debug Price update`, `log.error
            # Price worker callback error`, every-50 rollup) — all info
            # already in PRICE_WS_TICK_FAIL `cumulative_dropped` field.
            log.warning(
                f"PRICE_WS_TICK_FAIL | sym={symbol} "
                f"err='{str(e)[:80]}' cumulative_dropped={self._dropped_count} | {ctx()}"
            )

    def _on_save_ticker_done(self, future: asyncio.Future) -> None:
        """Done-callback for the ``run_coroutine_threadsafe`` save_ticker future.

        Phase 3 of the price-source-divergence fix (2026-05-03)
        replaced the previous silently-swallowed ``loop.create_task``
        pattern with an ``asyncio.run_coroutine_threadsafe`` bridge.
        This callback retrieves the future's result so any exception
        inside ``save_ticker`` is logged via ``PRICE_WS_PERSIST_FAIL``
        instead of silently lost. Hard rule 5 (production-quality
        code): exception handling must fail LOUDLY when failure is
        unexpected; this helper is what makes the loud part real.

        ``future.result()`` re-raises any exception the awaited
        coroutine raised. ``asyncio.CancelledError`` is treated as a
        non-error (the loop was cancelled mid-flight; the worker is
        shutting down).

        Args:
            future: The ``concurrent.futures.Future`` returned by
                ``run_coroutine_threadsafe``. Its ``result()`` blocks
                briefly to retrieve the coroutine's return value (None
                for ``save_ticker``) or re-raise its exception.
        """
        try:
            future.result()
        except asyncio.CancelledError:
            # Loop cancelled — worker is shutting down. Not an error.
            return
        except Exception as e:
            # GIL-atomic int increment is safe under the WS callback's
            # thread (this method is called by the loop thread, not
            # the WS thread, but the increment is still atomic).
            self._ws_persist_fail_count += 1
            log.warning(
                f"PRICE_WS_PERSIST_FAIL | err='{str(e)[:160]}' "
                f"cumulative_persist_fails={self._ws_persist_fail_count} | {ctx()}"
            )

    def get_ws_quote(self, symbol: str, max_age_s: float = 5.0) -> float | None:
        """Return the freshest WS last-price for *symbol*, or None if stale.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            max_age_s: Maximum age of the cached quote in seconds. Default 5s
                       matches assembler's tolerance for "live" price.

        Returns:
            last_price float when cached and fresh, else None so the caller
            can fall back to REST ticker.
        """
        quote = self._ws_quotes.get(symbol)
        if not quote:
            return None
        price, ts = quote
        if _time.monotonic() - ts > max_age_s:
            return None
        return price if price > 0 else None


    async def cleanup(self) -> None:
        """Disconnect WebSocket on stop."""
        if self._connected:
            await self.ws.disconnect()
            self._connected = False
        # Issue 2 of cascade-fix series (2026-05-10): drain and stop
        # the TickerCacheBuffer cleanly. ``stop`` performs a final flush
        # so no pending puts are orphaned at shutdown.
        if self._ticker_buffer is not None:
            try:
                await self._ticker_buffer.stop()
            except Exception as e:
                log.warning(
                    f"TICKER_BUFFER_STOP_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )
