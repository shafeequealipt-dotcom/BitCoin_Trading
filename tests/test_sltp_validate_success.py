"""Observability G10 — SLTP_PAIR_OK success-path emission.

The audit (2026-05-13) noted SLTP_VALIDATE fires zero times.
Investigation confirmed only the SKIP paths emitted via SLTP_PAIR_SKIP;
the OK return was silent. Operators could not distinguish "validator
ran and passed" from "validator never ran".

G10 adds SLTP_PAIR_OK on the ``("OK", "")`` return path of
``validate_pair``, with the audit-required field set so every trade
has a positive validation event in the log.
"""

from __future__ import annotations

import re

import pytest
from loguru import logger as _loguru_logger

from src.core.sl_tp_validator import SLTPValidator


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


def test_validate_pair_ok_emits_success_event(loguru_sink) -> None:
    """A valid Buy pair (sl < entry < tp) returns ("OK", "") and emits SLTP_PAIR_OK."""
    v = SLTPValidator()
    action, reason = v.validate_pair(
        sl_price=78000.0,
        tp_price=84000.0,
        entry_price=80000.0,
        current_price=80000.0,
        direction="Buy",
        symbol="BTCUSDT",
    )
    assert action == "OK"
    assert reason == ""

    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    skips = _records_with_tag(loguru_sink, "SLTP_PAIR_SKIP")
    assert len(oks) == 1, "success path must emit exactly one SLTP_PAIR_OK"
    assert len(skips) == 0, "success path must NOT emit any SLTP_PAIR_SKIP"

    level, msg = oks[0]
    assert level == "INFO"
    kv = _parse_kv(msg)
    assert kv.get("sym") == "BTCUSDT"
    assert kv.get("side") == "Buy"
    # sl_pct = |78000 - 80000| / 80000 * 100 = 2.5
    assert abs(float(kv.get("sl_pct", "0")) - 2.5) < 0.01
    # tp_pct = |84000 - 80000| / 80000 * 100 = 5.0
    assert abs(float(kv.get("tp_pct", "0")) - 5.0) < 0.01
    # max_dist_pct = 10
    assert kv.get("max_dist_pct") == "10"
    # min_gap_bps = 0.001 * 10000 = 10
    assert float(kv.get("min_gap_bps", "0")) == 10.0
    assert kv.get("decision") == "OK"
    # G10 audit schema — `checks` documents the gates the directive cleared
    assert kv.get("checks") == "invalid_price,sl_equals_tp,wrong_side"


def test_validate_pair_ok_sell_direction(loguru_sink) -> None:
    """Valid Sell pair: tp < entry < sl. sl_pct/tp_pct use absolute distance."""
    v = SLTPValidator()
    action, reason = v.validate_pair(
        sl_price=82000.0,    # 2.5% above entry
        tp_price=76000.0,    # 5.0% below entry
        entry_price=80000.0,
        current_price=80000.0,
        direction="Sell",
        symbol="ETHUSDT",
    )
    assert action == "OK"

    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    assert len(oks) == 1
    kv = _parse_kv(oks[0][1])
    assert kv.get("side") == "Sell"
    assert abs(float(kv.get("sl_pct", "0")) - 2.5) < 0.01
    assert abs(float(kv.get("tp_pct", "0")) - 5.0) < 0.01


def test_validate_pair_skip_does_not_emit_ok(loguru_sink) -> None:
    """SKIP paths preserve existing SLTP_PAIR_SKIP — no SLTP_PAIR_OK."""
    v = SLTPValidator()
    # sl_equals_tp (gap collapses)
    action, _ = v.validate_pair(
        sl_price=79999.0,
        tp_price=80001.0,    # gap = $2 = 2.5 bps < min 10 bps
        entry_price=80000.0,
        current_price=80000.0,
        direction="Buy",
        symbol="BTCUSDT",
    )
    assert action == "SKIP"

    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    skips = _records_with_tag(loguru_sink, "SLTP_PAIR_SKIP")
    assert len(oks) == 0
    assert len(skips) == 1


def test_validate_pair_skip_wrong_side_no_ok(loguru_sink) -> None:
    """Wrong-side SKIP also does not emit OK."""
    v = SLTPValidator()
    # Buy direction but TP is below entry (wrong side)
    action, reason = v.validate_pair(
        sl_price=78000.0,
        tp_price=79000.0,    # below entry — wrong side for Buy
        entry_price=80000.0,
        current_price=80000.0,
        direction="Buy",
        symbol="BTCUSDT",
    )
    assert action == "SKIP"
    assert reason == "wrong_side"

    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    assert len(oks) == 0


def test_validate_pair_ok_uses_entry_when_provided(loguru_sink) -> None:
    """When entry_price > 0 it's used as reference (sl_pct anchored to entry)."""
    v = SLTPValidator()
    v.validate_pair(
        sl_price=78000.0,
        tp_price=84000.0,
        entry_price=80000.0,    # use this as ref
        current_price=79500.0,  # different from entry — should be ignored
        direction="Buy",
        symbol="BTCUSDT",
    )
    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    assert len(oks) == 1
    kv = _parse_kv(oks[0][1])
    # 2.5% relative to entry=80000, not current=79500
    assert abs(float(kv.get("sl_pct", "0")) - 2.5) < 0.01


def test_validate_pair_ok_fallback_to_current_when_entry_zero(loguru_sink) -> None:
    """entry_price=0 → falls back to current_price for the ref."""
    v = SLTPValidator()
    v.validate_pair(
        sl_price=78000.0,
        tp_price=84000.0,
        entry_price=0.0,        # zero — use current as fallback
        current_price=80000.0,
        direction="Buy",
        symbol="BTCUSDT",
    )
    oks = _records_with_tag(loguru_sink, "SLTP_PAIR_OK")
    assert len(oks) == 1
    kv = _parse_kv(oks[0][1])
    assert abs(float(kv.get("sl_pct", "0")) - 2.5) < 0.01
