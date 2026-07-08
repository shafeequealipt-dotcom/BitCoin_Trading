"""Issue 2.8 — DIRECTION_CONCENTRATION observability (src/apex/gate.py CHECK 3).

The all-contrarian-longs session was a market condition (every coin in an
extreme-fear contrarian-long regime), not a separate bug. Issue 2.8 surfaces
the book's long/short skew as a NOTE so the one-sided exposure is visible; it
is NEVER a directional gate.

This suite pins:
  * gate.py CHECK 3 (src/apex/gate.py:197-210) emits DIRECTION_CONCENTRATION
    with book_longs / book_shorts / skew computed from position .side, and
  * the trade is NOT blocked by concentration (no _gate_rejected, size intact),
  * the line does NOT appear when the book is empty (get_positions() -> []).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger as _loguru_logger

from src.apex.gate import TradeGate
from src.config.settings import APEXSettings
from src.core.types import Side  # the REAL Position.side type (str, Enum)


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


class _FakePositionService:
    """Async position service whose objects carry a .side attribute."""

    def __init__(self, sides: list[str]) -> None:
        # Use the REAL Side(str, Enum) so the fixture matches the live
        # PositionService.get_positions() -> Position(side=Side(...)) contract.
        # (A plain-string side would mask the str(enum) classifier bug.)
        self._positions = [SimpleNamespace(side=Side(s)) for s in sides]

    async def get_positions(self):
        return list(self._positions)

    async def get_position(self, symbol):
        # CHECK 5 duplicate-position check — no per-symbol position in this test.
        return None


def _gate(sides: list[str]) -> TradeGate:
    s = APEXSettings()
    # Flag OFF -> simple legacy path; CHECK 3 observability runs regardless.
    s.brain_authoritative_sizing_enabled = False
    s.max_position_size_usd = 4000.0
    services = {
        "position_service": _FakePositionService(sides),
        # fund_manager stub: CHECK 4 reads _account_state.available.
        "fund_manager": SimpleNamespace(
            _account_state=SimpleNamespace(available=100000.0)
        ),
    }
    return TradeGate(services, s)


def _trade(size: float = 1000.0) -> dict:
    # Non-zero conviction inputs so CHECK 4's zero-conviction reject does not
    # fire (that reject is unrelated to concentration; we want a clean pass).
    return {
        "symbol": "BTCUSDT",
        "direction": "Buy",
        "size_usd": size,
        "leverage": 3,
        "_xray_confidence": 0.7,
        "_setup_score": 80.0,
        "_expected_rr": 3.0,
        "_claude_original_size_usd": size,
    }


@pytest.mark.asyncio
async def test_direction_concentration_logged_with_skew(loguru_sink) -> None:
    """3 Buy + 1 Sell book -> DIRECTION_CONCENTRATION with longs=3 shorts=1 skew=0.75."""
    g = _gate(["Buy", "Buy", "Buy", "Sell"])
    t = await g.validate(_trade())

    matches = _records_with_tag(loguru_sink, "DIRECTION_CONCENTRATION")
    assert matches, "DIRECTION_CONCENTRATION must be emitted when the book is non-empty"
    msg = matches[0][1]
    assert "book_longs=3" in msg, msg
    assert "book_shorts=1" in msg, msg
    assert "skew=0.75" in msg, msg

    # It is observability only — the trade must NOT be blocked by concentration.
    assert t.get("_gate_rejected") is None, "concentration must NOT reject the trade"
    adj = t.get("_gate_adjustments", "") or ""
    assert "DIRECTION_CONCENTRATION" not in adj, "concentration must not be a gate modification"
    assert "concentration" not in adj.lower(), "no directional/concentration gate may be added"
    # Size untouched by the concentration note (book of 4 < default cap, plenty of capital).
    assert t.get("size_usd") == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_direction_concentration_silent_on_empty_book(loguru_sink) -> None:
    """Empty book (get_positions() -> []) -> the line does NOT appear."""
    g = _gate([])
    t = await g.validate(_trade())

    matches = _records_with_tag(loguru_sink, "DIRECTION_CONCENTRATION")
    assert not matches, f"no concentration log on an empty book, got: {matches}"
    assert t.get("_gate_rejected") is None


# ─── Issue 7 (2026-06-08): portfolio directional-drawdown breaker ──────


class _FakePosSvcPnl:
    """Position service whose objects carry .side AND .unrealized_pnl."""

    def __init__(self, rows):  # rows: list[(side_str, unrealized_pnl)]
        self._positions = [
            SimpleNamespace(side=Side(s), unrealized_pnl=float(u)) for s, u in rows
        ]

    async def get_positions(self):
        return list(self._positions)

    async def get_position(self, symbol):
        return None


def _breaker_gate(rows, *, equity=40000.0, enabled=True,
                  concentration=0.80, open_loss_pct=1.5, min_positions=3):
    s = APEXSettings()
    s.brain_authoritative_sizing_enabled = False
    s.max_position_size_usd = 4000.0
    s.portfolio_dd_breaker_enabled = enabled
    s.portfolio_dd_breaker_concentration = concentration
    s.portfolio_dd_breaker_open_loss_pct = open_loss_pct
    s.portfolio_dd_breaker_min_positions = min_positions
    services = {
        "position_service": _FakePosSvcPnl(rows),
        "fund_manager": SimpleNamespace(
            _account_state=SimpleNamespace(available=100000.0, total_equity=equity)
        ),
    }
    return TradeGate(services, s)


def _dir_trade(direction: str) -> dict:
    t = _trade()
    t["direction"] = direction
    return t


@pytest.mark.asyncio
async def test_issue7_breaker_halts_concentrated_bleeding_same_direction(loguru_sink):
    """Enabled + 100% long book bleeding -$800 (budget -$600 = 1.5% of $40k) +
    a new BUY → halted (never closes the open positions)."""
    g = _breaker_gate([("Buy", -200), ("Buy", -200), ("Buy", -200), ("Buy", -200)])
    t = await g.validate(_dir_trade("Buy"))
    assert t.get("_gate_rejected"), "breaker must halt the new same-direction entry"
    assert "portfolio_directional_drawdown" in t["_gate_rejected"]
    assert _records_with_tag(loguru_sink, "GATE_PORTFOLIO_DD_HALT")


@pytest.mark.asyncio
async def test_issue7_breaker_off_by_default():
    """Default OFF: the same bleeding concentrated book does NOT halt."""
    g = _breaker_gate(
        [("Buy", -200), ("Buy", -200), ("Buy", -200), ("Buy", -200)], enabled=False)
    t = await g.validate(_dir_trade("Buy"))
    assert t.get("_gate_rejected") is None


@pytest.mark.asyncio
async def test_issue7_breaker_allows_opposite_direction():
    """A new entry in the OPPOSITE (under-represented) direction is allowed -
    it rebalances the book; the breaker only halts the concentrated side."""
    g = _breaker_gate([("Buy", -200), ("Buy", -200), ("Buy", -200), ("Buy", -200)])
    t = await g.validate(_dir_trade("Sell"))
    assert t.get("_gate_rejected") is None


@pytest.mark.asyncio
async def test_issue7_breaker_no_halt_when_not_bleeding():
    """Concentrated book but the aggregate open loss is under budget → no halt."""
    g = _breaker_gate([("Buy", -50), ("Buy", -50), ("Buy", -50), ("Buy", -50)])
    t = await g.validate(_dir_trade("Buy"))  # -$200 > -$600 budget
    assert t.get("_gate_rejected") is None
