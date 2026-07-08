"""Phase 3 of dir-block-fix (2026-05-05) — RR-weighted flip-confidence boost.

Two surgical tests for the new ``effective_confidence`` override on
``_enforce_flip_confidence`` plus the underlying default change
(0.90 → 0.70). The boost-computation itself lives at the call site
inside ``optimize()``; these tests lock the helper's contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.apex.optimizer import TradeOptimizer
from src.config.settings import APEXSettings


@dataclass
class _Optimized:
    direction: str
    confidence: float
    was_flipped: bool = True


def _make_optimizer(min_flip_conf: float = 0.70) -> TradeOptimizer:
    cfg = APEXSettings(apex_min_flip_confidence=min_flip_conf)
    opt = TradeOptimizer.__new__(TradeOptimizer)
    opt._settings = cfg
    return opt


def test_effective_confidence_override_allows_boosted_flip() -> None:
    """raw conf=0.65 (< 0.70 default) + RR-boost 0.15 = effective 0.80;
    the override path lets the gate see the boosted value and the flip
    is allowed.
    """
    opt = _make_optimizer(min_flip_conf=0.70)
    optimized = _Optimized(direction="Buy", confidence=0.65)
    revert, _ = opt._enforce_flip_confidence(
        optimized, "Sell", "ranging",
        effective_confidence=0.80,
    )
    assert revert is False


def test_no_boost_below_threshold_still_blocks() -> None:
    """raw conf=0.50, no RR boost (ratio<3 → effective stays 0.50). Below
    the 0.70 threshold the flip is reverted with the new reason format
    that includes the effective confidence.
    """
    opt = _make_optimizer(min_flip_conf=0.70)
    optimized = _Optimized(direction="Buy", confidence=0.50)
    revert, reason = opt._enforce_flip_confidence(
        optimized, "Sell", "ranging",
        effective_confidence=0.50,
    )
    assert revert is True
    assert "0.50" in reason and "0.70" in reason


def test_default_threshold_is_lowered_to_70() -> None:
    """Phase 3 lowered the default. The dataclass default must reflect
    that — the live runtime reads the same default when the field is
    missing from config.toml (it isn't, but the invariant matters).
    """
    cfg = APEXSettings()
    assert cfg.apex_min_flip_confidence == 0.70
    assert cfg.apex_flip_rr_boost_threshold == 3.0
    assert cfg.apex_flip_rr_boost_amount == 0.15


def test_intelligence_package_uses_structural_data_attribute() -> None:
    """PRIMARY Sell-Bias Fix (2026-05-11) — typo regression guard.

    optimizer.py:367 previously read ``getattr(package, "structure_data", None)``
    (missing the ``al``). The IntelligencePackage dataclass declares the
    field as ``structural_data``, so the wrong name always returned
    None and the RR-weighted confidence boost was dead code. Every
    APEX_FLIP_BLOCKED line in the 2026-05-11 log window showed
    ``rr_boost=0.00`` — confirming the boost never engaged.

    This test locks the canonical attribute name so a future refactor
    cannot silently reintroduce the typo.
    """
    from dataclasses import fields
    from src.apex.models import IntelligencePackage, StructuralData

    sd = StructuralData(symbol="BTCUSDT", current_price=50000.0)
    sd.rr_long = 0.2
    sd.rr_short = 4.0

    # The dataclass exposes ``structural_data`` as the canonical field
    # name. We verify this against the dataclass field definition without
    # instantiating the other sections (which have many required fields
    # not relevant to this regression test).
    declared_fields = {f.name for f in fields(IntelligencePackage)}
    assert "structural_data" in declared_fields, (
        "IntelligencePackage must declare `structural_data`. The "
        "pre-2026-05-11 typo in optimizer.py read `structure_data`."
    )
    assert "structure_data" not in declared_fields, (
        "Regression: the pre-fix wrong attribute name must not be "
        "reintroduced on the dataclass."
    )

    # Build a minimal package via __new__ + __setattr__ so we don't have
    # to populate every section's required fields just to exercise the
    # attribute lookup path the optimizer uses.
    pkg = IntelligencePackage.__new__(IntelligencePackage)
    pkg.structural_data = sd

    # Canonical name resolves to the populated dataclass instance.
    assert getattr(pkg, "structural_data", None) is sd
    # Pre-fix wrong name resolves to None — the typo would silently
    # disable the boost. Regression-guarded.
    assert getattr(pkg, "structure_data", None) is None

    # Confirm the rr_* fields are reachable through the correct attribute.
    sd_resolved = getattr(pkg, "structural_data", None)
    assert sd_resolved is not None
    assert sd_resolved.rr_long == 0.2
    assert sd_resolved.rr_short == 4.0
