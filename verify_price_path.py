#!/usr/bin/env python3
"""Verify System 2 — per-second open-trade price path logger.

Observability-only. This script uses in-memory fakes for the PriceWorker and
TradeCoordinator and a controllable clock; it never touches the real exchange,
the trading database, any protected table, or the real price_path.log. It
checks, with concrete values:

  1. Per-second sampling + dedup: one point per second per open trade, even if
     the loop ticks faster.
  2. Correct unrealized PnL sign (long vs short).
  3. Honest gaps: a stale/missing WS quote yields no fabricated point.
  4. Zero new API calls: the only price read is get_ws_quote (a pure in-memory
     dict read); the module references no exchange/ticker/position call.
  5. Batch flush: buffered points are emitted to the price_path sink on flush.
  6. Final flush on close: a closing trade gets its tail flushed plus a final
     close=Y point, then its buffers are dropped.
  7. Same-symbol re-open: a new trade id starts a fresh path; the old one is
     finalized.
  8. Fire-and-forget: a forced sink failure does NOT raise into the close
     (trade) path.

Usage:  .venv/bin/python verify_price_path.py
"""
import src.workers.price_path_logger as M

PASS, FAIL = [], []


def ok(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t
    def time(self):
        return self.t
    def monotonic(self):
        return self.t


class St:
    def __init__(self, sym, entry, side, bid="", oid="", opened=1000):
        self.symbol = sym
        self.entry_price = entry
        self.side = side
        self.brain_decision_id = bid
        self.order_id = oid
        self.opened_at = opened


class Coord:
    def __init__(self):
        self._t = {}
        self.cbs = []
    def active_symbols(self):
        return frozenset(self._t.keys())
    def get_trade_state(self, s):
        return self._t.get(s)
    def register_close_callback(self, cb):
        self.cbs.append(cb)
    def fire_close(self, sym, close_price, pnl):
        # Simulate the coordinator close fan-out calling the callback DIRECTLY
        # (no outer try/except) so we prove the logger's own guard is enough.
        rec = {"symbol": sym, "close_price": close_price, "pnl_pct": pnl,
               "trade_id": self._t[sym].brain_decision_id}
        del self._t[sym]
        for cb in self.cbs:
            cb(rec)


class PW:
    def __init__(self):
        self.q = {}
        self.calls = 0
    def get_ws_quote(self, sym, max_age_s=5.0):
        self.calls += 1
        return self.q.get(sym)


class Obs:
    price_path_resolution_seconds = 1.0
    price_path_flush_seconds = 30
    price_path_ws_max_age_seconds = 5.0


class Collector:
    """Stand-in for the price_path loguru logger; records emitted messages."""
    def __init__(self):
        self.msgs = []
        self.raise_on_info = False
    def info(self, msg):
        if self.raise_on_info:
            raise OSError("sink down (forced)")
        self.msgs.append(msg)


def main():
    print("System 2 verification — per-second open-trade price path logger")

    # Patch the module clock and the price_path sink.
    clk = Clock()
    M.time = clk
    coll = Collector()
    M.pp = coll

    pw = PW()
    coord = Coord()
    obs = Obs()
    ppl = M.PricePathLogger(pw, coord, obs)
    coord.register_close_callback(ppl.on_trade_closed)

    # Two open trades: BTC long entry 100, ETH short entry 50.
    coord._t["BTCUSDT"] = St("BTCUSDT", 100.0, "Buy", bid="d-btc-1")
    coord._t["ETHUSDT"] = St("ETHUSDT", 50.0, "Sell", bid="d-eth-1")
    pw.q = {"BTCUSDT": 101.0, "ETHUSDT": 48.0}

    print("\nSampling + dedup + PnL sign")
    clk.t = 1000.0
    ppl._sample_once()
    n_btc = len(ppl._buffers.get("d-btc-1", []))
    n_eth = len(ppl._buffers.get("d-eth-1", []))
    ok("one point per trade at t=1000", n_btc == 1 and n_eth == 1)

    # second sample same integer second -> dedup, no new points
    ppl._sample_once()
    ok("dedup: same second adds no new point",
       len(ppl._buffers["d-btc-1"]) == 1 and len(ppl._buffers["d-eth-1"]) == 1)

    # advance one second -> new points
    clk.t = 1001.0
    ppl._sample_once()
    ok("next second adds one point per trade",
       len(ppl._buffers["d-btc-1"]) == 2 and len(ppl._buffers["d-eth-1"]) == 2)

    btc_line = ppl._buffers["d-btc-1"][0]
    eth_line = ppl._buffers["d-eth-1"][0]
    ok("long PnL sign correct (+1.0000%)", "pnl=+1.0000%" in btc_line)
    ok("short PnL sign correct (+4.0000%)", "pnl=+4.0000%" in eth_line)
    ok("point carries ts, sym, tid, px, pnl",
       all(k in btc_line for k in ("ts=", "sym=BTCUSDT", "tid=d-btc-1", "px=", "pnl=")))

    print("\nHonest gap on stale quote")
    pw.q["ETHUSDT"] = None  # WS quote missing/stale
    clk.t = 1002.0
    before_eth = len(ppl._buffers["d-eth-1"])
    ppl._sample_once()
    ok("stale ETH quote yields no fabricated point",
       len(ppl._buffers["d-eth-1"]) == before_eth)
    ok("BTC still sampled in the same tick", len(ppl._buffers["d-btc-1"]) == 3)

    print("\nBatch flush")
    flushed_before = len(coll.msgs)
    clk.t = 1035.0  # > last_flush(1000) + flush_seconds(30)
    ppl._maybe_flush()
    ok("flush emits all buffered points", len(coll.msgs) > flushed_before)
    ok("buffers cleared after flush",
       len(ppl._buffers["d-btc-1"]) == 0 and len(ppl._buffers["d-eth-1"]) == 0)

    print("\nSame-symbol re-open")
    # BTC re-opens as a NEW trade (new decision id) at a new entry.
    coord._t["BTCUSDT"] = St("BTCUSDT", 200.0, "Buy", bid="d-btc-2", opened=1040)
    pw.q["BTCUSDT"] = 202.0
    clk.t = 1040.0
    ppl._sample_once()
    ok("new trade id starts a fresh buffer", "d-btc-2" in ppl._buffers)
    ok("old trade id finalized (dropped)", "d-btc-1" not in ppl._buffers)
    ok("re-open meta updated to new entry", ppl._meta["BTCUSDT"]["tid"] == "d-btc-2")

    print("\nFinal flush on close")
    msgs_before = len(coll.msgs)
    coord.fire_close("BTCUSDT", close_price=210.0, pnl=5.0)
    close_msgs = [m for m in coll.msgs[msgs_before:] if "tid=d-btc-2" in m]
    ok("close emits a final point", len(close_msgs) >= 1)
    ok("final point marked close=Y and at close price 210",
       any("close=Y" in m and "px=210" in m for m in close_msgs))
    ok("closed trade buffers dropped",
       "d-btc-2" not in ppl._buffers and "BTCUSDT" not in ppl._meta)

    print("\nZero new API calls (structural)")
    import inspect
    src = inspect.getsource(M)
    banned = ["get_ticker", "get_positions", "market_service", "client.call",
              "requests.", "aiohttp", "httpx", ".rest", "fetch_ticker"]
    hits = [b for b in banned if b in src]
    ok("module makes no exchange/ticker/position call", not hits)
    ok("only price read is get_ws_quote (pure in-memory)", "get_ws_quote" in src)

    print("\nFire-and-forget (forced sink failure)")
    coll.raise_on_info = True
    coord._t["XRPUSDT"] = St("XRPUSDT", 1.0, "Buy", bid="d-xrp-1")
    pw.q["XRPUSDT"] = 1.01
    clk.t = 1050.0
    ppl._sample_once()  # buffers a point
    raised = {"v": False}
    try:
        coord.fire_close("XRPUSDT", close_price=1.02, pnl=2.0)  # flushes -> sink raises
    except Exception:
        raised["v"] = True
    ok("forced sink failure does NOT raise into the close (trade) path",
       raised["v"] is False)
    coll.raise_on_info = False

    print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed | get_ws_quote calls={pw.calls}")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)
    print("ALL SYSTEM 2 CHECKS PASSED")


if __name__ == "__main__":
    main()
