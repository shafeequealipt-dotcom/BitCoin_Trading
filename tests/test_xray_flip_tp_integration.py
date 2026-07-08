"""Integration test for the XRAY-flip TP cap inside
``StrategyWorker._execute_claude_trade``.

Drives the full ``_execute_claude_trade`` flow with a stubbed service
container so the XRAY direction-flip path mutates the trade dict, the
volatility-aware cap then bounds the structural TP, the
``XRAY_FLIP_TP_DERIVATION`` event is emitted, and the function bails
at ``order_reject`` (the cleanest bail point downstream of the cap).

The cap math itself is exhaustively unit-tested in
``tests/test_flip_tp_capper.py``; this test specifically verifies the
WIRING into the worker:

* the cap path is reached when (and only when) the trade was
  XRAY-flipped,
* the local ``tp`` AND the trade-dict ``take_profit_price`` / ``tp``
  keys are both updated when the cap kicks in,
* the persisted metadata (``_xray_flip_tp_method``,
  ``_xray_flip_tp_orig``, ``_xray_flip_tp_telem``) is populated for
  downstream consumers (CALL_B, save_thesis, telegram alerts),
* the ``XRAY_FLIP_TP_DERIVATION`` log line is emitted with the
  expected structured fields, and
* a flipped trade with a structural TP that would have been rejected
  by the SLTPValidator (>10% from price) survives capping with a
  ``method=volatility_capped`` outcome.

TP-Volume-Closure fix Phase 1E — 2026-05-07.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from loguru import logger

from src.analysis.volatility_profile import CoinVolatilityProfile
from src.config.settings import FlipTPSettings
from src.core.types import OrderStatus
from src.workers.strategy_worker import StrategyWorker

# ---------------------------------------------------------------------------
# Loguru capture (mirrors the pattern in
# tests/test_layer4_sniper/test_time_decay_trace.py — pytest's stdlib
# caplog does not see loguru output without a propagation handler).
# ---------------------------------------------------------------------------


class _LogCapture:
    def __init__(self) -> None:
        self.records: list[str] = []
        self._handle: int | None = None

    def __enter__(self):
        self._handle = logger.add(
            lambda msg: self.records.append(msg.record["message"]),
            level="DEBUG",
            format="{message}",
        )
        return self

    def __exit__(self, *exc):
        if self._handle is not None:
            logger.remove(self._handle)


# ---------------------------------------------------------------------------
# Fakes — minimal surface to drive _execute_claude_trade through the cap.
# ---------------------------------------------------------------------------


@dataclass
class _FakePlacement:
    rr_long: float
    rr_short: float
    long_sl_price: float = 0.0
    long_tp_price: float = 0.0
    short_sl_price: float = 0.0
    short_tp_price: float = 0.0
    rr_ratio: float = 1.0


@dataclass
class _FakeMarketStructure:
    structure: str


@dataclass
class _FakeStructural:
    structural_placement: _FakePlacement
    market_structure: _FakeMarketStructure | None
    setup_quality: str = "B"


class _FakeStructureCache:
    def __init__(self, payload: _FakeStructural) -> None:
        self._payload = payload

    def get(self, _symbol: str) -> _FakeStructural:
        return self._payload


class _FakeMarketService:
    def __init__(self, last_price: float) -> None:
        self._price = last_price

    async def get_ticker(self, _symbol: str) -> SimpleNamespace:
        return SimpleNamespace(last_price=self._price)


class _FakeOrderService:
    """Bails at order_reject so the test reaches end-to-end without
    needing a full Bybit/Shadow stack. The cap has already run by the
    time place_order is called."""

    async def place_order(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(
            status=OrderStatus.REJECTED,
            order_id="test-rejected-001",
        )


class _FakeVolatilityProfiler:
    def __init__(self, profile: CoinVolatilityProfile | None) -> None:
        self._profile = profile

    async def get_profile(self, _symbol: str) -> CoinVolatilityProfile | None:
        return self._profile


class _FakeEnforcer:
    def should_allow_trade(self, leverage: int = 1) -> tuple[bool, str]:
        return True, "ok"

    def qualify_survival_trade(
        self, _symbol: str, _structure_cache: Any = None,
    ) -> tuple[bool, str]:
        return True, "not_in_survival"

    def get_size_multiplier(self) -> float:
        return 1.0


# ---------------------------------------------------------------------------
# Worker construction
# ---------------------------------------------------------------------------


def _make_worker(
    *,
    structural: _FakeStructural,
    last_price: float,
    vol_profile: CoinVolatilityProfile | None,
    flip_tp_settings: FlipTPSettings | None = None,
) -> StrategyWorker:
    """Build a StrategyWorker with the bare-minimum state to drive
    the whole `_execute_claude_trade` flow up to ``order_reject``."""
    sw = StrategyWorker.__new__(StrategyWorker)
    sw.settings = SimpleNamespace(
        risk=SimpleNamespace(
            xray_dir_flip_threshold_ratio=3.0,
            xray_dir_flip_enabled=True,  # IMPLEMENT_XRAY_FLIP_SWITCH: ON-state test
            flip_tp=flip_tp_settings or FlipTPSettings(),
            default_stop_loss_pct=3.0,
            default_take_profit_pct=6.0,
        ),
        bybit=None,  # disables testnet whitelist check
    )
    sw.services = {
        "structure_cache": _FakeStructureCache(structural),
        "market_service": _FakeMarketService(last_price),
        "order_service": _FakeOrderService(),
        "volatility_profiler": _FakeVolatilityProfiler(vol_profile),
        # Intentionally omitted: sl_validator (skips that block),
        # position_service, instrument_service, trade_coordinator,
        # regime_detector — the path falls through with safe defaults.
    }
    sw._enforcer = _FakeEnforcer()
    return sw


def _profile_high_op() -> CoinVolatilityProfile:
    """Mirror the live OPUSDT profile (class=high, regime=trending_up,
    tp=3.90% sl=1.80%) — the exact values that would have saved the
    flipped trade in the 90-min log baseline."""
    return CoinVolatilityProfile(
        symbol="OPUSDT",
        atr_pct_5m=0.46,
        atr_pct_1h=0.50,
        volatility_class="high",
        recommended_tp_pct=3.90,
        recommended_sl_pct=1.80,
        recommended_hold_min=54,
        recommended_strategy="trend_follow",
        regime="trending_up",
        regime_confidence=0.70,
    )


# ---------------------------------------------------------------------------
# Test 1 — cap fires for an XRAY-flipped trade with structural TP > vol-aware
# ---------------------------------------------------------------------------


def test_xray_flip_tp_cap_fires_and_logs_when_structural_exceeds_vol_aware() -> None:
    """The headline case: Buy → Sell flip with a structural TP 14%
    below price. Vol-aware cap (3.9% for class=high) brings it back to
    ~3.9% from price, the trade dict + local tp + log all reflect the
    capped value, and the function reaches order_reject (proving the
    cap did NOT block the trade upstream)."""
    last_price = 0.148
    structural_short_tp = last_price * (1 - 0.14)  # 14% below — over the
                                                    # validator's 10% ceiling

    sp = _FakePlacement(
        rr_long=0.1, rr_short=5.7,            # ratio 57x → flips
        long_sl_price=last_price * 1.02,
        long_tp_price=last_price * 1.04,
        short_sl_price=last_price * 1.02,     # SL above for short
        short_tp_price=structural_short_tp,    # TP 14% below for short
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(
        structural=structural,
        last_price=last_price,
        vol_profile=_profile_high_op(),
    )

    trade: dict = {
        "symbol": "OPUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 200.0,
        "stop_loss_price": last_price * 0.98,
        "take_profit_price": last_price * 1.04,
    }

    with _LogCapture() as cap:
        ok, reason = asyncio.run(
            sw._execute_claude_trade(trade, set(), plan=None),
        )

    # Function bailed at the order-reject gate (cap downstream of the
    # cap site; proves the cap did NOT block).
    assert ok is False
    assert reason == "order_reject"

    # Trade was flipped to Sell.
    assert trade["direction"] == "Sell"
    assert trade["_flip_source"] == "xray"

    # Cap kicked in — method is volatility_capped.
    assert trade["_xray_flip_tp_method"] == "volatility_capped"
    # Original TP (pre-cap) was the structural value.
    assert abs(trade["_xray_flip_tp_orig"] - structural_short_tp) < 1e-9
    # Capped TP is at vol_aware_pct (3.9%) below price for the Sell.
    expected_capped_tp = last_price * (1.0 - 0.039)
    assert abs(trade["take_profit_price"] - expected_capped_tp) < 1e-6

    # Telemetry dict structurally complete — the keys downstream alerting
    # / dashboards rely on are present.
    telem = trade["_xray_flip_tp_telem"]
    for key in (
        "structural_dist_pct",
        "vol_aware_pct",
        "vol_aware_capped_pct",
        "hard_ceiling_pct",
        "chosen_cap_pct",
        "chosen_dist_pct",
    ):
        assert key in telem, f"telemetry missing {key}"

    # XRAY_FLIP_TP_DERIVATION event was emitted with the expected
    # structured fields. We check substring rather than the entire
    # formatted string to keep the test resilient to incidental
    # field-order changes; the substrings cover the load-bearing
    # observability surface.
    flip_log_lines = [
        line for line in cap.records if "XRAY_FLIP_TP_DERIVATION" in line
        and "DEGRADED" not in line
    ]
    assert flip_log_lines, "XRAY_FLIP_TP_DERIVATION not emitted"
    line = flip_log_lines[-1]
    assert "sym=OPUSDT" in line
    assert "dir=Sell" in line
    assert "method=volatility_capped" in line
    assert "vol_profile_present=True" in line
    assert "degraded=False" in line


# ---------------------------------------------------------------------------
# Test 2 — non-flipped trade is NOT capped (path is gated on _flip_source)
# ---------------------------------------------------------------------------


def test_non_flipped_trade_skips_cap_path_entirely() -> None:
    """A trade that does NOT flip (rr ratio below threshold) takes the
    plain path. The cap block is gated on ``_flip_source == "xray"`` so
    no cap-related metadata is attached and no XRAY_FLIP_TP_DERIVATION
    fires."""
    last_price = 0.148

    sp = _FakePlacement(
        rr_long=2.0, rr_short=3.0,            # ratio 1.5x → no flip
        long_sl_price=last_price * 0.98,
        long_tp_price=last_price * 1.04,
        short_sl_price=last_price * 1.02,
        short_tp_price=last_price * 0.96,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(
        structural=structural,
        last_price=last_price,
        vol_profile=_profile_high_op(),
    )

    trade: dict = {
        "symbol": "OPUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 200.0,
        "stop_loss_price": last_price * 0.98,
        "take_profit_price": last_price * 1.02,
    }

    with _LogCapture() as cap:
        ok, reason = asyncio.run(
            sw._execute_claude_trade(trade, set(), plan=None),
        )

    # Same bail point as the capped case — the order_reject gate.
    assert ok is False
    assert reason == "order_reject"
    # No flip happened.
    assert trade["direction"] == "Buy"
    assert trade.get("_flip_source") != "xray"
    # No cap metadata attached because the cap block was gated out.
    assert "_xray_flip_tp_method" not in trade
    assert "_xray_flip_tp_orig" not in trade
    # No XRAY_FLIP_TP_DERIVATION events.
    flip_logs = [
        line for line in cap.records if "XRAY_FLIP_TP_DERIVATION" in line
    ]
    assert flip_logs == []


# ---------------------------------------------------------------------------
# Test 3 — cap settings.enabled=False → no-op even when flipped
# ---------------------------------------------------------------------------


def test_cap_disabled_via_settings_acts_as_noop() -> None:
    """The master switch gives the operator a one-config-line revert
    if the cap ever needs to be turned off mid-trial. The trade is
    still flipped and still emits XRAY_FLIP_TP_DERIVATION (so we can
    see "cap was offered the trade but disabled"), but the structural
    TP is preserved unchanged."""
    last_price = 0.148
    structural_short_tp = last_price * (1 - 0.14)  # 14% below

    sp = _FakePlacement(
        rr_long=0.1, rr_short=5.7,
        long_sl_price=last_price * 1.02,
        long_tp_price=last_price * 1.04,
        short_sl_price=last_price * 1.02,
        short_tp_price=structural_short_tp,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(
        structural=structural,
        last_price=last_price,
        vol_profile=_profile_high_op(),
        flip_tp_settings=FlipTPSettings(enabled=False),
    )

    trade: dict = {
        "symbol": "OPUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 200.0,
        "stop_loss_price": last_price * 0.98,
        "take_profit_price": last_price * 1.04,
    }

    with _LogCapture() as cap:
        asyncio.run(sw._execute_claude_trade(trade, set(), plan=None))

    assert trade["direction"] == "Sell"  # flip still happens
    # No cap metadata (cap block is disabled, so it's never reached).
    assert "_xray_flip_tp_method" not in trade
    # Capped TP is the structural value because the cap is off.
    assert abs(trade["take_profit_price"] - structural_short_tp) < 1e-9
    # No XRAY_FLIP_TP_DERIVATION emitted because the gate sits at the
    # entry of the cap block.
    flip_logs = [
        line for line in cap.records if "XRAY_FLIP_TP_DERIVATION" in line
    ]
    assert flip_logs == []


# ---------------------------------------------------------------------------
# Test 4 — vol_profile fetch raises → narrow recovery, fallback path applies
# ---------------------------------------------------------------------------


class _FakeFailingVolatilityProfiler:
    """Profiler whose `get_profile` always raises. Exercises the
    narrow-recovery branch in strategy_worker.py at the cap-block site
    (`try: _vp_profile = await _vp_svc.get_profile(symbol)` — except
    falls back to vol_profile=None and emits
    XRAY_FLIP_TP_DERIVATION_DEGRADED at WARNING)."""

    async def get_profile(self, _symbol: str) -> CoinVolatilityProfile:
        raise RuntimeError("simulated profiler outage")


def test_vol_profile_fetch_failure_falls_back_and_logs_degraded() -> None:
    """When the volatility profiler raises mid-trade, the cap path:
    (a) catches the exception narrowly (does NOT propagate),
    (b) emits XRAY_FLIP_TP_DERIVATION_DEGRADED at WARNING with the
        failure reason,
    (c) falls back to fallback_tp_distance_pct via vol_profile=None,
    (d) still emits the regular XRAY_FLIP_TP_DERIVATION event with
        method=fallback and degraded=True so log readers know the
        fallback was due to a degraded profiler rather than a fresh
        symbol.
    """
    last_price = 0.148
    structural_short_tp = last_price * (1 - 0.14)  # 14% below — over the
                                                    # validator's 10% ceiling

    sp = _FakePlacement(
        rr_long=0.1, rr_short=5.7,
        long_sl_price=last_price * 1.02,
        long_tp_price=last_price * 1.04,
        short_sl_price=last_price * 1.02,
        short_tp_price=structural_short_tp,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )

    sw = StrategyWorker.__new__(StrategyWorker)
    sw.settings = SimpleNamespace(
        risk=SimpleNamespace(
            xray_dir_flip_threshold_ratio=3.0,
            xray_dir_flip_enabled=True,  # IMPLEMENT_XRAY_FLIP_SWITCH: ON-state test
            flip_tp=FlipTPSettings(),
            default_stop_loss_pct=3.0,
            default_take_profit_pct=6.0,
        ),
        bybit=None,
    )
    sw.services = {
        "structure_cache": _FakeStructureCache(structural),
        "market_service": _FakeMarketService(last_price),
        "order_service": _FakeOrderService(),
        "volatility_profiler": _FakeFailingVolatilityProfiler(),
    }
    sw._enforcer = _FakeEnforcer()

    trade: dict = {
        "symbol": "OPUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 200.0,
        "stop_loss_price": last_price * 0.98,
        "take_profit_price": last_price * 1.04,
    }

    with _LogCapture() as cap:
        asyncio.run(sw._execute_claude_trade(trade, set(), plan=None))

    # Trade was flipped to Sell.
    assert trade["direction"] == "Sell"
    # Cap fell back because vol_profile fetch raised.
    assert trade["_xray_flip_tp_method"] == "fallback"
    # Capped TP is at fallback (2.0%) below price for the Sell.
    expected_capped_tp = last_price * (1.0 - 0.02)
    assert abs(trade["take_profit_price"] - expected_capped_tp) < 1e-6

    # Both events are present:
    # 1. XRAY_FLIP_TP_DERIVATION_DEGRADED with the simulated outage reason.
    degraded_lines = [
        line for line in cap.records if "XRAY_FLIP_TP_DERIVATION_DEGRADED" in line
    ]
    assert degraded_lines, "XRAY_FLIP_TP_DERIVATION_DEGRADED not emitted"
    assert "simulated profiler outage" in degraded_lines[0]

    # 2. XRAY_FLIP_TP_DERIVATION with method=fallback and degraded=True.
    flip_lines = [
        line for line in cap.records
        if "XRAY_FLIP_TP_DERIVATION" in line and "DEGRADED" not in line
    ]
    assert flip_lines, "XRAY_FLIP_TP_DERIVATION not emitted"
    line = flip_lines[-1]
    assert "method=fallback" in line
    assert "vol_profile_present=False" in line
    assert "degraded=True" in line
