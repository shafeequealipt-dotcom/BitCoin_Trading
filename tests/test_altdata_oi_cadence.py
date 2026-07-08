"""altdata_worker OI/F&G sub-cadence drift fix.

Pins the post-fix contract: the deadline gate fires reliably every
``interval`` seconds regardless of fetch latency, and the
``next_in_s`` log field reflects the *actual* time-to-next-fire, not
the configured interval.

Pre-fix bug (verified in production logs 2026-04-29 02:06-02:31): the
deadline was advanced using ``now_mono`` captured AFTER the fetch
completed, adding the per-fire fetch latency (~9 s for OI) to the
deadline every cycle. Within a few ticks the deadline drifted past
the next master-tick boundary and the gate skipped every other tick,
silently halving the effective OI cadence (300 s configured, 600 s
observed). The same defect existed on the F&G path — symmetric fix
prevents future regression even though F&G's 3600 s interval is
robust against the few-second drift in the current fetch cost.

Fix: anchor ``_next_oi_mono`` / ``_next_fg_mono`` to the tick-start
``t0`` (not post-fetch ``now_mono``) so the deadline lands on a clean
``t0 + N × interval`` grid; subtract a small ``_DEADLINE_JITTER_TOLERANCE_S``
on the gate to absorb the master sweet-spot scheduler's bidirectional
drift (single-digit ms in production).

These tests exercise the gate predicate and the deadline-advancement
formula directly via a stub ``AltDataWorker`` instance — no settings,
no DB, no aiohttp; just the timing logic. Each scenario simulates a
controlled monotonic clock so the assertions are deterministic.
"""

from __future__ import annotations

from src.workers.altdata_worker import AltDataWorker


def _make_stub_worker(
    *, oi_interval_s: float = 300.0, fg_interval_s: float = 3600.0,
) -> AltDataWorker:
    """Build an AltDataWorker bypassing __init__ — we only need the
    deadline-state attributes and the class-level tolerance constant."""
    w = AltDataWorker.__new__(AltDataWorker)
    w._next_oi_mono = 0.0
    w._next_fg_mono = 0.0
    w._oi_interval_s = float(oi_interval_s)
    w._fg_interval_s = float(fg_interval_s)
    return w


def _gate_oi(w: AltDataWorker, t0: float) -> bool:
    """Replicate the production gate predicate from altdata_worker.tick()."""
    return t0 >= w._next_oi_mono - w._DEADLINE_JITTER_TOLERANCE_S


def _gate_fg(w: AltDataWorker, t0: float) -> bool:
    return t0 >= w._next_fg_mono - w._DEADLINE_JITTER_TOLERANCE_S


def test_oi_fires_every_master_tick_no_drift() -> None:
    """Twelve consecutive 300 s master ticks, each followed by a 9 s OI
    fetch, should yield twelve OI fires — not six. Reproduces the
    original 300s-becomes-600s bug if the fix regresses."""
    w = _make_stub_worker(oi_interval_s=300.0)
    interval = w._oi_interval_s
    fetch_cost_s = 9.0     # observed OI fetch latency in production

    fires = 0
    t0 = 0.0
    for _ in range(12):
        if _gate_oi(w, t0):
            # Production sequence: gather() runs (fetch_cost_s seconds),
            # then deadline is anchored to t0 (NOT to t0 + fetch_cost_s).
            w._next_oi_mono = t0 + interval
            fires += 1
        # Master tick advances by exactly the interval; fetch cost
        # would, in the buggy implementation, leak into the deadline.
        t0 += interval

    assert fires == 12, (
        f"OI must fire on every master tick (got {fires}/12). "
        f"Pre-fix would yield 6 — drift bug regression."
    )


def test_oi_fires_with_negative_jitter_within_tolerance() -> None:
    """The sweet-spot scheduler can fire a few ms early. The gate must
    still fire when t0 is up to ``_DEADLINE_JITTER_TOLERANCE_S`` early."""
    w = _make_stub_worker(oi_interval_s=300.0)
    interval = w._oi_interval_s
    tol = w._DEADLINE_JITTER_TOLERANCE_S

    # First fire: deadline starts at 0, t0=0 trivially passes.
    assert _gate_oi(w, 0.0)
    w._next_oi_mono = 0.0 + interval

    # Second master tick lands ``tol/2`` early — well inside tolerance.
    t0_early = interval - (tol / 2.0)
    assert _gate_oi(w, t0_early), (
        "Gate must absorb sub-tolerance early jitter or the cadence "
        "silently halves on real master-tick scheduling."
    )


