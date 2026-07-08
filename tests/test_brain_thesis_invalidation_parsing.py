"""Phase 3.3 — Mid-Hold Trade Management Fix: parse + validate brain criterion.

Tests ``DecisionParser.parse_thesis_invalidation``. Approach C primary:
brain states criterion. Approach A fallback: brain omits/returns invalid.

Per IMPLEMENT_MIDHOLD doc Rule 16 + Rule 7 + Rule 4: validation must be
strict, failures must be loud (BRAIN_THESIS_INVALIDATION_INVALID log),
and fallback must be observable (returns 'heuristic_fallback' source so
the caller's persisted source column reflects the path taken).
"""

from __future__ import annotations

import json
import re

import pytest
from loguru import logger as _loguru_logger

from src.brain.decision_parser import DecisionParser


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


@pytest.fixture
def parser() -> DecisionParser:
    return DecisionParser()


# ════════════════════════════════════════════════════════════════════
# 1. Valid criteria — PARSED + brain_stated
# ════════════════════════════════════════════════════════════════════


def test_valid_price_close_above(parser, loguru_sink) -> None:
    """Short justified by a structural ceiling at 245.30."""
    trade = {
        "symbol": "SOLUSDT",
        "direction": "Sell",
        "thesis_invalidation": {"type": "price_close_above", "value": 245.30},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=240.0, symbol="SOLUSDT")
    assert source == "brain_stated"
    parsed = json.loads(crit)
    assert parsed["type"] == "price_close_above"
    assert parsed["value"] == 245.30
    assert len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_PARSED ")) == 1


def test_valid_price_close_below(parser, loguru_sink) -> None:
    trade = {
        "symbol": "BTCUSDT",
        "direction": "Buy",
        "thesis_invalidation": {"type": "price_close_below", "value": 78000.0},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=80000.0, symbol="BTCUSDT")
    assert source == "brain_stated"
    assert json.loads(crit)["type"] == "price_close_below"


def test_valid_signal_criterion(parser, loguru_sink) -> None:
    """STRONG SELL consensus thesis with the corresponding flip signal."""
    trade = {
        "symbol": "ETHUSDT",
        "direction": "Sell",
        "thesis_invalidation": {
            "type": "signal",
            "value": "ensemble_flip_to_strong_buy",
        },
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2109.0, symbol="ETHUSDT")
    assert source == "brain_stated"
    parsed = json.loads(crit)
    assert parsed["type"] == "signal"
    assert parsed["value"] == "ensemble_flip_to_strong_buy"
    kv = _parse_kv(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_PARSED ")[0][1])
    assert kv["type"] == "signal"


def test_valid_none_type(parser, loguru_sink) -> None:
    """Pure trend pullback — no specific criterion. Brain says 'none' explicitly."""
    trade = {
        "symbol": "XRPUSDT",
        "direction": "Sell",
        "thesis_invalidation": {"type": "none", "value": None},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=1.36, symbol="XRPUSDT")
    assert source == "brain_stated"
    parsed = json.loads(crit)
    assert parsed["type"] == "none"
    assert parsed["value"] is None


# ════════════════════════════════════════════════════════════════════
# 2. Missing field — MISSING + heuristic_fallback
# ════════════════════════════════════════════════════════════════════


def test_missing_field_falls_back(parser, loguru_sink) -> None:
    """Legacy brain response with no thesis_invalidation key."""
    trade = {"symbol": "ETHUSDT", "direction": "Sell"}
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2109.0, symbol="ETHUSDT")
    assert crit == ""
    assert source == "heuristic_fallback"
    assert len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_MISSING ")) == 1


def test_explicit_empty_string_falls_back(parser, loguru_sink) -> None:
    """Brain returned thesis_invalidation: ''. Treat as missing."""
    trade = {
        "symbol": "ETHUSDT",
        "direction": "Sell",
        "thesis_invalidation": "",
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2109.0)
    assert crit == ""
    assert source == "heuristic_fallback"
    assert len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_MISSING ")) == 1


# ════════════════════════════════════════════════════════════════════
# 3. Malformed criteria — INVALID + heuristic_fallback
# ════════════════════════════════════════════════════════════════════


def test_non_dict_payload_invalid(parser, loguru_sink) -> None:
    """Brain returned a string when a dict was expected."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": "above 245.30",  # free-text, not a dict
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=240.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "not_a_dict" in msg


def test_unknown_type_invalid(parser, loguru_sink) -> None:
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "candle_pattern", "value": "doji"},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "unknown_type" in msg


def test_unknown_signal_keyword_invalid(parser, loguru_sink) -> None:
    """Brain invented a signal keyword the watchdog cannot monitor."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "signal", "value": "oversold_recovery"},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "unknown_signal" in msg


