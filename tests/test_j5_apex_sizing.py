"""J5 (2026-05-14) — APEX dynamic per-trade size cap + conviction scale.

Audit OBS-15: 15 of 18 trades (83%) collapsed to exactly $1200 because
the cap at src/apex/optimizer.py:828-830 was a static dollar amount.
Brain proposed $12k-$18k per trade; the static cap and the upstream
enforcer 0.5x multiplier produced identical output regardless of
signal strength.

The fix replaces the static cap with:

  effective_cap = max(static_cap, trading_capital * pct_of_equity / 100)
  scaled = min(pre_cap, effective_cap) * max(conviction_floor, confidence)
  final = max(100.0, scaled)

Defaults preserve byte-equivalent legacy behaviour
(``apex_size_cap_pct_of_equity = 0.0`` → static cap used). The fix
takes effect only when the operator wires the percentage value in
config.toml.

Tests in this module exercise ``TradeOptimizer._apply_settings_clamps``
directly with synthetic OptimizedTrade inputs so the sizing decision
is observable without standing up the full optimize() pipeline.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from loguru import logger as _loguru_logger

from src.apex.models import OptimizedTrade
from src.apex.optimizer import TradeOptimizer


# --- Helpers --------------------------------------------------------


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _build_settings(
    *,
    max_position_size_usd: float = 1200.0,
    apex_size_cap_pct_of_equity: float = 0.0,
    apex_size_conviction_floor: float = 0.5,
    max_leverage: int = 3,
    min_tp_pct: float = 0.3,
) -> SimpleNamespace:
    return SimpleNamespace(
        max_position_size_usd=max_position_size_usd,
        apex_size_cap_pct_of_equity=apex_size_cap_pct_of_equity,
        apex_size_conviction_floor=apex_size_conviction_floor,
        max_leverage=max_leverage,
        min_tp_pct=min_tp_pct,
    )


def _build_optimizer(
    *, settings, capital_getter=None,
) -> TradeOptimizer:
    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=settings)
    if capital_getter is not None:
        opt.attach_account_state_getter(capital_getter)
    return opt


def _trade(
    *,
    symbol: str = "BTCUSDT",
    position_size_usd: float = 12_000.0,
    confidence: float = 0.75,
) -> OptimizedTrade:
    return OptimizedTrade(
        symbol=symbol,
        direction="Buy",
        sl_pct=1.0,
        tp_pct=2.0,
        tp_mode="fixed",
        position_size_usd=position_size_usd,
        leverage=2,
        entry_timing="immediate",
        add_on_pullback=False,
        confidence=confidence,
    )


# --- Legacy / disabled path ----------------------------------------


def test_legacy_default_static_cap_when_pct_zero(loguru_sink) -> None:
    """Default ``apex_size_cap_pct_of_equity=0.0`` preserves byte-
    equivalent legacy semantics: pre_cap > static_cap → clamp to
    static_cap, then scale by conviction."""
    s = _build_settings()
    opt = _build_optimizer(settings=s)
    t = _trade(position_size_usd=18_000.0, confidence=0.75)

    opt._apply_constraints(t)

    # static_cap = 1200, conviction = 0.75 → 1200 * 0.75 = 900
    assert t.position_size_usd == pytest.approx(900.0)
    decisions = _records_with_tag(loguru_sink, "APEX_SIZING_DECISION")
    assert len(decisions) == 1
    kv = _parse_kv(decisions[0][1])
    assert kv["effective_cap"] == "$1200"
    assert kv["cap_hit"] == "True"


def test_no_getter_falls_back_to_static_cap(loguru_sink) -> None:
    """Even with pct_of_equity > 0, if the getter is unwired the
    optimizer falls back to the static cap."""
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s)
    t = _trade(position_size_usd=18_000.0, confidence=1.0)

    opt._apply_constraints(t)

    # Capital unknown → static cap = 1200 → conviction 1.0 → 1200
    assert t.position_size_usd == pytest.approx(1200.0)


def test_getter_returning_none_falls_back(loguru_sink) -> None:
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: None)
    t = _trade(position_size_usd=18_000.0, confidence=1.0)

    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(1200.0)


def test_getter_returning_zero_falls_back(loguru_sink) -> None:
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: 0.0)
    t = _trade(position_size_usd=18_000.0, confidence=1.0)
    opt._apply_constraints(t)
    assert t.position_size_usd == pytest.approx(1200.0)


# --- Dynamic cap path ----------------------------------------------


def test_dynamic_cap_uses_pct_of_capital(loguru_sink) -> None:
    """With pct_of_equity=10 and capital=$24k, the cap is max($1200,
    $2400) = $2400. Conviction 1.0 → final $2400."""
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)
    t = _trade(position_size_usd=18_000.0, confidence=1.0)

    opt._apply_constraints(t)

    assert t.position_size_usd == pytest.approx(2400.0)
    decisions = _records_with_tag(loguru_sink, "APEX_SIZING_DECISION")
    kv = _parse_kv(decisions[0][1])
    assert kv["effective_cap"] == "$2400"
    assert kv["capital_used"] == "$24000"


def test_dynamic_cap_below_static_floor_falls_back(loguru_sink) -> None:
    """Tiny account: 10% of $5k = $500, below the $1200 static floor.
    The static floor wins so a freshly-funded small account does not
    trade pathologically tiny positions."""
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: 5_000.0)
    t = _trade(position_size_usd=12_000.0, confidence=1.0)

    opt._apply_constraints(t)

    # max($1200, $500) = $1200; conviction 1.0 → $1200
    assert t.position_size_usd == pytest.approx(1200.0)


# --- Conviction scaling ---------------------------------------------


def test_conviction_scales_within_cap(loguru_sink) -> None:
    """Same cap, three convictions → three distinct final sizes —
    the audit-required differentiation."""
    s = _build_settings(
        apex_size_cap_pct_of_equity=10.0,
        apex_size_conviction_floor=0.5,
    )
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)

    sizes = []
    for conf in (0.65, 0.75, 0.85):
        t = _trade(position_size_usd=18_000.0, confidence=conf)
        opt._apply_constraints(t)
        sizes.append(t.position_size_usd)

    # cap is $2400; conviction 0.65 → $1560, 0.75 → $1800, 0.85 → $2040
    assert sizes[0] == pytest.approx(2400.0 * 0.65)
    assert sizes[1] == pytest.approx(2400.0 * 0.75)
    assert sizes[2] == pytest.approx(2400.0 * 0.85)
    # Each is strictly larger than the last — meaningful differentiation
    assert sizes[0] < sizes[1] < sizes[2]


def test_conviction_floor_prevents_pathological_shrink(loguru_sink) -> None:
    """A 0.0 conviction would normally produce a 0-sized trade; the
    floor keeps the size at floor * cap."""
    s = _build_settings(
        apex_size_cap_pct_of_equity=10.0,
        apex_size_conviction_floor=0.5,
    )
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)
    t = _trade(position_size_usd=18_000.0, confidence=0.0)

    opt._apply_constraints(t)

    # cap $2400, conviction 0.0 < floor 0.5 → scale = 0.5 → $1200
    assert t.position_size_usd == pytest.approx(1200.0)


def test_pre_cap_below_cap_is_preserved(loguru_sink) -> None:
    """When brain proposes less than the cap, the cap does NOT inflate
    the size up — only conviction scales it down."""
    s = _build_settings(
        apex_size_cap_pct_of_equity=10.0, apex_size_conviction_floor=0.5,
    )
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)
    t = _trade(position_size_usd=500.0, confidence=1.0)

    opt._apply_constraints(t)

    # pre_cap=500 < cap=$2400; final = 500 * 1.0 = 500
    assert t.position_size_usd == pytest.approx(500.0)


def test_small_size_preserved_not_floored(loguru_sink) -> None:
    """H3 (2026-05-30, IMPLEMENT_NEUTRALITY_AND_EXIT_SYSTEM_FIX): a tiny
    conviction-scaled size is PRESERVED, not floored up to an arbitrary $100.
    The neutrality fix removed the floor so the brain's risk-based size stands;
    a size genuinely below the EXCHANGE minimum is skipped downstream (qty<=0
    -> TRADE_SKIP rsn=qty_zero), never oversized into the weakest setups. This
    supersedes the earlier J5 'floor holds at $100' rule by operator decision."""
    s = _build_settings(apex_size_conviction_floor=0.5)
    opt = _build_optimizer(settings=s)
    t = _trade(position_size_usd=50.0, confidence=1.0)

    opt._apply_constraints(t)
    # pre_cap=50 < cap=1200; conviction 1.0 -> scaled 50; preserved (no $100 floor)
    assert t.position_size_usd == pytest.approx(50.0)


# --- Observability events -------------------------------------------


def test_cap_hit_event_fires_when_brain_exceeds_cap(loguru_sink) -> None:
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)
    t = _trade(position_size_usd=18_000.0, confidence=1.0)

    opt._apply_constraints(t)
    hits = _records_with_tag(loguru_sink, "APEX_SIZING_CAP_HIT")
    assert len(hits) == 1


def test_cap_hit_event_silent_when_under_cap(loguru_sink) -> None:
    s = _build_settings(apex_size_cap_pct_of_equity=10.0)
    opt = _build_optimizer(settings=s, capital_getter=lambda: 24_000.0)
    t = _trade(position_size_usd=500.0, confidence=1.0)

    opt._apply_constraints(t)
    hits = _records_with_tag(loguru_sink, "APEX_SIZING_CAP_HIT")
    assert hits == []


# --- Source pin ------------------------------------------------------


def test_settings_has_new_fields() -> None:
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/config/settings.py", encoding="utf-8",
    ).read()
    assert "apex_size_cap_pct_of_equity: float = 0.0" in src
    assert "apex_size_conviction_floor: float = 0.5" in src


def test_optimizer_has_attach_method() -> None:
    src = open(
        "/home/inshadaliqbal786/trading-intelligence-mcp/"
        "src/apex/optimizer.py", encoding="utf-8",
    ).read()
    assert "def attach_account_state_getter" in src
    assert "APEX_SIZING_DECISION" in src
    assert "APEX_SIZING_CAP_HIT" in src
    assert "APEX_SIZING_SMALL_SIZE" in src  # H3 (2026-05-30): floor removed; token renamed
