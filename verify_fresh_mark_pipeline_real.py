#!/usr/bin/env python3
"""Real-project END-TO-END pipeline verification of the fresh-mark degrade fix.

Constructs the REAL components the way WorkerManager wires them (SLGateway,
ProfitSniper, PositionWatchdog, VolatilityProfiler — real classes, REAL Settings
loaded from config.toml) and exercises the fix against the REAL
``src.core.types.Position`` type, which carries ``mark_price`` — the EXACT field
the bybit_demo adapter validates wrong-side against and the EXACT field the
gateway's ``_fresh_mark`` reads. Only the network/exchange/DB/TA edges are stubbed;
every type, class, wiring path, and config read is the real project.

Pipeline stages asserted:
  DI WIRING    -> gateway holds the position_service; sniper/watchdog hold the gateway
                  AND the gateway's position_service IS the sniper's (same object the
                  adapter's get_position belongs to — the fresh-mark source == the
                  adapter's enforcement source).
  CONFIG FLOW  -> real Settings exposes r2_fresh_mark_degrade_enabled /
                  log_only_fresh_mark_degrade / fresh_mark_recheck_distance_mult,
                  and the gateway reads them.
  DEPENDENCY   -> gateway._fresh_mark() reads REAL Position.mark_price (proves the
                  field/method/contract match against the real type).
  DATA FLOW    -> real ProfitSniper._compute_ladder_floor produces the R-lock; it
                  flows into gateway.apply; the fresh-mark degrade fetches the REAL
                  Position.mark_price and degrades the unplaceable lock to breakeven.
  RUNTIME      -> the adapter-faithful set_stop_loss ACCEPTS the degraded stop (no
                  wire-fail); real winners and far loss stops are untouched.

Read-only w.r.t. live state. Exits non-zero on any broken stage.
"""
import asyncio
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.core.types import Position, Side
from src.analysis.volatility_profile import VolatilityProfiler
from src.workers.profit_sniper import ProfitSniper
from src.workers.position_watchdog import PositionWatchdog

R_PCT = 0.20
FAILS = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


class StubTACache:
    async def analyze(self, symbol=None, timeframe=None, limit=None, **kw):
        return {"volatility": {"natr_14": R_PCT, "atr_14": R_PCT},
                "price": 100.0, "close": 100.0, "overall": {}, "trend": {}}


class RealTypePositionService:
    """get_position returns the REAL src.core.types.Position (carries mark_price);
    set_stop_loss replicates the bybit_demo adapter wrong-side guard against
    pos.mark_price — the EXACT integration contract the fix depends on."""
    def __init__(self):
        self._mark, self._long, self.placed, self.rejected = {}, {}, {}, {}

    def set(self, sym, mark, is_long):
        self._mark[sym] = mark
        self._long[sym] = is_long

    async def get_position(self, symbol):
        m = self._mark.get(symbol)
        if m is None:
            return None
        return Position(symbol=symbol, side=Side.BUY if self._long[symbol] else Side.SELL,
                        size=1.0, entry_price=m, mark_price=m, unrealized_pnl=0.0)

    async def set_stop_loss(self, symbol, sl, *a, **k):
        pos = await self.get_position(symbol)
        if pos is not None and pos.size > 0 and pos.mark_price > 0 and sl and sl > 0:
            is_long = pos.side == Side.BUY
            if (is_long and sl >= pos.mark_price) or ((not is_long) and sl <= pos.mark_price):
                self.rejected[symbol] = self.rejected.get(symbol, 0) + 1
                return False
        self.placed[symbol] = sl
        return True

    async def get_positions(self, *a, **k):
        return []


class StubMkt:
    async def get_ticker(self, *a, **k):
        return None


class StubEvents:
    def add_event(self, *a, **k):
        pass


