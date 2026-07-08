"""Definitive-fix Phase 6 — brain cold-start completeness gate.

Smoke-level coverage of ``LayerManager._cold_start_block_or_none`` for
the four cases the gate covers: empty packages, low avg completeness,
boot-grace strict gate, and the steady-state happy path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from unittest.mock import MagicMock

from src.config.settings import BrainColdStartProtection
from src.core.layer_manager import LayerManager


@dataclass
class _Pkg:
    completeness: float = 1.0
    # Q3d (2026-04-29) — gate now counts ``pkg.qualified`` OR
    # ``open_position is not None`` toward ``min_qualified_packages``.
    # Default ``qualified=True`` matches the original test intent: the
    # original tests treated every _Pkg as a real qualifier (their
    # completeness alone made them count). New fields give the gate
    # something to read; tests that need a ref-pair-style force-include
    # can construct ``_Pkg(0.94, qualified=False)`` explicitly.
    qualified: bool = True
    open_position: dict | None = None


def _build_lm(boot_offset_sec: float = 1000.0, packages: dict | None = None,
              cfg: BrainColdStartProtection | None = None) -> LayerManager:
    """Build a minimum LayerManager wired for the gate alone."""
    lm = LayerManager.__new__(LayerManager)
    lm.services = {}
    lm._coin_packages = packages or {}
    lm._boot_time = time.time() - boot_offset_sec
    lm.settings = MagicMock()
    lm.settings.brain.cold_start_protection = cfg or BrainColdStartProtection()
    return lm


def _plan(n_trades: int):
    p = MagicMock()
    p.new_trades = list(range(n_trades))
    return p


def test_phase6_empty_packages_blocks() -> None:
    """No packages → BRAIN_NO_PACKAGES."""
    lm = _build_lm(packages={})
    msg = lm._cold_start_block_or_none(_plan(2))
    assert msg is not None
    assert "BRAIN_NO_PACKAGES" in msg


def test_phase6_boot_grace_strict_gate_blocks_below_grace_threshold() -> None:
    """During boot grace, avg=0.75 < boot_grace=0.80 → block. Issue E12 relaxed
    the grace threshold from 0.95 to 0.80; 0.75 still blocks during grace yet
    would PASS the 0.70 steady gate, so this still demonstrates that boot grace
    is the stricter gate."""
    lm = _build_lm(
        boot_offset_sec=60.0,  # 1 min after boot — well inside grace
        packages={"BTCUSDT": _Pkg(0.75), "ETHUSDT": _Pkg(0.75)},
    )
    msg = lm._cold_start_block_or_none(_plan(1))
    assert msg is not None
    assert "BRAIN_COLD_START_BLOCK" in msg


def test_phase6_steady_state_happy_path() -> None:
    """Past grace, avg=0.90 ≥ 0.70 (E12-relaxed steady) with 4 qualified ≥ 1 → no block."""
    lm = _build_lm(
        boot_offset_sec=10_000.0,  # well past grace
        packages={
            "BTCUSDT": _Pkg(0.90), "ETHUSDT": _Pkg(0.90),
            "SOLUSDT": _Pkg(0.90), "BNBUSDT": _Pkg(0.90),
        },
    )
    assert lm._cold_start_block_or_none(_plan(2)) is None


def test_phase6_disabled_never_blocks() -> None:
    """When the cfg.enabled flag is False the gate is bypassed."""
    cfg = BrainColdStartProtection(enabled=False)
    lm = _build_lm(packages={}, cfg=cfg)
    assert lm._cold_start_block_or_none(_plan(2)) is None
