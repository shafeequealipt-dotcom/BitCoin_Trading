"""Mode 4: ProfitSniper — institutional-grade profit protection.

Monitors open positions every 5 seconds using five mathematical models:
  1. Hurst Exponent (trend persistence / mean-reversion detection)
  2. Momentum Decay (multi-scale PnL deceleration detection)
  3. ATR Extension (volatility-normalized distance from entry)
  4. Volume Divergence (Wyckoff-derived OBV divergence)
  5. Risk/Reward Shift (forward expected value computation)

Models are combined with regime-aware dynamic weights (Phase 7).
Actions are determined by regime-aware thresholds + anti-greed backstop (Phase 9).
Trailing stops are ATR-based with profit decay and momentum adjustment (Phase 8).

Runs in parallel with Modes 1-3:
  Mode 1 (Passive/Claude): strategic decisions every 30 seconds
  Mode 2 (Safety Net): rule-based when Claude offline
  Mode 3 (Emergency): close all when system in danger
  Mode 4 (Profit Sniper): institutional profit protection every 5 seconds

Build phases: M1=foundation, M2=price buffer, M3=models, M4=scoring,
              M5=execution, M6=Claude (removed), M7=recording, M8=integration,
              M9=action engine, M10=integration+observability

Current phase: M10 (COMPLETE — full institutional-grade rebuild)
"""

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from src.config.settings import Settings
from src.core.log_context import ctx, set_tid, tid_scope
from src.core.logging import get_logger
from src.core.time_dial import TimeDial
from src.core.types import AlertLevel, Side
from src.core.utils import format_price
from src.database.connection import DatabaseManager
from src.core.types import TimeFrame
from src.workers.base_worker import BaseWorker
from src.workers.sniper_models import SniperModels, TrailResult, ActionResult, LadderResult
from src.workers.sniper_ring_buffer import BufferPoint, EnhancedRingBuffer, PositionProfitState

log = get_logger("worker")

# Phase 8: Regime multipliers for ATR trailing stop width
REGIME_TRAIL_FACTORS: dict[str, float] = {
    "trending": 1.3,   # Wider — let trends run
    "ranging": 0.7,    # Tighter — reversion likely
    "volatile": 1.0,   # Standard — volatility already in ATR
    "dead": 0.6,       # Tightest — no momentum, protect gains
    "balanced": 0.85,  # Conservative — uncertain regime
}

# Phase 9: Regime-aware action thresholds
THRESHOLD_SETS: dict[str, dict] = {
    "trending":  {"tighten": 50, "partial": 70, "full": 85},
    "ranging":   {"tighten": 35, "partial": 55, "full": 70},
    "volatile":  {"tighten": 40, "partial": 60, "full": 75},
    "dead":      {"tighten": 30, "partial": 50, "full": 65},
    "balanced":  {"tighten": 35, "partial": 55, "full": 70},
}
ACTION_PRIORITY: dict[str, int] = {
    "hold": 0, "tighten": 1, "partial_close": 2, "full_close": 3,
}


# Issue C fix Phase 3a (2026-05-08) — explicit label for each full-close
# trigger path. Pre-fix every full closure carried the fixed string
# ``"mode4_p9"`` regardless of which code path triggered it, which is
# what produced the audit's "32 mode4_p9 events" misclassification of
# tick-evaluation log substrings as closure events. Distinct labels
# mean future incidents are diagnosable from the COORD_CLOSE_END /
# M4_ACT_CLOSE / Mode4 CLOSED log lines without reading source.
#
# Path semantics (see ``_determine_action`` and ``_stall_escape_action``):
#   - "score" / "both"  → score reached the regime full threshold
#                         (``score >= thresholds["full"]``); the
#                         legacy ``mode4_p9`` referred to this path
#                         and is preserved as the legacy label.
#   - "anti_greed"      → the pullback backstop fired
#                         (``peak_pnl >= 0.10 % AND pullback >= 75 %``).
#   - "stall_escape"    → ``_stall_escape_action`` overrode the action,
#                         either via the partial-cap path or the
#                         mature-stall valve at line 2481+.
_FULL_CLOSE_LABEL_BY_SOURCE: dict[str, str] = {
    "score":        "mode4_score_full",
    "both":         "mode4_score_full",
    "anti_greed":   "mode4_anti_greed_full",
    "stall_escape": "mode4_stall_valve",
}


def _resolve_full_close_label(action: Any) -> str:
    """Map an ``ActionResult`` to its trigger-path-specific
    ``closed_by`` label.

    Args:
        action: The ``ActionResult`` produced by ``_determine_action``
            and possibly mutated by ``_stall_escape_action`` (which
            sets ``action.source = "stall_escape"`` when overriding).

    Returns:
        One of ``mode4_score_full`` / ``mode4_anti_greed_full`` /
        ``mode4_stall_valve``. Unknown sources fall back to the legacy
        ``"mode4_p9"`` label so any future code path that doesn't
        register a source still produces a non-None label.
    """
    src = getattr(action, "source", "") or ""
    return _FULL_CLOSE_LABEL_BY_SOURCE.get(src, "mode4_p9")


# ─────────────────────────────────────────────────────────────────────
# Profit Sniper Worker
# ─────────────────────────────────────────────────────────────────────


