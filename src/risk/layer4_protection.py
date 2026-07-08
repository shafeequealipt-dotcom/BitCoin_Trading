"""Layer 4 Protection Service — shared close-time guardrails.

Layer 4 Realignment Phase 4 (2026-05-06).

Single source of truth for "is this position currently protected from
close?" Every Layer 4 close path (Profit Sniper, time-decay calculator,
strategic-action dispatcher, watchdog timer / sentinel / plan-timer)
consults this service through ``is_protected(...)`` before invoking
``position_service.close_position(...)``. The service returns a
``ProtectionResult`` whose ``protected`` flag tells the caller whether
to proceed and whose ``reason`` + ``evidence`` carry the cause for
structured logging.

The three protections orchestrated:

1. ``check_min_hold`` — refuses close on positions younger than
   ``settings.watchdog.strategic_action_min_hold_seconds`` (default
   300 s) UNLESS ``close_reason`` matches a substring in
   ``settings.watchdog.strategic_action_allowed_early_close_reasons``
   (genuine SL/TP/structure/regime/manual signals bypass the gate).
   Mirrors the existing watchdog ``_execute_strategic_actions``
   guardrail so all close paths converge on the same contract.

2. ``check_profit`` — refuses close on positions in profit (pnl >
   ``settings.layer4_sniper.profit_protection_threshold``) or in the
   normal-development loss window (pnl >
   ``settings.layer4_sniper.development_window_lower``). Aligned with
   the operator's aggressive-exploitation philosophy: a winning trade
   is not in trouble; a slightly-losing trade is still resolving.

3. ``check_structural`` — refuses close when no structural-
   invalidation evidence is present. Reuses the strict 3-signal
   disjunction (XRAY drop ≥ 40 %, setup-type drift, regime inversion
   ≥ 60 % conf) previously living on ``PositionWatchdog._compute_
   structural_invalidation``. The function is moved verbatim into
   this service so watchdog and sniper share one implementation.

The service is async-friendly (one of the helper methods is async to
match the signatures used by the worker layer), but the underlying
checks are synchronous — the async surface is for caller ergonomics,
not internal blocking I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.risk.time_decay_sl import TimeDecaySLCalculator, TimeDecayState

log = get_logger("layer4_protection")


@dataclass(frozen=True)
class ProtectionResult:
    """Outcome of a ``Layer4ProtectionService.is_protected`` call.

    Attributes:
        protected: True if at least one configured check refused the
            close. Caller should NOT proceed with close when True.
        reason: Compact human-readable reason string. Stable token
            grammar so log-parsing tools can split: "min_hold:age=Ns",
            "min_hold:reason_allowed=...", "profit_guard:pnl=...",
            "development_guard:pnl=...", "struct_intact:reason=...",
            "no_protection".
        evidence: Free-form dict carrying the numeric evidence behind
            the decision (age, pnl, threshold, struct_invalidation,
            invalidation_reason, etc.). Always serialisable to dict,
            so callers can pass it straight into ``log.info(...)``.
    """

    protected: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


class Layer4ProtectionService:
    """Shared protection-gate service for every Layer 4 close path.

    Constructed once by ``WorkerManager._init_services`` and registered
    in the service container under ``layer4_protection``. Watchdog and
    Profit Sniper both consume it via constructor DI.

    The service holds NO mutable state — every call is pure (apart
    from log emissions). This makes it safe to call from async
    coroutines, sync workers, and test code without contention.
    """

    # T2-9 (2026-05-12) — STRUCT_GUARD verdict cache age cap. Verdicts
    # older than this are treated as missing (sniper proceeds with stall
    # escape unimpeded). 60 s is one watchdog tick × 10 — long enough
    # to span the slowest realistic CALL_A/CALL_B + sniper interleaving,
    # short enough that a stale "stable" verdict cannot defer the
    # sniper indefinitely after market structure has actually broken.
    STRUCT_GUARD_VERDICT_MAX_AGE_S: float = 60.0

    def __init__(
        self,
        *,
        settings: Any,
        coordinator: Any,
        structure_cache: Any,
        regime_detector: Any,
        time_decay_calculator: TimeDecaySLCalculator | None = None,
    ) -> None:
        self.settings = settings
        self.coordinator = coordinator
        self.structure_cache = structure_cache
        self.regime_detector = regime_detector
        self._time_decay = time_decay_calculator
        # T2-9 (2026-05-12) — per-symbol cache of the latest STRUCT_GUARD
        # verdict computed by the watchdog after each
        # `time_decay.calculate()` call. Populated by
        # `record_struct_guard_verdict()` from
        # PositionWatchdog._handle_time_decay; consumed by
        # ProfitSniper._stall_escape_action via
        # `get_struct_guard_verdict()`. Pre-T2-9 sniper made stall-escape
        # decisions independently of STRUCT_GUARD; production logs (5h
        # window 2026-05-12) showed STRUCT_GUARD blocked=true on ENAUSDT
        # while sniper continued trailing — operator-approved decision
        # is "STRUCT_GUARD wins on stable" so sniper now defers when
        # the verdict is recent + stable.
        # Tuple = (verdict, monotonic_ts). verdict ∈ {"stable", "unstable"}.
        self._struct_verdicts: dict[str, tuple[str, float]] = {}

    # ─── T2-9 (2026-05-12): STRUCT_GUARD verdict cache API ─────────

    def record_struct_guard_verdict(
        self, symbol: str, verdict: str,
    ) -> None:
        """Stash the latest STRUCT_GUARD verdict for ``symbol``.

        Called by ``PositionWatchdog._handle_time_decay`` after every
        ``time_decay.calculate()`` call so the sniper can consult a
        coherent cross-layer view at stall-escape time.

        Args:
            symbol: Trading pair the verdict applies to.
            verdict: ``"stable"`` (STRUCT_GUARD blocked the close —
                structure is intact) or ``"unstable"`` (STRUCT_GUARD
                released — real invalidation evidence). Other values
                are treated as ``"unstable"`` (fail-open: don't block
                sniper on unknown verdicts).
        """
        import time as _t
        v = "stable" if verdict == "stable" else "unstable"
        self._struct_verdicts[symbol] = (v, _t.monotonic())

    def get_struct_guard_verdict(
        self, symbol: str,
    ) -> tuple[str, float]:
        """Return ``(verdict, age_seconds)`` for the latest cached
        STRUCT_GUARD verdict, or ``("", 0.0)`` if none / expired.

        Verdicts older than ``STRUCT_GUARD_VERDICT_MAX_AGE_S`` (60 s)
        are treated as missing — the sniper proceeds without
        deferral. This bounds the staleness window so a stale
        "stable" cannot defer sniper indefinitely after market
        structure has actually broken.

        Args:
            symbol: Trading pair to look up.

        Returns:
            ``(verdict, age_seconds)`` — verdict ∈ {"stable",
            "unstable"} when present and fresh, else ``("", 0.0)``.
        """
        import time as _t
        entry = self._struct_verdicts.get(symbol)
        if entry is None:
            return ("", 0.0)
        v, ts = entry
        age = _t.monotonic() - ts
        if age > self.STRUCT_GUARD_VERDICT_MAX_AGE_S:
            return ("", 0.0)
        return (v, age)

    # ─── Public API ───────────────────────────────────────────────────

    async def is_protected(
        self,
        *,
        symbol: str,
        side: str,
        close_reason: str,
        pnl_pct: float | None = None,
        check_min_hold: bool = True,
        check_profit: bool = True,
        check_structural: bool = True,
        time_decay_state: TimeDecayState | None = None,
    ) -> ProtectionResult:
        """Return a ProtectionResult describing whether this close is
        currently blocked by any active guardrail.

        Args:
            symbol: Trading symbol (e.g. "ETHUSDT").
            side: Position direction — "Buy" / "Sell" / "Long" / "Short".
            close_reason: Free-text close reason. Matched against the
                watchdog allow-list so genuine SL/TP/structure/regime/
                manual signals bypass the min-hold check.
            pnl_pct: Current unrealised PnL in percent. Required for
                ``check_profit`` to fire; if None, the profit check is
                silently skipped (treated as "no evidence; allow").
            check_min_hold: Apply the 5-min minimum-hold guardrail.
                Defaults True. Set False for known-mass-close paths
                (e.g. watchdog system-emergency) where every position
                must close immediately regardless of age.
            check_profit: Apply the profit/development guard. Defaults
                True. Set False for paths that have their own profit
                handling (e.g. profit_sniper Phase 1C already gates
                upstream — passing the gate again would be redundant).
            check_structural: Apply the structural-invalidation
                requirement. Defaults True. Set False when the caller
                already has a real structural reason (e.g. trail-hit,
                where the structure break IS the trail signal).
            time_decay_state: Optional pre-built TimeDecayState. When
                provided, ``check_structural`` reuses its entry
                anchors. When None, ``check_structural`` is
                fail-safe-block (returns protected=True with
                reason="no_struct_state") because we cannot validate
                invalidation without anchors.

        Returns:
            ProtectionResult. Caller checks ``.protected``; logs
            ``.reason`` and ``.evidence``.
        """
        # Phase 12.6 (lifecycle-logging-audit Gap 6.14-G1): per-call
        # heartbeat so operators can confirm L4P is actually invoked.
        def _emit_l4p(result: ProtectionResult) -> ProtectionResult:
            log.info(
                f"L4P_CHECK | sym={symbol} side={side} "
                f"protected={'Y' if result.protected else 'N'} "
                f"reason='{result.reason[:80]}' "
                f"close_reason='{close_reason[:60]}' | {ctx()}"
            )
            return result

        # 1) Min-hold guardrail. Reads the same config keys as the
        #    existing watchdog guardrail so all callers converge on a
        #    single 300-s contract.
        if check_min_hold:
            mh = await self._check_min_hold(symbol, close_reason)
            if mh.protected:
                return _emit_l4p(mh)

        # 2) Profit / development guard. Skips when pnl_pct unknown.
        if check_profit and pnl_pct is not None:
            pg = self._check_profit(symbol, pnl_pct)
            if pg.protected:
                return _emit_l4p(pg)

        # 3) Structural-invalidation requirement. Requires
        #    time_decay_state (entry anchors); without anchors the
        #    safest answer is "block" because we can't tell whether
        #    structure is intact or invalidated.
        if check_structural:
            sg = self._check_structural(
                symbol=symbol, side=side, state=time_decay_state,
            )
            if sg.protected:
                return _emit_l4p(sg)

        return _emit_l4p(ProtectionResult(
            protected=False,
            reason="no_protection",
            evidence={"close_reason": close_reason},
        ))

    async def get_position_age_seconds(self, symbol: str) -> float:
        """Resolve the position's current age in seconds via the
        TradeCoordinator. Returns 99999 for unregistered symbols
        (matching ``TradeCoordinator.get_age_seconds`` semantics) so
        callers naturally fall through age-based guards on
        unknown-to-coordinator positions."""
        if self.coordinator is None:
            return 99999.0
        try:
            return float(self.coordinator.get_age_seconds(symbol))
        except Exception as e:
            log.warning(
                f"L4_PROT_AGE_ERR | sym={symbol} err={e!r} | {ctx()}"
            )
            return 99999.0

    def compute_structural_invalidation(
        self, *, symbol: str, side: str, state: TimeDecayState,
    ) -> tuple[bool, str]:
        """Compute structural invalidation for a position.

        Disjunction across three signals (mirrors the IMPLEMENT doc):
          - XRAY confidence dropped ≥ ``cfg.xray_drop_threshold``
            (default 40 %) from the entry-time anchor.
          - Setup-type at entry is non-empty and differs from the
            current dominant pattern.
          - Regime has inverted to the opposite direction at ≥
            ``cfg.regime_inversion_confidence_threshold`` (default 60 %).

        Cache-miss / cold-start fail-safe: any missing input returns
        ``(False, "no_data:<which>")`` so the calculator BLOCKS
        force-close. Aligned with the operator philosophy of
        preferring false-negative invalidations over false-positive
        force-closes — a structurally healthy trade killed on a
        cold-start race is the worst outcome here.

        This function is the verbatim relocation of
        ``PositionWatchdog._compute_structural_invalidation`` (lines
        858-968 in the pre-Phase-4 tree). Watchdog and sniper now
        both call this implementation so behaviour is identical
        across paths.
        """
        td_cfg = self._time_decay.cfg if self._time_decay is not None else None
        if td_cfg is None:
            return (False, "no_data:no_calculator_cfg")
        if self.structure_cache is None or self.regime_detector is None:
            return (False, "no_data:services_unwired")
        try:
            cur_xray = self.structure_cache.get(symbol)
        except Exception:
            cur_xray = None
        if cur_xray is None:
            return (False, "no_data:xray_cache_miss")
        try:
            cur_regime = self.regime_detector.get_coin_regime(symbol)
        except Exception:
            cur_regime = None
        if cur_regime is None:
            return (False, "no_data:regime_unset")
        if state.entry_xray_confidence <= 0.0:
            return (False, "no_data:no_entry_anchor")

        reasons: list[str] = []
        invalidated = False

        # 1) XRAY confidence drop
        try:
            cur_xray_conf = float(getattr(cur_xray, "setup_type_confidence", 0.0) or 0.0)
        except Exception:
            cur_xray_conf = 0.0
        if state.entry_xray_confidence > 0.0:
            drop_pct = (
                state.entry_xray_confidence - cur_xray_conf
            ) / state.entry_xray_confidence
            if drop_pct >= td_cfg.xray_drop_threshold:
                reasons.append(f"xray_drop={drop_pct:.2f}")
                invalidated = True

        # 2) Setup-type drift
        try:
            cur_setup_type_obj = getattr(cur_xray, "setup_type", None)
            cur_setup_type = str(getattr(
                cur_setup_type_obj, "value", cur_setup_type_obj or "",
            ) or "")
        except Exception:
            cur_setup_type = ""
        if (
            state.entry_setup_type
            and cur_setup_type
            and state.entry_setup_type != cur_setup_type
        ):
            reasons.append(
                f"setup_drift:{state.entry_setup_type}->{cur_setup_type}"
            )
            invalidated = True

        # 3) Regime inversion (direction-aware)
        try:
            cur_regime_label = str(getattr(
                getattr(cur_regime, "regime", None), "value", "",
            ) or "")
            cur_regime_conf = float(getattr(cur_regime, "confidence", 0.0) or 0.0)
        except Exception:
            cur_regime_label = ""
            cur_regime_conf = 0.0
        if cur_regime_conf >= td_cfg.regime_inversion_confidence_threshold:
            inverted = (
                (side in ("Buy", "Long") and cur_regime_label == "trending_down")
                or (side in ("Sell", "Short") and cur_regime_label == "trending_up")
            )
            if inverted:
                reasons.append(
                    f"regime_inv:{cur_regime_label}@{cur_regime_conf:.2f}"
                )
                invalidated = True

        return (invalidated, ",".join(reasons) or "stable")

    # ─── Internal checks ──────────────────────────────────────────────

    async def _check_min_hold(
        self, symbol: str, close_reason: str,
    ) -> ProtectionResult:
        """Enforce the 5-min minimum-hold contract. Bypassed when
        ``close_reason`` matches a substring in the watchdog allow-list."""
        wd_cfg = getattr(self.settings, "watchdog", None)
        min_hold = float(
            getattr(wd_cfg, "strategic_action_min_hold_seconds", 300.0)
            if wd_cfg is not None
            else 300.0
        )
        allow_list = list(
            getattr(
                wd_cfg, "strategic_action_allowed_early_close_reasons", [],
            )
            if wd_cfg is not None
            else []
        )

        reason_lc = (close_reason or "").strip().lower()
        reason_allowed = (
            any(tok in reason_lc for tok in allow_list)
            if reason_lc
            else False
        )
        if reason_allowed:
            # Genuine hard-stop / TP / structure-break — bypass min-hold.
            return ProtectionResult(
                protected=False,
                reason=f"min_hold:reason_allowed:{close_reason[:60]!r}",
                evidence={"reason_allowed": True},
            )

        age = await self.get_position_age_seconds(symbol)
        if min_hold > 0.0 and age < min_hold:
            return ProtectionResult(
                protected=True,
                reason=f"min_hold:age={age:.0f}s<{min_hold:.0f}s",
                evidence={
                    "age_seconds": age,
                    "min_hold_seconds": min_hold,
                    "close_reason": close_reason,
                },
            )
        return ProtectionResult(
            protected=False,
            reason="min_hold:age_ok",
            evidence={"age_seconds": age, "min_hold_seconds": min_hold},
        )

    def _check_profit(self, symbol: str, pnl_pct: float) -> ProtectionResult:
        """Refuse close on profitable / developing positions. Aligned
        with the sniper Phase 1C guards so behaviour is identical."""
        sniper_cfg = getattr(self.settings, "layer4_sniper", None)
        profit_threshold = float(
            getattr(sniper_cfg, "profit_protection_threshold", 0.0)
            if sniper_cfg is not None
            else 0.0
        )
        development_floor = float(
            getattr(sniper_cfg, "development_window_lower", -0.3)
            if sniper_cfg is not None
            else -0.3
        )
        if pnl_pct > profit_threshold:
            return ProtectionResult(
                protected=True,
                reason=f"profit_guard:pnl={pnl_pct:+.2f}%>{profit_threshold:+.2f}%",
                evidence={"pnl_pct": pnl_pct, "threshold": profit_threshold},
            )
        if pnl_pct > development_floor:
            return ProtectionResult(
                protected=True,
                reason=f"development_guard:pnl={pnl_pct:+.2f}%>{development_floor:+.2f}%",
                evidence={"pnl_pct": pnl_pct, "floor": development_floor},
            )
        return ProtectionResult(
            protected=False,
            reason="profit:meaningful_loss",
            evidence={"pnl_pct": pnl_pct},
        )

    def _check_structural(
        self,
        *,
        symbol: str,
        side: str,
        state: TimeDecayState | None,
    ) -> ProtectionResult:
        """Refuse close when no structural-invalidation evidence is
        present. Falls back to ``protected=True`` when state is None
        because we cannot evaluate without entry anchors — fail-safe."""
        if state is None:
            return ProtectionResult(
                protected=True,
                reason="struct:no_state_provided",
                evidence={"note": "caller did not pass time_decay_state"},
            )
        invalidated, reason_str = self.compute_structural_invalidation(
            symbol=symbol, side=side, state=state,
        )
        if not invalidated:
            return ProtectionResult(
                protected=True,
                reason=f"struct:intact:{reason_str}",
                evidence={
                    "structural_invalidation": False,
                    "reason": reason_str,
                },
            )
        return ProtectionResult(
            protected=False,
            reason=f"struct:invalidated:{reason_str}",
            evidence={
                "structural_invalidation": True,
                "reason": reason_str,
            },
        )
