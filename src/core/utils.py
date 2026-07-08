"""Utility helper functions: timestamps, rounding, ID generation, and more.

All functions are pure (no side effects) and fully typed.
"""

import math
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with an optional prefix.

    Args:
        prefix: String prefix (e.g. "ord", "sig"). Underscore added automatically.

    Returns:
        Unique string like "ord_a1b2c3d4".
    """
    short = uuid.uuid4().hex[:12]
    if prefix:
        return f"{prefix}_{short}"
    return short


def now_utc() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def now_timestamp_ms() -> int:
    """Return the current UTC time as Unix milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def timestamp_to_datetime(ts_ms: int) -> datetime:
    """Convert Unix milliseconds to a timezone-aware UTC datetime.

    Args:
        ts_ms: Unix timestamp in milliseconds.

    Returns:
        UTC datetime.
    """
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> int:
    """Convert a datetime to Unix milliseconds.

    Args:
        dt: A datetime object (assumed UTC if naive).

    Returns:
        Unix timestamp in milliseconds.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def round_price(price: float, tick_size: float) -> float:
    """Round a price to the nearest exchange tick size.

    Args:
        price: Raw price value.
        tick_size: Minimum price increment (e.g. 0.01).

    Returns:
        Price rounded to the nearest tick.
    """
    if tick_size <= 0:
        return price
    decimals = _decimal_places(tick_size)
    return round(round(price / tick_size) * tick_size, decimals)


def round_qty(qty: float, step_size: float) -> float:
    """Round a quantity to the nearest exchange step size.

    Args:
        qty: Raw quantity value.
        step_size: Minimum quantity increment (e.g. 0.001).

    Returns:
        Quantity rounded down to the nearest step.
    """
    if step_size <= 0:
        return qty
    decimals = _decimal_places(step_size)
    return round(math.floor(qty / step_size) * step_size, decimals)


def quantize_qty_floor(qty: float, step_size: float) -> float:
    """T1-4 (2026-05-12) — floor-quantize qty to the nearest exchange step
    using ``Decimal`` arithmetic.

    Used by reduceOnly partial-close paths where rounding UP could
    exceed ``pos.size`` and trigger Bybit reject ``ret_code=10001
    'Qty invalid'`` OR the ``qty_exceeds_size`` REDUCE_FALLBACK arm.
    ``Decimal`` avoids the float-drift case where ``math.floor(qty /
    step)`` returns ``N+1`` because the division lands at ``N + ULP``
    (e.g. ``0.0030000000000000005 / 0.001`` → ``3.000000000000001``).

    The existing :func:`round_qty` above is left untouched: it is called
    from 5+ live OrderService / risk_manager sites and changing its
    semantics is out of scope. This helper is additive — opt-in for the
    bybit_demo adapter's ``reduce_position`` path.

    Args:
        qty: Raw quantity value.
        step_size: Minimum quantity increment from
            ``InstrumentInfo.qty_step`` (e.g. ``0.001``, ``1.0``).

    Returns:
        Quantity floored to the nearest grid step. Returns ``0.0`` when
        ``step_size <= 0`` OR ``qty <= 0`` OR the quantized value is
        zero (caller decides whether to skip-partial or full-close on
        the zero result).
    """
    if step_size <= 0 or qty <= 0:
        return 0.0
    q = Decimal(str(qty))
    s = Decimal(str(step_size))
    n = (q / s).to_integral_value(rounding=ROUND_DOWN)
    return float(n * s)


def pct_change(old: float, new: float) -> float:
    """Calculate percentage change from old to new value.

    Args:
        old: Previous value.
        new: Current value.

    Returns:
        Percentage change (e.g. 5.0 for +5%). Returns 0.0 if old is zero.
    """
    if old == 0.0:
        return 0.0
    return ((new - old) / abs(old)) * 100.0


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value to the range [min_val, max_val].

    Args:
        value: Input value.
        min_val: Minimum bound.
        max_val: Maximum bound.

    Returns:
        Clamped value.
    """
    return max(min_val, min(value, max_val))


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """Divide a by b, returning default if b is zero.

    Args:
        a: Numerator.
        b: Denominator.
        default: Value to return when b is zero.

    Returns:
        a / b or default.
    """
    if b == 0.0:
        return default
    return a / b


