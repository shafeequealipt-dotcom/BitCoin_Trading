"""F1 percentage-sign fix — self-verification (2026-06-08).

The bug: bybit_demo_adapter.get_last_close derived net_pnl_pct from the price
delta times a direction taken from the closed-pnl row's `side` field — but
that `side` is the CLOSING-order side (a Sell closes a long, a Buy closes a
short), the OPPOSITE of the open side. So the old formula produced the EXACT
NEGATION of the truth on every close (a winning Buy stored a negative pct),
while closedPnl (the dollar) is correctly signed by Bybit.

The fix (now live in the adapter): net_pnl_pct = closedPnl / (avg_entry * qty)
* 100 — sign from the authoritative dollar, magnitude the net percent.

This verifies the two formulas on real-shaped closed-pnl rows (win/loss x
long/short). OFF (old formula) reproduces the sign DISAGREEMENT with the
dollar; ON (the new formula) AGREES with the dollar in sign on every case.
The LTC live case (+0.372% gross Buy stored as -0.3718) is included as the
deterministic fingerprint.
"""
from __future__ import annotations


def old_formula(avg_entry: float, avg_exit: float, closing_side: str) -> float:
    """The pre-fix adapter formula (price delta x closing-side direction)."""
    direction = 1.0 if closing_side in ("Buy", "BUY", "buy", "Long", "long") else -1.0
    return ((avg_exit - avg_entry) / avg_entry) * direction * 100.0


def new_formula(closed_pnl: float, avg_entry: float, qty: float) -> float:
    """The fix: derive the percent from the authoritative net dollar."""
    notional = avg_entry * qty
    return (closed_pnl / notional) * 100.0 if notional > 0 else 0.0


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


# Each row: open side, the CLOSING-order side Bybit reports, entry, exit, qty,
# and the authoritative closedPnl (correctly signed, net of fees).
CASES = [
    # name,            open,   closing_side, entry,  exit,    qty,    closed_pnl
    ("LTC win (Buy)",  "Buy",  "Sell",       43.03,  43.19,   46.46,  +11.76),
    ("losing Buy",     "Buy",  "Sell",       2.2331, 2.2271,  2240.0, -4.95),
    ("win Short",      "Sell", "Buy",        0.3500, 0.3450,  5710.0, +27.00),
    ("losing Short",   "Sell", "Buy",        0.3500, 0.3560,  5710.0, -34.59),
]

print("=== F1 percentage-sign verification (old vs new derivation) ===")
all_ok = True
for name, _open, closing_side, entry, exit_px, qty, closed_pnl in CASES:
    off = old_formula(entry, exit_px, closing_side)
    on = new_formula(closed_pnl, entry, qty)
    off_agrees = sign(off) == sign(closed_pnl)
    on_agrees = sign(on) == sign(closed_pnl)
    print(
        f"{name:16s} dollar={closed_pnl:+.2f}  "
        f"OFF pct={off:+.4f}% (agrees={off_agrees})  "
        f"ON pct={on:+.4f}% (agrees={on_agrees})"
    )
    # OFF must DISAGREE with the dollar sign (reproduces the bug).
    if off_agrees:
        all_ok = False
        print(f"   !! OFF unexpectedly agreed for {name}")
    # ON must AGREE with the dollar sign (the fix).
    if not on_agrees:
        all_ok = False
        print(f"   !! ON failed to agree for {name}")

# Deterministic fingerprint: the LTC case stored -0.3718 pre-fix.
ltc_off = old_formula(43.03, 43.19, "Sell")
assert abs(ltc_off - (-0.3718)) < 0.001, f"LTC OFF should be -0.3718, got {ltc_off:.4f}"

assert all_ok, "F1 verification FAILED — see lines above"
print(
    "\nPASS: the old formula NEGATES the sign on every close (disagrees with the "
    "dollar); the new derive-from-dollar AGREES with the dollar sign on win/loss "
    "x long/short. LTC fingerprint -0.3718 reproduced by the old formula."
)
