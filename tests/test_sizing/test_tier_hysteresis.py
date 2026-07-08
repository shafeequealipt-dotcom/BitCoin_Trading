"""Sniper-Latency-Size Fix Phase 3C — capital tier hysteresis.

The legacy tier table fired transitions on every equity sample at the
raw 2.0/4.0 boundaries, so a portfolio fluctuating around a boundary
oscillated tiers within minutes and produced wildly different sizing
decisions for the same opportunity.

Hysteresis bands: promotion requires ratio ≥ 2.05 / ≥ 4.10; demotion
requires ratio ≤ 1.95 / ≤ 3.90. Once in a tier the position resists
movement until the buffer is crossed in the opposite direction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.fund_manager.tiered_capital import TieredCapitalManager


def _make_mgr(starting_equity: float = 100.0) -> TieredCapitalManager:
    """Construct a TieredCapitalManager with a stubbed DB; we only
    exercise the pure tier-resolution logic so DB calls never fire."""
    mgr = TieredCapitalManager(db=MagicMock(), starting_equity=starting_equity)
    return mgr


def test_cold_start_picks_tier_from_raw_bands() -> None:
    """First sample (no prior tier) lands directly in the matching
    tier so a fresh deployment doesn't artificially start at Tier 1
    when equity is already in Tier 2/3 territory."""
    # Equity 250 / starting 100 = ratio 2.5 -> Tier 2.
    mgr = _make_mgr()
    tier, pct, max_pos = mgr.get_tier(250.0)
    assert tier == 2
    assert pct == 0.30


def test_tier_2_holds_against_micro_dip_below_boundary() -> None:
    """Once in Tier 2, a small dip toward 2.0 (e.g. 1.96) does NOT
    trigger demotion — it must drop to ≤ 1.95 first."""
    mgr = _make_mgr()
    mgr.get_tier(250.0)  # establish Tier 2 at ratio 2.5
    # Dip to ratio 1.96 — below raw 2.0 but above demote band 1.95.
    tier, _, _ = mgr.get_tier(196.0)
    assert tier == 2, "hysteresis should hold Tier 2 above the demote band"


def test_tier_2_demotes_to_1_below_demote_band() -> None:
    """A drop below 1.95 demotes to Tier 1."""
    mgr = _make_mgr()
    mgr.get_tier(250.0)  # Tier 2
    # Ratio 1.94 — below demote band.
    tier, pct, _ = mgr.get_tier(194.0)
    assert tier == 1
    assert pct == 0.20


def test_tier_1_holds_against_micro_promotion_above_boundary() -> None:
    """At ratio 2.04 (just above raw 2.0 but below promote band
    2.05), Tier 1 holds. The portfolio must climb to ≥ 2.05 before
    promotion."""
    mgr = _make_mgr()
    mgr.get_tier(180.0)  # Tier 1 at ratio 1.8
    tier, _, _ = mgr.get_tier(204.0)
    assert tier == 1, "hysteresis should hold Tier 1 below the promote band"


def test_tier_1_promotes_above_promote_band() -> None:
    """Ratio ≥ 2.05 promotes Tier 1 to Tier 2."""
    mgr = _make_mgr()
    mgr.get_tier(180.0)  # Tier 1
    tier, pct, _ = mgr.get_tier(205.0)
    assert tier == 2
    assert pct == 0.30


def test_tier_3_holds_against_micro_dip() -> None:
    """A drop from Tier 3 to ratio 3.95 does NOT demote — needs ≤ 3.90."""
    mgr = _make_mgr()
    mgr.get_tier(450.0)  # Tier 3 at ratio 4.5
    tier, _, _ = mgr.get_tier(395.0)
    assert tier == 3, "hysteresis should hold Tier 3 above 3.90"


def test_oscillating_around_boundary_is_pinned_to_one_tier() -> None:
    """Simulates a portfolio fluctuating between ratio 1.96 and 2.04
    — the exact boundary-oscillation pattern the fix targets. With
    hysteresis the tier should stay STABLE at whatever it was after
    the first sample."""
    mgr = _make_mgr()
    mgr.get_tier(250.0)  # establish Tier 2

    # Oscillate 196, 204, 196, 204 — all within the buffer band, no
    # transition should fire.
    for eq in [196.0, 204.0, 196.0, 204.0, 198.0, 202.0]:
        tier, _, _ = mgr.get_tier(eq)
        assert tier == 2, (
            f"Tier 2 should hold at equity={eq} (ratio={eq/100:.2f}); "
            f"got tier={tier}"
        )


def test_promote_ratios_constant_values() -> None:
    """Document the hysteresis band values so a future change is
    explicit rather than silent."""
    assert TieredCapitalManager._TIER_PROMOTE_RATIOS == (2.05, 4.10)
    assert TieredCapitalManager._TIER_DEMOTE_RATIOS == (1.95, 3.90)
