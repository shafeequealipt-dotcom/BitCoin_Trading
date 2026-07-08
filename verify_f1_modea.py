"""F1 Mode-A self-verification (2026-06-08).

Mode-A is the corruption where actual_pnl_pct stays 0.0 while actual_pnl_usd is a
real non-zero PnL (the adapter could not derive pct at source because avg_entry/
qty were missing, and no price back-derive ran). The coordinator's T2-8 corrector
is now extended to re-derive pct from the authoritative dollar / notional when
pct==0 with a real dollar, so the percent agrees with the dollar instead of
lying as 0.0.

Real TradeCoordinator.on_trade_closed: a -$4.93 loss arrives with pnl_pct=0 and
no exit price (so the price back-derive cannot run). The booked record must carry
the dollar-derived percent, not 0.0.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.core.trade_coordinator import TradeCoordinator

coord = TradeCoordinator()
captured: dict = {}
coord.register_close_callback(lambda r: captured.update(r))
# notional = size * entry = 2.5 * 2000 = 5000
coord.register_trade(symbol="ETHUSDT", entry_price=2000.0, side="Buy", size=2.5)
coord.on_trade_closed(
    symbol="ETHUSDT", pnl_pct=0.0, pnl_usd=-4.93, was_win=True,  # caller mis-says win
    closed_by="bybit_demo_sl_tp", exit_price=None,
    price_source="exchange_authoritative",
)
pct = captured.get("pnl_pct")
win = captured.get("was_win")
expected = -4.93 / 5000.0 * 100.0   # = -0.0986%
print("=== F1 Mode-A verification (real on_trade_closed) ===")
print(f"booked pnl_pct={pct:+.4f}%  pnl_usd={captured.get('pnl_usd'):+.4f}  was_win={win}")
print(f"expected pct from dollar = {expected:+.4f}%")
assert pct is not None and abs(pct - expected) < 1e-6, "Mode-A pct must be re-derived from the dollar, not 0.0"
assert win is False, "was_win must follow the dollar (a loss), not the caller's claim"
assert (pct < 0) == (captured.get("pnl_usd") < 0), "pct sign must match the dollar"
print("\nPASS: a Mode-A close (pct=0, real -$4.93 dollar) is re-derived to "
      f"{pct:+.4f}% (matches the dollar), instead of being booked as a flat 0.0%.")
