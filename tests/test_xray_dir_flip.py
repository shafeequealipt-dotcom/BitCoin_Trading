"""Phase 1 of dir-block-fix (2026-05-05) — XRAY direction recheck → flip.

Surgical smoke tests for the new flip path inside
``StrategyWorker._execute_claude_trade``. The flip block sits between
the X-RAY conflict check and the testnet/dup-position checks; we drive
the function to the dup_position bail-out so we can assert on the
mutated trade dict and the function's return contract.

Each test constructs the minimum state required to reach the flip block:
* a StrategyWorker instance via ``__new__`` (no full DI wiring),
* a settings shim exposing ``risk.xray_dir_flip_threshold_ratio`` and a
  null ``bybit`` attribute (skips the testnet whitelist check),
* a structure_cache stub returning a hand-built ``StructuralAnalysis`` /
  ``StructuralPlacement`` / ``MarketStructure`` triplet,
* an enforcer stub that always allows the trade,
* ``position_symbols`` pre-populated with the test symbol so the
  function returns ``(False, "dup_position")`` once the flip has run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from src.workers.strategy_worker import StrategyWorker

# ─── Fakes for the structure_cache payload ──────────────────────────────


@dataclass
class _FakePlacement:
    rr_long: float
    rr_short: float
    long_sl_price: float = 0.0
    long_tp_price: float = 0.0
    short_sl_price: float = 0.0
    short_tp_price: float = 0.0
    rr_ratio: float = 1.0  # any non-zero value to bypass the SKIP+rr<0.5 gate


@dataclass
class _FakeMarketStructure:
    structure: str  # "uptrend" / "downtrend" / "ranging" / etc.


@dataclass
class _FakeStructural:
    structural_placement: _FakePlacement
    market_structure: _FakeMarketStructure | None
    setup_quality: str = "B"
    trade_direction: str = ""  # "long" / "short" / "" — drives veto path


class _FakeStructureCache:
    def __init__(self, payload: _FakeStructural) -> None:
        self._payload = payload

    def get(self, _symbol: str) -> _FakeStructural:
        return self._payload


class _FakeEnforcer:
    def should_allow_trade(self, leverage: int = 1) -> tuple[bool, str]:
        return True, "ok"

    def qualify_survival_trade(
        self, _symbol: str, _structure_cache: Any = None,
    ) -> tuple[bool, str]:
        return True, "not_in_survival"


def _make_worker(
    structural: _FakeStructural,
    flip_threshold: float = 3.0,
    flip_enabled: bool = True,
    high_conviction_protection: bool = True,
    regime_detector: Any = None,
    suppression_enabled: bool = True,
) -> StrategyWorker:
    """Construct a StrategyWorker with the bare-minimum state to drive
    the X-RAY direction-flip path inside ``_execute_claude_trade``.

    ``flip_enabled`` maps to the IMPLEMENT_XRAY_FLIP_SWITCH (2026-05-25)
    config key ``risk.xray_dir_flip_enabled``. It defaults to True so the
    pre-switch flip tests below exercise the ON-state unchanged; the
    OFF-state tests pass flip_enabled=False. ``regime_detector`` and the
    structural ``trade_direction`` drive the high-conviction veto path.

    ``suppression_enabled`` maps to the IMPLEMENT_XRAY_SUPPRESS_SWITCH
    (2026-05-25) config key ``risk.xray_trade_suppression_enabled``. It
    defaults to True so every existing block test keeps its enforce-path
    behavior (X-RAY skips the trade); the booklog tests pass
    suppression_enabled=False and assert X-RAY records the would-be block
    (``trade["_xray_suppression_booklog"]``) without skipping.
    """
    sw = StrategyWorker.__new__(StrategyWorker)
    # Settings shim — only the attrs the flip path reads.
    sw.settings = SimpleNamespace(
        risk=SimpleNamespace(
            xray_dir_flip_threshold_ratio=flip_threshold,
            xray_dir_flip_enabled=flip_enabled,
            xray_high_conviction_protection_enabled=high_conviction_protection,
            xray_trade_suppression_enabled=suppression_enabled,
        ),
        bybit=None,  # disables testnet whitelist check at line 1579
    )
    sw.services = {
        "structure_cache": _FakeStructureCache(structural),
        # Intentionally omit layer_manager/market_service/order_service —
        # the flip block doesn't need them and the dup-position bail-out
        # short-circuits before any of the downstream services are read.
    }
    sw._enforcer = _FakeEnforcer()
    if regime_detector is not None:
        sw.regime_detector = regime_detector
    return sw


# ─── Tests ─────────────────────────────────────────────────────────────


def test_flip_buy_to_sell_on_high_ratio() -> None:
    """ratio = rr_short / rr_long = 2.4 / 0.4 = 6.0 (> 3.0 default) →
    flip Buy → Sell. Trade dict is mutated; function bails at
    ``dup_position`` after the flip. Assert on the mutated state.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",  # avoids the X-RAY conflict block
    )
    sw = _make_worker(structural)
    trade: dict = {
        "symbol": "TESTBUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 600.0,
        "stop_loss_price": 99.5,
        "take_profit_price": 105.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    # Function bails at dup_position after the flip already mutated state.
    assert ok is False
    assert reason == "dup_position"
    # Direction was flipped.
    assert trade["direction"] == "Sell"
    # SL/TP swapped to the short levels.
    assert trade["stop_loss_price"] == 101.0
    assert trade["take_profit_price"] == 90.0
    # Flip metadata recorded for downstream consumers.
    assert trade["_apex_was_flipped"] is True
    assert trade["_apex_original_direction"] == "Buy"
    assert trade["_flip_source"] == "xray"
    assert trade["_xray_flip_ratio"] == pytest.approx(6.0)


def test_flip_blocked_when_dual_levels_missing() -> None:
    """ratio is high enough to flip, but the structural payload is
    missing the opposite-direction SL/TP. The function falls back to
    XRAY_DIR_BLOCK with reason ``missing_dual_structural_levels``.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        # short_sl_price and short_tp_price intentionally left at 0.0
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(structural)
    trade: dict = {
        "symbol": "TESTBUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 600.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, set(), plan=None),
    )

    assert ok is False
    assert reason == "xray_dir_block"
    # Direction must NOT have been mutated since we fell back to BLOCK.
    assert trade["direction"] == "Buy"
    assert "_apex_was_flipped" not in trade
    assert "_flip_source" not in trade


def test_no_flip_below_threshold() -> None:
    """ratio = 2.0 / 1.0 = 2.0 (< 3.0 default) → no flip. Trade dict
    keeps the original direction; the function continues past the
    direction-recheck block and bails at dup_position.
    """
    sp = _FakePlacement(
        rr_long=1.0, rr_short=2.0,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(structural)
    trade: dict = {
        "symbol": "TESTBUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 600.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    # Direction unchanged.
    assert trade["direction"] == "Buy"
    assert "_apex_was_flipped" not in trade
    assert "_flip_source" not in trade


def test_no_flip_when_switch_off() -> None:
    """IMPLEMENT_XRAY_FLIP_SWITCH (2026-05-25). Same high-ratio setup as
    test_flip_buy_to_sell_on_high_ratio (ratio 6.0 > 3.0) but with the
    switch OFF. X-RAY must NOT reverse the direction: the sanctioned Buy
    proceeds unchanged, the suppression marker is set, no flip metadata is
    attached, and the function falls through to the dup_position bail.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(structural, flip_enabled=False)
    trade: dict = {
        "symbol": "TESTBUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 600.0,
        "stop_loss_price": 99.5,
        "take_profit_price": 105.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    # Direction was NOT flipped — sanctioned Buy stands.
    assert trade["direction"] == "Buy"
    # SL/TP untouched (still the brain/APEX values, not the short levels).
    assert trade["stop_loss_price"] == 99.5
    assert trade["take_profit_price"] == 105.0
    # Switch-off marker set; no flip metadata attached.
    assert trade["_xray_flip_disabled_by_switch"] is True
    assert "_apex_was_flipped" not in trade
    assert "_flip_source" not in trade


def test_high_conviction_veto_preserved_when_switch_off() -> None:
    """IMPLEMENT_XRAY_FLIP_SWITCH (2026-05-25). The switch gates the
    reversal ONLY. With the switch OFF but a HIGH-conviction directive
    (per-coin regime trending_up + Buy AND structural trade_direction
    'long' + Buy) and a structural disagreement (ratio 6.0 > 3.0), the
    pre-existing high-conviction veto still fires: the trade is skipped,
    not reversed. Proves the veto is independent of the flip switch.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="uptrend"),
        setup_quality="A",
        trade_direction="long",
    )
    regime_detector = SimpleNamespace(
        _per_coin_regimes={
            "TESTBUSDT": SimpleNamespace(
                regime=SimpleNamespace(value="trending_up"),
            ),
        },
    )
    sw = _make_worker(
        structural,
        flip_enabled=False,
        high_conviction_protection=True,
        regime_detector=regime_detector,
    )
    trade: dict = {
        "symbol": "TESTBUSDT",
        "direction": "Buy",
        "leverage": 3,
        "size_usd": 600.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, set(), plan=None),
    )

    assert ok is False
    assert reason == "xray_veto_high_conviction"
    # Veto skips the trade; direction is never reversed.
    assert trade["direction"] == "Buy"
    assert "_flip_source" not in trade


# ─── IMPLEMENT_XRAY_SUPPRESS_SWITCH (2026-05-25) ────────────────────────
# The suppression switch gates ALL five X-RAY trade-blocks. When OFF
# (operator default) each would-be block is booklogged (the trade dict
# gets ``_xray_suppression_booklog``) and the brain's direction proceeds —
# the function then bails at dup_position. When ON, the original block and
# reason code are byte-identical to pre-switch behavior.


def test_xray_skip_blocks_when_suppression_on() -> None:
    """ON-state (unchanged): setup_quality=SKIP + rr_ratio<0.5 →
    X-RAY skips with reason ``xray_skip``; no booklog marker.
    """
    sp = _FakePlacement(rr_long=0.0, rr_short=0.0, rr_ratio=0.4)
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="SKIP",
    )
    sw = _make_worker(structural, suppression_enabled=True)
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Buy"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, set(), plan=None),
    )

    assert ok is False
    assert reason == "xray_skip"
    assert "_xray_suppression_booklog" not in trade


def test_xray_skip_booklog_when_suppression_off() -> None:
    """OFF-state: same structurally-invalid setup, but suppression
    disabled — X-RAY does NOT skip; it booklogs and the brain's Buy
    proceeds (function bails later at dup_position).
    """
    sp = _FakePlacement(rr_long=0.0, rr_short=0.0, rr_ratio=0.4)
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="SKIP",
    )
    sw = _make_worker(structural, suppression_enabled=False)
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Buy"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"          # proceeded past X-RAY
    assert reason != "xray_skip"
    assert trade["direction"] == "Buy"        # direction untouched
    assert trade["_xray_suppression_booklog"] is True


def test_xray_conflict_blocks_when_suppression_on() -> None:
    """ON-state (unchanged): Sell into an uptrend with weak quality C →
    reason ``xray_conflict``; no booklog marker.
    """
    sp = _FakePlacement(rr_long=0.0, rr_short=0.0, rr_ratio=1.0)
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="uptrend"),
        setup_quality="C",
    )
    sw = _make_worker(structural, suppression_enabled=True)
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Sell"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, set(), plan=None),
    )

    assert ok is False
    assert reason == "xray_conflict"
    assert "_xray_suppression_booklog" not in trade


def test_xray_conflict_booklog_when_suppression_off() -> None:
    """OFF-state: same conflict, suppression disabled — no skip, booklog
    marker set, Sell proceeds to dup_position.
    """
    sp = _FakePlacement(rr_long=0.0, rr_short=0.0, rr_ratio=1.0)
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="uptrend"),
        setup_quality="C",
    )
    sw = _make_worker(structural, suppression_enabled=False)
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Sell"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    assert reason != "xray_conflict"
    assert trade["direction"] == "Sell"
    assert trade["_xray_suppression_booklog"] is True


def test_xray_veto_booklog_when_suppression_off() -> None:
    """OFF-state: high-conviction disagreement (ratio 6.0, trending_up +
    Buy, trade_direction long) that today fires
    ``xray_veto_high_conviction``. With suppression disabled the veto is
    booklogged and the brain's Buy proceeds — proving the suppression
    switch gates the veto too. Flip switch stays OFF; no reversal occurs.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="uptrend"),
        setup_quality="A",
        trade_direction="long",
    )
    regime_detector = SimpleNamespace(
        _per_coin_regimes={
            "TESTBUSDT": SimpleNamespace(
                regime=SimpleNamespace(value="trending_up"),
            ),
        },
    )
    sw = _make_worker(
        structural,
        flip_enabled=False,
        high_conviction_protection=True,
        regime_detector=regime_detector,
        suppression_enabled=False,
    )
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Buy"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    assert reason != "xray_veto_high_conviction"
    assert trade["direction"] == "Buy"        # not reversed, not skipped
    assert trade["_xray_suppression_booklog"] is True
    assert "_flip_source" not in trade


