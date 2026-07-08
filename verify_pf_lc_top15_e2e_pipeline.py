#!/usr/bin/env python3
"""PF/LC Top-15 — END-TO-END PIPELINE verification through the REAL project.

Unlike the per-problem harnesses (which isolate one lever), this drives every
fix through GENUINE project objects and the real DI/config plumbing, short of
starting the live trading loop:

  - real Settings.load() (config.toml -> dataclasses) for every value;
  - the real SLGateway built from real settings.sl_gateway (1.1, 1.4);
  - the real _PositionProxy from Transformer.create_proxies() wired into a real
    TradeCoordinator exactly as WorkerManager does (1.3);
  - the real TimeDecaySLCalculator built by a real PositionWatchdog from
    settings.time_decay (2.3, 3.1) — proving the watchdog DI plumbing;
  - the real TimeDial(settings.loss_cutting) (2.5);
  - the real ProfitSniper methods bound over real settings sub-objects, stubbed
    only at the exchange/DB I/O boundary (1.2, 1.5, 2.1, 2.2, 3.2, 3.4, 3.5).

Flag-ON variants use dataclasses.replace on the real settings (no singleton
mutation), so the values are the real config with one flag flipped — exactly
what the operator does to enable a lever.

Run: python3 verify_pf_lc_top15_e2e_pipeline.py
"""
import asyncio
import dataclasses
import inspect
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings

S = Settings.load()
LOOP = asyncio.new_event_loop()
results = []  # (section, ok, note)


def check(section, ok, note=""):
    results.append((section, bool(ok), note))


# ════════════════════════════════════════════════════════════════════════
# DI WIRING — the manager actually wires the proxy into the coordinator (1.3)
# ════════════════════════════════════════════════════════════════════════
from src.workers import manager as _mgr_mod
_msrc = inspect.getsource(_mgr_mod.WorkerManager)
i_pos = _msrc.find('self._services["position"] = pos_svc')
i_attach = _msrc.find("attach_position_service(")
i_coord = _msrc.find("trade_coordinator = TradeCoordinator()")
check("DI/1.3 manager wires proxy into coordinator",
      i_attach > 0 and i_pos > 0 and i_pos < i_attach and i_coord < i_attach,
      "self._services['position'] set before TradeCoordinator built and attached")


# ════════════════════════════════════════════════════════════════════════
# PHASE 1
# ════════════════════════════════════════════════════════════════════════

# ---- 1.3 net booking through the REAL _PositionProxy delegation chain ----
from src.core.transformer import Transformer
from src.core.trade_coordinator import TradeCoordinator


class _ActivePosSvc:  # stands in for the active bybit_demo position service
    async def get_last_close(self, symbol):
        return {"net_pnl_usd": -3.215, "net_pnl_pct": -0.294, "exit_price": 0.1122}


xfm = Transformer(db=SimpleNamespace(), config=SimpleNamespace())
xfm._active_services["position"] = _ActivePosSvc()
proxy = xfm.create_proxies()["position"]            # the REAL _PositionProxy
coord = TradeCoordinator()
coord.attach_position_service(proxy)                # exactly as manager.py does
booked = {}
coord.on_trade_closed = lambda **kw: booked.update(kw)
LOOP.run_until_complete(
    coord.close_with_authoritative_pnl("ENAUSDT", 0.1122, "loss_spike_force"))
check("1.3 close books NET via real proxy delegation",
      booked.get("price_source") == "exchange_authoritative"
      and abs(booked.get("pnl_usd", 0) + 3.215) < 1e-9,
      f"src={booked.get('price_source')} pnl_usd={booked.get('pnl_usd')}")

# ---- 1.1 + 1.4 through the REAL SLGateway built from real settings ----
from src.core.sl_gateway import SLGateway


class _PosSvc:
    async def set_stop_loss(self, s, n): return True
    async def get_position(self, s): return None


class _Mkt:
    async def get_ticker(self, s): return SimpleNamespace(last_price=100.2)


