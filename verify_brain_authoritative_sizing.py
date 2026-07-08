#!/usr/bin/env python3
"""Read-only verification for Brain-Authoritative Fund Management (2026-05-31).

Demonstrates, on real objects (no DB/network), that the downstream now HONORS
the brain's size_usd instead of shrinking it: the APEX optimizer floor, the gate
CHECK 4 hard available-capital ceiling, and the disabled performance enforcer.
Shows BEFORE (flag off / legacy) vs AFTER (flag on) for a brain $3000 trade.

    PYTHONPATH=. .venv/bin/python verify_brain_authoritative_sizing.py
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import APEXSettings, EnforcerSettings, Settings

_p = _f = 0


def check(name, ok, detail=""):
    global _p, _f
    _p += ok
    _f += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _apex(flag):
    from src.apex.optimizer import TradeOptimizer
    s = APEXSettings(); s.brain_authoritative_sizing_enabled = flag; s.max_position_size_usd = 4000.0
    return TradeOptimizer(None, None, s)


def _trade(size):
    from src.apex.optimizer import OptimizedTrade
    return OptimizedTrade(symbol="BTCUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
                          tp_mode="fixed", position_size_usd=size, leverage=3,
                          entry_timing="immediate", add_on_pullback=False,
                          reasoning="t", confidence=0.5, original_size=size)


def _gate(flag, usable=23242.0, max_pos=10, deployed=0.0, equity=46484.0):
    # Single source = tiered_capital (margin), leverage-aware (the live path).
    from src.apex.gate import TradeGate
    from src.fund_manager.tiered_capital import FundLimits
    s = APEXSettings(); s.brain_authoritative_sizing_enabled = flag
    s.brain_auth_per_trade_pct_of_available = 0.40; s.max_position_size_usd = 100000.0
    class _TCM:
        def get_limits(self, eq, dep):
            return FundLimits(total_equity=eq, starting_equity=eq, tier=3, tier_pct=usable / eq,
                usable_capital=usable, currently_deployed=dep, available_for_trades=max(0.0, usable - dep),
                max_single_trade=usable * 0.25, max_positions=max_pos, user_override_pct=None)
    svc = {"tiered_capital": _TCM(),
           "fund_manager": SimpleNamespace(_account_state=SimpleNamespace(total_equity=equity, in_use=deployed))}
    return TradeGate(svc, s)


def _gtrade(size):
    return {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": size, "leverage": 3,
            "_xray_confidence": 0.7, "_setup_score": 80.0, "_expected_rr": 3.0,
            "_claude_original_size_usd": size, "original_size": size, "entry_price": 100.0}


def _enf(flag):
    from src.strategies.performance_enforcer import PerformanceEnforcer
    ec = EnforcerSettings(); ec.size_reduction_enabled = flag
    e = PerformanceEnforcer(SimpleNamespace(enforcer=ec), MagicMock(), {})
    e._profit_today_pct = -5.0  # red day (would be x0.50 if enabled)
    return e


async def main():
    print("\n== APEX optimizer: honor brain size (brain $3000, low conviction 0.5) ==")
    t_off = _trade(3000.0); _apex(False)._apply_constraints(t_off)
    t_on = _trade(3000.0); _apex(True)._apply_constraints(t_on)
    print(f"   BEFORE (flag off): brain $3000 -> APEX ${t_off.position_size_usd:.0f} (shrunk)")
    print(f"   AFTER  (flag on) : brain $3000 -> APEX ${t_on.position_size_usd:.0f} (honored)")
    check("APEX honors brain size when flag on", t_on.position_size_usd >= 3000.0)
    check("APEX legacy shrinks when flag off", t_off.position_size_usd < 3000.0)

    print("\n== gate CHECK 4: per-trade MARGIN cap = usable / max_positions (size_usd is MARGIN) ==")
    g_big = await _gate(True).validate(_gtrade(50000.0))   # brain over-asks
    print(f"   brain $50,000 over-ask -> ${g_big['size_usd']:.0f} MARGIN (= usable $23,242 / 10; executor notional ${g_big['size_usd']*3:.0f} @3x)")
    check("gate caps size_usd to per-trade MARGIN usable/max_positions ($2,324)", abs(g_big["size_usd"] - 2324.2) < 5)
    g_ok = await _gate(True).validate(_gtrade(2000.0))   # $2,000 margin < $2,324 -> honored
    print(f"   brain $2,000 margin (< $2,324) -> ${g_ok['size_usd']:.0f} (honored untouched)")
    check("trade within per-trade margin passes untouched", abs(g_ok["size_usd"] - 2000.0) < 1)

    print("\n== performance enforcer switch (red day -5% PnL) ==")
    m_off, m_on = _enf(False).get_size_multiplier(), _enf(True).get_size_multiplier()
    print(f"   BEFORE (enabled): multiplier {m_on} (halves size on a red day)")
    print(f"   AFTER  (disabled): multiplier {m_off} (no throttle; brain size stands)")
    check("enforcer disabled -> multiplier 1.0", m_off == 1.0)
    check("enforcer enabled -> still throttles", m_on < 1.0)

    print("\n== execution venue cap (strategy_worker._execute_claude_trade) ==")
    # Replicates the venue-cap branch: bybit_demo runs bybit.testnet=false, so the
    # legacy cap is $1000 and would re-clamp the brain's $3000 AFTER the gate.
    # Under brain-auth the venue ceiling = apex.max_position_size_usd.
    _s = Settings.load()
    is_testnet = bool(getattr(getattr(_s, "bybit", None), "testnet", False))
    legacy_cap = 5000 if is_testnet else 1000
    brain_cap = float(_s.apex.max_position_size_usd) if _s.apex.brain_authoritative_sizing_enabled else legacy_cap
    print(f"   testnet={is_testnet} -> legacy venue cap ${legacy_cap} (would clamp brain $3000 to ${min(3000,legacy_cap)})")
    print(f"   brain-auth venue cap ${brain_cap:.0f} -> brain $3000 executes as ${min(3000.0,brain_cap):.0f}")
    check("venue cap no longer pins brain $3000 to $1000 under flag", min(3000.0, brain_cap) == 3000.0)

    print("\n== in-cycle book guard: book of max_positions fits usable, no over-deploy ==")
    gA = _gate(True)
    margins = []
    for _ in range(11):
        t = _gtrade(50000.0); t["_cycle_did"] = "cyc-v"
        margins.append((await gA.validate(t))["size_usd"])
    margin_sum = sum(margins)   # size_usd IS margin
    print(f"   11 over-asks -> margins sum ${margin_sum:.0f} (10 fit usable $23,242, 11th=${margins[10]:.0f})")
    check("cycle margin bounded to usable (no over-deploy)", margin_sum <= 23242.0 + 1 and margins[10] < 1.0)

    print("\n== live config + prompt ==")
    s = Settings.load()
    check("config: brain_authoritative_sizing_enabled=true", s.apex.brain_authoritative_sizing_enabled is True)
    check("config: enforcer size_reduction_enabled=false", s.enforcer.size_reduction_enabled is False)
    from src.brain.strategist import TRADE_SYSTEM_PROMPT_ZERO_TWO as P
    check("live prompt instructs PROPER FUNDING vs Available-for-new-trades",
          "PROPER FUNDING" in P and "Available for new trades" in P)

    print(f"\n{'='*52}\nBRAIN-AUTH SIZING: {_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