def test_price_not_numeric_invalid(parser, loguru_sink) -> None:
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "price_close_above", "value": "high"},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "price_not_numeric" in msg


def test_price_out_of_sanity_range_invalid(parser, loguru_sink) -> None:
    """Brain hallucination: criterion 200% above entry. Reject."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "price_close_above", "value": 6300.0},  # 3x entry
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "price_out_of_range" in msg


def test_price_string_encoded_is_normalized(parser, loguru_sink) -> None:
    """Brain returned a stringified number — accept after normalization."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "price_close_above", "value": "2128.5"},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "brain_stated"
    parsed = json.loads(crit)
    assert isinstance(parsed["value"], (int, float))
    assert parsed["value"] == 2128.5


def test_none_with_non_null_value_invalid(parser, loguru_sink) -> None:
    """{'type':'none','value':42} is a contradiction."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "none", "value": 42},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=2100.0)
    assert source == "heuristic_fallback"
    msg = _records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID ")[0][1]
    assert "none_with_value" in msg


# ════════════════════════════════════════════════════════════════════
# 4. Edge cases
# ════════════════════════════════════════════════════════════════════


def test_zero_entry_price_skips_sanity_check(parser) -> None:
    """When entry_price is unknown (zero), sanity check is skipped —
    only the type/value shape is validated. Better to accept a price
    criterion than to reject everything."""
    trade = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "price_close_above", "value": 2128.5},
    }
    crit, source = parser.parse_thesis_invalidation(trade, entry_price=0.0)
    assert source == "brain_stated"


def test_post_flip_criterion_discard_simulation(parser, loguru_sink) -> None:
    """Audit hotfix (2026-05-19): when APEX/XRAY flips direction after the
    parser ran, the strategy_worker downgrades source to
    heuristic_fallback so the (direction-aware) snapshot drives
    monitoring. This test simulates the strategy_worker control flow.

    Live production trace that motivated the fix: INJUSDT — brain said
    Buy with price_close_below 4.78 (valid Buy floor). XRAY flipped to
    Sell. Without the downgrade, the watchdog would have monitored a
    price_close_below criterion for a Sell trade — but a Sell falling
    below the level is PROFIT, not invalidation. Result: the watchdog
    would have either never fired (criterion in the TP direction) or
    fired a false INVALIDATED signal on a profitable price move.
    """
    # Brain's raw response — direction=Buy + price_close_below.
    brain_trade = {
        "symbol": "INJUSDT",
        "direction": "Buy",
        "thesis_invalidation": {"type": "price_close_below", "value": 4.78},
    }
    crit_json, source = parser.parse_thesis_invalidation(
        brain_trade, entry_price=4.938, symbol="INJUSDT",
    )
    # Parser succeeds — the brain's Buy + price_close_below is valid.
    assert source == "brain_stated"
    assert "price_close_below" in crit_json

    # Simulate the strategy_worker post-flip downgrade (the actual code
    # lives in src/workers/strategy_worker.py near line 2735).
    _apex_was_flipped = True   # the flip flag the strategy_worker sets
    _xray_flip_source = "xray"
    if (_apex_was_flipped or _xray_flip_source) and source == "brain_stated":
        crit_json = ""
        source = "heuristic_fallback"

    # After the simulated downgrade, criterion is empty and source is
    # heuristic — the watchdog now uses the (direction-aware) snapshot.
    assert crit_json == ""
    assert source == "heuristic_fallback"


def test_exactly_one_log_emission_per_call(parser, loguru_sink) -> None:
    """The parser must emit exactly one of PARSED/MISSING/INVALID per
    call. Mute observability is a Rule 4 anti-pattern."""
    trade_valid = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "signal", "value": "regime_inverted"},
    }
    parser.parse_thesis_invalidation(trade_valid, entry_price=2100.0)

    trade_missing = {"symbol": "ETHUSDT"}
    parser.parse_thesis_invalidation(trade_missing, entry_price=2100.0)

    trade_invalid = {
        "symbol": "ETHUSDT",
        "thesis_invalidation": {"type": "BAD", "value": None},
    }
    parser.parse_thesis_invalidation(trade_invalid, entry_price=2100.0)

    parsed_n = len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_PARSED "))
    missing_n = len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_MISSING "))
    invalid_n = len(_records_with_tag(loguru_sink, "BRAIN_THESIS_INVALIDATION_INVALID "))
    assert parsed_n == 1
    assert missing_n == 1
    assert invalid_n == 1
