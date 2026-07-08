"""Issue 2.9 (2026-06-07) — FEE_DRAG_OBS observability log on close.

``TradeCoordinator.on_trade_closed`` emits a ``FEE_DRAG_OBS`` INFO line that
surfaces the round-trip taker-fee estimate and a fee-dominated SCRATCH flag so
the operator can quantify scratch-trade fee drag against the now-truthful net
PnL (Phase 1). This is *pure observability* — it must never change the booked
``pnl_usd`` that fans out to the close callbacks.

These tests pin the FEE_DRAG_OBS emission at
``src/core/trade_coordinator.py:1506-1514`` and its notional fallback at
``:1494-1501`` (``_fee_drag_notional = _t2_8_notional_used`` → falls back to
``abs(size * entry_price)`` when the sign-mismatch notional is 0;
``_rt_taker_fee_est = notional * _BYBIT_TAKER_FEE_PER_SIDE * 2.0``;
``scratch`` = ``Y`` when ``abs(net) <= fee_est`` else ``N``).
"""
from __future__ import annotations

import re

import pytest
from loguru import logger as _loguru_logger

from src.core.trade_coordinator import (
    TradeCoordinator,
    _BYBIT_TAKER_FEE_PER_SIDE,
)


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


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


def _fee_drag_line(records: list[tuple[str, str]]) -> str:
    matches = [r[1] for r in records if r[1].startswith("FEE_DRAG_OBS")]
    assert len(matches) == 1, f"expected exactly one FEE_DRAG_OBS line, got {matches}"
    return matches[0]


def _parse_kv(msg: str) -> dict[str, str]:
    # values are space-delimited up to the next key=, sufficient for the
    # numeric/flag fields this test asserts (sym/net_usd/notional/...).
    return dict(re.findall(r"(\w+)=([^\s|]+)", msg))


def _register(
    coord: TradeCoordinator,
    symbol: str,
    side: str,
    entry: float,
    size: float,
) -> None:
    coord.register_trade(
        symbol=symbol,
        strategy_category="default",
        strategy_name="test",
        entry_price=entry,
        side=side,
        size=size,
    )


def test_rt_taker_fee_est_formula_and_notional_fallback(
    coordinator: TradeCoordinator, loguru_sink
) -> None:
    """No sign-mismatch → ``_t2_8_notional_used`` stays 0 → the FEE_DRAG_OBS
    notional falls back to ``abs(size * entry_price)``, and the round-trip
    taker-fee estimate is exactly ``notional * _BYBIT_TAKER_FEE_PER_SIDE * 2.0``.

    Buy entry=100 size=10 → notional 1000. pnl_pct/pnl_usd share a sign so the
    T2-8 sign-mismatch branch (the only thing that sets _t2_8_notional_used) is
    skipped, exercising the size*entry fallback at trade_coordinator.py:1494-1498.
    """
    _register(coordinator, symbol="BTCUSDT", side="Buy", entry=100.0, size=10.0)
    coordinator.on_trade_closed(
        symbol="BTCUSDT",
        pnl_pct=0.001,          # tiny positive: no sign mismatch
        pnl_usd=0.50,           # positive: shares pnl_pct sign
        was_win=True,
        closed_by="wd_timeout",
    )

    line = _fee_drag_line(loguru_sink)
    kv = _parse_kv(line)

    expected_notional = abs(10.0 * 100.0)  # size * entry fallback
    expected_fee_est = expected_notional * _BYBIT_TAKER_FEE_PER_SIDE * 2.0

    assert float(kv["notional"]) == pytest.approx(expected_notional)
    assert float(kv["rt_taker_fee_est"]) == pytest.approx(expected_fee_est, abs=1e-4)


def test_scratch_flag_Y_when_net_within_fee_estimate(
    coordinator: TradeCoordinator, loguru_sink
) -> None:
    """``abs(net) <= rt_taker_fee_est`` → scratch=Y (fee-dominated close).

    notional 1000 → fee_est = 1000 * 0.00055 * 2 = 1.10. net = +0.50 is inside
    the estimate, so the close is flagged a scratch.
    """
    _register(coordinator, symbol="ETHUSDT", side="Buy", entry=100.0, size=10.0)
    coordinator.on_trade_closed(
        symbol="ETHUSDT",
        pnl_pct=0.05,
        pnl_usd=0.50,           # |0.50| <= 1.10 fee est
        was_win=True,
        closed_by="wd_timeout",
    )

    kv = _parse_kv(_fee_drag_line(loguru_sink))
    fee_est = 1000.0 * _BYBIT_TAKER_FEE_PER_SIDE * 2.0
    assert abs(0.50) <= fee_est          # guard the premise of this case
    assert kv["scratch"] == "Y"


def test_scratch_flag_N_when_net_exceeds_fee_estimate(
    coordinator: TradeCoordinator, loguru_sink
) -> None:
    """``abs(net) > rt_taker_fee_est`` → scratch=N (a real win/loss, not drag).

    Same notional/fee_est (1.10) but net = +5.00 clears it, so NOT a scratch.
    """
    _register(coordinator, symbol="SOLUSDT", side="Buy", entry=100.0, size=10.0)
    coordinator.on_trade_closed(
        symbol="SOLUSDT",
        pnl_pct=0.5,
        pnl_usd=5.00,           # |5.00| > 1.10 fee est
        was_win=True,
        closed_by="wd_timeout",
    )

    kv = _parse_kv(_fee_drag_line(loguru_sink))
    fee_est = 1000.0 * _BYBIT_TAKER_FEE_PER_SIDE * 2.0
    assert abs(5.00) > fee_est           # guard the premise of this case
    assert kv["scratch"] == "N"


def test_fee_drag_obs_is_observability_only_does_not_change_booked_pnl(
    coordinator: TradeCoordinator, loguru_sink
) -> None:
    """The FEE_DRAG_OBS measurement must not mutate the booked ``pnl_usd``.

    The exact net passed in is the value broadcast to the close callbacks; the
    fee-drag block reads it but writes nothing back (trade_coordinator.py:1486-1514).
    """
    captured: list[dict] = []
    coordinator.register_close_callback(lambda r: captured.append(r))

    passed_pnl_usd = 0.50
    _register(coordinator, symbol="ADAUSDT", side="Buy", entry=100.0, size=10.0)
    coordinator.on_trade_closed(
        symbol="ADAUSDT",
        pnl_pct=0.05,
        pnl_usd=passed_pnl_usd,
        was_win=True,
        closed_by="wd_timeout",
    )

    # FEE_DRAG_OBS did fire (so the assertion below is meaningful)…
    _fee_drag_line(loguru_sink)

    # …yet the booked record carries the unmodified net that was passed in.
    assert len(captured) == 1
    assert captured[0]["pnl_usd"] == pytest.approx(passed_pnl_usd)
    assert captured[0]["was_win"] is True
