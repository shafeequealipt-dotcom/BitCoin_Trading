"""Entry Volume-Ratio Gate (2026-07-15) — Phase 0, observe-only.

Pure-function gate. Returns a structured verdict for a proposed trade's
entry-time ``volume_ratio`` (M5 current volume vs its SMA). No I/O, no
settings object, no service dependencies — trivial to unit test and safe
to call from a hot execution path.

Why this exists: a 371-trade VM analysis (2026-07-11..14,
``trade_intelligence``) found volume_ratio at entry separates winners
from losers — the first entry-time feature to do so, after the June
diagnosis (ENTRIES_QUALITY_DIAGNOSIS.md) found none among X-RAY
confidence, signal confidence, ensemble agreement, regime confidence, or
ADX. See IMPLEMENT_ENTRY_VOLUME_GATE.md for the full evidence, the five
robustness checks it passed, and the phased rollout plan.

Fail-open convention: ``volume_ratio is None`` (feature unavailable, e.g.
insufficient candle history) always passes. This matches the existing
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
