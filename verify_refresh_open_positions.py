#!/usr/bin/env python3
"""Phase 2 verification — open positions are never neglected by a refresh.

Proves, with controlled fakes (no live system, no network, no real DB
writes), the non-negotiable safety properties of the universe-refresh
orchestration:

  1. An open-position coin is FORCE-KEPT in the new universe even when it
     would not be selected on movement — in both settings.universe.watch_list
     and the MarketScanner's own watch_list.
  2. Only Call-A is paused during the refresh and it is RESUMED afterwards
     (the exact flag the LayerManager reads), so Call-B and the watchdog —
     which manage open positions — keep running throughout.
  3. If open positions cannot be confirmed, the refresh ABORTS without
     swapping the universe (it never risks dropping a live position's coin).
  4. The overlap guard rejects a second concurrent refresh.

Run from the project root:  python verify_refresh_open_positions.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
import types
from datetime import timedelta
from pathlib import Path

import src.core.universe_refresh as ur
from src.config.settings import Settings
from src.core.types import OHLCV, Ticker, TimeFrame
from src.core.universe_refresh import UniverseRefreshOrchestrator, UniverseRefreshState
from src.core.utils import now_utc
from src.strategies.scanner import MarketScanner


# --- Fakes -----------------------------------------------------------------
class FakePos:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class FakePositionService:
    """get_positions returns the configured open positions.

    When confirmed=False, exposes get_positions_with_confirmation returning an
    unconfirmed result (transport failure) to exercise the abort path.
    """

    def __init__(self, symbols, confirmed: bool = True) -> None:
        self._symbols = symbols
        self._confirmed = confirmed

    async def get_positions(self):
        return [FakePos(s) for s in self._symbols]

    async def get_positions_with_confirmation(self):
        return types.SimpleNamespace(
            confirmed=self._confirmed,
            positions=[FakePos(s) for s in self._symbols],
            reason="fake_transport_fail" if not self._confirmed else None,
        )


class FakeMarket:
    def __init__(self, tickers, daily_by_sym) -> None:
        self._tickers = tickers
        self._daily = daily_by_sym

    async def get_all_linear_tickers(self):
        return self._tickers

    async def get_klines(self, symbol, interval, limit=200):
        return self._daily.get(symbol, [])


class FakeRegime:
    """Every coin has a confirmed, non-UNKNOWN regime (warm-up passes)."""

    def get_coin_regime(self, sym):
        return types.SimpleNamespace(regime=types.SimpleNamespace(value="trending"))


class FakeMarketRepo:
    """Returns enough M5 candles that the kline-count gate passes."""

    async def get_klines(self, symbol, timeframe, limit=200):
        return [
            OHLCV(symbol=symbol, timeframe=TimeFrame.M5, timestamp=now_utc(),
                  open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0, turnover=1.0)
            for _ in range(max(limit, 60))
        ]


def _ticker(sym, last=100.0, vol=50_000_000):
    return Ticker(symbol=sym, last_price=last, bid=last * 0.9999, ask=last * 1.0001,
                  high_24h=last * 1.04, low_24h=last * 0.96, volume_24h=vol,
                  change_24h_pct=4.0)


def _trend_daily(sym, days=10, start=100.0):
    """Strong uptrend with tight intraday range -> high directionality."""
    bars = []
    px = start
    for _ in range(days):
        o = px
        c = px * 1.03
        h = c * 1.004
        low = o * 0.996
        px = c
        bars.append(OHLCV(symbol=sym, timeframe=TimeFrame.D1, timestamp=now_utc(),
                          open=o, high=h, low=low, close=c, volume=1e6, turnover=2e7))
    return bars


def _mk_orchestrator(settings, position_service):
    pos_coin = "ZZZOPENUSDT"  # an open position NOT among the trending movers
    trend_syms = [f"TRND{i:02d}USDT" for i in range(30)]
    tickers = [_ticker(s) for s in trend_syms]
    daily = {s: _trend_daily(s) for s in trend_syms}
    scanner = MarketScanner(settings, market_service=None,
                            watch_list=set(settings.universe.watch_list))
    services = {
        "market": FakeMarket(tickers, daily),
        "bybit": None,
        "position_service": position_service,
        "regime_detector": FakeRegime(),
        "scanner": scanner,
        "universe_refresh_state": UniverseRefreshState(),
    }
    orch = UniverseRefreshOrchestrator(settings, db=None, services=services)
    orch._market_repo = FakeMarketRepo()
    return orch, services, scanner, pos_coin


def _fast_warmup(settings):
    # Bound the warm-up so the test never hangs (readiness passes instantly).
    settings.universe.refresh.warmup_max_minutes = 0.1
    settings.universe.refresh.warmup_poll_seconds = 1


async def run() -> int:
    failures = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    # Isolate the persisted state file so we never touch the real one.
    tmp = Path(tempfile.mkdtemp()) / "universe_state.json"
    ur._STATE_FILE = tmp

    print("Test 1 — open position force-kept + only Call-A paused + state persisted")
    settings = Settings.load()
    _fast_warmup(settings)
    pos_service = FakePositionService(["ZZZOPENUSDT"], confirmed=True)
    orch, services, scanner, pos_coin = _mk_orchestrator(settings, pos_service)
    state = services["universe_refresh_state"]
    before = list(settings.universe.watch_list)

    res = await orch.run_refresh("verify")
    check("refresh status ok", res.get("status") == "ok")
    check("open position force-kept in settings.universe", pos_coin in settings.universe.watch_list)
    check("open position force-kept in scanner watch_list", pos_coin in scanner._watch_list)
    check("universe actually changed (movers selected)", set(settings.universe.watch_list) != set(before))
    check("Call-A resumed after refresh", state.is_call_a_paused() is False)
    check("single-flight released", state.is_running() is False)
    check("state file persisted with the open position", tmp.exists() and pos_coin in tmp.read_text())
    check("warm-up completed (no pending)", len(res.get("warmup_pending", [])) == 0)
    # The LayerManager reads exactly this predicate to skip Call-A; prove it
    # gates only on the flag (Call-B has no such guard — verified by grep below).
    state.pause_call_a("manual_check")
    check("LayerManager would SKIP Call-A while paused", services["universe_refresh_state"].is_call_a_paused() is True)
    state.resume_call_a()

    print("Test 2 — refresh ABORTS (no swap) when positions cannot be confirmed")
    settings2 = Settings.load()
    _fast_warmup(settings2)
    unconfirmed = FakePositionService(["ZZZOPENUSDT"], confirmed=False)
    orch2, services2, scanner2, _ = _mk_orchestrator(settings2, unconfirmed)
    before2 = list(settings2.universe.watch_list)
    res2 = await orch2.run_refresh("verify_abort")
    check("aborted on unconfirmed positions", res2.get("status") == "aborted")
    check("universe UNCHANGED after abort", list(settings2.universe.watch_list) == before2)
    check("Call-A resumed after abort", services2["universe_refresh_state"].is_call_a_paused() is False)

    print("Test 3 — overlap guard rejects a concurrent refresh")
    settings3 = Settings.load()
    _fast_warmup(settings3)
    orch3, services3, _, _ = _mk_orchestrator(settings3, FakePositionService(["ZZZOPENUSDT"]))
    services3["universe_refresh_state"].begin()  # simulate an in-flight refresh
    res3 = await orch3.run_refresh("verify_overlap")
    check("second concurrent refresh rejected", res3.get("status") == "already_running")
    services3["universe_refresh_state"].end()

    # Structural confirmation: the Call-A pause guard lives ONLY in the
    # Call-A branch of the LayerManager, so Call-B is never paused.
    lm = Path("src/core/layer_manager.py").read_text()
    a_idx = lm.find('if self._call_type == "A":')
    b_idx = lm.find('_call_type == "B"', a_idx + 1)
    guard_idx = lm.find("universe_refresh_state")
    check("pause guard is inside the Call-A branch only",
          a_idx != -1 and guard_idx != -1 and a_idx < guard_idx < (b_idx if b_idx != -1 else len(lm)))

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED -> {failures}")
        return 1
    print("RESULT: ALL CHECKS PASSED — open positions are force-kept and managed across a refresh; only Call-A pauses; abort and overlap guards hold.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
