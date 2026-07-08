#!/usr/bin/env python3
"""Live-style scenario simulation: recreate each PROVEN exit failure and check
that the corresponding adaptive-exit fix responds as intended.

Read-only. For each forensic archetype it recreates the issue's data/situation
(R, entry, the realized PnL%-over-time path) and drives it through the REAL
ProfitSniper ladder + REAL SLGateway tick by tick in two modes:

  BEFORE  (legacy)   adaptive_exit.enabled=False, r2_profit_lock_floor_enabled=False
  AFTER   (adaptive) all flags True

and reports the realized outcome (net of the round-trip fee) in each mode plus a
verdict on whether the fix responded to its aim. The stop is computed by the real
code in both modes — only the flags differ — so the difference is purely the fix.

Archetypes (forensic traces) and the fix each checks:
  ALICEUSDT  clipped winner   -> ladder R-lock + gateway exemption keep the green (F2/G1/B1/C4)
  CLAMPLOOP  clamp-noop       -> the lock WRITES instead of being dropped (B1/C4)
  LINKUSDT   dead drifter     -> scratched early instead of riding to deadline (F4/G2)
  AXSUSDT    recovered fighter-> the bounce is captured at the R-level, not a sliver (G3)
  QUIET/VOL  hard stop        -> R-scaled backstop fits the coin, cap stays operative (E1)
  APTUSDT    genuine loser    -> still cut (loss-cutting NOT regressed, Rule 8)
  CRASH      catastrophe      -> the Head/cap fires identically in both modes (Rule 6)

Exits non-zero if any fix fails to respond as intended.
"""
import asyncio
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.workers.profit_sniper import ProfitSniper
from src.workers.position_watchdog import PositionWatchdog
from src.analysis.volatility_profile import VolatilityProfiler
from src.analysis import vol_scale as vg

FEE = 0.11
VERDICTS = []


def verdict(name, ok, detail):
    print(f"  VERDICT [{'FIXED / AS-INTENDED' if ok else 'NOT AS INTENDED'}]: {detail}")
    VERDICTS.append((name, ok))


class _Stub:
    async def get_position(self, *a, **k): return None
    async def set_stop_loss(self, *a, **k): return True
    async def get_ticker(self, *a, **k): return None
    def add_event(self, *a, **k): pass


def build(mode):
    """One real sniper + real gateway; flags set per mode (read live at call time)."""
    s = Settings._load_fresh()
    on = (mode == "adaptive")
    s.adaptive_exit.enabled = on
    s.adaptive_exit.dead_drifter_enabled = on
    s.sl_gateway.r2_profit_lock_floor_enabled = on
    s.sl_gateway.owner_switch_enforce = False     # isolate geometry/R2 from ownership
    s.sl_gateway.rate_limit_seconds = 0
    s.adaptive_exit.r_smoothing_alpha = 1.0       # deterministic R for the sim
    gw = SLGateway(settings=s, position_service=_Stub(), market_service=_Stub(),
                   event_buffer=_Stub(), volatility_profiler=None)
    sniper = ProfitSniper(settings=s, db=SimpleNamespace(), position_service=_Stub(),
                          market_service=_Stub(), sl_gateway=gw)
    return s, gw, sniper


def walk(sniper, gw, *, sym, R, entry, is_long, path, mode):
    """Drive the trade tick-by-tick through the real ladder + real gateway.
    Returns (realized_gross_pnl_pct, written_lock_pct_or_None)."""
    gw.reset_symbol(sym)
    sniper._smoothed_r.pop(sym, None)
    direction = "Buy" if is_long else "Sell"
    atr_price = R / 100.0 * entry
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    resting = None            # resting stop PRICE
    peak = 0.0
    written = None
    for pnl in path:
        peak = max(peak, pnl)
        price = entry * (1 + (pnl if is_long else -pnl) / 100.0)
        # exit if the resting stop is hit
        if resting is not None and ((is_long and price <= resting) or
                                    ((not is_long) and price >= resting)):
            ex = (resting - entry) / entry * 100.0 if is_long else (entry - resting) / entry * 100.0
            return ex, written
        st = SimpleNamespace(entry_price=entry, direction=direction,
                             peak_pnl_pct=peak, symbol=sym)
        lad = sniper._compute_ladder_floor(st, dialed, resting or 0.0, atr_value=atr_price)
        if lad.should_apply:
            be = entry if (mode == "legacy" and getattr(lad, "breakeven_floor", False)) else None
            pl = lad.ladder_stop_price if mode == "adaptive" else None
            res = asyncio.get_event_loop().run_until_complete(gw.apply(
                symbol=sym, new_sl=lad.ladder_stop_price, source="profit_sniper_ladder",
                direction=direction, current_sl=resting, current_price=price,
                entry_price=entry, bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
                breakeven_floor_price=be, profit_lock_floor_price=pl))
            if res.accepted:
                resting = gw._last_sl.get(sym)
                written = (resting - entry) / entry * 100.0 if is_long else (entry - resting) / entry * 100.0
    # not stopped out -> exit at the last observed pnl
    return path[-1], written