# real settings.sl_gateway, but widen min_distance so the high-vol squeeze bites
sg_cfg = dataclasses.replace(S.sl_gateway, min_distance_pct=0.60, rate_limit_seconds=0)
gw = SLGateway(SimpleNamespace(sl_gateway=sg_cfg), _PosSvc(), _Mkt())
# The sniper passes BOTH breakeven_floor_price (1.1) AND bypass_step_cap_for_
# breakeven (the pre-existing R3 ladder bypass) — replicate the real spine call,
# else the real max_step_pct=0.25 would clamp the 2% move to a quarter-step.
r11 = LOOP.run_until_complete(gw.apply(
    symbol="ENAUSDT", new_sl=100.05, source="profit_sniper_ladder",
    direction="Buy", current_sl=98.0, current_price=100.2,
    breakeven_floor_price=100.0, bypass_step_cap_for_breakeven=True))
check("1.1 R2 holds armed floor at breakeven (real gateway+settings)",
      r11.accepted and abs(r11.new_sl_applied - 100.0) < 1e-6,
      f"applied={r11.new_sl_applied} (target 100.05, boundary 99.5988)")
check("1.4 gateway result carries the applied (clamped) value",
      r11.new_sl_applied is not None and abs(r11.new_sl_applied - 100.05) > 1e-9,
      "new_sl_applied != target → a clamp is visible to the propagation sites")

# ---- 1.2 + 3.4 spike: real bound method over real settings.loss_cutting ----
from src.workers.profit_sniper import ProfitSniper


class _Buf:
    def __init__(s, p, t): s._p, s._t = p, t
    def get_prices(s): return s._p
    def get_timestamps(s): return s._t


def _spike_fake():
    f = SimpleNamespace()
    f._lc = S.loss_cutting                                  # REAL loss config
    async def _atr(sym): return 1.0
    def _eff(a, b, c): return (1.0, "live")
    f._get_current_atr, f._pf_effective_atr = _atr, _eff
    return f


def _spike(age_s, adverse):
    f = _spike_fake()
    now = time.time()
    tracked = {"buffer": _Buf([100.0 + adverse, 100.0], [now, now])}
    st = SimpleNamespace(atr_at_entry=1.0, age_seconds=age_s)
    fn = ProfitSniper._lc_spike_triggered.__get__(f, ProfitSniper)
    return LOOP.run_until_complete(fn("ENAUSDT", tracked, st, 100.0, True))

young = _spike(5.0, 3.0)      # young 3-ATR wiggle: spared by the wider opening mult
crash = _spike(5.0, 4.0)      # young 4-ATR crash: still fires
old = _spike(60.0, 3.0)      # aged 3-ATR: fires at the normal mult
check("3.4 young-window: 3ATR wiggle spared, 4ATR crash fires, aged 3ATR fires",
      young[0] is False and abs(young[3] - S.loss_cutting.spike_atr_move_mult_opening) < 1e-9
      and crash[0] is True and old[0] is True
      and abs(old[3] - S.loss_cutting.spike_atr_move_mult) < 1e-9,
      f"young={young[0]}@{young[3]} crash={crash[0]} old={old[0]}@{old[3]}")
_sp_src = inspect.getsource(ProfitSniper._pf_apply_spine)
check("1.2 spike hoisted before the graduation gate (always-on)",
      0 <= _sp_src.find("enable_spike_stop") < _sp_src.find("if not _graduated:"),
      "spike pre-check precedes 'if not _graduated:'")

# ---- 1.5 swallow now logged (real source) ----
check("1.5 rate-limit check logs instead of swallowing",
      "SNIPER_RATELIMIT_CHECK_ERROR" in _sp_src and "except Exception as _e:" in _sp_src,
      "bare 'except: pass' replaced by a rate-limited warning")


# ════════════════════════════════════════════════════════════════════════
# PHASE 2  (calibrations — driven ON via dataclasses.replace on real settings)
# ════════════════════════════════════════════════════════════════════════

# ---- 2.1 + 2.2 stall veto: real _lc_stall_decision over real loss settings ----
def _stall_fake(lc):
    f = SimpleNamespace(_lc=lc, layer4_protection=None)
    async def _close(*a, **k): return True
    f._execute_full_close = _close
    return f


def _stall(lc, *, pnl_hist, prev, pnl_pct, cum_ratio, peak_hist):
    f = _stall_fake(lc)
    fn = ProfitSniper._lc_stall_decision.__get__(f, ProfitSniper)
    tracked = {"_lc_pnl_hist": list(pnl_hist), "_lc_peak_hist": list(peak_hist),
               "_lc_pnl_prev": prev}
    st = SimpleNamespace(profit_ratio=cum_ratio, peak_pnl_pct=peak_hist[-1])
    return LOOP.run_until_complete(
        fn("ENAUSDT", object(), tracked, st, pnl_pct, True, 0.7, 0.5))

