"""Phase 3 behavioral self-verification — stepped break-even ladder (technique 1).

Confirms the ladder math without a live tick:
  1. Not armed below the first level; arms and locks level-minus-offset above it.
  2. The floor only rises as high-water profit climbs (monotonic, tighten-only).
  3. A ladder below the current SL does not apply (tighten-only).
  4. Short mirrors long.
  5. The time dial makes the ladder lock tighter (higher) late in the trade.
  6. 'profit_sniper_ladder' is registered in the gateway R3-bypass allow-list.

Run from the project root:  PYTHONPATH=. python scripts/verify_profit_fetching_phase3.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.workers.profit_sniper import ProfitSniper


def _state(peak: float, direction: str = "Buy", entry: float = 100.0):
    # _compute_ladder_floor reads only entry_price, direction, peak_pnl_pct.
    return SimpleNamespace(symbol="X", entry_price=entry, direction=direction, peak_pnl_pct=peak)


def main() -> int:
    settings = Settings.load(config_path="config.toml")
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    pf = settings.profit_fetching

    young = sniper._time_dial.resolve(0.0, 50.0)   # step/offset at young anchor
    step, offset = young.ladder_step_pct, young.lock_offset_pct
    arm = pf.min_profit_to_arm_ladder_pct

    # 1. Below the first level -> not armed.
    r = sniper._compute_ladder_floor(_state(arm - 0.1), young, current_sl=0.0)
    assert not r.armed and not r.should_apply, "must not arm below the first level"

    # 2. At the first crossed level -> locks (level - offset).
    peak1 = step  # exactly one step
    r1 = sniper._compute_ladder_floor(_state(peak1), young, current_sl=0.0)
    exp_lock = step - offset
    exp_stop = 100.0 * (1.0 + exp_lock / 100.0)
    assert r1.armed and r1.should_apply, "must arm at the first level"
    assert abs(r1.lock_pct - exp_lock) < 1e-6, (r1.lock_pct, exp_lock)
    assert abs(r1.ladder_stop_price - exp_stop) < 1e-6, (r1.ladder_stop_price, exp_stop)
    print(f"First level OK: peak={peak1}% -> lock={r1.lock_pct}% stop={r1.ladder_stop_price}")

    # 3. Higher peak -> higher (tighter) floor; monotonic vs the prior lock.
    r2 = sniper._compute_ladder_floor(_state(1.7), young, current_sl=r1.ladder_stop_price)
    assert r2.ladder_stop_price > r1.ladder_stop_price and r2.should_apply, "floor must rise"
    print(f"Monotonic OK: peak=1.7% -> level={r2.level_crossed_pct}% lock={r2.lock_pct}% stop={r2.ladder_stop_price}")

    # 4. Ladder below the current SL -> not tighter -> does not apply.
    r3 = sniper._compute_ladder_floor(_state(1.7), young, current_sl=r2.ladder_stop_price + 5.0)
    assert not r3.should_apply, "must not loosen below an already-tighter SL"

    # 5. Short mirrors long (stop below entry, applies).
    rs = sniper._compute_ladder_floor(_state(1.7, direction="Sell"), young, current_sl=0.0)
    assert rs.ladder_stop_price < 100.0 and rs.should_apply, "short ladder must sit below entry"
    print(f"Short mirror OK: peak=1.7% -> stop={rs.ladder_stop_price}")

    # 6. Time dial tightens the lock late in the trade (old anchor) vs young.
    old = sniper._time_dial.resolve(50.0, 50.0)
    r_old = sniper._compute_ladder_floor(_state(1.7), old, current_sl=0.0)
    assert r_old.ladder_stop_price > r2.ladder_stop_price, "old-age ladder must lock tighter"
    print(f"Dial-tighten OK: young stop={r2.ladder_stop_price} -> old stop={r_old.ladder_stop_price}")

    # 7. Gateway source registered for the R3 bypass.
    assert "profit_sniper_ladder" in SLGateway._BREAKEVEN_BYPASS_SOURCES, "ladder source not registered"
    print("Gateway source OK: profit_sniper_ladder in R3-bypass allow-list")

    print("\nPHASE_3_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
