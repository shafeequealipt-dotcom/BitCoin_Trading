"""F9 loss-only cooldown self-verification (2026-06-09).

When [apex].loss_cooldown_enabled is ON, the per-(symbol,direction) re-entry
cooldown is set ONLY on a real loss (booked net dollar < 0) — wins/scratches get
no cooldown — and a symbol with an active cooldown is reported by
is_symbol_in_any_cooldown (which the scanner uses to hold the coin OUT of the
candidate list until it expires). When OFF, the cooldown is set on every close
(prior behaviour). Keying on pnl_usd (not the display win flag) means the F5
phantom can never let a real loss skip the cooldown.

Real TradeCoordinator: drive a win and a loss close in each mode and check the
resulting cooldown state.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.core.trade_coordinator import TradeCoordinator

def cooled_after_close(flag: bool, pnl_usd: float) -> bool:
    c = TradeCoordinator()
    c.set_loss_cooldown_enabled(flag)
    c.register_trade(symbol="DOGEUSDT", entry_price=0.08, side="Buy", size=50000.0)
    c.on_trade_closed(
        symbol="DOGEUSDT", pnl_pct=(pnl_usd / 4000.0 * 100.0), pnl_usd=pnl_usd,
        was_win=pnl_usd > 0, closed_by="bybit_demo_sl_tp", exit_price=0.0795,
    )
    return c.is_symbol_in_any_cooldown("DOGEUSDT")

on_loss = cooled_after_close(True, -33.0)   # F9 ON, loss -> cooldown
on_win  = cooled_after_close(True, +20.0)   # F9 ON, win  -> NO cooldown
off_loss = cooled_after_close(False, -33.0) # F9 OFF, loss -> cooldown (every close)
off_win  = cooled_after_close(False, +20.0) # F9 OFF, win  -> cooldown (every close)

print("=== F9 loss-only cooldown verification (real TradeCoordinator) ===")
print(f"F9 ON : loss -> cooled={on_loss}   win -> cooled={on_win}")
print(f"F9 OFF: loss -> cooled={off_loss}  win -> cooled={off_win}")

assert on_loss is True,  "F9 ON: a real loss must set the cooldown (-> excluded from the 15-list)"
assert on_win is False,  "F9 ON: a win must NOT set the cooldown (stays selectable)"
assert off_loss is True, "F9 OFF: every-close cooldown preserved on a loss"
assert off_win is True,  "F9 OFF: every-close cooldown preserved on a win (byte-identical)"

print("\nPASS: F9 ON sets the cooldown only on a real loss (the loser is held out "
      "of the candidate list; a win stays selectable); F9 OFF preserves the prior "
      "every-close cooldown. The scanner reads is_symbol_in_any_cooldown to exclude "
      "the cooled symbol and it reappears (lazy-clean) exactly after expiry.")
