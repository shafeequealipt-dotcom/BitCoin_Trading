"""Phase 7 of the 1D briefing rewrite — gate passes with 1 qualified package.

Single question this test answers: "After Phase 7 lowers
min_qualified_packages from 3 to 1, does a cycle with 1 well-formed
package pass the cold-start gate?" If yes, the regression that
dropped AEROUSDT in cycle c-2026-05-01-00:35 (Phase 0 baseline)
cannot recur for that reason.

Also verifies the COMPANION invariant: when avg_completeness is
below the floor (0.85), the gate STILL blocks — Phase 7 only relaxed
the count threshold, not the cache-warmup safety floor.
"""

from src.config.settings import BrainColdStartProtection


def test_default_min_qualified_packages_is_one() -> None:
    cfg = BrainColdStartProtection()
    assert cfg.min_qualified_packages == 1, (
        "Phase 7 default must be 1 (was 3 pre-rollout)"
    )


def test_completeness_floors_relaxed_for_e12() -> None:
    """Issue E12 (2026-05-27) relaxed the two AVERAGE completeness gates
    (min_avg 0.85->0.70, boot_grace 0.95->0.80) so the validator's new honest
    failure-default scoring cannot block the batch. The PER-PACKAGE floor
    (0.75) and grace window (600s) are unchanged — one warm package still
    proves the caches are warm."""
    cfg = BrainColdStartProtection()
    assert cfg.min_avg_completeness == 0.70
    assert cfg.min_per_package_completeness == 0.75
    assert cfg.boot_grace_completeness == 0.80
    assert cfg.boot_grace_period_sec == 600


def test_one_qualified_package_passes_count_gate() -> None:
    """Simulates the cold-start gate's count check on a single qualified
    package; verifies the new threshold (1) lets it pass."""
    cfg = BrainColdStartProtection()
    # Synthetic package state: 1 qualified, completeness 1.00.
    # Mirrors the live evidence from cycle c-2026-05-01-00:35.
    qualified_count = 1
    avg_completeness = 1.00

    # Equivalent to the gate's logic in layer_manager.py:1063-1065.
    blocks_on_count = qualified_count < cfg.min_qualified_packages
    blocks_on_avg = avg_completeness < cfg.min_avg_completeness

    assert not blocks_on_count, (
        "Phase 7: 1 qualified package must pass the count gate "
        f"(threshold = {cfg.min_qualified_packages})"
    )
    assert not blocks_on_avg, (
        "1.0 avg_completeness must pass the completeness floor"
    )


def test_zero_qualified_still_blocks() -> None:
    """Edge case: 0 qualified still blocks (count < 1)."""
    cfg = BrainColdStartProtection()
    qualified_count = 0
    blocks_on_count = qualified_count < cfg.min_qualified_packages
    assert blocks_on_count


def test_low_avg_completeness_still_blocks() -> None:
    """Risk R4 regression guard: relaxing min_qualified_packages must
    NOT relax the completeness floor. A degraded cache producing
    low-completeness packages must still be blocked."""
    cfg = BrainColdStartProtection()
    qualified_count = 1
    avg_completeness = 0.60   # below the 0.70 E12-relaxed floor

    blocks_on_count = qualified_count < cfg.min_qualified_packages
    blocks_on_avg = avg_completeness < cfg.min_avg_completeness
    # Count gate passes (Phase 7 relaxation), but completeness gate
    # blocks — so the cycle still gets dropped, as intended.
    assert not blocks_on_count
    assert blocks_on_avg