def test_xray_dir_block_keeps_brain_dir_when_suppression_off() -> None:
    """OFF-state flip path: flip switch ON + high ratio + missing dual
    levels (today → ``xray_dir_block``). With suppression disabled the
    flip cannot be applied safely, so X-RAY booklogs and keeps the brain's
    Buy — no skip, no flip.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        # short_sl_price / short_tp_price left 0.0 → missing dual levels
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    sw = _make_worker(
        structural, flip_enabled=True, suppression_enabled=False,
    )
    trade: dict = {
        "symbol": "TESTBUSDT", "direction": "Buy",
        "stop_loss_price": 99.5, "take_profit_price": 105.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    assert reason != "xray_dir_block"
    assert trade["direction"] == "Buy"         # brain dir kept
    assert trade["_xray_suppression_booklog"] is True
    assert "_apex_was_flipped" not in trade     # flip NOT applied
    assert "_flip_source" not in trade


def test_hc_veto_no_flip_when_suppression_off_and_flip_on() -> None:
    """IMPLEMENT_XRAY_SUPPRESS_SWITCH cross-switch edge case (finding 5a):
    suppression OFF + flip ON + HIGH-conviction disagreement (ratio 6.0,
    trending_up + Buy, trade_direction long). The booklogged veto must NOT
    be converted into a reversal — a high-conviction directive holds the
    brain's Buy and never enters the low-conviction flip path. Decision is
    recorded as reason=high_conviction_booklog_no_flip.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="uptrend"),
        setup_quality="A",
        trade_direction="long",
    )
    regime_detector = SimpleNamespace(
        _per_coin_regimes={
            "TESTBUSDT": SimpleNamespace(
                regime=SimpleNamespace(value="trending_up"),
            ),
        },
    )
    sw = _make_worker(
        structural,
        flip_enabled=True,              # flip switch ON
        high_conviction_protection=True,
        regime_detector=regime_detector,
        suppression_enabled=False,      # suppression OFF
    )
    trade: dict = {"symbol": "TESTBUSDT", "direction": "Buy"}

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    # High-conviction directive held the brain's Buy — NOT reversed.
    assert trade["direction"] == "Buy"
    assert trade["_xray_suppression_booklog"] is True
    assert "_apex_was_flipped" not in trade
    assert "_flip_source" not in trade


