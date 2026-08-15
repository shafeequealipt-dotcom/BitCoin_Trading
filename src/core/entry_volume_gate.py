"""Entry Quality Gates — volume-ratio (2026-07-15), ATR (2026-07-16),
recent-loss (2026-07-17), symbol circuit-breaker (2026-08-02).

Pure-function gates. Each returns a structured verdict for one entry-time
feature of a proposed trade. No I/O, no settings object, no service
dependencies — trivial to unit test and safe to call from a hot execution
path. The gates are independent: separate thresholds, separate modes,
separate verdicts — the caller (strategy_worker) decides per-gate whether
a ``would_block`` verdict actually skips the trade.

## Volume-ratio gate (deployed, enforcing since 2026-07-15)

A 371-trade VM analysis (2026-07-11..14, ``trade_intelligence``) found
volume_ratio at entry separates winners from losers — the first entry-time
feature to do so, after the June diagnosis (ENTRIES_QUALITY_DIAGNOSIS.md)
found none among X-RAY confidence, signal confidence, ensemble agreement,
regime confidence, or ADX. See IMPLEMENT_ENTRY_VOLUME_GATE.md for the full
evidence and phased rollout. Later correction (see
IMPLEMENT_ENTRY_QUALITY_SELECTIVITY.md §1b/§4): the original separation was
partly a close-time capture artifact; the live entry-time gate data shows
no gradient above the 0.30 floor, so this gate is kept as a dead-tape floor
and NOT tuned upward.

## ATR gate (2026-07-16)

A 342-trade analysis (2026-07-13..16, split at the R:R fix deploy) found
entry ATR% is a strong, monotonic selector: trades entered on near-flat
coins (ATR < 0.20%) lose money as a cohort (cum PnL negative across two
independent windows); trades entered on genuinely moving coins (ATR >=
0.20%) carry the entire post-fix profit (68% win, +14.6% cum on the >=0.25
split). Mechanism: a barely-moving coin can't reach TP before fees and
stall/timeout exits erode it. See IMPLEMENT_ENTRY_QUALITY_SELECTIVITY.md
§1a for the full evidence and robustness caveats.

Fail-open convention (both gates): a ``None`` feature value (unavailable,
e.g. insufficient candle history) always passes. This matches the existing
per-label volume gates in ``src/workers/scanner/state_labeler.py``
("volume_ratio gate bypassed when input is None") — a data outage must
never silently halt trading.

## Recent-loss gate (2026-07-17)

Forensic trace of every GWEIUSDT trade in the first ~21h post-ATR-gate
window found a same-direction repeat-loss pattern the system was
already *supposed* to prevent: the brain's own prompt carries a
``RECENT_LOSER_COOLDOWN`` rule ("closed at a loss within 1h — do NOT
re-enter... require fresh, independent per-coin structure"), and the
scanner has a `recent_failure_blocker_hours=1` qualitative blocker
(``scanner_worker._check_blockers``) — but three consecutive GWEIUSDT
shorts closed at -1.76%, -1.90%, -1.94% within 59 minutes of each
other, and one surviving thesis literally reads "Despite
RECENT_LOSER_COOLDOWN, the setup quality is B and the action hint
suggests short-side pullback continuation." Both existing mechanisms
are either advisory (the prompt rule — the free-tier model can and did
rationalize past it) or upstream in the pipeline (the scanner blocker,
which a force-included/protected coin can route around). See
IMPLEMENT_ENTRY_QUALITY_SELECTIVITY.md §8 for the full forensic trace.

This gate is the last-mile version: it runs at the same point as the
volume-ratio and ATR gates — immediately before order placement, after
every other check has passed — so no upstream bypass matters. It counts
LOSSES on this exact (symbol, direction) pair within a lookback window
and blocks if the count meets or exceeds a threshold, independent of
the brain's own self-assessed "setup quality."

## Symbol circuit-breaker gate (2026-08-02)

The recent-loss gate above is scoped to one (symbol, direction) pair, so
a symbol bleeding money on alternating directions (a loss shorting, then
a loss longing the bounce, then a loss shorting again) sails through it
every time — each individual (symbol, direction) count never reaches the
threshold even though the SYMBOL itself is clearly a repeat offender. A
404-trade audit found exactly this: GWEIUSDT lost -$26.25 over 16 trades
and CLUSDT lost -$14.32 over 25, both mixed-direction, post-dating the
recent-loss gate's deploy.

This gate is symbol-only (direction-independent) and dual-threshold: it
blocks on EITHER cumulative dollar loss OR loss count within the lookback
window, whichever trips first. Same last-mile execution point and
fail-safe conventions as its siblings.
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICT_PASS = "pass"
VERDICT_BLOCK = "block"
VERDICT_UNKNOWN_PASS = "unknown_pass"


@dataclass(frozen=True)
class VolumeGateResult:
    """Structured verdict for one entry-volume-gate evaluation.

    Attributes:
        verdict: One of VERDICT_PASS / VERDICT_BLOCK / VERDICT_UNKNOWN_PASS.
        would_block: True iff volume_ratio was known and below threshold.
            Distinct from ``verdict`` so Phase 0 (observe mode) can log
            what WOULD have happened without ever actually blocking —
            the caller decides whether to act on ``would_block``.
        volume_ratio: The measured value, or None if unavailable.
        threshold: The ``min_volume_ratio`` this was evaluated against.
        reason: Short machine-readable reason code for the verdict.
    """
    verdict: str
    would_block: bool
    volume_ratio: float | None
    threshold: float
    reason: str


def evaluate_entry_volume_gate(
    volume_ratio: float | None,
    min_volume_ratio: float,
) -> VolumeGateResult:
    """Evaluate a proposed trade's entry volume_ratio against the gate.

    Args:
        volume_ratio: Current M5 volume / SMA at entry time, or None when
            the TA cache had no data for the symbol.
        min_volume_ratio: Threshold below which a trade would be flagged/
            blocked. <= 0 disables the gate entirely (always passes) —
            the config-level kill switch.

    Returns:
        VolumeGateResult with the verdict and would_block flag. The
        caller (strategy_worker) decides whether would_block actually
        skips the trade, based on the gate's configured mode
        ("observe" vs "enforce").
    """
    if min_volume_ratio <= 0:
        return VolumeGateResult(
            verdict=VERDICT_PASS, would_block=False,
            volume_ratio=volume_ratio, threshold=min_volume_ratio,
            reason="gate_disabled_threshold_zero",
        )
    if volume_ratio is None:
        return VolumeGateResult(
            verdict=VERDICT_UNKNOWN_PASS, would_block=False,
            volume_ratio=None, threshold=min_volume_ratio,
            reason="volume_ratio_unavailable",
        )
    if volume_ratio < min_volume_ratio:
        return VolumeGateResult(
            verdict=VERDICT_BLOCK, would_block=True,
            volume_ratio=volume_ratio, threshold=min_volume_ratio,
            reason="volume_ratio_below_threshold",
        )
    return VolumeGateResult(
        verdict=VERDICT_PASS, would_block=False,
        volume_ratio=volume_ratio, threshold=min_volume_ratio,
        reason="volume_ratio_ok",
    )


@dataclass(frozen=True)
class ATRGateResult:
    """Structured verdict for one entry-ATR-gate evaluation.

    Mirrors ``VolumeGateResult`` field-for-field (see its docstring for the
    meaning of each field) — the two gates are evaluated independently by
    the caller but share the same verdict/would_block/reason shape so
    logging and downstream analysis treat them uniformly.
    """
    verdict: str
    would_block: bool
    atr_pct: float | None
    threshold: float
    reason: str


def evaluate_entry_atr_gate(
    atr_pct: float | None,
    min_atr_pct: float,
) -> ATRGateResult:
    """Evaluate a proposed trade's entry ATR% against the gate.

    Args:
        atr_pct: The coin's ATR as a percent of price at entry time (TA
            engine's ``natr_14``), or None when unavailable.
        min_atr_pct: Threshold below which a trade would be flagged/
            blocked — a coin moving less than this is "dead tape" and
            structurally can't reach TP before fees/stall erode it.
            <= 0 disables the gate entirely (always passes) — the
            config-level kill switch.

    Returns:
        ATRGateResult with the verdict and would_block flag. The caller
        decides whether would_block actually skips the trade, based on
        the gate's configured mode ("observe" vs "enforce").
    """
    if min_atr_pct <= 0:
        return ATRGateResult(
            verdict=VERDICT_PASS, would_block=False,
            atr_pct=atr_pct, threshold=min_atr_pct,
            reason="gate_disabled_threshold_zero",
        )
    if atr_pct is None:
        return ATRGateResult(
            verdict=VERDICT_UNKNOWN_PASS, would_block=False,
            atr_pct=None, threshold=min_atr_pct,
            reason="atr_pct_unavailable",
        )
    if atr_pct < min_atr_pct:
        return ATRGateResult(
            verdict=VERDICT_BLOCK, would_block=True,
            atr_pct=atr_pct, threshold=min_atr_pct,
            reason="atr_pct_below_threshold",
        )
    return ATRGateResult(
        verdict=VERDICT_PASS, would_block=False,
        atr_pct=atr_pct, threshold=min_atr_pct,
        reason="atr_pct_ok",
    )


@dataclass(frozen=True)
class RecentLossGateResult:
    """Structured verdict for one recent-loss-gate evaluation.

    Unlike the volume-ratio and ATR gates, ``recent_loss_count`` is never
    "unavailable" — a DB query failure or a genuinely empty history both
    resolve to 0 (no fail-open ambiguity needed; 0 losses naturally
    passes). See module docstring for why this gate exists.
    """
    verdict: str
    would_block: bool
    recent_loss_count: int
    threshold: int
    reason: str


def evaluate_recent_loss_gate(
    recent_loss_count: int,
    max_recent_losses: int,
) -> RecentLossGateResult:
    """Evaluate a proposed trade's recent same-(symbol, direction) loss count.

    Args:
        recent_loss_count: Number of losing closes on this exact
            (symbol, direction) pair within the configured lookback
            window (computed by the caller via a direct DB query — kept
            out of this pure function so it stays trivially testable).
        max_recent_losses: Block once the count reaches this many.
            <= 0 disables the gate entirely (always passes) — the
            config-level kill switch.

    Returns:
        RecentLossGateResult with the verdict and would_block flag. The
        caller decides whether would_block actually skips the trade,
        based on the gate's configured mode ("observe" vs "enforce").
    """
    if max_recent_losses <= 0:
        return RecentLossGateResult(
            verdict=VERDICT_PASS, would_block=False,
            recent_loss_count=recent_loss_count, threshold=max_recent_losses,
            reason="gate_disabled_threshold_zero",
        )
    if recent_loss_count >= max_recent_losses:
        return RecentLossGateResult(
            verdict=VERDICT_BLOCK, would_block=True,
            recent_loss_count=recent_loss_count, threshold=max_recent_losses,
            reason="recent_loss_threshold_reached",
        )
    return RecentLossGateResult(
        verdict=VERDICT_PASS, would_block=False,
        recent_loss_count=recent_loss_count, threshold=max_recent_losses,
        reason="recent_loss_count_ok",
    )


@dataclass(frozen=True)
class SymbolCircuitBreakerResult:
    """Structured verdict for one symbol-circuit-breaker evaluation.

    Like ``RecentLossGateResult``, never "unavailable" — a DB query
    failure or genuinely empty history both resolve to 0/0.0 (no
    fail-open ambiguity needed).
    """
    verdict: str
    would_block: bool
    cumulative_loss_usd: float
    loss_count: int
    max_cumulative_loss_usd: float
    max_loss_count: int
    reason: str


def evaluate_symbol_circuit_breaker_gate(
    cumulative_loss_usd: float,
    loss_count: int,
    max_cumulative_loss_usd: float,
    max_loss_count: int,
) -> SymbolCircuitBreakerResult:
    """Evaluate a symbol's recent loss history against the circuit breaker.

    Args:
        cumulative_loss_usd: Sum of ``pnl_usd`` for this symbol's LOSING
            closes within the lookback window, as a non-positive number
            (computed by the caller via a direct DB query, e.g.
            ``SUM(pnl_usd) WHERE pnl_usd < 0`` — kept out of this pure
            function so it stays trivially testable). 0.0 when there are
            no losses in the window.
        loss_count: Number of losing closes on this symbol (any
            direction) within the same window.
        max_cumulative_loss_usd: Block once the MAGNITUDE of cumulative
            loss reaches this many dollars. <= 0 disables the
            dollar-threshold check only (the count check below still
            applies independently).
        max_loss_count: Block once the loss count reaches this many.
            <= 0 disables the count-threshold check only.
        Both thresholds <= 0 disables the gate entirely (always passes) —
        the config-level kill switch.

    Returns:
        SymbolCircuitBreakerResult with the verdict and would_block flag.
        The caller decides whether would_block actually skips the trade,
        based on the gate's configured mode ("observe" vs "enforce").
    """
    if max_cumulative_loss_usd <= 0 and max_loss_count <= 0:
        return SymbolCircuitBreakerResult(
            verdict=VERDICT_PASS, would_block=False,
            cumulative_loss_usd=cumulative_loss_usd, loss_count=loss_count,
            max_cumulative_loss_usd=max_cumulative_loss_usd,
            max_loss_count=max_loss_count,
            reason="gate_disabled_thresholds_zero",
        )

    _dollar_tripped = (
        max_cumulative_loss_usd > 0
        and abs(cumulative_loss_usd) >= max_cumulative_loss_usd
    )
    _count_tripped = max_loss_count > 0 and loss_count >= max_loss_count

    if _dollar_tripped and _count_tripped:
        reason = "cumulative_loss_and_count_threshold_reached"
    elif _dollar_tripped:
        reason = "cumulative_loss_threshold_reached"
    elif _count_tripped:
        reason = "loss_count_threshold_reached"
    else:
        reason = "symbol_loss_history_ok"

    return SymbolCircuitBreakerResult(
        verdict=VERDICT_BLOCK if (_dollar_tripped or _count_tripped) else VERDICT_PASS,
        would_block=_dollar_tripped or _count_tripped,
        cumulative_loss_usd=cumulative_loss_usd, loss_count=loss_count,
        max_cumulative_loss_usd=max_cumulative_loss_usd,
        max_loss_count=max_loss_count,
        reason=reason,
    )


@dataclass(frozen=True)
class MinMoveGateResult:
    """Structured verdict for one minimum-expected-move-vs-fee evaluation.

    Mirrors ``ATRGateResult``'s shape (fail-open on missing data) — see its
    docstring for the meaning of each shared field.

    ``expected_capture_pct`` is the REALISTIC first-lock profit level (the
    adaptive-exit arm), not the brain's aspirational take-profit target.
    See ``evaluate_min_move_gate`` for why that distinction is the whole
    point of this gate.
    """
    verdict: str
    would_block: bool
    expected_capture_pct: float | None
    required_pct: float
    reason: str


def evaluate_min_move_gate(
    expected_capture_pct: float | None,
    round_trip_fee_pct: float,
    min_fee_multiple: float,
) -> MinMoveGateResult:
    """Evaluate whether a trade's REALISTIC profit capture clears round-trip
    cost by a comfortable margin.

    ## Why this compares the arm, not the take-profit target (2026-08-15)

    The first version of this gate compared the brain's PLANNED take-profit
    distance against the fee. That made it a no-op: planned TPs average
    ~6.5% while the threshold was 0.33%, so it blocked 0 of 1039 trades.
    The flaw was comparing an aspirational number to a real cost — live
    trades captured only 10.6% of their planned target, so planned TP says
    nothing about what the trade will actually bank.

    The honest input is the level at which the exit machinery first locks
    profit — ``vol_scale.arm_pct(R, adaptive_exit)`` where ``R`` is the
    coin's ATR%. That is the smallest move the trade must make for a
    "win" to be bankable at all. If THAT cannot clear the round-trip cost
    by a healthy multiple, the trade is structurally unprofitable no
    matter how good the direction call is.

    Empirical backing: bucketing 283 live trades by entry ATR and
    subtracting the measured 0.2398% round-trip cost, every band below
    ATR ~1.0% was net-negative pre-Phase-1, and win rate FELL as ATR rose
    (60.9% -> 45.3%) while net PnL improved — proof the binding constraint
    is move size versus fixed cost, not direction accuracy.

    Args:
        expected_capture_pct: The realistic first-lock profit level as a
            percent of entry (the adaptive-exit arm). None when the caller
            could not compute it (e.g. ATR unavailable) -> fail-open.
        round_trip_fee_pct: Measured all-in round-trip cost percent
            (``settings.adaptive_exit.round_trip_fee_pct``) — the single
            shared constant, not a new fee number.
        min_fee_multiple: Capture must be at least this many multiples of
            the round-trip cost. <= 0 disables the gate (always passes) —
            the config-level kill switch.

    Returns:
        MinMoveGateResult with the verdict and would_block flag. The
        caller decides whether would_block actually skips the trade,
        based on the gate's configured mode ("observe" vs "enforce").
    """
    _required_pct = round_trip_fee_pct * min_fee_multiple
    if min_fee_multiple <= 0:
        return MinMoveGateResult(
            verdict=VERDICT_PASS, would_block=False,
            expected_capture_pct=expected_capture_pct, required_pct=_required_pct,
            reason="gate_disabled_threshold_zero",
        )
    if expected_capture_pct is None:
        return MinMoveGateResult(
            verdict=VERDICT_UNKNOWN_PASS, would_block=False,
            expected_capture_pct=None, required_pct=_required_pct,
            reason="expected_capture_unavailable",
        )
    if expected_capture_pct < _required_pct:
        return MinMoveGateResult(
            verdict=VERDICT_BLOCK, would_block=True,
            expected_capture_pct=expected_capture_pct, required_pct=_required_pct,
            reason="capture_below_fee_multiple",
        )
    return MinMoveGateResult(
        verdict=VERDICT_PASS, would_block=False,
        expected_capture_pct=expected_capture_pct, required_pct=_required_pct,
        reason="capture_clears_fee",
    )
