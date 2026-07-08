"""Five-Fix Follow-Up — Fix 5 (2026-06-10): APEX size-override kill-switch.

With apex_size_override_enabled = False (the default, operator decision) the
brain's parsed size_usd flows through the optimizer UNMODIFIED — the J5
dynamic-sizing adoption of the optimizer-proposed size is inert (no raise, no
conviction shrink) — and the gate's A+ ceiling boost is switched off on the
legacy CHECK-4 path. Safety stays: the optimizer leverage clamp and the gate
ceilings are untouched. With the switch True, the pre-fix behaviour applies.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config.settings import APEXSettings


def _opt(override: bool, brain_auth: bool = True):
    from src.apex.optimizer import TradeOptimizer
    s = APEXSettings()
    s.apex_size_override_enabled = override
    s.brain_authoritative_sizing_enabled = brain_auth
    s.max_position_size_usd = 4000.0
    return TradeOptimizer(None, None, s)


def _trade(**kw):
    from src.apex.optimizer import OptimizedTrade
    base = dict(symbol="HYPEUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
                tp_mode="fixed", position_size_usd=1200.0, leverage=2,
                entry_timing="immediate", add_on_pullback=False,
                reasoning="t", confidence=1.0, original_size=700.0)
    base.update(kw)
    return OptimizedTrade(**base)


def test_switch_off_brain_size_stands_against_inflation():
    """The proven live case: brain $700, optimizer proposal $1200 (would have
    executed $1050 through the 1.5x cap). Switch off -> exactly $700."""
    opt = _opt(override=False)
    t = _trade(position_size_usd=1200.0, original_size=700.0, confidence=1.0)
    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(700.0)
    assert t.reasoning.startswith("[SIZE OVERRIDE DISABLED by switch]")


def test_switch_off_no_conviction_shrink_either():
    """Unmodified means unmodified: a low-confidence trade is NOT shrunk
    below the brain's deliberate size when the switch is off."""
    opt = _opt(override=False, brain_auth=False)
    t = _trade(position_size_usd=700.0, original_size=700.0, confidence=0.1)
    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(700.0)


def test_switch_off_unchanged_proposal_not_tagged():
    """When the optimizer proposal already equals the brain's size, nothing
    changed — the reasoning is not tagged."""
    opt = _opt(override=False)
    t = _trade(position_size_usd=700.0, original_size=700.0)
    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(700.0)
    assert not t.reasoning.startswith("[SIZE OVERRIDE")


def test_switch_off_leverage_safety_clamp_still_fires():
    """Rule 6: disabling the multiplier disables NO safety check — the
    optimizer leverage clamp still binds with the switch off."""
    opt = _opt(override=False)
    max_lev = int(opt._settings.max_leverage)
    t = _trade(leverage=max_lev + 7)
    opt._apply_constraints(t)
    assert t.leverage == max_lev


def test_switch_on_replays_legacy_j5_behavior():
    """Switch on = pre-fix behaviour: the proposal is capped and
    conviction-scaled, then floored at the brain's size (brain-auth on), so
    the HYPE shape inflates above the brain's $700 exactly as before."""
    opt = _opt(override=True, brain_auth=True)
    t = _trade(position_size_usd=1200.0, original_size=700.0, confidence=1.0)
    opt._apply_constraints(t)
    # legacy: min(1200, 4000) * max(0.5, 1.0) = 1200, floored at 700 -> 1200
    assert t.position_size_usd == pytest.approx(1200.0)


def test_switch_off_defensive_fallthrough_without_brain_size():
    """A trade with no recorded brain size (original_size 0 — defensive)
    falls through to the legacy block rather than zeroing the trade."""
    opt = _opt(override=False, brain_auth=False)
    t = _trade(position_size_usd=1200.0, original_size=0.0, confidence=1.0)
    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(1200.0)  # legacy: min(1200,4000)*1.0


def _gate(override: bool, available: float = 10000.0):
    from src.apex.gate import TradeGate
    s = APEXSettings()
    s.apex_size_override_enabled = override
    s.brain_authoritative_sizing_enabled = False  # legacy CHECK-4 weight path
    s.conviction_enabled = True
    s.max_position_size_usd = 4000.0
    s.gate_a_plus_conf_floor = 0.0
    services = {"fund_manager": SimpleNamespace(
        _account_state=SimpleNamespace(available=available))}
    g = TradeGate(services, s)
    return g


def _gtrade(size: float):
    return {"symbol": "HYPEUSDT", "direction": "Buy", "size_usd": size,
            "leverage": 2, "_xray_confidence": 0.7, "_setup_score": 85.0,
            "_expected_rr": 2.0, "_claude_original_size_usd": size,
            "original_size": size, "entry_price": 100.0}


@pytest.mark.asyncio
async def test_gate_a_plus_boost_switched_off(monkeypatch):
    """Switch off: the A+ boost does not multiply the conviction weight; the
    modification trail records A_PLUS_BOOST_SWITCHED_OFF instead."""
    g = _gate(override=False)

    async def _w(symbol):
        return 1.0
    monkeypatch.setattr(g, "_get_conviction_weight", _w)
    t = await g.validate(_gtrade(900.0))
    mods = str(t.get("_gate_adjustments", ""))
    assert "A_PLUS_BOOST_SWITCHED_OFF" in mods
    assert "A_PLUS_BOOST_WITHHELD" not in mods


@pytest.mark.asyncio
async def test_gate_a_plus_boost_applies_when_switch_on(monkeypatch):
    """Switch on: pre-fix behaviour — the boost fires on a confident A+
    setup (no switched-off marker in the trail)."""
    g = _gate(override=True)

    async def _w(symbol):
        return 1.0
    monkeypatch.setattr(g, "_get_conviction_weight", _w)
    t = await g.validate(_gtrade(900.0))
    mods = str(t.get("_gate_adjustments", ""))
    assert "A_PLUS_BOOST_SWITCHED_OFF" not in mods
