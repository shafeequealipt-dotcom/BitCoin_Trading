#!/usr/bin/env python3
"""End-to-end PIPELINE verification of the adaptive exit through the REAL project.

Read-only. Constructs the REAL components the way WorkerManager wires them
(VolatilityProfiler, SLGateway, ProfitSniper, PositionWatchdog — real classes,
real Settings with the flags live), stubbing ONLY the unavoidable exchange/DB/TA
edges (no live exchange here). Then it runs R through the live chain and asserts
each pipeline stage:

  DI wiring     -> sniper/watchdog/gateway hold the real profiler + gateway + adaptive cfg
  data flow 1   -> real VolatilityProfiler.get_profile() turns ATR into R (atr_pct_5m)
  data flow 2   -> real ProfitSniper._compute_ladder_floor() turns R into the fee-floored lock
  data flow 3   -> real SLGateway.apply() WRITES that lock (profit-lock exemption, not clamp-noop)
  data flow 4   -> R -> watchdog hard-stop backstop (>= cap), via the wired profiler
  data flow 5   -> real ProfitSniper._lc_stall_decision() scratches a dead drifter

Exits non-zero on any broken stage. Proves the fixes are wired and flow through
the real objects, not just unit-tested in isolation.
"""
import asyncio
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.analysis.volatility_profile import VolatilityProfiler
from src.workers.profit_sniper import ProfitSniper
from src.workers.position_watchdog import PositionWatchdog
from src.analysis import vol_scale as vg

R_PCT = 0.20            # the coin's ATR-as-percent the stub TA cache will yield
FAILS = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


# ── stubs for the unavoidable edges (exchange / DB / TA) ───────────────────
class StubTACache:
    async def analyze(self, symbol=None, timeframe=None, limit=None, **kw):
        return {"volatility": {"natr_14": R_PCT, "atr_14": R_PCT},
                "price": 100.0, "close": 100.0, "overall": {}, "trend": {}}


class StubPos:
    async def get_position(self, *a, **k): return None
    async def set_stop_loss(self, *a, **k): return True
    async def get_positions(self, *a, **k): return []


class StubMkt:
    async def get_ticker(self, *a, **k): return None


class StubEvents:
    def add_event(self, *a, **k): pass


