"""Execution-geometry alignment (2026-09-16).

The live zero-two prompt told the brain to put TP "at nearest resistance /
support" and "TIGHT TP" on dead coins, but never stated the
[risk].min_rr_ratio gate SLTPValidator enforces before placement, while rule
4 forces SL >= 1.5%. 19 of 36 directives in a week died on rr_below_min
(median rejected R:R 0.74). These tests pin the fix: both prompts state the
requirement from config, no instruction contradicts it, and each candidate
renders a Min TP distance derived from the same number the gate uses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_EXEC_GEOMETRY_VERSION,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    _PROMPT_CALIBRATION_TOKENS,
    _resolve_prompt_calibration,
)
from tests.test_stage2_phase2.test_full_block_renders_seven_subblocks import (
    _eth_pkg,
    _stub_strategist_with_services,
)

BOTH_PROMPTS = (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO)


def _resolve(prompt: str, min_rr: float = 1.5) -> str:
    return _resolve_prompt_calibration(
        prompt, thin_vol_ratio=0.25, heavy_attempts=6, min_rr_ratio=min_rr,
    )


# ── The contradictions that caused the rejections are gone ───────────────

@pytest.mark.parametrize("prompt", BOTH_PROMPTS)
def test_no_nearest_level_tp_instruction(prompt: str) -> None:
    assert "EXACT price at nearest resistance" not in prompt


def test_live_prompt_drops_tight_tp_on_dead_coins() -> None:
    assert "TIGHT TP" not in TRADE_SYSTEM_PROMPT_ZERO_TWO


def test_legacy_prompt_has_no_sl_below_the_floor() -> None:
    # Per-class SL numbers under 1.5% contradicted rule 4's absolute floor.
    for stale in ("0.3-0.5%", "SL at 0.5-1.0%", "1.5% TP, 1.0% SL",
                  "tight TP from VOL data"):
        assert stale not in TRADE_SYSTEM_PROMPT


def test_rule4_floor_preserved_verbatim_in_live_prompt() -> None:
    assert "Absolute minimum 1.5% from entry" in TRADE_SYSTEM_PROMPT_ZERO_TWO
    assert "tighter is rejected" in TRADE_SYSTEM_PROMPT_ZERO_TWO


# ── The requirement is stated, from config ───────────────────────────────

@pytest.mark.parametrize("prompt", BOTH_PROMPTS)
def test_requirement_stated_and_resolved(prompt: str) -> None:
    resolved = _resolve(prompt)
    assert "AT LEAST 1.5x your SL distance" in resolved
    assert "rejected before" in resolved.lower() or "REJECTED before" in resolved
    assert "Min TP distance" in resolved
    for token in _PROMPT_CALIBRATION_TOKENS:
        assert token not in resolved


def test_requirement_follows_configured_ratio() -> None:
    resolved = _resolve(TRADE_SYSTEM_PROMPT_ZERO_TWO, min_rr=2.0)
    assert "AT LEAST 2x your SL distance" in resolved
    assert "1.5x your SL distance" not in resolved


def test_resolver_default_keeps_existing_callers_working() -> None:
    resolved = _resolve_prompt_calibration(
        TRADE_SYSTEM_PROMPT_ZERO_TWO, thin_vol_ratio=0.25, heavy_attempts=6,
    )
    assert "__MIN_RR_RATIO__" not in resolved
    assert "AT LEAST 1.5x" in resolved


def test_json_schema_intact_after_resolution() -> None:
    for prompt in BOTH_PROMPTS:
        assert '{"new_trades":[{"symbol":"SYM"' in _resolve(prompt)


def test_version_sentinel() -> None:
    assert TRADE_SYSTEM_PROMPT_EXEC_GEOMETRY_VERSION == 1


# ── Per-coin Min TP distance line ────────────────────────────────────────

def _with_min_rr(s, min_rr):
    s.settings.risk = SimpleNamespace(min_rr_ratio=min_rr)
    return s


def test_min_tp_line_renders_with_validator_default() -> None:
    s = _stub_strategist_with_services()  # stub has no [risk] -> 1.5
    out = s._format_packages_for_prompt_full(
        {"ETHUSDT": _eth_pkg()}, vol_floors={"ETHUSDT": 2.4},
    )
    assert "Vol stop floor: 2.40%" in out
    assert "Min TP distance: 3.60% (1.5x the stop floor" in out


def test_min_tp_line_uses_configured_ratio() -> None:
    s = _with_min_rr(_stub_strategist_with_services(), 2.0)
    out = s._format_packages_for_prompt_full(
        {"ETHUSDT": _eth_pkg()}, vol_floors={"ETHUSDT": 1.8},
    )
    assert "Min TP distance: 3.60% (2x the stop floor" in out


@pytest.mark.parametrize("floor, expected", [
    (1.8, "2.70"),    # float noise (2.7000000000000002) must not round up
    (1.5, "2.25"),
    (1.83, "2.75"),   # 2.745 rounds UP, never down under the gate
])
def test_min_tp_rounds_up_never_below_gate(floor: float, expected: str) -> None:
    s = _with_min_rr(_stub_strategist_with_services(), 1.5)
    out = s._format_packages_for_prompt_full(
        {"ETHUSDT": _eth_pkg()}, vol_floors={"ETHUSDT": floor},
    )
    assert f"Min TP distance: {expected}%" in out
    assert float(expected) >= floor * 1.5 - 1e-9


def test_min_tp_line_absent_when_gate_disabled() -> None:
    s = _with_min_rr(_stub_strategist_with_services(), 0.0)
    out = s._format_packages_for_prompt_full(
        {"ETHUSDT": _eth_pkg()}, vol_floors={"ETHUSDT": 2.4},
    )
    assert "Vol stop floor: 2.40%" in out
    assert "Min TP distance" not in out


def test_min_tp_line_absent_without_prefetch() -> None:
    s = _stub_strategist_with_services()
    out = s._format_packages_for_prompt_full({"ETHUSDT": _eth_pkg()})
    assert "Min TP distance" not in out


def test_malformed_setting_falls_back_to_validator_default() -> None:
    s = _with_min_rr(_stub_strategist_with_services(), "not-a-number")
    assert s._resolved_min_rr_ratio() == 1.5
