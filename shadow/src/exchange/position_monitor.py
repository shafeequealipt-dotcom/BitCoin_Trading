"""Position monitor — watches open positions and auto-closes on SL/TP hit.

Runs as a 1-second async loop. For each open position:
  - Calculates unrealized PnL from live WebSocket prices
  - Tracks peak PnL, max drawdown, time in profit/loss
  - Checks if price has hit stop loss or take profit
  - Auto-closes via order engine when SL/TP triggers
  - Flushes tracking data to DB every 30 seconds
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from src.database.connection import DatabaseManager
from src.exchange.order_engine import OrderEngine
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("exchange.monitor")

FLUSH_INTERVAL = 30  # seconds between DB flushes
MAX_CLOSE_RETRIES = 3
POSITION_LOG_INTERVAL = 60  # log each position status every 60s


class PositionMonitor:
    """Monitors open positions and auto-closes when SL/TP is hit.

    Args:
        db: Connected DatabaseManager instance.
        order_engine: OrderEngine for closing positions.
        price_fn: Callable returning price data dict for a symbol.
        config: Shadow configuration.
    """

    def __init__(
        self,
        db: DatabaseManager,
        order_engine: OrderEngine,
        price_fn: Callable[[str], dict[str, Any] | None],
        config: ShadowConfig,
    ) -> None:
        self._db = db
        self._engine = order_engine
        self._price_fn = price_fn
        self._interval = config.exchange.position_monitor_interval

        # In-memory tracking per position_id
        self._tracking: dict[str, dict[str, Any]] = {}
        self._running = False
        self._last_flush = time.time()

        # Per-position last log time (to avoid spamming every second)
        self._last_position_log: dict[str, float] = {}

        # Stats
        self._stats = {
            "checks_total": 0,
            "sl_triggered": 0,
            "tp_triggered": 0,
            "cycles": 0,
        }

    # ─── Main loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main monitoring loop — runs every 1 second until cancelled."""
        self._running = True
        log.info("Position monitor started (interval: {sec}s)", sec=self._interval)

        try:
            while self._running:
                try:
                    await self._check_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("Monitor cycle error: {err}", err=str(e))
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass
        finally:
            # Final flush on shutdown
            await self._flush_tracking()
            log.info(
                "Position monitor stopped. SL:{sl} TP:{tp} total checks:{chk}",
                sl=self._stats["sl_triggered"],
                tp=self._stats["tp_triggered"],
                chk=self._stats["checks_total"],
            )

    def get_stats(self) -> dict[str, Any]:
        """Return monitoring statistics."""
        return {
            "running": self._running,
            "positions_monitored": len(self._tracking),
            "total_checks": self._stats["checks_total"],
            "total_cycles": self._stats["cycles"],
            "sl_triggered": self._stats["sl_triggered"],
            "tp_triggered": self._stats["tp_triggered"],
            "last_flush_ago": time.time() - self._last_flush,
        }

    # ─── Check cycle (runs every 1 second) ──────────────────────────────

    async def _check_cycle(self) -> None:
        """One iteration of the monitoring loop."""
        # Step 1: Get all open positions
        positions = await self._db.fetch_all(
            "SELECT * FROM virtual_positions WHERE status = 'open'"
        )

        if not positions:
            # Clear any stale tracking entries (positions closed externally)
            if self._tracking:
                self._tracking.clear()
                self._last_position_log.clear()
            self._stats["cycles"] += 1
            return

        # Step 2: Sync tracking dict
        open_ids = {p["position_id"] for p in positions}

        # Add new positions to tracking
        for pos in positions:
            pid = pos["position_id"]
            if pid not in self._tracking:
                self._tracking[pid] = {
                    "peak_pnl_pct": pos.get("peak_pnl_pct") or 0.0,
                    "max_drawdown_pct": pos.get("max_drawdown_pct") or 0.0,
                    "time_in_profit": pos.get("time_in_profit_seconds") or 0,
                    "time_in_loss": pos.get("time_in_loss_seconds") or 0,
                    "close_retries": 0,
                }

        # Remove closed positions from tracking
        stale_ids = [pid for pid in self._tracking if pid not in open_ids]
        for pid in stale_ids:
            del self._tracking[pid]
            self._last_position_log.pop(pid, None)

        # Step 3: Check each position
        for pos in positions:
            await self._check_position(pos)

        # Step 4: Periodic flush
        if time.time() - self._last_flush >= FLUSH_INTERVAL:
            await self._flush_tracking()

        self._stats["cycles"] += 1

    # ─── Check single position ──────────────────────────────────────────

    async def _check_position(self, position: dict[str, Any]) -> None:
        """Check a single position against current price for SL/TP."""
        pid = position["position_id"]
        symbol = position["symbol"]
        side = position["side"]
        entry_price = position["entry_price"]
        notional = position["notional_value"]
        track = self._tracking.get(pid)

        if track is None:
            return

        # Skip if max retries exceeded
        if track["close_retries"] >= MAX_CLOSE_RETRIES:
            return

        # Step 1: Get current price
        price_data = self._price_fn(symbol)
        if price_data is None:
            return

        current_price = float(price_data["last"])
        if current_price <= 0:
            return

        # Step 2: Calculate unrealized PnL
        if side == "Buy":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # Step 3: Update tracking
        if pnl_pct > track["peak_pnl_pct"]:
            track["peak_pnl_pct"] = pnl_pct

        if pnl_pct < track["max_drawdown_pct"]:
            track["max_drawdown_pct"] = pnl_pct

        if pnl_pct > 0:
            track["time_in_profit"] += self._interval
        else:
            track["time_in_loss"] += self._interval

        self._stats["checks_total"] += 1

        # Step 4: Check Stop Loss (checked FIRST — takes priority)
        sl = position["stop_loss_price"]
        if sl is not None and sl > 0:
            sl_hit = False
            if side == "Buy" and current_price <= sl:
                sl_hit = True
            elif side == "Sell" and current_price >= sl:
                sl_hit = True

            if sl_hit:
                log.warning(
                    "SL HIT: {sym} {side} SL=${sl:,.4f} price=${price:,.4f} PnL={pnl:+.2f}%",
                    sym=symbol, side=side, sl=sl, price=current_price, pnl=pnl_pct,
                )
                # Close at current_price (realistic — price may have gapped past SL)
                await self._trigger_close(position, current_price, "sl_hit")
                return

        # Step 5: Check Take Profit
        tp = position["take_profit_price"]
        if tp is not None and tp > 0:
            tp_hit = False
            if side == "Buy" and current_price >= tp:
                tp_hit = True
            elif side == "Sell" and current_price <= tp:
                tp_hit = True

            if tp_hit:
                log.info(
                    "TP HIT: {sym} {side} TP=${tp:,.4f} price=${price:,.4f} PnL={pnl:+.2f}%",
                    sym=symbol, side=side, tp=tp, price=current_price, pnl=pnl_pct,
                )
                # Close at TP price (optimistic fill at target)
                await self._trigger_close(position, tp, "tp_hit")
                return

        # Step 6: Periodic position logging (every 60s per position)
        now = time.time()
        last_log = self._last_position_log.get(pid, 0)
        if now - last_log >= POSITION_LOG_INTERVAL:
            pnl_usd = pnl_pct / 100 * notional
            log.debug(
                "Monitor: {sym} {side} PnL={pnl:+.2f}% (${pnl_usd:+,.2f}) "
                "peak={peak:+.2f}% dd={dd:.2f}%",
                sym=symbol, side=side, pnl=pnl_pct, pnl_usd=pnl_usd,
                peak=track["peak_pnl_pct"], dd=track["max_drawdown_pct"],
            )
            self._last_position_log[pid] = now

    # ─── Trigger close ──────────────────────────────────────────────────

    async def _trigger_close(
        self,
        position: dict[str, Any],
        close_price: float,
        trigger: str,
    ) -> None:
        """Close a position via the order engine."""
        pid = position["position_id"]
        symbol = position["symbol"]
        track = self._tracking.get(pid)

        # Step 1: Flush final tracking data to DB before closing
        if track:
            await self._db.execute(
                """UPDATE virtual_positions SET
                   peak_pnl_pct = ?, max_drawdown_pct = ?,
                   time_in_profit_seconds = ?, time_in_loss_seconds = ?
                   WHERE position_id = ?""",
                (
                    track["peak_pnl_pct"],
                    track["max_drawdown_pct"],
                    track["time_in_profit"],
                    track["time_in_loss"],
                    pid,
                ),
            )

        # Step 2: Close via order engine
        try:
            result = await self._engine.close_position(
                symbol, close_trigger=trigger, close_price=close_price
            )

            if result.get("status") == "Rejected":
                log.error(
                    "Monitor close rejected for {sym}: {reason}",
                    sym=symbol, reason=result.get("reason"),
                )
                if track:
                    track["close_retries"] = track.get("close_retries", 0) + 1
                    if track["close_retries"] >= MAX_CLOSE_RETRIES:
                        log.critical(
                            "Failed to close {sym} after {n} attempts — giving up",
                            sym=symbol, n=MAX_CLOSE_RETRIES,
                        )
                return

            # Step 3: Remove from tracking
            self._tracking.pop(pid, None)
            self._last_position_log.pop(pid, None)

            # Step 4: Update stats
            if trigger == "sl_hit":
                self._stats["sl_triggered"] += 1
            elif trigger == "tp_hit":
                self._stats["tp_triggered"] += 1

            # Log with monitor context
            net_pnl = result.get("net_pnl_usd", 0)
            hold = result.get("hold_duration_seconds", 0)
            peak = track["peak_pnl_pct"] if track else 0
            log.info(
                "Monitor closed {sym}: {trigger} | net=${net:+,.2f} | peak={peak:+.2f}% | held {hold}s",
                sym=symbol, trigger=trigger, net=net_pnl, peak=peak, hold=hold,
            )

        except Exception as e:
            log.error("Monitor close error for {sym}: {err}", sym=symbol, err=str(e))
            if track:
                track["close_retries"] = track.get("close_retries", 0) + 1

    # ─── Flush tracking to DB ───────────────────────────────────────────

    async def _flush_tracking(self) -> None:
        """Write all in-memory tracking data to the database."""
        if not self._tracking:
            self._last_flush = time.time()
            return

        params = [
            (
                t["peak_pnl_pct"],
                t["max_drawdown_pct"],
                t["time_in_profit"],
                t["time_in_loss"],
                pid,
            )
            for pid, t in self._tracking.items()
        ]

        await self._db.executemany(
            """UPDATE virtual_positions SET
               peak_pnl_pct = ?, max_drawdown_pct = ?,
               time_in_profit_seconds = ?, time_in_loss_seconds = ?
               WHERE position_id = ?""",
            params,
        )

        self._last_flush = time.time()
        log.debug("Tracking flushed: {n} positions", n=len(params))