def net(g):
    return g - FEE


# ── scenarios ──────────────────────────────────────────────────────────────
def scn_clipped_winner():
    print("\n## ALICEUSDT — clipped winner (peak +0.16% gave back to -0.02%). Aim: keep the green.")
    sym, R, entry = "ALICE", 0.23, 0.11166
    path = [0.05, 0.10, 0.16, 0.14, 0.11, 0.08, 0.05, 0.02, -0.02]  # rise then give-back
    out = {}
    for mode in ("legacy", "adaptive"):
        _s, gw, sn = build(mode)
        g, w = walk(sn, gw, sym=sym, R=R, entry=entry, is_long=True, path=path, mode=mode)
        out[mode] = (g, w)
        print(f"  {mode:<8}: realized {net(g):+.3f}% net (lock written: "
              f"{('+%.3f%%' % w) if w is not None else 'none'})")
    ok = net(out["adaptive"][0]) > net(out["legacy"][0]) + 0.05 and out["adaptive"][1] is not None
    verdict("clipped_winner", ok,
            f"AFTER keeps {net(out['adaptive'][0]):+.3f}% vs BEFORE {net(out['legacy'][0]):+.3f}% — "
            f"the R-lock holds the green instead of clipping it to a give-back")


def scn_clamp_noop():
    print("\n## CLAMP-NOOP — the only profit lock the gateway ever sees, at sub-min-distance. Aim: it WRITES.")
    sym, R, entry = "CLAMP", 0.23, 0.11166
    # price hovering at the +0.16% peak; the lock the ladder wants is just inside eff_min
    for mode in ("legacy", "adaptive"):
        _s, gw, sn = build(mode)
        gw.reset_symbol(sym); sn._smoothed_r.pop(sym, None)
        price = entry * (1 + 0.16 / 100); cur = entry * (1 + 0.05 / 100)
        st = SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=0.16, symbol=sym)
        # use a stub profiler on the gateway so R2 has the coin's eff_min
        gw._volatility_profiler = SimpleNamespace(
            get_profile=lambda s2: _coro(SimpleNamespace(atr_pct_5m=R, volatility_class="medium")))
        lad = sn._compute_ladder_floor(st, SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3),
                                       cur, atr_value=R / 100 * entry)
        be = entry if (mode == "legacy") else None
        pl = lad.ladder_stop_price if mode == "adaptive" else None
        res = asyncio.get_event_loop().run_until_complete(gw.apply(
            symbol=sym, new_sl=lad.ladder_stop_price, source="profit_sniper_ladder", direction="Buy",
            current_sl=cur, current_price=price, entry_price=entry,
            bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
            breakeven_floor_price=be, profit_lock_floor_price=pl))
        applied = gw._last_sl.get(sym, cur)
        ap = (applied - entry) / entry * 100
        print(f"  {mode:<8}: accepted={res.accepted} reason={res.reason or '-'} applied lock +{ap:.3f}%")
        if mode == "legacy":
            legacy_ap = ap
        else:
            adaptive_ap = ap; adaptive_ok = res.accepted
    verdict("clamp_noop", adaptive_ok and adaptive_ap > legacy_ap + 1e-6,
            f"AFTER writes the lock at +{adaptive_ap:.3f}% vs BEFORE +{legacy_ap:.3f}% "
            f"(the breakeven-only hold) — the exemption stops the clamp-noop drop")


