"""Phase 3 of the 1D briefing rewrite — per-coin state labeller.

Pure-function classifier that turns a coin's current state into one or
more *opportunity* labels. The labeller has no side effects, no IO, and
never raises — given any combination of inputs it returns a
:class:`StateLabelResult` (which may be ``[NO_TRADEABLE_STATE]`` when
nothing fires).

Why this exists:

    The legacy Layer 1D pipeline asked "should we trade this coin?"
    via a 5-gate AND-checklist that dropped 49 of 50 coins per cycle.
    The briefing-pack rewrite asks "what kind of opportunity is this
    coin's *current state*?" — a coin can carry multiple labels
    (e.g. ``TREND_PULLBACK_LONG`` + ``FUNDING_EXTREME_FADE_LONG``)
    and the brain decides how to weight them.

Triggers reference fields available in existing caches today
(structure_worker._cache, regime_worker._per_coin_regimes,
layer_manager._strategy_consensus, signal_worker._cache,
altdata_worker, market.get_ticker_cached). No new computation is
required at this phase. Phase 5 introduces a richer ``CoinState``
dataclass that wraps these reads behind a single object; until then
this module accepts each input as a kwarg with a safe default so an
incomplete state never crashes the labeller.

Two label classes:

    TRADE-ACTIONABLE — the brain may use these as candidates:
        TREND_PULLBACK_LONG / SHORT
        RANGE_FADE_LONG / SHORT
        BREAKOUT_PENDING
        LIQUIDITY_SWEEP_REVERSAL_LONG / SHORT
        FUNDING_EXTREME_FADE_LONG / SHORT
        COUNTER_TRADE_LONG / SHORT
        MOMENTUM_BURST_LONG / SHORT
        OB_MITIGATED_FVG_ONLY_LONG / SHORT
        KILL_ZONE_OPPORTUNITY
        EXTREME_FEAR_CONTRARIAN_LONG
        EXTREME_GREED_CONTRARIAN_SHORT

    ADVISORY-ONLY — surfaced for transparency, not as candidates:
        MANIPULATION_WINDOW
        RECENT_LOSER_COOLDOWN
        NO_TRADEABLE_STATE
        OPEN_POSITION_HOLD_REVIEW

The numeric thresholds are deliberately conservative for the additive
surface phase. Phase 4 introduces a ``[scanner.briefing.label_thresholds]``
config block so the operator can tune them without redeploy; until then
they live as module-level constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Issue 3 of 2026-05-19 direction-bias fix Phase B. Bumped when the
# per-trigger regime hard-kill predicates at lines 253, 268, 283, 301,
# 356, 371, 477, 491 are softened to confidence-haircut multipliers.
# Operators tail STATE_LABELLER_REGIME_HAIRCUT_INIT at boot to verify
# the current haircut value loaded into memory.
#
# Version 1: legacy hard-kill (regime mismatch → return None).
# Version 2: soft-haircut (regime mismatch → base_conf * haircut). The
# haircut value is operator-tunable via
# ``[scanner.labeller] counter_regime_confidence_haircut`` in
# config.toml (default 0.5 — labels in mismatched regime fire at half
# their normal confidence). Setting haircut=0.0 reproduces v1 hard-kill
# semantics; haircut>=1.0 removes the regime gate entirely.
LABELLER_REGIME_HAIRCUT_VERSION = 2

# ── Trade-actionable labels ──────────────────────────────────────────
LABEL_TREND_PULLBACK_LONG = "TREND_PULLBACK_LONG"
LABEL_TREND_PULLBACK_SHORT = "TREND_PULLBACK_SHORT"
LABEL_RANGE_FADE_LONG = "RANGE_FADE_LONG"
LABEL_RANGE_FADE_SHORT = "RANGE_FADE_SHORT"
LABEL_BREAKOUT_PENDING = "BREAKOUT_PENDING"
LABEL_LIQUIDITY_SWEEP_REVERSAL_LONG = "LIQUIDITY_SWEEP_REVERSAL_LONG"
LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT = "LIQUIDITY_SWEEP_REVERSAL_SHORT"
LABEL_FUNDING_EXTREME_FADE_LONG = "FUNDING_EXTREME_FADE_LONG"
LABEL_FUNDING_EXTREME_FADE_SHORT = "FUNDING_EXTREME_FADE_SHORT"
LABEL_COUNTER_TRADE_LONG = "COUNTER_TRADE_LONG"
LABEL_COUNTER_TRADE_SHORT = "COUNTER_TRADE_SHORT"
LABEL_MOMENTUM_BURST_LONG = "MOMENTUM_BURST_LONG"
LABEL_MOMENTUM_BURST_SHORT = "MOMENTUM_BURST_SHORT"
LABEL_OB_MITIGATED_FVG_ONLY_LONG = "OB_MITIGATED_FVG_ONLY_LONG"
LABEL_OB_MITIGATED_FVG_ONLY_SHORT = "OB_MITIGATED_FVG_ONLY_SHORT"
LABEL_KILL_ZONE_OPPORTUNITY = "KILL_ZONE_OPPORTUNITY"
# Neutrality fix (D1, 2026-05-30): the displayed values were renamed from
# "..._LONG_BIAS"/"..._SHORT_BIAS" to neutral, data-conditional SETUP names so
# the label the brain reads no longer editorializes a directional lean. The
# Python constant NAMES are unchanged (all references and tests key off the
# constant, not the literal), and the triggers still fire only when the coin's
# OWN consensus/trade direction already points that way — the direction comes
# from the coin's data, not from sentiment.
LABEL_EXTREME_FEAR_LONG_BIAS = "EXTREME_FEAR_CONTRARIAN_LONG"
LABEL_EXTREME_GREED_SHORT_BIAS = "EXTREME_GREED_CONTRARIAN_SHORT"

# ── Advisory-only labels ─────────────────────────────────────────────
LABEL_MANIPULATION_WINDOW = "MANIPULATION_WINDOW"
LABEL_RECENT_LOSER_COOLDOWN = "RECENT_LOSER_COOLDOWN"
LABEL_NO_TRADEABLE_STATE = "NO_TRADEABLE_STATE"
LABEL_OPEN_POSITION_HOLD_REVIEW = "OPEN_POSITION_HOLD_REVIEW"

ADVISORY_LABELS: frozenset[str] = frozenset({
    LABEL_MANIPULATION_WINDOW,
    LABEL_RECENT_LOSER_COOLDOWN,
    LABEL_NO_TRADEABLE_STATE,
    LABEL_OPEN_POSITION_HOLD_REVIEW,
})

# Base weights consumed by the Phase 4 interestingness ranker. Higher =
# more attractive opportunity in the brain's eye. Advisory labels carry
# a low-but-nonzero weight so coins with only an advisory label still
# surface in the briefing (the brain learns "no edge here today").
LABEL_BASE_WEIGHTS: dict[str, float] = {
    LABEL_TREND_PULLBACK_LONG: 0.85,
    LABEL_TREND_PULLBACK_SHORT: 0.85,
    LABEL_LIQUIDITY_SWEEP_REVERSAL_LONG: 0.85,
    LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT: 0.85,
    LABEL_BREAKOUT_PENDING: 0.70,
    LABEL_RANGE_FADE_LONG: 0.65,
    LABEL_RANGE_FADE_SHORT: 0.65,
    LABEL_FUNDING_EXTREME_FADE_LONG: 0.60,
    LABEL_FUNDING_EXTREME_FADE_SHORT: 0.60,
    LABEL_KILL_ZONE_OPPORTUNITY: 0.60,
    LABEL_MOMENTUM_BURST_LONG: 0.55,
    LABEL_MOMENTUM_BURST_SHORT: 0.55,
    LABEL_EXTREME_FEAR_LONG_BIAS: 0.55,
    LABEL_EXTREME_GREED_SHORT_BIAS: 0.55,
    LABEL_OPEN_POSITION_HOLD_REVIEW: 0.50,
    LABEL_COUNTER_TRADE_LONG: 0.45,
    LABEL_COUNTER_TRADE_SHORT: 0.45,
    LABEL_OB_MITIGATED_FVG_ONLY_LONG: 0.40,
    LABEL_OB_MITIGATED_FVG_ONLY_SHORT: 0.40,
    LABEL_MANIPULATION_WINDOW: 0.20,
    LABEL_RECENT_LOSER_COOLDOWN: 0.15,
    LABEL_NO_TRADEABLE_STATE: 0.05,
}

# One-line action hints surfaced into the brain prompt by Phase 6. The
# brain treats them as the *system's* read on what the state suggests;
# the brain may override (with reasoning).
ACTION_HINTS: dict[str, str] = {
    LABEL_TREND_PULLBACK_LONG: (
        "Long-side pullback continuation. Enter on retest of OB; SL below OB low; TP previous swing high."
    ),
    LABEL_TREND_PULLBACK_SHORT: (
        "Short-side pullback continuation. Enter on retest of OB; SL above OB high; TP previous swing low."
    ),
    LABEL_RANGE_FADE_LONG: (
        "Mean-revert long at range low. SL just below support; TP mid-range. Tight RR (1.3-2.0)."
    ),
    LABEL_RANGE_FADE_SHORT: (
        "Mean-revert short at range high. SL just above resistance; TP mid-range. Tight RR (1.3-2.0)."
    ),
    LABEL_BREAKOUT_PENDING: (
        "Range compression at level — wait for breakout candle, enter on retest. RR 2.0-4.0 if it fires."
    ),
    LABEL_LIQUIDITY_SWEEP_REVERSAL_LONG: (
        "Stop-hunt swept lows. Reversal long; SL below sweep low; TP equal-highs cluster."
    ),
    LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT: (
        "Stop-hunt swept highs. Reversal short; SL above sweep high; TP equal-lows cluster."
    ),
    LABEL_FUNDING_EXTREME_FADE_LONG: (
        "Negative funding extreme — shorts overcrowded. Fade long with tight SL; size with funding-rate bias."
    ),
    LABEL_FUNDING_EXTREME_FADE_SHORT: (
        "Positive funding extreme — longs overcrowded. Fade short; tighter SL; smaller size than trend setups."
    ),
    LABEL_COUNTER_TRADE_LONG: (
        "Counter-trade long against bearish bias — opposite-direction OB present; lower conviction, half-size."
    ),
    LABEL_COUNTER_TRADE_SHORT: (
        "Counter-trade short against bullish bias; lower conviction, half-size."
    ),
    LABEL_MOMENTUM_BURST_LONG: (
        "Volatile momentum long — wider SL, trail aggressively. Don't chase; enter on first pullback."
    ),
    LABEL_MOMENTUM_BURST_SHORT: (
        "Volatile momentum short — wider SL, trail aggressively."
    ),
    LABEL_OB_MITIGATED_FVG_ONLY_LONG: (
        "Only FVG remains for long entry (OB used). Thinner edge — smaller size; tighter RR."
    ),
    LABEL_OB_MITIGATED_FVG_ONLY_SHORT: (
        "Only FVG remains for short entry (OB used). Thinner edge — smaller size; tighter RR."
    ),
    LABEL_KILL_ZONE_OPPORTUNITY: (
        "Active session kill-zone with structural setup — confirm signal then enter."
    ),
    LABEL_EXTREME_FEAR_LONG_BIAS: (
        "Extreme market fear coincides with this coin's own long-pointing "
        "read — a contrarian-long SETUP, valid only if the coin's structure "
        "confirms. Fear alone is not a buy signal."
    ),
    LABEL_EXTREME_GREED_SHORT_BIAS: (
        "Extreme greed coincides with this coin's own short-pointing read — "
        "a contrarian-short SETUP, valid only if the coin's structure "
        "confirms. Greed alone is not a sell signal."
    ),
    LABEL_MANIPULATION_WINDOW: (
        "London-open manipulation window; observe, do not enter until window closes."
    ),
    LABEL_RECENT_LOSER_COOLDOWN: (
        "Recent loss within cooldown window. Suggest skipping unless thesis materially changed."
    ),
    LABEL_NO_TRADEABLE_STATE: (
        "No clear edge surfaced. Skip unless brain sees something the labeller missed."
    ),
    LABEL_OPEN_POSITION_HOLD_REVIEW: (
        "Open position present — review hold/tighten/close; do not stack."
    ),
}


@dataclass(frozen=True)
class StateLabelResult:
    """Output of :func:`label_state`.

    Attributes:
        primary: Highest-base-weight label that fired. ``"NO_TRADEABLE_STATE"``
            when nothing fired.
        secondary: All other labels that fired, ordered by base weight
            descending. Empty when only the primary label fires.
        confidence: 0..1 — how cleanly the primary trigger matched.
            Today this is the trigger function's own confidence value
            (typically ``setup_type_confidence`` for structure-derived
            labels, ``regime_confidence`` for regime-derived, etc.).
            Phase 4 ranker reads this alongside ``primary``.
    """

    primary: str
    secondary: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def all_labels(self) -> list[str]:
        """All labels that fired, primary first."""
        return [self.primary] + list(self.secondary)


# ── Trigger predicates ───────────────────────────────────────────────
#
# Each trigger returns ``None`` when it does not fire, or a confidence
# float in [0, 1] when it does. The label is recorded with that
# confidence; the labeller picks the one with the highest base weight
# (× confidence as tiebreaker) as primary.
#
# All functions are tolerant of missing inputs: a kwarg defaulting to
# its empty/zero value never crashes the predicate; the label simply
# won't fire. This lets Phase 3 callers pass a partial input set
# (today's _build_package locals) while Phase 5 callers pass the
# richer CoinState fields.


def _is_trending_up(regime: str) -> bool:
    return "trending_up" in regime or "trend_up" in regime


def _is_trending_down(regime: str) -> bool:
    return "trending_down" in regime or "trend_down" in regime


def _is_ranging(regime: str) -> bool:
    return "rang" in regime  # ranging / range


def _is_volatile(regime: str) -> bool:
    return "volat" in regime


def _conviction_scale(setup_type_confidence: float, floor: float) -> float:
    """Phase 1 calibration (2026-06-08): scale a GLOBAL sentiment-extremity
    confidence by the coin's OWN structural conviction, floored so a
    structure-blind coin keeps a small (not zero) confidence rather than the
    full extremity value.

    Returns a multiplier in ``[floor, 1.0]``. With the back-compat default
    ``floor=1.0`` the result is always 1.0 (the pre-calibration behaviour: the
    extremity passes through unscaled).
    """
    if setup_type_confidence and setup_type_confidence > 0.0:
        return max(floor, min(1.0, float(setup_type_confidence)))
    return floor


def _trigger_trend_pullback_long(
    *, regime: str, setup_type: str, trade_direction: str,
    setup_type_confidence: float,
    regime_haircut: float = 1.0,
) -> float | None:
    """Long-side trend-continuation pullback trigger.

    Issue 3 fix (2026-05-19): regime gate softened from hard kill (return
    None when ``regime != trending_up``) to a haircut multiplier. The
    direction and setup_type checks remain mandatory because they define
    what KIND of opportunity this trigger detects, not the market regime
    in which it should fire. The regime check only applies the
    ``regime_haircut`` to the base confidence when regime mismatches.

    - ``regime_haircut <= 0.0``: legacy hard kill (label suppressed).
    - ``0.0 < regime_haircut < 1.0``: soft haircut — label fires at
      reduced confidence (``base_conf * haircut``). Default 0.5.
    - ``regime_haircut >= 1.0``: full pass — label fires at full
      confidence regardless of regime.
    """
    if trade_direction != "long":
        return None
    if setup_type not in {
        "bullish_fvg_ob", "bullish_structural_break",
    }:
        return None
    base_conf = max(0.30, min(1.0, setup_type_confidence or 0.55))
    if not _is_trending_up(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_trend_pullback_short(
    *, regime: str, setup_type: str, trade_direction: str,
    setup_type_confidence: float,
    regime_haircut: float = 1.0,
) -> float | None:
    """Mirror of ``_trigger_trend_pullback_long`` for short side."""
    if trade_direction != "short":
        return None
    if setup_type not in {
        "bearish_fvg_ob", "bearish_structural_break",
    }:
        return None
    base_conf = max(0.30, min(1.0, setup_type_confidence or 0.55))
    if not _is_trending_down(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_range_fade_long(
    *, regime: str, trade_direction: str, position_in_range: float | None,
    consensus_direction: str, setup_type_confidence: float,
    regime_haircut: float = 1.0,
    range_breakout: str = "",
) -> float | None:
    """Range-low fade long trigger.

    Issue 3 fix (2026-05-19): regime gate (must be ranging) softened to a
    haircut. position_in_range and direction checks remain mandatory.

    Element 3 (2026-06-11): a price OUTSIDE the range falsifies the
    fade premise in BOTH directions — below the range there is no floor
    to buy (June-11 DYDX carried RANGE_FADE_LONG on all 24 submissions
    while price fell THROUGH the range), and above the range "buy the
    range low" is equally meaningless (the cross-check audit caught the
    original one-sided guard leaving the with-break-direction fade
    firing with an incoherent mid-range TP hint). Any non-empty
    ``range_breakout`` suppresses the trigger. The default ""
    reproduces legacy behaviour verbatim for callers that do not pass it.
    """
    # Element 3 truth check FIRST: the fade premise requires the price
    # to be inside (or at) the range.
    if range_breakout:
        return None
    # Long fade at low end of range. Use trade_direction or consensus
    # direction as a directional hint when position_in_range is unknown
    # (Phase 3 — derived in Phase 5 from structural high/low). We
    # require AT LEAST ONE directional anchor to point long.
    points_long = trade_direction == "long" or consensus_direction == "long"
    if not points_long:
        return None
    if position_in_range is not None and position_in_range >= 0.40:
        return None
    base_conf = max(0.30, min(1.0, setup_type_confidence or 0.45))
    if not _is_ranging(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_range_fade_short(
    *, regime: str, trade_direction: str, position_in_range: float | None,
    consensus_direction: str, setup_type_confidence: float,
    regime_haircut: float = 1.0,
    range_breakout: str = "",
) -> float | None:
    """Mirror of ``_trigger_range_fade_long`` for short side.

    Element 3 (2026-06-11): any price outside the range falsifies the
    fade premise — above the range there is no ceiling to sell, and
    below the range "sell the range high" is equally meaningless.
    """
    if range_breakout:
        return None
    points_short = trade_direction == "short" or consensus_direction == "short"
    if not points_short:
        return None
    if position_in_range is not None and position_in_range <= 0.60:
        return None
    base_conf = max(0.30, min(1.0, setup_type_confidence or 0.45))
    if not _is_ranging(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_breakout_pending(
    *, setup_type: str, range_compression: bool, regime: str,
    setup_type_confidence: float,
) -> float | None:
    if setup_type in {"bullish_range_breakout", "bearish_range_breakdown"}:
        return max(0.40, min(1.0, setup_type_confidence or 0.55))
    if range_compression and (_is_ranging(regime) or "dead" in regime):
        return 0.40
    return None


def _trigger_liquidity_sweep_long(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bullish_liquidity_sweep":
        return None
    if trade_direction not in {"", "long"}:
        return None
    return max(0.40, min(1.0, setup_type_confidence or 0.65))


def _trigger_liquidity_sweep_short(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bearish_liquidity_sweep":
        return None
    if trade_direction not in {"", "short"}:
        return None
    return max(0.40, min(1.0, setup_type_confidence or 0.65))


# Funding-blocker threshold (decimal): 0.001 = 0.1%. Must mirror
# scanner.qualitative.funding_blocker_threshold_pct so the "fade the
# crowd" label fires at the same boundary the qualitative gate blocks
# the with-crowd direction. Layer 1 Defect 8 corrected the historical
# drift (this constant was 0.0015 while the gate stayed at 0.001,
# producing a dead band 0.001-0.0015 where neither side fired).
# scanner_worker.__init__ asserts equality with the settings value at
# boot via BOOT_FUNDING_BOUNDARY_OK / _MISMATCH log lines. Phase 4
# promotes this to config so the assertion becomes redundant.
_FUNDING_EXTREME_DECIMAL = 0.001


def _trigger_funding_extreme_fade_long(
    *, funding_rate: float, regime: str,
    position_in_range: float | None,
    regime_haircut: float = 1.0,
    range_breakout: str = "",
) -> float | None:
    """Negative funding extreme fade-long trigger.

    Issue 3 fix (2026-05-19): regime gate (must not be trending_down)
    softened to a haircut. Funding-rate threshold and position_in_range
    upper-bound remain mandatory because they define the fade signal
    itself, not the regime context.
    """
    # Element 3 (2026-06-11): same false-premise guard as the range
    # fades — a mean-reversion fade toward a range the price has
    # genuinely broken (either side) is not a fade location.
    if range_breakout:
        return None
    # Negative funding = shorts pay longs → crowd is short → fade by going long.
    if funding_rate >= -_FUNDING_EXTREME_DECIMAL:
        return None
    if position_in_range is not None and position_in_range >= 0.55:
        return None
    # Confidence scales with how far past the threshold the funding is.
    excess = abs(funding_rate) - _FUNDING_EXTREME_DECIMAL
    base_conf = min(1.0, 0.40 + excess * 200.0)
    if _is_trending_down(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_funding_extreme_fade_short(
    *, funding_rate: float, regime: str,
    position_in_range: float | None,
    regime_haircut: float = 1.0,
    range_breakout: str = "",
) -> float | None:
    """Mirror of ``_trigger_funding_extreme_fade_long`` for short side."""
    if range_breakout:
        return None
    if funding_rate <= _FUNDING_EXTREME_DECIMAL:
        return None
    if position_in_range is not None and position_in_range <= 0.45:
        return None
    excess = funding_rate - _FUNDING_EXTREME_DECIMAL
    base_conf = min(1.0, 0.40 + excess * 200.0)
    if _is_trending_up(regime):
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_counter_trade_long(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bullish_fvg_ob_counter":
        return None
    if trade_direction != "long":
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.40))


def _trigger_counter_trade_short(
    *, setup_type: str, trade_direction: str, setup_type_confidence: float,
) -> float | None:
    if setup_type != "bearish_fvg_ob_counter":
        return None
    if trade_direction != "short":
        return None
    return max(0.30, min(1.0, setup_type_confidence or 0.40))


def _trigger_momentum_burst_long(
    *, regime: str, change_24h_pct: float,
    consensus_direction: str, signal_direction: str,
    volume_ratio: float | None,
) -> float | None:
    if not _is_volatile(regime):
        return None
    if change_24h_pct < 5.0:
        return None
    if consensus_direction == "short" or signal_direction == "short":
        return None
    if volume_ratio is not None and volume_ratio < 1.5:
        return None
    return min(1.0, 0.40 + change_24h_pct / 25.0)


def _trigger_momentum_burst_short(
    *, regime: str, change_24h_pct: float,
    consensus_direction: str, signal_direction: str,
    volume_ratio: float | None,
) -> float | None:
    if not _is_volatile(regime):
        return None
    if change_24h_pct > -5.0:
        return None
    if consensus_direction == "long" or signal_direction == "long":
        return None
    if volume_ratio is not None and volume_ratio < 1.5:
        return None
    return min(1.0, 0.40 + abs(change_24h_pct) / 25.0)


def _trigger_ob_mitigated_fvg_only_long(
    *, trade_direction: str,
    in_direction_fvg_present: bool | None,
    in_direction_ob_present: bool | None,
) -> float | None:
    if trade_direction != "long":
        return None
    if in_direction_fvg_present is None or in_direction_ob_present is None:
        return None  # Phase-3 caller without enriched state — silently skip.
    if in_direction_fvg_present and not in_direction_ob_present:
        return 0.50
    return None


def _trigger_ob_mitigated_fvg_only_short(
    *, trade_direction: str,
    in_direction_fvg_present: bool | None,
    in_direction_ob_present: bool | None,
) -> float | None:
    if trade_direction != "short":
        return None
    if in_direction_fvg_present is None or in_direction_ob_present is None:
        return None
    if in_direction_fvg_present and not in_direction_ob_present:
        return 0.50
    return None


def _trigger_kill_zone_opportunity(
    *, session: str, session_phase: str, setup_type: str,
) -> float | None:
    if session not in {"london", "new_york"}:
        return None
    if session_phase not in {"early", "mid"}:
        return None
    if setup_type == "none":
        return None
    return 0.55


def _trigger_extreme_fear_long(
    *, fear_greed: int, regime: str,
    consensus_direction: str, trade_direction: str,
    setup_type_confidence: float = 0.0,
    conviction_floor: float = 1.0,
    offtrend_haircut: bool = False,
    regime_haircut: float = 1.0,
) -> float | None:
    """Extreme-fear contrarian long bias trigger.

    Issue 3 fix (2026-05-19): regime gate (must not be trending_down)
    softened to a haircut. F&G window and directional anchor remain
    mandatory because they define the contrarian signal itself.

    Phase 1 calibration (2026-06-08): the fear-extremity below is a GLOBAL
    F&G-only scalar carrying NO per-coin information, which made this label
    the uniform-confidence (0.64) primary on ~87% of candidates during a
    sentiment extreme. Two calibrations of the EXISTING trigger (no new gate,
    no flip — the long-only directional anchor is untouched):
    (1) scale the fear-extremity by the coin's OWN structural conviction
        (``setup_type_confidence``) against ``conviction_floor`` so a
        structure-blind coin floors low and loses primary to a real
        structural label, while a coin with genuine bullish structure keeps a
        high score; and
    (2) when ``offtrend_haircut`` is set, broaden the counter-regime haircut
        from trending-down-only to also cover dead/balanced regimes (any
        regime that is NOT ranging / trending_up / volatile — the regimes
        where a fear contrarian-long actually has an edge).
    Back-compat defaults (conviction_floor=1.0, offtrend_haircut=False)
    reproduce the pre-calibration behaviour exactly.
    """
    if fear_greed <= 0 or fear_greed >= 20:
        return None
    points_long = consensus_direction == "long" or trade_direction == "long"
    if not points_long:
        return None
    fear_extremity = min(1.0, 0.40 + (20 - fear_greed) / 50.0)
    base_conf = fear_extremity * _conviction_scale(
        setup_type_confidence, conviction_floor
    )
    _counter_regime = _is_trending_down(regime) or (
        offtrend_haircut and not (
            _is_ranging(regime) or _is_trending_up(regime) or _is_volatile(regime)
        )
    )
    if _counter_regime:
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_extreme_greed_short(
    *, fear_greed: int, regime: str,
    consensus_direction: str, trade_direction: str,
    setup_type_confidence: float = 0.0,
    conviction_floor: float = 1.0,
    offtrend_haircut: bool = False,
    regime_haircut: float = 1.0,
) -> float | None:
    """Mirror of ``_trigger_extreme_fear_long`` for short side.

    Phase 1 calibration (2026-06-08) is applied IDENTICALLY here to avoid
    introducing a buy/sell asymmetry: the greed-extremity is scaled by the
    coin's own structural conviction, and the counter-regime haircut is
    broadened to cover any regime that is NOT ranging / trending_down /
    volatile (the regimes where a greed contrarian-short has an edge).
    """
    if fear_greed <= 80 or fear_greed > 100:
        return None
    points_short = consensus_direction == "short" or trade_direction == "short"
    if not points_short:
        return None
    greed_extremity = min(1.0, 0.40 + (fear_greed - 80) / 50.0)
    base_conf = greed_extremity * _conviction_scale(
        setup_type_confidence, conviction_floor
    )
    _counter_regime = _is_trending_up(regime) or (
        offtrend_haircut and not (
            _is_ranging(regime) or _is_trending_down(regime) or _is_volatile(regime)
        )
    )
    if _counter_regime:
        if regime_haircut <= 0.0:
            return None
        return base_conf * regime_haircut
    return base_conf


def _trigger_manipulation_window(*, manipulation_likely: bool) -> float | None:
    return 0.6 if manipulation_likely else None


def _trigger_recent_loser_cooldown(*, is_recent_loser: bool) -> float | None:
    return 0.5 if is_recent_loser else None


def _trigger_open_position_hold_review(*, has_open_position: bool) -> float | None:
    return 0.7 if has_open_position else None


# ── Public API ────────────────────────────────────────────────────────


def label_state(
    *,
    setup_type: str = "none",
    setup_type_confidence: float = 0.0,
    trade_direction: str = "",
    suggested_direction: str = "",
    regime: str = "",
    regime_confidence: float = 0.0,
    consensus: str = "",
    consensus_direction: str = "",
    funding_rate: float = 0.0,
    fear_greed: int = 0,
    change_24h_pct: float = 0.0,
    session: str = "",
    session_phase: str = "",
    manipulation_likely: bool = False,
    range_compression: bool = False,
    atr_pct_h1: float = 0.0,
    in_direction_fvg_present: bool | None = None,
    in_direction_ob_present: bool | None = None,
    counter_direction_fvg_present: bool | None = None,
    counter_direction_ob_present: bool | None = None,
    position_in_range: float | None = None,
    range_breakout: str = "",
    has_open_position: bool = False,
    is_recent_loser: bool = False,
    asian_range_broken: bool = False,
    volume_ratio: float | None = None,
    regime_haircut: float = 0.0,
    # Phase 1 calibration (2026-06-08) — extreme-sentiment label conviction
    # scaling + broadened off-trend haircut. Back-compat defaults
    # (floor=1.0, offtrend=False) reproduce the pre-calibration behaviour.
    extreme_conviction_floor: float = 1.0,
    extreme_offtrend_haircut: bool = False,
) -> StateLabelResult:
    """Classify a coin's current state into one or more opportunity labels.

    The function evaluates every trigger predicate; each may return a
    confidence value (firing) or ``None`` (not firing). All firing
    labels are collected; the one with the highest ``LABEL_BASE_WEIGHTS``
    × confidence becomes ``primary``, the rest become ``secondary``
    (sorted by base weight descending).

    Args:
        setup_type: Lowercased XRAY setup type. ``"none"`` when XRAY
            classified the coin as no-tradeable-setup.
        setup_type_confidence: 0..1 from the XRAY classifier.
        trade_direction: ``"long"``, ``"short"``, or ``""``. Opposite of
            ``suggested_direction`` for ``*_FVG_OB_COUNTER`` setups.
        suggested_direction: Direction the structural bias points
            (``"long"``/``"short"``/``""``).
        regime: Per-coin regime label. Lowercase, e.g. ``"trending_up"``,
            ``"ranging"``, ``"volatile"``, ``"dead"``.
        regime_confidence: 0..1 from the regime detector.
        consensus: ``"STRONG"`` / ``"GOOD"`` / ``"LEAN"`` / ``"WEAK"`` /
            ``"CONFLICT"`` / ``""`` (no votes).
        consensus_direction: ``"long"``/``"short"``/``"neutral"``.
        funding_rate: Decimal — positive = longs pay shorts.
        fear_greed: 0-100 (0 = no data).
        change_24h_pct: 24h price change percentage (signed).
        session: ``"asian"``/``"london"``/``"new_york"``/``"late_ny"``.
        session_phase: ``"early"``/``"mid"``/``"late"``.
        manipulation_likely: ``True`` for London-open manipulation window.
        range_compression: ``True`` when the structure engine flagged
            range compression.
        atr_pct_h1: H1 NATR as percent. (Surfaced for Phase 4 ranker.)
        in_direction_fvg_present: ``True`` when an unfilled in-direction
            FVG exists within the ATR-scaled window. ``None`` when the
            caller doesn't have this enriched data (Phase 3 callers).
        in_direction_ob_present: same, for OB.
        counter_direction_fvg_present: same, for counter direction.
        counter_direction_ob_present: same.
        position_in_range: 0..1 — current price's position inside the
            recent range (0 = at low, 1 = at high). ``None`` when
            unavailable.
        range_breakout: Element 3 (2026-06-11) — pre-clamp range truth
            from the structure engine: ``""`` (in range or unavailable),
            ``"below"`` (price broke below the range low), ``"above"``
            (price broke above the range high). ANY genuine break
            suppresses the range-fade and funding-fade labels — the
            mean-reversion fade premise requires an in-range price, in
            both directions. The default ``""`` reproduces legacy
            behaviour verbatim; production scanner_worker passes it
            gated by ``[scanner.labeller]
            range_fade_breakout_guard_enabled``.
        has_open_position: True iff the coin has an active position.
        is_recent_loser: True iff the coin closed at a loss within the
            cooldown window.
        asian_range_broken: True when the Asian range was broken in
            the current session (used by manipulation-window logic
            upstream; passed for completeness).
        volume_ratio: Volume relative to its SMA. ``None`` when unknown.
        regime_haircut: Issue 3 fix soft-haircut multiplier (2026-05-19).
            Applied to base confidence when a regime-gated trigger's
            regime predicate mismatches the current ``regime`` input.
            ``0.0`` (function default) reproduces the legacy hard-kill
            so callers that do not pass this argument see legacy
            behavior verbatim; production scanner_worker passes the
            operator-tunable value from ``[scanner.labeller]
            counter_regime_confidence_haircut`` (default ``0.5``).
            ``0.5`` → labels fire at half their normal confidence in
            mismatched regime; ``1.0`` → regime gate fully removed
            (labels fire at full confidence regardless of regime).

    Returns:
        :class:`StateLabelResult`. Always returns — never raises. When
        no trade-actionable trigger fires AND no advisory trigger fires
        (no open position, no recent loss, no manipulation), returns
        ``[NO_TRADEABLE_STATE]`` with confidence 0.0.
    """
    setup_type = (setup_type or "none").lower()
    trade_direction = (trade_direction or "").lower()
    suggested_direction = (suggested_direction or "").lower()
    regime = (regime or "").lower()
    consensus_direction = (consensus_direction or "").lower()
    range_breakout = (range_breakout or "").lower()

    fired: dict[str, float] = {}

    def _fire(label: str, conf: float | None) -> None:
        if conf is not None and conf > 0.0:
            fired[label] = max(fired.get(label, 0.0), float(conf))

    # ── Trade-actionable triggers ─────────────────────────────────
    _fire(LABEL_TREND_PULLBACK_LONG, _trigger_trend_pullback_long(
        regime=regime, setup_type=setup_type,
        trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
        regime_haircut=regime_haircut,
    ))
    _fire(LABEL_TREND_PULLBACK_SHORT, _trigger_trend_pullback_short(
        regime=regime, setup_type=setup_type,
        trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
        regime_haircut=regime_haircut,
    ))
    _fire(LABEL_RANGE_FADE_LONG, _trigger_range_fade_long(
        regime=regime, trade_direction=trade_direction,
        position_in_range=position_in_range,
        consensus_direction=consensus_direction,
        setup_type_confidence=setup_type_confidence,
        regime_haircut=regime_haircut,
        range_breakout=range_breakout,
    ))
    _fire(LABEL_RANGE_FADE_SHORT, _trigger_range_fade_short(
        regime=regime, trade_direction=trade_direction,
        position_in_range=position_in_range,
        consensus_direction=consensus_direction,
        setup_type_confidence=setup_type_confidence,
        regime_haircut=regime_haircut,
        range_breakout=range_breakout,
    ))
    _fire(LABEL_BREAKOUT_PENDING, _trigger_breakout_pending(
        setup_type=setup_type, range_compression=range_compression,
        regime=regime, setup_type_confidence=setup_type_confidence,
    ))
    _fire(LABEL_LIQUIDITY_SWEEP_REVERSAL_LONG, _trigger_liquidity_sweep_long(
        setup_type=setup_type, trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
    ))
    _fire(LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT, _trigger_liquidity_sweep_short(
        setup_type=setup_type, trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
    ))
    _fire(LABEL_FUNDING_EXTREME_FADE_LONG, _trigger_funding_extreme_fade_long(
        funding_rate=funding_rate, regime=regime,
        position_in_range=position_in_range,
        regime_haircut=regime_haircut,
        range_breakout=range_breakout,
    ))
    _fire(LABEL_FUNDING_EXTREME_FADE_SHORT, _trigger_funding_extreme_fade_short(
        funding_rate=funding_rate, regime=regime,
        position_in_range=position_in_range,
        regime_haircut=regime_haircut,
        range_breakout=range_breakout,
    ))
    _fire(LABEL_COUNTER_TRADE_LONG, _trigger_counter_trade_long(
        setup_type=setup_type, trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
    ))
    _fire(LABEL_COUNTER_TRADE_SHORT, _trigger_counter_trade_short(
        setup_type=setup_type, trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
    ))
    _fire(LABEL_MOMENTUM_BURST_LONG, _trigger_momentum_burst_long(
        regime=regime, change_24h_pct=change_24h_pct,
        consensus_direction=consensus_direction,
        signal_direction=consensus_direction,  # legacy alias for now
        volume_ratio=volume_ratio,
    ))
    _fire(LABEL_MOMENTUM_BURST_SHORT, _trigger_momentum_burst_short(
        regime=regime, change_24h_pct=change_24h_pct,
        consensus_direction=consensus_direction,
        signal_direction=consensus_direction,
        volume_ratio=volume_ratio,
    ))
    _fire(LABEL_OB_MITIGATED_FVG_ONLY_LONG, _trigger_ob_mitigated_fvg_only_long(
        trade_direction=trade_direction,
        in_direction_fvg_present=in_direction_fvg_present,
        in_direction_ob_present=in_direction_ob_present,
    ))
    _fire(LABEL_OB_MITIGATED_FVG_ONLY_SHORT, _trigger_ob_mitigated_fvg_only_short(
        trade_direction=trade_direction,
        in_direction_fvg_present=in_direction_fvg_present,
        in_direction_ob_present=in_direction_ob_present,
    ))
    _fire(LABEL_KILL_ZONE_OPPORTUNITY, _trigger_kill_zone_opportunity(
        session=session, session_phase=session_phase, setup_type=setup_type,
    ))
    _fire(LABEL_EXTREME_FEAR_LONG_BIAS, _trigger_extreme_fear_long(
        fear_greed=fear_greed, regime=regime,
        consensus_direction=consensus_direction,
        trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
        conviction_floor=extreme_conviction_floor,
        offtrend_haircut=extreme_offtrend_haircut,
        regime_haircut=regime_haircut,
    ))
    _fire(LABEL_EXTREME_GREED_SHORT_BIAS, _trigger_extreme_greed_short(
        fear_greed=fear_greed, regime=regime,
        consensus_direction=consensus_direction,
        trade_direction=trade_direction,
        setup_type_confidence=setup_type_confidence,
        conviction_floor=extreme_conviction_floor,
        offtrend_haircut=extreme_offtrend_haircut,
        regime_haircut=regime_haircut,
    ))

    # ── Advisory triggers (always evaluated; surface for transparency) ──
    _fire(LABEL_MANIPULATION_WINDOW, _trigger_manipulation_window(
        manipulation_likely=manipulation_likely,
    ))
    _fire(LABEL_RECENT_LOSER_COOLDOWN, _trigger_recent_loser_cooldown(
        is_recent_loser=is_recent_loser,
    ))
    _fire(LABEL_OPEN_POSITION_HOLD_REVIEW, _trigger_open_position_hold_review(
        has_open_position=has_open_position,
    ))

    # ── Pick primary by base weight × confidence; fall through to NO_TRADEABLE_STATE ──
    if not fired:
        return StateLabelResult(
            primary=LABEL_NO_TRADEABLE_STATE,
            secondary=[],
            confidence=0.0,
        )

    # Sort by (base_weight × confidence) descending, ties broken by base_weight alone.
    ranked = sorted(
        fired.items(),
        key=lambda kv: (
            LABEL_BASE_WEIGHTS.get(kv[0], 0.0) * kv[1],
            LABEL_BASE_WEIGHTS.get(kv[0], 0.0),
        ),
        reverse=True,
    )
    primary, primary_conf = ranked[0]
    secondary = [name for name, _ in ranked[1:]]
    return StateLabelResult(
        primary=primary,
        secondary=secondary,
        confidence=round(primary_conf, 4),
    )
