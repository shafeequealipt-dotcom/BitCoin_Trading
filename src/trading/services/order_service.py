"""Order management service: place, modify, cancel orders with safety checks.

Every order goes through validation: symbol support, quantity/price alignment,
mandatory stop-loss, leverage caps, and instrument rule checks.
"""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Literal

from src.config.constants import SUPPORTED_SYMBOLS
from src.config.settings import Settings
from src.core.decorators import retry, timed
from src.core.exceptions import (
    BybitAPIError,
    DuplicateOrderLinkIdError,
    InvalidOrderError,
    Layer3BootNotReadyError,
    Layer3DisabledError,
    Layer3RaceError,
    OrderError,
    OrderRejectedError,
    RateLimitError,
    RiskLimitExceededError,
)
from src.core.log_context import ctx, get_tid
from src.core.logging import get_logger
from src.core.types import Order, OrderStatus, OrderType, Side
from src.core.utils import generate_id, now_utc, round_price, round_qty
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository
from src.trading.client import BybitClient
from src.trading.services.instrument_service import InstrumentService

if TYPE_CHECKING:
    from src.core.layer_manager import LayerManager, LayerSnapshot


# Phase 2 (Layer 3 enforcement). Allowed values for the ``purpose`` field
# on ``OrderService.place_order``. The set is closed: any other value
# raises ``ValueError`` so callers can't silently slip past the gate by
# misspelling. ``layer3_entry`` is the only category that is gated by
# default; ``layer4_close`` and ``layer4_sl`` deliberately bypass the
# gate (Layer 4 actions on existing positions are independent of
# Layer 3 by design — see dev_notes/phase0_issue_3_layer3_investigation.md).
# ``telegram_manual`` and ``mcp_tool`` honour the gate unless an explicit
# ``force=True`` override is supplied.
_VALID_PURPOSES = frozenset({
    "layer3_entry",
    "layer4_close",
    "layer4_sl",
    "telegram_manual",
    "mcp_tool",
    "test",
    "other",
})
_GATED_PURPOSES = frozenset({"layer3_entry", "telegram_manual", "mcp_tool"})

# Phase 5 (post-Layer-1 fix): the @retry decorator on place_order was the
# source of the duplicate ORDER_START pattern observed in production. The
# whole-method retry (incl. the ORDER_START log AND the Bybit place_order
# call) ran twice for any caught exception, with no idempotency key — Bybit
# could not deduplicate and almost certainly accepted both attempts when
# attempt 1 reached the exchange before our client's timeout fired.
#
# The fix narrows the retry to the Bybit place_order RPC itself, generates
# a UUID-based ``orderLinkId`` ONCE per call (so Bybit can dedup), and
# treats the duplicate-link-id error as a recoverable success — fetching
# the canonical order rather than re-placing it. See
# dev_notes/phase0_issue_duplicate_orders.md and
# dev_notes/phase5_order_start_duplicates_report.md.
_ORDER_LINK_ID_PREFIX = "ti"
_ORDER_LINK_ID_LEN = 24            # hex chars from uuid4 (Bybit V5: <= 36 chars total)
_ORDER_PLACE_RETRY_DELAY_S = 0.5
_ORDER_PLACE_MAX_ATTEMPTS = 2      # exclusive — one initial + one retry on transient


def _new_order_link_id() -> str:
    """Generate a fresh idempotent client order id.

    Format ``ti-<24-hex>`` — 27 chars, well within Bybit V5's 36-char limit
    and unambiguously identifiable as trading-intelligence-originated in the
    Bybit dashboard.
    """
    return f"{_ORDER_LINK_ID_PREFIX}-{uuid.uuid4().hex[:_ORDER_LINK_ID_LEN]}"

log = get_logger("trading")


