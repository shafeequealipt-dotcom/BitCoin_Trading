#!/usr/bin/env python3
"""Pass 4 — live simulation with issue-occurring data.

Reproduces the REAL situations observed live and asserts each shipped fix responds
as intended, driving the real SLGateway with stubs. Two fixes under test:
  FIX 1 — placement forensics (PLACEMENT_FORENSIC) + the fresh-mark degrade it records
  FIX 2 — the inert, source-aware profit-lock cadence window

Each scenario prints PASS/FAIL with the observed values; exit code != 0 on any FAIL.
"""
import asyncio, os, glob, sys
from types import SimpleNamespace

LOGDIR = "/tmp/fix_verify_logs"
os.system(f"rm -rf {LOGDIR}")
from src.core.logging import setup_logging
setup_logging("INFO", LOGDIR)
from src.config.settings import Settings
from src.core.sl_gateway import SLGateway

FAILS = []
def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)

class VP:
    def __init__(self, atr=0.30): self.atr = atr
    async def get_profile(self, sym): return SimpleNamespace(atr_pct_5m=self.atr, volatility_class="medium")
class POS:
    def __init__(self): self.fresh = 0.0
    async def get_position(self, sym): return SimpleNamespace(mark_price=self.fresh, stop_loss=None)
    async def set_stop_loss(self, sym, v): return True
class MKT:
    async def get_ticker(self, x): return None
class EVT:
    def add_event(self, *a, **k): pass

def mk(settings, atr=0.30):
    pos = POS()
    gw = SLGateway(settings=settings, position_service=pos, market_service=MKT(),
                   event_buffer=EVT(), volatility_profiler=VP(atr))
    return gw, pos

def last_forensic(sym, tries=60):
    """Retry-read the forensic line for `sym` — the loguru enqueue=True sink writes
    asynchronously, so we wait until the line lands (no read-before-flush race)."""
    import time
    pf = f"{LOGDIR}/placement_forensic.log"
    for _ in range(tries):
        if os.path.exists(pf):
            lines = [l for l in open(pf) if "PLACEMENT_FORENSIC |" in l and f"sym={sym} " in l]
            if lines:
                body = lines[-1].split("PLACEMENT_FORENSIC |", 1)[1].split("|")[0]
                return {k: v for k, v in (t.split("=", 1) for t in body.split() if "=" in t)}
        time.sleep(0.05)
    return {}

