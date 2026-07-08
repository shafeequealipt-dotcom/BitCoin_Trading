#!/usr/bin/env python3
"""Verify the fresh-mark placeability degrade (Dynamic Adaptive Exit FIX, 2026-06-15).

Drives the REAL SLGateway.apply pipeline with a stub position service that
REPLICATES the exchange adapter's wrong-side rejection (bybit_demo_adapter
set_stop_loss: a long SL must be < pos.mark_price, a short SL must be >
pos.mark_price). The stub's get_position returns a FRESH mark distinct from the
caller-passed current_price snapshot — exactly the staleness that caused the
PYTHUSDT/MONUSDT/EGLDUSDT wire-fail give-back.

Read-only w.r.t. live state. Exits non-zero on any failed assertion.

Scenarios:
  1. FIX OFF (control) — reproduces the bug: the +fee-floor lock is wrong-side of
     the fresh mark, wire fails, nothing placed.
  2. FIX ON — repairs it: the gateway re-validates against the fresh mark and
     degrades to the placeable breakeven stop; the wire succeeds.
  3. Real winner (lock well outside min-distance) — NOT degraded; the real lock wires.
  4. Tighten-only preserved — when even the fresh boundary cannot improve on the
     existing stop, the gateway no-ops (keeps the placed stop), never loosens.
  5. Short-side symmetry — a Sell whose lock is wrong-side of the fresh mark degrades.
  6. Loss path untouched — a far loss-cap stop wires unchanged (no false trigger).
"""
import asyncio
import sys
from types import SimpleNamespace

FAILS = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


class StubPos:
    """Replicates the adapter: set_stop_loss rejects a wrong-side SL against the
    FRESH mark; get_position returns that fresh mark. Per-symbol (mark, is_long)."""
    def __init__(self):
        self._mark = {}        # symbol -> fresh mark price
        self._long = {}        # symbol -> is_long
        self.placed = {}       # symbol -> last successfully placed SL
        self.rejected = {}     # symbol -> count of wrong-side rejects

    def set(self, symbol, mark, is_long):
        self._mark[symbol] = mark
        self._long[symbol] = is_long

    async def get_position(self, symbol, *a, **k):
        m = self._mark.get(symbol)
        if m is None:
            return None
        return SimpleNamespace(mark_price=m, stop_loss=0.0, size=1.0,
                               side="Buy" if self._long.get(symbol) else "Sell")

    async def set_stop_loss(self, symbol, sl, *a, **k):
        mark = self._mark.get(symbol)
        is_long = self._long.get(symbol, True)
        if mark and mark > 0 and sl and sl > 0:
            wrong = (is_long and sl >= mark) or ((not is_long) and sl <= mark)
            if wrong:
                self.rejected[symbol] = self.rejected.get(symbol, 0) + 1
                return False        # adapter would emit SET_SL_DIRECTION_BUG
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


def _mk_gateway(settings):
    from src.core.sl_gateway import SLGateway
    return SLGateway(settings=settings, position_service=StubPos(),
                     market_service=StubMkt(), event_buffer=StubEvents(),
                     volatility_profiler=None)


