"""Sweet-spot scheduler — corrected Layer 1 architecture (Phase 1).

Replaces ``BaseWorker``'s fixed-interval ``await asyncio.sleep(self.interval)``
with a wall-clock-anchored ``await scheduler.wait_for_sweet_spot()``. Each
worker fires once per ``window_minutes`` at its configured MM:SS offset.

Why time-based, not event-based: simpler, predictable, visible in config.
Workers don't synchronize via inter-worker events — the chain of sweet
spots is implicit through timing. See blueprint §8.3 for rationale.

This module is the runtime counterpart to ``SweetSpotsSettings`` in
``src/config/settings.py``. Validation lives in the dataclass; the parser
here is a thin pure function used at runtime.
"""

import asyncio
import time
from dataclasses import dataclass

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("worker")


def parse_sweet_spot(value: str) -> tuple[int, int]:
    """Parse an MM:SS offset string into ``(minutes, seconds)``.

    Args:
        value: String like ``"0:30"`` or ``"1:45"``. Whitespace not tolerated.

    Returns:
        ``(minutes, seconds)`` tuple.

    Raises:
        ValueError: If ``value`` is not a properly-formed MM:SS string with
            integer components in the legal ranges (minute 0-59, second 0-59).
            Validation against window bounds is the dataclass's job —
            ``SweetSpotsSettings.__post_init__`` rejects out-of-window minutes
            BEFORE this parser ever sees them at runtime.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"sweet spot must be a string in MM:SS format, "
            f"got {type(value).__name__}: {value!r}"
        )
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"sweet spot must be in MM:SS format, got {value!r}")
    try:
        m = int(parts[0])
        s = int(parts[1])
    except ValueError as e:
        raise ValueError(
            f"sweet spot MM:SS components must be integers, got {value!r}"
        ) from e
    if m < 0 or m > 59:
        raise ValueError(f"sweet spot minute must be 0-59, got {m}")
    if s < 0 or s > 59:
        raise ValueError(f"sweet spot second must be 0-59, got {s}")
    return (m, s)


def seconds_until_next_sweet_spot(
    spot: tuple[int, int],
    *,
    window_minutes: int = 5,
    now: float | None = None,
    skip_threshold_s: float = 0.1,
) -> float:
    """Seconds from ``now`` until the next firing of ``spot`` within the window.

    Windows are anchored to wall-clock seconds — for a 5-minute window,
    they begin at every clock minute divisible by 5 (00:00, 00:05, 00:10,
    ..., 23:55). The configured offset's distance into each window is the
    same every cycle, so two workers configured at ``"0:30"`` and ``"1:00"``
    will fire 30 seconds apart every window forever.

    Args:
        spot: ``(minute, second)`` from ``parse_sweet_spot``.
        window_minutes: Window length (default 5).
        now: Optional wall-clock seconds (UTC epoch). Defaults to ``time.time()``.
            Test code injects deterministic times here.
        skip_threshold_s: If the spot is within this many seconds in the past
            OR the future, fire in the NEXT window instead. This avoids
            same-cycle re-fires when this function is called at the exact
            moment the worker just fired.

    Returns:
        Strictly positive seconds. Caller passes this directly to
        ``asyncio.sleep``.
    """
    if now is None:
        now = time.time()
    window_s = window_minutes * 60
    spot_s = spot[0] * 60 + spot[1]
    pos_in_window = now % window_s
    delta = spot_s - pos_in_window
    if delta > skip_threshold_s:
        return delta
    return delta + window_s


def is_at_sweet_spot(
    spot: tuple[int, int],
    *,
    window_minutes: int = 5,
    now: float | None = None,
    tolerance_s: float = 1.0,
) -> bool:
    """True iff ``now`` is within ``tolerance_s`` of the spot inside its window.

    Used by tests and ad-hoc verification. Production scheduling never
    needs this — it sleeps until the spot via ``wait_for_sweet_spot``.
    """
    if now is None:
        now = time.time()
    window_s = window_minutes * 60
    spot_s = spot[0] * 60 + spot[1]
    pos_in_window = now % window_s
    return abs(pos_in_window - spot_s) <= tolerance_s


@dataclass
class SweetSpotStats:
    """Runtime stats for one scheduler instance.

    Attributes:
        fires: Number of times ``wait_for_sweet_spot`` has returned.
        cumulative_drift_ms: Sum of absolute drifts; divide by ``fires``
            for mean drift.
        max_drift_ms: Worst single drift observed.
        last_drift_ms: Drift of the most recent fire.
    """
    fires: int = 0
    cumulative_drift_ms: float = 0.0
    max_drift_ms: float = 0.0
    last_drift_ms: float = 0.0


class SweetSpotScheduler:
    """Schedules a worker's tick at a fixed MM:SS offset within each window.

    The scheduler is owned by a single worker. ``wait_for_sweet_spot``
    sleeps until the next firing and returns the drift (positive = late).
    Workers should treat the drift as observability only; the schedule
    re-anchors to the wall clock every cycle, so a slow tick this window
    does NOT compound across cycles.

    Args:
        worker_name: For log lines (``SWEET_SPOT_REGISTERED`` / ``SWEET_SPOT_FIRED``).
        offset: MM:SS string (validated upstream by ``SweetSpotsSettings``).
        window_minutes: Window length (default 5).

    Emits at construction:
        ``SWEET_SPOT_REGISTERED | worker={name} offset={MM:SS} window_min={n} | {ctx()}``

    Emits per fire:
        ``SWEET_SPOT_FIRED | worker={name} offset={MM:SS} drift_ms={d} | {ctx()}``
    """

    def __init__(
        self,
        worker_name: str,
        offset: str,
        window_minutes: int = 5,
    ) -> None:
        self.worker_name = worker_name
        self.offset_str = offset
        self.offset = parse_sweet_spot(offset)
        self.window_minutes = int(window_minutes)
        if self.window_minutes < 1:
            raise ValueError(
                f"window_minutes must be >= 1, got {window_minutes!r}"
            )
        self.stats = SweetSpotStats()
        log.info(
            f"SWEET_SPOT_REGISTERED | worker={self.worker_name} "
            f"offset={self.offset_str} window_min={self.window_minutes} | {ctx()}"
        )

    def seconds_until_next(self, *, now: float | None = None) -> float:
        """Wrapper around the module-level helper, useful for tests."""
        return seconds_until_next_sweet_spot(
            self.offset, window_minutes=self.window_minutes, now=now,
        )

    async def wait_for_sweet_spot(self) -> float:
        """Sleep until the next sweet spot, then return drift in ms.

        Returns:
            Drift in milliseconds — positive = woke up after the target,
            negative = woke up early (rare; only happens when ``asyncio.sleep``
            returns slightly under-time on some platforms).

        Note:
            Drift is computed against the wall-clock anchor, not against
            when ``asyncio.sleep`` returned. So if a worker's previous tick
            ran long enough that we missed this cycle's spot entirely, the
            scheduler waits for the NEXT window's spot and reports drift
            relative to THAT window's anchor — not a five-minute miss.
        """
        delay_s = self.seconds_until_next()
        await asyncio.sleep(delay_s)

        now = time.time()
        window_s = self.window_minutes * 60
        spot_s = self.offset[0] * 60 + self.offset[1]
        pos_in_window = now % window_s
        drift_s = pos_in_window - spot_s
        # Normalize: a tiny early/late split-window flip can yield very large
        # absolute drift (e.g. spot=0:30, pos_in_window=4:59 because the
        # asyncio.sleep returned just before the next window). Treat as the
        # smaller of (drift, drift - window_s) by absolute value.
        if abs(drift_s - window_s) < abs(drift_s):
            drift_s -= window_s
        elif abs(drift_s + window_s) < abs(drift_s):
            drift_s += window_s
        drift_ms = drift_s * 1000.0

        self.stats.fires += 1
        self.stats.cumulative_drift_ms += abs(drift_ms)
        if abs(drift_ms) > self.stats.max_drift_ms:
            self.stats.max_drift_ms = abs(drift_ms)
        self.stats.last_drift_ms = drift_ms

        log.info(
            f"SWEET_SPOT_FIRED | worker={self.worker_name} "
            f"offset={self.offset_str} drift_ms={drift_ms:.0f} "
            f"fires={self.stats.fires} | {ctx()}"
        )
        # Phase 11 (dead-workers fix). Notify the liveness tracker of
        # this fire so the watchdog can annotate WORKER_NEVER_TICKED
        # alarms with the number of sweet-spots the worker missed.
        # Module-level singleton import is lazy so tests that don't
        # exercise the tracker don't pay the import cost.
        try:
            from src.core.worker_liveness import get_default_tracker
            get_default_tracker().record_sweet_spot(self.worker_name)
        except Exception:  # pragma: no cover — defensive
            pass
        return drift_ms

    def get_stats(self) -> dict:
        """Return cumulative stats for periodic chain-health logging."""
        n = max(self.stats.fires, 1)
        return {
            "worker": self.worker_name,
            "offset": self.offset_str,
            "window_min": self.window_minutes,
            "fires": self.stats.fires,
            "mean_drift_ms": round(self.stats.cumulative_drift_ms / n, 1),
            "max_drift_ms": round(self.stats.max_drift_ms, 1),
            "last_drift_ms": round(self.stats.last_drift_ms, 1),
        }
