"""Phantom-loss fix — END-TO-END pipeline verification through the REAL
coordinator close path.

Drives close_with_authoritative_pnl (the WS self-close entry point where all
three confirmed phantom losses occurred) with real DI wiring
(attach_position_service + attach_tick_resolver) and a stub proxy that returns
a STALE prior-trade closed-pnl row (the Bybit indexer-lag bug). Confirms the
booked record (read from the coordinator's own _closed_trades, after the full
resolve -> gate -> on_trade_closed -> fan-out pipeline) is the correct WS net,
NOT the phantom loss.

This exercises: close_with_authoritative_pnl -> _local_pnl_from_ws (real WS
guardrail) -> resolve_authoritative_pnl (qty-primary gate) -> on_trade_closed
(exit-divergence gate + sign-mismatch precondition) -> record build + callback
fan-out. The exact MONUSDT scenario.

Run:  .venv/bin/python verify_phantom_loss_pipeline_e2e.py
"""
import asyncio
import sys
import time

from src.core.trade_coordinator import TradeCoordinator, TradeState

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


class StaleProxy:
    """Stand-in for the _PositionProxy/BybitDemoAdapter that returns a STALE
    prior-trade closed-pnl row — exactly what the Bybit closed-pnl indexer lag
    produces for a re-traded symbol."""

    def __init__(self, row: dict):
        self.row = row

    async def get_last_close(self, symbol: str, **kwargs):
        # Mimics the adapter: in legacy mode hints are ignored; the stale row
        # is returned (the bug). The fix must catch it downstream.
        return self.row


def price_decimals(symbol: str) -> int:
    # Mimics InstrumentService.price_decimals (sub-cent coin).
    return 6


async def run() -> dict:
    # ---- DI wiring, exactly as WorkerManager does at boot ----
    coord = TradeCoordinator()
    coord.attach_position_service(  # DI #1: the proxy that exposes get_last_close
        StaleProxy({
            "net_pnl_usd": -83.6953, "net_pnl_pct": -1.8599,
            "exit_price": 0.021344,            # prior trade's exit (stale)
            "qty": 249353.0,                   # prior trade's size (differs!)
            "entry_price": 0.021003, "side": "Buy",
        })
    )
    coord.attach_tick_resolver(price_decimals)  # DI #2: the staleness-gate tolerance

    # The MONUSDT scenario: entry 0.021003, size 214255, Buy.
    coord._trades["MONUSDT"] = TradeState(
        symbol="MONUSDT", entry_price=0.021003, size=214255.0,
        side="Buy", opened_at=time.time(),
    )

    # ---- Drive the REAL WS self-close pipeline ----
    # exec_price = the TRUE WS fill 0.021062 (above the Buy entry => a WIN).
    await coord.close_with_authoritative_pnl(
        symbol="MONUSDT", exit_price=0.021062, closed_by="bybit_sl_hit",
        exec_pnl=0.0, exec_fee=0.0, ws_order_id="656ff17e-e00",
        ws_exec_qty=214255.0, ws_close_ts=int(time.time() * 1000),
        close_pnl_source="legacy",
    )
    return coord._closed_trades[-1] if coord._closed_trades else {}


rec = asyncio.run(run())
check("E2E: the real pipeline produced a booked record", bool(rec),
      f"keys={list(rec.keys())[:5] if rec else None}")
check("E2E: the stale -83.70 was NOT booked", rec.get("pnl_usd", 0) > 0,
      f"pnl_usd={rec.get('pnl_usd')}")
check("E2E: booked as a WIN (the real outcome, ~+12.64)",
      rec.get("was_win") is True and rec.get("pnl_usd", 0) > 10,
      f"was_win={rec.get('was_win')} pnl_usd={rec.get('pnl_usd')}")
check("E2E: price_source flags the demotion", rec.get("price_source") == "local_fallback_stale",
      f"price_source={rec.get('price_source')}")
check("E2E: close_price is the real WS fill, not the stale 0.021344",
      abs(float(rec.get("close_price", 0) or 0) - 0.021062) < 1e-9,
      f"close_price={rec.get('close_price')}")

print(f"\n{'=' * 52}\nE2E RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("FULL WS-CLOSE PIPELINE DEMOTES THE PHANTOM LOSS — END TO END")
sys.exit(0)