class ProfitSniper(BaseWorker):
    """Mode 4: ProfitSniper — institutional-grade profit protection.

    Monitors open positions every 5 seconds using five mathematical models:
      1. Hurst Exponent (trend persistence / mean-reversion detection)
      2. Momentum Decay (multi-scale PnL deceleration detection)
      3. ATR Extension (volatility-normalized distance from entry)
      4. Volume Divergence (Wyckoff-derived OBV divergence)
      5. Risk/Reward Shift (forward expected value computation)

    Models combined with regime-aware dynamic weights → composite score (0-100).
    Actions (HOLD/TIGHTEN/PARTIAL_CLOSE/FULL_CLOSE) determined by regime-aware
    thresholds + anti-greed peak pullback backstop.
    Trailing stops are ATR-based with profit decay + momentum adjustment.

    Args:
        settings: Application settings (includes mode4 config).
        db: Database manager for sniper_log persistence.
        position_service: For reading open positions (Transformer proxy).
        market_service: For reading current prices from ticker_cache.
        order_service: For closing positions (Transformer proxy).
        account_service: For reading wallet balance.
        claude_client: For consulting Claude (kept for watchdog/other uses).
        alert_manager: For sending Telegram alerts (M8).
        transformer: For checking exchange mode and switch state.
        trade_coordinator: For immunity and cooldown coordination.
        event_buffer: For notifying Claude of Mode 4 actions (M8).
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        position_service: Any = None,
        market_service: Any = None,
        order_service: Any = None,
        account_service: Any = None,
        claude_client: Any = None,
        alert_manager: Any = None,
        transformer: Any = None,
        trade_coordinator: Any = None,
        event_buffer: Any = None,
        ta_cache: Any = None,
        regime_detector: Any = None,
        volatility_profiler: Any = None,
        sl_gateway: Any = None,
        layer4_protection: Any = None,
        structure_cache: Any = None,
    ) -> None:
        super().__init__(
            name="profit_sniper",
            interval_seconds=float(settings.mode4.check_interval_seconds),
            settings=settings,
            db=db,
        )

        # Sniper partial-close disable boot sentinel (2026-05-26). Operator
        # greps this one line to confirm whether the sniper may partial-close
        # this session. Default disabled per operator decision; the winner
        # trail, full_close, deadline/time-decay, sentinel advisor, hard stop,
        # native stop, and SL gateway are all unaffected by this flag.
        _partial_enabled = bool(
            getattr(settings.mode4, "sniper_partial_close_enabled", False)
        )
        log.info(
            f"SNIPER_PARTIAL_CLOSE_SENTINEL | "
            f"sniper_partial_close_enabled={_partial_enabled} "
            f"partial_closing={'on' if _partial_enabled else 'DISABLED'} "
            f"winner_trail=on full_close=on time_decay=on sentinel=on | no_ctx"
        )

        # Service references
        self.position_service = position_service
        self.market_service = market_service
        self.order_service = order_service
        self.account_service = account_service
        self.claude_client = claude_client
        self.alert_manager = alert_manager
        self.transformer = transformer
        self.trade_coordinator = trade_coordinator
        self.event_buffer = event_buffer
        self.ta_cache = ta_cache
        self.regime_detector = regime_detector
        self.volatility_profiler = volatility_profiler
        self.sl_gateway = sl_gateway
        # Layer 4 Realignment Phase 4.2 (2026-05-06) — shared
        # Layer4ProtectionService. Phase 4.4 has the sniper consult
        # this service before firing stall-escape closes; in Phase
        # 4.2 the field is wired but not yet consumed (kept for the
        # next sub-commit). When None, sniper falls through to its
        # existing Phase 1A/1C guards only — fail-loud behaviour
        # added in Phase 4.4 catches missing wiring at runtime.
        self.layer4_protection = layer4_protection
        # Loss-Cutting Technique 3 (2026-05-31) — shared X-RAY StructureCache,
        # read in the spine's loss-side block to place the structure stop just
        # beyond the invalidation level. Optional (default None): when absent
        # (tests/legacy boot), the structure candidate is simply skipped and the
        # ATR/cap candidates still protect the position (fail-safe).
        self.structure_cache = structure_cache

        # Profit-Fetching Exit System (2026-05-29) — the time-decay master
        # dial. Drives the ATR/Chandelier trail width (Phase 2), the ladder
        # step spacing and lock offset (Phase 3), and the highest-stop-wins
        # spine (Phase 4) as smooth functions of trade age. Built once;
        # queried per position per tick. Every consumer is gated by
        # self._pf.enabled, so when the switch is off the legacy paths run
        # unchanged. All values live in config.toml [profit_fetching].
        self._pf = settings.profit_fetching
        self._time_dial = TimeDial(self._pf)
        if self._pf.enabled:
            log.info(
                f"PROFIT_FETCHING_CONFIG_LOADED | enabled={self._pf.enabled} "
                f"atr_young={self._pf.atr_multiple_young} "
                f"atr_old={self._pf.atr_multiple_old} "
                f"step_young={self._pf.ladder_step_pct_young} "
                f"step_old={self._pf.ladder_step_pct_old} "
                f"lock_young={self._pf.lock_offset_pct_young} "
                f"lock_old={self._pf.lock_offset_pct_old} "
                f"arm_pct={self._pf.min_profit_to_arm_ladder_pct} "
                f"micro_arm={getattr(self._pf, 'micro_floor_arm_pct', self._pf.min_profit_to_arm_ladder_pct)} "
                # F2 (2026-06-09) boot sentinel — fee-aware micro-floor arm
                # (default OFF). Confirms the new key loaded.
                f"micro_arm_fee_aware={getattr(self._pf, 'micro_floor_arm_fee_aware_enabled', False)} "
                f"be_lock={self._pf.ladder_breakeven_lock_pct} "
                f"fee_clear={getattr(self._pf, 'ladder_lock_fee_clearance_pct', 0.0)} "
                f"floor_jump={self._pf.ladder_floor_jump_on_arm} "
                # F6 (2026-06-09) boot sentinel — first step-rung lock jump
                # (default OFF). Confirms the new key loaded.
                f"first_lock_jump={getattr(self._pf, 'ladder_first_lock_jump_enabled', False)} "
                f"safety_pct={self._pf.safety_stop_pct} "
                f"atr_zero_fallback_pct={self._pf.atr_zero_fallback_pct} "
                # PF/LC Top-15 boot sentinels (Rule 14) — confirm the new
                # profit-side config is loaded: the aligned trail arm (2.4), the
                # deadline dial freeze (2.5, default off), the real-M5-ATR trail
                # feed (3.2/3.5, default off).
                f"trail_arm={self._pf.min_profit_for_trail_pct} "
                f"dial_freeze={self._pf.dial_freeze_on_original_deadline_enabled} "
                f"trail_live_m5_atr={self._pf.trail_live_m5_atr_enabled}"
            )

        # Loss-Cutting System (2026-05-31) — the protective half of the engine
        # (companion to the profit-fetching dial above). It shares this master
        # clock: a SECOND TimeDial, built from settings.loss_cutting, resolves
        # the loss-side anchors via resolve_loss(). The volatility (ATR) dial is
        # applied the same way the profit side does — the dialed multiple is
        # multiplied by the effective ATR in the spine. Every loss consumer is
        # gated by self._lc.enabled; when off, the legacy watchdog backstops run
        # unchanged. All values live in config.toml [loss_cutting].
        self._lc = settings.loss_cutting
        self._loss_dial = TimeDial(self._lc)
        if self._lc.enabled:
            log.info(
                f"LOSS_CUTTING_CONFIG_LOADED | enabled={self._lc.enabled} "
                f"cap_ceiling={self._lc.cap_dollar_ceiling} "
                f"cap_pct_young={self._lc.cap_pct_of_notional_young} "
                f"cap_pct_old={self._lc.cap_pct_of_notional_old} "
                f"cap_net_fee={getattr(self._lc, 'cap_round_trip_fee_pct', 0.0)} "
                f"atr_init_young={self._lc.atr_initial_multiple_young} "
                f"atr_init_old={self._lc.atr_initial_multiple_old} "
                f"stall={self._lc.enable_stall_exit} "
                f"structure={self._lc.enable_structure_stop} "
                f"winprob={self._lc.enable_winprob_observe} "
                f"spike={self._lc.enable_spike_stop} "
                f"recovery={self._lc.enable_history_recovery} "
                f"vol_sizing={self._lc.volatility_entry_sizing_enabled} "
                # PF/LC Top-15 boot sentinels (Rule 14) — confirm the new
                # loss-side config is loaded: the spike young-window (3.4), the
                # windowed stall ratio (2.1, default off) and the sustained
                # improving reprieve (2.2, default off).
                f"spike_young_s={self._lc.spike_young_opening_seconds} "
                f"spike_mult_open={self._lc.spike_atr_move_mult_opening} "
                f"stall_windowed={self._lc.stall_veto_windowed_profit_ratio_enabled} "
                f"stall_sustained_improving={self._lc.stall_signs_of_life_sustained_improving_enabled} "
                # Issue 2.5 (2026-06-07) boot sentinel — confirm the graduation
                # crater re-arm config loaded (default OFF).
                f"grad_crater_rearm={self._lc.graduation_crater_rearm_enabled} "
                f"grad_crater_loss_pct={self._lc.graduation_crater_loss_pct}"
            )

        # Enhanced data pipeline: per-position profit state + ATR cache
        self._profit_states: dict[str, PositionProfitState] = {}
        self._atr_cache: dict[str, tuple[float, float]] = {}  # symbol → (atr_value, fetch_time)
        # Finding 4 (2026-06-02): per-symbol last-known-good live ATR. A
        # freshly-active symbol has < 50 M5 candles in the DB, so a fresh M5
        # compute raises DataError (engine MIN_CANDLES=50) and the live ATR
        # reads zero for the first few minutes after open. This retains the
        # most recent real ATR (seeded with the entry ATR at open) so
        # _get_current_atr returns real volatility through that cold-cache
        # bridge instead of zero — warming the cache so the _pf_effective_atr
        # fallback is rarely needed (it remains as insurance). Cleared on close.
        self._atr_last_good: dict[str, float] = {}  # symbol → last non-zero ATR
        # Phase 12.6 (lifecycle-logging-audit Gap 6.6-G1): per-symbol
        # last-emitted M4_TRAIL_FLOOR value to suppress per-tick noise.
        # Pre-fix the tag fired 42k+ times per rotation. Now emit only
        # when the floor changes >5% from the prior emission for this
        # symbol, OR after 60 s since the last emission (whichever first).
        # Cleared when the symbol's profit state is popped on close.
        self._last_trail_floor_logged: dict[str, tuple[float, float]] = {}  # sym -> (floor, mono_ts)
        # T2-10 (2026-05-12) — per-symbol consecutive WRONG_SIDE_GUARD
        # trip counter. Pre-fix, the trail watermark _trail_hwm could
        # become stuck after a sharp market reversal: new SL would land
        # on the wrong side of price, WRONG_SIDE_GUARD blocked the push,
        # the watermark never updated, and the next tick was capped by
        # the stale HWM, blocking re-entry closer to the new market.
        # Production evidence (5 h window 2026-05-12): 4+ retries on
        # AAVE with new_sl=97.76497500 while price drifted 97.86 → 97.99.
        # Now: after N consecutive wrong-side trips, the watermark is
        # force-refreshed (dropped entirely so the next tick re-
        # establishes from current peak). Counter resets on any
        # successful trail push or on any non-wrong-side tick.
        # Default 3 is short enough to recover within ~15 s (3 ticks ×
        # 5 s/tick) but long enough to absorb a single transient
        # geometry mismatch from a fast market move.
        self._trail_wrong_side_streak: dict[str, int] = {}
        # Threshold for force-refresh. Reads settings only at access
        # time so a future config knob can be wired without rebuild.
        self._trail_hwm_refresh_after_wrong_side_count: int = 3
        # T1-3 (2026-05-12): separate per-symbol throttle dict for the new
        # SNIPER_TRAIL_FLOOR_CLAMP event. Distinct from
        # _last_trail_floor_logged above (which throttles M4_TRAIL_FLOOR
        # for the from-PEAK floor) so the two floor concepts don't
        # suppress each other's events. Cleared in _on_position_closed.
        # Value tuple: (floor_pct, mono_ts).
        self._last_trail_from_price_floor_logged: dict[
            str, tuple[float, float]
        ] = {}
        # Finding 6 (2026-06-02): per-symbol 60s throttle for the
        # LADDER_ZERO_CROSSING_FLOOR sentinel so the breakeven-floor lock is
        # observable without per-tick log spam. Cleaned up on close.
        self._last_breakeven_floor_logged: dict[str, float] = {}
        # Dynamic Adaptive Exit (2026-06-15) — per-symbol smoothed R (the
        # movement unit, ATR-as-percent). EMA at the fetch boundary; popped on
        # close. Throttle dict for the adaptive-ladder observability log.
        self._smoothed_r: dict[str, float] = {}
        self._last_ladder_adaptive_logged: dict[str, float] = {}
        # Phase 3: TIAS snapshot — saved just before _profit_states is popped so TIAS can read it
        self._closed_snapshots: dict[str, dict] = {}  # symbol → last profit state snapshot

        # Phase 7: Regime-aware composite scoring.
        # Per-coin-authority Phase 7 (2026-05-29): PER-SYMBOL regime cache
        # {symbol: (RegimeState|None, ts)}. Was a single global slot
        # (_cached_regime/_regime_cache_time) that leaked one position's regime
        # onto every other position within the 30s window and applied BTC's
        # global regime to every coin's exit-weight selection.
        self._regime_cache: dict[str, tuple] = {}
        self._log_counter: dict[str, int] = {}  # symbol → tick count for log throttling

        # M1: Tick counter
        self._tick_count: int = 0

        # Observability G2 — per-heartbeat-window counters for SL push
        # outcomes. Incremented in the two sl_gateway.apply call sites
        # (trail + SL prop) before/after the await, reset on each
        # SNIPER_TICK emission (~60 s sample cadence). Pure logging
        # state per Rule 3; no behavioural effect.
        self._sl_updates_attempted_window: int = 0
        self._sl_updates_accepted_window: int = 0

        # M2: Position tracking and price buffers
        self._tracked: dict[str, dict] = {}
        # Format: {"ETHUSDT": {"buffer": EnhancedRingBuffer, "first_seen_at": float, "position": Position}}
        self._recently_closed: dict[str, dict] = {}
        # Format: {"DOGEUSDT": {"buffer": EnhancedRingBuffer, "closed_at": float, "last_known_position": Position}}
        self._buffer_size: int = settings.mode4.buffer_max_size
        self._stale_skip_count: dict[str, int] = {}  # tracks consecutive stale ticks per symbol

        # M5: Cooldown tracking
        self._cooldowns: dict[str, float] = {}  # symbol → expiry timestamp

        # M6: Claude rate limiting
        self._claude_queries_this_hour: int = 0
        self._claude_hour_start: float = time.time()
        self._claude_last_query_time: dict[str, float] = {}
        self._claude_consecutive_timeouts: int = 0
        self._claude_disabled_until: float = 0.0

        # M7: Counterfactual tracking
        self._counterfactuals: dict[str, dict] = {}

        # Phase 9: Action cooldown tracking (separate from legacy _cooldowns)
        self._last_action_time: dict[str, float] = {}
        self._last_action_type: dict[str, str] = {}

        # Phase 10: DB write frequency counter (write every N ticks OR on action)
        self._log_write_counter: dict[str, int] = {}

        # M3: Mathematical models
        weights = {
            "zscore": settings.mode4.weight_zscore,
            "velocity": settings.mode4.weight_velocity,
            "volume": settings.mode4.weight_volume,
            "bollinger": settings.mode4.weight_bollinger,
            "momentum": settings.mode4.weight_momentum,
        }
        self._models = SniperModels(weights=weights)
        self._model_log_count: dict[str, int] = {}

        log.info(
            "ProfitSniper initialized (M10: COMPLETE, buffer_size={bs})",
            bs=self._buffer_size,
        )

    # ─── tick() — main cycle ───────────────────────────────────────

    def _maybe_emit_tick_heartbeat(self, _tick_start: float) -> None:
        """Observability G2 — emit a sampled SNIPER_TICK heartbeat.

        The sniper has many state events (SNIPER_AGE_GUARD,
        SNIPER_STRUCT_GUARD_DEFER, SNIPER_SPIKE etc.) but none are
        guaranteed per-tick. A hung sniper would produce identical
        silence to a healthy idle one. This heartbeat fires every 12
        ticks (~60 s at the default 5 s cadence) so operators can
        verify liveness and measure tick-latency distribution. Sampling
        keeps volume at ~60 events/hour. Worker-level emission point
        (after per-symbol ``set_tid("")``) so ctx() never carries a
        stale tid from the per-symbol loop.

        Args:
            _tick_start: ``time.time()`` recorded at tick entry, used
                to compute the tick latency this heartbeat reports.
        """
        if self._tick_count % 12 != 0:
            return
        _tick_el = (time.time() - _tick_start) * 1000
        _syms = list(self._tracked.keys())
        _syms_str = ",".join(_syms[:5])
        _more = f"+{len(_syms) - 5}" if len(_syms) > 5 else ""
        _mode = getattr(self.transformer, "current_mode", "?") if self.transformer else "?"
        # Observability G2 — snapshot + reset SL push counters so the
        # next window starts at zero. Read-and-reset is single-threaded
        # (sniper tick runs serially), so no lock needed.
        _sl_attempted = self._sl_updates_attempted_window
        _sl_accepted = self._sl_updates_accepted_window
        self._sl_updates_attempted_window = 0
        self._sl_updates_accepted_window = 0
        log.info(
            f"SNIPER_TICK | tick={self._tick_count} el={_tick_el:.0f}ms "
            f"n={len(_syms)} syms=[{_syms_str}{_more}] mode={_mode} "
            f"sl_updates_attempted={_sl_attempted} "
            f"sl_updates_accepted={_sl_accepted} | {ctx()}"
        )

    async def tick(self) -> None:
        """One cycle of Mode 4 monitoring.

        M2: Reads positions, updates price buffers, detects opens/closes.
        M3+: Runs models, detects spikes, executes.

        Observability G2 (SNIPER_TICK heartbeat): every exit path
        calls ``_maybe_emit_tick_heartbeat`` so the 1/minute sampled
        liveness event fires regardless of which exit path the tick
        takes (transformer-switching skip, get_positions failure, or
        normal completion).
        """
        self._tick_count += 1
        _tick_start = time.time()

        # Skip during exchange switch
        if self.transformer and hasattr(self.transformer, "is_switching") and self.transformer.is_switching:
            self._maybe_emit_tick_heartbeat(_tick_start)
            return

        # Step 1: Get current open positions
        positions = await self._get_positions()
        if positions is None:
            self._maybe_emit_tick_heartbeat(_tick_start)
            return  # error getting positions, skip tick

        current_symbols = {pos.symbol for pos in positions}
        tracked_symbols = set(self._tracked.keys())

        # Step 2: Detect NEW positions
        for symbol in current_symbols - tracked_symbols:
            pos = next(p for p in positions if p.symbol == symbol)
            await self._on_position_opened(symbol, pos)

        # Step 3: Detect CLOSED positions
        for symbol in tracked_symbols - current_symbols:
            self._on_position_closed(symbol)

        # Step 4: Update existing positions with latest price
        for pos in positions:
            if pos.symbol in self._tracked:
                await self._update_position(pos)

        # Step 5: Continue feeding recently-closed buffers (for counterfactual in M7)
        for symbol, data in list(self._recently_closed.items()):
            price_data = await self._get_price_data(symbol)
            if price_data is not None:
                data["buffer"].append(price_data)

        # Step 6: Clean up expired counterfactual entries
        self._cleanup_counterfactuals()

        # Step 7: Periodic summary log (every 60 ticks = 5 minutes)
        if self._tick_count % 60 == 0 and self._tracked:
            symbols = ", ".join(
                f"{s}({len(d['buffer'])}pts)"
                for s, d in self._tracked.items()
            )
            log.info(
                "ProfitSniper: tracking {n} positions [{syms}]",
                n=len(self._tracked),
                syms=symbols,
            )

        # M3: Run mathematical models on each tracked position.
        # Phase 24 (Y-23): set per-symbol tid at the TOP of every
        # iteration so log lines emitted for symbol X never inherit the
        # tid of the previous symbol Y. The brief observed
        # ``sym=BASEDUSDT`` lines tagged ``tid=t-RAREUSDT-sniper``
        # because the sniper set tid mid-iteration but never cleared
        # it between symbols. The post-loop clear at the bottom of
        # tick() ensures the wid resumes for the M5/M7 phases.
        # Issue 3 of cascade-fix series (2026-05-10): snapshot the
        # items list before iteration. The loop body contains multiple
        # awaits (regime/profiler/sniper-log writes), each of which
        # yields the event loop to other tasks. While yielded, threads
        # bridged via ``asyncio.run_coroutine_threadsafe`` (pybit WS
        # callbacks) or other workers can mutate ``_tracked`` and
        # raise ``RuntimeError: dictionary changed size during
        # iteration``. Empirically observed in workers.log on
        # 2026-05-10 17:25:39.767 (XRPUSDT) and 2026-05-09 15:38:43.785
        # (MONUSDT). Mirrors the already-applied list() guards at
        # lines ~649 / ~689 in the same file. The list copy is cheap
        # (≤ 8 keys typical) compared to the model work that follows.
        for symbol, tracked in list(self._tracked.items()):
            set_tid(f"t-{symbol}-sniper")
            buf = tracked["buffer"]
            buf_len = len(buf)
            if buf_len < 12:
                tick_count = tracked.get("_skip_count", 0) + 1
                tracked["_skip_count"] = tick_count
                if tick_count % 12 == 1:  # Every ~60s (12 × 5s ticks)
                    log.debug(
                        "M4_SKIP | sym={sym} rsn=buffer_filling size={sz} need=12",
                        sym=symbol,
                        sz=buf_len,
                    )
                continue
            eval_tier = 2 if buf_len >= 100 else 1

            prices = buf.get_prices()
            timestamps = buf.get_timestamps()
            latest = buf.get_latest()
            if not latest:
                continue

            # All 5 models (Model 1: Hurst Exponent replaces Z-Score in Phase 2)
            import numpy as _np
            _prices_np = buf.get_prices_np() if hasattr(buf, "get_prices_np") else _np.array(prices)
            hurst_result = self._models.compute_hurst(_prices_np)
            z_raw = hurst_result.hurst_value  # downstream compat (logged as H=, stored in z_score col)
            z_pts = int(hurst_result.score * self._models._w.get("zscore", 25) / 100)

            # Model 2: Momentum Decay Detector (replaces Velocity in Phase 3)
            _pnl_np = buf.get_pnl_series()
            momentum_result = self._models.compute_momentum_decay(_pnl_np)
            vel = momentum_result.slope_short       # Anti-greed compat: PnL velocity
            accel = momentum_result.accel_short     # Anti-greed compat: PnL acceleration
            vel_pts = int(momentum_result.score * self._models._w.get("velocity", 25) / 100)
            exhaust = momentum_result.consecutive_decelerations

            # Model 4: Volume Divergence (replaces Volume-Price in Phase 5)
            _state_v = self._profit_states.get(symbol)
            volume_result = self._models.compute_volume_divergence(
                prices=_np.array(prices),
                volumes=buf.get_volumes(),
                buy_volumes=buf.get_buy_volumes(),
                sell_volumes=buf.get_sell_volumes(),
                direction=_state_v.direction if _state_v else "Buy",
            )
            vol_ratio = volume_result.price_obv_correlation  # Downstream compat
            vol_pts = int(volume_result.score * self._models._w.get("volume", 20) / 100)

            # Model 3: ATR Extension (replaces Bollinger Bands in Phase 4)
            _state = self._profit_states.get(symbol)
            _latest_bp = buf.get_latest()
            _atr_now = _latest_bp.get("atr_current", 0) if isinstance(_latest_bp, dict) else getattr(_latest_bp, "atr_current", 0) if _latest_bp else 0
            extension_result = self._models.compute_atr_extension(
                entry_price=_state.entry_price if _state else pos.entry_price,
                current_price=_latest_bp["price"] if isinstance(_latest_bp, dict) and _latest_bp else pos.mark_price,
                direction=_state.direction if _state else ("Buy" if pos.side.value == "Buy" else "Sell"),
                atr_current=_atr_now,
                atr_at_entry=_state.atr_at_entry if _state else 0,
                peak_pnl_pct=_state.peak_pnl_pct if _state else 0,
                prices=_np.array(prices) if len(prices) >= 60 else None,
            )
            bb_pos = extension_result.extension_atr  # Downstream compat for last_score["bb_position"]
            bb_pts = int(extension_result.score * self._models._w.get("bollinger", 15) / 100)

            # Model 5: Risk/Reward Shift (replaces Momentum Exhaustion in Phase 6)
            # Cross-model dependency: uses hurst_result.hurst_value from Phase 2
            _state_rr = self._profit_states.get(symbol)
            _current_pnl = self._calculate_pnl_pct(tracked["position"], latest["price"]) if latest else 0.0
            risk_reward_result = self._models.compute_risk_reward(
                prices=_np.array(prices),
                current_pnl_pct=_current_pnl,
                peak_pnl_pct=_state_rr.peak_pnl_pct if _state_rr else 0.0,
                hurst_value=hurst_result.hurst_value,
            )
            sf = risk_reward_result.ev_ratio  # Downstream compat for last_score["speed_factor"]
            mom_pts = int(risk_reward_result.score * self._models._w.get("momentum", 15) / 100)

            # ─── M4: Exploit Score Engine ───────────────────────
            pos = tracked["position"]

            # 4.1: Regime-aware composite scoring (Phase 7) — per-coin-authority
            # Phase 7 (2026-05-29): use THIS position's own per-coin regime.
            _regime = await self._get_regime(getattr(pos, "symbol", "") or "")
            composite_result = self._compute_composite_score(
                hurst_result, momentum_result, extension_result,
                volume_result, risk_reward_result, _regime,
            )
            raw_score = int(composite_result.score)

            # 4.1b: Phase 8 — ATR-based dynamic trailing stop computation
            _current_sl = self._get_current_sl(pos)
            _trail_state = self._profit_states.get(symbol)
            ladder_result = None  # Profit-Fetching Phase 3/4 — ladder candidate
            if _trail_state and self._pf.enabled:
                # ── Profit-Fetching Exit System (Phase 2): techniques 2 + 4 ──
                # The time-decay master dial sets the trail width (ATR multiple
                # 3.0 young -> 1.0 old). ATR itself is the per-coin volatility
                # sizing, so no extra hardcoded volatility-class modifier is
                # applied here (it would double-count volatility on top of ATR
                # and adds hardcoded bias the project aim forbids). The trail
                # can never vanish: effective ATR falls back live -> entry-ATR
                # -> percent-of-price floor when the TA cache returns zero.
                _age_min, _deadline_min = self._pf_age_and_deadline(symbol)
                _dialed = self._time_dial.resolve(_age_min, _deadline_min)
                _cur_price = (
                    _latest_bp["price"]
                    if (isinstance(_latest_bp, dict) and _latest_bp)
                    else float(getattr(pos, "mark_price", 0.0) or 0.0)
                )
                # PF/LC Top-15 Problems 3.2 + 3.5 — feed the trail the real M5
                # Wilder ATR (warm-seeded _get_current_atr, the same source the
                # loss path uses) instead of the cold ring-buffer atr_current /
                # price-range/4 proxy, so the leash is sized by precise live
                # volatility and the per-tick fallback (and its log flood) stops.
                # The existing _pf_effective_atr fallback chain (live -> entry-ATR
                # -> floor) is preserved for the cold-start window. Off → the
                # prior source and INFO log (unchanged).
                if self._pf.trail_live_m5_atr_enabled:
                    _live_atr_in = await self._get_current_atr(symbol)
                else:
                    _live_atr_in = extension_result.atr_current
                _eff_atr, _atr_src = self._pf_effective_atr(
                    _live_atr_in,
                    _trail_state.atr_at_entry,
                    _cur_price,
                )
                if _atr_src != "live":
                    _atr_fb_msg = (
                        f"SNIPER_ATR_FALLBACK | sym={symbol} reason={_atr_src} "
                        f"atr_used={_eff_atr:.8f} "
                        f"live_atr={_live_atr_in:.8f} "
                        f"entry_atr={_trail_state.atr_at_entry:.8f} "
                        f"age_min={_age_min:.1f} | {ctx()}"
                    )
                    if self._pf.trail_live_m5_atr_enabled:
                        # 3.5 — warm cache makes this rare; demote the flood.
                        log.debug(_atr_fb_msg)
                    else:
                        log.info(_atr_fb_msg)
                trail_result = self._compute_trail_stop(
                    _trail_state,
                    extension_result,
                    momentum_result,
                    composite_result.regime_used,
                    _current_sl,
                    base_atr_mult=_dialed.atr_multiple,
                    atr_value=_eff_atr,
                )
                # Phase 3 ladder candidate — reconciled with the trail by the
                # Phase 4 spine (highest-stop-wins) in the M5 execution loop.
                # Dynamic Adaptive Exit: feed the ladder R only from a REAL
                # measured ATR (live or entry-ATR) so the lock derives from true
                # volatility. When only the percent-of-price fallback is available
                # (cold cache), pass None so the ladder declines to the legacy
                # path — mirroring the watchdog/dead-drifter real-ATR-or-legacy
                # rule, never running R-geometry off a fabricated proxy.
                ladder_result = self._compute_ladder_floor(
                    _trail_state, _dialed, _current_sl,
                    atr_value=(_eff_atr if _atr_src in ("live", "entry_atr") else None),
                )
            elif _trail_state and extension_result.atr_current > 0:
                # ── Legacy path (profit-fetching disabled) — UNCHANGED ──
                # Adapt trail multiplier to coin volatility class
                _base_mult = self.settings.mode4.base_atr_multiplier
                if self.volatility_profiler:
                    try:
                        _vp = await self.volatility_profiler.get_profile(symbol)
                        if _vp:
                            if _vp.volatility_class in ("dead", "low"):
                                _base_mult *= 0.6  # Tighter trail for low-vol
                            elif _vp.volatility_class == "extreme":
                                _base_mult *= 1.4  # Wider trail for extreme vol
                    except Exception:
                        pass
                trail_result = self._compute_trail_stop(
                    _trail_state,
                    extension_result,
                    momentum_result,
                    composite_result.regime_used,
                    _current_sl,
                    base_atr_mult=_base_mult,
                )
            else:
                trail_result = None

            # 4.2: Direction and PnL
            current_price = latest["price"]
            direction = self._determine_direction(pos, current_price)
            pnl_pct = self._calculate_pnl_pct(pos, current_price)

            # 4.3: Anti-greed adjustment
            is_long = pos.side == Side.BUY
            adjusted_score, spike_status = self._apply_anti_greed(
                raw_score, direction, vel, accel, is_long
            )

            # 4.4: Classify
            position_age = time.time() - tracked["first_seen_at"]
            classification, is_actionable = self._classify_score(
                adjusted_score, direction, pnl_pct, position_age
            )

            # 4.4b: Phase 9 — Determine action (pure computation, no execution yet)
            action_result = None
            if _trail_state:
                action_result = self._determine_action(
                    composite_result, _trail_state, pnl_pct, trail_result
                )
                # Log when profit gate or min-profit gate blocks action
                if action_result.source in ("profit_gate", "below_min_profit"):
                    gate_count = tracked.get("_gate_log_count", 0)
                    tracked["_gate_log_count"] = gate_count + 1
                    if gate_count % 12 == 0:  # Every ~60s
                        log.debug(
                            "M4_EVAL | sym={sym} score={sc:.0f} act=hold "
                            "src={src} pnl={pnl:+.2f}%",
                            sym=symbol,
                            sc=action_result.score_value,
                            src=action_result.source,
                            pnl=pnl_pct,
                        )

            # 4.5: Log based on classification
            _trail_info = (
                f" trl_sl={trail_result.trail_stop_price:.8f}"
                f" trl_dist={trail_result.trail_distance_pct:.2f}%"
                f"(rf{trail_result.regime_factor:.2f}"
                f",pd{trail_result.profit_decay:.2f}"
                f",mf{trail_result.momentum_factor:.1f})"
                if trail_result else " trl=N/A"
            )
            _act_tag = action_result.action if action_result else "N/A"
            _greed_tag = action_result.greed_rule_triggered if action_result else "none"
            _pb_tag = f"{action_result.pullback_pct:.0f}%" if action_result else "0%"
            if classification in ("NORMAL", "WATCH"):
                log.debug(
                    "{sym}: {cls} score={sc} dir={d} pnl={pnl:+.2f}% "
                    "H={h:.3f} MD={md:.0f}(d{mdd}) Ext={ext:.1f}ATR(s{exts:.0f}) "
                    "status={st} act={act}",
                    sym=symbol, cls=classification, sc=adjusted_score,
                    d=direction, pnl=pnl_pct, h=z_raw,
                    md=momentum_result.score, mdd=momentum_result.consecutive_decelerations,
                    ext=extension_result.extension_atr, exts=extension_result.score,
                    st=spike_status, act=_act_tag,
                )
            else:
                set_tid(f"t-{symbol}-sniper")
                log.info(f"SNIPER_SPIKE | sym={symbol} dir={direction} score={adjusted_score}/100 pnl={pnl_pct:+.2f}% cls={classification} act={_act_tag} greed={_greed_tag} pb={_pb_tag} tier={eval_tier} buf={buf_len} | {ctx()}")
                log.debug(f"SNIPER_MODELS | sym={symbol} H={z_raw:.3f}({hurst_result.regime[:4]}) MD={momentum_result.score:.0f}(d{momentum_result.consecutive_decelerations}{'R' if momentum_result.momentum_reversed else ''}) Ext={extension_result.extension_atr:.1f}ATR(s{extension_result.score:.0f}) Vol={volume_result.score:.0f}({volume_result.divergence_type[:4]}) EV={risk_reward_result.ev_ratio:+.2f}(s{risk_reward_result.score:.0f},a{risk_reward_result.profit_amplifier:.1f}x){_trail_info} | {ctx()}")
                log.info(
                    "{sym}: SPIKE score={sc} class={cls} dir={d} "
                    "pnl={pnl:+.2f}% status={st} actionable={act} | "
                    "H={h:.3f} MD={md:.0f}(d{mdd}{mdr}) sl={sls:.4f} Vol={vs:.0f}({vt}) "
                    "Ext={ext:.1f}ATR(s{exts:.0f}) EV={evr:+.2f}(s{evs:.0f},a{eva:.1f}x) | "
                    "raw={raw} adj={adj} antigreed={ag:+d} | "
                    "P9_act={p9act} src={p9src} pb={pb} greed={gr}{ti}",
                    sym=symbol, sc=adjusted_score, cls=classification,
                    d=direction, pnl=pnl_pct, st=spike_status,
                    act=is_actionable, h=z_raw,
                    md=momentum_result.score,
                    mdd=momentum_result.consecutive_decelerations,
                    mdr="R" if momentum_result.momentum_reversed else "",
                    sls=momentum_result.slope_short,
                    ext=extension_result.extension_atr, exts=extension_result.score,
                    vs=volume_result.score, vt=volume_result.divergence_type[:4],
                    evr=risk_reward_result.ev_ratio, evs=risk_reward_result.score,
                    eva=risk_reward_result.profit_amplifier,
                    raw=raw_score, adj=adjusted_score, ag=adjusted_score - raw_score,
                    p9act=_act_tag,
                    p9src=action_result.source if action_result else "N/A",
                    pb=_pb_tag, gr=_greed_tag, ti=_trail_info,
                )

            # 4.6: Store snapshot for M5 execution
            tracked["last_score"] = {
                "exploit_score": adjusted_score,
                "raw_score": raw_score,
                "direction": direction,
                "classification": classification,
                "is_actionable": is_actionable,
                "spike_status": spike_status,
                "pnl_pct": pnl_pct,
                "z_raw": z_raw,
                "velocity": vel,
                "acceleration": accel,
                "volume_ratio": vol_ratio,
                "bb_position": bb_pos,
                "speed_factor": sf,
                "exhaustion": exhaust,
                "z_score": z_pts,
                "vel_score": vel_pts,
                "vol_score": vol_pts,
                "bb_score": bb_pts,
                "mom_score": mom_pts,
                # Phase 8+9: trail and action data
                "trail_stop": trail_result.trail_stop_price if trail_result else None,
                "trail_distance_pct": trail_result.trail_distance_pct if trail_result else None,
                "action": _act_tag,
                "pullback_pct": action_result.pullback_pct if action_result else 0.0,
                "greed_rule": _greed_tag,
            }
            # Phase 9 (P1-8 Sniper Stall Escape) — when the sniper has been
            # screaming `actionable=True` but the action engine keeps voting
            # `hold` (because score < threshold even though signals all
            # demand exit), bump the action up the priority ladder. After
            # `stall_escape_partial_after_ticks` ticks of consecutive
            # actionable+hold, escalate to ``partial_close``. After
            # `stall_escape_full_after_ticks`, escalate to ``full_close``.
            # The counter resets the moment any non-stall condition holds.
            try:
                stall_action = self._stall_escape_action(
                    symbol, tracked, is_actionable,
                    action_result.action if action_result else "hold",
                )
                if stall_action is not None:
                    # Mutate the existing ActionResult (dataclass slots=True
                    # supports attribute assignment) so the M5 execution
                    # loop dispatches the escalated action through the
                    # existing path. Source/note record the override.
                    if action_result is not None:
                        action_result.original_action = action_result.action
                        action_result.action = stall_action
                        action_result.source = "stall_escape"
                    log.warning(
                        f"SNIPER_STALL_ESCAPE | sym={symbol} "
                        f"ticks={tracked.get('_stall_ticks', 0)} "
                        f"escalated_to={stall_action} score={adjusted_score:.0f} "
                        f"pnl={pnl_pct:+.2f}% | {ctx()}"
                    )
            except Exception as _e:
                log.debug(
                    f"stall escape check failed for {{sym}}: {{err}}",
                    sym=symbol, err=str(_e),
                )

            # Store action and trail objects for M5 Phase 9 execution loop
            tracked["last_action"] = action_result
            tracked["last_trail"] = trail_result
            # Profit-Fetching Phase 4 — ladder candidate for the spine.
            tracked["last_ladder"] = ladder_result

            # Phase 10: Write to sniper_log (every N ticks OR action != hold OR high score)
            self._log_write_counter[symbol] = self._log_write_counter.get(symbol, 0) + 1
            _write_interval = self.settings.mode4.sniper_log_write_every_n_ticks
            _should_write = (
                self._log_write_counter[symbol] % _write_interval == 0
                or (action_result and action_result.action != "hold")
                or adjusted_score >= self.settings.mode4.log_always_above_score
            )
            if _should_write and _trail_state:
                await self._write_sniper_log(
                    symbol,
                    hurst_result,
                    momentum_result,
                    extension_result,
                    volume_result,
                    risk_reward_result,
                    composite_result,
                    trail_result,
                    action_result,
                    _trail_state,
                    pnl_pct,
                )

            # Periodic summary log (every 60 ticks = 5 min)
            self._model_log_count[symbol] = self._model_log_count.get(symbol, 0) + 1
            if self._model_log_count[symbol] % 60 == 0:
                log.info(
                    "Mode4 ({sym}): score={sc} {cls} dir={d} pnl={pnl:+.2f}% | "
                    "H={h:.3f} MD={md:.0f}(d{mdd}) Ext={ext:.1f}ATR(s{exts:.0f}) "
                    "Vol={vs:.0f}({vt}) EV={evr:+.2f}(s{evs:.0f},a{eva:.1f}x) | "
                    "act={act} pb={pb} trail={tsl} buf={blen}pts",
                    sym=symbol, sc=adjusted_score, cls=classification,
                    d=direction, pnl=pnl_pct, h=z_raw,
                    md=momentum_result.score, mdd=momentum_result.consecutive_decelerations,
                    ext=extension_result.extension_atr, exts=extension_result.score,
                    vs=volume_result.score, vt=volume_result.divergence_type[:4],
                    evr=risk_reward_result.ev_ratio, evs=risk_reward_result.score,
                    eva=risk_reward_result.profit_amplifier, blen=len(buf),
                    act=_act_tag, pb=_pb_tag,
                    tsl=(
                        f"{trail_result.trail_stop_price:.8f}({trail_result.trail_distance_pct:.2f}%)"
                        if trail_result and trail_result.should_apply else "off"
                    ),
                )

        # ─── M5: Phase 9 Action Execution ────────────────────────────
        # HIGH-9 fix (2026-05-09): wrap the per-symbol body in tid_scope
        # so logs emitted from _execute_action / _apply_trail_stop see
        # THIS iteration's tid, not the LAST tid from the M3/M4 loop
        # above. Pre-fix the audit observed sym=INJUSDT logs tagged
        # tid=t-KATUSDT-sniper because Loop 1 set tid to KATUSDT (last
        # iter) and Loop 2 inherited it for all symbols.
        for symbol, tracked in list(self._tracked.items()):
            with tid_scope(symbol, "sniper"):
                # ── Profit-Fetching spine (Phase 4) ──
                # Per-tick highest-stop-wins stop management, independent of
                # the score-based action engine (blueprint Part 6.2 — ratchet
                # every tick). Runs even when the score action is "hold" so a
                # climbing winner's stop keeps rising. Writes at most one stop
                # per tick through the gateway (R1/R2/R4 enforced; the ladder
                # source bypasses R3 only). When disabled, this is skipped and
                # the legacy score-tighten path below owns trailing.
                if self._pf.enabled or self._lc.enabled:
                    _pos_spine = tracked.get("position")
                    _latest_spine = tracked["buffer"].get_latest()
                    if _pos_spine is not None and _latest_spine is not None:
                        try:
                            await self._pf_apply_spine(
                                symbol, _pos_spine, tracked,
                                float(_latest_spine["price"]),
                            )
                        except Exception as _e:  # never let the spine kill the tick
                            log.error(
                                f"SNIPER_SPINE_ERR | sym={symbol} "
                                f"err='{str(_e)[:120]}' | {ctx()}"
                            )

                action_result = tracked.get("last_action")
                if not action_result or action_result.action == "hold":
                    continue
                if not tracked.get("last_score"):
                    continue

                pos = tracked["position"]
                trail_result = tracked.get("last_trail")

                # Bug 1 fix: re-read this symbol's price from its own buffer.
                # `current_price` from the M3/M4 loop held the LAST processed
                # coin's price at function scope, so passing it here routed the
                # wrong price into _apply_trail_stop's profit gate / distance
                # checks. Mirror the M7 pattern (line ~580) exactly.
                latest = tracked["buffer"].get_latest()
                if latest is None:
                    continue
                symbol_price = latest["price"]

                await self._execute_action(
                    symbol, action_result, trail_result, pos, symbol_price,
                )

                tracked["last_execution"] = {
                    "timestamp": time.time(),
                    "action": action_result.action,
                    "source": action_result.source,
                    "score": action_result.score_value,
                    "pnl_captured_pct": action_result.current_pnl,
                    "peak_pnl": action_result.peak_pnl,
                    "pullback_pct": action_result.pullback_pct,
                    "greed_rule": action_result.greed_rule_triggered,
                    "cooled_down": action_result.cooled_down,
                    "success": True,
                }

        # ─── M7: Record spikes and start counterfactuals ──────
        # HIGH-9 fix (2026-05-09): same pattern as M5 above.
        for symbol, tracked in list(self._tracked.items()):
            with tid_scope(symbol, "sniper"):
                score_data = tracked.get("last_score")
                if not score_data:
                    continue
                cls = score_data.get("classification", "NORMAL")
                action_result = tracked.get("last_action")
                has_p9_action = action_result and action_result.action != "hold"
                if cls not in ("CONSULT", "STRONG", "EXTREME") and not has_p9_action:
                    continue

                pos = tracked["position"]
                execution = tracked.get("last_execution", {})
                latest = tracked["buffer"].get_latest()
                detection_price = latest["price"] if latest else pos.mark_price

                # Determine action label for recording
                if execution.get("success"):
                    action_label = execution.get("action", "no_action")
                    close_pct_val = execution.get("close_pct", 0)
                    captured_pct = execution.get("pnl_captured_pct")
                elif not score_data.get("is_actionable"):
                    action_label = "blocked_immunity"
                    close_pct_val = 0
                    captured_pct = None
                elif self.is_in_cooldown(symbol):
                    action_label = "blocked_cooldown"
                    close_pct_val = 0
                    captured_pct = None
                else:
                    action_label = "no_action"
                    close_pct_val = 0
                    captured_pct = None

                pnl_usd = None
                if score_data.get("pnl_pct") and pos.entry_price > 0:
                    pnl_usd = score_data["pnl_pct"] / 100 * abs(pos.size * pos.entry_price)

                row_id = await self._record_spike(
                    symbol=symbol,
                    pos=pos,
                    score_data=score_data,
                    action=action_label,
                    close_pct=close_pct_val,
                    close_price=detection_price if execution.get("success") else None,
                    profit_captured_pct=captured_pct,
                    profit_captured_usd=pnl_usd if execution.get("success") else None,
                    claude_consulted=execution.get("claude_consulted", False),
                    claude_response=execution.get("claude_response"),
                    claude_response_time_ms=execution.get("claude_response_time_ms"),
                )

                # Start counterfactual tracking
                if row_id is not None and len(self._counterfactuals) < 20:
                    cf_key = f"{symbol}_{row_id}"
                    self._counterfactuals[cf_key] = {
                        "sniper_log_id": row_id,
                        "symbol": symbol,
                        "detection_time": time.time(),
                        "detection_price": detection_price,
                        "action_taken": action_label,
                        "profit_captured_pct": captured_pct or score_data.get("pnl_pct", 0),
                        "entry_price": pos.entry_price,
                        "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                        "prices_after": [],
                        "timestamps_after": [],
                    }

        # M7: Update counterfactual trackers
        await self._update_counterfactuals()

        # M8: Integration (future)

        # Phase 24 (Y-23): clear the per-symbol tid so post-tick code
        # (lifecycle hooks, manager hooks, the next worker's tick) does
        # not inherit the LAST symbol processed by the sniper.
        set_tid("")

        # Observability G2 — sampled SNIPER_TICK heartbeat (after tid
        # clear so the line carries no per-symbol tid).
        self._maybe_emit_tick_heartbeat(_tick_start)

    # ─── Position lifecycle ────────────────────────────────────────

    async def _get_positions(self) -> list | None:
        """Get current open positions from the active exchange.

        Uses the position_service which routes through the Transformer proxy
        to either Shadow or Bybit depending on the current mode. Positions
        are already enriched with local Bybit prices (price separation fix).

        Returns:
            List of Position objects, or None on error.
        """
        if not self.position_service:
            return []
        try:
            return await self.position_service.get_positions()
        except Exception as e:
            log.debug("ProfitSniper: failed to get positions: {err}", err=str(e))
            return None

    async def _on_position_opened(self, symbol: str, pos: Any) -> None:
        """Handle a newly detected position.

        Creates a ring buffer, pre-fills with historical kline data,
        and starts tracking the position.
        """
        # Enhanced ring buffer (buffer_max_size entries, buffer_min_ready for new models)
        buffer = EnhancedRingBuffer(
            symbol=symbol,
            max_size=self.settings.mode4.buffer_max_size,
            min_ready=self.settings.mode4.buffer_min_ready,
        )

        # Pre-fill with historical kline data so models have data immediately
        await self._prefill_buffer(symbol, buffer)

        # Get ATR at entry time for normalization
        side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
        atr_at_entry = await self._get_current_atr(symbol)

        # Finding 4 (2026-06-02): warm the ATR cache at open. The entry ATR
        # above is real volatility at entry; seed it as the last-known-good so
        # the trail uses it through the cold-cache bridge (a fresh M5 compute
        # raises DataError until the symbol has >= 50 M5 candles) instead of
        # falling back every tick. Live volatility takes over automatically
        # once the recompute succeeds. The _pf_effective_atr chain stays as
        # insurance for the case where even the entry ATR was unavailable.
        if atr_at_entry and atr_at_entry > 0:
            self._atr_last_good[symbol] = atr_at_entry
            self._atr_cache[symbol] = (atr_at_entry, time.time())
            log.info(
                f"SNIPER_ATR_CACHE_WARM | sym={symbol} atr_entry={atr_at_entry:.8f} "
                f"| seeded warm ATR at open (Finding 4) | {ctx()}"
            )
        else:
            # Direction-reconcile fix (2026-06-04, Problem 3 / F12,F27) — never
            # open a position with a zero seed ATR. When the entry ATR is
            # unavailable (a freshly-active low-data coin with too few klines for
            # a Wilder ATR, like MON), seed the warm cache with a percent-of-price
            # floor so the volatility-adaptive trail has a non-zero leash from the
            # first tick and its stop updates apply instead of no-op'ing; the live
            # M5 ATR takes over automatically once it computes. This seeds the
            # entry-ATR fallback too (PositionProfitState.atr_at_entry below), so
            # the trail refuses to rely on a zero value at the source rather than
            # only at the per-tick floor.
            _entry_px = float(getattr(pos, "entry_price", 0.0) or 0.0)
            _floor_pct = float(getattr(self._pf, "atr_zero_fallback_pct", 0.5))
            if _entry_px > 0 and _floor_pct > 0:
                atr_at_entry = _entry_px * _floor_pct / 100.0
                self._atr_last_good[symbol] = atr_at_entry
                self._atr_cache[symbol] = (atr_at_entry, time.time())
                log.warning(
                    f"SNIPER_ATR_SEED_FLOOR | sym={symbol} "
                    f"entry_px={_entry_px:.8f} floor_pct={_floor_pct:.2f} "
                    f"seed_atr={atr_at_entry:.8f} | entry ATR unavailable — "
                    f"seeded percent-of-price floor so the trail never opens on "
                    f"a zero ATR | {ctx()}"
                )

        # Create profit state tracker
        self._profit_states[symbol] = PositionProfitState(
            symbol=symbol,
            entry_price=pos.entry_price,
            direction=side_str,
            atr_at_entry=atr_at_entry,
            opened_at=time.time(),
        )

        self._tracked[symbol] = {
            "buffer": buffer,
            "first_seen_at": time.time(),
            "position": pos,
            # Definitive-fix Phase 10 (2026-04-28) — per-position
            # partial-emit counter so a stalled position can't fire
            # repeated partial_close actions across cooldown windows.
            # Reset implicitly when this dict is replaced on a new
            # _on_position_opened call (one position lifetime = one
            # cap budget).
            "_partials_emitted": 0,
            # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — type-aware
            # grace gap. ``_last_escape_type`` is "" / "partial" / "full"
            # so the gate at ``_stall_escape_action`` can require
            # ``partial_to_partial_grace_ticks`` between consecutive
            # partials and ``partial_to_full_grace_ticks`` between a
            # partial and a cap-path full close. ``_last_escape_tick``
            # captures ``_stall_ticks`` at the moment of emission so the
            # gate can compare on tick count rather than wall-clock time.
            "_last_escape_type": "",
            "_last_escape_tick": 0,
        }

        log.info(
            "ProfitSniper: new position {sym} {side} @ ${ep:,.2f}, "
            "buffer pre-filled with {pts} points, atr_entry={atr:.6f}",
            sym=symbol,
            side=side_str,
            ep=pos.entry_price,
            pts=len(buffer),
            atr=atr_at_entry,
        )

        # ── Loss-Cutting Technique 1: ATR-based initial stop ──
        # Place a volatility-sized stop the moment the position is tracked so a
        # stop exists from second one (blueprint 2.1), closing the up-to-5s gap
        # before the spine's first tick. Best-effort + fail-safe: the spine's
        # safety sweeper and the -3% watchdog hard stop remain the backstops.
        if (
            self._lc.enabled and self._lc.enable_atr_initial_stop
            and self.sl_gateway is not None
        ):
            try:
                await self._lc_place_initial_atr_stop(symbol, pos, atr_at_entry)
            except Exception as e:
                log.warning(
                    f"LOSS_ATR_INITIAL_STOP_FAIL | sym={symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )

    def _lc_cap_stop_distance(self, cap_dollars: float, size: float) -> float:
        """Cap STOP-placement distance, pulled inside the ceiling by the
        slippage buffer (Finding 5, 2026-06-02).

        The cap dollars convert to a price distance ``cap_dollars / size``. The
        exchange stop is a market-trigger stop that fills past its trigger on a
        fast move, so the placed trigger is pulled ``cap_slippage_buffer_pct``
        percent of that distance INSIDE the ceiling, so an expected slipped fill
        still lands within it. Placement only — the sacred force-close still
        fires at the true cap dollars. Returns 0.0 for a non-positive cap/size.
        """
        if cap_dollars <= 0 or size <= 0:
            return 0.0
        raw = cap_dollars / size
        # Clamp to [0, 0.99): a negative value can never loosen the stop, and a
        # misconfigured value >= 100% can never zero/flip the cap distance.
        buf = min(0.99, max(0.0, self._lc.cap_slippage_buffer_pct) / 100.0)
        return raw * (1.0 - buf)

    def _lc_net_cap_dollars(self, gross_cap_dollars: float, notional: float) -> float:
        """Finding N (2026-06-08) — tighten the gross cap so the NET loss is
        bounded by the intended budget.

        The cap distance bounds the GROSS price loss; the round-trip taker fee
        pushes the realized NET past the ceiling. Live: NEAR's gross ws_net was
        -74.69 (~= the $75 cap) but the realized NET was -81.24, ~8% over the
        "sacred" ceiling. Since realized net = gross + fee, bounding the gross
        budget at (cap - round-trip fee) makes the realized net land at or under
        the cap. Applied to BOTH the force-close threshold and the placed cap SL,
        so the worst net loss is bounded on every path.

        TIGHTENS only (Rule 8): it never widens the cap (floored at 0); a
        cap_round_trip_fee_pct <= 0 is the clean off-switch (restores the gross
        cap). Returns the net-aware gross budget in dollars.
        """
        fee_pct = getattr(self._lc, "cap_round_trip_fee_pct", 0.0)
        if fee_pct <= 0.0 or notional <= 0.0 or gross_cap_dollars <= 0.0:
            return max(0.0, gross_cap_dollars)
        return max(0.0, gross_cap_dollars - notional * fee_pct / 100.0)

    async def _lc_place_initial_atr_stop(
        self, symbol: str, pos: Any, entry_atr: float,
    ) -> None:
        """Place the ATR-based initial stop the moment a position is tracked.

        Loss-Cutting Technique 1 (blueprint 2.1). Distance = the young
        ATR-initial multiple x the effective ATR (live -> entry-ATR -> pct
        floor, so it is never zero), clamped to never sit looser than the
        sacred cap distance. Routed through the gateway tighten-only on the
        urgent lane (bypass_rate_limit) so a fresh position is protected without
        waiting on the 30s window; if a wider brain stop already exists this
        tightens it, if the position is naked this attaches one. R1/R2 still
        apply, so it can neither loosen nor sit pathologically close to price.
        """
        entry = float(getattr(pos, "entry_price", 0.0) or 0.0)
        size = abs(float(getattr(pos, "size", 0.0) or 0.0))
        if entry <= 0 or size <= 0:
            return
        side = getattr(pos, "side", None)
        is_long = (side == Side.BUY) or (getattr(side, "value", "") == "Buy")
        live_atr = await self._get_current_atr(symbol)
        atr_value, atr_src = self._pf_effective_atr(live_atr, entry_atr, entry)
        mult = self._lc.atr_initial_multiple_young
        dist = mult * atr_value
        if dist <= 0:
            return
        # Clamp so the initial stop is never looser than the sacred cap.
        cap_dist = None
        if self._lc.enable_hard_cap:
            ld = self._loss_dial.resolve_loss(0.0, self._pf.default_deadline_minutes)
            notional = size * entry
            # Finding N: bound the NET loss — subtract the round-trip fee so the
            # gross cap budget yields a realized net at/under the ceiling.
            cap_dollars = self._lc_net_cap_dollars(
                min(self._lc.cap_dollar_ceiling, notional * ld.cap_pct / 100.0),
                notional,
            )
            # Finding 5: place the cap-bounded trigger inside the ceiling so a
            # market-stop's slipped fill still lands within the cap.
            cap_dist = self._lc_cap_stop_distance(cap_dollars, size) or None
            if cap_dist and cap_dist > 0:
                dist = min(dist, cap_dist)
        init_stop = round(entry - dist, 8) if is_long else round(entry + dist, 8)
        direction = "Buy" if is_long else "Sell"
        self._sl_updates_attempted_window += 1
        result = await self.sl_gateway.apply(
            symbol=symbol,
            new_sl=init_stop,
            source="loss_atr_initial",
            direction=direction,
            entry_price=entry,
            current_sl=self._get_current_sl(pos),
            current_price=entry,
            bypass_step_cap_for_breakeven=True,
            bypass_rate_limit=True,
        )
        log.info(
            f"LOSS_ATR_INITIAL_STOP | sym={symbol} stop={init_stop:.8f} "
            f"dist={dist:.8f} mult={mult} atr={atr_value:.8f} atr_src={atr_src} "
            f"cap_dist={('%.8f' % cap_dist) if cap_dist else 'na'} "
            f"accepted={result.accepted} reason={result.reason or '-'} "
            f"dir={direction} | {ctx()}"
        )

    def _on_position_closed(self, symbol: str) -> None:
        """Handle a position that disappeared (closed by another mode).

        Moves the buffer to recently_closed for counterfactual tracking.
        The buffer is kept for 60 seconds to see what the price does
        after the close (M7 uses this data).
        """
        tracked = self._tracked.pop(symbol, None)
        if tracked is None:
            return

        # Phase 3: Save profit state snapshot for TIAS BEFORE deleting it.
        # TIAS callback fires independently (coordinator callbacks) — by the time
        # it runs, _profit_states[symbol] may already be gone. The snapshot
        # preserves peak PnL, tick counts, and entry data for TIAS Group E.
        ps = self._profit_states.get(symbol)
        if ps is not None:
            self._closed_snapshots[symbol] = {
                "peak_pnl_pct": ps.peak_pnl_pct,
                "ticks_in_profit": ps.ticks_in_profit,
                "ticks_total": ps.ticks_total,
                "peak_price": ps.peak_price,
                "entry_price": ps.entry_price,
                "direction": ps.direction,
                "atr_at_entry": ps.atr_at_entry,
            }

        self._recently_closed[symbol] = {
            "buffer": tracked["buffer"],
            "closed_at": time.time(),
            "last_known_position": tracked["position"],
        }
        self._stale_skip_count.pop(symbol, None)
        self._profit_states.pop(symbol, None)
        self._atr_cache.pop(symbol, None)
        self._atr_last_good.pop(symbol, None)  # Finding 4: bound the warm cache
        # T1-3 (2026-05-12): clean up the per-symbol throttle dict for the
        # SNIPER_TRAIL_FLOOR_CLAMP event so memory stays bounded across
        # symbol churn. Mirrors the cleanup pattern for _last_trail_floor_logged
        # (currently kept; consider symmetrising in a follow-up).
        self._last_trail_from_price_floor_logged.pop(symbol, None)
        self._last_breakeven_floor_logged.pop(symbol, None)  # Finding 6 throttle
        self._smoothed_r.pop(symbol, None)  # Dynamic Adaptive Exit — bound R state
        self._last_ladder_adaptive_logged.pop(symbol, None)

        log.info(
            "ProfitSniper: position closed {sym}, retaining buffer ({pts} points) for counterfactual",
            sym=symbol,
            pts=len(tracked["buffer"]),
        )

    def get_closed_snapshot(self, symbol: str) -> dict:
        """Pop and return the last profit-state snapshot for a recently closed position.

        Used by TIAS to capture Mode4 peak PnL and tick data that would be lost
        once _profit_states is cleaned up. Returns empty dict if no snapshot exists.
        Pops to prevent unbounded memory growth.
        """
        return self._closed_snapshots.pop(symbol, {})

    # ─── Price data collection ─────────────────────────────────────

    async def _update_position(self, pos: Any) -> None:
        """Update a tracked position with the latest price data.

        Captures an enriched BufferPoint with price, PnL, ATR, volume data
        and appends to the enhanced ring buffer.
        """
        symbol = pos.symbol
        tracked = self._tracked.get(symbol)
        if not tracked:
            return

        # Update stored position reference
        tracked["position"] = pos

        # Capture enriched data point (replaces old simple dict append)
        await self._capture_buffer_point(symbol, pos)

    async def _get_price_data(self, symbol: str) -> dict | None:
        """Get current price data from the main project's own Bybit data.

        Uses MarketService.get_ticker() which has a 5-second in-memory cache
        backed by the PriceWorker's real-time WebSocket stream.

        Returns:
            Dict with ts, price, bid, ask, volume_24h — or None if
            the data is stale (>15 seconds) or unavailable.
        """
        if not self.market_service:
            return None
        try:
            ticker = await self.market_service.get_ticker(symbol)
            if ticker is None:
                return None

            # Freshness check — don't feed stale data into models
            age_seconds = (
                datetime.now(timezone.utc) - ticker.timestamp
            ).total_seconds()
            if age_seconds > 15:
                count = self._stale_skip_count.get(symbol, 0) + 1
                self._stale_skip_count[symbol] = count
                if count == 1 or count % 5 == 0:
                    level = log.warning if count >= 5 else log.debug
                    level(
                        "ProfitSniper: {sym} ticker stale ({age:.0f}s), skipping (count={c})",
                        sym=symbol,
                        age=age_seconds,
                        c=count,
                    )
                return None

            # Reset stale counter on fresh data
            self._stale_skip_count.pop(symbol, None)

            return {
                "ts": time.time(),
                "price": ticker.last_price,
                "bid": ticker.bid,
                "ask": ticker.ask,
                "volume_24h": ticker.volume_24h,
            }
        except Exception as e:
            log.debug("ProfitSniper: price lookup failed for {sym}: {err}", sym=symbol, err=str(e))
            return None

    # ─── Enhanced data capture ─────────────────────────────────────

    async def _capture_buffer_point(self, symbol: str, pos: Any) -> BufferPoint | None:
        """Capture one enriched data point for the enhanced ring buffer.

        Computes: mid-price, volume delta, Lee-Ready buy/sell estimation,
        PnL from own entry_price, peak tracking, ATR, ATR-normalized distance.
        Falls back to old-style dict append if enhanced data is unavailable.
        """
        tracked = self._tracked.get(symbol)
        state = self._profit_states.get(symbol)
        if not tracked:
            return None

        buffer = tracked["buffer"]

        # Get ticker data (same as _get_price_data but richer)
        price_data = await self._get_price_data(symbol)
        if price_data is None:
            return None

        now = time.time()
        price = price_data["price"]
        bid = price_data.get("bid", 0.0) or 0.0
        ask = price_data.get("ask", 0.0) or 0.0

        # Mid-price if spread available
        if bid > 0 and ask > 0:
            price = (bid + ask) / 2

        # Volume delta (5-second window)
        cumulative_vol = price_data.get("volume_24h", 0.0) or 0.0
        volume_delta = buffer.compute_volume_delta(cumulative_vol)

        # Buy/sell estimation (Lee-Ready 1991)
        buy_vol, sell_vol = EnhancedRingBuffer.estimate_buy_sell_volume(
            price, bid, ask, volume_delta,
        )

        # PnL computation (independent of Shadow — from our own entry_price)
        pnl_pct = 0.0
        if state and state.entry_price > 0:
            if state.direction in ("Buy", "Long"):
                pnl_pct = ((price - state.entry_price) / state.entry_price) * 100
            elif state.direction in ("Sell", "Short"):
                pnl_pct = ((state.entry_price - price) / state.entry_price) * 100

        # Update peak tracking
        peak_pnl = 0.0
        drawdown = 0.0
        if state:
            state.update(pnl_pct, price, now)
            peak_pnl = state.peak_pnl_pct
            drawdown = pnl_pct - peak_pnl

        # ATR (cached for 30s to avoid per-tick fetch)
        atr_current = await self._get_current_atr(symbol)

        # ATR-normalized distance from entry
        distance_atr = 0.0
        if state and atr_current > 0:
            distance_atr = abs(price - state.entry_price) / atr_current

        # Spread
        spread = (ask - bid) if (bid > 0 and ask > 0) else 0.0

        point = BufferPoint(
            timestamp=now,
            price=price,
            bid=bid,
            ask=ask,
            spread=spread,
            volume_delta=volume_delta,
            buy_volume_est=buy_vol,
            sell_volume_est=sell_vol,
            pnl_pct=pnl_pct,
            peak_pnl_pct=peak_pnl,
            drawdown_from_peak=drawdown,
            distance_from_entry_atr=distance_atr,
            atr_current=atr_current,
            cumulative_volume=cumulative_vol,
        )

        buffer.add_point(point)
        return point

    async def _get_current_atr(self, symbol: str) -> float:
        """Get current 14-period ATR on 5-minute candles.

        Cached per-symbol for 30 seconds to avoid redundant TA computation.

        Finding 4 (2026-06-02): when a fresh M5 compute is unavailable — a
        newly-active symbol has < 50 M5 candles so ``engine.analyze`` raises
        DataError (MIN_CANDLES=50), or the result is zero — this returns the
        last-known-good ATR (seeded with the entry ATR at open) instead of
        zero, so the trail uses real volatility through the cold-cache bridge.
        Live volatility takes over automatically the moment the symbol has
        enough candles to recompute. Returns 0.0 only when no good value has
        ever been seen (then the ``_pf_effective_atr`` chain is the insurance).
        """
        # Check cache (30s TTL)
        cached = self._atr_cache.get(symbol)
        if cached:
            atr_val, fetch_time = cached
            if time.time() - fetch_time < 30.0:
                return atr_val

        if not self.ta_cache:
            return self._atr_last_good.get(symbol, 0.0)

        try:
            ta_result = await self.ta_cache.analyze(
                symbol=symbol,
                timeframe=TimeFrame.M5,
            )
            if ta_result:
                atr_val = (ta_result.get("volatility") or {}).get("atr_14") or 0.0
                if atr_val > 0:
                    # Fresh live ATR — refresh both the 30s cache and the
                    # sticky last-known-good that bridges future cold reads.
                    self._atr_cache[symbol] = (atr_val, time.time())
                    self._atr_last_good[symbol] = atr_val
                    return atr_val
        except Exception as e:
            log.debug("ProfitSniper: ATR fetch failed for {sym}: {err}", sym=symbol, err=str(e))

        # Cold read (DataError on < 50 candles, or a zero/None result). Serve
        # the warm last-known-good and refresh the 30s cache with it so the
        # engine is not hammered with repeated failing computes every tick
        # during the bridge. Falls through to 0.0 only when truly never warmed.
        warm = self._atr_last_good.get(symbol, 0.0)
        if warm > 0:
            self._atr_cache[symbol] = (warm, time.time())
        return warm

    # ─── Regime-aware composite scoring (Phase 7) ────────────────

    async def _get_regime(self, symbol: str):
        """Per-coin-authority Phase 7 (2026-05-29): the PER-SYMBOL market regime
        for sniper exit-weight selection (cached 30s PER SYMBOL). Returns the
        COIN'S OWN RegimeState or None (None -> BALANCED weights downstream via
        _select_weights). NEVER reads the global _last_regime and NEVER calls
        detect() — RegimeWorker is the sole detector (Phase 1); the sniper only
        READS the per-coin cache. Previously a single global slot leaked one
        position's regime onto every other position within the 30s window and
        applied BTC's regime to every coin.
        """
        now = time.time()
        cached = self._regime_cache.get(symbol)
        if cached is not None and (now - cached[1]) <= 30.0:
            return cached[0]
        regime = None
        if self.regime_detector:
            try:
                regime = self.regime_detector.get_coin_regime(symbol)
            except Exception:
                regime = None
        self._regime_cache[symbol] = (regime, now)
        return regime

    @staticmethod
    def _select_weights(regime) -> tuple[dict, str]:
        """Select model weights based on current market regime."""
        from src.workers.sniper_models import (
            BALANCED_WEIGHTS, DEAD_WEIGHTS, RANGING_WEIGHTS,
            TRENDING_WEIGHTS, VOLATILE_WEIGHTS,
        )
        if regime is None:
            return BALANCED_WEIGHTS, "balanced"

        adx = getattr(regime, "adx", None) or getattr(regime, "adx_value", 15)
        choppiness = getattr(regime, "choppiness", None) or getattr(regime, "choppiness_value", 50)
        regime_type = getattr(regime, "regime", None)
        regime_str = regime_type.value if hasattr(regime_type, "value") else str(regime_type or "")

        if adx >= 25:
            return TRENDING_WEIGHTS, "trending"
        elif adx < 12:
            return DEAD_WEIGHTS, "dead"
        elif choppiness > 60 or "volatile" in regime_str.lower():
            return VOLATILE_WEIGHTS, "volatile"
        else:
            return RANGING_WEIGHTS, "ranging"

    def _compute_composite_score(self, hurst, momentum, extension, volume, risk_reward, regime):
        """Combine 5 model scores with regime-aware dynamic weights.

        Adds consensus boost (3+ models agree) and urgency boost (any model >80).
        """
        from src.workers.sniper_models import CompositeScoreResult

        weights, regime_name = self._select_weights(regime)

        # Weighted average
        base = (
            hurst.score * weights["hurst"]
            + momentum.score * weights["momentum_decay"]
            + extension.score * weights["atr_extension"]
            + volume.score * weights["volume_divergence"]
            + risk_reward.score * weights["risk_reward"]
        )

        # Consensus boost
        scores = [hurst.score, momentum.score, extension.score, volume.score, risk_reward.score]
        high_count = sum(1 for s in scores if s > 50)
        if high_count >= 4:
            consensus = 12.0
        elif high_count == 3:
            consensus = 8.0
        else:
            consensus = 0.0

        # Urgency boost
        max_s = max(scores)
        urgency = max(0.0, (max_s - 80) * 0.3) if max_s > 80 else 0.0

        composite = max(0.0, min(100.0, base + consensus + urgency))

        return CompositeScoreResult(
            score=round(composite, 1),
            base_score=round(base, 1),
            regime_used=regime_name,
            consensus_count=high_count,
            consensus_boost=consensus,
            urgency_max_score=round(max_s, 1),
            urgency_boost=round(urgency, 1),
            hurst_score=round(hurst.score, 1),
            momentum_decay_score=round(momentum.score, 1),
            atr_extension_score=round(extension.score, 1),
            volume_divergence_score=round(volume.score, 1),
            risk_reward_score=round(risk_reward.score, 1),
        )

    # ─── Phase 8: ATR-Based Dynamic Trailing Stop ───────────────────

    @staticmethod
    def _get_current_sl(pos) -> float:
        """Extract current stop loss from position, defaulting to 0.0 if None."""
        sl = getattr(pos, "stop_loss", None)
        return float(sl) if sl is not None else 0.0

    # ─── Profit-Fetching Exit System helpers (2026-05-29) ──────────────
    def _pf_age_and_deadline(self, symbol: str) -> tuple[float, float]:
        """Trade age and per-trade deadline (minutes) for the time dial.

        Prefers the brain's TradePlan (authoritative ``opened_at`` +
        ``max_hold_minutes``); falls back to the sniper's own ``first_seen_at``
        clock and the configured ``default_deadline_minutes`` when no plan is
        registered (e.g. an externally-opened position).
        """
        plan = None
        if self.trade_coordinator is not None:
            try:
                plan = self.trade_coordinator.get_trade_plan(symbol)
            except Exception:
                plan = None
        if plan is not None and getattr(plan, "max_hold_minutes", 0) > 0:
            # PF/LC Top-15 Problem 2.5 — when dial-freeze is enabled, drive the
            # time dial off the ORIGINAL (pre-extension) deadline so a watchdog
            # deadline extension does not re-loosen the protective dial (the
            # dialed stall_min_age_fraction / cap / structure buffer would
            # otherwise slide back toward their young anchors as the age fraction
            # drops). Off → the possibly-extended max_hold_minutes (current
            # behaviour). The close-timer in the watchdog still uses the extended
            # value, so the grace is preserved either way.
            _deadline = float(plan.max_hold_minutes)
            if getattr(self._pf, "dial_freeze_on_original_deadline_enabled", False):
                _orig = float(getattr(plan, "_original_max_hold_minutes", 0) or 0)
                if _orig > 0:
                    _deadline = _orig
            return float(plan.age_minutes), _deadline
        first_seen = self._tracked.get(symbol, {}).get("first_seen_at")
        age_min = ((time.time() - first_seen) / 60.0) if first_seen else 0.0
        return age_min, float(self._pf.default_deadline_minutes)

    def _pf_effective_atr(
        self, live_atr: float, entry_atr: float, current_price: float,
    ) -> tuple[float, str]:
        """ATR distance (absolute price units) for the trail — never zero.

        Root fix for the ATR-zero hole (blueprint 6.3 / Part 9): when the live
        TA-cache ATR reads zero (cache miss, exception, or too few candles),
        fall back to the ATR captured at entry, then to a configured
        percent-of-price floor, so a position can never be left with no
        trailing protection (the trail must never fail open).

        Returns ``(atr_value, source)`` where source is
        ``"live" | "entry_atr" | "pct_floor"``.
        """
        if live_atr and live_atr > 0:
            return float(live_atr), "live"
        if entry_atr and entry_atr > 0:
            return float(entry_atr), "entry_atr"
        floor = max(0.0, current_price) * (self._pf.atr_zero_fallback_pct / 100.0)
        return floor, "pct_floor"

    def _log_trail_floor_clamp(
        self,
        *,
        symbol: str,
        proposed: float,
        floor_pct: float,
        floor_dist_abs: float,
        final: float,
        action: str,                      # "clamp" | "reject_would_loosen"
        atr_pct: float,
        cls: str | None,
        cur_dist_pct: float,
        symbol_price: float,
        direction: str,
    ) -> None:
        """T1-3 (2026-05-12) — throttled emitter for SNIPER_TRAIL_FLOOR_CLAMP.

        Emits at most once per symbol when (a) the floor changes >5 %
        from the prior emission OR (b) 60 s have elapsed. Mirrors the
        proven throttle pattern used by M4_TRAIL_FLOOR (the from-PEAK
        floor) — empirically prevents the per-tick log flood (~42 k
        events per rotation pre-throttle) while preserving observability.

        Two distinct ``action`` values:
          - ``clamp``               — proposed SL was too close to current
                                       price; pushed outward to floor
                                       distance. The push CONTINUES through
                                       the gateway.
          - ``reject_would_loosen`` — clamp would have moved SL past the
                                       existing cur_sl in the loosening
                                       direction. The push is dropped to
                                       preserve the R1 / Bug-2 tighten-only
                                       contract.
        """
        import time as _t
        _now = _t.monotonic()
        _last_floor, _last_ts = self._last_trail_from_price_floor_logged.get(
            symbol, (0.0, 0.0),
        )
        if _last_floor > 0:
            _changed_pct = abs(floor_pct - _last_floor) / max(_last_floor, 1e-9)
        else:
            _changed_pct = 1.0  # first emission for this symbol
        if _changed_pct > 0.05 or (_now - _last_ts) > 60.0:
            log.info(
                f"SNIPER_TRAIL_FLOOR_CLAMP | sym={symbol} "
                f"proposed={proposed:.8f} floor={final:.8f} final={final:.8f} "
                f"action={action} cur_dist_pct={cur_dist_pct:.3f}% "
                f"floor_pct={floor_pct:.3f}% floor_abs={floor_dist_abs:.8f} "
                f"atr5={atr_pct:.3f}% cls={cls or '?'} "
                f"price={symbol_price:.8f} dir={direction} | {ctx()}"
            )
            self._last_trail_from_price_floor_logged[symbol] = (floor_pct, _now)

    def _adaptive_r(self, symbol: str, raw_r_pct: float, ae) -> float:
        """Smoothed movement unit R (the coin's ATR-as-percent) for the adaptive
        exit geometry. EMA per symbol at the fetch boundary so the geometry
        breathes without vibrating; the profiler's 60-120s cache already makes
        the raw value step-wise stable, this smooths the steps. alpha=1.0 (or a
        first observation) disables smoothing. The pure geometry functions stay
        stateless — the only state lives here, popped on close."""
        raw = max(0.0, float(raw_r_pct))
        alpha = float(getattr(ae, "r_smoothing_alpha", 0.3))
        if alpha >= 1.0 or symbol not in self._smoothed_r:
            self._smoothed_r[symbol] = raw
            return raw
        prev = self._smoothed_r[symbol]
        sm = alpha * raw + (1.0 - alpha) * prev
        self._smoothed_r[symbol] = sm
        return sm

    def _compute_trail_stop(
        self,
        state: PositionProfitState,
        extension_result,
        momentum_result,
        regime_name: str,
        current_sl: float,
        base_atr_mult: float,
        atr_value: float | None = None,
    ) -> TrailResult:
        """Compute ATR-based dynamic trailing stop price.

        trail_distance = base_atr_mult × ATR × regime_factor × profit_decay × momentum_factor
        Trail is measured from PEAK price (not current price) — creates ratchet effect.
        Trail only tightens (ratchet rule). Trail never worse than entry (breakeven floor).

        ``atr_value`` (Profit-Fetching Phase 2): when provided, overrides
        ``extension_result.atr_current`` as the ATR distance. The caller passes
        the ATR-zero fallback distance (live -> entry-ATR -> percent-of-price
        floor) so the trail never silently disappears when the TA cache returns
        zero. Defaults to the live ATR for the legacy (disabled) path.
        """
        atr = atr_value if atr_value is not None else extension_result.atr_current
        peak_price = state.peak_price
        entry_price = state.entry_price
        direction = state.direction
        is_long = direction == "Buy"

        # No-op result template for guard returns
        def _noop(reason: str = "") -> TrailResult:
            return TrailResult(
                trail_stop_price=current_sl,
                trail_distance=0.0,
                trail_distance_pct=0.0,
                base_atr_mult=base_atr_mult,
                atr_used=atr,
                regime_factor=REGIME_TRAIL_FACTORS.get(regime_name, 0.85),
                profit_decay=1.0,
                momentum_factor=1.0,
                peak_price=peak_price,
                entry_price=entry_price,
                direction=direction,
                is_tighter_than_current=False,
                current_sl=current_sl,
                should_apply=False,
            )

        # Guard: cannot compute trail without valid ATR
        if atr <= 0 or peak_price <= 0:
            return _noop()

        # ── Regime factor ──
        regime_factor = REGIME_TRAIL_FACTORS.get(regime_name, 0.85)

        # ── Profit decay: trail tightens as position extends further in profit ──
        ext_atr = max(0.0, extension_result.extension_atr)  # signed, positive = profit
        profit_decay = 1.0 / (1.0 + 0.2 * ext_atr)
        profit_decay = max(profit_decay, self.settings.mode4.min_profit_decay)

        # ── Momentum factor: trail tightens when momentum is dying ──
        mom_score = momentum_result.score
        if mom_score < 20:
            momentum_factor = 1.1   # Low pressure → wider trail (let it run)
        elif mom_score < 50:
            momentum_factor = 1.0   # Normal
        elif mom_score < 75:
            momentum_factor = 0.8   # Decaying → tighter
        else:
            momentum_factor = 0.6   # Dying → very tight

        # ── Trail distance ──
        trail_distance = base_atr_mult * atr * regime_factor * profit_decay * momentum_factor

        # TRADE LIBERATION: Trail distance floor — prevent suicidal micro-trails
        min_trail_atr = atr * self.settings.mode4.min_trail_atr_multiplier
        min_trail_pct_abs = entry_price * (self.settings.mode4.min_trail_pct / 100)
        min_trail = max(min_trail_atr, min_trail_pct_abs)
        if trail_distance < min_trail:
            # Phase 12.6 (Gap 6.6-G1): emit only on >5% change or after 60s.
            # Pre-fix this tag fired 42k+ times per rotation; now bounded
            # by change-detection so operators see meaningful floor moves
            # without per-tick noise.
            import time as _t
            _now = _t.monotonic()
            _last_floor, _last_ts = self._last_trail_floor_logged.get(
                state.symbol, (0.0, 0.0)
            )
            _changed_pct = (
                abs(min_trail - _last_floor) / max(_last_floor, 1e-9)
                if _last_floor > 0
                else 1.0
            )
            if _changed_pct > 0.05 or (_now - _last_ts) > 60.0:
                log.info(
                    f"M4_TRAIL_FLOOR | sym={state.symbol} "
                    f"raw={trail_distance:.2f} floor={min_trail:.2f} "
                    f"atr_floor={min_trail_atr:.2f} pct_floor={min_trail_pct_abs:.2f} | {ctx()}"
                )
                self._last_trail_floor_logged[state.symbol] = (min_trail, _now)
            trail_distance = min_trail

        # ── Trail stop price from peak ──
        if is_long:
            trail_stop = peak_price - trail_distance
            trail_stop = max(trail_stop, entry_price)               # Breakeven floor
            is_tighter = (current_sl <= 0) or (trail_stop > current_sl)
        else:
            trail_stop = peak_price + trail_distance
            trail_stop = min(trail_stop, entry_price)               # Breakeven floor
            is_tighter = (current_sl <= 0) or (trail_stop < current_sl)

        trail_stop = round(trail_stop, 8)

        # ── Minimum change threshold: avoid flooding Shadow with tiny SL mods ──
        if current_sl > 0 and peak_price > 0:
            change_pct = abs(trail_stop - current_sl) / peak_price * 100
            meets_threshold = change_pct > self.settings.mode4.trail_min_change_pct
        else:
            meets_threshold = True

        # ── Only apply if position has meaningful profit ──
        # PF/LC Top-15 Problem 2.4 — the Chandelier trail now activates at the
        # SAME threshold the ladder arms (profit_fetching.min_profit_for_trail_pct
        # = the 0.2% arm), instead of the stale mode4 0.5%. Previously the
        # 0.2-to-0.5% band had only the ladder active and the Chandelier idle,
        # contrary to the blueprint's two-coexisting-candidates intent. The
        # ladder floor already protects that band (no naked exposure) and the
        # trail can only tighten (R1), so this is low risk.
        in_profit = state.peak_pnl_pct >= self._pf.min_profit_for_trail_pct

        should_apply = in_profit and is_tighter and meets_threshold

        trail_distance_pct = round(trail_distance / peak_price * 100, 4) if peak_price > 0 else 0.0

        return TrailResult(
            trail_stop_price=trail_stop,
            trail_distance=round(trail_distance, 8),
            trail_distance_pct=trail_distance_pct,
            base_atr_mult=base_atr_mult,
            atr_used=atr,
            regime_factor=regime_factor,
            profit_decay=round(profit_decay, 4),
            momentum_factor=momentum_factor,
            peak_price=peak_price,
            entry_price=entry_price,
            direction=direction,
            is_tighter_than_current=is_tighter,
            current_sl=current_sl,
            should_apply=should_apply,
        )

    def _compute_ladder_floor(
        self,
        state: PositionProfitState,
        dialed,
        current_sl: float,
        atr_value: float | None = None,
    ) -> LadderResult:
        """Stepped break-even ladder floor — Profit-Fetching technique 1.

        As the trade's high-water profit climbs past successive levels (every
        ``dialed.ladder_step_pct``), the stop locks a rising guaranteed-profit
        floor a fixed ``dialed.lock_offset_pct`` behind the level just crossed
        (blueprint 2.1). Driven by ``state.peak_pnl_pct`` so the floor is
        monotonic — once a level is crossed the lock stays even on a pullback
        (tighten-only). Returns a candidate the Phase 4 spine reconciles with
        the Chandelier trail and the current SL under highest-stop-wins.

        ``dialed`` is a time_dial.DialedParams (step/offset already glided to
        the trade's age). All thresholds are config, never hardcoded inline.
        """
        entry = state.entry_price
        direction = state.direction
        is_long = direction == "Buy"
        peak = state.peak_pnl_pct
        step = dialed.ladder_step_pct
        offset = dialed.lock_offset_pct
        arm = self._pf.min_profit_to_arm_ladder_pct
        # Issue 1 (CALL_A exploit/fetch, 2026-06-05) — decoupled micro-floor arm.
        # The breakeven / dead-band floor arms at this LOWER threshold so the
        # small green most losers reach (median ~+0.07%, below the 0.2%
        # graduation arm) can be locked instead of round-tripping. The one-way
        # GRADUATION_LATCH (loss-cutting -> profit-system authority handoff)
        # still reads min_profit_to_arm_ladder_pct, so authority is NOT
        # transferred early and the spike/cap/stall protection is retained until
        # genuine +0.2%. Bounded to never exceed the graduation arm.
        _micro_arm = float(getattr(self._pf, "micro_floor_arm_pct", arm))
        _raw_floor_arm = min(_micro_arm, arm)  # F2: the old effective arm, for the suppress sentinel
        # F2 (fee-scratch churn, 2026-06-09) — fee-aware micro-floor arm. Default
        # OFF (behaviour-preserving). The micro arm (0.10%) and the breakeven lock
        # (0.05%) both sit BELOW the round-trip taker fee (~0.11%), and the
        # fee-aware LIFT below (ladder_lock_fee_clearance_pct, ~line 2050) only
        # fires when peak > fee_clear. So a trade peaking in [micro_arm, fee_clear)
        # arms a breakeven stop that is never lifted, locks sub-fee, and books a
        # guaranteed NET FEE LOSS when a tiny pullback taps it — the proven
        # sub-2-min fee-scratch. When enabled, raise the effective arm to the SAME
        # fee hurdle the lift uses (reuse the existing fee-clearance value — no
        # inline fee), so the floor does not arm and pull the stop to a sub-fee
        # breakeven until the trade has actually cleared the fee. Below that the
        # ladder no-ops (return just below) and the trade keeps its original wider
        # stop to breathe, with loss-cutting authority retained until graduation.
        # The outer min(..., arm) clamp is RETAINED so the effective arm can never
        # exceed the graduation arm (min_profit_to_arm_ladder_pct) nor touch the
        # GRADUATION_LATCH, which reads min_profit_to_arm_ladder_pct only.
        if getattr(self._pf, "micro_floor_arm_fee_aware_enabled", False):
            _fee_clear_arm = float(
                getattr(self._pf, "ladder_lock_fee_clearance_pct", 0.0)
            )
            if _fee_clear_arm > 0.0:
                _micro_arm = max(_micro_arm, _fee_clear_arm)
        floor_arm = min(_micro_arm, arm)

        def _noop(armed: bool) -> LadderResult:
            return LadderResult(
                ladder_stop_price=current_sl,
                level_crossed_pct=0.0,
                lock_pct=0.0,
                step_pct=step,
                lock_offset_pct=offset,
                peak_pnl_pct=round(peak, 4),
                entry_price=entry,
                direction=direction,
                is_tighter_than_current=False,
                current_sl=current_sl,
                armed=armed,
                should_apply=False,
            )

        # ── Dynamic Adaptive Exit (2026-06-15): R-based ladder lock ──
        # When enabled, the lock is a bounded multiple of R (the coin's ATR-as-
        # percent) floored at the round-trip fee: the staged capture plus the
        # R-fraction trail behind the high-water peak (vol_scale.profit_lock_pct).
        # Driven by the monotonic high-water peak it ratchets and is tighten-only,
        # so it IS the per-coin trailing lock — the Chandelier candidate still
        # computes but loses highest-stop-wins to this richer lock, harmlessly.
        # The legacy fixed-percentage branches below are bypassed; the spine +
        # gateway profit-lock exemption write it. Nothing here is hardcoded.
        _ae = getattr(getattr(self, "settings", None), "adaptive_exit", None)
        if (
            _ae is not None and getattr(_ae, "enabled", False)
            and entry > 0 and atr_value and atr_value > 0
        ):
            from src.analysis import vol_scale as _vg
            _sym = getattr(state, "symbol", "?")
            R = self._adaptive_r(_sym, atr_value / entry * 100.0, _ae)
            _lock = _vg.profit_lock_pct(peak, R, _ae)
            if _lock is None:
                return _noop(armed=False)
            if is_long:
                _astop = round(entry * (1.0 + _lock / 100.0), 8)
                _is_t = (current_sl <= 0) or (_astop > current_sl)
            else:
                _astop = round(entry * (1.0 - _lock / 100.0), 8)
                _is_t = (current_sl <= 0) or (_astop < current_sl)
            if _is_t:
                _now = time.monotonic()
                if _now - self._last_ladder_adaptive_logged.get(_sym, 0.0) >= 60.0:
                    self._last_ladder_adaptive_logged[_sym] = _now
                    _teff = _vg.effective_trail_r(peak, R, _ae)
                    _kept = (100.0 * _lock / peak) if peak > 0 else 0.0
                    log.info(
                        f"LADDER_ADAPTIVE | sym={_sym} peak={peak:.3f}% R={R:.3f}% "
                        f"arm={_vg.arm_pct(R, _ae):.3f}% lock={_lock:.3f}% "
                        f"trail_eff={_teff:.3f}R kept={_kept:.0f}% "
                        f"stop={_astop:.8f} entry={entry:.8f} dir={direction} "
                        f"| R-derived fee-floored lock (staged + profit-scaled trail behind peak) | {ctx()}"
                    )
            return LadderResult(
                ladder_stop_price=_astop,
                level_crossed_pct=0.0,
                lock_pct=round(_lock, 4),
                step_pct=step,
                lock_offset_pct=offset,
                peak_pnl_pct=round(peak, 4),
                entry_price=entry,
                direction=direction,
                is_tighter_than_current=_is_t,
                current_sl=current_sl,
                armed=True,
                should_apply=_is_t,
                breakeven_floor=False,
            )

        # Not armed until the high-water profit reaches the floor-arm (Issue 1:
        # the decoupled micro arm, below the graduation arm), and the ladder is
        # meaningless without a valid entry / positive step.
        if entry <= 0 or step <= 0 or peak < floor_arm:
            # F2 observability — the fee-aware arm SUPPRESSED a sub-fee floor that
            # the old micro arm would have armed (peak in [raw_arm, fee_aware_arm)).
            # This is a scratch PREVENTED: arming here would have pulled the stop to
            # a sub-fee breakeven that a tiny pullback taps for a net fee loss.
            # Throttled per-symbol (reuse the existing breakeven-floor 60s cadence
            # dict, cleaned in _on_position_closed) so it cannot spam per tick.
            if (
                getattr(self._pf, "micro_floor_arm_fee_aware_enabled", False)
                and entry > 0 and step > 0
                and _raw_floor_arm <= peak < floor_arm
            ):
                _sym_fs = getattr(state, "symbol", "?")
                _now_fs = time.monotonic()
                if _now_fs - self._last_breakeven_floor_logged.get(_sym_fs, 0.0) >= 60.0:
                    self._last_breakeven_floor_logged[_sym_fs] = _now_fs
                    log.info(
                        f"MICRO_FLOOR_FEE_SUPPRESS | sym={_sym_fs} peak={peak:+.3f}% "
                        f"raw_arm={_raw_floor_arm:.3f}% fee_arm={floor_arm:.3f}% "
                        f"fee_clear={getattr(self._pf, 'ladder_lock_fee_clearance_pct', 0.0):.3f}% "
                        f"| sub-fee breakeven floor NOT armed; trade keeps its wider "
                        f"stop to breathe — fee-scratch prevented | {ctx()}"
                    )
            return _noop(armed=(peak >= floor_arm and step > 0 and entry > 0))

        # Highest fully-crossed step level on the high-water profit. The small
        # epsilon absorbs float error at exact boundaries (e.g. 1.5/0.5).
        level = int(peak / step + 1e-9) * step
        lock_pct = level - offset

        # Finding 6 (2026-06-02): zero-crossing breakeven floor. The arm
        # threshold (0.5%) sits below the first step rung (0.6% young), so a
        # modest peak in [arm, first_step) armed the ladder but level=0 made
        # lock_pct negative and nothing locked — a +0.59% peak rode back to a
        # small loss. Once the high-water profit reaches arm, guarantee AT LEAST
        # the breakeven-floor lock (entry plus a tiny sliver) while price is
        # still elevated, so the stop ratchets to at least breakeven. The floor
        # only fills the gap where the step lock is at/below break-even (a
        # positive crossed-rung lock is structurally left untouched — gated on
        # lock_pct <= 0 — so it can never be overridden even if be_lock is
        # mistuned above a real rung's lock). The min-distance rule is NOT
        # loosened — breakeven is reached by locking earlier (price is well above
        # entry at arming), not by placing a stop on noise.
        be_lock = self._pf.ladder_breakeven_lock_pct
        breakeven_floor = be_lock > 0.0 and lock_pct <= 0.0
        if breakeven_floor:
            # Fix 3 (2026-06-05) — dead-band give-back trail. A peak in
            # [arm, first_step) has level=0 so the step lock is negative; instead
            # of locking only the breakeven sliver and round-tripping the whole
            # modest peak (live: IMXUSDT +$21 peak booked -$5), trail a fixed
            # give-back below the high-water peak so the floor banks
            # (peak - giveback). Monotonic (peak is high-water) and bounded below
            # by be_lock so it is tighten-only and never sub-breakeven. The normal
            # step lock takes over once a real rung is crossed (breakeven_floor
            # goes False). Gateway R1/R2 still apply. giveback <= 0 = old behaviour.
            giveback = getattr(self._pf, "ladder_deadband_giveback_pct", 0.0)
            if giveback > 0.0:
                lock_pct = max(be_lock, round(peak - giveback, 4))
            else:
                lock_pct = be_lock
        if lock_pct <= 0:
            # Either the breakeven floor is disabled (be_lock <= 0, the clean
            # off-switch that restores the old behaviour) or the step lock still
            # sits at/below break-even — no guaranteed-profit floor to lock yet.
            return _noop(armed=True)

        # Finding A (2026-06-08) — fee-aware floor. A gross-positive lock that
        # does not clear the round-trip taker fee still books a NET loss after
        # fees (a "breakeven" that quietly loses the fee — the dominant
        # small-loss mechanism: NEAR locked +0.035% -> net -$4.57). When a
        # sub-fee floor would lock AND the trade's peak actually cleared the fee
        # hurdle, raise the floor to the fee-clearing level so a "breakeven" lock
        # is net-breakeven-or-better. Bounded by peak (peak > fee_clear is the
        # arming condition), so the lift never places a stop above the high-water
        # the trade reached. The step locks (level - offset, >= ~0.3%) already
        # clear the fee, so only the sub-fee breakeven/dead-band floor is lifted.
        # When the peak never cleared the fee, the existing floor is KEPT (it caps
        # the loss near the fee rather than riding to the cap) — protection is not
        # removed, it just cannot be net-positive. Off-switch: fee_clearance <= 0.
        _fee_clear = getattr(self._pf, "ladder_lock_fee_clearance_pct", 0.0)
        if _fee_clear > 0.0 and lock_pct < _fee_clear and peak > _fee_clear:
            lock_pct = _fee_clear

        if is_long:
            ladder_stop = entry * (1.0 + lock_pct / 100.0)
            is_tighter = (current_sl <= 0) or (ladder_stop > current_sl)
        else:
            ladder_stop = entry * (1.0 - lock_pct / 100.0)
            is_tighter = (current_sl <= 0) or (ladder_stop < current_sl)
        ladder_stop = round(ladder_stop, 8)

        if breakeven_floor and is_tighter:
            _now = time.monotonic()
            _sym = getattr(state, "symbol", "?")
            if _now - self._last_breakeven_floor_logged.get(_sym, 0.0) >= 60.0:
                self._last_breakeven_floor_logged[_sym] = _now
                log.info(
                    f"LADDER_ZERO_CROSSING_FLOOR | sym={_sym} peak={peak:.3f}% "
                    f"arm={arm:.2f}% step={step:.2f}% level={level:.2f}% "
                    f"be_lock={be_lock:.3f}% "
                    f"fee_clear={getattr(self._pf, 'ladder_lock_fee_clearance_pct', 0.0):.3f}% "
                    f"lock={lock_pct:.3f}% "
                    f"stop={ladder_stop:.8f} entry={entry:.8f} dir={direction} "
                    f"| modest peak locks at least net-breakeven (Finding 6 + A) | {ctx()}"
                )
                # Issue 1 — the micro-floor specifically captured green BELOW the
                # graduation arm (peak in [floor_arm, arm)) — the round-tripping
                # small-green band the old single-arm floor never reached. The
                # loss-cutting authority is still held (peak < graduation arm).
                if peak < arm:
                    log.info(
                        f"MICRO_FLOOR_ARM | sym={_sym} peak={peak:.3f}% "
                        f"micro_arm={floor_arm:.3f}% grad_arm={arm:.2f}% "
                        f"be_lock={be_lock:.3f}% lock={lock_pct:.3f}% "
                        f"stop={ladder_stop:.8f} entry={entry:.8f} dir={direction} "
                        f"| small green captured below graduation arm; "
                        f"loss-cutting authority retained | {ctx()}"
                    )

        return LadderResult(
            ladder_stop_price=ladder_stop,
            level_crossed_pct=round(level, 4),
            lock_pct=round(lock_pct, 4),
            step_pct=step,
            lock_offset_pct=offset,
            peak_pnl_pct=round(peak, 4),
            entry_price=entry,
            direction=direction,
            is_tighter_than_current=is_tighter,
            current_sl=current_sl,
            armed=True,
            should_apply=is_tighter,
            breakeven_floor=breakeven_floor,
        )

    @staticmethod
    def _pf_safety_floor(entry_price: float, is_long: bool, safety_pct: float) -> float:
        """The safety-stop / loss-cap price: ``safety_pct`` off entry.

        Long: below entry; short: above entry. The fixed loss cap for trades
        that never climb (blueprint Part 5). Not age-dialed — it is a hard cap,
        sitting inside the watchdog -3% hard stop so it acts first.
        """
        if entry_price <= 0:
            return 0.0
        if is_long:
            return round(entry_price * (1.0 - safety_pct / 100.0), 8)
        return round(entry_price * (1.0 + safety_pct / 100.0), 8)

    @staticmethod
    def _pf_select_stop(
        trail, ladder, current_sl: float, is_long: bool,
        safety_stop: float | None = None,
        loss_candidates: list[tuple[str, float, str]] | None = None,
        offer_profit: bool = True,
        offer_loss: bool = True,
    ) -> tuple[str, float, str] | None:
        """Highest-stop-wins selection — the spine (Rule 7, blueprint 3.1).

        Pure function over every candidate stop. The profit side names the
        ladder floor and the Chandelier trail (each included only if it is
        proposing a tighten this tick); the safety stop, when supplied, is
        always a candidate (the loss cap / naked-position floor). The
        Loss-Cutting System contributes its own candidates via
        ``loss_candidates`` (already filtered/computed by the caller as
        ``(name, price, source)`` tuples: the sacred cap in Phase 2, plus the
        structure / spike / recovery stops in later phases). All candidates
        compete under one rule — the tightest wins (highest price for a long,
        lowest for a short). Returns the winner or ``None`` when nothing beats
        the current SL. The current SL is the implicit final candidate: a
        winner must beat it (tighten-only) or it is dropped.
        """
        candidates: list[tuple[str, float, str]] = []
        # Phase 3 (green-owner consolidation): the profit-side tools (ladder and
        # Chandelier trail) are offered ONLY when the trade is green-owned. When
        # offer_profit is False (the red owner holds the stop, decided by the
        # caller via gateway.peek_owner under enforcement), they do not compete,
        # so a profit candidate cannot win the spine on a red trade and then be
        # deferred by the gateway — which would leave the red trade unwritten.
        # The safety floor and the loss candidates still compete (the naked-
        # position safety_sweeper is always-allowed). offer_profit defaults True,
        # so log-only mode and every non-enforcing caller behave exactly as
        # before. The arm/lock/trail VALUES are untouched (Rule 6) — this changes
        # only WHEN the profit tools are offered, not what they compute.
        if offer_profit and ladder is not None and ladder.should_apply:
            candidates.append(
                ("ladder", ladder.ladder_stop_price, "profit_sniper_ladder")
            )
        if offer_profit and trail is not None and trail.should_apply:
            candidates.append(
                ("chandelier", trail.trail_stop_price, "profit_sniper_trail")
            )
        if safety_stop is not None and safety_stop > 0:
            candidates.append(("safety", safety_stop, "safety_sweeper"))
        if loss_candidates:
            for (n, p, s) in loss_candidates:
                if not (p and p > 0):
                    continue
                # Phase 4 (red-owner consolidation): the red owner's tools (the
                # structure stop and the final-phase recovery trail) are offered
                # ONLY when the trade is red-owned. The sacred cap
                # (loss_cap / loss_cap_emergency) is the Head floor and competes
                # in BOTH states — it is never suppressed. offer_loss defaults
                # True so log-only mode and non-enforcing callers are unchanged.
                if (not offer_loss) and s not in (
                    "loss_cap", "loss_cap_emergency",
                ):
                    continue
                candidates.append((n, p, s))
        if not candidates:
            return None
        winner = (
            max(candidates, key=lambda c: c[1])
            if is_long
            else min(candidates, key=lambda c: c[1])
        )
        _name, price, _source = winner
        # Tighten-only vs the current SL (the gateway enforces this too; the
        # explicit check keeps the SNIPER_SPINE_SELECT log honest).
        if current_sl and current_sl > 0:
            if is_long and price <= current_sl:
                return None
            if (not is_long) and price >= current_sl:
                return None
        return winner

    async def _lc_recovery_candidate(
        self, symbol: str, tracked: dict, state: Any, is_long: bool,
        current_price: float,
    ) -> float | None:
        """Final-phase history-aware recovery bounce-capture (blueprint 5.3).

        In the trade's final minutes, reads its life history: a mostly-profit-
        side struggler (high ticks-in-profit ratio) earns a WIDER bounce trail
        (more room to recover); a mostly-loss-side struggler gets a TIGHT trail
        that captures near its least loss. The stop trails the recovery extreme
        since the trough — the Chandelier pattern applied on the loss side — so
        it captures near the least loss WITHOUT predicting the minimum (which is
        unknowable in real time, Rule 10). Returns a loss-side SL candidate
        price, or None when there has been no real bounce yet. It competes in the
        spine; the cap (also a candidate) wins if it is tighter, so the recovery
        always stays inside the cap.
        """
        # Reset the recovery extreme whenever a new (worse) trough is made.
        _seen = tracked.get("_lc_trough_pnl_seen")
        if _seen is None or state.trough_pnl_pct < _seen - 1e-12:
            tracked["_lc_trough_pnl_seen"] = state.trough_pnl_pct
            tracked["_lc_recovery_ext"] = state.trough_price
        _rec = tracked.get("_lc_recovery_ext", state.trough_price)
        _rec = max(_rec, current_price) if is_long else min(_rec, current_price)
        tracked["_lc_recovery_ext"] = _rec
        # No candidate until a real bounce off the trough has begun (else the
        # ATR/cap candidates protect; the recovery only acts on a live bounce).
        if (is_long and _rec <= state.trough_price) or (
            (not is_long) and _rec >= state.trough_price
        ):
            return None

        live_atr = await self._get_current_atr(symbol)
        atr_value, _src = self._pf_effective_atr(
            live_atr, getattr(state, "atr_at_entry", 0.0), current_price,
        )
        if atr_value <= 0:
            return None
        _profit_side = state.profit_ratio >= self._lc.recovery_profit_side_ratio
        _trail_atr = (
            self._lc.recovery_bounce_trail_atr_profit_side if _profit_side
            else self._lc.recovery_bounce_trail_atr_loss_side
        )
        _dist = max(0.0, _trail_atr) * atr_value
        if _dist <= 0:
            return None
        if is_long:
            stop = round(_rec - _dist, 8)
            if stop <= 0.0 or stop >= current_price:
                return None
        else:
            stop = round(_rec + _dist, 8)
            if stop <= 0.0 or stop <= current_price:
                return None
        _now = time.time()
        if _now - tracked.get("_lc_recovery_log_ts", 0.0) >= 60.0:
            tracked["_lc_recovery_log_ts"] = _now
            log.info(
                f"LOSS_RECOVERY | sym={symbol} "
                f"side={'profit' if _profit_side else 'loss'} "
                f"profit_ratio={state.profit_ratio:.2f} "
                f"trough_pnl={state.trough_pnl_pct:.3f} recovery_ext={_rec:.8f} "
                f"trail_atr={_trail_atr} stop={stop:.8f} "
                f"dir={'Buy' if is_long else 'Sell'} | trailing the bounce near "
                f"least-loss (Chandelier on the loss side) | {ctx()}"
            )
        return stop

    async def _lc_spike_triggered(
        self, symbol: str, tracked: dict, state: Any,
        current_price: float, is_long: bool,
    ) -> tuple[bool, float, float, float]:
        """Detect a volatility-spike-down catastrophe (Technique 5).

        Measures the adverse price excursion over the last
        ``spike_window_seconds`` from the per-position ring buffer (the recent
        favorable extreme minus the current price for a long; mirrored for a
        short) and compares it to a multiple x the effective ATR. Independent of
        the time DIAL (blueprint Rule 8); the only age input is the brief
        opening-seconds carve-out (Problem 3.4) that uses the wider
        ``spike_atr_move_mult_opening`` for the first
        ``spike_young_opening_seconds`` so a young trade's settling wiggle is not
        misread as a crash, then reverts to ``spike_atr_move_mult``. Returns
        ``(triggered, adverse_move, atr_value, mult_used)``. Anchored to the
        in-buffer window only — when the buffer is still filling (a brand-new
        position) there may be too few points to detect a spike; the -3%
        watchdog hard stop remains the always-on backstop for that window.
        """
        buf = tracked.get("buffer")
        if buf is None or current_price <= 0:
            # 4-tuple to match the declared signature and the caller's unpack
            # (_spk, _adv, _spk_atr, _spk_mult). A 3-tuple here raised
            # "ValueError: not enough values to unpack" inside _pf_apply_spine
            # for a buffer-less position (cache-miss / freshly-detected
            # externally-opened trade) — Pass-3 runtime audit. Behaviour-
            # preserving: the 4th value is only read when triggered is True.
            return (False, 0.0, 0.0, 0.0)
        live_atr = await self._get_current_atr(symbol)
        atr_value, _src = self._pf_effective_atr(
            live_atr, getattr(state, "atr_at_entry", 0.0), current_price,
        )
        if atr_value <= 0:
            return (False, 0.0, atr_value, 0.0)  # 4-tuple — match caller unpack
        now = time.time()
        try:
            prices = buf.get_prices() or []
            stamps = buf.get_timestamps() or []
        except Exception:
            prices, stamps = [], []
        window = float(self._lc.spike_window_seconds)
        recent = [
            p for (p, t) in zip(prices, stamps)
            if p > 0 and (now - float(t)) <= window
        ]
        recent.append(current_price)
        if len(recent) < 2:
            return (False, 0.0, atr_value, 0.0)   # buffer still filling (4-tuple)
        if is_long:
            adverse = max(recent) - current_price       # drop from recent high
        else:
            adverse = current_price - min(recent)        # rise against the short
        # PF/LC Top-15 Problem 3.4 — opening-seconds carve-out: a very young
        # trade needs the wider multiple so a settling wiggle is not read as a
        # crash; afterwards it uses the normal multiple. Genuine crashes (>= the
        # opening multiple in ATR) still fire at any age.
        _age_s = float(getattr(state, "age_seconds", 0.0) or 0.0)
        _mult = (
            float(self._lc.spike_atr_move_mult_opening)
            if _age_s < float(self._lc.spike_young_opening_seconds)
            else float(self._lc.spike_atr_move_mult)
        )
        triggered = adverse >= _mult * atr_value
        return (triggered, adverse, atr_value, _mult)

    async def _lc_structure_stop(
        self, symbol: str, state: Any, is_long: bool, current_price: float,
        buffer_atr: float,
    ) -> float | None:
        """Structure-based stop just beyond the X-RAY invalidation level (Tech 3).

        Reads the shared StructureCache (per-symbol, TTL-bounded). For a long the
        stop sits just BELOW the invalidation level (the swing low the uptrend's
        higher-low pattern depends on); for a short, just ABOVE it. The buffer
        (in ATR units) shrinks with age via the loss dial. Fail-safe: returns
        None on a cache miss, no invalidation level, or a wrong-side level — so
        the ATR/cap candidates still protect and a stale/wrong-side structure
        stop is never placed. The spine's tightest-wins selection and the
        gateway's tighten-only / min-distance rules govern whether it is written.
        """
        try:
            sa = self.structure_cache.get(symbol)
        except Exception:
            sa = None
        if sa is None:
            return None
        ms = getattr(sa, "market_structure", None)
        inv = float(getattr(ms, "invalidation_level", 0.0) or 0.0) if ms else 0.0
        if inv <= 0.0:
            return None
        live_atr = await self._get_current_atr(symbol)
        atr_value, _src = self._pf_effective_atr(
            live_atr, getattr(state, "atr_at_entry", 0.0), current_price,
        )
        buf = max(0.0, buffer_atr) * atr_value
        if is_long:
            stop = round(inv - buf, 8)
            if stop <= 0.0 or (current_price > 0 and stop >= current_price):
                return None
        else:
            stop = round(inv + buf, 8)
            if stop <= 0.0 or (current_price > 0 and stop <= current_price):
                return None
        return stop

    async def _lc_stall_decision(
        self, symbol: str, pos: Any, tracked: dict, state: Any,
        pnl_pct: float, is_long: bool, age_fraction: float,
        stall_min_age_fraction: float,
    ) -> bool:
        """Time-based stall-exit with the signs-of-life veto (Technique 2).

        Cuts a dead/bleeding non-climber on time AND a lack of positive
        behaviour, but SPARES a slightly-building late-bloomer (blueprint 2.2,
        Rule 9) — the veto is what stops this becoming the new over-tightening.
        Impatience is driven by the loss time dial (the trade must be past the
        dialed ``stall_min_age_fraction`` of its deadline). It honours the 5-min
        settling contract (check_min_hold=True) and yields to the existing
        stall-escape valve, a fresh "stable" struct-guard verdict, and the
        watchdog's very-late timeout, so it never double-cuts. The signs-of-life
        tracking is updated every tick (even when not stalled). Returns True iff
        it force-closed the position.
        """
        # ── Always update the signs-of-life tracking ──
        _hist = tracked.setdefault("_lc_peak_hist", [])
        _hist.append(state.peak_pnl_pct)
        _look = max(1, int(self._lc.stall_signs_of_life_lookback_ticks))
        if len(_hist) > _look:
            del _hist[: len(_hist) - _look]
        # PF/LC Top-15 Problems 2.1 / 2.2 — a windowed recent-PnL history over
        # the same lookback as the peak history. 2.1 reads it for a WINDOWED
        # in-profit ratio (instead of the stale cumulative one); 2.2 reads it for
        # SUSTAINED (not single-tick) improvement. Maintained every tick
        # alongside the peak history so the window is always current.
        _pnl_hist = tracked.setdefault("_lc_pnl_hist", [])
        _pnl_hist.append(pnl_pct)
        if len(_pnl_hist) > _look:
            del _pnl_hist[: len(_pnl_hist) - _look]
        _pnl_prev = tracked.get("_lc_pnl_prev")
        tracked["_lc_pnl_prev"] = pnl_pct

        if not self._lc.enable_stall_exit:
            return False
        if pnl_pct >= 0:                      # loss side only
            return False
        # Dynamic Adaptive Exit (2026-06-15) — dead-drifter scratch. A trade
        # whose LIFETIME peak never reached 1R (the coin made no real progress)
        # and that is past dead_drifter_age_fraction of its deadline is declared
        # dead and becomes stall-eligible EARLIER than the (young-dialed) stall
        # age, so capital is not tied up to the deadline on a flat drifter (the
        # proven LINKUSDT trap, forensic F4/G2). Conservative: it only LOWERS the
        # stall age for a proven-dead drifter; the signs-of-life veto below STILL
        # runs, so a building late-bloomer is never scratched. It rides the
        # existing stall close path, not a new gate. Off unless adaptive_exit and
        # dead_drifter_enabled are both on (then legacy behaviour is unchanged).
        _eff_stall_age = stall_min_age_fraction
        _dd_scratch = False
        _ae_dd = getattr(getattr(self, "settings", None), "adaptive_exit", None)
        if (
            _ae_dd is not None and getattr(_ae_dd, "enabled", False)
            and getattr(_ae_dd, "dead_drifter_enabled", False)
            and state.entry_price > 0
        ):
            _dd_age = float(getattr(_ae_dd, "dead_drifter_age_fraction", 0.70))
            # Cheap pre-gate: a trade too young to be scratch-eligible can never
            # be a dead drifter (the age gate below would reject it anyway), so
            # skip the awaited ATR fetch entirely until it is old enough.
            if age_fraction >= _dd_age:
                try:
                    _atr_dd = await self._get_current_atr(symbol)
                except Exception:
                    _atr_dd = 0.0
                _R_dd = (_atr_dd / state.entry_price * 100.0) if _atr_dd and _atr_dd > 0 else 0.0
                if _R_dd > 0:
                    _one_r = float(getattr(_ae_dd, "dead_drifter_min_move_r", 1.0)) * _R_dd
                    if state.peak_pnl_pct < _one_r:
                        _eff_stall_age = min(stall_min_age_fraction, _dd_age)
                        _dd_scratch = age_fraction < self._lc.stall_tail_yield_fraction
        if age_fraction < _eff_stall_age:     # too young to stall-cut
            return False
        if age_fraction >= self._lc.stall_tail_yield_fraction:
            return False                      # the watchdog timeout owns the tail

        # ── Signs of life — any one spares the trade (the late-bloomer veto) ──
        # PF/LC Top-15 Problem 2.1 — the "building" veto uses a WINDOWED recent
        # in-profit ratio (last lookback ticks) when enabled, so stale early
        # profit no longer spares a trade that has since turned bad; otherwise it
        # keeps the cumulative lifetime ratio. Same threshold either way.
        if self._lc.stall_veto_windowed_profit_ratio_enabled and _pnl_hist:
            _win_ratio = sum(1 for _p in _pnl_hist if _p > 0) / len(_pnl_hist)
        else:
            _win_ratio = state.profit_ratio
        _building = _win_ratio >= self._lc.stall_signs_of_life_profit_ratio
        # PF/LC Top-15 Problem 2.2 — sustained vs single-tick improvement. When
        # enabled, the reprieve fires only if the current PnL has risen at least
        # the floor above the lowest of the last N ticks (a genuine recovery from
        # a recent low), so a single noise up-tick no longer rescues a dying
        # trade. _pnl_hist already includes the current tick, so the comparison
        # window is the N ticks BEFORE it.
        if self._lc.stall_signs_of_life_sustained_improving_enabled:
            _imp_n = max(1, int(self._lc.stall_signs_of_life_improving_lookback_ticks))
            _imp_floor = float(self._lc.stall_signs_of_life_improving_floor_bps) / 100.0
            _prior = _pnl_hist[-(_imp_n + 1):-1]
            _improving = (
                len(_prior) >= _imp_n
                and pnl_pct >= min(_prior) + _imp_floor
            )
        else:
            _improving = _pnl_prev is not None and pnl_pct > _pnl_prev
        _peak_rise = (
            len(_hist) >= 2
            and (_hist[-1] - _hist[0])
            >= self._lc.stall_signs_of_life_peak_improve_pct
        )
        if _building or _improving or _peak_rise:
            _now = time.time()
            if _now - tracked.get("_lc_veto_log_ts", 0.0) >= 60.0:
                tracked["_lc_veto_log_ts"] = _now
                # Finding 2 (2026-06-02): count distinct ~per-minute sparings so
                # the veto leniency (and its interaction with the recovery trail)
                # is observable. The count rides the per-position tracked dict,
                # which is replaced on each new position, so it needs no cleanup.
                _vcount = tracked.get("_lc_veto_count", 0) + 1
                tracked["_lc_veto_count"] = _vcount
                log.info(
                    f"LOSS_STALL_VETO | sym={symbol} pnl_pct={pnl_pct:.3f} "
                    f"age_frac={age_fraction:.3f} profit_ratio={state.profit_ratio:.2f} "
                    f"win_ratio={_win_ratio:.2f} "
                    f"windowed={self._lc.stall_veto_windowed_profit_ratio_enabled} "
                    f"building={_building} improving={_improving} "
                    f"peak_rise={_peak_rise} veto_count={_vcount} | spared a "
                    f"building late-bloomer | {ctx()}"
                )
                # One-shot flag when a single position has been spared past the
                # watch budget — surfaces a notably-lenient sparing live so the
                # interaction lever can be tuned. Observability ONLY: it does not
                # force a cut, so the late-bloomer protection (Rule 9) is intact.
                _budget = self._lc.stall_veto_budget_warn
                if _budget > 0 and _vcount == _budget:
                    log.warning(
                        f"LOSS_STALL_VETO_BUDGET | sym={symbol} veto_count={_vcount} "
                        f"budget={_budget} pnl_pct={pnl_pct:.3f} "
                        f"age_frac={age_fraction:.3f} profit_ratio={state.profit_ratio:.2f} "
                        f"| spared past the watch budget — leniency candidate "
                        f"(no cut forced) | {ctx()}"
                    )
            return False

        # ── Yield to the existing stall-escape valve (same tick) ──
        _la = tracked.get("last_action")
        if _la is not None and getattr(_la, "source", "") == "stall_escape":
            return False

        # ── Defer to a fresh "stable" struct-guard verdict (T2-9 contract) ──
        l4p = getattr(self, "layer4_protection", None)
        if l4p is not None and hasattr(l4p, "get_struct_guard_verdict"):
            try:
                _v, _age_s = l4p.get_struct_guard_verdict(symbol)
            except Exception:
                _v, _age_s = ("", 0.0)
            if _v == "stable" and _age_s < 60.0:
                return False

        # ── Cut the dead/bleeding non-climber ──
        if _dd_scratch:
            log.info(
                f"DEAD_DRIFTER_SCRATCH | sym={symbol} pnl_pct={pnl_pct:.3f} "
                f"peak_pnl_pct={state.peak_pnl_pct:.3f} age_frac={age_fraction:.3f} "
                f"| lifetime peak never reached 1R and past the scratch age; the "
                f"signs-of-life veto did not fire — scratching the dead drifter "
                f"early to free capital | {ctx()}"
            )
        log.warning(
            f"LOSS_STALL_EXIT | sym={symbol} pnl_pct={pnl_pct:.3f} "
            f"age_frac={age_fraction:.3f} profit_ratio={state.profit_ratio:.2f} "
            f"peak_pnl_pct={state.peak_pnl_pct:.3f} | no signs of life past the "
            f"stall age — cutting (signs-of-life veto did not fire) | {ctx()}"
        )
        return await self._execute_full_close(
            symbol, pos,
            {"pnl_pct": pnl_pct, "exploit_score": 0},
            closed_by="loss_stall",
            check_min_hold=True,
        )

    async def _pf_apply_spine(
        self, symbol: str, pos: Any, tracked: dict, current_price: float,
    ) -> bool:
        """Apply the highest-stop-wins selection through the gateway (Phase 4).

        The single, auditable per-tick step that reconciles the ladder and the
        Chandelier trail: select the tightest candidate (``_pf_select_stop``),
        log which one won, and write it once through the SL gateway. The ladder
        source bypasses R3 (max-step) only; R1 tighten-only, R2 min-distance
        and R4 rate-limit still apply. Returns True if a stop was written.
        """
        trail = tracked.get("last_trail")
        ladder = tracked.get("last_ladder")
        current_sl = self._get_current_sl(pos)
        _state = self._profit_states.get(symbol)
        _side = getattr(pos, "side", None)
        is_long = (_side == Side.BUY) or (getattr(_side, "value", "") == "Buy")
        if _state is not None:
            is_long = (_state.direction == "Buy")

        # ── Phase 6: safety stop / naked-position floor ──
        # Always a candidate when the position is naked (no exchange stop), so
        # the confirmed no-naked gap is filled. When safety_floor_reassert is
        # on, it is also a candidate on a position whose stop is LOOSER than the
        # floor, re-asserting the loss cap (tighten-only via the gateway).
        _entry = (
            _state.entry_price if _state is not None
            else float(getattr(pos, "entry_price", 0.0) or 0.0)
        )
        _is_naked = current_sl <= 0
        _safety_stop = None
        if _entry > 0 and (_is_naked or self._pf.safety_floor_reassert):
            _safety_stop = self._pf_safety_floor(
                _entry, is_long, self._pf.safety_stop_pct,
            )
            # Naked-underwater guard (blueprint Part 5 — catch a trade that
            # broke the opposite way). If the position has NO stop and price
            # has already moved past the entry-based floor, that floor sits on
            # the wrong side of price and the exchange would reject it, leaving
            # the position naked. Clamp to a valid just-inside-price emergency
            # cap so a stop actually attaches; the -3% hard stop remains the
            # outer backstop. Only when naked and we have a live price.
            if _is_naked and current_price > 0 and _safety_stop > 0:
                _buf = self._pf.atr_zero_fallback_pct / 100.0
                if is_long and _safety_stop >= current_price:
                    _safety_stop = round(current_price * (1.0 - _buf), 8)
                elif (not is_long) and _safety_stop <= current_price:
                    _safety_stop = round(current_price * (1.0 + _buf), 8)

        # ── Loss-Cutting System: the loss-side authority block ──
        # Authority split (operator directive): while the position has NOT
        # graduated to the profit side — its peak PnL never reached the ladder
        # arm threshold, a MONOTONIC high-water latch that cannot flap around
        # zero — the loss system owns it. On the loss side it contributes its
        # cut/close decisions (the spike catastrophe stop, the sacred-cap
        # force-close, the stall-exit) and its tighten-only SL candidates (the
        # cap, the structure stop, the final-phase recovery). Once peak crosses
        # the arm threshold the profit-fetching system owns the position.
        _loss_candidates: list[tuple[str, float, str]] = []
        if self._lc.enabled and _state is not None and _entry > 0:
            _arm = self._pf.min_profit_to_arm_ladder_pct
            _graduated = _state.peak_pnl_pct >= _arm
            if _graduated and not tracked.get("_lc_graduated_logged"):
                # Observe the one-way graduation handoff once per position.
                tracked["_lc_graduated_logged"] = True
                log.info(
                    f"GRADUATION_LATCH | sym={symbol} "
                    f"peak_pnl_pct={_state.peak_pnl_pct:.3f} arm={_arm} | profit-"
                    f"side authority latched — loss-cutting yields to the profit "
                    f"system for this trade's life | {ctx()}"
                )
            # Issue 2.5 (2026-06-07): re-arm the loss-cutting block when a
            # graduated trade CRATERS into a real loss (one-shot per position).
            # The graduation latch is otherwise one-way, so a winner that fully
            # evaporated lost the tightening cap; this restores it. A trade that
            # keeps climbing never craters, so a genuine winner is never cut.
            # Default-OFF flag (graduation_crater_rearm_enabled).
            if (
                _graduated
                and self._lc.graduation_crater_rearm_enabled
                and not tracked.get("_lc_crater_rearmed")
            ):
                _crater_pnl = self._calculate_pnl_pct(pos, current_price)
                if _crater_pnl <= -float(self._lc.graduation_crater_loss_pct):
                    tracked["_lc_crater_rearmed"] = True
                    log.warning(
                        f"GRADUATION_CRATER_REARM | sym={symbol} "
                        f"peak_pnl_pct={_state.peak_pnl_pct:.3f} "
                        f"cur_pnl_pct={_crater_pnl:.3f} "
                        f"loss_thresh={self._lc.graduation_crater_loss_pct} | "
                        f"graduated trade cratered — loss-cutting re-armed "
                        f"(one-shot) | {ctx()}"
                    )
            # (0) Volatility-spike catastrophe stop (Technique 5) — ALWAYS-ON.
            # PF/LC Top-15 Problem 1.2: this used to live inside `if not
            # _graduated`, so once a trade graduated it lost crash protection,
            # violating the blueprint rule (Loss 2.5 / Rule 8) that the spike
            # overrides everything at ANY age. It is now evaluated BEFORE the
            # graduation branch, so a graduated winner that suddenly crashes into
            # loss is still cut by the catastrophe exit. Deliberately OUTSIDE the
            # time dial; age-independent (check_min_hold=False). The rest of the
            # loss block below stays graduation-gated — the profit side still owns
            # a graduated trade's normal management. Coordinates with Problem 3.4
            # (the opening-seconds carve-out, inside _lc_spike_triggered, that
            # keeps it from over-firing on a very young low-vol trade).
            if self._lc.enable_spike_stop:
                _spike_pnl_pct = self._calculate_pnl_pct(pos, current_price)
                if _spike_pnl_pct < 0:
                    _spk, _adv, _spk_atr, _spk_mult = await self._lc_spike_triggered(
                        symbol, tracked, _state, current_price, is_long,
                    )
                    if _spk:
                        _spk_age_min, _ = self._pf_age_and_deadline(symbol)
                        log.warning(
                            f"LOSS_SPIKE_STOP | sym={symbol} adverse_move={_adv:.8f} "
                            f"atr={_spk_atr:.8f} mult={_spk_mult:.2f} "
                            f"window_s={self._lc.spike_window_seconds} "
                            f"pnl_pct={_spike_pnl_pct:.3f} age_min={_spk_age_min:.1f} "
                            f"graduated={'Y' if _graduated else 'N'} | violent adverse "
                            f"move — closing (catastrophe, age- and graduation-"
                            f"independent) | {ctx()}"
                        )
                        return await self._execute_full_close(
                            symbol, pos,
                            {"pnl_pct": _spike_pnl_pct, "exploit_score": 0},
                            closed_by="loss_spike_force",
                            check_min_hold=False,
                        )
            if not _graduated or tracked.get("_lc_crater_rearmed"):
                _size = abs(float(getattr(pos, "size", 0.0) or 0.0))
                _notional = _size * _entry
                _age_min, _deadline_min = self._pf_age_and_deadline(symbol)
                _ld = self._loss_dial.resolve_loss(_age_min, _deadline_min)
                _pnl_pct = self._calculate_pnl_pct(pos, current_price)
                _loss_usd = (
                    (-_pnl_pct / 100.0) * _notional if _pnl_pct < 0 else 0.0
                )
                # Layer B (Phase 1 owner switch): when the owner switch is
                # ENFORCING, the loss-side structure candidate is offered only
                # on a red trade (pnl < 0), so the highest-stop-wins spine cannot
                # select a loss stop on a green trade and starve the rightful
                # profit write (the gateway would defer that loss stop, leaving
                # the green trade unwritten that tick). Dormant in log-only mode
                # (state_enforcement_active is False) — selection is unchanged
                # until enforcement is turned on at the gate. The sacred cap
                # candidate is left in both states as the Head floor (it sits at
                # catastrophic distance, so it never wins a green spine anyway).
                # NOTE: this raw-pnl construction filter is a cheap EARLY PRUNE;
                # the AUTHORITATIVE suppressor is the spine's offer_loss gate
                # below, which uses gateway.peek_owner (deadband + faded-winner +
                # hysteresis) and matches the gateway gate exactly. The two are
                # intentionally not identical (pnl<0 vs owner==green) but both
                # err safe — on a faded winner the construction filter may build
                # the structure candidate, then offer_loss suppresses it.
                _state_enforce = (
                    self.sl_gateway is not None
                    and getattr(
                        self.sl_gateway, "state_enforcement_active", False,
                    )
                )
                # (1) The sacred cap — the inviolable force-close on a genuine
                # breach (the outer wall, holds even where the cap SL is
                # un-placeable inside min-distance). check_min_hold=False so the
                # cap holds at any age (Rule 7).
                # Finding N: the force-close threshold AND the cap SL both use
                # this net-aware budget (cap - round-trip fee) so the realized
                # NET loss is bounded by the ceiling, not overshot by fees.
                _cap_dollars = (
                    self._lc_net_cap_dollars(
                        min(
                            self._lc.cap_dollar_ceiling,
                            _notional * _ld.cap_pct / 100.0,
                        ),
                        _notional,
                    )
                    if (self._lc.enable_hard_cap and _size > 0 and _notional > 0)
                    else 0.0
                )
                if (
                    self._lc.enable_hard_cap
                    and self._lc.force_close_when_cap_unplaceable
                    and _cap_dollars > 0 and _loss_usd >= _cap_dollars
                ):
                    # Finding 5: surface the overshoot trend. When the observed
                    # loss is meaningfully past the cap, a fast move blew through
                    # it between ticks (the market-stop slippage signature) — log
                    # it so a rising trend is caught even though the cap still
                    # force-closes here and holds.
                    _cap_overshoot = _loss_usd - _cap_dollars
                    if _cap_overshoot > _cap_dollars * 0.02:
                        log.warning(
                            f"CAP_SLIPPAGE_OBSERVED | sym={symbol} "
                            f"loss_usd={_loss_usd:.4f} cap_usd={_cap_dollars:.4f} "
                            f"overshoot_usd={_cap_overshoot:.4f} "
                            f"overshoot_pct={(_cap_overshoot / _cap_dollars * 100.0):.2f}% "
                            f"buffer_pct={self._lc.cap_slippage_buffer_pct} "
                            f"age_min={_age_min:.1f} | realized loss past the "
                            f"ceiling — watch the trend | {ctx()}"
                        )
                    log.warning(
                        f"LOSS_CAP_FORCE_CLOSE | sym={symbol} "
                        f"loss_usd={_loss_usd:.4f} cap_usd={_cap_dollars:.4f} "
                        f"pnl_pct={_pnl_pct:.3f} notional={_notional:.2f} "
                        f"cap_pct={_ld.cap_pct:.3f} age_min={_age_min:.1f} | "
                        f"sacred cap reached — closing (inviolable) | {ctx()}"
                    )
                    return await self._execute_full_close(
                        symbol, pos,
                        {"pnl_pct": _pnl_pct, "exploit_score": 0},
                        closed_by="loss_cap_force",
                        check_min_hold=False,
                    )
                # (2) The stall-exit (Technique 2). Runs every tick to update
                # the signs-of-life tracking; force-closes a dead non-climber
                # past the stall age while sparing a building late-bloomer.
                if await self._lc_stall_decision(
                    symbol, pos, tracked, _state, _pnl_pct, is_long,
                    _ld.age_fraction, _ld.stall_min_age_fraction,
                ):
                    return True
                # (3) The cap as a tighten-only SL candidate (placeable on calm
                # coins; the force-close above is the real wall meanwhile).
                if _cap_dollars > 0 and _size > 0:
                    # Finding 5: place the cap SL trigger inside the ceiling by
                    # the slippage buffer so a market-stop's slipped fill still
                    # lands within the cap (the force-close above stays at the
                    # true ceiling).
                    _cap_dist = self._lc_cap_stop_distance(_cap_dollars, _size)
                    _cap_stop = (
                        round(_entry - _cap_dist, 8) if is_long
                        else round(_entry + _cap_dist, 8)
                    )
                    # If the cap SL would sit on the wrong side of live price
                    # (price already through it), use a just-inside-price
                    # emergency cap so a stop still attaches (source flagged so
                    # it bypasses R3 and the rate gate).
                    _cap_src = "loss_cap"
                    if current_price > 0 and (
                        (is_long and _cap_stop >= current_price)
                        or ((not is_long) and _cap_stop <= current_price)
                    ):
                        _buf = self._pf.atr_zero_fallback_pct / 100.0
                        _cap_stop = (
                            round(current_price * (1.0 - _buf), 8) if is_long
                            else round(current_price * (1.0 + _buf), 8)
                        )
                        _cap_src = "loss_cap_emergency"
                    _loss_candidates.append(("cap", _cap_stop, _cap_src))
                # (4) Structure stop (Technique 3) — just beyond the X-RAY
                # invalidation level; the buffer shrinks with age. Fail-safe:
                # skipped on cache miss / no invalidation / wrong-side level, so
                # the ATR/cap candidates still protect (never a wrong-side stop).
                if (
                    self._lc.enable_structure_stop
                    and self.structure_cache is not None
                    and (not _state_enforce or _pnl_pct < 0)
                ):
                    _struct = await self._lc_structure_stop(
                        symbol, _state, is_long, current_price,
                        _ld.structure_buffer_atr,
                    )
                    if _struct is not None:
                        _loss_candidates.append(
                            ("structure", _struct, "loss_structure")
                        )
                # (5) Final-phase history-aware recovery (blueprint 5.3) — in the
                # trade's last minutes, trail the bounce off the trough to
                # capture near the least loss. It competes in the spine; the cap
                # (also a candidate) wins if tighter, so it stays inside the cap.
                if (
                    self._lc.enable_history_recovery
                    and _pnl_pct < 0
                    and _ld.age_fraction >= self._lc.recovery_final_fraction
                ):
                    _rec_stop = await self._lc_recovery_candidate(
                        symbol, tracked, _state, is_long, current_price,
                    )
                    if _rec_stop is not None:
                        _loss_candidates.append(
                            ("recovery", _rec_stop, "loss_recovery")
                        )

        # Compact loss-candidate summary for the spine-select log (cap,
        # structure, and — in later phases — spike / recovery).
        _loss_str = (
            ",".join(f"{n}:{p:.8f}" for (n, p, _s) in _loss_candidates)
            if _loss_candidates else "na"
        )

        # Phase 3/4 — ask the gateway who owns the stop right now so the spine
        # offers only the owning engine's candidates. "unknown" (switch off,
        # log-only, or no entry) leaves every candidate competing, so behavior
        # is unchanged until enforcement is turned on. Under enforcement a
        # red-owned trade does not offer the profit tools (Phase 3) and a
        # green-owned trade has already had its loss candidates filtered at
        # construction (Phase 1/4). _entry, is_long, current_price are all in
        # scope here (assigned at the top of the spine).
        _sp_enforce = (
            self.sl_gateway is not None
            and getattr(self.sl_gateway, "state_enforcement_active", False)
        )
        _sp_owner = (
            self.sl_gateway.peek_owner(symbol, is_long, _entry, current_price)
            if (_sp_enforce and self.sl_gateway is not None) else "unknown"
        )
        _offer_profit = not (_sp_enforce and _sp_owner == "red")
        _offer_loss = not (_sp_enforce and _sp_owner == "green")
        winner = self._pf_select_stop(
            trail, ladder, current_sl, is_long, safety_stop=_safety_stop,
            loss_candidates=_loss_candidates,
            offer_profit=_offer_profit,
            offer_loss=_offer_loss,
        )
        if winner is None:
            return False
        name, new_sl, source = winner

        # Urgent protective writes — the naked fix, the just-inside cap emergency
        # tighten, and (Phase 6) the spike stop — must NOT be starved by the 30s
        # rate-limit window on a fast-falling position. They skip the eligibility
        # short-circuit and pass bypass_rate_limit=True below. R1 tighten-only and
        # R2 min-distance still apply, so none can loosen or strangle.
        _naked_fix = (name == "safety" and _is_naked)
        # Item 2.2 / O6 / F12 — breakeven-floor arming tick. The first time the
        # zero-crossing floor wins the spine, let it JUMP immediately by joining
        # the urgent lane: it skips the rate-limit short-circuit below and passes
        # bypass_rate_limit so the floor lands in one move instead of waiting up
        # to 30s for the next eligible write (the lag that round-tripped modest-
        # peak faders, Finding 12). One-shot per position via the tracked flag
        # (the per-position dict is replaced on each new position, so no cleanup
        # is needed); gated by the jump-on-arm switch AND a live floor
        # (ladder.breakeven_floor is only True when ladder_breakeven_lock_pct > 0).
        # R1 tighten-only and R2 min-distance still apply, so it can never loosen
        # or sit on noise — it only removes the rate-limit wait on the first lock.
        _be_floor_arming = (
            self._pf.ladder_floor_jump_on_arm
            and name == "ladder"
            and ladder is not None
            and getattr(ladder, "breakeven_floor", False)
            and not tracked.get("_be_floor_jumped", False)
        )
        # F6 (2026-06-09) — first step-rung lock jump. The _be_floor_arming jump
        # above covers ONLY the zero-crossing breakeven floor. On a FAST young pop
        # the price clears the first real step rung before the breakeven floor
        # ever gets its turn, so the FIRST step-rung lock (a real guaranteed-profit
        # lock — breakeven_floor False, lock_pct > 0) is the one delayed up to the
        # 30s rate-limit window, and a fast pop can fade back through that gap
        # before the lock is written (the choppy-capture collapse). Let that first
        # step-rung lock join the urgent lane too: one-shot per position via its
        # own _first_step_lock_jumped flag, gated by the default-OFF switch. It is
        # mutually exclusive with _be_floor_arming on any tick (that requires
        # breakeven_floor True, this requires it False), and uses a separate flag,
        # so the two one-shots never interfere. R1 tighten-only and R2 min-distance
        # still apply below, so it only removes the rate-limit wait on the first
        # real lock — it can never loosen a stop or place one on noise.
        _first_step_arming = (
            getattr(self._pf, "ladder_first_lock_jump_enabled", False)
            and name == "ladder"
            and ladder is not None
            and ladder.should_apply
            and not getattr(ladder, "breakeven_floor", False)
            and getattr(ladder, "lock_pct", 0.0) > 0.0
            and not tracked.get("_first_step_lock_jumped", False)
        )
        _urgent_source = (
            _naked_fix or _be_floor_arming or _first_step_arming
            or source in ("loss_cap_emergency",)
        )

        # Rate-limit-aware short-circuit (avoids reject spam; blueprint says the
        # 30s gateway window naturally spaces the per-tick ratchet). Skipped for
        # the urgent protective lane.
        if self.sl_gateway is not None and not _urgent_source:
            try:
                # Pass the winner's source so the profit-lock lane uses its
                # (default-inert) faster window; every other source keeps 30s.
                if self.sl_gateway.next_eligible_in_seconds(symbol, source=source) > 0.0:
                    return False
            except Exception as _e:
                # PF/LC Top-15 Problem 1.5 — do not silently swallow a thrown
                # rate-limit eligibility check. The fall-through to the gateway
                # write below is still R4-validated (not capital-at-risk), so
                # behaviour is unchanged, but a persistently-throwing accessor
                # would otherwise be completely invisible. Log it at most once a
                # minute per position so a real gateway-state bug is greppable
                # without spamming (the per-position dict is replaced on each new
                # position, so no cleanup is needed).
                _now = time.time()
                if _now - tracked.get("_lc_ratelimit_err_ts", 0.0) >= 60.0:
                    tracked["_lc_ratelimit_err_ts"] = _now
                    log.warning(
                        f"SNIPER_RATELIMIT_CHECK_ERROR | sym={symbol} "
                        f"err='{type(_e).__name__}: {str(_e)[:100]}' | rate-limit "
                        f"eligibility check threw; proceeding to the R4-validated "
                        f"gateway write | {ctx()}"
                    )

        _ladder_str = (
            f"{ladder.ladder_stop_price:.8f}"
            if (ladder is not None and ladder.should_apply) else "na"
        )
        _trail_str = (
            f"{trail.trail_stop_price:.8f}"
            if (trail is not None and trail.should_apply) else "na"
        )
        _prev_str = f"{current_sl:.8f}" if current_sl > 0 else "unknown"
        # Item 2.5 (O4) — append age observability to the spine-select log so
        # age-windowed chandelier-vs-ladder tuning is possible next round. The
        # fields are recomputed LOCALLY here via _pf_age_and_deadline + the
        # profit time dial: the loss-authority block's _age_min/_dialed are bound
        # only on the non-graduated path, so they are UNBOUND for a graduated
        # winner and the verbatim variables would NameError on every graduated
        # winner. The dial's DialedParams already carry the clamped age fraction
        # and age in minutes. Pure observability — zero PnL risk.
        _obs_age_min, _obs_deadline_min = self._pf_age_and_deadline(symbol)
        _obs_dial = self._time_dial.resolve(_obs_age_min, _obs_deadline_min)
        log.info(
            f"SNIPER_SPINE_SELECT | sym={symbol} winner={name} "
            f"new_sl={new_sl:.8f} cur_sl={_prev_str} "
            f"ladder={_ladder_str} chandelier={_trail_str} "
            f"safety={('%.8f' % _safety_stop) if _safety_stop else 'na'} "
            f"loss=[{_loss_str}] "
            f"age_min={_obs_dial.age_minutes:.2f} "
            f"age_frac={_obs_dial.age_fraction:.3f} "
            f"owner={_sp_owner} offer_profit={_offer_profit} "
            f"offer_loss={_offer_loss} "
            f"dir={'Buy' if is_long else 'Sell'} | {ctx()}"
        )
        direction = "Buy" if is_long else "Sell"
        # The ladder, the safety floor, the loss-cutting candidates, and — as of
        # Finding H (2026-06-08) — the Chandelier runner trail make larger single
        # moves than the R3 max-step cap allows; all are legitimate, monotonic,
        # peak/level-anchored protective tightens that bypass R3 only (R1
        # tighten-only, R2 min-distance, R4 rate-limit still apply). The trail
        # was previously excluded, so on a fast vertical runner it won
        # highest-stop-wins but clamped to 0.25%/tick — the protected floor
        # lagged the peak by up to ~1.4% per write (AAVE: chandelier raw 64.197
        # clamped to 63.298). Its ATR leash is the sole noise guard, so
        # unthrottling the SPEED it reaches (high_water - leash) captures more of
        # a runner without changing the give-back distance or whipsaw tolerance.
        _bypass_r3 = source in (
            "profit_sniper_ladder", "safety_sweeper",
            "loss_cap", "loss_cap_emergency", "loss_atr_initial",
            "loss_structure", "loss_recovery",
            "profit_sniper_trail",
        )
        # PF/LC Top-15 Problem 1.1 — when the armed breakeven floor is the
        # active ladder stop, pass the trade's breakeven (entry) price so the
        # gateway's R2 clamp holds the floor at or above breakeven on high-vol
        # coins instead of rewriting it below. None for every other write, so
        # R2 behaves exactly as before for them. The floor is only the
        # breakeven floor when ladder.breakeven_floor is True (lock at the
        # zero-crossing rung); higher rungs sit well above entry and are
        # unaffected even if a value were passed.
        _be_floor_price = (
            _entry
            if (
                name == "ladder"
                and ladder is not None
                and _entry > 0
                and (
                    getattr(ladder, "breakeven_floor", False)
                    # Dynamic Adaptive Exit FIX (2026-06-15): the R-based adaptive
                    # ladder lock has breakeven_floor=False, yet it ALSO needs a
                    # breakeven floor supplied — so the gateway's fresh-mark degrade
                    # can fall back to breakeven (a placeable, profit-neutral stop)
                    # instead of wire-failing when a fast retrace makes the
                    # +fee-floor lock unplaceable against the live mark. The lock
                    # itself is still passed as profit_lock_floor_price; this only
                    # gives R2/the degrade a breakeven fallback to hold.
                    or (
                        getattr(getattr(getattr(self, "settings", None), "adaptive_exit", None), "enabled", False)
                        and source == "profit_sniper_ladder"
                    )
                )
            )
            else None
        )
        # Dynamic Adaptive Exit (2026-06-15) — when the spine selected the
        # R-based ladder lock, pass it as the profit-lock floor so the gateway's
        # R2 holds it at its value inside the min-distance (the clamp-noop
        # enabler) instead of dropping the lock. Only for the R-based ladder
        # source and only when enabled; None otherwise so R2 is unchanged for
        # every other writer. Gated additionally by r2_profit_lock_floor_enabled
        # in the gateway itself.
        _profit_lock_price = (
            new_sl
            if (
                getattr(getattr(getattr(self, "settings", None), "adaptive_exit", None), "enabled", False)
                and source == "profit_sniper_ladder"
            )
            else None
        )
        if self.sl_gateway is not None:
            self._sl_updates_attempted_window += 1
            result = await self.sl_gateway.apply(
                symbol=symbol,
                new_sl=new_sl,
                source=source,
                direction=direction,
                current_sl=current_sl,
                current_price=current_price,
                entry_price=_entry,
                bypass_step_cap_for_breakeven=_bypass_r3,
                bypass_rate_limit=_urgent_source,
                breakeven_floor_price=_be_floor_price,
                profit_lock_floor_price=_profit_lock_price,
            )
            if not result.accepted:
                if _naked_fix:
                    # Honest observability: the naked-position fix did NOT land
                    # (e.g. gateway/exchange rejected). The -3% hard stop is the
                    # remaining backstop. Logged so a persistently-naked
                    # position is visible rather than silently assumed fixed.
                    log.warning(
                        f"SNIPER_NAKED_FIX_FAILED | sym={symbol} "
                        f"safety_sl={new_sl:.8f} reason={result.reason or 'rejected'} "
                        f"dir={direction} | naked position still has no stop | {ctx()}"
                    )
                return False
            self._sl_updates_accepted_window += 1
            # PF/LC Top-15 Problem 1.4 — report the stop the gateway ACTUALLY
            # wrote after its R2/R3 clamp (result.new_sl_applied), not the
            # pre-gateway target. The raw-target logs are what hid Problem 1.1
            # (an armed floor silently clamped sub-breakeven by R2). Fall back
            # to the target only if the field is somehow missing.
            _na = result.new_sl_applied
            _applied_sl = (
                _na if isinstance(_na, (int, float)) and _na > 0 else new_sl
            )
            _sl_clamped = abs(_applied_sl - new_sl) > 1e-12
            if _be_floor_arming:
                # Item 2.2 — record the one-shot jump and surface it so the
                # operator can confirm the floor lands on the arming tick rather
                # than ratcheting over minutes. Subsequent ladder writes use the
                # normal rate-limited cadence. 1.4: show both the applied stop
                # and the target so an R2 clamp of the floor is visible here.
                tracked["_be_floor_jumped"] = True
                log.info(
                    f"LADDER_FLOOR_JUMP | sym={symbol} applied_sl={_applied_sl:.8f} "
                    f"target_sl={new_sl:.8f} clamped={'Y' if _sl_clamped else 'N'} "
                    f"cur_sl={_prev_str} entry={_entry:.8f} dir={direction} "
                    f"be_lock={self._pf.ladder_breakeven_lock_pct:.3f}% | breakeven "
                    f"floor jumped on the arming tick (R4 rate-limit bypassed "
                    f"once; R1/R2 still enforced) | {ctx()}"
                )
            if _first_step_arming:
                # F6 (2026-06-09) — record the one-shot first step-rung jump and
                # surface it so the operator can confirm the first real profit lock
                # lands on the fast pop instead of waiting up to 30s. Subsequent
                # ladder writes use the normal rate-limited cadence. Shows both the
                # applied stop and the target so an R2 clamp is visible here.
                tracked["_first_step_lock_jumped"] = True
                log.info(
                    f"LADDER_FIRST_LOCK_JUMP | sym={symbol} applied_sl={_applied_sl:.8f} "
                    f"target_sl={new_sl:.8f} clamped={'Y' if _sl_clamped else 'N'} "
                    f"cur_sl={_prev_str} entry={_entry:.8f} dir={direction} "
                    f"lock_pct={getattr(ladder, 'lock_pct', 0.0):.3f}% | first "
                    f"step-rung lock jumped on a fast young pop (R4 rate-limit "
                    f"bypassed once; R1/R2 still enforced) | {ctx()}"
                )
            if self.trade_coordinator is not None:
                plan = self.trade_coordinator.get_trade_plan(symbol)
                if plan is not None:
                    plan.stop_loss_price = _applied_sl
            if _naked_fix:
                # Blueprint Rule 10: a naked position was found and a safety
                # stop actually attached (logged only after the wire accept).
                log.warning(
                    f"SNIPER_NAKED_POSITION_FIXED | sym={symbol} "
                    f"safety_sl={new_sl:.8f} entry={_entry:.8f} dir={direction} "
                    f"pct={self._pf.safety_stop_pct} | attached safety stop to a "
                    f"naked position | {ctx()}"
                )
            log.info(
                f"SL_PROPAGATED | sym={symbol} new={_applied_sl:.8f} "
                f"prev={_prev_str} src={source}"
                + (f" target={new_sl:.8f} clamped=Y" if _sl_clamped else "")
                + f" | {ctx()}"
            )
            return True

        # Legacy fallback (no gateway wired, e.g. unit tests).
        try:
            ok = await self.position_service.set_stop_loss(symbol, new_sl)
        except Exception as e:
            log.warning(
                f"SNIPER_SPINE_SET_SL_FAIL | sym={symbol} new_sl={new_sl:.8f} "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return False
        if ok and self.trade_coordinator is not None:
            plan = self.trade_coordinator.get_trade_plan(symbol)
            if plan is not None:
                plan.stop_loss_price = new_sl
        if _naked_fix:
            if ok:
                log.warning(
                    f"SNIPER_NAKED_POSITION_FIXED | sym={symbol} "
                    f"safety_sl={new_sl:.8f} entry={_entry:.8f} dir={direction} "
                    f"pct={self._pf.safety_stop_pct} | attached safety stop to a "
                    f"naked position | {ctx()}"
                )
            else:
                log.warning(
                    f"SNIPER_NAKED_FIX_FAILED | sym={symbol} safety_sl={new_sl:.8f} "
                    f"reason=service_returned_false dir={direction} | naked "
                    f"position still has no stop | {ctx()}"
                )
        return bool(ok)

    async def _apply_trail_stop(
        self, symbol: str, trail: TrailResult, symbol_price: float,
    ) -> bool:
        """Apply a computed trailing stop via SL Gateway (with fallback).

        Only acts if trail.should_apply is True.
        Logs M4_TRAIL with full factor breakdown on success.
        Returns True if SL was successfully set.

        Layer 3 hardening:
          - Bug 2 guard (inline, defense-in-depth): refuses to push a
            trail SL that would LOOSEN the current Shadow SL. Cheap and
            makes log diagnostics clearer (``M4_TRAIL_SKIP`` distinguishes
            Sniper's pre-check from a gateway reject).
          - Bug 1 sync: on success, mirrors the new SL onto the local
            TradePlan so downstream gates (sl_buffer_ok, etc.) see truth.
          - Bug 4 obs: failures are logged at warning level.

        SL Hierarchy overhaul (2026-04-22):
          - **Profit gate** at the top: trail is *profit protection*, not
            risk management. If the position is losing, skip the entire
            compute. The 5 math models and trail computation run in
            ``_compute_trail_stop`` regardless; this gate only blocks the
            push. (The Phase-9 ``_determine_action`` gate already blocks
            full closes on losing positions; this duplicates the
            discipline at the Phase-8 trail level.)
          - **Gateway routing**: when ``self.sl_gateway`` is injected,
            the wire push + tighten-only + min-distance + max-step +
            rate-limit rules are delegated to the gateway. Legacy body
            preserved as fallback for the ``sl_gateway=None`` case
            (e.g. unit tests without DI).

        Args:
            symbol: Trading pair.
            trail: Computed trail stop from ``_compute_trail_stop``.
            symbol_price: THIS symbol's live market price, re-read from
                its own ring buffer by the M5 caller (not the stale
                function-scope ``current_price`` from the M3/M4 loop).

        Note: when the Profit-Fetching Exit System is enabled, the score-path
        callers in ``_execute_action`` skip this method — the spine
        (``_pf_apply_spine``) owns all per-tick stop-raising, so there is a
        single stop-writer. This method stays directly callable for the legacy
        (disabled) path and for unit tests of its internal mechanics.
        """
        if not trail.should_apply:
            return False

        # Bug 1 guard (defense-in-depth): verify the passed price matches
        # this symbol's own buffer. If they diverge >0.1%, log a warning —
        # caller has regressed to the Bug 1 stale-variable pattern. This
        # does NOT short-circuit; it only flags future regressions.
        _tracked_entry = self._tracked.get(symbol)
        if _tracked_entry is not None:
            _latest_buf = _tracked_entry["buffer"].get_latest()
            if _latest_buf is not None:
                _buf_price = float(_latest_buf.get("price", 0.0) or 0.0)
                if _buf_price > 0:
                    _drift_pct = abs(symbol_price - _buf_price) / _buf_price * 100.0
                    if _drift_pct > 0.1:
                        log.warning(
                            f"SNIPER_PRICE_DRIFT | sym={symbol} "
                            f"received={symbol_price:.8f} buffer={_buf_price:.8f} "
                            f"drift={_drift_pct:.3f}% | {ctx()}"
                        )

        # ── Profit gate (new, Phase 4 of SL Hierarchy overhaul) ──
        # Trail tightens toward current price; on a losing trade, that
        # tightens the stop toward certain execution. Trail exists to
        # PROTECT PROFIT. If we're losing, bail before wasting the push.
        entry = trail.entry_price
        if entry and entry > 0:
            if trail.direction in ("Buy", "Long"):
                pnl_pct = (symbol_price - entry) / entry * 100.0
            else:
                pnl_pct = (entry - symbol_price) / entry * 100.0
            if pnl_pct < 0:
                log.info(
                    f"SNIPER_SKIP | sym={symbol} rsn=not_in_profit "
                    f"pnl={pnl_pct:+.3f}% entry={entry:.8f} "
                    f"mark={symbol_price:.8f} dir={trail.direction} | {ctx()}"
                )
                return False

        # Phase 17 (P1-16) — high-water-mark trailing ratchet guard.
        # The brief observed BASEDUSDT trailing SL move from 0.13604368
        # DOWN to 0.13603484 — for a Buy that's a loosening, which the
        # Bug 2 guard below should catch via cur_sl. But Shadow's
        # current SL had already been clamped to the new lower value by
        # an earlier loose push, so cur_sl <= new_sl appeared "valid".
        # We additionally maintain a per-symbol HWM that cannot be
        # ratcheted backwards — for Buy hwm only goes UP, for Sell only
        # DOWN. The HWM survives across ticks so even a transient
        # cur_sl regression cannot let the trail walk back.
        if _tracked_entry is not None:
            _hwm = _tracked_entry.get("_trail_hwm")
            if _hwm is not None and _hwm > 0:
                if trail.direction in ("Buy", "Long"):
                    if trail.trail_stop_price < _hwm:
                        log.warning(
                            f"TRAIL_RATCHET_CLAMP | sym={symbol} dir=Buy "
                            f"proposed={format_price(trail.trail_stop_price, symbol_price)} "
                            f"clamped={format_price(_hwm, symbol_price)} | {ctx()}"
                        )
                        trail.trail_stop_price = _hwm
                else:
                    if trail.trail_stop_price > _hwm:
                        log.warning(
                            f"TRAIL_RATCHET_CLAMP | sym={symbol} dir=Sell "
                            f"proposed={format_price(trail.trail_stop_price, symbol_price)} "
                            f"clamped={format_price(_hwm, symbol_price)} | {ctx()}"
                        )
                        trail.trail_stop_price = _hwm

        # Phase 20 (Y-19): no-op skip BEFORE Bug 2 guard. Catches micro-
        # noise where the new SL differs from current by less than 1 bp
        # (0.01%). The brief observed 4 consecutive M4_ACT_TIGHTEN logs
        # with identical SL — each wasting a Shadow API call. Threshold
        # is symmetric (covers both Buy and Sell directions).
        cur_sl = trail.current_sl
        if cur_sl is not None and cur_sl > 0:
            _diff_ratio = abs(trail.trail_stop_price - cur_sl) / cur_sl
            if _diff_ratio < 1e-4:
                log.info(
                    f"M4_TIGHTEN_NOOP | sym={symbol} sl_unchanged "
                    f"new={trail.trail_stop_price:.8f} cur={cur_sl:.8f} "
                    f"diff_bps={_diff_ratio * 10000:.3f} | {ctx()}"
                )
                return False

        # Bug 2 guard: refuse to loosen Shadow's current SL.
        if cur_sl is not None and cur_sl > 0:
            if trail.direction in ("Buy", "Long") and trail.trail_stop_price <= cur_sl:
                log.info(
                    "M4_TRAIL_SKIP | sym={sym} new={nsl:.8f} cur={csl:.8f} dir={dr} rsn=not_tighter",
                    sym=symbol, nsl=trail.trail_stop_price, csl=cur_sl, dr=trail.direction,
                )
                return False
            if trail.direction in ("Sell", "Short") and trail.trail_stop_price >= cur_sl:
                log.info(
                    "M4_TRAIL_SKIP | sym={sym} new={nsl:.8f} cur={csl:.8f} dir={dr} rsn=not_tighter",
                    sym=symbol, nsl=trail.trail_stop_price, csl=cur_sl, dr=trail.direction,
                )
                return False

        # ── Phase 3 Checks 2 & 3: max-step cap + min-distance reject ──
        # Fix doc mandate (SL_HIERARCHY_AND_COORDINATION_New.md Phase 3):
        #   Check 2 (SNIPER_CAP): if step > max_step_pct, cap new_sl at
        #     that bound — still tighten, just less aggressively. This is
        #     the RIVERUSDT strangulation fix: trail wanted 2.5% jump,
        #     cap to 0.5% so SL moves incrementally.
        #   Check 3 (SNIPER_TOO_CLOSE): if distance from current_price is
        #     below min_distance_pct, reject entirely — SL too close to
        #     price gets triggered by bid-ask noise.
        # Thresholds pulled from settings.sl_gateway so Sniper stays in
        # lockstep with the gateway's rules (the gateway acts as the
        # safety net if Sniper forgets to cap).
        gw_cfg = getattr(self.settings, "sl_gateway", None)
        max_step_pct = float(getattr(gw_cfg, "max_step_pct", 0.5)) if gw_cfg else 0.5

        # ATR-scaled min_distance (user spec: max(0.05%, atr_5m_pct * 0.5)).
        # Matches the gateway's R2 so Sniper's pre-screen and R2 enforcement
        # never drift. Falls back to legacy static min_distance_pct when the
        # profiler is unwired or returns no useful ATR. Cached 60s inside
        # the profiler, so amortised cost per tick is ~0.
        _vp_atr = 0.0
        _vp_cls: str | None = None
        if self.volatility_profiler is not None:
            try:
                _vp = await self.volatility_profiler.get_profile(symbol)
                if _vp is not None:
                    _vp_atr = float(_vp.atr_pct_5m or 0.0)
                    _vp_cls = _vp.volatility_class
            except Exception as e:
                log.debug(
                    f"SNIPER_VP_FAIL | sym={symbol} err='{str(e)[:80]}' | {ctx()}"
                )

        if gw_cfg is not None and _vp_atr > 0:
            from src.analysis.vol_scale import min_distance_for_class
            min_dist_pct = min_distance_for_class(_vp_atr, _vp_cls, gw_cfg)
        else:
            min_dist_pct = float(getattr(gw_cfg, "min_distance_pct", 0.3)) if gw_cfg else 0.3
        new_sl_candidate = trail.trail_stop_price

        # Check 2 — max-step cap (only when prior SL exists)
        # Rounded to 6 decimals to match gateway's FP-safe boundary check.
        if cur_sl is not None and cur_sl > 0:
            requested_step_pct = round(abs(new_sl_candidate - cur_sl) / cur_sl * 100.0, 6)
            if requested_step_pct > max_step_pct:
                if trail.direction in ("Buy", "Long"):
                    capped = cur_sl * (1.0 + max_step_pct / 100.0)
                else:
                    capped = cur_sl * (1.0 - max_step_pct / 100.0)
                log.info(
                    f"SNIPER_CAP | sym={symbol} requested={requested_step_pct:.3f}% "
                    f"capped={max_step_pct:.3f}% new_sl={capped:.8f} "
                    f"raw_new_sl={new_sl_candidate:.8f} cur_sl={cur_sl:.8f} "
                    f"dir={trail.direction} | {ctx()}"
                )
                new_sl_candidate = capped

        # Check 3 — min-distance reject (FP-safe rounding, see gateway R2)
        dist_pct = round(abs(symbol_price - new_sl_candidate) / symbol_price * 100.0, 6)
        if dist_pct < min_dist_pct:
            log.info(
                f"SNIPER_TOO_CLOSE | sym={symbol} dist={dist_pct:.3f}% "
                f"min={min_dist_pct:.3f}% atr5={_vp_atr:.3f}% cls={_vp_cls or '?'} "
                f"new_sl={new_sl_candidate:.8f} price={symbol_price:.8f} "
                f"dir={trail.direction} | {ctx()}"
            )
            return False

        # ── T1-3 (2026-05-12): Trail floor from CURRENT price ──
        # The from-PEAK floor in _compute_trail_stop (min_trail at lines
        # 1259-1285) protects the trail-distance-from-peak only. As
        # current price oscillates BELOW peak (mean reversion), the
        # distance-from-CURRENT shrinks toward zero independently —
        # gateway R2 (min_distance_for_class) is the only safety net,
        # and on low-vol coins R2's effective min collapses to
        # min_distance_abs_floor_pct=0.05 %, leaving the trail vulnerable
        # to noise stop-outs.
        # Empirical bug (verified 2026-05-12): ARBUSDT and SKRUSDT
        # entered with APEX SL ~0.9 %; trail ratcheted to sl_dist ≈ 0.15
        # %; mean-reversion 0.13 % noise stopped both out for -$2.35 each
        # (-$4.70 total in 70 s). Pattern: trail penalises positions
        # that show transient profit and rewards positions that never
        # go profitable.
        # Fix: distance-from-CURRENT-price floor with ATR scaling. Defaults
        # tighter than gateway R2 (atr_mult=0.75 vs 0.50, abs_floor=0.20 %
        # vs 0.05 %) because the trail is the only ratcheting writer and
        # needs the stricter from-current bound.
        # Behaviour: CLAMP outward (preserve trail intent) — never
        # reject. Subcase: if the clamp would loosen prior cur_sl
        # (violates Bug 2 / R1 tighten-only contract) reject explicitly
        # with action=reject_would_loosen.
        m4_cfg = self.settings.mode4
        floor_atr_mult = float(getattr(
            m4_cfg, "trail_floor_from_price_atr_multiplier", 0.75,
        ))
        floor_min_pct = float(getattr(
            m4_cfg, "trail_floor_from_price_min_pct", 0.20,
        ))
        floor_max_pct = float(getattr(
            m4_cfg, "trail_floor_from_price_max_pct", 1.50,
        ))

        if _vp_atr > 0:
            floor_pct_raw = max(floor_min_pct, _vp_atr * floor_atr_mult)
        else:
            floor_pct_raw = floor_min_pct
        floor_pct = min(floor_pct_raw, floor_max_pct)
        floor_dist_abs = symbol_price * floor_pct / 100.0
        cur_dist_abs = abs(symbol_price - new_sl_candidate)

        if cur_dist_abs < floor_dist_abs:
            proposed_sl = new_sl_candidate
            cur_dist_pct_for_log = round(cur_dist_abs / symbol_price * 100.0, 6)
            if trail.direction in ("Buy", "Long"):
                # Long: SL below price. Clamp = price - floor_dist_abs.
                clamped_sl = symbol_price - floor_dist_abs
                if cur_sl is not None and cur_sl > 0 and clamped_sl <= cur_sl:
                    self._log_trail_floor_clamp(
                        symbol=symbol, proposed=proposed_sl,
                        floor_pct=floor_pct, floor_dist_abs=floor_dist_abs,
                        final=cur_sl, action="reject_would_loosen",
                        atr_pct=_vp_atr, cls=_vp_cls,
                        cur_dist_pct=cur_dist_pct_for_log,
                        symbol_price=symbol_price, direction=trail.direction,
                    )
                    return False
                new_sl_candidate = round(clamped_sl, 8)
            else:
                # Sell/Short: SL above price. Clamp = price + floor_dist_abs.
                clamped_sl = symbol_price + floor_dist_abs
                if cur_sl is not None and cur_sl > 0 and clamped_sl >= cur_sl:
                    self._log_trail_floor_clamp(
                        symbol=symbol, proposed=proposed_sl,
                        floor_pct=floor_pct, floor_dist_abs=floor_dist_abs,
                        final=cur_sl, action="reject_would_loosen",
                        atr_pct=_vp_atr, cls=_vp_cls,
                        cur_dist_pct=cur_dist_pct_for_log,
                        symbol_price=symbol_price, direction=trail.direction,
                    )
                    return False
                new_sl_candidate = round(clamped_sl, 8)
            self._log_trail_floor_clamp(
                symbol=symbol, proposed=proposed_sl,
                floor_pct=floor_pct, floor_dist_abs=floor_dist_abs,
                final=new_sl_candidate, action="clamp",
                atr_pct=_vp_atr, cls=_vp_cls,
                cur_dist_pct=cur_dist_pct_for_log,
                symbol_price=symbol_price, direction=trail.direction,
            )

        # ── CRITICAL-5 fix (2026-05-09) — wrong-side guard ──
        # Both SNIPER_TOO_CLOSE above and the gateway's R2 use absolute
        # distance (`abs(price - new_sl)`) — direction-agnostic. A trail
        # SL on the WRONG side of current_price (above current for Buy,
        # below current for Sell) passes both checks if absolute distance
        # exceeds min_dist_pct. Bybit then rejects with retCode 10001
        # ("StopLoss set for X position should greater/less base_price"),
        # producing the audit's KATUSDT/RENDERUSDT 7-event alert burst
        # in 2.85h. Root cause is in _compute_trail_stop above: as
        # current price retraces past peak (Sell going against you),
        # `peak_price + trail_distance` falls below current_price.
        # Defensive guard here prevents the wire roundtrip and keeps
        # alert volume at 0 even if the trail formula regresses.
        is_long_dir = trail.direction in ("Buy", "Long")
        wrong_side = (
            (is_long_dir and new_sl_candidate >= symbol_price)
            or (not is_long_dir and new_sl_candidate <= symbol_price)
        )
        if wrong_side:
            # T2-10 (2026-05-12) — track consecutive wrong-side trips
            # per symbol. After threshold consecutive trips the
            # watermark is force-refreshed (dropped) so the next tick
            # can re-establish from the current peak. Pre-T2-10 a
            # stuck watermark could keep the trail capped at a stale
            # value while price drifted 100+ bps adversely (4+ retries
            # observed on AAVE 2026-05-12).
            _streak = self._trail_wrong_side_streak.get(symbol, 0) + 1
            self._trail_wrong_side_streak[symbol] = _streak
            log.warning(
                f"SNIPER_WRONG_SIDE_GUARD | sym={symbol} "
                f"new_sl={new_sl_candidate:.8f} price={symbol_price:.8f} "
                f"dir={trail.direction} blocked=true "
                f"streak={_streak} "
                f"reason=trail_stop_on_wrong_side_of_current_price | {ctx()}"
            )
            if _streak >= self._trail_hwm_refresh_after_wrong_side_count:
                # Force-refresh: drop the watermark so next tick can
                # re-establish from the current peak. We also clear
                # the streak so the refresh cycle resets cleanly.
                _tracked_entry = self._tracked.get(symbol)
                _old_hwm = (
                    _tracked_entry.get("_trail_hwm")
                    if _tracked_entry is not None
                    else None
                )
                if _tracked_entry is not None and _old_hwm is not None:
                    _tracked_entry["_trail_hwm"] = None
                self._trail_wrong_side_streak[symbol] = 0
                log.info(
                    f"SNIPER_TRAIL_WATERMARK_REFRESHED | sym={symbol} "
                    f"old_hwm={_old_hwm if _old_hwm is not None else 'none':}"
                    f" reason=wrong_side_repeated count={_streak} "
                    f"new_sl_attempted={new_sl_candidate:.8f} "
                    f"price={symbol_price:.8f} dir={trail.direction} | {ctx()}"
                )
            return False
        # Non-wrong-side path: reset the streak counter so transient
        # blips don't accumulate and trigger an unnecessary refresh.
        if symbol in self._trail_wrong_side_streak:
            self._trail_wrong_side_streak[symbol] = 0

        # ── Gateway delegation (primary path) ──
        if self.sl_gateway is not None:
            # T2-6 (2026-05-12) — rate-limit-aware short-circuit. The
            # sniper trail runs every 5 s tick but the gateway R4
            # window is 30 s by default. Pre-T2-6 every tick that
            # landed in the window got rejected with REASON_RATE_LIMIT
            # — production logs showed 127 rejects in 2 h 42 m
            # (FILUSDT 37, BLURUSDT 23, RENDERUSDT 18, ARBUSDT 17,
            # ENAUSDT 10). Wasted compute + log spam.
            # Now we ask the gateway "when is X next eligible?" first
            # and skip the apply() call entirely when ineligible.
            # SNIPER_RATE_LIMIT_AWARE_SKIP makes the avoided-call
            # pattern visible in production. R4 itself is unchanged
            # (still enforced as the safety net for any caller that
            # forgets this short-circuit).
            try:
                _t2_6_remaining_s = self.sl_gateway.next_eligible_in_seconds(
                    symbol,
                )
            except (AttributeError, Exception) as _e:
                # Defensive: legacy sl_gateway without the new accessor,
                # OR a runtime error. Either way, fall through to the
                # legacy apply() path — gateway R4 will still reject
                # with REASON_RATE_LIMIT if the window is open. The
                # short-circuit is purely an optimisation; the gateway
                # remains the authority.
                _t2_6_remaining_s = 0.0
            if _t2_6_remaining_s > 0.0:
                log.info(
                    f"SNIPER_RATE_LIMIT_AWARE_SKIP | sym={symbol} "
                    f"next_eligible_in_s={_t2_6_remaining_s:.1f} "
                    f"src=profit_sniper_trail | {ctx()}"
                )
                return False
            # Observability G2 — count attempts/accepts for the next
            # SNIPER_TICK heartbeat. Pure counters, no behavioural effect.
            self._sl_updates_attempted_window += 1
            result = await self.sl_gateway.apply(
                symbol=symbol,
                new_sl=new_sl_candidate,
                source="profit_sniper_trail",
                direction=trail.direction,
                current_sl=trail.current_sl,
                current_price=symbol_price,
                entry_price=getattr(
                    self._profit_states.get(symbol), "entry_price", None,
                ),
            )
            if result.accepted:
                self._sl_updates_accepted_window += 1
            if not result.accepted:
                return False
            # Bug 1 sync — only after gateway accept + wire-success.
            # PF/LC Top-15 Problem 1.4 — mirror and log the value the gateway
            # ACTUALLY wrote (result.new_sl_applied) after any R2/R3 clamp, not
            # the pre-gateway candidate, so the plan and the log match the broker.
            _na = result.new_sl_applied
            _applied_sl = (
                _na if isinstance(_na, (int, float)) and _na > 0 else new_sl_candidate
            )
            _sl_clamped = abs(_applied_sl - new_sl_candidate) > 1e-12
            if self.trade_coordinator is not None:
                plan = self.trade_coordinator.get_trade_plan(symbol)
                if plan is not None:
                    plan.stop_loss_price = _applied_sl
            _prev_str = (
                f"{trail.current_sl:.8f}"
                if (trail.current_sl is not None and trail.current_sl > 0)
                else "unknown"
            )
            log.info(
                f"SL_PROPAGATED | sym={symbol} new={_applied_sl:.8f} "
                + (f"target={new_sl_candidate:.8f} clamped=Y " if _sl_clamped else "")
                + f"prev={_prev_str} src=profit_sniper_trail | {ctx()}"
            )
            log.info(
                "M4_TRAIL | sym={sym} new_sl={nsl:.8f} old_sl={osl:.8f} "
                "dist={dist:.8f}({dpct:.2f}%) peak={pk:.8f} | "
                "atr={atr:.8f} rgm_f={rf:.2f} p_decay={pd:.3f} mom_f={mf:.1f} "
                "dir={dr}",
                sym=symbol,
                nsl=new_sl_candidate,
                osl=trail.current_sl,
                dist=trail.trail_distance,
                dpct=trail.trail_distance_pct,
                pk=trail.peak_price,
                atr=trail.atr_used,
                rf=trail.regime_factor,
                pd=trail.profit_decay,
                mf=trail.momentum_factor,
                dr=trail.direction,
            )
            # Phase 17 (P1-16): record the high-water mark on success.
            # Subsequent trail computations cannot ratchet below this.
            if _tracked_entry is not None:
                _tracked_entry["_trail_hwm"] = float(new_sl_candidate)
            return True

        # ── Legacy fallback (sl_gateway=None) ──
        # Preserves the exact pre-gateway behavior verbatim so tests
        # without DI continue to work. Also uses new_sl_candidate so the
        # Sniper-level SNIPER_CAP / SNIPER_TOO_CLOSE checks still apply.
        try:
            success = await self.position_service.set_stop_loss(symbol, new_sl_candidate)
        except Exception as e:
            log.warning(
                "M4_TRAIL_FAIL | sym={sym} new={nsl:.8f} err='{err}'",
                sym=symbol, nsl=new_sl_candidate, err=str(e)[:120],
            )
            return False

        if not success:
            log.warning(
                "M4_TRAIL_FAIL | sym={sym} new={nsl:.8f} rsn=service_returned_false",
                sym=symbol, nsl=new_sl_candidate,
            )
            return False

        # Bug 1 sync: keep local TradePlan in step with Shadow.
        if self.trade_coordinator is not None:
            plan = self.trade_coordinator.get_trade_plan(symbol)
            if plan is not None:
                plan.stop_loss_price = new_sl_candidate

        # Unified SL propagation tag — mirrors PositionWatchdog._push_sl_to_shadow
        # so SL_PROPAGATED counts line up with Shadow's SL modifications even
        # when profit_sniper is the mover (Mode 4 trail).
        _prev_str = (
            f"{trail.current_sl:.8f}"
            if (trail.current_sl is not None and trail.current_sl > 0)
            else "unknown"
        )
        log.info(
            f"SL_PROPAGATED | sym={symbol} new={new_sl_candidate:.8f} "
            f"prev={_prev_str} src=profit_sniper_trail | {ctx()}"
        )
        log.info(
            "M4_TRAIL | sym={sym} new_sl={nsl:.8f} old_sl={osl:.8f} "
            "dist={dist:.8f}({dpct:.2f}%) peak={pk:.8f} | "
            "atr={atr:.8f} rgm_f={rf:.2f} p_decay={pd:.3f} mom_f={mf:.1f} "
            "dir={dr}",
            sym=symbol,
            nsl=new_sl_candidate,
            osl=trail.current_sl,
            dist=trail.trail_distance,
            dpct=trail.trail_distance_pct,
            pk=trail.peak_price,
            atr=trail.atr_used,
            rf=trail.regime_factor,
            pd=trail.profit_decay,
            mf=trail.momentum_factor,
            dr=trail.direction,
        )
        # Phase 17 (P1-16): record HWM in the legacy fallback path too,
        # so a system that ever switches between gateway and direct push
        # still cannot ratchet backwards.
        if _tracked_entry is not None:
            _tracked_entry["_trail_hwm"] = float(new_sl_candidate)
        return True

    # ─── Phase 9: Action Decision Engine ───────────────────────────

    def _determine_action(
        self,
        composite,
        state: PositionProfitState,
        current_pnl: float,
        trail,
    ) -> ActionResult:
        """Determine the action for a position: hold / tighten / partial_close / full_close.

        Combines regime-aware score thresholds (Phase 7) with anti-greed pullback
        backstop. Final action = max(score_action, greed_action) by ACTION_PRIORITY.
        Applies cooldown to prevent action spam.
        """
        symbol = state.symbol
        score = composite.score
        regime_name = composite.regime_used
        thresholds = THRESHOLD_SETS.get(regime_name, THRESHOLD_SETS["balanced"])
        cfg = self.settings.mode4

        # ═══ PROFIT GATE ═══════════════════════════════════════════
        # Mode4 exists to PROTECT PROFIT. When PnL ≤ 0, there is
        # no profit to protect. Loss management belongs to:
        #   - Hard stop loss (set at entry)
        #   - Watchdog brain review (Claude every 30s)
        #   - Enforcer (kills sustained losses)
        # ═══════════════════════════════════════════════════════════
        if current_pnl <= 0:
            return ActionResult(
                action="hold",
                source="profit_gate",
                score_action="hold",
                score_value=round(score, 1),
                regime_used=regime_name,
                threshold_set=thresholds,
                greed_action="hold",
                peak_pnl=round(state.peak_pnl_pct, 2),
                current_pnl=round(current_pnl, 2),
                pullback_pct=0.0,
                greed_rule_triggered="none",
                cooled_down=False,
                original_action="hold",
            )

        # ═══ MINIMUM PROFIT THRESHOLD ═════════════════════════════
        # Even at +0.01% profit, trading fees would consume it.
        # Require meaningful profit before any protective action.
        # ═══════════════════════════════════════════════════════════
        if current_pnl < cfg.min_profit_for_action:
            return ActionResult(
                action="hold",
                source="below_min_profit",
                score_action="hold",
                score_value=round(score, 1),
                regime_used=regime_name,
                threshold_set=thresholds,
                greed_action="hold",
                peak_pnl=round(state.peak_pnl_pct, 2),
                current_pnl=round(current_pnl, 2),
                pullback_pct=0.0,
                greed_rule_triggered="none",
                cooled_down=False,
                original_action="hold",
            )

        # ── Score-based action ──────────────────────────────────────
        if score >= thresholds["full"]:
            score_action = "full_close"
        elif score >= thresholds["partial"]:
            score_action = "partial_close"
        elif score >= thresholds["tighten"]:
            score_action = "tighten"
        else:
            score_action = "hold"

        # ── P9 Close Gate: prevent killing tiny winners ────────────
        # full_close at 0.16% profit (e.g. ADA) protects $1 while TP
        # target is $20+. Downgrade to tighten so SL protects while TP runs.
        _min_close_pnl = getattr(cfg, "min_profit_for_close", 0.50)
        if score_action == "full_close" and current_pnl < _min_close_pnl:
            log.info(
                f"P9_CLOSE_GATE | sym={state.symbol} score={score:.0f} "
                f"pnl={current_pnl:.2f}% (< {_min_close_pnl}%) | "
                f"Close blocked — profit too small, downgrading to tighten"
            )
            score_action = "tighten"

        # ── Phase 4 (Sniper-loop fix) — PROFIT GATE on partials ────
        # The legacy P9_CLOSE_GATE only applied to full_close. With no
        # gate on partials, the score-based partial branch could fire
        # repeatedly on a position that had just gone red, locking in
        # losses (the INJUSDT 21:48 reproduction). The default
        # ``min_profit_for_partial_pct = 0.0`` requires break-even
        # before any partial fires; operators can raise it without code
        # change. The anti-greed pullback backstop (below) still wins
        # when the position was previously in profit and pulled back —
        # that path is independent and intentionally unaffected.
        _min_partial_pnl = float(getattr(cfg, "min_profit_for_partial_pct", 0.0))
        if score_action == "partial_close" and current_pnl < _min_partial_pnl:
            log.info(
                f"M4_GATED | sym={state.symbol} proposed=partial_close "
                f"reason=profit_gate pnl={current_pnl:.2f}% "
                f"min_required={_min_partial_pnl:.2f}% score={score:.0f} | "
                f"Partial blocked — pnl below profit gate"
            )
            score_action = "hold"

        # ── Anti-greed pullback backstop ────────────────────────────
        greed_action = "hold"
        greed_rule = "none"
        peak_pnl = state.peak_pnl_pct
        pullback_abs = peak_pnl - current_pnl
        pullback_pct = (pullback_abs / peak_pnl * 100) if peak_pnl > 0.1 else 0.0
        if cfg.anti_greed_enabled:
            if peak_pnl >= cfg.anti_greed_pullback_75_min_peak and pullback_pct >= 75:
                greed_action = "full_close"
                greed_rule = "75pct"
            elif peak_pnl >= cfg.anti_greed_pullback_60_min_peak and pullback_pct >= 60:
                greed_action = "partial_close"
                greed_rule = "60pct"
            elif peak_pnl >= cfg.anti_greed_pullback_40_min_peak and pullback_pct >= 40:
                greed_action = "tighten"
                greed_rule = "40pct"

        # ── Combine — take the more aggressive action ───────────────
        if ACTION_PRIORITY[greed_action] > ACTION_PRIORITY[score_action]:
            final_action = greed_action
            source = "anti_greed"
        elif ACTION_PRIORITY[score_action] > ACTION_PRIORITY[greed_action]:
            final_action = score_action
            source = "score"
        else:
            final_action = score_action
            source = "both" if greed_action != "hold" else "score"

        original_action = final_action

        # ── Cooldown check ──────────────────────────────────────────
        # Phase 4 (Sniper-loop fix). The legacy gate at this site
        # (``last_type == "partial_close" and elapsed < partial_close_
        # cooldown_seconds``) only blocked the NEXT partial when the
        # IMMEDIATELY-prior action was also a partial. An alternating
        # tighten ↔ partial pattern reset ``last_type`` to "tighten"
        # between partials, defeating the cooldown — see the INJUSDT
        # 21:48 reproduction in dev_notes/phase0_issue_2_sniper_
        # investigation.md (4× partials in 60 s).
        #
        # The new gate is type-agnostic: any M4 action of any type
        # starts a per-position cooldown. ``min_seconds_between_actions``
        # (default 60) bounds the partial cadence regardless of what
        # was executed in between; ``min_seconds_before_close`` (default
        # 180) bounds full_close to give the position time to recover
        # if the score spike was noise. The legacy
        # ``tighten_cooldown_seconds`` / ``partial_close_cooldown_seconds``
        # config keys are still respected as the OUTER bound — we
        # take ``max(...)`` of the new and legacy values so existing
        # operator overrides are honoured.
        now = time.time()
        last_time = self._last_action_time.get(symbol, 0)
        elapsed = now - last_time

        _min_between = max(
            int(getattr(cfg, "min_seconds_between_actions", 60)),
            int(cfg.tighten_cooldown_seconds),
        )
        _min_before_close = max(
            int(getattr(cfg, "min_seconds_before_close", 180)),
            int(cfg.partial_close_cooldown_seconds),
        )

        cooled_down = False
        if final_action == "tighten" and elapsed < cfg.tighten_cooldown_seconds:
            final_action = "hold"
            cooled_down = True
            log.info(
                f"M4_GATED | sym={symbol} proposed=tighten reason=cooldown "
                f"elapsed_since_last={elapsed:.0f}s "
                f"min_required={cfg.tighten_cooldown_seconds}s | {ctx()}"
            )
        elif final_action == "partial_close" and elapsed < _min_between:
            # Type-agnostic partial cooldown: any prior action counts.
            final_action = "tighten"  # Downgrade during cooldown
            cooled_down = True
            log.info(
                f"M4_GATED | sym={symbol} proposed=partial_close reason=cooldown "
                f"elapsed_since_last={elapsed:.0f}s min_required={_min_between}s | "
                f"{ctx()}"
            )
        elif final_action == "full_close" and elapsed < _min_before_close:
            # Phase 4 fix: full_close also gated by per-position cooldown,
            # but ONLY when the source is the score branch — anti-greed
            # backstops bypass intentionally. The score branch's
            # full_close on a position that just took an action is
            # almost certainly noise; let the trail stop work.
            if source != "anti_greed":
                final_action = "tighten"
                cooled_down = True
                log.info(
                    f"M4_GATED | sym={symbol} proposed=full_close reason=cooldown "
                    f"elapsed_since_last={elapsed:.0f}s "
                    f"min_required={_min_before_close}s source={source} | {ctx()}"
                )
        # anti_greed full_close still bypasses cooldown — that pathway
        # protects an already-realised peak from giving back gains and
        # must remain immediate.

        # Sniper partial-close disable (2026-05-26, operator decision).
        # Downgrade any remaining partial_close decision to a tighten BEFORE
        # the M4_DECISION trace so the decision log and cooldown semantics
        # reflect the real action (no phantom partial_close traces). The
        # execution-level gate in _execute_action is the hard stop that also
        # catches the stall-escape override path.
        if final_action == "partial_close" and not getattr(
            cfg, "sniper_partial_close_enabled", False
        ):
            final_action = "tighten"

        # Phase 4 (Sniper-loop fix) — M4_DECISION trace.
        # Emitted once per evaluation regardless of action so operators
        # see WHY each tick chose what it did. Includes the score, the
        # threshold set, the gate verdict, and the cooldown elapsed
        # value — enough to reconstruct the decision offline.
        log.info(
            f"M4_DECISION | sym={symbol} action={final_action} "
            f"score_action={score_action} greed_action={greed_action} "
            f"source={source} score={score:.1f} thresholds={thresholds} "
            f"pnl={current_pnl:.2f}% peak_pnl={peak_pnl:.2f}% "
            f"pullback_pct={pullback_pct:.1f}% greed_rule={greed_rule} "
            f"cooldown_elapsed={elapsed:.0f}s cooled_down={cooled_down} "
            f"regime={regime_name} | {ctx()}"
        )

        return ActionResult(
            action=final_action,
            source=source,
            score_action=score_action,
            score_value=round(score, 1),
            regime_used=regime_name,
            threshold_set=thresholds,
            greed_action=greed_action,
            peak_pnl=round(peak_pnl, 2),
            current_pnl=round(current_pnl, 2),
            pullback_pct=round(pullback_pct, 1),
            greed_rule_triggered=greed_rule,
            cooled_down=cooled_down,
            original_action=original_action,
        )

    async def _execute_action(
        self,
        symbol: str,
        action: ActionResult,
        trail,
        pos,
        symbol_price: float,
    ) -> None:
        """Execute the determined action on a position.

        HOLD     → nothing
        TIGHTEN  → apply Phase 8 trail stop via _apply_trail_stop
        PARTIAL  → close 50% via _execute_partial_close, then tighten trail
        FULL     → close 100% via _execute_full_close

        symbol_price is THIS symbol's most recent price, re-read from its
        own ring buffer by the M5 caller. Must not be the function-scope
        `current_price` from the M3/M4 loop (Bug 1).
        """
        now = time.time()

        if action.action == "hold":
            return

        # Guard: transformer switching — don't touch positions during exchange switch
        transformer = getattr(self, "_transformer", None) or getattr(self, "transformer", None)
        if transformer and getattr(transformer, "is_switching", False):
            log.debug("M4_ACT blocked | sym={sym} reason=transformer_switching", sym=symbol)
            return

        if action.action == "tighten":
            # Always start cooldown timer — even if trail can't apply this tick.
            # If omitted, failed attempts never set the timer so elapsed stays at
            # ~1.7B seconds and the cooldown never fires → tighten every 5s.
            self._last_action_time[symbol] = now
            self._last_action_type[symbol] = "tighten"

            # Profit-Fetching Phase 4: when enabled, the spine owns per-tick
            # stop-raising — skip the legacy score-driven trail write so there
            # is a single stop-writer.
            if trail and trail.should_apply and not self._pf.enabled:
                success = await self._apply_trail_stop(symbol, trail, symbol_price)
                if success:
                    # Phase 4 (P0-7): symbol-magnitude precision instead of
                    # hardcoded :.8f (which makes large-coin output unreadable
                    # and small-coin output coarse-but-aligned with no signal).
                    log.info(
                        "M4_ACT_TIGHTEN | sym={sym} new_sl=${nsl} "
                        "dist={dpct:.2f}% src={src} greed={gr} score={sc:.0f}",
                        sym=symbol,
                        nsl=format_price(trail.trail_stop_price, symbol_price),
                        dpct=trail.trail_distance_pct,
                        src=action.source,
                        gr=action.greed_rule_triggered,
                        sc=action.score_value,
                    )

        elif action.action == "partial_close":
            # Sniper partial-close disable (2026-05-26, operator decision).
            # Single hard chokepoint for ALL partial_close actions — the
            # score path, the greed path, and the stall-escape override all
            # dispatch through here. When disabled, never reduce the
            # position: keep the winner-protecting trail (a no-op on losers
            # via its profit gate) and skip the reduce-only fill, its fee,
            # and the winner-clip entirely.
            if not getattr(self.settings.mode4, "sniper_partial_close_enabled", False):
                self._last_action_time[symbol] = now
                self._last_action_type[symbol] = "tighten"
                _redir = False
                # Profit-Fetching Phase 4: spine owns stop-raising when enabled.
                if trail and trail.should_apply and not self._pf.enabled:
                    _redir = await self._apply_trail_stop(symbol, trail, symbol_price)
                log.info(
                    "SNIPER_PARTIAL_CLOSE_DISABLED | sym={sym} src={src} "
                    "score={sc:.0f} pnl={pnl:+.2f}% redirected_to_tighten={r}",
                    sym=symbol, src=action.source, sc=action.score_value,
                    pnl=action.current_pnl, r=_redir,
                )
                return
            # Phase 4B (session-stability): Shadow's POST /api/reduce now
            # supports true partial close (ShadowPositionService.reduce_position
            # calls the new endpoint and emits REDUCE_FALLBACK on rejection).
            # Try the real reduction first; fall back to the legacy
            # tighten_agg defence-in-depth path only if the partial failed
            # (adapter will already have logged REDUCE_FALLBACK with the
            # upstream reason).
            close_pct = int(
                getattr(self.settings.mode4, "partial_close_pct", 50)
            )
            score_data = {
                "pnl_pct": action.current_pnl,
                "exploit_score": action.score_value,
            }
            partial_ok = await self._execute_partial_close(
                symbol, pos, close_pct, score_data,
            )
            if partial_ok:
                self._last_action_time[symbol] = now
                self._last_action_type[symbol] = "partial_close"
                log.warning(
                    "M4_ACT_PARTIAL | sym={sym} pct={pct}% "
                    "src={src} greed={gr} score={sc:.0f} pnl={pnl:+.2f}%",
                    sym=symbol, pct=close_pct,
                    src=action.source, gr=action.greed_rule_triggered,
                    sc=action.score_value, pnl=action.current_pnl,
                )
            else:
                # The reduction didn't take (Shadow rejected, HTTP error, or
                # the adapter ran out of paths). Legacy tighten_agg fallback
                # remains as a last-resort profit-protection move. The
                # PARTIAL_CLOSE_UNSUPPORTED line is now gated behind this
                # real failure rather than firing on every partial_close
                # attempt (was 20× per stall pre-Phase-4B on MOVRUSDT).
                log.warning(
                    f"PARTIAL_CLOSE_UNSUPPORTED | sym={symbol} "
                    f"fallback=tighten_agg score={action.score_value:.0f} "
                    f"pnl={action.current_pnl:+.2f}% src={action.source} "
                    f"| {ctx()}"
                )
                # Profit-Fetching Phase 4: spine owns stop-raising when enabled.
                if trail and trail.should_apply and not self._pf.enabled:
                    success = await self._apply_trail_stop(
                        symbol, trail, symbol_price,
                    )
                    if success:
                        self._last_action_time[symbol] = now
                        self._last_action_type[symbol] = "partial_close"
                        # Phase 4A de-escalation counter: each downgraded
                        # tighten_agg brings us closer to full_close.
                        _tracked = self._tracked.get(symbol)
                        if _tracked is not None:
                            _tracked["_stall_tighten_applications"] = (
                                int(_tracked.get("_stall_tighten_applications", 0)) + 1
                            )
                        log.warning(
                            "M4_ACT_TIGHTEN_AGG | sym={sym} new_sl=${nsl} "
                            "dist={dpct:.2f}% src={src} greed={gr} score={sc:.0f} "
                            "note=partial_fell_back_then_tighten",
                            sym=symbol,
                            nsl=format_price(trail.trail_stop_price, symbol_price),
                            dpct=trail.trail_distance_pct,
                            src=action.source,
                            gr=action.greed_rule_triggered,
                            sc=action.score_value,
                        )
                else:
                    log.info(
                        "M4_ACT_SKIP | sym={sym} reason=partial_no_trail "
                        "score={sc:.0f} pnl={pnl:+.2f}%",
                        sym=symbol,
                        sc=action.score_value,
                        pnl=action.current_pnl,
                    )

        elif action.action == "full_close":
            score_data = {"pnl_pct": action.current_pnl, "exploit_score": action.score_value}
            # Issue C fix Phase 3a (2026-05-08) — disambiguate the
            # ``closed_by`` label by trigger path. The legacy fixed
            # string ``"mode4_p9"`` was attached to every full closure
            # regardless of whether the score path, the anti-greed
            # pullback backstop, or the mature-stall valve fired —
            # which masked which actual trigger killed each trade and
            # produced the audit's "32 mode4_p9 events" misclassification
            # (only 4 actual closures fired in the window). The
            # resolved label distinguishes the four paths so future
            # COORD_CLOSE_END / M4_ACT_CLOSE log lines are diagnosable
            # without reading source.
            _close_label = _resolve_full_close_label(action)
            success = await self._execute_full_close(
                symbol, pos, score_data, closed_by=_close_label,
            )
            if success:
                self._last_action_time[symbol] = now
                self._last_action_type[symbol] = "full_close"
                log.warning(
                    "M4_ACT_CLOSE | sym={sym} pnl={pnl:+.2f}% "
                    "peak={pk:+.2f}% pullback={pb:.0f}% src={src} "
                    "greed={gr} score={sc:.0f}",
                    sym=symbol,
                    pnl=action.current_pnl,
                    pk=action.peak_pnl,
                    pb=action.pullback_pct,
                    src=action.source,
                    gr=action.greed_rule_triggered,
                    sc=action.score_value,
                )
                await self._send_mode4_alert(symbol, action, trail, pos)

    async def _send_mode4_alert(self, symbol: str, action: ActionResult, trail, pos) -> None:
        """Send Telegram alert for partial/full close actions from Phase 9."""
        if not self.alert_manager:
            return
        try:
            emoji = "⚡" if action.action == "partial_close" else "🎯"
            source_text = {
                "score": "Model Score",
                "anti_greed": "Anti-Greed",
                "both": "Score + Anti-Greed",
            }
            msg = (
                f"{emoji} Mode4 {action.action.upper().replace('_', ' ')} — {symbol}\n"
                f"PnL: {action.current_pnl:+.2f}% | Peak: {action.peak_pnl:+.2f}%\n"
                f"Pullback: {action.pullback_pct:.0f}% of peak given back\n"
                f"Score: {action.score_value:.0f}/100 | Regime: {action.regime_used}\n"
                f"Trigger: {source_text.get(action.source, action.source)}"
            )
            if action.greed_rule_triggered != "none":
                msg += f"\nAnti-greed rule: {action.greed_rule_triggered}"
            await self.alert_manager.send_custom(msg, AlertLevel.INFO)
        except Exception:
            pass  # Never let alert failure block the trade action

    # ─── Phase 10: Comprehensive sniper_log writer ────────────────

    async def _write_sniper_log(
        self,
        symbol: str,
        hurst,
        momentum,
        extension,
        volume,
        risk_reward,
        composite,
        trail,
        action,
        state,
        current_pnl: float,
    ) -> None:
        """Write comprehensive Mode4 evaluation to sniper_log table.

        Writes all 5 model outputs, composite score, trail state, and
        action decision. Called every N ticks (30s), on any action != hold,
        or when score >= log_always_above_score.

        Uses existing 'action' column for Phase 9 action value.
        New columns added via SCHEMA_VERSION 16 migration.
        """
        try:
            # timestamp/side/spike_direction are NOT NULL in the old schema — provide defaults
            _side = state.direction if state else "N/A"
            await self.db.execute(
                """INSERT INTO sniper_log (
                    symbol, timestamp, side, spike_direction, created_at,
                    hurst_value, hurst_score, hurst_regime, hurst_confidence,
                    momentum_decay_score, momentum_consec_decel, momentum_reversed,
                    slope_short, slope_long,
                    extension_atr, extension_score, atr_value, vol_ratio,
                    volume_div_score, price_obv_corr, volume_trend_ratio, divergence_type,
                    risk_reward_score, ev_ratio, profit_amplifier,
                    composite_score, composite_base, regime,
                    consensus_boost, urgency_boost,
                    trail_stop_price, trail_distance_pct,
                    action, action_source, peak_pnl_pct,
                    pullback_from_peak, anti_greed_rule
                ) VALUES (
                    ?, datetime('now'), ?, 'EVAL', datetime('now'),
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                )""",
                (
                    symbol, _side,
                    hurst.hurst_value, hurst.score, hurst.regime, hurst.confidence,
                    momentum.score, momentum.consecutive_decelerations,
                    1 if momentum.momentum_reversed else 0,
                    momentum.slope_short, momentum.slope_long,
                    extension.extension_atr, extension.score, extension.atr_current,
                    extension.vol_ratio,
                    volume.score, volume.price_obv_correlation,
                    volume.volume_trend_ratio, volume.divergence_type,
                    risk_reward.score, risk_reward.ev_ratio, risk_reward.profit_amplifier,
                    composite.score, composite.base_score, composite.regime_used,
                    composite.consensus_boost, composite.urgency_boost,
                    trail.trail_stop_price if trail else None,
                    trail.trail_distance_pct if trail else None,
                    action.action if action else None,
                    action.source if action else None,
                    state.peak_pnl_pct if state else None,
                    action.pullback_pct if action else None,
                    action.greed_rule_triggered if action else None,
                ),
            )
        except Exception as e:
            log.error(
                "M4_LOG_FAIL | sym={sym} err='{err}' | {ctx_}",
                sym=symbol, err=str(e)[:100], ctx_=ctx(),
            )

    # ─── Buffer pre-fill from klines ───────────────────────────────

    async def _prefill_buffer(self, symbol: str, buffer) -> None:
        """Pre-fill the ring buffer with historical kline data.

        Loads the last 30+ minutes of M5 close prices from the klines table.
        These are lower-resolution than Mode 4's 5-second data, but they
        give the models enough history to start producing scores immediately.

        As Mode 4 ticks, real 5-second data gradually replaces the
        kline-based data in the buffer.
        """
        try:
            rows = await self.db.fetch_all(
                "SELECT close, volume, timestamp FROM klines "
                "WHERE symbol = ? AND timeframe = '5' "
                "ORDER BY timestamp DESC LIMIT 36",
                (symbol,),
            )
            if not rows:
                log.debug(
                    "ProfitSniper: no kline data for {sym}, buffer starts empty",
                    sym=symbol,
                )
                return

            # Reverse to chronological order (query returns newest first)
            rows = list(reversed(rows))

            for row in rows:
                try:
                    ts_str = row["timestamp"]
                    if "+" in ts_str or ts_str.endswith("Z"):
                        ts = datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).timestamp()
                    else:
                        ts = datetime.fromisoformat(ts_str).replace(
                            tzinfo=timezone.utc
                        ).timestamp()

                    close_price = float(row["close"])
                    buffer.append({
                        "ts": ts,
                        "price": close_price,
                        "bid": close_price,   # klines don't have bid/ask
                        "ask": close_price,
                        "volume_24h": float(row.get("volume", 0)),
                    })
                except (ValueError, TypeError, KeyError):
                    continue  # skip malformed rows

        except Exception as e:
            log.warning(
                "ProfitSniper: failed to pre-fill buffer for {sym}: {err}",
                sym=symbol,
                err=str(e),
            )

    # ─── Counterfactual cleanup ────────────────────────────────────

    # ─── M4: Exploit scoring helpers ──────────────────────────────

    @staticmethod
    def _determine_direction(pos: Any, current_price: float) -> str:
        """Determine if the current movement is a profit or loss spike.

        For Buy/Long: price UP = PROFIT, price DOWN = LOSS.
        For Sell/Short: price DOWN = PROFIT, price UP = LOSS.

        Args:
            pos: Position object with side and entry_price.
            current_price: Current market price.

        Returns:
            "PROFIT" or "LOSS".
        """
        if pos.entry_price <= 0:
            return "LOSS"
        is_long = pos.side == Side.BUY
        if is_long:
            return "PROFIT" if current_price > pos.entry_price else "LOSS"
        return "PROFIT" if current_price < pos.entry_price else "LOSS"

    @staticmethod
    def _calculate_pnl_pct(pos: Any, current_price: float) -> float:
        """Calculate current unrealized PnL percentage.

        Args:
            pos: Position object with side and entry_price.
            current_price: Current market price.

        Returns:
            PnL as percentage (positive = profit, negative = loss).
        """
        if pos.entry_price <= 0:
            return 0.0
        if pos.side == Side.BUY:
            return (current_price - pos.entry_price) / pos.entry_price * 100
        return (pos.entry_price - current_price) / pos.entry_price * 100

    @staticmethod
    def _apply_anti_greed(
        raw_score: int,
        direction: str,
        velocity: float,
        acceleration: float,
        is_long: bool,
    ) -> tuple[int, str]:
        """Adjust exploit score based on whether the spike is peaking.

        For PROFIT spikes:
          GROWING (favorable vel>0, favorable accel>0): -20 pts (wait for peak)
          PEAKING (favorable vel>0, favorable accel≤0): no change (ideal exit)
          REVERTING (favorable vel≤0): +10 pts (urgency — missed peak)

        For LOSS spikes:
          No adjustment. Cut losses ASAP.

        Args:
            raw_score: Combined score from models (0-100).
            direction: "PROFIT" or "LOSS".
            velocity: Price velocity from Model 2 (positive = price up).
            acceleration: Price acceleration from Model 2.
            is_long: True if Buy/Long position.

        Returns:
            (adjusted_score, status): Clamped 0-100 score and status string.
        """
        if direction == "LOSS":
            return raw_score, "CRASH"

        # Normalize velocity to "favorable" direction
        favorable_vel = velocity if is_long else -velocity
        favorable_accel = acceleration if is_long else -acceleration

        if favorable_vel > 0 and favorable_accel > 0:
            return max(0, raw_score - 20), "GROWING"
        elif favorable_vel > 0:
            return raw_score, "PEAKING"
        else:
            return min(100, raw_score + 10), "REVERTING"

    def _classify_score(
        self,
        score: int,
        direction: str,
        pnl_pct: float,
        position_age_seconds: float,
    ) -> tuple[str, bool]:
        """Classify the exploit score into an action category.

        Categories: NORMAL, WATCH, CONSULT, STRONG, EXTREME.

        Applies minimum profit filter and immunity checks.

        Args:
            score: Adjusted exploit score (0-100).
            direction: "PROFIT" or "LOSS".
            pnl_pct: Current PnL percentage.
            position_age_seconds: How long the position has been open.

        Returns:
            (classification, is_actionable).
        """
        cfg = self.settings.mode4

        # Base classification
        if score >= cfg.score_auto_full:
            classification = "EXTREME"
        elif score >= cfg.score_auto_partial:
            classification = "STRONG"
        elif score >= cfg.score_consult_claude:
            classification = "CONSULT"
        elif score >= cfg.score_watch:
            classification = "WATCH"
        else:
            classification = "NORMAL"

        # Minimum profit filter (profit side only)
        if direction == "PROFIT" and pnl_pct < cfg.min_profit_pct:
            if classification in ("CONSULT", "STRONG", "EXTREME"):
                classification = "WATCH"

        # Immunity check
        is_actionable = True

        if direction == "PROFIT":
            if position_age_seconds < cfg.profit_immunity_seconds:
                is_actionable = False
            elif position_age_seconds < cfg.full_rules_after_seconds:
                if classification != "EXTREME":
                    is_actionable = False
        elif direction == "LOSS":
            if position_age_seconds < cfg.loss_immunity_seconds:
                is_actionable = False

        return classification, is_actionable

    # ─── Phase 9 (P1-8 Sniper Stall Escape) ────────────────────────

    def _stall_escape_action(
        self,
        symbol: str,
        tracked: dict,
        is_actionable: bool,
        current_action: str,
    ) -> str | None:
        """Escalate when actionable+hold persists for many consecutive ticks.

        The brief's observed RAREUSDT case: 114 sniper evaluations all
        scored ``actionable=True`` while the action engine voted ``hold``
        because the score sat just below the close threshold. The
        ``actionable`` flag was added as a signal but no dispatcher
        consumed it — write-only telemetry. This method bridges the gap.

        Counter is stored on the per-symbol ``tracked`` dict so it
        survives across sniper ticks but disappears with the position.

        Phase 4A (session-stability) — de-escalation guards:

        1. After an escape action is emitted the method falls silent for
           ``stall_escape_cooldown_seconds`` so downstream dispatch does
           not fire ``partial_close`` on every tick. This stops the 20×
           ``PARTIAL_CLOSE_UNSUPPORTED`` spam observed on MOVRUSDT.
        2. Each Shadow-downgraded tighten_agg is counted against
           ``stall_tighten_max_applications``. Once the cap is reached and
           PnL has not recovered by at least
           ``stall_recovery_threshold_pct`` from the worst-observed PnL,
           the method escalates straight to ``full_close`` (logged as
           ``MODE4_STALL_ESCALATE``) rather than emitting another
           partial_close that Shadow will silently swallow.

        Layer 4 Realignment Phase 1A (2026-05-06) — minimum-age guardrail:

        Before any stall-counter logic runs, the position's age is
        consulted. A position younger than
        ``settings.layer4_sniper.min_age_seconds`` (default 300 s, mirror
        of the watchdog's ``strategic_action_min_hold_seconds`` and
        time-decay's ``min_age_seconds``) returns immediately without
        touching the stall counter or any escape decision. This holds
        the sniper to the same 5-minute settling contract as the other
        Layer 4 close paths so fresh trades cannot be force-closed by
        the sniper before they have had time to develop. ``trade_coord``
        returns 99999 s for unregistered positions; that high value
        passes the guard naturally so untracked positions fall through
        to the existing logic.
        """
        # Layer 4 Realignment Phase 1A — minimum-age guardrail. Reads
        # ``settings.layer4_sniper.min_age_seconds`` (default 300 s).
        # All attribute accesses use ``getattr(..., default)`` so legacy
        # tests that build a ProfitSniper via ``__new__`` (skipping
        # __init__) without setting ``trade_coordinator`` or with a
        # ``MagicMock()`` settings still execute the existing
        # stall-escape logic. In production, every ProfitSniper goes
        # through __init__ which sets ``trade_coordinator`` from
        # WorkerManager DI and ``settings.layer4_sniper`` from
        # _build_layer4_sniper.
        #
        # ``self.trade_coordinator.get_age_seconds`` returns 99999 for
        # unregistered symbols (see core/trade_coordinator.py:358); that
        # value passes the comparison so unregistered positions are not
        # spuriously blocked by this guardrail. ``min_age_seconds <= 0``
        # disables the guard entirely (kill-switch).
        sniper_cfg = getattr(self.settings, "layer4_sniper", None)
        try:
            min_age_s = (
                float(getattr(sniper_cfg, "min_age_seconds", 300.0))
                if sniper_cfg is not None
                else 300.0
            )
        except (TypeError, ValueError):
            # Defensive: if a test injects a non-numeric mock, fall
            # through with the guard disabled rather than crashing the
            # tick loop. Production never hits this path because
            # Layer4SniperSettings.min_age_seconds is a plain ``float``.
            min_age_s = 0.0
        trade_coord = getattr(self, "trade_coordinator", None)
        if trade_coord is not None and min_age_s > 0.0:
            try:
                age_s = float(trade_coord.get_age_seconds(symbol))
            except Exception:
                # Fail-open on coordinator errors — don't block the
                # sniper because of an unrelated coordinator failure.
                # The error is logged at WARNING for visibility.
                log.warning(
                    f"SNIPER_AGE_GUARD_ERR | sym={symbol} "
                    f"err=coordinator_get_age_failed | {ctx()}"
                )
                age_s = 99999.0
            if age_s < min_age_s:
                log.info(
                    f"SNIPER_AGE_GUARD | sym={symbol} age={age_s:.0f}s "
                    f"min_age={min_age_s:.0f}s blocked=true | {ctx()}"
                )
                return None

        # T2-9 (2026-05-12) — STRUCT_GUARD vs Sniper coordination.
        # Operator-approved decision (2026-05-12): when STRUCT_GUARD
        # verdict is "stable" (structure intact, time-decay blocked the
        # close), sniper DEFERS its stall escape for this tick. The
        # next tick re-evaluates with a fresh verdict.
        # Pre-T2-9: sniper made stall-escape decisions independently of
        # STRUCT_GUARD. Production logs (5 h window 2026-05-12) showed
        # 15 STRUCT_GUARD blocked=true events on ENAUSDT while sniper
        # continued trailing — two protection layers in conflict.
        # Now: layer4_protection caches the verdict; sniper consults
        # before any escape computation. See
        # Layer4ProtectionService.get_struct_guard_verdict +
        # PositionWatchdog._handle_time_decay (line ~1408 records the
        # verdict). Verdicts older than 60 s are treated as missing
        # (sniper proceeds normally) so a stale "stable" cannot defer
        # sniper indefinitely after structure has actually broken.
        l4p = getattr(self, "layer4_protection", None)
        if l4p is not None and hasattr(l4p, "get_struct_guard_verdict"):
            try:
                _sg_verdict, _sg_age_s = l4p.get_struct_guard_verdict(symbol)
            except Exception as e:
                # Defensive: never let a verdict-cache failure break
                # the sniper tick loop. Log + proceed.
                log.debug(
                    f"SNIPER_STRUCT_GUARD_LOOKUP_FAIL | sym={symbol} "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )
                _sg_verdict, _sg_age_s = ("", 0.0)
            if _sg_verdict == "stable":
                # T2-9: defer. Stall counter is NOT incremented this
                # tick (preserving the position's escape budget for
                # when STRUCT_GUARD actually releases). The next sniper
                # tick will re-check the verdict.
                _stall_ticks_now = int(tracked.get("_stall_ticks", 0))
                log.info(
                    f"SNIPER_STRUCT_GUARD_DEFER | sym={symbol} "
                    f"struct_verdict=stable verdict_age_s={_sg_age_s:.1f} "
                    f"stall_ticks={_stall_ticks_now} action=defer | {ctx()}"
                )
                return None

        cfg = self.settings.mode4
        partial_after = int(getattr(cfg, "stall_escape_partial_after_ticks", 20))
        full_after = int(getattr(cfg, "stall_escape_full_after_ticks", 40))
        cooldown_s = float(getattr(cfg, "stall_escape_cooldown_seconds", 30))
        tighten_max = int(getattr(cfg, "stall_tighten_max_applications", 3))
        recovery_thresh = float(
            getattr(cfg, "stall_recovery_threshold_pct", 0.15)
        )
        # Definitive-fix Phase 10 (2026-04-28) — lifetime cap on partial
        # emissions per position. Forensic S6 captured 5 ladder steps in
        # 1:49-1:54 because the cooldown reset and partial fired again.
        # max_partials_per_position is the budget; once spent, the next
        # stall escape is full_close instead of another partial.
        max_partials = int(getattr(cfg, "max_partials_per_position", 1))
        # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — type-aware
        # grace gap. After a partial emission a position must accumulate
        # ``partial_to_partial_grace_ticks`` ticks before the next
        # partial can fire, and ``partial_to_full_grace_ticks`` ticks
        # before the cap-path full close can fire. The forced-full path
        # (``ticks > full_after``) is the mature-stall valve and
        # bypasses these gates by design (it predates this check).
        p2p_grace = int(getattr(cfg, "partial_to_partial_grace_ticks", 60))
        p2f_grace = int(getattr(cfg, "partial_to_full_grace_ticks", 60))

        if is_actionable and current_action == "hold":
            tracked["_stall_ticks"] = int(tracked.get("_stall_ticks", 0)) + 1
        else:
            # Any non-stall condition resets — a single non-hold tick is
            # evidence the sniper is no longer stuck and the escalation
            # ladder restarts from zero.
            tracked["_stall_ticks"] = 0
            tracked["_stall_last_escape_ts"] = 0.0
            tracked["_stall_tighten_applications"] = 0
            tracked["_stall_worst_pnl_pct"] = None
            return None

        ticks = tracked["_stall_ticks"]
        if ticks <= partial_after:
            return None  # still inside the quiet window

        # Extract the latest PnL snapshot once. The scorer loop stores
        # the latest composite snapshot in ``tracked["last_score"]``
        # before this method runs (see the ``last_score = {... "pnl_pct":
        # pnl_pct ...}`` assignment that immediately precedes the
        # stall_escape call site). Layer 4 Realignment Phase 1C
        # (2026-05-06) consumes the same ``_last_pnl`` for the PnL
        # guards below and the existing worst-PnL recovery tracking.
        _last_pnl: float | None = None
        _last_score = tracked.get("last_score") or {}
        if isinstance(_last_score, dict):
            _raw = _last_score.get("pnl_pct")
            if _raw is not None:
                try:
                    _last_pnl = float(_raw)
                except (TypeError, ValueError):
                    _last_pnl = None

        # Layer 4 Realignment Phase 1C (2026-05-06) — PnL-aware stall
        # escape. A profitable position is by definition not stalling
        # (the structure that triggered entry has been validated by
        # price action); killing it captures pullback noise rather
        # than real failure. A position in the development window
        # (small loss between development_window_lower and
        # profit_protection_threshold) is still in normal-resolution
        # territory; the move could go either way and the signal-to-
        # noise ratio is too low to act. Stall escape only fires when
        # the position is in a meaningful loss (pnl <=
        # development_window_lower).
        #
        # Reads ``settings.layer4_sniper.profit_protection_threshold``
        # (default 0.0 %) and ``settings.layer4_sniper.development_
        # window_lower`` (default -0.3 %). The PnL guard fires AFTER
        # the quiet-window check so that the SNIPER_PROFIT_GUARD log
        # only emits on positions that have already accumulated 120+
        # actionable=True / action=hold ticks — the log is rate-
        # limited by the existing stall counter and won't spam the
        # general.log on every 5-second tick.
        if _last_pnl is not None:
            try:
                profit_threshold = (
                    float(getattr(sniper_cfg, "profit_protection_threshold", 0.0))
                    if sniper_cfg is not None
                    else 0.0
                )
                development_floor = (
                    float(getattr(sniper_cfg, "development_window_lower", -0.3))
                    if sniper_cfg is not None
                    else -0.3
                )
            except (TypeError, ValueError):
                # Non-numeric mock in tests — disable the guards.
                profit_threshold = float("inf")
                development_floor = float("-inf")
            if _last_pnl > profit_threshold:
                log.info(
                    f"SNIPER_PROFIT_GUARD | sym={symbol} "
                    f"pnl={_last_pnl:+.2f}% "
                    f"threshold={profit_threshold:+.2f}% "
                    f"ticks={ticks} blocked=true | {ctx()}"
                )
                return None
            if _last_pnl > development_floor:
                log.info(
                    f"SNIPER_DEVELOPMENT_GUARD | sym={symbol} "
                    f"pnl={_last_pnl:+.2f}% "
                    f"floor={development_floor:+.2f}% "
                    f"ticks={ticks} blocked=true | {ctx()}"
                )
                return None

        if _last_pnl is not None:
            _worst = tracked.get("_stall_worst_pnl_pct")
            if _worst is None or _last_pnl < float(_worst):
                tracked["_stall_worst_pnl_pct"] = _last_pnl

        now = time.monotonic()

        # Forced escalation path — either too many stall ticks outright
        # (ticks > full_after) or the tighten_agg fallback has been applied
        # enough times without measurable PnL recovery.
        applications = int(tracked.get("_stall_tighten_applications", 0))
        worst = tracked.get("_stall_worst_pnl_pct")
        recovered = (
            worst is not None
            and _last_pnl is not None
            and (float(_last_pnl) - float(worst)) >= recovery_thresh
        )

        # Issue C fix Phase 3b (2026-05-08) — peak-protected stall
        # extension. Positions that touched a meaningful peak before
        # reverting demonstrated edge; the operator's aggressive-
        # exploitation aim says give them more time before forced full
        # close. The base ``stall_escape_full_after_ticks`` (default 40,
        # ~3.3 min at 5 s cadence) is doubled to
        # ``peak_protected_full_after_ticks`` (default 80, ~6.7 min) for
        # positions whose ``state.peak_pnl_pct`` ever crossed
        # ``peak_protection_threshold_pct`` (default 0.10 %).
        # Positions with peak <= threshold (e.g. HYPERUSDT 0.00 %)
        # continue to use the base threshold — the runaway-loss
        # protection is preserved exactly. The peak is read from
        # ``self._profit_states`` which carries the per-position
        # ``PositionProfitState``; protected at runtime with
        # ``getattr`` so legacy tests that build the worker via
        # ``__new__`` (skipping ``__init__``) without
        # ``_profit_states`` still execute the existing logic.
        peak_thresh_pct = (
            float(getattr(sniper_cfg, "peak_protection_threshold_pct", 0.10))
            if sniper_cfg is not None
            else 0.10
        )
        peak_full_after = int(
            getattr(sniper_cfg, "peak_protected_full_after_ticks", 80)
            if sniper_cfg is not None
            else 80
        )
        peak_pnl_pct: float | None = None
        _profit_states = getattr(self, "_profit_states", None)
        if _profit_states is not None:
            _state = _profit_states.get(symbol)
            if _state is not None:
                try:
                    peak_pnl_pct = float(getattr(_state, "peak_pnl_pct", 0.0))
                except (TypeError, ValueError):
                    peak_pnl_pct = None
        peak_qualifies = (
            peak_pnl_pct is not None
            and peak_thresh_pct > 0
            and peak_pnl_pct >= peak_thresh_pct
        )
        effective_full_after = (
            peak_full_after if peak_qualifies else full_after
        )

        # Issue C fix Phase 3c (2026-05-08) — recovering-PnL gate. Even
        # when ticks would otherwise force the valve, a position
        # actively recovering from its worst observed PnL by at least
        # ``recovering_threshold_pct`` is given another tick. Distinct
        # from the existing ``recovered`` (line above) which uses
        # ``stall_recovery_threshold_pct`` (default 0.15 %) only as the
        # tighten-cap escape condition; this new gate is independent
        # and protects the tick-count path too. Set
        # ``recovering_threshold_pct <= 0`` to disable.
        recovery_gate_thresh = (
            float(getattr(sniper_cfg, "recovering_threshold_pct", 0.10))
            if sniper_cfg is not None
            else 0.10
        )
        is_recovering = (
            recovery_gate_thresh > 0
            and worst is not None
            and _last_pnl is not None
            and (float(_last_pnl) - float(worst)) >= recovery_gate_thresh
        )

        ticks_path_fires = ticks > effective_full_after
        tighten_path_fires = applications >= tighten_max and not recovered
        valve_would_fire = ticks_path_fires or tighten_path_fires

        if valve_would_fire and is_recovering:
            log.info(
                f"MODE4_VALVE_BLOCKED_RECOVERING | sym={symbol} "
                f"ticks={ticks} "
                f"current_pnl={_last_pnl if _last_pnl is None else f'{float(_last_pnl):+.2f}%'} "
                f"worst_pnl={worst if worst is None else f'{worst:+.2f}%'} "
                f"delta={(float(_last_pnl) - float(worst)) if (worst is not None and _last_pnl is not None) else 0:+.2f}% "
                f"threshold={recovery_gate_thresh:+.2f}% "
                f"effective_full_after={effective_full_after} "
                f"peak_qualifies={peak_qualifies} | {ctx()}"
            )
            return None

        if valve_would_fire:
            if peak_qualifies and ticks_path_fires:
                log.info(
                    f"MODE4_PEAK_PROTECTED | sym={symbol} "
                    f"peak_pnl={peak_pnl_pct:+.2f}% "
                    f"threshold={peak_thresh_pct:+.2f}% "
                    f"ticks={ticks} "
                    f"effective_full_after={effective_full_after} "
                    f"base_full_after={full_after} | {ctx()}"
                )
            log.warning(
                f"MODE4_STALL_ESCALATE | sym={symbol} ticks={ticks} "
                f"tighten_attempts={applications} "
                f"worst_pnl={worst if worst is None else f'{worst:+.2f}%'} "
                f"current_pnl={_last_pnl if _last_pnl is None else f'{float(_last_pnl):+.2f}%'} "
                f"recovered={recovered} "
                f"peak_qualifies={peak_qualifies} "
                f"effective_full_after={effective_full_after} | {ctx()}"
            )
            tracked["_stall_last_escape_ts"] = now
            tracked["_last_escape_type"] = "full"
            tracked["_last_escape_tick"] = ticks
            return "full_close"

        # Cooldown: after the most recent escape emission, stay quiet so
        # the partial_close warning does not re-fire every tick.
        last_emit = float(tracked.get("_stall_last_escape_ts", 0.0))
        if last_emit > 0 and (now - last_emit) < cooldown_s:
            return None

        # Sniper-Latency-Size Fix Phase 1 (2026-05-07) — type-aware
        # grace gap. The 30-second blanket cooldown above is not enough:
        # at 5s tick cadence cooldown=30s = 6 ticks, producing the 5-6
        # tick ladder steps observed 2026-05-07 10:57:40-10:59:19 (4
        # escalations on RENDERUSDT in 99 sec). The block below requires
        # at least ``partial_to_partial_grace_ticks`` (default 60) to
        # have elapsed since the last partial before the next partial
        # can fire, and the same for the cap-path full close. The
        # forced-full path (``ticks > full_after``) above is the
        # mature-stall valve and is unaffected.
        last_type = str(tracked.get("_last_escape_type", "") or "")
        last_tick = int(tracked.get("_last_escape_tick", 0) or 0)
        if last_type == "partial":
            ticks_since = ticks - last_tick
            partials_so_far_check = int(tracked.get("_partials_emitted", 0))
            cap_path_pending = partials_so_far_check >= max_partials
            grace_required = p2f_grace if cap_path_pending else p2p_grace
            if ticks_since < grace_required:
                log.info(
                    f"SNIPER_GRACE_BLOCKED | sym={symbol} ticks={ticks} "
                    f"last_type=partial last_tick={last_tick} "
                    f"ticks_since_last={ticks_since} "
                    f"grace_required={grace_required} "
                    f"would_be={'full_close' if cap_path_pending else 'partial_close'} "
                    f"blocked=true | {ctx()}"
                )
                return None

        # Definitive-fix Phase 10 (2026-04-28) — partial-emit lifetime cap.
        # If the partial budget is spent, escalate to full_close so the
        # position exits in one shot instead of repeatedly slicing 50%
        # of a shrinking notional. The counter is reset on
        # _on_position_opened (fresh position = fresh budget).
        partials_so_far = int(tracked.get("_partials_emitted", 0))
        if partials_so_far >= max_partials:
            log.warning(
                f"MODE4_PARTIAL_CAP_REACHED | sym={symbol} ticks={ticks} "
                f"partials_so_far={partials_so_far} cap={max_partials} "
                f"escalating_to=full_close current_pnl="
                f"{(_last_pnl if _last_pnl is None else f'{_last_pnl:+.2f}%')} "
                f"| {ctx()}"
            )
            tracked["_stall_last_escape_ts"] = now
            tracked["_last_escape_type"] = "full"
            tracked["_last_escape_tick"] = ticks
            return "full_close"

        # Emit a fresh partial_close escape and record the timestamp +
        # increment the lifetime counter. ``_last_escape_type`` and
        # ``_last_escape_tick`` are written so the grace-gap check above
        # can enforce the inter-partial spacing on subsequent ticks.
        tracked["_stall_last_escape_ts"] = now
        tracked["_last_escape_type"] = "partial"
        tracked["_last_escape_tick"] = ticks
        tracked["_partials_emitted"] = partials_so_far + 1
        return "partial_close"

    # ─── M5: Execution methods ─────────────────────────────────────

    async def _execute_full_close(
        self, symbol: str, pos: Any, score_data: dict, closed_by: str = "mode4_spike",
        check_min_hold: bool = True,
    ) -> bool:
        """Close 100% of a position — full profit capture or loss cut.

        Follows the exact same close path as the PositionWatchdog:
        close_position → event_buffer → coordinator.

        Layer 4 Realignment Phase 4.4 (2026-05-06) — the sniper now
        consults the shared Layer4ProtectionService BEFORE actually
        closing the position. This catches BOTH the stall-escape path
        (which already gates upstream via Phase 1A age + Phase 1C PnL
        guards inside ``_stall_escape_action``) AND the score-based
        "full" path (called from ``_execute_action`` at line ~1921 with
        closed_by=mode4_p9), which had no min-hold gate before.

        ``check_min_hold=True`` enforces the same 5-min settling
        contract as the watchdog's strategic-action guardrail.
        ``check_profit=False`` because the sniper's existing profit
        gate (line 1571) and Phase 1C guards already filter winners;
        re-checking here would emit duplicate guard logs.
        ``check_structural=False`` because the sniper does not own a
        TimeDecayState; the structural check is layered on the
        watchdog/time-decay path which DOES have anchors.

        When ``layer4_protection`` is None (legacy boot order or
        tests building the sniper directly), this method emits
        SNIPER_PROTECTION_SERVICE_UNWIRED at ERROR level and refuses
        to escalate (fail-loud, fail-safe). Production flow always
        wires the service via WorkerManager.

        Args:
            symbol: Trading pair to close.
            pos: Position object.
            score_data: M4 score snapshot for logging.
            closed_by: Tag for trade records.

        Returns:
            True if closed successfully, False on failure or when
            blocked by the protection service.
        """
        # Phase 4.4 — consult Layer4ProtectionService.
        if self.layer4_protection is not None:
            try:
                _result = await self.layer4_protection.is_protected(
                    symbol=symbol,
                    side=str(getattr(pos, "side", "") or ""),
                    close_reason=closed_by,
                    pnl_pct=None,             # check_profit=False so unused
                    # The sacred cap (Rule 7, inviolable at any age) and the
                    # spike catastrophe stop (Rule 8, protect young trades from
                    # crashes) pass check_min_hold=False so they can close a
                    # position younger than the 5-min settling contract; every
                    # other close keeps the guard (default True).
                    check_min_hold=check_min_hold,
                    check_profit=False,
                    check_structural=False,
                )
            except Exception as e:
                # Service should never raise — fail-safe block on
                # unexpected error so we don't accidentally close
                # a protected position.
                log.error(
                    f"SNIPER_PROTECTION_SERVICE_ERR | sym={symbol} "
                    f"err={e!r} blocked=true | {ctx()}"
                )
                return False
            if _result.protected:
                log.info(
                    f"SNIPER_PROTECTED | sym={symbol} closed_by={closed_by} "
                    f"reason={_result.reason} blocked=true | {ctx()}"
                )
                return False
        else:
            # Strict fail-loud. Do NOT silently fall through and close
            # the position — that would re-create the pre-fix bypass.
            log.error(
                f"SNIPER_PROTECTION_SERVICE_UNWIRED | sym={symbol} "
                f"closed_by={closed_by} refusing to escalate | {ctx()}"
            )
            return False

        try:
            # Issue 2.11 (2026-06-07): record the real exit reason on the
            # coordinator BEFORE the close so both pop_close_reason consumers
            # (the WS subscriber and the watchdog poll) return it instead of the
            # generic "{mode}_sl_tp" fallback. The adapter close_trigger cache and
            # the booked closed_by already carry it; this closes the coordinator-
            # store gap that lost the provenance for every sniper full-close
            # (loss_spike_force, the cap/stall/recovery cuts, mode4_*).
            if self.trade_coordinator and closed_by:
                self.trade_coordinator.set_close_reason(symbol, str(closed_by))
            # Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1 follow-up):
            # propagate close_trigger so BYBIT_DEMO_POSITION_CLOSE shows
            # the source-specific reason. closed_by is sniper-action-string
            # (e.g., "mode4_p9_close" / "stall_escape") — pass directly.
            await self.position_service.close_position(
                symbol,
                close_trigger=str(closed_by)[:40] or "sniper_close",
            )

            if self.event_buffer:
                self.event_buffer.add_event(
                    "HIGH", closed_by, symbol,
                    score=score_data.get("exploit_score", 0),
                    pnl_pct=round(score_data.get("pnl_pct", 0), 2),
                )

            if self.trade_coordinator:
                self.trade_coordinator.remove_trade_plan(symbol)
                pnl_pct = score_data.get("pnl_pct", 0)
                auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                    await self.trade_coordinator.resolve_authoritative_pnl(
                        symbol=symbol,
                        position_service=self.position_service,
                        fallback_pnl_usd=pos.unrealized_pnl,
                        fallback_pnl_pct=pnl_pct,
                        # PnL-truth (2026-06-07): identity-match this trade's
                        # closed-pnl row by qty instead of the legacy rows[0].
                        qty=pos.size,
                    )
                )
                # F5 phantom-close fix (2026-06-08, leg A) — arm the EXISTING
                # on_trade_closed staleness gate on the sniper force-close path
                # (the proven AERO#3 phantom route: a win-prob cut whose
                # resolve_authoritative_pnl matched a same-qty STALE row at a
                # +6.7% phantom exit, booking a fake win that the qty gate could
                # not catch). Pass the sniper's trusted local reference (the live
                # unrealized + mark + qty) so an exchange-authoritative row whose
                # exit diverges from the live mark is demoted to the local net
                # BEFORE booking. No new gate; identity_confirmed left unset so
                # the exit-divergence demotion (trade_coordinator.py:1375-1383)
                # applies. candidate_qty is unavailable here (resolve does not
                # return the matched-row qty), so exit-divergence is the active
                # signal; armed only when a usable mark/qty exist (else ref_*
                # are None and the gate stays inert exactly as before).
                self.trade_coordinator.on_trade_closed(
                    symbol=symbol,
                    pnl_pct=auth_pnl_pct,
                    pnl_usd=auth_pnl_usd,
                    was_win=auth_pnl_usd > 0,
                    closed_by=closed_by,
                    exit_price=auth_exit,
                    price_source=price_src,
                    ref_pnl_usd=pos.unrealized_pnl,
                    ref_pnl_pct=pnl_pct,
                    ref_exit_price=(float(getattr(pos, "mark_price", 0) or 0) or None),
                    ref_qty=(float(getattr(pos, "size", 0) or 0) or None),
                    # ref is the live MARK, not the exact fill — 3% band.
                    ref_is_mark=True,
                )

            log.info(
                "Mode4 CLOSED {sym}: pnl={pnl:+.2f}% score={sc} by={by}",
                sym=symbol, pnl=score_data.get("pnl_pct", 0),
                sc=score_data.get("exploit_score", 0), by=closed_by,
            )
            return True
        except Exception as e:
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.error(f"M4_CLOSE_FAIL | sym={symbol} err='{str(e)[:120]}' | {ctx()}")
            return False

    async def _execute_partial_close(
        self, symbol: str, pos: Any, close_pct: int, score_data: dict
    ) -> bool:
        """Close a percentage of a position — capture most profit, let rest ride.

        Layer 4 Realignment Phase 4.4 (2026-05-06) — like
        ``_execute_full_close``, the partial-close path consults the
        shared Layer4ProtectionService before reducing position size.
        ``check_min_hold=True`` keeps fresh trades safe; partials on
        positions younger than 5 min would still constitute trade
        interference. ``check_profit=False`` and ``check_structural=
        False`` for the same reasons documented in ``_execute_full_
        close``. Service-unwired path is fail-loud + fail-safe.

        Args:
            symbol: Trading pair.
            pos: Position object.
            close_pct: Percentage to close (e.g. 75).
            score_data: M4 score snapshot.

        Returns:
            True if the action took effect (partial reduce OR full-close
            fallback). False on hard failure / exception or when blocked
            by the protection service. The caller in ``_execute_action``
            treats any True as success; on fallback we still return True
            so the outer loop doesn't re-emit partial_close (Phase 4A
            stall cooldown would already gate that, but this preserves
            pre-fix behaviour).

        Phase 4B session-stability: ``ShadowPositionService.reduce_position``
        returns an ``Order`` carrying ``qty=reduced_qty`` on a true partial,
        or ``qty=position.qty`` after a full-close fallback. We detect the
        fallback case by comparing the returned qty against the requested
        ``close_qty`` and, when a fallback landed, notify the trade
        coordinator so ``_trades`` / event buffer reflect the real close
        immediately rather than waiting for the position-monitor tick to
        catch the vanished position.
        """
        # Phase 4.4 — consult Layer4ProtectionService.
        if self.layer4_protection is not None:
            try:
                _result = await self.layer4_protection.is_protected(
                    symbol=symbol,
                    side=str(getattr(pos, "side", "") or ""),
                    close_reason="mode4_partial",
                    pnl_pct=None,
                    check_min_hold=True,
                    check_profit=False,
                    check_structural=False,
                )
            except Exception as e:
                log.error(
                    f"SNIPER_PROTECTION_SERVICE_ERR | sym={symbol} "
                    f"action=partial err={e!r} blocked=true | {ctx()}"
                )
                return False
            if _result.protected:
                log.info(
                    f"SNIPER_PROTECTED | sym={symbol} action=partial "
                    f"reason={_result.reason} blocked=true | {ctx()}"
                )
                return False
        else:
            log.error(
                f"SNIPER_PROTECTION_SERVICE_UNWIRED | sym={symbol} "
                f"action=partial refusing to escalate | {ctx()}"
            )
            return False

        try:
            close_qty = abs(pos.size * close_pct / 100)
            if close_qty <= 0:
                return False

            order = await self.position_service.reduce_position(symbol, close_qty)

            # Detect fallback-to-full-close. Tolerance handles float rounding.
            order_qty = float(getattr(order, "qty", 0) or getattr(order, "filled_qty", 0) or 0)
            _full_position_qty = abs(float(pos.size))
            _was_fallback = (
                order_qty > 0
                and abs(order_qty - close_qty) > (close_qty * 0.02)  # >2% off requested
                and abs(order_qty - _full_position_qty) <= (_full_position_qty * 0.02)
            )

            if _was_fallback:
                # Shadow rejected the partial (old Shadow without /api/reduce,
                # or qty out-of-range) and the adapter fell back to full
                # close. The position is now GONE. Tell the coordinator +
                # event buffer about the full close so downstream state
                # matches reality without waiting for position-monitor.
                _pnl_pct = float(score_data.get("pnl_pct", 0) or 0)
                log.warning(
                    f"MODE4_PARTIAL_DEGRADED_TO_FULL | sym={symbol} "
                    f"requested_qty={close_qty} full_qty={order_qty} "
                    f"pnl={_pnl_pct:+.2f}% | {ctx()}"
                )
                if self.event_buffer:
                    self.event_buffer.add_event(
                        "HIGH", "mode4_partial_fallback_full", symbol,
                        score=score_data.get("exploit_score", 0),
                        pnl_pct=round(_pnl_pct, 2),
                    )
                if self.trade_coordinator:
                    try:
                        self.trade_coordinator.remove_trade_plan(symbol)
                        _local_pnl_usd = float(getattr(pos, "unrealized_pnl", 0) or 0)
                        auth_pnl_usd, auth_pnl_pct, price_src, auth_exit = (
                            await self.trade_coordinator.resolve_authoritative_pnl(
                                symbol=symbol,
                                position_service=self.position_service,
                                fallback_pnl_usd=_local_pnl_usd,
                                fallback_pnl_pct=_pnl_pct,
                                # PnL-truth (2026-06-07): identity-match by qty.
                                qty=getattr(pos, "size", 0) or 0,
                            )
                        )
                        # F5 phantom-close fix (2026-06-08, leg A) — same gate
                        # arming as the full-close path above; the local
                        # reference here is the partial-fallback's in-hand
                        # values (_local_pnl_usd / _pnl_pct) plus the live
                        # mark + qty.
                        self.trade_coordinator.on_trade_closed(
                            symbol=symbol,
                            pnl_pct=auth_pnl_pct,
                            pnl_usd=auth_pnl_usd,
                            was_win=auth_pnl_usd > 0,
                            closed_by="mode4_partial_fallback_full",
                            exit_price=auth_exit,
                            price_source=price_src,
                            ref_pnl_usd=_local_pnl_usd,
                            ref_pnl_pct=_pnl_pct,
                            ref_exit_price=(float(getattr(pos, "mark_price", 0) or 0) or None),
                            ref_qty=(float(getattr(pos, "size", 0) or 0) or None),
                            # ref is the live MARK, not the exact fill — 3% band.
                            ref_is_mark=True,
                        )
                    except Exception as _e:
                        log.warning(
                            f"MODE4_COORD_NOTIFY_FAIL | sym={symbol} "
                            f"err='{str(_e)[:120]}' | {ctx()}"
                        )
                return True

            if self.event_buffer:
                self.event_buffer.add_event(
                    "MED", "mode4_partial", symbol,
                    score=score_data.get("exploit_score", 0),
                    pnl_pct=round(score_data.get("pnl_pct", 0), 2),
                    close_pct=close_pct,
                )

            log.info(
                "Mode4 PARTIAL {sym}: {pct}% closed, pnl={pnl:+.2f}% score={sc}",
                sym=symbol, pct=close_pct,
                pnl=score_data.get("pnl_pct", 0),
                sc=score_data.get("exploit_score", 0),
            )
            return True
        except Exception as e:
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.error(f"M4_PARTIAL_CLOSE_FAIL | sym={symbol} err='{str(e)[:120]}' | {ctx()}")
            return False

    async def _execute_tighten_sl(
        self, symbol: str, pos: Any, score_data: dict
    ) -> bool:
        """Move stop loss to lock in current profit.

        Sets new SL at entry_price + 50% of current profit distance.

        NOTE: This method has no live callers today (the Mode4 profit-lock
        logic runs through `_apply_trail_stop` via the Phase 8 trail). It
        is preserved for future re-wiring; if re-enabled, it routes
        through the SL Gateway so the single-entry-point invariant
        (tighten-only + min-distance + max-step + rate-limit) holds.

        Args:
            symbol: Trading pair.
            pos: Position object.
            score_data: M4 score snapshot.

        Returns:
            True if SL tightened, False on failure or if not beneficial.
        """
        try:
            current_price = pos.mark_price
            is_long = pos.side == Side.BUY
            lock_ratio = 0.5

            if is_long:
                profit_distance = current_price - pos.entry_price
                if profit_distance <= 0:
                    return False
                new_sl = pos.entry_price + (profit_distance * lock_ratio)
                if pos.stop_loss is not None and new_sl <= pos.stop_loss:
                    return False  # not better
            else:
                profit_distance = pos.entry_price - current_price
                if profit_distance <= 0:
                    return False
                new_sl = pos.entry_price - (profit_distance * lock_ratio)
                if pos.stop_loss is not None and new_sl >= pos.stop_loss:
                    return False  # not better

            # ── Gateway delegation (single-entry-point invariant) ──
            direction = "Buy" if is_long else "Sell"
            # PF/LC Top-15 Problem 1.4 — default the applied stop to the target;
            # the gateway branch overrides it with result.new_sl_applied. The
            # legacy (no-gateway) path applies new_sl verbatim, so the default holds.
            _applied_sl = new_sl
            _sl_clamped = False
            if self.sl_gateway is not None:
                # T2-5 (2026-05-12) — request R3 step-cap bypass for
                # this profit-lock move. Pre-T2-5 a 45-50 min position
                # near breakeven would propose ~0.9 % step (the
                # original SL distance from entry) which exceeded
                # max_step_pct=0.25 and got silently rejected — the
                # position kept its further SL exposing more capital.
                # The bypass is gated by source allowlist in the
                # gateway (only profit_sniper_lock / sentinel_breakeven
                # / profit_sniper_breakeven sources are honored), so
                # this kwarg cannot be misused by other writers. R1
                # tighten-only invariant still applies — a profit-lock
                # that would loosen SL is rejected normally.
                # Observability G2 — second sl_gateway.apply call site
                # (profit-lock breakeven). Same window-counters as the
                # trail path so the heartbeat reflects both sources.
                self._sl_updates_attempted_window += 1
                result = await self.sl_gateway.apply(
                    symbol=symbol,
                    new_sl=new_sl,
                    source="profit_sniper_lock",
                    direction=direction,
                    current_sl=pos.stop_loss,
                    current_price=current_price,
                    entry_price=getattr(pos, "entry_price", None),
                    bypass_step_cap_for_breakeven=True,
                )
                if result.accepted:
                    self._sl_updates_accepted_window += 1
                if not result.accepted:
                    return False
                # PF/LC Top-15 Problem 1.4 — use the gateway-applied stop for
                # the plan mirror and the log (R2/R3 may have clamped new_sl).
                _na = result.new_sl_applied
                _applied_sl = (
                    _na if isinstance(_na, (int, float)) and _na > 0 else new_sl
                )
                _sl_clamped = abs(_applied_sl - new_sl) > 1e-12
                success = True
            else:
                # Legacy fallback (sl_gateway=None)
                try:
                    success = await self.position_service.set_stop_loss(symbol, new_sl)
                except Exception as e:
                    log.warning(
                        "M4_TIGHTEN_FAIL | sym={sym} new_sl={sl} err='{err}'",
                        sym=symbol, sl=format_price(new_sl), err=str(e)[:120],
                    )
                    return False
                if not success:
                    log.warning(
                        "M4_TIGHTEN_FAIL | sym={sym} new_sl={sl} rsn=service_returned_false",
                        sym=symbol, sl=format_price(new_sl),
                    )
                    return False
            # Bug 1 sync — keep local TradePlan in step with Shadow (1.4: the
            # gateway-applied value, so the plan matches the broker after a clamp).
            if self.trade_coordinator is not None:
                plan = self.trade_coordinator.get_trade_plan(symbol)
                if plan is not None:
                    plan.stop_loss_price = _applied_sl
            # Unified SL propagation tag — pairs with PositionWatchdog's
            # SL_PROPAGATED so Mode4 profit-lock tightens are visible end-to-end.
            _prev_str = (
                f"{pos.stop_loss:.8f}"
                if (pos.stop_loss is not None and pos.stop_loss > 0)
                else "unknown"
            )
            log.info(
                f"SL_PROPAGATED | sym={symbol} new={_applied_sl:.8f} "
                + (f"target={new_sl:.8f} clamped=Y " if _sl_clamped else "")
                + f"prev={_prev_str} src=profit_sniper_lock | {ctx()}"
            )
            log.info(
                "Mode4 SL TIGHTENED {sym}: new_sl={sl:.4f} (locked {pct:.0f}% profit)",
                sym=symbol, sl=new_sl, pct=lock_ratio * 100,
            )
            return True
        except Exception as e:
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.warning(f"M4_SL_TIGHTEN_OUTER_FAIL | sym={symbol} err='{str(e)[:120]}' | {ctx()}")
            return False

    async def _is_safe_to_execute(
        self, symbol: str, score_data: dict
    ) -> tuple[bool, str]:
        """Check all safety conditions before executing a Mode 4 action.

        Returns:
            (is_safe, reason): True if safe, False with explanation if not.
        """
        # Check 1: Transformer not switching
        if self.transformer and hasattr(self.transformer, "is_switching") and self.transformer.is_switching:
            return False, "Exchange switch in progress"

        # Check 2: Not in cooldown
        if self.is_in_cooldown(symbol):
            remaining = self.get_cooldown_remaining(symbol)
            return False, f"Cooldown active ({remaining}s remaining)"

        # Check 3: Immunity from M4
        if not score_data.get("is_actionable", True):
            return False, "Position immune (too young)"

        # Check 4: Classification threshold
        cls = score_data.get("classification", "NORMAL")
        direction = score_data.get("direction", "PROFIT")
        if direction == "PROFIT" and cls not in ("STRONG", "EXTREME"):
            return False, f"Score below auto-execute threshold ({cls})"
        if direction == "LOSS":
            if score_data.get("exploit_score", 0) < self.settings.mode4.flash_crash_auto_score:
                return False, "Loss score below flash crash threshold"

        # Check 5: Position still tracked
        if symbol not in self._tracked:
            return False, "Position no longer tracked"

        return True, ""

    # ─── M6: Claude communication ──────────────────────────────────

    def _build_claude_prompt(self, symbol: str, pos: Any, score_data: dict) -> str:
        """Build a focused prompt for Claude's spike consultation.

        Intentionally SHORT — Claude has 15 seconds to respond.
        """
        side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
        pnl_pct = score_data.get("pnl_pct", 0)
        pnl_usd = pnl_pct / 100 * abs(pos.size * pos.entry_price) if pos.entry_price > 0 else 0
        age_sec = time.time() - self._tracked.get(symbol, {}).get("first_seen_at", time.time())
        age_str = f"{int(age_sec // 60)}m {int(age_sec % 60)}s"

        # TP target from position
        tp_info = "TP target: not set"
        if pos.take_profit and pos.entry_price > 0:
            if pos.side == Side.BUY:
                tp_pct = (pos.take_profit - pos.entry_price) / pos.entry_price * 100
            else:
                tp_pct = (pos.entry_price - pos.take_profit) / pos.entry_price * 100
            progress = (pnl_pct / tp_pct * 100) if tp_pct > 0 else 0
            tp_info = f"TP target: {tp_pct:.1f}% | Progress: {progress:.0f}%"

        # Bollinger interpretation
        bb_pos = score_data.get("bb_position", 0.5)
        if bb_pos > 1.0:
            bb_text = "ABOVE upper band (breakout)"
        elif bb_pos > 0.8:
            bb_text = "near upper band"
        elif bb_pos < 0.0:
            bb_text = "BELOW lower band (breakdown)"
        elif bb_pos < 0.2:
            bb_text = "near lower band"
        else:
            bb_text = "inside bands"

        return (
            "MODE 4 SPIKE ALERT — RESPOND IN 15 SECONDS\n\n"
            f"Position: {symbol} {side_str} {pos.leverage}x\n"
            f"Entry: ${format_price(pos.entry_price)}\n"
            f"Current: ${format_price(pos.mark_price)}\n"
            f"PnL: {pnl_pct:+.2f}% (${pnl_usd:+,.2f})\n"
            f"Held: {age_str}\n\n"
            "SPIKE DATA:\n"
            f"Exploit score: {score_data.get('exploit_score', 0)}/100\n"
            f"Hurst: {score_data.get('z_raw', 0.5):.3f}\n"
            f"Velocity status: {score_data.get('spike_status', '?')}\n"
            f"Volume: {score_data.get('volume_ratio', 1):.1f}x average\n"
            f"Bollinger: {bb_text}\n"
            f"{tp_info}\n\n"
            "OPTIONS:\n"
            "TAKE — close position now, capture this profit\n"
            "PARTIAL — close 50-75%, let rest ride to target\n"
            "HOLD — spike may continue, wait 30 seconds\n"
            "TIGHTEN — move stop loss to lock in current profit\n\n"
            "Respond with ONE word: TAKE, PARTIAL, HOLD, or TIGHTEN"
        )

    async def _consult_claude(self, symbol: str, pos: Any, score_data: dict) -> dict:
        """Send a fast spike query to Claude and return the decision.

        Uses ClaudeCodeClient.send_message() with a 15-second timeout.

        Returns:
            Dict with action, raw_response, response_time_ms.
        """
        prompt = self._build_claude_prompt(symbol, pos, score_data)
        timeout_sec = self.settings.mode4.claude_timeout_seconds
        start_time = time.time()

        try:
            response = await asyncio.wait_for(
                self.claude_client.send_message(prompt),
                timeout=timeout_sec,
            )
            response_time_ms = int((time.time() - start_time) * 1000)

            if not response or not response.strip():
                return {"action": "ERROR", "raw_response": "", "response_time_ms": response_time_ms}

            parsed = self._parse_claude_response(response)
            parsed["raw_response"] = response[:200]
            parsed["response_time_ms"] = response_time_ms

            log.info(f"SNIPER_CLAUDE | sym={symbol} act={parsed['action']} el={response_time_ms}ms | {ctx()}")
            log.info(
                "Claude responded for {sym}: {act} ({ms}ms)",
                sym=symbol, act=parsed["action"], ms=response_time_ms,
            )

            # Update rate limit counters
            self._claude_queries_this_hour += 1
            self._claude_last_query_time[symbol] = time.time()

            return parsed

        except (asyncio.TimeoutError, TimeoutError):
            response_time_ms = int((time.time() - start_time) * 1000)
            log.warning(
                "Claude TIMEOUT for {sym} spike query ({ms}ms)",
                sym=symbol, ms=response_time_ms,
            )
            self._claude_queries_this_hour += 1
            self._claude_last_query_time[symbol] = time.time()
            return {"action": "TIMEOUT", "raw_response": "", "response_time_ms": response_time_ms}

        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.error(f"M4_BRAIN_FAIL | sym={symbol} err='{str(e)[:120]}' | {ctx()}")
            self._claude_queries_this_hour += 1
            self._claude_last_query_time[symbol] = time.time()
            return {"action": "ERROR", "raw_response": str(e)[:200], "response_time_ms": response_time_ms}

    @staticmethod
    def _parse_claude_response(response: str) -> dict:
        """Parse Claude's spike consultation response.

        Extracts action keyword from Claude's response. Handles both
        clean single-word and verbose multi-word responses.

        Priority: TAKE > PARTIAL > TIGHTEN > HOLD.
        Default: HOLD (safe).
        """
        text = response.strip().upper()
        if not text:
            return {"action": "HOLD", "partial_pct": 50}

        valid_actions = {"TAKE", "PARTIAL", "HOLD", "TIGHTEN"}
        words = text.split()
        first_word = words[0].strip(".,!:;-") if words else ""

        if first_word in valid_actions:
            action = first_word
        elif "TAKE" in text:
            action = "TAKE"
        elif "PARTIAL" in text:
            action = "PARTIAL"
        elif "TIGHTEN" in text:
            action = "TIGHTEN"
        elif "HOLD" in text or "WAIT" in text:
            action = "HOLD"
        else:
            action = "HOLD"

        partial_pct = 50
        if action == "PARTIAL":
            numbers = re.findall(r"(\d+)", text)
            for num_str in numbers:
                num = int(num_str)
                if 20 <= num <= 100:
                    partial_pct = num
                    break

        return {"action": action, "partial_pct": partial_pct}

    def _can_query_claude(self, symbol: str) -> tuple[bool, str]:
        """Check if Mode 4 is allowed to query Claude right now.

        Checks: not disabled, hourly limit, per-symbol cooldown.
        """
        now = time.time()

        if now < self._claude_disabled_until:
            remaining = int(self._claude_disabled_until - now)
            return False, f"Claude disabled ({remaining}s remaining)"

        if now - self._claude_hour_start >= 3600:
            self._claude_queries_this_hour = 0
            self._claude_hour_start = now

        max_queries = self.settings.mode4.max_claude_queries_per_hour
        if self._claude_queries_this_hour >= max_queries:
            return False, f"Hourly limit reached ({max_queries}/hr)"

        recheck = self.settings.mode4.claude_hold_recheck_seconds
        last_query = self._claude_last_query_time.get(symbol, 0)
        if now - last_query < recheck:
            remaining = int(recheck - (now - last_query))
            return False, f"Per-symbol cooldown ({remaining}s)"

        return True, ""

    def _handle_claude_result(
        self, symbol: str, pos: Any, score_data: dict, claude_result: dict
    ) -> str:
        """Process Claude's response and determine the final action.

        Returns: "full_close", "partial_close", "tighten", "hold", or "skip".
        """
        action = claude_result.get("action", "ERROR")

        if action == "TAKE":
            self._claude_consecutive_timeouts = 0
            return "full_close"

        if action == "PARTIAL":
            self._claude_consecutive_timeouts = 0
            score_data["claude_partial_pct"] = claude_result.get("partial_pct", 50)
            return "partial_close"

        if action == "HOLD":
            self._claude_consecutive_timeouts = 0
            self._claude_last_query_time[symbol] = time.time()
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.info(f"M4_BRAIN_HOLD | sym={symbol} recheck_in_s=30 | {ctx()}")
            return "hold"

        if action == "TIGHTEN":
            self._claude_consecutive_timeouts = 0
            return "tighten"

        if action == "TIMEOUT":
            self._claude_consecutive_timeouts += 1
            if self._claude_consecutive_timeouts >= 3:
                self._claude_disabled_until = time.time() + 600
                log.warning(
                    "3 consecutive Claude timeouts — Mode4 Claude disabled for 10 minutes"
                )
            score = score_data.get("exploit_score", 0)
            if score > 65:
                log.info(
                    "Claude timeout {sym}, score {sc} > 65 — auto-partial",
                    sym=symbol, sc=score,
                )
                return "partial_close"
            log.info(
                "Claude timeout {sym}, score {sc} <= 65 — skipping",
                sym=symbol, sc=score,
            )
            return "skip"

        # ERROR or unrecognized
        # Phase 12.6 (Gap 6.X-G1): structured tag.
        log.warning(f"M4_BRAIN_HOLD_ON_ERROR | sym={symbol} | {ctx()}")
        return "hold"

    # ─── Cooldown system ───────────────────────────────────────────

    def _set_cooldown(self, symbol: str, score: int) -> None:
        """Set a cooldown period for a symbol after Mode 4 closes it.

        Duration depends on exploit score severity.
        """
        cfg = self.settings.mode4
        if score >= cfg.score_auto_full:
            duration = cfg.cooldown_extreme_seconds
        elif score >= cfg.score_auto_partial:
            duration = cfg.cooldown_strong_seconds
        else:
            duration = cfg.cooldown_medium_seconds

        self._cooldowns[symbol] = time.time() + duration
        log.info(
            "Mode4 cooldown: {sym} for {dur}s",
            sym=symbol, dur=duration,
        )

    def is_in_cooldown(self, symbol: str) -> bool:
        """Check if a symbol is in Mode 4 cooldown."""
        expiry = self._cooldowns.get(symbol)
        if expiry is None:
            return False
        if time.time() >= expiry:
            del self._cooldowns[symbol]
            return False
        return True

    def get_cooldown_remaining(self, symbol: str) -> int:
        """Get remaining cooldown seconds for a symbol."""
        expiry = self._cooldowns.get(symbol)
        if expiry is None:
            return 0
        return max(0, int(expiry - time.time()))

    # ─── M7: Data recording ───────────────────────────────────────

    async def _record_spike(
        self,
        symbol: str,
        pos: Any,
        score_data: dict,
        action: str,
        close_pct: float,
        close_price: float | None = None,
        profit_captured_pct: float | None = None,
        profit_captured_usd: float | None = None,
        claude_consulted: bool = False,
        claude_response: str | None = None,
        claude_response_time_ms: int | None = None,
    ) -> int | None:
        """Record a spike detection and action to sniper_log.

        Called for every spike classified CONSULT or higher (score >= 50).
        Counterfactual fields are NULL at insert — filled 60 seconds later.

        Returns:
            Inserted row ID for counterfactual tracking, or None on error.
        """
        try:
            side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            hold_sec = int(time.time() - self._tracked.get(symbol, {}).get("first_seen_at", time.time()))
            latest = self._tracked.get(symbol, {}).get("buffer")
            det_price = latest.get_latest()["price"] if latest and latest.get_latest() else pos.mark_price
            pnl_usd = None
            if score_data.get("pnl_pct") and pos.entry_price > 0:
                pnl_usd = score_data["pnl_pct"] / 100 * abs(pos.size * pos.entry_price)

            cursor = await self.db.execute(
                """INSERT INTO sniper_log (
                    timestamp, symbol, side, spike_direction,
                    entry_price, detection_price, pnl_at_detection_pct, pnl_at_detection_usd,
                    hold_duration_seconds, exploit_score, z_score, velocity, acceleration,
                    volume_ratio, bb_position, speed_factor, consecutive_direction_count,
                    action, close_percentage, close_price,
                    profit_captured_pct, profit_captured_usd,
                    claude_consulted, claude_response, claude_response_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    side_str,
                    score_data.get("direction", "PROFIT"),
                    pos.entry_price,
                    det_price,
                    score_data.get("pnl_pct", 0),
                    pnl_usd,
                    hold_sec,
                    score_data.get("exploit_score", 0),
                    score_data.get("z_raw", 0),
                    score_data.get("velocity", 0),
                    score_data.get("acceleration", 0),
                    score_data.get("volume_ratio", 0),
                    score_data.get("bb_position", 0),
                    score_data.get("speed_factor", 0),
                    score_data.get("exhaustion", 0),
                    action,
                    close_pct,
                    close_price,
                    profit_captured_pct,
                    profit_captured_usd,
                    1 if claude_consulted else 0,
                    claude_response,
                    claude_response_time_ms,
                ),
            )
            row_id = cursor.lastrowid
            log.debug(
                "Spike recorded: {sym} score={sc} action={act} → row {rid}",
                sym=symbol, sc=score_data.get("exploit_score", 0), act=action, rid=row_id,
            )
            return row_id
        except Exception as e:
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.error(f"M4_SPIKE_RECORD_FAIL | sym={symbol} err='{str(e)[:120]}' | {ctx()}")
            return None

    async def _update_counterfactuals(self) -> None:
        """Feed current prices into active counterfactual trackers.

        Called once per tick. For each active counterfactual, reads the
        current price and appends. Triggers completion after 60 seconds.
        """
        now = time.time()
        completed = []

        for key, cf in self._counterfactuals.items():
            age = now - cf["detection_time"]

            price_data = await self._get_price_data(cf["symbol"])
            if price_data is not None:
                cf["prices_after"].append(price_data["price"])
                cf["timestamps_after"].append(now)

            if age >= 60:
                completed.append(key)

        for key in completed:
            await self._complete_counterfactual(key)

    async def _complete_counterfactual(self, key: str) -> None:
        """Complete a counterfactual after 60 seconds.

        Calculates what would have happened if Mode 4 had done the opposite,
        then UPDATE sniper_log with the results.
        """
        cf = self._counterfactuals.pop(key, None)
        if cf is None:
            return

        prices = cf["prices_after"]
        row_id = cf["sniper_log_id"]

        price_10s = prices[1] if len(prices) > 1 else None
        price_30s = prices[5] if len(prices) > 5 else None
        price_60s = prices[-1] if prices else None

        counterfactual_pnl = None
        sniper_value = None
        was_right = None

        if price_60s is not None and cf["entry_price"] > 0:
            entry = cf["entry_price"]
            is_long = cf["side"] in ("Buy", "BUY", "Long")

            if is_long:
                counterfactual_pnl = (price_60s - entry) / entry * 100
            else:
                counterfactual_pnl = (entry - price_60s) / entry * 100

            action = cf["action_taken"]
            captured = cf.get("profit_captured_pct") or 0

            if action in ("auto_full_close", "auto_partial_close", "full_close",
                          "claude_take", "claude_partial", "claude_timeout_auto",
                          "mode4_spike", "mode4_crash", "mode4_claude_take"):
                sniper_value = round(captured - counterfactual_pnl, 4)
                was_right = 1 if sniper_value > 0 else 0
            else:
                sniper_value = round(counterfactual_pnl - captured, 4)
                was_right = 1 if sniper_value >= 0 else 0

        try:
            await self.db.execute(
                """UPDATE sniper_log SET
                   price_after_10s = ?, price_after_30s = ?, price_after_60s = ?,
                   counterfactual_pnl_pct = ?, sniper_value_pct = ?, mode4_was_right = ?
                   WHERE id = ?""",
                (price_10s, price_30s, price_60s,
                 round(counterfactual_pnl, 4) if counterfactual_pnl is not None else None,
                 sniper_value, was_right, row_id),
            )
            verdict = "RIGHT" if was_right else "WRONG" if was_right is not None else "N/A"
            log.debug(
                "Counterfactual complete: {sym} action={act} value={sv} {v}",
                sym=cf["symbol"], act=cf["action_taken"],
                sv=f"{sniper_value:+.4f}%" if sniper_value is not None else "N/A",
                v=verdict,
            )
            # M8: Send counterfactual alert
            await self._send_counterfactual_alert(
                cf["symbol"], cf["action_taken"],
                cf.get("profit_captured_pct") or 0,
                counterfactual_pnl, sniper_value, bool(was_right),
            )
        except Exception as e:
            # Phase 12.6 (Gap 6.X-G1): structured tag.
            log.error(f"M4_COUNTERFACTUAL_FAIL | err='{str(e)[:120]}' | {ctx()}")

    # ─── M8: Integration — notifications ───────────────────────────

    async def _send_execution_alert(
        self, symbol: str, pos: Any, score_data: dict, action: str, pnl_pct: float
    ) -> None:
        """Send Telegram alert when Mode 4 executes a trade action."""
        try:
            if not self.alert_manager:
                return
            direction = score_data.get("direction", "PROFIT")
            score = score_data.get("exploit_score", 0)
            status = score_data.get("spike_status", "N/A")

            if direction == "PROFIT":
                msg = (
                    f"SNIPER: {symbol} {pnl_pct:+.2f}% captured\n"
                    f"Action: {action.replace('_', ' ').title()}\n"
                    f"Score: {score}/100 | Status: {status}\n"
                    f"H: {score_data.get('z_raw', 0.5):.3f} | "
                    f"Vol: {score_data.get('volume_ratio', 0):.1f}x"
                )
            else:
                msg = (
                    f"SNIPER: {symbol} {pnl_pct:+.2f}% loss cut\n"
                    f"Flash crash protection | Score: {score}/100\n"
                    f"V: {score_data.get('velocity', 0):+.6f}"
                )
            await self.alert_manager.send_custom(msg, AlertLevel.INFO)
        except Exception as e:
            log.debug("Mode4 alert failed: {err}", err=str(e))

    async def _send_counterfactual_alert(
        self,
        symbol: str,
        action: str,
        captured_pct: float,
        counterfactual_pnl: float | None,
        sniper_value: float | None,
        was_right: bool,
    ) -> None:
        """Send Telegram alert with counterfactual result."""
        try:
            if not self.alert_manager:
                return
            if action in ("no_action", "blocked_immunity", "blocked_cooldown"):
                return
            if counterfactual_pnl is None or sniper_value is None:
                return
            verdict = "RIGHT" if was_right else "WRONG"
            msg = (
                f"Sniper result: {symbol}\n"
                f"Closed at: {captured_pct:+.2f}%\n"
                f"Price now: {counterfactual_pnl:+.2f}%\n"
                f"Verdict: {verdict} ({sniper_value:+.2f}%)"
            )
            await self.alert_manager.send_custom(msg, AlertLevel.INFO)
        except Exception as e:
            log.debug("Mode4 counterfactual alert failed: {err}", err=str(e))

    def _notify_event_buffer(self, symbol: str, score_data: dict, action: str) -> None:
        """Notify EventBuffer of a Mode 4 action for Claude's context."""
        try:
            if not self.event_buffer:
                return
            self.event_buffer.add_event(
                "HIGH", "MODE4_SPIKE", symbol,
                action=action,
                direction=score_data.get("direction", "PROFIT"),
                pnl_captured_pct=round(score_data.get("pnl_pct", 0), 2),
                exploit_score=score_data.get("exploit_score", 0),
                spike_status=score_data.get("spike_status", ""),
                classification=score_data.get("classification", ""),
                cooldown_seconds=self.get_cooldown_remaining(symbol),
            )
        except Exception as e:
            log.debug("Mode4 event buffer notify failed: {err}", err=str(e))

    # ─── Counterfactual cleanup ────────────────────────────────────

    def _cleanup_counterfactuals(self) -> None:
        """Remove counterfactual entries older than 60 seconds."""
        now = time.time()
        expired = [
            symbol
            for symbol, data in self._recently_closed.items()
            if now - data["closed_at"] > 60
        ]
        for symbol in expired:
            log.debug(
                "ProfitSniper: counterfactual expired {sym}, buffer deleted",
                sym=symbol,
            )
            del self._recently_closed[symbol]

    # ─── Cleanup on shutdown ───────────────────────────────────────

    async def cleanup(self) -> None:
        """Clean up Mode 4 resources on shutdown."""
        tracked_count = len(self._tracked)
        closed_count = len(self._recently_closed)
        self._tracked.clear()
        self._recently_closed.clear()
        self._stale_skip_count.clear()
        self._cooldowns.clear()
        self._claude_last_query_time.clear()
        self._counterfactuals.clear()
        log.info(
            "ProfitSniper shutting down (ticks={t}, tracked={tr}, counterfactual={cf})",
            t=self._tick_count,
            tr=tracked_count,
            cf=closed_count,
        )
