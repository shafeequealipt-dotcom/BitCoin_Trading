"""Brain-Authoritative Fund Management (2026-05-31).

Verifies the downstream now HONORS the brain's size_usd (APEX floor + gate CHECK 4
hard ceiling), the enforcer switch, the config flags, the prompt sizing instruction,
and the trim-whitelist for the new fund-context lines. Flag-OFF stays legacy.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.settings import APEXSettings, EnforcerSettings, Settings


# ── optimizer: APEX floors final size at the brain's size under the flag ──
def _opt(brain_auth: bool):
    from src.apex.optimizer import TradeOptimizer
    s = APEXSettings()
    s.brain_authoritative_sizing_enabled = brain_auth
    s.max_position_size_usd = 4000.0
    # Five-Fix Follow-Up Fix 5 (2026-06-10): these tests exercise the J5
    # dynamic-sizing mechanics (cap + conviction scale + brain-auth floor),
    # which now live behind the size-override switch (default OFF = the
    # brain's size flows unmodified; covered by
    # tests/test_fix5_size_override_switch.py). Switch ON here to keep
    # testing the legacy path this file was written for.
    s.apex_size_override_enabled = True
    return TradeOptimizer(None, None, s)

def _trade(**kw):
    from src.apex.optimizer import OptimizedTrade
    base = dict(symbol="BTCUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
                tp_mode="fixed", position_size_usd=3000.0, leverage=3,
                entry_timing="immediate", add_on_pullback=False,
                reasoning="t", confidence=0.5, original_size=3000.0)
    base.update(kw)
    return OptimizedTrade(**base)

def test_optimizer_floors_at_brain_size_when_flag_on():
    opt = _opt(True)
    t = _trade(position_size_usd=3000.0, confidence=0.5, original_size=3000.0)
    opt._apply_constraints(t)
    assert t.position_size_usd >= 3000.0  # not shrunk below the brain's size

def test_optimizer_shrinks_when_flag_off_legacy():
    opt = _opt(False)
    t = _trade(position_size_usd=3000.0, confidence=0.5, original_size=3000.0)
    opt._apply_constraints(t)
    # legacy: min(3000, cap=4000) * conviction_scale(max(0.5,0.5)) = 1500
    assert t.position_size_usd < 3000.0


# ── gate CHECK 4: hard available-capital ceiling under the flag ──
def _gate(brain_auth: bool, available: float):
    from src.apex.gate import TradeGate
    s = APEXSettings()
    s.brain_authoritative_sizing_enabled = brain_auth
    s.brain_auth_per_trade_pct_of_available = 0.40
    s.max_position_size_usd = 4000.0
    services = {"fund_manager": SimpleNamespace(
        _account_state=SimpleNamespace(available=available))}
    return TradeGate(services, s)

def _gtrade(size):
    return {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": size,
            "leverage": 3, "_xray_confidence": 0.7, "_setup_score": 80.0,
            "_expected_rr": 3.0, "_claude_original_size_usd": size,
            "original_size": size, "entry_price": 100.0}

@pytest.mark.asyncio
async def test_gate_honors_brain_size_when_available_sufficient():
    g = _gate(True, available=7636.0)  # ceiling 7636*0.40=3054
    t = await g.validate(_gtrade(3000.0))
    assert t.get("size_usd") == pytest.approx(3000.0)  # passes untouched

@pytest.mark.asyncio
async def test_gate_clamps_when_available_low():
    g = _gate(True, available=2000.0)  # ceiling 2000*0.40=800
    t = await g.validate(_gtrade(3000.0))
    assert t.get("size_usd") == pytest.approx(800.0)  # hard rail still binds

@pytest.mark.asyncio
async def test_gate_legacy_conviction_shrink_when_flag_off():
    # Low available so the legacy conviction cap (available * <=0.50)
    # deterministically shrinks a $3000 trade regardless of conviction weight.
    g = _gate(False, available=500.0)
    t = await g.validate(_gtrade(3000.0))
    assert t.get("size_usd") < 3000.0  # legacy conviction-capital cap binds


# ── enforcer switch ──
def test_enforcer_disabled_returns_unity():
    ec = EnforcerSettings()
    ec.size_reduction_enabled = False
    enf = __import__("src.strategies.performance_enforcer", fromlist=["PerformanceEnforcer"]).PerformanceEnforcer(
        SimpleNamespace(enforcer=ec), MagicMock(), {})
    enf._profit_today_pct = -5.0  # would be x0.50 if enabled
    assert enf.get_size_multiplier() == 1.0

def test_enforcer_enabled_still_throttles():
    ec = EnforcerSettings()
    ec.size_reduction_enabled = True
    enf = __import__("src.strategies.performance_enforcer", fromlist=["PerformanceEnforcer"]).PerformanceEnforcer(
        SimpleNamespace(enforcer=ec), MagicMock(), {})
    enf._profit_today_pct = -5.0
    assert enf.get_size_multiplier() < 1.0


# ── config flags load ──
def test_config_flags_load():
    s = Settings.load()
    assert s.apex.brain_authoritative_sizing_enabled is True
    assert s.apex.brain_auth_per_trade_pct_of_available == pytest.approx(0.40)
    assert s.apex.max_position_size_usd >= 3000
    assert s.enforcer.size_reduction_enabled is False


# ── system-prompt proper-funding instruction (both prompts) ──
def test_system_prompts_have_proper_funding_instruction():
    from src.brain.strategist import TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO
    for p in (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO):
        assert "Available for new trades" in p
        assert "PROPER FUNDING" in p
        assert "probe" in p.lower()


# ── trim whitelist: the new fund lines survive token-trim ──
@pytest.mark.parametrize("line", [
    "Open trades: 3", "Used funds: $1,200", "Usable funds: $9,300",
    "Available for new trades: $8,100",
])
def test_fund_lines_are_trim_essential(line):
    from src.brain.strategist import _infer_section_priority, _TRIM_PRIORITY_ESSENTIAL
    assert _infer_section_priority(line, 7) == _TRIM_PRIORITY_ESSENTIAL


# ── in-cycle aggregate guard: a cycle's trades can't collectively over-deploy ──
def _gtrade_cyc(size, cycle_did):
    t = _gtrade(size)
    t["_cycle_did"] = cycle_did
    return t

@pytest.mark.asyncio
async def test_gate_in_cycle_aggregate_does_not_over_deploy():
    # available stays STALE within a cycle (fund_manager.in_use refreshes ~60s),
    # so without the accumulator 4 trades would each pass at available*0.40 and
    # total ~1.6x available. The per-cycle reservation must bound the SUM to
    # <= available.
    g = _gate(True, available=9000.0)  # per-trade ceiling 0.40*9000=3600
    approved = []
    for _ in range(4):
        t = await g.validate(_gtrade_cyc(3000.0, cycle_did="cyc-1"))
        approved.append(t["size_usd"])
    assert sum(approved) <= 9000.0 + 1e-6, f"over-deploy: {approved} sum={sum(approved)}"
    assert approved[0] == pytest.approx(3000.0)   # first trades pass at brain size
    assert approved[-1] == pytest.approx(0.0)      # budget exhausted -> last clamped to 0

@pytest.mark.asyncio
async def test_gate_aggregate_resets_on_new_cycle():
    g = _gate(True, available=9000.0)
    # exhaust cycle 1
    for _ in range(4):
        await g.validate(_gtrade_cyc(3000.0, cycle_did="cyc-A"))
    # new cycle did -> accumulator resets -> first trade passes at brain size again
    t = await g.validate(_gtrade_cyc(3000.0, cycle_did="cyc-B"))
    assert t["size_usd"] == pytest.approx(3000.0)


# ── leverage-aware MARGIN path (tiered single source: book of N fits usable) ──
def _gate_margin(usable: float, max_pos: int, deployed: float = 0.0, equity: float = 46484.0):
    from src.apex.gate import TradeGate
    from src.fund_manager.tiered_capital import FundLimits
    s = APEXSettings(); s.brain_authoritative_sizing_enabled = True; s.max_position_size_usd = 100000.0
    class _TCM:
        def get_limits(self, eq, dep):
            return FundLimits(total_equity=eq, starting_equity=eq, tier=3, tier_pct=usable / eq,
                usable_capital=usable, currently_deployed=dep,
                available_for_trades=max(0.0, usable - dep), max_single_trade=usable * 0.25,
                max_positions=max_pos, user_override_pct=None)
    svc = {"tiered_capital": _TCM(),
           "fund_manager": SimpleNamespace(_account_state=SimpleNamespace(total_equity=equity, in_use=deployed))}
    return TradeGate(svc, s)

def _mtrade(size, lev, did="m"):
    t = _gtrade(size); t["leverage"] = lev; t["_cycle_did"] = did; return t

@pytest.mark.asyncio
async def test_margin_per_trade_is_usable_over_maxpos_not_25pct():
    # size_usd IS the MARGIN. usable=$23,242, max_pos=10 -> per-trade MARGIN cap =
    # $2,324 (10%), NOT the old max_single_trade 25% ($5,811). A big over-ask caps
    # to that margin DIRECTLY (no x leverage — the executor applies leverage).
    g = _gate_margin(usable=23242.0, max_pos=10)
    t = await g.validate(_mtrade(50000.0, lev=3))   # over-asks
    assert t["size_usd"] == pytest.approx(2324.2, rel=0.01)  # MARGIN = usable / max_pos

@pytest.mark.asyncio
async def test_margin_cap_is_leverage_independent():
    # size_usd IS margin -> capped at usable/max_pos REGARDLESS of leverage. The
    # executor (qty = size_usd x leverage / price) turns the same margin into a
    # bigger NOTIONAL at higher leverage; the gate returns the SAME margin for 3x
    # and 5x. (Multiplying by leverage HERE was the double-leverage bug.)
    m3 = (await _gate_margin(usable=23242.0, max_pos=10).validate(_mtrade(50000.0, lev=3)))["size_usd"]
    m5 = (await _gate_margin(usable=23242.0, max_pos=10).validate(_mtrade(50000.0, lev=5)))["size_usd"]
    assert m3 == pytest.approx(2324.2, rel=0.01)
    assert m5 == pytest.approx(2324.2, rel=0.01)   # same MARGIN; notional differs downstream

@pytest.mark.asyncio
async def test_full_book_of_maxpos_fits_usable_then_zero():
    # 10 trades (max_pos) each at the per-trade MARGIN should fit usable; the 11th -> 0.
    g = _gate_margin(usable=23242.0, max_pos=10)
    margins = [(await g.validate(_mtrade(50000.0, lev=3, did="book")))["size_usd"] for _ in range(11)]
    assert sum(margins) <= 23242.0 + 1.0          # cumulative MARGIN within usable
    assert margins[10] == pytest.approx(0.0)       # 11th over the book -> skipped

@pytest.mark.asyncio
async def test_deployed_margin_reduces_available():
    # $20,000 margin already deployed -> only ~$3,242 margin left -> ~1.4 trades.
    g = _gate_margin(usable=23242.0, max_pos=10, deployed=20000.0)
    margins = [(await g.validate(_mtrade(50000.0, lev=3, did="dep")))["size_usd"] for _ in range(3)]
    assert sum(margins) <= 3242.0 + 1.0

@pytest.mark.asyncio
async def test_check1_margin_backstop_is_usable_not_fixed_config():
    # size_usd is MARGIN. CHECK 4 caps it at per-trade margin (usable/max_pos =
    # $2,324) regardless of leverage; CHECK 1's MARGIN backstop is the whole usable
    # pool ($23,242), NOT the low fixed config ($4,000) -> a legit per-trade-margin
    # trade is never clipped by the fixed value, and never re-inflated by leverage.
    g = _gate_margin(usable=23242.0, max_pos=10)
    g._settings.max_position_size_usd = 4000.0   # low fixed config
    g._settings.max_leverage = 5
    t = await g.validate(_mtrade(50000.0, lev=5))
    assert t["size_usd"] == pytest.approx(2324.2, rel=0.01)  # MARGIN cap, leverage-independent
