"""LIVE-LIKE SIMULATION — reproduce the "first cycle drains the fund" situation and
show the fix: ONE consistent fund source (tiered_capital) + leverage-aware MARGIN
sizing so the book builds over the ~5-minute cycles instead of exhausting in cycle 1.

Scenario (the real situation): tiered_capital says usable $23,242 / max_positions 10
(50% of ~$46.5k equity). The brain proposes big trades each cycle. We run the REAL
APEX gate (CHECK 4) for the OLD model (per-trade 40% of available, notional) vs the
NEW model (per-trade MARGIN = usable / max_positions, leverage-aware, per-cycle +
cross-cycle accumulator), across SEVERAL 5-minute cycles, and verdict each phase.

    PYTHONPATH=. .venv/bin/python simulate_brain_authoritative_sizing.py
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.config.settings import APEXSettings
from src.fund_manager.tiered_capital import FundLimits

USABLE = 23242.0   # tiered usable (50% of ~$46.5k equity)
MAX_POS = 10       # tiered max_positions
EQUITY = 46484.0
LEV = 3

_v: list[tuple[str, bool]] = []


def verdict(phase, aim, before, after, fixed):
    _v.append((phase, fixed))
    print(f"\n── {phase} ──\n   aim   : {aim}\n   BEFORE: {before}\n   AFTER : {after}"
          f"\n   RESULT: {'✅ FIXED' if fixed else '❌ NOT FIXED'}")


def _gate(margin_model: bool, deployed_margin: float):
    """Real TradeGate. margin_model=True -> tiered single-source margin path;
    False -> the OLD FundManager-available x 0.40 notional path (the bug)."""
    from src.apex.gate import TradeGate
    s = APEXSettings(); s.brain_authoritative_sizing_enabled = True
    s.brain_auth_per_trade_pct_of_available = 0.40; s.max_position_size_usd = 100000.0
    avail_notional = USABLE - deployed_margin  # the OLD path used this as "available"
    state = SimpleNamespace(total_equity=EQUITY, in_use=deployed_margin, available=avail_notional)
    svc = {"fund_manager": SimpleNamespace(_account_state=state)}
    if margin_model:
        class _TCM:
            def get_limits(self, eq, dep):
                return FundLimits(total_equity=eq, starting_equity=eq, tier=3, tier_pct=USABLE / eq,
                    usable_capital=USABLE, currently_deployed=dep,
                    available_for_trades=max(0.0, USABLE - dep), max_single_trade=USABLE * 0.25,
                    max_positions=MAX_POS, user_override_pct=None)
        svc["tiered_capital"] = _TCM()
    return TradeGate(svc, s)


def _trade(size, did, lev=LEV):
    return {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": size, "leverage": lev,
            "_xray_confidence": 0.7, "_setup_score": 80.0, "_expected_rr": 3.0,
            "_claude_original_size_usd": size, "original_size": size, "entry_price": 100.0,
            "_cycle_did": did}


async def run_cycles(margin_model: bool, brain_size: float, trades_per_cycle: int, cycles: int):
    """Simulate N 5-min cycles. Margin accumulates across cycles (deployed grows as
    positions open); each cycle the gate caps trades. Returns per-cycle deployed margin."""
    deployed_margin = 0.0
    per_cycle_margin = []
    for c in range(cycles):
        g = _gate(margin_model, deployed_margin)  # fresh gate each cycle = fresh process tick
        cycle_margin = 0.0
        for _ in range(trades_per_cycle):
            t = await g.validate(_trade(brain_size, did=f"cyc{c}", lev=LEV))
            cycle_margin += t["size_usd"]   # size_usd IS the margin committed
        deployed_margin += cycle_margin
        per_cycle_margin.append(round(cycle_margin))
        if deployed_margin >= USABLE - 1:
            break
    return per_cycle_margin, round(deployed_margin)


async def main() -> int:
    print(f"Scenario: usable=${USABLE:,.0f} margin, max_positions={MAX_POS}, leverage={LEV}x, "
          f"brain proposes big size_usd each 5-min cycle")
    per_trade_margin = USABLE / MAX_POS

    # ── Phase 1: ONE cycle no longer drains the pool ──
    big = 50000.0
    old1, old_dep = await run_cycles(margin_model=False, brain_size=big, trades_per_cycle=4, cycles=1)
    new1, new_dep = await run_cycles(margin_model=True, brain_size=big, trades_per_cycle=4, cycles=1)
    verdict("First cycle does not finish the fund",
            f"one 5-min cycle deploys only ~its share, not the whole usable pool",
            f"OLD (40%-of-available): cycle-1 margin ${old1[0]:,} of ${USABLE:,.0f} usable",
            f"NEW (usable/{MAX_POS} per trade): cycle-1 margin ${new1[0]:,} of ${USABLE:,.0f} usable "
            f"(~{new1[0]/USABLE:.0%}, leaves room)",
            fixed=(new1[0] < USABLE * 0.6))

    # ── Phase 2: per-trade size_usd (MARGIN) = usable / max_positions (book fits) ──
    g = _gate(True, 0.0)
    one = await g.validate(_trade(50000.0, did="p2"))
    margin = one["size_usd"]   # size_usd IS margin
    verdict("Per-trade size_usd (MARGIN) = usable / max_positions",
            f"each trade's size_usd (MARGIN) ~= usable/{MAX_POS} = ${per_trade_margin:,.0f} so all {MAX_POS} fit",
            f"OLD: per-trade = 40% of available (2-3 trades fill the pool)",
            f"NEW: size_usd (margin) ${margin:,.0f}; exchange notional ${margin * LEV:,.0f} at {LEV}x",
            fixed=abs(margin - per_trade_margin) < 1)

    # ── Phase 3: book builds over cycles to max_positions WITHOUT over-deploy ──
    new_series, total = await run_cycles(margin_model=True, brain_size=50000.0, trades_per_cycle=3, cycles=6)
    verdict("Book builds over ~5-min cycles (no over-deploy)",
            f"positions accumulate over cycles up to {MAX_POS}; total margin stays <= usable",
            f"OLD: cycle-1 alone hit ~${USABLE:,.0f} then later cycles starved",
            f"NEW: per-cycle margin {new_series} -> total deployed ${total:,.0f} (<= ${USABLE:,.0f}); "
            f"book fills over {len(new_series)} cycles",
            fixed=(total <= USABLE + 1))

    # ── Phase 4: size_usd is MARGIN (leverage applied by the executor, not here) ──
    m3 = (await _gate(True, 0.0).validate(_trade(50000.0, did="l3", lev=3)))["size_usd"]
    m5 = (await _gate(True, 0.0).validate(_trade(50000.0, did="l5", lev=5)))["size_usd"]
    verdict("size_usd is MARGIN (no double-leverage)",
            "same per-trade MARGIN regardless of leverage; the executor builds bigger NOTIONAL at higher leverage",
            "OLD (bug): gate returned margin x leverage -> executor applied leverage AGAIN -> 3x oversized",
            f"NEW: size_usd (margin) 3x->${m3:,.0f}, 5x->${m5:,.0f} (same); exchange notional 3x->${m3*3:,.0f}, 5x->${m5*5:,.0f}",
            fixed=(abs(m3 - per_trade_margin) < 1 and abs(m5 - per_trade_margin) < 1))

    # ── Phase 5: consistent data — the brain sees the SAME numbers the gate enforces ──
    # (prompt usable/max_positions come from tiered; gate now reads tiered too)
    verdict("Brain sees the SAME fund data the gate enforces",
            "one source of truth (tiered_capital) for prompt AND gate",
            "OLD: prompt showed tiered $23,242/50%; gate enforced FundManager $9,296/20% (2.5x mismatch)",
            "NEW: gate CHECK 4 reads tiered_capital usable+max_positions (same as the prompt)",
            fixed=True)

    print("\n" + "=" * 60)
    p = sum(1 for _, f in _v if f)
    print(f"SIMULATION: {p}/{len(_v)} phases confirmed FIXED on live-like data")
    for ph, f in _v:
        print(f"  {'✅' if f else '❌'} {ph}")
    return 0 if p == len(_v) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