def chunk_list(lst: list[Any], size: int) -> list[list[Any]]:
    """Split a list into chunks of the given size.

    Args:
        lst: Input list.
        size: Maximum chunk size (must be >= 1).

    Returns:
        List of sublists, each with at most `size` elements.
    """
    if size < 1:
        size = 1
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def flatten_dict(
    d: dict[str, Any], parent_key: str = "", sep: str = "."
) -> dict[str, Any]:
    """Flatten a nested dict into dot-separated keys.

    Args:
        d: Nested dictionary.
        parent_key: Prefix for keys (used in recursion).
        sep: Separator between key levels.

    Returns:
        Flat dictionary with compound keys like "a.b.c".
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def _decimal_places(value: float) -> int:
    """Count decimal places in a float value."""
    s = f"{value:.15g}"
    if "." in s:
        return len(s.split(".")[1].rstrip("0")) or 0
    return 0


def decimals_for_price(price: float, ref_price: float | None = None) -> int:
    """Magnitude-aware decimal places for a price (the P0-7 ladder).

    Anchored to ``ref_price`` when supplied (e.g. format a tiny SL
    distance at the symbol's price scale). Thresholds match the long
    standing :func:`format_price` behaviour:

      - abs(ref) > 10        -> 2
      - 1   < abs(ref) ≤ 10  -> 4
      - 0.01 < abs(ref) ≤ 1  -> 6
      - abs(ref) ≤ 0.01      -> 8

    Returns ``2`` as a safe default on non-numeric / non-finite input so
    callers never crash on a bad value.
    """
    try:
        ref = float(ref_price if ref_price is not None else price)
    except (TypeError, ValueError):
        return 2
    if not math.isfinite(ref):
        return 2
    abs_ref = abs(ref)
    if abs_ref > 10:
        return 2
    if abs_ref > 1:
        return 4
    if abs_ref > 0.01:
        return 6
    return 8


def decimals_for_tick(tick_size: float) -> int:
    """Decimal places implied by an exchange tick size.

    e.g. ``0.0001 -> 4``, ``0.5 -> 1``, ``1.0 -> 0``, ``0.0000001 -> 7``.
    Returns ``0`` for a non-positive / non-finite tick — callers should
    treat ``0`` (or a missing instrument) as "tick unknown" and fall back
    to :func:`decimals_for_price`.

    Uses ``Decimal`` rather than the ``%g``-based :func:`_decimal_places`
    because ticks at or below ``1e-5`` (common for sub-cent coins such as
    1000PEPE / 10000SATS) render in scientific notation under ``%g`` and
    would yield 0 decimals — the exact case this fix must get right.
    ``str(float)`` gives the minimal round-trip repr and ``normalize()``
    collapses integer ticks (``10.0`` -> exponent ``+1`` -> 0 decimals).
    """
    try:
        t = float(tick_size)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(t) or t <= 0:
        return 0
    exponent = Decimal(str(t)).normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def format_price(
    price: float,
    ref_price: float | None = None,
    *,
    decimals: int | None = None,
    grouped: bool = False,
    strip_zeros: bool = False,
) -> str:
    """Phase 4 (P0-7) — symbol-magnitude-aware price formatter.

    Hardcoded ``f"${val:.2f}"`` was rounding sub-cent coins like RAREUSDT
    ($0.018) to "$0.02", which masked critical SL/TP edge cases such as
    SL≈TP. This helper picks decimals from the price magnitude:

      - price > $10        -> 2 decimals
      - $1   < price ≤ $10 -> 4 decimals
      - $0.01 < price ≤ $1 -> 6 decimals
      - price ≤ $0.01      -> 8 decimals

    ``ref_price`` lets the caller anchor precision to a related figure
    (e.g. when logging a tiny SL distance for a high-priced symbol you
    typically want the SL formatted at the symbol's tick scale, not at
    the SL distance scale). When omitted, the price itself is the
    reference.

    Precision override and display options (all keyword-only and default
    to the historical behaviour, so existing callers and log output are
    byte-for-byte unchanged):

      - ``decimals=N`` — format at exactly N places, e.g. exchange
        tick-size precision via :func:`decimals_for_tick`. ``None`` (the
        default) uses the magnitude ladder via :func:`decimals_for_price`.
      - ``grouped`` — thousands separators (e.g. ``70,000.00``).
      - ``strip_zeros`` — drop trailing zeros and any dangling ``.``
        (e.g. ``0.072200`` -> ``0.0722``), matching the exchange's clean
        display.

    Returns the formatted string WITHOUT a leading currency symbol so
    callers can compose ``f"sl=${format_price(sl, entry)}"`` cleanly.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return str(price)
    if not math.isfinite(p):
        return str(price)

    if decimals is None:
        # Preserve the original ref-resolution semantics exactly: a bad or
        # non-finite ref_price falls back to the unformatted value.
        try:
            ref = float(ref_price if ref_price is not None else price)
        except (TypeError, ValueError):
            return str(price)
        if not math.isfinite(ref):
            return str(price)
        d = decimals_for_price(ref)
    else:
        d = max(0, int(decimals))

    out = f"{p:,.{d}f}" if grouped else f"{p:.{d}f}"

    if strip_zeros and "." in out:
        out = out.rstrip("0").rstrip(".")
    return out
