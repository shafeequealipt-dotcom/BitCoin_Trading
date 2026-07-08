"""Phase 8 of the 1D briefing rewrite — A/B alternation determinism.

Single question per test: "Given two consecutive cycle_ids, does the
alternation produce different modes? Given the same cycle_id called
twice, is the result idempotent?"

Determinism is critical: a flaky alternation would corrupt the A/B
samples by attributing some cycles to the wrong mode.
"""

import pytest

from src.config.settings import ScannerSettings
from src.workers.scanner_worker import ScannerWorker


def test_ab_mode_default_off() -> None:
    """A/B harness is off by default — operator opts in via config."""
    s = ScannerSettings()
    assert s.ab_mode == "off"


def test_ab_mode_invalid_value_rejected() -> None:
    with pytest.raises(ValueError, match="scanner.ab_mode must be one of"):
        ScannerSettings(ab_mode="banana")


def test_alternation_5min_slots_deterministic() -> None:
    """Even slot → exclusion, odd slot → briefing; idempotent."""
    f = ScannerWorker._derive_ab_mode_from_cycle_id
    assert f("c-2026-05-01-00:00") == "exclusion"   # slot 0
    assert f("c-2026-05-01-00:05") == "briefing"    # slot 1
    assert f("c-2026-05-01-00:10") == "exclusion"   # slot 2
    assert f("c-2026-05-01-00:15") == "briefing"    # slot 3
    assert f("c-2026-05-01-00:20") == "exclusion"   # slot 4
    assert f("c-2026-05-01-00:25") == "briefing"    # slot 5
    # Idempotent — same input → same output.
    assert f("c-2026-05-01-00:00") == "exclusion"


def test_alternation_persists_across_hour_boundaries() -> None:
    """The slot index resets every hour but the parity is consistent."""
    f = ScannerWorker._derive_ab_mode_from_cycle_id
    # 00:55 = slot 11 → odd → briefing
    assert f("c-2026-05-01-00:55") == "briefing"
    # 01:00 = slot 0 → even → exclusion
    assert f("c-2026-05-01-01:00") == "exclusion"


def test_unparsable_cycle_id_defaults_safely() -> None:
    """Garbage cycle_id falls back to exclusion (safe path)."""
    f = ScannerWorker._derive_ab_mode_from_cycle_id
    assert f("garbage") == "exclusion"
    assert f("") == "exclusion"
    assert f("c-not-a-cycle") == "exclusion"


def test_consecutive_cycles_alternate() -> None:
    """Adjacent cycles always produce different modes — the contract
    that makes A/B comparison work."""
    f = ScannerWorker._derive_ab_mode_from_cycle_id
    pairs = [
        ("c-2026-05-01-00:00", "c-2026-05-01-00:05"),
        ("c-2026-05-01-12:30", "c-2026-05-01-12:35"),
        ("c-2026-05-01-23:50", "c-2026-05-01-23:55"),
    ]
    for a, b in pairs:
        assert f(a) != f(b), f"adjacent {a} / {b} share mode"
