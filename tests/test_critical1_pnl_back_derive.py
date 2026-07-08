"""Unit + integration tests for CRITICAL-1 (pnl_pct back-derive in coordinator).

Closes /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md CRITICAL-1.

The bybit_demo WS subscriber (`bybit_demo_websocket_subscriber.py:489-497`)
passes ``pnl_pct=0.0, pnl_usd=0.0, was_win=False`` as sentinel placeholders
together with the authoritative ``exit_price`` from the Bybit fill. The fix
adds a back-derive branch in ``trade_coordinator.on_trade_closed`` that
computes pnl_pct from ``entry_price``, ``close_price``, and ``state.side``
when those sentinel values arrive. This file pins the new contract.

Test groups:
    1. Sell back-derive (5 fixtures from c1_phase1_data_samples.md — real
       2026-05-09 trades whose trade_log shows pnl=0 but trade_history
       computed the correct values inline).
    2. Buy back-derive (3 mirror fixtures).
    3. was_win flip from back-derived pnl.
    4. Negative controls — back-derive must NOT run when:
       - Caller already provided a non-zero pnl_pct (system-initiated path).
       - entry_price is zero (defensive).
       - close_price is zero (defensive).
    5. Flat trade — entry==exit yields pnl_pct=0, was_win=False, no
       DL_TRADE_SUSPECT firing predicted (the data_lake guard requires
       entry != exit).
    6. Cooldown timing — every close (win or loss, back-derived or
       caller-provided) starts the uniform 300s per-(symbol, direction)
       cooldown introduced in Issue 3 (2026-05-18). The legacy
       180/600/900 split that this group originally regression-tested
       was removed in issue3/p3-3.
    7. Integration — full coordinator close path produces a record dict
       carrying the back-derived values, ready for the 14-callback
       fan-out at trade_coordinator.py:776-784.
"""

from __future__ import annotations

import pytest

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def coordinator() -> TradeCoordinator:
    return TradeCoordinator()


def _register(
    coord: TradeCoordinator,
    symbol: str,
    side: str,
    entry: float,
    size: float = 100.0,
) -> None:
    """Helper: call register_trade with the minimal fields needed for tests."""
    coord.register_trade(
        symbol=symbol,
        strategy_category="default",
        strategy_name="test",
        entry_price=entry,
        side=side,
        size=size,
    )


def _last_record(coord: TradeCoordinator) -> dict:
    """Helper: return the most recent close record from the in-memory ring."""
    assert coord._closed_trades, "expected at least one closed trade"
    return coord._closed_trades[-1]


# ──────────────────────────────────────────────────────────────────────
# Group 1 — Sell back-derive (5 real samples from data_samples.md)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol,entry,exit_price,expected_pnl_pct,expected_win",
    [
        # ADAUSDT 2026-05-09 19:52:32 — observed loss, trade_log corrupted to 0
        # Adapter computed -0.0367647058823489 (DB display); full FP value below
        ("ADAUSDT", 0.272, 0.2721, -0.03676470588234889, False),
        # IMXUSDT 2026-05-09 19:52:31 — observed win, trade_log corrupted to 0
        ("IMXUSDT", 0.18976, 0.18974, +0.010539629005069561, True),
        # ARBUSDT 2026-05-09 19:52:31 — observed loss, trade_log corrupted to 0
        ("ARBUSDT", 0.14207, 0.14208, -0.007038783698183995, False),
        # NEARUSDT 2026-05-09 19:52:30 — observed win, trade_log corrupted to 0
        ("NEARUSDT", 1.5585, 1.5582, +0.019249278152067176, True),
        # KATUSDT 2026-05-09 19:52:30 — flat trade, both tables already 0
        ("KATUSDT", 0.01031, 0.01031, 0.0, False),
    ],
)
def test_sell_back_derive_matches_trade_history(
    coordinator: TradeCoordinator,
    symbol: str,
    entry: float,
    exit_price: float,
    expected_pnl_pct: float,
    expected_win: bool,
) -> None:
    """Real-trade fixtures: coordinator's back-derived pnl_pct must equal
    the value bybit_demo_adapter inline-computed and wrote to trade_history."""
    _register(coordinator, symbol=symbol, side="Sell", entry=entry)
    coordinator.on_trade_closed(
        symbol=symbol,
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=exit_price,
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == pytest.approx(expected_pnl_pct, rel=1e-9, abs=1e-12)
    assert record["was_win"] is expected_win
    # close_price must be the authoritative exit_price the caller passed
    assert record["close_price"] == pytest.approx(exit_price, abs=1e-9)
    # direction is preserved from state.side
    assert record["direction"] == "Sell"


# ──────────────────────────────────────────────────────────────────────
# Group 2 — Buy back-derive (3 mirror fixtures)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol,entry,exit_price,expected_pnl_pct,expected_win",
    [
        # Synthetic Buy win: BTC moves up 1%
        ("BTCUSDT", 50000.0, 50500.0, +1.0, True),
        # Synthetic Buy loss: ETH moves down 0.1%
        ("ETHUSDT", 3000.0, 2997.0, -0.1, False),
        # Synthetic Buy flat: SOL stays put
        ("SOLUSDT", 100.0, 100.0, 0.0, False),
    ],
)
def test_buy_back_derive_uses_canonical_formula(
    coordinator: TradeCoordinator,
    symbol: str,
    entry: float,
    exit_price: float,
    expected_pnl_pct: float,
    expected_win: bool,
) -> None:
    """Buy direction back-derive: pnl_pct = ((exit - entry) / entry) * 100."""
    _register(coordinator, symbol=symbol, side="Buy", entry=entry)
    coordinator.on_trade_closed(
        symbol=symbol,
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=exit_price,
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == pytest.approx(expected_pnl_pct, rel=1e-9, abs=1e-12)
    assert record["was_win"] is expected_win
    assert record["direction"] == "Buy"


# ──────────────────────────────────────────────────────────────────────
# Group 3 — was_win flip is consistent with pnl_pct sign
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "side,entry,exit_price,expected_win",
    [
        ("Buy", 100.0, 101.0, True),    # up = Buy win
        ("Buy", 100.0, 99.0, False),    # down = Buy loss
        ("Sell", 100.0, 99.0, True),    # down = Sell win
        ("Sell", 100.0, 101.0, False),  # up = Sell loss
        ("Buy", 100.0, 100.0, False),   # flat = not a win
        ("Sell", 100.0, 100.0, False),  # flat = not a win
    ],
)
def test_was_win_flip(
    coordinator: TradeCoordinator,
    side: str,
    entry: float,
    exit_price: float,
    expected_win: bool,
) -> None:
    """was_win must be True iff back-derived pnl_pct > 0."""
    _register(coordinator, symbol="X", side=side, entry=entry)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=exit_price,
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["was_win"] is expected_win


