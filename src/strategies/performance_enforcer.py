"""Enforcer v2 — PnL-Based Intelligent Throttling.

Primary signal: Daily PnL % (are we actually losing money?)
Secondary signal: Loss streak (only when PnL is meaningfully negative).

Key principle: A 3-loss streak during a profitable day is normal statistical
variance, NOT a problem. Only restrict trading when the system is genuinely
losing money. Full halt is delegated to DailyPnLManager (and the new
HALTED level 3 below for emergency stop).

Levels (CALL_B Framing Fix Phase 2A, 2026-05-06 raised SURVIVAL +
added HALTED):
  0 (NORMAL):              PnL >= 0%      — trade freely
  1 (CAPITAL_PRESERVATION): PnL < -3%     — leverage clamped to ``level_1_max_leverage``
                                            (was BLOCK pre-Phase-4; now CLAMP via
                                            ``clamp_leverage``)
  2 (SURVIVAL):             PnL < -12%    — leverage clamped to ``level_2_max_leverage``,
                                            ``qualify_survival_trade`` enforces
                                            quality gate (RR floor adjusted via
                                            Phase 2B TP-scaling). Was -7% pre-Phase-2A.
  3 (HALTED):               PnL < -15%    — emergency stop (qualify_*_trade returns
                                            (False, "halted")). Operator-driven
                                            recovery only. ENFORCER_HALTED entry
                                            and exit logged once per transition.

Streak path (secondary): both ``streak <= streak_boost_threshold`` (default -8)
AND ``pnl < streak_boost_pnl_floor_pct`` (default -1 %) elevate to level 1.

Size multiplier (soft throttle, applied independently of level):
  PnL >= 0%             → 1.0x (full size)
  0% to caution (-3%)   → 0.75x (25% reduction; ``size_reduction_factor``)
  caution to survival   → 0.50x (50% reduction)
  below survival (-12%) → 0.25x / 0.40x / 0.50x depending on recovery stage
  below halted (-15%)   → 0.0x (effectively no new trades; qualify gate blocks)
"""

import time

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc

log = get_logger("enforcer")