class OrderService:
    """Service for order placement, modification, and cancellation.

    Enforces all safety checks before sending orders to the exchange.

    Args:
        client: Connected BybitClient.
        db: Database manager.
        settings: Application settings (for risk parameters).
    """

    def __init__(
        self,
        client: BybitClient,
        db: DatabaseManager,
        settings: Settings,
    ) -> None:
        self._client = client
        self._db = db
        self._settings = settings
        self._trading_repo = TradingRepository(db)
        self._instrument_svc = InstrumentService(client)
        # Phase 2 (Layer 3 enforcement). The LayerManager is constructed
        # AFTER OrderService in every wiring path (workers/manager.py,
        # core/container.py, brain/__init__.py, mcp/server.py), so it
        # cannot be passed at construction. WorkerManager calls
        # ``attach_layer_manager(lm)`` once the LayerManager exists. The
        # gate enforces purpose-aware boot policy: Layer 4 management
        # purposes are allowed during the boot window so close/SL paths
        # remain active even pre-attach; entry surfaces (layer3_entry,
        # telegram_manual, mcp_tool) raise Layer3BootNotReadyError.
        self._layer_manager: "LayerManager | None" = None
        # Phase 1 (post-Layer-1 fix) — boot-window deadline. After
        # ``lm_attach_deadline_sec`` seconds, even Layer 4 purposes
        # fail-close until LM attaches: a deadline overrun implies an
        # attachment failure and there is no longer a safe
        # interpretation of "permissive Layer 4 during boot".
        self._init_monotonic: float = time.monotonic()

    def attach_layer_manager(self, layer_manager: "LayerManager") -> None:
        """Inject the LayerManager AFTER both services exist.

        Phase 2 (Layer 3 enforcement). Called once during boot from the
        WorkerManager / brain / MCP server wiring AFTER LayerManager is
        constructed. Idempotent — attaching twice is a no-op.
        """
        if self._layer_manager is layer_manager:
            return
        self._layer_manager = layer_manager
        log.info(f"ORDER_SVC_LAYER_MANAGER_ATTACHED | id={id(layer_manager)} | {ctx()}")

    def _emit_order_blocked(
        self,
        *,
        order_link_id: str,
        symbol: str,
        side: Side,
        purpose: str,
        reason: str,
        force: bool,
        extra: dict | None = None,
    ) -> None:
        """Emit the unified ``ORDER_BLOCKED`` audit line.

        Phase 2 (post-Layer-1 fix). Every gate rejection — Layer 3 off,
        Layer 3 race, LM boot-not-ready, LM deadline-exceeded — emits this
        line in addition to the existing reason-specific event
        (``ORDER_REJECT_*``). Operators can grep ``ORDER_BLOCKED`` to see
        every refused order with consistent fields, regardless of which
        gate path triggered. The reason-specific events stay for
        backward-compatible dashboards.

        Args:
            order_link_id: The idempotent client order id (already
                generated when this is called).
            symbol: Trading pair.
            side: BUY/SELL — recorded for audit.
            purpose: Closed-set purpose token from ``_VALID_PURPOSES``.
            reason: Closed-set reason token: ``layer3_off``,
                ``layer3_race``, ``lm_boot_not_ready``,
                ``lm_deadline_exceeded``.
            force: The ``force`` flag the caller passed (for context).
            extra: Optional dict of reason-specific fields (e.g.
                snapshot age, deadline elapsed) merged into the line.
        """
        extra_str = ""
        if extra:
            # Sort keys so the line is greppable / diffable across runs.
            extra_str = " " + " ".join(
                f"{k}={v}" for k, v in sorted(extra.items())
            )
        # Phase 14 Gap J4 (output-quality obs): explicit actor= field
        # derived from reason so the audit log says WHO blocked.
        # Mapping: layer3_off / layer3_race → layer3_auto;
        # lm_boot_not_ready / lm_deadline_exceeded → system_auto.
        if reason in ("layer3_off", "layer3_race"):
            _actor = "layer3_auto"
        elif reason in ("lm_boot_not_ready", "lm_deadline_exceeded"):
            _actor = "system_auto"
        else:
            _actor = "gate"
        log.error(
            f"ORDER_BLOCKED | link_id={order_link_id} sym={symbol} "
            f"side={side.value} purpose={purpose} reason={reason} "
            f"actor={_actor} "
            f"force={force}{extra_str} | {ctx()}"
        )

    def _enforce_layer3_gate(
        self,
        *,
        order_link_id: str,
        symbol: str,
        side: Side,
        purpose: str,
        layer_snapshot: "LayerSnapshot | None",
        force: bool,
    ) -> None:
        """Reject the placement if Layer 3 is OFF for a gated purpose.

        Phase 2 (Layer 3 enforcement). Called from ``place_order`` BEFORE
        ORDER_START is logged so a rejected entry produces only an
        ``ORDER_REJECT_*`` / ``ORDER_BLOCKED`` line — not a half-formed
        ORDER_START followed by an exception.

        Four rejection paths:

        1. ``layer3_entry`` and Layer 3 is OFF → ``Layer3DisabledError``.
           ``force=True`` does NOT bypass for ``layer3_entry`` — operator
           overrides go through ``telegram_manual``/``mcp_tool`` paths.
        2. ``telegram_manual`` / ``mcp_tool`` and Layer 3 is OFF AND
           ``force=False`` → ``Layer3DisabledError``. ``force=True`` is
           accepted with a loud warn so the override is auditable.
        3. ``layer3_entry`` and ``layer_snapshot`` was supplied AND its
           view of L3 differs from the live LayerManager view →
           ``Layer3RaceError``. Catches the directive→execution race
           where L3 flipped off mid-call.
        4. Phase 1 (post-Layer-1 fix). LayerManager is not attached yet
           (boot window) AND the purpose is in ``_GATED_PURPOSES``
           (entry-side surfaces) → ``Layer3BootNotReadyError``.
           Layer 4 management purposes (``layer4_close``, ``layer4_sl``)
           continue to be allowed pre-attach so close/SL paths remain
           active even during the brief startup window. After
           ``settings.layer_manager.lm_attach_deadline_sec`` seconds
           without attachment, ALL purposes including Layer 4 fail-close.

        Every rejection path emits both the reason-specific
        ``ORDER_REJECT_*`` event AND a unified ``ORDER_BLOCKED`` line so
        operators have one greppable tag covering every refusal.
        """
        lm = self._layer_manager
        if lm is None:
            # Phase 1 (post-Layer-1 fix) — purpose-aware boot policy.
            elapsed_s = time.monotonic() - self._init_monotonic
            deadline_s = float(self._settings.layer_manager.lm_attach_deadline_sec)

            # Path 4a: deadline exceeded → fail-close ALL purposes.
            # Implies LM never attached; there is no safe interpretation
            # of "permissive Layer 4 during boot" past the deadline.
            if elapsed_s > deadline_s:
                log.error(
                    f"ORDER_GATE_LM_DEADLINE_EXCEEDED | link_id={order_link_id} "
                    f"sym={symbol} purpose={purpose} elapsed_s={elapsed_s:.1f} "
                    f"deadline_s={deadline_s:.1f} action=block | {ctx()}"
                )
                self._emit_order_blocked(
                    order_link_id=order_link_id,
                    symbol=symbol,
                    side=side,
                    purpose=purpose,
                    reason="lm_deadline_exceeded",
                    force=force,
                    extra={
                        "elapsed_s": f"{elapsed_s:.1f}",
                        "deadline_s": f"{deadline_s:.1f}",
                    },
                )
                raise Layer3BootNotReadyError(
                    f"LayerManager attachment deadline exceeded "
                    f"(elapsed={elapsed_s:.1f}s, deadline={deadline_s:.1f}s); "
                    f"all placements rejected.",
                    details={
                        "symbol": symbol,
                        "purpose": purpose,
                        "elapsed_s": elapsed_s,
                        "deadline_s": deadline_s,
                    },
                )

            # Path 4b: gated purpose during boot window → reject.
            # Layer 3 entries and operator-facing surfaces have no
            # legitimate pre-attach call site.
            if purpose in _GATED_PURPOSES:
                log.error(
                    f"ORDER_REJECT_LM_BOOT | link_id={order_link_id} "
                    f"sym={symbol} side={side.value} purpose={purpose} "
                    f"elapsed_s={elapsed_s:.1f} deadline_s={deadline_s:.1f} "
                    f"reason=layer_manager_not_attached_yet | {ctx()}"
                )
                self._emit_order_blocked(
                    order_link_id=order_link_id,
                    symbol=symbol,
                    side=side,
                    purpose=purpose,
                    reason="lm_boot_not_ready",
                    force=force,
                    extra={
                        "elapsed_s": f"{elapsed_s:.1f}",
                        "deadline_s": f"{deadline_s:.1f}",
                    },
                )
                raise Layer3BootNotReadyError(
                    f"LayerManager not attached yet (elapsed={elapsed_s:.1f}s); "
                    f"placement of purpose={purpose!r} rejected.",
                    details={
                        "symbol": symbol,
                        "purpose": purpose,
                        "elapsed_s": elapsed_s,
                        "deadline_s": deadline_s,
                    },
                )

            # Path 4c: Layer 4 management purpose during boot window —
            # allowed (existing behavior) so watchdog close / SL adjust
            # can fire even if the position-watchdog tick beats the LM
            # attach. Single ALLOW warn so the boot window is observable.
            log.warning(
                f"ORDER_GATE_NO_LM | link_id={order_link_id} sym={symbol} "
                f"purpose={purpose} reason=layer_manager_not_attached_yet "
                f"elapsed_s={elapsed_s:.1f} action=allow_layer4_only "
                f"| {ctx()}"
            )
            return

        live_l3 = bool(lm.is_layer_active(3))

        # Race check (Approach C) — only for layer3_entry, only when a
        # snapshot was provided.
        if purpose == "layer3_entry" and layer_snapshot is not None:
            snap_l3 = bool(layer_snapshot.is_layer_active(3))
            if snap_l3 != live_l3:
                age_ms = layer_snapshot.age_ms()
                log.error(
                    f"ORDER_REJECT_LAYER3_RACE | link_id={order_link_id} "
                    f"sym={symbol} side={side.value} purpose={purpose} "
                    f"snapshot_l3={snap_l3} live_l3={live_l3} "
                    f"snapshot_age_ms={age_ms:.0f} captured_at={layer_snapshot.captured_at_wall} "
                    f"| {ctx()}"
                )
                self._emit_order_blocked(
                    order_link_id=order_link_id,
                    symbol=symbol,
                    side=side,
                    purpose=purpose,
                    reason="layer3_race",
                    force=force,
                    extra={
                        "snapshot_l3": snap_l3,
                        "live_l3": live_l3,
                        "snapshot_age_ms": f"{age_ms:.0f}",
                    },
                )
                raise Layer3RaceError(
                    f"Layer 3 state changed between snapshot and OrderService "
                    f"(snapshot_l3={snap_l3}, live_l3={live_l3}, age_ms={age_ms:.0f})",
                    details={
                        "symbol": symbol,
                        "purpose": purpose,
                        "snapshot_l3": snap_l3,
                        "live_l3": live_l3,
                        "snapshot_age_ms": age_ms,
                    },
                )

        # Hard gate (Approach A) — Layer 3 OFF blocks gated purposes.
        if not live_l3:
            # ``layer3_entry`` is unconditionally gated; ``force`` does not
            # apply. ``telegram_manual``/``mcp_tool`` honour ``force=True``.
            if purpose == "layer3_entry" or not force:
                log.error(
                    f"ORDER_REJECT_LAYER3_OFF | link_id={order_link_id} "
                    f"sym={symbol} side={side.value} purpose={purpose} "
                    f"force={force} reason=\"Layer 3 disabled\" | {ctx()}"
                )
                self._emit_order_blocked(
                    order_link_id=order_link_id,
                    symbol=symbol,
                    side=side,
                    purpose=purpose,
                    reason="layer3_off",
                    force=force,
                )
                raise Layer3DisabledError(
                    f"Layer 3 is OFF; placement of purpose={purpose!r} rejected.",
                    details={
                        "symbol": symbol,
                        "side": side.value,
                        "purpose": purpose,
                        "force": force,
                    },
                )
            # force=True path for telegram/mcp — log loudly and proceed.
            log.warning(
                f"ORDER_LAYER3_OFF_FORCED | link_id={order_link_id} "
                f"sym={symbol} side={side.value} purpose={purpose} force=True "
                f"reason=operator_override | {ctx()}"
            )

    @timed
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
        """Place a new order with full safety validation.

        Idempotency: a per-call UUID ``orderLinkId`` is generated ONCE before
        any logging or RPC. The Bybit place_order RPC is retried at most once
        on transient (non-Bybit-mapped) exceptions; on retry, the same
        ``orderLinkId`` is reused so Bybit deduplicates the second submission.
        If Bybit returns the duplicate-link-id error on retry, the canonical
        order is recovered via a lookup rather than re-placed.

        Validation, position-size capping, leverage setting, and post-place
        stop-loss verification are NOT wrapped by the retry — they fail fast.

        Phase 2 (Layer 3 enforcement). Three new keyword-only parameters
        gate placements when Layer 3 is OFF:

        - ``purpose``: categorises the placement so logs and gating can
          distinguish entries from Layer 4 management actions. Must be
          one of ``layer3_entry|layer4_close|layer4_sl|telegram_manual|
          mcp_tool|test|other``. ``layer3_entry`` and the operator
          surfaces (``telegram_manual``, ``mcp_tool``) are gated when
          Layer 3 is OFF; Layer 4 purposes intentionally bypass.
        - ``layer_snapshot``: optional ``LayerSnapshot`` captured at the
          start of a directive→execution chain. If provided AND its
          view of L3 differs from the live LayerManager view AND
          ``purpose == "layer3_entry"``, the placement is aborted with
          ``Layer3RaceError`` — covers the case where L3 toggled off
          between Claude's decision and OrderService entry.
        - ``force``: explicit operator override for ``telegram_manual``
          and ``mcp_tool`` purposes (logged loudly). Has no effect on
          ``layer3_entry`` (always gated).

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            side: BUY or SELL.
            order_type: MARKET, LIMIT, etc.
            qty: Order quantity in base currency.
            price: Limit price (required for LIMIT orders).
            stop_loss: Stop-loss price.
            take_profit: Take-profit price.
            leverage: Position leverage (optional, set before order).
            purpose: Classification (see above).
            layer_snapshot: Captured layer state for race detection.
            force: Bypass the L3 gate for telegram/mcp purposes.

        Returns:
            Order dataclass with exchange-assigned order_id.

        Raises:
            InvalidOrderError: If order parameters fail validation.
            RiskLimitExceededError: If leverage exceeds max.
            OrderRejectedError: If exchange rejects the order.
            Layer3DisabledError: If purpose is gated and Layer 3 is OFF.
            Layer3RaceError: If layer_snapshot disagrees with live state
                for a layer3_entry purpose.
            ValueError: If ``purpose`` is not in the allowed set.
        """
        # Validate purpose closed-set membership at entry — fail loud on
        # typos so the gate cannot be bypassed silently.
        if purpose not in _VALID_PURPOSES:
            raise ValueError(
                f"OrderService.place_order: invalid purpose={purpose!r}; "
                f"must be one of {sorted(_VALID_PURPOSES)}"
            )

        # Generate idempotency key BEFORE any logging or RPC — guarantees
        # ORDER_START log and Bybit submission share the same trace id.
        order_link_id = _new_order_link_id()

        # Phase 10 Gap B2 (output-quality obs): ORDER_ATTEMPT log at the
        # very top of place_order so a rejected entry (Layer 3 OFF, gate
        # boot-not-ready, race) still produces an audit trail BEFORE the
        # ORDER_BLOCKED emit. The full ORDER_START at line ~488 keeps
        # firing for surviving placements with the richer field set.
        log.info(
            f"ORDER_ATTEMPT | link_id={order_link_id} sym={symbol} "
            f"side={side.value} purpose={purpose} qty={qty} "
            f"force={force} | {ctx()}"
        )

        # --- Phase 2 (Layer 3 enforcement) — gate ---
        # Layer 4 purposes (close, SL) intentionally bypass: managing
        # existing positions is independent of L3 by design (see
        # dev_notes/phase0_issue_3_layer3_investigation.md).
        if purpose in _GATED_PURPOSES:
            self._enforce_layer3_gate(
                order_link_id=order_link_id,
                symbol=symbol,
                side=side,
                purpose=purpose,
                layer_snapshot=layer_snapshot,
                force=force,
            )

        # --- Safety checks ---
        log.info(
            f"ORDER_START | link_id={order_link_id} sym={symbol} "
            f"side={side.value} type={order_type.value} qty={qty} "
            f"lev={leverage} sl={stop_loss} tp={take_profit} "
            f"purpose={purpose} | {ctx()}"
        )
        self._validate_symbol(symbol)
        self._validate_stop_loss(stop_loss)
        self._validate_leverage(leverage)

        # Get instrument info for qty/price validation
        instrument = await self._instrument_svc.get_instrument_info(symbol)

        # Round qty and price to instrument precision
        qty = round_qty(qty, instrument.qty_step)
        if price is not None:
            price = round_price(price, instrument.price_tick)

        # Validate against instrument rules
        issues = self._instrument_svc.validate_order_params(symbol, qty, price)
        if issues:
            raise InvalidOrderError(
                f"Order validation failed: {'; '.join(issues)}",
                details={"symbol": symbol, "issues": issues},
            )

        # Validate price for limit orders
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and price is None:
            raise InvalidOrderError(
                "Price is required for limit orders",
                details={"order_type": order_type.value},
            )

        # Set leverage if specified
        if leverage is not None:
            await self._set_leverage(symbol, leverage)

        # FIX 2: HARD POSITION SIZE CAP
        try:
            from src.trading.services.account_service import AccountService
            _acc_client = getattr(self, '_client', None)
            if _acc_client:
                _acc_svc = AccountService(_acc_client, self._db)
                _account = await _acc_svc.get_wallet_balance()
                equity = _account.total_equity
                if equity > 0:
                    _instrument = await self._instrument_svc.get_instrument_info(symbol)
                    _price = price if price else (await self._client.call("get_tickers", category="linear", symbol=symbol)).get("list", [{}])[0].get("lastPrice", "0")
                    notional_price = float(_price) if _price else 0
                    if notional_price > 0:
                        notional_value = float(qty) * notional_price
                        max_pct = self._settings.risk.max_position_size_pct
                        max_usd = equity * (max_pct / 100)
                        if notional_value > max_usd:
                            old_qty = qty
                            qty = max_usd / notional_price
                            qty = round_qty(qty, _instrument.qty_step)
                            log.warning(
                                "POSITION SIZE CAPPED: {sym} qty {old} -> {new} (max ${max:.0f} = {pct}% of ${eq:.0f})",
                                sym=symbol, old=old_qty, new=qty, max=max_usd, pct=max_pct, eq=equity,
                            )
                        # Per-trade max loss: 2% of equity
                        eff_lev = int(leverage) if leverage else 1
                        if stop_loss and float(stop_loss) > 0 and notional_price > 0:
                            sl_dist = abs(notional_price - float(stop_loss))
                            potential_loss = sl_dist * float(qty) * eff_lev
                            max_loss = equity * 0.02
                            if potential_loss > max_loss and sl_dist > 0 and eff_lev > 0:
                                old_qty = qty
                                qty = max_loss / (sl_dist * eff_lev)
                                qty = round_qty(qty, _instrument.qty_step)
                                log.warning(
                                    "PER-TRADE RISK CAPPED: {sym} qty {old} -> {new} (max loss ${ml:.0f} = 2% of equity)",
                                    sym=symbol, old=old_qty, new=qty, ml=max_loss,
                                )
        except Exception as e:
            log.warning("Position size cap check failed: {err}", err=str(e))

        # Build order params. ``orderLinkId`` is the idempotency key Bybit
        # uses to deduplicate retries — without it, the previous code path
        # could place a real second order on transient timeouts.
        order_params: dict = {
            "category": "linear",
            "symbol": symbol,
            "side": side.value,
            "orderType": order_type.value,
            "qty": str(qty),
            "orderLinkId": order_link_id,
        }

        if price is not None:
            order_params["price"] = str(price)
        if stop_loss is not None:
            order_params["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            order_params["takeProfit"] = str(take_profit)

        # Scoped retry: at most one transient retry of the Bybit RPC itself.
        # Validation/sizing/SL all happened above and are NOT retried.
        # Bybit-mapped errors (InvalidOrderError, RateLimitError,
        # InsufficientBalanceError, AuthenticationError, PositionError) are
        # business failures — they propagate immediately. The inner
        # BybitClient.call already retries BybitAPIError up to 3× before we
        # see it here.
        result = await self._place_order_with_idempotent_retry(
            order_link_id=order_link_id,
            order_params=order_params,
            symbol=symbol,
            purpose=purpose,
        )

        order_id = result.get("orderId", "") or generate_id("ord")

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price or 0.0,
            qty=qty,
            status=OrderStatus.NEW,
            stop_loss=stop_loss,
            take_profit=take_profit,
            created_at=now_utc(),
            updated_at=now_utc(),
        )

        await self._trading_repo.save_order(order)

        log.info(
            f"ORDER_OK | link_id={order_link_id} sym={symbol} oid={order_id} "
            f"side={side.value} qty={qty} price={price or 'market'} "
            f"sl={stop_loss} tp={take_profit} purpose={purpose} | {ctx()}"
        )
        log.info(
            "Order placed: {id} {side} {qty} {sym} @ {price} (SL={sl}, TP={tp})",
            id=order_id,
            side=side.value,
            qty=qty,
            sym=symbol,
            price=price or "market",
            sl=stop_loss,
            tp=take_profit,
        )

        # FIX 3: VERIFY STOP-LOSS ON EXCHANGE
        if stop_loss:
            try:
                import asyncio as _aio
                await _aio.sleep(1.5)
                from src.trading.services.position_service import PositionService
                _pos_svc = PositionService(self._client, self._db, self._settings)
                _pos = await _pos_svc.get_position(symbol)
                # Phase 12.5 (lifecycle-logging-audit Gap 5.9-G1, HIGH):
                # 5 prose lines for SL VERIFIED / SL FAILED replaced with
                # structured tags. SL on exchange is the trade's primary
                # safety boundary — silent failure = uncovered downside.
                if _pos and (not _pos.stop_loss or _pos.stop_loss == 0):
                    log.warning(
                        f"SL_VERIFY_FAIL | sym={symbol} expected_sl={stop_loss} "
                        f"actual=missing reason=not_on_exchange | {ctx()}"
                    )
                    await _pos_svc.set_stop_loss(symbol, float(stop_loss))
                    await _aio.sleep(0.5)
                    _pos2 = await _pos_svc.get_position(symbol)
                    if _pos2 and _pos2.stop_loss and _pos2.stop_loss > 0:
                        log.info(
                            f"SL_VERIFY_RETRY_OK | sym={symbol} sl={_pos2.stop_loss} | {ctx()}"
                        )
                    else:
                        log.error(
                            f"SL_VERIFY_RETRY_FAIL | sym={symbol} expected_sl={stop_loss} "
                            f"actual=missing | {ctx()}"
                        )
                        # Loss-Cutting (2026-05-31): a persistent entry-SL attach
                        # failure leaves a NAKED position (the market order has
                        # already filled). Previously this was logged at ERROR
                        # and otherwise swallowed. Escalate to a CRITICAL log
                        # (LOSS_ENTRY_SL_NAKED) so the nakedness is loud and
                        # greppable in workers.log, not assumed-fixed. (Note: this
                        # is an observability escalation, not a Telegram alert —
                        # order_service logs under the 'trading' component, which
                        # the BybitDemoAlertRelay does not observe.) We do NOT
                        # abort the order (it is filled — aborting would not
                        # change which trades enter, Rule 16); the ProfitSniper
                        # naked-position sweeper (urgent lane) attaches a
                        # protective stop on its next tick and the -3% watchdog
                        # hard stop is the outer backstop.
                        log.critical(
                            f"LOSS_ENTRY_SL_NAKED | sym={symbol} "
                            f"expected_sl={stop_loss} | entry stop-loss did NOT "
                            f"attach after retry — position is NAKED until the "
                            f"sniper sweeper covers it | {ctx()}"
                        )
                elif _pos:
                    log.info(
                        f"SL_VERIFY_OK | sym={symbol} sl={_pos.stop_loss} | {ctx()}"
                    )
            except Exception as e:
                log.warning(
                    f"SL_VERIFY_EXCEPTION | sym={symbol} err='{str(e)[:120]}' | {ctx()}"
                )

        return order

    async def _place_order_with_idempotent_retry(
        self,
        *,
        order_link_id: str,
        order_params: dict,
        symbol: str,
        purpose: str = "other",
    ) -> dict:
        """Submit a place_order RPC with at-most-one transient retry.

        Behavior matrix:

        - ``DuplicateOrderLinkIdError`` (Bybit retCode 110072): a prior
          attempt already won the race; recover the canonical order via
          ``get_open_orders`` (or ``get_order_history`` if it filled) and
          return a synthetic result. This is the **safety guarantee**: a
          duplicate exchange order cannot occur as long as ``orderLinkId``
          is shared across attempts.
        - Bybit-mapped business errors (``InvalidOrderError``,
          ``RateLimitError``, ``InsufficientBalanceError``,
          ``AuthenticationError``, ``PositionError``, ``BybitAPIError``):
          propagate immediately — retrying changes nothing.
        - All other exceptions (network/timeout/asyncio): one retry after
          ``_ORDER_PLACE_RETRY_DELAY_S`` reusing the same ``orderLinkId``,
          then re-raise.

        Args:
            order_link_id: Idempotency key already embedded in
                ``order_params["orderLinkId"]``. Passed explicitly for
                correlation logging.
            order_params: Full kwargs dict for ``BybitClient.call("place_order", ...)``.
            symbol: For logging context.

        Returns:
            The Bybit ``result`` dict containing ``orderId`` (and possibly
            ``orderLinkId``).

        Raises:
            BybitAPIError or subclass: If the exchange rejects the order
                for a non-recoverable reason.
            Exception: Any non-Bybit error after one retry exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _ORDER_PLACE_MAX_ATTEMPTS + 1):
            try:
                result = await self._client.call("place_order", **order_params)
                if attempt > 1:
                    log.info(
                        f"ORDER_RETRY_OK | link_id={order_link_id} "
                        f"sym={symbol} attempt={attempt} purpose={purpose} | {ctx()}"
                    )
                return result
            except DuplicateOrderLinkIdError:
                # Bybit confirmed a prior submission with the same link_id
                # was already accepted. Treat as success; recover the order.
                log.warning(
                    f"ORDER_DEDUPED | link_id={order_link_id} sym={symbol} "
                    f"attempt={attempt} purpose={purpose} | {ctx()}"
                )
                return await self._recover_order_by_link_id(
                    order_link_id=order_link_id, symbol=symbol,
                )
            except (
                InvalidOrderError,
                RateLimitError,
                OrderRejectedError,
                BybitAPIError,
            ) as e:
                # Bybit-side hard failure — re-trying changes nothing.
                # Re-raise without retry.
                log.error(
                    f"ORDER_FAIL | link_id={order_link_id} sym={symbol} "
                    f"attempt={attempt} purpose={purpose} err={str(e)[:120]} | {ctx()}"
                )
                raise
            except Exception as e:
                last_exc = e
                if attempt >= _ORDER_PLACE_MAX_ATTEMPTS:
                    log.error(
                        f"ORDER_RETRY_EXHAUSTED | link_id={order_link_id} "
                        f"sym={symbol} attempts={attempt} purpose={purpose} "
                        f"err={str(e)[:120]} | {ctx()}"
                    )
                    raise
                log.warning(
                    f"ORDER_RETRY | link_id={order_link_id} sym={symbol} "
                    f"attempt={attempt} purpose={purpose} err={str(e)[:80]} | {ctx()}"
                )
                await asyncio.sleep(_ORDER_PLACE_RETRY_DELAY_S)

        # Defensive: loop must have either returned or raised. The line below
        # is unreachable in practice, present only to satisfy static analysis.
        assert last_exc is not None
        raise last_exc

    async def _recover_order_by_link_id(
        self, *, order_link_id: str, symbol: str,
    ) -> dict:
        """Look up an order Bybit already accepted under our ``orderLinkId``.

        Tries open-orders first (most common — limits not yet filled,
        markets very recently submitted), falls back to order-history
        (filled markets), and finally synthesises a placeholder so the
        caller can persist a record of the deduplication.

        Returns:
            ``{"orderId": <str>, "orderLinkId": order_link_id}`` —
            ``orderId`` is the canonical exchange id when recoverable,
            else a synthetic ``DEDUP-<link_id>`` placeholder for traceability.
        """
        # Open orders (linear). Bybit V5 supports ``orderLinkId`` as a filter.
        try:
            res = await self._client.call(
                "get_open_orders",
                category="linear",
                symbol=symbol,
                orderLinkId=order_link_id,
            )
            for row in res.get("list", []) or []:
                if row.get("orderLinkId") == order_link_id and row.get("orderId"):
                    log.info(
                        f"ORDER_RECOVERED | link_id={order_link_id} sym={symbol} "
                        f"oid={row['orderId']} src=open | {ctx()}"
                    )
                    return {"orderId": row["orderId"], "orderLinkId": order_link_id}
        except Exception as e:
            log.debug(
                f"ORDER_RECOVERY_OPEN_FAIL | link_id={order_link_id} "
                f"err={str(e)[:120]} | {ctx()}"
            )

        # Order history (linear) — for fills that already moved off the
        # active book.
        try:
            res = await self._client.call(
                "get_order_history",
                category="linear",
                symbol=symbol,
                orderLinkId=order_link_id,
                limit=10,
            )
            for row in res.get("list", []) or []:
                if row.get("orderLinkId") == order_link_id and row.get("orderId"):
                    log.info(
                        f"ORDER_RECOVERED | link_id={order_link_id} sym={symbol} "
                        f"oid={row['orderId']} src=history | {ctx()}"
                    )
                    return {"orderId": row["orderId"], "orderLinkId": order_link_id}
        except Exception as e:
            log.debug(
                f"ORDER_RECOVERY_HISTORY_FAIL | link_id={order_link_id} "
                f"err={str(e)[:120]} | {ctx()}"
            )

        # Lookup failed. The order exists on Bybit (the dedup error proves
        # that) but we cannot identify its orderId right now. Position
        # reconciliation will pick it up on the next position-sync tick.
        log.warning(
            f"ORDER_RECOVERY_SYNTH | link_id={order_link_id} sym={symbol} "
            f"reason=lookup_failed | {ctx()}"
        )
        return {"orderId": f"DEDUP-{order_link_id}", "orderLinkId": order_link_id}

    # Phase 5 follow-up (post-Layer-1 fix): narrow the retry's exception
    # filter so validation/business errors fail fast. The pre-fix
    # ``@retry(exceptions=(Exception,))`` (the decorator default) caused
    # invalid amends to spend two attempts before bubbling up; with
    # ``InvalidOrderError`` etc. excluded, callers see the error
    # immediately. ``BybitAPIError`` is the umbrella for retryable
    # exchange-side issues; OSError covers connection reset / DNS hiccup
    # at the kernel level; ``RuntimeError`` is what BybitClient.call
    # wraps misc subprocess-style errors in.
    @retry(
        max_attempts=2, delay=0.5,
        exceptions=(BybitAPIError, OSError, RuntimeError),
    )
    @timed
    async def modify_order(
        self,
        symbol: str,
        order_id: str,
        qty: float | None = None,
        price: float | None = None,
    ) -> Order:
        """Amend an open order's quantity or price.

        The amend is naturally idempotent on Bybit's side — amending to
        the same value twice is a no-op — so this method's retry is
        safe to keep, but its exception filter has been narrowed (Phase
        5 follow-up) so validation / instrument-rule failures fail fast
        instead of burning two attempts.

        Args:
            symbol: Trading pair.
            order_id: Exchange order ID.
            qty: New quantity (optional).
            price: New price (optional).

        Returns:
            Updated Order dataclass.
        """
        params: dict = {
            "category": "linear",
            "symbol": symbol,
            "orderId": order_id,
        }

        if qty is not None:
            instrument = await self._instrument_svc.get_instrument_info(symbol)
            qty = round_qty(qty, instrument.qty_step)
            params["qty"] = str(qty)

        if price is not None:
            instrument = await self._instrument_svc.get_instrument_info(symbol)
            price = round_price(price, instrument.price_tick)
            params["price"] = str(price)

        await self._client.call("amend_order", **params)

        # Fetch updated order state
        order = await self._get_order_from_exchange(symbol, order_id)
        await self._trading_repo.save_order(order)

        log.info(
            "Order modified: {id} qty={qty} price={price}",
            id=order_id,
            qty=qty,
            price=price,
        )
        return order

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel a specific order.

        Args:
            symbol: Trading pair.
            order_id: Exchange order ID.

        Returns:
            True if cancellation was successful.
        """
        await self._client.call(
            "cancel_order",
            category="linear",
            symbol=symbol,
            orderId=order_id,
        )

        # Update order in DB
        existing = await self._trading_repo.get_order(order_id)
        if existing:
            existing.status = OrderStatus.CANCELLED
            existing.updated_at = now_utc()
            await self._trading_repo.save_order(existing)

        log.info("Order cancelled: {id} ({sym})", id=order_id, sym=symbol)
        return True

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all open orders.

        Args:
            symbol: Optional filter by symbol. None cancels all.

        Returns:
            Number of orders cancelled.
        """
        params: dict = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol

        result = await self._client.call("cancel_all_orders", **params)

        cancelled = result.get("list", [])
        count = len(cancelled)

        log.info(
            "Cancelled {n} orders{sym}",
            n=count,
            sym=f" for {symbol}" if symbol else "",
        )
        return count

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """List all open orders from the exchange.

        Args:
            symbol: Optional filter by symbol.

        Returns:
            List of Order dataclasses.
        """
        params: dict = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol

        result = await self._client.call("get_open_orders", **params)

        orders = []
        for item in result.get("list", []):
            order = _parse_order(item)
            await self._trading_repo.save_order(order)
            orders.append(order)

        return orders

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_order_history(
        self,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[Order]:
        """Fetch recent order history from the exchange.

        Args:
            symbol: Optional filter by symbol.
            limit: Maximum orders to return.

        Returns:
            List of Order dataclasses.
        """
        params: dict = {"category": "linear", "limit": limit}
        if symbol:
            params["symbol"] = symbol

        result = await self._client.call("get_order_history", **params)

        orders = []
        for item in result.get("list", []):
            order = _parse_order(item)
            await self._trading_repo.save_order(order)
            orders.append(order)

        return orders

    # --- Private helpers ---

    def _validate_symbol(self, symbol: str) -> None:
        """Check symbol is in the supported list."""
        if symbol not in SUPPORTED_SYMBOLS:
            raise InvalidOrderError(
                f"Unsupported symbol: {symbol}",
                details={"symbol": symbol, "supported": list(SUPPORTED_SYMBOLS)},
            )

    def _validate_stop_loss(self, stop_loss: float | None) -> None:
        """Enforce mandatory stop-loss if configured."""
        if self._settings.risk.mandatory_stop_loss and stop_loss is None:
            raise InvalidOrderError(
                "Stop-loss is mandatory. Set a stop_loss price for every order. "
                "This is a non-negotiable risk management requirement.",
            )

    def _validate_leverage(self, leverage: int | None) -> None:
        """Check leverage is within configured max."""
        if leverage is not None and leverage > self._settings.risk.max_leverage:
            raise RiskLimitExceededError(
                f"Leverage {leverage}x exceeds max allowed {self._settings.risk.max_leverage}x",
                details={
                    "requested": leverage,
                    "max_allowed": self._settings.risk.max_leverage,
                },
            )

    async def _set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol's position."""
        try:
            await self._client.call(
                "set_leverage",
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            log.debug("Leverage set to {lev}x for {sym}", lev=leverage, sym=symbol)
        except Exception as e:
            # Leverage already set is not an error
            if "leverage not modified" in str(e).lower() or "110043" in str(e):
                log.debug("Leverage already at {lev}x for {sym}", lev=leverage, sym=symbol)
            else:
                raise

    async def _get_order_from_exchange(self, symbol: str, order_id: str) -> Order:
        """Fetch a single order from the exchange by ID."""
        result = await self._client.call(
            "get_open_orders",
            category="linear",
            symbol=symbol,
            orderId=order_id,
        )
        items = result.get("list", [])
        if not items:
            # Try order history
            result = await self._client.call(
                "get_order_history",
                category="linear",
                symbol=symbol,
                orderId=order_id,
            )
            items = result.get("list", [])

        if not items:
            raise OrderError(
                f"Order {order_id} not found on exchange",
                details={"order_id": order_id, "symbol": symbol},
            )
        return _parse_order(items[0])


# =============================================================================
# Response parsing
# =============================================================================

def _parse_order(data: dict) -> Order:
    """Parse a Bybit order response into an Order dataclass."""
    from datetime import datetime, timezone
    from src.core.utils import timestamp_to_datetime

    created_ms = data.get("createdTime", "0")
    updated_ms = data.get("updatedTime", "0")

    return Order(
        order_id=data.get("orderId", ""),
        symbol=data.get("symbol", ""),
        side=Side(data.get("side", "Buy")),
        order_type=_map_order_type(data.get("orderType", "Market")),
        price=float(data.get("price", "0")),
        qty=float(data.get("qty", "0")),
        status=_map_order_status(data.get("orderStatus", "New")),
        filled_qty=float(data.get("cumExecQty", "0")),
        avg_fill_price=float(data.get("avgPrice", "0")),
        stop_loss=_parse_optional_float(data.get("stopLoss", "")),
        take_profit=_parse_optional_float(data.get("takeProfit", "")),
        created_at=timestamp_to_datetime(int(created_ms)) if created_ms != "0" else now_utc(),
        updated_at=timestamp_to_datetime(int(updated_ms)) if updated_ms != "0" else now_utc(),
    )


def _map_order_type(raw: str) -> OrderType:
    """Map Bybit order type string to OrderType enum."""
    mapping = {
        "Market": OrderType.MARKET,
        "Limit": OrderType.LIMIT,
        "StopMarket": OrderType.STOP_MARKET,
        "StopLimit": OrderType.STOP_LIMIT,
        "TakeProfit": OrderType.TAKE_PROFIT,
    }
    return mapping.get(raw, OrderType.MARKET)


def _map_order_status(raw: str) -> OrderStatus:
    """Map Bybit order status string to OrderStatus enum."""
    mapping = {
        "New": OrderStatus.NEW,
        "Created": OrderStatus.NEW,
        "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Deactivated": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
    }
    return mapping.get(raw, OrderStatus.NEW)


def _parse_optional_float(value: str) -> float | None:
    """Parse a string to float, returning None for empty/zero."""
    if not value or value == "0" or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
