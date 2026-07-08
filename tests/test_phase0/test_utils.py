"""Tests for utility helper functions."""

from datetime import datetime, timezone

from src.core.utils import (
    chunk_list,
    clamp,
    datetime_to_timestamp,
    flatten_dict,
    generate_id,
    now_timestamp_ms,
    now_utc,
    pct_change,
    round_price,
    round_qty,
    safe_divide,
    timestamp_to_datetime,
)


class TestGenerateId:
    def test_with_prefix(self):
        id_ = generate_id("ord")
        assert id_.startswith("ord_")
        assert len(id_) == 16  # "ord_" + 12 hex chars

    def test_without_prefix(self):
        id_ = generate_id()
        assert "_" not in id_
        assert len(id_) == 12

    def test_unique(self):
        ids = {generate_id("test") for _ in range(100)}
        assert len(ids) == 100


class TestTimestamps:
    def test_now_utc_is_aware(self):
        dt = now_utc()
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_now_timestamp_ms_is_positive(self):
        ts = now_timestamp_ms()
        assert ts > 0
        assert isinstance(ts, int)

    def test_timestamp_roundtrip(self):
        original = datetime(2024, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        ts_ms = datetime_to_timestamp(original)
        restored = timestamp_to_datetime(ts_ms)
        assert abs((restored - original).total_seconds()) < 0.001

    def test_timestamp_to_datetime_epoch(self):
        dt = timestamp_to_datetime(0)
        assert dt.year == 1970

    def test_datetime_to_timestamp_naive(self):
        naive = datetime(2024, 1, 1, 0, 0, 0)
        ts = datetime_to_timestamp(naive)
        assert ts > 0


class TestRounding:
    def test_round_price_basic(self):
        assert round_price(50123.456, 0.01) == 50123.46

    def test_round_price_whole(self):
        assert round_price(50123.456, 1.0) == 50123.0

    def test_round_price_small_tick(self):
        assert round_price(0.123456, 0.0001) == 0.1235

    def test_round_price_zero_tick(self):
        assert round_price(123.456, 0.0) == 123.456

    def test_round_qty_floors(self):
        assert round_qty(1.999, 0.01) == 1.99

    def test_round_qty_exact(self):
        assert round_qty(1.0, 0.1) == 1.0

    def test_round_qty_zero_step(self):
        assert round_qty(1.5, 0.0) == 1.5


class TestMath:
    def test_pct_change_positive(self):
        assert pct_change(100.0, 110.0) == 10.0

    def test_pct_change_negative(self):
        assert pct_change(100.0, 90.0) == -10.0

    def test_pct_change_zero_old(self):
        assert pct_change(0.0, 100.0) == 0.0

    def test_clamp_within(self):
        assert clamp(5.0, 0.0, 10.0) == 5.0

    def test_clamp_below(self):
        assert clamp(-5.0, 0.0, 10.0) == 0.0

    def test_clamp_above(self):
        assert clamp(15.0, 0.0, 10.0) == 10.0

    def test_safe_divide_normal(self):
        assert safe_divide(10.0, 2.0) == 5.0

    def test_safe_divide_by_zero(self):
        assert safe_divide(10.0, 0.0) == 0.0

    def test_safe_divide_custom_default(self):
        assert safe_divide(10.0, 0.0, default=-1.0) == -1.0


class TestChunkList:
    def test_even_split(self):
        result = chunk_list([1, 2, 3, 4], 2)
        assert result == [[1, 2], [3, 4]]

    def test_uneven_split(self):
        result = chunk_list([1, 2, 3, 4, 5], 2)
        assert result == [[1, 2], [3, 4], [5]]

    def test_empty_list(self):
        assert chunk_list([], 3) == []

    def test_chunk_size_larger(self):
        result = chunk_list([1, 2], 10)
        assert result == [[1, 2]]

    def test_chunk_size_zero(self):
        result = chunk_list([1, 2, 3], 0)
        assert len(result) == 3  # Falls back to size=1


class TestFlattenDict:
    def test_simple(self):
        assert flatten_dict({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested(self):
        result = flatten_dict({"a": {"b": {"c": 1}}})
        assert result == {"a.b.c": 1}

    def test_mixed(self):
        result = flatten_dict({"a": 1, "b": {"c": 2, "d": 3}})
        assert result == {"a": 1, "b.c": 2, "b.d": 3}

    def test_custom_separator(self):
        result = flatten_dict({"a": {"b": 1}}, sep="/")
        assert result == {"a/b": 1}

    def test_empty(self):
        assert flatten_dict({}) == {}