async def main():
    s = Settings._load_fresh()
    s.sl_gateway.owner_switch_enforce = False
    s.sl_gateway.rate_limit_seconds = 30
    s.sl_gateway.profit_lock_rate_limit_seconds = 30   # inert default

    # ── SCENARIO A — the DEXE-style fast retrace (the issue that motivated everything) ──
    # Long; between the caller snapshot and the live mark the price dropped ~1%, so the
    # ladder's profit lock is wrong-side of the live mark and unplaceable. The fresh-mark
    # degrade must place the closest placeable stop (NOT wire-fail, NOT wrong-side), and
    # the forensic line must record the divergence, the degrade, and the forgone tightening.
    print("SCENARIO A — fast-retrace mover (DEXE-style): degrade places + forensic records")
    gw, pos = mk(s)
    entry = 23.0354
    snap = 23.4275            # caller price near peak
    pos.fresh = 23.1817       # live mark retraced ~1.05% below snap
    proposed = entry * (1 + 1.41/100)  # +1.41% lock (above the live mark -> wrong-side for a long)
    r = await gw.apply(symbol="DEXE_SIM", new_sl=proposed, source="profit_sniper_ladder",
                       direction="Buy", current_sl=entry*(1-0.5/100), current_price=snap,
                       entry_price=entry, profit_lock_floor_price=proposed,
                       breakeven_floor_price=entry, bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    fx = last_forensic("DEXE_SIM")
    check("A1 a stop IS placed (no wire-fail)", r.accepted, f"reason={r.reason!r}")
    check("A2 placed stop is on the correct side of the live mark", r.accepted and r.new_sl_applied < pos.fresh,
          f"applied={r.new_sl_applied} fresh={pos.fresh}")
    check("A3 forensic captured the live mark != snapshot (divergence visible)", fx.get("mark") not in (None, "na"),
          f"snap={fx.get('snap')} mark={fx.get('mark')}")
    check("A4 forensic recorded fresh_degraded + a real forgone", fx.get("fresh_degraded") == "True" and float(fx.get("forgone_pct", 0)) > 0.5,
          f"fresh_degraded={fx.get('fresh_degraded')} forgone_pct={fx.get('forgone_pct')}")
    check("A5 forensic outcome=placed", fx.get("outcome") == "placed", f"outcome={fx.get('outcome')}")

    # ── SCENARIO B — benign far stop (placeable, no degrade, zero API) ──
    print("SCENARIO B — far placeable stop: placed, mark=na (no live-mark fetch), tiny forgone")
    gw, pos = mk(s)
    pos.fresh = 105.0
    r = await gw.apply(symbol="FAR_SIM", new_sl=101.0, source="profit_sniper_ladder",
                       direction="Buy", current_sl=100.5, current_price=105.0, entry_price=100.0,
                       profit_lock_floor_price=101.0, breakeven_floor_price=100.0,
                       bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    fx = last_forensic("FAR_SIM")
    check("B1 placed", r.accepted and fx.get("outcome") == "placed", f"outcome={fx.get('outcome')}")
    check("B2 mark=na (the degrade did NOT fetch -> zero added API)", fx.get("mark") == "na", f"mark={fx.get('mark')}")

    # ── SCENARIO C — clamp_noop (lock no better than the existing stop -> held, no looser wire) ──
    print("SCENARIO C — tighter lock the degrade can't place: clamp_noop, existing stop kept")
    gw, pos = mk(s)
    pos.fresh = 100.00            # live mark; the tighter lock sits inside its min-distance
    r = await gw.apply(symbol="NOOP_SIM", new_sl=99.98, source="profit_sniper_ladder",
                       direction="Buy", current_sl=99.95, current_price=100.00, entry_price=99.80,
                       profit_lock_floor_price=99.98, breakeven_floor_price=99.80,
                       bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    fx = last_forensic("NOOP_SIM")
    check("C1 not accepted, reason=clamp_noop (degrade boundary can't beat existing stop)",
          (not r.accepted) and r.reason == "clamp_noop", f"reason={r.reason}")
    check("C2 forensic outcome=clamp_noop", fx.get("outcome") == "clamp_noop", f"outcome={fx.get('outcome')}")

    # ── SCENARIO D/E/F — the cadence window (FIX 2): inert / source-aware / clamped ──
    print("SCENARIO D/E/F — cadence window: inert at 30, source-aware at 10, clamped above base")
    gw, _ = mk(s)
    cfg = s.sl_gateway
    cfg.profit_lock_rate_limit_seconds = 30
    check("D inert: ladder window == base (30)", gw._rate_limit_window_for("profit_sniper_ladder", cfg) == 30.0,
          f"ladder={gw._rate_limit_window_for('profit_sniper_ladder', cfg)}")
    cfg.profit_lock_rate_limit_seconds = 10
    check("E source-aware: ladder=10 but loss_cap stays 30",
          gw._rate_limit_window_for("profit_sniper_ladder", cfg) == 10.0 and gw._rate_limit_window_for("loss_cap", cfg) == 30.0,
          f"ladder={gw._rate_limit_window_for('profit_sniper_ladder', cfg)} loss_cap={gw._rate_limit_window_for('loss_cap', cfg)}")
    cfg.profit_lock_rate_limit_seconds = 99
    check("F clamp: above-base value clamps to base (can only go faster)",
          gw._rate_limit_window_for("profit_sniper_ladder", cfg) == 30.0,
          f"ladder={gw._rate_limit_window_for('profit_sniper_ladder', cfg)}")
    cfg.profit_lock_rate_limit_seconds = 30  # restore inert

    # ── SCENARIO G — safety: the catastrophic cap still fires; forensic does NOT touch loss-side ──
    print("SCENARIO G — safety: Head admitted on a green trade; loss_cap NOT logged as forensic")
    s2 = Settings._load_fresh()
    s2.sl_gateway.owner_switch_enabled = True
    s2.sl_gateway.owner_switch_enforce = True
    gw, pos = mk(s2)
    pos.fresh = 100.50
    rh = await gw.apply(symbol="CAP_SIM", new_sl=100.30, source="loss_cap_emergency", direction="Buy",
                        current_sl=100.10, current_price=100.50, entry_price=100.0, bypass_rate_limit=True)
    check("G1 emergency cap places on a green trade (Head admitted)", rh.accepted, f"reason={rh.reason}")
    import time as _t; _t.sleep(0.5)  # let the enqueue sink drain before the negative check
    pf_syms = [l for l in open(f"{LOGDIR}/placement_forensic.log")] if os.path.exists(f"{LOGDIR}/placement_forensic.log") else []
    check("G2 loss_cap is NOT instrumented (forensic is profit-lock-only)",
          not any("CAP_SIM" in l for l in pf_syms), "no CAP_SIM forensic line")

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)} check(s) failed: {FAILS}")
        sys.exit(1)
    print("RESULT: PASS — every shipped fix responds correctly on issue-occurring data "
          "(degrade places + records, benign/clamp cases labeled, cadence source-aware & inert, cap intact, forensic scoped).")

if __name__ == "__main__":
    asyncio.run(main())
