"""T2-3 TIAS exchange_mode resolution tests (2026-05-12).

Pre-fix bug (F18, verified 5h log scan):
  - trade_intelligence schema (v32) has an exchange_mode column with
    NOT NULL DEFAULT 'shadow'.
  - The collector's _extract_group_a NEVER populated this column.
  - Every TIAS row defaulted to 'shadow' even for unambiguously
    bybit_demo trades (closed_by=bybit_sl_hit, mode4_*, etc.).
  - Brain's learning loop saw cross-mode contamination — bybit_demo
    trades labeled as shadow trades, performance metrics mixed
    across modes.

Fix: thread exchange_mode through coordinator → collector → row:
  - TradeCoordinator.on_trade_closed reads `_current_mode()` (which
    pulls Transformer.current_mode) and adds it to the record dict.
  - Same for on_partial_close (parity).
  - TradeContextCollector._extract_group_a reads it from the record
    and includes in the Group A dict.
  - TradeIntelligence dataclass has the field; repo's asdict-based
    INSERT picks it up automatically.
  - Always emits TIAS_MODE_RESOLVED log so the wiring is observable.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── T2-3 unit tests: model + collector + coordinator wiring ──────────


def test_t2_3_trade_intelligence_has_exchange_mode_field():
    """TradeIntelligence dataclass must expose exchange_mode for the
    asdict-based repo INSERT to pick up the column."""
    from src.tias.models import TradeIntelligence
    ti = TradeIntelligence(
        symbol="BTCUSDT", direction="Buy", strategy_name="x",
        strategy_category="x", source="x", closed_by="x",
        entry_price=1.0, exit_price=1.05, pnl_pct=5.0, pnl_usd=5.0,
        win=True, hold_seconds=60.0,
    )
    assert hasattr(ti, "exchange_mode")
    assert ti.exchange_mode == ""  # default


def test_t2_3_trade_intelligence_accepts_exchange_mode_kwarg():
    """The dataclass accepts exchange_mode as a kwarg (used by asdict
    round-trip when the collector passes it through Group A)."""
    from src.tias.models import TradeIntelligence
    ti = TradeIntelligence(
        symbol="BTCUSDT", direction="Buy", strategy_name="x",
        strategy_category="x", source="x", closed_by="x",
        entry_price=1.0, exit_price=1.05, pnl_pct=5.0, pnl_usd=5.0,
        win=True, hold_seconds=60.0,
        exchange_mode="bybit_demo",
    )
    assert ti.exchange_mode == "bybit_demo"


def test_t2_3_collector_extracts_exchange_mode_from_record():
    """_extract_group_a must read exchange_mode from the record dict
    and include it in the returned Group A fields."""
    from src.tias.collector import TradeContextCollector
    coll = TradeContextCollector(
        services={}, db=MagicMock(),
    )
    record = {
        "symbol": "BTCUSDT",
        "direction": "Buy",
        "strategy_name": "test_strat",
        "strategy_category": "trend",
        "source": "claude",
        "closed_by": "bybit_sl_hit",
        "entry_price": 50000.0,
        "close_price": 49500.0,  # collector reads close_price → exit_price
        "pnl_pct": -1.0,
        "pnl_usd": -50.0,
        "was_win": False,
        "hold_seconds": 600.0,
        "exchange_mode": "bybit_demo",
    }
    out = coll._extract_group_a(record)
    assert out["exchange_mode"] == "bybit_demo"
    assert out["symbol"] == "BTCUSDT"
    assert out["pnl_pct"] == -1.0


def test_t2_3_collector_handles_missing_exchange_mode_gracefully():
    """Legacy callers (or test fixtures) that don't pass exchange_mode
    must produce empty-string in Group A — never raise. The TIAS_MODE_
    RESOLVED warning fires so the gap is visible in production logs."""
    from src.tias.collector import TradeContextCollector
    coll = TradeContextCollector(services={}, db=MagicMock())
    record = {
        "symbol": "BTCUSDT",
        "direction": "Buy",
        "strategy_name": "x",
        "strategy_category": "x",
        "source": "x",
        "closed_by": "x",
        "entry_price": 1.0,
        "close_price": 1.0,
        "pnl_pct": 0.0,
        "pnl_usd": 0.0,
        "was_win": False,
        "hold_seconds": 1.0,
        # exchange_mode omitted
    }
    out = coll._extract_group_a(record)
    assert out["exchange_mode"] == ""


def test_t2_3_coordinator_current_mode_uses_transformer():
    """TradeCoordinator._current_mode reads transformer.current_mode."""
    from src.core.trade_coordinator import TradeCoordinator
    coord = TradeCoordinator()
    # No transformer attached → empty string
    assert coord._current_mode() == ""

    # Attach a stub transformer with current_mode='bybit_demo'
    transformer = MagicMock()
    transformer.current_mode = "bybit_demo"
    coord.attach_transformer(transformer)
    assert coord._current_mode() == "bybit_demo"

    # Mode change reflected immediately
    transformer.current_mode = "shadow"
    assert coord._current_mode() == "shadow"


def test_t2_3_coordinator_current_mode_handles_transformer_failure():
    """If transformer.current_mode raises, coordinator falls back to ''."""
    from src.core.trade_coordinator import TradeCoordinator

    class _BrokenTransformer:
        @property
        def current_mode(self):
            raise RuntimeError("boom")

    coord = TradeCoordinator()
    coord.attach_transformer(_BrokenTransformer())
    assert coord._current_mode() == ""


def test_t2_3_repo_save_persists_exchange_mode_via_asdict():
    """The TIAS repo uses asdict(trade) so adding exchange_mode to the
    dataclass automatically includes it in the INSERT column list. This
    test verifies the dict produced by asdict includes the field with
    the expected value."""
    from dataclasses import asdict

    from src.tias.models import TradeIntelligence
    ti = TradeIntelligence(
        symbol="BTCUSDT", direction="Buy", strategy_name="x",
        strategy_category="x", source="x", closed_by="bybit_sl_hit",
        entry_price=1.0, exit_price=1.05, pnl_pct=5.0, pnl_usd=5.0,
        win=True, hold_seconds=60.0,
        exchange_mode="bybit_demo",
    )
    d = asdict(ti)
    assert "exchange_mode" in d
    assert d["exchange_mode"] == "bybit_demo"
    # Group A field set is preserved
    assert d["symbol"] == "BTCUSDT"
    assert d["closed_by"] == "bybit_sl_hit"


# ── T2-3 contract test: forward-only behaviour for legacy data ───────


def test_t2_3_default_empty_string_does_not_break_existing_rows():
    """The default empty-string for exchange_mode allows existing
    code paths (and test fixtures) that don't yet set the field to
    pass without raising. The forward-only fix per the plan: existing
    historical TIAS rows are unaffected; new rows after deploy carry
    the correct mode."""
    from src.tias.models import TradeIntelligence
    ti = TradeIntelligence(
        symbol="x", direction="Buy", strategy_name="x",
        strategy_category="x", source="x", closed_by="x",
        entry_price=1.0, exit_price=1.0, pnl_pct=0.0, pnl_usd=0.0,
        win=False, hold_seconds=0.0,
    )
    assert ti.exchange_mode == ""
