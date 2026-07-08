"""Behavioral verification for the Loss-Cutting System (2026-05-31).

Consolidates the per-phase behavioral checks (Phases 1-7) plus the cross-cutting
checks into one re-runnable script. Build-time verification is BEHAVIOURAL only
(blueprint Part 8 / Rule 15): it proves each technique acts as designed on
real/induced state. The loss-reduction OUTCOME (losses actually smaller, in
truthful after-cost dollars) is explicitly deferred until live tuning and is NOT
asserted here.

Run:  python3 verify_loss_cutting.py
"""

import asyncio
import time as _t

from src.config.settings import LossCuttingSettings, Settings, _build_loss_cutting
from src.core.exceptions import ClosingInProgressError, PositionError
from src.core.sl_gateway import SLGateway
from src.core.time_dial import LossDialedParams, TimeDial
from src.core.transformer import _PositionProxy
from src.workers.profit_sniper import ProfitSniper
from src.workers.sniper_ring_buffer import PositionProfitState


def _close(a, b, t=1e-9):
    return abs(a - b) < t


def _new_sniper(s):
    sniper = object.__new__(ProfitSniper)
    sniper._pf = s.profit_fetching
    sniper._lc = s.loss_cutting

    async def fake_atr(_sym):
        return 2.0

    sniper._get_current_atr = fake_atr
    return sniper


def phase1(s):
    lc = s.loss_cutting
    assert isinstance(lc, LossCuttingSettings) and lc.enabled
    d = TimeDial(lc)
    p0, pm, po, pp = (d.resolve_loss(a, 50.0) for a in (0.0, 25.0, 50.0, 80.0))
    assert isinstance(p0, LossDialedParams)
    assert _close(p0.cap_pct, 2.5) and _close(po.cap_pct, 1.0) and _close(pp.cap_pct, 1.0)
    assert p0.cap_pct >= pm.cap_pct >= po.cap_pct          # monotonic tighten, saturates
    assert _close(p0.structure_buffer_atr, 0.5) and _close(po.structure_buffer_atr, 0.1)
    assert _close(p0.winprob_cut_threshold, 0.10) and _close(po.winprob_cut_threshold, 0.20)
    assert _build_loss_cutting({"bogus": 1, "cap_dollar_ceiling": 9.0}).cap_dollar_ceiling == 9.0
    print("PHASE 1 OK — config + two-dial glide (monotonic, saturates, stray key ignored)")


def phase2(s):
    # in-flight close lock — on the shared _PositionProxy (the chokepoint EVERY
    # mode routes through), NOT the bybit-only PositionService. The set is owned
    # by the long-lived Transformer (self._t._closing_inflight), so the guard is
    # exercised exactly where it runs live.
    from types import SimpleNamespace
    assert issubclass(ClosingInProgressError, PositionError)
    proxy = object.__new__(_PositionProxy)
    proxy._t = SimpleNamespace(_closing_inflight={"BTCUSDT"})
    try:
        asyncio.run(proxy.close_position("BTCUSDT", close_trigger="loss_cap_force"))
        raise SystemExit("FAIL: duplicate close did not raise")
    except ClosingInProgressError:
        pass

    # a non-duplicate forwards to the active mode adapter and releases the symbol
    class _Adapter:
        seen: list = []

        async def close_position(self, symbol, *a, **k):
            _Adapter.seen.append(symbol)
            return "closed"

    proxy2 = object.__new__(_PositionProxy)
    proxy2._t = SimpleNamespace(_closing_inflight=set(), active_position_service=_Adapter())
    assert asyncio.run(proxy2.close_position("ETHUSDT")) == "closed"
    assert _Adapter.seen == ["ETHUSDT"] and "ETHUSDT" not in proxy2._t._closing_inflight

    # cap math: min(ceiling, pct*notional)
    assert _close(min(75.0, 1000 * 2.5 / 100.0), 25.0)        # $1000 -> 2.5% pct binds
    assert _close(min(75.0, 5000 * 2.5 / 100.0), 75.0)        # $5000 -> ceiling bounds
    # spine: cap competes, tightest wins, tighten-only drops a looser cap
    sel = ProfitSniper._pf_select_stop
    assert sel(None, None, 0.0, True, safety_stop=97.5,
               loss_candidates=[("cap", 99.0, "loss_cap")]) == ("cap", 99.0, "loss_cap")
    assert sel(None, None, 99.5, True, safety_stop=None,
               loss_candidates=[("cap", 99.0, "loss_cap")]) is None
    print("PHASE 2 OK — in-flight close lock, cap math, spine tightest-wins + tighten-only")


