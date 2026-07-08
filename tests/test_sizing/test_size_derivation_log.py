"""Sniper-Latency-Size Fix Phase 3D — unified SIZE_DERIVATION event.

A single per-trade event captures the full per-layer breadcrumb chain
(Claude original -> APEX -> Gate Check 0 -> Gate Check 4 ->
Enforcer multiplier -> final) plus the conviction context that drove
the gate's CHECK 4 weighting. The event is emitted from
``strategy_worker._execute_claude_trade`` after every layer has
finished modifying size, before the trade is dispatched to the
exchange.

Tests target ``format_size_derivation_line``, the pure function
underlying ``log_size_derivation``. Loguru's file sinks don't
reliably surface in pytest's stdlib caplog so direct format
verification is more robust than log capture.
"""

from __future__ import annotations

from src.core.sizing_orchestrator import (
    format_size_derivation_line,
    log_size_derivation,
)


def _make_trade(**overrides) -> dict:
    base = {
        "symbol": "BTCUSDT",
        "_claude_original_size_usd": 200.0,
        "_apex_size_usd": 250.0,
        "_apex_optimized": True,
        "_gate_post_check0_size_usd": 250.0,
        "_gate_post_check4_size_usd": 240.0,
        "_xray_confidence": 0.85,
        "_setup_score": 75.0,
        "_expected_rr": 3.5,
    }
    base.update(overrides)
    return base


def test_full_breadcrumb_chain_rendered() -> None:
    """When every layer wrote its breadcrumb, the line renders all
    five sizes plus the conviction context."""
    trade = _make_trade()
    line = format_size_derivation_line(
        trade=trade,
        symbol="BTCUSDT",
        final_size_usd=180.0,
        final_leverage=3,
        enforcer_multiplier=0.75,
        enforcer_pre_size_usd=240.0,
    )
    assert "SIZE_DERIVATION | sym=BTCUSDT" in line
    assert "claude=$200" in line
    assert "apex=$250" in line
    assert "apex_opt=True" in line
    assert "gate_c0=$250" in line
    assert "gate_c4=$240" in line
    assert "enforcer_mult=0.75" in line
    assert "enforcer_pre=$240" in line
    assert "final=$180" in line
    assert "lev=3x" in line
    assert "xray_conf=0.85" in line
    assert "setup_score=75.0" in line
    assert "expected_rr=3.50" in line


def test_missing_breadcrumbs_render_as_na() -> None:
    """Missing breadcrumbs render as ``n/a`` rather than ``$0`` so the
    event is not ambiguous between 'layer fired and produced 0' vs
    'layer was bypassed'."""
    trade = {"symbol": "BTCUSDT"}  # no breadcrumbs at all
    line = format_size_derivation_line(
        trade=trade,
        symbol="BTCUSDT",
        final_size_usd=100.0,
        final_leverage=2,
        enforcer_multiplier=None,
        enforcer_pre_size_usd=None,
    )
    assert "claude=n/a" in line
    assert "apex=n/a" in line
    assert "gate_c0=n/a" in line
    assert "gate_c4=n/a" in line
    assert "enforcer_mult=n/a" in line
    assert "enforcer_pre=n/a" in line
    # Final size is always present (it's a required arg).
    assert "final=$100" in line


def test_apex_optimized_flag_in_event() -> None:
    """The ``apex_opt`` field surfaces whether APEX actually ran. A
    failed APEX optimization with the original Claude direction kept
    will show apex_opt=False so the operator can correlate."""
    trade = _make_trade(_apex_optimized=False)
    line = format_size_derivation_line(
        trade=trade,
        symbol="ETHUSDT",
        final_size_usd=200.0,
        final_leverage=2,
        enforcer_multiplier=1.0,
        enforcer_pre_size_usd=200.0,
    )
    assert "apex_opt=False" in line


def test_log_size_derivation_does_not_raise() -> None:
    """Smoke test that the live-emission entry point can be invoked
    with the same kwargs strategy_worker passes. Catches import-time
    regressions and signature mismatches."""
    log_size_derivation(
        trade={},
        symbol="TESTUSDT",
        final_size_usd=50.0,
        final_leverage=1,
    )
    # Also exercise the full-context path so both branches run.
    log_size_derivation(
        trade=_make_trade(),
        symbol="BTCUSDT",
        final_size_usd=180.0,
        final_leverage=3,
        enforcer_multiplier=0.75,
        enforcer_pre_size_usd=240.0,
    )
