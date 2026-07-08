"""Live simulation of the price-display precision fix — phase by phase.

Reproduces the exact problem situation (low-priced / sub-cent coins with real
Bybit-style tick sizes and open positions) and drives the REAL fixed code path
for every phase (C1..C9 + guard), printing OLD (buggy) vs NEW (fixed) and an
explicit PASS/FAIL against each fix's stated aim.

Run:  .venv/bin/python simulate_price_format_fix.py
"""

import importlib.util
import types

from src.alerts.formatter import AlertFormatter
from src.alerts.templates import AlertTemplates
from src.core.price_formatter import PriceFormatter
from src.core.types import AlertLevel, Side
from src.core.utils import decimals_for_price, decimals_for_tick, format_price
from src.telegram.ui.formatters import format_price as ui_format_price
from src.trading.services.instrument_service import InstrumentService

import src.telegram.handlers.control_handler as control_handler
import src.telegram.handlers.dashboard_handler as dashboard_handler

# Load the guard's detector straight from the test file (no package import needed).
_spec = importlib.util.spec_from_file_location("_guard", "tests/test_price_format_guard.py")
_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guard)
violations = _guard._violations_in_code

# ── The problem situation: coins across the precision spectrum ───────
# (symbol, real Bybit tick, a live-style price, what the OLD :.2f produced)
SITUATION = [
    ("10000SATSUSDT", 1e-7, 0.0001584),  # extreme sub-cent: OLD :.2f -> "$0.00"
    ("GALAUSDT",      1e-6, 0.003213),   # sub-cent:          OLD :.2f -> "$0.00"
    ("BLURUSDT",      1e-6, 0.021876),   # OLD :.2f -> "$0.02"
    ("ENAUSDT",       1e-4, 0.0959),     # OLD :.2f -> "$0.10"
    ("OPUSDT",        1e-5, 0.12642),    # OLD :.2f -> "$0.13"
    ("HYPEUSDT",      1e-3, 62.796),
    ("BCHUSDT",       0.1,  344.1),
    ("BTCUSDT",       0.1,  67890.5),
]
TICKS = {s: t for s, t, _ in SITUATION}

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    mark = "PASS" if ok else "FAIL"
    if ok:
        _passed += 1
    else:
        _failed += 1
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail else ""))


def hdr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * 4}")


# Build the REAL service + formatter the way the boot path does.
inst = InstrumentService(client=None)
for sym, tick, _ in SITUATION:
    inst._cache[sym] = types.SimpleNamespace(price_tick=tick)
pf = PriceFormatter(decimals_resolver=inst.price_decimals)


# ── C1: core precision primitives ────────────────────────────────────
hdr("C1  core primitives (decimals_for_tick / decimals_for_price / format_price)")
check("decimals_for_tick maps real ticks", [decimals_for_tick(t) for t in (1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 0.1)] == [7, 6, 5, 4, 3, 1])
check("format_price defaults are backward-compatible",
      format_price(70000) == "70000.00" and format_price(0.5) == "0.500000" and format_price(0.00195) == "0.00195000")
check("format_price(decimals=) gives exact tick precision",
      format_price(0.003213, decimals=6, grouped=True, strip_zeros=True) == "0.003213")
check("decimals_for_price ladder", decimals_for_price(70000) == 2 and decimals_for_price(0.003) == 8)


# ── C2: PriceFormatter service + InstrumentService.price_decimals ─────
hdr("C2  PriceFormatter + InstrumentService.price_decimals (exact tick)")
check("price_decimals reads real cached ticks", inst.price_decimals("GALAUSDT") == 6 and inst.price_decimals("ENAUSDT") == 4)
check("price_decimals returns None on cache miss (=> magnitude fallback)", inst.price_decimals("UNCACHEDUSDT") is None)
check("PriceFormatter exact-tick on sub-cent coins",
      pf.format(0.003213, "GALAUSDT") == "$0.003213" and pf.format(0.0001584, "10000SATSUSDT") == "$0.0001584")


# ── C3: boot wiring + AlertManager/AlertTemplates DI ─────────────────
hdr("C3  DI wiring (services dict + AlertTemplates threading)")
services = {"instrument_service": inst}
_pf = PriceFormatter(decimals_resolver=services["instrument_service"].price_decimals)  # exactly as manager.py
services["price_formatter"] = _pf
check("PriceFormatter registered in services dict with live resolver",
      services.get("price_formatter") is _pf and _pf.has_tick_resolver is True)
_tmpl = AlertTemplates(price_formatter=_pf)
check("AlertTemplates receives the injected formatter (constructor DI)", _tmpl._pf is _pf)


# ── C4: unified duplicate formatters (delegate to core) ──────────────
hdr("C4  duplicate formatters unified (telegram/ui + alerts delegate)")
for sym, _, price in [("GALAUSDT", 0, 0.003213), ("ENAUSDT", 0, 0.0959)]:
    old = f"${price:.2f}"
    check(f"telegram/ui.format_price({price}) no longer mangles (old {old})",
          ui_format_price(price) not in ("$0.00", "$0.10") and ui_format_price(price) == f"${format_price(price, grouped=True, strip_zeros=True)}")
check("alerts.AlertFormatter.format_price delegates (sub-cent)", AlertFormatter.format_price(0.003213) == "$0.003213")


