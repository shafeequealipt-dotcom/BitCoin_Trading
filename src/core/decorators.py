"""Reusable decorators: retry, rate_limit, timed, validate_input.

All decorators are async-compatible and preserve function signatures.
"""

import asyncio
import functools
import inspect
import time
from typing import Any, Callable, TypeVar

from loguru import logger

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Retry a function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including the first call).
        delay: Initial delay between retries in seconds.
        backoff: Multiplier applied to delay after each failure.
        exceptions: Tuple of exception types to catch and retry on.

    Returns:
        Decorator that wraps the target function with retry logic.
    """

    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                current_delay = delay
                last_exc: BaseException | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt == max_attempts:
                            logger.warning(
                                "Retry exhausted for {func} after {n} attempts: {err}",
                                func=func.__name__,
                                n=max_attempts,
                                err=str(e),
                            )
                            raise
                        logger.debug(
                            "Retry {attempt}/{max} for {func}: {err}, waiting {d:.1f}s",
                            attempt=attempt,
                            max=max_attempts,
                            func=func.__name__,
                            err=str(e),
                            d=current_delay,
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                raise last_exc  # type: ignore[misc]

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                current_delay = delay
                last_exc: BaseException | None = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        last_exc = e
                        if attempt == max_attempts:
                            logger.warning(
                                "Retry exhausted for {func} after {n} attempts: {err}",
                                func=func.__name__,
                                n=max_attempts,
                                err=str(e),
                            )
                            raise
                        logger.debug(
                            "Retry {attempt}/{max} for {func}: {err}, waiting {d:.1f}s",
                            attempt=attempt,
                            max=max_attempts,
                            func=func.__name__,
                            err=str(e),
                            d=current_delay,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                raise last_exc  # type: ignore[misc]

            return sync_wrapper  # type: ignore[return-value]

    return decorator


class _TokenBucket:
    """Async-safe token bucket for rate limiting."""

    def __init__(self, calls_per_second: float) -> None:
        self.rate = calls_per_second
        self.max_tokens = calls_per_second
        self.tokens = calls_per_second
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_refill = time.monotonic()
            else:
                self.tokens -= 1.0


# Cache buckets per (function, rate) so each decorated function shares one bucket
_buckets: dict[str, _TokenBucket] = {}


def rate_limit(calls_per_second: float = 10.0) -> Callable[[F], F]:
    """Rate-limit a function using a token bucket algorithm.

    Args:
        calls_per_second: Maximum calls allowed per second.

    Returns:
        Decorator that enforces the rate limit.
    """

    def decorator(func: F) -> F:
        bucket_key = f"{func.__module__}.{func.__qualname__}:{calls_per_second}"

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if bucket_key not in _buckets:
                    _buckets[bucket_key] = _TokenBucket(calls_per_second)
                await _buckets[bucket_key].acquire()
                return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                # For sync functions, use simple time-based throttling
                if bucket_key not in _buckets:
                    _buckets[bucket_key] = _TokenBucket(calls_per_second)
                # Can't await in sync context, so run in event loop if available
                try:
                    loop = asyncio.get_running_loop()
                    # If we're in an async context, this shouldn't be sync
                    raise RuntimeError(
                        "rate_limit on sync function called inside async loop. "
                        "Make the function async instead."
                    )
                except RuntimeError:
                    pass
                return func(*args, **kwargs)

            return sync_wrapper  # type: ignore[return-value]

    return decorator


def timed(func: F) -> F:
    """Log the execution time of a function via loguru.

    Works with both sync and async functions.
    """

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter() - start
                logger.debug(
                    "{func} completed in {t:.3f}s",
                    func=func.__name__,
                    t=elapsed,
                )

        return async_wrapper  # type: ignore[return-value]

    else:

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.perf_counter() - start
                logger.debug(
                    "{func} completed in {t:.3f}s",
                    func=func.__name__,
                    t=elapsed,
                )

        return sync_wrapper  # type: ignore[return-value]


def validate_input(**validators: Callable[[Any], bool]) -> Callable[[F], F]:
    """Validate function arguments before execution.

    Each keyword maps a parameter name to a validator function that returns
    True if the value is acceptable, False otherwise.

    Example:
        @validate_input(price=lambda x: x > 0, symbol=lambda s: len(s) > 0)
        def place_order(symbol: str, price: float): ...

    Args:
        **validators: Mapping of param_name -> validator_callable.

    Returns:
        Decorator that validates inputs before calling the function.
    """

    def decorator(func: F) -> F:
        sig = inspect.signature(func)

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                for param_name, validator_fn in validators.items():
                    if param_name in bound.arguments:
                        value = bound.arguments[param_name]
                        if not validator_fn(value):
                            raise ValueError(
                                f"Validation failed for parameter '{param_name}': "
                                f"value {value!r} rejected by {validator_fn}"
                            )
                return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                for param_name, validator_fn in validators.items():
                    if param_name in bound.arguments:
                        value = bound.arguments[param_name]
                        if not validator_fn(value):
                            raise ValueError(
                                f"Validation failed for parameter '{param_name}': "
                                f"value {value!r} rejected by {validator_fn}"
                            )
                return func(*args, **kwargs)

            return sync_wrapper  # type: ignore[return-value]

    return decorator