def test_low_conviction_still_flips_when_suppression_off_and_flip_on() -> None:
    """Complement to finding 5a: suppression OFF + flip ON + LOW-conviction
    disagreement (ranging regime → not high-conviction) STILL flips via the
    low-conviction path. Proves `_hc_veto_booklogged` gates ONLY the
    high-conviction case and leaves the normal flip path intact.
    """
    sp = _FakePlacement(
        rr_long=0.4, rr_short=2.4,
        long_sl_price=99.0, long_tp_price=110.0,
        short_sl_price=101.0, short_tp_price=90.0,
    )
    structural = _FakeStructural(
        structural_placement=sp,
        market_structure=_FakeMarketStructure(structure="ranging"),
        setup_quality="A",
    )
    # No regime_detector → coin_regime "" → not high-conviction.
    sw = _make_worker(
        structural, flip_enabled=True, suppression_enabled=False,
    )
    trade: dict = {
        "symbol": "TESTBUSDT", "direction": "Buy",
        "stop_loss_price": 99.5, "take_profit_price": 105.0,
    }

    ok, reason = asyncio.run(
        sw._execute_claude_trade(trade, {"TESTBUSDT"}, plan=None),
    )

    assert ok is False
    assert reason == "dup_position"
    # Low-conviction disagreement → flipped to Sell (flip switch ON).
    assert trade["direction"] == "Sell"
    assert trade["_apex_was_flipped"] is True
    assert trade["_flip_source"] == "xray"
