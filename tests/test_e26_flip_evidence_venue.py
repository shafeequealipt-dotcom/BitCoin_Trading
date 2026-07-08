"""E26 — per-symbol, per-venue directional flip evidence.

Closes the latent venue-pooling gap: before E26 the flip insufficient-data
gate and the APEX prompt counted a coin's directional history across ALL
exchange_modes (demo + live + paper pooled). E26 isolates by the live venue.

Three surfaces:
  1. Repo: TIASRepository.get_symbol_flip_evidence filters by exchange_mode.
  2. Gate: optimizer._check_insufficient_data_for_flip prefers the venue-
     isolated count when present (fail-permissive fallback to pooled).
  3. Prompt: build_apex_user_prompt renders a per-coin+venue line, sample-gated.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest


# ──────────────────────────────────────────────────────────────────────
# Shared builders
# ──────────────────────────────────────────────────────────────────────

_INSERT_COLS = (
    "symbol, direction, strategy_name, strategy_category, closed_by, "
    "entry_price, exit_price, pnl_pct, pnl_usd, win, hold_seconds, "
    "regime, exchange_mode, trade_closed_at, captured_at"
)


async def _insert_trade(db, symbol, direction, win, regime, exchange_mode):
    await db.execute(
        f"INSERT INTO trade_intelligence ({_INSERT_COLS}) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            symbol, direction, "momentum", "trend", "tp",
            100.0, 101.0, 1.0, 5.0, int(win), 60.0,
            regime, exchange_mode, "2026-05-28T03:00:00", "2026-05-28T03:00:00",
        ),
    )


def _evidence(symbol="BTCUSDT", exchange_mode="bybit_demo", regime="trending_up",
              buy_count=0, sell_count=0, buy_wr=0.0, sell_wr=0.0, total=None):
    from src.apex.models import SymbolFlipEvidence
    return SymbolFlipEvidence(
        symbol=symbol, exchange_mode=exchange_mode, regime=regime,
        buy_count=buy_count, sell_count=sell_count,
        buy_win_rate=buy_wr, sell_win_rate=sell_wr,
        total=(buy_count + sell_count) if total is None else total,
    )


def _opt():
    from src.apex.optimizer import TradeOptimizer
    from src.config.settings import APEXSettings
    o = TradeOptimizer.__new__(TradeOptimizer)
    o._settings = APEXSettings()  # apex_min_trades_for_flip == 8 (E27)
    return o


def _full_package(flip_evidence):
    """A minimal-but-valid IntelligencePackage with flip_evidence attached."""
    from src.apex.models import (
        CoinData, DirectiveContext, IntelligencePackage,
        TIASSituationData, TIASSymbolHistory,
    )
    directive = DirectiveContext(
        symbol="BTCUSDT", direction="Buy", sl=100.0, tp=110.0,
        leverage=3, size_usd=600, reasoning="x", plan_view="y",
    )
    coin = CoinData(symbol="BTCUSDT", current_price=105.0)
    hist = TIASSymbolHistory(
        symbol="BTCUSDT", total_trades=0, wins=0, losses=0,
        win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
        total_pnl_usd=0.0, ev_per_trade=0.0, trades=[], regime="trending_up",
    )
    sit = TIASSituationData(
        regime="trending_up", fear_greed=55, total_trades_in_condition=40,
        buy_win_rate=55.0, sell_win_rate=45.0,
        avg_buy_pnl=0.5, avg_sell_pnl=-0.2, direction_bias="buy",
    )
    return IntelligencePackage(
        directive=directive, coin_data=coin, symbol_history=hist,
        situation_data=sit, flip_evidence=flip_evidence,
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Repo — venue isolation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_symbol_flip_evidence_isolates_by_venue() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "t.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # bybit_demo: 3 Buy (2 win), 1 Sell (0 win)
            for w in (True, True, False):
                await _insert_trade(db, "BTCUSDT", "Buy", w, "trending_up", "bybit_demo")
            await _insert_trade(db, "BTCUSDT", "Sell", False, "trending_up", "bybit_demo")
            # bybit (live): 5 Sell (all win) — must NOT leak into the demo count
            for _ in range(5):
                await _insert_trade(db, "BTCUSDT", "Sell", True, "trending_up", "bybit")

            repo = TradeIntelligenceRepo(db)

            demo = await repo.get_symbol_flip_evidence(
                "BTCUSDT", regime="trending_up", exchange_mode="bybit_demo")
            assert demo["buy_count"] == 3
            assert demo["sell_count"] == 1          # NOT 6 (live Sells excluded)
            assert demo["total"] == 4
            assert demo["buy_win_rate"] == pytest.approx(66.7, abs=0.2)

            live = await repo.get_symbol_flip_evidence(
                "BTCUSDT", regime="trending_up", exchange_mode="bybit")
            assert live["sell_count"] == 5
            assert live["buy_count"] == 0
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_get_symbol_flip_evidence_pooled_when_no_mode() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.tias.repository import TradeIntelligenceRepo

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "t.db"))
        await db.connect()
        try:
            await run_migrations(db)
            for _ in range(3):
                await _insert_trade(db, "ETHUSDT", "Buy", True, "ranging", "bybit_demo")
            for _ in range(2):
                await _insert_trade(db, "ETHUSDT", "Buy", True, "ranging", "bybit")

            repo = TradeIntelligenceRepo(db)
            pooled = await repo.get_symbol_flip_evidence(
                "ETHUSDT", regime="ranging", exchange_mode="")  # no filter
            assert pooled["buy_count"] == 5          # both venues pooled
            assert pooled["exchange_mode"] == ""     # echoes "no filter applied"
        finally:
            await db.disconnect()


# ──────────────────────────────────────────────────────────────────────
# 2. Gate — prefers venue-isolated evidence
# ──────────────────────────────────────────────────────────────────────


def test_flip_gate_prefers_venue_isolated_evidence() -> None:
    """Pooled trades say 10 Sell (sufficient), but venue-isolated evidence
    says only 3 Sell on this venue → gate must block (3 < 8)."""
    opt = _opt()
    ev = _evidence(exchange_mode="bybit_demo", buy_count=0, sell_count=3)
    pkg = _full_package(ev)
    # Pooled history says plenty of Sell — must be ignored in favor of venue.
    pkg.symbol_history.trades = [{"direction": "Sell"}] * 10
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell")
    assert insufficient is True
    assert count == 3        # venue count, not the pooled 10


def test_flip_gate_allows_when_venue_evidence_sufficient() -> None:
    opt = _opt()
    ev = _evidence(exchange_mode="bybit_demo", buy_count=0, sell_count=8)
    pkg = _full_package(ev)
    pkg.symbol_history.trades = []      # pooled empty — venue is authoritative
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell")
    assert insufficient is False
    assert count == 8


def test_flip_gate_falls_back_when_evidence_pooled() -> None:
    """When exchange_mode is empty (live mode unknown) the evidence is
    non-authoritative; the gate falls back to the pooled trades list."""
    opt = _opt()
    ev = _evidence(exchange_mode="", buy_count=0, sell_count=0)  # pooled sentinel
    pkg = _full_package(ev)
    pkg.symbol_history.trades = [{"direction": "Sell"}] * 9   # pooled sufficient
    insufficient, count = opt._check_insufficient_data_for_flip(
        pkg, claude_direction="Buy", qwen_direction="Sell")
    assert insufficient is False
    assert count == 9        # used the pooled list, ignored the empty evidence


# ──────────────────────────────────────────────────────────────────────
# 3. Prompt — sample-gated per-coin line
# ──────────────────────────────────────────────────────────────────────


def test_prompt_renders_per_coin_line_when_sample_sufficient() -> None:
    from src.apex.prompts import build_apex_user_prompt
    ev = _evidence(symbol="BTCUSDT", exchange_mode="bybit_demo",
                   buy_count=5, sell_count=4, buy_wr=60.0, sell_wr=25.0)  # total 9 >= 8
    prompt = build_apex_user_prompt(_full_package(ev))
    assert "PER-COIN DIRECTIONAL HISTORY" in prompt
    assert "venue=bybit_demo" in prompt
    assert "60.0% win rate over 5 trades" in prompt


def test_prompt_omits_per_coin_line_when_sample_sparse() -> None:
    from src.apex.prompts import build_apex_user_prompt
    ev = _evidence(symbol="BTCUSDT", exchange_mode="bybit_demo",
                   buy_count=2, sell_count=1)  # total 3 < 8
    prompt = build_apex_user_prompt(_full_package(ev))
    assert "PER-COIN DIRECTIONAL HISTORY" not in prompt


def test_prompt_unaffected_when_no_evidence() -> None:
    from src.apex.prompts import build_apex_user_prompt
    prompt = build_apex_user_prompt(_full_package(None))
    assert "PER-COIN DIRECTIONAL HISTORY" not in prompt
    # The all-coins situation data is still present (not regressed).
    assert "TIAS SITUATION DATA" in prompt
