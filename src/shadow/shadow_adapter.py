"""Shadow service adapters — drop-in replacements for Bybit services.

Each adapter class mirrors the EXACT interface of its Bybit counterpart:
  ShadowOrderService    → mirrors OrderService
  ShadowPositionService → mirrors PositionService
  ShadowAccountService  → mirrors AccountService

They translate between Shadow's HTTP JSON API and the main project's
typed dataclasses (Order, Position, AccountInfo) with proper enum
conversion (Side, OrderType, OrderStatus).

Built in Transformer Phase T2. Tested standalone against Shadow's API.
Wired into the system by Phase T3 (Router).
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp

from src.core.log_context import ctx, get_tid
from src.core.logging import get_logger
from src.core.types import (
    AccountInfo,
    Order,
    OrderStatus,
    OrderType,
    Position,
    PositionsQueryResult,
    Side,
)

if TYPE_CHECKING:
    # Forward-reference only — Shadow has no Layer 3 gate so it does not
    # USE the LayerSnapshot at runtime, but its place_order signature
    # accepts one for parity with the live OrderService (Phase 2 of the
    # Layer 1 restructure added the kw-only arg). Drift between the two
    # signatures is a TypeError on every brain-driven paper trade — see
    # dev_notes/phase0_post_layer1_fixes/issue_1_shadow_signature.md.
    from src.core.layer_manager import LayerSnapshot

# Phase 1 (post-Layer-1 fix): boot-grace window for Shadow connection
# errors. Without this, the first ~10 worker calls to Shadow raced the
# Shadow service's HTTP listener startup, producing a burst of ERROR
# lines and a fictitious zero-balance state in the fund manager. The
# helper below retries with exponential backoff and demotes log level
# to DEBUG inside the grace window so operators don't see false alarms
# during a normal restart sequence.
_PROCESS_START_MONOTONIC = time.monotonic()
_BOOT_GRACE_SECONDS = 30.0


def _in_boot_grace() -> bool:
    """Return True during the first ``_BOOT_GRACE_SECONDS`` of the process."""
    return (time.monotonic() - _PROCESS_START_MONOTONIC) < _BOOT_GRACE_SECONDS


async def _shadow_get_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    *,
    log,
    op: str,
    attempts: int = 5,
    base_delay: float = 0.2,
) -> dict | None:
    """GET wrapper with boot-grace-aware retry and logging.

    Retries up to ``attempts`` times with exponential backoff
    (``base_delay * 2**(attempt-1)``). Total worst-case latency at the
    defaults (5 attempts, 0.2s base) is ~3.0s of sleeping plus per-attempt
    timeouts, which is well inside Shadow's listener startup time.

    During the boot-grace window (first 30 s of the workers process), an
    exhausted retry chain logs at DEBUG. After the window, it logs at
    ERROR. This separates normal restart-races from genuine outages.

    Returns the parsed JSON response on success, ``None`` on full
    exhaustion. Callers that need a different sentinel (e.g.
    :class:`AccountInfo`) translate ``None`` themselves.

    Args:
        session: Shared aiohttp ClientSession.
        url: Full Shadow URL to GET.
        log: Caller's loguru logger.
        op: Short tag for log lines (e.g. ``"balance"``, ``"positions"``).
        attempts: Maximum total attempts including the first.
        base_delay: First-retry delay; doubles each subsequent retry.
    """
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                # HTTP-level failure (non-200): treat as transient. Skip
                # retry for client errors that won't change on retry.
                if 400 <= resp.status < 500 and resp.status != 429:
                    log.warning(
                        f"SHADOW_HTTP_FAIL | op={op} status={resp.status} "
                        f"url={url} | {ctx()}"
                    )
                    return None
                last_err = aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=f"HTTP {resp.status}",
                )
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            last_err = e

        if attempt < attempts:
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
            continue

        # Final attempt exhausted.
        level = log.debug if _in_boot_grace() else log.error
        level(
            f"SHADOW_CALL_FAIL | op={op} attempts={attempts} "
            f"err={str(last_err)[:120]} boot_grace={_in_boot_grace()} "
            f"| {ctx()}"
        )
        return None

    return None


# =============================================================================
# ShadowPositionService
# =============================================================================


class ShadowPositionService:
    """Shadow adapter for PositionService.

    Mirrors every public method of the real PositionService. Calls
    Shadow's HTTP API and returns Position/Order dataclass instances
    with proper Side enum values.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str
    ) -> None:
        self._session = session
        self._url = base_url
        self._log = get_logger("shadow")

    async def get_positions(
        self, symbol: str | None = None
    ) -> list[Position]:
        """Get all open positions from Shadow. Mirrors PositionService.get_positions.

        Wrapped in :func:`_shadow_get_with_retry` for boot-grace handling.
        Legacy contract — returns ``[]`` on transport failure. Callers
        that need to distinguish "confirmed empty" from "unknown" use
        :meth:`get_positions_with_confirmation` (Issue I1 / F-26).
        """
        result = await self.get_positions_with_confirmation(symbol=symbol)
        return list(result.positions)

    async def get_positions_with_confirmation(
        self, symbol: str | None = None
    ) -> PositionsQueryResult:
        """Get open positions with an explicit "confirmed" flag.

        Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14) — Shadow parity for
        the architectural fix. Shadow has no 10002 trigger (it's a
        local HTTP service with no signing) but the SAME phantom-close
        cascade applies if Shadow's HTTP server hangs or restarts:
        :func:`_shadow_get_with_retry` returns ``None`` on transport
        failure, and the legacy ``get_positions`` collapsed that to
        ``[]``, which the watchdog interprets as "all positions
        closed."

        Returns :class:`PositionsQueryResult`:
          * ``confirmed=True``  → ``positions`` reflects Shadow truth
          * ``confirmed=False`` → Shadow did not respond; caller
            preserves last-known state. Emits
            ``SHADOW_POSITIONS_UNKNOWN_STATE`` at WARNING.
        """
        data = await _shadow_get_with_retry(
            self._session,
            f"{self._url}/api/positions",
            log=self._log,
            op="positions",
        )
        if data is None:
            # Transport failure (auth, network, service down). After
            # the retry helper's exhaustion this is the strongest
            # signal that Shadow ground truth is unknown. Preserve
            # last-known state at the caller.
            self._log.warning(
                f"SHADOW_POSITIONS_UNKNOWN_STATE | "
                f"reason=transport_failure | {ctx()}"
            )
            return PositionsQueryResult(
                confirmed=False, reason="transport_failure",
            )

        positions = []
        for p in data.get("positions", []):
            pos = _build_position(p)
            if symbol is None or pos.symbol == symbol:
                positions.append(pos)
        return PositionsQueryResult(
            confirmed=True, positions=tuple(positions),
        )

    async def get_position(self, symbol: str) -> Position | None:
        """Get a single position by symbol. Mirrors PositionService.get_position."""
        try:
            async with self._session.get(
                f"{self._url}/api/position/{symbol}"
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    return None
                data = await resp.json()
        except aiohttp.ClientError as e:
            self._log.error("Shadow connection error (position): {err}", err=str(e))
            return None

        if "error" in data:
            return None
        return _build_position(data)

    async def get_last_close(
        self,
        symbol: str,
        *,
        order_id: str | None = None,
        ws_exec_price: float | None = None,
        ws_close_ts_ms: float | None = None,
        qty: float | None = None,
        tick_tolerance: float | None = None,
    ) -> dict[str, Any] | None:
        """Fetch Shadow's authoritative close data for the most recent
        closed position of `symbol`.

        Phantom-loss fix Commit 2: accepts the same optional identity hints
        as BybitDemoAdapter.get_last_close for signature symmetry, but
        IGNORES them — Shadow commits its close synchronously (no indexer
        lag), so its most-recent record always belongs to this close. The
        coordinator's staleness gate runs in shadow mode too and is a
        pass-through here (the fresh record matches the caller's WS fallback).

        Returns the raw JSON dict written by order_engine.close_position:
        exit_price, net_pnl_pct, net_pnl_usd, close_trigger, closed_at
        (ISO 8601 UTC), hold_duration_seconds, result, etc.

        Returns None when the symbol has no closed record in Shadow, when
        Shadow is unreachable, or when the endpoint returns an error — the
        caller should fall back to the ticker/last-tick cache in that case.

        Used by the watchdog to bypass the Bug 2 race where poll-detected
        closes picked up a live Bybit ticker seconds/minutes after Shadow
        actually closed, yielding inflated or sign-flipped PnL.
        """
        try:
            async with self._session.get(
                f"{self._url}/api/position/{symbol}/last_close"
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    return None
                data = await resp.json()
        except aiohttp.ClientError as e:
            self._log.error(
                "Shadow connection error (last_close): {err}", err=str(e)
            )
            return None

        if not isinstance(data, dict) or "error" in data:
            return None
        return data

    async def close_position(
        self,
        symbol: str,
        *,
        purpose: str = "layer4_close",
        close_trigger: str = "system_close",
    ) -> Order:
        """Close an open position. Mirrors PositionService.close_position.

        Phase 1 (post-Layer-1 fix). The ``purpose`` keyword-only argument
        mirrors the live ``PositionService.close_position`` contract that
        Phase 2 of the Layer 1 restructure added. Shadow has no Layer 3
        gate so the value is recorded for audit but not enforced.

        Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1): added
        ``close_trigger`` keyword-only argument to keep Shadow's signature
        compatible with PositionService + BybitDemo's. Without it, the
        Transformer's *args/**kwargs passthrough would raise TypeError
        when running in shadow mode and a caller passes close_trigger=.

        Args:
            symbol: Trading pair to close.
            purpose: Classification for the audit log (``layer4_close``,
                ``layer4_sl``, ``manual``, etc.). Default ``layer4_close``
                matches every native caller in the workers tree.
            close_trigger: Source-specific trigger reason (sniper_p9 /
                wd_hard_stop / time_decay_force_close / manual_telegram /
                etc.). Surfaces in SHADOW_POSITION_CLOSE log.

        Returns:
            An ``Order`` object representing the close execution, or a
            REJECTED order on Shadow-side rejection / transport failure.
        """
        # Phase 1 (post-Layer-1 fix): SHADOW_POSITION_CLOSE audit log
        # carries the ``purpose`` so close events are reconcilable with
        # directive→execution traces, matching SHADOW_ORDER_RECEIVED.
        # Phase 12.7 (Gap 7.4-G1): added close_trigger= field.
        self._log.info(
            f"SHADOW_POSITION_CLOSE | sym={symbol} purpose={purpose} "
            f"close_trigger={close_trigger} | {ctx()}"
        )

        try:
            payload = {"symbol": symbol, "trigger": "manual"}
            async with self._session.post(
                f"{self._url}/api/close", json=payload
            ) as resp:
                data = await resp.json()
        except aiohttp.ClientError as e:
            self._log.error("Shadow close error: {err}", err=str(e))
            return _rejected_order(symbol)

        if data.get("status") == "Rejected":
            self._log.warning(
                "Shadow close rejected: {reason}",
                reason=data.get("reason", "unknown"),
            )
            return _rejected_order(symbol)

        return _build_close_order(data)

    async def reduce_position(self, symbol: str, qty: float) -> Order:
        """Reduce an open position by ``qty`` via Shadow's /api/reduce.

        Phase 4B (session-stability fix): Shadow previously only supported
        full close, so this adapter silently downgraded to ``close_position``
        and the caller never knew. Now Shadow exposes POST /api/reduce and
        this method calls it; if Shadow rejects (qty >= current quantity,
        no open position, invalid qty) or the HTTP call fails, we fall
        back to a full close and emit ``REDUCE_FALLBACK`` so the fallback
        is visible to operators rather than silent.

        Returns:
            Order with status=FILLED carrying the reduced slice's qty
            when the reduction succeeded. Returns a rejected Order when
            neither the reduce nor the full-close fallback succeeds.
        """
        try:
            payload = {"symbol": symbol, "qty": qty, "trigger": "partial_close"}
            async with self._session.post(
                f"{self._url}/api/reduce", json=payload
            ) as resp:
                data = await resp.json()
                http_status = resp.status
        except aiohttp.ClientError as e:
            self._log.warning(
                f"REDUCE_FALLBACK | sym={symbol} qty={qty} "
                f"reason=http_error err='{str(e)[:160]}' | {ctx()}"
            )
            return await self.close_position(symbol)

        if http_status == 200 and data.get("status") == "Reduced":
            # Shape the successful response as a FILLED Order so callers
            # (ProfitSniper, trade coordinator) can treat it like any other
            # Shadow order. exit_price + qty come from the reduced slice.
            side_str = data.get("side", "Buy")
            return Order(
                order_id="",
                symbol=data.get("symbol", symbol),
                side=_parse_side(side_str),
                order_type=OrderType.MARKET,
                price=float(data.get("exit_price", 0)),
                qty=float(data.get("reduced_qty", qty)),
                status=OrderStatus.FILLED,
                filled_qty=float(data.get("reduced_qty", qty)),
                avg_fill_price=float(data.get("exit_price", 0)),
            )

        # Shadow rejected the partial: log the reason (qty out-of-range,
        # no open position, etc.) and fall back to full close. Preserves
        # the pre-fix behaviour under all failure modes while making the
        # downgrade visible.
        self._log.warning(
            f"REDUCE_FALLBACK | sym={symbol} qty={qty} "
            f"reason=shadow_reject http={http_status} "
            f"err='{str(data.get('error') or data.get('reason') or 'unknown')[:160]}' "
            f"| {ctx()}"
        )
        return await self.close_position(symbol)

    async def close_all_positions(self) -> list[Order]:
        """Close all open positions."""
        positions = await self.get_positions()
        results = []
        for pos in positions:
            order = await self.close_position(pos.symbol)
            results.append(order)
        return results

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage. Shadow sets leverage per order, not globally."""
        return True

    async def set_stop_loss(self, symbol: str, stop_loss: float) -> bool:
        """Set stop loss on an open position. Mirrors PositionService.set_stop_loss."""
        try:
            payload = {"symbol": symbol, "sl_price": stop_loss}
            async with self._session.post(
                f"{self._url}/api/set-sl", json=payload
            ) as resp:
                data = await resp.json()
            return data.get("status") == "OK"
        except aiohttp.ClientError as e:
            self._log.error("Shadow set_sl error: {err}", err=str(e))
            return False

    async def set_take_profit(self, symbol: str, take_profit: float) -> bool:
        """Set take profit on an open position. Mirrors PositionService.set_take_profit."""
        try:
            payload = {"symbol": symbol, "tp_price": take_profit}
            async with self._session.post(
                f"{self._url}/api/set-tp", json=payload
            ) as resp:
                data = await resp.json()
            return data.get("status") == "OK"
        except aiohttp.ClientError as e:
            self._log.error("Shadow set_tp error: {err}", err=str(e))
            return False

    async def get_pnl_summary(self) -> dict:
        """Get PnL summary from open positions. Mirrors PositionService.get_pnl_summary."""
        positions = await self.get_positions()
        total_unrealized = sum(p.unrealized_pnl for p in positions)

        return {
            "total_unrealized_pnl": total_unrealized,
            "total_realized_pnl": 0.0,
            "position_count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "size": p.size,
                    "entry_price": p.entry_price,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in positions
            ],
        }

    async def health_check(self) -> bool:
        """Check if Shadow API is reachable."""
        try:
            async with self._session.get(
                f"{self._url}/api/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False


# =============================================================================
# ShadowOrderService
# =============================================================================


class ShadowOrderService:
    """Shadow adapter for OrderService.

    Mirrors every public method of the real OrderService. Converts
    Side/OrderType enums to strings for Shadow's API, and converts
    responses back to Order dataclass instances.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str
    ) -> None:
        self._session = session
        self._url = base_url
        self._log = get_logger("shadow")

    async def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        leverage: int | None = None,
        *,
        purpose: str = "other",
        layer_snapshot: "LayerSnapshot | None" = None,
        force: bool = False,
    ) -> Order:
        """Place a virtual order. Mirrors OrderService.place_order.

        Converts Side enum to string for Shadow API, constructs Order
        dataclass from response.

        Phase 1 (post-Layer-1 fix). The three keyword-only parameters
        below mirror the live OrderService.place_order contract that
        Phase 2 of the Layer 1 restructure added. Shadow has no Layer 3
        gate, so the values are ACCEPTED but not enforced here; they are
        emitted in ``SHADOW_ORDER_RECEIVED`` for paper-trade audit so the
        directive→execution chain remains reconstructable from logs.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            side: ``Side.BUY`` or ``Side.SELL``.
            order_type: ``OrderType.MARKET`` (Shadow only fills market orders).
            qty: Order quantity in base currency.
            price: Ignored by Shadow (market-only); accepted for parity.
            stop_loss: Optional SL price.
            take_profit: Optional TP price.
            leverage: Position leverage hint (Shadow records on order).
            purpose: Closed-set classification (``layer3_entry``,
                ``layer4_close``, ``layer4_sl``, ``telegram_manual``,
                ``mcp_tool``, ``test``, ``other``). Logged for audit.
            layer_snapshot: ``LayerSnapshot`` captured at the start of a
                directive→execution chain. Recorded by key set for audit.
                Shadow has no L3 race detection.
            force: Operator override flag from telegram_manual / mcp_tool
                paths. Accepted but not acted on (Shadow is permissive).

        Returns:
            ``Order`` dataclass with status FILLED on success, REJECTED
            on Shadow-side rejection, or REJECTED+empty on transport
            failure.
        """
        # Convert enum to string for Shadow's JSON API
        side_str = side.value if isinstance(side, Side) else str(side)

        # Phase 1 (post-Layer-1 fix): SHADOW_ORDER_RECEIVED audit log.
        # Records the full kwargs context so paper-trade history can be
        # reconciled against directive→execution traces. layer_snapshot
        # is reduced to its key set so the line stays one-row even when
        # the snapshot grows new fields.
        snap_keys = ""
        if layer_snapshot is not None:
            try:
                snap_keys = ",".join(sorted(layer_snapshot.__dict__.keys()))
            except Exception:
                # Defensive — snapshot type may evolve. We never want
                # the audit log to be the thing that breaks a trade.
                snap_keys = type(layer_snapshot).__name__
        self._log.info(
            f"SHADOW_ORDER_RECEIVED | sym={symbol} side={side_str} qty={qty} "
            f"purpose={purpose} layer_snapshot_keys=[{snap_keys}] force={force} "
            f"| {ctx()}"
        )

        payload = {
            "symbol": symbol,
            "side": side_str,
            "qty": qty,
            "leverage": leverage or 1,
            "sl": stop_loss,
            "tp": take_profit,
        }

        self._log.info(f"SHADOW_ORD_SEND | sym={symbol} side={side_str} qty={qty} lev={leverage or 1} sl={stop_loss} tp={take_profit} | {ctx()}")

        try:
            async with self._session.post(
                f"{self._url}/api/order", json=payload
            ) as resp:
                data = await resp.json()
        except aiohttp.ClientError as e:
            self._log.error("Shadow order error: {err}", err=str(e))
            return _rejected_order(symbol, side=side)

        if data.get("status") == "Rejected":
            self._log.warning(
                "Shadow order rejected: {reason}",
                reason=data.get("reason", "unknown"),
            )
            return Order(
                order_id=data.get("order_id", ""),
                symbol=symbol,
                side=_parse_side(side_str),
                order_type=OrderType.MARKET,
                price=0.0,
                qty=qty,
                status=OrderStatus.REJECTED,
            )

        oid = data.get("order_id", "")
        fill_price = float(data.get("price", 0))
        self._log.info(f"SHADOW_ORD_RESP | sym={symbol} oid={oid} fill={fill_price} st=FILLED | {ctx()}")

        return Order(
            order_id=oid,
            symbol=symbol,
            side=_parse_side(side_str),
            order_type=OrderType.MARKET,
            price=fill_price,
            qty=float(data.get("qty", qty)),
            status=OrderStatus.FILLED,
            filled_qty=float(data.get("qty", qty)),
            avg_fill_price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    async def modify_order(
        self,
        symbol: str,
        order_id: str,
        qty: float | None = None,
        price: float | None = None,
    ) -> Order:
        """Modify order. Not applicable in Shadow (market orders fill instantly)."""
        self._log.debug("Shadow: modify_order not applicable (market orders)")
        return _rejected_order(symbol, reason="Shadow uses market orders only")

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order. Not applicable in Shadow (instant fill)."""
        return True

    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all orders. Not applicable in Shadow."""
        return 0

    async def get_open_orders(
        self, symbol: str | None = None
    ) -> list[Order]:
        """Get open orders. Shadow has none (all fill instantly)."""
        return []

    async def get_order_history(
        self, symbol: str | None = None, limit: int = 50
    ) -> list[Order]:
        """Get order history. Not implemented in Shadow adapter."""
        return []

    async def health_check(self) -> bool:
        """Check if Shadow API is reachable."""
        try:
            async with self._session.get(
                f"{self._url}/api/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False


# =============================================================================
# ShadowAccountService
# =============================================================================


class ShadowAccountService:
    """Shadow adapter for AccountService.

    Mirrors every public method of the real AccountService. Calls
    Shadow's GET /api/balance and constructs AccountInfo dataclass.
    """

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str
    ) -> None:
        self._session = session
        self._url = base_url
        self._log = get_logger("shadow")

    async def get_wallet_balance(self) -> AccountInfo:
        """Get wallet balance. Mirrors AccountService.get_wallet_balance.

        Wrapped in :func:`_shadow_get_with_retry` so a boot-time race
        with the Shadow HTTP listener doesn't surface 10 spurious ERROR
        lines and a fictitious zero balance in the fund manager.
        """
        data = await _shadow_get_with_retry(
            self._session,
            f"{self._url}/api/balance",
            log=self._log,
            op="balance",
        )
        if data is None:
            return _empty_account_info()
        return _build_account_info(data)

    async def get_available_balance(self) -> float:
        """Get available balance. Mirrors AccountService.get_available_balance."""
        info = await self.get_wallet_balance()
        return info.available_balance

    async def get_equity(self) -> float:
        """Get total equity. Mirrors AccountService.get_equity."""
        info = await self.get_wallet_balance()
        return info.total_equity

    async def get_margin_usage(self) -> dict[str, float]:
        """Get margin usage breakdown. Mirrors AccountService.get_margin_usage."""
        info = await self.get_wallet_balance()
        return {
            "used_margin": info.used_margin,
            "free_margin": info.available_balance,
            "margin_ratio_pct": info.margin_level_pct,
            "total_equity": info.total_equity,
            "unrealized_pnl": info.unrealized_pnl,
        }

    async def health_check(self) -> bool:
        """Check if Shadow API is reachable."""
        try:
            async with self._session.get(
                f"{self._url}/api/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False


# =============================================================================
# Builder helpers — translate Shadow JSON → main project dataclasses
# =============================================================================


def _parse_side(side_str: str) -> Side:
    """Convert string 'Buy'/'Sell' to Side enum."""
    if side_str in ("Buy", "BUY", "buy", "Long", "long"):
        return Side.BUY
    return Side.SELL


def _build_position(data: dict[str, Any]) -> Position:
    """Convert Shadow API position JSON to Position dataclass.

    Field mapping:
      Shadow JSON           → Position dataclass
      data["symbol"]        → symbol
      data["side"]          → side (converted to Side enum)
      data["qty"]           → size
      data["entry_price"]   → entry_price
      data["current_price"] → mark_price
      data["unrealized_pnl_usd"] → unrealized_pnl
      data["leverage"]      → leverage
      data["stop_loss_price"]    → stop_loss
      data["take_profit_price"]  → take_profit
    """
    return Position(
        symbol=data.get("symbol", ""),
        side=_parse_side(data.get("side", "Buy")),
        size=float(data.get("qty", 0)),
        entry_price=float(data.get("entry_price", 0)),
        mark_price=float(data.get("current_price", data.get("entry_price", 0))),
        unrealized_pnl=float(data.get("unrealized_pnl_usd", 0)),
        realized_pnl=0.0,
        leverage=int(data.get("leverage", 1)),
        liquidation_price=0.0,
        stop_loss=_optional_float(data.get("stop_loss_price")),
        take_profit=_optional_float(data.get("take_profit_price")),
    )


def _build_close_order(data: dict[str, Any]) -> Order:
    """Convert Shadow close response to Order dataclass."""
    side_str = data.get("side", "Buy")
    return Order(
        order_id="",
        symbol=data.get("symbol", ""),
        side=_parse_side(side_str),
        order_type=OrderType.MARKET,
        price=float(data.get("exit_price", 0)),
        qty=float(data.get("qty", 0)),
        status=OrderStatus.FILLED,
        filled_qty=float(data.get("qty", 0)),
        avg_fill_price=float(data.get("exit_price", 0)),
    )


def _build_account_info(data: dict[str, Any]) -> AccountInfo:
    """Convert Shadow balance JSON to AccountInfo dataclass.

    Field mapping:
      Shadow JSON                → AccountInfo dataclass
      data["total_equity"]       → total_equity
      data["available_balance"]  → available_balance
      data["margin_in_use"]      → used_margin
      data["total_unrealized_pnl"] → unrealized_pnl
    """
    return AccountInfo(
        total_equity=float(data.get("total_equity", 0)),
        available_balance=float(data.get("available_balance", 0)),
        used_margin=float(data.get("margin_in_use", 0)),
        unrealized_pnl=float(data.get("total_unrealized_pnl", 0)),
        margin_level_pct=0.0,
    )


def _empty_account_info() -> AccountInfo:
    """Return a zero-valued AccountInfo for error fallback."""
    return AccountInfo(
        total_equity=0.0,
        available_balance=0.0,
        used_margin=0.0,
        unrealized_pnl=0.0,
    )


def _rejected_order(
    symbol: str = "",
    side: Side | str = Side.BUY,
    reason: str = "",
) -> Order:
    """Create a rejected Order for error cases."""
    if isinstance(side, str):
        side = _parse_side(side)
    return Order(
        order_id="",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        price=0.0,
        qty=0.0,
        status=OrderStatus.REJECTED,
    )


def _optional_float(val: Any) -> float | None:
    """Convert to float or return None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