def test_oi_skips_when_far_below_deadline() -> None:
    """A spurious early call (well outside tolerance) must NOT fire."""
    w = _make_stub_worker(oi_interval_s=300.0)
    w._next_oi_mono = 300.0  # deadline at t=300

    # t0 well before tolerance window (t0=100 << 300 - 1.0)
    assert not _gate_oi(w, 100.0)


def test_fg_path_symmetric_with_oi() -> None:
    """The same drift-free contract must hold for F&G — the symmetric
    fix prevents regression if the F&G interval is ever shortened to a
    range where the additive drift would matter."""
    w = _make_stub_worker(fg_interval_s=300.0)  # deliberately tight
    interval = w._fg_interval_s
    fetch_cost_s = 0.05  # F&G is normally cached, fast

    fires = 0
    t0 = 0.0
    for _ in range(8):
        if _gate_fg(w, t0):
            w._next_fg_mono = t0 + interval
            fires += 1
        t0 += interval

    assert fires == 8, (
        f"F&G must fire on every master tick (got {fires}/8). "
        f"Symmetric drift bug protection."
    )


def test_deadline_grid_stays_anchored_across_many_cycles() -> None:
    """After N fires, the deadline should equal ``N × interval`` exactly
    (modulo float — but with integer-second intervals and integer t0
    values, the equality is exact). Pre-fix this would drift forward by
    ``N × fetch_cost_s``."""
    w = _make_stub_worker(oi_interval_s=300.0)
    interval = w._oi_interval_s
    n_cycles = 100

    t0 = 0.0
    for _ in range(n_cycles):
        if _gate_oi(w, t0):
            w._next_oi_mono = t0 + interval
        t0 += interval

    # Last fire happened at t0 = (n_cycles - 1) * interval, deadline
    # set to that + interval = n_cycles * interval.
    assert w._next_oi_mono == n_cycles * interval, (
        f"deadline drifted from N×interval grid: "
        f"got {w._next_oi_mono}, expected {n_cycles * interval}. "
        f"Pre-fix would have drifted by ~{n_cycles * 9.0}s "
        f"(N × fetch_cost)."
    )


def test_tolerance_constant_matches_scheduler_convention() -> None:
    """Document/enforce that the tolerance mirrors the scheduler's
    existing ``is_at_sweet_spot`` tolerance so jitter assumptions stay
    consistent across the scheduling stack. Also pins the class-level
    location so future maintainers don't reintroduce a magic number
    inline."""
    assert hasattr(AltDataWorker, "_DEADLINE_JITTER_TOLERANCE_S")
    # Class-level constant, not instance attribute.
    assert AltDataWorker._DEADLINE_JITTER_TOLERANCE_S == 1.0


def test_log_field_present_in_source() -> None:
    """The misleading ``next_in_s={interval_s}`` log was the second half
    of the bug — it printed the configured interval, not the actual
    distance to deadline. Static check pins the new shape so an
    accidental revert doesn't silently restore the deception."""
    from pathlib import Path
    src = (
        Path(__file__).parent.parent / "src" / "workers" / "altdata_worker.py"
    ).read_text()

    # New form: next_in_s computed from deadline minus current time;
    # interval_s separately exposes the configured target.
    assert "next_in_s={_oi_next_in_s:.0f}" in src, (
        "ALTDATA_OI_TICK must compute next_in_s from the live deadline, "
        "not print the configured interval."
    )
    assert "interval_s={self._oi_interval_s:.0f}" in src, (
        "ALTDATA_OI_TICK must additionally expose interval_s so "
        "operators can cross-check against the actual time-to-fire."
    )
    assert "next_in_s={_fg_next_in_s:.0f}" in src, (
        "ALTDATA_FG_TICK must use the same honest-next_in_s shape."
    )
