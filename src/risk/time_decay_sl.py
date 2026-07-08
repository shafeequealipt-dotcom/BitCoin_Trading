"""Loser-Lane Time-Decay SL — 5-model institutional exit intelligence.

Pure math. Stateless calculator + per-symbol state dataclass. The watchdog
owns IO (volatility profile fetch, regime fetch, push_sl_to_shadow, close).

Combined formula (applied multiplicatively per spec):

    allowed_loss = (
        atr_room                 # Model 2: base room (volatility-scaled, 2 × ATR)
        * time_factor            # Model 1: convex decay  (1 - (age/max)**1.5)
        * recovery_multiplier    # Model 3: MAE recovery bonus / stagnation penalty
        * momentum_multiplier    # Model 4: (velocity, accel) 4-case switch
        * probability_multiplier # Model 5: Bayesian p_win threshold multiplier
    )
    allowed_loss = max(allowed_loss, MIN_ALLOWED_LOSS_PCT)   # 0.15% floor
    allowed_loss = min(allowed_loss, original_sl_pct)        # never widen SL

Bayesian p_win (Model 5):
    prior:   p_win = 0.40 + regime_confidence × 0.25
    updates: *= 0.85 if 1 ATR deeper this tick
             *= 0.70 if 2 ATR deeper this tick
             *= 1.15 if recovered 50%+ of MAE
             *= 1.05 if regime still supports trade
             *= 0.60 if regime reversed
             clamp to [0.05, 0.95]
    force-close: p_win < P_WIN_FORCE_CLOSE (0.25) → return -1.0

Conventions:
    mae_pct stored as NEGATIVE (actual PnL value at worst point).
    current_pnl_pct is NEGATIVE for losers.
    allowed_loss is POSITIVE magnitude (% distance from entry).
    SL price derived: entry × (1 - allowed/100) for Buy, (1 + allowed/100) for Sell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time as _wall_time
from typing import Optional

from src.core.logging import get_logger
from src.core.log_context import ctx

log = get_logger("time_decay_sl")


@dataclass
class TimeDecayState:
    """Per-position mutable state for the Time-Decay loser lane."""
    symbol: str
    direction: str                                # "Buy" | "Sell"
    entry_price: float
    original_sl_pct: float                        # abs% distance from entry at open (positive)
    max_hold_seconds: int
    atr_5m_pct: float
    regime_confidence: float                      # 0.0-1.0, used only in p_win prior

    # Running metrics (mutated each tick)
    mae_pct: float = 0.0                          # NEGATIVE value at worst PnL (spec convention)
    last_pnl_pct: float = 0.0                     # CURRENT tick's pnl (updated by observe)
    prev_pnl_pct: float = 0.0                     # PREVIOUS tick's pnl (captured by observe
                                                  # before last_pnl_pct is overwritten;
                                                  # consumed by _update_p_win to detect
                                                  # whether the loss deepened this tick)
    prev_velocity: float = 0.0
    tick_seconds: float = 5.0
    p_win: float = 0.5
    last_allowed_loss: float = float("inf")       # positive magnitude, tighter-only guard
    last_sl_sent: float = 0.0
    tick_count: int = 0
    created_at: float = field(default_factory=_wall_time)
    # Volatility class (dead/low/medium/high/extreme) from the profiler. Used
    # for per-class grace_seconds and atr_room_multiplier lookups in
    # TimeDecayConfig. None falls through to the flat ("medium") defaults —
    # exactly pre-fix behaviour.
    volatility_class: Optional[str] = None

    # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # entry-time XRAY/regime anchors. The watchdog seeds these in
    # `_handle_time_decay`'s lazy-init from `TradeCoordinator.TradeState`
    # first and falls back to the `trade_thesis` v27 columns when
    # state was lost across a watchdog restart for an in-flight position.
    # Read each tick to compute structural invalidation; never mutated
    # after init. `entry_xray_confidence <= 0` is the sentinel for "no
    # anchor available" → fail-safe block (no force-close).
    entry_xray_confidence: float = 0.0
    entry_setup_type: str = ""
    entry_regime_at_open: str = ""
    entry_regime_confidence: float = 0.0

    # Item 2.4 (C5/F13) — close-reason split. The calculator returns its -1.0
    # force-close sentinel ONLY on a win-probability cut (p_win <
    # p_win_force_close). Booking that under "time_decay_force_close" made it
    # look like a deadline force-close and conflated legitimate near-certain-
    # loser cuts with true deadline-bleed in the leak attribution. The
    # calculator stamps the precise reason here just before returning -1.0; the
    # watchdog reads it to book a truthful, distinct close reason. Observability
    # only — it does not change WHAT is cut. "win_prob_near_certain" = the clear
    # bleeder caught by the H1 carve-out (p_win <= near_certain_loser_p_win);
    # "win_prob_force_close" = a p_win cut at the main threshold with structural
    # evidence.
    force_close_reason: str = ""

    # PF/LC Top-15 Problem 3.1 — win-prob over-cut smoothing state.
    # regime_mismatch_streak counts consecutive not-supporting ticks so the
    # regime penalty can be edge-triggered (applied only on a SUSTAINED mismatch,
    # not a single 10s flicker). recent_pnl is a short bounded PnL history the
    # recovery guard reads to tell a genuinely-recovering trade from a stuck one.
    regime_mismatch_streak: int = 0
    recent_pnl: list = field(default_factory=list)

    # F4/F4b/F7 (2026-06-09) — sustained-near-trough stall streak for the
    # standalone monotonic-grind cut. Counts CONSECUTIVE ticks the trade has been
    # pinned within `monotonic_grind_near_trough_band_pct` of its running MAE (a
    # dead stall at the worst excursion, no bounce); any tick that climbs out of
    # the band resets it to 0. Offline validation on the 2026-06-08 DOGE-vs-BLUR
    # tape proved this — NOT a new-low fraction (which is ~0.20 for grinders AND
    # recoverers alike at 10 s resolution and does not discriminate) — is the only
    # feature that separates a dying grind (pinned at the floor for minutes, then
    # closes there) from a recovering dip (bounces out of the band, resetting the
    # streak, then recovers). Written ONLY in calculate(), right after the
    # MAE-monotonic assignment, so it is live when the grind-cut check reads it.
    near_trough_streak: int = 0


@dataclass(frozen=True)
class TimeDecayConfig:
    """Frozen parameter bundle. Populated from settings.time_decay."""
    # Model 1 — convex time decay
    time_decay_exponent: float = 1.5

    # Model 2 — ATR-scaled room (base multiplier + per-class override table).
    # Per-class values let dead/low coins run tighter (atr × 1.0-1.2) and
    # high/extreme coins run wider (atr × 2.5-3.0). Empty dict → flat
    # atr_room_multiplier applies everywhere (pre-fix behaviour).
    atr_room_multiplier: float = 2.0
    atr_room_multiplier_by_class: dict = field(default_factory=dict)

    # Model 3 — MAE recovery multiplier
    mae_recovery_threshold: float = 0.5           # recovery > 0.5 → bonus
    mae_stagnation_threshold: float = 0.2         # recovery < 0.2 → penalty
    mae_bonus: float = 1.2
    mae_penalty: float = 0.8
    # Issue 3 (2026-06-08) — recovery-responsive tightening (defaults kept in
    # sync with TimeDecaySettings). On a STRONG recovery from the worst MAE,
    # tighten the stop toward the recovered level (bounce-capture near the least
    # loss) instead of holding the wide budget set during the worst dip; a buffer
    # below current price keeps a still-running recovery from being cut.
    mae_recovery_tighten_enabled: bool = True
    mae_tightening_recovery_threshold: float = 0.75
    recovery_tightening_buffer_pct: float = 0.3

    # Model 4 — velocity/acceleration 4-case switch
    momentum_danger: float = 0.7                  # vel<0 & accel<0
    momentum_favorable: float = 1.3               # vel>0 & accel>0
    momentum_slow_fall: float = 0.9               # vel<0 & accel>0
    momentum_slow_rise: float = 1.1               # vel>0 & accel<0

    # Model 5 — Bayesian p_win (prior + update + threshold multipliers)
    # Bug 3 fix (2026-04-23): defaults kept in sync with TimeDecaySettings
    # so direct construction (tests, one-off CLI) matches runtime. The
    # watchdog populates every field from settings.time_decay at boot.
    p_win_prior_base: float = 0.55
    p_win_prior_regime_weight: float = 0.25       # prior = base + regime_conf × weight
    p_win_force_close: float = 0.15
    # H1 (2026-05-30) — near-certain-loser carve-out. When p_win is at/below
    # this (a position the model itself rates near-certain to lose), the
    # structural-invalidation guard YIELDS so the force-close fires instead of
    # holding a clear bleeder until it stops out. Scoped strictly to this band;
    # positions with higher p_win keep the guard. Must be <= p_win_force_close
    # to take effect.
    near_certain_loser_p_win: float = 0.10
    # PF/LC Top-15 Problem 2.3 — age-aware near-certain-loser threshold. The
    # carve-out yields (cuts) only at p_win <= near_certain_loser_p_win, so the
    # (threshold, p_win_force_close] band with stable structure was held to the
    # stop regardless of age. The blueprint's time-dial intent is that the
    # win-prob cut tightens with age. When winprob_age_aware_band_enabled is
    # True, the effective yield threshold rises from _young to _old once the
    # trade is older than age_threshold_to_raise_p_win_seconds; both stay
    # <= p_win_force_close. Default off → the single near_certain_loser_p_win is
    # used (current behaviour).
    winprob_age_aware_band_enabled: bool = False
    near_certain_loser_p_win_young: float = 0.10
    near_certain_loser_p_win_old: float = 0.13
    age_threshold_to_raise_p_win_seconds: float = 600.0
    p_win_tight: float = 0.40
    p_win_loose: float = 0.60
    p_win_tight_mult: float = 0.7
    p_win_loose_mult: float = 1.2

    # p_win update-rule factors
    p_win_atr1_penalty: float = 0.85              # 1 ATR deeper this tick
    p_win_atr2_penalty: float = 0.70              # 2 ATR deeper this tick
    p_win_recovery_bonus: float = 1.15            # recovered 50%+ of MAE
    p_win_regime_bonus: float = 1.05
    p_win_regime_penalty: float = 0.60
    p_win_min: float = 0.05
    p_win_max: float = 0.95
    # PF/LC Top-15 Problem 3.1 — win-probability over-cut smoothing (the biggest
    # controllable exit lever). The regime multiplier applied p_win_regime_penalty
    # (0.60) UNCONDITIONALLY every tick on a single regime flicker, even at flat
    # price, collapsing p_win toward the near-certain-loser band and cutting
    # trades that were recovering. Do NOT remove the exit (it still halves the
    # loss vs riding to the stop) — smooth it. smooth_p_win_enabled is the master
    # switch (default off; ships inert). When on:
    #   - edge-triggered regime penalty: apply the penalty only after a SUSTAINED
    #     mismatch of p_win_regime_penalty_sustained_ticks consecutive ticks, so
    #     a single flicker no longer halves p_win;
    #   - recovery guard: do not force-close while the trade is within
    #     p_win_recovery_guard_be_band_pct of breakeven AND making a new local
    #     high over the last p_win_recovery_guard_n_ticks ticks (unless the
    #     watchdog has real structural-invalidation evidence — then the cut stands).
    smooth_p_win_enabled: bool = False
    p_win_regime_edge_trigger_enabled: bool = True
    p_win_regime_penalty_sustained_ticks: int = 3
    p_win_recovery_guard_enabled: bool = True
    p_win_recovery_guard_be_band_pct: float = 0.5
    p_win_recovery_guard_n_ticks: int = 3

    # Safety — per-class grace table overrides the flat grace_seconds when
    # state.volatility_class matches a key. Slow bleeders (dead/low) act
    # sooner; fast movers (high/extreme) get more settling room. Empty dict
    # preserves flat grace behaviour.
    grace_seconds: int = 120
    grace_seconds_by_class: dict = field(default_factory=dict)
    min_allowed_loss_pct: float = 0.15

    # Phase 1 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # minimum-age guardrail. Independent of and stricter than the per-class
    # `grace_seconds` (30-240 s). When `position_age_seconds < min_age_seconds`,
    # `calculate()` returns None unconditionally — both the force-close
    # sentinel and the tighter-SL push are suppressed for that tick.
    # Default mirrors `settings.watchdog.strategic_action_min_hold_seconds=300`
    # so the calculator-side bypass to position_service.close_position()
    # at watchdog:977 is gated by the same age policy that the strategic-
    # action queue at watchdog:2410 already enforces. Zero disables.
    min_age_seconds: float = 300.0

    # Phase 2 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # MAE-relative-to-SL gate. Force-close is suppressed when the worst
    # PnL drawdown (state.mae_pct) is less than this fraction of the
    # original SL distance (state.original_sl_pct). At the default 0.5,
    # a position must have drawn down at least half of its original SL
    # before the calculator can force-close it. Below that threshold the
    # trade is still in normal-development territory — killing it is
    # killing on noise. Symmetric: returns None (suppresses both
    # force-close AND SL-tighten this tick). Zero disables.
    mae_to_sl_ratio_threshold: float = 0.5

    # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
    # structural-invalidation gate. When True, force-close is permitted
    # ONLY if the watchdog computed structural_invalidation=True (real
    # evidence: XRAY confidence dropped >= xray_drop_threshold from
    # entry, OR setup-type drifted, OR regime inverted at >= regime_
    # inversion_confidence_threshold). When False, the gate is a no-op
    # and the calculator falls through to the existing `p_win <
    # p_win_force_close` test alone — preserves pre-fix behaviour for
    # back-compat / debugging. Symmetric: returns None when blocked.
    structural_invalidation_required: bool = True
    # Issue 2.6 (2026-06-07): slow-bleed cumulative-drawdown carve-out. When a
    # trade is statistically dead (p_win < p_win_force_close) AND has bled to a
    # large cumulative loss (current pnl_pct <= -slow_bleed_cumulative_loss_pct),
    # the structural-invalidation guard YIELDS so the force-close fires — catches
    # the slow structureless grind the guard otherwise holds to the plain stop.
    # A recovering winner has a higher p_win, so the low-p_win AND large-loss
    # combination is a clearly-losing trade, not a winner-in-progress. Default
    # OFF pending the offline check + live observation.
    slow_bleed_cumulative_force_close_enabled: bool = False
    slow_bleed_cumulative_loss_pct: float = 2.5
    # F4/F4b/F7 (2026-06-09) — standalone monotonic-grind force-close (the
    # DOGE-grind lever). A p_win-INDEPENDENT cut for a trade that has stalled at
    # its worst excursion for a sustained run with no bounce and crossed a real
    # loss floor — the dying slow grind the MAE-to-SL gate (0.50) never reaches
    # because a low-volatility coin's grind keeps its drawdown under half its SL
    # distance (the F4 root: p_win freezes and the MAE gate never trips, so the
    # trade rides to its stop). Fires AFTER the min-age guard (respects min-hold)
    # and BEFORE the MAE-to-SL gate. Independent of p_win so it does NOT couple to
    # the p_win unfreeze (Part A, deliberately held as a separate later step).
    # Default OFF; offline-validated net-positive (+$10.99) and ZERO-strangle
    # across the whole 2026-06-08 tape (every recovering dip spared — BLUR, HBAR,
    # IMX, NEAR, AERO, EGLD, ONDO) before any enable. The discriminator is a
    # sustained-near-trough STALL streak, not a new-low fraction: see
    # TimeDecayState.near_trough_streak. Thresholds are deliberately conservative
    # — they catch only the cleanest grinds (pinned within 0.05% of the trough for
    # ~4 minutes) so NO recoverer is strangled; catching every grinder is provably
    # impossible without strangling a recoverer at these loss depths.
    monotonic_grind_cut_enabled: bool = False
    monotonic_grind_near_trough_band_pct: float = 0.05   # within this % of MAE = "pinned at trough"
    monotonic_grind_sustained_ticks: int = 24            # consecutive pinned ticks (~10 s each → ~4 min)
    monotonic_grind_max_recovery_ratio: float = 0.20     # (pnl-mae)/|mae|; above this = bouncing → spare
    monotonic_grind_min_loss_pct: float = 0.30           # require a real loss has been crossed
    xray_drop_threshold: float = 0.40
    regime_inversion_confidence_threshold: float = 0.60
    # Phase 11 (P1-10c) — price-relative floor that aligns TD's output
    # with the SL Gateway's R2 min-distance check. When > 0, any computed
    # SL whose distance from the derived current_price falls below this
    # percentage is skipped (not pushed) — preventing the gateway from
    # rejecting every TD push as too-tight. Default 0.0 keeps the
    # pre-Phase11 behaviour for back-compat; the position_watchdog setter
    # configures it from settings.sl_gateway.min_distance_pct so the
    # operational default tracks gateway config.
    min_price_relative_distance_pct: float = 0.0

    # Absolute-PnL-depth penalty (Bayesian p_win update). ATR-relative
    # penalties don't fire for slow bleeders (a dead coin deepening
    # 0.02%/tick at -2% PnL is <1 ATR deepening per tick). Absolute depth
    # catches that case. Compounds with the ATR-relative penalties.
    p_win_abs_depth_threshold_pct: float = 1.5   # |pnl| > 1.5% → mild
    p_win_abs_depth_strong_pct: float = 3.0      # |pnl| > 3.0% → strong
    p_win_abs_depth_penalty: float = 0.90
    p_win_abs_depth_strong_penalty: float = 0.70

    # Observability
    # Phase 10 (logging overhaul): dataclass default dropped to 1 to guarantee
    # every TIME_DECAY_CALC is logged even when TimeDecaySLCalculator is
    # instantiated without the Settings-derived override. Revert to 10 once
    # per-tick flow is stable.
    log_every_n_ticks: int = 1


class TimeDecaySLCalculator:
    """Stateless 5-model calculator. Reads/writes a TimeDecayState per call.

    Usage:
        calc = TimeDecaySLCalculator(TimeDecayConfig(...))
        state = calc.create_state(symbol=..., ...)
        # ... each tick ...
        velocity, accel = observe(state, current_pnl_pct)
        outcome = calc.calculate(state, current_pnl_pct=..., ...)
        # outcome is None (no-op), -1.0 (force-close), or a float SL price.
    """

    def __init__(self, config: TimeDecayConfig | None = None) -> None:
        self.cfg = config or TimeDecayConfig()

    # ─── Public API ───────────────────────────────────────────────────

    def create_state(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: float,
        original_sl_pct: float,
        max_hold_seconds: int,
        atr_5m_pct: float,
        regime_confidence: float,
        volatility_class: Optional[str] = None,
        tick_seconds: float = 5.0,
        # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
        # entry-time XRAY/regime anchors. Optional with neutral defaults
        # so test fixtures and any caller pre-dating Phase 3 keep working;
        # the watchdog populates them at lazy-init from TradeState /
        # trade_thesis. `entry_xray_confidence <= 0.0` is the sentinel
        # for "no anchor" → fail-safe block in the structural-invalidation
        # gate.
        entry_xray_confidence: float = 0.0,
        entry_setup_type: str = "",
        entry_regime_at_open: str = "",
        entry_regime_confidence: float = 0.0,
        # T1-2 (2026-05-12): cross-recreation MAE preservation. The
        # watchdog snapshots state.mae_pct into _td_mae_high_water before
        # each _td_states deletion (force-close finally, profit handoff,
        # stale-symbol cleanup) and passes the snapshot back here on
        # lazy re-init. Default 0.0 → first-creation case (no history),
        # bypasses the seeding branch.
        prior_mae_pct: float = 0.0,
    ) -> TimeDecayState:
        """Initialize state for a new losing position.

        Sets the Bayesian prior per spec: p_win = 0.40 + regime_confidence × 0.25.

        ``volatility_class`` (dead/low/medium/high/extreme) is stored on the
        state so per-tick calculations (grace window, atr_room multiplier)
        can scale by the coin's class without re-fetching the profile.

        ``entry_xray_confidence``, ``entry_setup_type``, ``entry_regime_at_open``,
        ``entry_regime_confidence`` (Phase 3, 2026-05-06) — entry-time
        anchors used by the Phase 3 structural-invalidation gate. Captured
        once at first-loser-tick and never mutated thereafter.

        ``prior_mae_pct`` (T1-2, 2026-05-12) — MAE high-water-mark inherited
        from a prior incarnation of this position's state. Negative values
        seed the new state's mae_pct via _assign_mae_monotonic so the
        worst-PnL excursion seen across a destroy/recreate cycle is not
        lost. Production logs (2026-05-12) showed 56 of 159 HANDOFF→INIT
        round-trips lost MAE history pre-fix (top: INJUSDT lost -0.68%).
        Default 0.0 (or any value >= 0) is treated as "no history" and
        leaves the state's default mae_pct untouched.
        """
        prior = self.cfg.p_win_prior_base + (
            regime_confidence * self.cfg.p_win_prior_regime_weight
        )
        prior = max(self.cfg.p_win_min, min(self.cfg.p_win_max, prior))

        state = TimeDecayState(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            original_sl_pct=max(original_sl_pct, self.cfg.min_allowed_loss_pct),
            max_hold_seconds=max(max_hold_seconds, 60),
            atr_5m_pct=max(atr_5m_pct, 0.05),
            regime_confidence=regime_confidence,
            tick_seconds=tick_seconds,
            p_win=prior,
            volatility_class=volatility_class,
            entry_xray_confidence=entry_xray_confidence,
            entry_setup_type=entry_setup_type,
            entry_regime_at_open=entry_regime_at_open,
            entry_regime_confidence=entry_regime_confidence,
        )
        # T1-2: seed the high-water mark via the monotonic helper so the
        # assignment is observable and the strict-min contract is honoured
        # even on the seeding path.
        if prior_mae_pct < 0.0:
            self._assign_mae_monotonic(
                state, candidate=prior_mae_pct, source="init_seed",
            )
        return state

    def calculate(
        self,
        state: TimeDecayState,
        *,
        current_pnl_pct: float,
        position_age_seconds: float,
        regime_still_supports: bool,
        velocity_pct_per_s: float,
        acceleration_pct_per_s2: float,
        structural_invalidation: bool,
        invalidation_reason: str,
    ) -> Optional[float]:
        """Run the 5-model pipeline and return a tighter-only SL price.

        Returns:
            float > 0  — new SL price to push (tighter than previous).
            -1.0       — force-close sentinel (p_win < p_win_force_close).
            None       — no-op (grace period, Phase 1/2/3 guardrail blocked,
                          or not tighter than last push).

        Caller contracts:
          - Only invoke when pnl_pct < 0 (loser lane).
          - Run ``observe(state, pnl_pct)`` before calling this to keep
            velocity/acceleration history on the state in sync.
          - Phase 3 (2026-05-06): caller (PositionWatchdog) MUST compute
            ``structural_invalidation`` (and ``invalidation_reason`` for
            logs) before invoking. Required keyword-only — defaults are
            intentionally absent so a future caller that forgets fails
            loud instead of silently disabling the gate. See
            ``PositionWatchdog._compute_structural_invalidation``.
        """
        state.tick_count += 1

        # Grace period — per-class so slow-bleeder coins (dead/low) act
        # sooner and fast movers get more settling room. Falls back to the
        # flat grace_seconds when class is unknown or not in the map.
        _cls = state.volatility_class or "medium"
        _grace = self.cfg.grace_seconds_by_class.get(_cls, self.cfg.grace_seconds)
        if position_age_seconds < _grace:
            # Phase 10: surface the silent skip so investigators can tell
            # "no TIME_DECAY_CALC yet" apart from "the gate never fired".
            log.debug(
                f"TIME_DECAY_GRACE | sym={state.symbol} age={position_age_seconds:.0f}s "
                f"grace={_grace}s cls={_cls} tick={state.tick_count} | {ctx()}"
            )
            return None

        # T1-1 fix (2026-05-12): Track Max Adverse Excursion BEFORE the
        # AGE_GUARD early-return. Pre-fix, MAE-tracking lived after the
        # AGE_GUARD return at line 349, so worst-PnL excursions during
        # the 0-300 s immunity window were silently discarded. When
        # AGE_GUARD then released, the first MAE_GUARD evaluation
        # initialised mae_pct from CURRENT pnl (not the dead-zone peak)
        # and Phase 2's MAE/SL ratio gate saw a fictitious shallow
        # mae_pct, blocking force-close on losing trades that had
        # actually drawn down further during immunity.
        # Empirical bug evidence (2026-05-12 logs):
        #   SAND 10:00:29 AGE_GUARD: age=296s pnl=-0.07% mae=+0.00%
        #   SAND 10:00:39 MAE_GUARD: mae=-0.07% (initialised from
        #     current pnl, NOT the deepest excursion across 5 minutes)
        # MAE *measurement* now runs every tick that passes the grace
        # window. MAE-driven *action* (force-close, SL-tighten) is still
        # gated by the AGE_GUARD block below — only the measurement
        # moves above the gate.
        # MAE stored as NEGATIVE, spec convention (cf. line 29 doc).
        # T1-2 (2026-05-12): assignment routed through the monotonic
        # helper so the strict-min contract is enforceable and any future
        # writer that attempts a regression is held + logged.
        prev_mae = state.mae_pct
        deepened = self._assign_mae_monotonic(
            state, candidate=current_pnl_pct, source="live_tick",
        )
        if deepened and (
            self.cfg.min_age_seconds > 0
            and position_age_seconds < self.cfg.min_age_seconds
        ):
            # Emit only when the deepening happened DURING the AGE_GUARD
            # window — that's the new path the T1-1 fix unlocks. Operators
            # can grep TIME_DECAY_MAE_INIT_FROM_PEAK to verify the dead
            # zone is no longer leaking history.
            log.info(
                f"TIME_DECAY_MAE_INIT_FROM_PEAK | sym={state.symbol} "
                f"mae={state.mae_pct:+.2f}% prev_mae={prev_mae:+.2f}% "
                f"age={position_age_seconds:.0f}s "
                f"source=age_guard_history | {ctx()}"
            )

        # F4/F4b/F7 (2026-06-09) — sustained-near-trough stall tracking
        # (p_win-INDEPENDENT). A tick is "pinned at the trough" when the current
        # PnL is within `monotonic_grind_near_trough_band_pct` of the running MAE
        # (no meaningful bounce off the worst point). A bounce out of the band
        # resets the streak. Tracked EVERY tick regardless of the guards below so
        # the streak is live when the standalone monotonic-grind cut reads it.
        # `state.mae_pct` is the freshly-updated trough from the assignment above.
        # This is the discriminator the offline validation selected: a smooth
        # descent and a stall-at-bottom both look "near trough" instantaneously,
        # but only a trade that STAYS pinned (no bounce out of the band) for the
        # full sustained run is the dying grind — a recovering dip bounces out and
        # resets the streak long before the threshold is reached. Inert until
        # monotonic_grind_cut_enabled is set; the counter just advances.
        if state.mae_pct < 0 and (
            (current_pnl_pct - state.mae_pct)
            <= self.cfg.monotonic_grind_near_trough_band_pct
        ):
            state.near_trough_streak += 1
        else:
            state.near_trough_streak = 0

        # Phase 1 — Minimum-age guardrail (Time-Decay Force-Close Fix, 2026-05-06).
        # Stricter than `grace_seconds` (30-240 s per class). Suppresses BOTH
        # force-close AND tighter-SL push for positions younger than
        # `min_age_seconds`. Plugs the bypass at watchdog:977 — the
        # `_execute_strategic_actions` minimum-hold guardrail at watchdog:2410
        # only covers strategic-action closes, not the calculator's `-1.0`
        # sentinel which calls position_service.close_position() directly.
        # By gating both return paths here, the time-decay path is held to
        # the same age policy as the CALL_B path. Zero disables.
        # T1-1 (2026-05-12): MAE tracking moved ABOVE this block so the
        # 0-300 s dead zone no longer leaks worst-PnL history. The
        # `mae=` field below now reflects the true peak adverse
        # excursion seen during the immunity window.
        if (
            self.cfg.min_age_seconds > 0
            and position_age_seconds < self.cfg.min_age_seconds
        ):
            # Observability G11 (noise reduction) — downgraded from
            # WARNING to INFO. This is normal-operation gate behaviour
            # (position too young to consider time-decay action), not
            # an exceptional condition. Pre-G11 it fired 100x/1.5h at
            # WARNING, contributing to WARNING-tail noise that drowned
            # real warnings. The event itself is preserved; only the
            # severity classification changes.
            log.info(
                f"TIME_DECAY_AGE_GUARD | sym={state.symbol} "
                f"age={position_age_seconds:.0f}s min_age={self.cfg.min_age_seconds:.0f}s "
                f"pnl={current_pnl_pct:+.2f}% mae={state.mae_pct:+.2f}% "
                f"p_win={state.p_win:.3f} blocked=true | {ctx()}"
            )
            return None

        # F4/F4b/F7 (2026-06-09) — standalone monotonic-grind force-close.
        # p_win-INDEPENDENT and placed deliberately HERE: after the min-age guard
        # above (so it respects min-hold) and BEFORE the MAE-to-SL gate below
        # (which a slow low-volatility grind never trips, because its drawdown
        # stays under half the SL distance — that is the F4 freeze that lets a
        # dying grind ride to its stop). Fires only when ALL hold: the trade has
        # been pinned within the near-trough band for a sustained run
        # (near_trough_streak — a dead stall at the bottom with no bounce), the
        # current point is still essentially at the trough (recovery ratio under
        # the cap — a belt-and-suspenders guard that spares a dip that has bounced
        # out), and a real loss floor has been crossed. The recovery-ratio veto
        # emits TIME_DECAY_MONOTONIC_GRIND_SPARED so a saved recoverer is visible.
        # Offline-validated ZERO-strangle on the 2026-06-08 tape. Default OFF.
        if (
            self.cfg.monotonic_grind_cut_enabled
            and state.mae_pct < 0
            and state.near_trough_streak >= self.cfg.monotonic_grind_sustained_ticks
            and abs(current_pnl_pct) >= self.cfg.monotonic_grind_min_loss_pct
        ):
            _grind_recov_off_trough = (
                (current_pnl_pct - state.mae_pct) / abs(state.mae_pct)
            )
            if (
                _grind_recov_off_trough
                <= self.cfg.monotonic_grind_max_recovery_ratio
            ):
                log.warning(
                    f"TIME_DECAY_MONOTONIC_GRIND_CUT | sym={state.symbol} "
                    f"pnl={current_pnl_pct:+.2f}% mae={state.mae_pct:+.2f}% "
                    f"streak={state.near_trough_streak} "
                    f"sustained_req={self.cfg.monotonic_grind_sustained_ticks} "
                    f"recov_off_trough={_grind_recov_off_trough:.2f} "
                    f"band={self.cfg.monotonic_grind_near_trough_band_pct:.2f}% "
                    f"floor={self.cfg.monotonic_grind_min_loss_pct:.2f}% "
                    f"age_s={position_age_seconds:.0f} p_win={state.p_win:.3f} "
                    f"action=cut_dying_grind | dead stall at the trough, no "
                    f"recovery — cutting the slow grind | {ctx()}"
                )
                # Item 2.4-style truthful close reason: distinct from the p_win
                # cuts so the grind cut is separable in leak attribution.
                state.force_close_reason = "monotonic_grind_cut"
                return -1.0
            else:
                # The stall streak is long but the trade has climbed off its
                # trough — a recovering dip (the BLUR / HBAR / IMX case). Spare
                # it; emit so the operator can see the veto saving a recoverer.
                log.info(
                    f"TIME_DECAY_MONOTONIC_GRIND_SPARED | sym={state.symbol} "
                    f"pnl={current_pnl_pct:+.2f}% mae={state.mae_pct:+.2f}% "
                    f"streak={state.near_trough_streak} "
                    f"recov_off_trough={_grind_recov_off_trough:.2f} "
                    f"max_recov={self.cfg.monotonic_grind_max_recovery_ratio:.2f} "
                    f"reason=recovering_off_trough | {ctx()}"
                )

        # Phase 2 — MAE-relative-to-SL gate (Time-Decay Force-Close Fix, 2026-05-06).
        # Suppresses BOTH force-close AND tighter-SL push when the worst
        # drawdown to date has not reached `mae_to_sl_ratio_threshold` of
        # `state.original_sl_pct`. The original SL exists precisely to
        # define the structural-invalidation point; until the position
        # approaches that point, the trade is still in normal-development
        # territory. `state.original_sl_pct` is floored to
        # `min_allowed_loss_pct=0.15` in `create_state()`, so the
        # ratio computation is structurally safe. Zero threshold disables.
        if (
            self.cfg.mae_to_sl_ratio_threshold > 0
            and state.original_sl_pct > 0
        ):
            mae_ratio = abs(state.mae_pct) / state.original_sl_pct
            if mae_ratio < self.cfg.mae_to_sl_ratio_threshold:
                # Observability G11 — downgraded WARNING→INFO. Same
                # rationale as TIME_DECAY_AGE_GUARD: normal-operation
                # gate behaviour (MAE hasn't reached the threshold for
                # time-decay action), not an exceptional condition.
                # 254x/1.5h at WARNING pre-G11.
                log.info(
                    f"TIME_DECAY_MAE_GUARD | sym={state.symbol} "
                    f"mae={state.mae_pct:+.2f}% sl_dist={state.original_sl_pct:.2f}% "
                    f"ratio={mae_ratio:.2f} threshold={self.cfg.mae_to_sl_ratio_threshold:.2f} "
                    f"p_win={state.p_win:.3f} blocked=true | {ctx()}"
                )
                return None

        # Update Bayesian p_win from observable price action + regime
        self._update_p_win(
            state,
            current_pnl_pct=current_pnl_pct,
            regime_still_supports=regime_still_supports,
        )

        # Phase 3 — Structural-invalidation gate (Time-Decay Force-Close
        # Definitive Fix, 2026-05-06). When `structural_invalidation_required`
        # is True (default), the calculator force-closes ONLY when the
        # caller has computed structural_invalidation=True (real evidence:
        # XRAY confidence drop, setup-type drift, or regime inversion).
        # This prevents the calculator from killing trades on early-life
        # noise just because p_win has decayed below threshold without
        # a real failure signal. Symmetric early-return: returns None to
        # block both the force-close sentinel AND the SL-tighten branch
        # for this tick — the position runs on its existing SL until the
        # next tick re-evaluates. See PositionWatchdog._compute_structural_
        # invalidation for the caller-side criteria.
        # PF/LC Top-15 Problem 2.3 — the effective near-certain-loser threshold,
        # computed UNCONDITIONALLY here so both the carve-out yield below AND the
        # close-reason label at the force-close sentinel use the same value (it
        # would be unbound at the label site otherwise, on the structural-evidence
        # path). When age-aware is enabled it rises from young to old past the age
        # threshold; off, it is the single near_certain_loser_p_win (unchanged).
        if self.cfg.winprob_age_aware_band_enabled:
            _eff_ncl = (
                self.cfg.near_certain_loser_p_win_old
                if position_age_seconds >= self.cfg.age_threshold_to_raise_p_win_seconds
                else self.cfg.near_certain_loser_p_win_young
            )
        else:
            _eff_ncl = self.cfg.near_certain_loser_p_win
        if (
            self.cfg.structural_invalidation_required
            and state.p_win < self.cfg.p_win_force_close
            and not structural_invalidation
        ):
            # H1 (2026-05-30) — near-certain-loser carve-out. The guard's
            # caution is right for AMBIGUOUS positions but wrong for one the
            # model itself rates near-certain to lose. When p_win is at/below
            # near_certain_loser_p_win the guard YIELDS (does not block), so the
            # force-close sentinel below fires and the clear bleeder is cut.
            # Scoped strictly to this band — positions with higher p_win keep
            # the guard. The -3% hard stop, loser timeout, SENTINEL tiers, the
            # 2.5% safety stop and the min-age / MAE gates above are unchanged,
            # and healthy positions are never affected.
            # PF/LC Top-15 Problem 2.3 — the age-aware yield threshold _eff_ncl is
            # computed unconditionally above (so the close-reason label can reuse
            # it). When the trade is in the (young, old] band and aged past the
            # threshold it yields here instead of being held to the stop.
            if state.p_win <= _eff_ncl:
                log.warning(
                    f"TIME_DECAY_STRUCT_GUARD_YIELD | sym={state.symbol} "
                    f"p_win={state.p_win:.3f} "
                    f"threshold={_eff_ncl:.3f} "
                    f"age_s={position_age_seconds:.0f} "
                    f"age_aware={self.cfg.winprob_age_aware_band_enabled} "
                    f"pnl={current_pnl_pct:+.2f}% mae={state.mae_pct:+.2f}% "
                    f"reason='{invalidation_reason or 'stable'}' "
                    f"action=cut_near_certain_loser | {ctx()}"
                )
                # fall through to the force-close sentinel below
            elif (
                self.cfg.slow_bleed_cumulative_force_close_enabled
                and current_pnl_pct <= -self.cfg.slow_bleed_cumulative_loss_pct
            ):
                # Issue 2.6 (2026-06-07): slow-bleed cumulative-drawdown carve-out.
                # The trade is below p_win_force_close (statistically dead) AND has
                # bled past the cumulative-loss threshold — a clearly-losing slow
                # grind the structural guard would otherwise hold to the plain
                # stop. Yield so the force-close sentinel fires. A recovering
                # winner carries a higher p_win, so this band is not a winner.
                log.warning(
                    f"TIME_DECAY_SLOW_BLEED_CUT | sym={state.symbol} "
                    f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
                    f"loss_thresh=-{self.cfg.slow_bleed_cumulative_loss_pct:.2f}% "
                    f"mae={state.mae_pct:+.2f}% age_s={position_age_seconds:.0f} "
                    f"reason='{invalidation_reason or 'stable'}' "
                    f"action=cut_slow_bleed | structural guard yielded on a "
                    f"clearly-losing slow grind | {ctx()}"
                )
                # fall through to the force-close sentinel below
            else:
                log.warning(
                    f"TIME_DECAY_STRUCT_GUARD | sym={state.symbol} "
                    f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
                    f"mae={state.mae_pct:+.2f}% "
                    f"entry_xray={state.entry_xray_confidence:.2f} "
                    f"entry_setup={state.entry_setup_type or '-'} "
                    f"entry_regime={state.entry_regime_at_open or '-'} "
                    f"reason='{invalidation_reason or 'stable'}' "
                    f"blocked=true | {ctx()}"
                )
                return None

        # Force-close sentinel: trade is statistically dead
        if state.p_win < self.cfg.p_win_force_close:
            # PF/LC Top-15 Problem 3.1 — recovery guard. The p_win collapse can
            # cut a trade that is actually recovering. When smoothing is enabled,
            # HOLD this tick (do not force-close) if the trade is within the
            # breakeven band AND making a new local high over the last N ticks —
            # UNLESS the watchdog has real structural-invalidation evidence, in
            # which case the cut stands. Holding returns None: the position keeps
            # its existing SL and is re-evaluated next tick (the exit is not
            # removed, only deferred while it is genuinely recovering near BE).
            if (
                self.cfg.smooth_p_win_enabled
                and self.cfg.p_win_recovery_guard_enabled
                and not structural_invalidation
            ):
                _rg_n = max(1, int(self.cfg.p_win_recovery_guard_n_ticks))
                _prior = state.recent_pnl[-(_rg_n + 1):-1]
                _improving = (
                    len(_prior) >= _rg_n and current_pnl_pct >= max(_prior)
                )
                _near_be = current_pnl_pct >= -abs(
                    self.cfg.p_win_recovery_guard_be_band_pct
                )
                if _improving and _near_be:
                    log.warning(
                        f"TIME_DECAY_RECOVERY_GUARD | sym={state.symbol} "
                        f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
                        f"be_band={self.cfg.p_win_recovery_guard_be_band_pct:.2f}% "
                        f"n={_rg_n} prior_max={max(_prior):+.2f}% | recovering near "
                        f"breakeven, no structural invalidation — holding the cut "
                        f"this tick | {ctx()}"
                    )
                    return None
            # Layer 4 Realignment Phase 2 (2026-05-06) — full evidence
            # TRACE before force-close emission. Captures entry-vs-
            # current XRAY/setup/regime + structural_invalidation flag
            # + invalidation_reason so the operator can verify whether
            # every force-close was justified by real structural
            # evidence (Branch A: gate working as designed) or weak
            # evidence (Branch B: gate too permissive — schedule
            # threshold tightening). The TRACE event fires
            # UNCONDITIONALLY — even when structural_invalidation_
            # required=False (back-compat / debug mode) — so every
            # force-close has a forensic record.
            #
            # The ``invalidation_reason`` string is already structured
            # by ``Layer4ProtectionService.compute_structural_
            # invalidation`` (formerly PositionWatchdog._compute_
            # structural_invalidation): "xray_drop=0.42", "setup_drift
            # :bullish_fvg_ob->bearish_fvg_ob", "regime_inv:trending_
            # down@0.65", "stable", "no_data:xray_cache_miss". Splitting
            # on "," gives the operator a clean per-signal view.
            log.warning(
                f"TIME_DECAY_FORCE_CLOSE_TRACE | sym={state.symbol} "
                f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
                f"mae={state.mae_pct:+.2f}% "
                f"entry_xray={state.entry_xray_confidence:.2f} "
                f"entry_setup={state.entry_setup_type or '-'} "
                f"entry_regime={state.entry_regime_at_open or '-'} "
                f"entry_regime_conf={state.entry_regime_confidence:.2f} "
                f"struct_required={self.cfg.structural_invalidation_required} "
                f"struct_invalidation={structural_invalidation} "
                f"reason='{invalidation_reason or '-'}' | {ctx()}"
            )

            # Phase 3: paired observability — when the calculator does
            # force-close, the watchdog has already computed real
            # structural-invalidation evidence (or the gate is disabled
            # for back-compat). Emit a TIME_DECAY_STRUCT_INVALIDATED
            # info-line so the operator can pair every force-close with
            # its triggering reason in logs.
            if self.cfg.structural_invalidation_required:
                log.info(
                    f"TIME_DECAY_STRUCT_INVALIDATED | sym={state.symbol} "
                    f"p_win={state.p_win:.3f} "
                    f"entry_xray={state.entry_xray_confidence:.2f} "
                    f"entry_setup={state.entry_setup_type or '-'} "
                    f"entry_regime={state.entry_regime_at_open or '-'} "
                    f"reason='{invalidation_reason or 'unknown'}' "
                    f"proceed=true | {ctx()}"
                )
            log.warning(
                f"TIME_DECAY_FORCE_CLOSE | sym={state.symbol} "
                f"p_win={state.p_win:.3f} pnl={current_pnl_pct:+.2f}% "
                f"mae={state.mae_pct:+.2f}% | {ctx()}"
            )
            # Item 2.4 (C5/F13) — stamp the truthful close reason for the
            # watchdog to book. This sentinel is reached ONLY via p_win <
            # p_win_force_close, so every -1.0 here is a win-probability cut, not
            # a deadline force-close. Distinguish the clear near-certain-loser
            # (the H1 carve-out band) from a threshold-band p_win cut so the two
            # are separable in the leak attribution. Labeling only.
            # PF/LC Top-15 Problem 2.3 — label against the EFFECTIVE (age-aware)
            # near-certain threshold so an aged band cut via the carve-out yield
            # is attributed to the near-certain-loser bucket, not the structural-
            # evidence one. _eff_ncl == near_certain_loser_p_win when age-aware is
            # off, so the label is unchanged in the default case.
            state.force_close_reason = (
                "win_prob_near_certain"
                if state.p_win <= _eff_ncl
                else "win_prob_force_close"
            )
            return -1.0

        # ── Model 1: Convex time decay ──
        time_frac = min(position_age_seconds / state.max_hold_seconds, 1.0)
        time_factor = 1.0 - (time_frac ** self.cfg.time_decay_exponent)

        # ── Model 2: ATR-scaled base room (per-class multiplier) ──
        # Dead/low coins get a tighter multiplier so `allowed_loss` shrinks
        # quickly toward the 0.15 % floor when they start bleeding. High/
        # extreme coins get more room so normal ATR-scale swings don't
        # trigger premature exits. Falls back to the flat multiplier when
        # class is unknown or not in the override table.
        _atr_mult = self.cfg.atr_room_multiplier_by_class.get(
            _cls, self.cfg.atr_room_multiplier,
        )
        atr_room = state.atr_5m_pct * _atr_mult

        # ── Model 3: MAE recovery multiplier ──
        recovery_multiplier = self._recovery_multiplier(state, current_pnl_pct)

        # ── Model 4: velocity/acceleration 4-case switch ──
        momentum_multiplier = self._momentum_multiplier(
            velocity_pct_per_s, acceleration_pct_per_s2,
        )

        # ── Model 5: Bayesian probability multiplier (discrete thresholds) ──
        if state.p_win < self.cfg.p_win_tight:
            probability_multiplier = self.cfg.p_win_tight_mult
        elif state.p_win > self.cfg.p_win_loose:
            probability_multiplier = self.cfg.p_win_loose_mult
        else:
            probability_multiplier = 1.0

        # ── Combined formula (multiplicative, spec §Combined) ──
        allowed_loss = (
            atr_room
            * time_factor
            * recovery_multiplier
            * momentum_multiplier
            * probability_multiplier
        )

        # Floor — prevent noise-driven ultra-tight exits
        if allowed_loss < self.cfg.min_allowed_loss_pct:
            allowed_loss = self.cfg.min_allowed_loss_pct
        # Cap — never widen the original stop
        if allowed_loss > state.original_sl_pct:
            allowed_loss = state.original_sl_pct

        # ── Issue 3 (2026-06-08): recovery-responsive tightening ──
        # The MAE monotonic hold keeps mae_pct at the worst excursion, and the
        # recovery_multiplier (1.2 bonus) WIDENS the computed budget as the trade
        # recovers, so the tighter-only guard below would otherwise pin the stop
        # at the wide level set during the worst dip — the loss budget stays
        # widened through a genuine recovery. On a STRONG recovery, tighten the
        # stop toward the recovered level (a tight bounce-capture near the least
        # loss, blueprint Part 5.3), leaving a buffer below the current price so a
        # still-running recovery is NOT cut. A moderate recovery (below the
        # threshold) still gets the room the 1.2 bonus grants — no strangle.
        _recovery_tighten = False
        _recov_ratio = 0.0
        if (
            getattr(self.cfg, "mae_recovery_tighten_enabled", False)
            and state.mae_pct <= -0.10
        ):
            _recov_ratio = (current_pnl_pct - state.mae_pct) / abs(state.mae_pct)
            if _recov_ratio >= self.cfg.mae_tightening_recovery_threshold:
                # allowed_loss is a positive %, the distance below entry. Place
                # the stop recovery_tightening_buffer_pct below the current PnL so
                # it sits just under the recovered price (loss-side, tighten-only;
                # ratchets up as the recovery continues). min_allowed_loss_pct and
                # the price-relative floor below keep it off noise / placeable.
                _recov_allowed = max(
                    self.cfg.min_allowed_loss_pct,
                    -current_pnl_pct + self.cfg.recovery_tightening_buffer_pct,
                )
                if _recov_allowed < allowed_loss:
                    allowed_loss = _recov_allowed
                    _recovery_tighten = True

        # Tighter-only: never loosen a previously-set budget
        if allowed_loss >= state.last_allowed_loss:
            self._maybe_log(
                state, allowed_loss, current_pnl_pct,
                velocity_pct_per_s, acceleration_pct_per_s2,
                regime_still_supports,
                time_factor, recovery_multiplier,
                momentum_multiplier, probability_multiplier,
                action="no_tighten",
            )
            return None

        # Map allowed_loss% → absolute SL price (direction-aware)
        if state.direction in ("Buy", "Long"):
            new_sl = state.entry_price * (1.0 - allowed_loss / 100.0)
        else:
            new_sl = state.entry_price * (1.0 + allowed_loss / 100.0)

        # Phase 11 (P1-10c) — price-relative floor.
        # The gateway R2 rule rejects SLs whose distance from CURRENT
        # PRICE is below the per-class minimum. TD's floor is entry-
        # relative, so on a Buy at 1.00 with current=0.998 and SL=0.9985
        # (entry-relative 0.15%), distance from current is only 0.05% —
        # well below gateway's 0.3% min. Result: gateway rejects every
        # TD push and the lane runs blind.
        #
        # Fix: derive current_price from entry+pnl, check whether the
        # computed SL satisfies the configured price-relative floor. If
        # not, skip the push (return None) and log so the operator sees
        # WHY TD went quiet rather than seeing endless "rejected by
        # gateway" lines.
        min_pr_floor = getattr(self.cfg, "min_price_relative_distance_pct", 0.0)
        if min_pr_floor > 0:
            if state.direction in ("Buy", "Long"):
                current_price = state.entry_price * (1.0 + current_pnl_pct / 100.0)
                distance_pct = (current_price - new_sl) / current_price * 100 if current_price > 0 else 0.0
            else:
                current_price = state.entry_price * (1.0 - current_pnl_pct / 100.0)
                distance_pct = (new_sl - current_price) / current_price * 100 if current_price > 0 else 0.0
            if distance_pct < min_pr_floor:
                log.info(
                    f"TIME_DECAY_FLOOR_PRICE_REL | sym={state.symbol} "
                    f"sl={new_sl:.6f} current={current_price:.6f} "
                    f"distance_pct={distance_pct:.3f}% min={min_pr_floor:.2f}% "
                    f"action=skip_below_gateway_floor | {ctx()}"
                )
                return None

        state.last_allowed_loss = allowed_loss
        state.last_sl_sent = new_sl

        if _recovery_tighten:
            log.info(
                f"TIME_DECAY_RECOVERY_TIGHTEN | sym={state.symbol} "
                f"mae={state.mae_pct:+.3f}% current={current_pnl_pct:+.3f}% "
                f"recovery={_recov_ratio:.2f} allowed_loss={allowed_loss:.3f}% "
                f"buffer={self.cfg.recovery_tightening_buffer_pct:.2f}% "
                f"new_sl={new_sl:.6f} | bounce-capture near least loss | {ctx()}"
            )

        self._maybe_log(
            state, allowed_loss, current_pnl_pct,
            velocity_pct_per_s, acceleration_pct_per_s2,
            regime_still_supports,
            time_factor, recovery_multiplier,
            momentum_multiplier, probability_multiplier,
            action="tighten",
        )
        return new_sl

    # ─── Internal ─────────────────────────────────────────────────────

    def _assign_mae_monotonic(
        self,
        state: TimeDecayState,
        *,
        candidate: float,
        source: str,
    ) -> bool:
        """T1-2 (2026-05-12) — sole assignment site for ``state.mae_pct``.

        MAE is the worst (most-negative) PnL excursion seen for the
        position lifetime. The contract is strictly monotonic: once
        observed, MAE may only deepen. Direct mutators that regress MAE
        indicate either (a) a bug in the caller (SL-modify path that
        clears state, state recreation losing the high-water mark), or
        (b) a logic error here. Either way: HOLD the prior value and
        emit ``TIME_DECAY_MAE_MONOTONIC_HOLD`` so the regression is
        observable in production logs without losing the floor.

        ``source`` distinguishes call sites for forensic correlation:
          - ``live_tick``  — normal per-tick deepening from ``calculate``.
          - ``init_seed``  — preservation across state recreation
                             (passed by ``create_state`` when a
                             ``prior_mae_pct`` kwarg seeds the new state).

        Returns:
            ``True`` when MAE was deepened to ``candidate``.
            ``False`` otherwise (no-op when equal; HOLD when regressed).
        """
        prior = state.mae_pct
        if candidate < prior:
            state.mae_pct = candidate
            return True
        if candidate > prior:
            # Regression attempt — hold and log. Originally WARNING
            # ("any emission is a smoking gun"), but the audited
            # 1.5 h window produced 296 emissions which contradicts
            # the "smoking gun" expectation: in practice the call
            # pattern routinely produces benign monotonic-hold rejects
            # (e.g., a positive MAE update arrives after a deeper
            # negative one in the same tick due to multi-source
            # updates). Observability G11 (noise reduction): downgrade
            # to INFO so the WARNING tail surfaces only true
            # exceptional conditions. The event itself, fields, and
            # invariant (monotonic-hold rejects the regression) are
            # preserved. Operators investigating the smoking-gun case
            # still grep INFO logs for the tag.
            log.info(
                f"TIME_DECAY_MAE_MONOTONIC_HOLD | sym={state.symbol} "
                f"attempted={candidate:+.2f}% held={prior:+.2f}% "
                f"source={source} tick={state.tick_count} | {ctx()}"
            )
        return False

    def _recovery_multiplier(
        self, state: TimeDecayState, current_pnl_pct: float,
    ) -> float:
        """Model 3 — MAE recovery as direct multiplier.

        recovery_ratio is in [0, 1]:
            0.0 = at worst point (still at MAE)
            1.0 = fully recovered to breakeven
        If no meaningful MAE yet (|mae| < 0.10%), multiplier is neutral.
        """
        if state.mae_pct > -0.10:  # no meaningful drawdown yet
            return 1.0
        # Both mae_pct and current_pnl_pct are negative numbers.
        # recovery = (current - mae) / |mae|  → positive if current > mae (recovered).
        recovery = (current_pnl_pct - state.mae_pct) / abs(state.mae_pct)
        if recovery > self.cfg.mae_recovery_threshold:
            return self.cfg.mae_bonus
        if recovery < self.cfg.mae_stagnation_threshold:
            return self.cfg.mae_penalty
        return 1.0

    def _momentum_multiplier(self, velocity: float, acceleration: float) -> float:
        """Model 4 — four-case switch on (velocity, accel) signs."""
        if velocity > 0 and acceleration > 0:
            return self.cfg.momentum_favorable      # 1.3 — recovering, accelerating
        if velocity > 0 and acceleration < 0:
            return self.cfg.momentum_slow_rise      # 1.1 — recovering but slowing
        if velocity < 0 and acceleration > 0:
            return self.cfg.momentum_slow_fall      # 0.9 — falling but decelerating
        if velocity < 0 and acceleration < 0:
            return self.cfg.momentum_danger         # 0.7 — falling AND accelerating
        return 1.0  # exactly zero velocity or acceleration → neutral

    def _update_p_win(
        self,
        state: TimeDecayState,
        *,
        current_pnl_pct: float,
        regime_still_supports: bool,
    ) -> None:
        """Bayesian p_win update per spec §Model 5.

        Rules (each tick):
          - Price moved deeper against us AND total distance > 2 ATR → *= 0.70
          - Price moved deeper against us AND total distance > 1 ATR → *= 0.85
          - Recovered 50%+ of MAE                                    → *= 1.15
          - Regime still supports trade                              → *= 1.05
          - Regime reversed                                          → *= 0.60
        Then clamp to [p_win_min, p_win_max].
        """
        atr = state.atr_5m_pct
        atrs_from_entry = abs(current_pnl_pct) / atr if atr > 0 else 0.0
        # Compare to the PREVIOUS tick's pnl (captured by observe() before it
        # overwrites last_pnl_pct). Using last_pnl_pct here would always see
        # current_pnl_pct == state.last_pnl_pct (same tick) → penalty never fires.
        deeper_this_tick = current_pnl_pct < state.prev_pnl_pct

        # Price-action penalties (compound if conditions apply)
        if deeper_this_tick:
            if atrs_from_entry > 2.0:
                state.p_win *= self.cfg.p_win_atr2_penalty
            elif atrs_from_entry > 1.0:
                state.p_win *= self.cfg.p_win_atr1_penalty

            # Absolute-depth reality check. ATR-relative thresholds above
            # never fire on a slow bleeder (a dead-vol coin deepening
            # 0.02%/tick at -2% PnL is <1 ATR of tick-over-tick deepening).
            # This branch catches that case — positions losing real money
            # get their p_win penalised regardless of ATR scale. Compounds
            # with the ATR-relative penalties so a fast bleeder deepening
            # at 2 ATR/tick at -4% PnL pays BOTH (0.70 × 0.70 = 0.49/tick).
            _abs = abs(current_pnl_pct)
            if _abs > self.cfg.p_win_abs_depth_strong_pct:
                state.p_win *= self.cfg.p_win_abs_depth_strong_penalty
            elif _abs > self.cfg.p_win_abs_depth_threshold_pct:
                state.p_win *= self.cfg.p_win_abs_depth_penalty

        # Recovery bonus — only if MAE is meaningful
        if state.mae_pct < -0.10:
            recovery = (current_pnl_pct - state.mae_pct) / abs(state.mae_pct)
            if recovery > 0.5:
                state.p_win *= self.cfg.p_win_recovery_bonus

        # Regime update. PF/LC Top-15 Problem 3.1 — when smoothing is enabled the
        # regime penalty is EDGE-TRIGGERED: it applies only after a SUSTAINED
        # mismatch (N consecutive not-supporting ticks), so a single 10s regime
        # flicker no longer halves p_win at flat price. The supporting bonus and
        # the streak reset apply every tick. Off → the original unconditional
        # per-tick penalty.
        if regime_still_supports:
            state.regime_mismatch_streak = 0
            state.p_win *= self.cfg.p_win_regime_bonus
        else:
            state.regime_mismatch_streak += 1
            if (
                self.cfg.smooth_p_win_enabled
                and self.cfg.p_win_regime_edge_trigger_enabled
            ):
                if (
                    state.regime_mismatch_streak
                    >= self.cfg.p_win_regime_penalty_sustained_ticks
                ):
                    state.p_win *= self.cfg.p_win_regime_penalty
                # else: still inside the flicker window — no penalty this tick
            else:
                state.p_win *= self.cfg.p_win_regime_penalty

        # Clamp
        state.p_win = max(
            self.cfg.p_win_min,
            min(self.cfg.p_win_max, state.p_win),
        )

        # PF/LC Top-15 Problem 3.1 — keep a short, bounded recent-PnL history for
        # the recovery guard (read at the force-close sentinel in calculate()).
        state.recent_pnl.append(current_pnl_pct)
        if len(state.recent_pnl) > 32:
            del state.recent_pnl[: len(state.recent_pnl) - 32]

    def _maybe_log(
        self,
        state: TimeDecayState,
        allowed_loss: float,
        pnl: float,
        vel: float,
        accel: float,
        regime_ok: bool,
        time_factor: float,
        recovery_mult: float,
        momentum_mult: float,
        probability_mult: float,
        *,
        action: str,
    ) -> None:
        """Emit a TIME_DECAY_CALC line at cadence or on significant tightening."""
        tightened_significantly = (
            state.last_allowed_loss < float("inf")
            and allowed_loss < state.last_allowed_loss * 0.90
        )
        if state.tick_count % self.cfg.log_every_n_ticks == 0 or tightened_significantly:
            log.info(
                f"TIME_DECAY_CALC | sym={state.symbol} act={action} "
                f"pnl={pnl:+.2f}% mae={state.mae_pct:+.2f}% "
                f"allowed={-allowed_loss:.2f}% p_win={state.p_win:.3f} "
                f"| tfact={time_factor:.2f} rec={recovery_mult:.2f} "
                f"mom={momentum_mult:.2f} prob={probability_mult:.2f} "
                f"| vel={vel:+.3f}%/s accel={accel:+.3f}%/s^2 "
                f"regime_ok={regime_ok} tick={state.tick_count} | {ctx()}"
            )


def observe(state: TimeDecayState, current_pnl_pct: float) -> tuple[float, float]:
    """Compute (velocity, acceleration) from tick-to-tick PnL. Mutates state.

    velocity     = (current_pnl - state.last_pnl_pct) / state.tick_seconds
    acceleration = velocity - state.prev_velocity

    Must be called exactly once per tick BEFORE ``TimeDecaySLCalculator.calculate``.

    Side effects on state:
      - prev_pnl_pct  ← previous value of last_pnl_pct (so _update_p_win can
                        detect whether this tick deepened the loss).
      - last_pnl_pct  ← current_pnl_pct (for next tick's velocity calc).
      - prev_velocity ← newly-computed velocity (for next tick's accel calc).
    """
    velocity = (current_pnl_pct - state.last_pnl_pct) / max(state.tick_seconds, 0.1)
    acceleration = velocity - state.prev_velocity
    state.prev_pnl_pct = state.last_pnl_pct
    state.last_pnl_pct = current_pnl_pct
    state.prev_velocity = velocity
    return velocity, acceleration
