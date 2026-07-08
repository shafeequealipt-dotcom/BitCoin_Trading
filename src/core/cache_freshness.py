"""Phase 6 (output-quality) — cross-cycle data freshness measurement.

Lightweight singleton helper that records timestamps when a cache is
written and exposes a read-side delta lookup. Used to surface end-to-end
pipeline timing in workers.log + /health.

Design constraints:
    * Must add < 1 ms per cache read/write (production hot path).
    * Must NOT gate execution — measurement only.
    * Must NOT log every cache read at INFO (sample at cycle boundary
      via the cycle_tracker aggregator).
    * Module-level singleton — every worker writes/reads the same
      instance; tests reset between cases.

Usage (writer side):
    from src.core.cache_freshness import record_write
    record_write("klines", symbol)             # immediate post-cache-write

Usage (reader side):
    from src.core.cache_freshness import read_age_ms
    age_ms = read_age_ms("klines", symbol)     # None if never written
"""

from __future__ import annotations

import time
from threading import RLock

# ─── Singleton state ────────────────────────────────────────────────
# Keys are (cache_name, key) tuples; values are unix timestamps. RLock
# is paranoid — all asyncio callers run on the same event-loop thread —
# but cheap, and protects us if a thread executor ever invokes a hook.
_writes: dict[tuple[str, str], float] = {}
_lock = RLock()


def record_write(cache_name: str, key: str = "") -> None:
    """Record that ``cache_name[key]`` was written at the current monotonic time.

    O(1) dict insertion. Empty ``key`` is allowed (for cache-wide
    timestamps like "klines:batch").
    """
    with _lock:
        _writes[(cache_name, key)] = time.time()


def read_age_ms(cache_name: str, key: str = "") -> float | None:
    """Return milliseconds since the most recent write, or None if never written."""
    with _lock:
        ts = _writes.get((cache_name, key))
    if ts is None:
        return None
    return (time.time() - ts) * 1000.0


def get_snapshot() -> dict[tuple[str, str], float]:
    """Return a shallow copy of all recorded writes (for /health).

    Caller should not mutate; we copy under the lock to avoid iteration
    races with concurrent writers.
    """
    with _lock:
        return dict(_writes)


def reset() -> None:
    """Clear all recorded timestamps. Used by tests between cases."""
    with _lock:
        _writes.clear()
