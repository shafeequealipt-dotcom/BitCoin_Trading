#!/usr/bin/env python3
"""PF/LC Top-15 Problem 1.4 — applied-stop logging verification.

Read-only proof that the gateway's result carries the value it ACTUALLY wrote
after an R2 clamp (result.new_sl_applied), which is the value the four
propagation sites now mirror and log instead of the pre-gateway target. This
is what makes a clamp (e.g. the Problem 1.1 sub-breakeven floor) visible.

Run: python3 verify_pf_lc_top15_1_4.py
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
    async def get_ticker(self, symbol):
        return SimpleNamespace(last_price=100.0)


def _cfg():
    return SimpleNamespace(
        sl_gateway=SimpleNamespace(
            enabled=True, log_only_global=False,
            min_distance_pct=0.30, max_step_pct=5.0, rate_limit_seconds=0,
            log_only_tighten_only=False, log_only_min_distance=False,
            log_only_max_step=False, log_only_rate_limit=False,
            min_distance_atr_multiplier=0.5, min_distance_abs_floor_pct=0.05,
        )
    )


def _run():
    failures = []
    gw = SLGateway(_cfg(), _PosSvc(), _MktSvc())

    # Long: price 100, current_sl 98, target 99.90 is only 0.10% from price —
    # inside the 0.30% min-distance. R2 must clamp to 99.70 (the boundary).
    res = asyncio.get_event_loop().run_until_complete(
        gw.apply(
            symbol="TESTUSDT", new_sl=99.90, source="profit_sniper_ladder",
            direction="Buy", current_sl=98.0, current_price=100.0,
        )
    )
    if not res.accepted:
        failures.append(f"expected accepted on clamp, got reason={res.reason!r}")
    if res.new_sl_applied is None:
        failures.append("new_sl_applied is None — the field 1.4 consumes is empty")
    elif abs(res.new_sl_applied - 99.70) > 1e-6:
        failures.append(
            f"expected applied=99.70 (R2 boundary), got {res.new_sl_applied}"
        )
    # The whole point of 1.4: applied differs from the target the sniper logged.
    if res.new_sl_applied is not None and abs(res.new_sl_applied - 99.90) < 1e-9:
        failures.append("applied == target — a clamp would be invisible (the 1.4 bug)")

    # A stop with room: target 99.50 (0.50% away) is NOT clamped; applied==target.
    gw2 = SLGateway(_cfg(), _PosSvc(), _MktSvc())
    res2 = asyncio.get_event_loop().run_until_complete(
        gw2.apply(
            symbol="TESTUSDT", new_sl=99.50, source="profit_sniper_ladder",
            direction="Buy", current_sl=98.0, current_price=100.0,
        )
    )
    if not res2.accepted or res2.new_sl_applied is None:
        failures.append("unclamped move should accept with a populated applied value")
    elif abs(res2.new_sl_applied - 99.50) > 1e-6:
        failures.append(
            f"unclamped applied should equal target 99.50, got {res2.new_sl_applied}"
        )
    return failures


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 1.4 applied-stop verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 1.4: gateway result.new_sl_applied carries the clamped "
          "value (99.70 vs target 99.90); the four propagation sites now mirror "
          "and log it, so an R2 clamp is visible. Unclamped moves report the "
          "target unchanged.")
