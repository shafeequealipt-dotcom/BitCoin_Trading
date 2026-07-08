"""Phantom-loss fix verification — the staleness/sign gate at the single
writer (TradeCoordinator.on_trade_closed) plus the canonical net helper.

Builds a REAL TradeCoordinator, registers a TradeState, and drives
on_trade_closed with stale-vs-fresh exchange rows, asserting the gate
demotes a stale prior-trade row to the trusted WS net (the root-cause fix)
and leaves a fresh matching row untouched.

Run:  .venv/bin/python verify_phantom_loss_ws_pnl.py
"""
import sys
import time

from src.core.trade_coordinator import TradeCoordinator, TradeState

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def coord_with(symbol: str, entry: float, size: float, side: str) -> TradeCoordinator:
    c = TradeCoordinator()
    c._trades[symbol] = TradeState(
        symbol=symbol, entry_price=entry, size=size, side=side, opened_at=time.time()
    )
    return c


def last(c: TradeCoordinator) -> dict:
    return c._closed_trades[-1] if c._closed_trades else {}


# ── Case A — the real MONUSDT phantom loss ──────────────────────────────
# Stale prior-trade exit 0.021344 booked -83.70 on a Buy whose true WS fill
# was 0.021062 (entry 0.021003) — a real win. exit_divergence -> demote.
sym, entry, size = "MONUSDT", 0.021003, 214255.0
c = coord_with(sym, entry, size, "Buy")
ws_exit = 0.021062
ws_net = (ws_exit - entry) * size  # gross +~12.6
c.on_trade_closed(
    sym, pnl_pct=-1.8599, pnl_usd=-83.6953, was_win=False,
    closed_by="bybit_sl_hit", exit_price=0.021344,
    price_source="exchange_authoritative",
    ref_pnl_usd=ws_net, ref_pnl_pct=(ws_net / abs(size * entry) * 100),
    ref_exit_price=ws_exit, ref_qty=size,
)
r = last(c)
check("A: stale row demoted (exit_divergence)",
      r.get("price_source") == "local_fallback_stale", f"src={r.get('price_source')}")
check("A: booked the WS net (a WIN, not -83.70)",
      r.get("pnl_usd", 0) > 0 and r.get("was_win") is True,
      f"pnl_usd={r.get('pnl_usd')} win={r.get('was_win')}")

# ── Case B — fee-driven sign flip is NOT a stale row (regression) ───────
# The exit MATCHES the WS fill, but the exchange NET is a small loss (fees)
# while the gross price-move is a profit. The exchange net is the truth and
# MUST be booked — the gate must NOT demote it (else it reverts the
# 2026-05-26 net-booking fix). This pins the fee-flip false-positive that the
# cross-check caught: sign-alone is not a stale signal.
sym, entry, size = "AAAUSDT", 100.0, 100.0
c = coord_with(sym, entry, size, "Buy")
ws_exit = 101.0
ws_net = (ws_exit - entry) * size            # +100 gross reconstruction (no fee)
c.on_trade_closed(
    sym, pnl_pct=-0.035, pnl_usd=-3.50, was_win=False,   # exchange NET (after fees)
    closed_by="bybit_sl_hit", exit_price=101.0,          # exit MATCHES ws_exit
    price_source="exchange_authoritative",
    ref_pnl_usd=ws_net, ref_pnl_pct=+1.0, ref_exit_price=ws_exit, ref_qty=size,
)
r = last(c)
check("B: fee-flip NOT demoted (matching exit -> trust the net)",
      r.get("price_source") == "exchange_authoritative", f"src={r.get('price_source')}")
check("B: keeps the exchange NET loss (-3.50, not reverted to gross +100)",
      r.get("pnl_usd") == -3.50 and r.get("was_win") is False,
      f"pnl_usd={r.get('pnl_usd')} win={r.get('was_win')}")

# ── Case C — fresh matching row passes (no false demotion) ──────────────
sym, entry, size = "BBBUSDT", 50.0, 4.0
c = coord_with(sym, entry, size, "Buy")
ws_exit = 51.0
ws_net = (ws_exit - entry) * size    # +4
c.on_trade_closed(
    sym, pnl_pct=+2.0, pnl_usd=+4.0, was_win=True,
    closed_by="bybit_sl_hit", exit_price=51.0001,   # within 0.1% rel tol
    price_source="exchange_authoritative",
    ref_pnl_usd=ws_net, ref_pnl_pct=+2.0, ref_exit_price=ws_exit, ref_qty=size,
)
r = last(c)
check("C: fresh matching row NOT demoted",
      r.get("price_source") == "exchange_authoritative", f"src={r.get('price_source')}")
check("C: keeps the (correct) exchange net",
      r.get("pnl_usd") == 4.0 and r.get("was_win") is True,
      f"pnl_usd={r.get('pnl_usd')} win={r.get('was_win')}")

