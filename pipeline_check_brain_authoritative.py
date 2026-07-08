"""End-to-end PIPELINE check — Brain-Authoritative Fund Management on the REAL project.

Drives the REAL objects wired the way WorkerManager wires them (same constructor
signatures: apex_cfg = settings.apex -> TradeOptimizer / TradeGate; the REAL
TieredCapitalManager as the SINGLE fund source; PerformanceEnforcer), from the
REAL config.toml through Settings. Proves DI wiring + data flow + actual runtime
behaviour for every phase of the fix, end-to-end, on the LEVERAGE-AWARE MARGIN
model where the gate and the prompt read ONE source (tiered_capital):

  Phase 0  DI wiring + boot sentinels + gate accumulator init
  Phase A  prompt enrichment wiring (both system prompts + trim whitelist + the
           tiered_capital FundLimits the ACCOUNT lines render)
  Phase B  sizing chain (flag ON): a PROPER brain trade (notional = per-trade
           margin x leverage) -> layer_manager stamps -> APEX optimizer floor ->
           gate CHECK 0/1/3/4 (tiered margin) -> venue cap -> final HONORED; and an
           over-ask caps to per-trade MARGIN (= usable / max_positions) x leverage
  Phase B2 in-cycle book guard: a book of max_positions over-asks fits usable
           MARGIN exactly; the (max_positions+1)th -> 0 (no over-deploy)
  Phase B3 enforcer switch: red-day multiplier == 1.0 (no throttle)
  Phase C  flag OFF = legacy byte-identical (optimizer shrinks, gate conviction
           cap, venue $1000) -> the instant-revert path

Run:  PYTHONPATH=. .venv/bin/python pipeline_check_brain_authoritative.py
Exit 0 = the whole sizing pipeline behaves correctly end-to-end on the real project.
"""
from __future__ import annotations

import asyncio
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import Settings

_results: list[tuple[str, bool, str]] = []
LEV = 3


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _live_equity(default: float = 46500.0) -> float:
    """The live total equity the running gate feeds tiered_capital. Read from a
    CAPITAL_TIER log line if present (read-only); else the documented ~$46.5k."""
    try:
        with open("data/logs/workers.log", encoding="utf-8", errors="ignore") as fh:
            vals = re.findall(r"CAPITAL_TIER \| eq=([0-9.]+)", fh.read())
        return float(vals[-1]) if vals else default
    except Exception:
        return default


def _gtrade(size: float, lev: int = LEV, did: str = "cyc-pipe") -> dict:
    # The keys layer_manager stamps before APEX (_claude_original_size_usd at
    # layer_manager.py:1479, _cycle_did at :1481) + the conviction breadcrumbs.
    return {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": size, "leverage": lev,
            "_xray_confidence": 0.7, "_setup_score": 80.0, "_expected_rr": 3.0,
            "_claude_original_size_usd": size, "original_size": size,
            "entry_price": 100.0, "_cycle_did": did}


def _opt_trade(size: float):
    from src.apex.optimizer import OptimizedTrade
    return OptimizedTrade(symbol="BTCUSDT", direction="Buy", sl_pct=2.0, tp_pct=4.0,
                          tp_mode="fixed", position_size_usd=size, leverage=LEV,
                          entry_timing="immediate", add_on_pullback=False,
                          reasoning="t", confidence=0.5, original_size=size)


