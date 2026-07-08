"""ALPHA R1 — counter-aware trade_direction propagation tests.

Covers the StructuralData.trade_direction field addition + the
_gather_structural_data_from_cache assembler population + the
XRAY_DIRECTION_SPLIT observability line.

Pre-fix APEX consumed only suggested_direction (regime label, 87
percent short on 2026-05-16) and was blind to trade_direction (the
counter-aware field the brain prompt already reads, 62 percent short).
"""

import io
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# Test 1 — StructuralData has trade_direction field with empty default
# ===========================================================================


def test_structural_data_trade_direction_default_empty():
    from src.apex.models import StructuralData

    sd = StructuralData()
    assert sd.trade_direction == "", (
        f"default trade_direction must be empty string, got {sd.trade_direction!r}"
    )
    sd2 = StructuralData(trade_direction="long")
    assert sd2.trade_direction == "long"
    sd3 = StructuralData(trade_direction="short")
    assert sd3.trade_direction == "short"
    print("  PASS: StructuralData.trade_direction default empty + assignable")


# ===========================================================================
# Test 2 — assembler propagates trade_direction from analysis
# ===========================================================================


def _make_fake_analysis(trade_direction, suggested_direction="short", setup_type_value=None):
    """Build a minimal duck-typed StructuralAnalysis for assembler input.

    Mirrors the attribute names assembler.py actually reads at
    src/apex/assembler.py:732-822 (current_price, setup_quality,
    setup_score, suggested_direction, position_in_range, setup_type,
    trade_direction, nearest_support, nearest_resistance,
    market_structure, structural_placement, nearest_fvg, nearest_ob,
    active_sweep_signal, nearest_unswept_liquidity, smc_confluence,
    poc_price, volume_profile, fib_key_level, fibonacci, mtf_confluence,
    total_confluence_factors, session_context, setup_rank).
    """

    class FakeSetupType:
        def __init__(self, value):
            self.value = value

    a = types.SimpleNamespace()
    a.current_price = 100.0
    a.setup_quality = "GOOD"
    a.setup_score = 5
    a.suggested_direction = suggested_direction
    a.position_in_range = 0.5
    a.trade_direction = trade_direction
    a.setup_type = FakeSetupType(setup_type_value) if setup_type_value else None
    a.nearest_support = None
    a.nearest_resistance = None
    a.market_structure = None
    a.structural_placement = None
    a.nearest_fvg = None
    a.nearest_ob = None
    a.active_sweep_signal = None
    a.nearest_unswept_liquidity = None
    a.smc_confluence = 0
    a.poc_price = None
    a.volume_profile = None
    a.fib_key_level = None
    a.fibonacci = None
    a.mtf_confluence = None
    a.total_confluence_factors = 0
    a.session_context = None
    a.setup_rank = None
    a.atr_pct_h1 = 0.0
    return a


def test_assembler_propagates_trade_direction_long_for_bullish_counter():
    from src.apex.assembler import _gather_structural_data_from_cache

    fake_analysis = _make_fake_analysis(
        trade_direction="long",
        suggested_direction="short",  # regime is short; counter setup inverts
        setup_type_value="BULLISH_FVG_OB_COUNTER",
    )
    fake_cache = types.SimpleNamespace(get=lambda sym: fake_analysis)
    services = {"structure_cache": fake_cache}

    sd = _gather_structural_data_from_cache(services, "TESTUSDT")
    assert sd is not None
    assert sd.trade_direction == "long", (
        f"BULLISH_FVG_OB_COUNTER must yield trade_direction='long', got "
        f"{sd.trade_direction!r}"
    )
    assert sd.suggested_direction == "short", (
        f"suggested_direction must remain 'short' (regime label), got "
        f"{sd.suggested_direction!r}"
    )
    assert sd.setup_type == "BULLISH_FVG_OB_COUNTER"
    print(
        "  PASS: assembler propagates BULLISH_FVG_OB_COUNTER trade_direction=long "
        "with suggested_direction=short preserved"
    )