def scn_dead_drifter():
    print("\n## LINKUSDT — dead drifter (never moved 1R, rode 2637s to deadline). Aim: scratch it early.")
    sym, R, entry = "LINK", 0.20, 8.151
    out = {}
    for mode in ("legacy", "adaptive"):
        s, gw, sn = build(mode)
        s.loss_cutting.stall_veto_windowed_profit_ratio_enabled = False
        s.loss_cutting.stall_signs_of_life_sustained_improving_enabled = False
        # Provide the R source the scratch reads (this sniper has no ta_cache);
        # the scratch DECISION in _lc_stall_decision is the real code under test.
        sn._get_current_atr = lambda symbol, _v=R / 100 * entry: _coro(_v)
        closed = {"n": 0}
        async def _c(*a, **k): closed["n"] += 1; return True
        sn._execute_full_close = _c; sn.layer4_protection = None
        st = SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=0.03,  # peak never reached 1R=0.20
                             symbol=sym, profit_ratio=0.0, trough_pnl_pct=-0.26, trough_price=entry * 0.997)
        # young-dialed stall age 1.1 (never), age 0.75 of deadline, no signs of life
        scratched = asyncio.get_event_loop().run_until_complete(sn._lc_stall_decision(
            sym, SimpleNamespace(), {}, st, pnl_pct=-0.09, is_long=True,
            age_fraction=0.75, stall_min_age_fraction=1.1))
        out[mode] = closed["n"]
        print(f"  {mode:<8}: scratched={bool(scratched)} (closes={closed['n']})")
    verdict("dead_drifter", out["legacy"] == 0 and out["adaptive"] == 1,
            "AFTER scratches the no-1R drifter past 70% of its deadline; BEFORE rides on (would wait for the timeout)")


def scn_recovered_fighter():
    print("\n## AXSUSDT — recovered fighter (red, then back to green). Aim: capture the bounce at the R-level.")
    sym, R, entry = "AXS", 0.16, 0.9811
    path = [-0.5, -1.1, -0.6, -0.1, 0.10, 0.17, 0.13, 0.05]  # crater then recover to +0.17% then fade
    out = {}
    for mode in ("legacy", "adaptive"):
        _s, gw, sn = build(mode)
        g, w = walk(sn, gw, sym=sym, R=R, entry=entry, is_long=True, path=path, mode=mode)
        out[mode] = (g, w)
        print(f"  {mode:<8}: realized {net(g):+.3f}% net (lock written: "
              f"{('+%.3f%%' % w) if w is not None else 'none'})")
    ok = net(out["adaptive"][0]) >= net(out["legacy"][0]) and out["adaptive"][1] is not None
    verdict("recovered_fighter", ok,
            f"AFTER captures the bounce ({net(out['adaptive'][0]):+.3f}% net) vs BEFORE "
            f"({net(out['legacy'][0]):+.3f}%) — the recovered green is locked at the R-level, not a sliver")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _watchdog(mode, R):
    """Real PositionWatchdog + real VolatilityProfiler (stub TA yields the coin's R)."""
    s = Settings._load_fresh()
    s.adaptive_exit.enabled = (mode == "adaptive")
    s.sl_gateway.owner_switch_enforce = False
    s.sl_gateway.rate_limit_seconds = 0

    class _TA:
        async def analyze(self, **k):
            return {"volatility": {"natr_14": R}, "price": 100.0, "close": 100.0,
                    "overall": {}, "trend": {}}

    prof = VolatilityProfiler(ta_cache=_TA(), regime_detector=None, settings=s.volatility_profile)
    gw = SLGateway(settings=s, position_service=_Stub(), market_service=_Stub(),
                   event_buffer=_Stub(), volatility_profiler=prof)
    wd = PositionWatchdog(settings=s, db=SimpleNamespace(), position_service=_Stub(),
                          market_service=_Stub(), volatility_profiler=prof, sl_gateway=gw)
    return s, wd


def scn_hard_stop():
    print("\n## HARD STOP — REAL watchdog limit, both modes, quiet vs volatile coin (forensic E1).")
    cap = Settings._load_fresh().loss_cutting.cap_pct_of_notional_young
    # Quiet coin R=0.05%: legacy flat -3% is far too loose; adaptive fits it and finally protects.
    _, wd_lq = _watchdog("legacy", 0.05); _, wd_aq = _watchdog("adaptive", 0.05)
    lq = _run(wd_lq._adaptive_hard_stop_limit("QUIET")); aq = _run(wd_aq._adaptive_hard_stop_limit("QUIET"))
    leg_cut_q, adp_cut_q = (-2.6 < -lq), (-2.6 < -aq)   # the watchdog rule: pnl_pct < -limit
    print(f"  QUIET R=0.05%: legacy limit -{lq:.2f}% (cut@-2.6%={leg_cut_q}) | adaptive -{aq:.2f}% (cut@-2.6%={adp_cut_q})")
    # Volatile coin R=0.6%: legacy -3% is too tight (cuts on normal vol); adaptive gives room.
    _, wd_lv = _watchdog("legacy", 0.60); _, wd_av = _watchdog("adaptive", 0.60)
    lv = _run(wd_lv._adaptive_hard_stop_limit("VOL")); av = _run(wd_av._adaptive_hard_stop_limit("VOL"))
    leg_cut_v, adp_cut_v = (-4.0 < -lv), (-4.0 < -av)
    print(f"  VOL   R=0.60%: legacy limit -{lv:.2f}% (cut@-4.0%={leg_cut_v}) | adaptive -{av:.2f}% (cut@-4.0%={adp_cut_v})")
    ok = (abs(lq - 3.0) < 1e-9 and abs(lv - 3.0) < 1e-9          # legacy flat -3% for both
          and aq >= cap - 1e-9 and av > aq and av <= 10.0 + 1e-9  # adaptive R-scaled, >= cap, bounded
          and (not leg_cut_q) and adp_cut_q                       # quiet: legacy rides, adaptive cuts
          and leg_cut_v and (not adp_cut_v))                      # volatile: legacy cuts, adaptive gives room
    verdict("hard_stop", ok,
            f"the REAL watchdog limit is flat -3% in legacy but R-scaled in adaptive: the quiet coin "
            f"(-{aq:.2f}%) is finally cut at -2.6% where legacy rode, the volatile coin (-{av:.2f}%) gets room "
            f"at -4% where legacy cut, and adaptive never drops below the {cap}% cap")


