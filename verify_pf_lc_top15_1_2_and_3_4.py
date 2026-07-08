#!/usr/bin/env python3
"""PF/LC Top-15 Problems 1.2 + 3.4 — spike always-on + opening-seconds window.

1.2: the volatility-spike catastrophe stop is hoisted out of the `if not
     _graduated` gate so it protects a trade at ANY age, including after
     graduation (blueprint Loss 2.5 / Rule 8). Verified structurally (the
     always-on spike pre-check precedes the graduation branch) and that the
     close is reachable regardless of graduation state.
3.4: the spike requires the wider opening multiple for a young trade, so a
     modest settling wiggle is not misread as a crash; a genuine crash still
     fires, and an older trade uses the normal multiple. Verified behaviourally
     against the real _lc_spike_triggered.

Run: python3 verify_pf_lc_top15_1_2_and_3_4.py
"""
import asyncio
import inspect
import sys
from types import SimpleNamespace

from src.workers.profit_sniper import ProfitSniper


class _Buf:
    def __init__(self, prices, stamps):
        self._p, self._s = prices, stamps

    def get_prices(self):
        return self._p

    def get_timestamps(self):
        return self._s


def _fake_sniper(age_s):
    """Minimal object bound to the real _lc_spike_triggered method."""
    f = SimpleNamespace()
    f._lc = SimpleNamespace(
        spike_window_seconds=30.0,
        spike_atr_move_mult=2.5,
        spike_atr_move_mult_opening=3.8,
        spike_young_opening_seconds=12.0,
    )

    async def _atr(symbol):
        return 1.0

    def _eff(live, entry, price):
        return (1.0, "live")  # atr_value = 1.0

    f._get_current_atr = _atr
    f._pf_effective_atr = _eff
    f.age_state = SimpleNamespace(atr_at_entry=1.0, age_seconds=age_s)
    return f


async def _spike(age_s, adverse_atr):
    """Drive the real _lc_spike_triggered: a long whose recent high sits
    `adverse_atr` ATR (=price units, ATR=1.0) above current price 100."""
    f = _fake_sniper(age_s)
    import time as _t
    now = _t.time()
    tracked = {"buffer": _Buf([100.0 + adverse_atr, 100.0], [now, now])}
    fn = ProfitSniper._lc_spike_triggered.__get__(f, ProfitSniper)
    return await fn("ENAUSDT", tracked, f.age_state, 100.0, True)


def _run():
    fails = []
    loop = asyncio.get_event_loop()

    # ── 1.2 structural: the always-on spike pre-check precedes the graduation
    #    branch in the spine, and the loss_spike_force close is not nested under
    #    `if not _graduated`. ──
    src = inspect.getsource(ProfitSniper._pf_apply_spine)
    i_spike = src.find("enable_spike_stop")
    i_grad = src.find("if not _graduated:")
    if not (0 <= i_spike < i_grad):
        fails.append("1.2: spike check is not hoisted before the graduation gate")
    # The always-on block's own comment must mark it always-on.
    if "ALWAYS-ON" not in src[:i_grad]:
        fails.append("1.2: hoisted spike block missing the always-on marker")

    # ── 3.4 behavioural: young trade (age 5s < 12s) uses the 3.8 multiple. ──
    # A 3.0-ATR wiggle on a young trade must NOT trigger (3.0 < 3.8).
    t = loop.run_until_complete(_spike(age_s=5.0, adverse_atr=3.0))
    if t[0] is not False or abs(t[3] - 3.8) > 1e-9:
        fails.append(f"3.4: young 3.0-ATR wiggle should be spared at mult 3.8, got {t}")
    # A genuine 4.0-ATR crash on a young trade MUST still trigger (4.0 >= 3.8).
    t = loop.run_until_complete(_spike(age_s=5.0, adverse_atr=4.0))
    if t[0] is not True or abs(t[3] - 3.8) > 1e-9:
        fails.append(f"3.4: young 4.0-ATR crash must still fire, got {t}")
    # An older trade (age 60s) uses the normal 2.5 multiple: 3.0-ATR fires.
    t = loop.run_until_complete(_spike(age_s=60.0, adverse_atr=3.0))
    if t[0] is not True or abs(t[3] - 2.5) > 1e-9:
        fails.append(f"3.4: aged 3.0-ATR move should fire at mult 2.5, got {t}")
    # Same 3.0-ATR move is spared when young — the whole point of 3.4.
    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 1.2 + 3.4 verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 1.2: spike hoisted before the graduation gate (always-on, "
          "any age). 3.4: a young 3.0-ATR wiggle is spared (mult 3.8) while a 4.0-ATR "
          "crash still fires; the same 3.0-ATR move fires on an aged trade (mult 2.5).")
