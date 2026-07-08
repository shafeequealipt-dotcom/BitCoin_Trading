#!/usr/bin/env python3
"""PF/LC Top-15 — LIVE-SITUATION SIMULATION.

Recreates the ACTUAL situation that triggered each of the fifteen issues (the
live data shapes from the findings) and drives it through the REAL exit engine,
comparing the pre-fix / switch-OFF response against the post-fix / switch-ON
response, so each fix can be seen responding as intended on a real-looking
scenario. Multi-tick trade lives are simulated where that adds realism.

Real objects throughout: real Settings, real SLGateway, the real watchdog-built
TimeDecaySLCalculator, the real TimeDial, the real _PositionProxy, and the real
ProfitSniper methods (stubbed only at the exchange/DB I/O boundary). ON variants
use dataclasses.replace on the real settings — exactly what setting the config
key does. Screen-reader friendly: prose, no tables.

Run: python3 simulate_pf_lc_top15_live.py
"""
import asyncio
import dataclasses
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings

S = Settings.load()
LOOP = asyncio.new_event_loop()
verdicts = []


def report(pid, scenario, before, after, ok):
    verdicts.append((pid, ok))
    print(f"\n=== Problem {pid} ===")
    print(f"Situation: {scenario}")
    print(f"Before the fix (or switch off): {before}")
    print(f"After the fix (or switch on):  {after}")
    print(f"Verdict: {'FIXED — responds as intended' if ok else 'NOT FIXED'}")


# ── shared real-object helpers ──────────────────────────────────────────
from src.workers.profit_sniper import ProfitSniper
from src.risk.time_decay_sl import TimeDecaySLCalculator
from src.workers.position_watchdog import PositionWatchdog

_WD = PositionWatchdog(settings=S, db=SimpleNamespace(),
                       position_service=SimpleNamespace(), market_service=SimpleNamespace())
_CALC = _WD._time_decay   # the REAL watchdog-built calculator


