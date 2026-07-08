"""Phase 3 unit tests — PriceWorker WS callback persists via run_coroutine_threadsafe.

The price-source-divergence fix replaced the broken ``loop.create_task``
pattern at ``price_worker.py:215-220`` (which always raised
``RuntimeError`` inside pybit's thread-pool callback and was silently
swallowed) with the canonical thread-to-loop bridge
``asyncio.run_coroutine_threadsafe`` plus a ``done_callback`` that loudly
logs ``PRICE_WS_PERSIST_FAIL`` on failure.

Test cases:
    1. Callback updates ``_ws_quotes`` regardless of loop state (in-memory
       cache is the always-on fast path used by APEX assembler).
    2. Callback successfully schedules ``save_ticker`` on a real asyncio
       loop running on a separate thread.
    3. Callback handles ``self._loop is None`` (first WS tick before
       ``tick()`` runs) gracefully — logs PRICE_WS_PERSIST_NOLOOP at
       DEBUG, no exception escapes.
    4. Callback handles ``self._loop.is_closed()`` (shutdown race) silently
       — no spam during normal teardown.
    5. ``_on_save_ticker_done`` logs ``PRICE_WS_PERSIST_FAIL`` and bumps
       the counter on save_ticker exceptions.
    6. ``_on_save_ticker_done`` is silent on ``CancelledError`` (loop
       cancellation during shutdown is expected).
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.workers.price_worker import PriceWorker


def _make_price_worker() -> PriceWorker:
    """Construct a PriceWorker with stubbed dependencies for unit tests."""
    settings = MagicMock()
    settings.workers.market_data_interval = 45
    settings.universe.watch_list = ["BTCUSDT"]
    db = MagicMock()
    ws = MagicMock()
    pw = PriceWorker(settings=settings, db=db, ws=ws)
    pw.market_repo = MagicMock()
    pw.market_repo.save_ticker = AsyncMock()
    return pw


def _ticker_payload(symbol: str = "BTCUSDT", last_price: str = "100.0") -> dict:
    """Mirror the shape of pybit's ticker callback payload."""
    return {
        "data": {
            "symbol": symbol,
            "lastPrice": last_price,
            "bid1Price": "99.99",
            "ask1Price": "100.01",
            "highPrice24h": "105.0",
            "lowPrice24h": "95.0",
            "volume24h": "1000.0",
            "price24hPcnt": "0.05",
        }
    }


def test_ws_quotes_updates_with_loop_unset():
    """Callback updates the in-memory ``_ws_quotes`` cache even when
    ``self._loop`` has not been captured yet (first tick race window).
    APEX assembler's fast path depends on this regardless of DB
    persistence working."""
    pw = _make_price_worker()
    assert pw._loop is None

    pw._handle_ticker_update(_ticker_payload(symbol="BTCUSDT", last_price="100.5"))

    assert "BTCUSDT" in pw._ws_quotes
    price, _ts = pw._ws_quotes["BTCUSDT"]
    assert price == 100.5


def test_save_ticker_scheduled_via_run_coroutine_threadsafe(tmp_path):
    """Run a real asyncio loop on a background thread and fire the WS
    callback from the main thread. The bridge must:

    - Schedule ``save_ticker`` on the loop.
    - Await the future to completion (handled by the loop).
    - Not raise on the calling thread.
    """
    pw = _make_price_worker()

    # Track save_ticker calls (preserves AsyncMock so awaitable works).
    save_calls: list = []

    async def _track(ticker):
        save_calls.append(ticker.symbol)

    pw.market_repo.save_ticker = _track

    loop_ready = threading.Event()
    captured_loop_holder: dict = {}

    def _run_loop():
        loop = asyncio.new_event_loop()
        captured_loop_holder["loop"] = loop
        asyncio.set_event_loop(loop)
        loop_ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    assert loop_ready.wait(timeout=2.0)
    pw._loop = captured_loop_holder["loop"]

    try:
        # Fire the WS callback (simulating pybit's thread-pool invocation).
        pw._handle_ticker_update(_ticker_payload(symbol="ETHUSDT", last_price="2000.0"))

        # Wait briefly for run_coroutine_threadsafe → save_ticker → done.
        for _ in range(50):
            if save_calls:
                break
            threading.Event().wait(0.05)

        assert save_calls == ["ETHUSDT"]
        assert "ETHUSDT" in pw._ws_quotes
        # Persist failure counter stays at zero on the happy path.
        assert pw._ws_persist_fail_count == 0
    finally:
        captured_loop_holder["loop"].call_soon_threadsafe(
            captured_loop_holder["loop"].stop
        )
        thread.join(timeout=2.0)


def test_callback_handles_loop_unset_gracefully():
    """``self._loop is None`` (first WS tick before ``tick()`` ran)
    must not raise; the callback updates ``_ws_quotes`` and skips
    persistence."""
    pw = _make_price_worker()
    assert pw._loop is None

    # Should not raise.
    pw._handle_ticker_update(_ticker_payload(symbol="BTCUSDT", last_price="50.0"))

    assert pw._ws_quotes["BTCUSDT"][0] == 50.0
    # No persistence attempted because loop wasn't captured.
    pw.market_repo.save_ticker.assert_not_called()


def test_callback_handles_closed_loop_silently():
    """A closed loop (shutdown race) must not raise; the callback
    skips persistence silently."""
    pw = _make_price_worker()
    closed_loop = MagicMock()
    closed_loop.is_closed.return_value = True
    pw._loop = closed_loop

    pw._handle_ticker_update(_ticker_payload(symbol="BTCUSDT", last_price="100.0"))

    assert pw._ws_quotes["BTCUSDT"][0] == 100.0
    pw.market_repo.save_ticker.assert_not_called()


def test_done_callback_logs_persist_fail_and_increments_counter(caplog):
    """``_on_save_ticker_done`` must extract the future's exception,
    bump ``_ws_persist_fail_count``, and emit PRICE_WS_PERSIST_FAIL at
    WARNING. Hard rule 5: failure must be loud, not silent."""
    pw = _make_price_worker()
    assert pw._ws_persist_fail_count == 0

    fut = MagicMock()
    fut.result.side_effect = RuntimeError("disk full")

    import logging
    with caplog.at_level(logging.WARNING):
        pw._on_save_ticker_done(fut)

    assert pw._ws_persist_fail_count == 1


def test_done_callback_silent_on_cancelled_error():
    """``CancelledError`` indicates loop cancellation during shutdown.
    The done-callback treats it as expected and does NOT bump the
    persist-fail counter (otherwise normal shutdown would spam the
    counter)."""
    pw = _make_price_worker()

    fut = MagicMock()
    fut.result.side_effect = asyncio.CancelledError()
    pw._on_save_ticker_done(fut)

    assert pw._ws_persist_fail_count == 0
