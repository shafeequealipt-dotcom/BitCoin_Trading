"""Sniper-Latency-Size Fix Phase 1 — type-aware grace gap.

The 30-second blanket cooldown was the only inter-escape gate before
this fix. At the 5-second tick cadence that's six ticks, producing the
five-tick ladder steps observed 2026-05-07 10:57:40-10:59:19 (four
escalations on RENDERUSDT in 99 seconds). The keys
``partial_to_partial_grace_ticks`` (default 60) and
``partial_to_full_grace_ticks`` (default 60) restore the recovery
window: after a partial emission the next partial must wait at least
``partial_to_partial_grace_ticks`` ticks, and a cap-path full close
must wait at least ``partial_to_full_grace_ticks`` ticks. The
forced-full path (``ticks > stall_escape_full_after_ticks``) is the
mature-stall valve and bypasses these gates by design.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config.settings import Mode4Settings, Settings
from src.workers.profit_sniper import ProfitSniper


def _make_sniper(
    max_partials: int = 3,
    p2p_grace: int = 60,
    p2f_grace: int = 60,
) -> ProfitSniper:
    sw = ProfitSniper.__new__(ProfitSniper)
    cfg = Mode4Settings()
    cfg.max_partials_per_position = max_partials
    cfg.stall_escape_partial_after_ticks = 1   # fire fast in tests
    cfg.stall_escape_full_after_ticks = 9999   # avoid forced-full valve
    cfg.stall_escape_cooldown_seconds = 0       # isolate the grace gate
    cfg.stall_tighten_max_applications = 9999
    cfg.partial_to_partial_grace_ticks = p2p_grace
    cfg.partial_to_full_grace_ticks = p2f_grace
    sw.settings = MagicMock()
    sw.settings.mode4 = cfg
    # Production defaults from [layer4.sniper]: at pnl=-0.5% both guards
    # are non-blocking (pnl > 0.0 and pnl > -0.3 are both False).
    sw.settings.layer4_sniper = MagicMock(
        profit_protection_threshold=0.0,
        development_window_lower=-0.3,
    )
    return sw


def test_grace_defaults_in_dataclass() -> None:
    """Mode4Settings carries the grace defaults so a fresh deployment
    without an explicit config edit gets the recalibrated behaviour."""
    cfg = Mode4Settings()
    assert cfg.partial_to_partial_grace_ticks == 60
    assert cfg.partial_to_full_grace_ticks == 60


def test_settings_load_picks_up_grace_keys() -> None:
    """config.toml [mode4] keys flow into Mode4Settings via the generic
    builder. Confirms the live config carries the new fields."""
    s = Settings._load_fresh(config_path="config.toml")
    assert s.mode4.partial_to_partial_grace_ticks == 60
    assert s.mode4.partial_to_full_grace_ticks == 60


def test_partial_to_partial_blocked_within_grace() -> None:
    """After a partial fires the next partial cannot fire until
    ``partial_to_partial_grace_ticks`` ticks have elapsed. Re-creates
    the RENDERUSDT 10:57:40 → 10:58:11 (5-tick gap) symptom and verifies
    the new gate blocks it."""
    sw = _make_sniper(max_partials=3, p2p_grace=60)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    # First emission fires at tick=2 (partial_after=1).
    first = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
    assert first is None  # tick=1 still inside quiet window
    second = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
    assert second == "partial_close", f"expected partial_close, got {second!r}"
    assert tracked["_last_escape_type"] == "partial"
    assert tracked["_last_escape_tick"] == 2

    # The next 59 calls should all be blocked by the grace gate
    # (ticks_since=1..59 < grace_required=60). _partials_emitted stays
    # at 1 throughout.
    for _ in range(59):
        result = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
        assert result is None, (
            f"expected None within grace gap, got {result!r}; "
            f"ticks={tracked['_stall_ticks']}"
        )
    assert tracked["_partials_emitted"] == 1
    assert tracked["_stall_ticks"] == 61

    # Tick=2 + 60 = 62 satisfies grace_required=60 (ticks_since=60 is
    # NOT < 60) so the next call fires the second partial.
    third = sw._stall_escape_action("ETHUSDT", tracked, True, "hold")
    assert tracked["_stall_ticks"] == 62
    assert third == "partial_close", (
        f"expected partial_close after grace, got {third!r}; "
        f"ticks_since_last={tracked['_stall_ticks'] - tracked['_last_escape_tick']}"
    )


def test_cap_path_full_close_blocked_within_p2f_grace() -> None:
    """When the partial cap is reached and the next emission would be
    a cap-path full_close, the ``partial_to_full_grace_ticks`` gate
    must block it until the gap elapses. Otherwise a position emits 3
    partials in 18 ticks and is force-closed at tick 24 — the 99-second
    kill the operator observed."""
    sw = _make_sniper(max_partials=1, p2p_grace=60, p2f_grace=60)
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    # First call quiet, second call emits the (only) partial, exhausts
    # the cap.
    sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
    second = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
    assert second == "partial_close"
    assert tracked["_partials_emitted"] == 1
    assert tracked["_last_escape_type"] == "partial"

    # The cap-path full_close must wait p2f_grace=60 ticks before firing.
    for _ in range(59):
        assert sw._stall_escape_action("BTCUSDT", tracked, True, "hold") is None
    # tick=2 + 60 = 62 → grace satisfied (ticks_since=60 is NOT < 60);
    # cap-path now allowed to fire.
    final = sw._stall_escape_action("BTCUSDT", tracked, True, "hold")
    assert final == "full_close", (
        f"cap-path full_close should fire once grace elapsed, got {final!r}"
    )
    assert tracked["_last_escape_type"] == "full"


def test_mature_stall_bypasses_grace() -> None:
    """The forced-full path (``ticks > stall_escape_full_after_ticks``)
    is the mature-stall valve and predates the grace gate; it must
    still fire even if a partial just emitted (otherwise positions can
    sit dead with no protection at all once they drift past the full
    threshold)."""
    sw = _make_sniper(max_partials=3, p2p_grace=60, p2f_grace=60)
    sw.settings.mode4.stall_escape_full_after_ticks = 5  # hit the valve fast
    tracked = {"_partials_emitted": 0, "last_score": {"pnl_pct": -0.5}}

    # Emit one partial first so _last_escape_type='partial' is set.
    sw._stall_escape_action("XRPUSDT", tracked, True, "hold")  # tick=1 quiet
    sw._stall_escape_action("XRPUSDT", tracked, True, "hold")  # tick=2 emits
    assert tracked["_last_escape_type"] == "partial"

    # Now drive ticks past stall_escape_full_after_ticks. The forced-full
    # path should fire even though grace hasn't elapsed.
    for _ in range(4):
        sw._stall_escape_action("XRPUSDT", tracked, True, "hold")
    # tick=2..5 inside grace, ticks > 5 (=full_after) triggers forced
    # full close on the very next call.
    forced = sw._stall_escape_action("XRPUSDT", tracked, True, "hold")
    assert forced == "full_close", (
        f"forced-full path should bypass grace gate; got {forced!r} "
        f"at ticks={tracked['_stall_ticks']}"
    )
    assert tracked["_last_escape_type"] == "full"
