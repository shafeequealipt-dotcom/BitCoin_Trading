"""Brain Liveness Heartbeat (2026-07-25) — cross-process hang detection.

``trading-brain`` and ``trading-workers`` are separate OS processes (separate
systemd units, separate event loops) — the existing in-process
``WorkerLivenessTracker`` (src/core/worker_liveness.py) has no visibility
into the brain process at all. This module is the pure, testable half of a
cross-process watchdog: ``LayerManager._brain_review_loop`` writes a
heartbeat file at the top of every loop iteration (see
``write_brain_heartbeat``), and ``BrainLivenessWatchdog``
(src/workers/brain_liveness_watchdog.py), running inside the always-healthy
``trading-workers`` process, reads it and calls ``evaluate_brain_liveness``
to decide whether to alarm.

Why this exists: on 2026-07-23/24 ``trading-brain`` hung for 28+ hours with
ZERO log lines and ZERO exceptions — the process was "active" per systemd
(never crashed) but stuck forever on a single ``await`` that never returned
(almost certainly an un-timed-out network call) inside
``_run_brain_cycle()``. The loop's own try/except around that call can only
catch exceptions or cancellation; it cannot catch a coroutine that simply
never resumes. Nothing detected this until the operator asked "what's the
status" and it was found by hand. Heartbeat staleness is the only signal
that works for this failure mode: writing at the START of each iteration
(not after ``_run_brain_cycle`` completes) means a stuck iteration is
visible as growing staleness, even though the "cycle finished" event that
normally logs it never fires.

Fail-open convention: like the entry-quality gates, a missing/unreadable
heartbeat file is reported as ``VERDICT_UNKNOWN`` (not immediately
``VERDICT_STALE``) so a fresh boot (before the first heartbeat write) or a
transient read race never fires a false alarm on its own — the caller
decides how many consecutive UNKNOWNs constitute a real problem.
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICT_HEALTHY = "healthy"
VERDICT_STALE = "stale"
VERDICT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrainLivenessResult:
    """Structured verdict for one liveness check.

    Attributes:
        verdict: One of VERDICT_HEALTHY / VERDICT_STALE / VERDICT_UNKNOWN.
        age_seconds: Seconds since the heartbeat was last written, or None
            if the heartbeat is unavailable/unparseable.
        threshold_seconds: The staleness threshold this was evaluated against.
        reason: Short machine-readable reason code.
    """
    verdict: str
    age_seconds: float | None
    threshold_seconds: float
    reason: str


def evaluate_brain_liveness(
    heartbeat_timestamp: float | None,
    now: float,
    threshold_seconds: float,
) -> BrainLivenessResult:
    """Evaluate the brain process's liveness from its last heartbeat write.

    Args:
        heartbeat_timestamp: Unix timestamp of the last heartbeat write, or
            None if the heartbeat file doesn't exist / couldn't be parsed.
        now: Current unix timestamp (caller-supplied so this stays pure and
            trivially testable — no ``time.time()`` inside).
        threshold_seconds: Age beyond which the heartbeat is considered
            stale. <= 0 disables the check entirely (always healthy) — the
            config-level kill switch, same convention as the entry gates.

    Returns:
        BrainLivenessResult with the verdict and measured age.
    """
    if threshold_seconds <= 0:
        return BrainLivenessResult(
            verdict=VERDICT_HEALTHY, age_seconds=None,
            threshold_seconds=threshold_seconds,
            reason="check_disabled_threshold_zero",
        )
    if heartbeat_timestamp is None:
        return BrainLivenessResult(
            verdict=VERDICT_UNKNOWN, age_seconds=None,
            threshold_seconds=threshold_seconds,
            reason="heartbeat_unavailable",
        )
    age = now - heartbeat_timestamp
    if age < 0:
        # Clock skew or a heartbeat written "in the future" (e.g. reading a
        # half-written file mid-rename, or NTP jump). Never alarm on this —
        # report unknown so the caller's next tick (a fresh read) resolves it.
        return BrainLivenessResult(
            verdict=VERDICT_UNKNOWN, age_seconds=age,
            threshold_seconds=threshold_seconds,
            reason="heartbeat_in_future",
        )
    if age > threshold_seconds:
        return BrainLivenessResult(
            verdict=VERDICT_STALE, age_seconds=age,
            threshold_seconds=threshold_seconds,
            reason="heartbeat_stale",
        )
    return BrainLivenessResult(
        verdict=VERDICT_HEALTHY, age_seconds=age,
        threshold_seconds=threshold_seconds,
        reason="heartbeat_fresh",
    )


def compute_staleness_threshold(
    strategic_interval_seconds: float,
    multiplier: float,
    floor_seconds: float,
) -> float:
    """Derive the staleness threshold from the brain's own configured cadence.

    Deliberately NOT a fixed constant: the operator has already changed
    ``strategic_interval`` once this project (150s -> 2700s, 2026-07-23,
    to fit a free-tier LLM budget) — a hardcoded threshold would have
    needed a matching manual update or it would either false-alarm
    constantly (threshold too tight for the new slow cadence) or take
    hours to notice a real hang (threshold too loose). Deriving it from
    the live setting means the watchdog auto-adapts to future cadence
    changes with zero extra wiring.

    Args:
        strategic_interval_seconds: The brain's configured A/B alternation
            interval (``settings.brain.strategic_interval``).
        multiplier: How many intervals of silence are tolerated before
            alarming. >1 to allow for one slow/retried cycle without a
            false alarm (LLM calls can legitimately take tens of seconds,
            plus up to ``glm_max_retries`` retries).
        floor_seconds: Minimum threshold regardless of interval, so a very
            fast-cadence config doesn't produce alarm-spam from ordinary
            LLM latency variance.

    Returns:
        The staleness threshold in seconds.
    """
    return max(floor_seconds, strategic_interval_seconds * multiplier)
