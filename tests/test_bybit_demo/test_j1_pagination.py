"""J1 Phase 3 Step E (2026-05-14) — Bybit V5 /v5/position/list pagination
loop tests.

Pre-J1 the adapter read only page 1 of the position list, silently
dropping the tail if the operator's strategy ever held more than the
default limit=20 simultaneous positions. The fix adds a cursor loop
capped at _MAX_POSITIONS_PAGES (5) with two safety posture tests:

  * Mid-pagination error returns confirmed=False (partial truth must
    not phantom-prune).
  * Cap-exhausted-with-cursor returns confirmed=False with a loud
    BYBIT_DEMO_POSITIONS_PAGINATION_CAP warning.

Plus the happy-path tests: single page, two pages, last page empty
cursor terminates the loop.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from loguru import logger as _loguru_logger

from src.bybit_demo.bybit_demo_adapter import BybitDemoPositionService
from src.core.exceptions import BybitAPIError


class _PagedClient:
    """Returns the configured response per call, in order."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.gets: list[tuple[str, dict[str, Any] | None, str]] = []

    async def get(
        self, path: str, params: dict[str, Any] | None = None, *, op: str = "",
    ) -> dict[str, Any]:
        self.gets.append((path, dict(params or {}), op))
        if not self._responses:
            return {"retCode": 0, "result": {"list": []}}
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _v5(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "side": "Buy",
        "size": "1.0",
        "avgPrice": "100",
        "markPrice": "100",
        "unrealisedPnl": "0",
        "leverage": "1",
        "liqPrice": "0",
    }


def _page(rows: list[dict[str, Any]], cursor: str = "") -> dict[str, Any]:
    return {
        "retCode": 0,
        "result": {"list": rows, "nextPageCursor": cursor},
    }


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


@pytest.mark.asyncio
async def test_single_page_no_cursor_returns_full_list(loguru_sink) -> None:
    client = _PagedClient([_page([_v5("BTCUSDT"), _v5("ETHUSDT")], cursor="")])
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT", "ETHUSDT"}
    # Exactly one HTTP call — no continuation
    assert len(client.gets) == 1
    assert "cursor" not in client.gets[0][1]


@pytest.mark.asyncio
async def test_two_pages_concatenated_in_order(loguru_sink) -> None:
    client = _PagedClient([
        _page([_v5("BTCUSDT")], cursor="cursor-page-2"),
        _page([_v5("ETHUSDT"), _v5("RUNEUSDT")], cursor=""),
    ])
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    syms = [p.symbol for p in result.positions]
    assert syms == ["BTCUSDT", "ETHUSDT", "RUNEUSDT"]
    # Two HTTP calls; second passes the cursor
    assert len(client.gets) == 2
    assert client.gets[1][1].get("cursor") == "cursor-page-2"
    # op tag distinguishes paginated continuations
    assert client.gets[0][2] == "positions"
    assert client.gets[1][2] == "positions_pg"


@pytest.mark.asyncio
async def test_mid_pagination_error_returns_confirmed_false(loguru_sink) -> None:
    """Page 1 succeeds, page 2 raises a non-10002 error. The adapter
    has partial truth and must NOT prune; returns confirmed=False so
    the watchdog preserves last-known state."""
    page1_err = BybitAPIError("rate limited", details={"ret_code": 10018})
    client = _PagedClient([
        _page([_v5("BTCUSDT")], cursor="cursor-page-2"),
        page1_err,
    ])
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is False
    assert result.reason == "mid_pagination_error"
    unknowns = _records_with_tag(loguru_sink, "BYBIT_DEMO_POSITIONS_UNKNOWN_STATE")
    assert len(unknowns) == 1
    assert "mid_pagination_error" in unknowns[0][1]


@pytest.mark.asyncio
async def test_first_page_non_10002_error_preserves_legacy_empty(loguru_sink) -> None:
    """A non-10002 error on the FIRST page preserves the legacy
    ``confirmed=True, positions=()`` contract that existing dashboards
    rely on. (10002 still returns confirmed=False per the I1/F-26 fix.)"""
    err = BybitAPIError("auth failed", details={"ret_code": 10003})
    client = _PagedClient([err])
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert result.positions == ()


@pytest.mark.asyncio
async def test_pagination_cap_returns_confirmed_false_and_warns(loguru_sink) -> None:
    """If the cursor never empties before _MAX_POSITIONS_PAGES, the
    adapter returns confirmed=False with a loud warning rather than
    silently truncating (which would re-introduce H3)."""
    # _MAX_POSITIONS_PAGES = 5; supply 5 pages all with non-empty cursors
    pages = [_page([_v5(f"SYM{i}USDT")], cursor=f"cur-{i+1}") for i in range(5)]
    client = _PagedClient(pages)
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is False
    assert result.reason == "pagination_cap"
    assert len(client.gets) == 5
    caps = _records_with_tag(loguru_sink, "BYBIT_DEMO_POSITIONS_PAGINATION_CAP")
    assert len(caps) == 1
    assert "pages=5" in caps[0][1]
    assert "cursor_still_present=true" in caps[0][1]


@pytest.mark.asyncio
async def test_zero_size_rows_still_filtered_across_pages(loguru_sink) -> None:
    """The existing size>0 filter must apply to every page, not just
    page 1. Bybit returns zero-size entries for symbols the account
    once traded; both pages may carry them."""
    p1 = _page([
        _v5("BTCUSDT"),
        {"symbol": "ZERO1USDT", "side": "Buy", "size": "0"},
    ], cursor="next")
    p2 = _page([
        _v5("ETHUSDT"),
        {"symbol": "ZERO2USDT", "side": "Buy", "size": "0"},
    ], cursor="")
    client = _PagedClient([p1, p2])
    svc = BybitDemoPositionService(client)  # type: ignore[arg-type]

    result = await svc.get_positions_with_confirmation()

    assert result.confirmed is True
    assert {p.symbol for p in result.positions} == {"BTCUSDT", "ETHUSDT"}
