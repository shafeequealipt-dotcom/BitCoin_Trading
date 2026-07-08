"""Phase 2 of dir-block-fix (2026-05-05) — trail-tightening recalibration.

Smoke tests covering the recalibrated SL trail-tightening surface:

* SL_GATEWAY R3 (max_step_pct) is enforced (not log-only) and lowered
  to 0.25 % so each tighten moves only a quarter of the remaining
  distance.
* Mode4 tighten cooldown lowered to 15 s so M4 can react inside the
  anti-greed pullback window.
* Mode4 trail-activation gate raised to +0.50 % peak PnL so winners
  breathe before lock-in begins.
* APEX gate.py Check 9 in-code fallback aligned with the dataclass
  default (Discovery 2) — 15.0 % of TP distance, not the prior 50.0 %.

These are config/default invariants. The behavior they enforce is
exercised live by the running system; these tests just lock the
defaults in place so a future config-touch can't silently revert
them.
"""

from __future__ import annotations

from src.config.settings import (
    APEXSettings,
    Mode4Settings,
    SLGatewaySettings,
)


def test_sl_gateway_defaults_enforce_quarter_step() -> None:
    cfg = SLGatewaySettings()
    assert cfg.max_step_pct == 0.25, (
        "SL_GATEWAY R3 max_step_pct must be 0.25 % — Phase 2 of "
        "dir-block-fix lowered it from 0.5 to slow trail tightening."
    )
    # Hard enforcement (not audit) is the post-fix posture.
    assert cfg.log_only_global is False
    assert cfg.log_only_max_step is False
    # Tighten-only safety must always remain enforced.
    assert cfg.log_only_tighten_only is False


def test_mode4_cooldown_and_activation() -> None:
    cfg = Mode4Settings()
    assert cfg.tighten_cooldown_seconds == 15, (
        "M4 tighten_cooldown_seconds must be 15 — Phase 2 of "
        "dir-block-fix lowered it from 30 so M4 can react inside the "
        "anti-greed pullback window."
    )
    assert cfg.min_profit_for_trail_pct == 0.50, (
        "M4 min_profit_for_trail_pct must be 0.50 % — Phase 2 of "
        "dir-block-fix raised it from 0.30 so winners breathe before "
        "trail activates."
    )


def test_apex_trail_floor_default_aligned() -> None:
    cfg = APEXSettings()
    assert cfg.gate_trail_activation_floor_pct_of_tp == 15.0, (
        "APEX gate_trail_activation_floor_pct_of_tp default must be "
        "15.0 — Discovery 2 of Phase 2 aligned the dataclass with the "
        "in-code fallback in apex/gate.py:241."
    )
