"""TradeCoordinator — Shared state between Brain, Watchdog, Enforcer, and all trading components.

Solves the scoping problem: BrainV2 is a service, Watchdog is a worker, Enforcer is in strategies.
They can't reference each other directly. TradeCoordinator is registered in ServiceContainer
and passed to ALL components that need coordination.

Usage:
    coordinator = services.get("trade_coordinator")
    coordinator.register_trade("BTCUSDT", "momentum")
    coordinator.is_immune("BTCUSDT")
    coordinator.on_trade_closed("BTCUSDT", 1.5, 50.0, True)
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from src.core.log_context import ctx, get_did, get_tid
from src.core.logging import get_logger

log = get_logger("coordinator")

# Issue 2.9 (2026-06-07): Bybit linear taker fee per side. Used ONLY for the
# FEE_DRAG_OBS scratch-flag estimate (a diagnostic) — never for any booked
# figure or trading decision. Centralized here per Rule 9 rather than hardcoded
# inline at the call site.
_BYBIT_TAKER_FEE_PER_SIDE = 0.00055


@dataclass
class TradeState:
    """Tracks the state of an active trade."""

    symbol: str
    strategy_name: str = ""
    strategy_category: str = "default"
    opened_at: float = 0.0
    opened_at_dt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    immunity_seconds: int = 60
    entry_price: float = 0.0
    side: str = ""
    size: float = 0.0
    peak_pnl_pct: float = 0.0
    brain_decision_id: str = ""
    source: str = ""
    # Phase 3: TIAS entry-time context — stored at entry, read at close callback time
    claude_directive: str = ""
    claude_plan_view: str = ""
    signal_score: float | None = None
    ensemble_score: str = ""
    # Layer 2 Defect 6 (2026-05-22) — numeric supporting/opposing strategy
    # counts captured at register_trade time from EnsembleStateCache. Stored
    # alongside the formatted ensemble_score string so trade_intelligence
    # can persist a queryable numeric per-trade for Layer 3-4 herding +
    # weighting analysis. None when no cache record exists (rare; logged).
    supporting_count: int | None = None
    opposing_count: int | None = None
    # Layer 2 Defect 1 (2026-05-22) — per-cycle-per-symbol setup_id join key.
    # Captured at register_trade time from the EnsembleResult.setup_id (set
    # by EnsembleVoter.vote per cycle). Persisted to trade_intelligence so
    # outcomes join to the cycle's vote-set in the ensemble_votes table.
    setup_id: str = ""
    entry_regime: str = ""
    entry_rsi: float | None = None
    entry_macd_hist: float | None = None
    entry_atr_pct: float | None = None
    # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) —
    # entry-time anchors used by the watchdog to detect structural
    # invalidation. `entry_regime_at_open` mirrors `entry_regime` above
    # but is captured paired with `entry_regime_confidence` for the
    # invalidation check; the existing `entry_regime` (TIAS field) is
    # left untouched to avoid breaking close-callback consumers.
    entry_xray_confidence: float = 0.0
    entry_setup_type: str = ""
    entry_regime_at_open: str = ""
    entry_regime_confidence: float = 0.0
    # APEX optimization tracking — stored at entry, forwarded to TIAS at close
    apex_optimized: bool = False
    apex_was_flipped: bool = False
    apex_confidence: float = 0.0
    apex_tp_mode: str = ""
    apex_reasoning: str = ""
    apex_original_direction: str = ""
    apex_original_sl: float = 0.0
    apex_original_tp: float = 0.0
    apex_original_size: float = 0.0
    apex_model: str = ""
    apex_response_ms: int = 0
    apex_cost_usd: float = 0.0
    gate_adjustments: str = ""
    # Definitive-fix Phase 8 (2026-04-28) — exchange order_id stored at
    # entry so the close callback can forward it to thesis_manager and
    # the (symbol, order_id) WHERE filter prevents a previous trade's
    # thesis from matching a fresh same-symbol open.
    order_id: str = ""

    # Issue 4 fix (2026-05-11) — partial-close counter. Incremented each
    # time a partial close runs against this trade. Used to derive a
    # unique trade_id suffix for per-partial trade_history / trade_log
    # rows. The final close (after all partials) keeps the unsuffixed
    # trade_id. See dev_notes/five_critical_fixes/i4_phase2_report.md.
    partial_index: int = 0

    # Issues I2 + I5 (2026-05-14, five-critical-fixes series) — shared
    # exchange_mode field. Two complementary callers populate it:
    #
    # I2 (F-17): register_trade captures the transformer's current_mode
    #   at entry time so the close-callback chain has access to the
    #   TRADE'S registered mode rather than the GLOBAL mode at close-
    #   dispatch time. Pre-I2 the positions-cleanup callback at
    #   manager.py:2198 used transformer.current_mode and silently
    #   skipped cleanup during boot / mid-switch / SEGV-recovery
    #   moments — every close in such a window leaked.
    #
    # I5 (F-32): recover_state_from_db reads exchange_mode from
    #   trade_thesis when rebuilding _trades on boot, so the
    #   restart-restored TradeState carries the same authority the
    #   live one had.
    exchange_mode: str = ""


class TradeCoordinator:
    """Shared coordination hub for all trading components."""

    MINIMUM_HOLD_SECONDS = {
        "claude_direct": 120,    # Claude trades: 120s settle — let thesis play out
        "scalping": 120,
        "momentum": 300,
        "mean_reversion": 180,
        "funding_arb": 600,
        "sentiment": 300,
        "advanced": 180,
        "predatory": 120,
        "microstructure": 60,
        "time_based": 180,
        "cross_market": 300,
        "ai_enhanced": 180,
        "ai_generated": 180,
        "kickstart": 120,
        "default": 60,
    }

    # Maturity model simplified (2026-04-22 SL Hierarchy overhaul).
    # Old model had 4 phases gating close decisions: newborn (0-120s),
    # infant (120-300s), developing (300-900s), mature (900s+). The infant
    # and developing phases were not part of the original design and had
    # two harmful side effects:
    #   1. Time-Decay (loser protection) was blocked for ~15 minutes; avg
    #      trade duration is 13.6 min so it effectively never ran.
    #   2. Hard-stop (-3%) was blocked during 'infant' for losses between
    #      -3% and -5% (branch condition required > -5%), turning the
    #      hard-stop into a soft-stop.
    # New model: 120s grace ('newborn') then immediately 'mature'. Aged
    # retained for observability (>1h positions).
    MATURITY_PHASES = {
        "newborn": (0, 120),
        "mature": (120, 3600),
        "aged": (3600, float("inf")),
    }

    # Issue 3 (5-min reentry cooldown, 2026-05-18) — uniform 300s
    # default for the per-(symbol, direction) cooldown set in
    # ``on_trade_closed``. Operator may override via APEXSettings.
    _DEFAULT_REENTRY_COOLDOWN_SECONDS = 300

    def __init__(self) -> None:
        self._trades: dict[str, TradeState] = {}
        self._closed_trades: list[dict] = []
        self._callbacks_on_close: list = []
        # PnL-truth reconcile (2026-06-07): a SEPARATE callback channel fired
        # only by the PnL reconciler when a provisionally-booked close is
        # corrected to the exchange-authoritative net. Only the idempotent
        # sinks (data_lake/trade_history upsert-by-trade_id, thesis
        # update-by-order_id, TIAS update_outcome-by-trade_id) register here;
        # the enforcer/pnl-manager/cooldown deliberately do NOT, so a reconcile
        # never double-counts a streak or a daily total.
        self._reconcile_callbacks: list = []
        # F5 part 3 (2026-06-09 phantom-close follow-up): a CORRECTIVE channel,
        # fired by fire_reconcile ONLY when an authoritative reconcile FLIPS the
        # booked outcome (e.g. a phantom +win booked first, now a real loss). Unlike
        # the idempotent _reconcile_callbacks (which fix the trade_log row in place),
        # these sinks are the STATEFUL consumers — the performance enforcer streak,
        # the daily realized PnL, the learning loop, the re-entry cooldown — that the
        # reconcile channel deliberately excludes to avoid double-counting on a
        # normal (non-flipping) correction. They run only on a genuine flip and
        # carry both the prior and the corrected outcome so the consumer can reverse
        # the wrong booking and apply the right one.
        self._correction_callbacks: list = []
        self._last_brain_context: dict[str, str] = {}
        self._trade_plans: dict = {}  # symbol -> TradePlan
        self._trade_info: dict[str, dict] = {}  # Extended trade info for Telegram alerts
        # Issue 3 (5-min reentry cooldown, 2026-05-18). Per-(symbol,
        # direction) monotonic expiry timestamps. Set on every close
        # in ``on_trade_closed`` and consulted by the gate via
        # ``is_reentry_blocked``. Replaces the legacy per-symbol
        # ``_symbol_cooldowns`` + ``_loss_cooldown_direction`` pair
        # (T2-1 / F20 revenge-trade defense, six-tier-fixes 2026-05-11)
        # and the J6/H4 ``check_reentry_learning_gate`` method (J6
        # 2026-05-14 + H4 2026-05-16 calibration).
        self._reentry_cooldown: dict[tuple[str, str], float] = {}
        self._reentry_cooldown_seconds: int = (
            self._DEFAULT_REENTRY_COOLDOWN_SECONDS
        )
        # F9 (2026-06-09) — when True, the re-entry cooldown is set ONLY on a
        # real loss (net dollar < 0), not on every close, and the scanner
        # excludes a symbol in an active loss cooldown from the candidate list.
        # Wired from [apex].loss_cooldown_enabled via set_loss_cooldown_enabled.
        # Default False = the prior every-close cooldown (byte-identical).
        self._loss_cooldown_enabled: bool = False
        # F5 (2026-06-08): the exit-divergence plausibility band for MARK-
        # referenced staleness checks (the poll / sniper / watchdog-strategic
        # self-close paths, whose reference is the live MARK, not the exact
        # fill). A mark legitimately differs from the realised fill by ordinary
        # slippage, so these paths use this wider band (mirrors the reconciler's
        # close_pnl_reconcile_max_exit_divergence_pct=3.0 exit-plausibility gate)
        # instead of the tight half-tick _close_exit_tolerance (which is for the
        # WS fill-vs-fill comparison). Catches the >~3% phantom exits while never
        # demoting a real fee-flip close (net loss vs gross-win mark) to the
        # local gross — which would itself be a transient phantom win.
        # Centralized (2026-06-09): this default is OVERRIDDEN at boot by
        # WorkerManager via set_close_exit_divergence_pct, reading the SAME
        # centralized key the reconciler uses
        # ([bybit_demo].close_pnl_reconcile_max_exit_divergence_pct), so the
        # coordinator gates and the reconciler exit-plausibility gate stay in sync
        # and the value is tunable from config without a code edit. The 3.0 here is
        # the safe fallback if wiring is absent (e.g. tests / direct construction).
        self._close_exit_divergence_pct: float = 3.0
        self._strategic_actions: list[dict] = []  # Queued position actions from LayerManager
        self._close_reasons: dict[str, str] = {}  # symbol -> close reason (for attribution)
        # P2 of P1-P10: late-bound transformer reference for mode-aware
        # default close-reason and price-source labels. Wired by
        # WorkerManager via attach_transformer() AFTER both objects are
        # constructed (avoids circular DI). Until set, mode-aware defaults
        # fall back to "exchange_*" generic labels — never the misleading
        # "shadow_*" literal that triggered this fix series.
        self._transformer = None  # late-bound; see attach_transformer
        # PF/LC Top-15 Problem 1.3 (2026-06-04) — the authoritative-PnL
        # resolver needs an object that exposes get_last_close (the
        # _PositionProxy, NOT the raw Transformer). Late-bound by
        # WorkerManager via attach_position_service() so the WebSocket
        # self-close path books the exchange's real net closedPnl instead
        # of silently falling back to a gross, fee-free figure.
        self._position_service = None  # late-bound; see attach_position_service
        # Phantom-loss fix (2026-06-05) Commit 3: late-bound price-decimals
        # resolver (InstrumentService.price_decimals) for the staleness
        # gate's exit tolerance. See attach_tick_resolver / manager wiring.
        self._tick_resolver = None

        # Issue 4 fix (2026-05-11) — partial-close pending map and a
        # parallel callback list. The pending map is set by
        # reduce_position BEFORE the order goes out so the WS subscriber
        # can distinguish a partial fill from a full close at dispatch
        # time. The partial-callback list runs a strict subset of the
        # full close-callback fan-out: only trade_history + trade_log
        # writers fire on a partial, so subsystems that own
        # lifecycle-end semantics (thesis_close, fund release, sniper
        # buffer cleanup, perf accumulation) stay quiet until the
        # eventual final close. See
        # dev_notes/five_critical_fixes/i4_phase2_report.md.
        self._partial_close_pending: dict[str, dict] = {}
        self._callbacks_on_partial_close: list = []

    async def recover_state_from_db(self, db) -> int:
        """Issue I5 (F-32, 2026-05-14) — restart-resilient state recovery.

        Reads open theses from ``trade_thesis WHERE status='open'`` and
        rebuilds the in-memory ``_trades`` map. Without this, every
        workers.py restart (SEGV-induced or graceful) wipes the
        coordinator's view of open trades; the watchdog catches up over
        the first few ticks via WD_CLOSE_THESIS_RECOVERY, but the
        intermediate dashboard reads show 0 PnL / 0 age for live
        positions — exactly the operator-visible symptom captured in
        the audit (22:42 SEGV → 22:44-22:52 partial recovery).

        Idempotent: empty result set → no-op. Symbols already in
        ``_trades`` are NOT overwritten (preserves any state set
        between attach and this call).

        Args:
            db: DatabaseManager-like instance with an awaitable
                ``fetch_all(sql, params) -> list[dict]``.

        Returns:
            Count of TradeState entries restored. Operators see the
            count in the DASHBOARD_STATE_RECOVERED summary log.
        """
        if db is None:
            return 0
        try:
            rows = await db.fetch_all(
                "SELECT symbol, direction, entry_price, size_usd, leverage, "
                "       opened_at, order_id, exchange_mode "
                "FROM trade_thesis WHERE status = 'open'",
                (),
            )
        except Exception as e:
            log.warning(
                f"DASHBOARD_STATE_RECOVER_FAIL | stage=query "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return 0
        restored = 0
        for r in rows or []:
            sym = r.get("symbol") or ""
            if not sym or sym in self._trades:
                continue
            try:
                # Build a minimal TradeState from the thesis row. Most
                # fields are best-effort; the critical ones for
                # dashboard visibility are entry_price, side, size,
                # opened_at, order_id, exchange_mode.
                _opened_at_ts = 0.0
                _opened_at_dt = datetime.now(timezone.utc)
                _oa = r.get("opened_at")
                if _oa:
                    try:
                        # SQLite stores as text; tolerate both ISO and
                        # CURRENT_TIMESTAMP formats.
                        from datetime import datetime as _dt
                        if isinstance(_oa, str):
                            _parsed = _dt.fromisoformat(_oa.replace("Z", "+00:00"))
                            if _parsed.tzinfo is None:
                                _parsed = _parsed.replace(tzinfo=timezone.utc)
                            _opened_at_dt = _parsed
                            _opened_at_ts = _parsed.timestamp()
                    except Exception:
                        pass
                _side = str(r.get("direction") or "")
                # size in TradeState is qty (not USD); thesis stores
                # size_usd. Without entry_price the qty derivation is
                # division by zero; fall back to 0.0 which is the
                # legacy register_trade default for callers that don't
                # pass size.
                _entry_price = float(r.get("entry_price") or 0.0)
                _size_usd = float(r.get("size_usd") or 0.0)
                _lev = int(r.get("leverage") or 0)
                _qty = (
                    (_size_usd * _lev) / _entry_price if _entry_price > 0 else 0.0
                )
                self._trades[sym] = TradeState(
                    symbol=sym,
                    opened_at=_opened_at_ts or time.time(),
                    opened_at_dt=_opened_at_dt,
                    entry_price=_entry_price,
                    side=_side,
                    size=_qty,
                    order_id=str(r.get("order_id") or ""),
                    exchange_mode=str(r.get("exchange_mode") or ""),
                    source="state_recovery",
                )
                restored += 1
                log.info(
                    f"DASHBOARD_STATE_RECOVERED | sym={sym} side={_side} "
                    f"entry_price={_entry_price} size_usd={_size_usd} "
                    f"lev={_lev} order_id={str(r.get('order_id') or '-')[:24]} "
                    f"mode={r.get('exchange_mode') or '-'} | {ctx()}"
                )
            except Exception as e:
                log.warning(
                    f"DASHBOARD_STATE_RECOVER_FAIL | stage=build sym={sym} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )
        if restored:
            log.info(
                f"DASHBOARD_STATE_RECOVER_SUMMARY | restored={restored} "
                f"total_open_theses={len(rows or [])} | {ctx()}"
            )
        return restored

    def attach_transformer(self, transformer) -> None:
        """Wire the Transformer reference for mode-aware default labels.

        Called by WorkerManager after both objects are constructed.
        Setting this is what enables ``pop_close_reason`` to return
        ``f"{current_mode}_sl_tp"`` (e.g., ``"bybit_demo_sl_tp"``) instead
        of the generic ``"exchange_sl_tp"`` fallback.
        """
        self._transformer = transformer

    def attach_position_service(self, position_service) -> None:
        """Wire the position-service proxy used to resolve authoritative net PnL.

        PF/LC Top-15 Problem 1.3 (2026-06-04). The WebSocket self-close
        path (:meth:`close_with_authoritative_pnl`) resolves the
        exchange's real net ``closedPnl`` via ``get_last_close``. The raw
        Transformer has no such method — only the inner ``_PositionProxy``
        does (it forwards to the active adapter's
        ``/v5/position/closed-pnl`` query, already net of fees). Passing
        the Transformer made ``getattr(..., "get_last_close", None)``
        return None, so every WS close silently fell back to a gross,
        fee-free figure. WorkerManager wires the same ``_PositionProxy``
        the watchdog already uses (``self._services["position"]``) here,
        late-bound to avoid the circular Transformer↔Coordinator DI.
        """
        self._position_service = position_service

    def attach_tick_resolver(self, fn) -> None:
        """Phantom-loss fix Commit 3: wire a per-symbol price-decimals
        resolver (InstrumentService.price_decimals) so the staleness gate's
        exit-divergence tolerance is exact per instrument. Late-bound, like
        attach_transformer / attach_position_service. Optional — the gate
        falls back to a relative 0.1% band (qty/sign remain primary) when
        unwired or on a cache miss.
        """
        self._tick_resolver = fn

    def _local_pnl_from_ws(
        self, state, exit_price, exec_fee: float = 0.0, exec_pnl: float = 0.0
    ) -> tuple[float, float]:
        """Phantom-loss fix Commit 3: canonical per-close net PnL from the
        in-hand WS fill — the SSOT formula (same operator order as the
        on_trade_closed back-derive). Prefers Bybit's signed net execPnl
        when present; else reconstructs net = gross price-delta minus fees.
        Returns (pnl_pct, pnl_usd).
        """
        if state is None:
            return 0.0, 0.0
        entry = float(getattr(state, "entry_price", 0.0) or 0.0)
        size = float(getattr(state, "size", 0.0) or 0.0)
        side = getattr(state, "side", "") or ""
        notional = abs(size * entry)
        if exec_pnl and abs(exec_pnl) > 0:
            net_usd = float(exec_pnl)
        elif entry > 0 and exit_price and exit_price > 0:
            pct = ((float(exit_price) - entry) / entry) * 100.0
            if side in ("Sell", "Short"):
                pct = -pct
            net_usd = pct / 100.0 * notional - float(exec_fee or 0.0)
        else:
            return 0.0, 0.0
        net_pct = (net_usd / notional * 100.0) if notional > 0 else 0.0
        return net_pct, net_usd

    def _close_exit_tolerance(self, symbol: str, ref_price: float | None) -> float:
        """Phantom-loss fix Commit 3: exit-divergence tolerance for the
        staleness gate. Half a tick from InstrumentService.price_decimals
        when wired; else a relative 0.1% band (safe because qty/sign are the
        primary gate signals, not price proximity).
        """
        base = abs(float(ref_price)) if ref_price else 0.0
        fn = getattr(self, "_tick_resolver", None)
        if fn is not None and base > 0:
            try:
                decimals = fn(symbol)
                if decimals is not None and int(decimals) >= 0:
                    return 0.5 * (10 ** (-int(decimals)))
            except Exception:
                pass
        return base * 0.001 if base > 0 else 0.0

    def set_reentry_cooldown_seconds(self, seconds: int) -> None:
        """Override the per-(symbol, direction) reentry cooldown duration.

        Issue 3 (5-min reentry cooldown, 2026-05-18). Called by
        WorkerManager after construction so operators can tune the
        cooldown from APEXSettings (``reentry_cooldown_seconds``).
        Non-positive values are ignored so misconfiguration cannot
        disable the cooldown silently — the default
        (``_DEFAULT_REENTRY_COOLDOWN_SECONDS`` = 300) is preserved.

        Args:
            seconds: New cooldown duration in seconds. Must be > 0.
        """
        if seconds and seconds > 0:
            self._reentry_cooldown_seconds = int(seconds)

    def set_loss_cooldown_enabled(self, enabled: bool) -> None:
        """F9 (2026-06-09): toggle the loss-only cooldown + selection exclusion.

        Called by WorkerManager at wiring time from
        ``[apex].loss_cooldown_enabled``. When True, the re-entry cooldown is set
        only on a real loss (net dollar < 0) and the scanner excludes a symbol in
        an active loss cooldown from the candidate list. Default False preserves
        the prior every-close cooldown with no selection exclusion.
        """
        self._loss_cooldown_enabled = bool(enabled)

    def is_loss_cooldown_enabled(self) -> bool:
        """F9: whether the loss-only cooldown + selection exclusion is active."""
        return self._loss_cooldown_enabled

    def set_close_exit_divergence_pct(self, pct: float) -> None:
        """F5 (centralization, 2026-06-09): set the MARK-referenced exit-divergence
        band from config so the coordinator's staleness gates and the reconciler's
        exit-plausibility gate read ONE source of truth.

        Wired by WorkerManager at boot from
        ``[bybit_demo].close_pnl_reconcile_max_exit_divergence_pct`` (the same key
        the reconciler reads). A non-positive value is ignored so a misconfiguration
        cannot silently disable the phantom-loss staleness band; the constructor
        default (3.0) then stands. Behaviour-preserving when the config equals the
        default.
        """
        try:
            _p = float(pct)
        except (TypeError, ValueError):
            return
        if _p > 0:
            self._close_exit_divergence_pct = _p

    def _current_mode(self) -> str:
        """Return current trading mode for label derivation.

        Returns the empty string when transformer is not yet wired or has
        no mode set; callers must treat that as "use generic exchange_*
        prefix" rather than letting an empty string leak into a label.
        """
        if self._transformer is None:
            return ""
        try:
            mode = self._transformer.current_mode
            return str(mode) if mode else ""
        except Exception:
            return ""

    # ══════════════════════════════════════════════
    # STRATEGIC ACTION QUEUE (LayerManager → Watchdog)
    # ══════════════════════════════════════════════

    def queue_strategic_action(
        self, symbol: str, action: str, reason: str = "",
        new_sl: float = 0, exit_price: float = 0,
    ) -> None:
        """Queue a position action from strategic review for watchdog to execute.

        T1-1 / F18 phantom-close defense (six-tier-fixes 2026-05-11):
        rejects ``close`` and ``take_profit`` actions on symbols that
        are no longer in :attr:`_trades`. This is the coordinator-level
        layer of the three-layer defense (firewall, coordinator,
        layer_manager) and is independent of the upstream firewall —
        any future caller that bypasses the firewall still cannot queue
        a close on a non-active symbol. See
        ``dev_notes/six_tier_fixes/t1_1_phase1_investigation.md``.
        """
        if action in ("close", "take_profit") and symbol not in self._trades:
            log.warning(
                f"PHANTOM_CLOSE_REJECTED | layer=coordinator sym={symbol} "
                f"act={action} rsn='{reason[:80]}' | {ctx()}"
            )
            return
        self._strategic_actions.append({
            "symbol": symbol, "action": action, "reason": reason,
            "new_sl": new_sl, "exit_price": exit_price,
            "queued_at": time.time(),
        })
        log.info(f"COORD_QUEUE | sym={symbol} act={action} rsn='{reason[:60]}' | {ctx()}")

    def active_symbols(self) -> frozenset[str]:
        """Snapshot the symbols with an active :class:`TradeState`.

        Returns a frozenset safe to pass to
        :func:`src.sentinel.firewall.should_allow_strategic_action` as
        the ``active_symbols`` precondition. The snapshot is taken
        non-atomically; a concurrent register/close that races with the
        snapshot is picked up on the next caller invocation. T1-1
        callers (layer_manager) construct a fresh snapshot per
        position-action loop iteration. See
        ``dev_notes/six_tier_fixes/t1_1_phase1_investigation.md``.
        """
        return frozenset(self._trades.keys())

    def drain_strategic_actions(self) -> list[dict]:
        """Return and clear all pending strategic actions. Called by Watchdog each tick."""
        actions = self._strategic_actions[:]
        self._strategic_actions.clear()
        return actions

    def set_close_reason(self, symbol: str, reason: str) -> None:
        """Record why a position is being closed (before the close happens)."""
        self._close_reasons[symbol] = reason
        # Phase 13 Gap I3 (output-quality obs): emit POSITION_CLOSE_REASON
        # at INFO so the close-cause is captured in the audit trail
        # the moment the operator/system decides to close, not just when
        # Shadow finishes the placement. Pairs with SHADOW_POSITION_CLOSE
        # at shadow_adapter.py:251.
        log.info(
            f"POSITION_CLOSE_REASON | sym={symbol} reason={reason} | {ctx()}"
        )

    def pop_close_reason(self, symbol: str) -> str:
        """Get and remove the close reason for a symbol.

        When no explicit reason was set via ``set_close_reason``, returns
        the mode-aware default ``f"{current_mode}_sl_tp"``
        (e.g., ``"bybit_demo_sl_tp"``, ``"shadow_sl_tp"``,
        ``"bybit_sl_tp"``). When the transformer has not yet been wired
        (early boot edge case) returns ``"exchange_sl_tp"`` — generic but
        non-misleading.

        P2 of P1-P10: replaces the audit-flagged hardcoded
        ``"shadow_sl_tp"`` literal that fired on every external close
        regardless of which exchange triggered the SL/TP.
        """
        explicit = self._close_reasons.pop(symbol, None)
        if explicit is not None:
            return explicit
        mode = self._current_mode()
        return f"{mode}_sl_tp" if mode else "exchange_sl_tp"

    # ══════════════════════════════════════════════
    # TRADE REGISTRATION (Brain, Enforcer, Manual)
    # ══════════════════════════════════════════════

    def register_trade(
        self,
        symbol: str,
        strategy_category: str = "default",
        strategy_name: str = "",
        entry_price: float = 0.0,
        side: str = "",
        source: str = "brain_v2",
        decision_id: str = "",
        size: float = 0.0,
        # Phase 3: TIAS entry-time context
        claude_directive: str = "",
        claude_plan_view: str = "",
        signal_score: float | None = None,
        ensemble_score: str = "",
        # Layer 2 Defect 6 — numeric per-trade vote counts (see TradeState).
        supporting_count: int | None = None,
        opposing_count: int | None = None,
        # Layer 2 Defect 1 — per-cycle-per-symbol setup_id join key.
        setup_id: str = "",
        entry_regime: str = "",
        entry_rsi: float | None = None,
        entry_macd_hist: float | None = None,
        entry_atr_pct: float | None = None,
        # APEX optimization tracking (Phase 3 feedback loop)
        apex_optimized: bool = False,
        apex_was_flipped: bool = False,
        apex_confidence: float = 0.0,
        apex_tp_mode: str = "",
        apex_reasoning: str = "",
        apex_original_direction: str = "",
        apex_original_sl: float = 0.0,
        apex_original_tp: float = 0.0,
        apex_original_size: float = 0.0,
        apex_model: str = "",
        apex_response_ms: int = 0,
        apex_cost_usd: float = 0.0,
        gate_adjustments: str = "",
        order_id: str = "",
        # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) —
        # entry-time XRAY/regime anchors. Optional with neutral defaults so
        # legacy callers (brain_v2.register_trade at brain_v2.py:526) keep
        # working unchanged; only the strategy_worker caller passes them.
        entry_xray_confidence: float = 0.0,
        entry_setup_type: str = "",
        entry_regime_at_open: str = "",
        entry_regime_confidence: float = 0.0,
        # Observability G6 (field completeness, 2026-05-14) — the
        # audit asked for sl_price / tp_price / leverage / size_usd in
        # COORD_REG. Pre-G6 these arrived via the subsequent
        # register_trade_plan + _trade_info[...] paths, so COORD_REG
        # could only see what register_trade itself took. Adding them
        # here as optional kwargs (defaults preserve legacy callers
        # such as brain_v2.register_trade which doesn't pass them).
        sl_price: float = 0.0,
        tp_price: float = 0.0,
        leverage: int = 0,
        size_usd: float = 0.0,
    ) -> None:
        """Called by Brain/Enforcer/Manual AFTER placing a trade.
        Grants immunity from Watchdog for the appropriate duration.

        ``order_id`` (Definitive-fix Phase 8) is the exchange order id
        the trade was opened with. Stored on the TradeState and
        forwarded into the close callback record so thesis_manager can
        filter close_thesis by ``(symbol, order_id)`` instead of just
        ``symbol`` — prevents a previous trade's thesis from matching a
        fresh same-symbol re-open.

        ``sl_price`` / ``tp_price`` / ``leverage`` / ``size_usd`` are
        observability-only kwargs (G6 audit, 2026-05-14). They feed
        the COORD_REG emission's audit-required field set but are NOT
        persisted on TradeState (which has its own size + entry_price
        fields). Callers that don't pass them get COORD_REG with
        defaults (0 / 0.0); the values still live elsewhere (trade plan,
        _trade_info dict) for downstream consumers.
        """
        immunity = self.MINIMUM_HOLD_SECONDS.get(strategy_category, 60)

        # Observability G6 cluster — Phase 1 investigation surfaced
        # COORD_DUPLICATE_REGISTER as a missing event in the coordinator
        # cluster (audit Part D, Cluster D). register_trade overwrites
        # ``self._trades[symbol]`` silently if a prior registration
        # exists; downstream cooldown gate normally prevents this from
        # happening, but observability has to confirm the gate held —
        # not assume it. Emit a WARNING (not ERROR) since the overwrite
        # is intentional historical behaviour; the event flags the
        # condition so operators can audit gate effectiveness.
        if symbol in self._trades:
            _prior = self._trades[symbol]
            _prior_did = getattr(_prior, "brain_decision_id", "") or "-"
            _prior_opened = getattr(_prior, "opened_at", 0.0)
            _prior_age = time.time() - _prior_opened if _prior_opened else 0.0
            log.warning(
                f"COORD_DUPLICATE_REGISTER | sym={symbol} prior_did={_prior_did} "
                f"prior_age_s={_prior_age:.1f} new_did={decision_id} "
                f"new_src={source} | {ctx()}"
            )

        # Issue I2 (F-17, 2026-05-14) — capture the transformer's
        # current mode at register_trade time so the close-callback
        # chain (specifically _positions_table_cleanup_on_close at
        # manager.py:2198) has access to the TRADE'S mode rather than
        # the GLOBAL mode at close-dispatch time. Empty string falls
        # back to current_mode at close time (legacy behaviour).
        _trade_exchange_mode = self._current_mode()

        self._trades[symbol] = TradeState(
            symbol=symbol,
            strategy_name=strategy_name,
            strategy_category=strategy_category,
            opened_at=time.time(),
            opened_at_dt=datetime.now(timezone.utc),
            immunity_seconds=immunity,
            entry_price=entry_price,
            side=side,
            size=size,
            brain_decision_id=decision_id,
            source=source,
            claude_directive=claude_directive,
            claude_plan_view=claude_plan_view,
            signal_score=signal_score,
            ensemble_score=ensemble_score,
            supporting_count=supporting_count,
            opposing_count=opposing_count,
            setup_id=setup_id,
            entry_regime=entry_regime,
            entry_rsi=entry_rsi,
            entry_macd_hist=entry_macd_hist,
            entry_atr_pct=entry_atr_pct,
            apex_optimized=apex_optimized,
            apex_was_flipped=apex_was_flipped,
            apex_confidence=apex_confidence,
            apex_tp_mode=apex_tp_mode,
            apex_reasoning=apex_reasoning,
            apex_original_direction=apex_original_direction,
            apex_original_sl=apex_original_sl,
            apex_original_tp=apex_original_tp,
            apex_original_size=apex_original_size,
            apex_model=apex_model,
            apex_response_ms=apex_response_ms,
            apex_cost_usd=apex_cost_usd,
            gate_adjustments=gate_adjustments,
            order_id=order_id,
            entry_xray_confidence=entry_xray_confidence,
            entry_setup_type=entry_setup_type,
            entry_regime_at_open=entry_regime_at_open,
            entry_regime_confidence=entry_regime_confidence,
            exchange_mode=_trade_exchange_mode,
        )

        # Observability G6 (field completeness) — Phase 0 baseline
        # showed COORD_REG was firing per the audit, but missing
        # several audit-prescribed fields (side, qty, entry_price,
        # sl, tp, leverage, size_usd). The followup adds the remaining
        # audit fields via optional kwargs above so the emission is
        # complete against the audit schema. Legacy callers that don't
        # pass the new kwargs emit defaults (0 / 0.0) — same
        # observability surface as before for those paths.
        log.info(
            f"COORD_REG | sym={symbol} src={source} cat={strategy_category} "
            f"side={side or '-'} qty={size} entry_price={entry_price} "
            f"sl={sl_price} tp={tp_price} leverage={leverage} "
            f"size_usd={size_usd} immunity={immunity}s did={decision_id} "
            f"order_id={order_id or '-'} | {ctx()}"
        )

    # ══════════════════════════════════════════════
    # TRADE PLAN MANAGEMENT
    # ══════════════════════════════════════════════

    def register_trade_plan(self, symbol: str, plan) -> None:
        """Store the Brain's trade plan for watchdog monitoring."""
        import time as _time
        plan.opened_at = _time.time()
        plan.peak_price = plan.entry_price
        self._trade_plans[symbol] = plan
        from src.core.utils import format_price
        log.info(
            "TradePlan: {sym} {dir} target=${tp} SL=${sl} "
            "hold={hold}min trail@{trail}% tier={tier}",
            sym=symbol, dir=plan.direction,
            tp=format_price(plan.target_price), sl=format_price(plan.stop_loss_price),
            hold=plan.max_hold_minutes,
            trail=plan.trailing_activation_pct,
            tier=plan.size_tier,
        )
        # Issue I5 (F-32, 2026-05-14) — structured emission so
        # operators see TradePlan registrations in the canonical
        # tag inventory. The trade plan itself is persisted via
        # thesis_manager.save_thesis at the strategy_worker site
        # (which already happens before this register_trade_plan
        # call). This event confirms the trade plan reached the
        # coordinator's in-memory tracker AND that the DB-side
        # thesis row is what state-recovery will read on restart.
        try:
            log.info(
                f"TRADEPLAN_PERSISTED | sym={symbol} dir={plan.direction} "
                f"entry={plan.entry_price} sl={plan.stop_loss_price} "
                f"tp={plan.target_price} "
                f"hold_min={plan.max_hold_minutes} "
                f"tier={getattr(plan, 'size_tier', '-')} "
                f"| {ctx()}"
            )
        except Exception:
            # Best-effort observability; never break trade plan
            # registration on a log-formatting error.
            pass

    def get_trade_plan(self, symbol: str):
        """Get the trade plan for a symbol, or None."""
        return self._trade_plans.get(symbol)

    def remove_trade_plan(self, symbol: str) -> None:
        """Remove trade plan after position closes."""
        self._trade_plans.pop(symbol, None)
        self._trade_info.pop(symbol, None)

    def get_trade_info(self, symbol: str) -> dict:
        """Get extended trade info (score, consensus, leverage, amount, etc.)"""
        return self._trade_info.get(symbol, {})

    # ══════════════════════════════════════════════
    # IMMUNITY & MATURITY CHECKS (Watchdog uses these)
    # ══════════════════════════════════════════════

    def is_immune(self, symbol: str) -> tuple[bool, float, str]:
        """Check if a position has trade immunity.
        Returns: (is_immune, remaining_seconds, reason)"""
        state = self._trades.get(symbol)
        if not state:
            return (False, 0, "No registered trade — normal monitoring")

        elapsed = time.time() - state.opened_at
        remaining = state.immunity_seconds - elapsed

        if remaining > 0:
            return (
                True,
                remaining,
                f"IMMUNE: {remaining:.0f}s left (source={state.source}, "
                f"category={state.strategy_category})",
            )

        return (False, 0, f"Immunity expired after {state.immunity_seconds}s")

    def get_maturity(
        self, symbol: str, pnl_pct: float, sl_proximity_pct: float,
    ) -> tuple[bool, str, str]:
        """Check position maturity phase.
        Returns: (can_close, phase_name, reason)

        Two-phase model after the SL Hierarchy overhaul (2026-04-22):
          - newborn (0-120s): grace period, cannot close.
          - mature/aged (120s+): all normal rules apply.

        pnl_pct and sl_proximity_pct are retained in the signature for
        caller compatibility but unused in the two-phase model.
        """
        state = self._trades.get(symbol)
        if not state:
            return (True, "unknown", "No registered trade — allow watchdog action")

        age = time.time() - state.opened_at

        if age < 120:
            return (False, "newborn", f"Newborn ({age:.0f}s) — NEVER close")

        phase = "mature" if age < 3600 else "aged"
        return (True, phase, f"{phase.capitalize()} ({age / 60:.0f}min) — normal rules")

    def get_trade_state(self, symbol: str) -> TradeState | None:
        return self._trades.get(symbol)

    def get_age_seconds(self, symbol: str) -> float:
        state = self._trades.get(symbol)
        if not state:
            return 99999
        return time.time() - state.opened_at

    def get_age_context_for_prompt(self, symbol: str, pnl_pct: float) -> dict:
        """Build age context dict for the Watchdog Claude prompt."""
        state = self._trades.get(symbol)
        if not state:
            return {
                "position_age": "unknown",
                "strategy_category": "unknown",
                "maturity_phase": "unknown",
                "age_context": "No registration data — apply standard analysis.",
            }

        age_seconds = time.time() - state.opened_at
        age_minutes = age_seconds / 60
        expected_hold_min = state.immunity_seconds / 60

        if age_seconds < 60:
            position_age = f"{age_seconds:.0f} seconds"
        elif age_seconds < 3600:
            position_age = f"{age_minutes:.1f} minutes"
        else:
            position_age = f"{age_seconds / 3600:.1f} hours"

        # Display phases match MATURITY_PHASES (two-phase + aged).
        if age_seconds < 120:
            phase = "NEWBORN"
        elif age_seconds < 3600:
            phase = "MATURE"
        else:
            phase = "AGED"

        if age_minutes < 5:
            ctx = (
                ">>> MANDATORY: Position is LESS THAN 5 MINUTES OLD. "
                "Small losses are expected from spread/slippage. "
                "DO NOT CLOSE. Default to HOLD."
            )
        elif age_minutes < expected_hold_min:
            ctx = (
                f">>> Position is {age_minutes:.0f}min old, BELOW expected hold time "
                f"of {expected_hold_min:.0f}min for {state.strategy_category}. "
                f"Trade thesis has NOT played out. STRONGLY prefer HOLD."
            )
        elif pnl_pct > 0:
            ctx = (
                f">>> Position is {age_minutes:.0f}min old and PROFITABLE at {pnl_pct:+.2f}%. "
                f"Tighten stop-loss to protect gains. Do NOT close profitable positions."
            )
        else:
            ctx = (
                f">>> Position is {age_minutes:.0f}min old, past expected hold time. "
                f"Full analysis appropriate."
            )

        return {
            "position_age": position_age,
            "strategy_category": state.strategy_category,
            "maturity_phase": phase,
            "age_context": ctx,
        }

    # ══════════════════════════════════════════════
    # TRADE CLOSE NOTIFICATION
    # ══════════════════════════════════════════════

    async def resolve_authoritative_pnl(
        self,
        *,
        symbol: str,
        position_service: Any,
        fallback_pnl_usd: float,
        fallback_pnl_pct: float,
        fallback_exit_price: float | None = None,
        order_id: str | None = None,
        ws_exec_price: float | None = None,
        ws_close_ts_ms: float | None = None,
        qty: float | None = None,
        tick_tolerance: float | None = None,
        entry_price: float | None = None,
    ) -> tuple[float, float, str, float | None]:
        """Resolve authoritative ``pnl_usd`` / ``pnl_pct`` for a self-initiated close.

        The price-source-divergence forensic
        (``dev_notes/price_source_divergence/FULL_BUNDLE.md``) found that
        self-initiated close sites in ``position_watchdog`` and ``profit_sniper``
        were persisting locally-computed P&L (derived from
        ``pos.unrealized_pnl``, which is the Transformer-overwritten value)
        instead of Shadow's authoritative ``virtual_positions.net_pnl_usd``
        post-fee post-slippage figure. The external-detection path at
        ``position_watchdog.py:2569-2578`` already had a fix that prefers
        Shadow's net values via ``get_last_close``; this helper extends the
        same pattern to the 11 self-initiated close sites identified by
        the Phase 0 grep survey.

        Behaviour:

        - Shadow paper-trading mode: calls ``position_service.get_last_close
          (symbol)``. The proxy at ``transformer.py:1020-1030`` forwards to
          ``ShadowPositionService.get_last_close`` which queries Shadow's
          ``GET /api/position/{sym}/last_close`` and returns the
          authoritative virtual_positions row (post-fee, post-slippage).
          When the dict is well-formed, returns ``(net_pnl_usd, net_pnl_pct,
          "exchange_authoritative", exit_price)``. (The label was renamed
          from ``"shadow_authoritative"`` in P2 of P1-P10 — the resolved
          data applies equally to Shadow, Bybit demo, and Bybit live
          authoritative stores; the old name was misleading.)

        - Bybit live mode: the underlying ``PositionService`` has no
          ``get_last_close`` method, so the proxy returns ``None``. In this
          mode the helper falls back to the caller-supplied locally-computed
          values with ``price_source="local_fallback"``. Live mode does not
          have the slippage/fee-simulation gap so the local computation is
          itself authoritative there (Bybit's order response carries the real
          fill price).

        - Shadow transport / race failures: rare per Shadow's
          ``order_engine.close_position`` which commits the close row
          *before* returning the HTTP response, so the row is queryable by
          the time ``close_position`` returns. Network blips, Shadow DB
          locks, or service restarts can still cause ``get_last_close`` to
          return ``None`` or raise; the helper falls back to local with a
          WARNING log so reconciliation problems are visible.

        Args:
            symbol: Trading pair, e.g. ``"BTCUSDT"``.
            position_service: The position service the caller already
                holds (typically the Transformer's ``_PositionProxy``).
                Must expose ``async get_last_close(symbol)`` or equivalent;
                missing method falls back silently.
            fallback_pnl_usd: Locally-computed ``pnl_usd`` to use when
                Shadow data is unavailable. Typically ``pos.unrealized_pnl``
                from the closing site.
            fallback_pnl_pct: Locally-computed ``pnl_pct`` for the same
                fallback path.
            fallback_exit_price: Optional locally-known exit price to fall
                back to. ``None`` when the caller has none to offer.

        Returns:
            Tuple ``(pnl_usd, pnl_pct, price_source, exit_price)``.
            ``price_source`` is one of:

            - ``"exchange_authoritative"`` — values resolved from the
              active exchange's authoritative store (Shadow's
              ``virtual_positions`` for shadow mode, Bybit demo's
              ``/v5/position/closed-pnl`` for bybit_demo). ``exit_price``
              is the exchange's authoritative post-slippage fill price.
              (Renamed from ``"shadow_authoritative"`` in P2 of P1-P10.)
            - ``"local_fallback"`` — exchange returned ``None``, raised,
              or returned malformed data. Tuple carries the caller's
              fallbacks.
        """
        fn = getattr(position_service, "get_last_close", None)
        if fn is None:
            # Position service has no get_last_close at all (e.g. test mock
            # or a non-standard service). Use local fallback. PF/LC Top-15
            # Problem 1.3: this is exactly the wiring gap that booked every
            # WS self-close gross — the raw Transformer reaches here while
            # the _PositionProxy does not. Log it at WARNING so any future
            # mis-wire is visible in a grep instead of silently regressing
            # the PnL ruler. (Close-time frequency, so not log-spammy.)
            log.warning(
                f"WD_LAST_CLOSE_FALLBACK | sym={symbol} reason=no_get_last_close "
                f"svc={type(position_service).__name__} "
                f"local_pnl_usd={fallback_pnl_usd:+.4f} "
                f"local_pnl_pct={fallback_pnl_pct:+.4f} | {ctx()}"
            )
            return (
                fallback_pnl_usd, fallback_pnl_pct,
                "local_fallback", fallback_exit_price,
            )

        # Phantom-loss fix Commit 3: only forward identity hints when the
        # caller supplied them (the 11 watchdog/sniper callers pass none, so
        # fn(symbol) is called exactly as before — byte-identical legacy).
        _hints: dict[str, Any] = {}
        if order_id is not None:
            _hints["order_id"] = order_id
        if ws_exec_price is not None:
            _hints["ws_exec_price"] = ws_exec_price
        if ws_close_ts_ms is not None:
            _hints["ws_close_ts_ms"] = ws_close_ts_ms
        if qty is not None:
            _hints["qty"] = qty
        if tick_tolerance is not None:
            _hints["tick_tolerance"] = tick_tolerance
        if entry_price is not None:
            _hints["entry_price"] = entry_price
        try:
            shadow_close = await fn(symbol, **_hints)
        except Exception as e:
            log.warning(
                f"WD_LAST_CLOSE_FALLBACK | sym={symbol} reason=exception "
                f"err='{str(e)[:120]}' "
                f"local_pnl_usd={fallback_pnl_usd:+.4f} "
                f"local_pnl_pct={fallback_pnl_pct:+.4f} | {ctx()}"
            )
            return (
                fallback_pnl_usd, fallback_pnl_pct,
                "local_fallback", fallback_exit_price,
            )

        if not shadow_close or not isinstance(shadow_close, dict):
            # None or empty dict: Bybit live mode (proxy returns None when
            # underlying service lacks the method) or Shadow race / transport
            # blip. Logged at INFO level because Bybit-mode None is expected;
            # operators should filter on WARNING-level WD_LAST_CLOSE_FALLBACK
            # entries (the exception/missing-fields paths) to find real issues.
            log.info(
                f"WD_LAST_CLOSE_FALLBACK | sym={symbol} reason=no_data "
                f"local_pnl_usd={fallback_pnl_usd:+.4f} "
                f"local_pnl_pct={fallback_pnl_pct:+.4f} | {ctx()}"
            )
            return (
                fallback_pnl_usd, fallback_pnl_pct,
                "local_fallback", fallback_exit_price,
            )

        # Phantom-loss fix (cross-check completion 2026-06-05): qty-primary
        # staleness gate for ALL resolve-based close paths (close_with plus the
        # 11 watchdog/sniper self-close sites funnel here). The coordinator
        # holds the live trade state, so if the exchange row's qty does not
        # match THIS trade's size, the row belongs to a DIFFERENT (earlier)
        # trade — the closed-pnl indexer returning a stale row — and we fall
        # back to the caller's local value rather than book the wrong trade.
        # The WS self-close path additionally has the exit-divergence gate in
        # on_trade_closed; this covers the poll-detected paths that carry no WS
        # exit to compare. Skipped when the row has no qty (e.g. Shadow, which
        # commits synchronously and has no indexer lag).
        _gate_state = self._trades.get(symbol)
        _gate_local_qty = (
            float(getattr(_gate_state, "size", 0.0) or 0.0)
            if _gate_state is not None else 0.0
        )
        try:
            _gate_row_qty = float(shadow_close.get("qty") or 0.0)
        except (TypeError, ValueError):
            _gate_row_qty = 0.0
        if (
            _gate_local_qty > 0 and _gate_row_qty > 0
            and abs(_gate_row_qty - _gate_local_qty) / _gate_local_qty > 0.01
        ):
            log.warning(
                f"WD_LAST_CLOSE_STALE_ROW | sym={symbol} reason=qty_mismatch "
                f"row_qty={_gate_row_qty} local_qty={_gate_local_qty} "
                f"shadow_pnl_usd={shadow_close.get('net_pnl_usd')} "
                f"shadow_exit={shadow_close.get('exit_price')} | {ctx()}"
            )
            return (
                fallback_pnl_usd, fallback_pnl_pct,
                "local_fallback_stale", fallback_exit_price,
            )

        try:
            _s_usd = shadow_close.get("net_pnl_usd")
            _s_pct = shadow_close.get("net_pnl_pct")
            if _s_usd is None or _s_pct is None:
                raise ValueError(
                    f"net_pnl_usd={_s_usd!r} net_pnl_pct={_s_pct!r}"
                )
            shadow_pnl_usd = float(_s_usd)
            shadow_pnl_pct = float(_s_pct)
        except (TypeError, ValueError) as e:
            log.warning(
                f"WD_LAST_CLOSE_FALLBACK | sym={symbol} reason=missing_fields "
                f"err='{str(e)[:120]}' "
                f"keys={list(shadow_close.keys())} | {ctx()}"
            )
            return (
                fallback_pnl_usd, fallback_pnl_pct,
                "local_fallback", fallback_exit_price,
            )

        shadow_exit: float | None = None
        try:
            _exit = shadow_close.get("exit_price")
            if _exit is not None:
                shadow_exit = float(_exit)
        except (TypeError, ValueError):
            shadow_exit = None

        # F5 follow-up (2026-06-08, F5-a): exit-divergence staleness gate at the
        # single resolve chokepoint, covering the watchdog strategic-action
        # self-close paths (plan_timer / trailing_stop / early_exit / hard_stop /
        # timeout / profit_take and siblings) that funnel through here. The qty
        # gate above misses a STALE row that happens to share THIS trade's qty
        # (a same-size earlier close the qty-only adapter match can grab). When
        # the caller supplies its trusted local mark (fallback_exit_price) and
        # the resolved exchange exit diverges beyond the per-symbol tolerance,
        # the row is a wrong-trade row — fall back to the caller's local value
        # tagged local_fallback_stale so the reconciler retries for the real row.
        #
        # GATED to NON-identity callers only (order_id is None AND ws_exec_price
        # is None): the WS identity path (close_with_authoritative_pnl in ws_exec
        # mode) deliberately keeps an exchange-authoritative row whose
        # post-slippage fill legitimately differs from the WS exit
        # (COORD_IDENTITY_CONFIRMED_NO_DEMOTE, the 2026-06-07 fix) — it must NOT
        # be demoted here. Robust even after _trades is popped (unlike the qty
        # gate). Active only when the caller supplies fallback_exit_price.
        if (
            order_id is None and ws_exec_price is None
            and fallback_exit_price is not None and fallback_exit_price > 0
            and shadow_exit is not None and shadow_exit > 0
        ):
            # MARK-referenced band (3%): fallback_exit_price is the caller's live
            # mark, not the exact fill, so use the wider exit-plausibility band
            # (matches the reconciler's gate) — catches the >~3% phantom exits
            # without demoting a legitimate fee-flip close to the local gross.
            _ediv_tol = abs(fallback_exit_price) * self._close_exit_divergence_pct / 100.0
            if _ediv_tol > 0 and abs(shadow_exit - fallback_exit_price) > _ediv_tol:
                log.warning(
                    f"WD_LAST_CLOSE_STALE_ROW | sym={symbol} "
                    f"reason=exit_divergence_resolve "
                    f"row_exit={shadow_exit} local_exit={fallback_exit_price} "
                    f"tol={_ediv_tol} shadow_pnl_usd={shadow_pnl_usd:+.4f} "
                    f"local_pnl_usd={fallback_pnl_usd:+.4f} | {ctx()}"
                )
                return (
                    fallback_pnl_usd, fallback_pnl_pct,
                    "local_fallback_stale", fallback_exit_price,
                )

        log.info(
            f"WD_LAST_CLOSE_AUTH | sym={symbol} "
            f"shadow_pnl_usd={shadow_pnl_usd:+.4f} "
            f"local_pnl_usd={fallback_pnl_usd:+.4f} "
            f"delta=${shadow_pnl_usd - fallback_pnl_usd:+.4f} "
            f"shadow_exit={shadow_exit} | {ctx()}"
        )
        # P2 of P1-P10: rename "shadow_authoritative" → "exchange_authoritative".
        # The label means "post-fee data resolved from the exchange's
        # authoritative store" — applies equally to Shadow virtual_positions
        # and to Bybit demo's /v5/position/closed-pnl. The old "shadow_"
        # prefix misled operators into thinking only Shadow contributed.
        # Watchdog comparisons updated in lockstep (position_watchdog.py).
        return shadow_pnl_usd, shadow_pnl_pct, "exchange_authoritative", shadow_exit

    async def close_with_authoritative_pnl(
        self,
        symbol: str,
        exit_price: float,
        closed_by: str,
        *,
        exec_pnl: float = 0.0,
        exec_fee: float = 0.0,
        ws_order_id: str | None = None,
        ws_exec_qty: float | None = None,
        ws_close_ts: float | None = None,
        close_pnl_source: str = "legacy",
    ) -> None:
        """Resolve the exchange's authoritative net PnL, then book the close.

        PnL-truth fix (2026-05-26, operator-approved "truth everywhere").
        The bybit_demo WS execution path used to call ``on_trade_closed``
        with a zero sentinel, so the coordinator back-derived a GROSS,
        fee-free PnL from entry/exit prices. Confirmed from the real
        wallet: that booked +$65 for a window while the wallet lost
        ~$2,274. This wrapper resolves the exchange's real ``closedPnl``
        (net of fees) via the already-trusted
        :meth:`resolve_authoritative_pnl` over the coordinator's own
        late-bound transformer (the same path the watchdog uses, proven
        live by WD_LAST_CLOSE_AUTH), then books that net number so every
        downstream sink and consumer sees the truth.

        Fallback: if no transformer is wired, or the exchange returns no
        data (indexer lag, transport blip), this degrades to the prior
        sentinel/back-derive behaviour (pnl_pct=0 + exit_price) so the
        close is never lost — only its precision regresses to gross, and
        the PNL_RECONCILE log will surface the residual.
        """
        # Phantom-loss fix Commit 3: compute the in-hand WS net as the REAL
        # local guardrail (was a dead 0.0). state is READ, not popped — the
        # single pop stays in on_trade_closed. ws_net/ws_pct/ws_exit become
        # both the resolver fallback AND the staleness-gate reference.
        state = self._trades.get(symbol)
        ws_pct, ws_net = self._local_pnl_from_ws(
            state, exit_price, exec_fee=exec_fee, exec_pnl=exec_pnl
        )
        ref_qty = (
            float(getattr(state, "size", 0.0) or 0.0) if state is not None else None
        )

        auth_usd, auth_pct, src, auth_exit = (ws_net, ws_pct, "local_fallback", exit_price)
        _resolver = self._position_service or self._transformer
        if _resolver is not None:
            try:
                # In 'gated'/'ws_exec' modes, thread WS identity so the REST
                # closed-pnl row is identity-matched (no stale rows[0]); in
                # 'legacy' (default) the source is unchanged and the
                # unconditional staleness gate below is the protection.
                _hint: dict[str, Any] = {}
                if close_pnl_source in ("gated", "ws_exec"):
                    if ws_order_id and close_pnl_source == "ws_exec":
                        _hint["order_id"] = ws_order_id
                    if exit_price and exit_price > 0:
                        _hint["ws_exec_price"] = exit_price
                    if ws_close_ts:
                        _hint["ws_close_ts_ms"] = ws_close_ts
                    if ws_exec_qty:
                        _hint["qty"] = ws_exec_qty
                    _tol = self._close_exit_tolerance(symbol, exit_price)
                    if _tol > 0:
                        _hint["tick_tolerance"] = _tol
                auth_usd, auth_pct, src, auth_exit = await self.resolve_authoritative_pnl(
                    symbol=symbol,
                    position_service=_resolver,
                    fallback_pnl_usd=ws_net,
                    fallback_pnl_pct=ws_pct,
                    fallback_exit_price=exit_price,
                    **_hint,
                )
            except Exception as e:
                log.warning(
                    f"COORD_AUTH_CLOSE_RESOLVE_FAIL | sym={symbol} "
                    f"err='{str(e)[:120]}' falling_back=ws_net | {ctx()}"
                )
                auth_usd, auth_pct, src, auth_exit = (
                    ws_net, ws_pct, "local_fallback", exit_price,
                )
        # Identity-confirmed when we asked the resolver to match by the close
        # order_id (ws_exec mode) AND it returned an exchange-authoritative row.
        # The adapter's identity resolver never falls back to rows[0] — it
        # returns None (→ local_fallback) on no confident match — so an
        # exchange_authoritative result in ws_exec+order_id mode IS this trade.
        _identity_confirmed = (
            close_pnl_source == "ws_exec"
            and bool(ws_order_id)
            and src == "exchange_authoritative"
        )
        log.info(
            f"COORD_AUTH_CLOSE | sym={symbol} src={src} mode={close_pnl_source} "
            f"match={'order_id' if _identity_confirmed else 'none'} "
            f"net_pnl_usd={auth_usd:+.4f} net_pnl_pct={auth_pct:+.4f} "
            f"ws_net={ws_net:+.4f} ws_exit={exit_price} by={closed_by} | {ctx()}"
        )
        self.on_trade_closed(
            symbol=symbol,
            pnl_pct=auth_pct,
            pnl_usd=auth_usd,
            was_win=auth_usd > 0,
            closed_by=closed_by,
            exit_price=(auth_exit if auth_exit else exit_price),
            price_source=src,
            ref_pnl_usd=ws_net,
            ref_pnl_pct=ws_pct,
            ref_exit_price=exit_price,
            ref_qty=ref_qty,
            identity_confirmed=_identity_confirmed,
        )

    def on_trade_closed(
        self,
        symbol: str,
        pnl_pct: float,
        pnl_usd: float,
        was_win: bool,
        closed_by: str = "watchdog",
        exit_price: float | None = None,
        price_source: str | None = None,
        *,
        ref_pnl_usd: float | None = None,
        ref_pnl_pct: float | None = None,
        ref_exit_price: float | None = None,
        ref_qty: float | None = None,
        candidate_qty: float | None = None,
        identity_confirmed: bool = False,
        ref_is_mark: bool = False,
    ) -> None:
        """Called when a position is closed by ANY component.

        Bug 2 fix: `exit_price` is accepted as an optional kwarg so the
        watchdog (and any other caller) can pass the exchange's
        authoritative close price instead of relying on back-derivation
        from pnl_pct.

        `price_source` is a label recorded in the close record for
        downstream auditing (TIAS, data lake). Known values:
        ``"exchange_authoritative"`` (post-fee data from the active
        exchange's authoritative store; renamed from
        ``"shadow_authoritative"`` in P2 of P1-P10),
        ``"bybit_ws_authoritative"`` (P1 — pushed via WS execution
        stream), ``"ticker_fallback"`` (mid-price fallback when
        authoritative data unavailable), ``"last_tick_cache"`` (cached
        ticker), ``"derived"`` (back-derived from pnl_pct + entry).
        """
        state = self._trades.pop(symbol, None)

        # Double-close guard: if state is None, this symbol was already closed
        # by another component (race between Watchdog/ProfitSniper/SENTINEL)
        if state is None:
            log.warning(
                f"COORD_DOUBLE_CLOSE | sym={symbol} by={closed_by} | "
                f"already closed — skipping duplicate | {ctx()}"
            )
            return

        hold_seconds = time.time() - state.opened_at

        # Resolve side once so the record dict always has a valid direction
        # even when pnl_pct is zero (edge case on ticker-fallback closes).
        entry_price = state.entry_price if state and hasattr(state, "entry_price") else 0
        _side = state.side if state and hasattr(state, "side") else ""

        # Bug 2 fix: prefer the caller-supplied authoritative exit_price.
        # Only back-derive from pnl_pct when no exit_price was passed.
        close_price = 0.0
        if exit_price is not None and exit_price > 0:
            close_price = float(exit_price)
        elif entry_price > 0 and pnl_pct != 0:
            if _side in ("Sell", "Short"):
                close_price = entry_price * (1 - pnl_pct / 100)
            else:
                close_price = entry_price * (1 + pnl_pct / 100)

        # CRITICAL-1 fix (sentinel-zero contract): back-derive pnl_pct from
        # entry/exit/side when the caller passed pnl_pct=0 with a valid
        # exit_price. The bybit_demo WS subscriber (
        # bybit_demo_websocket_subscriber.py:489-497) explicitly does this:
        # it has authoritative exec_price from the Bybit fill but no
        # pre-computed PnL, so it passes pnl_pct=0.0 as a sentinel meaning
        # "compute it from prices and side".
        #
        # Without this branch, the existing pnl_usd back-derive below stays
        # gated by `pnl_pct != 0` and produces pnl=0 across trade_log,
        # trade_intelligence, and trade_thesis (the close-callback fan-out
        # at lines 776-784 broadcasts the corrupt record dict to all 14
        # registered consumers).
        #
        # Direction sign matches the canonical inline computation at
        # bybit_demo_adapter.close_position lines 392-401 and the existing
        # close_price back-derive at lines 689-693 (same `("Sell", "Short")`
        # membership convention). was_win is flipped from the back-derived
        # value so the 6 downstream was_win consumers (Performance Enforcer,
        # fund_manager, registry, /pnl handler, TIAS, learning_repo) see
        # the correct outcome.
        if pnl_pct == 0 and entry_price > 0 and close_price > 0:
            # Compute with the exact same operator order as
            # bybit_demo_adapter.close_position:392-401 so coordinator-path
            # records and trade_history rows produce bit-identical pnl_pct
            # values (Python float arithmetic is not commutative for
            # `(a-b)/c` vs `-(b-a)/c` at the 1e-10 magnitude).
            pnl_pct = ((close_price - entry_price) / entry_price) * 100
            if _side in ("Sell", "Short"):
                pnl_pct = -pnl_pct
            was_win = pnl_pct > 0
            log.info(
                f"COORD_PNL_BACK_DERIVED | sym={symbol} ent={entry_price} "
                f"ext={close_price} side={_side or 'unk'} "
                f"pnl_pct={pnl_pct:+.4f}% win={'Y' if was_win else 'N'} "
                f"by={closed_by} | {ctx()}"
            )

        # Calculate pnl_usd if not provided
        if pnl_usd == 0 and pnl_pct != 0 and entry_price > 0:
            _size = getattr(state, "size", 0) if state else 0
            if _size > 0:
                pnl_usd = pnl_pct / 100 * abs(_size * entry_price)
            else:
                # Fallback: compute notional from _trade_info (amount_usd * leverage)
                info = self._trade_info.get(symbol, {})
                amount_usd = info.get("amount_usd", 0)
                leverage = info.get("leverage", 1)
                if amount_usd > 0:
                    notional = amount_usd * leverage
                    pnl_usd = pnl_pct / 100 * notional

        # ── Phantom-loss staleness/sign gate (2026-06-05 Commit 3) ──
        # Single-writer placement: covers EVERY booking path that reaches
        # on_trade_closed. When the booked value claims exchange authority,
        # verify it belongs to THIS close by comparing the candidate
        # exit/sign/qty against the trusted local reference (the in-hand WS
        # fill, or the caller's local mark). A stale prior-trade row (Bybit
        # closed-pnl indexer lag — the phantom-loss root cause) diverges and
        # is demoted to the local net tagged local_fallback_stale. This is
        # the UNCONDITIONAL fix (active in every mode). See
        # PHANTOM_LOSS_FIX_DESIGN_2026-06-04.md section 4.4.
        # F5 part 1 (2026-06-09 phantom-close follow-up): the gate previously
        # required price_source=='exchange_authoritative', so the watchdog
        # TICKER_FALLBACK path (which booked the SKR +15.06 phantom from a stale
        # row) bypassed it entirely — "armed only when it wasn't needed". The
        # qty-mismatch and exit-divergence checks below are authority-INDEPENDENT
        # (a stale wrong-trade row diverges in qty/exit no matter the price source),
        # so the gate now fires whenever a trusted reference (ref_pnl_usd) is
        # supplied. When no reference is supplied (ref_pnl_usd is None) the gate is
        # inert exactly as before — no behaviour change on un-armed paths.
        if ref_pnl_usd is not None:
            _stale_reason = None
            _cand_exit = (
                float(exit_price) if (exit_price and exit_price > 0) else close_price
            )
            # F5 (2026-06-08): MARK-referenced callers (poll / sniper /
            # watchdog-strategic, ref_is_mark=True) compare a stale row's exit
            # against the live MARK, which legitimately differs from the realised
            # fill by ordinary slippage — so they use the wider exit-plausibility
            # band (self._close_exit_divergence_pct, 3%) rather than the tight
            # half-tick _close_exit_tolerance (which is correct only for the WS
            # fill-vs-fill comparison). This prevents a real fee-flip close (net
            # loss whose gross mark looked like a win) being demoted to the local
            # gross — itself a transient phantom win.
            if ref_is_mark:
                _mark_ref = ref_exit_price or _cand_exit
                _tol = (
                    abs(float(_mark_ref)) * self._close_exit_divergence_pct / 100.0
                    if _mark_ref else 0.0
                )
            else:
                _tol = self._close_exit_tolerance(symbol, ref_exit_price or _cand_exit)
            # 1. qty mismatch (primary, when the candidate row's qty is known)
            if candidate_qty is not None and ref_qty and ref_qty > 0:
                if abs(candidate_qty - ref_qty) / max(ref_qty, 1e-9) > 0.01:
                    _stale_reason = "qty_mismatch"
            # 2. exit divergence — a stale wrong-trade row belongs to a
            #    DIFFERENT trade and therefore has a different exit price than
            #    this close's in-hand WS fill.
            #    PnL-truth fix (2026-06-07): when the close was identity-matched
            #    by the exchange order_id (ws_exec mode, identity_confirmed=True),
            #    the row IS this trade by definition. The exchange's authoritative
            #    post-slippage fill price legitimately differs from the locally
            #    observed WS exit, and the exchange NET (after fees/funding) is
            #    the truth — it must NOT be demoted to the local gross. So the
            #    exit-divergence demotion applies ONLY to non-identity-confirmed
            #    callers (legacy/qty-only resolution). This is the root fix for
            #    the booked-win / exchange-loss sign-flip (e.g. DOGE booked
            #    +18.84 while the exchange truth was -4.36).
            _exit_diverges = (
                _stale_reason is None
                and ref_exit_price and ref_exit_price > 0
                and _cand_exit and _cand_exit > 0
                and abs(_cand_exit - ref_exit_price) > _tol
            )
            if _exit_diverges and not identity_confirmed:
                _stale_reason = "exit_divergence"
            elif _exit_diverges and identity_confirmed:
                log.info(
                    f"COORD_IDENTITY_CONFIRMED_NO_DEMOTE | sym={symbol} "
                    f"cand_exit={_cand_exit} ref_exit={ref_exit_price} tol={_tol} "
                    f"exchange_pnl_usd={pnl_usd:+.4f} local_ws_usd={ref_pnl_usd:+.4f} "
                    f"booked=exchange_authoritative | {ctx()}"
                )
            # NOTE (2026-06-05 cross-check fix): a sign-only difference is
            # deliberately NOT treated as stale. When the exit matches but the
            # sign differs, that is a LEGITIMATE fee-driven flip (gross profit,
            # net loss after fees) — the exchange NET is the truth and must be
            # booked (the 2026-05-26 PnL-truth fix). A stale wrong-trade row is
            # caught by qty/exit divergence above; sign-alone would false-
            # positive on every fee-flip close and wrongly revert net to gross.
            if _stale_reason is not None:
                log.warning(
                    f"WD_LAST_CLOSE_STALE_ROW | sym={symbol} reason={_stale_reason} "
                    f"cand_exit={_cand_exit} ref_exit={ref_exit_price} tol={_tol} "
                    f"cand_qty={candidate_qty} ref_qty={ref_qty} "
                    f"cand_pnl_usd={pnl_usd:+.4f} ref_pnl_usd={ref_pnl_usd:+.4f} "
                    f"| {ctx()}"
                )
                pnl_usd = float(ref_pnl_usd)
                if ref_pnl_pct is not None:
                    pnl_pct = float(ref_pnl_pct)
                if ref_exit_price and ref_exit_price > 0:
                    close_price = float(ref_exit_price)
                was_win = pnl_usd > 0
                price_source = "local_fallback_stale"

        # T2-8 (2026-05-12) — two-source PnL contradiction resolution.
        # F65 case: a single COORD_CLOSE event reported
        #   pnl=+0.0958%  pnl$=-0.6174  win=N
        # Three sources disagreed: percentage POSITIVE (suggests win),
        # dollar amount NEGATIVE (loss after fees), win flag N.
        # Pre-fix the coordinator preserved caller-supplied pnl_pct and
        # pnl_usd unmodified; downstream consumers (TIAS,
        # performance_enforcer, dashboard) might pick the wrong source.
        #
        # Resolution rule: pnl_usd is the AUTHORITATIVE outcome (Bybit's
        # realized PnL with fees). When signs disagree, back-derive
        # pnl_pct from pnl_usd / notional so all consumers see a
        # consistent signal. The pre-fee pnl_pct is preserved in
        # `pnl_pct_pre_fee` for forensic correlation.
        # `was_win` is derived from pnl_usd (the post-fee outcome) so
        # the strategy enforcer counts losses correctly.
        # Always emit PNL_SOURCE_RESOLVED so the resolution is greppable.
        _pnl_pct_pre_fee = pnl_pct
        _t2_8_resolved = False
        _t2_8_notional_used = 0.0
        # F1 Mode-A (2026-06-08): also fire when the percent was never populated
        # (0.0) while the dollar is a real non-zero PnL — the adapter could not
        # derive pct at source (avg_entry/qty missing) and no price back-derive
        # ran above, so the percent would otherwise lie as a flat 0.0 on a real
        # win/loss. Re-derive it from the authoritative dollar / notional exactly
        # like a sign flip, so the percent agrees with the dollar.
        _t2_8_sign_flip = (
            pnl_pct != 0 and pnl_usd != 0 and ((pnl_pct > 0) != (pnl_usd > 0))
        )
        _t2_8_mode_a = (pnl_pct == 0 and pnl_usd != 0)
        _t2_8_reason = "mode_a_zero_pct" if _t2_8_mode_a else "sign_flip"
        if (_t2_8_sign_flip or _t2_8_mode_a) and (
            price_source != "local_fallback_stale"
        ):
            # Phantom-loss fix Commit 3 precondition: the staleness gate above
            # runs FIRST and demotes any stale exchange_authoritative row to a
            # consistent-sign local value tagged local_fallback_stale, so by
            # the time this block evaluates a poisoned row no longer exists.
            # The guard documents that and prevents re-processing a demoted
            # value (whose signs already agree).
            # Sign mismatch — pnl_usd wins. Back-derive pnl_pct.
            _size = getattr(state, "size", 0) if state else 0
            if _size > 0 and entry_price > 0:
                _t2_8_notional_used = abs(_size * entry_price)
            else:
                info = self._trade_info.get(symbol, {})
                amount_usd = info.get("amount_usd", 0)
                leverage = info.get("leverage", 1)
                if amount_usd > 0:
                    _t2_8_notional_used = amount_usd * leverage
            if _t2_8_notional_used > 0:
                _new_pnl_pct = pnl_usd / _t2_8_notional_used * 100
                log.warning(
                    f"PNL_SIGN_MISMATCH | sym={symbol} "
                    f"pnl_pct={pnl_pct:+.4f}% pnl_usd={pnl_usd:+.4f} "
                    f"new_pnl_pct={_new_pnl_pct:+.4f}% "
                    f"notional={_t2_8_notional_used:.2f} "
                    f"resolution=use_pnl_usd reason={_t2_8_reason} | {ctx()}"
                )
                pnl_pct = _new_pnl_pct
                was_win = pnl_usd > 0
                _t2_8_resolved = True
            else:
                # No notional available — preserve both values, flag the
                # gap. was_win still uses pnl_usd as the authoritative
                # source for downstream consumers.
                log.warning(
                    f"PNL_SIGN_MISMATCH | sym={symbol} "
                    f"pnl_pct={pnl_pct:+.4f}% pnl_usd={pnl_usd:+.4f} "
                    f"resolution=use_pnl_usd_for_win_flag_only "
                    f"reason=no_notional | {ctx()}"
                )
                was_win = pnl_usd > 0
                _t2_8_resolved = True

        log.info(
            f"PNL_SOURCE_RESOLVED | sym={symbol} "
            f"pnl_pct={pnl_pct:+.4f}% pnl_usd={pnl_usd:+.4f} "
            f"was_win={'Y' if was_win else 'N'} "
            f"sign_mismatch={'Y' if _t2_8_resolved else 'N'} "
            f"pnl_pct_pre_fee={_pnl_pct_pre_fee:+.4f}% "
            # PF/LC Top-15 Problem 1.3 — report the REAL provenance the
            # caller resolved (exchange_authoritative = net closedPnl;
            # local_fallback = gross back-derive) instead of the hardcoded
            # "pnl_usd_authoritative" claim, which read authoritative even
            # when the value was a gross fallback. caller_supplied = a
            # close path that did not tag provenance.
            f"source={price_source or 'caller_supplied'} | {ctx()}"
        )

        # Issue 2.9 (2026-06-07): fee-drag MEASUREMENT — observability only, no
        # lever (an entry-time edge-vs-fee filter would be a forbidden new
        # feature). Against the now-truthful net PnL (Phase 1), surface the
        # gross-vs-net gap (the fee/funding component the resolution applied) and
        # flag a fee-dominated SCRATCH close (|net| within a round-trip taker-fee
        # estimate), so the operator can quantify scratch-trade fee drag before
        # deciding whether to pull the only existing lever (the minimum hold
        # duration). Pure observability — no booked figure or decision changes.
        _fee_drag_notional = _t2_8_notional_used
        if _fee_drag_notional <= 0:
            _fd_sz = getattr(state, "size", 0) if state else 0
            if _fd_sz and entry_price > 0:
                _fee_drag_notional = abs(_fd_sz * entry_price)
        # Bybit linear taker ~0.055%; this round-trip estimate is for the SCRATCH
        # flag only and does not affect any booked figure or decision.
        _rt_taker_fee_est = _fee_drag_notional * _BYBIT_TAKER_FEE_PER_SIDE * 2.0
        _gross_minus_net = (
            (_pnl_pct_pre_fee - pnl_pct) / 100.0 * _fee_drag_notional
            if _fee_drag_notional > 0 else 0.0
        )
        log.info(
            f"FEE_DRAG_OBS | sym={symbol} net_usd={pnl_usd:+.4f} "
            f"notional={_fee_drag_notional:.2f} "
            f"rt_taker_fee_est={_rt_taker_fee_est:.4f} "
            f"gross_minus_net={_gross_minus_net:+.4f} "
            f"scratch={'Y' if (_rt_taker_fee_est > 0 and abs(pnl_usd) <= _rt_taker_fee_est) else 'N'} "
            # F2 (fee-scratch churn, 2026-06-09) — sub-two-minute-by-reason tags so
            # the fee-scratch cost can be pivoted post-restart against truthful PnL
            # (the proven scratch population closes within ~2 min via a pulled-up
            # breakeven stop). Additive observability only; no decision change.
            f"sub2min={'Y' if hold_seconds <= 120 else 'N'} hold_s={hold_seconds:.0f} "
            f"by={closed_by} "
            f"src={price_source or 'caller_supplied'} | fee-drag measurement "
            f"(observability only; no lever) | {ctx()}"
        )

        _trade_id = (
            getattr(state, "brain_decision_id", "") if state else ""
        ) or f"t-{symbol}-{int(time.time())}"

        # T2-3 (2026-05-12) — read exchange_mode from the transformer at
        # close time. The transformer holds the authoritative current_mode
        # (bybit_demo / shadow / paper / bybit live). Pre-fix, TIAS rows
        # silently defaulted to 'shadow' (schema v32 column default)
        # because the data flow never threaded this field through —
        # bybit_demo trades were mislabeled as shadow, contaminating the
        # brain's learning loop. See dev_notes/three_issues for the
        # original audit. Empty-string fallback when transformer is not
        # yet wired (boot race) — TIAS collector logs a TIAS_MODE_RESOLVED
        # WARNING in that case so the gap is visible.
        #
        # Issue I2 (F-17, 2026-05-14) — prefer the mode captured on
        # TradeState at register_trade time. The positions-cleanup
        # callback was silently skipping closes when transformer
        # briefly returned non-bybit_demo at close-dispatch time. The
        # trade's REGISTERED mode is the authoritative answer; fall
        # back to current_mode only when TradeState has none (legacy
        # pre-I2 trades).
        _state_mode = (
            getattr(state, "exchange_mode", "") if state else ""
        )
        _t2_3_exchange_mode = _state_mode or self._current_mode()

        record = {
            "symbol": symbol,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "was_win": was_win,
            "closed_by": closed_by,
            "hold_seconds": hold_seconds,
            "strategy_name": state.strategy_name if state else "",
            "strategy_category": state.strategy_category if state else "",
            "source": state.source if state else "",
            # T2-3 (2026-05-12): authoritative exchange mode for this
            # trade — populates the trade_intelligence.exchange_mode
            # column (was silently defaulting to 'shadow' for every row).
            "exchange_mode": _t2_3_exchange_mode,
            # CRITICAL-2 fix — pair opened_at with closed_at so downstream
            # consumers (trade_log, dashboards, /history filters) can sort
            # and filter by entry time. state.opened_at_dt is captured at
            # register_trade (line 281) precisely so the close path can
            # serialize ISO without re-converting from epoch. Empty-string
            # fallback when state is None matches the existing pattern
            # used by sibling fields above (state was already popped by the
            # double-close guard at lines 666-675; the record still gets
            # built for telemetry).
            "opened_at": state.opened_at_dt.isoformat() if state else "",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "entry_price": entry_price,
            # CRITICAL-3 fix — forward state.size so the new
            # _trade_history_close_callback in workers/manager.py can
            # populate trade_history.qty for both bybit_demo system-
            # initiated closes (where the adapter previously self-
            # persisted) AND WS-only closes (SL/TP hit) which were
            # never persisted before.
            "size": state.size if state else 0.0,
            "close_price": round(close_price, 6),
            "direction": _side,
            "trade_id": _trade_id,
            # Definitive-fix Phase 8 (2026-04-28) — exchange order_id
            # forwarded into the close record so callbacks (notably
            # thesis_manager.close_thesis) can filter by it and avoid
            # closing a fresh same-symbol thesis under a stale entry.
            "order_id": (state.order_id if state else "") or "",
            # Bug 2 fix: preserve provenance of the exit price so TIAS /
            # Data Lake can distinguish Shadow-authoritative closes from
            # ticker/last-tick fallbacks when auditing PnL accuracy.
            "price_source": price_source or "derived",
            # Phase 3: TIAS entry-time context forwarded from TradeState
            "claude_directive": state.claude_directive if state else "",
            "claude_plan_view": state.claude_plan_view if state else "",
            "signal_score": state.signal_score if state else None,
            "ensemble_score": state.ensemble_score if state else "",
            # Layer 2 Defect 6 — numeric vote counts forwarded to TIAS
            # collector via the close-record dict (Phase 3 override path).
            "supporting_count": state.supporting_count if state else None,
            "opposing_count": state.opposing_count if state else None,
            # Layer 2 Defect 1 — setup_id join key forwarded to trade_intelligence.
            "setup_id": state.setup_id if state else "",
            "entry_regime": state.entry_regime if state else "",
            "entry_rsi": state.entry_rsi if state else None,
            "entry_macd_hist": state.entry_macd_hist if state else None,
            "entry_atr_pct": state.entry_atr_pct if state else None,
            # APEX optimization data (for TIAS feedback loop)
            "apex_optimized": state.apex_optimized if state else False,
            "apex_was_flipped": state.apex_was_flipped if state else False,
            "apex_confidence": state.apex_confidence if state else 0.0,
            "apex_tp_mode": state.apex_tp_mode if state else "",
            "apex_reasoning": state.apex_reasoning if state else "",
            "apex_original_direction": state.apex_original_direction if state else "",
            "apex_original_sl": state.apex_original_sl if state else 0.0,
            "apex_original_tp": state.apex_original_tp if state else 0.0,
            "apex_original_size": state.apex_original_size if state else 0.0,
            "apex_model": state.apex_model if state else "",
            "apex_response_ms": state.apex_response_ms if state else 0,
            "apex_cost_usd": state.apex_cost_usd if state else 0.0,
            "gate_adjustments": state.gate_adjustments if state else "",
        }

        self._closed_trades.append(record)
        if len(self._closed_trades) > 100:
            self._closed_trades = self._closed_trades[-100:]

        log.info(
            f"COORD_CLOSE_START | sym={symbol} pnl={pnl_pct:+.4f}% pnl$={pnl_usd:+.4f} "
            f"win={'Y' if was_win else 'N'} by={closed_by} held={hold_seconds:.0f}s "
            f"ent={entry_price} ext={round(close_price, 6)} cbs={len(self._callbacks_on_close)} | {ctx()}"
        )

        self._last_brain_context.pop(symbol, None)
        self._trade_plans.pop(symbol, None)
        self._trade_info.pop(symbol, None)

        for i, callback in enumerate(self._callbacks_on_close):
            try:
                callback(record)
                cb_name = getattr(callback, "__name__", str(callback)[:50])
                log.debug(f"COORD_CB_OK | #{i+1} {cb_name} sym={symbol} | {ctx()}")
            except Exception as e:
                cb_name = getattr(callback, "__name__", str(callback)[:50])
                log.error(f"COORD_CB_FAIL | #{i+1} {cb_name} sym={symbol} err='{str(e)[:500]}' | {ctx()}")
                log.error("Close callback failed: {err}", err=str(e))

        # Issue 3 (5-min reentry cooldown, 2026-05-18). Per-(symbol,
        # direction) cooldown set on EVERY close regardless of outcome
        # or reason. Monotonic clock per Risk 7 mitigation (clock-skew
        # defense). Direction is the popped TradeState side. Replaces
        # the legacy per-symbol _symbol_cooldowns (180/600/900 win/
        # loss/hard-stop branches) and the T2-1 loss-direction tracker.
        _new_dir = str(_side or "").strip()
        # F9 (2026-06-09): in loss-only mode set the cooldown ONLY on a real loss
        # (booked net dollar < 0) — wins/scratches get no cooldown and stay
        # immediately re-tradeable / re-selectable. Keyed on pnl_usd (the final
        # booked net), so the F5 phantom can never let a real loss skip the
        # cooldown. When the flag is off the cooldown is set on every close (the
        # prior Issue-3 behaviour, byte-identical).
        _set_cooldown = bool(_new_dir) and (
            (not self._loss_cooldown_enabled) or (pnl_usd < 0)
        )
        if _set_cooldown:
            _expiry = time.monotonic() + self._reentry_cooldown_seconds
            self._reentry_cooldown[(symbol, _new_dir)] = _expiry
            log.info(
                f"REENTRY_COOLDOWN_5MIN_SET | sym={symbol} dir={_new_dir} "
                f"cooldown_sec={self._reentry_cooldown_seconds} "
                f"closed_by={closed_by} was_win={was_win} pnl_usd={pnl_usd:+.4f} "
                f"mode={'loss_only' if self._loss_cooldown_enabled else 'every_close'} "
                f"| {ctx()}"
            )
        elif _new_dir and self._loss_cooldown_enabled:
            log.info(
                f"REENTRY_COOLDOWN_SKIP_WIN | sym={symbol} dir={_new_dir} "
                f"pnl_usd={pnl_usd:+.4f} closed_by={closed_by} "
                f"| F9 loss-only: no cooldown on a win/scratch | {ctx()}"
            )
        log.info(
            f"COORD_CLOSE_END | sym={symbol} "
            f"cooldown_sec={self._reentry_cooldown_seconds} "
            f"by={closed_by} cbs_fired={len(self._callbacks_on_close)} | {ctx()}"
        )

    # ══════════════════════════════════════════════
    # PARTIAL-CLOSE API (Issue 4 fix, 2026-05-11)
    # ══════════════════════════════════════════════

    def mark_partial_close_pending(
        self, symbol: str, qty: float, *, by: str = "mode4_partial",
    ) -> None:
        """Stamp a partial-close intent before the reduceOnly order goes out.

        Called by ``BybitDemoOrderService.reduce_position`` BEFORE the
        order POST. The WS subscriber's close-dispatch path calls
        :meth:`pop_partial_close_pending` after the resulting execution
        event arrives; if a pending entry exists, the fill is routed to
        :meth:`on_partial_close` instead of the full :meth:`on_trade_closed`.

        Args:
            symbol: Symbol being partially closed.
            qty: Quantity intended to close in the reduceOnly order.
            by: Origin tag carried into the partial-close record's
                ``closed_by`` field (e.g. ``mode4_partial``).
        """
        self._partial_close_pending[symbol] = {
            "qty": float(qty),
            "by": str(by),
            "ts": time.time(),
        }
        log.info(
            f"COORD_PARTIAL_PENDING | sym={symbol} qty={qty} by={by} | {ctx()}"
        )

    def pop_partial_close_pending(self, symbol: str) -> dict | None:
        """Return and clear the partial-close pending entry for ``symbol``.

        Returns ``None`` if no entry is pending. Consumers should treat
        a non-None return as the signal that the next close event for
        this symbol is the partial signaled by reduce_position.
        """
        return self._partial_close_pending.pop(symbol, None)

    def register_partial_close_callback(
        self, callback: Callable[[dict], Any],
    ) -> None:
        """Register a callback to fire on partial close (subset of full).

        Partial callbacks receive a record dict identical in shape to
        the full-close callback record, with two additions:
        ``is_partial=True`` and ``partial_index`` (1-based counter per
        trade). The record's ``size`` is the closed quantity (not the
        full trade qty); ``pnl_usd`` is computed on the closed-portion
        notional. Use this for trade_history / trade_log persistence of
        per-partial outcomes. Lifecycle-end consumers (thesis_close,
        fund release, sniper buffer cleanup) should stay registered via
        :meth:`register_close_callback` so they only fire on the final
        close after all partials.
        """
        self._callbacks_on_partial_close.append(callback)

    def on_partial_close(
        self,
        symbol: str,
        closed_qty: float,
        exec_price: float,
        *,
        closed_by: str = "mode4_partial",
        price_source: str = "bybit_ws_authoritative",
    ) -> None:
        """Record a partial close without popping the trade state.

        Builds a record dict that mirrors :meth:`on_trade_closed`'s shape
        (so the same writer callbacks work), with ``size=closed_qty`` and
        PnL computed on the closed portion. State.size is decremented by
        ``closed_qty`` so the eventual final close fires against the
        residual. ``state.partial_index`` is incremented and stamped onto
        the record's ``order_id`` field so trade_history / trade_log
        produce a row with a unique ``trade_id`` per partial (no
        INSERT-OR-REPLACE collision against the eventual final row).

        Args:
            symbol: Symbol of the partially-closed position.
            closed_qty: Quantity that actually closed in this fill.
            exec_price: Bybit's authoritative execution price for the
                partial fill (used for PnL back-derive).
            closed_by: Origin tag (defaults to ``mode4_partial``).
            price_source: Provenance label preserved into the record.
        """
        state = self._trades.get(symbol)
        if state is None:
            log.warning(
                f"COORD_PARTIAL_NO_STATE | sym={symbol} closed_qty={closed_qty} "
                f"by={closed_by} | partial close ignored — no trade state | {ctx()}"
            )
            return
        if closed_qty <= 0:
            log.warning(
                f"COORD_PARTIAL_INVALID_QTY | sym={symbol} qty={closed_qty} "
                f"by={closed_by} | {ctx()}"
            )
            return
        if state.entry_price <= 0 or exec_price <= 0:
            log.warning(
                f"COORD_PARTIAL_INVALID_PRICE | sym={symbol} "
                f"ent={state.entry_price} ext={exec_price} | {ctx()}"
            )
            return

        # Compute PnL for the closed portion only. Direction sign matches
        # on_trade_closed:722-724 so partial / final records use the same
        # convention.
        pnl_pct = ((exec_price - state.entry_price) / state.entry_price) * 100
        if state.side in ("Sell", "Short"):
            pnl_pct = -pnl_pct
        pnl_usd = (pnl_pct / 100) * abs(closed_qty * state.entry_price)
        was_win = pnl_pct > 0

        state.partial_index = (state.partial_index or 0) + 1
        partial_idx = state.partial_index
        hold_seconds = time.time() - state.opened_at

        # Build a unique order_id for trade_id derivation in the
        # _trade_history_close_callback. The base order_id stays unique
        # per trade; suffixing -partial-{idx} ensures the partial row
        # does not collide with the eventual final row.
        base_oid = state.order_id or ""
        partial_oid = f"{base_oid}-partial-{partial_idx}" if base_oid else ""

        _trade_id_base = state.brain_decision_id or f"t-{symbol}-{int(time.time())}"
        _trade_id = f"{_trade_id_base}-partial-{partial_idx}"

        # T2-3 (2026-05-12) — partial-close path also gets the
        # authoritative exchange_mode for parity with on_trade_closed.
        # Future TIAS-on-partial consumer will pick this up automatically
        # via the same _extract_group_a path.
        #
        # Issue I2 (F-17, 2026-05-14) — prefer TradeState mode for
        # symmetry with on_trade_closed's resolution order.
        _state_mode = getattr(state, "exchange_mode", "") or ""
        _t2_3_exchange_mode = _state_mode or self._current_mode()

        record = {
            "symbol": symbol,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "was_win": was_win,
            "closed_by": closed_by,
            "hold_seconds": hold_seconds,
            "strategy_name": state.strategy_name,
            "strategy_category": state.strategy_category,
            "source": state.source,
            "exchange_mode": _t2_3_exchange_mode,
            "opened_at": state.opened_at_dt.isoformat(),
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "entry_price": state.entry_price,
            "size": float(closed_qty),
            "close_price": round(float(exec_price), 6),
            "direction": state.side,
            "trade_id": _trade_id,
            "order_id": partial_oid,
            "price_source": price_source,
            # TIAS entry context preserved so a future TIAS-on-partial
            # consumer (not in scope for first ship) has the same fields
            # as a full close.
            "claude_directive": state.claude_directive,
            "claude_plan_view": state.claude_plan_view,
            "signal_score": state.signal_score,
            "ensemble_score": state.ensemble_score,
            # Layer 2 Defect 6 — vote counts preserved across partials so
            # every partial-close row in trade_log carries the same numeric
            # supporting/opposing context as the eventual final close.
            "supporting_count": state.supporting_count,
            "opposing_count": state.opposing_count,
            # Layer 2 Defect 1 — setup_id preserved across partials.
            "setup_id": state.setup_id,
            "entry_regime": state.entry_regime,
            "entry_rsi": state.entry_rsi,
            "entry_macd_hist": state.entry_macd_hist,
            "entry_atr_pct": state.entry_atr_pct,
            # APEX context preserved (constant across partials)
            "apex_optimized": state.apex_optimized,
            "apex_was_flipped": state.apex_was_flipped,
            "apex_confidence": state.apex_confidence,
            "apex_tp_mode": state.apex_tp_mode,
            "apex_reasoning": state.apex_reasoning,
            "apex_original_direction": state.apex_original_direction,
            "apex_original_sl": state.apex_original_sl,
            "apex_original_tp": state.apex_original_tp,
            "apex_original_size": state.apex_original_size,
            "apex_model": state.apex_model,
            "apex_response_ms": state.apex_response_ms,
            "apex_cost_usd": state.apex_cost_usd,
            "gate_adjustments": state.gate_adjustments,
            # Partial-close markers
            "is_partial": True,
            "partial_index": partial_idx,
        }

        # State accounting BEFORE callback fan-out so any callback that
        # reads state.size sees the residual qty consistent with this
        # partial's record.
        prior_size = state.size
        state.size = max(0.0, state.size - closed_qty)

        log.warning(
            f"COORD_PARTIAL_CLOSE | sym={symbol} closed_qty={closed_qty} "
            f"prior_size={prior_size:.4f} residual={state.size:.4f} "
            f"pnl={pnl_pct:+.4f}% pnl$={pnl_usd:+.4f} ent={state.entry_price} "
            f"ext={exec_price} idx={partial_idx} by={closed_by} "
            f"cbs={len(self._callbacks_on_partial_close)} | {ctx()}"
        )

        for i, callback in enumerate(self._callbacks_on_partial_close):
            try:
                callback(record)
                cb_name = getattr(callback, "__name__", str(callback)[:50])
                log.debug(
                    f"COORD_PARTIAL_CB_OK | #{i+1} {cb_name} sym={symbol} | {ctx()}"
                )
            except Exception as e:
                cb_name = getattr(callback, "__name__", str(callback)[:50])
                log.error(
                    f"COORD_PARTIAL_CB_FAIL | #{i+1} {cb_name} sym={symbol} "
                    f"err='{str(e)[:500]}' | {ctx()}"
                )

    # ══════════════════════════════════════════════════════════════════
    # Issue 3 (2026-05-18) — 5-min per-(symbol, direction) reentry cooldown
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        """Canonicalise direction to ``"Buy"``/``"Sell"`` for cooldown key.

        Accepts ``"Buy"``/``"Sell"``/``"long"``/``"short"`` in any case.
        Returns the canonical capitalised form, or an empty string when
        the input does not map. Empty input cannot key into the
        cooldown dict so callers see "not blocked" — matches the
        legacy fallthrough behaviour for malformed directions.
        """
        normalised = str(direction or "").strip().lower()
        if normalised in ("buy", "long"):
            return "Buy"
        if normalised in ("sell", "short"):
            return "Sell"
        return ""

    def is_reentry_blocked(
        self, symbol: str, direction: str,
    ) -> tuple[bool, int]:
        """Check whether (symbol, direction) is within the 5-min reentry cooldown.

        Issue 3 (2026-05-18). After ANY close (win or loss / any
        reason) the gate blocks new entries on the same
        (symbol, direction) for ``self._reentry_cooldown_seconds`` (300
        by default). Opposite-direction entry on the same symbol is
        allowed (per the operator's per-direction design).

        Performs LAZY CLEANUP on read — expired entries are popped
        from the dict and a single ``REENTRY_COOLDOWN_5MIN_CLEARED``
        event is emitted. This keeps the dict bounded under steady
        state without a separate cleanup tick. The periodic
        ``clear_expired_reentry_cooldowns`` sweep handles entries
        that nobody queries again after their window expires.

        Args:
            symbol: Trading symbol (e.g. ``"AVAXUSDT"``).
            direction: Proposed entry direction. ``"Buy"``/``"Sell"``;
                ``"long"``/``"short"`` accepted as legacy aliases.

        Returns:
            ``(blocked, remaining_seconds)`` — ``blocked`` is True
            when a fresh (within-window) cooldown entry exists for
            this (symbol, direction); ``remaining_seconds`` is the
            integer floor of seconds until expiry (0 when not
            blocked).
        """
        canonical = self._normalize_direction(direction)
        if not canonical:
            return (False, 0)
        key = (symbol, canonical)
        expiry = self._reentry_cooldown.get(key)
        if expiry is None:
            return (False, 0)
        now = time.monotonic()
        if now >= expiry:
            # Lazy cleanup — pop and emit cleared event exactly once.
            del self._reentry_cooldown[key]
            log.info(
                f"REENTRY_COOLDOWN_5MIN_CLEARED | sym={symbol} "
                f"dir={canonical} trigger=lazy_on_read | {ctx()}"
            )
            return (False, 0)
        remaining = max(0, int(expiry - now))
        return (True, remaining)

    def is_symbol_in_any_cooldown(self, symbol: str) -> bool:
        """Return True when ANY direction on the symbol is in cooldown.

        Issue 3 (2026-05-18). Symbol-level convenience for callers that
        do not have a proposed direction (e.g. the position watchdog's
        Time-Decay guard and its vanished-symbol dedup, where the
        intent is "this symbol just closed, skip"). Prefer
        ``is_reentry_blocked`` when direction is available (apex gate,
        rule engine).

        Lazy-cleans expired entries it encounters on the scan and emits
        one ``REENTRY_COOLDOWN_5MIN_CLEARED`` event per popped entry.
        Returns True as soon as a still-active entry is found (no full
        sweep needed).

        Args:
            symbol: Trading symbol to check.

        Returns:
            True if at least one (symbol, *) entry has a fresh expiry;
            False if no entry exists or every entry has expired.
        """
        if not self._reentry_cooldown:
            return False
        now = time.monotonic()
        expired_keys: list[tuple[str, str]] = []
        active = False
        for key, expiry in list(self._reentry_cooldown.items()):
            if key[0] != symbol:
                continue
            if now >= expiry:
                expired_keys.append(key)
                continue
            active = True
            break
        for key in expired_keys:
            self._reentry_cooldown.pop(key, None)
            _sym_e, _dir_e = key
            log.info(
                f"REENTRY_COOLDOWN_5MIN_CLEARED | sym={_sym_e} "
                f"dir={_dir_e} trigger=symbol_scan | {ctx()}"
            )
        return active

    def clear_expired_reentry_cooldowns(self) -> int:
        """Sweep all expired entries from the reentry cooldown dict.

        Issue 3 (2026-05-18). Called once per gate check (cheap O(N)
        sweep over a small dict) to bound memory growth and ensure
        ``REENTRY_COOLDOWN_5MIN_CLEARED`` fires for entries that
        nobody queries via ``is_reentry_blocked`` after expiry.

        Returns:
            Number of entries cleared in this sweep. 0 when the dict
            is empty or all entries are still active.
        """
        if not self._reentry_cooldown:
            return 0
        now = time.monotonic()
        expired = [
            key for key, expiry in self._reentry_cooldown.items()
            if now >= expiry
        ]
        for key in expired:
            del self._reentry_cooldown[key]
            symbol, direction = key
            log.info(
                f"REENTRY_COOLDOWN_5MIN_CLEARED | sym={symbol} "
                f"dir={direction} trigger=periodic_sweep | {ctx()}"
            )
        return len(expired)

    def get_active_reentry_cooldowns(self) -> list[tuple[str, str, int]]:
        """Snapshot active per-(symbol, direction) cooldowns.

        Issue 3 (2026-05-18). Brain-prompt consumer surface — used by
        ``src/brain/strategist.py`` to render per-direction cooldown
        lines so the brain does not propose blocked re-entries.

        Returns:
            List of ``(symbol, direction, remaining_seconds)`` for
            every entry whose expiry has not passed. Expired entries
            are popped (same as ``is_reentry_blocked``) and emit one
            ``REENTRY_COOLDOWN_5MIN_CLEARED`` event each. Order is
            insertion order (Python dict semantics) — caller may
            sort if a stable display order is needed.
        """
        if not self._reentry_cooldown:
            return []
        now = time.monotonic()
        active: list[tuple[str, str, int]] = []
        expired_keys: list[tuple[str, str]] = []
        for key, expiry in list(self._reentry_cooldown.items()):
            if now >= expiry:
                expired_keys.append(key)
                continue
            symbol, direction = key
            active.append((symbol, direction, max(0, int(expiry - now))))
        for key in expired_keys:
            self._reentry_cooldown.pop(key, None)
            sym_e, dir_e = key
            log.info(
                f"REENTRY_COOLDOWN_5MIN_CLEARED | sym={sym_e} "
                f"dir={dir_e} trigger=snapshot_read | {ctx()}"
            )
        return active


    def register_close_callback(self, callback) -> None:
        """Register a function to be called when any trade closes."""
        self._callbacks_on_close.append(callback)

    def register_reconcile_callback(self, callback) -> None:
        """Register a sink to re-fire when a provisional close is reconciled.

        Only idempotent sinks (upsert/update by a stable key) belong here — the
        PnL reconciler calls ``fire_reconcile`` with a corrected record carrying
        the SAME trade_id/order_id, so each sink updates its existing row to the
        exchange-authoritative net. Non-idempotent consumers (enforcer streak,
        pnl-manager running total, re-entry cooldown) must NOT register here.
        """
        self._reconcile_callbacks.append(callback)

    def register_correction_callback(self, callback) -> None:
        """F5 part 3 (2026-06-09): register a STATEFUL consumer to be notified ONLY
        when a reconcile FLIPS a provisionally-booked outcome (win<->loss).

        Unlike the reconcile channel (idempotent row-upserts), this channel is for
        the non-idempotent stateful metrics that booked at close time — the
        performance-enforcer win/loss streak, the daily realized PnL, the learning
        loop. The callback receives the corrected record carrying both
        ``prior_was_win``/``prior_pnl_usd`` and the authoritative ``was_win``/
        ``pnl_usd`` so it can REVERSE the wrong booking and apply the right one. It
        fires only on a genuine flip, so a normal fee-only correction never
        double-counts here.
        """
        self._correction_callbacks.append(callback)

    def fire_reconcile(self, record: dict) -> None:
        """Fire the reconcile channel with a corrected close record.

        Called by the PnL reconciler once the exchange-authoritative figure for a
        previously-provisional close is available. Each sink is invoked exactly
        like a normal close callback (same record shape) but only the idempotent
        sinks are on this channel, so the correction updates rows in place
        without double-counting. Per-callback try/except: one failing sink never
        blocks the others.
        """
        for cb in list(self._reconcile_callbacks):
            try:
                cb(record)
            except Exception as e:
                log.warning(
                    f"RECONCILE_CB_FAIL | sym={record.get('symbol')} "
                    f"trade_id={record.get('trade_id')} err='{str(e)[:150]}' | {ctx()}"
                )

        # F5 part 3 (2026-06-09 phantom-close follow-up) — corrective backstop.
        # The idempotent reconcile sinks above fixed the trade_log row, but the
        # STATEFUL consumers (enforcer win/loss streak, daily realized PnL, learning
        # loop, re-entry cooldown) booked at close time and are deliberately NOT on
        # the reconcile channel. When the authoritative reconcile FLIPS the outcome
        # (a phantom win booked first, now a real loss — or the reverse), those
        # stateful metrics keep the WRONG result. Detect a genuine flip here and (a)
        # emit a loud PNL_PHANTOM_CORRECTION, (b) self-correct the coordinator-owned
        # re-entry cooldown (the F9 link: a phantom win that skipped the loss
        # cooldown must now arm it), and (c) fire the correction channel so external
        # consumers (the enforcer) reverse the prior result and apply the corrected
        # one. Guarded on a present prior outcome + an actual flip, so a normal
        # (non-flipping) fee correction never touches these metrics.
        _prior_win = record.get("prior_was_win")
        _new_win = record.get("was_win")
        if _prior_win is not None and _new_win is not None and bool(_prior_win) != bool(_new_win):
            _sym = record.get("symbol")
            _dir = record.get("direction") or ""
            log.warning(
                f"PNL_PHANTOM_CORRECTION | sym={_sym} dir={_dir} "
                f"prior_usd={record.get('prior_pnl_usd')} corrected_usd={record.get('pnl_usd')} "
                f"prior_win={bool(_prior_win)} corrected_win={bool(_new_win)} "
                f"trade_id={record.get('trade_id')} | a provisionally-booked outcome "
                f"flipped at reconcile; correcting the stateful metrics | {ctx()}"
            )
            # (b) cooldown self-correction: a flip to a LOSS under loss-only mode must
            # arm the re-entry cooldown the phantom win skipped (per-direction key).
            try:
                if (
                    not bool(_new_win)
                    and self._loss_cooldown_enabled
                    and _sym and _dir
                ):
                    self._reentry_cooldown[(_sym, _dir)] = (
                        time.time() + self._reentry_cooldown_seconds
                    )
                    log.info(
                        f"REENTRY_COOLDOWN_CORRECTED | sym={_sym} dir={_dir} "
                        f"cooldown_sec={self._reentry_cooldown_seconds} "
                        f"reason=reconcile_flip_to_loss | {ctx()}"
                    )
            except Exception as _ce:
                log.warning(
                    f"REENTRY_COOLDOWN_CORRECT_FAIL | sym={_sym} err='{str(_ce)[:120]}' | {ctx()}"
                )
            # (c) external stateful consumers (enforcer streak/daily/learning).
            for cb in list(self._correction_callbacks):
                try:
                    cb(record)
                except Exception as e:
                    log.warning(
                        f"CORRECTION_CB_FAIL | sym={_sym} "
                        f"trade_id={record.get('trade_id')} err='{str(e)[:150]}' | {ctx()}"
                    )

    async def reresolve_close_pnl(
        self, symbol: str, *, fallback_pnl_usd: float, fallback_pnl_pct: float,
        qty: float | None = None, order_id: str | None = None,
        min_row_ts_ms: float | None = None, entry_price: float | None = None,
    ) -> tuple[float, float, str, float | None]:
        """Re-resolve a closed trade's authoritative PnL for the PnL reconciler.

        Thin wrapper over ``resolve_authoritative_pnl`` that supplies the
        attached resolver (position_service or transformer) so the reconciler
        does not reach into private attrs. The trade state has already been
        popped by ``on_trade_closed``, so the resolve-level qty gate (which
        reads ``self._trades``) is inert — identity matching is driven purely by
        the qty/order_id hints passed here. Returns the same 4-tuple as
        ``resolve_authoritative_pnl``; ``local_fallback`` when no resolver or no
        exchange row yet (caller keeps retrying within its budget).
        """
        resolver = self._position_service or self._transformer
        if resolver is None:
            return (fallback_pnl_usd, fallback_pnl_pct, "local_fallback", None)
        _hint: dict[str, Any] = {}
        if qty is not None:
            _hint["qty"] = qty
        if order_id:
            _hint["order_id"] = order_id
        # F5-c (2026-06-08): freshness floor for the qty-only close-row match.
        # The reconciler matches a closed-pnl row by qty only — the trade state
        # is popped and only the OPENING order_id is known, which never matches a
        # closed-pnl row (those are keyed by the CLOSING order). A same-qty row
        # from a PRIOR same-symbol trade (a re-entry whose qty is within the 1%
        # tolerance) was being accepted because no freshness floor was supplied:
        # the proven LDO clobber booked Trade A's 23:14 row (+$0.07) onto Trade B
        # (true +$3.26). Pass this trade's OPEN time as the floor — a closed-pnl
        # row for THIS trade can never pre-date its open — so the adapter rejects
        # prior-trade rows and the reconciler RETRIES until this trade's own row
        # indexes, rather than booking a stale wrong-trade value. (Reuses the
        # adapter's existing ws_close_ts_ms freshness gate; the 5s skew covers
        # detection lag.)
        if min_row_ts_ms is not None and min_row_ts_ms > 0:
            _hint["ws_close_ts_ms"] = min_row_ts_ms
        # F5-b (2026-06-08): the trade's entry price disambiguates the qty-only
        # close-row match between same-qty re-entries (which have different
        # entries), covering a LATER re-entry's row the freshness floor cannot
        # reject. bybit_demo-only (the reconciler is mode-gated), so Shadow is
        # untouched.
        if entry_price is not None and entry_price > 0:
            _hint["entry_price"] = entry_price
        return await self.resolve_authoritative_pnl(
            symbol=symbol,
            position_service=resolver,
            fallback_pnl_usd=fallback_pnl_usd,
            fallback_pnl_pct=fallback_pnl_pct,
            **_hint,
        )

    # ══════════════════════════════════════════════
    # PEAK PnL TRACKING (trailing stop)
    # ══════════════════════════════════════════════

    def update_peak_pnl(self, symbol: str, current_pnl_pct: float) -> float:
        state = self._trades.get(symbol)
        if state and current_pnl_pct > state.peak_pnl_pct:
            state.peak_pnl_pct = current_pnl_pct
        return state.peak_pnl_pct if state else 0.0

    # ══════════════════════════════════════════════
    # POSITION HEALTH SCORE
    # ══════════════════════════════════════════════

    @staticmethod
    def get_position_health(pnl_pct: float, sl_proximity_pct: float) -> int:
        """Calculate a 0-100 health score for a position."""
        score = 50

        if pnl_pct > 3.0:
            score += 30
        elif pnl_pct > 1.0:
            score += 20
        elif pnl_pct > 0:
            score += 10
        elif pnl_pct > -1.0:
            score -= 5
        elif pnl_pct > -2.0:
            score -= 15
        elif pnl_pct > -3.0:
            score -= 25
        else:
            score -= 30

        if sl_proximity_pct < 20:
            score += 20
        elif sl_proximity_pct < 40:
            score += 10
        elif sl_proximity_pct < 60:
            score -= 5
        elif sl_proximity_pct < 80:
            score -= 15
        else:
            score -= 20

        return max(0, min(100, score))

    # ══════════════════════════════════════════════
    # BRAIN CONTEXT DEDUP (Opt 3)
    # ══════════════════════════════════════════════

    def should_call_brain(self, symbol: str, current_context_hash: str) -> bool:
        """Prevent calling Claude when nothing has changed."""
        last_hash = self._last_brain_context.get(symbol, "")
        if current_context_hash == last_hash:
            return False
        self._last_brain_context[symbol] = current_context_hash
        return True

    # ══════════════════════════════════════════════
    # STATUS / DEBUG
    # ══════════════════════════════════════════════

    def get_status(self) -> dict:
        active = {}
        # Issue 3 of cascade-fix series (2026-05-10): snapshot the
        # items list. ``get_status`` is currently synchronous and has no
        # awaits inside, so no in-function yield to other tasks — but
        # callers like Telegram /status and the MCP get_status tool
        # invoke this method from async contexts where mutation by other
        # workers (TradeCoordinator.register_trade /
        # TradeCoordinator.on_trade_closed) is plausible if the call site
        # is later changed to add an internal await. Defensive snapshot
        # is cheap (≤ 20 keys typical) and matches the snapshot pattern
        # applied to the active hot-path iteration in profit_sniper.py.
        for symbol, state in list(self._trades.items()):
            elapsed = time.time() - state.opened_at
            immune = elapsed < state.immunity_seconds
            active[symbol] = {
                "age_seconds": round(elapsed),
                "age_minutes": round(elapsed / 60, 1),
                "immune": immune,
                "remaining_immunity": max(0, round(state.immunity_seconds - elapsed)),
                "source": state.source,
                "category": state.strategy_category,
                "strategy": state.strategy_name,
                "peak_pnl": state.peak_pnl_pct,
            }

        return {
            "active_trades": len(self._trades),
            "positions": active,
            "recent_closes": len(self._closed_trades),
            "last_close": self._closed_trades[-1] if self._closed_trades else None,
            "close_callbacks_registered": len(self._callbacks_on_close),
        }

    def cleanup_stale(self) -> None:
        """Remove tracking for positions older than 2 hours with no update."""
        cutoff = time.time() - 7200
        stale = [s for s, state in self._trades.items() if state.opened_at < cutoff]
        for s in stale:
            self._trades.pop(s, None)
            log.debug("Cleaned stale tracking for {sym}", sym=s)
