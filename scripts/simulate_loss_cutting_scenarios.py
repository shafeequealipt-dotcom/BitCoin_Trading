"""Scenario simulation for the LOSS-CUTTING SYSTEM — recreates the real adverse
situations each fix targets and plays them tick-by-tick through the REAL system,
scoring each response against its aim (FIX WORKING / CHECK FAILED).

Real objects used (only the exchange + price + structure source are simulated at
the boundary):
  Settings + LossCuttingSettings, SLGateway (all four rules + the loss R3-bypass
  sources), TimeDial.resolve_loss, the ProfitSniper spine + every loss helper
  (_pf_apply_spine, _pf_select_stop, _lc_place_initial_atr_stop, _lc_stall_decision,
  _lc_structure_stop, _lc_spike_triggered, _lc_recovery_candidate,
  _execute_full_close), PositionProfitState (real peak/trough), the transformer
  _PositionProxy close-lock.

A SimExchange holds the stop the gateway writes; when price crosses it the
position closes there. A force-close routes through the real _execute_full_close
into a recording SimCoordinator. Each tick is one minute; the trade plan's
opened_at is back-dated so the loss time-dial sees the intended age.

Run:  PYTHONPATH=. python scripts/simulate_loss_cutting_scenarios.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.core.trade_plan import TradePlan
from src.core.types import Side
from src.workers.profit_sniper import ProfitSniper
from src.workers.sniper_ring_buffer import EnhancedRingBuffer, PositionProfitState


class SimExchange:
    """Boundary stub — holds the SL and records a close. No network."""

    def __init__(self, initial_sl):
        self.current_sl = initial_sl  # None == naked
        self.closed = None

    async def set_stop_loss(self, symbol, sl):
        self.current_sl = round(float(sl), 8)
        return True

    async def close_position(self, symbol, *, purpose="layer4_close",
                             close_trigger="system_close"):
        self.closed = {"symbol": symbol, "trigger": close_trigger}
        return SimpleNamespace(order_id="sim", symbol=symbol)

    async def get_position(self, symbol):
        return None


class SimCoordinator:
    """Records the authoritative close booking — the single close authority."""

    def __init__(self, plan):
        self._plan = plan
        self.booked = None

    def get_trade_plan(self, symbol):
        return self._plan

    def remove_trade_plan(self, symbol):
        self._plan = None

    async def resolve_authoritative_pnl(self, *, symbol, position_service,
                                        fallback_pnl_usd, fallback_pnl_pct):
        return (fallback_pnl_usd, fallback_pnl_pct, "sim_authoritative", None)

    def on_trade_closed(self, *, symbol, pnl_pct, pnl_usd, was_win,
                        closed_by, exit_price=None, price_source=None):
        self.booked = {"symbol": symbol, "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                       "closed_by": closed_by, "was_win": was_win}


class SimL4:
    """layer4_protection stub — permissive so the close path runs; the guard
    behaviour itself is unit-tested elsewhere."""

    async def is_protected(self, **kwargs):
        return SimpleNamespace(protected=False, reason="sim")

    def get_struct_guard_verdict(self, symbol):
        return ("", 0.0)


class SimStructureCache:
    def __init__(self, invalidation_level):
        self._inv = invalidation_level

    def get(self, symbol):
        if self._inv is None:
            return None
        return SimpleNamespace(
            market_structure=SimpleNamespace(invalidation_level=self._inv)
        )


def _plan(entry, sl, max_hold, direction):
    # Real TradePlan so age_minutes / expires_at behave exactly as in production.
    p = TradePlan(symbol="SIM", direction=direction, entry_price=entry,
                  target_price=0.0, stop_loss_price=(sl or 0.0),
                  max_hold_minutes=max_hold)
    p.opened_at = time.time()
    return p


def _path(segments):
    out = []
    for i in range(len(segments) - 1):
        (t0, p0), (t1, p1) = segments[i], segments[i + 1]
        for t in range(t0, t1):
            out.append(p0 + (p1 - p0) * (t - t0) / (t1 - t0))
    out.append(segments[-1][1])
    return out


def _mk_sniper(settings, ex, plan, structure_inv=None):
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    sniper.sl_gateway = SLGateway(settings=settings, position_service=ex,
                                  market_service=SimpleNamespace(),
                                  volatility_profiler=None)
    sniper.position_service = ex
    sniper.trade_coordinator = SimCoordinator(plan)
    sniper.layer4_protection = SimL4()
    sniper.event_buffer = None
    sniper.structure_cache = SimStructureCache(structure_inv)
    return sniper


async def simulate(price_path, *, entry, atr, max_hold, direction="Buy",
                   size, brain_sl, structure_inv=None, spike_at=None):
    settings = Settings.load(config_path="config.toml")
    is_long = direction == "Buy"
    ex = SimExchange(brain_sl)
    plan = _plan(entry, brain_sl, max_hold, direction)
    sniper = _mk_sniper(settings, ex, plan, structure_inv)
    sym = "SIM"
    gw = sniper.sl_gateway

    st = PositionProfitState(symbol=sym, entry_price=entry, direction=direction,
                             atr_at_entry=atr, opened_at=time.time())
    sniper._profit_states[sym] = st
    buf = EnhancedRingBuffer(symbol=sym, max_size=720, min_ready=1)
    tracked = {"buffer": buf, "first_seen_at": time.time(),
               "position": None, "_last_escape_type": "", "_last_escape_tick": 0}
    sniper._tracked[sym] = tracked

    # ATR-at-entry initial stop (Technique 1) — placed once at "open".
    pos0 = SimpleNamespace(symbol=sym, stop_loss=ex.current_sl or 0.0,
                           side=Side.BUY if is_long else Side.SELL,
                           size=size, entry_price=entry, mark_price=entry,
                           unrealized_pnl=0.0)
    init_log = None
    try:
        await sniper._lc_place_initial_atr_stop(sym, pos0, atr)
        init_log = ex.current_sl
    except Exception as e:  # noqa: BLE001
        init_log = f"err:{e}"

    def pnl_of(p):
        return ((p - entry) / entry * 100) if is_long else ((entry - p) / entry * 100)

    exit_price = exit_reason = None
    grad_logged_tick = None
    prev_price = entry
    for t, price in enumerate(price_path):
        now = time.time()
        plan.opened_at = now - t * 60.0  # back-date so age_minutes == t
        cur_sl = ex.current_sl
        pnl = pnl_of(price)

        # exchange stop trigger (a written SL exits here)
        if cur_sl and cur_sl > 0 and ((is_long and price <= cur_sl)
                                      or (not is_long and price >= cur_sl)):
            exit_price, exit_reason = cur_sl, "stop_hit"
            break

        st.update(pnl, price, now)
        # Spike window model: a spike is a large move within ~30s. Each tick is
        # one minute, so the last 30s = [previous-tick price, current price]. A
        # gradual minute-scale drift looks ~flat within the window (no false
        # spike); only a sharp single-tick move reads as a spike.
        buf.clear()
        buf.add_point(SimpleNamespace(timestamp=now - 29.0, price=prev_price,
                                      bid=prev_price, ask=prev_price, atr_current=atr))
        buf.add_point(SimpleNamespace(timestamp=now, price=price,
                                      bid=price, ask=price, atr_current=atr))
        prev_price = price

        pos = SimpleNamespace(symbol=sym, stop_loss=cur_sl or 0.0,
                              side=Side.BUY if is_long else Side.SELL,
                              size=size, entry_price=entry, mark_price=price,
                              unrealized_pnl=pnl / 100.0 * size * entry)
        tracked["position"] = pos
        gw._last_change.pop(sym, None)  # compressed time -> R4 eligible

        graduated_before = st.peak_pnl_pct >= settings.profit_fetching.min_profit_to_arm_ladder_pct
        await sniper._pf_apply_spine(sym, pos, tracked, price)
        if (grad_logged_tick is None and graduated_before
                and tracked.get("_lc_graduated_logged")):
            grad_logged_tick = t

        if ex.closed is not None:  # a force-close fired
            exit_price, exit_reason = price, ex.closed["trigger"]
            break

    if exit_price is None:
        exit_price, exit_reason = price_path[-1], "end_of_path"
    realized = (((exit_price - entry) / entry * 100) if is_long
                else ((entry - exit_price) / entry * 100))
    booked = sniper.trade_coordinator.booked
    return {
        "exit_price": round(exit_price, 6), "reason": exit_reason,
        "realized_pct": round(realized, 3), "peak_pct": round(st.peak_pnl_pct, 3),
        "trough_pct": round(st.trough_pnl_pct, 3), "final_sl": ex.current_sl,
        "init_stop": init_log, "booked_by": (booked or {}).get("closed_by"),
        "graduated": st.peak_pnl_pct >= settings.profit_fetching.min_profit_to_arm_ladder_pct,
    }


async def scenario(n, title, aim, *, common, expect):
    r = await simulate(**common)
    ok = expect(r)
    print(f"\n=== {n}. {title} ===")
    print(f"   aim    : {aim}")
    print(f"   result : exit={r['reason']} @ {r['exit_price']} -> {r['realized_pct']:+.3f}% "
          f"(peak {r['peak_pct']:+.2f}%, trough {r['trough_pct']:+.2f}%) "
          f"final_sl={r['final_sl']} booked_by={r['booked_by']}")
    print(f"   verdict: {'FIX WORKING' if ok else 'CHECK FAILED'}")
    return ok, title


async def double_close_race():
    """The close-lock chokepoint: two cutters race the same symbol; exactly one
    forwards, the other is rejected (ClosingInProgressError) — no double close."""
    from src.core.exceptions import ClosingInProgressError
    from src.core.transformer import _PositionProxy
    calls = []

    class _Adapter:
        async def close_position(self, symbol, *a, **k):
            await asyncio.sleep(0.01)
            calls.append(symbol)
            return "closed"

    t = SimpleNamespace(_closing_inflight=set(), active_position_service=_Adapter())
    proxy = object.__new__(_PositionProxy)
    proxy._t = t
    results = await asyncio.gather(
        proxy.close_position("SIM", close_trigger="loss_cap_force"),
        proxy.close_position("SIM", close_trigger="wd_hard_stop"),
        return_exceptions=True,
    )
    forwarded = sum(1 for r in results if r == "closed")
    rejected = sum(1 for r in results if isinstance(r, ClosingInProgressError))
    ok = forwarded == 1 and rejected == 1 and calls == ["SIM"] and "SIM" not in t._closing_inflight
    print("\n=== 10. Double-close race (in-flight lock prevents the duplicate close) ===")
    print("   aim    : two cutters race the same symbol; exactly one close fires, the other is skipped")
    print(f"   result : forwarded={forwarded} rejected_ClosingInProgress={rejected} "
          f"adapter_calls={calls} released={'SIM' not in t._closing_inflight}")
    print(f"   verdict: {'FIX WORKING' if ok else 'CHECK FAILED'}")
    return ok, "Double-close race"


async def _focused(*, brain_sl, price, entry, atr, size, max_hold=50,
                   age_min=1.0, structure_inv=None, spike_window=None):
    """Drive a single REAL _pf_apply_spine tick for the force-close mechanisms
    that an SL-at-cap would otherwise pre-empt in a tick loop."""
    settings = Settings.load(config_path="config.toml")
    ex = SimExchange(brain_sl)
    plan = _plan(entry, brain_sl, max_hold, "Buy")
    sniper = _mk_sniper(settings, ex, plan, structure_inv)
    sym = "SIM"
    now = time.time()
    plan.opened_at = now - age_min * 60.0
    st = PositionProfitState(symbol=sym, entry_price=entry, direction="Buy",
                             atr_at_entry=atr, opened_at=now - age_min * 60.0)
    sniper._profit_states[sym] = st
    buf = EnhancedRingBuffer(symbol=sym, max_size=720, min_ready=1)
    tracked = {"buffer": buf, "first_seen_at": now - age_min * 60.0,
               "position": None, "_last_escape_type": "", "_last_escape_tick": 0}
    sniper._tracked[sym] = tracked
    pnl = (price - entry) / entry * 100
    st.update(pnl, price, now)
    for secs_ago, p in (spike_window or [(0.0, price)]):
        buf.add_point(SimpleNamespace(timestamp=now - secs_ago, price=p,
                                      bid=p, ask=p, atr_current=atr))
    pos = SimpleNamespace(symbol=sym, stop_loss=brain_sl or 0.0, side=Side.BUY,
                          size=size, entry_price=entry, mark_price=price,
                          unrealized_pnl=pnl / 100.0 * size * entry)
    tracked["position"] = pos
    sniper.sl_gateway._last_change.pop(sym, None)
    await sniper._pf_apply_spine(sym, pos, tracked, price)
    return {"closed": (ex.closed or {}).get("trigger"),
            "booked_by": (sniper.trade_coordinator.booked or {}).get("closed_by"),
            "final_sl": ex.current_sl, "realized_pct": round(pnl, 3)}


async def cap_force_close():
    """Cap force-close: a position whose loss is already past the cap, with no
    placeable tighter SL, is closed by the inviolable wall (loss_cap_force).
    notional 10*100=$1000 -> cap=min(75, 2.5%*1000=25)=$25; price 97 -> loss $30 > $25."""
    r = await _focused(brain_sl=None, price=97.0, entry=100.0, atr=0.6, size=10.0)
    ok = r["closed"] == "loss_cap_force"
    print("\n=== 7. Sacred-cap force-close (inviolable wall when no SL can hold the cap) ===")
    print("   aim    : when the loss reaches the cap and no tighter SL holds, force-close (cap never exceeded)")
    print(f"   result : closed_by={r['closed']} realized={r['realized_pct']:+.2f}% (cap=$25 on a $1000 position)")
    print(f"   verdict: {'FIX WORKING' if ok else 'CHECK FAILED'}")
    return ok, "Sacred-cap force-close"


async def spike_force_close():
    """Spike catastrophe stop: a 2-minute-old trade crashes ~7 ATR within the 30s
    window -> force-close age-independently (catastrophe insurance for young trades)."""
    r = await _focused(brain_sl=None, price=95.0, entry=100.0, atr=0.6, size=10.0,
                       age_min=2.0, spike_window=[(12.0, 99.6), (0.0, 95.0)])
    ok = r["closed"] == "loss_spike_force"
    print("\n=== 8. Volatility-spike catastrophe stop (young-trade crash, age-independent) ===")
    print("   aim    : a violent adverse move on a 2-min-old trade is force-closed fast (not held)")
    print(f"   result : closed_by={r['closed']} realized={r['realized_pct']:+.2f}% age=2min")
    print(f"   verdict: {'FIX WORKING' if ok else 'CHECK FAILED'}")
    return ok, "Spike catastrophe stop"


async def main() -> int:
    results = []
    hold = 50  # deadline minutes

    # 1. Flat-fader (never climbs) — the main loss population. Drifts to -1% and sits.
    results.append(await scenario(
        1, "Flat-fader never climbs (stall-exit cuts the dead non-climber)",
        "a position with no signs of life past the stall age is cut (frees capital)",
        common=dict(price_path=_path([(0, 100.0), (10, 99.0), (50, 99.0)]),
                    entry=100.0, atr=0.5, max_hold=hold, size=10.0, brain_sl=96.0),
        expect=lambda r: r["reason"] == "loss_stall" and r["realized_pct"] < 0,
    ))

    # 2. Late-bloomer — slightly building (small green ticks), then surges past arm.
    results.append(await scenario(
        2, "Late-bloomer spared then graduates (signs-of-life veto)",
        "a slightly-building trade is NOT stall-cut; it survives and graduates to the profit side",
        common=dict(price_path=_path([(0, 100.0), (20, 100.2), (35, 100.05),
                                      (45, 103.0)]),
                    entry=100.0, atr=0.5, max_hold=hold, size=10.0, brain_sl=96.0),
        expect=lambda r: r["reason"] != "loss_stall" and r["graduated"],
    ))

    # 3. Cap binds via the SL — wild-ish coin (atr 0.9) where the volatility-sized
    #    ATR stop would be WIDER than the cap, so the cap (2.5% of $1000 = $25 =
    #    97.5) is the binding protection. Gradual drop (no spike).
    results.append(await scenario(
        3, "Sacred cap binds and tightens with age (loss held well within -3%)",
        "the cap is the binding protection and glides tighter with age, so the loss is bounded inside the ceiling",
        common=dict(price_path=_path([(0, 100.0), (18, 96.0), (30, 95.0)]),
                    entry=100.0, atr=0.9, max_hold=hold, size=10.0, brain_sl=96.0),
        # The cap holds via either the cap-distance SL or the age-tightening
        # force-close; either way the realized loss is bounded inside the young
        # 2.5% cap and never reaches the -3% backstop.
        expect=lambda r: r["reason"] in ("stop_hit", "loss_cap_force")
        and -2.6 <= r["realized_pct"] < 0,
    ))

    # 4. Naked position (no broker stop) — initial-stop/sweeper attaches protection.
    results.append(await scenario(
        4, "Naked position (a stop is attached, loss capped — never left naked)",
        "a position with NO broker stop is given one and the loss is capped (not naked to -3%)",
        common=dict(price_path=_path([(0, 100.0), (20, 96.5), (35, 96.5)]),
                    entry=100.0, atr=0.6, max_hold=hold, size=10.0, brain_sl=None),
        expect=lambda r: r["final_sl"] is not None and r["realized_pct"] > -3.0,
    ))

    # 5. Structural breakdown — invalidation at 99.2; the structure stop (just
    #    below it, ~99.0) is TIGHTER than the generic ATR stop, so it wins and
    #    protects the thesis level. Gradual drift (no spike).
    results.append(await scenario(
        5, "Structure stop (protect just beyond the X-RAY invalidation level)",
        "a long is protected by a stop placed just below the structural invalidation level (it wins the spine)",
        common=dict(price_path=_path([(0, 100.0), (25, 98.8), (40, 98.5)]),
                    entry=100.0, atr=0.4, max_hold=hold, size=10.0, brain_sl=95.0,
                    structure_inv=99.2),
        expect=lambda r: r["reason"] == "stop_hit" and r["final_sl"] is not None
        and 98.85 <= r["final_sl"] <= 99.15,  # stop sits just below invalidation 99.2
    ))

    # 6. Winner — climbs past the arm threshold; the loss system must YIELD.
    results.append(await scenario(
        6, "Winner graduates (loss system yields to the profit system)",
        "once the trade is meaningfully green the loss-cutting candidates/cuts stop; profit owns it",
        common=dict(price_path=_path([(0, 100.0), (10, 101.5), (30, 101.0)]),
                    entry=100.0, atr=0.5, max_hold=hold, size=10.0, brain_sl=98.0),
        expect=lambda r: r["graduated"] and r["booked_by"] is None
        and r["reason"] != "loss_cap_force" and r["reason"] != "loss_stall",
    ))

    # 7-8. Force-close mechanisms (single-shot, since an SL-at-cap would pre-empt
    #      them in a tick loop): the sacred-cap force-close + the spike catastrophe.
    results.append(await cap_force_close())
    results.append(await spike_force_close())

    # 9. Double-close race (the in-flight close lock at the real chokepoint).
    results.append(await double_close_race())

    print("\n" + "=" * 64)
    passed = sum(1 for ok, _ in results if ok)
    for ok, title in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {title}")
    print(f"\nLOSS-CUTTING SCENARIO SIMULATION: {passed}/{len(results)} respond as intended")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
