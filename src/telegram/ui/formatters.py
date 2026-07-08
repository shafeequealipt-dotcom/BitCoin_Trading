"""Message formatting helpers for Telegram."""

from datetime import datetime, timezone

from src.core.utils import format_price as _core_format_price


def format_price(price: float, symbol: str = "") -> str:
    """Magnitude-aware price for Telegram UI ($-prefixed, grouped, zeros stripped).

    Delegates decimal precision to the canonical ``src.core.utils.format_price``
    so sub-cent coins are no longer mangled — the old fixed-tier ``<$1 -> .4f``
    rounded 0.0001584 to "$0.0002". The ``symbol`` arg is kept for signature
    compatibility; exact per-symbol tick precision is applied by callers that
    hold a ``PriceFormatter`` (the dashboard/handlers), so this module
    deliberately does not depend on the trading layer — it is the
    magnitude-aware fallback used everywhere a resolver isn't threaded.
    """
    return f"${_core_format_price(price, grouped=True, strip_zeros=True)}"


def format_pnl(pnl: float, pnl_pct: float = 0.0) -> str:
    emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
    sign = "+" if pnl >= 0 else ""
    pct = f" ({sign}{pnl_pct:.1f}%)" if pnl_pct != 0 else ""
    return f"{emoji} {sign}${abs(pnl):,.2f}{pct}"


def format_side(side_value: str) -> str:
    if side_value in ("Buy", "BUY", "long", "LONG"):
        return "\U0001f4c8 LONG"
    return "\U0001f4c9 SHORT"


def format_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")


def truncate(text: str, max_len: int = 100) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def calc_pnl_pct(entry: float, mark: float, side: str) -> float:
    if entry <= 0:
        return 0.0
    if side in ("Buy", "BUY"):
        return ((mark - entry) / entry) * 100
    return ((entry - mark) / entry) * 100