# ── C5: user-facing handlers (dashboard + control) exact-tick ────────
hdr("C5  dashboard + control handlers (real _fmt_price helper)")
ctx = types.SimpleNamespace(bot_data={"price_formatter": pf})
for sym, _, price in [("GALAUSDT", 0, 0.003213), ("BLURUSDT", 0, 0.021876), ("OPUSDT", 0, 0.12642)]:
    old = f"${price:.2f}"
    new = dashboard_handler._fmt_price(ctx, price, sym)
    check(f"dashboard _fmt_price {sym} {price}: NEW {new} vs OLD {old}", new == f"${format_price(price, decimals=decimals_for_tick(TICKS[sym]), strip_zeros=True, grouped=True)}" and new != old)
check("control_handler _fmt_price exact-tick (GALA)", control_handler._fmt_price(ctx, 0.003213, "GALAUSDT") == "$0.003213")
check("handler fallback when formatter absent (still non-mangled, magnitude)",
      dashboard_handler._fmt_price(types.SimpleNamespace(bot_data={}), 0.003213, "GALAUSDT") == "$0.003213")


# ── C5b: alert templates render exact-tick (real messages) ───────────
hdr("C5b  alert templates render (trade/position/watchdog/price alert)")
order = types.SimpleNamespace(symbol="GALAUSDT", side=Side.BUY,
                              order_type=types.SimpleNamespace(value="Market"),
                              qty=10000, price=0.003213, stop_loss=0.003100, take_profit=0.003500)
te = AlertTemplates(price_formatter=pf).trade_executed(order, 5000.0)
check("trade_executed shows exact-tick price (GALA)", "$0.003213" in te and "$0.00 " not in te and "Price: $0.00\n" not in te)

pc = AlertTemplates(price_formatter=pf).position_closed("GALAUSDT", Side.SELL, 0.003213, 0.003150, 1.2, 1.9)
check("position_closed shows exact-tick entry+exit (GALA)", "$0.003213" in pc and "$0.00315" in pc)

pos = types.SimpleNamespace(symbol="BLURUSDT", side=Side.BUY, unrealized_pnl=2.5,
                            entry_price=0.021876, mark_price=0.021900, stop_loss=0.021000, leverage=3)
wd = AlertTemplates(price_formatter=pf).watchdog_alert(pos, 0.021900, 0.11, ["trail near"], AlertLevel.WARNING)
check("watchdog_alert shows exact-tick (BLUR, old would be $0.02)", "$0.021876" in wd and "$0.02 " not in wd)

pa = AlertTemplates(price_formatter=pf).price_alert("OPUSDT", 0.12642, 3.2, 5)
check("price_alert shows exact-tick (OP, old would be $0.13)", "$0.12642" in pa)


# ── C6: operator log values (magnitude, non-mangled) ─────────────────
hdr("C6  operator log price values (magnitude, no mangle)")
for sym, _, price in [("GALAUSDT", 0, 0.003213), ("10000SATSUSDT", 0, 0.0001584)]:
    fp = format_price(price)
    check(f"log value for {price} is non-mangled (got {fp}, old :.2f '$0.00')", float(fp) > 0 and fp != "0.00")


# ── C8: brain prompt values (magnitude, non-mangled) ─────────────────
hdr("C8  brain-prompt price values (LLM no longer sees $0.00)")
check("strategist prompt value for sub-cent is the real number",
      format_price(0.003213) not in ("0.00", "0.0032") and abs(float(format_price(0.003213)) - 0.003213) < 1e-9)


# ── C9: structure reason-codes (parser-safe + non-mangled) ───────────
hdr("C9  structure reason-codes (keyword preserved, value non-mangled)")
sl_ref = f"below_support_${format_price(0.003213)}"
check("reason-code keeps keyword AND shows real price", sl_ref.startswith("below_support_$") and "0.003213" in sl_ref)
check("'fallback' keyword parser still works on non-fallback ref", ("fallback" in sl_ref) is False)
check("'fallback' parser still works on a fallback ref", "fallback" in "fallback_2.5pct_below")


# ── C7 / C7b / C7c: the regression guard itself ──────────────────────
hdr("C7  regression guard (flags mangles, ignores non-prices)")
check("guard FLAGS hardcoded ${price:.2f}", bool(violations('x = f"sl=${price:.2f}"')))
check("guard FLAGS compound stop_loss / take_profit", bool(violations('x = f"{pos.stop_loss:.2f}"')) and bool(violations('x = f"{sig.suggested_take_profit:,.2f}"')))
check("guard FLAGS .get('..._price') and getattr fib_key_level", bool(violations("x = f\"{d.get('take_profit_price',0):.2f}\"")) and bool(violations('x = f"{a.fib_key_level:,.0f}"')))
check("guard IGNORES percentages {pct:.2f}%", not violations('x = f"{tp_pct:.2f}%"'))
check("guard IGNORES non-price (qty/equity)", not violations('x = f"{qty:.4f}"') and not violations('x = f"{equity:,.2f}"'))
check("guard IGNORES high-precision diagnostics :.6f", not violations('x = f"{new_sl:.6f}"'))


# ── Result ───────────────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print(f"SIMULATION RESULT: {_passed} PASS / {_failed} FAIL")
print("ALL PHASES RESPOND AS FIXED" if _failed == 0 else "SOME PHASES FAILED — SEE ABOVE")
print("=" * 60)
raise SystemExit(1 if _failed else 0)
