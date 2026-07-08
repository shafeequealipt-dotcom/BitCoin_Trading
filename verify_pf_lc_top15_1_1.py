#!/usr/bin/env python3
"""PF/LC Top-15 Problem 1.1 — R2 breakeven-floor verification.

Read-only proof that the gateway's R2 min-distance clamp now holds an armed
breakeven floor AT (or above) breakeven on a high-volatility coin, instead of
rewriting it BELOW breakeven (the live 68x/30-symbol defeat). Drives the real
SLGateway. R1 tighten-only and min-distance for non-floor writes are unchanged.

Run: python3 verify_pf_lc_top15_1_1.py
"""
import asyncio
import sys
from types import SimpleNamespace

from src.core.sl_gateway import SLGateway


class _PosSvc:
    async def set_stop_loss(self, symbol, new_sl):
        return True

    async def get_position(self, symbol):
        return None


class _MktSvc:
    def __init__(self, price):
        self._p = price

    async def get_ticker(self, symbol):
        return SimpleNamespace(last_price=self._p)


def _cfg(eff_min_pct=0.60, r2_be_enabled=True):
    # High-vol coin: a large min-distance so eff_min exceeds the +0.2% graduation
    # gain, which is exactly when R2 used to push the floor sub-breakeven.
    return SimpleNamespace(
        sl_gateway=SimpleNamespace(
            enabled=True, log_only_global=False,
            min_distance_pct=eff_min_pct, max_step_pct=50.0, rate_limit_seconds=0,
            log_only_tighten_only=False, log_only_min_distance=False,
            log_only_max_step=False, log_only_rate_limit=False,
            min_distance_atr_multiplier=0.5, min_distance_abs_floor_pct=0.05,
            r2_breakeven_floor_enabled=r2_be_enabled,
        )
    )


async def _apply(cfg, *, price, **kw):
    gw = SLGateway(cfg, _PosSvc(), _MktSvc(price))
    return await gw.apply(current_price=price, **kw)


def _run():
    loop = asyncio.get_event_loop()
    fails = []
    ENTRY = 100.0

    # ── LONG, high-vol squeeze: entry 100, price 100.2 (+0.2% graduated),
    #    floor target 100.05 (entry+0.05% lock), prior stop 98 (loss stop).
    #    R2 boundary = 100.2*(1-0.6%) = 99.5988 (BELOW breakeven). ──
    base = dict(symbol="ENAUSDT", source="profit_sniper_ladder",
                direction="Buy", current_sl=98.0, new_sl=100.05)

    # WITHOUT the breakeven price → old behaviour: clamps sub-breakeven.
    r_old = loop.run_until_complete(_apply(_cfg(), price=100.2, **base))
    if not (r_old.accepted and r_old.new_sl_applied < ENTRY):
        fails.append(
            f"control(no-be): expected sub-breakeven clamp, got "
            f"accepted={r_old.accepted} applied={r_old.new_sl_applied}"
        )

    # WITH the breakeven price → floor held at breakeven (100.0).
    r_new = loop.run_until_complete(
        _apply(_cfg(), price=100.2, breakeven_floor_price=ENTRY, **base)
    )
    if not r_new.accepted:
        fails.append(f"long-fix: expected accepted, reason={r_new.reason!r}")
    elif abs(r_new.new_sl_applied - ENTRY) > 1e-6:
        fails.append(
            f"long-fix: expected floor held at breakeven {ENTRY}, got "
            f"{r_new.new_sl_applied}"
        )

    # ── SHORT, high-vol squeeze: entry 100, price 99.8 (+0.2% short),
    #    floor target 99.95, prior stop 102. R2 boundary = 99.8*(1+0.6%) =
    #    100.3988 (ABOVE breakeven = a loss for a short). ──
    sbase = dict(symbol="ENAUSDT", source="profit_sniper_ladder",
                 direction="Sell", current_sl=102.0, new_sl=99.95)
    rs = loop.run_until_complete(
        _apply(_cfg(), price=99.8, breakeven_floor_price=ENTRY, **sbase)
    )
    if not rs.accepted:
        fails.append(f"short-fix: expected accepted, reason={rs.reason!r}")
    elif abs(rs.new_sl_applied - ENTRY) > 1e-6:
        fails.append(
            f"short-fix: expected floor held at breakeven {ENTRY}, got "
            f"{rs.new_sl_applied}"
        )

    # ── Off-switch: r2_breakeven_floor_enabled=false reverts to sub-breakeven. ──
    r_off = loop.run_until_complete(
        _apply(_cfg(r2_be_enabled=False), price=100.2,
               breakeven_floor_price=ENTRY, **base)
    )
    if not (r_off.accepted and r_off.new_sl_applied < ENTRY):
        fails.append(
            f"off-switch: expected sub-breakeven clamp when disabled, got "
            f"applied={r_off.new_sl_applied}"
        )

    # ── Non-floor source: a trail (not a breakeven source) is NOT floor-held
    #    even if a price is passed — min-distance discipline preserved. ──
    r_trail = loop.run_until_complete(
        _apply(_cfg(), price=100.2, breakeven_floor_price=ENTRY,
               symbol="ENAUSDT", source="profit_sniper_trail",
               direction="Buy", current_sl=98.0, new_sl=100.05)
    )
    if not (r_trail.accepted and r_trail.new_sl_applied < ENTRY):
        fails.append(
            f"non-floor-source: expected normal sub-breakeven clamp, got "
            f"applied={r_trail.new_sl_applied}"
        )

    # ── R1 preserved: a floor that would LOOSEN (prior stop already above
    #    breakeven) must NOT be written — held as a no-op, never loosened. ──
    r_r1 = loop.run_until_complete(
        _apply(_cfg(), price=100.2, breakeven_floor_price=ENTRY,
               symbol="ENAUSDT", source="profit_sniper_ladder",
               direction="Buy", current_sl=100.04, new_sl=100.05)
    )
    if r_r1.accepted and r_r1.new_sl_applied is not None and r_r1.new_sl_applied < 100.04:
        fails.append(
            f"R1: floor must never loosen below the existing stop 100.04, got "
            f"{r_r1.new_sl_applied}"
        )
    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 1.1 R2 breakeven-floor verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 1.1: on a high-vol coin the armed floor now holds at "
          "breakeven (long and short) instead of being clamped sub-breakeven; "
          "off-switch reverts; non-floor sources keep min-distance; R1 "
          "tighten-only never loosened.")