# ──────────────────────────────────────────────────────────────────────
# Group 4 — Negative controls (back-derive must NOT run)
# ──────────────────────────────────────────────────────────────────────


def test_no_back_derive_when_pnl_already_provided(
    coordinator: TradeCoordinator,
) -> None:
    """System-initiated callers (and any future caller) that pre-compute
    pnl_pct must have their value preserved unchanged. The back-derive
    runs ONLY on the sentinel-zero contract."""
    _register(coordinator, symbol="X", side="Buy", entry=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=2.5,        # caller-supplied; back-derive must not overwrite
        pnl_usd=25.0,       # caller-supplied
        was_win=True,       # caller-supplied
        exit_price=102.5,
        price_source="exchange_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == pytest.approx(2.5)
    assert record["pnl_usd"] == pytest.approx(25.0)
    assert record["was_win"] is True


def test_no_back_derive_when_zero_entry_price(
    coordinator: TradeCoordinator,
) -> None:
    """Defensive: when entry_price is zero (impossible in production but
    possible if state was never registered properly), skip the back-derive
    and pass the sentinel zeros through unchanged."""
    _register(coordinator, symbol="X", side="Buy", entry=0.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=100.0,
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == 0.0
    assert record["was_win"] is False


def test_no_back_derive_when_zero_exit_price(
    coordinator: TradeCoordinator,
) -> None:
    """Defensive: when exit_price is zero (cannot happen via WS — Bybit's
    execPrice is always > 0 — but possible via direct calls), skip the
    back-derive."""
    _register(coordinator, symbol="X", side="Buy", entry=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=None,
        price_source="ticker_fallback",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == 0.0
    assert record["was_win"] is False


def test_short_alias_treated_as_sell(coordinator: TradeCoordinator) -> None:
    """Defensive: the legacy "Short" string alias maps to Sell semantics,
    matching the existing close_price back-derive at lines 689-693."""
    _register(coordinator, symbol="X", side="Short", entry=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=99.0,  # Short with price down = win
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == pytest.approx(1.0)
    assert record["was_win"] is True


# ──────────────────────────────────────────────────────────────────────
# Group 5 — pnl_usd back-derive runs after pnl_pct back-derive
# ──────────────────────────────────────────────────────────────────────


def test_pnl_usd_is_back_derived_after_pnl_pct(
    coordinator: TradeCoordinator,
) -> None:
    """The existing pnl_usd back-derive at lines 731-740 (gated on
    pnl_pct != 0) must now run because the new pnl_pct back-derive
    populates pnl_pct first."""
    # size=100, entry=100, exit=101 → notional=10000, pnl_pct=+1.0%,
    # pnl_usd should equal 0.01 * abs(100 * 100) = 100.0
    _register(coordinator, symbol="X", side="Buy", entry=100.0, size=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=101.0,
        price_source="bybit_ws_authoritative",
    )
    record = _last_record(coordinator)
    assert record["pnl_pct"] == pytest.approx(1.0)
    assert record["pnl_usd"] == pytest.approx(100.0)
    assert record["was_win"] is True


# ──────────────────────────────────────────────────────────────────────
# Group 6 — Cooldown timing reflects back-derived was_win
# ──────────────────────────────────────────────────────────────────────


def test_cooldown_reflects_back_derived_win(coordinator: TradeCoordinator) -> None:
    """One of six was_win consumers is the cooldown-timing branch at
    trade_coordinator.py:786-792. A winning back-derive must produce a
    180s cooldown (not 600s loss-grade)."""
    import time

    _register(coordinator, symbol="X", side="Buy", entry=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=101.0,
        price_source="bybit_ws_authoritative",
    )
    # Issue 3 (2026-05-18) — cooldown is now uniform 300s per
    # (symbol, direction) regardless of win/loss. The legacy 180/600/900
    # branch was deleted in issue3/p3-3. The Buy side opened above; the
    # close set (X, Buy) -> expiry ~300s from now.
    blocked, remaining = coordinator.is_reentry_blocked("X", "Buy")
    assert blocked is True
    assert 280 <= remaining <= 300, (
        f"expected ~300s uniform cooldown, got {remaining}s"
    )


def test_cooldown_reflects_back_derived_loss(coordinator: TradeCoordinator) -> None:
    """Mirror: losing back-derive produces 600s loss-grade cooldown."""
    import time

    _register(coordinator, symbol="Y", side="Buy", entry=100.0)
    coordinator.on_trade_closed(
        symbol="Y",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=99.0,
        price_source="bybit_ws_authoritative",
    )
    # Issue 3 (2026-05-18) — cooldown is now uniform 300s per
    # (symbol, direction). The 600s loss-grade window from T2-1 was
    # replaced. The Buy side opened above; the close set (Y, Buy) ->
    # expiry ~300s from now.
    blocked, remaining = coordinator.is_reentry_blocked("Y", "Buy")
    assert blocked is True
    assert 280 <= remaining <= 300, (
        f"expected ~300s uniform cooldown, got {remaining}s"
    )


# ──────────────────────────────────────────────────────────────────────
# Group 7 — Integration: full close path with a registered callback
# ──────────────────────────────────────────────────────────────────────


def test_close_callback_receives_back_derived_record(
    coordinator: TradeCoordinator,
) -> None:
    """The coordinator's close_callback fan-out at lines 776-784 broadcasts
    the post-back-derive record dict. Downstream consumers (data_lake,
    thesis_manager, TIAS, etc.) must see the corrected values, not the
    sentinel zeros that arrived at the function."""
    captured: list[dict] = []

    def my_callback(record: dict) -> None:
        captured.append(record)

    coordinator.register_close_callback(my_callback)

    # IMXUSDT real sample: Sell 0.18976 → 0.18974 = +0.0105% win
    _register(coordinator, symbol="IMXUSDT", side="Sell", entry=0.18976)
    coordinator.on_trade_closed(
        symbol="IMXUSDT",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=0.18974,
        price_source="bybit_ws_authoritative",
    )

    assert len(captured) == 1
    record = captured[0]
    assert record["pnl_pct"] == pytest.approx(0.010539629005069561, rel=1e-9)
    assert record["was_win"] is True
    assert record["close_price"] == pytest.approx(0.18974)
    assert record["direction"] == "Sell"
    # The fields that data_lake.write_trade reads must all be present
    assert "trade_id" in record
    assert "entry_price" in record
    assert "closed_at" in record


def test_double_close_does_not_back_derive(coordinator: TradeCoordinator) -> None:
    """The L2 dedup at lines 666-675 pops state on first close. A second
    call for the same symbol returns early with COORD_DOUBLE_CLOSE — the
    back-derive must not run because there is no state to reference."""
    _register(coordinator, symbol="X", side="Buy", entry=100.0)
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=101.0,
        price_source="bybit_ws_authoritative",
    )
    closed_count_first = len(coordinator._closed_trades)
    # Second close for same symbol → state already popped; should warn + return
    coordinator.on_trade_closed(
        symbol="X",
        pnl_pct=0.0,
        pnl_usd=0.0,
        was_win=False,
        exit_price=102.0,
        price_source="bybit_ws_authoritative",
    )
    # Ring did not grow on the second call
    assert len(coordinator._closed_trades) == closed_count_first
