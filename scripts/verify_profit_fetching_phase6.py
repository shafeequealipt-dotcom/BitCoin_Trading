"""Phase 6 behavioral self-verification — safety stop / naked-position sweeper.

Confirms (pure, no live tick):
  1. The safety floor price is safety_pct off entry (below for long, above for
     short).
  2. As a spine candidate the safety floor: wins for a naked non-climber;
     loses to the ladder/Chandelier on a climber; re-asserts (tighten-only) on
     a looser existing stop; is a no-op when the existing stop is tighter.
  3. 'safety_sweeper' is registered in the gateway R3-bypass allow-list.

Run from the project root:  PYTHONPATH=. python scripts/verify_profit_fetching_phase6.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.workers.profit_sniper import ProfitSniper

floor = ProfitSniper._pf_safety_floor
select = ProfitSniper._pf_select_stop


async def _spine_naked(entry, current_price, direction="Buy"):
    """Drive the real _pf_apply_spine for a NAKED position and capture the SL
    actually sent to the gateway (tests the naked-underwater clamp)."""
    settings = Settings.load(config_path="config.toml")
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    sym = "X"
    sniper._profit_states[sym] = SimpleNamespace(direction=direction, entry_price=entry)
    sniper.trade_coordinator = None
    captured = {}

    async def fake_apply(**kw):
        captured.update(kw)
        return SimpleNamespace(accepted=True, reason="", new_sl_applied=kw.get("new_sl"))

    sniper.sl_gateway = SimpleNamespace(
        apply=fake_apply, next_eligible_in_seconds=lambda s: 0.0,
    )
    pos = SimpleNamespace(
        symbol=sym, stop_loss=None,  # NAKED
        side=SimpleNamespace(value=direction), mark_price=current_price,
    )
    await sniper._pf_apply_spine(sym, pos, {}, current_price)
    return captured


def _ladder(price, apply=True):
    return SimpleNamespace(ladder_stop_price=price, should_apply=apply)


def _trail(price, apply=True):
    return SimpleNamespace(trail_stop_price=price, should_apply=apply)


def main() -> int:
    pf = Settings.load(config_path="config.toml").profit_fetching
    pct = pf.safety_stop_pct  # 2.5

    # 1. Safety floor price.
    long_floor = floor(100.0, True, pct)
    short_floor = floor(100.0, False, pct)
    assert abs(long_floor - 100.0 * (1 - pct / 100)) < 1e-9, long_floor
    assert abs(short_floor - 100.0 * (1 + pct / 100)) < 1e-9, short_floor
    assert long_floor < 100.0 < short_floor
    print(f"Floor OK: long={long_floor} short={short_floor} (pct={pct})")

    # 2a. Naked non-climber (no ladder/trail) -> safety wins and attaches.
    w = select(None, None, current_sl=0.0, is_long=True, safety_stop=long_floor)
    assert w == ("safety", long_floor, "safety_sweeper"), w
    print("Naked OK: safety floor attached when no stop and not climbing")

    # 2b. Climber -> ladder/Chandelier beat the safety floor.
    w = select(_trail(101.4), _ladder(101.9), current_sl=100.0, is_long=True, safety_stop=long_floor)
    assert w == ("ladder", 101.9, "profit_sniper_ladder"), w
    print("Climber OK: ladder beats safety floor (highest-stop-wins)")

    # 2c. Looser existing stop -> safety re-asserts (tighten-only).
    w = select(None, None, current_sl=97.0, is_long=True, safety_stop=long_floor)  # 97.5 > 97.0
    assert w == ("safety", long_floor, "safety_sweeper"), w
    print("Re-assert OK: floor tightens a looser existing stop")

    # 2d. Tighter existing stop -> safety is a no-op (never loosens).
    w = select(None, None, current_sl=98.0, is_long=True, safety_stop=long_floor)  # 97.5 < 98.0
    assert w is None, w
    print("No-op OK: floor does not loosen a tighter existing stop")

    # 2e. Short mirror — naked attaches above entry; tighter (lower) stop is kept.
    w = select(None, None, current_sl=0.0, is_long=False, safety_stop=short_floor)
    assert w == ("safety", short_floor, "safety_sweeper"), w
    w = select(None, None, current_sl=101.0, is_long=False, safety_stop=short_floor)  # 102.5 >= 101.0
    assert w is None, w
    print("Short OK: attaches above entry when naked, keeps a tighter stop")

    # 3. Gateway source registered for the R3 bypass.
    assert "safety_sweeper" in SLGateway._BREAKEVEN_BYPASS_SOURCES
    print("Gateway source OK: safety_sweeper in R3-bypass allow-list")

    # 4. Naked-position spine: fresh naked (above entry) attaches the entry-based
    #    floor; naked-underwater (below the floor) clamps to a VALID just-inside-
    #    price stop so a stop actually attaches (the dangerous case).
    fresh = asyncio.run(_spine_naked(entry=100.0, current_price=105.0))
    assert fresh.get("source") == "safety_sweeper", fresh
    assert abs(fresh["new_sl"] - 97.5) < 1e-6, fresh  # entry*(1-2.5%), no clamp
    assert fresh["new_sl"] < 105.0, fresh  # valid side for a long
    print(f"Naked fresh OK: attaches entry floor {fresh['new_sl']} (< price 105)")

    under = asyncio.run(_spine_naked(entry=100.0, current_price=95.0))
    assert under.get("source") == "safety_sweeper", under
    # entry floor 97.5 is wrong-side of price 95 -> clamp to 95*(1-0.5%)=94.525
    assert under["new_sl"] < 95.0, ("must be a VALID (below-price) stop", under)
    assert abs(under["new_sl"] - 94.525) < 1e-6, under
    print(f"Naked underwater OK: clamped to valid stop {under['new_sl']} (< price 95)")

    short_under = asyncio.run(_spine_naked(entry=100.0, current_price=105.0, direction="Sell"))
    assert short_under["new_sl"] > 105.0, ("short stop must be above price", short_under)
    print(f"Naked underwater short OK: clamped to {short_under['new_sl']} (> price 105)")

    print("\nPHASE_6_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
