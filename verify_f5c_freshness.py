"""F5-c — reconcile freshness-floor self-verification (2026-06-08).

The reconciler matches a closed-pnl row by qty only (the trade state is popped
and only the OPENING order id is known, which never matches a closed-pnl row
keyed by the CLOSING order). When a PRIOR same-symbol trade's row shares this
trade's qty within the 1% tolerance, and this trade's own row is not indexed yet
(indexer lag), the qty-only match accepted the stale prior row — the proven LDO
clobber (Trade A's 23:14 row, +$0.07, booked onto Trade B, true +$3.26).

F5-c feeds the trade's OPEN time as a freshness floor (the reconciler passes it
via reresolve_close_pnl -> ws_close_ts_ms). A closed-pnl row for THIS trade can
never pre-date its open, so the adapter rejects prior-trade rows and the
reconciler RETRIES until this trade's own row indexes.

Drives the REAL adapter row-selector (_select_close_row), using the actual LDO
epochs: Trade A close 1780960449 (stale row), Trade B open 1780961018, Trade B
close 1780961218 (fresh row); Trade A qty 15625 vs Trade B 15742.1 = 0.74% < 1%.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

A_CLOSE_MS = 1780960449000.0   # Trade A close (~23:14) — the STALE row's time
B_OPEN_MS = 1780961018000.0    # Trade B open  (~23:23:38)
B_CLOSE_MS = 1780961218000.0   # Trade B close (~23:26:58) — the FRESH row's time

stale_row = {  # Trade A (qty 15625, +$0.0701) — the wrong row the clobber grabbed
    "qty": "15625.0", "updatedTime": str(int(A_CLOSE_MS)),
    "avgEntryPrice": "0.2688", "avgExitPrice": "0.2685",
    "closedPnl": "0.0701", "orderId": "oid-A-closing", "side": "Buy",
}
fresh_row = {  # Trade B (qty 15742.1, +$3.2589) — this trade's real row
    "qty": "15742.1", "updatedTime": str(int(B_CLOSE_MS)),
    "avgEntryPrice": "0.2668", "avgExitPrice": "0.2661",
    "closedPnl": "3.2589", "orderId": "95fa061e", "side": "Buy",
}

# _select_close_row does not use self — call it unbound with self=None.
select = BybitDemoPositionService._select_close_row
OPENING_OID = "c844a7a0-opening"  # never matches a closed-pnl row (keyed by close oid)


def pick(rows, floor):
    r = select(
        None, rows, order_id=OPENING_OID, ws_exec_price=None,
        ws_close_ts_ms=floor, qty=15742.1, tick_tolerance=None,
    )
    return None if r is None else float(r.get("closedPnl"))


# Attempt 1 (Trade B's row not indexed yet — only the stale prior row is present):
off_attempt1 = pick([stale_row], None)        # OFF: no floor -> accepts the stale row
on_attempt1 = pick([stale_row], B_OPEN_MS)    # ON: floor rejects the stale row -> None (retry)
# Attempt N (Trade B's own row has now indexed):
on_attemptN = pick([stale_row, fresh_row], B_OPEN_MS)  # ON: matches THIS trade's row

print("=== F5-c reconcile freshness-floor verification (real _select_close_row) ===")
print(f"attempt 1, OFF (no floor): booked = {off_attempt1}  (the bug: stale prior row)")
print(f"attempt 1, ON  (floor):    booked = {on_attempt1}  (None -> reconciler retries)")
print(f"attempt N, ON  (floor):    booked = {on_attemptN}  (this trade's real row)")

assert off_attempt1 == 0.0701, "OFF must reproduce the clobber (grab the stale +0.0701 prior row)"
assert on_attempt1 is None, "ON must REJECT the stale prior row (older than this trade's open) and retry"
assert on_attemptN == 3.2589, "ON must match THIS trade's real row (+3.2589) once it indexes"

print(
    "\nPASS: without the floor the qty-only match grabs the stale prior-trade row "
    "(+$0.0701, the LDO clobber); with the open-time floor that row is rejected so "
    "the reconciler retries, and once this trade's own row indexes it books the "
    "true +$3.2589."
)
