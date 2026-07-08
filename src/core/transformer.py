"""Transformer — exchange routing state machine with live mode switching.

Routes all exchange-facing calls (orders, positions, wallet) to either
Shadow (virtual paper trading) or Bybit (real mainnet trading).

T1: State machine — mode persistence and crash recovery.
T3: Routing — proxy objects delegate to active service set based on mode.
T4: Switch engine — runtime mode switching with position close, trade blocking,
    equity capture, and switch history recording.
"""

import json
from datetime import datetime, timezone
from typing import Any

from src.core.exceptions import ClosingInProgressError
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.modes import (
    ADAPTER_MODES,
    MODE_BYBIT,
    MODE_BYBIT_DEMO,
    MODE_SHADOW,
)
from src.database.connection import DatabaseManager

log = get_logger("worker")


class Transformer:
    """Exchange routing state machine with service proxies and live switching.

    Holds both Shadow and Bybit service sets. Exposes proxy objects
    that delegate calls to whichever set is currently active. switch_to()
    closes positions, flips mode, and records history — all at runtime.

    Args:
        db: The main project's DatabaseManager instance.
        config: The project's Settings configuration.
    """

    def __init__(self, db: DatabaseManager, config: Any) -> None:
        self._db = db
        self._config = config
        self._current_mode: str = MODE_SHADOW
        self._is_switching: bool = False
        self._switching_to: str | None = None
        self._last_switched_at: str | None = None
        self._initialized: bool = False

        # Service sets (populated by set_services before initialize)
        self._shadow_services: dict[str, Any] = {}
        self._bybit_services: dict[str, Any] = {}
        # Bybit demo (paper-money) — additive 3rd slot. Populated only
        # when settings.bybit_demo.enabled at boot. Empty when the
        # adapter hasn't been wired (the project tolerates partial sets;
        # _apply_mode will leave _active_services empty if the slot is
        # selected but unwired, and downstream proxies log/return
        # gracefully — same contract as the existing bybit slot).
        self._bybit_demo_services: dict[str, Any] = {}
        self._active_services: dict[str, Any] = {}
        self._shadow_available: bool = True
        self._bybit_demo_available: bool = True
        self._event_buffer: Any = None
        # Close-arbitration in-flight lock (Loss-Cutting, 2026-05-31). Owned by
        # the long-lived Transformer (not the proxy instance) so the guard is
        # authoritative regardless of how many _PositionProxy objects exist — it
        # cannot be reset by a proxy rebuild. The _PositionProxy.close_position
        # chokepoint reserves a symbol here before any await, so two cutters
        # cannot both place a reduceOnly close on the same symbol in overlapping
        # async slices. Survives mode switches (the proxy persists and keeps
        # delegating to the active adapter).
        self._closing_inflight: set[str] = set()
        self._on_switch_callbacks: list = []
        # Phase 3 (P0-2): max |divergence%| observed in the last call to
        # _enrich_positions_with_local_prices. Read by the strategist to
        # decide whether to defer Claude's B-cycle prompt. Reset to 0.0
        # at the start of each enrichment call so a single stale tick
        # cannot poison every subsequent prompt build.
        self._last_enrichment_max_divergence_pct: float = 0.0

        # P6 of P1-P10: late-bound LayerManager reference for the
        # Layer-3 gate that runs in _OrderProxy.place_order when
        # current_mode == bybit_demo. Wired by WorkerManager via
        # attach_layer_manager AFTER LayerManager is constructed
        # (avoids circular DI between Transformer and LayerManager).
        # When None, the gate falls open for non-gated purposes
        # (Layer-4 management) and fails closed for gated purposes —
        # same contract as the live OrderService boot-window check.
        self._layer_manager: Any = None
        # Issue 2 of cascade-fix series (2026-05-10): late-bound
        # TickerCacheBuffer reference. ``_get_local_price`` consults it
        # before falling back to the DB ``SELECT FROM ticker_cache``
        # query. The buffer holds writes that have not yet been flushed
        # to disk (up to ``flush_interval_ms`` worth — default 500 ms),
        # so it is strictly fresher than the DB. When None (legacy /
        # tests), the existing DB-only path is preserved.
        self._ticker_buffer: Any = None

    def attach_layer_manager(self, layer_manager: Any) -> None:
        """Wire the LayerManager reference for the bybit_demo L3 gate.

        P6 of P1-P10. Called by WorkerManager after both objects are
        constructed (manager.py:687 region).
        """
        self._layer_manager = layer_manager

    def attach_ticker_buffer(self, ticker_buffer: Any) -> None:
        """Wire the TickerCacheBuffer reference for sub-flush-interval
        ticker reads. Called by WorkerManager after both objects are
        constructed (mirrors the ``attach_layer_manager`` pattern).
        Issue 2 of cascade-fix series.
        """
        self._ticker_buffer = ticker_buffer

    # ─── Per-symbol cache invalidation (Phase 2 — P0-1 ghost positions) ───

    def invalidate_position_cache(self, symbol: str) -> None:
        """Drop any cached state tied to a specific symbol.

        Wired into ``coordinator.on_trade_closed`` so that whenever a
        position closes — externally or otherwise — every consumer of
        Transformer-enriched data starts fresh. Today the Transformer
        does not keep per-symbol position state directly (enrichment is
        re-derived per call), so this is a no-op-safe hook that exists
        to make the close-broadcast architecturally complete and to
        future-proof against any cache added under this class.
        """
        log.debug(
            f"TRANSFORMER_INVALIDATE | sym={symbol} | {ctx()}"
        )

    # ─── Service configuration (T3) ─────────────────────────────────────

    def set_services(
        self,
        shadow_order: Any = None,
        shadow_position: Any = None,
        shadow_account: Any = None,
        bybit_order: Any = None,
        bybit_position: Any = None,
        bybit_account: Any = None,
        bybit_demo_order: Any = None,
        bybit_demo_position: Any = None,
        bybit_demo_account: Any = None,
    ) -> None:
        """Set every service set. Called before initialize().

        Bybit-demo kwargs are additive — existing callers passing only
        the shadow/bybit kwargs continue to work; the demo slot stays
        empty and is only selected when ``general.mode == "bybit_demo"``.
        """
        self._shadow_services = {
            "order": shadow_order,
            "position": shadow_position,
            "account": shadow_account,
        }
        self._bybit_services = {
            "order": bybit_order,
            "position": bybit_position,
            "account": bybit_account,
        }
        self._bybit_demo_services = {
            "order": bybit_demo_order,
            "position": bybit_demo_position,
            "account": bybit_demo_account,
        }
        # Phase 12.5 (lifecycle-logging-audit Gap 5.2-G1): structured tag.
        log.info(f"XFORM_SVCS_CONFIGURED | sets=[shadow,bybit,bybit_demo] | {ctx()}")

    # ─── Initialize (T1 + T3) ───────────────────────────────────────────

    async def initialize(self) -> None:
        """Read saved state from database, restore mode, set active services."""
        was_switching = False

        try:
            row = await self._db.fetch_one(
                "SELECT * FROM transformer_state WHERE id = 1"
            )
            if row is None:
                # Phase 12.5 (lifecycle-logging-audit Gap 5.2-G1): structured tag.
                log.warning(f"XFORM_STATE_MISSING | reason=no_existing_state action=creating_default | {ctx()}")
                await self._db.execute(
                    """INSERT OR IGNORE INTO transformer_state
                       (id, current_mode, is_switching, updated_at)
                       VALUES (1, 'shadow', 0, ?)""",
                    (_now_iso(),),
                )
                self._current_mode = MODE_SHADOW
            else:
                self._current_mode = row["current_mode"]
                self._is_switching = bool(row["is_switching"])
                self._switching_to = row["switching_to"]
                self._last_switched_at = row["last_switched_at"]

                if self._is_switching:
                    was_switching = True
                    old_mode = self._current_mode
                    target = self._switching_to
                    log.warning(
                        "Transformer: detected interrupted switch ({old} → {target})",
                        old=old_mode, target=target,
                    )

                    # T6: Check positions on exchange we were leaving
                    recovery_action = "cancelled"
                    try:
                        pos_svc = self._active_services.get("position")
                        if pos_svc:
                            positions = await pos_svc.get_positions()
                            has_positions = len(positions) > 0
                        else:
                            has_positions = True  # can't check → assume exist
                    except Exception as e:
                        # Phase 12.5 (Gap 5.2-G1): structured tag.
                        log.error(f"XFORM_RECOVERY_POS_CHECK_FAIL | err='{str(e)[:120]}' | {ctx()}")
                        has_positions = True  # safe default

                    if has_positions:
                        log.warning(
                            "Positions still open on {mode}. Cancelling interrupted switch.",
                            mode=old_mode,
                        )
                        recovery_action = "cancelled"
                    else:
                        # All positions closed before crash — complete the switch
                        self._current_mode = target
                        self._apply_mode()
                        self._last_switched_at = _now_iso()
                        log.info(
                            "All positions were closed. Completing interrupted switch → {mode}",
                            mode=target,
                        )
                        recovery_action = "completed"

                    self._is_switching = False
                    self._switching_to = None
                    await self._persist_state()

                    # Record recovery in switch_history
                    await self.record_switch(
                        from_mode=old_mode if recovery_action == "completed" else old_mode,
                        to_mode=self._current_mode,
                        positions_closed=0,
                        close_results=[],
                        reason="startup_recovery",
                        success=(recovery_action == "completed"),
                        error_message=f"Crash recovery: {recovery_action}" if recovery_action == "cancelled" else None,
                    )

        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.error(f"XFORM_INIT_FAIL | err='{str(e)[:160]}' fallback_mode=shadow | {ctx()}")
            self._current_mode = MODE_SHADOW

        self._apply_mode()
        self._initialized = True

        # T6: Check Shadow health at startup (only when active)
        if self._current_mode == MODE_SHADOW:
            self._shadow_available = await self._check_shadow_health()
            if not self._shadow_available:
                log.warning(
                    "Shadow API is not reachable at startup. "
                    "Trading calls will fail until Shadow starts. "
                    "Data workers will continue normally."
                )
            else:
                # Phase 12.5 (Gap 5.2-G1): structured tag.
                log.info(f"XFORM_API_PROBE | adapter=shadow status=reachable | {ctx()}")

        # Bybit demo health probe at startup (only when active)
        if self._current_mode == MODE_BYBIT_DEMO:
            self._bybit_demo_available = await self._check_bybit_demo_health()
            if not self._bybit_demo_available:
                log.warning(
                    "Bybit demo API is not reachable at startup. "
                    "Trading calls will fail until network/credentials recover. "
                    "Data workers will continue normally."
                )
            else:
                # Phase 12.5 (Gap 5.2-G1): structured tag.
                log.info(f"XFORM_API_PROBE | adapter=bybit_demo status=reachable | {ctx()}")

        recovery_note = " (recovered from interrupted switch)" if was_switching else ""
        if self.is_shadow:
            routing_target = "Shadow API"
        elif self.is_bybit_demo:
            routing_target = "Bybit demo (paper)"
        else:
            routing_target = "Bybit mainnet"
        log.info(f"XFORM_INIT | mode={self._current_mode} shadow={'Y' if self.is_shadow else 'N'} bybit_demo={'Y' if self.is_bybit_demo else 'N'} recovered={'Y' if was_switching else 'N'} | {ctx()}")
        log.info(
            "Transformer initialized: mode={mode}, routing to {target}{note}",
            mode=self._current_mode.upper(),
            target=routing_target,
            note=recovery_note,
        )

    def _apply_mode(self) -> None:
        """Set the active service set based on current mode.

        3-way dispatch over ``ADAPTER_MODES``. The ``bybit_demo`` slot
        was added additively alongside the existing ``shadow`` and live
        ``bybit`` slots; the live hot-swap path through ``switch_to``
        is preserved.
        """
        self._active_services = self._services_for_mode(self._current_mode)

    def _services_for_mode(self, mode: str) -> dict[str, Any]:
        """Return the service-set dict matching ``mode``.

        Centralizes the 3-way dispatch so callers (``_apply_mode``,
        ``switch_to``'s reachability check, ``get_target_equity``) all
        agree on which dict belongs to which mode.
        """
        if mode == MODE_BYBIT_DEMO:
            return self._bybit_demo_services
        if mode == MODE_SHADOW:
            return self._shadow_services
        return self._bybit_services

    def set_event_buffer(self, event_buffer: Any) -> None:
        """Set the event buffer for switch notifications."""
        self._event_buffer = event_buffer

    def register_switch_callback(self, callback) -> None:
        """Register a callback to fire after a successful exchange switch.

        Callbacks receive (old_mode: str, new_mode: str) and are called
        synchronously after the switch completes.
        """
        self._on_switch_callbacks.append(callback)

    async def _check_shadow_health(self) -> bool:
        """Check if Shadow's API is reachable."""
        try:
            acc = self._shadow_services.get("account")
            if acc and hasattr(acc, "health_check"):
                return await acc.health_check()
            if acc:
                await acc.get_wallet_balance()
                return True
            return False
        except Exception:
            return False

    async def _check_bybit_demo_health(self) -> bool:
        """Check if Bybit demo API is reachable.

        Mirrors :meth:`_check_shadow_health` exactly — calls
        ``health_check`` if the adapter exposes it (the BybitDemoClient
        has one), otherwise probes via a wallet-balance call.
        """
        try:
            acc = self._bybit_demo_services.get("account")
            if acc and hasattr(acc, "health_check"):
                return await acc.health_check()
            if acc:
                await acc.get_wallet_balance()
                return True
            return False
        except Exception:
            return False

    # ─── T4: Switch Engine ──────────────────────────────────────────────

    async def switch_to(
        self,
        target_mode: str,
        reason: str = "user_initiated",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Switch the Transformer to a different exchange at runtime.

        Closes all positions on the current exchange, flips routing to
        the target exchange, persists the change, and records history.
        New trades are blocked during the switch window.

        Args:
            target_mode: "shadow" or "bybit".
            reason: Why the switch is happening.
            confirmed: Required True for switching to Bybit (real money).

        Returns:
            Dict with success status and details.
        """
        old_mode = self._current_mode

        # ── Validation ──────────────────────────────────────────────
        # bybit_demo is accepted by switch_to too (for symmetry / direct
        # API callers), but the operator-facing restart-based switching
        # for bybit_demo lives in ExchangeSwitcher — switch_to does an
        # in-memory hot-swap which is fine for live bybit (the original
        # use case) but the demo flow prefers a full process restart.
        if target_mode not in ADAPTER_MODES:
            return {"success": False, "error": f"Invalid mode: {target_mode}"}

        if target_mode == self._current_mode:
            return {"success": False, "error": f"Already on {target_mode} mode"}

        if self._is_switching:
            return {"success": False, "error": "Switch already in progress"}

        # Live bybit (real money) requires explicit confirmation.
        # bybit_demo is paper money so no confirmation flag is required.
        if target_mode == MODE_BYBIT and not confirmed:
            return {
                "success": False,
                "error": "Switching to Bybit requires confirmation. Real money at risk.",
            }

        # Check target exchange is reachable
        target_services = self._services_for_mode(target_mode)
        target_account = target_services.get("account")
        if target_account is None:
            return {"success": False, "error": f"{target_mode} services not configured"}

        try:
            await target_account.get_wallet_balance()
        except Exception as e:
            return {
                "success": False,
                "error": f"{target_mode} is not reachable: {e}",
            }

        log.info(f"XFORM_SWITCH | from={old_mode} to={target_mode} rsn='{reason[:80]}' confirmed={'Y' if confirmed else 'N'} | {ctx()}")
        log.info(
            "Transformer: starting switch {old} → {new} (reason: {reason})",
            old=old_mode, new=target_mode, reason=reason,
        )

        # ── Enter switching state ───────────────────────────────────
        self._is_switching = True
        self._switching_to = target_mode
        await self._persist_state()
        # Phase 12.5 (Gap 5.2-G1): structured tag.
        log.info(f"XFORM_SWITCHING_STATE | entered=Y | {ctx()}")

        # ── Close all positions on current exchange ─────────────────
        close_results: list[dict] = []
        has_failures = False

        try:
            current_position_svc = self._active_services.get("position")
            if current_position_svc:
                positions = await current_position_svc.get_positions()
            else:
                positions = []

            if not positions:
                # Phase 12.5 (Gap 5.2-G1): structured tag.
                log.info(f"XFORM_SWITCH_NO_POSITIONS | action=skip_close | {ctx()}")
            else:
                for pos in positions:
                    try:
                        result = await current_position_svc.close_position(pos.symbol)
                        close_results.append({
                            "symbol": pos.symbol,
                            "side": str(pos.side),
                            "success": True,
                        })
                        log.info("  Closed {sym}", sym=pos.symbol)
                    except Exception as e:
                        close_results.append({
                            "symbol": pos.symbol,
                            "success": False,
                            "error": str(e),
                        })
                        has_failures = True
                        log.error("  FAILED to close {sym}: {err}", sym=pos.symbol, err=str(e))

        except Exception as e:
            has_failures = True
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.error(f"XFORM_SWITCH_POSITIONS_FAIL | err='{str(e)[:120]}' | {ctx()}")

        positions_closed = len([r for r in close_results if r.get("success")])

        # ── Abort on failure ────────────────────────────────────────
        if has_failures:
            failed = [r["symbol"] for r in close_results if not r.get("success")]
            error_msg = f"Failed to close {len(failed)} position(s): {', '.join(failed)}"
            self._is_switching = False
            self._switching_to = None
            await self._persist_state()
            await self.record_switch(
                old_mode, target_mode, positions_closed, close_results,
                reason, success=False, error_message=error_msg,
            )
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.warning(f"XFORM_SWITCH_ABORTED | reason='{str(error_msg)[:120]}' | {ctx()}")
            return {"success": False, "error": error_msg, "close_results": close_results}

        # ── Verify zero positions ───────────────────────────────────
        try:
            if current_position_svc:
                remaining = await current_position_svc.get_positions()
                if remaining:
                    error_msg = f"{len(remaining)} positions still open after close"
                    self._is_switching = False
                    self._switching_to = None
                    await self._persist_state()
                    await self.record_switch(
                        old_mode, target_mode, positions_closed, close_results,
                        reason, success=False, error_message=error_msg,
                    )
                    # Phase 12.5 (Gap 5.2-G1): structured tag.
                    log.warning(f"XFORM_SWITCH_ABORTED | reason='{str(error_msg)[:120]}' | {ctx()}")
                    return {"success": False, "error": error_msg}
        except Exception as e:
            # Phase 14 (P1-13) — proceed but log; verification failure was
            # silent before, hiding root cause when a switch went sideways.
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.warning(f"XFORM_SUPPRESSED | step=pre_switch_verification err='{str(e)[:80]}' | {ctx()}")

        # ── Capture equity snapshots ────────────────────────────────
        shadow_equity = None
        bybit_equity = None
        try:
            sa = self._shadow_services.get("account")
            if sa:
                bal = await sa.get_wallet_balance()
                shadow_equity = bal.total_equity if hasattr(bal, "total_equity") else None
        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.warning(f"XFORM_SUPPRESSED | step=shadow_equity_snapshot err='{str(e)[:80]}' | {ctx()}")
        try:
            ba = self._bybit_services.get("account")
            if ba:
                bal = await ba.get_wallet_balance()
                bybit_equity = bal.total_equity if hasattr(bal, "total_equity") else None
        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.warning(f"XFORM_SUPPRESSED | step=bybit_equity_snapshot err='{str(e)[:80]}' | {ctx()}")

        # ── Flip the mode ───────────────────────────────────────────
        self._current_mode = target_mode
        self._apply_mode()

        # ── Persist + record ────────────────────────────────────────
        self._last_switched_at = _now_iso()
        self._is_switching = False
        self._switching_to = None
        await self._persist_state()

        await self.record_switch(
            old_mode, target_mode, positions_closed, close_results,
            reason, success=True, error_message=None,
            shadow_equity=shadow_equity, bybit_equity=bybit_equity,
        )

        if self.is_shadow:
            routing_target = "Shadow API"
        elif self.is_bybit_demo:
            routing_target = "Bybit demo (paper)"
        else:
            routing_target = "Bybit mainnet"
        log.info(
            "Transformer: switch COMPLETE — now on {mode} (routing to {target}). "
            "Positions closed: {n}",
            mode=target_mode.upper(),
            target=routing_target,
            n=positions_closed,
        )

        # T6: Notify event buffer so Claude knows about the switch
        if self._event_buffer:
            try:
                self._event_buffer.add_event(
                    "HIGH", "EXCHANGE_SWITCH", "",
                    old_mode=old_mode, new_mode=target_mode,
                    reason=reason, positions_closed=positions_closed,
                )
            except Exception as e:
                # Phase 12.5 (Gap 5.2-G1): structured tag.
                log.warning(f"XFORM_EVENT_BUFFER_FAIL | err='{str(e)[:120]}' | {ctx()}")

        # Fire switch callbacks (PnL manager reset, fund manager refresh, etc.)
        for cb in self._on_switch_callbacks:
            try:
                cb(old_mode, target_mode)
            except Exception as e:
                # Phase 12.5 (Gap 5.2-G1): structured tag.
                log.warning(f"XFORM_CB_FAIL | err='{str(e)[:120]}' | {ctx()}")

        return {
            "success": True,
            "from_mode": old_mode,
            "to_mode": target_mode,
            "positions_closed": positions_closed,
            "close_results": close_results,
            "shadow_equity": shadow_equity,
            "bybit_equity": bybit_equity,
        }

    async def set_switching_state(
        self,
        target_mode: str | None,
        switching: bool,
        *,
        persist: bool = True,
    ) -> None:
        """Atomically update the in-memory + DB switching state.

        Public surface used by external orchestrators (e.g.,
        :class:`ExchangeSwitcher`) so they don't need to write to
        ``_is_switching`` / ``_switching_to`` directly. Existing
        internal call sites in :meth:`switch_to` continue to mutate the
        private attributes inline because they need finer-grained
        control over when ``_persist_state`` fires.

        Args:
            target_mode: The mode being switched to, or ``None`` when
                clearing the switching state.
            switching: ``True`` to enter the switching window, ``False``
                to exit it.
            persist: When ``True``, write through to ``transformer_state``
                so an interrupted switch is recoverable across restarts.
        """
        self._is_switching = switching
        self._switching_to = target_mode if switching else None
        if persist:
            await self._persist_state()

    async def persist_target_mode(self, target_mode: str) -> None:
        """Atomically write current_mode = target_mode to transformer_state.

        Used by :class:`ExchangeSwitcher`'s restart-based path to persist
        the new mode in the DB WITHOUT performing the in-memory
        ``_apply_mode`` flip — the next boot's :meth:`initialize` does
        the dispatch via the existing crash-recovery branch. Also clears
        any pending switching flags so the system comes up clean.

        Reuses :meth:`_persist_state` so the SQL UPDATE (current_mode,
        is_switching, switching_to, last_switched_at, updated_at) stays
        in one place.

        In-process consistency note: ``self._current_mode`` is set so
        any post-call read of ``current_mode`` returns the new value.
        ``_apply_mode`` is intentionally NOT called — the restart-based
        contract relies on the next boot doing the dispatch flip.
        """
        self._current_mode = target_mode
        self._is_switching = False
        self._switching_to = None
        self._last_switched_at = _now_iso()
        await self._persist_state()

    async def record_switch(
        self,
        from_mode: str,
        to_mode: str,
        positions_closed: int,
        close_results: list,
        reason: str,
        success: bool,
        error_message: str | None = None,
        shadow_equity: float | None = None,
        bybit_equity: float | None = None,
    ) -> None:
        """Record a switch event in the switch_history table.

        Public surface used by both the in-memory hot-swap path
        (:meth:`switch_to`) and the restart-based orchestrator
        (:class:`ExchangeSwitcher`) so the schema stays consistent
        across both switching mechanisms.
        """
        try:
            await self._db.execute(
                """INSERT INTO switch_history
                   (timestamp, from_mode, to_mode, positions_closed,
                    close_results_json, reason, success, error_message,
                    shadow_equity, bybit_equity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now_iso(),
                    from_mode,
                    to_mode,
                    positions_closed,
                    json.dumps(close_results) if close_results else None,
                    reason,
                    1 if success else 0,
                    error_message,
                    shadow_equity,
                    bybit_equity,
                ),
            )
        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.error(f"XFORM_HISTORY_PERSIST_FAIL | err='{str(e)[:120]}' | {ctx()}")

    # ─── T4: Convenience methods ─────────────────────────────────────────

    async def get_open_positions_summary(self) -> dict[str, Any]:
        """Get a summary of open positions on the current exchange."""
        try:
            pos_svc = self._active_services.get("position")
            if not pos_svc:
                return {"count": 0, "positions": []}
            positions = await pos_svc.get_positions()
            return {
                "count": len(positions),
                "positions": [
                    {
                        "symbol": p.symbol,
                        "side": str(p.side),
                        "pnl_usd": p.unrealized_pnl if hasattr(p, "unrealized_pnl") else None,
                    }
                    for p in positions
                ],
            }
        except Exception as e:
            return {"count": 0, "positions": [], "error": str(e)}

    async def get_current_equity(self) -> dict[str, Any]:
        """Get equity from the current exchange."""
        try:
            acc_svc = self._active_services.get("account")
            if not acc_svc:
                return {"equity": None, "mode": self._current_mode}
            bal = await acc_svc.get_wallet_balance()
            return {
                "equity": bal.total_equity if hasattr(bal, "total_equity") else None,
                "available": bal.available_balance if hasattr(bal, "available_balance") else None,
                "mode": self._current_mode,
            }
        except Exception as e:
            return {"equity": None, "error": str(e), "mode": self._current_mode}

    async def get_target_equity(self, target_mode: str) -> dict[str, Any]:
        """Get equity from a specific exchange (for switch preview)."""
        services = self._services_for_mode(target_mode)
        try:
            acc_svc = services.get("account")
            if not acc_svc:
                return {"equity": None, "mode": target_mode}
            bal = await acc_svc.get_wallet_balance()
            return {
                "equity": bal.total_equity if hasattr(bal, "total_equity") else None,
                "mode": target_mode,
            }
        except Exception as e:
            return {"equity": None, "error": str(e), "mode": target_mode}

    # ─── Active service accessors (T3) ───────────────────────────────────

    @property
    def active_order_service(self) -> Any:
        return self._active_services.get("order")

    @property
    def active_position_service(self) -> Any:
        return self._active_services.get("position")

    @property
    def active_account_service(self) -> Any:
        return self._active_services.get("account")

    def create_proxies(self) -> dict[str, Any]:
        """Create proxy objects that delegate through the Transformer."""
        return {
            "order": _OrderProxy(self),
            "position": _PositionProxy(self),
            "account": _AccountProxy(self),
        }

    # ─── Properties ─────────────────────────────────────────────────────

    @property
    def current_mode(self) -> str:
        return self._current_mode

    @property
    def is_shadow(self) -> bool:
        return self._current_mode == MODE_SHADOW

    @property
    def is_bybit(self) -> bool:
        """True when current mode is live Bybit mainnet."""
        return self._current_mode == MODE_BYBIT

    @property
    def is_bybit_demo(self) -> bool:
        """True when current mode is Bybit demo (paper money)."""
        return self._current_mode == MODE_BYBIT_DEMO

    @property
    def is_switching(self) -> bool:
        return self._is_switching

    @property
    def mode_label(self) -> str:
        if self._is_switching:
            return f"🔄 SWITCHING to {(self._switching_to or '?').upper()}"
        if self._current_mode == MODE_SHADOW:
            return "🟡 [SHADOW]"
        if self._current_mode == MODE_BYBIT_DEMO:
            return "🟣 [BYBIT DEMO (PAPER)]"
        return "🔴 [LIVE]"

    @property
    def mode_indicator(self) -> str:
        if self._current_mode == MODE_SHADOW:
            return "🟡"
        if self._current_mode == MODE_BYBIT_DEMO:
            return "🟣"
        return "🔴"

    @property
    def last_switched_at(self) -> str | None:
        return self._last_switched_at

    # ─── State persistence ──────────────────────────────────────────────

    async def _persist_state(self) -> None:
        """Write current in-memory state to the database atomically."""
        try:
            await self._db.execute(
                """UPDATE transformer_state SET
                   current_mode = ?,
                   is_switching = ?,
                   switching_to = ?,
                   last_switched_at = ?,
                   updated_at = ?
                   WHERE id = 1""",
                (
                    self._current_mode,
                    1 if self._is_switching else 0,
                    self._switching_to,
                    self._last_switched_at,
                    _now_iso(),
                ),
            )
        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.error(f"XFORM_STATE_PERSIST_FAIL | err='{str(e)[:120]}' | {ctx()}")

    # ─── Status methods ─────────────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        return {
            "mode": self._current_mode,
            "mode_label": self.mode_label,
            "is_switching": self._is_switching,
            "switching_to": self._switching_to,
            "last_switched_at": self._last_switched_at,
            "initialized": self._initialized,
        }

    async def get_switch_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Read the last N switch events, parsing close_results JSON."""
        try:
            rows = await self._db.fetch_all(
                "SELECT * FROM switch_history ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            results = []
            for row in rows:
                entry = dict(row)
                if entry.get("close_results_json"):
                    try:
                        entry["close_results"] = json.loads(entry["close_results_json"])
                    except json.JSONDecodeError:
                        entry["close_results"] = []
                results.append(entry)
            return results
        except Exception as e:
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.error(f"XFORM_HISTORY_READ_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return []

    # ─── Price enrichment (uses main project's own Bybit data) ────────

    async def _get_local_price(self, symbol: str) -> float | None:
        """Get current price from main project's own ticker_cache.

        Phase 3 (P0-2): max-age tightened from 120 s to the value of
        ``settings.price.local_max_age_seconds`` (default 10 s) so a
        stale WebSocket no longer poisons every consumer with 1-2 %
        divergence vs Shadow's authoritative 1 Hz polling. When stale,
        emits ``PRICE_STALE`` and returns None so the caller falls back
        to Shadow's price. The threshold is configurable for staged
        rollout / kill-switch (set to a very large number to disable).

        Issue 2 of cascade-fix series (2026-05-10): when a
        :class:`TickerCacheBuffer` is wired (via ``attach_ticker_buffer``),
        the buffer is consulted FIRST. The buffer holds tickers from the
        WS stream that have not yet been flushed to disk; reading from
        it is strictly fresher than reading the DB and avoids one
        ``DB_LOCK_WAIT`` per call. The DB-only fallback path is preserved
        for cold-start (buffer empty for this symbol) and legacy callers
        without a wired buffer.
        """
        try:
            # Issue 2 cascade-fix: in-memory buffer takes precedence.
            # The buffer's get is GIL-atomic + lock-protected; latency
            # is microseconds, no DB hop. Returns None if no put has
            # happened for this symbol since the last flush.
            buf = self._ticker_buffer
            if buf is not None:
                buf_ticker = buf.get(symbol)
                if buf_ticker is not None:
                    # Buffer entry is by definition <= flush_interval_ms
                    # old (otherwise the drainer would have flushed it
                    # already) — well within the local_max_age_seconds
                    # threshold for any plausible config. Return the
                    # in-memory price directly.
                    bp = float(buf_ticker.last_price)
                    if bp > 0:
                        return bp
                    # Fall through to DB on degenerate (<= 0) price.
            row = await self._db.fetch_one(
                "SELECT last_price, updated_at FROM ticker_cache WHERE symbol = ?",
                (symbol,),
            )
            if row is None:
                return None

            # Resolve max-age from Settings; default 10 s when settings not
            # wired (tests, ad-hoc construction).
            max_age = 10.0
            try:
                max_age = float(
                    getattr(self._config, "price", None) and
                    getattr(self._config.price, "local_max_age_seconds", 10.0)
                    or 10.0
                )
            except Exception:
                max_age = 10.0

            # Check freshness — stale data is worse than Shadow's live data
            from datetime import datetime, timezone

            try:
                updated_str = row["updated_at"]
                if "+" in updated_str or updated_str.endswith("Z"):
                    updated_at = datetime.fromisoformat(
                        updated_str.replace("Z", "+00:00")
                    )
                else:
                    updated_at = datetime.fromisoformat(updated_str).replace(
                        tzinfo=timezone.utc
                    )
                age_seconds = (
                    datetime.now(timezone.utc) - updated_at
                ).total_seconds()
                if age_seconds > max_age:
                    log.warning(
                        f"PRICE_STALE | sym={symbol} age={age_seconds:.0f}s "
                        f"max_age={max_age:.0f}s | {ctx()}"
                    )
                    return None
            except (ValueError, TypeError):
                pass  # Can't parse timestamp — use the price anyway

            price = float(row["last_price"])
            return price if price > 0 else None
        except Exception as e:
            log.debug("ticker_cache lookup failed for {sym}: {err}", sym=symbol, err=str(e))
            return None

    async def _enrich_positions_with_local_prices(
        self, positions: list
    ) -> None:
        """Observe local-vs-Shadow price divergence per position (observation-only).

        Phase 2 of the price-source-divergence fix
        (``IMPLEMENT_PRICE_SOURCE_DEFINITIVE_FIX_INDEPTH.md``) demoted this
        helper from "enrich-and-mutate" to "observation-only". Pre-fix,
        when local-vs-Shadow divergence was within
        ``divergence_override_pct`` (default 0.5%), the helper mutated
        ``pos.mark_price`` and recomputed ``pos.unrealized_pnl`` from the
        main project's ``ticker_cache`` row — but ``ticker_cache`` was
        silently 5+ hours stale due to Bug 1 (the
        ``except RuntimeError: pass`` swallow at
        ``price_worker.py:215-220``). The operator-visible symptom was
        bursty per-symbol divergence on the Telegram ``/positions``
        dashboard.

        Post-fix: every Position dataclass passed in is returned
        untouched. Shadow's authoritative ``mark_price`` and
        ``unrealized_pnl_usd`` flow through to consumers verbatim.

        Divergence is still computed and the per-pass max is published
        to ``_last_enrichment_max_divergence_pct`` because the
        strategist's PROMPT_DEFERRED gate at
        ``src/brain/strategist.py:280-298, 500-523`` depends on that
        field (compares it against ``divergence_block_prompt_pct`` to
        decide whether to defer Claude's B-cycle prompt). That contract
        is preserved byte-for-byte.

        Above-threshold divergences are logged with the
        ``PRICE_DIVERGENCE_OBS`` tag (renamed from the pre-fix
        ``PRICE_OVERRIDE``) and the event-buffer event name is
        ``price_divergence_obs`` (renamed from ``price_override``) to
        reflect that no override is being applied — the threshold now
        controls log-emission verbosity, not mutation behavior.

        The method name is retained (rather than renamed to
        ``_observe_*``) to avoid touching the proxy callers at
        ``_PositionProxy.get_positions`` / ``get_position``; the
        observation-only semantics are documented here and in the
        renamed log tag.
        """
        if not positions:
            return

        observe_count = 0
        no_local_count = 0
        above_threshold_count = 0

        # Phase 2 (price-source fix): the threshold previously controlled
        # the override mutation; now it controls log-emission verbosity.
        # Above-threshold divergences fire a WARNING + event-buffer write
        # so divergence remains surfaced to operators and to Claude via
        # the event buffer. Below-threshold divergences are observed
        # silently — the running max still updates
        # ``_last_enrichment_max_divergence_pct`` so the strategist gate
        # has the same input it had pre-fix.
        observe_threshold = 0.5
        try:
            observe_threshold = float(
                getattr(self._config, "price", None) and
                getattr(self._config.price, "divergence_override_pct", 0.5)
                or 0.5
            )
        except Exception:
            observe_threshold = 0.5

        # Reset max-divergence tracker; updated below per-position. The
        # strategist's PROMPT_DEFERRED gate reads this field after every
        # observation pass — the reset MUST happen so a single stale
        # tick from a previous pass cannot poison every subsequent
        # prompt-build decision.
        self._last_enrichment_max_divergence_pct = 0.0

        for pos in positions:
            local_price = await self._get_local_price(pos.symbol)

            if local_price is None:
                no_local_count += 1
                # Pre-fix this branch was a notable "fallback to Shadow"
                # because the override would otherwise have happened.
                # Post-fix it just means no observation can be made for
                # this symbol — Shadow's value is used directly because
                # that is the authoritative source. Demoted from WARNING
                # to DEBUG to reduce log noise; non-existence of a
                # local_price for a symbol is the expected state for any
                # symbol that hasn't recently been REST-fetched.
                log.debug(
                    "No local price for {sym} — Shadow value used directly (observation-only)",
                    sym=pos.symbol,
                )
                continue

            shadow_price = pos.mark_price

            # Compute divergence; update the running max so the
            # strategist gate keeps functioning. Only positions with a
            # positive Shadow price contribute (zero/negative
            # shadow_price is a data-integrity edge case that we
            # deliberately skip rather than feed garbage to the
            # divergence calculation).
            diff_pct = 0.0
            if shadow_price > 0:
                diff_pct = (local_price - shadow_price) / shadow_price * 100
                abs_div = abs(diff_pct)
                if abs_div > self._last_enrichment_max_divergence_pct:
                    self._last_enrichment_max_divergence_pct = abs_div

                # Above-threshold divergence: log + event-buffer write.
                # Renamed from PRICE_OVERRIDE / price_override to
                # PRICE_DIVERGENCE_OBS / price_divergence_obs in Phase 2
                # to reflect observation-only semantics.
                if abs_div > observe_threshold:
                    above_threshold_count += 1
                    log.warning(
                        f"PRICE_DIVERGENCE_OBS | sym={pos.symbol} "
                        f"local=${local_price:.6f} shadow=${shadow_price:.6f} "
                        f"divergence={diff_pct:+.3f}% threshold={observe_threshold:.2f}% | {ctx()}"
                    )
                    if self._event_buffer is not None:
                        try:
                            self._event_buffer.add_event(
                                "MED", "price_divergence_obs", pos.symbol,
                                local=round(local_price, 6),
                                shadow=round(shadow_price, 6),
                                div_pct=round(diff_pct, 3),
                            )
                        except Exception as e:
                            log.debug(
                                "price_divergence_obs event_buffer write failed: {err}",
                                err=str(e),
                            )

            observe_count += 1

            # Phase 2: NO MUTATION. Shadow's ``pos.mark_price`` and
            # ``pos.unrealized_pnl`` are authoritative and pass through
            # unchanged. The previous override branch (which mutated to
            # the local stale price) and the previous fallback-and-
            # recompute branch (which mutated to the local fresh price)
            # are both removed.

            if shadow_price > 0:
                from src.core.utils import format_price
                log.debug(
                    "Observed {sym}: shadow=${sp} local=${lp} (Δ{d:+.3f}%)",
                    sym=pos.symbol, sp=format_price(shadow_price), lp=format_price(local_price), d=diff_pct,
                )

        if observe_count > 0 or no_local_count > 0 or above_threshold_count > 0:
            log.debug(
                "Position observation: {n} total, {o} observed, "
                "{a} above_threshold, {f} no_local_price",
                n=len(positions),
                o=observe_count,
                a=above_threshold_count,
                f=no_local_count,
            )

    async def _enrich_balance_with_local_prices(self, balance) -> None:
        """Observe local-vs-Shadow unrealized-pnl divergence (observation-only).

        Phase 2 of the price-source-divergence fix demoted this helper
        from "recompute and overwrite balance fields" to
        "observe-and-log". Pre-fix, the helper rebuilt
        ``balance.unrealized_pnl`` and ``balance.total_equity`` from
        local ``ticker_cache`` prices — but ``ticker_cache`` was silently
        5+ hours stale due to Bug 1, so the rebuilt balance disagreed
        with Shadow's authoritative ``/api/balance`` response.

        Post-fix: ``balance`` is returned unchanged. Shadow's authoritative
        ``unrealized_pnl``, ``total_equity``, ``available_balance``, and
        ``used_margin`` flow through to consumers verbatim.

        When divergence between Shadow's reported unrealized-pnl and what
        a local-price recomputation would produce exceeds ``$0.01``, a
        ``BALANCE_DIVERGENCE_OBS`` debug log is emitted so reconciliation
        anomalies remain observable. No mutation is performed.

        Returns ``balance`` unchanged so the caller (the
        ``_AccountProxy.get_wallet_balance`` proxy) keeps its expected
        interface.
        """
        try:
            shadow_pos_svc = self._shadow_services.get("position")
            if not shadow_pos_svc:
                return balance

            raw_positions = await shadow_pos_svc.get_positions()
            if not raw_positions:
                # No positions → no divergence to observe.
                return balance

            from src.core.types import Side

            shadow_unrealized = balance.unrealized_pnl
            local_recomputed_unrealized = 0.0

            for pos in raw_positions:
                local_price = await self._get_local_price(pos.symbol)
                if local_price is not None and pos.entry_price > 0:
                    if pos.side in (Side.BUY, "Buy"):
                        pnl_pct = (
                            (local_price - pos.entry_price)
                            / pos.entry_price
                            * 100
                        )
                    else:
                        pnl_pct = (
                            (pos.entry_price - local_price)
                            / pos.entry_price
                            * 100
                        )
                    notional = abs(pos.size * pos.entry_price)
                    local_recomputed_unrealized += pnl_pct / 100 * notional
                else:
                    # No local price for this symbol — Shadow's per-pos
                    # value is the authoritative input. Including it in
                    # the local recomputation makes the divergence number
                    # apples-to-apples (so a mostly-stale ticker_cache
                    # doesn't show a misleading-looking "huge divergence"
                    # just because we only have a couple of fresh rows).
                    local_recomputed_unrealized += pos.unrealized_pnl

            # Phase 2: NO MUTATION. balance.unrealized_pnl,
            # balance.total_equity, balance.available_balance all pass
            # through unchanged. Below-threshold divergences are silent;
            # above-threshold divergences emit BALANCE_DIVERGENCE_OBS at
            # DEBUG so the observation is captured without log spam.
            diff = local_recomputed_unrealized - shadow_unrealized
            if abs(diff) > 0.01:
                log.debug(
                    "BALANCE_DIVERGENCE_OBS | "
                    "shadow_unrealized=${su:.2f} local=${lu:.2f} "
                    "delta=${d:+.2f} | observation_only",
                    su=shadow_unrealized,
                    lu=local_recomputed_unrealized,
                    d=diff,
                )

        except Exception as e:
            log.debug(
                "Balance observation failed; Shadow values pass through: {err}",
                err=str(e),
            )

        return balance

    async def _save_account_snapshot(self, balance, *, exchange_mode: str = "") -> None:
        """Persist an account snapshot for equity curve tracking.

        HIGH-1 fix (2026-05-09): writes for BOTH shadow and bybit_demo
        modes. The previous docstring claimed "In Bybit mode this is
        handled by AccountService internally" but no Bybit code path
        wrote to account_snapshots — verified by `grep -rn "INSERT INTO
        account_snapshots" src/` (only this writer). Result: 33+ hours
        of bybit_demo equity history was lost (62,733 shadow rows but
        zero bybit_demo rows since the mode flip at 2026-05-08T11:19:26).

        HIGH-2 fix (2026-05-09): exchange_mode kwarg added so the writer
        tags the new column correctly. Empty default falls through to
        the column DEFAULT 'shadow'. Caller (_AccountProxy) passes
        self._t.current_mode.
        """
        try:
            if exchange_mode:
                await self._db.execute(
                    """INSERT INTO account_snapshots
                       (total_equity, available_balance, used_margin,
                        unrealized_pnl, margin_level_pct, updated_at,
                        exchange_mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        balance.total_equity,
                        balance.available_balance,
                        balance.used_margin,
                        balance.unrealized_pnl,
                        getattr(balance, "margin_level_pct", 0.0),
                        datetime.now(timezone.utc).isoformat(),
                        exchange_mode,
                    ),
                )
                return
            await self._db.execute(
                """INSERT INTO account_snapshots
                   (total_equity, available_balance, used_margin,
                    unrealized_pnl, margin_level_pct, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    balance.total_equity,
                    balance.available_balance,
                    balance.used_margin,
                    balance.unrealized_pnl,
                    getattr(balance, "margin_level_pct", 0.0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except Exception as e:
            # Phase 14 (P1-13) — was silent. Account snapshot persistence
            # is non-critical for the live wallet call so we still swallow,
            # but the failure is now visible at WARN.
            # Phase 12.5 (Gap 5.2-G1): structured tag.
            log.warning(f"XFORM_SUPPRESSED | step=account_snapshot_persist err='{str(e)[:80]}' | {ctx()}")


# =============================================================================
# Proxy classes — delegate through Transformer with T4 trade blocking
# =============================================================================


class _OrderProxy:
    """Proxies OrderService methods. Blocks place_order during switch."""

    def __init__(self, transformer: Transformer) -> None:
        self._t = transformer

    async def place_order(self, *args, **kwargs):
        if self._t.is_switching:
            from src.core.types import Order, OrderStatus, OrderType, Side
            return Order(
                order_id="", symbol=args[0] if args else "",
                side=Side.BUY, order_type=OrderType.MARKET,
                price=0.0, qty=0.0, status=OrderStatus.REJECTED,
            )

        # P6 of P1-P10: pre-dispatch Layer-3 gate for bybit_demo. The
        # live OrderService runs its own _enforce_layer3_gate; this
        # gate covers the bybit_demo path which previously bypassed
        # all safety checks (audit CRITICAL-3). Shadow path is
        # intentionally untouched per operator constraint.
        if self._t.current_mode == "bybit_demo":
            from src.trading.services.order_guards import (
                check_layer3_for_bybit_demo,
                reason_to_tag_fragment,
            )
            from src.core.types import Order, OrderStatus, OrderType, Side
            purpose = kwargs.get("purpose", "other")
            layer_snapshot = kwargs.get("layer_snapshot")
            force = bool(kwargs.get("force", False))
            symbol = args[0] if args else kwargs.get("symbol", "")
            allowed, reason = check_layer3_for_bybit_demo(
                layer_manager=self._t._layer_manager,
                purpose=purpose,
                layer_snapshot=layer_snapshot,
                force=force,
                symbol=symbol,
            )
            if not allowed:
                tag = reason_to_tag_fragment(reason)
                log.warning(
                    f"BYBIT_DEMO_ORDER_GATED | sym={symbol} purpose={purpose} "
                    f"force={force} reason={tag} | {ctx()}"
                )
                # Resolve side from positional args (BybitDemoOrderService.
                # place_order has side as positional arg #2) OR from
                # kwargs OR default BUY. The rejected sentinel carries
                # symbol + REJECTED status; side is informational.
                side = (args[1] if len(args) > 1 else kwargs.get("side")) or Side.BUY
                if not hasattr(side, "value"):
                    side = Side.BUY
                return Order(
                    order_id="", symbol=symbol,
                    side=side, order_type=OrderType.MARKET,
                    price=0.0, qty=0.0, status=OrderStatus.REJECTED,
                )

            # T3-1 / F-4 six-tier-fixes (2026-05-11) — five additional
            # safety gates that the live OrderService enforces but the
            # bybit_demo path previously did not. Gates run in cheap-
            # first order: pure-function predicates (gates 1+2) before
            # the I/O-bound predicates (gates 3+4). Gate 6 (post-place
            # SL verify) runs AFTER the adapter returns.
            from src.trading.services.order_guards import (
                check_mandatory_sl_for_bybit_demo,
                check_leverage_cap_for_bybit_demo,
                check_position_size_and_max_loss_for_bybit_demo,
                verify_post_place_sl_for_bybit_demo,
            )

            _stop_loss = kwargs.get("stop_loss")
            _take_profit = kwargs.get("take_profit")
            _leverage = kwargs.get("leverage")
            _qty = kwargs.get("qty")
            _price = kwargs.get("price")

            # Gate 1 — mandatory SL
            sl_allowed, sl_reason = check_mandatory_sl_for_bybit_demo(
                stop_loss=_stop_loss, purpose=purpose,
            )
            if not sl_allowed:
                log.warning(
                    f"BYBIT_DEMO_ORDER_GATED | sym={symbol} purpose={purpose} "
                    f"force={force} reason=MANDATORY_SL | {ctx()}"
                )
                side = (args[1] if len(args) > 1 else kwargs.get("side")) or Side.BUY
                if not hasattr(side, "value"):
                    side = Side.BUY
                return Order(
                    order_id="", symbol=symbol, side=side,
                    order_type=OrderType.MARKET, price=0.0, qty=0.0,
                    status=OrderStatus.REJECTED,
                )

            # Gate 2 — leverage cap. Mirrors live
            # OrderService._validate_leverage at order_service.py:1054
            # which reads settings.risk.max_leverage (RiskSettings default
            # 3). settings.bybit does NOT have a max_leverage field —
            # using settings.risk keeps the bybit_demo path in lockstep
            # with the live gate and operator's RiskSettings.
            _cfg_for_lev = getattr(self._t, "_config", None)
            _risk_block = (
                getattr(_cfg_for_lev, "risk", None) if _cfg_for_lev else None
            )
            _max_lev = int(
                getattr(_risk_block, "max_leverage", 5) if _risk_block else 5
            )
            lev_allowed, lev_reason = check_leverage_cap_for_bybit_demo(
                leverage=_leverage, max_leverage=_max_lev,
            )
            if not lev_allowed:
                log.warning(
                    f"BYBIT_DEMO_ORDER_GATED | sym={symbol} purpose={purpose} "
                    f"force={force} reason=LEVERAGE_CAP "
                    f"leverage={_leverage} max={_max_lev} | {ctx()}"
                )
                side = (args[1] if len(args) > 1 else kwargs.get("side")) or Side.BUY
                if not hasattr(side, "value"):
                    side = Side.BUY
                return Order(
                    order_id="", symbol=symbol, side=side,
                    order_type=OrderType.MARKET, price=0.0, qty=0.0,
                    status=OrderStatus.REJECTED,
                )

            # Gates 3 + 4 — position-size cap + per-trade max-loss cap.
            # Both fail OPEN on I/O errors so a transient account-service
            # failure does not halt all trading. Caller logs the warn.
            _services = getattr(self._t, "_active_services", None)
            _settings = getattr(self._t, "_config", None)
            if _services is not None and _settings is not None:
                size_allowed, size_reason, size_tel = (
                    await check_position_size_and_max_loss_for_bybit_demo(
                        services=_services,
                        settings=_settings,
                        symbol=symbol,
                        qty=float(_qty or 0),
                        stop_loss=_stop_loss,
                        leverage=_leverage,
                        price=_price,
                    )
                )
                if size_tel.get("warn"):
                    log.debug(
                        f"BYBIT_DEMO_SIZE_GATE_IO_WARN | sym={symbol} "
                        f"warn='{size_tel['warn']}' | {ctx()}"
                    )
                if not size_allowed:
                    log.warning(
                        f"BYBIT_DEMO_ORDER_GATED | sym={symbol} purpose={purpose} "
                        f"force={force} reason={size_reason.upper()} "
                        f"telemetry={size_tel} | {ctx()}"
                    )
                    side = (args[1] if len(args) > 1 else kwargs.get("side")) or Side.BUY
                    if not hasattr(side, "value"):
                        side = Side.BUY
                    return Order(
                        order_id="", symbol=symbol, side=side,
                        order_type=OrderType.MARKET, price=0.0, qty=0.0,
                        status=OrderStatus.REJECTED,
                    )

        # ── Place the order ──
        _placed = await self._t.active_order_service.place_order(*args, **kwargs)

        # T3-1 Gate 6 — post-place SL verification for bybit_demo only.
        # Best-effort: when the order succeeded AND a stop_loss was
        # specified AND we can fetch the live position, verify the SL
        # is attached. Failures emit BYBIT_DEMO_POST_PLACE_SL_FAIL at
        # WARN so the operator notices a silently-dropped SL even
        # though the order itself FILLED.
        if (
            self._t.current_mode == "bybit_demo"
            and getattr(_placed, "status", None)
            and str(_placed.status).endswith("FILLED")
        ):
            _expected_sl = kwargs.get("stop_loss")
            _services_post = getattr(self._t, "_active_services", None)
            if _expected_sl and _services_post is not None:
                symbol_for_verify = args[0] if args else kwargs.get("symbol", "")
                ok, reason, tel = await verify_post_place_sl_for_bybit_demo(
                    services=_services_post,
                    symbol=symbol_for_verify,
                    expected_sl=_expected_sl,
                )
                if not ok:
                    log.warning(
                        f"BYBIT_DEMO_POST_PLACE_SL_FAIL | sym={symbol_for_verify} "
                        f"reason={reason} telemetry={tel} | {ctx()}"
                    )
                else:
                    log.debug(
                        f"BYBIT_DEMO_POST_PLACE_SL_OK | sym={symbol_for_verify} "
                        f"telemetry={tel} | {ctx()}"
                    )
        return _placed

    async def modify_order(self, *args, **kwargs):
        return await self._t.active_order_service.modify_order(*args, **kwargs)

    async def cancel_order(self, *args, **kwargs):
        return await self._t.active_order_service.cancel_order(*args, **kwargs)

    async def cancel_all_orders(self, *args, **kwargs):
        return await self._t.active_order_service.cancel_all_orders(*args, **kwargs)

    async def get_open_orders(self, *args, **kwargs):
        return await self._t.active_order_service.get_open_orders(*args, **kwargs)

    async def get_order_history(self, *args, **kwargs):
        return await self._t.active_order_service.get_order_history(*args, **kwargs)


class _PositionProxy:
    """Proxies PositionService methods. Blocks SL/TP mods during switch."""

    def __init__(self, transformer: Transformer) -> None:
        self._t = transformer
        # Close-arbitration in-flight lock (Loss-Cutting, 2026-05-31). This proxy
        # is the SINGLE close chokepoint every mode routes through (sniper,
        # watchdog, telegram, layer-manager all call self.position_service
        # .close_position, which is this proxy; it then delegates to the active
        # mode's adapter — BybitDemo / Shadow / Bybit). Holding the reservation
        # in the proxy chokepoint — not on the per-mode adapter — guarantees the
        # synchronous double-close guard actually protects the live path in
        # every mode. The set is owned by the long-lived Transformer
        # (self._t._closing_inflight) so it is authoritative even if more than
        # one proxy is ever constructed.

    async def get_positions(self, *args, **kwargs):
        positions = await self._t.active_position_service.get_positions(*args, **kwargs)
        if self._t.is_shadow and positions:
            await self._t._enrich_positions_with_local_prices(positions)
        return positions

    async def get_positions_with_confirmation(self, *args, **kwargs):
        """Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14) — discriminated
        result variant routed through the active position service.

        Mirrors :meth:`get_positions` (active_position_service delegation,
        Shadow enrichment) but exposes the ``confirmed`` flag so the
        watchdog can distinguish exchange-confirmed state from API-error
        state. Falls back gracefully when the active service doesn't
        implement the new method (legacy live PositionService).
        """
        from src.core.types import PositionsQueryResult
        svc = self._t.active_position_service
        if hasattr(svc, "get_positions_with_confirmation"):
            result = await svc.get_positions_with_confirmation(*args, **kwargs)
            if self._t.is_shadow and result.confirmed and result.positions:
                # Enrichment mutates a list; convert the frozen tuple,
                # then re-pack into a new immutable result.
                _positions_list = list(result.positions)
                await self._t._enrich_positions_with_local_prices(_positions_list)
                return PositionsQueryResult(
                    confirmed=True,
                    positions=tuple(_positions_list),
                    reason=result.reason,
                )
            return result
        # Legacy service without the new method — synthesise
        # confirmed=True so the contract is uniform from the caller's
        # view. The watchdog's UNKNOWN_STATE path simply won't fire on
        # this exchange until the underlying service is upgraded.
        positions = await svc.get_positions(*args, **kwargs)
        if self._t.is_shadow and positions:
            await self._t._enrich_positions_with_local_prices(positions)
        return PositionsQueryResult(
            confirmed=True, positions=tuple(positions),
        )

    async def get_position(self, *args, **kwargs):
        pos = await self._t.active_position_service.get_position(*args, **kwargs)
        if self._t.is_shadow and pos is not None:
            await self._t._enrich_positions_with_local_prices([pos])
        return pos

    async def close_position(self, symbol, *args, **kwargs):
        # Allow close_position during switch (switch itself calls it).
        # In-flight guard (Loss-Cutting): reserve the symbol synchronously —
        # BEFORE any await, so it is atomic under asyncio's cooperative
        # scheduling — so two cutters (e.g. the sniper cap/spike force-close and
        # the watchdog hard-stop/time-decay) cannot both place a reduceOnly
        # close on the same symbol in overlapping async slices. A duplicate
        # raises ClosingInProgressError (a PositionError subclass); the existing
        # close handlers treat it as "could not close this tick" and skip
        # booking. The coordinator's on_trade_closed double-close guard remains
        # the booking-side backstop, and reduceOnly caps any over-close.
        _inflight = self._t._closing_inflight
        if symbol in _inflight:
            log.warning(
                f"CLOSE_INFLIGHT_SKIP | sym={symbol} | a close is already in "
                f"flight for this symbol — skipping the duplicate "
                f"(double-close guard) | {ctx()}"
            )
            raise ClosingInProgressError(
                f"Close already in flight for {symbol}",
                details={"symbol": symbol},
            )
        _inflight.add(symbol)
        try:
            return await self._t.active_position_service.close_position(
                symbol, *args, **kwargs,
            )
        finally:
            _inflight.discard(symbol)

    async def reduce_position(self, *args, **kwargs):
        return await self._t.active_position_service.reduce_position(*args, **kwargs)

    async def close_all_positions(self, *args, **kwargs):
        return await self._t.active_position_service.close_all_positions(*args, **kwargs)

    async def set_leverage(self, *args, **kwargs):
        return await self._t.active_position_service.set_leverage(*args, **kwargs)

    async def set_stop_loss(self, *args, **kwargs):
        if self._t.is_switching:
            return False
        return await self._t.active_position_service.set_stop_loss(*args, **kwargs)

    async def set_take_profit(self, *args, **kwargs):
        if self._t.is_switching:
            return False
        return await self._t.active_position_service.set_take_profit(*args, **kwargs)

    async def get_pnl_summary(self, *args, **kwargs):
        return await self._t.active_position_service.get_pnl_summary(*args, **kwargs)

    async def get_last_close(self, *args, **kwargs):
        """Forward get_last_close to the active position service when it
        implements it. The Shadow service and the bybit_demo adapter both
        provide it (bybit_demo returns the exchange's authoritative closedPnl
        from /v5/position/closed-pnl); only the legacy direct Bybit
        PositionService lacks it, in which case we return None and the caller
        (watchdog close-detector, or the Finding 8 zombie reconciler) falls
        back to its non-authoritative path.
        """
        active = self._t.active_position_service
        fn = getattr(active, "get_last_close", None)
        if fn is None:
            return None
        return await fn(*args, **kwargs)


class _AccountProxy:
    """Proxies AccountService methods. Reads always allowed."""

    def __init__(self, transformer: Transformer) -> None:
        self._t = transformer

    async def get_wallet_balance(self, *args, **kwargs):
        balance = await self._t.active_account_service.get_wallet_balance(*args, **kwargs)
        # HIGH-1 fix (2026-05-09): enrichment stays shadow-only (Shadow's
        # balance comes raw from the adapter and needs local-price
        # multiplication to compute equity); snapshot save now runs for
        # BOTH modes so bybit_demo equity history is captured. Pre-fix
        # the snapshot was inside the is_shadow gate, leaving
        # account_snapshots dormant for the entire bybit_demo era
        # (zero rows from 2026-05-08T11:19:26 onward).
        if self._t.is_shadow:
            balance = await self._t._enrich_balance_with_local_prices(balance)
        # HIGH-2 fix (2026-05-09): pass exchange_mode so the new
        # account_snapshots.exchange_mode column is tagged correctly.
        _mode = ""
        try:
            _mode = str(self._t.current_mode or "")
        except Exception:
            _mode = ""
        await self._t._save_account_snapshot(balance, exchange_mode=_mode)
        return balance

    async def get_available_balance(self, *args, **kwargs):
        if self._t.is_shadow:
            balance = await self.get_wallet_balance()
            return balance.available_balance
        return await self._t.active_account_service.get_available_balance(*args, **kwargs)

    async def get_equity(self, *args, **kwargs):
        if self._t.is_shadow:
            balance = await self.get_wallet_balance()
            return balance.total_equity
        return await self._t.active_account_service.get_equity(*args, **kwargs)

    async def get_margin_usage(self, *args, **kwargs):
        return await self._t.active_account_service.get_margin_usage(*args, **kwargs)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