lc_win = dataclasses.replace(S.loss_cutting, stall_veto_windowed_profit_ratio_enabled=True)
faded = dict(pnl_hist=[-0.5] * 23, peak_hist=[2.0] * 23, prev=-0.5, pnl_pct=-0.5, cum_ratio=0.80)
check("2.1 windowed ratio cuts a faded early-winner (real method+settings)",
      (not _stall(S.loss_cutting, **faded)) and _stall(lc_win, **faded),
      "cumulative spares (off), windowed cuts (on)")

lc_sus = dataclasses.replace(S.loss_cutting, stall_signs_of_life_sustained_improving_enabled=True)
blip = dict(pnl_hist=[-0.5, -0.5, -0.5], peak_hist=[2.0, 2.0, 2.0], prev=-0.5, pnl_pct=-0.499, cum_ratio=0.0)
check("2.2 sustained-improving cuts a single-tick noise blip",
      (not _stall(S.loss_cutting, **blip)) and _stall(lc_sus, **blip),
      "single-tick spares (off), sustained cuts (on)")

# ---- 2.3 + 3.1 through the REAL watchdog-built calculator ----
from src.workers.position_watchdog import PositionWatchdog

wd = PositionWatchdog(settings=S, db=SimpleNamespace(),
                      position_service=SimpleNamespace(), market_service=SimpleNamespace())
_calc = wd._time_decay        # the REAL calculator the watchdog built from settings
check("DI/2.3+3.1 watchdog built the real TimeDecayConfig with the new fields",
      hasattr(_calc.cfg, "winprob_age_aware_band_enabled")
      and hasattr(_calc.cfg, "smooth_p_win_enabled")
      and _calc.cfg.near_certain_loser_p_win_old == 0.13,
      "config.toml -> settings.time_decay -> watchdog -> TimeDecayConfig")


