"""Issue 2.3 (2026-06-07, commit 1cb63d4) — APEX leverage-override kill-switch.

Pins the REAL optimizer gate in src/apex/optimizer.py:476-493 (the
``_apex_lev_override_enabled`` block). The gate is symmetric with the
``apex_dir_flip_enabled`` flip switch: when the leverage override is
DISABLED (default), the optimizer LLM is NOT allowed to change the
brain's directed leverage — APEX forces ``optimized.leverage`` back to
the brain's ``claude_leverage`` and prepends a
``[LEV OVERRIDE DISABLED by switch]`` tag to the reasoning, while every
other optimized parameter (SL/TP/size) is preserved. When ENABLED, the
model's leverage stands untouched.

The gate runs inside ``optimize()`` after ``_parse_response`` and BEFORE
``_apply_constraints`` (which clamps leverage to ``[1, max_leverage]``).
``max_leverage`` defaults to 5, so the leverages used here (3 and 5) sit
inside the clamp band and the clamp never masks the gate's decision.

Mirrors the end-to-end drive pattern of
``tests/test_apex_flip_decision_log.py`` (real TradeOptimizer + real
APEXSettings + a SimpleNamespace package + a MagicMock qwen_client whose
AsyncMock ``optimize`` returns a controlled ``leverage``). The gate code
is exercised for real — it is never re-implemented in the test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as loguru_logger

from src.apex.optimizer import TradeOptimizer
from src.config.settings import APEXSettings, _build_apex


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
) -> SimpleNamespace:
    """Build a stub IntelligencePackage with the fields the optimizer
    and prompt builder read end-to-end (mirrors test_apex_flip_decision_log).

    ranging regime + neutral situation WR keep the composite direction
    lock from firing, and the model is driven to keep the brain's
    direction below, so the flip gates all no-op and the leverage gate
    is the only behaviour under test.
    """
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
    history_ctx = SimpleNamespace(
        symbol=symbol,
        total_trades=100,  # Tier 1 — skip the "no data" early return
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
        trades=[],
    )
    situation_ctx = SimpleNamespace(
        regime=regime,
        fear_greed=50,
        total_trades_in_condition=100,
        buy_win_rate=50.0,
        sell_win_rate=50.0,
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
            setup_type="bullish_fvg_ob",
            rr_long=1.0,
            rr_short=1.0,
            setup_quality="B",
            setup_score=70,
            format=lambda: "stub structural data",
        ),
    )


def _make_optimizer(
    package: SimpleNamespace,
    *,
    model_leverage: int,
    lev_override_enabled: bool,
    qwen_dir: str = "Buy",
) -> TradeOptimizer:
    """Build a TradeOptimizer wired with mocks that drive a specific
    DeepSeek leverage (``model_leverage``) and a real APEXSettings with
    the leverage-override switch in the requested state.

    ``qwen_dir`` defaults to "Buy" (== the brain direction) so the flip
    gates no-op and only the leverage gate exercises behaviour.
    """
    qwen_client = MagicMock()
    qwen_client.optimize = AsyncMock(return_value={
        "content": {
            "direction": qwen_dir,
            "sl_pct": 1.0,
            "tp_pct": 2.0,
            "tp_mode": "fixed",
            "position_size_usd": 600,
            "leverage": model_leverage,
            "entry_timing": "immediate",
            "add_on_pullback": False,
            "reasoning": "model rationale",
            "confidence": 0.80,
        },
        "response_time_ms": 100,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.001,
        "model_used": "deepseek/deepseek-v3.2",
    })

    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=package)

    # Real APEXSettings so every gate field has a real numeric/bool
    # default. enabled=True so optimize() does not short-circuit at the
    # disabled check.
    settings = APEXSettings(
        enabled=True,
        apex_leverage_override_enabled=lev_override_enabled,
    )
    return TradeOptimizer(
        qwen_client=qwen_client, assembler=assembler, settings=settings,
    )


# ===========================================================================
# (a) Switch OFF + brain lev 3 + model lev 5 => leverage forced to 3 + tag
# ===========================================================================

@pytest.mark.asyncio
async def test_lev_override_off_forces_brain_leverage(
    loguru_sink: list[str],
) -> None:
    """Switch OFF, brain leverage 3, model leverage 5 → the gate
    (optimizer.py:479-493) forces optimized.leverage back to 3 and
    prepends '[LEV OVERRIDE DISABLED by switch]' to the reasoning."""
    pkg = _make_package()
    opt = _make_optimizer(pkg, model_leverage=5, lev_override_enabled=False)
    directive = {
        "symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600,
        "leverage": 3, "stop_loss_price": 0.95, "take_profit_price": 1.10,
    }
    result = await opt.optimize(directive)

    # Real gate ran (not the exception fallback path).
    assert result.is_fallback is False
    # Brain's directed leverage stands.
    assert result.leverage == 3
    # Gate's reasoning tag is prepended.
    assert result.reasoning.startswith("[LEV OVERRIDE DISABLED by switch]")
    # Optimization otherwise preserved (SL/TP/size still set).
    assert result.sl_pct > 0 and result.tp_pct > 0
    assert result.position_size_usd > 0
    # Dedicated per-decision suppressed-leverage log emitted.
    assert any("APEX_LEVERAGE_OVERRIDE_OFF" in m for m in loguru_sink)
    assert any(
        "brain_lev=3 qwen_lev=5" in m for m in loguru_sink
    ), "log must carry brain_lev/qwen_lev"


# ===========================================================================
# (b) Switch ON => model leverage stays 5, no tag
# ===========================================================================

@pytest.mark.asyncio
async def test_lev_override_on_keeps_model_leverage(
    loguru_sink: list[str],
) -> None:
    """Switch ON, brain leverage 3, model leverage 5 → the gate is a
    no-op: optimized.leverage stays 5 and no tag is added."""
    pkg = _make_package()
    opt = _make_optimizer(pkg, model_leverage=5, lev_override_enabled=True)
    directive = {
        "symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600,
        "leverage": 3, "stop_loss_price": 0.95, "take_profit_price": 1.10,
    }
    result = await opt.optimize(directive)

    assert result.is_fallback is False
    # Model leverage stands (5 is within the max_leverage=5 clamp band).
    assert result.leverage == 5
    # No suppression tag.
    assert "[LEV OVERRIDE DISABLED by switch]" not in result.reasoning
    # No suppression log line.
    assert not any("APEX_LEVERAGE_OVERRIDE_OFF" in m for m in loguru_sink)


# ===========================================================================
# (c) Brain leverage unspecified (claude_leverage == 0) => gate no-op
# ===========================================================================

@pytest.mark.asyncio
async def test_lev_override_off_noop_when_brain_leverage_unspecified(
    loguru_sink: list[str],
) -> None:
    """Switch OFF but brain leverage absent (claude_leverage == 0) → the
    gate's ``claude_leverage > 0`` guard is False so it no-ops: the
    model leverage stands and no tag is added.

    optimizer.py:295 computes claude_leverage = int(directive.get(
    'leverage', 0) or 0); with no 'leverage' key it is 0, so the gate
    cannot honor an unspecified brain leverage."""
    pkg = _make_package()
    opt = _make_optimizer(pkg, model_leverage=5, lev_override_enabled=False)
    # No 'leverage' key in the directive => claude_leverage == 0.
    directive = {
        "symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600,
        "stop_loss_price": 0.95, "take_profit_price": 1.10,
    }
    result = await opt.optimize(directive)

    assert result.is_fallback is False
    # Gate no-ops: model leverage stands.
    assert result.leverage == 5
    assert "[LEV OVERRIDE DISABLED by switch]" not in result.reasoning
    assert not any("APEX_LEVERAGE_OVERRIDE_OFF" in m for m in loguru_sink)


# ===========================================================================
# (d) Equal leverages => no spurious tag
# ===========================================================================

@pytest.mark.asyncio
async def test_lev_override_off_noop_when_leverages_equal(
    loguru_sink: list[str],
) -> None:
    """Switch OFF, brain leverage 5, model leverage 5 → the gate's
    ``optimized.leverage != claude_leverage`` guard is False so it
    no-ops: no spurious tag and no suppression log even though the
    switch is OFF."""
    pkg = _make_package()
    opt = _make_optimizer(pkg, model_leverage=5, lev_override_enabled=False)
    directive = {
        "symbol": "BTCUSDT", "direction": "Buy", "size_usd": 600,
        "leverage": 5, "stop_loss_price": 0.95, "take_profit_price": 1.10,
    }
    result = await opt.optimize(directive)

    assert result.is_fallback is False
    assert result.leverage == 5
    assert "[LEV OVERRIDE DISABLED by switch]" not in result.reasoning
    assert not any("APEX_LEVERAGE_OVERRIDE_OFF" in m for m in loguru_sink)


# ===========================================================================
# (e) Config-load: _build_apex maps apex_leverage_override_enabled
# ===========================================================================

def test_build_apex_maps_leverage_override_flag() -> None:
    """_build_apex({'apex_leverage_override_enabled': True}) yields
    APEXSettings.apex_leverage_override_enabled == True, and an absent
    key falls back to the default False (kill-switch defaults OFF)."""
    on = _build_apex({"apex_leverage_override_enabled": True})
    assert on.apex_leverage_override_enabled is True

    off = _build_apex({})
    assert off.apex_leverage_override_enabled is False

    # Dataclass default is also False (failure-safe to honor the brain).
    assert APEXSettings().apex_leverage_override_enabled is False