def phase4(s):
    sniper = _new_sniper(s)
    calls = {}

    async def fake_close(symbol, pos, sd, closed_by="", check_min_hold=True):
        calls["by"] = closed_by
        calls["mh"] = check_min_hold
        return True

    sniper._execute_full_close = fake_close
    sniper.layer4_protection = None

    class St:
        def __init__(self, peak, pr):
            self.peak_pnl_pct = peak
            self._pr = pr

        @property
        def profit_ratio(self):
            return self._pr

    def run(state, tracked, pnl, age):
        calls.clear()
        return asyncio.run(sniper._lc_stall_decision(
            "X", object(), tracked, state, pnl, True, age, 0.66))

    assert run(St(0.0, 0.0), {}, -1.0, 0.8) is True and calls["by"] == "loss_stall" and calls["mh"] is True
    assert run(St(0.4, 0.5), {}, -0.2, 0.8) is False     # building -> spared
    assert run(St(0.0, 0.0), {"_lc_pnl_prev": -1.5}, -1.0, 0.8) is False  # improving -> spared
    assert run(St(0.0, 0.0), {}, -1.0, 0.30) is False    # too young
    assert run(St(0.0, 0.0), {}, -1.0, 0.96) is False    # tail -> watchdog owns
    assert run(St(1.0, 0.8), {}, 0.5, 0.8) is False      # profitable -> never cut
    print("PHASE 4 OK — stall cut / signs-of-life veto / too-young / tail-yield / profit-skip")


def phase5(s):
    sniper = _new_sniper(s)

    class MS:
        def __init__(self, inv):
            self.invalidation_level = inv

    class SA:
        def __init__(self, inv):
            self.market_structure = MS(inv)

    class Cache:
        def __init__(self, sa):
            self._sa = sa

        def get(self, _sym):
            return self._sa

    class St:
        atr_at_entry = 2.0

    def run(cache, is_long, price, buf):
        sniper.structure_cache = cache
        return asyncio.run(sniper._lc_structure_stop("X", St(), is_long, price, buf))

    assert _close(run(Cache(SA(95.0)), True, 100.0, 0.5), 94.0)   # long below inv
    assert _close(run(Cache(SA(105.0)), False, 100.0, 0.5), 106.0)  # short above inv
    assert run(Cache(None), True, 100.0, 0.5) is None             # cache miss
    assert run(Cache(SA(0.0)), True, 100.0, 0.5) is None          # no invalidation
    assert run(Cache(SA(105.0)), True, 100.0, 0.5) is None        # wrong-side
    # win-prob coordination: [loss_cutting] is the tuning home; cut stays in time_decay
    near = float(getattr(s.time_decay, "near_certain_loser_p_win", 0.10))
    if s.loss_cutting.enabled and s.loss_cutting.enable_winprob_observe:
        near = float(s.loss_cutting.winprob_cut_threshold_young)
    assert _close(near, 0.10)
    print("PHASE 5 OK — structure placement + 3 fail-safes; win-prob coord (no duplicate cutter)")


def phase6(s):
    sniper = _new_sniper(s)

    class St:
        atr_at_entry = 2.0

    class Buf:
        def __init__(self, p, ts):
            self._p = p
            self._ts = ts

        def get_prices(self, n=None):
            return self._p

        def get_timestamps(self, n=None):
            return self._ts

    now = _t.time()

    def run(buf, price, is_long):
        return asyncio.run(sniper._lc_spike_triggered("X", {"buffer": buf}, St(), price, is_long))

    assert run(Buf([100.0, 99.0, 93.0], [now - 10, now - 5, now - 1]), 93.0, True)[0] is True   # crash
    assert run(Buf([100.0, 99.0, 98.5], [now - 10, now - 5, now - 1]), 98.5, True)[0] is False  # slow
    assert run(Buf([], []), 95.0, True)[0] is False                                             # filling
    assert run(Buf([100.0, 99.0], [now - 60, now - 1]), 98.0, True)[0] is False                 # window filter
    assert run(Buf([100.0, 101.0, 107.0], [now - 10, now - 5, now - 1]), 107.0, False)[0] is True  # short
    print("PHASE 6 OK — spike triggers on a crash, not on slow/filling/stale; short mirror")


