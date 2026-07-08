"""Tests for decorators: retry, rate_limit, timed, validate_input."""

import asyncio
import time

import pytest

from src.core.decorators import rate_limit, retry, timed, validate_input


class TestRetrySync:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert succeed() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, backoff=1.0)
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        assert fail_twice() == "ok"
        assert call_count == 3

    def test_exhausts_retries(self):
        @retry(max_attempts=2, delay=0.01)
        def always_fail():
            raise RuntimeError("always")

        with pytest.raises(RuntimeError, match="always"):
            always_fail()

    def test_specific_exception_filter(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def fail_with_type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            fail_with_type_error()
        assert call_count == 1  # No retry for TypeError

    def test_preserves_function_name(self):
        @retry()
        def my_function():
            pass

        assert my_function.__name__ == "my_function"


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_async_retry_succeeds(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        async def async_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("retry me")
            return "async_ok"

        result = await async_succeed()
        assert result == "async_ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_async_retry_exhausted(self):
        @retry(max_attempts=2, delay=0.01)
        async def async_always_fail():
            raise RuntimeError("async fail")

        with pytest.raises(RuntimeError):
            await async_always_fail()


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit_allows_burst(self):
        """First call should go through immediately."""

        @rate_limit(calls_per_second=100.0)
        async def fast_fn():
            return "done"

        start = time.monotonic()
        result = await fast_fn()
        elapsed = time.monotonic() - start
        assert result == "done"
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_rate_limit_preserves_name(self):
        @rate_limit(calls_per_second=10.0)
        async def named_fn():
            pass

        assert named_fn.__name__ == "named_fn"


class TestTimed:
    def test_sync_timed(self):
        @timed
        def slow_fn():
            time.sleep(0.05)
            return 42

        assert slow_fn() == 42

    @pytest.mark.asyncio
    async def test_async_timed(self):
        @timed
        async def async_slow():
            await asyncio.sleep(0.05)
            return 99

        assert await async_slow() == 99

    def test_timed_preserves_name(self):
        @timed
        def my_fn():
            pass

        assert my_fn.__name__ == "my_fn"


class TestValidateInput:
    def test_valid_input_passes(self):
        @validate_input(x=lambda v: v > 0)
        def positive(x: int) -> int:
            return x

        assert positive(5) == 5

    def test_invalid_input_raises(self):
        @validate_input(x=lambda v: v > 0)
        def positive(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="Validation failed"):
            positive(-1)

    def test_multiple_validators(self):
        @validate_input(
            name=lambda v: len(v) > 0,
            age=lambda v: 0 < v < 150,
        )
        def create_user(name: str, age: int) -> dict:
            return {"name": name, "age": age}

        assert create_user("Alice", 30) == {"name": "Alice", "age": 30}

        with pytest.raises(ValueError):
            create_user("", 30)

        with pytest.raises(ValueError):
            create_user("Bob", -5)

    @pytest.mark.asyncio
    async def test_async_validate(self):
        @validate_input(symbol=lambda s: len(s) > 0)
        async def get_price(symbol: str) -> float:
            return 50000.0

        assert await get_price("BTCUSDT") == 50000.0

        with pytest.raises(ValueError):
            await get_price("")

    def test_preserves_name(self):
        @validate_input(x=lambda v: True)
        def fn(x: int) -> int:
            return x

        assert fn.__name__ == "fn"