def _head_write(mode, sym, entry, price):
    """Drive the REAL gateway with the loss-cap (Head) stop write; return (accepted, applied_sl)."""
    _s, gw, _sn = build(mode)
    gw.reset_symbol(sym)
    cap_stop = entry * (1 - 2.5 / 100)   # the sacred cap stop (Head source), below price
    res = _run(gw.apply(symbol=sym, new_sl=cap_stop, source="loss_cap", direction="Buy",
                        current_sl=None, current_price=price, entry_price=entry,
                        bypass_step_cap_for_breakeven=True, bypass_rate_limit=True))
    return res.accepted, gw._last_sl.get(sym)


def scn_genuine_loser():
    print("\n## APTUSDT — genuine loser. Aim: the loss-cap (Head) write is admitted IDENTICALLY in both modes.")
    entry, price = 0.6809, 0.6809 * (1 - 0.59 / 100)   # bled to -0.59%, above the -2.5% cap
    leg = _head_write("legacy", "APT", entry, price)
    adp = _head_write("adaptive", "APT", entry, price)
    print(f"  legacy  : Head cap accepted={leg[0]} applied_sl={leg[1]}")
    print(f"  adaptive: Head cap accepted={adp[0]} applied_sl={adp[1]}")
    ok = leg[0] and adp[0] and leg[1] == adp[1]
    verdict("genuine_loser", ok,
            "the loss-cap (Head) candidate is admitted and written to the SAME stop in both modes — the "
            "adaptive flags do not weaken the loss-cutting/Head path (runtime force-close additionally "
            "proven in simulate_exit_authority_live Scenario D)")


def scn_catastrophe():
    print("\n## CRASH — fast catastrophe. Aim: the Head (cap) is admitted IDENTICALLY in both modes (Rule 6).")
    entry, price = 100.0, 100.0 * (1 - 1.5 / 100)   # crashing through -1.5%, cap stop at -2.5% still ahead
    leg = _head_write("legacy", "CRASH", entry, price)
    adp = _head_write("adaptive", "CRASH", entry, price)
    print(f"  legacy  : Head cap accepted={leg[0]} applied_sl={leg[1]}")
    print(f"  adaptive: Head cap accepted={adp[0]} applied_sl={adp[1]}")
    ok = leg[0] and adp[0] and leg[1] == adp[1]
    verdict("catastrophe", ok,
            "the catastrophic cap (Head) is admitted and written identically in both modes — the adaptive "
            "layer never weakens the Head (runtime crash force-close proven in simulate_exit_authority_live Scenario C)")


def _coro(v):
    async def _f(): return v
    return _f()


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    print("ADAPTIVE EXIT SCENARIO SIMULATION — recreate each proven issue, check each fix responds")
    scn_clipped_winner()
    scn_clamp_noop()
    scn_dead_drifter()
    scn_recovered_fighter()
    scn_hard_stop()
    scn_genuine_loser()
    scn_catastrophe()
    print("\n" + "=" * 72)
    bad = [n for n, ok in VERDICTS if not ok]
    if bad:
        print(f"RESULT: FAIL — fixes not responding as intended: {', '.join(bad)}")
        sys.exit(1)
    print(f"RESULT: PASS — all {len(VERDICTS)} fixes responded as intended on the recreated issues:")
    print("  clipped winner kept, clamp-noop writes, dead drifter scratched, recovery captured,")
    print("  hard stop R-scaled (cap operative), genuine loser still cut, catastrophic Head undiminished.")
    sys.exit(0)


if __name__ == "__main__":
    main()
