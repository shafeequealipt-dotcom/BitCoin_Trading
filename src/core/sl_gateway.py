"""SL Gateway — single entry point for every stop-loss modification.

Background
==========
Six independent systems currently modify the stop-loss of every open position
(Claude entry SL, APEX override, SENTINEL Advisor, Profit Sniper trail,
Time-Decay tightening, watchdog trailing). Without coordination, last-write
wins and multiple systems can collide on the same symbol within seconds —
most visibly, Profit Sniper's trail has been observed jumping SL 2.5% in
one step to 0.08% from current price, strangling a position on 29s of
normal market noise.

This module consolidates those paths behind one validator. Every caller
passes through ``SLGateway.apply(...)``. The gateway enforces four rules:

  R1 Tighten-only — new SL must move TOWARDS current price (Buy: higher,
     Sell: lower). Never bypassable. Guards against accidental loosening.
  R2 Min-distance — new SL must be at least ``min_distance_pct`` away
     from current market price. Guards against bid-ask noise strangulation.
  R3 Max-step — new SL must not move more than ``max_step_pct`` from the
     previously-accepted SL per modification. Guards against aggressive
     "jumps" like RIVERUSDT. Bypassable via ``bypass_step_cap=True``.
  R4 Rate-limit — at least ``rate_limit_seconds`` must elapse between
     accepted modifications on the same symbol. Guards against thrash.
     Bypassable via ``bypass_rate_limit=True``.

Trade-state owner switch (2026-06-14, exit-authority consolidation)
===================================================================
Above the four rules sits an OWNER GATE that resolves the multi-writer
collision into one authority hierarchy. The gateway computes each trade's
green/red state from entry vs price (with a breakeven deadband + hysteresis)
and classifies every writer into a bucket: HEAD (the catastrophic cap —
always admitted, only tightens, the only thing that may seize a green trade),
GREEN owner (the profit engine — writes only when green), RED owner (the loss
engine — writes only when red), ADVISORY (brain/sentinel/watchdog-scoring —
deferred and surfaced as advice under ``advisory_enforce``), and ALWAYS (the
opening stop + naked-position sweeper — never blocked). The gate runs BEFORE
R1-R4, FAILS OPEN on any error (a gate bug can never block a protective
write), and ships LOG-ONLY by default (``owner_switch_enforce=false``). The
sniper spine reads ``peek_owner`` so its candidate selection agrees with the
gate (no starvation). All buckets and thresholds are centralized in
``[sl_gateway]`` config; ``head_only_seizes_green`` is the profit-priority
(Option A) switch. See verify_owner_switch.py for the behavior matrix.

Operational modes
=================
``enabled=false``
    Symmetric pass-through. Gateway still calls ``position_service
    .set_stop_loss`` and still tracks ``_last_sl`` state, but skips all
    four rule checks. Used during Phase 2 rollout to prove log/count
    parity with the pre-gateway baseline.

``log_only_global`` / ``log_only_<rule>``
    Would-be REJECTS become ``SL_GATEWAY_REJECT_WOULD`` log lines and the
    push proceeds. Used during staged Phase 3-7 enforcement rollout.

Wire contract
=============
The gateway is the single place that calls ``position_service
.set_stop_loss``. Callers handle their own post-accept side effects
(``plan.stop_loss_price`` mirror, ``SL_PROPAGATED`` log, coordinator
notifications) — the gateway stays domain-agnostic.

State is per-symbol in-memory; the event loop is single-threaded so no
locks are required. The "no await between check and state-update"
discipline is enforced by fetching ``current_sl`` and ``current_price``
upfront (the only awaits) and only updating ``_last_change`` /
``_last_sl`` after the wire push returns True.

Observability
=============
Per-event logs
    Every outcome emits a tagged log line with ``{ctx()}`` correlation
    suffix routing to workers.log (see logging.py COMPONENT_ROUTING):
      - ``SL_GATEWAY_INIT``    — boot banner with full config snapshot
      - ``SL_GATEWAY_ACCEPT``  — rule-passing wire push succeeded
      - ``SL_GATEWAY_REJECT``  — enforcement reject with reason
      - ``SL_GATEWAY_REJECT_WOULD`` — log-only mode would-reject
      - ``SL_GATEWAY_PASSTHROUGH`` — enabled=false wire push
      - ``SL_GATEWAY_WIRE_FAIL`` — downstream position_service failure
      - ``SL_GATEWAY_POS_FETCH_FAIL`` / ``_PRICE_FETCH_FAIL`` — resolution failure
    Owner-switch events (the trade-state hierarchy; emitted as literals at the
    cited call sites — listed here so they are greppable):
      - ``SL_GATEWAY_OWNER_SWITCH`` / ``SL_GATEWAY_BUCKETS`` — boot sentinels
      - ``SL_GATEWAY_OWNER_SWITCH_INCONSISTENT`` — boot warning: rearm flags
        not coordinated (faded_winner_rearm_red on, graduation_crater_rearm off)
      - ``SL_GATEWAY_OWNER_HANDOFF`` — a trade crossed the breakeven line
      - ``SL_GATEWAY_WRONG_OWNER`` / ``SL_GATEWAY_WRONG_OWNER_WOULD`` — a
        non-owner engine write deferred (enforce / log-only)
      - ``SL_GATEWAY_ADVISORY_DEFERRED`` / ``SL_GATEWAY_ADVISORY_DEFERRED_WOULD``
        — an advisory write deferred and surfaced to the owner (enforce / log-only)
      - ``SL_GATEWAY_HEAD_OVERRIDE`` — the Head tightening a green trade
      - ``SL_GATEWAY_OWNER_UNCLASSIFIED`` / ``SL_GATEWAY_OWNER_ERROR`` —
        fail-open paths (unbucketed source / gate exception)

Aggregated stats
    ``SL_GATEWAY_STATS`` summary emits every 300 seconds OR every 100
    events (whichever comes first) with outcome / source / reason
    breakdowns. Supports dashboard continuity and eyeballing trends
    without scanning raw event lines.

EventBuffer integration
    When an EventBuffer is injected (via DI from WorkerManager), the
    gateway surfaces two classes of events for Claude's next review:
      - ``sl_gateway_wire_fail`` (HIGH) — downstream is broken, operator
        attention needed; Claude will include it in the next prompt
      - ``sl_gateway_brain_blocked`` (MED) — a Claude-directed tighten
        (source=``brain_tighten`` or ``watchdog_tighten``) was rejected;
        Claude should reconsider its directive on the next cycle

See ``PROJECT_BIBLE.md`` for the original SL-hierarchy design spec and
``IMPLEMENT_EXIT_AUTHORITY_CONSOLIDATION.md`` + ``PHASE0_EXIT_AUTHORITY_FORENSICS.md``
(project root) for the exit-authority consolidation mandate and forensics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("sl_gateway")


# ═══════════════════════════════════════════════════════════════════════
# Reject reasons — closed enum. Rule violations + operational failures.
# ═══════════════════════════════════════════════════════════════════════

REASON_LOOSENING = "loosening"          # R1: would loosen the current SL
REASON_TOO_CLOSE = "too_close"          # R2: new SL within min_distance of price
REASON_STEP_EXCEEDED = "step_exceeded"  # R3: step from prev SL exceeds max
REASON_RATE_LIMIT = "rate_limit"        # R4: last change too recent
REASON_NO_POSITION = "no_position"      # operational: position not found
REASON_NO_PRICE = "no_price"            # operational: market service returned no ticker
REASON_WIRE_FAIL = "wire_fail"          # operational: position_service.set_stop_loss returned False / raised
REASON_INVALID_INPUT = "invalid_input"  # operational: new_sl <= 0 or direction unknown
REASON_CLAMP_NOOP = "clamp_noop"        # R2/R3 clamp: best valid stop does not improve on current SL — hold (no wire)
REASON_WRONG_SIDE = "wrong_side"        # terminal: stop on the wrong side of price — unplaceable, never wired (Issue 2.1)
REASON_FRESH_DEGRADE = "fresh_degrade"  # 2026-06-15: final stop re-validated against the freshest mark and degraded to a placeable value (avoids the wrong-side wire-fail give-back)
REASON_WRONG_OWNER = "wrong_owner"      # Phase 1 owner switch: write from a non-owner engine for the current trade state
REASON_ADVISORY_DEFER = "advisory_defer"  # Phase 5: an advisory writer (brain/sentinel/watchdog-scoring/trail) deferred — its input is routed to the owner instead

_VALID_DIRECTIONS = frozenset({"Buy", "Long", "Sell", "Short"})
_LONG_DIRECTIONS = frozenset({"Buy", "Long"})


# ═══════════════════════════════════════════════════════════════════════
# Result container — carries outcome plus diagnostic fields for the caller.
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SLGatewayResult:
    """Outcome of a single ``SLGateway.apply`` call.

    Attributes:
        accepted: True if the SL was propagated to the exchange (including
            log-only pass-through). False on reject or wire failure.
        reason: Closed-enum reason string — empty when accepted (unless
            a log-only flag fired, in which case the would-reject reason is
            preserved for auditability).
        old_sl: Previous SL on record, or None if unknown.
        new_sl_applied: The SL value actually sent to the exchange, or
            None if the call was rejected before the wire push.
    """

    accepted: bool
    reason: str = ""
    old_sl: float | None = None
    new_sl_applied: float | None = None


# ═══════════════════════════════════════════════════════════════════════
# Gateway implementation.
# ═══════════════════════════════════════════════════════════════════════


class SLGateway:
    """Single entry point for SL modifications.

    Inject via ``ServiceContainer`` and pass to every worker that can
    modify SL. Callers invoke ``await gateway.apply(...)`` in place of
    ``position_service.set_stop_loss`` (or the legacy
    ``_push_sl_to_shadow`` helper).

    State:
        ``_last_change`` — monotonic timestamp of last accepted push
        per symbol. Used for rate-limit rule.
        ``_last_sl`` — last accepted SL price per symbol. Used for the
        step-size rule and as fallback when the caller doesn't supply
        ``current_sl``.
    """

    # Stats summary cadence — whichever comes first triggers an emit
    STATS_EVENT_THRESHOLD = 100
    STATS_INTERVAL_SECONDS = 300

    # T2-5 (2026-05-12) — sources allowed to bypass R3 max_step_pct
    # via the `bypass_step_cap_for_breakeven` kwarg. The bypass is
    # narrowly scoped to profit-locking moves (sniper / sentinel
    # breakeven-protect paths) where the proposed step is large
    # because the position aged 45-50 min near breakeven and the
    # tightened SL distance from entry is naturally larger than the
    # 0.25 % per-tick max. Pre-T2-5 these were silently rejected as
    # step_exceeded; the position kept its original (further) SL,
    # exposing more capital. Adding new sources to this allowlist
    # requires a code change so the bypass scope stays auditable.
    # Profit-lock sources eligible for the R2 profit-lock-floor exemption
    # (Dynamic Adaptive Exit, 2026-06-15). These green-owner profit writers
    # supply an armed, R-derived, fee-floored profit lock that R2 holds at its
    # value (down to the lock, not just breakeven) so the lock writes instead of
    # being dropped as a clamp-noop. Auditable allowlist — adding a source is a
    # code change. Strict subset of _BREAKEVEN_BYPASS_SOURCES.
    _PROFIT_LOCK_SOURCES: frozenset[str] = frozenset({
        "profit_sniper_ladder",
        "profit_sniper_trail",
        "profit_sniper_lock",
    })

    _BREAKEVEN_BYPASS_SOURCES: frozenset[str] = frozenset({
        "profit_sniper_lock",     # sniper's _attempt_profit_lock path
        "profit_sniper_breakeven",  # reserved for future explicit BE move
        "sentinel_breakeven",     # reserved for future SENTINEL BE move
        # Profit-Fetching Exit System ladder (2026-05-29). Each ladder step
        # raises the lock by ~one step-spacing (~0.5%), exceeding the R3
        # max_step (0.25%) cap. These are legitimate, monotonic, profit-
        # locking tightens, so the ladder source bypasses R3 ONLY. R1
        # tighten-only, R2 min-distance, and R4 rate-limit still apply — the
        # ladder can neither place a stop pathologically close to price nor
        # write faster than the per-symbol window.
        "profit_sniper_ladder",
        # Profit-Fetching safety stop / naked-position sweeper (2026-05-29).
        # Re-asserting the loss-cap floor on a looser existing stop is one
        # larger protective tighten that exceeds R3; legitimate, so it bypasses
        # R3 only (R1 tighten-only, R2 min-distance, R4 rate-limit still apply).
        "safety_sweeper",
        # Loss-Cutting System (2026-05-31). Each is a legitimate protective
        # tighten that can exceed the 0.25% R3 max-step in a single move (the
        # sacred cap and the catastrophe/structure stops are placed at their
        # true distance, not ratcheted a quarter-step at a time). They bypass
        # R3 ONLY — R1 tighten-only and R2 min-distance still hold, so none can
        # loosen a stop or place one pathologically close to price.
        "loss_cap",            # the sacred hard cap placed as an SL
        "loss_cap_emergency",  # underwater just-inside-price emergency cap
        "loss_atr_initial",    # ATR-based initial stop at position open
        "loss_structure",      # structure stop just beyond X-RAY invalidation
        "loss_recovery",       # final-phase recovery bounce-capture trail
        # Profit-Fetching Chandelier runner trail (Finding H, 2026-06-08).
        # On a fast vertical runner the peak-anchored Chandelier (high-water -
        # ATR leash) wins highest-stop-wins but, NOT being bypassed, was clamped
        # to R3 max-step 0.25%/tick — so the protected floor lagged the peak by
        # up to ~1.4% per write (proven live on AAVE: chandelier raw 64.197
        # clamped to 63.298, src=profit_sniper_trail). Worse, the spine selected
        # the higher chandelier candidate which then clamped BELOW the bypassed
        # ladder candidate, a perverse give-back. The trail is a legitimate,
        # monotonic, peak-anchored protective tighten: bypass R3 ONLY (R1
        # tighten-only, R2 min-distance, R4 rate-limit still apply), with the
        # ATR leash as the sole noise guard so it captures more of a runner
        # without whipsawing. The leash distance is unchanged — only the SPEED
        # the floor reaches (high_water - leash) is unthrottled.
        "profit_sniper_trail",
        # NOTE: the volatility-spike catastrophe stop force-CLOSES the position
        # (closed_by=loss_spike_force); it never writes an SL, so it needs no
        # bypass source here.
    })

    # ─── T2-6 (2026-05-12): rate-limit-aware coordination API ─────

    def _rate_limit_window_for(self, source: str, cfg) -> float:
        """The R4 rate-limit window (seconds) for ``source``. The profit-lock
        lane (``_PROFIT_LOCK_SOURCES``) may use a SHORTER window
        (``profit_lock_rate_limit_seconds``) so a sustained peak is tracked at
        finer cadence; every other source keeps the base ``rate_limit_seconds``.
        Clamped to <= base, so the default (profit_lock_rate_limit_seconds ==
        rate_limit_seconds) is INERT and the lane can only ever go faster, never
        slower. The fresh-mark degrade and the terminal wrong-side guard run
        BEFORE R4 with no bypass, so a shorter window here cannot weaken the
        wire-fail safety; tighten-only (also un-bypassable) prevents whipsaw.
        """
        base = float(getattr(cfg, "rate_limit_seconds", 30))
        if source in self._PROFIT_LOCK_SOURCES:
            fast = float(getattr(cfg, "profit_lock_rate_limit_seconds", base))
            if fast > 0:
                return min(base, fast)
        return base

    def next_eligible_in_seconds(self, symbol: str, source: str | None = None) -> float:
        """Return seconds until ``symbol`` is next eligible for an SL
        modification under the R4 rate-limit window.

        Returns:
            ``0.0`` when the symbol is currently eligible (no rate
            limit pending OR window already elapsed).
            ``> 0.0`` when there are still seconds remaining in the
            rate-limit window.

        Pre-T2-6 sniper had no public API for this — it would call
        ``apply()`` every 5 s, get rejected with ``REASON_RATE_LIMIT``,
        log spam, and burn compute. Production logs showed 127
        rejections in 2 h 42 m (FILUSDT 37, BLURUSDT 23, RENDERUSDT 18,
        ARBUSDT 17, ENAUSDT 10, others). Now sniper consults this
        accessor first and emits a SNIPER_RATE_LIMIT_AWARE_SKIP log
        line on its own side, never reaching the gateway when
        ineligible.

        Thread-safety: read of ``self._last_change`` is a single dict
        lookup; ``time.monotonic()`` is monotonic + thread-safe.
        Concurrent writes to ``_last_change`` (from a successful
        ``apply()``) race-monotonically — the worst case is a
        slightly-stale "still ineligible" reading that converges on
        the next 5 s tick. No lock needed.
        """
        cfg = getattr(self._settings, "sl_gateway", None)
        if cfg is None or not getattr(cfg, "enabled", False):
            return 0.0
        last_change = self._last_change.get(symbol, 0.0)
        if last_change <= 0:
            return 0.0
        elapsed = time.monotonic() - last_change
        window = (self._rate_limit_window_for(source, cfg)
                  if source is not None else cfg.rate_limit_seconds)
        remaining = window - elapsed
        if remaining <= 0:
            return 0.0
        return float(remaining)

    def __init__(
        self,
        settings: Any,
        position_service: Any,
        market_service: Any,
        event_buffer: Any = None,
        volatility_profiler: Any = None,
    ) -> None:
        self._settings = settings
        self._position_service = position_service
        self._market_service = market_service
        self._event_buffer = event_buffer
        # Optional profiler reference. When present, R2 (min_distance) uses
        # vol_scale.min_distance_for_class to derive an ATR-scaled effective
        # min; when None, R2 falls back to the legacy static
        # `cfg.min_distance_pct`. The profiler has a 60s in-memory TTL so the
        # `await get_profile(symbol)` on the R2 path is amortised ~free.
        self._volatility_profiler = volatility_profiler
        # System 3 observability — per-attempt placement forensics. One
        # PLACEMENT_FORENSIC line per profit-lock placement attempt is written to
        # its own rotated file (the "placement_forensic" log route), fire-and-
        # forget and behaviour-neutral. Gated by
        # observability.placement_forensic_enabled (default on).
        self._pf_log = get_logger("placement_forensic")
        self._pf_enabled = bool(getattr(
            getattr(settings, "observability", None),
            "placement_forensic_enabled", True))
        self._last_change: dict[str, float] = {}
        self._last_sl: dict[str, float] = {}
        # Phase 1 owner switch — per-symbol authority memory.
        # ``_last_owner`` holds the last DEFINITE owner ("green"/"red") so the
        # breakeven deadband can hold the last owner inside the band (hysteresis,
        # no thrash at exactly breakeven). ``_ever_green`` records whether the
        # trade has ever crossed into profit, for the faded-winner rule. Both
        # are cleared per-symbol on position close via reset_symbol().
        self._last_owner: dict[str, str] = {}
        self._ever_green: dict[str, bool] = {}

        # Observability counters — bounded (keys are the finite set of
        # outcomes / sources / reasons). Reset on every SL_GATEWAY_STATS
        # emission so each summary line represents one 5-min window.
        self._cnt_by_outcome: dict[str, int] = {
            "accept": 0, "reject": 0, "would": 0,
            "passthrough": 0, "wire_fail": 0,
        }
        self._cnt_by_source: dict[str, int] = {}
        self._cnt_by_reason: dict[str, int] = {}
        self._events_since_emit: int = 0
        self._last_stats_emit_ts: float = time.monotonic()
        self._start_ts: float = time.monotonic()
        # Boot sentinel (Rule 14) — make the placement-forensics gate visible in
        # the operator-facing log so its enabled/disabled state is auditable.
        log.info(
            f"PLACEMENT_FORENSIC_INIT | enabled={self._pf_enabled} | per-attempt "
            f"profit-lock placement forensics -> placement_forensic.log "
            f"(piggyback mark, behaviour-neutral, fire-and-forget)"
        )

        cfg = getattr(settings, "sl_gateway", None)
        # Phase 1 owner-switch bucket memberships, loaded once from config
        # (centralized, Rule 9). Empty frozensets when config is absent — the
        # gate then classifies every source as "unclassified" and fails open.
        self._head_sources: frozenset[str] = frozenset(
            getattr(cfg, "head_sources", None) or [] if cfg is not None else []
        )
        self._green_sources: frozenset[str] = frozenset(
            getattr(cfg, "green_sources", None) or [] if cfg is not None else []
        )
        self._red_sources: frozenset[str] = frozenset(
            getattr(cfg, "red_sources", None) or [] if cfg is not None else []
        )
        self._advisory_sources: frozenset[str] = frozenset(
            getattr(cfg, "advisory_sources", None) or [] if cfg is not None else []
        )
        self._always_sources: frozenset[str] = frozenset(
            getattr(cfg, "always_allowed_sources", None) or [] if cfg is not None else []
        )
        if cfg is not None:
            log.info(
                "SL_GATEWAY_INIT | enabled={e} log_only_global={lg} "
                "min_dist_pct={md} max_step_pct={ms} rate_limit_s={rl} "
                "profit_lock_rl_s={plrl} "
                "log_only_tighten_only={lt} log_only_min_distance={lmd} "
                "log_only_max_step={lms} log_only_rate_limit={lrl} "
                "event_buffer={eb} volatility_profiler={vp} "
                "atr_mult={am} abs_floor={af} r2_be_floor={r2} "
                "r2_profit_lock_floor={r2pl} fresh_mark_degrade={fmd} "
                "log_only_fresh_mark_degrade={lfmd}",
                e=cfg.enabled,
                lg=cfg.log_only_global,
                md=cfg.min_distance_pct,
                ms=cfg.max_step_pct,
                rl=cfg.rate_limit_seconds,
                plrl=getattr(cfg, "profit_lock_rate_limit_seconds", cfg.rate_limit_seconds),
                lt=cfg.log_only_tighten_only,
                lmd=cfg.log_only_min_distance,
                lms=cfg.log_only_max_step,
                lrl=cfg.log_only_rate_limit,
                eb="wired" if event_buffer is not None else "absent",
                vp="wired" if volatility_profiler is not None else "absent",
                am=getattr(cfg, "min_distance_atr_multiplier", 0.5),
                af=getattr(cfg, "min_distance_abs_floor_pct", 0.05),
                # PF/LC Top-15 Problem 1.1 boot sentinel (Rule 14).
                r2=getattr(cfg, "r2_breakeven_floor_enabled", True),
                # Dynamic Adaptive Exit profit-lock-floor sentinel (Rule 14).
                r2pl=getattr(cfg, "r2_profit_lock_floor_enabled", False),
                # Dynamic Adaptive Exit FIX — fresh-mark degrade sentinel (Rule 14).
                fmd=getattr(cfg, "r2_fresh_mark_degrade_enabled", True),
                lfmd=getattr(cfg, "log_only_fresh_mark_degrade", False),
            )
            # Finding H boot sentinel (Rule 14): make the R3 max-step bypass
            # allowlist visible at startup so the operator can confirm the
            # Chandelier runner trail (profit_sniper_trail) is now bypassed.
            log.info(
                "SL_GATEWAY_R3_BYPASS_SOURCES | {srcs}",
                srcs=",".join(sorted(self._BREAKEVEN_BYPASS_SOURCES)),
            )
            # Phase 1 owner-switch boot sentinels (Rule 12) — make the
            # hierarchy's state and bucket memberships visible at startup so the
            # operator can confirm what loaded and in which mode.
            log.info(
                "SL_GATEWAY_OWNER_SWITCH | enabled={e} enforce={en} "
                "advisory_enforce={ae} head_only_seizes_green={hg} "
                "faded_winner_rearm_red={fr} breakeven_deadband_pct={db}",
                e=getattr(cfg, "owner_switch_enabled", False),
                en=getattr(cfg, "owner_switch_enforce", False),
                ae=getattr(cfg, "advisory_enforce", False),
                hg=getattr(cfg, "head_only_seizes_green", True),
                fr=getattr(cfg, "faded_winner_rearm_red", False),
                db=getattr(cfg, "breakeven_deadband_pct", 0.05),
            )
            log.info(
                "SL_GATEWAY_BUCKETS | head={h} green={g} red={r} "
                "advisory={a} always={al}",
                h=",".join(sorted(self._head_sources)) or "none",
                g=",".join(sorted(self._green_sources)) or "none",
                r=",".join(sorted(self._red_sources)) or "none",
                a=",".join(sorted(self._advisory_sources)) or "none",
                al=",".join(sorted(self._always_sources)) or "none",
            )
            # Coordination guard (audit MEDIUM): the faded-winner re-arm and the
            # sniper's graduation_crater_rearm MUST be flipped together. If
            # faded_winner_rearm_red hands a faded (graduated-then-red) winner to
            # the red owner but graduation_crater_rearm_enabled is off, the
            # sniper's loss block stays gated by the graduation latch and builds
            # NO red-owner tools — the red owner would own the stop with nothing
            # to write (only the always-on spike force-close and the resting stop
            # protect it). Surface this loudly at boot so the inconsistency
            # cannot ship silently. Inert in the shipped default (both off).
            _lc_cfg = getattr(settings, "loss_cutting", None)
            if getattr(cfg, "faded_winner_rearm_red", False) and not getattr(
                _lc_cfg, "graduation_crater_rearm_enabled", False
            ):
                log.warning(
                    "SL_GATEWAY_OWNER_SWITCH_INCONSISTENT | "
                    "faded_winner_rearm_red=true but "
                    "loss_cutting.graduation_crater_rearm_enabled=false — a faded "
                    "winner would be handed to the red owner with no tools (the "
                    "sniper loss block stays graduation-gated). Enable "
                    "graduation_crater_rearm_enabled in tandem before enforcing "
                    "the re-arm, or leave faded_winner_rearm_red=false."
                )
        else:
            log.warning(
                "SL_GATEWAY_INIT | settings.sl_gateway missing — defaulting "
                "to DISABLED pass-through (event_buffer={eb} profiler={vp})",
                eb="wired" if event_buffer is not None else "absent",
                vp="wired" if volatility_profiler is not None else "absent",
            )

    # ── Public API ─────────────────────────────────────────────────────

    async def apply(
        self,
        *,
        symbol: str,
        new_sl: float,
        source: str,
        direction: str,
        plan: Any = None,
        current_sl: float | None = None,
        current_price: float | None = None,
        reason: str | None = None,
        bypass_rate_limit: bool = False,
        bypass_step_cap: bool = False,
        bypass_step_cap_for_breakeven: bool = False,
        breakeven_floor_price: float | None = None,
        entry_price: float | None = None,
        profit_lock_floor_price: float | None = None,
    ) -> SLGatewayResult:
        """Public entry point. Delegates to the unchanged rule engine
        ``_apply_impl`` and emits one fire-and-forget PLACEMENT_FORENSIC line per
        profit-lock placement attempt.

        Behaviour-neutral by construction: ``_apply_impl`` is the verbatim 4-rule
        engine; this wrapper only builds the forensic capture dict, threads it
        through, and — for ``_PROFIT_LOCK_SOURCES`` only — emits a forensic line
        AFTER the decision is made. The emit is gated, wrapped in try/except, and
        reads only already-computed values, so it can never change the placement
        decision or the returned result. It adds no exchange API call: the live
        mark is logged only when ``_apply_impl``'s fresh-mark degrade already
        fetched it (a pure piggyback); otherwise it is ``na``.
        """
        _fx: dict = {
            "reached_eval": False, "proposed": new_sl, "snap": current_price,
            "cur_sl": current_sl, "entry": entry_price, "dir": direction,
            "eff_min": None, "boundary": None, "fresh": "na",
            "r2": False, "degraded": False,
        }
        result = await self._apply_impl(
            symbol=symbol, new_sl=new_sl, source=source, direction=direction,
            plan=plan, current_sl=current_sl, current_price=current_price,
            reason=reason, bypass_rate_limit=bypass_rate_limit,
            bypass_step_cap=bypass_step_cap,
            bypass_step_cap_for_breakeven=bypass_step_cap_for_breakeven,
            breakeven_floor_price=breakeven_floor_price, entry_price=entry_price,
            profit_lock_floor_price=profit_lock_floor_price, _fx=_fx,
        )
        if (self._pf_enabled and source in self._PROFIT_LOCK_SOURCES
                and _fx.get("reached_eval")):
            try:
                self._emit_placement_forensic(symbol, source, _fx, result)
            except Exception:
                pass
        return result

    async def _apply_impl(
        self,
        *,
        symbol: str,
        new_sl: float,
        source: str,
        direction: str,
        plan: Any = None,
        current_sl: float | None = None,
        current_price: float | None = None,
        reason: str | None = None,
        bypass_rate_limit: bool = False,
        bypass_step_cap: bool = False,
        bypass_step_cap_for_breakeven: bool = False,
        breakeven_floor_price: float | None = None,
        entry_price: float | None = None,
        profit_lock_floor_price: float | None = None,
        _fx: dict | None = None,
    ) -> SLGatewayResult:
        """Validate ``new_sl`` against the 4 rules and push to the exchange.

        Args:
            symbol: Trading pair.
            new_sl: Proposed new stop-loss price.
            source: Short identifier of the calling system (e.g.
                ``"time_decay"``, ``"profit_sniper_trail"``,
                ``"sentinel_advisor"``). Embedded in accept/reject logs
                to disambiguate origins.
            direction: Position direction. One of Buy/Long/Sell/Short.
            plan: Optional TradePlan reference. Unused by the gateway
                itself — callers handle plan mirror after accept.
            current_sl: Previous SL (from Shadow or trade plan). If None,
                the gateway consults its own ``_last_sl`` cache, then
                falls back to fetching the live position.
            current_price: Live mark price. If None, the gateway fetches
                via market_service.
            reason: Optional free-text justification (e.g. ``"p_win=0.22"``).
                Included in accept/reject logs for audit.
            bypass_rate_limit: Skip R4. For urgent Time-Decay force-exits
                that happen to go through the SL path (rare).
            bypass_step_cap: Skip R3. For the same rare case.
            bypass_step_cap_for_breakeven: T2-5 (2026-05-12). Caller
                requests R3 bypass specifically for a breakeven /
                profit-lock move. Honored ONLY when ``source`` is in
                ``_BREAKEVEN_BYPASS_SOURCES`` (sniper/sentinel
                breakeven paths). Always logged via
                ``SL_GATEWAY_BREAKEVEN_OVERRIDE`` so each large-step
                breakeven move is visible. R1 (tighten-only) is
                NEVER bypassed — a breakeven move that would loosen
                SL is still rejected.
            breakeven_floor_price: PF/LC Top-15 Problem 1.1
                (2026-06-04). The trade's breakeven price (entry).
                When supplied AND ``source`` is in
                ``_BREAKEVEN_BYPASS_SOURCES`` AND
                ``r2_breakeven_floor_enabled`` is set, the R2
                min-distance clamp may move the stop toward price only
                down to this breakeven price, never past it — so an
                armed ladder floor on a high-volatility coin holds AT
                breakeven instead of being rewritten below it. R1
                tighten-only is re-checked after the clamp; min-distance
                is unchanged for every write that does not pass this.
            entry_price: Phase 1 owner switch (2026-06-14). The trade's
                entry price, supplied by the caller (all callers know it
                cheaply). Used ONLY by the owner gate to compute the
                trade's green/red state relative to entry. When omitted,
                the gate falls back to ``breakeven_floor_price``; when
                neither is available the gate cannot determine state and
                fails OPEN (admits the write). Never used by R1-R4.

        Returns:
            SLGatewayResult describing the outcome. ``accepted=True``
            guarantees the wire push to the exchange succeeded (or that
            we were in pass-through mode and the wire push also
            succeeded).
        """
        # Forensic capture dict (behaviour-neutral; populated for the apply()
        # wrapper's PLACEMENT_FORENSIC line). Never None in production — the
        # wrapper always supplies it — but guard so a direct _apply_impl call
        # cannot crash on a forensic write.
        if _fx is None:
            _fx = {}
        # ── Input sanity ──
        if new_sl is None or not isinstance(new_sl, (int, float)) or new_sl <= 0:
            self._log_reject(
                symbol, REASON_INVALID_INPUT, source,
                extra=f"new_sl={new_sl!r}",
            )
            return SLGatewayResult(accepted=False, reason=REASON_INVALID_INPUT)
        if direction not in _VALID_DIRECTIONS:
            self._log_reject(
                symbol, REASON_INVALID_INPUT, source,
                extra=f"direction={direction!r}",
            )
            return SLGatewayResult(accepted=False, reason=REASON_INVALID_INPUT)

        cfg = getattr(self._settings, "sl_gateway", None)
        enabled = bool(cfg.enabled) if cfg is not None else False

        # ── Resolve current_sl (cache → caller → service) ──
        if current_sl is None or current_sl <= 0:
            current_sl = self._last_sl.get(symbol)
        if current_sl is None or current_sl <= 0:
            # Last-resort: fetch live position. Rare; only when neither
            # the caller nor the cache has a prior SL (e.g. first push
            # after a restart for an already-open position).
            try:
                pos = await self._position_service.get_position(symbol)
                if pos is not None:
                    sl_attr = getattr(pos, "stop_loss", None)
                    if sl_attr and sl_attr > 0:
                        current_sl = float(sl_attr)
            except Exception as e:
                log.debug(
                    f"SL_GATEWAY_POS_FETCH_FAIL | sym={symbol} "
                    f"src={source} err='{str(e)[:80]}' | {ctx()}"
                )

        # ── Pass-through path (enabled=false) ──
        # Still push, still update state, emit PASSTHROUGH log so Phase-2
        # count parity holds. Skip all rule checks.
        if not enabled:
            ok = await self._wire_push(symbol, new_sl, source)
            if not ok:
                # _wire_push already tracked the wire_fail; notify Claude.
                self._notify_event_buffer(
                    "HIGH", "sl_gateway_wire_fail", symbol,
                    source=source, new_sl=round(new_sl, 6),
                    mode="passthrough",
                )
                return SLGatewayResult(
                    accepted=False, reason=REASON_WIRE_FAIL,
                    old_sl=current_sl,
                )
            self._last_sl[symbol] = new_sl
            # Do NOT update _last_change — rate-limit state only makes
            # sense when enforcement is active; seeding it here would
            # cause the first real rule evaluation after enabling to
            # unfairly reject legitimate tightens.
            prev_str = f"{current_sl:.6f}" if current_sl else "unknown"
            log.info(
                f"SL_GATEWAY_PASSTHROUGH | sym={symbol} new={new_sl:.6f} "
                f"prev={prev_str} src={source} | {ctx()}"
            )
            self._track("passthrough", source)
            return SLGatewayResult(
                accepted=True, reason="", old_sl=current_sl,
                new_sl_applied=new_sl,
            )

        # ── Resolve current_price (caller → market service) ──
        if current_price is None or current_price <= 0:
            try:
                ticker = await self._market_service.get_ticker(symbol)
                if ticker is not None:
                    current_price = float(ticker.last_price)
            except Exception as e:
                log.debug(
                    f"SL_GATEWAY_PRICE_FETCH_FAIL | sym={symbol} "
                    f"src={source} err='{str(e)[:80]}' | {ctx()}"
                )
        if current_price is None or current_price <= 0:
            self._log_reject(symbol, REASON_NO_PRICE, source)
            return SLGatewayResult(
                accepted=False, reason=REASON_NO_PRICE, old_sl=current_sl,
            )

        # Forensic capture (behaviour-neutral): this attempt reached rule
        # evaluation with a resolved snapshot/state. Record the resolved values.
        _fx["reached_eval"] = True
        _fx["snap"] = current_price
        _fx["cur_sl"] = current_sl
        _fx["proposed"] = new_sl
        _fx["entry"] = entry_price
        _fx["dir"] = direction

        # ── Rule evaluation (synchronous — no await from here until push) ──
        is_long = direction in _LONG_DIRECTIONS
        log_only_global = bool(cfg.log_only_global)
        now = time.monotonic()

        # ── Owner gate (Phase 1: trade-state authority) ──────────────────
        # Decide whether this writer is the rightful owner of the stop given
        # the trade's current state (green/red relative to entry). Evaluated
        # before R1-R4. Fails OPEN on any internal error and admits the write —
        # a gate bug can never block a protective stop. In log-only mode
        # (owner_switch_enforce=false) a non-owner write is logged as a
        # WOULD-defer and still allowed through, preserving behavior parity.
        # The Head (catastrophic cap) and the always-allowed initial/naked
        # writers are admitted regardless of state inside _owner_gate.
        (
            _og_admit, _og_state, _og_owner, _og_bucket, _og_pnl,
        ) = self._owner_gate(
            symbol=symbol,
            source=source,
            is_long=is_long,
            entry_price=(
                entry_price if (entry_price and entry_price > 0)
                else breakeven_floor_price
            ),
            current_price=current_price,
        )
        if not _og_admit:
            # Phase 5: an ADVISORY writer (brain / sentinel / watchdog-scoring /
            # the watchdog green-side trails) does not write the stop directly —
            # it advises the owning engine. A deferred advisory write is logged
            # as SL_GATEWAY_ADVISORY_DEFERRED and its proposed stop is routed to
            # the owner via the EventBuffer (the owner's next decision cycle sees
            # it), rather than treated as a contending owner conflict. Every
            # other deferred write is a wrong-owner engine write.
            _is_advisory = _og_bucket == "advisory"
            _reason = REASON_ADVISORY_DEFER if _is_advisory else REASON_WRONG_OWNER
            _evt = "SL_GATEWAY_ADVISORY_DEFERRED" if _is_advisory else "SL_GATEWAY_WRONG_OWNER"
            if bool(getattr(cfg, "owner_switch_enforce", False)):
                log.info(
                    f"{_evt} | sym={symbol} src={source} "
                    f"bucket={_og_bucket} state={_og_state} owner={_og_owner} "
                    f"pnl_pct={_og_pnl:.3f} new_sl={new_sl:.6f} action=defer | "
                    f"{ctx()}"
                )
                if _is_advisory:
                    # Surface the advice to the brain's next review via the
                    # EventBuffer (the proposed stop + the current owner). NOTE:
                    # this is a review-surface signal, not a mechanical input the
                    # profit/loss engine reads — under profit-priority (Option A)
                    # the advisory's stop is intentionally dropped from direct
                    # effect, and the owning engine decides on its own logic. The
                    # brain (itself advisory) may act on it next cycle. Confirm
                    # this is the intended behavior at the Phase 5 gate before
                    # enforcing advisory_enforce.
                    self._notify_event_buffer(
                        "MED", "sl_gateway_advisory_deferred", symbol,
                        source=source, new_sl=round(new_sl, 6),
                        owner=_og_owner, state=_og_state,
                    )
                self._track("reject", source, _reason)
                return SLGatewayResult(
                    accepted=False, reason=_reason, old_sl=current_sl,
                )
            log.info(
                f"{_evt}_WOULD | sym={symbol} src={source} "
                f"bucket={_og_bucket} state={_og_state} owner={_og_owner} "
                f"pnl_pct={_og_pnl:.3f} new_sl={new_sl:.6f} action=would_defer | "
                f"{ctx()}"
            )
            self._track("would", source, _reason)

        # R1 Tighten-only. Never bypassable.
        if current_sl is not None and current_sl > 0:
            loosens = (is_long and new_sl <= current_sl) or (
                not is_long and new_sl >= current_sl
            )
            if loosens:
                if log_only_global or cfg.log_only_tighten_only:
                    self._log_reject_would(
                        symbol, REASON_LOOSENING, source,
                        extra=f"new={new_sl:.6f} cur={current_sl:.6f} dir={direction}",
                    )
                else:
                    self._log_reject(
                        symbol, REASON_LOOSENING, source,
                        extra=f"new={new_sl:.6f} cur={current_sl:.6f} dir={direction}",
                    )
                    return SLGatewayResult(
                        accepted=False, reason=REASON_LOOSENING,
                        old_sl=current_sl,
                    )

        # R2 Min-distance between new_sl and current_price.
        # Rounded to 6 decimal places to eliminate float-precision edge
        # cases at the exact threshold (e.g. (100-99.7)/100*100 actually
        # evaluates to 0.29999999999999716 which would spuriously reject
        # an SL the operator intended to be exactly 0.3% away).
        #
        # ATR-scaled effective min: when the profiler is wired and the
        # symbol has a fresh profile, use vol_scale.min_distance_for_class
        # to compute `eff_min` from atr_5m_pct × mult (per user spec, 0.5×).
        # Dead coins (ATR~0.04%) land near 0.05% instead of 0.30% (base),
        # unblocking Profit Sniper trails that today get rejected 160/160.
        # Falls back to base `cfg.min_distance_pct` when profile unavailable.
        _eff_min = cfg.min_distance_pct
        _vp_atr = 0.0
        _vp_cls: str | None = None
        if self._volatility_profiler is not None:
            try:
                _vp = await self._volatility_profiler.get_profile(symbol)
                if _vp is not None and _vp.atr_pct_5m > 0:
                    from src.analysis.vol_scale import min_distance_for_class
                    _vp_atr = float(_vp.atr_pct_5m)
                    _vp_cls = _vp.volatility_class
                    _eff_min = min_distance_for_class(_vp_atr, _vp_cls, cfg)
            except Exception as e:
                log.debug(
                    f"SL_GATEWAY_VP_FAIL | sym={symbol} err='{str(e)[:80]}' | {ctx()}"
                )
        _fx["eff_min"] = _eff_min  # forensic capture (behaviour-neutral)

        dist_pct = round(abs(current_price - new_sl) / current_price * 100.0, 6)
        # R2 Min-distance — CLAMP-and-apply (Profit-Fetching restoration,
        # 2026-05-30). Pre-fix this rejected wholesale any stop inside the
        # ATR-scaled min-distance which, together with R3, froze the
        # ladder/chandelier spine at a ~1.7% accept rate and let winners
        # round-trip to a loss. Now the gateway computes the closest VALID
        # stop — exactly eff_min from price on the correct side — and clamps
        # to it, so a protective stop can always move up while the
        # min-distance discipline (no stops on bid-ask noise) and tighten-only
        # (re-checked after the clamps) are both preserved. A stop that lands
        # on the WRONG side of price after a fast retrace (the NEAR failure)
        # is the same case: new_sl is beyond the boundary, so it is clamped to
        # the highest valid stop just inside the min-distance instead of being
        # rejected and re-spammed. log_only mode keeps the legacy observe-only
        # pass-through (raw value forwarded, would-clamp logged).
        _r2_boundary = self._eff_min_boundary(current_price, is_long, _eff_min)
        _fx["boundary"] = _r2_boundary  # forensic capture (behaviour-neutral)
        _r2_violates = (new_sl > _r2_boundary) if is_long else (new_sl < _r2_boundary)
        # PF/LC Top-15 Problem 1.1 — breakeven floor on the R2 clamp. R3
        # already exempts armed ladder/breakeven moves; R2 did not, so on a
        # high-volatility coin whose eff_min exceeds the trade's profit-above-
        # entry, R2 rewrote the armed floor to a stop BELOW breakeven (confirmed
        # live 68× / 30 symbols). When the caller supplies the trade's breakeven
        # price for a trusted breakeven source, R2 now clamps toward price only
        # down to breakeven — clamp_to = max(be, boundary) for a long,
        # min(be, boundary) for a short — never past it. On a calm coin the
        # boundary already sits at/above breakeven, so this is a no-op; only the
        # squeezed high-vol case is changed, and there the worst case is a
        # zero-loss (breakeven) exit. R1 tighten-only is re-checked below.
        _r2_target = _r2_boundary
        _r2_floor_held = False
        _be_floor_active = (
            getattr(cfg, "r2_breakeven_floor_enabled", True)
            and isinstance(breakeven_floor_price, (int, float))
            and breakeven_floor_price > 0
            and source in self._BREAKEVEN_BYPASS_SOURCES
        )
        if _r2_violates and _be_floor_active:
            if is_long:
                _bounded = round(max(float(breakeven_floor_price), _r2_boundary), 8)
                # Issue 2.1 (2026-06-07): on a round-trip the price can fall BELOW
                # the breakeven floor, so holding the floor (the max above) would
                # place the stop at/above current price — wrong-side for a long
                # and unplaceable (the BLUR retry-spam root). Never hold the floor
                # on the wrong side: if it is not strictly below price, clamp it
                # down to the min-distance boundary (just below price, the
                # operator's chosen behaviour). The high-vol Problem-1.1 case
                # (floor below price but above the boundary) is unaffected; R1
                # tighten-only below drops it if it is not an improvement.
                if _bounded >= current_price:
                    _bounded = _r2_boundary
            else:
                _bounded = round(min(float(breakeven_floor_price), _r2_boundary), 8)
                # Symmetric for a short: never hold the floor at/below price.
                if _bounded <= current_price:
                    _bounded = _r2_boundary
            if abs(_bounded - _r2_boundary) > 1e-12:
                _r2_target = _bounded
                _r2_floor_held = True
        # Profit-lock floor (Dynamic Adaptive Exit, 2026-06-15) — the clamp-noop
        # enabler. When a trusted profit source supplies an armed, R-derived,
        # fee-floored profit lock, R2 holds the clamp at the lock's value (down
        # to the lock, raising _r2_target FURTHER toward price than the
        # breakeven-only hold) so the lock writes instead of being dropped as a
        # clamp-noop (the breakeven-only hold did not improve on a stop already
        # at breakeven). Never held at/past price (wrong-side guard); R1
        # tighten-only is re-checked below; the absolute min-distance floor in
        # the lock itself (>= the round-trip fee) keeps it off bid-ask noise.
        # Gated by r2_profit_lock_floor_enabled and the source allowlist; off by
        # default so this is inert until the operator enables it.
        _profit_lock_held = False
        _profit_lock_active = (
            getattr(cfg, "r2_profit_lock_floor_enabled", False)
            and isinstance(profit_lock_floor_price, (int, float))
            and profit_lock_floor_price > 0
            and source in self._PROFIT_LOCK_SOURCES
        )
        if _r2_violates and _profit_lock_active:
            if is_long:
                _pl = round(max(float(profit_lock_floor_price), _r2_target), 8)
                if _pl >= current_price:        # never wrong-side for a long
                    _pl = _r2_target
            else:
                _pl = round(min(float(profit_lock_floor_price), _r2_target), 8)
                if _pl <= current_price:        # never wrong-side for a short
                    _pl = _r2_target
            if abs(_pl - _r2_target) > 1e-12:
                _r2_target = _pl
                _profit_lock_held = True
        if _r2_violates:
            if log_only_global or cfg.log_only_min_distance:
                self._log_reject_would(
                    symbol, REASON_TOO_CLOSE, source,
                    extra=f"new={new_sl:.6f} price={current_price:.6f} "
                          f"dist_pct={dist_pct:.3f} eff_min={_eff_min:.3f} "
                          f"base_min={cfg.min_distance_pct} "
                          f"atr5={_vp_atr:.3f}% cls={_vp_cls or '?'} "
                          f"clamp_to={_r2_target:.6f} "
                          f"floor_held={'Y' if _r2_floor_held else 'N'}",
                )
            else:
                log.info(
                    f"SL_GATEWAY_R2_CLAMP | sym={symbol} raw={new_sl:.6f} "
                    f"clamped={_r2_target:.6f} price={current_price:.6f} "
                    f"dist_pct={dist_pct:.3f} eff_min={_eff_min:.3f} "
                    f"atr5={_vp_atr:.3f}% cls={_vp_cls or '?'} src={source} "
                    f"floor_held={'Y' if _r2_floor_held else 'N'} | {ctx()}"
                )
                if _r2_floor_held:
                    # Problem 1.1 observability — the floor was held at breakeven
                    # instead of being clamped sub-breakeven (the old defeat).
                    log.info(
                        f"SL_GATEWAY_R2_FLOOR_HELD | sym={symbol} "
                        f"breakeven={float(breakeven_floor_price):.6f} "
                        f"r2_boundary={_r2_boundary:.6f} applied={_r2_target:.6f} "
                        f"price={current_price:.6f} eff_min={_eff_min:.3f} "
                        f"src={source} dir={direction} | armed floor held at or "
                        f"above breakeven (R2 no longer defeats it) | {ctx()}"
                    )
                if _profit_lock_held:
                    # Dynamic Adaptive Exit observability — the R-derived profit
                    # lock was held inside the min-distance instead of being
                    # dropped as a clamp-noop (the proven enabler).
                    log.info(
                        f"SL_GATEWAY_R2_PROFIT_LOCK_HELD | sym={symbol} "
                        f"profit_lock={float(profit_lock_floor_price):.6f} "
                        f"r2_boundary={_r2_boundary:.6f} applied={_r2_target:.6f} "
                        f"price={current_price:.6f} eff_min={_eff_min:.3f} "
                        f"src={source} dir={direction} | armed R-lock held inside "
                        f"min-distance (clamp-noop enabler) | {ctx()}"
                    )
                self._track("clamp", source, REASON_TOO_CLOSE)
                new_sl = _r2_target
                _fx["r2"] = True  # forensic capture (behaviour-neutral)

        # R3 Max-step relative to previous SL. Same 6-decimal rounding
        # as R2 to avoid float-precision rejects at the exact threshold.
        # T2-5 (2026-05-12) — when `bypass_step_cap_for_breakeven=True`
        # AND `source` is in the trusted allowlist (sniper/sentinel
        # breakeven-protect paths), the step cap is bypassed for THIS
        # call only. The bypass is always logged via
        # SL_GATEWAY_BREAKEVEN_OVERRIDE so the operator sees every
        # large-step move that the cap would otherwise have rejected.
        # The R1 tighten-only invariant above (lines 366-384) still
        # runs unconditionally so a "breakeven move" that would loosen
        # SL is still rejected — bypass only widens R3, not R1.
        _t2_5_breakeven_bypass = (
            bypass_step_cap_for_breakeven
            and source in self._BREAKEVEN_BYPASS_SOURCES
        )
        _effective_bypass_step_cap = bypass_step_cap or _t2_5_breakeven_bypass

        step_pct = 0.0
        if current_sl is not None and current_sl > 0:
            step_pct = round(abs(new_sl - current_sl) / current_sl * 100.0, 6)
            if (
                _t2_5_breakeven_bypass
                and step_pct > cfg.max_step_pct
            ):
                # T2-5: emit the override log so every breakeven bypass
                # is observable. Fires BEFORE the rule check so the
                # operator can correlate even when the cap is then
                # bypassed and the move proceeds.
                log.info(
                    f"SL_GATEWAY_BREAKEVEN_OVERRIDE | sym={symbol} "
                    f"step_pct={step_pct:.3f} max={cfg.max_step_pct:.3f} "
                    f"bypass=true reason=breakeven_protect "
                    f"src={source} new={new_sl:.6f} cur={current_sl:.6f} "
                    f"| {ctx()}"
                )
            if not _effective_bypass_step_cap and step_pct > cfg.max_step_pct:
                # R3 Max-step — CLAMP-and-apply (Profit-Fetching restoration,
                # 2026-05-30). Pre-fix this rejected the whole move, so a trail
                # that wanted to advance more than max_step_pct in one tick
                # never moved at all and the stop froze. Now the gateway moves
                # the stop exactly max_step_pct toward price from the current
                # SL and applies it, so the trail ratchets up incrementally
                # each tick. Tighten-only holds (the clamp only moves toward
                # price). Bypass sources (ladder / safety) keep their single
                # large breakeven jump via the override logged just above.
                if is_long:
                    _r3_boundary = round(
                        current_sl * (1.0 + cfg.max_step_pct / 100.0), 8
                    )
                else:
                    _r3_boundary = round(
                        current_sl * (1.0 - cfg.max_step_pct / 100.0), 8
                    )
                if log_only_global or cfg.log_only_max_step:
                    self._log_reject_would(
                        symbol, REASON_STEP_EXCEEDED, source,
                        extra=f"raw_step_pct={step_pct:.3f} max={cfg.max_step_pct} "
                              f"new={new_sl:.6f} cur={current_sl:.6f} "
                              f"clamp_to={_r3_boundary:.6f}",
                    )
                else:
                    log.info(
                        f"SL_GATEWAY_R3_CLAMP | sym={symbol} raw={new_sl:.6f} "
                        f"clamped={_r3_boundary:.6f} cur={current_sl:.6f} "
                        f"raw_step_pct={step_pct:.3f} max={cfg.max_step_pct} "
                        f"src={source} | {ctx()}"
                    )
                    self._track("clamp", source, REASON_STEP_EXCEEDED)
                    new_sl = _r3_boundary
                    step_pct = round(
                        abs(new_sl - current_sl) / current_sl * 100.0, 6
                    )

        # Final tighten-only re-check after the R2/R3 clamps. If the best
        # VALID stop does not improve on the current SL (e.g. price has already
        # moved past where any min-distance-respecting stop could sit), hold
        # the current SL as a no-op instead of wiring a non-improving or
        # wrong-side value — this is what stops the post-retrace re-spam loop
        # that produced the NEAR SL_GATEWAY_WIRE_FAIL cascade. No wire push
        # happens on a no-op, so the rate-limit budget and _last_sl are
        # untouched and the position keeps its existing protective stop.
        if current_sl is not None and current_sl > 0:
            _still_tightens = (is_long and new_sl > current_sl) or (
                not is_long and new_sl < current_sl
            )
            if not _still_tightens:
                self._log_reject(
                    symbol, REASON_CLAMP_NOOP, source,
                    extra=f"clamped={new_sl:.6f} cur={current_sl:.6f} "
                          f"price={current_price:.6f} dir={direction}",
                )
                return SLGatewayResult(
                    accepted=False, reason=REASON_CLAMP_NOOP, old_sl=current_sl,
                )

        # ── Fresh-mark placeability degrade (Dynamic Adaptive Exit FIX, 2026-06-15) ──
        # Everything above judged placeability against the caller's current_price
        # SNAPSHOT, which the wire latency (~150 ms) can stale. On a fast retrace a
        # value placeable against the snapshot is wrong-side of the LIVE mark, so the
        # adapter blocks it (SET_SL_DIRECTION_BUG) and the wire FAILS — nothing is
        # placed and the green trade rides its old wide stop back to a loss (proven
        # on PYTHUSDT/MONUSDT/EGLDUSDT). Only a near-the-money stop can flip, so this
        # is gated on a cheap stale-distance pre-check: stops comfortably outside the
        # min-distance (real winners, far loss stops) never trigger the fetch. For an
        # at-risk stop, re-validate against the freshest mark (the SAME field the
        # adapter enforces, pos.mark_price) and DEGRADE to the closest placeable stop
        # — held at breakeven for a trusted floor source when breakeven is itself on
        # the correct side of the live mark — instead of emitting the unplaceable
        # value. R1 tighten-only is re-checked; if even the fresh boundary cannot
        # improve on the existing stop, hold it (clamp-noop, no wire) so the position
        # keeps its already-placed protective stop. Never loosens; the cap, owner
        # gate, R3, and R4 are untouched. Gated + log-only switch.
        _validated_price = current_price
        if (
            getattr(cfg, "r2_fresh_mark_degrade_enabled", True)
            and current_price > 0
            and _eff_min > 0
            and abs(current_price - new_sl) / current_price * 100.0
                < getattr(cfg, "fresh_mark_recheck_distance_mult", 2.0) * _eff_min
        ):
            _fresh = await self._fresh_mark(symbol)
            if _fresh is not None and _fresh > 0:
                _fx["fresh"] = _fresh  # forensic capture (piggyback; behaviour-neutral)
                _fmd_log_only = (
                    log_only_global
                    or getattr(cfg, "log_only_fresh_mark_degrade", False)
                )
                # Enforcement: the terminal wrong-side guard below validates the
                # final stop against the live mark (the reference the adapter uses).
                # Log-only: change nothing, so the guard keeps the caller snapshot —
                # the observe-only contract (no wire_fail -> wrong_side reclassify).
                if not _fmd_log_only:
                    _validated_price = _fresh
                _fresh_boundary = self._eff_min_boundary(_fresh, is_long, _eff_min)
                _fx["boundary"] = _fresh_boundary  # forensic capture (behaviour-neutral)
                _f_unplaceable = (
                    (new_sl > _fresh_boundary) if is_long else (new_sl < _fresh_boundary)
                )
                if _f_unplaceable:
                    _degraded = _fresh_boundary
                    # Hold at breakeven (tighter, captures more) when a trusted
                    # floor source supplied it AND breakeven is itself on the correct
                    # side of the FRESH mark — the R2 breakeven floor, judged against
                    # the live mark instead of the stale snapshot.
                    if (
                        _be_floor_active
                        and isinstance(breakeven_floor_price, (int, float))
                        and breakeven_floor_price > 0
                    ):
                        _be = round(float(breakeven_floor_price), 8)
                        if is_long and _degraded < _be < _fresh:
                            _degraded = _be
                        elif (not is_long) and _fresh < _be < _degraded:
                            _degraded = _be
                    _f_tightens = (
                        current_sl is None or current_sl <= 0
                        or (is_long and _degraded > current_sl)
                        or ((not is_long) and _degraded < current_sl)
                    )
                    if _fmd_log_only:
                        self._log_reject_would(
                            symbol, REASON_FRESH_DEGRADE, source,
                            extra=f"stale={current_price:.6f} fresh={_fresh:.6f} "
                                  f"from={new_sl:.6f} to={_degraded:.6f} "
                                  f"eff_min={_eff_min:.3f} tightens="
                                  f"{'Y' if _f_tightens else 'N'}",
                        )
                    elif _f_tightens:
                        log.info(
                            f"SL_GATEWAY_FRESH_DEGRADE | sym={symbol} "
                            f"stale_price={current_price:.6f} fresh_mark={_fresh:.6f} "
                            f"from={new_sl:.6f} to={_degraded:.6f} eff_min={_eff_min:.3f} "
                            f"src={source} dir={direction} | final stop was unplaceable "
                            f"vs the live mark; degraded to the closest placeable stop "
                            f"(no wire-fail give-back) | {ctx()}"
                        )
                        self._track("clamp", source, REASON_FRESH_DEGRADE)
                        new_sl = _degraded
                        _fx["degraded"] = True  # forensic capture (behaviour-neutral)
                    else:
                        self._log_reject(
                            symbol, REASON_CLAMP_NOOP, source,
                            extra=f"fresh_degrade from={new_sl:.6f} to={_degraded:.6f} "
                                  f"cur={current_sl:.6f} fresh={_fresh:.6f} dir={direction} "
                                  f"| even the fresh-mark boundary cannot improve; "
                                  f"held existing stop",
                        )
                        return SLGatewayResult(
                            accepted=False, reason=REASON_CLAMP_NOOP, old_sl=current_sl,
                        )

        # Issue 2.1 (2026-06-07): terminal correct-side guard. After the R2/R3
        # clamps, the R1 tighten-only re-check, and the fresh-mark degrade, a stop
        # still on the WRONG side of price must NEVER be wired — the exchange would
        # reject it and, on the urgent breakeven lane that bypasses the rate-limit, it
        # would re-spam every tick (the ~150x/16min BLUR cascade). Recognize it as
        # unplaceable and reject terminally: no wire push, so nothing is retried.
        # Validated against the freshest mark resolved above (the same reference the
        # adapter uses); with the R2 clamp + degrade this should be unreachable — a
        # belt-and-suspenders backstop the spec explicitly calls for.
        _wrong_side = (
            (is_long and new_sl >= _validated_price)
            or ((not is_long) and new_sl <= _validated_price)
        )
        if _wrong_side:
            self._log_reject(
                symbol, REASON_WRONG_SIDE, source,
                extra=f"new={new_sl:.6f} price={_validated_price:.6f} dir={direction} "
                      f"| unplaceable wrong-side stop; not wired (no retry)",
            )
            return SLGatewayResult(
                accepted=False, reason=REASON_WRONG_SIDE, old_sl=current_sl,
            )

        # Recompute dist_pct from the (possibly clamped) new_sl so the
        # SL_GATEWAY_ACCEPT line reflects the value actually applied.
        dist_pct = round(abs(current_price - new_sl) / current_price * 100.0, 6)

        # R4 Rate-limit per symbol.
        last_change = self._last_change.get(symbol, 0.0)
        elapsed_s = now - last_change if last_change > 0 else float("inf")
        _rl_window = self._rate_limit_window_for(source, cfg)
        if not bypass_rate_limit and elapsed_s < _rl_window:
            remaining = _rl_window - elapsed_s
            if log_only_global or cfg.log_only_rate_limit:
                self._log_reject_would(
                    symbol, REASON_RATE_LIMIT, source,
                    extra=f"elapsed_s={elapsed_s:.1f} remaining_s={remaining:.1f} "
                          f"window_s={_rl_window:.0f}",
                )
            else:
                self._log_reject(
                    symbol, REASON_RATE_LIMIT, source,
                    extra=f"elapsed_s={elapsed_s:.1f} remaining_s={remaining:.1f} "
                          f"window_s={_rl_window:.0f}",
                )
                return SLGatewayResult(
                    accepted=False, reason=REASON_RATE_LIMIT,
                    old_sl=current_sl,
                )

        # ── All rules passed (or log-only downgraded) — push to exchange ──
        ok = await self._wire_push(symbol, new_sl, source)
        if not ok:
            # Do NOT update state: failed push should not consume the
            # rate-limit budget or advance _last_sl. _wire_push already
            # bumped wire_fail; surface as HIGH to Claude so a downstream
            # Shadow/Bybit outage becomes visible on the next review.
            self._notify_event_buffer(
                "HIGH", "sl_gateway_wire_fail", symbol,
                source=source, new_sl=round(new_sl, 6),
                mode="enforcement",
            )
            return SLGatewayResult(
                accepted=False, reason=REASON_WIRE_FAIL, old_sl=current_sl,
            )

        self._last_change[symbol] = now
        self._last_sl[symbol] = new_sl

        prev_str = f"{current_sl:.6f}" if current_sl else "unknown"
        elapsed_str = f"{elapsed_s:.1f}" if elapsed_s != float("inf") else "first"
        log.info(
            f"SL_GATEWAY_ACCEPT | sym={symbol} old={prev_str} "
            f"new={new_sl:.6f} src={source} step_pct={step_pct:.3f} "
            f"dist_pct={dist_pct:.3f} elapsed_s={elapsed_str} "
            f"own={_og_owner} st={_og_state}"
            + (f" rsn={reason}" if reason else "")
            + f" | {ctx()}"
        )
        self._track("accept", source)
        return SLGatewayResult(
            accepted=True, reason="", old_sl=current_sl, new_sl_applied=new_sl,
        )

    # ── Internals ──────────────────────────────────────────────────────

    @staticmethod
    def _eff_min_boundary(price: float, is_long: bool, eff_min_pct: float) -> float:
        """Closest VALID stop to ``price`` on the correct side — exactly
        ``eff_min_pct`` away (below price for a long, above for a short).

        Single source of truth for the R2 min-distance boundary, shared by the
        R2 clamp and the pre-wire fresh-mark recheck so the two can never compute
        the boundary differently (a divergence would re-open the wrong-side hole).
        """
        if is_long:
            return round(price * (1.0 - eff_min_pct / 100.0), 8)
        return round(price * (1.0 + eff_min_pct / 100.0), 8)

    async def _fresh_mark(self, symbol: str) -> float | None:
        """Freshest mark price from the position service — the SAME field the
        exchange adapter validates wrong-side against (``pos.mark_price``).

        Returns None when unavailable (no position, no mark, or a fetch error) so
        the caller keeps the snapshot ``current_price`` and behaves as before. Read
        only; never raises.
        """
        try:
            pos = await self._position_service.get_position(symbol)
        except Exception as e:
            log.debug(
                f"SL_GATEWAY_FRESH_MARK_FAIL | sym={symbol} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )
            return None
        if pos is None:
            return None
        m = getattr(pos, "mark_price", None)
        try:
            m = float(m) if m is not None else 0.0
        except (TypeError, ValueError):
            return None
        return m if m > 0 else None

    def _emit_placement_forensic(self, symbol: str, source: str,
                                 fx: dict, result: "SLGatewayResult") -> None:
        """Emit one PLACEMENT_FORENSIC line for a profit-lock placement attempt.

        Reads ONLY the captured ``fx`` dict and the ``result`` — never fetches
        anything, never touches placement state. Fire-and-forget to the dedicated
        ``placement_forensic.log`` route. The live mark (``fx['fresh']``) is the
        value the fresh-mark degrade already fetched, or ``na`` when that block
        did not run (a far, comfortably-placeable stop) — so this adds no API
        call. The forgone-tightening fields quantify how much lock the
        placeability mechanism surrendered: the proposed lock versus the value
        actually placed or held.
        """
        accepted = bool(getattr(result, "accepted", False))
        outcome = "placed" if accepted else (getattr(result, "reason", "") or "unknown")
        applied = getattr(result, "new_sl_applied", None)
        if not (accepted and isinstance(applied, (int, float))):
            applied = fx.get("cur_sl")  # rejected/no-op: the existing stop is held
        proposed = fx.get("proposed")
        snap = fx.get("snap")
        entry = fx.get("entry")
        fresh = fx.get("fresh", "na")
        eff_min = fx.get("eff_min")
        boundary = fx.get("boundary")

        def _f(v):
            return f"{v:.8f}" if isinstance(v, (int, float)) else "na"

        forgone_px = (abs(proposed - applied)
                      if isinstance(proposed, (int, float))
                      and isinstance(applied, (int, float)) else None)
        forgone_pct = (forgone_px / snap * 100.0
                       if forgone_px is not None
                       and isinstance(snap, (int, float)) and snap > 0 else None)
        prop_pct = ((proposed - entry) / entry * 100.0
                    if isinstance(proposed, (int, float))
                    and isinstance(entry, (int, float)) and entry > 0 else None)
        self._pf_log.info(
            f"PLACEMENT_FORENSIC | sym={symbol} src={source} dir={fx.get('dir')} "
            f"snap={_f(snap)} mark={_f(fresh)} proposed={_f(proposed)} "
            f"applied={_f(applied)} cur_sl={_f(fx.get('cur_sl'))} "
            f"eff_min={(f'{eff_min:.4f}' if isinstance(eff_min, (int, float)) else 'na')} "
            f"boundary={_f(boundary)} r2_clamped={fx.get('r2')} "
            f"fresh_degraded={fx.get('degraded')} outcome={outcome} "
            f"prop_pct_entry={(f'{prop_pct:.4f}' if prop_pct is not None else 'na')} "
            f"forgone_px={(_f(forgone_px) if forgone_px is not None else 'na')} "
            f"forgone_pct={(f'{forgone_pct:.4f}' if forgone_pct is not None else 'na')} "
            f"| {ctx()}"
        )

    async def _wire_push(self, symbol: str, new_sl: float, source: str) -> bool:
        """Push to the exchange. Returns True on success, False on fail/exception.

        Failure is logged with source so operators can disambiguate gateway
        rejects from downstream (Shadow/Bybit) rejects. Tracks outcome in
        the gateway's stats counters.
        """
        try:
            ok = await self._position_service.set_stop_loss(symbol, new_sl)
        except Exception as e:
            log.warning(
                f"SL_GATEWAY_WIRE_FAIL | sym={symbol} new={new_sl:.6f} "
                f"src={source} err='{str(e)[:120]}' | {ctx()}"
            )
            self._track("wire_fail", source)
            return False
        if not ok:
            log.warning(
                f"SL_GATEWAY_WIRE_FAIL | sym={symbol} new={new_sl:.6f} "
                f"src={source} rsn=service_returned_false | {ctx()}"
            )
            self._track("wire_fail", source)
            return False
        return True

    # ── Brain-sourced rejects worth surfacing to EventBuffer ──
    # When Claude's own directive (brain_tighten / watchdog_tighten) gets
    # rejected by the gateway, Claude should know on the next review cycle
    # so it can reconsider its SL logic.
    _BRAIN_SOURCES = frozenset({"brain_tighten", "watchdog_tighten"})

    def _log_reject(
        self, symbol: str, reason: str, source: str, extra: str = "",
    ) -> None:
        suffix = f" {extra}" if extra else ""
        log.info(
            f"SL_GATEWAY_REJECT | sym={symbol} rsn={reason} src={source}"
            f"{suffix} | {ctx()}"
        )
        self._track("reject", source, reason)
        # Surface Claude-sourced rejects to EventBuffer so Claude's next
        # review sees that its directive was blocked.
        if source in self._BRAIN_SOURCES:
            self._notify_event_buffer(
                "MED", "sl_gateway_brain_blocked", symbol,
                source=source, reason=reason,
            )

    def _log_reject_would(
        self, symbol: str, reason: str, source: str, extra: str = "",
    ) -> None:
        suffix = f" {extra}" if extra else ""
        log.info(
            f"SL_GATEWAY_REJECT_WOULD | sym={symbol} rsn={reason} "
            f"src={source}{suffix} | {ctx()}"
        )
        self._track("would", source, reason)

    # ── Stats & observability helpers ──

    def _track(
        self, outcome: str, source: str, reason: str | None = None,
    ) -> None:
        """Bump per-outcome / per-source / per-reason counters.

        Outcome is one of: accept, reject, would, passthrough, wire_fail.
        Triggers SL_GATEWAY_STATS summary when the cadence threshold is
        crossed (event count OR elapsed time).
        """
        self._cnt_by_outcome[outcome] = self._cnt_by_outcome.get(outcome, 0) + 1
        self._cnt_by_source[source] = self._cnt_by_source.get(source, 0) + 1
        if reason:
            self._cnt_by_reason[reason] = self._cnt_by_reason.get(reason, 0) + 1
        self._events_since_emit += 1
        self._maybe_emit_stats()

    def _maybe_emit_stats(self) -> None:
        """Emit SL_GATEWAY_STATS summary when cadence threshold hits.

        Two triggers:
          - Event count ≥ STATS_EVENT_THRESHOLD (catches burst activity)
          - Elapsed time ≥ STATS_INTERVAL_SECONDS (catches quiet periods)

        After emission, counters reset so each summary line represents a
        single window — easy to rate-calculate in post-processing.
        """
        now = time.monotonic()
        elapsed = now - self._last_stats_emit_ts
        if (
            self._events_since_emit < self.STATS_EVENT_THRESHOLD
            and elapsed < self.STATS_INTERVAL_SECONDS
        ):
            return
        # Skip if literally no events have happened (startup idle window)
        if self._events_since_emit == 0:
            self._last_stats_emit_ts = now
            return
        total = sum(self._cnt_by_outcome.values())
        by_src = dict(sorted(
            self._cnt_by_source.items(),
            key=lambda kv: -kv[1],
        ))
        by_rsn = dict(sorted(
            self._cnt_by_reason.items(),
            key=lambda kv: -kv[1],
        ))
        log.info(
            f"SL_GATEWAY_STATS | total={total} "
            f"accept={self._cnt_by_outcome.get('accept', 0)} "
            f"reject={self._cnt_by_outcome.get('reject', 0)} "
            f"would={self._cnt_by_outcome.get('would', 0)} "
            f"passthrough={self._cnt_by_outcome.get('passthrough', 0)} "
            f"wire_fail={self._cnt_by_outcome.get('wire_fail', 0)} "
            f"window_s={elapsed:.0f} uptime_s={int(now - self._start_ts)} "
            f"active_syms={len(self._last_sl)} "
            f"by_src={by_src} by_rsn={by_rsn} | {ctx()}"
        )
        # Reset window counters (keep _last_sl / _last_change — those are
        # functional state, not stats).
        for k in list(self._cnt_by_outcome):
            self._cnt_by_outcome[k] = 0
        self._cnt_by_source.clear()
        self._cnt_by_reason.clear()
        self._events_since_emit = 0
        self._last_stats_emit_ts = now

    def _notify_event_buffer(
        self, priority: str, event_type: str, symbol: str, **data,
    ) -> None:
        """Push an event to the injected EventBuffer (best-effort).

        EventBuffer integrates with DataLake for persistence, so events
        are both surfaced to Claude's next prompt AND recorded for
        post-hoc analysis.
        """
        if self._event_buffer is None:
            return
        try:
            self._event_buffer.add_event(priority, event_type, symbol, **data)
        except Exception as e:
            log.debug(
                f"SL_GATEWAY_EVBUF_FAIL | sym={symbol} type={event_type} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )

    # ── Test / ops helpers ─────────────────────────────────────────────

    def get_state_snapshot(self) -> dict[str, dict[str, float]]:
        """Return a shallow copy of internal state. For diagnostics only."""
        return {
            sym: {
                "last_sl": self._last_sl.get(sym, 0.0),
                "last_change_ts": self._last_change.get(sym, 0.0),
            }
            for sym in set(self._last_sl) | set(self._last_change)
        }

    def get_stats_snapshot(self) -> dict[str, Any]:
        """Return current in-window counters for health / diagnostic checks.

        Unlike ``get_state_snapshot`` (functional state), this reflects
        the SL_GATEWAY_STATS window — useful for an ad-hoc Telegram
        ``/health`` command or a system-health tick.
        """
        now = time.monotonic()
        return {
            "outcome": dict(self._cnt_by_outcome),
            "by_source": dict(self._cnt_by_source),
            "by_reason": dict(self._cnt_by_reason),
            "events_since_emit": self._events_since_emit,
            "window_s": now - self._last_stats_emit_ts,
            "uptime_s": int(now - self._start_ts),
            "active_symbols": len(self._last_sl),
            "event_buffer_wired": self._event_buffer is not None,
        }

    def emit_stats(self, force: bool = True) -> None:
        """Force-emit an SL_GATEWAY_STATS summary line.

        Call from graceful shutdown or operator-triggered diagnostic
        so the final window's counters aren't lost. Also callable at
        any point with ``force=False`` to respect the normal cadence.
        """
        if force:
            # Temporarily lower threshold so the next _maybe_emit_stats
            # call definitely emits even for tiny windows.
            if self._events_since_emit > 0:
                self._events_since_emit = max(
                    self._events_since_emit, self.STATS_EVENT_THRESHOLD,
                )
        self._maybe_emit_stats()

    def reset_symbol(self, symbol: str) -> None:
        """Clear per-symbol state. Called on position close to avoid stale rate-limit state."""
        self._last_change.pop(symbol, None)
        self._last_sl.pop(symbol, None)
        # Phase 1 owner switch — clear the authority memory so the next trade
        # on this symbol starts with no inherited owner / graduation state.
        self._last_owner.pop(symbol, None)
        self._ever_green.pop(symbol, None)

    @property
    def state_enforcement_active(self) -> bool:
        """True when the Phase-1 owner switch is ENFORCING (not log-only).

        The sniper spine reads this to filter its loss-side candidates off a
        green trade (Layer B) ONLY when enforcement is on — so log-only mode
        never changes stop selection. Returns False when config is absent or
        the switch is disabled / log-only.
        """
        cfg = getattr(self._settings, "sl_gateway", None)
        if cfg is None:
            return False
        return bool(getattr(cfg, "owner_switch_enabled", False)) and bool(
            getattr(cfg, "owner_switch_enforce", False)
        )

    # ── Owner gate internals (Phase 1) ─────────────────────────────────

    def _classify_bucket(self, source: str) -> str:
        """Map a writer source string to its authority bucket.

        Returns one of: ``head``, ``always``, ``green``, ``red``, ``advisory``,
        or ``unclassified`` (an unmapped source — the gate fails open on it but
        logs it so the gap is visible).
        """
        if source in self._head_sources:
            return "head"
        if source in self._always_sources:
            return "always"
        if source in self._green_sources:
            return "green"
        if source in self._red_sources:
            return "red"
        if source in self._advisory_sources:
            return "advisory"
        return "unclassified"

    @staticmethod
    def _compute_trade_state(
        is_long: bool,
        entry: float | None,
        current_price: float | None,
        deadband_pct: float,
    ) -> tuple[str, float] | None:
        """Compute (state, pnl_pct) for a trade, or None when undeterminable.

        State is ``green`` at PnL >= +deadband, ``red`` at PnL <= -deadband,
        and ``neutral`` inside the band. Returns None when entry or price is
        missing so the caller can fail open.
        """
        if not entry or entry <= 0 or not current_price or current_price <= 0:
            return None
        if is_long:
            pnl_pct = (current_price - entry) / entry * 100.0
        else:
            pnl_pct = (entry - current_price) / entry * 100.0
        if pnl_pct >= deadband_pct:
            return ("green", pnl_pct)
        if pnl_pct <= -deadband_pct:
            return ("red", pnl_pct)
        return ("neutral", pnl_pct)

    def _owner_gate(
        self,
        *,
        symbol: str,
        source: str,
        is_long: bool,
        entry_price: float | None,
        current_price: float | None,
    ) -> tuple[bool, str, str, str, float]:
        """Decide whether ``source`` may write the stop given the trade state.

        Returns ``(admit, state, owner, bucket, pnl_pct)``. NEVER raises — any
        internal error fails OPEN (``admit=True``) so a gate bug can never block
        a protective stop write. Updates the per-symbol owner/graduation memory
        and logs SL_GATEWAY_OWNER_HANDOFF on a definite owner transition.
        """
        try:
            cfg = getattr(self._settings, "sl_gateway", None)
            if cfg is None or not getattr(cfg, "owner_switch_enabled", False):
                return (True, "off", "off", "off", 0.0)

            bucket = self._classify_bucket(source)
            deadband = float(getattr(cfg, "breakeven_deadband_pct", 0.05))
            st = self._compute_trade_state(
                is_long, entry_price, current_price, deadband,
            )
            if st is None:
                # Entry/price unknown — cannot determine ownership; fail open.
                return (True, "unknown", "unknown", bucket, 0.0)
            state, pnl_pct = st

            prev_owner = self._last_owner.get(symbol)
            if state == "green":
                self._ever_green[symbol] = True
                owner = "green"
            elif state == "red":
                # Faded-winner rule: a once-green trade that craters stays
                # green-owned (Head still protects) UNLESS the operator turned
                # on the re-arm (Rule 5). A trade that was never green hands to
                # the red owner immediately.
                rearm = bool(getattr(cfg, "faded_winner_rearm_red", False))
                if (not rearm) and self._ever_green.get(symbol, False):
                    owner = "green"
                else:
                    owner = "red"
            else:  # neutral — hold the last definite owner (hysteresis); a
                # brand-new position with no prior owner defaults to red so the
                # opening floor is the baseline.
                owner = prev_owner if prev_owner in ("green", "red") else "red"

            if owner in ("green", "red") and owner != prev_owner:
                log.info(
                    f"SL_GATEWAY_OWNER_HANDOFF | sym={symbol} "
                    f"from={prev_owner or 'none'} to={owner} state={state} "
                    f"pnl_pct={pnl_pct:.3f} src={source} | {ctx()}"
                )
            if state in ("green", "red"):
                self._last_owner[symbol] = owner

            advisory_enforce = bool(getattr(cfg, "advisory_enforce", False))
            head_only_green = bool(getattr(cfg, "head_only_seizes_green", True))
            if bucket == "head":
                admit = True
                # Phase 2 observability: the Head seizing a running GREEN trade
                # is the one thing allowed to interrupt the profit engine —
                # surface it so the operator sees a winner being cut by
                # catastrophe (as opposed to the green owner's own tighten).
                if owner == "green":
                    log.info(
                        f"SL_GATEWAY_HEAD_OVERRIDE | sym={symbol} src={source} "
                        f"state={state} owner={owner} pnl_pct={pnl_pct:.3f} | "
                        f"Head tightening a green trade (catastrophe) | {ctx()}"
                    )
            elif bucket == "always":
                admit = True
            elif bucket == "green":
                admit = owner == "green"
            elif bucket == "red":
                admit = owner == "red"
            elif bucket == "advisory":
                # Phase 2 (profit-priority / Option A): on a GREEN trade only the
                # Head and the green owner may write — advisory writers are
                # deferred so nothing but catastrophe interrupts a running
                # winner. Off a green trade, advisory writers pass until Phase 5
                # flips advisory_enforce.
                if owner == "green" and head_only_green:
                    admit = False
                else:
                    admit = not advisory_enforce
            else:  # unclassified — fail open, but make the gap visible.
                log.warning(
                    f"SL_GATEWAY_OWNER_UNCLASSIFIED | sym={symbol} src={source} "
                    f"| source not in any bucket — admitting (fail-open) | "
                    f"{ctx()}"
                )
                # Surface to the operator/Claude review: an unbucketed source
                # means the hierarchy has a gap (a new writer was added without
                # being classified). Best-effort; EventBuffer coalesces.
                self._notify_event_buffer(
                    "MED", "sl_gateway_owner_unclassified", symbol, source=source,
                )
                admit = True
            return (admit, state, owner, bucket, pnl_pct)
        except Exception as e:  # noqa: BLE001 — must never break the SL path
            log.warning(
                f"SL_GATEWAY_OWNER_ERROR | sym={symbol} src={source} "
                f"err='{str(e)[:120]}' | owner gate failed open | {ctx()}"
            )
            # A persistently-throwing gate silently turns enforcement back into a
            # no-op (the pre-refactor collision). Surface it so a broken gate is
            # visible on the next review rather than only greppable in workers.log.
            self._notify_event_buffer(
                "HIGH", "sl_gateway_owner_error", symbol,
                source=source, err=str(e)[:120],
            )
            return (True, "error", "error", "error", 0.0)

    def peek_owner(
        self,
        symbol: str,
        is_long: bool,
        entry_price: float | None,
        current_price: float | None,
    ) -> str:
        """Owner determination matching the owner gate's logic, for the sniper
        spine to offer only the owning engine's candidates (Phase 3/4) so the
        spine's selection and the gateway's owner gate can never disagree and
        starve a trade. Returns ``green``, ``red``, or ``unknown`` (the latter
        when the switch is off or entry/price is missing — the caller then
        offers all candidates, unchanged behavior).

        Side effects: the ONLY state it mutates is the monotonic ever-green
        latch (set when it observes a green tick), which keeps the faded-winner
        rule reliable; it never logs and never changes the owner hysteresis, so
        it cannot double-log a hand-off or diverge from ``_owner_gate``.
        """
        try:
            cfg = getattr(self._settings, "sl_gateway", None)
            if cfg is None or not getattr(cfg, "owner_switch_enabled", False):
                return "unknown"
            deadband = float(getattr(cfg, "breakeven_deadband_pct", 0.05))
            st = self._compute_trade_state(
                is_long, entry_price, current_price, deadband,
            )
            if st is None:
                return "unknown"
            state, _pnl = st
            if state == "green":
                # Latch the monotonic ever-green flag here too (benign and
                # idempotent): peek_owner runs every spine tick, so a trade that
                # ticks green without triggering a gateway write still records
                # that it was green, keeping the faded-winner classification
                # reliable. This is the ONLY state peek_owner mutates — it never
                # logs and never touches the owner-hysteresis (_last_owner), so
                # it cannot double-log a hand-off or disagree with _owner_gate.
                self._ever_green[symbol] = True
                return "green"
            if state == "red":
                rearm = bool(getattr(cfg, "faded_winner_rearm_red", False))
                if (not rearm) and self._ever_green.get(symbol, False):
                    return "green"
                return "red"
            # neutral — hold the last definite owner; default red.
            prev = self._last_owner.get(symbol)
            return prev if prev in ("green", "red") else "red"
        except Exception:  # noqa: BLE001 — read-only helper, never raise
            return "unknown"

    def set_event_buffer(self, event_buffer: Any) -> None:
        """Late-wire the EventBuffer.

        EventBuffer is constructed in a later DI layer than SLGateway
        inside WorkerManager, so the gateway is first created with
        event_buffer=None and then set via this method once the buffer
        exists. Mirrors ``Transformer.set_event_buffer`` convention.
        """
        self._event_buffer = event_buffer
        log.info(
            "SL_GATEWAY_EVBUF_WIRED | event_buffer={eb} | {c}",
            eb=type(event_buffer).__name__ if event_buffer is not None else "None",
            c=ctx(),
        )