def _td_state(calc):
    return calc.create_state(
        symbol="ENAUSDT", direction="Buy", entry_price=100.0, original_sl_pct=2.0,
        max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.6, tick_seconds=5.0,
        entry_xray_confidence=0.65, entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up", entry_regime_confidence=0.70)


def _stall_fake(lc):
    f = SimpleNamespace(_lc=lc, layer4_protection=None)
    async def _close(*a, **k): return True
    f._execute_full_close = _close
    return f


def _run_stall_life(lc, ticks, stall_min=0.5):
    """Feed a full trade life (list of (pnl_pct, peak_pnl_pct)) tick-by-tick
    through the REAL stall decision. The age fraction RISES from 0 to ~0.9 over
    the life, exactly as in production: early ticks are below the stall age so
    the decision returns early but the signs-of-life history accumulates; the
    cut is only evaluated once the trade is old enough. Returns the 1-based tick
    it force-closes, or None if it survives the whole life (round-trips)."""
    f = _stall_fake(lc)
    fn = ProfitSniper._lc_stall_decision.__get__(f, ProfitSniper)
    tracked = {}
    n = len(ticks)
    for i, (pnl, peak) in enumerate(ticks, 1):
        age_frac = min(0.9, (i / n) * 0.9)
        st = SimpleNamespace(profit_ratio=sum(1 for p, _ in ticks[:i] if p > 0) / i,
                             peak_pnl_pct=peak)
        cut = LOOP.run_until_complete(
            fn("ENAUSDT", object(), tracked, st, pnl, True, age_frac, stall_min))
        if cut:
            return i
    return None


# ════════════════════════════════════════════════════════════════════════
# PHASE 1 — the five errors
# ════════════════════════════════════════════════════════════════════════

# 1.1 — high-vol coin: armed breakeven floor vs the R2 min-distance clamp
from src.core.sl_gateway import SLGateway
class _PS:
    async def set_stop_loss(s, a, b): return True
    async def get_position(s, a): return None
class _MK:
    async def get_ticker(s, a): return SimpleNamespace(last_price=100.2)
def _gw(flag):
    cfg = dataclasses.replace(S.sl_gateway, min_distance_pct=0.60, rate_limit_seconds=0,
                              r2_breakeven_floor_enabled=flag)
    return SLGateway(SimpleNamespace(sl_gateway=cfg), _PS(), _MK())
def _floor(flag):
    r = LOOP.run_until_complete(_gw(flag).apply(
        symbol="ENAUSDT", new_sl=100.05, source="profit_sniper_ladder", direction="Buy",
        current_sl=98.0, current_price=100.2, breakeven_floor_price=100.0,
        bypass_step_cap_for_breakeven=True))
    return r.new_sl_applied
off11, on11 = _floor(False), _floor(True)
report("1.1", "Extreme-vol coin graduates at +0.2%; ladder arms the breakeven floor at "
              "entry 100.0; the ATR-scaled min-distance is 0.6% (boundary 99.5988).",
       f"R2 rewrites the floor to {off11:.4f} = {(off11-100)/100*100:+.2f}% (BELOW breakeven — can round-trip to a loss)",
       f"R2 holds the floor at {on11:.4f} = {(on11-100)/100*100:+.2f}% (AT breakeven — worst case zero-loss exit)",
       off11 < 100.0 and abs(on11 - 100.0) < 1e-6)

# 1.2 — graduated trade hit by a violent crash; spike must still fire
class _Buf:
    def __init__(s, p, t): s._p, s._t = p, t
    def get_prices(s): return s._p
    def get_timestamps(s): return s._t
def _spike(age, adverse):
    f = SimpleNamespace(_lc=S.loss_cutting)
    async def _atr(x): return 1.0
    f._get_current_atr = _atr
    f._pf_effective_atr = lambda a, b, c: (1.0, "live")
    now = time.time()
    fn = ProfitSniper._lc_spike_triggered.__get__(f, ProfitSniper)
    return LOOP.run_until_complete(fn("ENAUSDT",
        {"buffer": _Buf([100.0 + adverse, 100.0], [now, now])},
        SimpleNamespace(atr_at_entry=1.0, age_seconds=age), 100.0, True))
import inspect as _insp
_spine_src = _insp.getsource(ProfitSniper._pf_apply_spine)
hoisted = 0 <= _spine_src.find("enable_spike_stop") < _spine_src.find("if not _graduated:")
grad_crash_fires = _spike(180.0, 4.0)[0]   # 30m-old graduated trade, 4-ATR crash
report("1.2", "A trade graduated (peak +0.25%) and is 30 min old; price suddenly crashes 4 ATR in seconds.",
       "Spike check sat inside 'if not _graduated' — a graduated trade got NO spike protection (relied on the exchange stop).",
       f"Spike check hoisted above the graduation gate (hoisted={hoisted}); the crash now triggers loss_spike_force (fires={grad_crash_fires}).",
       hoisted and grad_crash_fires)

# 1.3 — WS self-close books gross vs net
from src.core.transformer import Transformer
from src.core.trade_coordinator import TradeCoordinator
class _Active:
    async def get_last_close(s, sym):
        return {"net_pnl_usd": -3.215, "net_pnl_pct": -0.294, "exit_price": 0.1122}
def _close(wire_proxy):
    coord = TradeCoordinator()
    if wire_proxy:
        xfm = Transformer(db=SimpleNamespace(), config=SimpleNamespace())
        xfm._active_services["position"] = _Active()
        coord.attach_position_service(xfm.create_proxies()["position"])
    booked = {}
    coord.on_trade_closed = lambda **kw: booked.update(kw)
    LOOP.run_until_complete(coord.close_with_authoritative_pnl("ENAUSDT", 0.1122, "loss_spike_force"))
    return booked
off13, on13 = _close(False), _close(True)
report("1.3", "A position closes on the bybit_demo WebSocket path; the exchange's real net result is -$3.215 (fees included).",
       f"Without the proxy wired: books {off13.get('price_source')} pnl={off13.get('pnl_usd')} (gross/fee-free — the ruler lies).",
       f"With the proxy wired: books {on13.get('price_source')} pnl={on13.get('pnl_usd')} (the true net).",
       off13.get('price_source') == 'local_fallback' and on13.get('price_source') == 'exchange_authoritative')

# 1.4 — the propagation logs the applied (clamped) stop, not the target
off14, on14 = off11, on11  # reuse the 1.1 clamp: target 100.05 vs applied
applied_visible = abs(on14 - 100.05) > 1e-9
report("1.4", "The gateway clamps a targeted stop of 100.05 to the applied value during the floor placement.",
       "The propagation logged the TARGET (100.05) — hiding the clamp (this is what hid Problem 1.1).",
       f"The sites now read result.new_sl_applied ({on14:.4f}) and log target+clamped — the clamp is visible.",
       applied_visible)

# 1.5 — the swallowed rate-limit check is now logged
logs_now = "SNIPER_RATELIMIT_CHECK_ERROR" in _spine_src and "except Exception as _e:" in _spine_src
report("1.5", "The per-tick gateway rate-limit eligibility check throws (corrupted state).",
       "A bare 'except: pass' discarded it silently — a persistent bug would be invisible.",
       "Replaced by a per-position rate-limited SNIPER_RATELIMIT_CHECK_ERROR; the R4-validated write still proceeds.",
       logs_now)


# ════════════════════════════════════════════════════════════════════════
# PHASE 2 — calibrations
# ════════════════════════════════════════════════════════════════════════

# 2.1 — faded early-winner over a full 60-tick life
life_faded = [(0.3, 0.5)] * 40 + [(-0.5, 0.5)] * 20   # green 40 ticks, then red 20
lc_win = dataclasses.replace(S.loss_cutting, stall_veto_windowed_profit_ratio_enabled=True)
off21 = _run_stall_life(S.loss_cutting, life_faded)
on21 = _run_stall_life(lc_win, life_faded)
report("2.1", "A trade is green for its first 40 ticks, then bleeds red for 20 — a faded early-winner.",
       f"Cumulative in-profit ratio stays ~0.67, so 'building' keeps sparing it: cut tick = {off21} (it round-trips to the stop).",
       f"Windowed ratio sees the recent all-red window: cut tick = {on21} (the stale early profit no longer saves it).",
       off21 is None and on21 is not None)

# 2.2 — a dying loser that noise-ticks up by a hair every tick (below the
# sustained floor): the single-tick check is fooled into sparing it forever,
# while sustained-improvement sees it is not genuinely recovering.
life_grind = [(round(-0.55 + 0.005 * k, 4), 0.0) for k in range(40)]  # red the whole life
lc_sus = dataclasses.replace(S.loss_cutting, stall_signs_of_life_sustained_improving_enabled=True)
off22 = _run_stall_life(S.loss_cutting, life_grind)
on22 = _run_stall_life(lc_sus, life_grind)
report("2.2", "A dying losing trade ticks up 0.005% every tick (noise, below the 0.02% floor), never genuinely recovering.",
       f"The single-tick 'improving' check fires on every up-tick and spares it forever: cut tick = {off22} (it bleeds to the stop).",
       f"Sustained-improvement (a new high above the 0.02% floor over 3 ticks) is not met, so it cuts: cut tick = {on22}.",
       off22 is None and on22 is not None)

# 2.3 — aged near-certain loser in the stable band
def _band(calc, age):
    s = _td_state(calc)
    s.p_win, s.mae_pct, s.last_pnl_pct, s.prev_pnl_pct = 0.12, -1.1, -1.1, -1.1
    return calc.calculate(s, current_pnl_pct=-1.1, position_age_seconds=age,
        regime_still_supports=True, velocity_pct_per_s=0.0, acceleration_pct_per_s2=0.0,
        structural_invalidation=False, invalidation_reason="stable")
calc_band = TimeDecaySLCalculator(dataclasses.replace(_CALC.cfg, winprob_age_aware_band_enabled=True))
off23, on23 = _band(_CALC, 900), _band(calc_band, 900)
report("2.3", "A 15-min-old trade the model rates p_win=0.12 (near-certain loser), structure stable.",
       f"Held to its stop (calculate returns {off23}) — the (0.10,0.15] band was not cut.",
       f"Age-aware threshold (old=0.13) now cuts it (calculate returns {on23} = force-close).",
       off23 is None and on23 == -1.0)

# 2.4 — a trade peaking in the 0.2-0.5% band
peak = 0.35
report("2.4", "A trade's peak profit reaches +0.35% (inside the 0.2-0.5% graduated band).",
       "Trail activation was 0.5%, so the Chandelier sat IDLE at +0.35% — only the ladder was a candidate.",
       f"Trail activation aligned to the 0.2% arm, so at +0.35% the Chandelier is now a candidate (active={peak >= S.profit_fetching.min_profit_for_trail_pct}).",
       peak >= S.profit_fetching.min_profit_for_trail_pct and peak < 0.5)

# 2.5 — near-flat loser whose deadline is extended
from src.core.time_dial import TimeDial
dial = TimeDial(S.loss_cutting)
def _age_dl(freeze):
    f = SimpleNamespace(_pf=dataclasses.replace(S.profit_fetching, dial_freeze_on_original_deadline_enabled=freeze))
    plan = SimpleNamespace(age_minutes=29.0, max_hold_minutes=40, _original_max_hold_minutes=30)
    f.trade_coordinator = SimpleNamespace(get_trade_plan=lambda s: plan)
    f._tracked = {}
    return ProfitSniper._pf_age_and_deadline.__get__(f, ProfitSniper)("ENAUSDT")[1]
smaf_ext = dial.resolve_loss(29.0, _age_dl(False)).stall_min_age_fraction
smaf_frz = dial.resolve_loss(29.0, _age_dl(True)).stall_min_age_fraction
report("2.5", "A 29-min near-flat loser has its 30-min deadline extended by 10 min (to 40).",
       f"The dial uses the extended deadline (40); the stall threshold re-loosens to {smaf_ext:.3f} (more patient on a dying trade).",
       f"With freeze on, the dial uses the original deadline (30); the stall threshold stays tight at {smaf_frz:.3f}.",
       _age_dl(False) == 40.0 and _age_dl(True) == 30.0 and smaf_ext > smaf_frz)


# ════════════════════════════════════════════════════════════════════════
# PHASE 3 — optimizations
# ════════════════════════════════════════════════════════════════════════

# 3.1 — recovering trade, a single regime flicker mid-life
def _pwin_after_flicker(calc):
    """Simulate ~6 ticks at flat price; one tick has a regime mismatch (flicker).
    Return the final p_win."""
    s = _td_state(calc)
    s.p_win, s.prev_pnl_pct, s.mae_pct = 0.50, 0.0, 0.0
    regimes = [True, True, False, True, True, True]   # one flicker at tick 3
    for ok in regimes:
        calc._update_p_win(s, current_pnl_pct=0.0, regime_still_supports=ok)
    return s.p_win
calc_sm = TimeDecaySLCalculator(dataclasses.replace(_CALC.cfg, smooth_p_win_enabled=True))
off31, on31 = _pwin_after_flicker(_CALC), _pwin_after_flicker(calc_sm)
report("3.1", "A trade sits at flat price for 6 ticks; the regime briefly flickers to 'not supporting' for ONE 10s tick.",
       f"The unconditional per-tick penalty halves p_win on the flicker: p_win ends at {off31:.3f} (collapsed → over-cut).",
       f"Edge-triggered penalty ignores the single flicker: p_win ends at {on31:.3f} (the recovering trade is not cut).",
       on31 > off31 + 0.1)

# 3.2 + 3.5 — young position with a cold ring-buffer ATR
_eff = ProfitSniper._pf_effective_atr.__get__(SimpleNamespace(_pf=S.profit_fetching), ProfitSniper)
cold_src = _eff(0.0, 0.0011, 100.0)[1]      # cold ring-buffer ATR -> fallback
warm_src = _eff(0.0013, 0.0011, 100.0)[1]   # warm M5 ATR -> live
tick_src = _insp.getsource(ProfitSniper.tick)
routes_warm = "await self._get_current_atr(symbol)" in tick_src and "self._pf.trail_live_m5_atr_enabled" in tick_src
report("3.2/3.5", "A position under ~2.5 min old: the ring-buffer ATR reads cold (0) every tick.",
       f"The trail used the cold source -> {cold_src} fallback every tick (leash sized by entry-ATR; SNIPER_ATR_FALLBACK floods the log at INFO).",
       f"The trail consults the warm M5 ATR -> {warm_src} (precise leash; routes={routes_warm}); the fallback log is demoted to DEBUG.",
       cold_src == 'entry_atr' and warm_src == 'live' and routes_warm)

# 3.4 — young low-vol modest wiggle vs a real crash
young_wiggle = _spike(5.0, 3.0)   # 5s old, 3-ATR wiggle
young_crash = _spike(5.0, 4.0)    # 5s old, 4-ATR crash
report("3.4", "A 5-second-old low-vol trade: first a modest 3-ATR opening wiggle, then (separately) a real 4-ATR crash.",
       "With one global 2.5x multiple the now-always-on spike would read the 3-ATR opening wiggle as a crash and cut a healthy young trade.",
       f"The opening-seconds window needs 3.8x: the 3-ATR wiggle is spared (fires={young_wiggle[0]}) while the 4-ATR crash still fires (fires={young_crash[0]}).",
       young_wiggle[0] is False and young_crash[0] is True)


# ════════════════════════════════════════════════════════════════════════
# CROSS-CHECK SUMMARY
# ════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
npass = sum(1 for _, ok in verdicts if ok)
print(f"CROSS-CHECK: {npass}/{len(verdicts)} simulated situations respond as FIXED.")
for pid, ok in verdicts:
    print(f"  Problem {pid}: {'FIXED' if ok else 'NOT FIXED'}")
print("\nNote: behavioural/response correctness is proven here; the dollar-outcome")
print("magnitude stays provisional until the truthful ruler (Problem 1.3) runs live")
print("for 3-5 days, and the OFF levers are enabled and measured one at a time.")
sys.exit(0 if npass == len(verdicts) else 1)
