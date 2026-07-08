"""Position Watchdog — real-time position monitor + Claude Brain trigger.

Monitors all open positions every N seconds, detects when trades are going
against us, sends Telegram alerts, and triggers Claude Brain for smart exit
decisions (hold, tighten_stop, partial_close, full_close).
"""

import asyncio
import inspect
import time
from datetime import datetime, timezone

from src.alerts.alert_manager import AlertManager
from src.analysis.engine import TAEngine
# NOTE: post-Layer-1 audit — at runtime ``services["claude_client"]`` is
# a ``ClaudeCodeClient`` (CLI subprocess wrapper), not the SDK-based
# ``ClaudeClient`` that this annotation hints. The annotation is kept
# loose (``object | None`` would be too weak; ``ClaudeClient`` mismatches
# the live wiring). The watchdog reads the heartbeat attributes via
# ``hasattr`` / ``getattr`` so it works regardless of which client class
# is injected. See dev_notes/phase6_brain_credential_report.md.
from src.brain.claude_client import ClaudeClient  # noqa: F401  (annotation only)
from src.brain.cost_tracker import CostTracker
from src.brain.decision_parser import DecisionParser
from src.brain.prompts.position_review import (
    POSITION_REVIEW_PROMPT,
    WATCHDOG_SYSTEM_PROMPT,
)
from src.config.settings import Settings
from src.core.log_context import ctx, new_watchdog_id, set_tid, tid_scope
from src.core.logging import get_logger
from src.core.sl_geometry import is_long_side, is_tighter_sl
from src.core.types import (
    AlertLevel,
    OrderType,
    Position,
    Side,
    Ticker,
    TimeFrame,
    WatchdogDecision,
)
from src.core.utils import format_price, now_utc
from src.database.connection import DatabaseManager
from src.risk.risk_manager import RiskManager
from src.risk.wd_brain_scoring import compute_sl_consumption_pct
from src.risk.time_decay_sl import (
    TimeDecayConfig,
    TimeDecaySLCalculator,
    TimeDecayState,
    observe as td_observe,
)
from src.trading.services.account_service import AccountService
from src.trading.services.market_service import MarketService
from src.trading.services.order_service import OrderService
from src.trading.services.position_service import PositionService
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


