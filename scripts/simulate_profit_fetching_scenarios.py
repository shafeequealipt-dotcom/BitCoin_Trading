"""Scenario simulation — recreates the problems each Profit-Fetching fix targets
and plays them tick-by-tick through the REAL system, comparing the FIXED engine
against the LEGACY winner-cutting behavior, and scoring each against its aim.

Real objects used (only the exchange + price are simulated):
  Settings, SLGateway (all four rules), TimeDial, ProfitSniper._compute_ladder_floor,
  ._compute_trail_stop, ._pf_effective_atr, ._pf_select_stop, ._pf_apply_spine,
  PositionProfitState (real peak tracking), DeadlineEngine.

A SimExchange holds the stop the gateway writes; when price crosses it the
position closes there (that is the exit + realized PnL). Each minute is one tick.

Run:  PYTHONPATH=. python scripts/simulate_profit_fetching_scenarios.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.sentinel.deadline import DeadlineEngine
from src.workers.profit_sniper import ProfitSniper
from src.workers.sniper_models import ExtensionResult, MomentumDecayResult
from src.workers.sniper_ring_buffer import PositionProfitState


class SimExchange:
    def __init__(self, initial_sl):
        self.current_sl = initial_sl  # None == naked

    async def set_stop_loss(self, symbol, sl):
        self.current_sl = round(float(sl), 8)
        return True


def _ext(atr_current, extension_atr):
    return ExtensionResult(
        score=50.0, extension_atr=extension_atr, extension_pct=0.0,
        peak_extension_atr=max(0.0, extension_atr), drawdown_atr=0.0,
        atr_current=atr_current, atr_at_entry=atr_current, vol_ratio=1.0,
        vol_adjustment=1.0, base_score=50.0, atr_source="sim",
    )


def _mom(score):
    return MomentumDecayResult(
        score=score, accel_short=0.0, accel_medium=0.0, accel_score=0.0,
        consecutive_decelerations=0, decel_score=0.0, slope_short=0.0,
        slope_medium=0.0, slope_long=0.0, degradation_ratio=1.0,
        degradation_score=0.0, momentum_reversed=False, data_points_used=100,
    )


def _path(segments):
    """Piecewise-linear price path: segments = [(minute, price), ...]; returns a
    per-minute list."""
    out = []
    for i in range(len(segments) - 1):
        (t0, p0), (t1, p1) = segments[i], segments[i + 1]
        for t in range(t0, t1):
            out.append(p0 + (p1 - p0) * (t - t0) / (t1 - t0))
    out.append(segments[-1][1])
    return out


async def simulate(price_path, *, entry, atr, max_hold, direction="Buy",
                   brain_sl, fixed, ride=True, atr_zero_from=None):
    settings = Settings.load(config_path="config.toml")
    pf = settings.profit_fetching
    pf.enabled = fixed
    pf.ride_winner_past_deadline = ride
    is_long = direction == "Buy"
    ex = SimExchange(brain_sl)
    gw = SLGateway(settings=settings, position_service=ex,
                   market_service=SimpleNamespace(), volatility_profiler=None)
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    sniper.sl_gateway = gw
    sniper.trade_coordinator = None
    de = DeadlineEngine()
    sym = "SIM"
    now = time.time()
    st = PositionProfitState(symbol=sym, entry_price=entry, direction=direction,
                             atr_at_entry=atr, opened_at=now)
    sniper._profit_states[sym] = st

    def pnl_of(price):
        return ((price - entry) / entry * 100) if is_long else ((entry - price) / entry * 100)

    exit_price = exit_reason = None
    for t, price in enumerate(price_path):
        cur_sl = ex.current_sl
        pnl = pnl_of(price)

        # (1) exchange stop trigger
        if cur_sl and cur_sl > 0 and ((is_long and price <= cur_sl) or (not is_long and price >= cur_sl)):
            exit_price, exit_reason = cur_sl, "stop_hit"
            break

        # (2) deadline (watchdog decision)
        if t >= max_hold:
            act = de.evaluate(sym, pnl, entry, direction)
            ride_gate = fixed and ride and act.tier.value == "profit"
            if act.should_close and not ride_gate:
                exit_price, exit_reason = price, f"deadline_{act.tier.value}"
                break

        # (3) legacy winner-cap: +1.5% profit-take past half hold
        if (not fixed) and pnl > 1.5 and max_hold > 0 and (t / max_hold) > 0.5:
            exit_price, exit_reason = price, "legacy_profit_take_1.5%"
            break

        # (4) real peak tracking
        st.update(pnl, price, now + t * 60)

        if not fixed:
            continue  # legacy: SL stays at the static brain SL; no ladder/trail

        # (5) FIXED: compute real candidates, select, write via the REAL gateway
        age = float(t)
        dialed = sniper._time_dial.resolve(age, float(max_hold))
        live_atr = 0.0 if (atr_zero_from is not None and t >= atr_zero_from) else atr
        eff_atr, _src = sniper._pf_effective_atr(live_atr, st.atr_at_entry, price)
        ext_atr = (price - entry) / eff_atr if (eff_atr > 0 and is_long) else (entry - price) / eff_atr if eff_atr > 0 else 0.0
        ladder = sniper._compute_ladder_floor(st, dialed, cur_sl or 0.0)
        trail = sniper._compute_trail_stop(
            st, _ext(eff_atr, ext_atr), _mom(40.0), "volatile",
            cur_sl or 0.0, dialed.atr_multiple, atr_value=eff_atr,
        )
        gw._last_change.pop(sym, None)  # compressed sim time -> R4 always eligible
        pos = SimpleNamespace(symbol=sym, stop_loss=cur_sl,
                              side=SimpleNamespace(value=direction), mark_price=price)
        await sniper._pf_apply_spine(sym, pos, {"last_trail": trail, "last_ladder": ladder}, price)

    if exit_price is None:
        exit_price, exit_reason = price_path[-1], "end_of_path"
    realized = ((exit_price - entry) / entry * 100) if is_long else ((entry - exit_price) / entry * 100)
    return {
        "exit_price": round(exit_price, 4), "reason": exit_reason,
        "realized_pct": round(realized, 2), "peak_pct": round(st.peak_pnl_pct, 2),
        "final_sl": ex.current_sl,
    }


async def scenario(title, aim, *, common, expect):
    print(f"\n=== {title} ===\n   aim: {aim}")
    fixed = await simulate(fixed=True, **common)
    legacy = await simulate(fixed=False, **{k: v for k, v in common.items() if k != "ride"})
    cap = (f"{round(fixed['realized_pct'] / fixed['peak_pct'] * 100)}% of peak"
           if fixed["peak_pct"] > 0 else "n/a")
    print(f"   FIXED : exit {fixed['reason']} @ {fixed['exit_price']} -> "
          f"{fixed['realized_pct']:+.2f}% (peak {fixed['peak_pct']:+.2f}%, captured {cap})")
    print(f"   LEGACY: exit {legacy['reason']} @ {legacy['exit_price']} -> "
          f"{legacy['realized_pct']:+.2f}% (peak {legacy['peak_pct']:+.2f}%)")
    ok = expect(fixed, legacy)
    print(f"   VERDICT: {'FIX WORKING' if ok else 'CHECK FAILED'}")
    return ok


async def main() -> int:
    results = []

    # 1. Round-tripping winner — the #1 problem. Climb +3% by min 22, fade to -1%.
    results.append(await scenario(
        "1. Round-tripping winner (ladder locks profit, kills the round-trip)",
        "ride the climb, lock a rising floor, exit near the peak — NOT round-trip to a loss",
        common=dict(price_path=_path([(0, 100.0), (22, 103.0), (40, 99.0)]),
                    entry=100.0, atr=0.6, max_hold=50, brain_sl=97.0, ride=True),
        # Fixed must end positive (locked profit) while legacy round-trips far worse.
        expect=lambda f, l: f["realized_pct"] > 0.5 and f["realized_pct"] > l["realized_pct"],
    ))

    # 2. Big runner — Chandelier captures the fast move. +6% fast by min 15, fade to +3%.
    results.append(await scenario(
        "2. Big runner (Chandelier rides the fast move)",
        "let a big fast winner run and capture most of it via the peak-anchored trail",
        common=dict(price_path=_path([(0, 100.0), (15, 106.0), (35, 103.0)]),
                    entry=100.0, atr=1.0, max_hold=50, brain_sl=97.0, ride=True),
        expect=lambda f, l: f["realized_pct"] > 2.0 and f["realized_pct"] > l["realized_pct"],
    ))

    # 3. ATR-zero hole — same climb/fade, but live ATR reads 0 from minute 10.
    results.append(await scenario(
        "3. ATR-zero hole (trail never vanishes — fallback protects)",
        "even with ATR reading zero, the trail still protects and exits in profit (not naked)",
        common=dict(price_path=_path([(0, 100.0), (22, 103.0), (40, 99.0)]),
                    entry=100.0, atr=0.6, max_hold=50, brain_sl=97.0, ride=True, atr_zero_from=10),
        expect=lambda f, l: f["realized_pct"] > 0.5 and f["reason"] == "stop_hit",
    ))

    # 4. Deadline winner — still climbing at the deadline (min 50), climbs to min 62, then fades.
    results.append(await scenario(
        "4. Deadline winner (ride past the deadline, do not guillotine)",
        "a still-climbing winner is NOT cut at the deadline; it rides and captures more",
        common=dict(price_path=_path([(0, 100.0), (50, 102.0), (62, 103.2), (75, 101.5)]),
                    entry=100.0, atr=0.5, max_hold=50, brain_sl=97.0, ride=True),
        # Fixed stays alive PAST where legacy cuts the winner (a legacy cutter —
        # the deadline guillotine or the +1.5% profit-take — fires first), so the
        # fixed run sees a higher peak and captures materially more.
        expect=lambda f, l: (f["peak_pct"] > l["peak_pct"] + 1.0
                             and f["realized_pct"] > l["realized_pct"] + 0.5),
    ))

    # 5. Naked position — no broker stop; price falls to -3%.
    results.append(await scenario(
        "5. Naked position (sweeper attaches a safety stop, caps the loss)",
        "a position with NO stop is given a safety stop and capped at ~-2.5% (not left naked)",
        common=dict(price_path=_path([(0, 100.0), (20, 96.9), (30, 96.9)]),
                    entry=100.0, atr=0.6, max_hold=50, brain_sl=None, ride=True),
        # Fixed caps tighter than a fully-naked legacy (which only the -3% hard stop would catch).
        expect=lambda f, l: f["reason"] == "stop_hit" and f["realized_pct"] > -2.9,
    ))

    # 6. Too-wide brain SL — non-climber; safety floor re-asserts tighter than a -4% brain SL.
    results.append(await scenario(
        "6. Re-assert floor (tighten a too-wide brain stop on a non-climber)",
        "a non-climber with a -4% brain stop is capped at the tighter -2.5% safety floor",
        common=dict(price_path=_path([(0, 100.0), (30, 95.4), (40, 95.4)]),
                    entry=100.0, atr=0.6, max_hold=50, brain_sl=96.0, ride=True),
        expect=lambda f, l: f["realized_pct"] > l["realized_pct"] and f["realized_pct"] > -2.9,
    ))

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r)
    print(f"SCENARIO SIMULATION: {passed}/{len(results)} fixes respond as intended")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
