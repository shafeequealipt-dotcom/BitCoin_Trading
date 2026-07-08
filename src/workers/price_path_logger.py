"""System 2 (observability) — per-second open-trade price path logger.

A lightweight background task that records each open trade's price about once
per second, from entry to close, to a dedicated rotated file (price_path.log),
so the coming exit calibration can replay arm/lock/trail settings against the
real intrabar price path.

Observability only. It changes no trading behaviour, makes ZERO new exchange
API calls — it reads PriceWorker's already-running in-memory WebSocket quote
cache via ``get_ws_quote`` (a pure dict read) — and never blocks or slows the
exit tick: it is a separate asyncio task, reads coordinator and price state
only (never a mutator), and every write is fire-and-forget through loguru's
``enqueue=True`` sink. It never touches the trading database or any protected
table; its only sink is the dedicated ``price_path.log``.

Design: a ~1s loop snapshots the coordinator's open trades, reads each symbol's
freshest WebSocket price (skipping when stale — an honest gap, never a
fabricated point), computes unrealized PnL from the in-memory entry and side,
deduplicates to one point per second per trade, buffers in memory, and flushes
in batches (a periodic safety flush, plus a final flush when a trade closes).
Buffered-but-unflushed points are bounded by the flush cadence and lost only on
a hard crash — the spec's preferred memory-buffer-plus-batch-flush tradeoff.

Concurrency: the sampling loop and the close callback both run on the asyncio
event-loop thread (the coordinator invokes close callbacks synchronously from
the same loop the workers run on), and ``_sample_once`` / the close handler
contain no ``await`` between their reads and mutations, so they cannot
interleave — no lock is needed. Per-symbol and per-write failures are isolated
so one bad symbol or one failed emit never breaks the tick or the trade path.
"""
import asyncio
import time
from datetime import datetime, timezone

from src.core.log_context import ctx
from src.core.logging import get_logger

# Operational / diagnostic lines route to workers.log; the per-second price
# points route to the dedicated, rotated price_path.log so the replay tool has
# a clean single-purpose file.
log = get_logger("worker")
pp = get_logger("price_path")


