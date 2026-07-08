"""F5-b — entry-price disambiguation self-verification (2026-06-08).

F5-c (the freshness floor) rejects a stale PRIOR-trade row. But a LATER same-qty
re-entry that closes before this trade's reconcile completes produces a row that
POST-dates this trade's open, so the floor cannot reject it — and the qty-only
match, picking the freshest row, would grab that later row. Re-entries on the
same symbol share a qty within the 1% tolerance but have DIFFERENT entries (the
LDO case: 0.2688 / 0.2668 / 0.2659), so F5-b disambiguates by the CLOSEST
avgEntryPrice to the trade's entry.

Drives the REAL adapter row-selector (_select_close_row). Two qty-matching, fresh
rows: THIS trade (entry 0.2668, +$3.2589) and a LATER same-qty re-entry that LOST
(entry 0.2650, -$8.00, fresher). Without the entry hint the freshest (the loser)
wins — a win booked as a loss; with the entry hint this trade's own row wins.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService

B_OPEN_MS = 1780961018000.0     # this trade's open — both rows post-date it
B_CLOSE_MS = 1780961218000.0    # this trade's close
LATER_MS = 1780961400000.0      # a later same-qty re-entry's close (fresher)

this_trade_row = {  # entry 0.2668, +$3.2589 — the correct row
    "qty": "15742.1", "updatedTime": str(int(B_CLOSE_MS)),
    "avgEntryPrice": "0.2668", "avgExitPrice": "0.2661",
    "closedPnl": "3.2589", "orderId": "95fa061e", "side": "Buy",
}
later_reentry_row = {  # entry 0.2650, -$8.00, FRESHER — a different (losing) trade
    "qty": "15742.1", "updatedTime": str(int(LATER_MS)),
    "avgEntryPrice": "0.2650", "avgExitPrice": "0.2700",
    "closedPnl": "-8.0", "orderId": "later-oid", "side": "Buy",
}

select = BybitDemoPositionService._select_close_row
rows = [this_trade_row, later_reentry_row]


def pick(entry):
    r = select(
        None, rows, order_id="opening-oid-never-matches", ws_exec_price=None,
        ws_close_ts_ms=B_OPEN_MS, qty=15742.1, tick_tolerance=None,
        entry_price=entry,
    )
    return None if r is None else float(r.get("closedPnl"))


off = pick(None)          # OFF: no entry hint -> freshest (the later loser) wins
on = pick(0.2668)         # ON: closest entry (this trade) wins

print("=== F5-b entry-price disambiguation verification (real _select_close_row) ===")
print(f"OFF (no entry hint):  booked = {off}  (the bug: freshest later re-entry, a LOSS)")
print(f"ON  (entry=0.2668):   booked = {on}  (this trade's own row, the +$3.2589 win)")

assert off == -8.0, "OFF must grab the fresher later re-entry row (a win booked as a loss)"
assert on == 3.2589, "ON must pick THIS trade's row by closest entry price"

print(
    "\nPASS: with two same-qty fresh rows, no entry hint grabs the freshest (a "
    "LATER losing re-entry — a +$3.26 win booked as a -$8.00 loss); the entry hint "
    "selects this trade's own row by closest entry (+$3.2589)."
)