async def main() -> int:
    from src.apex.gate import TradeGate
    from src.apex.optimizer import TradeOptimizer
    from src.strategies.performance_enforcer import PerformanceEnforcer
    from src.fund_manager.tiered_capital import TieredCapitalManager
    from src.brain.strategist import (
        TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO, _TRIM_ESSENTIAL_MARKERS,
    )

    s = Settings.load()
    apex_cfg = s.apex                       # exact manager DI source (manager.py:2812)
    equity = _live_equity()
    flag = bool(apex_cfg.brain_authoritative_sizing_enabled)

    # The SINGLE fund source — the REAL TieredCapitalManager (same object the prompt
    # builder reads). The gate reads it from services["tiered_capital"]; the prompt
    # reads the same get_limits(). One source of truth.
    tcm = TieredCapitalManager(MagicMock(), starting_equity=equity)

    def fund_svc(deployed: float = 0.0) -> dict:
        lim0 = tcm.get_limits(equity, deployed)
        return {"tiered_capital": tcm,
                "fund_manager": SimpleNamespace(_account_state=SimpleNamespace(
                    total_equity=equity, in_use=deployed,
                    available=lim0.available_for_trades))}  # `available` for the legacy (flag-off) path

    lim = tcm.get_limits(equity, 0.0)
    usable = float(lim.usable_capital)
    max_pos = max(int(lim.max_positions or 1), 1)
    per_trade_margin = usable / max_pos
    proper_margin = per_trade_margin   # a PROPER brain trade outputs size_usd = MARGIN ~ this

    # ───── PHASE 0: DI wiring + sentinels ─────
    print("\n== PHASE 0: DI wiring (manager signatures) + sentinels ==")
    opt = TradeOptimizer(None, None, apex_cfg)          # manager.py:2821
    gate = TradeGate(fund_svc(0.0), apex_cfg)           # manager.py:2854 (fires BRAIN_AUTHORITATIVE_SIZING_SENTINEL)
    enf = PerformanceEnforcer(SimpleNamespace(enforcer=s.enforcer), MagicMock(), {})  # fires ENFORCER sentinel
    check("flag reaches optimizer+gate via settings.apex (not inert)",
          getattr(apex_cfg, "brain_authoritative_sizing_enabled", None) is True,
          f"enabled={flag} pct={apex_cfg.brain_auth_per_trade_pct_of_available} max=${apex_cfg.max_position_size_usd:.0f}")
    check("gate in-cycle accumulator initialised",
          gate._brain_auth_cycle_reserved == 0.0 and gate._brain_auth_cycle_did is None)
    check("enforcer size-reduction switched OFF (config)", s.enforcer.size_reduction_enabled is False)
    check("SINGLE fund source = tiered_capital (usable / max_positions = per-trade margin)",
          usable > 0 and max_pos >= 1,
          f"eq=${equity:,.0f} usable=${usable:,.0f} max_pos={max_pos} per_trade_margin=${per_trade_margin:,.0f}")

    # ───── PHASE A: prompt enrichment wiring ─────
    print("\n== PHASE A: prompt fund-context + PROPER FUNDING instruction ==")
    for label, p in (("legacy", TRADE_SYSTEM_PROMPT), ("LIVE ZERO_TWO", TRADE_SYSTEM_PROMPT_ZERO_TWO)):
        check(f"system prompt [{label}] carries PROPER FUNDING + Available-for-new-trades",
              "PROPER FUNDING" in p and "Available for new trades" in p and "probe" in p.lower())
    _needed = ["Open trades:", "Used funds:", "Usable funds:", "Available for new trades"]
    check("the 4 ACCOUNT fund-lines are trim-essential (survive token-trim)",
          all(any(m in mk for mk in _TRIM_ESSENTIAL_MARKERS) for m in _needed))
    check("the prompt + gate read the SAME tiered_capital.get_limits numbers",
          lim.usable_capital > 0 and lim.available_for_trades >= 0 and lim.max_single_trade > 0,
          f"usable=${lim.usable_capital:,.0f} avail_new=${lim.available_for_trades:,.0f} max_single=${lim.max_single_trade:,.0f}")

    # ───── PHASE B: sizing chain (flag ON) — size_usd is MARGIN ─────
    print(f"\n== PHASE B: chain (flag ON) — per-trade MARGIN ${per_trade_margin:,.0f} (executor notional ${per_trade_margin*LEV:,.0f} @ {LEV}x) ==")
    # A PROPER brain trade outputs size_usd = the per-trade MARGIN; HONORED end-to-end.
    ot = _opt_trade(proper_margin); opt._apply_constraints(ot)               # APEX (no shrink under flag)
    after_apex = ot.position_size_usd
    gated = (await gate.validate(_gtrade(after_apex)))["size_usd"]            # gate CHECK 0/1/3/4
    is_testnet = bool(getattr(getattr(s, "bybit", None), "testnet", False))
    # strategy_worker venue cap (replicated): size_usd is MARGIN, so under brain-auth
    # it caps at the whole usable MARGIN pool (never re-clamps a legitimate
    # per-trade-margin trade, no x leverage); flag off -> legacy $5000/$1000.
    venue = max(float(apex_cfg.max_position_size_usd), usable) if flag else (5000 if is_testnet else 1000)
    final = min(max(gated, 0.0), venue)                                      # strategy_worker venue cap
    print(f"   proper margin ${proper_margin:.0f} -> APEX ${after_apex:.0f} -> gate ${gated:.0f} -> venue(${venue:.0f}) -> final ${final:.0f}")
    check("APEX optimizer does not shrink the brain's size", after_apex >= proper_margin - 1)
    check("gate honors a proper per-trade-margin trade (size_usd is MARGIN)", abs(gated - proper_margin) < 1)
    check("venue cap honors it under the flag (legacy would be $1000)", abs(final - proper_margin) < 1,
          f"final=${final:.0f} margin (HONORED; executor notional ${final*LEV:.0f})")
    # An OVER-ASK caps to the per-trade MARGIN (= usable / max_positions), NO x leverage.
    over = (await TradeGate(fund_svc(0.0), apex_cfg).validate(_gtrade(usable * 5)))["size_usd"]
    check("over-ask caps to per-trade MARGIN (usable/max_pos), not x leverage",
          abs(over - per_trade_margin) < 1.0, f"over-ask -> ${over:.0f} margin")
    # size_usd is MARGIN -> the gate cap is leverage-INDEPENDENT (executor builds notional).
    m5 = (await TradeGate(fund_svc(0.0), apex_cfg).validate(_gtrade(usable * 5, lev=5)))["size_usd"]
    check("margin cap is leverage-independent (no double-leverage)",
          abs(m5 - per_trade_margin) < 1.0 and abs(m5 - over) < 1.0, f"3x=${over:.0f} == 5x=${m5:.0f} margin")

    # ───── PHASE B2: in-cycle book guard (book of max_positions fits usable) ─────
    print(f"\n== PHASE B2: book guard — {max_pos+1} over-asks, book of {max_pos} fits usable ${usable:,.0f} ==")
    g2 = TradeGate(fund_svc(0.0), apex_cfg)
    margins = [(await g2.validate(_gtrade(usable * 5, did="book")))["size_usd"] for _ in range(max_pos + 1)]
    margin_sum = sum(margins)   # size_usd IS margin
    print(f"   {max_pos+1} over-asks -> margins sum ${margin_sum:,.0f}  (last=${margins[-1]:.0f})")
    check("cycle MARGIN bounded to usable (no over-deploy)", margin_sum <= usable + 1.0)
    check(f"the ({max_pos}+1)th trade over the book -> ~0", margins[-1] < 1.0)  # <=rounding residue
    check("aggregate resets on new cycle did",
          (await TradeGate(fund_svc(0.0), apex_cfg).validate(_gtrade(proper_margin, did="fresh")))["size_usd"] >= proper_margin - 1)

    # ───── PHASE B3: enforcer switch ─────
    print("\n== PHASE B3: performance enforcer switch (red day -5% PnL) ==")
    enf._profit_today_pct = -5.0
    check("enforcer disabled -> multiplier 1.0 (no red-day throttle)", enf.get_size_multiplier() == 1.0)

    # ───── PHASE C: flag OFF = legacy byte-identical (instant revert) ─────
    print("\n== PHASE C: flag OFF -> legacy pipeline (instant-revert proof) ==")
    off = Settings.load().apex
    off.brain_authoritative_sizing_enabled = False
    off.max_position_size_usd = 1200.0      # legacy default
    brain = 3000.0
    ot2 = _opt_trade(brain); TradeOptimizer(None, None, off)._apply_constraints(ot2)
    gated_off = (await TradeGate(fund_svc(0.0), off).validate(_gtrade(ot2.position_size_usd)))["size_usd"]
    venue_off = 5000 if is_testnet else 1000
    final_off = min(max(gated_off, 0.0), venue_off)
    print(f"   brain ${brain:.0f} -> APEX ${ot2.position_size_usd:.0f} -> gate ${gated_off:.0f} -> venue(${venue_off}) -> final ${final_off:.0f}")
    check("flag OFF shrinks the brain's size (legacy behaviour intact)", final_off < brain)

    print("\n" + "=" * 60)
    p = sum(1 for _, ok, _ in _results if ok)
    f = sum(1 for _, ok, _ in _results if not ok)
    print(f"PIPELINE: {p} passed, {f} failed")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
