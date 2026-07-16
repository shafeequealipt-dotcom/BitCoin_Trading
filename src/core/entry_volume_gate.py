"""Entry Quality Gates — volume-ratio (2026-07-15) + ATR (2026-07-16).

Pure-function gates. Each returns a structured verdict for one entry-time
feature of a proposed trade. No I/O, no settings object, no service
dependencies — trivial to unit test and safe to call from a hot execution
path. The two gates are independent: separate thresholds, separate modes,
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
