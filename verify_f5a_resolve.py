"""F5-a — resolve-level exit-divergence gate self-verification (2026-06-08).

The watchdog strategic-action self-close paths (plan_timer / trailing_stop /
early_exit / hard_stop / timeout / profit_take and siblings) funnel through
TradeCoordinator.resolve_authoritative_pnl. Its existing qty gate misses a
STALE row that happens to share THIS trade's qty (a same-size earlier close the
qty-only adapter match can grab). F5-a adds an exit-divergence check at this
single chokepoint, using the live mark (fallback_exit_price) and the 3%
plausibility band, GATED to non-identity callers (order_id / ws_exec_price both
None) so the confirmed-working WS identity path is untouched.

Verifies (real TradeCoordinator.resolve_authoritative_pnl, mocked get_last_close):
  same-qty + >3% exit divergence, NON-identity -> demoted to local_fallback_stale.
  same-qty + <3% exit divergence (slippage) -> books exchange_authoritative.
  same-qty + >3% exit divergence, IDENTITY (order_id passed) -> NOT demoted.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.trade_coordinator import TradeCoordinator


def _coord_with_trade(symbol: str, entry: float, size: float) -> TradeCoordinator:
    c = TradeCoordinator()
    c.register_trade(symbol=symbol, entry_price=entry, side="Buy", size=size)
    return c


def _svc(exit_price: float, net_usd: float, qty: float) -> MagicMock:
    svc = MagicMock()
    svc.get_last_close = AsyncMock(return_value={
        "net_pnl_usd": net_usd, "net_pnl_pct": net_usd / 100.0,
        "exit_price": exit_price, "qty": qty,
    })
    return svc


async def main() -> None:
    # ENTRY 100, qty 100, live mark 100.2. A same-qty STALE row at exit 110 (+9.8%
    # from the mark) booking a fake +50 win — the qty gate passes (qty matches),
    # the exit-divergence gate must catch it.
    c1 = _coord_with_trade("AAAUSDT", 100.0, 100.0)
    usd1, _, src1, exit1 = await c1.resolve_authoritative_pnl(
        symbol="AAAUSDT", position_service=_svc(110.0, +50.0, 100.0),
        fallback_pnl_usd=-3.0, fallback_pnl_pct=-0.03, fallback_exit_price=100.2,
        qty=100.0,
    )

    # Same trade, a REAL row at exit 100.6 (+0.4% from the mark, ordinary slippage)
    # booking the exchange net loss -3.50 — must NOT be demoted.
    c2 = _coord_with_trade("BBBUSDT", 100.0, 100.0)
    usd2, _, src2, _ = await c2.resolve_authoritative_pnl(
        symbol="BBBUSDT", position_service=_svc(100.6, -3.50, 100.0),
        fallback_pnl_usd=+2.0, fallback_pnl_pct=0.02, fallback_exit_price=100.2,
        qty=100.0,
    )

    # IDENTITY caller (order_id passed): the >9.8% divergent row must NOT be
    # demoted by the resolve-level gate (the WS identity path is preserved;
    # its own on_trade_closed identity logic governs it).
    c3 = _coord_with_trade("CCCUSDT", 100.0, 100.0)
    usd3, _, src3, _ = await c3.resolve_authoritative_pnl(
        symbol="CCCUSDT", position_service=_svc(110.0, +50.0, 100.0),
        fallback_pnl_usd=-3.0, fallback_pnl_pct=-0.03, fallback_exit_price=100.2,
        qty=100.0, order_id="real-closing-oid",
    )

    print("=== F5-a resolve-level exit-divergence verification ===")
    print(f"same-qty >3% NON-identity : src={src1} usd={usd1:+.2f} exit={exit1}")
    print(f"same-qty <3% slippage     : src={src2} usd={usd2:+.2f}")
    print(f"same-qty >3% IDENTITY      : src={src3} usd={usd3:+.2f}")

    assert src1 == "local_fallback_stale" and usd1 == -3.0, "same-qty >3% phantom must demote to local (qty gate alone misses it)"
    assert src2 == "exchange_authoritative" and abs(usd2 - (-3.50)) < 1e-6, "ordinary <3% slippage must keep the exchange net"
    assert src3 == "exchange_authoritative" and abs(usd3 - 50.0) < 1e-6, "identity (order_id) path must NOT be demoted by the resolve gate"

    print(
        "\nPASS: a same-qty stale row at >3% exit divergence is demoted (the qty "
        "gate alone misses it); ordinary <3% slippage keeps the exchange net; and "
        "the WS identity (order_id) path is untouched."
    )


asyncio.run(main())