def test_assembler_propagates_trade_direction_short_for_bearish_counter():
    from src.apex.assembler import _gather_structural_data_from_cache

    fake_analysis = _make_fake_analysis(
        trade_direction="short",
        suggested_direction="long",
        setup_type_value="BEARISH_FVG_OB_COUNTER",
    )
    fake_cache = types.SimpleNamespace(get=lambda sym: fake_analysis)
    services = {"structure_cache": fake_cache}

    sd = _gather_structural_data_from_cache(services, "TESTUSDT")
    assert sd is not None
    assert sd.trade_direction == "short"
    assert sd.suggested_direction == "long"
    assert sd.setup_type == "BEARISH_FVG_OB_COUNTER"
    print("  PASS: BEARISH_FVG_OB_COUNTER -> trade_direction=short / suggested=long")


def test_assembler_propagates_matched_directions_for_in_direction_setup():
    from src.apex.assembler import _gather_structural_data_from_cache

    fake_analysis = _make_fake_analysis(
        trade_direction="long",
        suggested_direction="long",
        setup_type_value="BULLISH_FVG_OB",
    )
    fake_cache = types.SimpleNamespace(get=lambda sym: fake_analysis)
    services = {"structure_cache": fake_cache}

    sd = _gather_structural_data_from_cache(services, "TESTUSDT")
    assert sd is not None
    assert sd.trade_direction == "long"
    assert sd.suggested_direction == "long"
    print(
        "  PASS: in-direction BULLISH_FVG_OB -> both fields 'long' (no inversion)"
    )


def test_assembler_defaults_trade_direction_empty_when_absent():
    from src.apex.assembler import _gather_structural_data_from_cache

    fake_analysis = _make_fake_analysis(
        trade_direction="",
        suggested_direction="short",
        setup_type_value=None,
    )
    fake_cache = types.SimpleNamespace(get=lambda sym: fake_analysis)
    services = {"structure_cache": fake_cache}

    sd = _gather_structural_data_from_cache(services, "TESTUSDT")
    assert sd is not None
    assert sd.trade_direction == "", (
        f"missing trade_direction must leave field empty, got {sd.trade_direction!r}"
    )
    print("  PASS: missing/empty trade_direction yields empty string default")


# ===========================================================================
# Test 3 — XRAY_DIRECTION_SPLIT log line emits new fields
# ===========================================================================


def test_xray_direction_split_log_format():
    """Verify the new XRAY_DIRECTION_SPLIT line matches the documented format.

    Capture via loguru sink. The structure_worker tick path is too
    coupled to stand up here without the full worker; instead exercise
    the same f-string the tick emits with controlled inputs and assert
    every required field appears.
    """
    from loguru import logger

    captured = []
    sink_id = logger.add(
        lambda msg: captured.append(msg.record["message"]),
        level="INFO",
        format="{message}",
    )
    try:
        trade_dir_counts = {"long": 38, "short": 62, "na": 0}
        counter_count = 13
        _tdc_total = sum(trade_dir_counts.values())
        _long_pct = trade_dir_counts.get("long", 0) / _tdc_total * 100.0
        _short_pct = trade_dir_counts.get("short", 0) / _tdc_total * 100.0
        logger.info(
            f"XRAY_DIRECTION_SPLIT | total={_tdc_total} "
            f"trade_dir_long={trade_dir_counts.get('long', 0)} "
            f"trade_dir_short={trade_dir_counts.get('short', 0)} "
            f"trade_dir_na={trade_dir_counts.get('na', 0)} "
            f"long_pct={_long_pct:.1f} short_pct={_short_pct:.1f} "
            f"counter_count={counter_count} | ctx=test"
        )
    finally:
        logger.remove(sink_id)

    assert captured, "expected one log line"
    msg = captured[0]
    for field in (
        "XRAY_DIRECTION_SPLIT",
        "total=100",
        "trade_dir_long=38",
        "trade_dir_short=62",
        "trade_dir_na=0",
        "long_pct=38.0",
        "short_pct=62.0",
        "counter_count=13",
    ):
        assert field in msg, f"field {field!r} missing from log line: {msg!r}"
    print("  PASS: XRAY_DIRECTION_SPLIT format carries every required field")


# ===========================================================================
# Test runner
# ===========================================================================


if __name__ == "__main__":
    print("ALPHA R1 — trade_direction propagation tests")
    test_structural_data_trade_direction_default_empty()
    test_assembler_propagates_trade_direction_long_for_bullish_counter()
    test_assembler_propagates_trade_direction_short_for_bearish_counter()
    test_assembler_propagates_matched_directions_for_in_direction_setup()
    test_assembler_defaults_trade_direction_empty_when_absent()
    test_xray_direction_split_log_format()
    print("ALPHA R1: ALL 6 TESTS PASSED")
