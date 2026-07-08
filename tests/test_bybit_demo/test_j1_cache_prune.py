"""J1 Phase 3 Step A (2026-05-14) — adapter-level symmetric cache prune.

The bybit_demo adapter previously wrote every confirmed-true position
via INSERT OR REPLACE but never deleted rows for symbols that dropped
out of Bybit's response. Pruning was outsourced to the watchdog's
vanished-detection plus the close-callback chain, which only fires for
symbols the watchdog tracked at least once. Pre-c4eef5c stale rows
sat forever in the cache.

This suite verifies the symmetric-prune fix:

  * confirmed=True with positions → prune rows tagged 'bybit_demo'
    whose symbol is missing from the response.
  * confirmed=False → no prune (state unknown).
  * confirmed=True with positions=() once → no prune (dwell guard).
  * confirmed=True with positions=() twice → prune (dwell elapsed).
  * symbol-filtered call → no prune (partial view).
  * Shadow-tagged rows are untouched (scope to bybit_demo only).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from loguru import logger as _loguru_logger

from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
from src.core.exceptions import BybitAPIError


# --- Fakes ------------------------------------------------------------


class _FakeClient:
    """Minimal Bybit demo client stub. Each call pops from a queue or
    repeats the last response. Tests configure the queue."""

    def __init__(self) -> None:
        self.gets: list[tuple[str, dict[str, Any] | None, str]] = []
        self._responses: list[dict[str, Any]] = []
        self._exception: BybitAPIError | None = None

    def set_responses(self, *responses: dict[str, Any]) -> None:
        self._responses = list(responses)

    def set_exception(self, exc: BybitAPIError | None) -> None:
        self._exception = exc

    async def get(
        self, path: str, params: dict[str, Any] | None = None, *, op: str = "",
    ) -> dict[str, Any]:
        self.gets.append((path, params, op))
        if self._exception is not None:
            raise self._exception
        if not self._responses:
            return {"retCode": 0, "result": {"list": []}}
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


class _FakeTradingRepo:
    """Captures save_position and prune_positions_not_in_set calls
    without touching a DB. Models the row state as a per-mode dict."""

    def __init__(self) -> None:
        # mode -> {symbol: row}
        self.rows: dict[str, dict[str, dict[str, Any]]] = {}
        self.save_calls: list[tuple[str, str]] = []   # (symbol, mode)
        self.prune_calls: list[tuple[str, frozenset[str]]] = []
        self.raise_on_prune: Exception | None = None

    def seed(self, mode: str, symbols: list[str]) -> None:
        self.rows.setdefault(mode, {})
        for s in symbols:
            self.rows[mode][s] = {"symbol": s, "size": 1.0, "exchange_mode": mode}

    async def save_position(self, position: Any, *, exchange_mode: str = "") -> None:
        self.save_calls.append((position.symbol, exchange_mode))
        self.rows.setdefault(exchange_mode, {})
        self.rows[exchange_mode][position.symbol] = {
            "symbol": position.symbol,
            "size": position.size,
            "exchange_mode": exchange_mode,
        }

    async def prune_positions_not_in_set(
        self, mode: str, live_symbols: set[str],
    ) -> list[str]:
        self.prune_calls.append((mode, frozenset(live_symbols)))
        if self.raise_on_prune is not None:
            raise self.raise_on_prune
        cached = set(self.rows.get(mode, {}).keys())
        stale = sorted(cached - live_symbols)
        for s in stale:
            self.rows[mode].pop(s, None)
        return stale


# --- Fixtures --------------------------------------------------------


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _v5_position(symbol: str, side: str = "Buy", size: str = "1.0") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": side,
        "size": size,
        "avgPrice": "100",
        "markPrice": "100",
        "unrealisedPnl": "0",
        "leverage": "1",
        "liqPrice": "0",
    }


def _ok_response(*positions: dict[str, Any]) -> dict[str, Any]:
    return {"retCode": 0, "result": {"list": list(positions)}}


# --- Tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_runs_on_non_empty_confirmed_response(loguru_sink) -> None:
    """When Bybit returns one position and the cache holds two, the
    cached row that is missing from the response must be pruned, and a
    POSITIONS_CACHE_PRUNE log event must fire for it."""
    client = _FakeClient()
    client.set_responses(_ok_response(_v5_position("BTCUSDT")))
    repo = _FakeTradingRepo()
    # Cache holds BTC (will stay) + STALEUSDT (must be pruned)
    repo.seed("bybit_demo", ["BTCUSDT", "STALEUSDT"])
    # And a shadow row that must NOT be touched.
    repo.seed("shadow", ["SHADOWUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT"}
    # Save fired for BTC
    assert ("BTCUSDT", "bybit_demo") in repo.save_calls
    # Prune fired exactly once with the live set
    assert repo.prune_calls == [("bybit_demo", frozenset({"BTCUSDT"}))]
    # Stale row gone
    assert "STALEUSDT" not in repo.rows["bybit_demo"]
    # Shadow row untouched
    assert "SHADOWUSDT" in repo.rows["shadow"]
    # POSITIONS_CACHE_PRUNE emitted for the stale symbol
    pruned = _records_with_tag(loguru_sink, "POSITIONS_CACHE_PRUNE")
    assert len(pruned) == 1
    kv = _parse_kv(pruned[0][1])
    assert kv["sym"] == "STALEUSDT"
    assert kv["mode"] == "bybit_demo"
    assert kv["reason"] == "missing_from_response"


@pytest.mark.asyncio
async def test_prune_skipped_on_unknown_state(loguru_sink) -> None:
    """When the adapter returns confirmed=False (TIMESTAMP_FAIL), the
    cache must NOT be pruned — the live set is unknown."""
    client = _FakeClient()
    client.set_exception(
        BybitAPIError(
            "timestamp fail",
            details={"ret_code": 10002},
        ),
    )
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["BTCUSDT", "STALEUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is False
    # No prune attempted
    assert repo.prune_calls == []
    # Cache untouched
    assert set(repo.rows["bybit_demo"].keys()) == {"BTCUSDT", "STALEUSDT"}


@pytest.mark.asyncio
async def test_first_confirmed_empty_does_not_prune(loguru_sink) -> None:
    """One transient confirmed=True, positions=() must NOT prune — the
    dwell-time guard requires two consecutive empties before erasing
    everything."""
    client = _FakeClient()
    client.set_responses(_ok_response())  # empty
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["STALEUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert result.positions == ()
    # No prune yet (dwell counter at 1, threshold is 2)
    assert repo.prune_calls == []
    assert "STALEUSDT" in repo.rows["bybit_demo"]


@pytest.mark.asyncio
async def test_two_consecutive_empties_trigger_prune(loguru_sink) -> None:
    """After two consecutive confirmed-empty responses the dwell guard
    elapses and the cache is pruned to empty."""
    client = _FakeClient()
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["STALEUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    # Tick 1 — empty, dwell=1, no prune
    client.set_responses(_ok_response())
    await svc.get_positions_with_confirmation()
    assert repo.prune_calls == []
    assert "STALEUSDT" in repo.rows["bybit_demo"]

    # Tick 2 — empty, dwell=2, prune fires with empty live set
    client.set_responses(_ok_response())
    await svc.get_positions_with_confirmation()
    assert repo.prune_calls == [("bybit_demo", frozenset())]
    assert "STALEUSDT" not in repo.rows["bybit_demo"]


@pytest.mark.asyncio
async def test_non_empty_response_resets_dwell_counter(loguru_sink) -> None:
    """A non-empty confirmed response between two empties must reset
    the dwell counter so a subsequent transient empty cannot trigger
    a phantom prune."""
    client = _FakeClient()
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["BTCUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    # Tick 1 — empty, dwell=1
    client.set_responses(_ok_response())
    await svc.get_positions_with_confirmation()
    # Tick 2 — non-empty, dwell resets to 0; prune still runs with
    # live={BTC} which already matches the cache, so no row is removed.
    client.set_responses(_ok_response(_v5_position("BTCUSDT")))
    await svc.get_positions_with_confirmation()
    # Tick 3 — empty again, dwell=1 (NOT 3), no prune
    client.set_responses(_ok_response())
    await svc.get_positions_with_confirmation()

    # The only prune call should be tick 2 with the non-empty live set
    assert repo.prune_calls == [("bybit_demo", frozenset({"BTCUSDT"}))]
    # Row still present
    assert "BTCUSDT" in repo.rows["bybit_demo"]


@pytest.mark.asyncio
async def test_symbol_filtered_call_does_not_prune(loguru_sink) -> None:
    """When the caller passes ``symbol=X``, the adapter has only a
    single-symbol view and must NOT attempt to prune (it would wrongly
    delete every other cached row)."""
    client = _FakeClient()
    client.set_responses(_ok_response(_v5_position("BTCUSDT")))
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["BTCUSDT", "ETHUSDT"])
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation(symbol="BTCUSDT")

    assert result.confirmed is True
    # No prune attempted
    assert repo.prune_calls == []
    assert set(repo.rows["bybit_demo"].keys()) == {"BTCUSDT", "ETHUSDT"}


@pytest.mark.asyncio
async def test_prune_failure_is_logged_but_does_not_raise(loguru_sink) -> None:
    """If the repo raises during prune, the adapter must still return
    the parsed positions and log POSITIONS_CACHE_PRUNE_FAIL so the
    watchdog continues to operate on confirmed truth."""
    client = _FakeClient()
    client.set_responses(_ok_response(_v5_position("BTCUSDT")))
    repo = _FakeTradingRepo()
    repo.seed("bybit_demo", ["STALEUSDT"])
    repo.raise_on_prune = RuntimeError("simulated db failure")
    svc = BybitDemoPositionService(client, trading_repo=repo)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    # Positions still returned to caller
    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT"}
    # Failure logged
    failures = _records_with_tag(loguru_sink, "POSITIONS_CACHE_PRUNE_FAIL")
    assert len(failures) == 1
    # Successful prune log NOT emitted (because the prune itself raised)
    assert _records_with_tag(loguru_sink, "POSITIONS_CACHE_PRUNE |") == []


@pytest.mark.asyncio
async def test_prune_noop_when_no_trading_repo(loguru_sink) -> None:
    """Legacy callers / test fixtures that do not inject a repo must
    not crash on the prune path."""
    client = _FakeClient()
    client.set_responses(_ok_response(_v5_position("BTCUSDT")))
    svc = BybitDemoPositionService(client, trading_repo=None)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT"}
    # Nothing logged for the prune path
    assert _records_with_tag(loguru_sink, "POSITIONS_CACHE_PRUNE") == []
