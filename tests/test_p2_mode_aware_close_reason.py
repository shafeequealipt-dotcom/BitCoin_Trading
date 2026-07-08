"""P2 — TradeCoordinator mode-aware close reason fallback.

Surgical test: pop_close_reason returns f"{current_mode}_sl_tp" when
no explicit reason set, NOT the audit-flagged "shadow_sl_tp" literal.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.trade_coordinator import TradeCoordinator


def test_pop_close_reason_returns_mode_aware_default_in_bybit_demo() -> None:
    coord = TradeCoordinator()
    coord.attach_transformer(SimpleNamespace(current_mode="bybit_demo"))
    assert coord.pop_close_reason("BTCUSDT") == "bybit_demo_sl_tp"


def test_pop_close_reason_returns_mode_aware_default_in_shadow() -> None:
    coord = TradeCoordinator()
    coord.attach_transformer(SimpleNamespace(current_mode="shadow"))
    assert coord.pop_close_reason("BTCUSDT") == "shadow_sl_tp"


def test_pop_close_reason_returns_explicit_reason_when_set() -> None:
    coord = TradeCoordinator()
    coord.attach_transformer(SimpleNamespace(current_mode="bybit_demo"))
    coord.set_close_reason("BTCUSDT", "strategic_review")
    assert coord.pop_close_reason("BTCUSDT") == "strategic_review"
    # Second pop falls back to mode-aware default (set_close_reason consumed)
    assert coord.pop_close_reason("BTCUSDT") == "bybit_demo_sl_tp"


def test_pop_close_reason_falls_back_to_generic_when_no_transformer() -> None:
    coord = TradeCoordinator()
    # Not calling attach_transformer — early-boot edge case.
    assert coord.pop_close_reason("BTCUSDT") == "exchange_sl_tp"


def test_pop_close_reason_handles_transformer_exception_gracefully() -> None:
    coord = TradeCoordinator()

    class _BrokenTransformer:
        @property
        def current_mode(self):
            raise RuntimeError("transformer not initialised")

    coord.attach_transformer(_BrokenTransformer())
    # Falls back to generic — no leaked exception.
    assert coord.pop_close_reason("BTCUSDT") == "exchange_sl_tp"