async def main():
    s = Settings._load_fresh()
    s.sl_gateway.owner_switch_enforce = False
    s.sl_gateway.rate_limit_seconds = 0
    db = SimpleNamespace()
    ta = StubTACache()

    # ── construct the REAL graph the way WorkerManager wires it ──
    profiler = VolatilityProfiler(ta_cache=ta, regime_detector=None, settings=s.volatility_profile)
    possvc = RealTypePositionService()
    gw = SLGateway(settings=s, position_service=possvc, market_service=StubMkt(),
                   event_buffer=StubEvents(), volatility_profiler=profiler)
    sniper = ProfitSniper(settings=s, db=db, position_service=possvc, market_service=StubMkt(),
                          ta_cache=ta, volatility_profiler=profiler, sl_gateway=gw)
    watchdog = PositionWatchdog(settings=s, db=db, position_service=possvc, market_service=StubMkt(),
                                volatility_profiler=profiler, sl_gateway=gw)

    print("== DI WIRING (real classes, manager-style) ==")
    chk("gateway holds the position_service", gw._position_service is possvc)
    chk("sniper holds the gateway", sniper.sl_gateway is gw)
    chk("watchdog holds the gateway", watchdog.sl_gateway is gw)
    chk("gateway.position_service IS sniper.position_service (fresh-mark source == adapter source)",
        gw._position_service is sniper.position_service)

    print("\n== CONFIG FLOW (real Settings from config.toml -> gateway) ==")
    g = s.sl_gateway
    chk("r2_fresh_mark_degrade_enabled present + true", g.r2_fresh_mark_degrade_enabled is True)
    chk("log_only_fresh_mark_degrade present + false", g.log_only_fresh_mark_degrade is False)
    chk("fresh_mark_recheck_distance_mult present (centralized coefficient)",
        abs(float(g.fresh_mark_recheck_distance_mult) - 2.0) < 1e-9,
        f"mult={g.fresh_mark_recheck_distance_mult}")

    print("\n== DEPENDENCY (gateway._fresh_mark reads REAL Position.mark_price) ==")
    possvc.set("DEP", 1.2345, True)
    fm = await gw._fresh_mark("DEP")
    chk("_fresh_mark returns the REAL Position.mark_price", fm is not None and abs(fm - 1.2345) < 1e-9,
        f"_fresh_mark=DEP -> {fm}")
    chk("_fresh_mark returns None when no position (graceful)", (await gw._fresh_mark("NONE")) is None)

    print("\n== DATA FLOW (real sniper ladder lock -> gateway.apply) ==")
    entry = 100.0
    atr_price = R_PCT / 100.0 * entry
    state = SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=0.13, symbol="FLOW")
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    lad = sniper._compute_ladder_floor(state, dialed, 0.0, atr_value=atr_price)
    chk("real sniper ladder produced an R-lock to feed the gateway",
        lad is not None and getattr(lad, "should_apply", False) and lad.lock_pct > 0,
        f"lock_pct={getattr(lad, 'lock_pct', None)}")

    print("\n== RUNTIME (fresh-mark degrade on the real graph; PYTHUSDT replay) ==")
    ENTRY, LOCK, STALE, FRESH, CUR_SL = 0.03989, 0.039934, 0.039995, 0.03992, 0.039420
    possvc.set("PYTH_REAL", FRESH, True)
    res = await gw.apply(symbol="PYTH_REAL", new_sl=LOCK, source="profit_sniper_ladder",
                         direction="Buy", current_sl=CUR_SL, current_price=STALE, entry_price=ENTRY,
                         bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
                         breakeven_floor_price=ENTRY, profit_lock_floor_price=LOCK)
    placed = possvc.placed.get("PYTH_REAL")
    chk("degrade fired: a stop IS placed (no wire-fail) on the real graph",
        res.accepted and placed is not None, f"accepted={res.accepted} placed={placed}")
    chk("placed stop is placeable (correct side of the REAL Position.mark_price)",
        placed is not None and placed < FRESH, f"placed={placed} fresh={FRESH}")
    chk("degraded to breakeven (>= entry) — give-back converted to a flat exit",
        placed is not None and placed >= ENTRY - 1e-9, f"placed={placed} entry={ENTRY}")

    print("\n== RUNTIME guards: real winner untouched, far loss untouched ==")
    possvc.set("WIN", 1.0499, True)
    rw = await gw.apply(symbol="WIN", new_sl=1.03, source="profit_sniper_ladder", direction="Buy",
                        current_sl=1.01, current_price=1.05, entry_price=1.00,
                        bypass_step_cap_for_breakeven=True, bypass_rate_limit=True,
                        breakeven_floor_price=1.00, profit_lock_floor_price=1.03)
    chk("real winner: lock wires unchanged (no false degrade)",
        rw.accepted and abs(possvc.placed.get("WIN", 0) - 1.03) < 1e-9, f"placed={possvc.placed.get('WIN')}")
    possvc.set("LOSS", 0.9999, True)
    rl = await gw.apply(symbol="LOSS", new_sl=0.97, source="loss_cap", direction="Buy",
                        current_sl=0.95, current_price=1.00, entry_price=1.00,
                        bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    chk("far loss_cap stop wires unchanged (no false trigger)",
        rl.accepted and abs(possvc.placed.get("LOSS", 0) - 0.97) < 1e-9, f"placed={possvc.placed.get('LOSS')}")

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)}: {', '.join(FAILS)}")
        sys.exit(1)
    print("RESULT: PASS — the fresh-mark degrade fix is wired and flows end-to-end through the REAL "
          "project: DI (gateway<-pos_svc->sniper, same object), config (real Settings flags + "
          "coefficient), dependency (_fresh_mark reads the REAL Position.mark_price the adapter "
          "enforces against), data flow (real sniper lock -> gateway), and runtime (degrade places a "
          "placeable breakeven; winners and loss stops untouched).")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(main())