async def main():
    from src.config.settings import Settings

    # ── PYTHUSDT replay numbers (from the live log) ──
    ENTRY = 0.03989          # breakeven
    LOCK = 0.039934          # the +fee-floor adaptive lock (the wire-failed value)
    STALE = 0.039995         # current_price snapshot the sniper passed
    FRESH = 0.03992          # the live mark the adapter enforced against
    CUR_SL = 0.039420        # the resting stop before the lock attempt

    # ── Scenario 1 — FIX OFF (control): reproduce the bug ──
    print("== Scenario 1: FIX OFF — reproduce the wrong-side wire-fail give-back ==")
    s = Settings._load_fresh()
    s.sl_gateway.owner_switch_enforce = False
    s.sl_gateway.rate_limit_seconds = 0
    s.sl_gateway.r2_fresh_mark_degrade_enabled = False   # disable the fix
    gw = _mk_gateway(s)
    gw._position_service.set("PYTH_OFF", FRESH, True)
    res = await gw.apply(symbol="PYTH_OFF", new_sl=LOCK, source="profit_sniper_ladder",
                         direction="Buy", current_sl=CUR_SL, current_price=STALE,
                         entry_price=ENTRY, bypass_step_cap_for_breakeven=True,
                         bypass_rate_limit=True, breakeven_floor_price=ENTRY,
                         profit_lock_floor_price=LOCK)
    chk("control: lock wire-FAILS against the fresh mark (bug reproduced)",
        (not res.accepted) and gw._position_service.placed.get("PYTH_OFF") is None,
        f"accepted={res.accepted} reason={res.reason} rejected={gw._position_service.rejected.get('PYTH_OFF')}")

    # ── Scenario 2 — FIX ON: degrade to placeable breakeven ──
    print("\n== Scenario 2: FIX ON — degrade the unplaceable lock to a placeable breakeven ==")
    s2 = Settings._load_fresh()
    s2.sl_gateway.owner_switch_enforce = False
    s2.sl_gateway.rate_limit_seconds = 0
    s2.sl_gateway.r2_fresh_mark_degrade_enabled = True
    gw2 = _mk_gateway(s2)
    gw2._position_service.set("PYTH_ON", FRESH, True)
    res2 = await gw2.apply(symbol="PYTH_ON", new_sl=LOCK, source="profit_sniper_ladder",
                           direction="Buy", current_sl=CUR_SL, current_price=STALE,
                           entry_price=ENTRY, bypass_step_cap_for_breakeven=True,
                           bypass_rate_limit=True, breakeven_floor_price=ENTRY,
                           profit_lock_floor_price=LOCK)
    placed = gw2._position_service.placed.get("PYTH_ON")
    chk("fix: a stop IS placed on the exchange (no wire-fail)", res2.accepted and placed is not None,
        f"accepted={res2.accepted} placed={placed}")
    chk("fix: placed stop is on the correct side of the fresh mark", placed is not None and placed < FRESH,
        f"placed={placed} fresh={FRESH}")
    chk("fix: degraded to breakeven (>= entry, protects the green trade at flat)",
        placed is not None and placed >= ENTRY - 1e-9,
        f"placed={placed} entry={ENTRY} -> exits ~breakeven instead of the -0.34% give-back")

    # ── Scenario 3 — real winner: lock well outside min-distance, NOT degraded ──
    print("\n== Scenario 3: real winner — lock outside min-distance is NOT degraded ==")
    s3 = Settings._load_fresh()
    s3.sl_gateway.owner_switch_enforce = False
    s3.sl_gateway.rate_limit_seconds = 0
    gw3 = _mk_gateway(s3)
    WIN_PRICE, WIN_LOCK, WIN_FRESH = 1.05, 1.03, 1.0499   # stop ~1.9% below price
    gw3._position_service.set("WIN", WIN_FRESH, True)
    res3 = await gw3.apply(symbol="WIN", new_sl=WIN_LOCK, source="profit_sniper_ladder",
                           direction="Buy", current_sl=1.01, current_price=WIN_PRICE,
                           entry_price=1.00, bypass_step_cap_for_breakeven=True,
                           bypass_rate_limit=True, breakeven_floor_price=1.00,
                           profit_lock_floor_price=WIN_LOCK)
    chk("winner: the real lock wires unchanged (no false degrade)",
        res3.accepted and abs(gw3._position_service.placed.get("WIN", 0) - WIN_LOCK) < 1e-9,
        f"accepted={res3.accepted} placed={gw3._position_service.placed.get('WIN')} expected={WIN_LOCK}")

    # ── Scenario 4 — tighten-only preserved: degrade cannot improve -> no-op ──
    print("\n== Scenario 4: tighten-only — degrade that cannot improve no-ops (never loosens) ==")
    s4 = Settings._load_fresh()
    s4.sl_gateway.owner_switch_enforce = False
    s4.sl_gateway.rate_limit_seconds = 0
    gw4 = _mk_gateway(s4)
    # current stop already TIGHTER (higher, for a long) than any placeable fresh stop
    gw4._position_service.set("TIGHT", FRESH, True)
    ALREADY = 0.039900   # above breakeven and the fresh boundary; degrade can't beat it
    res4 = await gw4.apply(symbol="TIGHT", new_sl=LOCK, source="profit_sniper_ladder",
                           direction="Buy", current_sl=ALREADY, current_price=STALE,
                           entry_price=ENTRY, bypass_step_cap_for_breakeven=True,
                           bypass_rate_limit=True, breakeven_floor_price=ENTRY,
                           profit_lock_floor_price=LOCK)
    chk("tighten-only: no looser stop wired; existing tighter stop kept",
        (not res4.accepted) and gw4._position_service.placed.get("TIGHT") is None,
        f"accepted={res4.accepted} reason={res4.reason} placed={gw4._position_service.placed.get('TIGHT')}")

    # ── Scenario 5 — short-side symmetry ──
    print("\n== Scenario 5: short-side symmetry — Sell lock wrong-side of fresh mark degrades ==")
    s5 = Settings._load_fresh()
    s5.sl_gateway.owner_switch_enforce = False
    s5.sl_gateway.rate_limit_seconds = 0
    gw5 = _mk_gateway(s5)
    # Short: entry 0.02257, profit when price falls; lock below entry; fresh mark popped up
    S_ENTRY, S_LOCK, S_STALE, S_FRESH, S_CUR = 0.022569, 0.022544, 0.022540, 0.022563, 0.022700
    gw5._position_service.set("MON", S_FRESH, False)
    res5 = await gw5.apply(symbol="MON", new_sl=S_LOCK, source="profit_sniper_ladder",
                           direction="Sell", current_sl=S_CUR, current_price=S_STALE,
                           entry_price=S_ENTRY, bypass_step_cap_for_breakeven=True,
                           bypass_rate_limit=True, breakeven_floor_price=S_ENTRY,
                           profit_lock_floor_price=S_LOCK)
    placed5 = gw5._position_service.placed.get("MON")
    chk("short: a stop IS placed (no wire-fail) on the correct side of the fresh mark",
        res5.accepted and placed5 is not None and placed5 > S_FRESH,
        f"accepted={res5.accepted} placed={placed5} fresh={S_FRESH}")

    # ── Scenario 6 — loss path untouched (far stop, non-profit source) ──
    print("\n== Scenario 6: loss path untouched — far loss_cap stop wires unchanged ==")
    s6 = Settings._load_fresh()
    s6.sl_gateway.owner_switch_enforce = False
    s6.sl_gateway.rate_limit_seconds = 0
    gw6 = _mk_gateway(s6)
    L_PRICE, L_SL, L_FRESH = 1.00, 0.97, 0.9999   # stop 3% below price, far outside min-dist
    gw6._position_service.set("LOSS", L_FRESH, True)
    res6 = await gw6.apply(symbol="LOSS", new_sl=L_SL, source="loss_cap", direction="Buy",
                           current_sl=0.95, current_price=L_PRICE, entry_price=1.00,
                           bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    chk("loss: far loss_cap stop wires unchanged (no false trigger)",
        res6.accepted and abs(gw6._position_service.placed.get("LOSS", 0) - L_SL) < 1e-9,
        f"accepted={res6.accepted} placed={gw6._position_service.placed.get('LOSS')} expected={L_SL}")

    # ── Scenario 7 — log-only mode preserves the observe-only contract ──
    print("\n== Scenario 7: log-only mode — observe-only, behavior UNCHANGED ==")
    s7 = Settings._load_fresh()
    s7.sl_gateway.owner_switch_enforce = False
    s7.sl_gateway.rate_limit_seconds = 0
    s7.sl_gateway.r2_fresh_mark_degrade_enabled = True
    s7.sl_gateway.log_only_fresh_mark_degrade = True     # observe only — do not act
    gw7 = _mk_gateway(s7)
    gw7._position_service.set("PYTH_LO", FRESH, True)
    res7 = await gw7.apply(symbol="PYTH_LO", new_sl=LOCK, source="profit_sniper_ladder",
                           direction="Buy", current_sl=CUR_SL, current_price=STALE,
                           entry_price=ENTRY, bypass_step_cap_for_breakeven=True,
                           bypass_rate_limit=True, breakeven_floor_price=ENTRY,
                           profit_lock_floor_price=LOCK)
    chk("log-only: observe-only — same outcome as fix-off (wire_fail, NOT reclassified wrong_side)",
        (not res7.accepted) and res7.reason == "wire_fail"
        and gw7._position_service.placed.get("PYTH_LO") is None,
        f"accepted={res7.accepted} reason={res7.reason} placed={gw7._position_service.placed.get('PYTH_LO')}")

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)}: {', '.join(FAILS)}")
        sys.exit(1)
    print("RESULT: PASS — the fresh-mark degrade reproduces the bug with the fix off, "
          "repairs it with the fix on (places a placeable breakeven instead of "
          "wire-failing), leaves real winners and far loss stops untouched, preserves "
          "tighten-only, and is symmetric for shorts.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(main())