def _close_age_seconds(closed_at: str | None) -> float | None:
    """Seconds elapsed since a Shadow ISO-8601 UTC `closed_at` timestamp.

    Returns None when the input is missing or unparseable so the caller
    can decide how to react (fall back to ticker).
    """
    if not closed_at:
        return None
    try:
        dt = datetime.fromisoformat(closed_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None


# Issue I3 (F-28, 2026-05-14) — PNL mismatch retry limit. When the
# watchdog reconstructs a close event from incomplete data
# (price_source == ticker_fallback / last_tick_cache / derived) and
# the resulting pnl is 0% with entry==exit, the integrity check
# WD_PNL_MISMATCH fires. Pre-I3 the corrupted record committed
# anyway. Post-I3 we skip the commit for up to this many retries,
# giving Bybit's closed-pnl indexer time to populate authoritative
# exit price. After exhaustion we commit with WD_PNL_MISMATCH_FORCED
# so the trade isn't permanently stuck. 5 ticks ≈ 50s at the
# default watchdog tick cadence — sufficient for the indexer's
# typical 1-10s populate window plus headroom for slow markets.
_PNL_MISMATCH_RETRY_LIMIT: int = 5


class PositionWatchdog(BaseWorker):
    """Monitors open positions and triggers Claude Brain for exit decisions.

    Detects: loss from entry, trailing drawdown from peak, rapid price moves,
    stop-loss proximity, and accelerating losses. When danger thresholds are
    exceeded, asks Claude for a decision and executes it.

    Args:
        settings: Application settings.
        db: Database manager.
        position_service: For fetching positions and managing SL/close.
        market_service: For current ticker prices.
        order_service: For placing reducing orders.
        account_service: For wallet balance context.
        claude_client: For calling Claude API.
        cost_tracker: Shared cost tracker for budget enforcement.
        decision_parser: For parsing Claude responses.
        risk_manager: For updating drawdown on trade close.
        alert_manager: For Telegram notifications.
        ta_engine: For technical analysis context.
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        position_service: PositionService,
        market_service: MarketService,
        order_service: OrderService | None = None,
        account_service: AccountService | None = None,
        claude_client: ClaudeClient | None = None,
        cost_tracker: CostTracker | None = None,
        decision_parser: DecisionParser | None = None,
        risk_manager: RiskManager | None = None,
        alert_manager: AlertManager | None = None,
        ta_engine: TAEngine | None = None,
        trade_coordinator=None,
        event_buffer=None,
        data_lake=None,
        transformer=None,
        regime_detector=None,
        urgent_queue=None,
        volatility_profiler=None,
        sl_gateway=None,
        thesis_manager=None,
        structure_cache=None,
        layer4_protection=None,
        # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) —
        # EnsembleStateCache injected so _monitor_position can read the
        # current ensemble consensus for an open position and detect
        # STRONG opposite-direction flips. None when the cache is not
        # wired (e.g. strategy engine disabled in config); detection
        # short-circuits gracefully in that case.
        ensemble_state_cache=None,
    ) -> None:
        super().__init__(
            name="position_watchdog",
            interval_seconds=settings.watchdog.check_interval_seconds,
            settings=settings,
            db=db,
        )
        self.position_service = position_service
        self.market_service = market_service
        self.order_service = order_service
        self.account_service = account_service
        self.claude_client = claude_client
        self.cost_tracker = cost_tracker
        self.decision_parser = decision_parser
        self.risk_manager = risk_manager
        self.alert_manager = alert_manager
        self.ta_engine = ta_engine
        self.coordinator = trade_coordinator
        self.event_buffer = event_buffer
        self.data_lake = data_lake
        self.transformer = transformer
        self.regime_detector = regime_detector
        self.urgent_queue = urgent_queue
        self.volatility_profiler = volatility_profiler
        self.sl_gateway = sl_gateway
        self.thesis_manager = thesis_manager
        # Profit-Fetching Exit System (Phase 5, 2026-05-29) — Full
        # reconciliation of the watchdog winner-cutters. When the system is
        # enabled, the sniper's ladder + Chandelier spine is the sole winner
        # manager: the watchdog rides a still-profitable trade past its
        # deadline, skips the +1.5% profit-take, and fully disables its own
        # percentage trail. Each behaviour has its own revertible switch.
        # Non-climber backstops (-3% hard stop, loser timeout, SENTINEL
        # big-loss cut) are untouched. When disabled, the watchdog behaves
        # exactly as before.
        self._pf = settings.profit_fetching
        if self._pf.enabled:
            log.info(
                f"PF_WATCHDOG_RECONCILE | enabled={self._pf.enabled} "
                f"ride_winner_past_deadline={self._pf.ride_winner_past_deadline} "
                f"subordinate_profit_take={self._pf.subordinate_profit_take} "
                f"subordinate_watchdog_trail_exit={self._pf.subordinate_watchdog_trail_exit}"
            )
        # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) —
        # X-RAY structural cache feeds the structural-invalidation gate
        # in `_handle_time_decay`. None when StructureCache is disabled
        # in the WorkerManager (analysis stack inactive); the gate's
        # cache-miss branch fail-safes to "no_data:services_unwired" and
        # blocks force-close, consistent with operator philosophy of
        # preferring false-negative invalidations over false-positive
        # force-closes.
        self.structure_cache = structure_cache
        # Layer 4 Realignment Phase 4.2 (2026-05-06) — shared protection
        # service. Set to a Layer4ProtectionService instance via
        # post-init assignment by WorkerManager (the service depends on
        # this watchdog's _time_decay calculator so it's built AFTER
        # the watchdog is constructed). When None, watchdog falls back
        # to its inline `_compute_structural_invalidation` (DEPRECATED
        # in Phase 4.3 but kept as a back-compat path).
        self.layer4_protection = layer4_protection

        # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) —
        # EnsembleStateCache for the 1A (ensemble-flip) detection lane.
        # Per-symbol in-memory state of the last STRONG-direction
        # detection so we can dedupe within the operator-tunable window
        # (settings.watchdog.ensemble_flip_dedupe_window_seconds).
        self.ensemble_state_cache = ensemble_state_cache
        # _position_consensus_state[symbol] = (last_strong_dir, last_fire_ts).
        # last_strong_dir is the dominant_dir at the moment we last fired
        # an ENSEMBLE_FLIP_DETECTED for this symbol (used to detect
        # re-flips back and to enforce dedupe).
        self._position_consensus_state: dict[str, tuple[str, float]] = {}
        # Mid-Hold Trade Management Fix Phase 3.5 (2026-05-19) —
        # in-memory mirror of the DB ``thesis_state`` column. The DB row
        # is authoritative (survives restart); this dict is a fast-path
        # cache so we only persist a transition on actual state change,
        # not every monitor tick.
        # _position_thesis_state[symbol] = last_persisted_state.
        self._position_thesis_state: dict[str, str] = {}

        # Zombie reconciler cadence — runs every 5 min from tick()
        self._last_reconcile_at: float = 0.0
        # Phase 2 (P0-1): fast set-diff reconciler — runs every
        # WatchdogSettings.fast_reconcile_seconds (default 30s) from tick().
        # Catches Shadow-side closes between WD ticks (which can run as long
        # as 30+ s under contention). Independent of the 5-min thesis sweep.
        self._last_fast_reconcile_at: float = 0.0

        # Phase 4 DB contention fix: tick-local kline prefetch cache. One
        # batch query per tick replaces N serial get_klines() calls inside
        # _monitor_position. Keyed by symbol, cleared at tick end. Hoisted
        # MarketRepository avoids allocation churn per position.
        from src.database.repositories.market_repo import MarketRepository as _MR
        self.market_repo = _MR(db)
        self._wd_klines_m5: dict[str, list] = {}

        # ── Loser-lane Time-Decay SL ─────────────────────────────────
        # Pure-math calculator + per-symbol state. Runs only when pnl<0.
        # Config populated from settings.time_decay. If disabled, the
        # lane-split short-circuits on self._time_decay is None.
        td_settings = getattr(settings, "time_decay", None)
        # Loss-Cutting coordination (2026-05-31): the win-probability exit is a
        # loss-side cut, and the operator put the loss system in authority over
        # negative-PnL trades — but it must COORDINATE, not duplicate. The cut
        # itself stays here (the single owner, the H1 carve-out below); the
        # loss-cutting config simply becomes its single tuning home. When
        # loss-cutting is enabled, near_certain_loser_p_win is sourced from
        # [loss_cutting].winprob_cut_threshold_young (0.10 by default — identical
        # to the existing value, so this is a no-op until the operator tunes it).
        _lc_settings = getattr(settings, "loss_cutting", None)
        _td_enabled = td_settings is not None and getattr(td_settings, "enabled", True)
        _near_certain_p_win = float(getattr(td_settings, "near_certain_loser_p_win", 0.10)) \
            if td_settings is not None else 0.10
        if (
            _td_enabled                       # the cut owner must actually exist
            and _lc_settings is not None
            and getattr(_lc_settings, "enabled", False)
            and getattr(_lc_settings, "enable_winprob_observe", False)
        ):
            _near_certain_p_win = float(getattr(
                _lc_settings, "winprob_cut_threshold_young", _near_certain_p_win,
            ))
            log.info(
                f"LOSS_WINPROB_COORD | source=loss_cutting "
                f"near_certain_loser_p_win={_near_certain_p_win:.3f} | the loss "
                f"system owns the loss-side win-prob cut threshold; the cut stays "
                f"in the time-decay path (single owner, no duplicate) | no_ctx"
            )
        if td_settings is not None and getattr(td_settings, "enabled", True):
            self._time_decay: TimeDecaySLCalculator | None = TimeDecaySLCalculator(
                TimeDecayConfig(
                    time_decay_exponent=td_settings.time_decay_exponent,
                    atr_room_multiplier=td_settings.atr_room_multiplier,
                    mae_recovery_threshold=td_settings.mae_recovery_threshold,
                    mae_stagnation_threshold=td_settings.mae_stagnation_threshold,
                    mae_bonus=td_settings.mae_bonus,
                    mae_penalty=td_settings.mae_penalty,
                    # Issue 3 (2026-06-08) — recovery-responsive tightening.
                    # getattr keeps boot tolerant of stale config that pre-dates
                    # the fix.
                    mae_recovery_tighten_enabled=getattr(
                        td_settings, "mae_recovery_tighten_enabled", True,
                    ),
                    mae_tightening_recovery_threshold=getattr(
                        td_settings, "mae_tightening_recovery_threshold", 0.75,
                    ),
                    recovery_tightening_buffer_pct=getattr(
                        td_settings, "recovery_tightening_buffer_pct", 0.3,
                    ),
                    momentum_danger=td_settings.momentum_danger,
                    momentum_favorable=td_settings.momentum_favorable,
                    momentum_slow_fall=td_settings.momentum_slow_fall,
                    momentum_slow_rise=td_settings.momentum_slow_rise,
                    p_win_prior_base=td_settings.p_win_prior_base,
                    p_win_prior_regime_weight=td_settings.p_win_prior_regime_weight,
                    p_win_force_close=td_settings.p_win_force_close,
                    p_win_tight=td_settings.p_win_tight,
                    p_win_loose=td_settings.p_win_loose,
                    p_win_tight_mult=td_settings.p_win_tight_mult,
                    p_win_loose_mult=td_settings.p_win_loose_mult,
                    p_win_atr1_penalty=td_settings.p_win_atr1_penalty,
                    p_win_atr2_penalty=td_settings.p_win_atr2_penalty,
                    p_win_recovery_bonus=td_settings.p_win_recovery_bonus,
                    p_win_regime_bonus=td_settings.p_win_regime_bonus,
                    p_win_regime_penalty=td_settings.p_win_regime_penalty,
                    p_win_min=td_settings.p_win_min,
                    p_win_max=td_settings.p_win_max,
                    grace_seconds=td_settings.grace_seconds,
                    grace_seconds_by_class=getattr(
                        td_settings, "grace_seconds_by_class", {},
                    ),
                    atr_room_multiplier_by_class=getattr(
                        td_settings, "atr_room_multiplier_by_class", {},
                    ),
                    min_allowed_loss_pct=td_settings.min_allowed_loss_pct,
                    # Phase 1 (Time-Decay Force-Close Definitive Fix,
                    # 2026-05-06) — minimum-age guardrail. `getattr` with
                    # default keeps boot-time tolerant of stale config
                    # files that pre-date the fix.
                    min_age_seconds=float(getattr(
                        td_settings, "min_age_seconds", 300.0,
                    )),
                    # Phase 2 (Time-Decay Force-Close Definitive Fix,
                    # 2026-05-06) — MAE-relative-to-SL gate.
                    mae_to_sl_ratio_threshold=float(getattr(
                        td_settings, "mae_to_sl_ratio_threshold", 0.5,
                    )),
                    # Phase 3 (Time-Decay Force-Close Definitive Fix,
                    # 2026-05-06) — structural-invalidation gate.
                    structural_invalidation_required=bool(getattr(
                        td_settings, "structural_invalidation_required", True,
                    )),
                    # Issue 2.6 (2026-06-07) — slow-bleed cumulative force-close
                    # (default off). getattr-tolerant of configs pre-dating it.
                    slow_bleed_cumulative_force_close_enabled=bool(getattr(
                        td_settings, "slow_bleed_cumulative_force_close_enabled", False,
                    )),
                    slow_bleed_cumulative_loss_pct=float(getattr(
                        td_settings, "slow_bleed_cumulative_loss_pct", 2.5,
                    )),
                    # F4/F4b/F7 (2026-06-09) — standalone monotonic-grind cut
                    # (default OFF). getattr-tolerant of configs pre-dating it.
                    monotonic_grind_cut_enabled=bool(getattr(
                        td_settings, "monotonic_grind_cut_enabled", False,
                    )),
                    monotonic_grind_near_trough_band_pct=float(getattr(
                        td_settings, "monotonic_grind_near_trough_band_pct", 0.05,
                    )),
                    monotonic_grind_sustained_ticks=int(getattr(
                        td_settings, "monotonic_grind_sustained_ticks", 24,
                    )),
                    monotonic_grind_max_recovery_ratio=float(getattr(
                        td_settings, "monotonic_grind_max_recovery_ratio", 0.20,
                    )),
                    monotonic_grind_min_loss_pct=float(getattr(
                        td_settings, "monotonic_grind_min_loss_pct", 0.30,
                    )),
                    # H1 (2026-05-30) — near-certain-loser carve-out: when p_win
                    # is at/below this the struct-guard yields so a clear bleeder
                    # is cut. getattr default tolerates configs pre-dating the fix.
                    near_certain_loser_p_win=_near_certain_p_win,
                    # PF/LC Top-15 Problem 2.3 — age-aware near-certain-loser
                    # band (default off). getattr-tolerant of older configs.
                    winprob_age_aware_band_enabled=bool(getattr(
                        td_settings, "winprob_age_aware_band_enabled", False,
                    )),
                    near_certain_loser_p_win_young=float(getattr(
                        td_settings, "near_certain_loser_p_win_young", 0.10,
                    )),
                    near_certain_loser_p_win_old=float(getattr(
                        td_settings, "near_certain_loser_p_win_old", 0.13,
                    )),
                    age_threshold_to_raise_p_win_seconds=float(getattr(
                        td_settings, "age_threshold_to_raise_p_win_seconds", 600.0,
                    )),
                    # PF/LC Top-15 Problem 3.1 — win-prob over-cut smoothing
                    # (default off via smooth_p_win_enabled). getattr-tolerant.
                    smooth_p_win_enabled=bool(getattr(
                        td_settings, "smooth_p_win_enabled", False,
                    )),
                    p_win_regime_edge_trigger_enabled=bool(getattr(
                        td_settings, "p_win_regime_edge_trigger_enabled", True,
                    )),
                    p_win_regime_penalty_sustained_ticks=int(getattr(
                        td_settings, "p_win_regime_penalty_sustained_ticks", 3,
                    )),
                    p_win_recovery_guard_enabled=bool(getattr(
                        td_settings, "p_win_recovery_guard_enabled", True,
                    )),
                    p_win_recovery_guard_be_band_pct=float(getattr(
                        td_settings, "p_win_recovery_guard_be_band_pct", 0.5,
                    )),
                    p_win_recovery_guard_n_ticks=int(getattr(
                        td_settings, "p_win_recovery_guard_n_ticks", 3,
                    )),
                    xray_drop_threshold=float(getattr(
                        td_settings, "xray_drop_threshold", 0.40,
                    )),
                    regime_inversion_confidence_threshold=float(getattr(
                        td_settings,
                        "regime_inversion_confidence_threshold",
                        0.60,
                    )),
                    # Absolute-PnL-depth penalty (catches slow bleeders that
                    # never exceed the ATR-relative deepening thresholds).
                    p_win_abs_depth_threshold_pct=getattr(
                        td_settings, "p_win_abs_depth_threshold_pct", 1.5,
                    ),
                    p_win_abs_depth_strong_pct=getattr(
                        td_settings, "p_win_abs_depth_strong_pct", 3.0,
                    ),
                    p_win_abs_depth_penalty=getattr(
                        td_settings, "p_win_abs_depth_penalty", 0.90,
                    ),
                    p_win_abs_depth_strong_penalty=getattr(
                        td_settings, "p_win_abs_depth_strong_penalty", 0.70,
                    ),
                    log_every_n_ticks=td_settings.log_every_n_ticks,
                    # Phase 11 (P1-10c): align TD's price-relative floor
                    # with the gateway's R2 min_distance_pct. Pull from
                    # sl_gateway settings so a single source of truth (the
                    # gateway config) drives both gates.
                    min_price_relative_distance_pct=float(
                        getattr(
                            getattr(settings, "sl_gateway", None),
                            "min_distance_pct",
                            0.0,
                        ) or 0.0
                    ),
                )
            )
            # PF/LC Top-15 boot sentinel (Rule 14) — confirm the new time-decay
            # config is loaded: the age-aware near-certain band (2.3, default off)
            # and the win-prob over-cut smoothing (3.1, default off).
            log.info(
                f"TIME_DECAY_PFLC_CONFIG_LOADED | "
                f"age_aware_band={self._time_decay.cfg.winprob_age_aware_band_enabled} "
                f"ncl_young={self._time_decay.cfg.near_certain_loser_p_win_young} "
                f"ncl_old={self._time_decay.cfg.near_certain_loser_p_win_old} "
                f"age_thresh_s={self._time_decay.cfg.age_threshold_to_raise_p_win_seconds} "
                f"smooth_p_win={self._time_decay.cfg.smooth_p_win_enabled} "
                f"regime_edge={self._time_decay.cfg.p_win_regime_edge_trigger_enabled} "
                f"regime_sustain_ticks={self._time_decay.cfg.p_win_regime_penalty_sustained_ticks} "
                f"recovery_guard={self._time_decay.cfg.p_win_recovery_guard_enabled} "
                # Issue 2.6 (2026-06-07) boot sentinel — slow-bleed cumulative
                # force-close config (default OFF).
                f"slow_bleed={self._time_decay.cfg.slow_bleed_cumulative_force_close_enabled} "
                f"slow_bleed_loss_pct={self._time_decay.cfg.slow_bleed_cumulative_loss_pct} "
                # Issue 3 (2026-06-08) boot sentinel — MAE recovery-tightening.
                f"mae_recov_tighten={getattr(self._time_decay.cfg, 'mae_recovery_tighten_enabled', False)} "
                f"mae_recov_thresh={getattr(self._time_decay.cfg, 'mae_tightening_recovery_threshold', 0.0)} "
                f"recov_buffer={getattr(self._time_decay.cfg, 'recovery_tightening_buffer_pct', 0.0)} "
                # F4/F4b/F7 (2026-06-09) boot sentinel — standalone monotonic-grind
                # cut config (default OFF). Confirms the new keys loaded.
                f"grind_cut={self._time_decay.cfg.monotonic_grind_cut_enabled} "
                f"grind_band={self._time_decay.cfg.monotonic_grind_near_trough_band_pct} "
                f"grind_sustained_ticks={self._time_decay.cfg.monotonic_grind_sustained_ticks} "
                f"grind_max_recov={self._time_decay.cfg.monotonic_grind_max_recovery_ratio} "
                f"grind_min_loss={self._time_decay.cfg.monotonic_grind_min_loss_pct} | no_ctx"
            )
        else:
            self._time_decay = None
        self._td_states: dict[str, TimeDecayState] = {}
        # T1-2 (2026-05-12) — high-water-mark preservation across state
        # recreation. Snapshot of state.mae_pct keyed by symbol, written
        # at every _td_states deletion (force-close finally line ~1433,
        # profit handoff line ~1828, stale-symbol cleanup line ~3438) and
        # read at lazy-init in _handle_time_decay (line ~1281). Cleared
        # only when the trade is CONFIRMED closed via
        # coordinator.on_trade_closed (in _detect_and_record_closes near
        # line ~3357) or in cleanup() — so a transient get_positions
        # miss preserves MAE, while a real close starts fresh.
        # Production root-cause evidence: 56 of 159 HANDOFF→INIT pairs
        # in past 6h lost MAE history (top: INJUSDT lost -0.68% on
        # 2026-05-12 12:02→12:03).
        self._td_mae_high_water: dict[str, float] = {}
        # Issue 1 (2026-05-18) — brain-close scoring velocity fallback.
        # When a position is not in TimeDecayState's loser-lane,
        # ``_td_states[sym].prev_velocity`` is unavailable; the scoring
        # function needs SOME velocity signal so we stash
        # ``(pnl_pct, monotonic_ts)`` here on each pre-close eval and
        # derive ``(pnl_now - pnl_prev) / (ts_now - ts_prev)`` on the
        # next eval. Stale entries are not actively pruned (the dict
        # is bounded by open-position count, which is itself bounded).
        self._brain_score_prev_pnl: dict[str, tuple[float, float]] = {}

        # Per-position tracking state
        self._position_peaks: dict[str, float] = {}
        self._last_prices: dict[str, float] = {}
        self._last_pnls: dict[str, float] = {}
        self._last_brain_call: dict[str, float] = {}
        self._brain_calls_this_hour: int = 0
        self._hour_start: float = time.monotonic()
        # Hold suppression: after Brain says HOLD, extend cooldown significantly
        self._hold_suppression: dict[str, float] = {}
        self._consecutive_holds: dict[str, int] = {}
        # Alert dedup: don't send same-type alert repeatedly
        self._last_alert_time: dict[str, float] = {}
        # Skip-log dedup: TIME_DECAY_SKIP / TRAILING_SKIP emit at most once per
        # (symbol, reason) per 60s so the diagnostic line doesn't drown the log.
        self._last_skip_log: dict[tuple[str, str], float] = {}

        # Issue I3 (F-28, 2026-05-14) — per-symbol counter of consecutive
        # WD_PNL_MISMATCH blocks. When entry==exit and price_source is
        # degraded, we skip the corrupted commit and retry on the next
        # tick; this counter caps the retry window so a permanently
        # broken close path doesn't silently stall a trade. Cleared on
        # successful commit (authoritative price source or pnl != 0) and
        # on forced commit after retry exhaustion.
        self._pnl_mismatch_retries: dict[str, int] = {}

        # Legacy local tracking (used as fallback when coordinator not available)
        self._position_open_times: dict[str, datetime] = {}
        self._position_strategies: dict[str, str] = {}

        # External close detection: track symbols seen last tick
        self._last_known_symbols: set[str] = set()

        # Watchdog 3-mode system (#9)
        self._watchdog_mode: str = "passive"  # "passive", "safety_net", "emergency"
        self._started_at: float = time.time()  # startup grace period
        self._hard_stops_this_hour: int = 0
        self._hard_stop_hour_start: float = time.monotonic()
        self._consecutive_losses: int = 0
        self._session_pnl_pct: float = 0.0
        # Layer 4 Realignment Phase 3.2 (2026-05-06) — captures the
        # specific trigger that flipped the watchdog into mode=emergency
        # so EMERGENCY_CLOSED log + event-buffer entries embed the
        # cause (session_pnl=... vs hard_stops=...) and operators can
        # verify after the fact whether each emergency was justified.
        self._last_emergency_trigger: str = ""

        # ── C1 Phase 1.5b — WD_SCORING_ENFORCE_ACTIVE boot sentinel ──
        # Operator-visible single-line confirmation of the
        # brain-close scoring mode at process startup. Replaces the
        # need to grep config.toml or scan WD_SCORING_PATH_REACHED
        # events to determine which mode is live. Fires once per
        # PositionWatchdog construction (i.e. once per worker process
        # startup in production). The line carries:
        #   * scoring_enabled — kill-switch state
        #   * enforce         — Phase 1 (False, log-only) vs Phase 2 (True, enforce)
        #   * threshold       — composite gate
        # Read with screen-reader-friendly k=v formatting. Reads
        # defensively so a minimal test Settings without watchdog.* fields
        # still constructs the worker.
        _wd_boot_cfg = getattr(self.settings, "watchdog", None)
        _boot_scoring_enabled = bool(getattr(
            _wd_boot_cfg, "wd_brain_scoring_enabled", True,
        )) if _wd_boot_cfg is not None else True
        _boot_enforce = bool(getattr(
            _wd_boot_cfg, "wd_brain_scoring_enforce", False,
        )) if _wd_boot_cfg is not None else False
        _boot_threshold = float(getattr(
            _wd_boot_cfg, "wd_brain_scoring_threshold", 6.0,
        )) if _wd_boot_cfg is not None else 6.0
        log.info(
            f"WD_SCORING_ENFORCE_ACTIVE | scoring_enabled={_boot_scoring_enabled} "
            f"enforce={_boot_enforce} threshold={_boot_threshold:.2f} | {ctx()}"
        )

        # P0-3 fix (2026-05-22) — boot sentinel confirming the
        # brain-vote-factor + hard-risk-floor are active. Operator
        # queries this single line to verify the new factors are
        # contributing to the composite and the floor value is set.
        _boot_floor = float(getattr(
            _wd_boot_cfg, "wd_hard_risk_floor_sl_pct", 85.0,
        )) if _wd_boot_cfg is not None else 85.0
        log.info(
            f"P0_3_SENTINEL | brain_vote_factor=on "
            f"hard_risk_floor_sl_pct={_boot_floor:.1f} "
            f"threshold={_boot_threshold:.2f} "
            f"enforce_mode={_boot_enforce} | {ctx()}"
        )

    def _log_skip(self, symbol: str, reason: str, detail: str) -> None:
        """Emit a rate-limited skip-log for the TIME_DECAY / TRAILING lanes.

        Purpose is diagnostic: tell us which gate stopped Time-Decay or
        Trailing from reaching a position (immunity, maturity, missing
        plan, etc). Dedup window is 60 s per (symbol, reason) so the line
        surfaces once per gate transition instead of on every tick.
        """
        key = (symbol, reason)
        now = time.monotonic()
        last = self._last_skip_log.get(key, 0.0)
        if now - last < 60.0:
            return
        self._last_skip_log[key] = now
        log.info(
            f"TIME_DECAY_SKIP | sym={symbol} rsn={reason} {detail} | {ctx()}"
        )

    # Minimum hold times per strategy category (seconds)
    MINIMUM_HOLD_SECONDS = {
        "scalping": 300, "momentum": 900, "mean_reversion": 600,
        "funding_arb": 1800, "sentiment": 1800, "advanced": 600,
        "predatory": 300, "microstructure": 180, "time_based": 600,
        "cross_market": 900, "ai_enhanced": 600, "ai_generated": 600,
        "kickstart": 300, "default": 300,
    }

    def _determine_mode(self) -> str:
        """Determine watchdog mode: passive, safety_net, or emergency.

        PASSIVE: Default. Observe, collect events, don't act. Claude is boss.
        SAFETY_NET: Claude offline >10min, 3+ CLI errors, or 5+ consecutive losses.
                    Execute hard stop -3%, timer close, trailing exit. No new trades.
        EMERGENCY: Session PnL below ``settings.watchdog.emergency.session_pnl_threshold_pct``
                   (default -5 %) OR hard_stops_this_hour >= ``settings.watchdog
                   .emergency.hard_stops_per_hour_threshold`` (default 5; raised
                   from the pre-Phase-3.2 hardcoded 3 to reduce false-positive
                   emergencies during noisy hours). Close ALL positions. Stop
                   ALL trading.

        Layer 4 Realignment Phase 3.2 (2026-05-06): trigger reason is
        captured into ``self._last_emergency_trigger`` so the
        EMERGENCY_CLOSED event embeds the cause (session_pnl vs
        hard_stops) and operators can verify whether each emergency was
        justified after the fact.
        """
        import time as _t

        # Reset hourly hard stop counter
        if _t.monotonic() - self._hard_stop_hour_start > 3600:
            self._hard_stops_this_hour = 0
            self._hard_stop_hour_start = _t.monotonic()

        # Emergency checks (always apply, even during startup).
        # Phase 3.2: thresholds read from settings.watchdog.emergency
        # so the operator can tune without code change. The pre-fix
        # hardcoded values were -5.0 / 3; defaults now -5.0 / 5.
        em_cfg = getattr(self.settings.watchdog, "emergency", None)
        session_threshold = (
            float(em_cfg.session_pnl_threshold_pct) if em_cfg is not None else -5.0
        )
        hard_stops_threshold = (
            int(em_cfg.hard_stops_per_hour_threshold) if em_cfg is not None else 5
        )
        if self._session_pnl_pct < session_threshold:
            self._last_emergency_trigger = (
                f"session_pnl={self._session_pnl_pct:+.2f}%"
                f"<{session_threshold:+.2f}%"
            )
            return "emergency"
        if self._hard_stops_this_hour >= hard_stops_threshold:
            self._last_emergency_trigger = (
                f"hard_stops={self._hard_stops_this_hour}"
                f">={hard_stops_threshold}/h"
            )
            return "emergency"

        # Startup grace period: first 10 minutes, only escalate for CLI crashes
        uptime = _t.time() - self._started_at
        if uptime < 600:
            if self.claude_client and hasattr(self.claude_client, "_consecutive_failures"):
                if self.claude_client._consecutive_failures >= 3:
                    return "safety_net"
            return "passive"

        # Safety net checks (only after startup grace period).
        # Post-Layer-1 fix Phase 6: the ClaudeCodeClient now exposes a
        # consistent attribute triple:
        #   _last_call_attempt_time — refreshed BEFORE the subprocess spawn
        #   _last_response_time     — refreshed ONLY on a successful return
        #   _last_call_time         — kept as a success-time alias for
        #                              backwards compatibility
        # We use ``max(attempt_time, response_time)`` as the "any sign of
        # life" timestamp: a long in-flight call (attempt at T-7min, no
        # response yet) keeps ``_alive_at`` recent and doesn't false-
        # trip the 10-min staleness check. Once the 90s subprocess
        # timeout fires, no new attempt will refresh ``attempt_time``
        # and the check will eventually trip — which is what we want.
        if self.claude_client and hasattr(self.claude_client, "_last_call_attempt_time"):
            _attempt_t = float(getattr(self.claude_client, "_last_call_attempt_time", 0.0) or 0.0)
            _resp_t = float(getattr(self.claude_client, "_last_response_time", 0.0) or 0.0)
            _alive_at = max(_attempt_t, _resp_t)
            elapsed = _t.time() - _alive_at if _alive_at > 0 else 0.0
            if _alive_at > 0 and elapsed > 600:  # 10 minutes since last heartbeat
                return "safety_net"
        if self.claude_client and hasattr(self.claude_client, "_consecutive_failures"):
            if self.claude_client._consecutive_failures >= 3:
                return "safety_net"
        if self._consecutive_losses >= 5:
            return "safety_net"

        return "passive"

    async def tick(self) -> None:
        """One monitoring cycle with 3-mode system (#9)."""
        wid = new_watchdog_id()
        _tick_start = time.time()

        # Event-loop congestion detector: expected tick cadence is 10-30 s;
        # anything over 60 s between ticks means another coroutine starved us.
        # Phase 9e: emit WD_POLL_LAG as the milder precursor — configured
        # interval exceeded by >2× but not yet at the WD_TICK_GAP threshold.
        # Helps ops spot creeping contention before it becomes fatal.
        _prev_tick = getattr(self, "_last_tick_time", 0.0)
        if _prev_tick > 0.0:
            _gap = _tick_start - _prev_tick
            _cfg_interval = float(self.settings.watchdog.check_interval_seconds)
            if _gap > 60:
                log.warning(
                    f"WD_TICK_GAP | gap={_gap:.0f}s (expected <30s) event_loop_congested | {ctx()}"
                )
            elif _cfg_interval > 0 and _gap > _cfg_interval * 2:
                log.warning(
                    f"WD_POLL_LAG | configured={_cfg_interval:.0f}s "
                    f"actual={_gap:.0f}s ratio={_gap / _cfg_interval:.1f}x | {ctx()}"
                )
        self._last_tick_time = _tick_start

        # T6: Skip tick during exchange switch to prevent interference
        if hasattr(self, 'transformer') and self.transformer and self.transformer.is_switching:
            # Phase 12.6 (lifecycle-logging-audit Gap 6.4-G2): structured tag.
            log.info(f"WD_PAUSED | reason=exchange_switch_in_progress | {ctx()}")
            return

        # Step 1: Determine mode
        old_mode = getattr(self, "_watchdog_mode", "passive")
        self._watchdog_mode = self._determine_mode()
        if self._watchdog_mode != old_mode:
            log.warning(f"WD_MODE | old={old_mode} new={self._watchdog_mode} | {ctx()}")

        # Issue I1 (F-26 TIMESTAMP_FAIL, 2026-05-14) — use the
        # discriminated-result variant so a TIMESTAMP_FAIL or
        # transport-failure response does NOT trigger phantom closes
        # for every tracked position. The legacy get_positions() path
        # collapsed errors into ``[]`` which the close-detect logic at
        # L505 interpreted as "every tracked symbol vanished from
        # exchange." When confirmation is False, we preserve the
        # last-known state and skip the close-detection pass entirely.
        #
        # The iscoroutinefunction guard preserves backwards-compatibility
        # with PositionService stubs in tests (MagicMock auto-creates
        # attributes, so hasattr always returns True; we need a stronger
        # check that distinguishes a real async method from an auto-mock).
        # Real adapters (BybitDemoPositionService, ShadowPositionService,
        # Transformer's PositionServiceProxy) implement the method as
        # an async def; tests can opt-in by stubbing get_positions_with_confirmation
        # with AsyncMock.
        _gpwc = getattr(self.position_service, "get_positions_with_confirmation", None)
        if _gpwc is not None and inspect.iscoroutinefunction(_gpwc):
            _pos_result = await _gpwc()
            if not _pos_result.confirmed:
                log.warning(
                    f"WD_GROUND_TRUTH_UNKNOWN | reason={_pos_result.reason or 'unspecified'} "
                    f"last_known_n={len(self._last_known_symbols)} "
                    f"action=preserve_state | {ctx()}"
                )
                # Skip ALL downstream state mutations this tick. The next
                # confirmed-truth tick will catch any real changes.
                return
            positions = list(_pos_result.positions)
        else:
            positions = await self.position_service.get_positions()
        sym_list = ",".join(p.symbol for p in positions[:10]) if positions else "none"
        log.info(f"WD_TICK | mode={self._watchdog_mode} n={len(positions)} syms=[{sym_list}] | {ctx()}")

        # Zombie thesis reconciler — every 5 min close orphan theses whose
        # Shadow position has vanished. This is the safety net for any close
        # path that skipped coordinator.on_trade_closed (which normally
        # cascades to thesis_manager.close_thesis via registered callback).
        if self.thesis_manager and hasattr(self.thesis_manager, "reconcile_with_shadow"):
            _now = time.time()
            if _now - self._last_reconcile_at >= 300.0:
                self._last_reconcile_at = _now
                try:
                    _shadow_syms = {p.symbol for p in positions}
                    await self.thesis_manager.reconcile_with_shadow(_shadow_syms)
                except Exception as e:
                    log.warning(
                        f"ZOMBIE_RECONCILE_FAIL | stage=invoke err='{str(e)[:100]}' | {ctx()}"
                    )
                # Durable-open (2026-06-17): on the same 5-min cadence, resolve
                # any leftover status='reserving' rows — adopt (->open) when a
                # live position exists, else void. Closes the reserve-then-die /
                # finalize-failure window left by the thesis-before-order path.
                if hasattr(self.thesis_manager, "sweep_reserving_theses"):
                    try:
                        # `positions` here is the confirmed snapshot (this tick
                        # already returned early on unconfirmed ground truth).
                        # Pass the objects so the sweep can entry-price-match
                        # before adopting (no symbol-coincidence adoption).
                        await self.thesis_manager.sweep_reserving_theses(positions)
                    except Exception as e:
                        log.warning(
                            f"RESERVING_SWEEP_FAIL | stage=invoke err='{str(e)[:100]}' | {ctx()}"
                        )

        if not positions:
            # Phase 2 (P0-1) — empty Shadow set is the strongest possible
            # signal that everything we still track is a ghost. Run BOTH
            # the fast reconcile and the full close-detect pass: the fast
            # path emits GHOST_RECONCILED for any tracked symbol still in
            # our dicts; the full path drives the coordinator close-record.
            #
            # Issue I1 (F-26): this branch is now safe — confirmed=True
            # guarantees the empty list is exchange truth, not an API
            # error. Before I1, an API error could reach this branch
            # via get_positions's `return []` swallow.
            await self._reconcile_with_shadow_fast(positions)
            await self._detect_and_record_closes(set())
            if self.coordinator:
                self.coordinator.cleanup_stale()
            return

        # Phase 2 (P0-1) — close-detect runs BEFORE the monitoring loop so
        # ghost positions are pruned and broadcast to all subsystems before
        # `_monitor_position` wastes time on dead state. The post-loop
        # `_detect_and_record_closes(open_symbols)` call has been removed —
        # `open_symbols` here equals `{p.symbol for p in positions}`, which
        # is what the post-loop call previously reconstructed by accumulating
        # inside the loop. Order: fast reconcile first (catches Shadow-side
        # closes between WD ticks), then the full diff.
        _live_open_symbols = {p.symbol for p in positions}
        await self._reconcile_with_shadow_fast(positions)
        await self._detect_and_record_closes(_live_open_symbols)

        # Phase 4: Batch prefetch M5 klines for all watched symbols. One
        # partitioned DB query replaces N serial reads inside _monitor_position.
        # Failure here is non-fatal — the per-position path falls back to its
        # own per-symbol query.
        try:
            _syms = [p.symbol for p in positions]
            self._wd_klines_m5 = await self.market_repo.get_klines_batch(
                _syms, "5", 60,
            )
        except Exception as e:
            log.warning(
                f"WD_PREFETCH_FAIL | stage=klines_m5 err='{str(e)[:100]}' "
                f"n={len(positions)} | {ctx()}"
            )
            self._wd_klines_m5 = {}

        # Data Lake: snapshot all positions (#10)
        # HIGH-9 fix (2026-05-09): per-iteration tid_scope so logs from
        # write_position_snapshot (or any failure paths) carry the
        # iteration's symbol tid, not whatever was set before the loop.
        for pos in positions:
            with tid_scope(pos.symbol, "wd"):
                try:
                    if self.data_lake:
                        _dir = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                        _pnl = 0.0
                        if pos.entry_price > 0:
                            _pnl = ((pos.mark_price - pos.entry_price) / pos.entry_price) * 100
                            if _dir in ("Sell", "Short"):
                                _pnl = -_pnl
                        _age = 0.0
                        if self.coordinator:
                            _plan = self.coordinator.get_trade_plan(pos.symbol)
                            _age = _plan.age_minutes if _plan else 0.0
                        await self.data_lake.write_position_snapshot(
                            symbol=pos.symbol, direction=_dir,
                            entry_price=pos.entry_price, mark_price=pos.mark_price,
                            pnl_pct=round(_pnl, 2), unrealized_pnl=pos.unrealized_pnl,
                            age_minutes=round(_age, 1),
                        )
                except Exception as e:
                    log.debug("position snapshot write failed: {err}", err=str(e))

        # EMERGENCY MODE: close ALL positions immediately
        if self._watchdog_mode == "emergency":
            # Layer 4 Realignment Phase 3.2 (2026-05-06) — embed the
            # specific trigger reason (captured by _determine_mode)
            # in the log line + event-buffer entry so operators can
            # tell session_pnl-driven emergencies apart from hard-
            # stop-driven ones in audit history.
            _trigger = self._last_emergency_trigger or "unknown"
            log.error(
                "EMERGENCY MODE: Closing all {n} positions! trigger={trig}",
                n=len(positions), trig=_trigger,
            )
            # HIGH-9 fix (2026-05-09): per-iteration tid_scope.
            for pos in positions:
                with tid_scope(pos.symbol, "wd_emergency"):
                    try:
                        await self.position_service.close_position(pos.symbol, close_trigger="wd_emergency")
                        log.warning(
                            "EMERGENCY CLOSED: {sym} trigger={trig}",
                            sym=pos.symbol, trig=_trigger,
                        )
                        if self.event_buffer:
                            self.event_buffer.add_event(
                                "HIGH", "emergency_close", pos.symbol,
                                pnl_pct=getattr(pos, "unrealized_pnl", 0),
                                session_pnl=self._session_pnl_pct,
                                hard_stops_per_hour=self._hard_stops_this_hour,
                                trigger=_trigger,
                            )
                    except Exception as e:
                        # Phase 12.6 (Gap 6.4-G1): structured tag.
                        log.error(f"WD_EMERGENCY_CLOSE_FAIL | sym={pos.symbol} err='{str(e)[:120]}' | {ctx()}")
            if self.alert_manager:
                try:
                    await self.alert_manager.send_custom(
                        f"EMERGENCY: Closed all {len(positions)} positions.\n"
                        f"Trigger: {_trigger}\n"
                        f"Session PnL: {self._session_pnl_pct:+.2f}%\n"
                        f"Hard stops this hour: {self._hard_stops_this_hour}\n"
                        f"Manual restart required.",
                        AlertLevel.CRITICAL,
                    )
                except Exception as e:
                    log.debug("emergency alert send failed: {err}", err=str(e))
            return

        # DUPLICATE POSITION DETECTION: close the worse PnL duplicate
        position_by_symbol: dict = {}
        # HIGH-9 fix (2026-05-09): per-iteration tid_scope so the
        # close_position log lines (and any nested set_stop_loss /
        # CLOSE_FILL_CONFIRMED logs) carry the correct symbol tid.
        for pos in positions:
            with tid_scope(pos.symbol, "wd_dup"):
                if pos.symbol in position_by_symbol:
                    existing = position_by_symbol[pos.symbol]
                    existing_pnl = getattr(existing, 'unrealized_pnl', 0) or 0
                    new_pnl = getattr(pos, 'unrealized_pnl', 0) or 0
                    worse = pos if new_pnl < existing_pnl else existing
                    try:
                        await self.position_service.close_position(worse.symbol, close_trigger="wd_dup_close")
                        log.warning(
                            "DUPLICATE CLOSED: {sym} (kept better PnL position)",
                            sym=worse.symbol,
                        )
                    except Exception as e:
                        # Phase 12.6 (Gap 6.4-G1): structured tag.
                        log.error(f"WD_DUP_CLOSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
                else:
                    position_by_symbol[pos.symbol] = pos

        # Execute any strategic actions queued by LayerManager
        await self._execute_strategic_actions()

        # Execute SENTINEL advisor recommendations (stop tightening only)
        await self._execute_sentinel_recommendations()

        open_symbols: set[str] = set()
        for pos in positions:
            open_symbols.add(pos.symbol)

            # HIGH-9 fix (2026-05-09): set tid AT THE TOP of every
            # iteration so logs in the immunity / maturity / pre-monitor
            # phases (lines below up to the existing set_tid at line ~704)
            # carry THIS iteration's tid. Pre-fix those log lines (WD_NOTE,
            # WD_MATURITY_TICKER_FAIL, etc.) inherited whatever tid was
            # last set by an earlier loop (sniper M3/M4 last iter or one
            # of the watchdog data_lake/emergency/dup loops above).
            set_tid(f"t-{pos.symbol}-mon")

            # IMMUNITY CHECK (via coordinator, fallback to local tracking)
            if self.coordinator:
                is_immune, remaining, reason = self.coordinator.is_immune(pos.symbol)
                if is_immune:
                    if int(remaining) % 60 < 12:
                        # Phase 12.6 (Gap 6.4-G2): structured tag.
                        log.info(f"WD_NOTE | sym={pos.symbol} reason='{str(reason)[:120]}' | {ctx()}")
                    self._log_skip(pos.symbol, "immune", f"remaining={remaining:.0f}s")
                    continue
            else:
                # Fallback: local tracking when coordinator not available
                if pos.symbol not in self._position_open_times:
                    self._position_open_times[pos.symbol] = datetime.now(timezone.utc)
                    cat = await self._get_position_strategy_category(pos.symbol)
                    self._position_strategies[pos.symbol] = cat
                open_time = self._position_open_times[pos.symbol]
                age_seconds = (datetime.now(timezone.utc) - open_time).total_seconds()
                strategy_cat = self._position_strategies.get(pos.symbol, "default")
                min_hold = self.MINIMUM_HOLD_SECONDS.get(strategy_cat, 300)
                if age_seconds < min_hold:
                    continue

            # MATURITY CHECK (via coordinator)
            # After the 2026-04-22 SL Hierarchy overhaul the only phase
            # that blocks monitoring is 'newborn' (0-120s grace). The
            # skip reason is 'immune' to align with the fix doc's design
            # intent (a single 120s grace period, not a multi-phase
            # ramp) and to make the `rsn=immune` count in verification
            # inclusive of both the strategy-specific immunity gate
            # above AND this universal grace cap below.
            if self.coordinator:
                # Phase 9c (test_one_position_error_doesnt_block_others):
                # the maturity-check ticker fetch must NOT crash the
                # whole tick if a single symbol's REST call throws.
                # Pre-fix, an unhandled get_ticker exception here
                # propagated up through the per-position loop and
                # blocked monitoring of every subsequent position. The
                # per-position try/except below only wraps
                # ``_monitor_position``, not this maturity check, so a
                # transient API error against one coin starved the
                # rest of the universe of monitoring. Catch + skip.
                try:
                    ticker = await self.market_service.get_ticker(pos.symbol)
                    pnl_pct = self._calculate_pnl_pct(pos, ticker.last_price)
                    sl_prox = self._calculate_sl_proximity(pos, ticker.last_price) or 0
                except Exception as e:
                    log.warning(
                        f"WD_MATURITY_TICKER_FAIL | sym={pos.symbol} "
                        f"err='{str(e)[:100]}' | {ctx()}"
                    )
                    self._log_skip(pos.symbol, "ticker_fail", f"err={str(e)[:60]}")
                    continue
                can_close, phase, maturity_reason = self.coordinator.get_maturity(
                    pos.symbol, pnl_pct, sl_prox,
                )
                if not can_close:
                    log.debug(
                        "Watchdog: {sym} [{phase}] — {reason}",
                        sym=pos.symbol, phase=phase, reason=maturity_reason,
                    )
                    self._log_skip(pos.symbol, "immune", f"phase={phase}")
                    continue
                # Update peak PnL for trailing stop tracking
                self.coordinator.update_peak_pnl(pos.symbol, pnl_pct)

            _pos_t0 = time.time()
            # Phase 9b: per-position trace-id (now redundantly set at the
            # top of the loop body too — HIGH-9 fix at line ~629). Kept
            # here for safety in case future restructuring removes the
            # earlier set; tid is identical so this is a no-op.
            try:
                # Phase 5 (P0-4): per-position timeout. Without this, one
                # slow position (DB lock, slow REST call) can blow the
                # entire WD_TICK budget, propagating WD_POLL_LAG across
                # the rest of the universe. 3 s matches the brief's Fix C
                # and is generous vs the typical 50-200 ms per-position
                # budget. Failures still log via the existing exception
                # handler below.
                await asyncio.wait_for(
                    self._monitor_position(pos), timeout=3.0,
                )
            except asyncio.TimeoutError:
                log.warning(
                    f"WD_MONITOR_TIMEOUT | sym={pos.symbol} timeout=3.0s | {ctx()}"
                )
            except Exception as e:
                log.error(
                    "Watchdog error monitoring {sym}: {err}",
                    sym=pos.symbol, err=str(e),
                )
            finally:
                _pos_el_ms = (time.time() - _pos_t0) * 1000
                if _pos_el_ms > 2000:
                    # Compute PnL from position data only (no service calls); swallow errors defensively.
                    _pos_pnl = 0.0
                    try:
                        if pos.entry_price > 0 and pos.mark_price > 0:
                            _pos_pnl = ((pos.mark_price - pos.entry_price) / pos.entry_price) * 100.0
                            _side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                            if _side_val in ("Sell", "Short"):
                                _pos_pnl = -_pos_pnl
                    except Exception:
                        pass
                    log.warning(
                        f"WD_MONITOR_SLOW | sym={pos.symbol} el={_pos_el_ms:.0f}ms pnl={_pos_pnl:+.2f}% | {ctx()}"
                    )

        # Phase 9b: clear the per-position tid so the post-loop calls
        # (cleanup_stale, WD_TICK_DONE summary) don't inherit the last
        # monitored symbol's tid.
        set_tid("")

        # Phase 2 (P0-1) — close-detect was hoisted above the monitoring
        # loop. The previous post-loop `_detect_and_record_closes(open_symbols)`
        # call is intentionally removed: ghosts must not be allowed to
        # survive an entire WD_TICK before being reconciled. Symbols that
        # disappear DURING the monitoring loop (rare but possible) are
        # caught on the next tick's pre-loop reconcile within at most
        # `fast_reconcile_seconds` (default 30s).

        # Tick summary — STRAT_CYCLE_DONE equivalent for the watchdog
        _tick_el_ms = (time.time() - _tick_start) * 1000
        _td_active = len(getattr(self, "_td_states", {}))
        log.info(
            f"WD_TICK_DONE | mode={self._watchdog_mode} n={len(positions)} "
            f"el={_tick_el_ms:.0f}ms td_active={_td_active} | {ctx()}"
        )
        if _tick_el_ms > 5000:
            log.warning(
                f"WD_TICK_SLOW | el={_tick_el_ms:.0f}ms n={len(positions)} mode={self._watchdog_mode} | {ctx()}"
            )

    async def _push_sl_to_shadow(
        self,
        *,
        symbol: str,
        new_sl: float,
        plan,
        current_shadow_sl,
        direction: str,
        source: str,
    ) -> bool:
        """Single point of truth for propagating an SL change to the exchange.

        Layer 3 hardening — covers three coupled bugs in one place:

          - Bug 2 guard: refuses to LOOSEN the current Shadow SL. Trail and
            Mode4 trail can compute new SL values that move with price; if
            SENTINEL has already tightened tighter, propagating the trail
            value would silently undo SENTINEL's protection.
          - Bug 1 sync: on a successful set_stop_loss, mirrors the new SL
            onto the local TradePlan (``plan.stop_loss_price = new_sl``) so
            downstream reads — especially the ``sl_buffer_ok`` early-exit
            gate at lines 528-540 — see truth instead of the original
            stop-loss frozen at trade open.
          - Bug 4 obs: every outcome (push / skip / fail) gets a visible
            ``SL_PROPAGATED`` / ``SL_PROPAGATE_SKIP`` / ``SL_PROPAGATE_FAIL``
            log line. Previous trail paths swallowed failures with
            log.debug, hiding propagation issues at default log level.

        SL Hierarchy overhaul (2026-04-22)
        ----------------------------------
        When ``self.sl_gateway`` is injected, all validation (tighter-only,
        min-distance, max-step, rate-limit) and the wire push itself are
        delegated to the gateway. ``SL_GATEWAY_ACCEPT`` is emitted by the
        gateway as the policy-level outcome; this helper keeps the
        wire-level ``SL_PROPAGATED`` tag for backwards dashboard
        compatibility.

        Plan mirror happens HERE, not in the gateway — gateway stays
        domain-agnostic. Mirror runs only when gateway accepts AND the
        wire push succeeded (gateway guarantees both on ``accepted=True``).

        Existing decision-tag logs (SENTINEL_DEADLINE_SL, SENTINEL_ADVISOR_SL,
        STRAT_ACTION_SL, TRAILING ON, etc.) are PRESERVED at the call site;
        this helper logs the wire-level outcome.
        """
        if new_sl is None or new_sl <= 0:
            return False

        # ── Phase 7: no-op guard ──
        # If the proposed SL rounds to the same value already on the wire,
        # the gateway will log REJECT_WOULD loosening / REJECT_WOULD rate_limit
        # and we'd have burned one of the R4 rate-limit slots for nothing.
        # Catch this at the consumer so the gateway never sees it.
        if current_shadow_sl and current_shadow_sl > 0:
            try:
                _diff_ratio = abs(new_sl - current_shadow_sl) / current_shadow_sl
            except Exception:
                _diff_ratio = 1.0
            if _diff_ratio < 1e-4:  # <1 basis point
                log.debug(
                    f"TIME_DECAY_SKIP_NOOP | sym={symbol} src={source} "
                    f"new={new_sl:.6f} cur={current_shadow_sl:.6f} "
                    f"diff_bps={_diff_ratio * 10000:.2f} | {ctx()}"
                )
                return False

        # ── P3-2 (2026-05-13): rate-limit pre-check ──
        # Extends the T2-6 short-circuit pattern (already in
        # ``profit_sniper.py`` for ``source=profit_sniper_trail``) to
        # the four uncoordinated sources that fall through this
        # wrapper: ``trail_update``, ``sentinel_deadline``,
        # ``sentinel_advisor``, ``trail_activation``. Before P3-2 these
        # sources submitted blindly and produced 18 ``rsn=rate_limit``
        # rejects per 24 h (operator-visible log noise; gateway absorbed
        # the cost). After P3-2 the same skip path is observed via
        # ``SNIPER_RATE_LIMIT_AWARE_SKIP src=<source>`` and the gateway
        # never sees the wasted apply.
        #
        # Placement is intentional:
        #   * AFTER the no-op guard above so identical-SL calls return
        #     False without consulting the gateway either.
        #   * BEFORE the source-specific coalesce blocks so a blocked
        #     call does NOT advance the coalesce timestamps (which
        #     would silently delay the next legitimate call after the
        #     30 s window expires).
        #
        # ``next_eligible_in_seconds`` is a stateless query on the
        # gateway — see ``src/core/sl_gateway.py:183``. ``> 0.0`` means
        # the per-symbol R4 window is still active. The retry path is
        # the source's natural next tick (watchdog runs at 5–10 s).
        # ``self.sl_gateway is not None`` guard preserves the legacy
        # unit-test path that doesn't wire a gateway.
        if self.sl_gateway is not None:
            _p3_2_remaining_s = self.sl_gateway.next_eligible_in_seconds(symbol)
            if _p3_2_remaining_s > 0.0:
                log.info(
                    f"SNIPER_RATE_LIMIT_AWARE_SKIP | sym={symbol} "
                    f"next_eligible_in_s={_p3_2_remaining_s:.1f} "
                    f"src={source} | {ctx()}"
                )
                return False

        # ── Phase 7: Time-Decay consumer-side coalescing ──
        # Time-Decay runs every 10 s per position; the gateway enforces a
        # 30 s rate-limit per symbol. Without coalescing, the gateway
        # rejects 2/3 of TD pushes with REJECT_WOULD rate_limit. A 10 s
        # consumer-side window gives TD enough cadence without spamming.
        # Only TD is coalesced — SENTINEL / TRAILING / STRAT have their
        # own cadence and must not be throttled here.
        if source == "time_decay":
            if not hasattr(self, "_last_td_push_at"):
                self._last_td_push_at: dict[str, float] = {}
            _now_m = time.monotonic()
            _last = self._last_td_push_at.get(symbol, 0.0)
            if _now_m - _last < 10.0:
                log.debug(
                    f"TIME_DECAY_COALESCE | sym={symbol} "
                    f"last={_now_m - _last:.1f}s_ago new={new_sl:.6f} | {ctx()}"
                )
                return False
            # Record intent BEFORE push — two concurrent TD calls race into
            # the same slot; recording now ensures the second one is
            # coalesced even if the first is still in-flight at the gateway.
            self._last_td_push_at[symbol] = _now_m

        # ── T1-2 / F8 trail-source coalescing (six-tier-fixes 2026-05-11) ──
        # Sibling to the time_decay coalesce above. Watchdog ticks every
        # 5 s and trail_update / trail_activation can submit on every
        # tick that price moves. A 10 s consumer-side window prevents
        # in-flight thrash for trail sources after the step-clamp below
        # reshapes a single rejected large step into N small accepted
        # ones. R4 rate_limit_seconds (30 s, gateway-enforced) remains
        # the upper bound.
        if source in ("trail_activation", "trail_update"):
            if not hasattr(self, "_last_trail_push_at"):
                self._last_trail_push_at: dict[str, float] = {}
            _now_m = time.monotonic()
            _last = self._last_trail_push_at.get(symbol, 0.0)
            if _now_m - _last < 10.0:
                log.debug(
                    f"WD_TRAIL_COALESCE | sym={symbol} src={source} "
                    f"last={_now_m - _last:.1f}s_ago new={new_sl:.8f} | {ctx()}"
                )
                return False
            self._last_trail_push_at[symbol] = _now_m

        # ── T5-3 / F5 sentinel-source coalescing (six-tier-fixes 2026-05-11) ──
        # Live evidence at 13:43:50 named ``sentinel_advisor`` as the 4th
        # SL writer racing for the per-symbol 30 s gateway slot. Mirror
        # the trail and time_decay coalesce patterns: 10 s consumer-side
        # window keeps sentinel proposals from spamming the gateway when
        # the rate-limit is still active. Single-writer-of-record
        # consolidation (Architectural Theme 1) remains out of scope for
        # this engagement; this coalesce is the minimal F5 mitigation.
        if source in ("sentinel_advisor", "sentinel_deadline"):
            if not hasattr(self, "_last_sentinel_push_at"):
                self._last_sentinel_push_at: dict[str, float] = {}
            _now_m = time.monotonic()
            _last = self._last_sentinel_push_at.get(symbol, 0.0)
            if _now_m - _last < 10.0:
                log.debug(
                    f"WD_SENTINEL_COALESCE | sym={symbol} src={source} "
                    f"last={_now_m - _last:.1f}s_ago new={new_sl:.8f} | {ctx()}"
                )
                return False
            self._last_sentinel_push_at[symbol] = _now_m

        # ── T1-2 / F8 step-clamp (six-tier-fixes 2026-05-11) ──
        # Port-forward of profit_sniper.py SNIPER_CAP (lines 1469-1524).
        # Without this, trail_activation / trail_update compute steps
        # that exceed the gateway's R3 max_step_pct (currently 0.25 % in
        # config.toml) and the SL never advances. After this clamp the
        # watchdog submits a step at most max_step_pct wide; gateway R3
        # becomes a safety net for genuine rogue computations (the
        # RIVERUSDT 2.5 % strangulation case cited in sl_gateway.py:5-11)
        # rather than a production blocker for legitimate trail catch-up.
        # Trail SL advances over multiple ticks instead of one. R1
        # tighten-only is preserved by construction.
        #
        # Scope: TRAIL SOURCES ONLY. Strategic-action sources
        # (`watchdog_tighten`, `STRAT_ACTION_SL`) carry a Claude-decided
        # deliberate SL target; clamping those would silently spread
        # one Claude directive over N ticks, surprising the operator.
        # `time_decay` has its own original_sl_pct cap upstream.
        # `sentinel_advisor` and `sentinel_deadline` are coalesced
        # separately (above) but propose deliberate values that are
        # rarely cap-exceeding in practice — keeping them clamp-free
        # avoids a behaviour change to the SENTINEL contract.
        if (
            source in ("trail_activation", "trail_update")
            and current_shadow_sl is not None
            and current_shadow_sl > 0
        ):
            _gw_cfg = getattr(self.settings, "sl_gateway", None)
            _max_step_pct = (
                float(getattr(_gw_cfg, "max_step_pct", 0.5))
                if _gw_cfg is not None
                else 0.5
            )
            _requested_step_pct = round(
                abs(new_sl - current_shadow_sl) / current_shadow_sl * 100.0, 6
            )
            if _requested_step_pct > _max_step_pct:
                if direction in ("Buy", "Long"):
                    _capped = current_shadow_sl * (1.0 + _max_step_pct / 100.0)
                else:
                    _capped = current_shadow_sl * (1.0 - _max_step_pct / 100.0)
                log.info(
                    f"WD_TRAIL_STEP_CLAMPED | sym={symbol} src={source} "
                    f"requested_pct={_requested_step_pct:.3f}% "
                    f"capped_pct={_max_step_pct:.3f}% "
                    f"raw_new_sl={new_sl:.8f} capped_new_sl={_capped:.8f} "
                    f"cur_sl={current_shadow_sl:.8f} dir={direction} | {ctx()}"
                )
                new_sl = _capped

        # ── Gateway delegation (primary path) ──
        if self.sl_gateway is not None:
            result = await self.sl_gateway.apply(
                symbol=symbol,
                new_sl=new_sl,
                source=source,
                direction=direction,
                plan=plan,
                current_sl=current_shadow_sl,
                # Phase 1 owner switch — supply entry so the gateway can compute
                # the trade's green/red state for the owner gate. None-safe: a
                # plan without an entry leaves state undeterminable and the gate
                # fails open.
                entry_price=getattr(plan, "entry_price", None),
                # current_price intentionally omitted — gateway fetches
                # from market_service only if a rule needs it (skipped in
                # pass-through mode).
            )
            if not result.accepted:
                return False
            # Bug 1 sync — only after gateway accept + wire-success
            # (gateway guarantees both when accepted=True is returned).
            # PF/LC Top-15 Problem 1.4 — mirror and log the value the gateway
            # ACTUALLY wrote (result.new_sl_applied) after any R2/R3 clamp, not
            # the pre-gateway target, so the plan and the log match the broker.
            _na = result.new_sl_applied
            _applied_sl = (
                _na if isinstance(_na, (int, float)) and _na > 0 else new_sl
            )
            _sl_clamped = abs(_applied_sl - new_sl) > 1e-12
            if plan is not None:
                plan.stop_loss_price = _applied_sl
            prev_str = (
                f"{current_shadow_sl:.6f}"
                if (current_shadow_sl is not None and current_shadow_sl > 0)
                else "unknown"
            )
            log.info(
                f"SL_PROPAGATED | sym={symbol} new={_applied_sl:.6f} "
                + (f"target={new_sl:.6f} clamped=Y " if _sl_clamped else "")
                + f"prev={prev_str} src={source} | {ctx()}"
            )
            return True

        # ── Legacy path (sl_gateway=None, e.g. unit tests without DI) ──
        # Preserves the exact pre-gateway behavior verbatim so the gateway
        # rollout doesn't change semantics on code paths that haven't been
        # wired yet.
        if current_shadow_sl is not None and current_shadow_sl > 0:
            if direction in ("Buy", "Long") and new_sl <= current_shadow_sl:
                log.info(
                    f"SL_PROPAGATE_SKIP | sym={symbol} new={new_sl:.6f} "
                    f"cur={current_shadow_sl:.6f} src={source} rsn=not_tighter | {ctx()}"
                )
                return False
            if direction in ("Sell", "Short") and new_sl >= current_shadow_sl:
                log.info(
                    f"SL_PROPAGATE_SKIP | sym={symbol} new={new_sl:.6f} "
                    f"cur={current_shadow_sl:.6f} src={source} rsn=not_tighter | {ctx()}"
                )
                return False

        try:
            ok = await self.position_service.set_stop_loss(symbol, new_sl)
        except Exception as e:
            log.warning(
                f"SL_PROPAGATE_FAIL | sym={symbol} new={new_sl:.6f} "
                f"src={source} err='{str(e)[:120]}' | {ctx()}"
            )
            return False

        if not ok:
            log.warning(
                f"SL_PROPAGATE_FAIL | sym={symbol} new={new_sl:.6f} "
                f"src={source} rsn=service_returned_false | {ctx()}"
            )
            return False

        if plan is not None:
            plan.stop_loss_price = new_sl

        prev_str = f"{current_shadow_sl:.6f}" if (current_shadow_sl is not None and current_shadow_sl > 0) else "unknown"
        log.info(
            f"SL_PROPAGATED | sym={symbol} new={new_sl:.6f} prev={prev_str} "
            f"src={source} | {ctx()}"
        )
        return True

    async def _tighten_sl_breakeven_30pct(self, pos) -> bool:
        """Tighten the position's SL 30% toward break-even (entry price).

        Issue 1 (2026-05-18) — invoked when the brain-close scoring
        recommendation is ``"reject_and_tighten"`` (composite < 0).
        Brain wanted out; the scoring says hold; we compensate by
        moving the stop closer to entry by 30 percent of the remaining
        SL→entry distance. ``_push_sl_to_shadow`` enforces tighter-only
        and break-even protection so a malformed delta cannot widen
        the SL or push past entry.

        Args:
            pos: Position object (must have ``symbol``, ``side``,
                ``entry_price``, ``stop_loss``).

        Returns:
            True if the push succeeded; False if any pre-condition
            failed (no plan, missing SL, push rejected by guard).
        """
        try:
            entry = float(getattr(pos, "entry_price", 0.0) or 0.0)
            current_sl = float(getattr(pos, "stop_loss", 0.0) or 0.0)
            if entry <= 0 or current_sl <= 0:
                return False
            side_val = getattr(pos, "side", None)
            direction = getattr(side_val, "value", side_val)
            direction = str(direction or "")
            # Tighten 30% of remaining (entry - sl) toward entry.
            # Same direction-aware geometry both sides:
            # Buy: sl < entry; new_sl = sl + 0.30 * (entry - sl).
            # Sell: sl > entry; new_sl = sl - 0.30 * (sl - entry) = sl + 0.30 * (entry - sl).
            delta = (entry - current_sl) * 0.30
            new_sl = current_sl + delta
            plan = None
            if self.coordinator is not None:
                try:
                    plan = self.coordinator.get_trade_plan(pos.symbol)
                except Exception:
                    plan = None
            pushed = await self._push_sl_to_shadow(
                symbol=pos.symbol,
                new_sl=new_sl,
                plan=plan,
                current_shadow_sl=current_sl,
                direction=direction,
                source="wd_brain_scoring",
            )
            return bool(pushed)
        except Exception as e:
            log.warning(
                f"WD_BRAIN_SCORE_TIGHTEN_FAIL | sym={getattr(pos, 'symbol', '?')} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )
            return False

    def _compute_structural_invalidation(
        self,
        *,
        symbol: str,
        side: str,
        state: TimeDecayState,
    ) -> tuple[bool, str]:
        """DEPRECATED (Phase 4.3, 2026-05-06) — call
        ``self.layer4_protection.compute_structural_invalidation``
        instead. The canonical implementation now lives on
        ``src/risk/layer4_protection.py:Layer4ProtectionService``;
        this method is preserved as a back-compat fallback for boots
        where WorkerManager has not yet built the service or for
        legacy tests that instantiate the watchdog directly. New
        callers MUST use the service so behaviour stays consistent
        across watchdog, sniper, and any future close path.

        Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
        compute whether the position has structural evidence of failure.

        Returns a tuple of (invalidation, reason_string). The reason
        carries the evidence found (or a ``no_data:...`` token explaining
        why the gate fail-safed). The watchdog passes the tuple into
        ``time_decay_sl.calculate()`` which honors it via the
        ``structural_invalidation_required`` knob.

        Disjunction across three signals (matches IMPLEMENT doc Part D):

        - XRAY confidence has dropped by >= ``cfg.xray_drop_threshold``
          (default 0.40 → 40 %) from the entry-time anchor.
        - Setup-type at entry is non-empty and differs from the current
          dominant pattern (e.g., BULLISH_FVG_OB → BEARISH_FVG_OB).
        - Regime has inverted to the opposite direction with confidence
          >= ``cfg.regime_inversion_confidence_threshold`` (default 0.60).
          Regime weakening to RANGING / VOLATILE / DEAD does NOT count
          (matches the IMPLEMENT doc test case: "Long with entry
          trending_up, current ranging → invalidation NOT triggered").

        Cache-miss / cold-start fail-safe: any missing input (services
        unwired, structure cache miss, regime not yet computed, no entry
        anchor on state) returns ``(False, "no_data:<which>")`` so the
        calculator BLOCKS force-close. Aligned with the operator
        philosophy of preferring false-negative invalidations over
        false-positive force-closes — a structurally healthy trade
        killed on a cold-start race is the worst outcome here.
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

    async def _handle_time_decay(
        self, pos: Position, plan, pnl_pct: float, current_price: float,
    ) -> bool:
        """Loser-lane dynamic SL management.

        Called from _monitor_position only when pnl_pct < 0 and plan is
        present. Lazy-inits per-symbol TimeDecayState on first loser tick,
        runs the 5-model calculator, and either:
          - tightens SL via _push_sl_to_shadow (reuses tighter-only guard)
          - force-closes the position (matches hard_stop close template)
          - no-ops (grace period or not-tighter)

        Returns:
            True if the position was closed (caller should return);
            False otherwise (caller should fall through to timeout/etc).
        """
        if self._time_decay is None:
            return False

        # ── Cooldown guard ──
        # If the coordinator just closed this symbol (hard_stop, timeout,
        # time_decay force-close, etc.), it sets a cooldown window. Skip
        # Time-Decay processing entirely until the position is cleared by
        # _detect_and_record_closes — otherwise we'd re-init state and
        # potentially re-fire a force-close on an already-closing position.
        # Issue 3 (2026-05-18) — uses the new symbol-level
        # is_symbol_in_any_cooldown helper after the legacy
        # is_symbol_cooled_down was removed in issue3/p3-3. Symbol-level
        # check matches the legacy intent (any recent close on this
        # symbol means a re-init would be unsafe).
        if (
            self.coordinator
            and hasattr(self.coordinator, "is_symbol_in_any_cooldown")
            and self.coordinator.is_symbol_in_any_cooldown(pos.symbol)
        ):
            # Phase 10: surface the silent skip so investigators can tell
            # "Time-Decay never ran" apart from "Time-Decay ran then stopped".
            log.debug(f"TIME_DECAY_SKIP_COOLDOWN | sym={pos.symbol} | {ctx()}")
            return False

        state = self._td_states.get(pos.symbol)

        # ── 1. Lazy-init state on first loser tick ──
        if state is None:
            # Volatility snapshot (async, has safe fallback). Pull both the
            # ATR and the class — the class drives per-volatility grace
            # window + atr_room multiplier inside TimeDecayConfig.
            atr_5m_pct = 0.5
            vol_class: str | None = None
            if self.volatility_profiler is not None:
                try:
                    vp = await self.volatility_profiler.get_profile(pos.symbol)
                    if vp is not None:
                        atr_5m_pct = max(vp.atr_pct_5m, 0.05)
                        vol_class = vp.volatility_class
                except Exception as e:
                    log.debug(
                        f"TIME_DECAY_VP_FAIL | sym={pos.symbol} "
                        f"err='{str(e)[:80]}'"
                    )

            # Per-coin regime confidence (sync)
            regime_conf = 0.5
            if self.regime_detector is not None:
                try:
                    cr = self.regime_detector.get_coin_regime(pos.symbol)
                    if cr is not None:
                        regime_conf = cr.confidence
                except Exception:
                    pass

            # Derive original SL % from plan; fallback to hard_stop (3%)
            original_sl_pct = 3.0
            if (
                plan.stop_loss_price
                and plan.stop_loss_price > 0
                and plan.entry_price > 0
            ):
                if plan.direction in ("Buy", "Long"):
                    original_sl_pct = (
                        (plan.entry_price - plan.stop_loss_price)
                        / plan.entry_price
                    ) * 100.0
                else:
                    original_sl_pct = (
                        (plan.stop_loss_price - plan.entry_price)
                        / plan.entry_price
                    ) * 100.0
                original_sl_pct = max(original_sl_pct, 0.15)

            max_hold_s = max(int(plan.max_hold_minutes * 60), 60)
            tick_s = float(self.settings.watchdog.check_interval_seconds)

            # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06)
            # — load entry-time XRAY/regime anchors. Read from
            # TradeCoordinator.TradeState first (runtime-fast); fall back
            # to a one-shot SELECT on `trade_thesis` for in-flight
            # positions whose state was lost across a watchdog restart
            # (Hybrid anchor design — operator-confirmed). Any missing
            # value falls through to neutral defaults; the structural-
            # invalidation gate then fail-safes to "no_data:no_entry_anchor"
            # and BLOCKS force-close until the position closes naturally.
            _entry_xray_conf = 0.0
            _entry_setup_type = ""
            _entry_regime_at_open = ""
            _entry_regime_conf = 0.0
            _anchor_source = "missing"
            try:
                ts = (
                    self.coordinator._trades.get(pos.symbol)
                    if self.coordinator is not None else None
                )
                if ts is not None and float(getattr(ts, "entry_xray_confidence", 0.0) or 0.0) > 0.0:
                    _entry_xray_conf = float(ts.entry_xray_confidence)
                    _entry_setup_type = str(getattr(ts, "entry_setup_type", "") or "")
                    _entry_regime_at_open = str(getattr(ts, "entry_regime_at_open", "") or "")
                    _entry_regime_conf = float(getattr(ts, "entry_regime_confidence", 0.0) or 0.0)
                    _anchor_source = "trade_state"
            except Exception:
                pass
            if _anchor_source == "missing" and self.db is not None:
                try:
                    row = await self.db.fetch_one(
                        "SELECT entry_xray_confidence, entry_setup_type, "
                        "entry_regime_at_open, entry_regime_confidence "
                        "FROM trade_thesis "
                        "WHERE symbol = ? AND status = 'open' "
                        "ORDER BY opened_at DESC LIMIT 1",
                        (pos.symbol,),
                    )
                    if row is not None and float(row["entry_xray_confidence"] or 0.0) > 0.0:
                        _entry_xray_conf = float(row["entry_xray_confidence"])
                        _entry_setup_type = str(row["entry_setup_type"] or "")
                        _entry_regime_at_open = str(row["entry_regime_at_open"] or "")
                        _entry_regime_conf = float(row["entry_regime_confidence"] or 0.0)
                        _anchor_source = "trade_thesis"
                except Exception as e:
                    log.debug(
                        f"TIME_DECAY_ANCHOR_DB_FAIL | sym={pos.symbol} "
                        f"err='{str(e)[:80]}' | {ctx()}"
                    )

            # T1-2 (2026-05-12): inherit MAE high-water-mark from a prior
            # incarnation of this position's state if the watchdog
            # snapshotted one before deletion. Default 0.0 (no history)
            # is a no-op inside create_state's seeding branch.
            _prior_mae = self._td_mae_high_water.get(pos.symbol, 0.0)
            state = self._time_decay.create_state(
                symbol=pos.symbol,
                direction=plan.direction,
                entry_price=plan.entry_price,
                original_sl_pct=original_sl_pct,
                max_hold_seconds=max_hold_s,
                atr_5m_pct=atr_5m_pct,
                regime_confidence=regime_conf,
                volatility_class=vol_class,
                tick_seconds=tick_s,
                entry_xray_confidence=_entry_xray_conf,
                entry_setup_type=_entry_setup_type,
                entry_regime_at_open=_entry_regime_at_open,
                entry_regime_confidence=_entry_regime_conf,
                prior_mae_pct=_prior_mae,
            )
            log.info(
                f"TIME_DECAY_ANCHOR_LOAD | sym={pos.symbol} "
                f"xray={_entry_xray_conf:.2f} setup={_entry_setup_type or '-'} "
                f"regime={_entry_regime_at_open or '-'} regime_conf={_entry_regime_conf:.2f} "
                f"source={_anchor_source} | {ctx()}"
            )
            # T1-2 (2026-05-12): forensic state-creation log so we can
            # measure how often the high-water mark is actually being
            # restored across recreation, and which anchor source was
            # used. Pair with TIME_DECAY_STATE_DESTROY at the three
            # deletion sites for a full lifecycle trace.
            log.info(
                f"TIME_DECAY_STATE_CREATE | sym={pos.symbol} "
                f"mae={state.mae_pct:+.2f}% "
                f"seeded_from_prior={'true' if _prior_mae < 0 else 'false'} "
                f"prior_mae={_prior_mae:+.2f}% anchor_source={_anchor_source} "
                f"| {ctx()}"
            )
            # Seed last_pnl so the first observe() doesn't produce a huge
            # spurious velocity from 0.0 → pnl_pct
            state.last_pnl_pct = pnl_pct
            self._td_states[pos.symbol] = state
            # Observability: expose the per-class values that will drive this
            # position's grace gate and atr_room so operators can correlate
            # behaviour with class at a glance.
            _cls_str = vol_class or "medium"
            _cfg = self._time_decay.cfg
            _grace_s = _cfg.grace_seconds_by_class.get(_cls_str, _cfg.grace_seconds)
            _atr_mult = _cfg.atr_room_multiplier_by_class.get(
                _cls_str, _cfg.atr_room_multiplier,
            )
            log.info(
                f"TIME_DECAY_INIT | sym={pos.symbol} dir={plan.direction} "
                f"sl={original_sl_pct:.2f}% atr={atr_5m_pct:.2f}% "
                f"cls={_cls_str} "
                f"p_win={state.p_win:.2f} regime_conf={regime_conf:.2f} "
                f"max_hold_s={max_hold_s} grace_s={_grace_s} "
                f"atr_mult={_atr_mult:.2f} | {ctx()}"
            )
            return False  # First tick: no action, state seeded

        # ── 2. Observe velocity/acceleration (updates state) ──
        velocity, acceleration = td_observe(state, pnl_pct)

        # ── 3. Regime alignment (same pattern as early_exit gate) ──
        regime_still_supports = False
        if self.regime_detector is not None:
            try:
                cr = self.regime_detector.get_coin_regime(pos.symbol)
                if cr is not None:
                    r = cr.regime.value if hasattr(cr.regime, "value") else str(cr.regime)
                    r = r.lower()
                    regime_still_supports = (
                        ("up" in r and plan.direction == "Buy")
                        or ("down" in r and plan.direction == "Sell")
                    )
            except Exception:
                regime_still_supports = False

        # ── 4. Calculator ──
        position_age_s = plan.age_minutes * 60.0
        # Phase 3 (Time-Decay Force-Close Definitive Fix, 2026-05-06) —
        # compute structural-invalidation evidence before calling the
        # calculator. Disjunction across XRAY confidence drop, setup-
        # type drift, regime inversion. Cache-miss / missing-anchor fail-
        # safe block force-close.
        # Phase 4.3 (Layer 4 Realignment, 2026-05-06): consult the
        # shared Layer4ProtectionService when wired. The service holds
        # the canonical implementation of the structural-invalidation
        # function (relocated from this class — see DEPRECATED note
        # on `_compute_structural_invalidation`). Falls back to the
        # inline copy when the service is None (legacy boot order /
        # tests build the watchdog without a manager).
        if self.layer4_protection is not None:
            struct_inv, struct_reason = (
                self.layer4_protection.compute_structural_invalidation(
                    symbol=pos.symbol, side=plan.direction, state=state,
                )
            )
        else:
            struct_inv, struct_reason = self._compute_structural_invalidation(
                symbol=pos.symbol, side=plan.direction, state=state,
            )
        outcome = self._time_decay.calculate(
            state,
            current_pnl_pct=pnl_pct,
            position_age_seconds=position_age_s,
            regime_still_supports=regime_still_supports,
            velocity_pct_per_s=velocity,
            acceleration_pct_per_s2=acceleration,
            structural_invalidation=struct_inv,
            invalidation_reason=struct_reason,
        )

        # T2-9 (2026-05-12) — record the STRUCT_GUARD verdict so the
        # sniper can defer its stall escape when STRUCT_GUARD blocks
        # (verdict='stable'). The verdict is the inverse of
        # `struct_inv`: structural_invalidation=False means "structure
        # holds, defer close" (STRUCT_GUARD says stable);
        # structural_invalidation=True means "real invalidation
        # evidence, allow action" (STRUCT_GUARD says unstable).
        # See Layer4ProtectionService.record_struct_guard_verdict +
        # ProfitSniper._stall_escape_action for the consumer side.
        if self.layer4_protection is not None and hasattr(
            self.layer4_protection, "record_struct_guard_verdict"
        ):
            _verdict = "unstable" if struct_inv else "stable"
            self.layer4_protection.record_struct_guard_verdict(
                pos.symbol, _verdict,
            )

        # ── 5a. No-op (grace period or not-tighter) ──
        if outcome is None:
            return False

        # ── 5b. Force-close sentinel ──
        if outcome == -1.0:
            try:
                # Item 2.4 (C5/F13) — close-reason split. The calculator returns
                # -1.0 on a win-probability cut (p_win < p_win_force_close) or, as
                # of F4/F4b/F7 (2026-06-09), on the standalone monotonic-grind cut
                # ("monotonic_grind_cut", a p_win-independent stall-at-the-trough
                # cut) — never on a pure deadline timeout (that is the separate
                # loser-timeout path). Booking it as "time_decay_force_close" made
                # it look like a deadline force-close and conflated near-certain-
                # loser cuts with true deadline-bleed (Finding 13). Book the
                # truthful, distinct reason the calculator stamped on the state
                # so close_trigger / the event / closed_by all agree and the leak
                # attribution is honest. Defaults to "win_prob_force_close" if a
                # legacy state object predates the field. Labeling only — the cut
                # decision is unchanged.
                _fc_reason = (
                    getattr(state, "force_close_reason", "")
                    or "win_prob_force_close"
                )
                # Issue 2.11 (2026-06-07): record the real exit reason on the
                # coordinator BEFORE the close so pop_close_reason returns the
                # win-prob/time-decay reason instead of the generic
                # "{mode}_sl_tp" fallback (the provenance the system_close path
                # otherwise loses).
                if self.coordinator and _fc_reason:
                    self.coordinator.set_close_reason(pos.symbol, _fc_reason)
                # Phase 12.7 (Gap 7.4-G1 follow-up): close_trigger surfaces in
                # BYBIT_DEMO_POSITION_CLOSE so operators can grep the win-prob
                # force-close events distinct from generic system_close.
                await self.position_service.close_position(
                    pos.symbol, close_trigger=_fc_reason,
                )
                log.warning(
                    f"TIME_DECAY_CLOSE | sym={pos.symbol} pnl={pnl_pct:+.2f}% "
                    f"p_win={state.p_win:.3f} mae={state.mae_pct:+.2f}% | {ctx()}"
                )
                await self._send_close_alert(
                    pos.symbol,
                    "TIME DECAY",
                    f"p_win={state.p_win:.2f} pnl={pnl_pct:+.2f}% mae={state.mae_pct:+.2f}%",
                    pnl_pct,
                )
                if self.event_buffer:
                    self.event_buffer.add_event(
                        "HIGH", _fc_reason, pos.symbol,
                        pnl_pct=round(pnl_pct, 2),
                        p_win=round(state.p_win, 3),
                        mae_pct=round(state.mae_pct, 2),
                    )
                if self.coordinator:
                    auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                        await self.coordinator.resolve_authoritative_pnl(
                            symbol=pos.symbol,
                            position_service=self.position_service,
                            fallback_pnl_usd=pos.unrealized_pnl,
                            fallback_exit_price=pos.mark_price,
                            fallback_pnl_pct=pnl_pct,
                            # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                            # row by qty instead of the legacy rows[0] (no-hint) lookup.
                            qty=pos.size,
                        )
                    )
                    self.coordinator.on_trade_closed(
                        symbol=pos.symbol,
                        pnl_pct=auth_pnl_pct,
                        pnl_usd=auth_pnl_usd,
                        was_win=auth_pnl_usd > 0,
                        closed_by=_fc_reason,
                        exit_price=auth_exit,
                        price_source=price_src,
                    )
            except Exception as e:
                log.error(
                    f"TIME_DECAY_CLOSE_FAIL | sym={pos.symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )
            finally:
                # T1-2 (2026-05-12): snapshot MAE high-water-mark before
                # deletion so a possible re-entry on the same symbol can
                # inherit the worst-excursion floor. The MAE floor is
                # cleared on confirmed external close in
                # _detect_and_record_closes (see TIME_DECAY_CLEANUP block).
                _stale = self._td_states.pop(pos.symbol, None)
                if _stale is not None:
                    if _stale.mae_pct < 0:
                        self._td_mae_high_water[pos.symbol] = _stale.mae_pct
                    log.info(
                        f"TIME_DECAY_STATE_DESTROY | sym={pos.symbol} "
                        f"mae={_stale.mae_pct:+.2f}% ticks={_stale.tick_count} "
                        f"reason=force_close_finally "
                        f"preserve_mae={'true' if _stale.mae_pct < 0 else 'false'} "
                        f"| {ctx()}"
                    )
            return True  # Signal to _monitor_position: return early

        # ── 5c. Push tighter SL (reuses tighter-only guard in helper) ──
        new_sl = outcome
        side_str = plan.direction
        await self._push_sl_to_shadow(
            symbol=pos.symbol,
            new_sl=new_sl,
            plan=plan,
            current_shadow_sl=pos.stop_loss,
            direction=side_str,
            source="time_decay",
        )
        return False

    # ────────────────────────────────────────────────────────────────
    # Mid-Hold Trade Management Fix Phase 3.4 (2026-05-19) — Helper
    # ────────────────────────────────────────────────────────────────

    async def _detect_ensemble_flip(self, pos) -> None:
        """Detect a STRONG opposite-direction ensemble flip for an open position.

        When detected, queue a single 'ensemble_flip' event to the
        ``thesis_events`` table so the next CALL_A or CALL_B prompt
        surfaces it to the brain. The brain decides what to do — this
        is information supply, not a force-close (Rule 4 / Rule 16 of
        IMPLEMENT_MIDHOLD).

        Dedupe semantics:
          - Multiple ticks within ``ensemble_flip_dedupe_window_seconds``
            with the same STRONG dominant direction collapse to ONE
            event. The dedupe key is (symbol, dominant_dir).
          - A re-flip back to the position's direction clears the dedupe
            state so the next opposite-flip fires again.
        """
        cfg = self.settings.watchdog
        # Kill switch.
        if not bool(getattr(cfg, "ensemble_flip_detection_enabled", True)):
            return
        # Service availability gates.
        cache = self.ensemble_state_cache
        if cache is None or self.thesis_manager is None:
            return
        # Read current consensus for this symbol.
        try:
            current = cache.get_current_consensus(
                pos.symbol,
                strong_threshold=float(getattr(
                    cfg, "ensemble_flip_strong_threshold", 4.0,
                )),
            )
        except Exception as _e:  # pragma: no cover
            log.debug(
                f"ENSEMBLE_FLIP_READ_FAIL | sym={pos.symbol} "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )
            return
        if current is None or current.get("consensus") != "STRONG":
            return
        dom_dir = str(current.get("dominant_dir") or "").upper()
        if dom_dir not in ("BUY", "SELL"):
            return  # NEUTRAL or unknown — no flip semantics
        pos_dir = str(getattr(pos, "side", "") or "").upper()
        if pos_dir not in ("BUY", "SELL"):
            return  # Cannot evaluate flip if position direction unknown
        # Detect opposite direction.
        if dom_dir == pos_dir:
            # Ensemble currently agrees with the position. Clear any
            # prior dedupe state so a future opposite-flip can fire
            # immediately.
            if pos.symbol in self._position_consensus_state:
                self._position_consensus_state.pop(pos.symbol, None)
            return
        # Opposite direction at STRONG consensus — this is the flip.
        import time as _time
        now = _time.time()
        prev = self._position_consensus_state.get(pos.symbol)
        if prev is not None:
            prev_dir, prev_ts = prev
            dedupe_window = float(getattr(
                cfg, "ensemble_flip_dedupe_window_seconds", 300.0,
            ))
            if prev_dir == dom_dir and (now - prev_ts) < dedupe_window:
                # Same direction, within dedupe window — skip.
                return
        # Resolve order_id and thesis_id for the queue row.
        order_id = ""
        thesis_id: int | None = None
        try:
            thesis_row = await self.thesis_manager.get_open_thesis_for_symbol(
                pos.symbol,
            )
            if thesis_row is not None:
                order_id = thesis_row.get("order_id", "") or ""
                thesis_id = thesis_row.get("id")
        except Exception as _e:  # pragma: no cover
            log.debug(
                f"ENSEMBLE_FLIP_THESIS_LOOKUP_FAIL | sym={pos.symbol} "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )
        if not order_id:
            # No persisted thesis row — cannot queue (FK semantics +
            # the close-path purge would have nothing to clear).
            return
        # Build payload + emit log + queue event.
        agreeing = float(current.get("agreeing", 0.0))
        opposing = float(current.get("opposing", 0.0))
        ts_iso = current.get("ts")
        import json as _json
        payload = _json.dumps({
            "consensus": "STRONG",
            "dominant_dir": dom_dir,
            "position_dir": pos_dir,
            "agreeing": agreeing,
            "opposing": opposing,
            "detected_at_ts": now,
            "consensus_ts": ts_iso,
        })
        log.info(
            f"ENSEMBLE_FLIP_DETECTED | sym={pos.symbol} pos_dir={pos_dir} "
            f"ensemble_dir={dom_dir} agreeing={agreeing:.2f} "
            f"opposing={opposing:.2f} order_id={order_id} | {ctx()}"
        )
        try:
            await self.thesis_manager.queue_thesis_event(
                symbol=pos.symbol,
                order_id=order_id,
                event_type="ensemble_flip",
                payload=payload,
                thesis_id=thesis_id,
            )
        except Exception as _e:
            log.warning(
                f"ENSEMBLE_FLIP_QUEUE_FAIL | sym={pos.symbol} "
                f"err='{str(_e)[:120]}' | {ctx()}"
            )
            return
        # IMPLEMENT_MIDHOLD doc Rule 7 named tag — emitted after the
        # event has successfully landed in the queue (vs the lower-level
        # THESIS_EVENT_QUEUED that thesis_manager.queue_thesis_event
        # emits on the DB INSERT). This pair gives operators two grep
        # surfaces: the watchdog-side escalation and the persistence-
        # side row insert.
        log.info(
            f"ENSEMBLE_FLIP_EVENT_QUEUED | sym={pos.symbol} pos_dir={pos_dir} "
            f"ensemble_dir={dom_dir} order_id={order_id} | {ctx()}"
        )
        # Update dedupe state.
        self._position_consensus_state[pos.symbol] = (dom_dir, now)

    # ────────────────────────────────────────────────────────────────
    # Mid-Hold Trade Management Fix Phase 3.5 (2026-05-19) — Helper
    # ────────────────────────────────────────────────────────────────

    async def _monitor_thesis_state(self, pos, current_price: float) -> None:
        """Evaluate the thesis-invalidation criterion for an open position.

        Transitions a row's ``thesis_state`` between VALID / DEGRADING /
        INVALIDATED based on current price and (when available) the most
        recently closed M5 candle. On INVALIDATED transition, queues a
        single ``thesis_invalidation`` event so the next CALL_A or
        CALL_B prompt surfaces it. The watchdog never force-closes from
        this lane — brain decides (Rule 4 / Rule 16 of IMPLEMENT_MIDHOLD).
        """
        cfg = self.settings.watchdog
        if not bool(getattr(cfg, "thesis_invalidation_detection_enabled", True)):
            return
        if self.thesis_manager is None:
            return
        # Load the open thesis row.
        try:
            thesis_row = await self.thesis_manager.get_open_thesis_for_symbol(
                pos.symbol,
            )
        except Exception as _e:  # pragma: no cover
            log.debug(
                f"THESIS_STATE_LOOKUP_FAIL | sym={pos.symbol} "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )
            return
        if not thesis_row:
            return
        order_id = thesis_row.get("order_id", "") or ""
        thesis_id = thesis_row.get("id")
        prior_state = thesis_row.get("thesis_state", "VALID") or "VALID"
        # Latest M5 close from the watchdog's tick-local prefetch cache.
        last_m5_close: float | None = None
        klines = self._wd_klines_m5.get(pos.symbol) or []
        if klines:
            try:
                # OHLCV objects expose .close. The list is in
                # chronological order; the last entry is the most recent.
                last_m5_close = float(getattr(klines[-1], "close", current_price))
            except Exception:
                last_m5_close = None
        new_state, reason = self.thesis_manager.evaluate_thesis_state(
            thesis_row,
            current_price=current_price,
            last_m5_close=last_m5_close,
            close_buffer_pct=float(getattr(
                cfg, "thesis_invalidation_close_buffer_pct", 0.5,
            )),
            degrading_buffer_pct=float(getattr(
                cfg, "thesis_invalidation_wick_buffer_pct", 0.1,
            )),
        )
        # No-op when state did not transition (mirror the in-memory
        # cache; DB row is authoritative on restart).
        cached_state = self._position_thesis_state.get(pos.symbol, prior_state)
        if new_state == cached_state and new_state == prior_state:
            return
        # Persist transition.
        try:
            await self.thesis_manager.record_thesis_state(
                pos.symbol, order_id, new_state,
            )
        except Exception as _e:
            log.warning(
                f"THESIS_STATE_PERSIST_FAIL | sym={pos.symbol} "
                f"err='{str(_e)[:120]}' | {ctx()}"
            )
            return
        self._position_thesis_state[pos.symbol] = new_state
        log.info(
            f"THESIS_LEVEL_MONITORED | sym={pos.symbol} prior={prior_state} "
            f"new={new_state} reason={reason} current_price={current_price} "
            f"order_id={order_id or '-'} | {ctx()}"
        )
        # On INVALIDATED transition, queue the event for brain surfacing.
        # Only fires on the transition into INVALIDATED, not every tick
        # while still in that state (cached_state guard above).
        if new_state == "INVALIDATED" and prior_state != "INVALIDATED":
            log.warning(
                f"THESIS_INVALIDATION_DETECTED | sym={pos.symbol} "
                f"reason={reason} current_price={current_price} "
                f"order_id={order_id or '-'} | {ctx()}"
            )
            import json as _json
            payload = _json.dumps({
                "reason": reason,
                "current_price": current_price,
                "last_m5_close": last_m5_close,
                "prior_state": prior_state,
            })
            _queued_ok = False
            try:
                await self.thesis_manager.queue_thesis_event(
                    symbol=pos.symbol,
                    order_id=order_id,
                    event_type="thesis_invalidation",
                    payload=payload,
                    thesis_id=thesis_id,
                )
                _queued_ok = True
            except Exception as _e:
                log.warning(
                    f"THESIS_INVALIDATION_QUEUE_FAIL | sym={pos.symbol} "
                    f"err='{str(_e)[:120]}' | {ctx()}"
                )
            if _queued_ok:
                # IMPLEMENT_MIDHOLD doc Rule 7 named tag — fires after
                # the event row has landed in the queue. Pair with the
                # THESIS_INVALIDATION_DETECTED warning above; this INFO
                # confirms the brain will see the event in the next
                # CALL_A or CALL_B prompt.
                log.info(
                    f"THESIS_INVALIDATION_EVENT_QUEUED | sym={pos.symbol} "
                    f"reason={reason} order_id={order_id} | {ctx()}"
                )
        # When the heuristic fallback has no anchor, emit a single
        # diagnostic so operators know the brain's omission means this
        # position has no level monitoring. Throttle by only emitting
        # once per (symbol, transition) — the cached_state guard above
        # already ensures we only land here on transitions, but the
        # reason itself stays 'heuristic_fallback_no_anchor' so we wrap
        # it in its own log tag to avoid grep ambiguity with the
        # generic THESIS_LEVEL_MONITORED.
        if reason == "heuristic_fallback_no_anchor":
            log.info(
                f"THESIS_INVALIDATION_NO_ANCHOR | sym={pos.symbol} "
                f"order_id={order_id or '-'} | {ctx()}"
            )

    async def _adaptive_hard_stop_limit(self, symbol: str) -> float:
        """Resolve the watchdog hard-stop limit (percent, positive). With the
        Dynamic Adaptive Exit layer on, it is the R-scaled backstop sized to the
        coin (vol_scale.hard_stop_pct, floored at/above the sacred cap so the cap
        stays operative); otherwise the legacy flat 3.0%. Extracted from the
        hard-stop rule so it is unit-testable in both modes; behaviour is
        identical to the prior inline logic."""
        _ae = getattr(getattr(self, "settings", None), "adaptive_exit", None)
        if (
            _ae is not None and getattr(_ae, "enabled", False)
            and self.volatility_profiler is not None
        ):
            try:
                _vp = await self.volatility_profiler.get_profile(symbol)
                if _vp is not None and getattr(_vp, "atr_pct_5m", 0) > 0:
                    from src.analysis import vol_scale as _vg
                    return _vg.hard_stop_pct(float(_vp.atr_pct_5m), _ae)
            except Exception:
                pass
        return 3.0

    async def _monitor_position(self, pos: Position) -> None:
        """Analyze a single position for danger conditions."""
        ticker = await self.market_service.get_ticker(pos.symbol)
        current_price = ticker.last_price
        cfg = self.settings.watchdog

        # Get choppiness for noise filtering (lightweight — reuses cached TA)
        ta_data: dict = {}
        if self.ta_engine:
            try:
                # Phase 4: prefer the tick-local batch prefetch; fall back to
                # per-symbol fetch only when batch missed this coin.
                klines = self._wd_klines_m5.get(pos.symbol) or []
                if not klines:
                    klines = await self.market_repo.get_klines(pos.symbol, "5", 60)
                if len(klines) >= 50:
                    ta_data = await self.ta_engine.analyze(candles=klines)
            except Exception as e:
                log.debug("TA choppiness analysis failed: {err}", err=str(e))

        # --- Calculate metrics ---
        pnl_pct = self._calculate_pnl_pct(pos, current_price)

        # Profit-Fetching Phase 5 — when enabled with subordinate_watchdog_trail_exit,
        # the sniper spine is the SOLE trailing-SL writer, so EVERY autonomous
        # watchdog winner-trail is disabled: the CHECK 2/3 percentage trail
        # (below) AND the "Smart trailing stop (via coordinator)" lock-peak /
        # breakeven block further down. Computed once here so both gates share
        # it. Non-trail SL writes (sentinel deadline tiers, time-decay loser
        # lane, brain-directed tightens) are unaffected.
        _pf_trail_off = (
            self._pf.enabled and self._pf.subordinate_watchdog_trail_exit
        )

        # --- TRADE PLAN MONITORING ---
        plan = self.coordinator.get_trade_plan(pos.symbol) if self.coordinator else None
        if plan:
            pnl_from_plan = 0
            if plan.direction == "Buy":
                pnl_from_plan = ((current_price - plan.entry_price) / plan.entry_price) * 100
            else:
                pnl_from_plan = ((plan.entry_price - current_price) / plan.entry_price) * 100

            # CHECK 1: Timer expired — SENTINEL Deadline Engine
            if plan.is_expired:
                _sentinel_dl = getattr(self, "_sentinel_deadline", None)
                if _sentinel_dl is not None:
                    # ── SENTINEL tiered deadline logic ──
                    _dl_action = _sentinel_dl.evaluate(
                        symbol=pos.symbol,
                        pnl_pct=pnl_from_plan,
                        entry_price=plan.entry_price,
                        direction=plan.direction,
                    )
                    log.warning(
                        f"SENTINEL_DEADLINE | sym={pos.symbol} tier={_dl_action.tier.value} "
                        f"close={_dl_action.should_close} pnl={pnl_from_plan:+.2f}% "
                        f"age={plan.age_minutes:.0f}min rsn='{_dl_action.reason[:80]}' | {ctx()}"
                    )

                    # Apply SL tightening if recommended
                    if _dl_action.new_sl > 0:
                        pushed = await self._push_sl_to_shadow(
                            symbol=pos.symbol,
                            new_sl=_dl_action.new_sl,
                            plan=plan,
                            current_shadow_sl=pos.stop_loss,
                            direction=plan.direction,
                            source="sentinel_deadline",
                        )
                        if pushed:
                            log.info(
                                f"SENTINEL_DEADLINE_SL | sym={pos.symbol} "
                                f"new_sl={_dl_action.new_sl} tier={_dl_action.tier.value} | {ctx()}"
                            )

                    # Close if the tier requires it
                    # Profit-Fetching Phase 5 — ride the winner past the
                    # deadline. A still-profitable expired trade (SENTINEL
                    # "profit" tier) is NOT hard-closed; it rides the sniper's
                    # maximally-tightened trail and exits only on the defined
                    # give-back (blueprint 4.5). Self-limiting: once it fades
                    # below profit the non-climber tiers re-engage next tick.
                    if (
                        _dl_action.should_close
                        and self._pf.enabled
                        and self._pf.ride_winner_past_deadline
                        and _dl_action.tier.value == "profit"
                    ):
                        log.info(
                            f"SNIPER_DEADLINE_RIDE | sym={pos.symbol} "
                            f"pnl={pnl_from_plan:+.2f}% age={plan.age_minutes:.0f}min "
                            f"max={plan.max_hold_minutes}min | riding sniper trail "
                            f"past deadline (not force-closing winner) | {ctx()}"
                        )
                        return
                    if _dl_action.should_close:
                        try:
                            await self.position_service.close_position(pos.symbol, close_trigger="wd_dl_action")
                            _sentinel_dl.clear_grace(pos.symbol)
                            if self.event_buffer:
                                self.event_buffer.add_event(
                                    "MED", f"sentinel_deadline_{_dl_action.tier.value}", pos.symbol,
                                    pnl_pct=round(pnl_from_plan, 2),
                                    age_min=round(plan.age_minutes, 0),
                                    max_min=plan.max_hold_minutes,
                                )
                            if self.coordinator:
                                self.coordinator.remove_trade_plan(pos.symbol)
                                auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                                    await self.coordinator.resolve_authoritative_pnl(
                                        symbol=pos.symbol,
                                        position_service=self.position_service,
                                        fallback_pnl_usd=pos.unrealized_pnl,
                                        fallback_exit_price=pos.mark_price,
                                        fallback_pnl_pct=pnl_from_plan,
                                        # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                        # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                        qty=pos.size,
                                    )
                                )
                                self.coordinator.on_trade_closed(
                                    symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                                    pnl_usd=auth_pnl_usd,
                                    was_win=auth_pnl_usd > 0,
                                    closed_by=f"sentinel_deadline_{_dl_action.tier.value}",
                                    exit_price=auth_exit,
                                    price_source=price_src,
                                )
                        except Exception as e:
                            log.error(f"SENTINEL_DEADLINE_CLOSE_FAIL | sym={pos.symbol} err='{str(e)[:100]}' | {ctx()}")
                        return
                    else:
                        # Not closing — grace period or SL tightened, continue monitoring
                        return
                else:
                    # Profit-Fetching Phase 5 — ride the winner past the
                    # deadline on the binary-fallback path too. A profitable
                    # expired trade is not force-closed; the sniper trail rides
                    # it. Non-profitable expired trades still close (backstop).
                    if (
                        self._pf.enabled
                        and self._pf.ride_winner_past_deadline
                        and pnl_from_plan > 0.0
                    ):
                        log.info(
                            f"SNIPER_DEADLINE_RIDE | sym={pos.symbol} "
                            f"pnl={pnl_from_plan:+.2f}% age={plan.age_minutes:.0f}min "
                            f"max={plan.max_hold_minutes}min src=binary_fallback | "
                            f"riding sniper trail past deadline | {ctx()}"
                        )
                        return
                    # Fallback: original binary close behavior (SENTINEL disabled)
                    log.warning(
                        "PLAN TIMER: {sym} held {age:.0f}min (max {max}min) "
                        "PnL={pnl:+.2f}% — CLOSING",
                        sym=pos.symbol, age=plan.age_minutes,
                        max=plan.max_hold_minutes, pnl=pnl_from_plan,
                    )
                    try:
                        await self.position_service.close_position(pos.symbol, close_trigger="wd_plan_timer")
                        if self.event_buffer:
                            self.event_buffer.add_event(
                                "MED", "plan_timer_close", pos.symbol,
                                pnl_pct=round(pnl_from_plan, 2),
                                age_min=round(plan.age_minutes, 0),
                                max_min=plan.max_hold_minutes,
                            )
                        if self.coordinator:
                            self.coordinator.remove_trade_plan(pos.symbol)
                            auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                                await self.coordinator.resolve_authoritative_pnl(
                                    symbol=pos.symbol,
                                    position_service=self.position_service,
                                    fallback_pnl_usd=pos.unrealized_pnl,
                                    fallback_exit_price=pos.mark_price,
                                    fallback_pnl_pct=pnl_from_plan,
                                    # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                    # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                    qty=pos.size,
                                )
                            )
                            self.coordinator.on_trade_closed(
                                symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                                pnl_usd=auth_pnl_usd,
                                was_win=auth_pnl_usd > 0, closed_by="plan_timer",
                                exit_price=auth_exit,
                                price_source=price_src,
                            )
                    except Exception as e:
                        # Phase 12.6 (Gap 6.4-G1): structured tag.
                        log.error(f"WD_PLAN_TIMER_CLOSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
                    return

            # CHECK 2: Trailing activation
            # _pf_trail_off (computed at method top) disables the watchdog
            # percentage trail when the sniper spine owns trailing.
            if (
                not plan.trailing_active
                and pnl_from_plan >= plan.trailing_activation_pct
                and not _pf_trail_off
            ):
                plan.activate_trailing(current_price)
                log.info(
                    "TRAILING ON: {sym} profit={pnl:+.2f}% >= {act}% — "
                    "trail at ${trail}",
                    sym=pos.symbol, pnl=pnl_from_plan,
                    act=plan.trailing_activation_pct,
                    trail=format_price(plan.trailing_stop_price),
                )
                await self._push_sl_to_shadow(
                    symbol=pos.symbol,
                    new_sl=plan.trailing_stop_price,
                    plan=plan,
                    current_shadow_sl=pos.stop_loss,
                    direction=plan.direction,
                    source="trail_activation",
                )

            # CHECK 3: Update trailing
            if plan.trailing_active and not _pf_trail_off:
                old_trail = plan.trailing_stop_price
                plan.update_trailing(current_price)
                if plan.trailing_stop_price != old_trail:
                    await self._push_sl_to_shadow(
                        symbol=pos.symbol,
                        new_sl=plan.trailing_stop_price,
                        plan=plan,
                        current_shadow_sl=pos.stop_loss,
                        direction=plan.direction,
                        source="trail_update",
                    )

                if plan.should_trail_exit(current_price):
                    log.warning(
                        "TRAIL HIT: {sym} price=${price} <= trail=${trail}",
                        sym=pos.symbol, price=format_price(current_price),
                        trail=format_price(plan.trailing_stop_price),
                    )
                    try:
                        await self.position_service.close_position(pos.symbol, close_trigger="wd_trail")
                        if self.event_buffer:
                            self.event_buffer.add_event(
                                "MED", "trailing_stop_hit", pos.symbol,
                                pnl_pct=round(pnl_from_plan, 2),
                                trail_price=round(plan.trailing_stop_price, 4),
                            )
                        if self.coordinator:
                            self.coordinator.remove_trade_plan(pos.symbol)
                            auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                                await self.coordinator.resolve_authoritative_pnl(
                                    symbol=pos.symbol,
                                    position_service=self.position_service,
                                    fallback_pnl_usd=pos.unrealized_pnl,
                                    fallback_exit_price=pos.mark_price,
                                    fallback_pnl_pct=pnl_from_plan,
                                    # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                    # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                    qty=pos.size,
                                )
                            )
                            self.coordinator.on_trade_closed(
                                symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                                pnl_usd=auth_pnl_usd,
                                was_win=auth_pnl_usd > 0, closed_by="trailing_stop",
                                exit_price=auth_exit,
                                price_source=price_src,
                            )
                    except Exception as e:
                        # Phase 12.6 (Gap 6.4-G1): structured tag.
                        log.error(f"WD_TRAIL_CLOSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
                    return

            # CHECK 4: Early exit — losing after significant time (regime-aware)
            if pnl_from_plan < -0.5 and plan.max_hold_minutes > 0:
                time_pct = (plan.age_minutes / plan.max_hold_minutes) * 100
                if time_pct > 50 and pnl_from_plan < -1.0:
                    # --- Intelligence gates: suppress early_exit if trade is defensible ---

                    # Gate 1: Brain recently said HOLD for this symbol
                    brain_said_hold = self._consecutive_holds.get(pos.symbol, 0) > 0

                    # Gate 2: Trade direction aligns with coin regime
                    regime_aligned = False
                    if self.regime_detector:
                        try:
                            _cr = self.regime_detector.get_coin_regime(pos.symbol)
                            if _cr:
                                _regime_str = _cr.regime.value
                                regime_aligned = (
                                    ("up" in _regime_str and plan.direction == "Buy")
                                    or ("down" in _regime_str and plan.direction == "Sell")
                                )
                        except Exception:
                            regime_aligned = False

                    # Gate 3: SL buffer still healthy (< 70% consumed)
                    sl_buffer_ok = False
                    if plan.stop_loss_price and plan.stop_loss_price > 0:
                        sl_range = abs(plan.entry_price - plan.stop_loss_price)
                        if sl_range > 0:
                            if plan.direction == "Buy":
                                price_moved = max(0, plan.entry_price - current_price)
                            else:
                                price_moved = max(0, current_price - plan.entry_price)
                            sl_consumed_pct = (price_moved / sl_range) * 100
                            sl_buffer_ok = sl_consumed_pct < 70
                        else:
                            sl_buffer_ok = True
                    else:
                        sl_buffer_ok = True

                    # Decision: only exit if ALL gates fail (no protection)
                    if brain_said_hold or regime_aligned or sl_buffer_ok:
                        log.info(
                            "EARLY EXIT SUPPRESSED: {sym} {pct:.0f}% time, {pnl:+.2f}% loss "
                            "| brain_hold={bh} regime_aligned={ra} sl_buffer_ok={sb}",
                            sym=pos.symbol, pct=time_pct, pnl=pnl_from_plan,
                            bh=brain_said_hold, ra=regime_aligned, sb=sl_buffer_ok,
                        )
                    elif not getattr(self.settings.watchdog, "early_exit_enabled", False):
                        # Layer 3: early exit disabled by default — its
                        # historical win rate is 0% (24/24 losses, -$464).
                        # SL propagation now keeps Shadow's SL tight enough
                        # that the SL itself catches losers earlier than
                        # this gate would. Logged-but-not-fired so we can
                        # monitor what it WOULD have closed; flip
                        # `early_exit_enabled = true` in [watchdog] to
                        # re-enable.
                        log.info(
                            "EARLY_EXIT_DISABLED_WOULD_FIRE | sym={sym} time={t:.0f}% pnl={p:+.2f}% "
                            "brain_hold={bh} regime_aligned={ra} sl_buffer_ok={sb}",
                            sym=pos.symbol, t=time_pct, p=pnl_from_plan,
                            bh=brain_said_hold, ra=regime_aligned, sb=sl_buffer_ok,
                        )
                    else:
                        log.warning(
                            "EARLY EXIT: {sym} {pct:.0f}% time, {pnl:+.2f}% loss "
                            "| no Brain HOLD, not regime-aligned, SL buffer depleted",
                            sym=pos.symbol, pct=time_pct, pnl=pnl_from_plan,
                        )
                        try:
                            await self.position_service.close_position(pos.symbol, close_trigger="wd_early_exit")
                            if self.event_buffer:
                                self.event_buffer.add_event(
                                    "MED", "early_exit", pos.symbol,
                                    pnl_pct=round(pnl_from_plan, 2),
                                    time_pct=round(time_pct, 0),
                                )
                            if self.coordinator:
                                self.coordinator.remove_trade_plan(pos.symbol)
                                auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                                    await self.coordinator.resolve_authoritative_pnl(
                                        symbol=pos.symbol,
                                        position_service=self.position_service,
                                        fallback_pnl_usd=pos.unrealized_pnl,
                                        fallback_exit_price=pos.mark_price,
                                        fallback_pnl_pct=pnl_from_plan,
                                        # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                        # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                        qty=pos.size,
                                    )
                                )
                                self.coordinator.on_trade_closed(
                                    symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                                    pnl_usd=auth_pnl_usd,
                                    was_win=auth_pnl_usd > 0, closed_by="early_exit",
                                    exit_price=auth_exit,
                                    price_source=price_src,
                                )
                        except Exception as e:
                            # Phase 12.6 (Gap 6.4-G1): structured tag.
                            log.error(f"WD_EARLY_EXIT_FAIL | err='{str(e)[:120]}' | {ctx()}")
                        return

            # LOG plan status periodically
            if int(plan.age_minutes) % 5 < 0.3:
                log.info(
                    "PLAN: {sym} {dir} PnL={pnl:+.2f}% age={age:.0f}min "
                    "remain={rem:.0f}min trail={trail}",
                    sym=pos.symbol, dir=plan.direction, pnl=pnl_from_plan,
                    age=plan.age_minutes, rem=plan.remaining_minutes,
                    trail="active" if plan.trailing_active else "off",
                )

        # RULE: Hard loss limit — the wider watchdog BACKSTOP beneath the sacred
        # cap. Adaptive (Dynamic Adaptive Exit, 2026-06-15): an R-multiple sized
        # to the coin (8-10R, floored at/above the cap so the cap still fires
        # first), or the legacy flat -3% when disabled. Replaces the one-size
        # literal that was too loose for a 0.05%-ATR coin and too tight for a
        # 2%-ATR coin (forensic E1). The cap (sniper) remains the operative
        # catastrophic floor; this never weakens it.
        _hard_stop_limit = await self._adaptive_hard_stop_limit(pos.symbol)
        if pnl_pct < -_hard_stop_limit:
            try:
                await self.position_service.close_position(pos.symbol, close_trigger="wd_hard_stop")
                log.warning(
                    "HARD STOP: {sym} loss {pnl:.1f}% exceeded -{lim:.2f}% limit",
                    sym=pos.symbol, pnl=pnl_pct, lim=_hard_stop_limit,
                )
                await self._send_close_alert(
                    pos.symbol, "HARD STOP",
                    f"Loss {pnl_pct:.1f}% exceeded -{_hard_stop_limit:.2f}% limit", pnl_pct,
                )
                self._hard_stops_this_hour += 1
                if self.event_buffer:
                    self.event_buffer.add_event(
                        "HIGH", "hard_stop", pos.symbol,
                        pnl_pct=round(pnl_pct, 2), price=current_price,
                    )
                if self.coordinator:
                    auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                        await self.coordinator.resolve_authoritative_pnl(
                            symbol=pos.symbol,
                            position_service=self.position_service,
                            fallback_pnl_usd=pos.unrealized_pnl,
                            fallback_exit_price=pos.mark_price,
                            fallback_pnl_pct=pnl_pct,
                            # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                            # row by qty instead of the legacy rows[0] (no-hint) lookup.
                            qty=pos.size,
                        )
                    )
                    self.coordinator.on_trade_closed(
                        symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                        pnl_usd=auth_pnl_usd,
                        was_win=auth_pnl_usd > 0, closed_by="hard_stop",
                        exit_price=auth_exit,
                        price_source=price_src,
                    )
            except Exception as e:
                # Phase 12.6 (Gap 6.4-G1): structured tag.
                log.error(f"WD_HARD_STOP_FAIL | err='{str(e)[:120]}' | {ctx()}")
            return

        # ═══ Mid-Hold 1A: Ensemble-Flip Event Detection ═══
        # When the strategy ensemble's STRONG consensus flips to the
        # opposite direction from this open position, queue a
        # `ensemble_flip` event for the next CALL_A or CALL_B prompt.
        # The brain decides what to do — this is information supply, not
        # a force-close. Idempotent + dedupe-throttled to avoid spamming
        # the queue in choppy markets.
        try:
            await self._detect_ensemble_flip(pos)
        except Exception as e:  # pragma: no cover — observability-only
            log.debug(
                f"ENSEMBLE_FLIP_CHECK_FAIL | sym={pos.symbol} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )

        # ═══ Mid-Hold 2A: Thesis-Invalidation Level Monitoring ═══
        # Per-tick evaluation of the brain-stated criterion (or the
        # Approach A heuristic snapshot when brain omitted). State
        # transitions VALID → DEGRADING (wick beyond) → INVALIDATED
        # (close beyond). On INVALIDATED, queue a single
        # `thesis_invalidation` event for the next CALL_A or CALL_B.
        try:
            await self._monitor_thesis_state(pos, current_price)
        except Exception as e:  # pragma: no cover — observability-only
            log.debug(
                f"THESIS_STATE_CHECK_FAIL | sym={pos.symbol} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )

        # ═══ LOSER LANE: Time-Decay Dynamic Risk Budget ═══
        # Runs only when pnl_pct < 0 and a plan is present. Uses the existing
        # _push_sl_to_shadow helper (tighter-only). Force-close path returns
        # early to prevent timeout from double-firing on a position that is
        # already being closed. Non-force-close path falls through so the
        # timeout block below remains the ultimate time fallback when
        # Shadow's tightened SL has not fired yet.
        if pnl_pct < 0 and plan is not None:
            closed = await self._handle_time_decay(pos, plan, pnl_pct, current_price)
            if closed:
                return
        else:
            # Losing-without-plan diagnostic: Time-Decay needs plan.entry_price
            # / direction / max_hold to seed state. Dedup 60s per symbol.
            if pnl_pct < 0 and plan is None:
                self._log_skip(
                    pos.symbol, "plan_missing", f"pnl={pnl_pct:+.2f}%"
                )
            # Position crossed to profit (or plan missing) — hand off to
            # the existing SENTINEL / trailing / profit-take lane by
            # clearing Time-Decay state. Preserves original handoff
            # behavior so td_states does not leak.
            if pos.symbol in self._td_states:
                _td = self._td_states.pop(pos.symbol)
                # T1-2 (2026-05-12) PRODUCTION ROOT-CAUSE PATH: this is
                # the dominant trigger for MAE high-water-mark loss.
                # Production logs (past 6 h) show 173 HANDOFF events,
                # 159 followed by re-INIT, 56 of those lost MAE history
                # (top: INJUSDT lost -0.68%). Snapshot the MAE before
                # destroying the state so the inevitable re-INIT (when
                # the position swings back to losing) inherits it.
                if _td.mae_pct < 0:
                    self._td_mae_high_water[pos.symbol] = _td.mae_pct
                log.info(
                    f"TIME_DECAY_HANDOFF | sym={pos.symbol} pnl={pnl_pct:+.2f}% "
                    f"ticks={_td.tick_count} p_win={_td.p_win:.3f} "
                    f"mae={_td.mae_pct:+.2f}% | {ctx()}"
                )
                log.info(
                    f"TIME_DECAY_STATE_DESTROY | sym={pos.symbol} "
                    f"mae={_td.mae_pct:+.2f}% ticks={_td.tick_count} "
                    f"reason=profit_handoff "
                    f"preserve_mae={'true' if _td.mae_pct < 0 else 'false'} "
                    f"| {ctx()}"
                )

        # RULE: Running out of time and still negative
        _timeout_pct = getattr(self.settings.watchdog, 'timeout_threshold_pct', 95.0)
        if plan and pnl_pct < 0 and plan.max_hold_minutes > 0:
            time_used_pct = (plan.age_minutes / plan.max_hold_minutes) * 100
            if time_used_pct > _timeout_pct:
                # PnL-aware: if nearly flat at deadline, give a one-time extension
                if pnl_pct >= -0.5 and not getattr(plan, '_extended', False):
                    # PF/LC Top-15 Problem 2.5 — capture the ORIGINAL deadline
                    # before extending so the time dial can be frozen on it (the
                    # extension grants grace on the close-timer without
                    # re-loosening the protective dial). Dynamic attr, mirrors
                    # _extended; read by the sniper's _pf_age_and_deadline.
                    if not getattr(plan, '_original_max_hold_minutes', 0):
                        plan._original_max_hold_minutes = plan.max_hold_minutes
                    plan.max_hold_minutes += 10
                    plan._extended = True
                    log.info(
                        f"TIMEOUT_EXTEND | sym={pos.symbol} pnl={pnl_pct:+.2f}% "
                        f"time={time_used_pct:.0f}% | nearly flat — extending 10min | {ctx()}"
                    )
                    return
                try:
                    await self.position_service.close_position(pos.symbol, close_trigger="wd_timeout")
                    log.warning(
                        "TIMEOUT: {sym} {pct:.0f}% time, still losing {pnl:.1f}%",
                        sym=pos.symbol, pct=time_used_pct, pnl=pnl_pct,
                    )
                    await self._send_close_alert(
                        pos.symbol, "TIMEOUT",
                        f"{_timeout_pct:.0f}% of time gone, PnL {pnl_pct:+.1f}%", pnl_pct,
                    )
                    if self.event_buffer:
                        self.event_buffer.add_event(
                            "MED", "timeout_close", pos.symbol,
                            pnl_pct=round(pnl_pct, 2), time_used_pct=round(time_used_pct, 0),
                        )
                    if self.coordinator:
                        auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                            await self.coordinator.resolve_authoritative_pnl(
                                symbol=pos.symbol,
                                position_service=self.position_service,
                                fallback_pnl_usd=pos.unrealized_pnl,
                                fallback_exit_price=pos.mark_price,
                                fallback_pnl_pct=pnl_pct,
                                # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                qty=pos.size,
                            )
                        )
                        self.coordinator.on_trade_closed(
                            symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                            pnl_usd=auth_pnl_usd,
                            was_win=auth_pnl_usd > 0, closed_by="timeout",
                            exit_price=auth_exit,
                            price_source=price_src,
                        )
                except Exception as e:
                    # Phase 12.6 (Gap 6.4-G1): structured tag.
                    log.error(f"WD_TIMEOUT_CLOSE_FAIL | err='{str(e)[:120]}' | {ctx()}")
                return

        # RULE: Take profit — profitable + past half of hold time.
        # Profit-Fetching Phase 5 — when enabled, this hard +1.5% profit cap is
        # subordinated: the sniper's ladder + Chandelier spine rides the winner
        # and captures it via the trailing SL instead of cutting it here.
        _pf_pt_off = self._pf.enabled and self._pf.subordinate_profit_take
        if plan and pnl_pct > 1.5 and plan.max_hold_minutes > 0 and not _pf_pt_off:
            time_used_pct = (plan.age_minutes / plan.max_hold_minutes) * 100
            if time_used_pct > 50:
                try:
                    await self.position_service.close_position(pos.symbol, close_trigger="wd_profit_take")
                    log.info(
                        "PROFIT TAKEN: {sym} +{pnl:.1f}% at {pct:.0f}% of hold time",
                        sym=pos.symbol, pnl=pnl_pct, pct=time_used_pct,
                    )
                    await self._send_close_alert(
                        pos.symbol, "PROFIT TAKEN",
                        f"+{pnl_pct:.1f}% profit locked at {time_used_pct:.0f}% of hold time",
                        pnl_pct,
                    )
                    if self.event_buffer:
                        self.event_buffer.add_event(
                            "LOW", "profit_taken", pos.symbol,
                            pnl_pct=round(pnl_pct, 2), time_used_pct=round(time_used_pct, 0),
                        )
                    if self.coordinator:
                        auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                            await self.coordinator.resolve_authoritative_pnl(
                                symbol=pos.symbol,
                                position_service=self.position_service,
                                fallback_pnl_usd=pos.unrealized_pnl,
                                fallback_exit_price=pos.mark_price,
                                fallback_pnl_pct=pnl_pct,
                                # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                                # row by qty instead of the legacy rows[0] (no-hint) lookup.
                                qty=pos.size,
                            )
                        )
                        self.coordinator.on_trade_closed(
                            symbol=pos.symbol, pnl_pct=auth_pnl_pct,
                            pnl_usd=auth_pnl_usd,
                            was_win=auth_pnl_usd > 0, closed_by="profit_take",
                            exit_price=auth_exit,
                            price_source=price_src,
                        )
                except Exception as e:
                    # Phase 12.6 (Gap 6.4-G1): structured tag.
                    log.error(f"WD_PROFIT_TAKE_FAIL | err='{str(e)[:120]}' | {ctx()}")
                return

        # Track peak PnL for trailing drawdown
        current_unrealized = pos.unrealized_pnl
        if pos.symbol not in self._position_peaks or current_unrealized > self._position_peaks[pos.symbol]:
            self._position_peaks[pos.symbol] = current_unrealized
        peak_pnl = self._position_peaks[pos.symbol]

        # Trailing drawdown from peak (as % of position value)
        position_value = pos.size * pos.entry_price
        drawdown_from_peak_pct = 0.0
        if peak_pnl > 0 and position_value > 0:
            drawdown_from_peak = peak_pnl - current_unrealized
            drawdown_from_peak_pct = (drawdown_from_peak / position_value) * 100

        # Rapid price movement detection
        prev_price = self._last_prices.get(pos.symbol, current_price)
        price_change_pct = ((current_price - prev_price) / prev_price * 100) if prev_price > 0 else 0.0
        self._last_prices[pos.symbol] = current_price

        # Is the move against our position?
        is_against = (
            (pos.side == Side.BUY and price_change_pct < 0)
            or (pos.side == Side.SELL and price_change_pct > 0)
        )

        # Stop-loss proximity
        sl_proximity_pct = self._calculate_sl_proximity(pos, current_price)

        # Accelerating loss detection
        prev_pnl = self._last_pnls.get(pos.symbol, pnl_pct)
        accelerating = pnl_pct < prev_pnl < 0
        self._last_pnls[pos.symbol] = pnl_pct

        # --- Smart trailing stop (via coordinator) ---
        # Profit-Fetching Phase 5: this is a SECOND autonomous watchdog
        # winner-trail (locks a fraction of peak, or breakeven, on a profit
        # pullback). Gated by the same _pf_trail_off as the CHECK 2/3 trail so
        # that when the system is enabled the sniper spine is the sole
        # trailing-SL writer (single-writer invariant, Rule 6).
        if self.coordinator and pnl_pct > 0.5 and not _pf_trail_off:
            peak_pnl_pct = self.coordinator.update_peak_pnl(pos.symbol, pnl_pct)

            if peak_pnl_pct > 4.0 and pnl_pct < peak_pnl_pct * 0.6:
                # Big profit dropping — lock 50% of peak
                lock_pnl_frac = peak_pnl_pct * 0.5 / 100
                if pos.side == Side.BUY:
                    new_sl = pos.entry_price * (1 + lock_pnl_frac)
                else:
                    new_sl = pos.entry_price * (1 - lock_pnl_frac)
                current_sl = pos.stop_loss or 0
                should_tighten = (
                    (pos.side == Side.BUY and new_sl > current_sl)
                    or (pos.side == Side.SELL and (current_sl == 0 or new_sl < current_sl))
                )
                if should_tighten:
                    log.info(
                        "Watchdog TRAILING: {sym} locking {lock:.1f}% profit, SL -> ${sl}",
                        sym=pos.symbol, lock=peak_pnl_pct * 0.5, sl=format_price(new_sl),
                    )
                    plan_h = self.coordinator.get_trade_plan(pos.symbol) if self.coordinator else None
                    side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                    await self._push_sl_to_shadow(
                        symbol=pos.symbol,
                        new_sl=new_sl,
                        plan=plan_h,
                        current_shadow_sl=current_sl,
                        direction=side_str,
                        source="watchdog_lock_peak",
                    )

            elif peak_pnl_pct > 2.0 and pnl_pct < peak_pnl_pct * 0.5:
                # Profit dropped 50% from peak — tighten SL to breakeven
                # Guard: only set breakeven if it actually TIGHTENS the SL
                breakeven_sl = pos.entry_price
                current_sl = pos.stop_loss or 0
                should_set = False
                if pos.side == Side.BUY:
                    # For Buy: tighter SL = higher SL (closer to current price)
                    should_set = current_sl > 0 and breakeven_sl > current_sl
                elif pos.side == Side.SELL:
                    # For Sell: tighter SL = lower SL (closer to current price)
                    should_set = current_sl > 0 and breakeven_sl < current_sl

                if should_set:
                    log.info(
                        f"WD_TRAIL_BE | sym={pos.symbol} peak={peak_pnl_pct:+.2f}% "
                        f"now={pnl_pct:+.2f}% old_sl={current_sl} new_sl={breakeven_sl} | {ctx()}"
                    )
                    plan_h = self.coordinator.get_trade_plan(pos.symbol) if self.coordinator else None
                    side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                    await self._push_sl_to_shadow(
                        symbol=pos.symbol,
                        new_sl=breakeven_sl,
                        plan=plan_h,
                        current_shadow_sl=current_sl,
                        direction=side_str,
                        source="watchdog_breakeven",
                    )
                else:
                    log.debug(
                        f"WD_TRAIL_BE_SKIP | sym={pos.symbol} peak={peak_pnl_pct:+.2f}% "
                        f"now={pnl_pct:+.2f}% be_sl={breakeven_sl} cur_sl={current_sl} "
                        f"side={pos.side} — breakeven not tighter than current SL | {ctx()}"
                    )

        # --- Collect warnings ---
        warnings: list[str] = []
        severity = AlertLevel.INFO

        if pnl_pct < -cfg.loss_warning_pct:
            warnings.append(f"Position down {pnl_pct:.2f}% from entry")
            severity = AlertLevel.WARNING

        if drawdown_from_peak_pct > cfg.trailing_loss_pct and peak_pnl > 0:
            warnings.append(f"Dropped {drawdown_from_peak_pct:.2f}% from peak profit")
            severity = AlertLevel.WARNING

        # Rapid move — but suppress in choppy markets (high choppiness = noise)
        choppiness = None
        try:
            choppiness = ta_data.get("volatility", {}).get("choppiness_index") if hasattr(self, 'ta_engine') and self.ta_engine else None
        except Exception as e:
            log.debug("choppiness index extraction failed: {err}", err=str(e))

        is_choppy = choppiness is not None and choppiness > 60

        if is_against and abs(price_change_pct) > cfg.rapid_move_pct and not is_choppy:
            warnings.append(f"Rapid move against: {price_change_pct:+.2f}% in {cfg.check_interval_seconds}s")
            severity = AlertLevel.CRITICAL
        elif is_against and abs(price_change_pct) > cfg.rapid_move_pct and is_choppy:
            # Choppy market — the "rapid move" is just noise, downgrade to info
            pass  # Don't add warning for noise in choppy markets

        if sl_proximity_pct is not None and sl_proximity_pct > cfg.sl_proximity_pct:
            warnings.append(f"Price {sl_proximity_pct:.0f}% of the way to stop-loss")
            severity = AlertLevel.CRITICAL

        if accelerating and pnl_pct < -cfg.loss_warning_pct:
            warnings.append("Loss accelerating (PnL worse than last check)")

        if not warnings:
            return

        # --- Send alert (deduplicated: max once per 60 seconds per symbol) ---
        alert_cooldown = 60.0
        last_alert = self._last_alert_time.get(pos.symbol, 0)
        if self.alert_manager and time.monotonic() - last_alert >= alert_cooldown:
            try:
                await self.alert_manager.send_watchdog_alert(
                    position=pos,
                    current_price=current_price,
                    pnl_pct=pnl_pct,
                    warnings=warnings,
                    severity=severity,
                )
                self._last_alert_time[pos.symbol] = time.monotonic()
            except Exception as e:
                # Phase 12.6 (Gap 6.4-G1): structured tag.
                log.error(f"WD_ALERT_FAIL | err='{str(e)[:120]}' | {ctx()}")

        # --- Buffer notable events for Claude's next review (#8) ---
        if self.event_buffer:
            rapid_trigger = is_against and abs(price_change_pct) > cfg.rapid_move_pct * 2 and not is_choppy
            if rapid_trigger:
                self.event_buffer.add_event(
                    "MED", "rapid_move_against", pos.symbol,
                    pnl_pct=round(pnl_pct, 2),
                    price_change_pct=round(price_change_pct, 2),
                )
            if pnl_pct < -cfg.brain_trigger_loss_pct:
                self.event_buffer.add_event(
                    "HIGH", "critical_loss", pos.symbol,
                    pnl_pct=round(pnl_pct, 2), price=current_price,
                )
            if sl_proximity_pct is not None and sl_proximity_pct > 70:
                self.event_buffer.add_event(
                    "MED", "sl_proximity", pos.symbol,
                    sl_proximity_pct=round(sl_proximity_pct, 0),
                    pnl_pct=round(pnl_pct, 2),
                )

        # --- Brain review trigger (passive mode only) ---
        # In PASSIVE mode: queue concern for brain's next Call A/B cycle.
        # In SAFETY_NET mode: hardcoded rules (hard stop, trailing, timer) already
        # handle danger — they fire earlier in _monitor_position() regardless of mode.
        if self._watchdog_mode == "passive" and warnings:
            if pnl_pct < -cfg.brain_trigger_loss_pct or len(warnings) >= 2:
                await self._maybe_trigger_brain(pos, ticker, pnl_pct, warnings)

    async def _maybe_trigger_brain(
        self,
        pos: Position,
        ticker: Ticker,
        pnl_pct: float,
        warnings: list[str],
    ) -> None:
        """Check guards and trigger Claude Brain if allowed."""
        cfg = self.settings.watchdog
        symbol = pos.symbol

        # Hold suppression: after Brain says HOLD, use escalating cooldown
        # 1st hold = normal cooldown (120s), 2nd = 2x (240s), 3rd = 4x (480s), etc.
        holds = self._consecutive_holds.get(symbol, 0)
        suppressed_until = self._hold_suppression.get(symbol, 0)
        if time.monotonic() < suppressed_until:
            return

        # Standard cooldown check
        last_call = self._last_brain_call.get(symbol, 0)
        if time.monotonic() - last_call < cfg.brain_cooldown_seconds:
            return

        # Hourly rate limit
        self._reset_hourly_counter()
        if self._brain_calls_this_hour >= cfg.max_brain_calls_per_hour:
            # Phase 12.6 (Gap 6.4-G2): structured tag.
            log.warning(f"WD_BRAIN_BUDGET_LIMIT | reason=max_calls_per_hour | {ctx()}")
            return

        # Budget check
        if not self.claude_client or not self.cost_tracker:
            log.debug("Watchdog: Claude client not available, skipping Brain")
            return
        if not self.cost_tracker.can_afford_call():
            # Phase 12.6 (Gap 6.4-G2): structured tag.
            log.warning(f"WD_BUDGET_EXCEEDED | reason=daily_budget | {ctx()}")
            return

        if not self.decision_parser:
            log.debug("Watchdog: decision parser not available, skipping Brain")
            return

        # ═══ MODE DISPATCH ═══
        if self._watchdog_mode == "passive" and self.urgent_queue:
            # PASSIVE mode: queue concern for next Call A/B (no direct Claude call)
            from src.core.urgent_queue import WatchdogConcern
            sl_prox = self._calculate_sl_proximity(pos, ticker.last_price) or 0
            side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            age_minutes = 0
            if self.coordinator:
                _plan = self.coordinator.get_trade_plan(pos.symbol)
                age_minutes = _plan.age_minutes if _plan else 0

            concern = WatchdogConcern(
                symbol=pos.symbol,
                pnl_pct=pnl_pct,
                warnings=warnings,
                current_price=ticker.last_price,
                entry_price=pos.entry_price,
                side=side_val,
                sl_proximity_pct=sl_prox,
                position_age_minutes=age_minutes,
                stop_loss=pos.stop_loss if pos.stop_loss else 0,
                urgency="CRITICAL" if pnl_pct < -2.5 or sl_prox > 80 else "HIGH",
            )
            added = self.urgent_queue.add_concern(concern)
            if added:
                log.info(
                    f"WD_QUEUE | sym={pos.symbol} pnl={pnl_pct:+.2f}% "
                    f"sl={sl_prox:.0f}% | queued for next brain call | {ctx()}"
                )
            self._last_brain_call[symbol] = time.monotonic()
            return

        # Fallback: urgent_queue not available — call Claude directly (legacy path)
        decision = await self._ask_brain(pos, ticker, pnl_pct, warnings)
        if decision:
            await self._execute_decision(pos, decision)

            # Escalating hold suppression
            if decision.action == "hold":
                holds = self._consecutive_holds.get(symbol, 0) + 1
                self._consecutive_holds[symbol] = holds
                # Escalate: 2min, 4min, 8min, 16min, max 30min
                suppression_seconds = min(cfg.brain_cooldown_seconds * (2 ** (holds - 1)), 1800)
                self._hold_suppression[symbol] = time.monotonic() + suppression_seconds
                log.info(
                    "Watchdog: HOLD #{n} for {sym}, next Brain check in {s}s",
                    n=holds, sym=symbol, s=suppression_seconds,
                )
            else:
                # Action taken — reset hold counter
                self._consecutive_holds[symbol] = 0
                self._hold_suppression.pop(symbol, None)

        self._last_brain_call[symbol] = time.monotonic()
        self._brain_calls_this_hour += 1

    async def _ask_brain(
        self,
        pos: Position,
        ticker: Ticker,
        pnl_pct: float,
        warnings: list[str],
    ) -> WatchdogDecision | None:
        """Call Claude API for a position-specific analysis."""
        # Get TA context
        ta_signal = "unknown"
        ta_confidence = 0
        ta_reasons: list[str] = []
        if self.ta_engine:
            try:
                ta_result = await self.ta_engine.analyze(
                    symbol=pos.symbol, timeframe=TimeFrame.M5, limit=100,
                )
                overall = ta_result.get("overall", {})
                ta_signal = overall.get("signal", "unknown")
                ta_confidence = overall.get("confidence", 0)
                ta_reasons = overall.get("key_reasons", [])
            except Exception as e:
                log.debug("TA engine analysis for brain context failed: {err}", err=str(e))

        # Get account context
        equity = 0.0
        available = 0.0
        if self.account_service:
            try:
                account = await self.account_service.get_wallet_balance()
                equity = account.total_equity
                available = account.available_balance
            except Exception as e:
                log.debug("wallet balance fetch failed: {err}", err=str(e))

        # Build age context for intelligent watchdog decisions (via coordinator)
        if self.coordinator:
            age_ctx = self.coordinator.get_age_context_for_prompt(pos.symbol, pnl_pct)
        else:
            # Fallback to local tracking
            open_time = self._position_open_times.get(pos.symbol)
            strategy_cat = self._position_strategies.get(pos.symbol, "default")
            if open_time:
                age_seconds = (datetime.now(timezone.utc) - open_time).total_seconds()
                if age_seconds < 60:
                    position_age = f"{age_seconds:.0f} seconds"
                elif age_seconds < 3600:
                    position_age = f"{age_seconds / 60:.1f} minutes"
                else:
                    position_age = f"{age_seconds / 3600:.1f} hours"
                sl_prox = self._calculate_sl_proximity(pos, ticker.last_price) or 0
                _, maturity_phase = self._check_maturity(age_seconds, pnl_pct, sl_prox)
                age_context = self._build_age_context(age_seconds, pnl_pct, strategy_cat)
            else:
                position_age = "Unknown"
                strategy_cat = "unknown"
                maturity_phase = "Unknown"
                age_context = ""
            age_ctx = {
                "position_age": position_age,
                "strategy_category": strategy_cat,
                "maturity_phase": maturity_phase,
                "age_context": age_context,
            }

        # Brain context dedup — skip if situation unchanged
        import hashlib
        context_str = f"{pos.symbol}:{pnl_pct:.1f}:{ta_signal}"
        context_hash = hashlib.md5(context_str.encode()).hexdigest()[:8]
        if self.coordinator and not self.coordinator.should_call_brain(pos.symbol, context_hash):
            log.debug("Watchdog: Skipping Brain call for {sym} — situation unchanged", sym=pos.symbol)
            return None

        # Per-coin regime for watchdog brain context
        _coin_regime_str = "unknown"
        _coin_regime_conf = "N/A"
        _regime_guidance = ""
        if self.regime_detector:
            _cr = self.regime_detector.get_coin_regime(pos.symbol)
            if _cr:
                _coin_regime_str = _cr.regime.value
                _coin_regime_conf = f"{_cr.confidence*100:.0f}%"
                _side_str = pos.side.value if hasattr(pos.side, 'value') else str(pos.side)
                _dir_match = (
                    ("up" in _coin_regime_str and _side_str in ("Buy", "Long"))
                    or ("down" in _coin_regime_str and _side_str in ("Sell", "Short"))
                )
                if _dir_match:
                    _regime_guidance = (
                        ">>> Position direction MATCHES coin regime — "
                        "be patient with temporary drawdowns."
                    )
                elif "up" in _coin_regime_str or "down" in _coin_regime_str:
                    _regime_guidance = (
                        ">>> Position direction CONFLICTS with coin regime — "
                        "consider closing if loss persists."
                    )

        prompt = POSITION_REVIEW_PROMPT.format(
            symbol=pos.symbol,
            side=pos.side.value,
            entry_price=pos.entry_price,
            current_price=ticker.last_price,
            mark_price=pos.mark_price,
            pnl_pct=f"{pnl_pct:+.2f}",
            unrealized_pnl=f"{pos.unrealized_pnl:+.2f}",
            leverage=pos.leverage,
            position_size=pos.size,
            stop_loss=f"${format_price(pos.stop_loss)}" if pos.stop_loss else "None",
            take_profit=f"${format_price(pos.take_profit)}" if pos.take_profit else "None",
            liquidation_price=pos.liquidation_price,
            position_age=age_ctx["position_age"],
            strategy_category=age_ctx["strategy_category"],
            maturity_phase=age_ctx["maturity_phase"],
            age_context=age_ctx["age_context"],
            warnings="\n".join(f"  - {w}" for w in warnings),
            ta_signal=ta_signal,
            ta_confidence=ta_confidence,
            ta_key_reasons="\n".join(f"  - {r}" for r in ta_reasons) if ta_reasons else "  - N/A",
            equity=f"{equity:.2f}",
            available=f"{available:.2f}",
            coin_regime=_coin_regime_str,
            coin_regime_confidence=_coin_regime_conf,
            regime_guidance=_regime_guidance,
        )

        log.info(f"WD_BRAIN_ASK | sym={pos.symbol} pnl={pnl_pct:+.2f}% warnings={len(warnings)} | {ctx()}")
        try:
            response = await asyncio.wait_for(
                self.claude_client.send_message(
                    prompt=prompt,
                    system_prompt=WATCHDOG_SYSTEM_PROMPT,
                ),
                timeout=60.0,  # Hard timeout — fall back to safety net if Claude is slow
            )

            # ClaudeClient returns dict {"text": ...}, ClaudeCodeClient returns str
            if isinstance(response, dict):
                response_text = response.get("text", "")
                cost_usd = response.get("cost_usd", 0.0)
            else:
                response_text = str(response)
                cost_usd = 0.0

            decision = self.decision_parser.parse_watchdog_decision(response_text)
            decision.symbol = pos.symbol

            log.info(f"WD_BRAIN_RESP | sym={pos.symbol} act={decision.action} conf={decision.confidence:.2f} rsn='{decision.reasoning[:80]}' | {ctx()}")

            # Send decision alert
            if self.alert_manager:
                try:
                    await self.alert_manager.send_watchdog_decision(
                        pos, decision, cost_usd,
                    )
                except Exception as e:
                    # Phase 12.6 (Gap 6.4-G1): structured tag.
                    log.error(f"WD_DECISION_ALERT_FAIL | err='{str(e)[:120]}' | {ctx()}")

            return decision

        except asyncio.TimeoutError:
            log.warning(
                f"WD_CLAUDE_TIMEOUT | sym={pos.symbol} pnl={pnl_pct:+.2f}% "
                f"timeout=60s — using safety net rules | {ctx()}"
            )
            return None
        except Exception as e:
            log.error(
                "Watchdog Brain call failed for {sym}: {err}",
                sym=pos.symbol, err=str(e),
            )
            if self.alert_manager:
                try:
                    await self.alert_manager.send_error_alert(
                        "watchdog",
                        f"Brain call failed for {pos.symbol}: {e}",
                        AlertLevel.WARNING,
                    )
                except Exception as e:
                    log.debug("brain error alert send failed: {err}", err=str(e))
            return None

    async def _execute_decision(
        self, pos: Position, decision: WatchdogDecision,
    ) -> dict:
        """Execute the watchdog Brain's decision on a position."""
        try:
            if decision.action == "hold":
                log.info(
                    "Watchdog: HOLD {sym} -- {reason}",
                    sym=pos.symbol, reason=decision.reasoning[:100],
                )
                return {"executed": False, "action": "hold"}

            if decision.action == "tighten_stop":
                return await self._execute_tighten_stop(pos, decision)

            if decision.action == "partial_close":
                return await self._execute_partial_close(pos, decision)

            if decision.action == "full_close":
                return await self._execute_full_close(pos, decision)

            log.warning(
                "Watchdog: unknown action '{a}' for {sym}",
                a=decision.action, sym=pos.symbol,
            )
            return {"executed": False, "action": decision.action, "error": "unknown action"}

        except Exception as e:
            log.error(
                "Watchdog: failed to execute {action} for {sym}: {err}",
                action=decision.action, sym=pos.symbol, err=str(e),
            )
            if self.alert_manager:
                try:
                    await self.alert_manager.send_error_alert(
                        "watchdog",
                        f"Execution failed for {pos.symbol}: {e}",
                        AlertLevel.CRITICAL,
                    )
                except Exception as e:
                    log.debug("execution error alert send failed: {err}", err=str(e))
            return {"executed": False, "action": decision.action, "error": str(e)}

    async def _execute_tighten_stop(
        self, pos: Position, decision: WatchdogDecision,
    ) -> dict:
        """Move stop-loss closer to current price. SAFETY CRITICAL."""
        new_sl = decision.new_stop_loss
        if new_sl is None:
            log.warning(
                "Watchdog: tighten_stop for {sym} but no new_stop_loss provided",
                sym=pos.symbol,
            )
            return {"executed": False, "error": "no new_stop_loss"}

        # Validate: new SL must be TIGHTER than current
        if pos.side == Side.BUY:
            # LONG: tighter SL = HIGHER SL (closer to current price)
            if pos.stop_loss is not None and new_sl <= pos.stop_loss:
                log.warning(
                    "Watchdog: REJECTED tighten_stop for LONG {sym}: "
                    "new SL {new} <= current SL {old}",
                    sym=pos.symbol, new=new_sl, old=pos.stop_loss,
                )
                return {"executed": False, "error": "new SL not tighter for LONG"}
        elif pos.side == Side.SELL:
            # SHORT: tighter SL = LOWER SL (closer to current price)
            if pos.stop_loss is not None and new_sl >= pos.stop_loss:
                log.warning(
                    "Watchdog: REJECTED tighten_stop for SHORT {sym}: "
                    "new SL {new} >= current SL {old}",
                    sym=pos.symbol, new=new_sl, old=pos.stop_loss,
                )
                return {"executed": False, "error": "new SL not tighter for SHORT"}

        plan = self.coordinator.get_trade_plan(pos.symbol) if self.coordinator else None
        side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
        pushed = await self._push_sl_to_shadow(
            symbol=pos.symbol,
            new_sl=new_sl,
            plan=plan,
            current_shadow_sl=pos.stop_loss,
            direction=side_str,
            source="watchdog_tighten",
        )
        if pushed:
            log.info(
                "Watchdog: tightened SL for {sym}: {old} -> {new}",
                sym=pos.symbol,
                old=pos.stop_loss,
                new=new_sl,
            )
            return {"executed": True, "action": "tighten_stop", "new_stop_loss": new_sl}
        return {"executed": False, "action": "tighten_stop", "error": "propagation skipped or failed"}

    async def _execute_partial_close(
        self, pos: Position, decision: WatchdogDecision,
    ) -> dict:
        """Close a percentage of the position."""
        close_pct = self.settings.watchdog.partial_close_pct
        close_qty = pos.size * (close_pct / 100.0)

        order = await self.position_service.reduce_position(
            pos.symbol, close_qty,
        )

        # Update risk tracking with estimated PnL for the closed portion
        if self.risk_manager:
            estimated_pnl = pos.unrealized_pnl * (close_pct / 100.0)
            await self.risk_manager.on_trade_closed(estimated_pnl)

        log.info(
            "Watchdog: partial close {pct}% of {sym} ({qty})",
            pct=close_pct, sym=pos.symbol, qty=close_qty,
        )
        return {"executed": True, "action": "partial_close", "close_pct": close_pct}

    async def _execute_full_close(
        self, pos: Position, decision: WatchdogDecision,
    ) -> dict:
        """Close the entire position.

        Phase 1 of the price-source-divergence fix (2026-05-03)
        introduced ``coordinator.resolve_authoritative_pnl`` which
        fetches Shadow's post-fee post-slippage ``net_pnl_usd`` from
        ``get_last_close`` after the close completes. Both
        ``risk_manager.on_trade_closed`` and ``coordinator.on_trade_closed``
        receive the same resolved value so downstream consumers
        (drawdown tracking, win/loss buckets, TIAS) see consistent
        numbers — pre-fix the risk_manager saw ``pos.unrealized_pnl``
        (Transformer-overwritten live value) while the coordinator
        chain saw a different locally-recomputed value.
        """
        pnl_pct = self._calculate_pnl_pct(pos, pos.mark_price) if pos.mark_price else 0
        order = await self.position_service.close_position(pos.symbol, close_trigger="wd_full_close")

        # Resolve authoritative pnl_usd / pnl_pct once so both downstream
        # consumers (risk_manager + coordinator) see the same value.
        # Falls back to ``pos.unrealized_pnl`` when no coordinator is
        # wired (defensive — should not happen in production but kept
        # safe for unit-test mocks). Bybit live mode also falls back
        # because get_last_close is not implemented on the live
        # PositionService (per ``transformer.py:1020-1030``).
        if self.coordinator:
            auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                await self.coordinator.resolve_authoritative_pnl(
                    symbol=pos.symbol,
                    position_service=self.position_service,
                    fallback_pnl_usd=pos.unrealized_pnl,
                    fallback_exit_price=pos.mark_price,
                    fallback_pnl_pct=pnl_pct,
                    # PnL-truth (2026-06-07): identity-match this trade's closed-pnl
                    # row by qty instead of the legacy rows[0] (no-hint) lookup.
                    qty=pos.size,
                )
            )
        else:
            auth_pnl_usd = pos.unrealized_pnl
            auth_pnl_pct = pnl_pct
            price_src = "local_fallback"
            auth_exit = None

        if self.risk_manager:
            await self.risk_manager.on_trade_closed(auth_pnl_usd)

        if self.coordinator:
            self.coordinator.on_trade_closed(
                symbol=pos.symbol,
                pnl_pct=auth_pnl_pct,
                pnl_usd=auth_pnl_usd,
                was_win=auth_pnl_usd > 0,
                closed_by="watchdog",
                exit_price=auth_exit,
                price_source=price_src,
            )

        # Phase 12.6 (Gap 6.4-G2): structured tag.
        log.info(f"WD_FULL_CLOSE | sym={pos.symbol} | {ctx()}")
        return {"executed": True, "action": "full_close"}

    # --- Close alert ---

    async def _send_close_alert(
        self, symbol: str, reason: str, detail: str, pnl_pct: float,
    ) -> None:
        """Send detailed Telegram alert on position close — EVERY parameter visible."""
        result_label = "WIN" if pnl_pct > 0 else "LOSS"

        plan = None
        trade_info = {}
        if self.coordinator:
            plan = self.coordinator.get_trade_plan(symbol)
            trade_info = self.coordinator.get_trade_info(symbol) or {}

        current_price = 0
        try:
            if self.market_service:
                ticker = await self.market_service.get_ticker(symbol)
                current_price = ticker.last_price if ticker else 0
        except Exception as e:
            log.debug("ticker fetch for close notification failed: {err}", err=str(e))

        lines = [
            f"<b>{result_label}: {reason}</b>",
            "",
            f"<b>Symbol:</b> {symbol}",
            f"<b>PnL:</b> {pnl_pct:+.2f}%",
        ]

        if plan:
            pnl_dollars = pnl_pct / 100 * float(trade_info.get("amount_usd", 500))
            hold_pct = (
                (plan.age_minutes / plan.max_hold_minutes * 100)
                if plan.max_hold_minutes > 0
                else 0
            )

            lines.extend([
                f"<b>PnL $:</b> ${pnl_dollars:+.2f}",
                "",
                f"<b>Direction:</b> {plan.direction}",
                f"<b>Entry:</b> ${format_price(plan.entry_price)}",
                f"<b>Exit:</b> ${format_price(current_price)}",
                "",
                f"<b>SL was:</b> ${format_price(plan.stop_loss_price)}",
                f"<b>TP was:</b> ${format_price(plan.target_price)}",
                "",
                f"<b>Time held:</b> {plan.age_minutes:.0f}min / {plan.max_hold_minutes}min ({hold_pct:.0f}%)",
                f"<b>Trailing:</b> {'ACTIVE at $' + format_price(plan.trailing_stop_price) if plan.trailing_active else 'never activated'}",
                "",
                f"<b>Strategy:</b> {trade_info.get('strategy_name', 'unknown')}",
                f"<b>Score:</b> {trade_info.get('score', '?')}/100",
                f"<b>Consensus:</b> {trade_info.get('consensus', '?')}",
                f"<b>Thesis:</b> {plan.reasoning[:80]}",
            ])
        else:
            lines.append(f"<b>Detail:</b> {detail}")

        if self.alert_manager:
            try:
                await self.alert_manager.send_custom("\n".join(lines), AlertLevel.INFO)
            except Exception as e:
                log.debug("close notification alert send failed: {err}", err=str(e))

    # --- Helper methods ---

    @staticmethod
    def _calculate_pnl_pct(pos: Position, current_price: float) -> float:
        """Calculate PnL percentage from entry, accounting for side."""
        if pos.entry_price <= 0:
            return 0.0
        if pos.side == Side.BUY:
            return ((current_price - pos.entry_price) / pos.entry_price) * 100
        else:
            return ((pos.entry_price - current_price) / pos.entry_price) * 100

    @staticmethod
    def _calculate_sl_proximity(pos: Position, current_price: float) -> float | None:
        """Calculate how close price is to stop-loss as percentage (0-100).

        Returns None if no stop-loss set.
        100% means price has reached the stop-loss.

        C1 Phase 1.4b (2026-05-21): now delegates to the shared
        ``compute_sl_consumption_pct`` helper in
        ``src/risk/wd_brain_scoring.py``. The brain CALL_B prompt and
        this watchdog method now compute SL % consumed via the exact
        same arithmetic; the only remaining divergence axis is which
        SL value is passed (entry-time vs current trailed), surfaced
        per-vote by ``WD_SL_PCT_DIVERGENCE``.

        Behaviour preserved for the four existing callers — returns 0
        when no consumption (price at or beyond entry on the favourable
        side), clamps at 100 when price has reached or wicked past the
        stop. Prior implementation could return > 100 in the rare wick-
        past-SL case; the helper clamps to 100 (same semantic — "the
        stop has been reached").
        """
        side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
        return compute_sl_consumption_pct(
            side=side_val,
            entry_price=float(pos.entry_price or 0.0),
            stop_loss=float(pos.stop_loss or 0.0),
            current_price=float(current_price or 0.0),
        )

    def _reset_hourly_counter(self) -> None:
        """Reset brain call counter if an hour has passed."""
        now = time.monotonic()
        if now - self._hour_start >= 3600:
            self._brain_calls_this_hour = 0
            self._hour_start = now

    async def _get_position_strategy_category(self, symbol: str) -> str:
        """Find which strategy category opened this position."""
        try:
            result = await self.db.fetch_one(
                "SELECT strategy_name FROM strategy_trades WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol,),
            )
            if result and result.get("strategy_name"):
                prefix = result["strategy_name"].split("_")[0]
                cat_map = {
                    "A1": "scalping", "A2": "scalping", "A3": "scalping", "A4": "scalping",
                    "B1": "momentum", "B2": "momentum", "B3": "momentum", "B4": "momentum",
                    "C1": "mean_reversion", "C2": "mean_reversion",
                    "D1": "funding_arb", "D2": "funding_arb",
                    "E1": "sentiment", "E2": "sentiment", "E3": "sentiment",
                    "F1": "advanced", "F2": "advanced", "F3": "advanced", "F4": "advanced",
                    "G1": "predatory", "G2": "predatory", "G3": "predatory", "G4": "predatory",
                    "H1": "microstructure", "H2": "microstructure", "H3": "microstructure", "H4": "microstructure",
                    "I1": "time_based", "I2": "time_based", "I3": "time_based", "I4": "time_based",
                    "J1": "cross_market", "J2": "cross_market", "J3": "cross_market", "J4": "cross_market",
                    "K1": "ai_enhanced", "K2": "ai_enhanced", "X1": "kickstart", "GEN": "ai_generated",
                }
                return cat_map.get(prefix, "default")
        except Exception as e:
            log.debug("strategy category lookup failed: {err}", err=str(e))
        return "default"

    @staticmethod
    def _check_maturity(age_seconds: float, pnl_pct: float, sl_proximity_pct: float) -> tuple[bool, str]:
        """Fallback maturity check when the coordinator is unavailable.

        Mirrors TradeCoordinator.get_maturity() — two-phase model:
          - newborn (0-120s): cannot close (grace period).
          - mature (120s+): all normal rules apply.

        pnl_pct and sl_proximity_pct retained in signature for caller
        compatibility (no live caller uses them after the SL Hierarchy
        overhaul 2026-04-22).
        """
        if age_seconds < 120:
            return (False, f"Newborn ({age_seconds:.0f}s) -- cannot close")
        return (True, f"Mature ({age_seconds/60:.0f}min) -- normal rules apply")

    @staticmethod
    def _build_age_context(age_seconds: float, pnl_pct: float, strategy_cat: str) -> str:
        """Build age-aware guidance string for Claude Brain prompt."""
        if age_seconds < 300:
            return (
                ">>> MANDATORY: This position is LESS THAN 5 MINUTES OLD. "
                "You MUST choose 'hold'. Closing now guarantees a loss from fees. "
                "The strategy has not had time to play out. DO NOT CLOSE."
            )
        if age_seconds < 900:
            if pnl_pct > 0:
                return (
                    f">>> This position is {age_seconds / 60:.0f} min old and PROFITABLE ({pnl_pct:+.2f}%). "
                    "Prefer 'tighten_stop' to lock in gains. Do NOT close a winner early."
                )
            expected_hold = {"scalping": 300, "momentum": 900, "mean_reversion": 600}.get(strategy_cat, 600)
            if age_seconds < expected_hold:
                return (
                    f">>> This {strategy_cat} position is only {age_seconds / 60:.0f} min old "
                    f"(expected hold: {expected_hold / 60:.0f} min). Prefer 'hold' — "
                    "it hasn't reached its expected timeframe yet."
                )
            return (
                f">>> Position is {age_seconds / 60:.0f} min old. Still developing. "
                "Only close if loss > 2% or clearly reversing."
            )
        if age_seconds < 1800:
            if pnl_pct > 0:
                return (
                    f">>> Mature position ({age_seconds / 60:.0f} min), PROFITABLE ({pnl_pct:+.2f}%). "
                    "Consider tightening stop to lock in gains."
                )
            return f">>> Mature position ({age_seconds / 60:.0f} min). Normal rules apply — use full judgment."
        # Aged position (30+ min)
        if pnl_pct < -1.0:
            return (
                f">>> AGED position ({age_seconds / 60:.0f} min) with loss ({pnl_pct:+.2f}%). "
                "Consider closing — this trade has had enough time."
            )
        return f">>> Aged position ({age_seconds / 60:.0f} min). Normal rules apply."

    async def _execute_strategic_actions(self) -> None:
        """Execute strategic position actions queued by LayerManager via TradeCoordinator."""
        if not self.coordinator:
            return
        actions = self.coordinator.drain_strategic_actions()
        if not actions:
            return

        for action in actions:
            symbol = action["symbol"]
            act = action["action"]
            reason = action.get("reason", "")

            # Position re-verification: check existence BEFORE executing
            # (brain cycle is 3-5min — position may have closed during that time)
            if act in ("close", "take_profit", "set_exit", "tighten_stop"):
                try:
                    _pos_check = await self.position_service.get_position(symbol)
                    if not _pos_check or not _pos_check.size:
                        log.info(
                            f"POS_ACTION_SKIP | sym={symbol} act={act} | "
                            f"Position closed during brain cycle | {ctx()}"
                        )
                        continue
                except Exception:
                    pass  # If check fails, proceed with action (fail-safe)

            # Phase 1B (Post-Execution Closure Fix, 2026-05-05) —
            # minimum-hold guardrail. Refuses strategic close/take_profit
            # actions on positions younger than
            # ``strategic_action_min_hold_seconds`` UNLESS the close reason
            # matches a recognised hard-stop signal (SL/TP/structure/
            # regime/manual). tighten_stop and set_exit are unaffected
            # — they modify but do not destroy the position.
            #
            # Defense-in-depth against the recency-bias closure path
            # (Phase 1A removed the trigger language; this layer ensures
            # any future re-introduction or operator-edited prompt cannot
            # destroy a fresh position before SL/TP can resolve).
            #
            # Fail-closed semantics: a malformed/empty reason on a young
            # position is BLOCKED. Missing coordinator → ``_age_sec=0`` →
            # blocked too. ``coordinator.get_age_seconds`` returns 99999
            # for unregistered trades; those have no min-hold contract and
            # fall through to existing behaviour intentionally.
            if act in ("close", "take_profit"):
                _wd_cfg = getattr(self.settings, "watchdog", None)
                _min_hold = float(getattr(
                    _wd_cfg, "strategic_action_min_hold_seconds", 300.0,
                )) if _wd_cfg is not None else 300.0
                _allowed_reasons = list(getattr(
                    _wd_cfg,
                    "strategic_action_allowed_early_close_reasons",
                    [
                        "stop loss hit", "sl hit",
                        "take profit hit", "tp hit",
                        "structure invalidated", "setup broken",
                        "regime change", "regime shift",
                        "manual operator close", "manual close",
                    ],
                )) if _wd_cfg is not None else []

                if self.coordinator is None:
                    _age_sec = 0.0
                else:
                    try:
                        _age_sec = float(
                            self.coordinator.get_age_seconds(symbol),
                        )
                    except Exception:
                        _age_sec = 0.0

                _reason_lc = (reason or "").strip().lower()
                _reason_allowed = (
                    any(tok in _reason_lc for tok in _allowed_reasons)
                    if _reason_lc else False
                )

                if _age_sec < _min_hold and not _reason_allowed:
                    log.warning(
                        f"STRAT_ACTION_CLOSE_BLOCKED | sym={symbol} "
                        f"age={_age_sec:.0f}s min_hold={_min_hold:.0f}s "
                        f"rsn='{(reason or '')[:120]}' "
                        f"reason_allowed=false close_skipped=true "
                        f"| {ctx()}"
                    )
                    continue

            # ── Issue 1 (2026-05-18) — brain-close multi-factor scoring ──
            # Runs ONLY for discretionary brain closes that survived the
            # 300s min-hold guard above (explicit SL/TP/structure/regime/
            # manual reasons bypass scoring via the allowed-reasons list).
            # Phase 1 (log-only) emits the score and falls through to the
            # existing close; Phase 2 (enforce) blocks sub-threshold votes
            # and tightens SL on strongly-rejected votes. See
            # IMPLEMENT_THREE_ISSUES_FIX.md Issue 1 §B.
            _scoring_skip_close = False
            if act in ("close", "take_profit"):
                _wd_cfg_s = getattr(self.settings, "watchdog", None)
                _scoring_enabled = bool(getattr(
                    _wd_cfg_s, "wd_brain_scoring_enabled", True,
                )) if _wd_cfg_s is not None else True
                # IMPLEMENT_FIVE_ISSUES_FIX.md Rule 7 (2026-05-20) — diagnostic
                # that confirms the close-vote reached the scoring intercept.
                # Fires unconditionally for every close/take_profit action so
                # operator log queries can correlate close volume against
                # WATCHDOG_CLOSE_SCORE_COMPUTED volume to detect any silent
                # bypass regression in the future. Pre-emit so the event
                # fires even if scoring is disabled or fails inside the try.
                _enforce_flag = bool(getattr(
                    _wd_cfg_s, "wd_brain_scoring_enforce", False,
                )) if _wd_cfg_s is not None else False
                log.info(
                    f"WD_SCORING_PATH_REACHED | sym={symbol} act={act} "
                    f"scoring_enabled={_scoring_enabled} "
                    f"enforce={_enforce_flag} "
                    f"reason_lc='{(reason or '').strip().lower()[:40]}' "
                    f"| {ctx()}"
                )
                if _scoring_enabled:
                    try:
                        from src.risk.wd_brain_scoring import (
                            compute_brain_close_score,
                            DEFAULT_THRESHOLD,
                        )
                        _enforce = bool(getattr(
                            _wd_cfg_s, "wd_brain_scoring_enforce", False,
                        )) if _wd_cfg_s is not None else False
                        _threshold = float(getattr(
                            _wd_cfg_s,
                            "wd_brain_scoring_threshold",
                            DEFAULT_THRESHOLD,
                        )) if _wd_cfg_s is not None else DEFAULT_THRESHOLD

                        # Re-use the position object fetched above when
                        # the close-branch position re-verify ran; on a
                        # miss (re-verify exception or unavailable
                        # service) re-fetch silently. Falls through to
                        # log-only neutral score if the position truly
                        # cannot be inspected.
                        _pos_for_score = locals().get("_pos_check", None)
                        if _pos_for_score is None:
                            try:
                                _pos_for_score = (
                                    await self.position_service.get_position(symbol)
                                )
                            except Exception:
                                _pos_for_score = None

                        log.info(
                            f"BRAIN_CLOSE_VOTE_RECEIVED | sym={symbol} "
                            f"act={act} rsn='{(reason or '')[:80]}' | {ctx()}"
                        )

                        # Factor inputs — each guarded against missing
                        # data; scoring module fail-softs internally too.
                        _pnl_pct = 0.0
                        _sl_consumption = None
                        _current_price = 0.0
                        if _pos_for_score is not None:
                            try:
                                _current_price = float(
                                    getattr(_pos_for_score, "mark_price", 0.0)
                                    or 0.0
                                )
                            except Exception:
                                _current_price = 0.0
                            if _current_price > 0:
                                try:
                                    _pnl_pct = float(self._calculate_pnl_pct(
                                        _pos_for_score, _current_price,
                                    ))
                                except Exception:
                                    _pnl_pct = 0.0
                                try:
                                    _sl_consumption = float(
                                        self._calculate_sl_proximity(
                                            _pos_for_score, _current_price,
                                        ),
                                    )
                                except Exception:
                                    _sl_consumption = None

                        # ── C1 Phase 1.4 — WD_SL_PCT_DIVERGENCE diagnostic ──
                        # Surfaces the brain-vs-scorer SL% divergence side
                        # by side per vote. The scorer reads pos.stop_loss
                        # (current, possibly trailed). The brain prompt
                        # reads thesis_data.stop_loss_price (entry-time).
                        # When the SL has been trailed, the two numbers
                        # diverge by definition. Pre-alignment this was an
                        # invisible factor in the composite; post-alignment
                        # (commit c1: brain CALL_B prompt renders current+
                        # entry SL% via shared helper) the brain prompt
                        # now shows both, and this diagnostic confirms the
                        # gap is purely a trailing artefact, not a formula
                        # bug. Read-only — does not feed the composite.
                        try:
                            _thesis_for_div = None
                            if (
                                self.thesis_manager is not None
                                and _pos_for_score is not None
                            ):
                                try:
                                    _thesis_for_div = (
                                        await self.thesis_manager
                                        .get_open_thesis_for_symbol(symbol)
                                    )
                                except Exception:
                                    _thesis_for_div = None
                            _sl_current = float(
                                getattr(_pos_for_score, "stop_loss", 0.0) or 0.0
                            ) if _pos_for_score is not None else 0.0
                            _sl_entry = 0.0
                            if _thesis_for_div:
                                try:
                                    _sl_entry = float(
                                        _thesis_for_div.get(
                                            "stop_loss_price", 0.0,
                                        ) or 0.0
                                    )
                                except Exception:
                                    _sl_entry = 0.0
                            _pct_current = _sl_consumption
                            _pct_entry = None
                            if (
                                _sl_entry > 0
                                and _pos_for_score is not None
                                and _current_price > 0
                            ):
                                _side_div = (
                                    _pos_for_score.side.value
                                    if hasattr(_pos_for_score.side, "value")
                                    else str(_pos_for_score.side)
                                )
                                _pct_entry = compute_sl_consumption_pct(
                                    side=_side_div,
                                    entry_price=_pos_for_score.entry_price,
                                    stop_loss=_sl_entry,
                                    current_price=_current_price,
                                )

                            def _bucket_of(_p: float | None) -> str:
                                if _p is None:
                                    return "unknown"
                                if _p < 30:
                                    return "spacious"
                                if _p < 60:
                                    return "comfortable"
                                if _p < 80:
                                    return "tight"
                                return "imminent"

                            _bk_current = _bucket_of(_pct_current)
                            _bk_entry = _bucket_of(_pct_entry)
                            _bk_flipped = (
                                _bk_current != _bk_entry
                                and _bk_current != "unknown"
                                and _bk_entry != "unknown"
                            )
                            _sl_trailed_flag = (
                                _sl_entry > 0
                                and _sl_current > 0
                                and abs(_sl_current - _sl_entry)
                                / max(_sl_entry, 1e-9) > 1e-4
                            )
                            _delta_pct = (
                                _pct_current - _pct_entry
                                if (
                                    _pct_current is not None
                                    and _pct_entry is not None
                                )
                                else None
                            )

                            def _fmt_pct(v: float | None) -> str:
                                return (
                                    f"{v:.2f}" if v is not None else "na"
                                )

                            log.info(
                                f"WD_SL_PCT_DIVERGENCE | sym={symbol} "
                                f"sl_current={_sl_current:.8f} "
                                f"sl_entry={_sl_entry:.8f} "
                                f"pct_current={_fmt_pct(_pct_current)} "
                                f"pct_entry={_fmt_pct(_pct_entry)} "
                                f"delta_pct={_fmt_pct(_delta_pct)} "
                                f"sl_tightened={_sl_trailed_flag} "
                                f"bucket_current={_bk_current} "
                                f"bucket_entry={_bk_entry} "
                                f"bucket_flipped={_bk_flipped} | {ctx()}"
                            )
                        except Exception as _de:
                            # Diagnostic must never block scoring.
                            log.debug(
                                f"WD_SL_PCT_DIVERGENCE_FAIL | sym={symbol} "
                                f"err='{str(_de)[:120]}' | {ctx()}"
                            )

                        _time_remaining_s = 0.0
                        _age_s = 0.0
                        if self.coordinator is not None:
                            try:
                                _plan_for_score = (
                                    self.coordinator.get_trade_plan(symbol)
                                )
                                if _plan_for_score is not None:
                                    _time_remaining_s = float(
                                        getattr(
                                            _plan_for_score,
                                            "remaining_minutes",
                                            0.0,
                                        ),
                                    ) * 60.0
                            except Exception:
                                _time_remaining_s = 0.0
                            try:
                                _age_s = float(
                                    self.coordinator.get_age_seconds(symbol),
                                )
                            except Exception:
                                _age_s = 0.0

                        # Velocity: prefer TimeDecayState.prev_velocity
                        # (loser-lane positions only); fall back to a
                        # derived (pnl_now - pnl_prev)/(ts_now - ts_prev)
                        # via the _brain_score_prev_pnl cache.
                        _velocity = None
                        _td_state = self._td_states.get(symbol)
                        if _td_state is not None:
                            _v_raw = getattr(_td_state, "prev_velocity", None)
                            if _v_raw is not None:
                                try:
                                    _velocity = float(_v_raw)
                                except Exception:
                                    _velocity = None
                        if _velocity is None:
                            _now_mono = time.monotonic()
                            _prev = self._brain_score_prev_pnl.get(symbol)
                            if _prev is not None:
                                _pnl_prev, _ts_prev = _prev
                                _dt = _now_mono - _ts_prev
                                if _dt > 0:
                                    _velocity = (_pnl_pct - _pnl_prev) / _dt
                            self._brain_score_prev_pnl[symbol] = (
                                _pnl_pct, _now_mono,
                            )

                        # XRAY structural match: compare verdict direction
                        # to the position side with staleness guard.
                        _xray_match = "unavailable"
                        if self.structure_cache is not None:
                            try:
                                _xray = self.structure_cache.get(symbol)
                                if _xray is not None:
                                    _xray_age = float(getattr(
                                        _xray, "age_seconds", 0.0,
                                    )) if hasattr(_xray, "age_seconds") else 0.0
                                    if _xray_age > 60.0:
                                        _xray_match = "stale"
                                    else:
                                        _xray_dir = str(getattr(
                                            _xray, "trade_direction", "",
                                        ) or "").lower()
                                        _pos_dir = ""
                                        if _pos_for_score is not None:
                                            _side_val = getattr(
                                                _pos_for_score, "side", None,
                                            )
                                            _pos_dir = str(getattr(
                                                _side_val, "value", _side_val,
                                            ) or "").lower()
                                        if _xray_dir and _pos_dir:
                                            _expected = (
                                                "long" if _pos_dir in ("buy",)
                                                else "short"
                                            )
                                            if _xray_dir == _expected:
                                                _xray_match = "supports"
                                            elif _xray_dir and _xray_dir != _expected:
                                                _xray_match = "broken"
                                            else:
                                                _xray_match = "neutral"
                                        else:
                                            _xray_match = "neutral"
                            except Exception:
                                _xray_match = "unavailable"

                        # P0-3 fix (2026-05-22) — pass brain_vote_present=True
                        # so the brain's explicit close vote contributes a
                        # bounded authority weight to the composite (gated
                        # on reasoning quality). The automated close paths
                        # do not reach this intercept (they bypass the
                        # scoring entirely), so True is correct for every
                        # call site that runs through this branch.
                        _score = compute_brain_close_score(
                            pnl_pct=_pnl_pct,
                            time_remaining_s=_time_remaining_s,
                            age_s=_age_s,
                            velocity_pct_per_s=_velocity,
                            sl_consumption_pct=_sl_consumption,
                            xray_match=_xray_match,
                            reasoning_text=reason or "",
                            threshold=_threshold,
                            brain_vote_present=True,
                        )

                        # P0-3 fix (2026-05-22) — hard_risk_floor. When the
                        # current SL consumption exceeds the configured
                        # floor (default 85%), force-close regardless of
                        # composite. Catches edge cases where the
                        # composite is mathematically below threshold but
                        # the position is already burning through its
                        # risk budget. Emit WATCHDOG_HARD_FLOOR_HIT so the
                        # operator can audit which closes the floor
                        # accelerated.
                        _hard_floor_pct = float(getattr(
                            _wd_cfg_s,
                            "wd_hard_risk_floor_sl_pct",
                            85.0,
                        )) if _wd_cfg_s is not None else 85.0
                        _hard_floor_active = (
                            _sl_consumption is not None
                            and _sl_consumption >= _hard_floor_pct
                        )

                        _log_fields = " ".join(
                            f"{k}={v}" for k, v in _score.as_log_dict().items()
                        )
                        log.warning(
                            f"WATCHDOG_CLOSE_SCORE_COMPUTED | sym={symbol} "
                            f"{_log_fields} "
                            f"hard_floor_pct={_hard_floor_pct:.1f} "
                            f"hard_floor_active={_hard_floor_active} | {ctx()}"
                        )

                        if not _enforce:
                            log.info(
                                f"WD_CLOSE_SCORE_LOG_ONLY | sym={symbol} "
                                f"composite={_score.composite:.2f} "
                                f"would_be={_score.recommendation} | {ctx()}"
                            )
                            # Fall through to the existing close call.
                        elif _hard_floor_active:
                            # P0-3 hard-floor branch — overrides composite.
                            # Even when the scoring recommendation is
                            # reject / reject_and_tighten, the floor
                            # fires the close. Single log line names the
                            # floor as the authority.
                            log.warning(
                                f"WATCHDOG_HARD_FLOOR_HIT | sym={symbol} "
                                f"sl_pct={(_sl_consumption or 0.0):.1f} "
                                f"floor={_hard_floor_pct:.1f} "
                                f"composite={_score.composite:.2f} "
                                f"would_be={_score.recommendation} | {ctx()}"
                            )
                            # Fall through to the existing close call.
                        else:
                            if _score.recommendation == "execute":
                                log.warning(
                                    f"WATCHDOG_CLOSE_EXECUTED | sym={symbol} "
                                    f"composite={_score.composite:.2f} | {ctx()}"
                                )
                                # Fall through to the existing close call.
                            elif _score.recommendation == "reject":
                                log.warning(
                                    f"WATCHDOG_CLOSE_REJECTED | sym={symbol} "
                                    f"composite={_score.composite:.2f} "
                                    f"threshold={_threshold:.2f} | {ctx()}"
                                )
                                _scoring_skip_close = True
                            else:  # reject_and_tighten
                                log.warning(
                                    f"WATCHDOG_CLOSE_OVERRIDE_TIGHTEN | "
                                    f"sym={symbol} "
                                    f"composite={_score.composite:.2f} | "
                                    f"{ctx()}"
                                )
                                if _pos_for_score is not None:
                                    await self._tighten_sl_breakeven_30pct(
                                        _pos_for_score,
                                    )
                                _scoring_skip_close = True
                    except Exception as _se:
                        # Fail-soft — scoring must not block legitimate
                        # close paths. Logged loudly so investigators
                        # can spot the regression.
                        log.warning(
                            f"WD_BRAIN_SCORE_FAIL | sym={symbol} "
                            f"err='{str(_se)[:120]}' enforce=skipped | "
                            f"{ctx()}"
                        )

            if _scoring_skip_close:
                continue

            try:
                if act in ("close", "take_profit"):
                    await self.position_service.close_position(symbol, close_trigger="wd_claude_action")
                    log.warning(f"STRAT_ACTION_CLOSE | sym={symbol} act={act} rsn='{reason[:80]}' | {ctx()}")

                elif act == "tighten_stop" and action.get("new_sl", 0) > 0:
                    pos = await self.position_service.get_position(symbol)
                    if pos:
                        new_sl = action["new_sl"]
                        current_sl = pos.stop_loss or 0
                        pos_dir = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                        is_tighter = True
                        if current_sl > 0:
                            if pos_dir in ("Buy", "Long"):
                                is_tighter = new_sl > current_sl
                            elif pos_dir in ("Sell", "Short"):
                                is_tighter = new_sl < current_sl
                        if is_tighter:
                            plan = self.coordinator.get_trade_plan(symbol) if self.coordinator else None
                            pushed = await self._push_sl_to_shadow(
                                symbol=symbol,
                                new_sl=new_sl,
                                plan=plan,
                                current_shadow_sl=current_sl,
                                direction=pos_dir,
                                source="brain_tighten",
                            )
                            if pushed:
                                log.info(f"STRAT_ACTION_SL | sym={symbol} old={current_sl} new={new_sl} | {ctx()}")
                        else:
                            log.warning(f"STRAT_ACTION_SL_SKIP | sym={symbol} req={new_sl} cur={current_sl} | {ctx()}")

                elif act == "set_exit" and action.get("exit_price", 0) > 0:
                    await self.position_service.set_take_profit(symbol, action["exit_price"])
                    log.info(f"STRAT_ACTION_TP | sym={symbol} tp={action['exit_price']} | {ctx()}")

            except Exception as e:
                err_str = str(e).lower()
                if "zero position" in err_str or "no open position" in err_str:
                    log.info(f"STRAT_ACTION_GONE | sym={symbol} act={act} | {ctx()}")
                else:
                    log.error(f"STRAT_ACTION_ERR | sym={symbol} act={act} err='{str(e)[:150]}' | {ctx()}")

    async def _execute_sentinel_recommendations(self) -> None:
        """Execute stop-tightening recommendations from SENTINEL Portfolio Advisor."""
        advisor = getattr(self, "_sentinel_advisor", None)
        if not advisor:
            return

        recs = advisor.drain_recommendations()
        if not recs:
            return

        for rec in recs:
            try:
                pos = await self.position_service.get_position(rec.symbol)
                if not pos:
                    continue

                entry_price = pos.entry_price
                if entry_price <= 0:
                    continue

                pos_dir = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                # J7 (2026-05-14) — direction-aware new-SL price uses
                # the shared helper signal (``is_long_side``) so the
                # sentinel path cannot drift from sniper / SL gateway
                # tightness conventions. Pre-J7 the same direction
                # branching was open-coded across six sites; the agent
                # investigation in dev_notes/seven_fixes/ confirmed
                # every one was correct but flagged the duplication as
                # the source of future regression risk. The helper
                # imports live at module scope above.
                _pos_is_long = is_long_side(pos.side)
                if _pos_is_long:
                    new_sl = entry_price * (1 - rec.new_sl_pct_from_entry / 100)
                else:
                    new_sl = entry_price * (1 + rec.new_sl_pct_from_entry / 100)

                # Only tighten, never widen. The helper handles both
                # directions plus the "no current stop" case (returns
                # True so first-install is treated as tightening).
                current_sl = pos.stop_loss or 0
                is_tighter = is_tighter_sl(pos.side, current_sl, new_sl)
                _skip_reason = "not_tighter" if not is_tighter else ""

                # TRADE LIBERATION: Block tighten on micro-profits.
                # J7 (2026-05-14) — capture the *actual* reason for the
                # skip so the downstream log emits accurate
                # attribution. Pre-J7 the SENTINEL_ADVISOR_SKIP message
                # claimed "not tighter" for both genuine direction-blind
                # skips AND for micro-profit blocks; the audit's OBS-12
                # interpretation was based on that misleading message.
                mark = pos.mark_price
                if is_tighter and mark > 0 and entry_price > 0:
                    if _pos_is_long:
                        position_pnl_pct = (mark - entry_price) / entry_price * 100
                    else:
                        position_pnl_pct = (entry_price - mark) / entry_price * 100
                    min_profit = self.settings.sentinel.advisor_min_profit_for_tighten_pct
                    if position_pnl_pct < min_profit:
                        log.info(
                            f"SENTINEL_ADVISOR_BLOCK | sym={rec.symbol} "
                            f"pnl={position_pnl_pct:+.3f}% < min={min_profit}% "
                            f"| Trade needs room to breathe | {ctx()}"
                        )
                        is_tighter = False
                        _skip_reason = "micro_profit_block"

                if is_tighter:
                    # ── Phase 8: consumer-side max_step clamp ──
                    # Gateway R3 rejects step > max_step_pct (0.5% default).
                    # SENTINEL occasionally proposes 2-3 % in one shot, which
                    # gets bounced with REJECT_WOULD step_exceeded and the
                    # remaining tightening is lost for this cycle. Clamping
                    # HERE ensures organic re-tightening over subsequent cycles
                    # closes the gap, rather than rejecting the whole move.
                    try:
                        _sl_cfg = getattr(self.settings, "sl_gateway", None)
                        _max_step = float(getattr(_sl_cfg, "max_step_pct", 0.5) or 0.5)
                    except Exception:
                        _max_step = 0.5
                    if current_sl > 0 and _max_step > 0:
                        _step_pct = abs(new_sl - current_sl) / current_sl * 100.0
                        if _step_pct > _max_step:
                            _sign = 1 if new_sl > current_sl else -1
                            _clamped = current_sl * (1.0 + _sign * _max_step / 100.0)
                            log.info(
                                f"SENTINEL_STEP_CLAMP | sym={rec.symbol} "
                                f"raw={new_sl:.6f} clamped={_clamped:.6f} "
                                f"step={_step_pct:.2f}% max={_max_step:.2f}% | {ctx()}"
                            )
                            new_sl = _clamped

                    plan = self.coordinator.get_trade_plan(rec.symbol) if self.coordinator else None
                    pushed = await self._push_sl_to_shadow(
                        symbol=rec.symbol,
                        new_sl=new_sl,
                        plan=plan,
                        current_shadow_sl=current_sl,
                        direction=pos_dir,
                        source="sentinel_advisor",
                    )
                    if pushed:
                        log.info(
                            f"SENTINEL_ADVISOR_SL | sym={rec.symbol} old={current_sl} "
                            f"new={new_sl} urgency={rec.urgency} "
                            f"rsn='{rec.reason[:60]}' | {ctx()}"
                        )
                else:
                    # J7 (2026-05-14) — emit the actual skip reason
                    # (``not_tighter`` vs ``micro_profit_block``) plus
                    # the side so operators reading the log can tell
                    # which gate fired. Pre-J7 the message always said
                    # "not tighter" regardless of which condition
                    # produced the skip; OBS-12 mistook the misleading
                    # message for a direction-blind comparison bug.
                    # Always include SENTINEL_TIGHTNESS_DIRECTION_AWARE
                    # so the corrected behaviour is observable.
                    log.info(
                        f"SENTINEL_TIGHTNESS_DIRECTION_AWARE | "
                        f"sym={rec.symbol} side={pos_dir} "
                        f"cur_sl={current_sl} req_sl={new_sl} "
                        f"is_tighter={is_tighter} "
                        f"reason={_skip_reason or 'not_tighter'} | {ctx()}"
                    )
                    log.info(
                        f"SENTINEL_ADVISOR_SKIP | sym={rec.symbol} "
                        f"req_sl={new_sl} cur_sl={current_sl} "
                        f"side={pos_dir} "
                        f"reason={_skip_reason or 'not_tighter'} | {ctx()}"
                    )
            except Exception as e:
                log.error(
                    f"SENTINEL_ADVISOR_ERR | sym={rec.symbol} "
                    f"err='{str(e)[:100]}' | {ctx()}"
                )

    async def _reconcile_with_shadow_fast(self, positions_now: list) -> None:
        """Phase 2 (P0-1) — fast set-diff reconcile with Shadow.

        Throttled to ``settings.watchdog.fast_reconcile_seconds`` (default 30 s).
        Compares the union of every per-symbol dict the watchdog owns
        against Shadow's current open-position set. Any symbol the
        watchdog still tracks but Shadow no longer reports is treated
        as a ghost: it routes through ``_detect_and_record_closes`` for
        the same ghost symbol so the existing close-fan-out (coordinator
        callbacks → sniper, thesis_manager, event_buffer, transformer,
        strategist) fires once.

        Why this exists: the per-tick close detector at ``_detect_and_record_closes``
        runs only at watchdog cadence (10 s nominal, observed up to 31 s under
        contention). A position that Shadow closes mid-WD-pause stays
        ghost-managed until the next tick — long enough for the sniper to
        evaluate it 3+ times and the event buffer to fire duplicate
        ``critical_loss`` events. The fast reconcile shortens that window
        to ``fast_reconcile_seconds`` independent of WD_TICK length.
        """
        cadence = float(getattr(self.settings.watchdog, "fast_reconcile_seconds", 30.0))
        if cadence <= 0.0:
            return  # disabled via config (kill switch)
        now = time.monotonic()
        if now - self._last_fast_reconcile_at < cadence:
            return
        self._last_fast_reconcile_at = now

        live_syms = {p.symbol for p in positions_now}
        # Track-set: every per-symbol dict the watchdog owns. If a symbol
        # is in any of these, the watchdog is still managing it. Shadow's
        # absence overrides our tracking.
        tracked: set[str] = (
            set(self._last_known_symbols)
            | set(self._td_states.keys())
            | set(self._position_open_times.keys())
            | set(self._position_peaks.keys())
        )
        ghosts = tracked - live_syms
        if not ghosts:
            return

        for sym in ghosts:
            opened = self._position_open_times.get(sym)
            age_s = 0.0
            if opened is not None:
                try:
                    age_s = (datetime.now(timezone.utc) - opened).total_seconds()
                except Exception:
                    age_s = 0.0
            log.warning(
                f"GHOST_RECONCILED | sym={sym} age={age_s:.0f}s "
                f"tracked_in=[wd,sniper,thesis,evbuf] | {ctx()}"
            )

        # Route through the existing close-record path. Pass live_syms as
        # `open_symbols` so the same set-diff inside _detect_and_record_closes
        # marks every ghost vanished and fires its callbacks. The method
        # already updates `_last_known_symbols` to `open_symbols.copy()` at
        # exit, which keeps the next per-tick diff coherent.
        await self._detect_and_record_closes(live_syms)

    async def _detect_and_record_closes(self, open_symbols: set[str]) -> None:
        """Detect externally-closed positions and fire callbacks.

        Compares symbols seen this tick vs last tick. Any symbol that
        disappeared (e.g., Shadow SL/TP triggered) gets recorded through
        coordinator.on_trade_closed() so all 8 callbacks fire properly.
        """
        # Detect disappeared symbols (were open last tick, gone now)
        vanished = self._last_known_symbols - open_symbols
        # Phase 12.8 (lifecycle-logging-audit Gap 8.1-G1): per-cycle
        # set-difference visibility — distinguishes "detection started" from
        # "close emission complete" (WD_CLOSE).
        if vanished:
            log.info(
                f"WD_POSITIONS_VANISHED | count={len(vanished)} "
                f"symbols=[{','.join(sorted(vanished))}] | {ctx()}"
            )
        for symbol in vanished:
            # Guard: skip if coordinator already processed this close
            # (prevents phantom double-close). Issue 3 (2026-05-18) —
            # uses the new symbol-level is_symbol_in_any_cooldown helper
            # after is_symbol_cooled_down was removed in issue3/p3-3.
            if (
                self.coordinator
                and hasattr(self.coordinator, "is_symbol_in_any_cooldown")
                and self.coordinator.is_symbol_in_any_cooldown(symbol)
            ):
                # Phase 12.8 (Gap 8.7-G1): explicit dedup tag (WD_SKIP_CLOSE
                # already exists; rename-friendly alias WD_CLOSE_DEDUP for
                # operators searching the audit-prompt terminology).
                log.info(
                    f"WD_SKIP_CLOSE | sym={symbol} rsn=already_processed_by_coordinator | {ctx()}"
                )
                continue
            # ── Step 1: Get exit price ──
            # Bug 2 fix: prefer Shadow's authoritative close data over a
            # live Bybit ticker. Shadow committed the true exit_price into
            # virtual_positions the moment it closed the position; the
            # watchdog only poll-detects the close N seconds later. A
            # ticker read at detection-time can be sign-flipped vs Shadow.
            exit_price = 0.0
            price_source = "unknown"
            shadow_close: dict | None = None

            if hasattr(self.position_service, "get_last_close"):
                # F5 part 2 (2026-06-09 phantom-close follow-up): pass IDENTITY
                # HINTS so get_last_close identity-matches THIS close instead of
                # the legacy single-shot rows[0] — which can be a STALE prior
                # same-symbol close row and book a phantom win (the SKR 14:22:58
                # case: a stale 0.011923/254490 row booked +15.06 before the
                # authoritative -1.75 arrived). The trade state is NOT yet popped
                # here, so the true qty (state.size) and entry are available;
                # passing qty engages the adapter's identity-match + 1% qty gate,
                # and entry_price engages the F5-b closest-entry disambiguation.
                # No match (real row not indexed yet) -> None -> ticker fallback +
                # the reconciler retries; never a stale wrong row.
                _hint_qty = 0.0
                _hint_entry = 0.0
                if self.coordinator:
                    _pre_state = self.coordinator._trades.get(symbol)
                    _pre_plan = self.coordinator.get_trade_plan(symbol)
                    if _pre_state is not None:
                        _hint_qty = float(getattr(_pre_state, "size", 0.0) or 0.0)
                    if _hint_qty <= 0 and _pre_plan is not None:
                        _hint_qty = float(getattr(_pre_plan, "size", 0.0) or 0.0)
                    if _pre_plan is not None and float(
                        getattr(_pre_plan, "entry_price", 0.0) or 0.0
                    ) > 0:
                        _hint_entry = float(_pre_plan.entry_price)
                    if _hint_qty <= 0:
                        # Coordinator present but the trade's true qty is
                        # unavailable (state already popped by a racing path, or
                        # plan.size=0) — get_last_close reverts to legacy mode and
                        # the Part-1 gate + Part-3 reconcile backstops carry the
                        # phantom protection. Surface it so a persistent miss is
                        # greppable rather than silent.
                        log.warning(
                            f"WD_CLOSE_HINT_MISS | sym={symbol} "
                            f"reason=true_qty_unavailable | identity-match disabled; "
                            f"staleness gate + reconcile backstops active | {ctx()}"
                        )
                try:
                    shadow_close = await self.position_service.get_last_close(
                        symbol,
                        qty=(_hint_qty if _hint_qty > 0 else None),
                        entry_price=(_hint_entry if _hint_entry > 0 else None),
                    )
                except Exception as e:
                    log.warning(
                        f"WD_SHADOW_CLOSE_LOOKUP_FAIL | sym={symbol} "
                        f"err='{str(e)[:100]}' | {ctx()}"
                    )
                    shadow_close = None

                if shadow_close:
                    age_s = _close_age_seconds(shadow_close.get("closed_at"))
                    if age_s is not None and age_s <= 120:
                        _shadow_px = shadow_close.get("exit_price")
                        try:
                            _shadow_px_f = float(_shadow_px) if _shadow_px is not None else 0.0
                        except (TypeError, ValueError):
                            _shadow_px_f = 0.0
                        if _shadow_px_f > 0:
                            exit_price = _shadow_px_f
                            price_source = "exchange_authoritative"

            # Fallback 1: current ticker
            if exit_price == 0.0:
                try:
                    ticker = await self.market_service.get_ticker(symbol)
                    if ticker and ticker.last_price > 0:
                        exit_price = ticker.last_price
                        price_source = "ticker_fallback"
                except Exception:
                    pass

            # Fallback 2: last tick's cached price
            if exit_price == 0.0:
                exit_price = self._last_prices.get(symbol, 0.0)
                price_source = "last_tick_cache"

            if price_source != "exchange_authoritative":
                _reason = (
                    "no_shadow_data"
                    if shadow_close is None
                    else ("stale_close" if shadow_close else "empty_close")
                )
                log.warning(
                    f"WD_CLOSE_PRICE_FALLBACK | sym={symbol} "
                    f"src={price_source} reason={_reason} | {ctx()}"
                )

            # ── Step 2: Resolve entry/direction/PnL ──
            pnl_pct = 0.0
            pnl_usd = 0.0
            entry_price = 0.0
            direction = ""
            recovered_size_usd = 0.0  # USD notional recovered from thesis
            recovered_leverage = 0  # leverage recovered from thesis
            recovered_qty = 0.0  # qty recovered from orders
            if self.coordinator:
                plan = self.coordinator.get_trade_plan(symbol)
                state = self.coordinator._trades.get(symbol)
                if plan and hasattr(plan, "entry_price") and plan.entry_price > 0:
                    entry_price = plan.entry_price
                    direction = getattr(plan, "direction", "")
                if not direction and state:
                    direction = getattr(state, "side", "")

            # Issue 3 fix (2026-05-11) — defensive recovery when
            # coordinator state is absent. Pre-fix the writer emitted
            # WD_CLOSE with ent=$0 / dir="" / pnl$=0 whenever the
            # coordinator state had been popped before WD_CLOSE fired
            # (typically because Issue 4's bug popped the state on
            # partial close, or a true race with the WS-driven close
            # path). Recovery order:
            #   1. trade_thesis WHERE status='open' — authoritative for
            #      entry_price/direction at open time.
            #   2. orders WHERE status='Filled' — latest fill is the
            #      next-best source of entry_price + side + qty.
            # When both fail we emit WD_CLOSE_RECOVERY_FAIL (ERROR) and
            # let the writer proceed with whatever it has — per the
            # directive's Forbidden list, skipping the write entirely
            # is not an option (loses the trade record).
            if (entry_price <= 0 or not direction) and self.db is not None:
                try:
                    row = await self.db.fetch_one(
                        "SELECT direction, entry_price, size_usd, leverage "
                        "FROM trade_thesis "
                        "WHERE status='open' AND symbol = ? "
                        "ORDER BY opened_at DESC LIMIT 1",
                        (symbol,),
                    )
                    if row:
                        r = dict(row)
                        _t_ent = float(r.get("entry_price") or 0)
                        _t_dir = str(r.get("direction") or "")
                        if _t_ent > 0 and entry_price <= 0:
                            entry_price = _t_ent
                        if _t_dir and not direction:
                            direction = _t_dir
                        recovered_size_usd = float(r.get("size_usd") or 0)
                        recovered_leverage = int(r.get("leverage") or 0)
                        log.info(
                            f"WD_CLOSE_THESIS_RECOVERY | sym={symbol} "
                            f"ent={entry_price} dir={direction} "
                            f"size_usd={recovered_size_usd} "
                            f"lev={recovered_leverage} | {ctx()}"
                        )
                except Exception as _exc:
                    log.warning(
                        f"WD_CLOSE_THESIS_RECOVERY_FAIL | sym={symbol} "
                        f"err='{str(_exc)[:100]}' | {ctx()}"
                    )

            if (entry_price <= 0 or not direction) and self.db is not None:
                try:
                    row = await self.db.fetch_one(
                        "SELECT side, qty, avg_fill_price "
                        "FROM orders "
                        "WHERE status='Filled' AND symbol = ? "
                        "ORDER BY created_at DESC LIMIT 1",
                        (symbol,),
                    )
                    if row:
                        r = dict(row)
                        _o_ent = float(r.get("avg_fill_price") or 0)
                        _o_dir = str(r.get("side") or "")
                        if _o_ent > 0 and entry_price <= 0:
                            entry_price = _o_ent
                        if _o_dir and not direction:
                            direction = _o_dir
                        recovered_qty = float(r.get("qty") or 0)
                        log.info(
                            f"WD_CLOSE_ORDERS_RECOVERY | sym={symbol} "
                            f"ent={entry_price} dir={direction} "
                            f"qty={recovered_qty} | {ctx()}"
                        )
                except Exception as _exc:
                    log.warning(
                        f"WD_CLOSE_ORDERS_RECOVERY_FAIL | sym={symbol} "
                        f"err='{str(_exc)[:100]}' | {ctx()}"
                    )

            if entry_price <= 0 or not direction:
                log.error(
                    f"WD_CLOSE_RECOVERY_FAIL | sym={symbol} ent={entry_price} "
                    f"dir='{direction}' | thesis+orders both unavailable; "
                    f"row will have defensive zero/empty fields | {ctx()}"
                )

            if self.coordinator:
                if entry_price > 0 and exit_price > 0:
                    if direction in ("Buy", "Long"):
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                    elif direction in ("Sell", "Short"):
                        pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                    # Calculate USD PnL
                    size = getattr(plan, "size", 0) if plan else 0
                    if size == 0 and state:
                        size = getattr(state, "size", 0)
                    notional = abs(entry_price * size) if size and size > 0 else 0
                    # Fallback: use trade_info amount_usd * leverage for notional
                    if notional == 0 and self.coordinator:
                        info = self.coordinator.get_trade_info(symbol)
                        amount_usd = info.get("amount_usd", 0)
                        leverage = info.get("leverage", 1)
                        if amount_usd > 0:
                            notional = amount_usd * leverage
                    # Issue 3 fix (2026-05-11) — when coordinator state
                    # was missing, use the values recovered from thesis /
                    # orders so pnl_usd is non-zero. Thesis carries
                    # size_usd + leverage so notional = size_usd * lev.
                    # Orders carries qty directly so notional = qty * entry.
                    # Both are populated only when the upstream lookups
                    # succeeded.
                    if notional == 0 and recovered_size_usd > 0:
                        _lev = recovered_leverage if recovered_leverage > 0 else 1
                        notional = recovered_size_usd * _lev
                    if notional == 0 and recovered_qty > 0 and entry_price > 0:
                        notional = abs(entry_price * recovered_qty)
                    pnl_usd = pnl_pct / 100 * notional if notional else 0
                else:
                    # Final fallback to last known PnL from previous tick
                    pnl_pct = self._last_pnls.get(symbol, 0.0)
                    if plan and hasattr(plan, "entry_price") and plan.entry_price > 0:
                        notional = abs(plan.entry_price * getattr(plan, "size", 0.01))
                        pnl_usd = pnl_pct / 100 * notional
            else:
                pnl_pct = self._last_pnls.get(symbol, 0.0)

            # Bug 2 fix: when Shadow returned usable close data, prefer its
            # fee-inclusive net_pnl_pct / net_pnl_usd over our locally
            # back-derived values. Shadow already accounts for entry/exit
            # fees, slippage, and funding — we don't.
            if price_source == "exchange_authoritative" and shadow_close:
                try:
                    _s_pct = shadow_close.get("net_pnl_pct")
                    _s_usd = shadow_close.get("net_pnl_usd")
                    if _s_pct is not None:
                        pnl_pct = float(_s_pct)
                    if _s_usd is not None:
                        pnl_usd = float(_s_usd)
                except (TypeError, ValueError):
                    pass

            was_win = pnl_usd > 0 if price_source == "exchange_authoritative" else pnl_pct > 0
            # Determine close reason: strategic_review, claude_brain, or
            # mode-aware "{mode}_sl_tp" via coordinator. P2 of P1-P10:
            # the coordinator now returns mode-aware default
            # (e.g., "bybit_demo_sl_tp"); the no-coordinator branch falls
            # back to mode-aware string derived from self.transformer.
            if self.coordinator:
                close_reason = self.coordinator.pop_close_reason(symbol)
            else:
                _mode = (
                    self.transformer.current_mode
                    if (hasattr(self, "transformer") and self.transformer)
                    else ""
                )
                close_reason = f"{_mode}_sl_tp" if _mode else "exchange_sl_tp"
            set_tid(f"t-{symbol}-ext")

            # Phase 12.7 (lifecycle-logging-audit Gap 7.1-G1, HIGH):
            # close_trigger inference. Compare close price to last-known
            # SL/TP from the trade plan to distinguish sl_hit / tp_hit /
            # trail_hit / exchange_match. The audit's #1 named structural
            # gap — pre-fix all exchange-initiated closes hardcoded to
            # close_trigger="exchange_match", losing trigger attribution
            # for downstream data_lake / TIAS / strategy-edge measurement.
            close_trigger = "exchange_match"  # default when unknown
            try:
                if self.coordinator:
                    plan = self.coordinator.get_trade_plan(symbol)
                    last_sl = float(getattr(plan, "stop_loss", 0) or 0) if plan else 0.0
                    last_tp = float(getattr(plan, "take_profit", 0) or 0) if plan else 0.0
                    # Tolerance: 0.2 % of close price (covers tick rounding
                    # and minor slippage between trigger and fill).
                    _tol = max(exit_price * 0.002, 1e-9) if exit_price > 0 else 0.0
                    if last_sl > 0 and abs(exit_price - last_sl) <= _tol:
                        close_trigger = "sl_hit"
                    elif last_tp > 0 and abs(exit_price - last_tp) <= _tol:
                        close_trigger = "tp_hit"
            except Exception:
                # Silent fallback to exchange_match — never block the
                # close-recording path on inference failure. Keep behaviour
                # backward-compatible.
                pass

            # Phase 4 (P0-7): use entry_price as the magnitude reference so
            # both ent= and ext= align to the symbol's tick scale (sub-cent
            # coins show 6-8 decimals instead of being rounded to 2dp).
            log.warning(
                f"WD_CLOSE | sym={symbol} pnl={pnl_pct:+.4f}% pnl$={pnl_usd:+.4f} "
                f"ent=${format_price(entry_price)} "
                f"ext=${format_price(exit_price, entry_price)} dir={direction} "
                f"price_src={price_source} rsn={close_reason} "
                f"close_trigger={close_trigger} "
                f"win={'Y' if was_win else 'N'} | {ctx()}"
            )
            # Finding 5 follow-up (2026-06-02): the actual cap-slippage breach
            # (a market-stop FILLING past its trigger) closes here via the
            # exchange SL, not on the sniper tick — so the authoritative
            # overshoot monitor lives at the real close. When a realized loss
            # exceeds the sacred dollar ceiling by more than 2%, log
            # CAP_SLIPPAGE_OBSERVED so a rising overshoot trend is caught.
            # Read-only/defensive — never affects the close path.
            try:
                _lc_cfg = getattr(self.settings, "loss_cutting", None)
                _ceiling = float(getattr(_lc_cfg, "cap_dollar_ceiling", 0.0) or 0.0)
                if (
                    _lc_cfg is not None and getattr(_lc_cfg, "enable_hard_cap", False)
                    and _ceiling > 0 and pnl_usd < 0
                    and abs(pnl_usd) > _ceiling * 1.02
                ):
                    _over = abs(pnl_usd) - _ceiling
                    log.warning(
                        f"CAP_SLIPPAGE_OBSERVED | sym={symbol} loss_usd={abs(pnl_usd):.4f} "
                        f"cap_ceiling={_ceiling:.2f} overshoot_usd={_over:.4f} "
                        f"overshoot_pct={(_over / _ceiling * 100.0):.2f}% "
                        f"close_trigger={close_trigger} src=watchdog_close | realized "
                        f"loss past the sacred ceiling — watch the trend | {ctx()}"
                    )
            except Exception:
                pass
            # 0.00% PnL diagnostic
            if pnl_pct == 0 and entry_price > 0:
                log.error(f"WD_PNL_MISMATCH | sym={symbol} pnl=0.00 ent={entry_price} ext={exit_price} — possible data integrity issue | {ctx()}")
            if exit_price == 0:
                log.error(f"WD_ZERO_EXIT | sym={symbol} exit_price=0 price_src={price_source} — exit price unknown | {ctx()}")

            # Issue I3 (F-28, 2026-05-14) — block corrupted commits when
            # the integrity check fires AND the price source is degraded.
            # Pre-I3 the WD_PNL_MISMATCH ERROR was purely advisory: the
            # code fell through to on_trade_closed which committed the
            # corrupted row to trade_log unchanged. TIAS / enforcer /
            # capital tier consumed pnl=0 entries and learned wrong.
            #
            # Block rule:
            #   * entry == exit (pnl==0 with entry_price>0) AND
            #     price_source NOT in the authoritative set
            #   → skip on_trade_closed entirely; track the symbol in
            #     _close_retry_pending so the next watchdog tick can
            #     re-attempt with fresh data
            #   → after _PNL_MISMATCH_RETRY_LIMIT consecutive blocks,
            #     force-commit with WD_PNL_MISMATCH_FORCED so the trade
            #     doesn't get stuck (defensive: respects aggressive
            #     opportunity exploitation — never permanently silence
            #     a trade)
            #
            # When entry == exit BUT price_source IS authoritative
            # (exchange_authoritative / bybit_ws_authoritative), the
            # zero pnl is genuine (rare but possible: slow market close
            # at the entry tick). Commit normally with the warning.
            _AUTHORITATIVE_SOURCES = frozenset({
                "exchange_authoritative",
                "bybit_ws_authoritative",
                "shadow_authoritative",
            })
            _is_corrupted = (
                pnl_pct == 0 and entry_price > 0
                and price_source not in _AUTHORITATIVE_SOURCES
            )
            _retries = self._pnl_mismatch_retries.get(symbol, 0)
            if _is_corrupted and _retries < _PNL_MISMATCH_RETRY_LIMIT:
                self._pnl_mismatch_retries[symbol] = _retries + 1
                log.warning(
                    f"WD_PNL_MISMATCH_BLOCKED | sym={symbol} "
                    f"pnl=0.00 ent={entry_price} ext={exit_price} "
                    f"price_src={price_source} retry={_retries + 1}/"
                    f"{_PNL_MISMATCH_RETRY_LIMIT} "
                    f"action=skip_commit_retry_next_tick | {ctx()}"
                )
                # Skip on_trade_closed for this tick. The position is
                # still gone on exchange, so the next watchdog tick
                # will hit this same path (entry to _detect_and_record_closes
                # via set-diff). On that retry, Bybit's closed-pnl API
                # may have indexed the close and price_source becomes
                # authoritative.
                continue

            # If we got here with corrupted data after exhausting retries,
            # force-commit with a distinguishable tag so post-mortem
            # analysis can identify the records.
            if _is_corrupted and _retries >= _PNL_MISMATCH_RETRY_LIMIT:
                log.error(
                    f"WD_PNL_MISMATCH_FORCED | sym={symbol} "
                    f"pnl=0.00 ent={entry_price} ext={exit_price} "
                    f"price_src={price_source} retries_exhausted={_retries} "
                    f"action=force_commit_corrupted | {ctx()}"
                )
                # Clear the retry counter (the trade lifecycle ends here)
                self._pnl_mismatch_retries.pop(symbol, None)
            elif symbol in self._pnl_mismatch_retries:
                # Healthy close after prior retries — clear the counter.
                self._pnl_mismatch_retries.pop(symbol, None)

            # Fire coordinator callbacks (thesis close, trade_log, daily_pnl, etc.)
            if self.coordinator:
                # F5 phantom-close fix (2026-06-08, leg A) — arm the EXISTING
                # single-writer staleness gate (trade_coordinator.on_trade_closed
                # :1346) on the watchdog poll path. The gate was INERT here
                # because this path never passed ref_*/candidate_qty (it supplied
                # only price_source + the booked value). We build an INDEPENDENT
                # trusted-local reference — the last cached mark this watchdog saw
                # (_last_prices, written every monitor tick at line ~2800) and the
                # trade's TRUE qty — so a stale/wrong-trade closed-pnl row (the
                # phantom exit from the adapter's qty-only match) is demoted to
                # the local net BEFORE booking instead of clobbering the correct
                # close. Reuses the proven gate (qty mismatch is the primary
                # signal; exit divergence the backstop); no new gate, no new
                # config. Armed only when price_source is exchange_authoritative
                # AND a usable mark + true qty exist; otherwise ref_* stay None
                # and the gate stays inert exactly as before (no behaviour
                # change). identity_confirmed is deliberately NOT set, so the
                # exit-divergence demotion (1375-1383) applies on this poll path.
                _f5_ref_pnl_usd: float | None = None
                _f5_ref_pnl_pct: float | None = None
                _f5_ref_exit: float | None = None
                _f5_ref_qty: float | None = None
                _f5_candidate_qty: float | None = None
                # F5 part 1 (2026-06-09 phantom-close follow-up): arm the staleness
                # reference on ANY path that returned a closed-pnl ROW (shadow_close),
                # not just exchange_authoritative. The phantom (SKR 14:22:58) booked a
                # stale row on the TICKER_FALLBACK path, where this block previously
                # stayed inert — "armed only when not needed". A qty mismatch between
                # the stale row's qty and the trade's TRUE qty is an authority-
                # INDEPENDENT staleness signal, so the coordinator gate can demote the
                # stale row before booking regardless of price_source.
                if shadow_close:
                    try:
                        _local_mark = float(self._last_prices.get(symbol, 0.0) or 0.0)
                        _true_qty = 0.0
                        if state is not None:
                            _true_qty = float(getattr(state, "size", 0.0) or 0.0)
                        if _true_qty <= 0 and plan is not None:
                            _true_qty = float(getattr(plan, "size", 0.0) or 0.0)
                        if _true_qty <= 0 and recovered_qty > 0:
                            _true_qty = float(recovered_qty)
                        if _local_mark > 0 and entry_price > 0 and _true_qty > 0:
                            _ref_pct: float | None = None
                            if direction in ("Buy", "Long"):
                                _ref_pct = ((_local_mark - entry_price) / entry_price) * 100
                            elif direction in ("Sell", "Short"):
                                _ref_pct = ((entry_price - _local_mark) / entry_price) * 100
                            if _ref_pct is not None:
                                _f5_ref_pnl_pct = _ref_pct
                                _f5_ref_pnl_usd = _ref_pct / 100.0 * abs(entry_price * _true_qty)
                                _f5_ref_exit = _local_mark
                                _f5_ref_qty = _true_qty
                                _cand_q = float(shadow_close.get("qty") or 0.0)
                                _f5_candidate_qty = _cand_q if _cand_q > 0 else None
                                log.info(
                                    f"WD_CLOSE_STALENESS_REF | sym={symbol} "
                                    f"cand_exit={exit_price} ref_mark={_local_mark} "
                                    f"cand_pnl_usd={pnl_usd:+.4f} "
                                    f"ref_pnl_usd={_f5_ref_pnl_usd:+.4f} "
                                    f"cand_qty={_f5_candidate_qty} ref_qty={_f5_ref_qty} "
                                    f"| arming poll-path staleness gate (F5) | {ctx()}"
                                )
                    except Exception as _f5_exc:
                        log.debug(
                            f"WD_CLOSE_STALENESS_REF_SKIP | sym={symbol} "
                            f"err='{str(_f5_exc)[:80]}' | {ctx()}"
                        )
                try:
                    self.coordinator.on_trade_closed(
                        symbol=symbol,
                        pnl_pct=pnl_pct,
                        pnl_usd=pnl_usd,
                        was_win=was_win,
                        closed_by=close_reason,
                        exit_price=exit_price,
                        price_source=price_source,
                        ref_pnl_usd=_f5_ref_pnl_usd,
                        ref_pnl_pct=_f5_ref_pnl_pct,
                        ref_exit_price=_f5_ref_exit,
                        ref_qty=_f5_ref_qty,
                        candidate_qty=_f5_candidate_qty,
                        # ref is the live cached MARK, not the exact fill — use
                        # the 3% exit-plausibility band, not the tight half-tick.
                        ref_is_mark=True,
                    )
                    self.coordinator.remove_trade_plan(symbol)
                except Exception as e:
                    log.error(
                        "Failed to record external close for {sym}: {err}",
                        sym=symbol, err=str(e),
                    )

            # Time-Decay state cleanup (log terminal state if active)
            if symbol in self._td_states:
                _td = self._td_states[symbol]
                _last = (
                    f"{_td.last_allowed_loss:.2f}%"
                    if _td.last_allowed_loss != float("inf")
                    else "n/a"
                )
                log.info(
                    f"TIME_DECAY_CLEANUP | sym={symbol} ticks={_td.tick_count} "
                    f"p_win={_td.p_win:.3f} mae={_td.mae_pct:+.2f}% "
                    f"last_allowed={_last} closed_by={close_reason} | {ctx()}"
                )
            # T1-2 (2026-05-12): confirmed external close — clear the
            # MAE high-water-mark snapshot too. Re-entries on the same
            # symbol after a real close start with a fresh MAE; only
            # intra-position-life round-trips (HANDOFF→re-INIT) preserve.
            self._td_mae_high_water.pop(symbol, None)

            # Send alert. P2 of P1-P10: replace literal "Closed by: Shadow SL/TP"
            # with mode-aware label so demo / live operators see the actual
            # exchange that triggered the close. Falls back to
            # close_reason (already mode-aware via coordinator) when the
            # transformer mode lookup fails.
            if self.alert_manager:
                try:
                    _mode = (
                        self.transformer.current_mode
                        if (hasattr(self, "transformer") and self.transformer)
                        else ""
                    )
                    _mode_label = {
                        "shadow": "Shadow",
                        "bybit_demo": "Bybit Demo",
                        "bybit": "Bybit Live",
                    }.get(_mode, close_reason)
                    await self.alert_manager.send_custom(
                        f"Position closed externally: {symbol}\n"
                        f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
                        f"Entry: ${format_price(entry_price)} → Exit: ${format_price(exit_price)}\n"
                        f"Closed by: {_mode_label} SL/TP",
                        priority=AlertLevel.INFO,
                    )
                except Exception:
                    pass

        # Phase 12.5 (lifecycle-logging-audit Gap 5.12-G1): emit
        # POSITION_CONFIRMED for new positions detected this tick (set
        # difference: open this tick but not last tick). Closes the
        # end-to-end placement-visibility gap — operators now see one
        # log line confirming "place_order succeeded AND position is
        # visible via get_positions" (the watchdog's set-difference is
        # the only authoritative source for the latter).
        new_positions = open_symbols - self._last_known_symbols
        for _new_sym in new_positions:
            log.info(
                f"POSITION_CONFIRMED | sym={_new_sym} "
                f"detected_via=watchdog_poll | {ctx()}"
            )

        # Update last known symbols for next tick
        self._last_known_symbols = open_symbols.copy()

        # Clean up tracking dicts for vanished symbols
        # Issue 1 (2026-05-18) — _brain_score_prev_pnl is included so
        # the velocity-fallback cache cannot grow unbounded across
        # long-running sessions with high symbol turnover.
        for tracking_dict in (
            self._position_peaks, self._last_prices, self._last_pnls,
            self._last_brain_call, self._hold_suppression,
            self._consecutive_holds, self._last_alert_time,
            self._position_open_times, self._position_strategies,
            self._brain_score_prev_pnl,
        ):
            stale = [s for s in tracking_dict if s not in open_symbols]
            for s in stale:
                del tracking_dict[s]

        # T1-2 (2026-05-12): handle _td_states separately so we can
        # snapshot the MAE high-water-mark before deletion. A transient
        # get_positions miss must NOT lose MAE — only a CONFIRMED close
        # (handled in _detect_and_record_closes near the
        # coordinator.on_trade_closed callsite via TIME_DECAY_CLEANUP)
        # clears the floor. The profit-handoff and force-close paths
        # above also snapshot before pop; this is defense-in-depth for
        # the rarer cleanup path.
        td_stale = [s for s in self._td_states if s not in open_symbols]
        for s in td_stale:
            _stale = self._td_states.pop(s)
            if _stale.mae_pct < 0:
                self._td_mae_high_water[s] = _stale.mae_pct
            log.warning(
                f"TIME_DECAY_STATE_DESTROY | sym={s} "
                f"mae={_stale.mae_pct:+.2f}% ticks={_stale.tick_count} "
                f"reason=stale_symbol_cleanup "
                f"preserve_mae={'true' if _stale.mae_pct < 0 else 'false'} "
                f"| {ctx()}"
            )

    async def cleanup(self) -> None:
        """Reset all tracking state on stop."""
        self._position_peaks.clear()
        self._last_prices.clear()
        self._last_pnls.clear()
        self._last_brain_call.clear()
        self._hold_suppression.clear()
        self._consecutive_holds.clear()
        self._last_alert_time.clear()
        self._position_open_times.clear()
        self._position_strategies.clear()
        self._td_states.clear()
        self._td_mae_high_water.clear()
        self._brain_score_prev_pnl.clear()