class PricePathLogger:
    """Samples each open trade's WS price ~once per second to price_path.log."""

    def __init__(self, price_worker, trade_coordinator, obs_settings) -> None:
        self._pw = price_worker
        self._coord = trade_coordinator
        self._resolution = max(
            0.1, float(getattr(obs_settings, "price_path_resolution_seconds", 1.0))
        )
        self._flush_seconds = max(
            1, int(getattr(obs_settings, "price_path_flush_seconds", 30))
        )
        # The WS quote must be at least as fresh as our sampling cadence; never
        # accept a quote older than ws_max_age (default 5s) — that would be a
        # repeated stale value, not a real per-second point.
        self._ws_max_age = max(
            self._resolution,
            float(getattr(obs_settings, "price_path_ws_max_age_seconds", 5.0)),
        )
        # per-trade-id buffer of pre-formatted point lines, awaiting flush.
        self._buffers: dict[str, list[str]] = {}
        # per-trade-id last emitted integer second (per-second dedup).
        self._last_second: dict[str, int] = {}
        # symbol -> {tid, entry, side}, resolved once when a trade is first seen.
        self._meta: dict[str, dict] = {}
        self._last_flush: float = time.monotonic()
        self._points_emitted: int = 0

    # ── identity + math helpers ───────────────────────────────────────────
    @staticmethod
    def _tid(state) -> str:
        """Stable per-trade id for the life of the trade.

        Prefers ``brain_decision_id`` (which equals the coordinator's close
        record ``trade_id`` for brain trades, giving exact correlation), then
        ``order_id``, then ``symbol-opened_at`` so a same-symbol re-open is a
        distinct trade.
        """
        bid = getattr(state, "brain_decision_id", "") or ""
        if bid:
            return bid
        oid = getattr(state, "order_id", "") or ""
        if oid:
            return oid
        return f"{getattr(state, 'symbol', '?')}-{int(getattr(state, 'opened_at', 0) or 0)}"

    @staticmethod
    def _is_long(side: str) -> bool:
        return (side or "").lower() in ("buy", "long")

    def _format_point(self, ts_iso: str, sym: str, tid: str, price: float,
                      pnl: float, final: bool = False) -> str:
        tail = " close=Y" if final else ""
        return (f"PRICE_PATH | ts={ts_iso} sym={sym} tid={tid} "
                f"px={price:.8g} pnl={pnl:+.4f}%{tail}")

    # ── sampling ──────────────────────────────────────────────────────────
    def _sample_once(self) -> None:
        coord = self._coord
        pw = self._pw
        try:
            symbols = coord.active_symbols()
        except Exception:
            return
        now = time.time()
        sec = int(now)
        ts_iso = datetime.fromtimestamp(now, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
        for sym in symbols:
            try:
                st = coord.get_trade_state(sym)
                if st is None:
                    continue
                tid = self._tid(st)
                meta = self._meta.get(sym)
                if meta is None or meta.get("tid") != tid:
                    # New trade, or a same-symbol re-open: finalize the old
                    # trade's buffer (flush + drop) and start the new one.
                    if meta is not None:
                        self._finalize(meta["tid"])
                    meta = {
                        "tid": tid,
                        "entry": float(getattr(st, "entry_price", 0.0) or 0.0),
                        "side": getattr(st, "side", "") or "",
                    }
                    self._meta[sym] = meta
                    self._buffers.setdefault(tid, [])
                entry = meta["entry"]
                if entry <= 0:
                    continue
                price = pw.get_ws_quote(sym, max_age_s=self._ws_max_age)
                if price is None or price <= 0:
                    # Stale/missing WS quote — an honest gap, never fabricated.
                    continue
                if self._last_second.get(tid) == sec:
                    continue  # one point per second per trade
                self._last_second[tid] = sec
                if self._is_long(meta["side"]):
                    pnl = (price - entry) / entry * 100.0
                else:
                    pnl = (entry - price) / entry * 100.0
                self._buffers.setdefault(tid, []).append(
                    self._format_point(ts_iso, sym, tid, price, pnl)
                )
            except Exception:
                # Per-symbol isolation — one bad symbol never breaks the tick.
                continue

    # ── flushing ──────────────────────────────────────────────────────────
    def _flush_tid(self, tid: str) -> None:
        lines = self._buffers.get(tid)
        if not lines:
            return
        for line in lines:
            pp.info(line)  # enqueue=True — non-blocking, fire-and-forget
            self._points_emitted += 1
        lines.clear()

    def _flush_all(self) -> None:
        for tid in list(self._buffers.keys()):
            self._flush_tid(tid)
        self._last_flush = time.monotonic()

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush >= self._flush_seconds:
            self._flush_all()

    def _finalize(self, tid: str) -> None:
        """Flush and drop all per-trade state for a finished/superseded trade."""
        self._flush_tid(tid)
        self._buffers.pop(tid, None)
        self._last_second.pop(tid, None)

    # ── lifecycle ─────────────────────────────────────────────────────────
    def on_trade_closed(self, record: dict) -> None:
        """Close callback (sync, fire-and-forget). Final-flush this trade's path.

        Registered via ``TradeCoordinator.register_close_callback``. The
        coordinator invokes every close callback inside its own per-callback
        try/except, and this body is additionally guarded, so a failure here can
        never disturb the close path or the other callbacks.
        """
        try:
            sym = record.get("symbol")
            if not sym:
                return
            meta = self._meta.pop(sym, None)
            if meta is None:
                return
            tid = meta["tid"]
            # Optional final point at the exact close price/pnl so the path runs
            # to the close even if the last ~1s sample missed it.
            try:
                close_price = float(record.get("close_price") or 0.0)
                if close_price > 0:
                    ts_iso = datetime.now(timezone.utc).isoformat(
                        timespec="milliseconds"
                    )
                    pnl = float(record.get("pnl_pct") or 0.0)
                    self._buffers.setdefault(tid, []).append(
                        self._format_point(ts_iso, sym, tid, close_price, pnl,
                                            final=True)
                    )
            except Exception:
                pass
            self._finalize(tid)
        except Exception as e:
            log.warning(f"PRICE_PATH_CLOSE_CB_FAIL | err='{str(e)[:150]}' | {ctx()}")

    async def run(self) -> None:
        """The ~1s sampling loop. Cancelled on shutdown with a final flush."""
        log.info(
            f"PRICE_PATH_LOGGER_START | resolution_s={self._resolution} "
            f"flush_s={self._flush_seconds} ws_max_age_s={self._ws_max_age} | {ctx()}"
        )
        try:
            while True:
                try:
                    self._sample_once()
                    self._maybe_flush()
                except Exception as e:
                    log.warning(
                        f"PRICE_PATH_TICK_FAIL | err='{str(e)[:150]}' | {ctx()}"
                    )
                await asyncio.sleep(self._resolution)
        except asyncio.CancelledError:
            try:
                self._flush_all()
            except Exception:
                pass
            log.info(
                f"PRICE_PATH_LOGGER_STOP | points_emitted={self._points_emitted} "
                f"| {ctx()}"
            )
            raise
