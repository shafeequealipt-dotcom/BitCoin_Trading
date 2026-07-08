"""F5 phantom-close fix — leg A self-verification (2026-06-08).

Proves on a REAL ``TradeCoordinator`` (no DB, no mock of the booking logic)
that the EXISTING single-writer staleness gate, when ARMED with the
trusted-local reference the watchdog poll path and the two sniper force-close
paths now pass (ref_pnl_usd / ref_pnl_pct / ref_exit_price / ref_qty /
candidate_qty / ref_is_mark=True), DEMOTES a stale exchange-authoritative row
whose exit diverges from the live MARK to the local net BEFORE booking — instead
of booking the phantom win — while NOT touching a legitimate fee-flip close.

The reference on these paths is the live MARK (not the exact fill), so the gate
uses the wider 3% exit-plausibility band (ref_is_mark=True), matching the
reconciler's gate. This catches the >~3% phantom exits WITHOUT demoting an
ordinary-slippage close — critically, a real net-loss close whose gross mark
looked like a win must NOT be reverted to that gross win (that would itself be a
transient phantom).

Cases (all real on_trade_closed bookings; the close callback only captures the
record dict):
  OFF       — phantom booked WITHOUT ref_* (the pre-fix inert gate) -> phantom win.
  ON        — phantom booked WITH ref_* + ref_is_mark -> demoted to the real loss.
  CORRECT   — genuine close, exit == mark -> not demoted.
  FEE-FLIP  — exchange NET loss whose gross MARK looked like a win, fill 0.4%
              from the mark (< 3%) -> NOT demoted; books the exchange net loss.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.trade_coordinator import TradeCoordinator

ENTRY = 0.3501
SIZE = 5710.0           # ~ $2000 notional / 0.3501 (AERO-ish)
PHANTOM_EXIT = 0.3734   # stale row exit, +6.7% above entry, +7.4% above the mark
PHANTOM_USD = +34.89
PHANTOM_PCT = +0.83
LIVE_MARK = 0.3476      # the real live mark (a loss)
REAL_USD = -34.59
REAL_PCT = -0.71


def _book(symbol: str, entry: float, size: float, **kwargs) -> dict:
    coord = TradeCoordinator()
    captured: dict = {}
    coord.register_close_callback(lambda record: captured.update(record))
    coord.register_trade(symbol=symbol, entry_price=entry, side="Buy", size=size)
    coord.on_trade_closed(symbol=symbol, **kwargs)
    return captured


# OFF — phantom WITHOUT ref_* (pre-fix inert gate).
off = _book(
    "AEROUSDT", ENTRY, SIZE,
    pnl_pct=PHANTOM_PCT, pnl_usd=PHANTOM_USD, was_win=True,
    closed_by="win_prob_near_certain", exit_price=PHANTOM_EXIT,
    price_source="exchange_authoritative",
)
# ON — phantom WITH ref_* + ref_is_mark (the fixed call sites).
on = _book(
    "AEROUSDT", ENTRY, SIZE,
    pnl_pct=PHANTOM_PCT, pnl_usd=PHANTOM_USD, was_win=True,
    closed_by="win_prob_near_certain", exit_price=PHANTOM_EXIT,
    price_source="exchange_authoritative",
    ref_pnl_usd=REAL_USD, ref_pnl_pct=REAL_PCT, ref_exit_price=LIVE_MARK,
    ref_qty=SIZE, candidate_qty=SIZE, ref_is_mark=True,
)
# CORRECT — genuine close, exit == mark.
correct = _book(
    "AEROUSDT", ENTRY, SIZE,
    pnl_pct=REAL_PCT, pnl_usd=REAL_USD, was_win=False,
    closed_by="win_prob_near_certain", exit_price=LIVE_MARK,
    price_source="exchange_authoritative",
    ref_pnl_usd=REAL_USD, ref_pnl_pct=REAL_PCT, ref_exit_price=LIVE_MARK,
    ref_qty=SIZE, candidate_qty=SIZE, ref_is_mark=True,
)
# FEE-FLIP — exchange NET loss (-3.50) whose gross MARK looked like a win (+2.0);
# fill 100.6 is 0.4% from the mark 100.2 (< 3%) -> must NOT be demoted.
feeflip = _book(
    "ETHUSDT", 100.0, 100.0,
    pnl_pct=-0.35, pnl_usd=-3.50, was_win=False,
    closed_by="hard_stop", exit_price=100.6,
    price_source="exchange_authoritative",
    ref_pnl_usd=+2.0, ref_pnl_pct=+0.20, ref_exit_price=100.2,
    ref_qty=100.0, candidate_qty=100.0, ref_is_mark=True,
)

print("=== F5 leg-A verification (real TradeCoordinator.on_trade_closed) ===")
print(f"OFF (no ref_*):  pnl_usd={off.get('pnl_usd'):+.2f} win={off.get('was_win')} src={off.get('price_source')}")
print(f"ON  (ref+mark):  pnl_usd={on.get('pnl_usd'):+.2f} win={on.get('was_win')} src={on.get('price_source')}")
print(f"CORRECT:         pnl_usd={correct.get('pnl_usd'):+.2f} win={correct.get('was_win')} src={correct.get('price_source')}")
print(f"FEE-FLIP:        pnl_usd={feeflip.get('pnl_usd'):+.2f} win={feeflip.get('was_win')} src={feeflip.get('price_source')}")

# OFF reproduces the phantom win.
assert abs(off.get("pnl_usd", 0.0) - PHANTOM_USD) < 0.01 and off.get("was_win") is True, "OFF should book the phantom win"
# ON demotes the >3% phantom to the real loss.
assert abs(on.get("pnl_usd", 0.0) - REAL_USD) < 0.5 and on.get("was_win") is False, "ON should demote to the real loss"
assert on.get("price_source") == "local_fallback_stale", "ON should tag local_fallback_stale"
# CORRECT close is not demoted.
assert correct.get("price_source") != "local_fallback_stale" and abs(correct.get("pnl_usd", 0.0) - REAL_USD) < 0.5, "correct close must not be demoted"
# FEE-FLIP (0.4% < 3%) is NOT demoted — books the exchange NET LOSS, never the gross-win mark.
assert feeflip.get("price_source") == "exchange_authoritative", "fee-flip close must keep the exchange value (3% band)"
assert abs(feeflip.get("pnl_usd", 0.0) - (-3.50)) < 0.01 and feeflip.get("was_win") is False, "fee-flip must book the exchange NET LOSS, not the gross-win mark"

print(
    "\nPASS: OFF reproduces the +$34.89 phantom; ON demotes the >3% phantom to the "
    "real -$34.59 loss; a genuine close is not demoted; and a fee-flip close (0.4% "
    "< 3% band) keeps the exchange NET LOSS (no reversion to the gross-win mark)."
)
