"""Sniper-Latency-Size Fix Phase 3D — unified sizing-derivation logging.

Phase 0 investigation showed the existing breadcrumbs (``ENFORCER_SIZE``,
``CONVICTION_SIZE_CAP``, ``GATE_ADJUST``, ``XRAY_DIR_FLIP``) are
scattered across four files and only fire conditionally — there is no
single per-trade event the operator can grep to answer "why did this
trade get $X?". This module provides a single ``SIZE_DERIVATION``
event with the full per-layer breadcrumb chain plus the conviction
context that drove the gate's CHECK 4 weighting.

Each contributing layer writes a small breadcrumb onto the trade dict
as it modifies size; this module reads them and emits one log line
per executed trade. Failure to read any single breadcrumb does not
crash the emission — missing values render as ``None`` so the event
shape stays consistent regardless of which layers fired.

Pure helper — no service registration, no async, no dependencies on
runtime services. The caller is responsible for invoking
``log_size_derivation`` after the executing layer has finished.
"""

from __future__ import annotations

from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("sizing")


def _f(value: Any, default: float | None = 0.0) -> float | None:
    """Coerce a trade-dict breadcrumb to float; ``None`` if missing or
    non-numeric so the caller can render ``None`` rather than 0 (which
    would be ambiguous with a layer firing and producing 0)."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_money(v: float | None) -> str:
    """Render a USD breadcrumb. ``None`` becomes ``n/a`` so missing
    layers are visually distinct from layers that produced 0."""
    return "n/a" if v is None else f"${v:.0f}"


def _fmt_float(v: float | None, decimals: int = 2) -> str:
    """Render a float field. ``None`` becomes ``n/a``."""
    return "n/a" if v is None else f"{v:.{decimals}f}"


def format_size_derivation_line(
    *,
    trade: dict,
    symbol: str,
    final_size_usd: float,
    final_leverage: int,
    enforcer_multiplier: float | None = None,
    enforcer_pre_size_usd: float | None = None,
) -> str:
    """Build the ``SIZE_DERIVATION`` log line body (without the
    trailing ``| ctx``). Pure function — accepts a trade-dict snapshot
    and returns a string. Tests use this directly to verify the
    rendering shape; ``log_size_derivation`` wraps this for the live
    emission path.
    """
    claude_orig = _f(trade.get("_claude_original_size_usd"), default=None)
    apex_size = _f(trade.get("_apex_size_usd"), default=None)
    gate_c0_size = _f(trade.get("_gate_post_check0_size_usd"), default=None)
    gate_c4_size = _f(trade.get("_gate_post_check4_size_usd"), default=None)
    xray_conf = _f(trade.get("_xray_confidence"), default=None)
    setup_score = _f(trade.get("_setup_score"), default=None)
    expected_rr = _f(trade.get("_expected_rr"), default=None)
    apex_optimized = bool(trade.get("_apex_optimized", False))

    return (
        f"SIZE_DERIVATION | sym={symbol} "
        f"claude={_fmt_money(claude_orig)} "
        f"apex={_fmt_money(apex_size)} apex_opt={apex_optimized} "
        f"gate_c0={_fmt_money(gate_c0_size)} "
        f"gate_c4={_fmt_money(gate_c4_size)} "
        f"enforcer_mult={_fmt_float(enforcer_multiplier)} "
        f"enforcer_pre={_fmt_money(enforcer_pre_size_usd)} "
        f"final=${final_size_usd:.0f} lev={final_leverage}x "
        f"xray_conf={_fmt_float(xray_conf)} "
        f"setup_score={_fmt_float(setup_score, 1)} "
        f"expected_rr={_fmt_float(expected_rr)}"
    )


def log_size_derivation(
    *,
    trade: dict,
    symbol: str,
    final_size_usd: float,
    final_leverage: int,
    enforcer_multiplier: float | None = None,
    enforcer_pre_size_usd: float | None = None,
) -> None:
    """Emit the unified ``SIZE_DERIVATION`` event for one executed trade.

    Args:
        trade: the trade dict carried through the sizing pipeline.
            Must contain breadcrumbs stamped by each upstream layer
            (claude_original, _apex_size_usd, _gate_post_check0_size,
            _gate_post_check4_size). Any missing breadcrumb renders
            as ``None`` in the event.
        symbol: trading pair (e.g. ``"BTCUSDT"``).
        final_size_usd: the size that will actually be sent to the
            exchange (post-enforcer).
        final_leverage: the leverage that will actually be sent.
        enforcer_multiplier: the enforcer's PnL-based size multiplier.
            ``None`` when the enforcer wasn't consulted (e.g. PnL > 0
            so no throttle applied).
        enforcer_pre_size_usd: the size BEFORE the enforcer multiplied
            it, so the event can reconstruct ``pre × mult = final``.

    Side effects: emits one ``SIZE_DERIVATION`` log line at INFO level.
    """
    line = format_size_derivation_line(
        trade=trade,
        symbol=symbol,
        final_size_usd=final_size_usd,
        final_leverage=final_leverage,
        enforcer_multiplier=enforcer_multiplier,
        enforcer_pre_size_usd=enforcer_pre_size_usd,
    )
    log.info(f"{line} | {ctx()}")