# ── Case D — qty mismatch demoted (different-size prior trade) ──────────
sym, entry, size = "DDDUSDT", 10.0, 100.0
c = coord_with(sym, entry, size, "Buy")
ws_exit = 10.2
ws_net = (ws_exit - entry) * size    # +20
c.on_trade_closed(
    sym, pnl_pct=-3.0, pnl_usd=-30.0, was_win=False,
    closed_by="bybit_sl_hit", exit_price=10.2,        # exit matches!
    price_source="exchange_authoritative",
    ref_pnl_usd=ws_net, ref_pnl_pct=+2.0, ref_exit_price=ws_exit,
    ref_qty=size, candidate_qty=37.0,                 # stale row's qty differs
)
r = last(c)
check("D: qty mismatch demoted (even when exit matches)",
      r.get("price_source") == "local_fallback_stale", f"src={r.get('price_source')}")

# ── Case G — legacy caller (no reference) -> gate is a no-op ────────────
sym, entry, size = "GGGUSDT", 10.0, 5.0
c = coord_with(sym, entry, size, "Buy")
c.on_trade_closed(
    sym, pnl_pct=-1.0, pnl_usd=-5.0, was_win=False,
    closed_by="watchdog", exit_price=9.9, price_source="exchange_authoritative",
)
r = last(c)
check("G: legacy no-ref path untouched",
      r.get("price_source") == "exchange_authoritative", f"src={r.get('price_source')}")

# ── Case H — canonical net helper (_local_pnl_from_ws) ──────────────────
c = TradeCoordinator()
st = TradeState(symbol="X", entry_price=100.0, size=2.0, side="Buy")
_, usd = c._local_pnl_from_ws(st, 101.0, exec_fee=0.5)        # (101-100)*2 - 0.5
check("H: Buy net with fee", abs(usd - 1.5) < 1e-9, f"usd={usd}")
st2 = TradeState(symbol="Y", entry_price=100.0, size=2.0, side="Sell")
_, usd2 = c._local_pnl_from_ws(st2, 99.0, exec_fee=0.0)       # Sell, fell 1 -> +2
check("H: Sell sign correct", abs(usd2 - 2.0) < 1e-9, f"usd={usd2}")
_, usd3 = c._local_pnl_from_ws(st, 101.0, exec_fee=0.5, exec_pnl=7.7)
check("H: execPnl preferred when present", abs(usd3 - 7.7) < 1e-9, f"usd={usd3}")

# ── Resolve-path gate — covers the 11 watchdog/sniper self-close paths ──
# These call coordinator.resolve_authoritative_pnl directly with a proxy that
# reaches the (stale-prone) closed-pnl query. The qty-primary gate inside
# resolve uses the coordinator's own trade state, so no call-site edits are
# needed. Driven through the REAL resolve method with a stub proxy.
import asyncio


class _StubProxy:
    """Mimics the _PositionProxy: async get_last_close -> a bybit_demo
    closed-pnl-shaped dict."""

    def __init__(self, row):
        self._row = row

    async def get_last_close(self, symbol, **kw):
        return self._row


async def _resolve(coord, symbol, row, fb_usd, fb_pct, fb_exit):
    return await coord.resolve_authoritative_pnl(
        symbol=symbol, position_service=_StubProxy(row),
        fallback_pnl_usd=fb_usd, fallback_pnl_pct=fb_pct, fallback_exit_price=fb_exit,
    )


# R1 — stale row (wrong qty) -> demoted to the caller's local fallback
c = coord_with("RRRUSDT", 10.0, 100.0, "Buy")
stale_row = {"net_pnl_usd": -50.0, "net_pnl_pct": -5.0, "exit_price": 9.5, "qty": 37.0}
u, p, src, ex = asyncio.run(_resolve(c, "RRRUSDT", stale_row, 2.0, 0.2, 10.02))
check("R1: resolve demotes stale-qty row (watchdog/sniper coverage)",
      src == "local_fallback_stale", f"src={src}")
check("R1: returns the caller's local fallback (not the -50 stale)", u == 2.0, f"usd={u}")

# R2 — matching qty -> the exchange net is trusted
c = coord_with("SSSUSDT", 10.0, 100.0, "Buy")
good_row = {"net_pnl_usd": -3.2, "net_pnl_pct": -0.32, "exit_price": 10.2, "qty": 100.0}
u, p, src, ex = asyncio.run(_resolve(c, "SSSUSDT", good_row, 20.0, 2.0, 10.2))
check("R2: matching qty -> exchange net trusted", src == "exchange_authoritative", f"src={src}")
check("R2: books the exchange net (-3.2, not the +20 fallback)", u == -3.2, f"usd={u}")

# R3 — row without qty (Shadow has no lag) -> not gated
c = coord_with("TTTUSDT", 10.0, 100.0, "Buy")
shadow_row = {"net_pnl_usd": -1.5, "net_pnl_pct": -0.15, "exit_price": 9.99}
u, p, src, ex = asyncio.run(_resolve(c, "TTTUSDT", shadow_row, 0.0, 0.0, 9.99))
check("R3: no-qty row (Shadow) not gated", src == "exchange_authoritative", f"src={src}")

print(f"\n{'=' * 52}\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL PHANTOM-LOSS VERIFY CASES PASSED")
sys.exit(0)