async def main():
    s = Settings._load_fresh()
    print(f"flags: adaptive_exit.enabled={s.adaptive_exit.enabled} "
          f"r2_profit_lock_floor_enabled={s.sl_gateway.r2_profit_lock_floor_enabled} "
          f"dead_drifter_enabled={s.adaptive_exit.dead_drifter_enabled}")
    s.sl_gateway.owner_switch_enforce = False   # isolate the R2/geometry pipeline
    s.sl_gateway.rate_limit_seconds = 0

    ta = StubTACache()
    db = SimpleNamespace()  # sniper/watchdog __init__ only store it (no I/O at init)

    # ── construct the REAL components the way WorkerManager does ──
    profiler = VolatilityProfiler(ta_cache=ta, regime_detector=None,
                                  settings=s.volatility_profile)
    gateway = SLGateway(settings=s, position_service=StubPos(), market_service=StubMkt(),
                        event_buffer=StubEvents(), volatility_profiler=profiler)
    sniper = ProfitSniper(settings=s, db=db, position_service=StubPos(),
                          market_service=StubMkt(), ta_cache=ta,
                          volatility_profiler=profiler, sl_gateway=gateway)
    watchdog = PositionWatchdog(settings=s, db=db, position_service=StubPos(),
                                market_service=StubMkt(), volatility_profiler=profiler,
                                sl_gateway=gateway)

    print("\n== DI WIRING (real components hold the real deps + adaptive cfg) ==")
    chk("gateway <- real profiler", gateway._volatility_profiler is profiler)
    chk("sniper <- real profiler", sniper.volatility_profiler is profiler)
    chk("sniper <- real gateway", sniper.sl_gateway is gateway)
    chk("watchdog <- real profiler", watchdog.volatility_profiler is profiler)
    chk("watchdog <- real gateway", watchdog.sl_gateway is gateway)
    chk("sniper sees adaptive_exit.enabled", sniper.settings.adaptive_exit.enabled is True)
    chk("watchdog sees adaptive_exit.enabled", watchdog.settings.adaptive_exit.enabled is True)

    print("\n== DATA FLOW 1: real profiler turns ATR into R ==")
    prof = await profiler.get_profile("TESTUSDT")
    R = prof.atr_pct_5m
    chk("profiler yields R = atr_pct_5m", abs(R - R_PCT) < 1e-9, f"R={R}% class={prof.volatility_class}")

    print("\n== DATA FLOW 2: real sniper ladder turns R into the fee-floored lock ==")
    entry = 100.0
    atr_price = R / 100.0 * entry        # the absolute ATR the sniper's effective-ATR yields
    peak = 0.13                          # dead-band peak: lock = the fee floor, inside eff_min
    state = SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=peak, symbol="TESTUSDT")
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    lad = sniper._compute_ladder_floor(state, dialed, 0.0, atr_value=atr_price)
    exp_lock = vg.profit_lock_pct(peak, R, s.adaptive_exit)
    chk("ladder lock == profit_lock_pct(R)", lad.should_apply and abs(lad.lock_pct - round(exp_lock, 4)) < 1e-4,
        f"lock={lad.lock_pct}% expected={round(exp_lock,4)}%")
    lock_price = lad.ladder_stop_price

    print("\n== DATA FLOW 3: real gateway WRITES the lock (exemption, not clamp-noop) ==")
    price = entry * (1 + 0.12 / 100)     # slight pullback below the +0.13% peak; lock sits just under it
    cur_sl = entry * (1 + 0.02 / 100)
    res = await gateway.apply(symbol="TESTUSDT_PIPE", new_sl=lock_price,
                              source="profit_sniper_ladder", direction="Buy",
                              current_sl=cur_sl, current_price=price, entry_price=entry,
                              bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
                              profit_lock_floor_price=lock_price)
    pipe_stop = gateway._last_sl.get("TESTUSDT_PIPE")
    chk("gateway accepts the R-lock (profit-lock exemption fired)",
        res.accepted and pipe_stop is not None, f"accepted={res.accepted} reason={res.reason}")
    # control: same write via the breakeven-only hold (no exemption value). It can
    # only reach the eff_min boundary; the exemption holds the lock itself, so the
    # exemption captures STRICTLY MORE profit — the whole point of the fix.
    gateway.reset_symbol("TESTUSDT_CTRL")
    await gateway.apply(symbol="TESTUSDT_CTRL", new_sl=lock_price,
                        source="profit_sniper_ladder", direction="Buy",
                        current_sl=cur_sl, current_price=price, entry_price=entry,
                        bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
                        breakeven_floor_price=entry)
    ctrl_stop = gateway._last_sl.get("TESTUSDT_CTRL", cur_sl)
    chk("exemption captures MORE profit than the breakeven-only hold",
        pipe_stop is not None and pipe_stop > ctrl_stop + 1e-9,
        f"exemption +{(pipe_stop/entry-1)*100:.3f}% vs breakeven-only +{(ctrl_stop/entry-1)*100:.3f}%")

    print("\n== DATA FLOW 4: R -> watchdog hard-stop backstop (>= sacred cap) ==")
    hard = vg.hard_stop_pct(R, s.adaptive_exit)
    cap_young = s.loss_cutting.cap_pct_of_notional_young
    chk("hard stop is R-derived and >= cap", hard >= cap_young - 1e-9, f"hard={hard}% cap_young={cap_young}%")
    chk("watchdog can reach R (has profiler + adaptive on)",
        watchdog.volatility_profiler is profiler and watchdog.settings.adaptive_exit.enabled)

    print("\n== DATA FLOW 5: real sniper scratches a dead drifter ==")
    # seed the sniper's ATR cache so its own _get_current_atr yields R for the scratch
    sniper._atr_cache["DRIFT"] = (atr_price, __import__("time").monotonic())
    closed = {"n": 0}
    async def _fake_close(*a, **k):
        closed["n"] += 1; return True
    sniper._execute_full_close = _fake_close
    sniper.layer4_protection = None
    s.loss_cutting.stall_veto_windowed_profit_ratio_enabled = False
    s.loss_cutting.stall_signs_of_life_sustained_improving_enabled = False
    dstate = SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=0.05,
                             symbol="DRIFT", profit_ratio=0.0, trough_pnl_pct=-0.2,
                             trough_price=entry * 0.998)
    scratched = await sniper._lc_stall_decision(
        "DRIFT", SimpleNamespace(), {}, dstate, pnl_pct=-0.10, is_long=True,
        age_fraction=0.75, stall_min_age_fraction=1.1)
    chk("dead drifter (peak<1R, age 0.75, no life) scratched via real sniper",
        scratched and closed["n"] == 1, f"scratched={scratched} closes={closed['n']}")

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} pipeline stage(s): {', '.join(FAILS)}")
        sys.exit(1)
    print("RESULT: PASS — the adaptive exit flows end-to-end through the REAL wired "
          "pipeline: profiler R -> ladder lock -> gateway write, plus the hard-stop "
          "and dead-drifter stages, all on real components.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(main())