class PerformanceEnforcer:
    """PnL-Based Intelligent Throttling — manages trade INTENSITY, never halts.

    Keeps the same class name so all existing wiring stays intact.
    """

    def __init__(self, settings, db, services: dict) -> None:
        self.settings = settings
        self.db = db
        self.services = services
        self.coordinator = services.get("trade_coordinator")

        # Rolling stats
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._profit_today_pct = 0.0
        self._streak = 0  # positive = win streak, negative = loss streak
        self._per_coin: dict[str, dict] = {}
        self._per_direction: dict[str, dict] = {
            "Buy": {"wins": 0, "losses": 0},
            "Sell": {"wins": 0, "losses": 0},
        }
        self._last_claude_call = now_utc()
        self._today_date = now_utc().strftime("%Y-%m-%d")

        # Enforcement state — 0 (normal), 1 (preservation), 2 (survival)
        self._enforcement_level: int = 0
        self._level_changed_at: float = time.time()
        self._reset_grace_until: float = 0.0  # manual-reset grace window

        # Config shortcuts. Phase 4 of dir-block-fix (2026-05-05) raised
        # the dataclass defaults — the getattr fallbacks below mirror
        # the new defaults so they stay in sync if settings.enforcer is
        # None. The runtime never hits this fallback in production
        # because settings.enforcer is always populated by
        # _build_enforcer; aligning the literal here is purely the
        # same hygiene fix as Discovery 2 in apex/gate.py:241.
        _ecfg = getattr(settings, "enforcer", None)
        self._pnl_caution_pct: float = getattr(_ecfg, "pnl_caution_pct", -3.0)
        # CALL_B Framing Fix Phase 2A (2026-05-06) — survival raised
        # from -7.0 to -12.0; halted introduced at -15.0. Fallback
        # values mirror the new dataclass defaults so any code path
        # that runs without settings.enforcer (test fixtures) gets
        # the post-2A behaviour.
        self._pnl_survival_pct: float = getattr(_ecfg, "pnl_survival_pct", -12.0)
        self._pnl_halted_pct: float = getattr(_ecfg, "pnl_halted_pct", -15.0)
        self._size_reduction_enabled: bool = getattr(_ecfg, "size_reduction_enabled", True)
        self._size_reduction_at_pnl_pct: float = getattr(_ecfg, "size_reduction_at_pnl_pct", 0.0)
        self._size_reduction_factor: float = getattr(_ecfg, "size_reduction_factor", 0.75)
        self._streak_boost_threshold: int = getattr(_ecfg, "streak_boost_threshold", -8)
        self._streak_boost_pnl_floor_pct: float = getattr(
            _ecfg, "streak_boost_pnl_floor_pct", -1.0,
        )
        self._max_enforcement_minutes: int = getattr(_ecfg, "max_enforcement_minutes", 45)
        self._grace_period_minutes: int = getattr(_ecfg, "grace_period_minutes", 30)
        # Per-level restrictions from config
        self._l1_max_pos: int = getattr(_ecfg, "level_1_max_positions", 3)
        self._l1_max_lev: int = getattr(_ecfg, "level_1_max_leverage", 3)
        self._l1_min_score: int = getattr(_ecfg, "level_1_min_score", 80)
        self._l2_max_pos: int = getattr(_ecfg, "level_2_max_positions", 2)
        self._l2_max_lev: int = getattr(_ecfg, "level_2_max_leverage", 3)
        self._l2_min_score: int = getattr(_ecfg, "level_2_min_score", 80)
        self._l2_min_confluence: int = getattr(_ecfg, "level_2_min_confluence", 7)
        self._l2_min_rr: float = getattr(_ecfg, "level_2_min_rr", 3.0)
        # Recovery tracking
        self._recent_results: list[bool] = []  # last 5 trade results (True=win)
        self._recovery_stage: int = 0  # 0=not recovering, 1-2=recovering

        # Boot sentinel (2026-05-31) — make the size-reduction switch state
        # visible on every restart. When disabled, get_size_multiplier() always
        # returns 1.0 (the brain's deliberate size is not throttled on red days);
        # the emergency qualify-gate halts at HALTED PnL remain active regardless.
        try:
            # f-string (loguru uses {} not %-style, so positional %s args would
            # render literally — interpolate here so the sentinel is readable).
            log.info(
                f"ENFORCER_SIZE_REDUCTION_SENTINEL | "
                f"size_reduction_enabled={self._size_reduction_enabled} "
                f"at_pnl_pct={self._size_reduction_at_pnl_pct} "
                f"factor={self._size_reduction_factor} | "
                f"(disabled -> no PnL-band size throttle; emergency halts unaffected)"
            )
        except Exception:
            pass

    # ─── Public API (same interface as before) ───

    def is_trading_halted(self) -> bool:
        """Enforcer v2 NEVER halts — DailyPnLManager handles full halt."""
        return False

    def should_allow_trade(self, leverage: int = 1) -> tuple[bool, str]:
        """Phase 4 of dir-block-fix (2026-05-05): leverage limits are now
        enforced via ``clamp_leverage`` (modify, never block). This
        method is preserved for the layer_manager and rule_engine call
        sites, which always pass ``leverage=1`` and only consult the
        boolean as an enforcer-halt signal — and the enforcer never
        halts (delegated to DailyPnLManager). Always returns
        ``(True, "ok")``; the str field is kept for backward compat.
        """
        return True, "ok"

    def clamp_leverage(self, leverage: int) -> tuple[int, str]:
        """Phase 4 of dir-block-fix (2026-05-05): clamp the requested
        leverage to the current enforcement level's cap. Replaces the
        prior block-on-leverage-exceeds-cap behavior under the
        operator's aggressive-exploitation philosophy.

        Returns ``(clamped_leverage, reason)``. ``reason`` is the empty
        string when no clamp was applied; otherwise a human-readable
        string suitable for the ENFORCER_LEV_CLAMP log line, e.g.
        ``"PRESERVATION_CLAMP: 5→3 (PnL=-3.50%)"``.
        """
        # Phase 2A (2026-05-06) — HALTED emergency clamp. The
        # qualify_*_trade gate would have already blocked the trade
        # (returning (False, "halted")), so this branch is defense in
        # depth: if a future call site forgets to consult the gate,
        # leverage still drops to 1 so size_usd * leverage is bounded.
        if self._enforcement_level >= 3:
            clamped = 1
            return clamped, (
                f"HALTED_CLAMP: {leverage}->{clamped} "
                f"(PnL={self._profit_today_pct:+.2f}%)"
            )
        if self._enforcement_level >= 2 and leverage > self._l2_max_lev:
            clamped = int(self._l2_max_lev)
            return clamped, (
                f"SURVIVAL_CLAMP: {leverage}->{clamped} "
                f"(PnL={self._profit_today_pct:+.2f}%)"
            )
        if self._enforcement_level >= 1 and leverage > self._l1_max_lev:
            clamped = int(self._l1_max_lev)
            return clamped, (
                f"PRESERVATION_CLAMP: {leverage}->{clamped} "
                f"(PnL={self._profit_today_pct:+.2f}%)"
            )
        return int(leverage), ""

    def get_max_positions_override(self) -> int | None:
        """Return reduced max positions if throttled, or None for normal.

        Phase 2A (2026-05-06): HALTED returns 0 — caller treats this as
        "no new positions" so the cap matches the qualify-gate's
        (False, "halted") result.
        """
        if self._enforcement_level >= 3:
            return 0
        if self._enforcement_level >= 2:
            return self._l2_max_pos
        if self._enforcement_level >= 1:
            return self._l1_max_pos
        return None

    def get_min_score_override(self) -> int | None:
        """Return minimum setup score if throttled, or None for normal."""
        if self._enforcement_level >= 3:
            # HALTED — no setup will pass; gate is qualify_*_trade.
            return 100
        if self._enforcement_level >= 2:
            return self._l2_min_score
        if self._enforcement_level >= 1:
            return self._l1_min_score
        return None

    def get_size_multiplier(self) -> float:
        """Position size multiplier based on daily PnL.

        This is a soft throttle — doesn't block trades, just makes them smaller.
        Called by strategy_worker when calculating position size.
        Configurable via size_reduction_enabled and size_reduction_at_pnl_pct.
        """
        if not self._size_reduction_enabled:
            return 1.0

        pnl = self._profit_today_pct
        if pnl >= self._size_reduction_at_pnl_pct:  # default 0.0%
            return 1.0
        elif pnl > self._pnl_caution_pct:  # 0% down to caution (default -3 %)
            return self._size_reduction_factor  # default 0.75
        elif pnl > self._pnl_survival_pct:  # caution down to survival (default -3 % to -12 %)
            return 0.50
        elif pnl > self._pnl_halted_pct:  # survival down to halted (default -12 % to -15 %)
            # Recovery stage eases the multiplier
            if self._recovery_stage >= 2:
                return 0.50
            elif self._recovery_stage >= 1:
                return 0.40
            return 0.25
        else:  # below halted (default -15 %) — emergency stop
            # 0.0 reflects the fact that qualify_*_trade returns
            # (False, "halted") so no new size is being deployed; the
            # multiplier surfacing 0.0 matches the upstream invariant
            # for any caller that reads sz_mult without consulting
            # qualify gates.
            return 0.0

    def _scale_tp_for_rr(
        self,
        side: str,
        entry: float,
        sl: float,
        target_rr: float,
        structural_ceiling: float | None,
    ) -> tuple[float | None, str]:
        """Compute a TP that achieves ``target_rr`` while staying within
        ``structural_ceiling``.

        Helper for the SURVIVAL RR adjustment path (Phase 2B). The
        ceiling is the maximum reasonable TP price for the trade's
        direction (price ABOVE entry for Buy, BELOW entry for Sell).
        When ``structural_ceiling`` is None, the scaling is unbounded.

        Returns ``(new_tp_or_None, reason)``:
          - ``(new_tp, "rr_scaled_to_floor")`` on success.
          - ``(None, "rr_scale_zero_risk")`` when entry == sl.
          - ``(None, "rr_scale_exceeds_ceiling:..."``) when scaled TP
            for a Buy would exceed the ceiling.
          - ``(None, "rr_scale_below_floor:..."``) when scaled TP for a
            Sell would fall below the (price-)floor.
        """
        risk = abs(entry - sl)
        if risk <= 0:
            return (None, "rr_scale_zero_risk")
        reward_target = target_rr * risk
        is_buy = side in ("Buy", "Long")
        if is_buy:
            new_tp = entry + reward_target
            if structural_ceiling is not None and new_tp > structural_ceiling:
                return (
                    None,
                    f"rr_scale_exceeds_ceiling:{new_tp:.6f}>{structural_ceiling:.6f}",
                )
        else:
            new_tp = entry - reward_target
            if structural_ceiling is not None and new_tp < structural_ceiling:
                return (
                    None,
                    f"rr_scale_below_floor:{new_tp:.6f}<{structural_ceiling:.6f}",
                )
        return (float(new_tp), "rr_scaled_to_floor")

    def try_adjust_for_survival_rr(
        self,
        symbol: str,
        side: str,
        structure_cache,
        target_rr: float | None = None,
    ) -> tuple[float | None, str, float, float]:
        """Phase 2B (2026-05-06) — attempt to scale TP outward to
        achieve the SURVIVAL RR floor instead of blocking the trade.

        Convert SURVIVAL's RR floor from BLOCK to ADJUSTMENT. The X-RAY
        already computed the structural RR for the chosen direction; if
        it's below ``level_2_min_rr`` (default 3.0), scale TP by up to
        50% beyond the structural target. If the scaled TP would exceed
        that buffer, fall through to a block.

        Returns ``(new_tp_or_None, reason, old_rr, new_rr)``:
          - ``(new_tp, "rr_scaled_to_floor", old_rr, new_rr)`` on success.
          - ``(None, reason, old_rr, 0.0)`` when adjustment is
            infeasible. Caller blocks the trade with the legacy reason.

        HALTED (level 3) returns ``(None, "halted", 0.0, 0.0)`` so the
        adjustment path can never override the emergency stop.
        """
        if target_rr is None:
            target_rr = self._l2_min_rr
        # Phase 2A — HALTED never gets adjustment; emergency stop
        # rules.
        if self._enforcement_level >= 3:
            return (None, "halted", 0.0, 0.0)
        # Adjustment is only meaningful in SURVIVAL.
        if self._enforcement_level < 2:
            return (None, "rr_not_in_survival", 0.0, 0.0)
        if not structure_cache:
            return (None, "rr_no_cache", 0.0, 0.0)
        analysis = structure_cache.get(symbol)
        if not analysis:
            return (None, "rr_no_xray_data", 0.0, 0.0)
        sp = analysis.structural_placement
        if not sp:
            return (None, "rr_no_placement", 0.0, 0.0)

        is_buy = side in ("Buy", "Long")
        struct_sl = sp.long_sl_price if is_buy else sp.short_sl_price
        struct_tp = sp.long_tp_price if is_buy else sp.short_tp_price
        old_rr = float(sp.rr_long if is_buy else sp.rr_short)

        # Reference (entry) price: midpoint of entry_zone if available,
        # else fall back to halfway between structural SL and TP.
        if sp.entry_zone_low > 0 and sp.entry_zone_high > 0:
            ref_price = (sp.entry_zone_low + sp.entry_zone_high) / 2.0
        elif struct_sl > 0 and struct_tp > 0:
            ref_price = (struct_sl + struct_tp) / 2.0
        else:
            return (None, "rr_invalid_levels", old_rr, 0.0)

        if struct_sl <= 0 or struct_tp <= 0 or ref_price <= 0:
            return (None, "rr_invalid_levels", old_rr, 0.0)

        # Structural buffer — allow scaling up to 50% past the structural
        # TP. Beyond that the trade is fictional rather than aggressive,
        # so block.
        if is_buy:
            ceiling = struct_tp + (struct_tp - ref_price) * 0.5
        else:
            ceiling = struct_tp - (ref_price - struct_tp) * 0.5

        new_tp, scale_reason = self._scale_tp_for_rr(
            side=side,
            entry=ref_price,
            sl=struct_sl,
            target_rr=target_rr,
            structural_ceiling=ceiling,
        )
        if new_tp is None:
            return (None, scale_reason, old_rr, 0.0)

        risk = abs(ref_price - struct_sl)
        new_rr = abs(new_tp - ref_price) / risk if risk > 0 else 0.0
        return (float(new_tp), "rr_scaled_to_floor", old_rr, float(new_rr))

    def qualify_survival_trade(
        self, symbol: str, structure_cache=None,
    ) -> tuple[bool, str]:
        """Quality-gate for SURVIVAL mode trades using X-RAY data.

        Instead of restricting to BTC/ETH by name, allows ANY coin
        with A+/A quality, high confluence, and strong R:R.

        Phase 2A (2026-05-06): when level >= 3 (HALTED), returns
        ``(False, "halted")`` regardless of structure data — emergency
        stop. The strategy_worker call site emits TRADE_SKIP rsn=
        survival_block which is the same skip reason used for the
        SURVIVAL quality gate (operators differentiate via the detail
        field carrying ``halted``).
        """
        # Phase 2A — HALTED emergency stop.
        if self._enforcement_level >= 3:
            return False, "halted"
        if self._enforcement_level < 2:
            return True, "not_in_survival"
        if not structure_cache:
            return True, "no_cache_available"

        analysis = structure_cache.get(symbol)
        if not analysis:
            return False, "no_xray_data"

        if analysis.setup_quality not in ("A+", "A"):
            return False, f"quality_{analysis.setup_quality}_below_A"

        mtf = analysis.mtf_confluence
        if mtf and mtf.score < self._l2_min_confluence:
            return False, f"confluence_{mtf.score}_below_{self._l2_min_confluence}"

        sp = analysis.structural_placement
        if sp and sp.rr_ratio < self._l2_min_rr:
            return False, f"rr_{sp.rr_ratio:.1f}_below_{self._l2_min_rr}"

        return True, "quality_pass"

    def _check_recovery(self) -> None:
        """Check if recent wins allow easing SURVIVAL restrictions."""
        if self._enforcement_level < 2 or len(self._recent_results) < 2:
            self._recovery_stage = 0
            return

        recent = self._recent_results[-5:]
        wins = sum(1 for r in recent if r)
        total = len(recent)

        if total >= 4 and wins >= 3:
            self._recovery_stage = 2
        elif total >= 2 and wins >= 2:
            self._recovery_stage = 1
        else:
            self._recovery_stage = 0

    async def check_and_enforce(self) -> dict:
        """Called every cycle. Collect stats and enforce PnL-based rules."""

        # ══ GRACE PERIOD — skip ALL enforcement logic during grace ══
        if self._reset_grace_until and time.time() < self._reset_grace_until:
            remaining = (self._reset_grace_until - time.time()) / 60
            log.info(
                f"ENFORCER_GRACE | el={self._enforcement_level} "
                f"remaining={remaining:.0f}min | Grace active, skipping enforcement | {ctx()}"
            )
            return self._build_report()

        self._check_day_reset()

        # Collect stats from recent trades
        try:
            await self._collect_stats()
        except Exception as e:
            log.warning("Stats collection failed: {err}", err=str(e))

        # Check Claude heartbeat
        heartbeat_ok = self._check_heartbeat()

        # ══ PnL-Based Level Calculation ══
        wr = self._wins_today / max(self._trades_today, 1)
        streak = self._streak
        pnl = self._profit_today_pct
        old_level = self._enforcement_level

        # Auto-recovery: if stuck at el>=1 for too long, recover
        if self._enforcement_level >= 1:
            elapsed_min = (time.time() - self._level_changed_at) / 60
            if elapsed_min >= self._max_enforcement_minutes:
                log.warning(
                    f"ENFORCER_AUTO_RECOVERY | el={self._enforcement_level} "
                    f"stuck_for={elapsed_min:.0f}min "
                    f"max={self._max_enforcement_minutes}min "
                    f"| Auto-recovering to el=0 | {ctx()}"
                )
                self._enforcement_level = 0
                self._level_changed_at = time.time()
                old_level = 0

        # ── Primary signal: Daily PnL ──
        new_level = 0

        if pnl >= 0:
            # PROFITABLE — streak doesn't matter. System is making money.
            new_level = 0
        elif pnl > self._pnl_caution_pct:  # 0% to caution (default -3 %)
            # Slightly negative — keep trading with reduced sizes
            # UNLESS there's also a long loss streak AND the PnL has
            # crossed below streak_boost_pnl_floor_pct (default -1 %).
            # Phase 4 of dir-block-fix added the PnL floor so the streak
            # path can't elevate level on near-flat days.
            if (
                streak <= self._streak_boost_threshold
                and pnl < self._streak_boost_pnl_floor_pct
            ):
                new_level = 1  # Long streak AND meaningfully negative PnL
            else:
                new_level = 0
        elif pnl > self._pnl_survival_pct:  # caution to survival (default -3 % to -12 %)
            new_level = 1  # Capital preservation
        elif pnl > self._pnl_halted_pct:  # survival to halted (default -12 % to -15 %)
            new_level = 2  # Survival
        else:  # below halted (default -15 %)
            new_level = 3  # HALTED — emergency stop, qualify_* gate blocks trades

        self._enforcement_level = new_level

        # Track level changes
        if self._enforcement_level != old_level:
            self._level_changed_at = time.time()
            reason = self._get_level_change_reason(old_level, new_level, pnl, streak)
            log.warning(
                f"ENFORCER_LEVEL | old_el={old_level} new_el={new_level} "
                f"| reason={reason} | pnl={pnl:+.2f}% strk={streak:+d} | {ctx()}"
            )
            # Phase 2A (2026-05-06) — HALTED entry/exit explicit
            # sentinel for operators tailing logs. Distinct from the
            # generic ENFORCER_LEVEL transition so a HALTED entry is
            # immediately greppable. Fires once per transition (the
            # outer `if` already gates on level change).
            if new_level == 3 and old_level < 3:
                log.error(
                    f"ENFORCER_HALTED | event=entry pnl={pnl:+.2f}% "
                    f"halted_threshold={self._pnl_halted_pct:+.2f}% "
                    f"streak={streak:+d} | new trades blocked, "
                    f"emergency manual review | {ctx()}"
                )
            elif old_level == 3 and new_level < 3:
                log.warning(
                    f"ENFORCER_HALTED | event=exit pnl={pnl:+.2f}% "
                    f"new_level={new_level} streak={streak:+d} "
                    f"| recovered above halted threshold | {ctx()}"
                )

        # State log
        sz_mult = self.get_size_multiplier()
        trigger = "none"
        if pnl < self._pnl_halted_pct:
            trigger = "pnl_halted"
        elif pnl < self._pnl_survival_pct:
            trigger = "pnl_survival"
        elif pnl < self._pnl_caution_pct:
            trigger = "pnl_caution"
        elif pnl < 0 and streak <= self._streak_boost_threshold:
            trigger = "streak_boost"

        log.info(
            f"ENFORCER_STATE | trades={self._trades_today} | wins={self._wins_today} "
            f"| losses={self._losses_today} | wr={wr:.2f} | strk={streak:+d} "
            f"| pnl={pnl:+.2f}% | el={self._enforcement_level} "
            f"| sz_mult={sz_mult:.2f} | trigger={trigger} | {ctx()}"
        )

        return self._build_report()

    def _build_report(self) -> dict:
        """Build diagnostic report dict."""
        return {
            "trades_today": self._trades_today,
            "wins": self._wins_today,
            "losses": self._losses_today,
            "win_rate": self._wins_today / max(self._trades_today, 1),
            "profit_pct": self._profit_today_pct,
            "streak": self._streak,
            "heartbeat_ok": self._check_heartbeat(),
            "enforcement_level": self._enforcement_level,
            "size_multiplier": self.get_size_multiplier(),
        }

    def _get_level_change_reason(self, old: int, new: int, pnl: float, streak: int) -> str:
        if new > old:
            if pnl < self._pnl_halted_pct:
                return "pnl_below_halted"
            elif pnl < self._pnl_survival_pct:
                return "pnl_below_survival"
            elif pnl < self._pnl_caution_pct:
                return "pnl_below_caution"
            elif streak <= self._streak_boost_threshold:
                return "streak_boost"
            return "pnl_deterioration"
        else:
            return "pnl_recovery"

    # ─── Stats Collection ───

    async def _collect_stats(self) -> None:
        """Collect rolling stats from trade_thesis.

        P4 of P1-P10: WHERE filter restricted to current_mode (resolved
        from transformer in services dict). Without this filter, a
        morning shadow session contaminated afternoon bybit_demo
        enforcement state — wrong inputs → wrong NORMAL/CAUTION/SURVIVAL
        mode → wrong behavioural changes. Falls back to no-filter when
        transformer is unavailable (early-boot edge case); the rare
        contamination is preferable to silently halting enforcement.
        """
        today = now_utc().strftime("%Y-%m-%d")
        # P4: resolve current trading mode for cross-mode isolation.
        _xfm = self.services.get("transformer")
        _mode: str | None = None
        if _xfm is not None:
            try:
                _mode = str(_xfm.current_mode) if _xfm.current_mode else None
            except Exception:
                _mode = None
        try:
            if _mode:
                rows = await self.db.fetch_all(
                    """SELECT symbol, direction, actual_pnl_pct, close_reason, exchange_mode
                       FROM trade_thesis
                       WHERE status = 'closed' AND DATE(closed_at) = ?
                         AND exchange_mode = ?
                         AND (close_reason IS NULL OR close_reason != 'transformer_switch')
                       ORDER BY closed_at DESC""",
                    (today, _mode),
                )
            else:
                # Fallback path — transformer not yet wired. Logged once
                # so the gap is observable; behaviour matches pre-P4.
                log.warning(
                    "ENFORCER_NO_MODE_FILTER | transformer_unavailable_in_services | "
                    "cross_mode_contamination_possible_until_wired"
                )
                rows = await self.db.fetch_all(
                    """SELECT symbol, direction, actual_pnl_pct, close_reason, exchange_mode
                       FROM trade_thesis
                       WHERE status = 'closed' AND DATE(closed_at) = ?
                         AND (close_reason IS NULL OR close_reason != 'transformer_switch')
                       ORDER BY closed_at DESC""",
                    (today,),
                )
            if rows:
                self._trades_today = len(rows)
                self._wins_today = sum(1 for r in rows if (r["actual_pnl_pct"] or 0) > 0)
                _breakeven = sum(1 for r in rows if (r["actual_pnl_pct"] or 0) == 0)
                self._losses_today = self._trades_today - self._wins_today - _breakeven
                self._profit_today_pct = sum(r["actual_pnl_pct"] or 0 for r in rows)

                # Streak detection (most recent trades first)
                # Breakeven trades (pnl == 0) do NOT extend a loss streak
                streak = 0
                for r in rows:
                    pnl = r["actual_pnl_pct"] or 0
                    if pnl > 0:
                        if streak >= 0:
                            streak += 1
                        else:
                            break
                    elif pnl < 0:
                        if streak <= 0:
                            streak -= 1
                        else:
                            break
                    # pnl == 0 (breakeven): skip, don't break or extend streak
                self._streak = streak

                # Per-coin stats
                self._per_coin = {}
                for r in rows:
                    sym = r["symbol"]
                    if sym not in self._per_coin:
                        self._per_coin[sym] = {"wins": 0, "losses": 0, "pnl": 0.0}
                    pnl = r["actual_pnl_pct"] or 0
                    if pnl > 0:
                        self._per_coin[sym]["wins"] += 1
                    elif pnl < 0:
                        self._per_coin[sym]["losses"] += 1
                    # pnl == 0: neither win nor loss
                    self._per_coin[sym]["pnl"] += pnl

                # Per-direction stats
                self._per_direction = {"Buy": {"wins": 0, "losses": 0}, "Sell": {"wins": 0, "losses": 0}}
                for r in rows:
                    d = r["direction"] if r["direction"] in ("Buy", "Sell") else "Buy"
                    pnl = r["actual_pnl_pct"] or 0
                    if pnl > 0:
                        self._per_direction[d]["wins"] += 1
                    else:
                        self._per_direction[d]["losses"] += 1
        except Exception as e:
            log.error(f"ENFORCER_STATS_FAIL | err='{str(e)[:500]}' | {ctx()}")

    def _check_heartbeat(self) -> bool:
        """Check if Claude has shown signs of life in the last 10 minutes.

        Phase 6 follow-up (post-Layer-1 fix): use ``max(_last_call_attempt_time,
        _last_response_time)`` so a long in-flight call (attempt at T-7min,
        no response yet) doesn't false-trip the staleness check.

        Pre-fix the enforcer read only ``_last_call_time`` (which on the
        ``ClaudeCodeClient`` is success-time, not attempt-time) and thus
        couldn't distinguish "no call has happened in 11 min" from "a
        call started 11 min ago and is still pending". With the new
        triple, both cases route correctly:
          - No activity at all → max(...) is old → stale → False
          - Call in flight → attempt_time is recent → not stale → True
          - Call completed → response_time is recent → not stale → True
        """
        claude_client = self.services.get("claude_client")
        if claude_client is None:
            return True
        attempt_t = float(getattr(claude_client, "_last_call_attempt_time", 0.0) or 0.0)
        resp_t = float(getattr(claude_client, "_last_response_time", 0.0) or 0.0)
        # Fallback to the legacy attribute if neither new field is set
        # (e.g. an alternate client class without the Phase 6 fix).
        if attempt_t == 0.0 and resp_t == 0.0:
            legacy = float(getattr(claude_client, "_last_call_time", 0.0) or 0.0)
            if legacy == 0.0:
                return True
            return (time.time() - legacy) < 600
        alive_at = max(attempt_t, resp_t)
        return (time.time() - alive_at) < 600

    def _check_day_reset(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if today != self._today_date:
            self._today_date = today
            self._trades_today = 0
            self._wins_today = 0
            self._losses_today = 0
            self._profit_today_pct = 0.0
            self._streak = 0
            self._per_coin = {}
            self._per_direction = {"Buy": {"wins": 0, "losses": 0}, "Sell": {"wins": 0, "losses": 0}}
            # Reset enforcement on new day
            self._enforcement_level = 0
            self._level_changed_at = time.time()

    # ─── Coaching Text for Claude's Prompt ───

    def get_coaching_text(self, structure_cache=None) -> str:
        """Format PnL-aware coaching text for Claude's prompt."""
        wr = self._wins_today / max(self._trades_today, 1)
        pnl = self._profit_today_pct
        level = self._enforcement_level
        streak = self._streak
        sz_mult = self.get_size_multiplier()

        lines = ["PERFORMANCE COACH (your stats today):"]
        lines.append(
            f"  Trades: {self._trades_today} | Wins: {self._wins_today} | Losses: {self._losses_today}"
        )
        lines.append(f"  Win rate: {wr:.0%} | PnL: {pnl:+.2f}% | Streak: {streak:+d}")

        if level == 0 and pnl >= 0:
            lines.append(
                f"  Session: PROFITABLE. Trade normally with full conviction. "
                f"Focus on quality setups and let the system work."
            )
        elif level == 0 and pnl < 0:
            lines.append(
                f"  Session: SLIGHTLY NEGATIVE. Position sizes reduced to {sz_mult:.0%}. "
                f"Focus on highest conviction A+ setups. Quality over quantity."
            )
        elif level == 1:
            lines.append(
                f"  CAPITAL PRESERVATION MODE. Max 3 positions, leverage capped at 3x. "
                f"Only A+ setups with strong consensus. Protect capital."
            )
        elif level == 3:
            # Phase 2A (2026-05-06) — HALTED emergency mode coaching
            # text. qualify_*_trade returns (False, "halted") so this
            # text is rarely surfaced (no new trades open during HALTED)
            # but kept for diagnostic completeness in the rare case
            # check_and_enforce runs with level=3 still cached.
            lines.append(
                f"  HALTED MODE. Daily PnL has crossed {self._pnl_halted_pct:+.2f}%. "
                f"All new trades blocked by qualify_*_trade. Operator review required "
                f"before resuming. Existing positions remain managed by CALL_B / "
                f"watchdog as normal."
            )
        elif level == 2:
            # X-RAY-aware coaching — show top quality setups, not "BTC/ETH only"
            top_picks = ""
            if structure_cache:
                try:
                    ranked = structure_cache.get_ranked_setups()
                    quality_setups = [s for s in ranked[:5] if s.setup_quality in ("A+", "A")]
                    if quality_setups:
                        top_picks = " Current top X-RAY picks: " + ", ".join(
                            f"{s.symbol}(RR={s.rr_ratio:.1f},C={s.confluence_score})"
                            for s in quality_setups[:3]
                        )
                except Exception as e:
                    # Phase 14 (P1-13) — was silent. Coaching text still
                    # works without the X-RAY picks block; log so the
                    # missing picks are diagnosable.
                    log.warning(f"Suppressed: {e} (X-RAY picks for L2 coaching)")
            recovery_tag = f" Recovery stage {self._recovery_stage}/2." if self._recovery_stage > 0 else ""
            lines.append(
                f"  RISK MANAGEMENT MODE. Max {self._l2_max_pos} positions, leverage {self._l2_max_lev}x. "
                f"Quality-gate: A+/A setups only with confluence>={self._l2_min_confluence} and RR>={self._l2_min_rr}. "
                f"ANY coin is allowed IF X-RAY structural quality is A+/A.{recovery_tag}"
                f"{top_picks}"
            )

        if self._per_coin:
            best = max(self._per_coin.items(), key=lambda x: x[1]["pnl"])
            worst = min(self._per_coin.items(), key=lambda x: x[1]["pnl"])
            lines.append(f"  Best coin: {best[0]} ({best[1]['pnl']:+.2f}%)")
            lines.append(f"  Worst coin: {worst[0]} ({worst[1]['pnl']:+.2f}%)")

        buy_wr = self._per_direction["Buy"]["wins"] / max(
            self._per_direction["Buy"]["wins"] + self._per_direction["Buy"]["losses"], 1
        )
        sell_wr = self._per_direction["Sell"]["wins"] / max(
            self._per_direction["Sell"]["wins"] + self._per_direction["Sell"]["losses"], 1
        )
        lines.append(f"  Buy win rate: {buy_wr:.0%} | Sell win rate: {sell_wr:.0%}")

        if not self._check_heartbeat():
            lines.append("  WARNING: Claude heartbeat stale (>10min since last call)")

        return "\n".join(lines)

    # ─── Callbacks (same interface as before) ───

    def on_signal_generated(self) -> None:
        pass

    def on_setup_sent_to_brain(self) -> None:
        pass

    def on_trade_executed(self) -> None:
        # No in-memory increment — _collect_stats() is authoritative from DB.
        # Incrementing here would cause double-counting until the next stats cycle.
        pass

    def on_trade_closed(self, pnl_pct: float, was_win: bool) -> None:
        # Update streak immediately so it's fresh between collect_stats cycles.
        # _collect_stats() will overwrite with DB-authoritative values on next tick.
        if was_win:
            self._streak = max(1, self._streak + 1) if self._streak >= 0 else 1
        else:
            self._streak = min(-1, self._streak - 1) if self._streak <= 0 else -1
        self._profit_today_pct += pnl_pct

        # Track for recovery path
        self._recent_results.append(was_win)
        if len(self._recent_results) > 10:
            self._recent_results = self._recent_results[-10:]
        self._check_recovery()

        log.info(
            f"ENFORCER_TRADE_IN | pnl={pnl_pct:+.2f} | win={'Y' if was_win else 'N'} "
            f"| strk={self._streak} | recovery={self._recovery_stage} | {ctx()}"
        )

    def get_urgency_level(self) -> int:
        """Calculate urgency from PnL conditions."""
        if self._profit_today_pct < self._pnl_survival_pct:
            return 2
        elif self._profit_today_pct < self._pnl_caution_pct:
            return 1
        return 0

    def get_status(self) -> dict:
        now = now_utc()
        return {
            "trades_today": self._trades_today,
            "wins": self._wins_today,
            "losses": self._losses_today,
            "win_rate": self._wins_today / max(self._trades_today, 1),
            "profit_today_pct": self._profit_today_pct,
            "streak": self._streak,
            "urgency": self.get_urgency_level(),
            "minutes_into_hour": now.minute,
            "heartbeat_ok": self._check_heartbeat(),
            "per_coin": self._per_coin,
            "per_direction": self._per_direction,
            "enforcement_level": self._enforcement_level,
            "size_multiplier": self.get_size_multiplier(),
            "max_positions": self.get_max_positions_override(),
            "min_score": self.get_min_score_override(),
            "reset_grace_until": self._reset_grace_until,
        }

    def reset(self) -> None:
        """Manually reset enforcement level to 0 (NORMAL).

        Sets a grace period during which check_and_enforce() skips entirely —
        no recalculation, no level changes. This is necessary because DB stats
        are unchanged after reset and would re-trigger enforcement immediately.
        """
        prev_level = self._enforcement_level
        self._enforcement_level = 0
        self._level_changed_at = time.time()
        self._reset_grace_until = time.time() + (self._grace_period_minutes * 60)
        log.warning(
            f"ENFORCER_MANUAL_RESET | prev_el={prev_level} | new_el=0 "
            f"| grace_min={self._grace_period_minutes} | {ctx()}"
        )