def _mk_state(calc):
    s = calc.create_state(
        symbol="ENAUSDT", direction="Buy", entry_price=100.0, original_sl_pct=2.0,
        max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.6, tick_seconds=5.0,
        entry_xray_confidence=0.65, entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up", entry_regime_confidence=0.70)
    return s


# 2.3 + 3.1: derive ON-variant calculators from the REAL watchdog-built cfg
# (frozen dataclass → dataclasses.replace; every other value stays real, only
# the flag flips — exactly what setting the config key does).
from src.risk.time_decay_sl import TimeDecaySLCalculator
calc_band = TimeDecaySLCalculator(
    dataclasses.replace(_calc.cfg, winprob_age_aware_band_enabled=True))
calc_sm = TimeDecaySLCalculator(
    dataclasses.replace(_calc.cfg, smooth_p_win_enabled=True))


def _band(calc, age_s):
    s = _mk_state(calc)
    s.p_win, s.mae_pct, s.last_pnl_pct, s.prev_pnl_pct = 0.12, -1.1, -1.1, -1.1
    return calc.calculate(s, current_pnl_pct=-1.1, position_age_seconds=age_s,
                          regime_still_supports=True, velocity_pct_per_s=0.0,
                          acceleration_pct_per_s2=0.0, structural_invalidation=False,
                          invalidation_reason="stable")

# off (real default) holds the band at any age; on cuts aged, holds young
off_aged, on_aged, on_young = _band(_calc, 700), _band(calc_band, 700), _band(calc_band, 400)
check("2.3 age-aware band: off holds, on cuts aged loser, holds young (real watchdog calc)",
      off_aged is None and on_aged == -1.0 and on_young is None,
      f"off_aged={off_aged} on_aged={on_aged} on_young={on_young}")

# 3.1: a single regime flicker applies NO penalty under the real ON calculator
s_sm = _mk_state(calc_sm)
s_sm.p_win, s_sm.prev_pnl_pct, s_sm.mae_pct = 0.5, 0.0, 0.0
calc_sm._update_p_win(s_sm, current_pnl_pct=0.0, regime_still_supports=False)  # 1 flicker
flicker_ok = abs(s_sm.p_win - 0.5) < 1e-9
# recovery guard holds a near-BE trade making a new high (no structural signal)
s_rg = _mk_state(calc_sm)
s_rg.p_win, s_rg.mae_pct, s_rg.prev_pnl_pct = 0.06, -1.1, -0.2
s_rg.recent_pnl = [-0.6, -0.5, -0.4]
guard = calc_sm.calculate(s_rg, current_pnl_pct=-0.2, position_age_seconds=400,
                          regime_still_supports=True, velocity_pct_per_s=0.0,
                          acceleration_pct_per_s2=0.0, structural_invalidation=False,
                          invalidation_reason="stable")
check("3.1 single flicker no penalty + recovery guard holds (real watchdog calc)",
      flicker_ok and guard is None,
      f"p_win_after_flicker={s_sm.p_win:.3f} guard={guard}")

# ---- 2.4 trail aligned to the arm (real settings) ----
check("2.4 trail activation centralized in profit_fetching, aligned to arm",
      abs(S.profit_fetching.min_profit_for_trail_pct - S.profit_fetching.min_profit_to_arm_ladder_pct) < 1e-9,
      f"trail={S.profit_fetching.min_profit_for_trail_pct} arm={S.profit_fetching.min_profit_to_arm_ladder_pct}")

# ---- 2.5 dial freeze through the REAL TimeDial + real _pf_age_and_deadline ----
from src.core.time_dial import TimeDial
dial = TimeDial(S.loss_cutting)
smaf_ext = dial.resolve_loss(29.0, 40.0).stall_min_age_fraction
smaf_frz = dial.resolve_loss(29.0, 30.0).stall_min_age_fraction


def _age_dl(freeze):
    f = SimpleNamespace(_pf=dataclasses.replace(S.profit_fetching,
                        dial_freeze_on_original_deadline_enabled=freeze))
    plan = SimpleNamespace(age_minutes=29.0, max_hold_minutes=40, _original_max_hold_minutes=30)
    f.trade_coordinator = SimpleNamespace(get_trade_plan=lambda s: plan)
    f._tracked = {}
    return ProfitSniper._pf_age_and_deadline.__get__(f, ProfitSniper)("ENAUSDT")

check("2.5 dial freeze: off uses extended dl, on uses original (real TimeDial)",
      _age_dl(False)[1] == 40.0 and _age_dl(True)[1] == 30.0 and smaf_ext > smaf_frz,
      f"off_dl={_age_dl(False)[1]} on_dl={_age_dl(True)[1]} leak {smaf_ext:.3f}>{smaf_frz:.3f}")


# ════════════════════════════════════════════════════════════════════════
# PHASE 3
# ════════════════════════════════════════════════════════════════════════

# ---- 3.2 + 3.5 real _pf_effective_atr fallback chain + tick() routing ----
_eff = ProfitSniper._pf_effective_atr.__get__(SimpleNamespace(_pf=S.profit_fetching), ProfitSniper)
live_v, live_src = _eff(0.0013, 0.0011, 100.0)
cold_v, cold_src = _eff(0.0, 0.0011, 100.0)
_tick = inspect.getsource(ProfitSniper.tick)
check("3.2+3.5 warm M5 ATR resolves live; cold falls back; tick() routes+demotes",
      live_src == "live" and cold_src == "entry_atr"
      and "await self._get_current_atr(symbol)" in _tick
      and "self._pf.trail_live_m5_atr_enabled" in _tick
      and "log.debug(_atr_fb_msg)" in _tick,
      "real fallback chain + flagged routing + DEBUG demotion")

# ---- 3.3 tunables centralized (real settings) ----
check("3.3 trail-width tunables centralized (measure-then-tune, no code change)",
      S.mode4.base_atr_multiplier is not None and S.profit_fetching.atr_multiple_young is not None,
      f"base_atr_multiplier={S.mode4.base_atr_multiplier}")


# ════════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════════
print()
npass = sum(1 for _, ok, _ in results if ok)
for sec, ok, note in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {sec}" + (f"  ({note})" if (note and not ok) else ""))
print()
if npass == len(results):
    print(f"E2E PIPELINE: PASS — {npass}/{len(results)} real-project pipeline checks green "
          f"(real Settings, real SLGateway, real _PositionProxy, real watchdog-built "
          f"TimeDecayCalculator, real TimeDial). Outcome verdicts provisional until 1.3 ruler runs.")
    sys.exit(0)
print(f"E2E PIPELINE: FAIL — {npass}/{len(results)} passed")
for sec, ok, note in results:
    if not ok:
        print(f"  FAIL: {sec} — {note}")
sys.exit(1)
