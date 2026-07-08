"""PRIMARY Sell-Bias Fix (2026-05-11) — APEX_FLIP_DECISION log integration tests.

Verifies that the new unified APEX_FLIP_DECISION log emits on every
optimize() path with the correct decision_reason and field values.

Six decision_reason values to cover:
  1. no_flip_attempt       — DeepSeek returns brain direction
  2. lock_override         — pre-call lock + DeepSeek tried flip
  3. counter_protected     — counter-trade gate fired
  4. insufficient_data     — <5 trades in target direction
  5. conf_below_threshold  — confidence gate fired
  6. flip_accepted         — flip stands
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as loguru_logger

from src.apex.optimizer import TradeOptimizer
from src.config.settings import APEXSettings


@pytest.fixture
def loguru_sink() -> Any:
    """Capture loguru log messages into a list for assertion.

    The project uses loguru with file sinks (configured in
    src/core/logging.py); pytest's standard caplog does not capture
    loguru output. This fixture adds a temporary sink that appends
    formatted log lines into a list, then removes the sink on teardown.
    """
    messages: list[str] = []
    sink_id = loguru_logger.add(
        lambda msg: messages.append(str(msg)),
        format="{message}",
        level="DEBUG",
    )
    yield messages
    loguru_logger.remove(sink_id)


def _make_package(
    *,
    symbol: str = "BTCUSDT",
    claude_direction: str = "Buy",
    regime: str = "ranging",
    setup_type: str = "bullish_fvg_ob",
    history_trades: list[dict] | None = None,
    rr_long: float = 1.0,
    rr_short: float = 1.0,
) -> SimpleNamespace:
    """Build a stub IntelligencePackage with the fields the optimizer
    and prompt builder read end-to-end."""
    # Provide enough fields for build_apex_user_prompt (prompts.py:107+)
    directive_ctx = SimpleNamespace(
        symbol=symbol,
        direction=claude_direction,
        sl=0.95,
        tp=1.10,
        leverage=3,
        size_usd=600,
        signal_score=50,
        strategy_name="test",
        reasoning="test",
        plan_view="",
    )
    # symbol_history needs format-compatible numeric fields.
    history = history_trades or []
    history_ctx = SimpleNamespace(
        symbol=symbol,
        total_trades=100,  # Tier 1 - skip the "no data" early return
        wins=50,
        losses=50,
        win_rate=50.0,
        avg_win_pct=1.0,
        avg_loss_pct=-0.5,
        total_pnl_usd=10.0,
        ev_per_trade=0.05,
        profit_factor=1.5,
        avg_win_usd=10.0,
        avg_loss_usd=5.0,
        pattern_summary="stub",
        trades=history,
    )
    situation_ctx = SimpleNamespace(
        regime=regime,
        fear_greed=50,
        total_trades_in_condition=100,
        buy_win_rate=50.0,
        sell_win_rate=40.0,
        avg_buy_pnl=0.5,
        avg_sell_pnl=-0.3,
        direction_bias="neutral",
        common_categories=[],
    )

    coin_ctx = SimpleNamespace(
        current_price=1.0,
        recommended_tp_pct=2.0,
        recommended_sl_pct=1.0,
        volatility_class="medium",
        rsi=None,
        m4_composite=None,
        book_imbalance_pct=None,
        format=lambda: "stub coin data",
    )

    return SimpleNamespace(
        directive=directive_ctx,
        symbol_history=history_ctx,
        situation_data=situation_ctx,
        coin_data=coin_ctx,
        structural_data=SimpleNamespace(
            symbol=symbol,
            setup_type=setup_type,
            rr_long=rr_long,
            rr_short=rr_short,
            setup_quality="B",
            setup_score=70,
            format=lambda: "stub structural data",
        ),
    )


def _make_optimizer(
    package: SimpleNamespace, qwen_dir: str, qwen_conf: float = 0.85,
    flip_enabled: bool = True,
) -> TradeOptimizer:
    """Build a TradeOptimizer wired with mocks that drive a specific
    DeepSeek response (direction + confidence)."""
    qwen_client = MagicMock()
    qwen_client.optimize = AsyncMock(return_value={
        "content": {
            "direction": qwen_dir,
            "sl_pct": 1.0,
            "tp_pct": 2.0,
            "tp_mode": "fixed",
            "position_size_usd": 600,
            "leverage": 3,
            "entry_timing": "immediate",
            "add_on_pullback": False,
            "reasoning": "test",
            "confidence": qwen_conf,
        },
        "response_time_ms": 100,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.001,
        "model_used": "deepseek/deepseek-v3.2",
    })

    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=package)

    # Use real APEXSettings so all the new asymmetric / gate fields
    # have real numeric defaults (not MagicMock placeholders).
    # IMPLEMENT_APEX_FLIP_SWITCH (2026-05-25): apex_dir_flip_enabled now
    # defaults False in APEXSettings, which would suppress every flip here.
    # These tests verify the ON-state flip/gate behavior, so opt in
    # (flip_enabled=True). The OFF-state is covered by the dedicated test
    # test_apex_flip_decision_switch_off_suppresses_flip below.
    settings = APEXSettings(enabled=True, apex_dir_flip_enabled=flip_enabled)
    return TradeOptimizer(
        qwen_client=qwen_client, assembler=assembler, settings=settings,
    )


def _capture_apex_logs(messages: list[str]) -> list[str]:
    """Return APEX_FLIP_DECISION lines from captured loguru messages."""
    return [m for m in messages if "APEX_FLIP_DECISION" in m]


@pytest.mark.asyncio
async def test_apex_flip_decision_no_flip_attempt(
    loguru_sink: list[str],
) -> None:
    """DeepSeek returns same direction as brain → decision_reason=no_flip_attempt."""
    pkg = _make_package(regime="ranging", claude_direction="Buy")
    opt = _make_optimizer(pkg, qwen_dir="Buy", qwen_conf=0.80)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3, "stop_loss_price": 0.95, "take_profit_price": 1.10}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1, f"expected one APEX_FLIP_DECISION, got {len(logs)}"
    assert "decision_reason=no_flip_attempt" in logs[0]
    assert "brain_dir=Buy" in logs[0]
    assert "apex_dir=Buy" in logs[0]
    assert "flip_attempted=N" in logs[0]
    assert "flip_accepted=N" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_lock_override(
    loguru_sink: list[str],
) -> None:
    """Brain dir opposed by regime + DeepSeek flips → decision_reason=lock_override.

    BETA R2 update (2026-05-17): the old lock fired on trending_up + Buy
    via 'regime aligns' (advisory directive-forcing). Under composite
    scoring an aligned brain direction with no opposing evidence does
    not lock — only opposing signals can drive the composite score
    below 0. Updated to trending_down + Buy: regime signal = -1,
    composite < 0, lock fires, Qwen's Sell flip triggers the
    lock_override path identically to before.
    """
    # trending_down + Buy: regime signal -1 -> composite < 0 -> locked
    # Qwen tries Sell -> lock_override path forces direction back to Buy
    pkg = _make_package(regime="trending_down")
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.95)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"  # override forced back
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=lock_override" in logs[0]
    assert "qwen_initial_dir=Sell" in logs[0]
    assert "dir_locked=Y" in logs[0]
    assert "flip_attempted=Y" in logs[0]
    assert "flip_accepted=N" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_counter_protected(
    loguru_sink: list[str],
) -> None:
    """Counter-trade setup + DeepSeek flips → decision_reason=counter_protected."""
    # Provide enough Sell history so the insufficient-data gate doesn't
    # also fire. The counter-trade gate runs FIRST so it wins precedence.
    history = [{"direction": "Sell"} for _ in range(10)]
    pkg = _make_package(
        regime="ranging",
        setup_type="bullish_fvg_ob_counter",
        history_trades=history,
    )
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.95)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"  # counter gate reverted
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=counter_protected" in logs[0]
    assert "qwen_initial_dir=Sell" in logs[0]
    assert "flip_attempted=Y" in logs[0]
    assert "flip_accepted=N" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_insufficient_data(
    loguru_sink: list[str],
) -> None:
    """<5 trades in target direction + DeepSeek flips → decision_reason=insufficient_data."""
    # Only 2 Sell trades, no Buy. Flip to Sell triggers insufficient-data
    # (2 < 5). Use a non-counter setup so the counter-trade gate doesn't
    # fire first.
    history = [{"direction": "Sell"} for _ in range(2)]
    pkg = _make_package(
        regime="ranging",
        setup_type="bullish_fvg_ob",  # non-counter
        history_trades=history,
    )
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.95)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"  # insufficient-data gate reverted
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=insufficient_data" in logs[0]
    assert "flip_dir_trades=2" in logs[0]
    assert "flip_attempted=Y" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_conf_below_threshold(
    loguru_sink: list[str],
) -> None:
    """Buy→Sell flip with conf=0.85 < 0.95 threshold → conf_below_threshold."""
    # Sufficient history (8+ Sell trades, E27 min) so the insufficient-data
    # gate doesn't fire. Non-counter setup. Confidence 0.85 fails the
    # asymmetric 0.95 floor for Buy→Sell.
    history = [{"direction": "Sell"} for _ in range(8)]
    pkg = _make_package(
        regime="ranging",
        setup_type="bullish_fvg_ob",
        history_trades=history,
    )
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.85)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"  # confidence gate reverted
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=conf_below_threshold" in logs[0]
    assert "raw_conf=0.85" in logs[0]
    assert "flip_attempted=Y" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_flip_accepted(
    loguru_sink: list[str],
) -> None:
    """Buy→Sell flip with conf=0.95 = threshold + 8+ trades (E27 min) +
    non-counter → flip stands → flip_accepted."""
    history = [{"direction": "Sell"} for _ in range(8)]
    pkg = _make_package(
        regime="ranging",
        setup_type="bullish_fvg_ob",
        history_trades=history,
    )
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.95)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Sell"  # flip stands
    assert result.was_flipped is True
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=flip_accepted" in logs[0]
    assert "flip_attempted=Y" in logs[0]
    assert "flip_accepted=Y" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_sell_to_buy_uses_lower_threshold(
    loguru_sink: list[str],
) -> None:
    """Sell→Buy flip with conf=0.75 passes (0.75 >= 0.70 sell_to_buy floor).
    Same confidence would FAIL the Buy→Sell direction (0.75 < 0.95).
    Verifies asymmetric thresholds are wired through the full pipeline.

    BETA R2 update (2026-05-17): the composite lock's WR signal must
    not pull score below 0 on the brain's Sell direction or
    lock_override fires before _enforce_flip_confidence can evaluate
    the asymmetric threshold. The base fixture's sell_win_rate=40
    contributes wr_signal=-0.2 which is enough to lock. Override
    situation_data on this fixture to provide a NEUTRAL (50/50) WR so
    composite stays at 0 (ranging contributes 0) and the lock doesn't
    fire, leaving _enforce_flip_confidence as the gating mechanism.
    """
    history = [{"direction": "Buy"} for _ in range(8)]  # 8 Buy trades (E27 min)
    pkg = _make_package(
        regime="ranging",
        setup_type="bearish_fvg_ob",
        history_trades=history,
    )
    # Override WR fixture to keep composite-lock neutral (regime=ranging
    # contributes 0, no structural rr, no trade_direction => only WR
    # signal remains; force it to neutral 50/50 so lock does not fire
    # and _enforce_flip_confidence is the sole gating mechanism).
    pkg.situation_data.sell_win_rate = 50.0
    pkg.situation_data.buy_win_rate = 50.0
    opt = _make_optimizer(pkg, qwen_dir="Buy", qwen_conf=0.75)
    directive = {"symbol": "BTCUSDT", "direction": "Sell", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"  # flip stands at lower threshold
    assert result.was_flipped is True
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=flip_accepted" in logs[0]
    assert "brain_dir=Sell" in logs[0]
    assert "apex_dir=Buy" in logs[0]


@pytest.mark.asyncio
async def test_apex_flip_decision_switch_off_suppresses_flip(
    loguru_sink: list[str],
) -> None:
    """IMPLEMENT_APEX_FLIP_SWITCH (2026-05-25). With apex_dir_flip_enabled=False,
    a model-proposed flip that would otherwise stand (Buy->Sell, conf 0.95, 8+
    Sell trades) is suppressed at the switch gate: the result keeps the brain's
    Buy, was_flipped=False, decision_reason=flip_switch_off, and APEX's
    optimization (SL/TP/size/leverage) is STILL set (applied to the brain's
    direction). The proposed flip remains visible in the log
    (qwen_initial_dir=Sell, flip_attempted=Y), and the dedicated
    APEX_FLIP_SWITCH_OFF line is emitted. Mirrors the existing revert gates;
    optimization is preserved.
    """
    history = [{"direction": "Sell"} for _ in range(8)]
    pkg = _make_package(
        regime="ranging",
        setup_type="bullish_fvg_ob",
        history_trades=history,
    )
    opt = _make_optimizer(pkg, qwen_dir="Sell", qwen_conf=0.95, flip_enabled=False)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    # Flip suppressed — brain's direction stands.
    assert result.direction == "Buy"
    assert result.was_flipped is False
    # Optimization preserved: APEX still set stop/target/size/leverage.
    assert result.sl_pct > 0 and result.tp_pct > 0
    assert result.position_size_usd > 0 and result.leverage >= 1
    # Unified decision log attributes it to the switch, flip still visible.
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=flip_switch_off" in logs[0]
    assert "qwen_initial_dir=Sell" in logs[0]
    assert "flip_attempted=Y" in logs[0]
    assert "flip_accepted=N" in logs[0]
    # Dedicated per-decision suppressed-flip log emitted.
    assert any("APEX_FLIP_SWITCH_OFF" in m for m in loguru_sink)


@pytest.mark.asyncio
async def test_apex_flip_decision_switch_off_no_op_when_no_flip(
    loguru_sink: list[str],
) -> None:
    """IMPLEMENT_APEX_FLIP_SWITCH: with the switch OFF but the model keeping the
    brain's direction (no flip proposed), the switch does nothing —
    decision_reason=no_flip_attempt, and no APEX_FLIP_SWITCH_OFF line."""
    pkg = _make_package(regime="ranging", claude_direction="Buy")
    opt = _make_optimizer(pkg, qwen_dir="Buy", qwen_conf=0.80, flip_enabled=False)
    directive = {"symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600, "leverage": 3}
    result = await opt.optimize(directive)

    assert result.direction == "Buy"
    assert result.was_flipped is False
    logs = _capture_apex_logs(loguru_sink)
    assert len(logs) == 1
    assert "decision_reason=no_flip_attempt" in logs[0]
    assert not any("APEX_FLIP_SWITCH_OFF" in m for m in loguru_sink)