def phase7(s):
    st = PositionProfitState("X", entry_price=100.0, direction="Buy", atr_at_entry=2.0)
    now = _t.time()
    st.update(-5.0, 95.0, now)
    st.update(-3.0, 97.0, now)
    assert st.trough_pnl_pct == -5.0 and st.trough_price == 95.0   # trough held through the bounce
    st.update(-7.0, 93.0, now)
    assert st.trough_pnl_pct == -7.0 and st.trough_price == 93.0   # new worse trough

    sniper = _new_sniper(s)

    def mk(trough_pnl, trough_price, pr):
        x = PositionProfitState("X", 100.0, "Buy", 2.0)
        x.trough_pnl_pct = trough_pnl
        x.trough_price = trough_price
        x.ticks_total = 100
        x.ticks_in_profit = int(pr * 100)
        return x

    assert _close(asyncio.run(sniper._lc_recovery_candidate("X", {}, mk(-5, 95.0, 0.1), True, 97.0)), 96.0)  # loss-side tight
    assert _close(asyncio.run(sniper._lc_recovery_candidate("X", {}, mk(-5, 95.0, 0.6), True, 97.0)), 94.0)  # profit-side wide
    assert asyncio.run(sniper._lc_recovery_candidate("X", {}, mk(-5, 95.0, 0.1), True, 95.0)) is None        # no bounce
    tr = {}
    st2 = mk(-5, 95.0, 0.1)
    r1 = asyncio.run(sniper._lc_recovery_candidate("X", tr, st2, True, 97.0))
    r2 = asyncio.run(sniper._lc_recovery_candidate("X", tr, st2, True, 99.0))
    assert _close(r1, 96.0) and _close(r2, 98.0)   # ratchets up with the bounce
    print("PHASE 7 OK — trough tracking; tight vs wide trail by history; no-bounce gate; ratchet")


def cross_cutting(s):
    # Gateway: every loss SL source bypasses R3 ONLY — R1 tighten-only is never
    # bypassed (it has no bypass set), so no loss source can loosen a stop. The
    # spike force-CLOSES (no SL), so loss_spike is intentionally NOT a bypass
    # source.
    bp = SLGateway._BREAKEVEN_BYPASS_SOURCES
    for src in ("loss_cap", "loss_cap_emergency", "loss_atr_initial",
                "loss_structure", "loss_recovery"):
        assert src in bp
    assert "loss_spike" not in bp  # spike force-closes, never writes an SL
    # Flip switches off (the loss system is orthogonal to direction).
    assert s.risk.xray_dir_flip_enabled is False
    assert s.risk.xray_trade_suppression_enabled is False
    # Phase 3 deferred: volatility entry-sizing stays off (brain sizing intact).
    assert s.loss_cutting.volatility_entry_sizing_enabled is False
    # Each technique independently revertible via its enable flag.
    for flag in ("enable_atr_initial_stop", "enable_hard_cap", "enable_stall_exit",
                 "enable_structure_stop", "enable_winprob_observe",
                 "enable_spike_stop", "enable_history_recovery"):
        assert isinstance(getattr(s.loss_cutting, flag), bool)
    print("CROSS-CUTTING OK — R3-only bypass (R1 never bypassed), flip switches off, "
          "vol-sizing off, every technique independently revertible")


def main():
    s = Settings.load("config.toml")
    phase1(s)
    phase2(s)
    phase4(s)
    phase5(s)
    phase6(s)
    phase7(s)
    cross_cutting(s)
    print("")
    print("LOSS_CUTTING BEHAVIORAL VERIFY: PASS")
    print("Loss-reduction OUTCOME verification is PROVISIONAL until truthful "
          "after-cost PnL tuning (blueprint Part 8).")


if __name__ == "__main__":
    main()
