#!/usr/bin/env python3
"""PF/LC Top-15 Problem 1.3 — net-booking truthful-ruler verification.

Read-only behavioural proof that the WebSocket self-close path now books the
exchange's authoritative NET closedPnl (not a gross fee-free fallback), via the
_PositionProxy wired into the coordinator. Does NOT mutate any data: it stubs
on_trade_closed to capture what would be booked.

Run: python3 verify_pf_lc_top15_1_3.py
"""
import asyncio
import sys

from src.core.trade_coordinator import TradeCoordinator


class _ProxyWithGetLastClose:
    """Mimics _PositionProxy: exposes get_last_close returning net values."""

    async def get_last_close(self, symbol):
        return {
            "net_pnl_usd": -3.2150,   # net of fees (the truthful number)
            "net_pnl_pct": -0.2940,
            "exit_price": 0.112293,
        }


class _RawTransformerNoGetLastClose:
    """Mimics the raw Transformer: NO get_last_close (the old mis-wire)."""

    current_mode = "bybit_demo"


def _run():
    failures = []

    # ── Check 1: attach_position_service stores the proxy ──
    coord = TradeCoordinator()
    proxy = _ProxyWithGetLastClose()
    coord.attach_position_service(proxy)
    if coord._position_service is not proxy:
        failures.append("attach_position_service did not store the proxy")

    # ── Check 2: close_with_authoritative_pnl resolves via the proxy and
    #            books the NET value with src=exchange_authoritative ──
    booked = {}

    def _capture(**kwargs):
        booked.update(kwargs)

    coord.on_trade_closed = _capture  # stub the sink (no DB writes)
    asyncio.get_event_loop().run_until_complete(
        coord.close_with_authoritative_pnl("ENAUSDT", 0.112293, "loss_spike_force")
    )
    if booked.get("price_source") != "exchange_authoritative":
        failures.append(
            f"expected exchange_authoritative, booked "
            f"price_source={booked.get('price_source')!r}"
        )
    if abs(booked.get("pnl_usd", 0.0) - (-3.2150)) > 1e-9:
        failures.append(
            f"expected net pnl_usd=-3.2150, booked {booked.get('pnl_usd')!r}"
        )
    if booked.get("was_win") is not False:
        failures.append(f"expected was_win=False, got {booked.get('was_win')!r}")

    # ── Check 3: with ONLY the raw transformer (no proxy), it degrades to
    #            local_fallback gross — proving the OLD path was the bug ──
    coord2 = TradeCoordinator()
    coord2.attach_transformer(_RawTransformerNoGetLastClose())
    booked2 = {}
    coord2.on_trade_closed = lambda **kw: booked2.update(kw)
    asyncio.get_event_loop().run_until_complete(
        coord2.close_with_authoritative_pnl("ENAUSDT", 0.112293, "loss_spike_force")
    )
    if booked2.get("price_source") != "local_fallback":
        failures.append(
            f"raw-transformer path should be local_fallback, got "
            f"{booked2.get('price_source')!r}"
        )

    # ── Check 4: resolver preference — proxy wins over transformer ──
    coord3 = TradeCoordinator()
    coord3.attach_transformer(_RawTransformerNoGetLastClose())
    coord3.attach_position_service(_ProxyWithGetLastClose())
    booked3 = {}
    coord3.on_trade_closed = lambda **kw: booked3.update(kw)
    asyncio.get_event_loop().run_until_complete(
        coord3.close_with_authoritative_pnl("ENAUSDT", 0.112293, "loss_spike_force")
    )
    if booked3.get("price_source") != "exchange_authoritative":
        failures.append(
            "with both wired, proxy must win (exchange_authoritative); got "
            f"{booked3.get('price_source')!r}"
        )

    return failures


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 1.3 net-booking verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 1.3 net-booking: proxy wired, WS close books net "
          "exchange_authoritative; raw-transformer degrades to local_fallback "
          "(the old bug); proxy wins when both present.")
