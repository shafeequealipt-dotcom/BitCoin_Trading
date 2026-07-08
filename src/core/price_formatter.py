"""Canonical user-facing price renderer — the single display seam.

Background. Prices were rendered with hardcoded fixed decimals (``:.2f`` /
``:.4f``) or two duplicate fixed-tier formatters, which mangled low-priced
coins: a $0.0959 coin showed as "$0.10" (~4% gap vs the exchange), a
$0.0001584 coin as "0.0002" or "0.00". The stored/traded values were always
full precision — only the *display* rounded. Routing every surface through
this one service means the precision rule lives in exactly one place.

Precision strategy:
  1. Exact exchange tick size when known — via an injected ``decimals``
     resolver that reads InstrumentService's cache — so a displayed price
     matches the exchange digit-for-digit.
  2. Magnitude-aware fallback (:func:`src.core.utils.format_price`) when the
     tick is unavailable (uncached symbol, or no resolver wired).

The resolver is an opaque ``Callable[[str], int | None]`` rather than the
InstrumentService itself, so this module stays in ``core`` with no
dependency on the trading layer (no circular import) and is trivially
testable with a stub. Instances are created at boot and injected via the
services dict (see ``WorkerManager``) — there is no module-level global,
honouring the "services dict is the only DI mechanism" invariant.
"""

from collections.abc import Callable

from src.core.log_context import ctx
from src.core.log_tags import PRICE_FMT_FALLBACK
from src.core.logging import get_logger
from src.core.utils import format_price

log = get_logger("price_formatter")


class PriceFormatter:
    """Formats prices for human display at exact-tick (or magnitude) precision.

    Args:
        decimals_resolver: Optional ``symbol -> decimals | None`` callable
            (e.g. ``InstrumentService.price_decimals``). Returns the exact
            number of decimal places for a symbol from cached exchange tick
            size, or ``None`` when unknown. When the resolver is ``None`` or
            returns ``None``, formatting falls back to magnitude-aware
            precision. Kept opaque (a plain callable) so ``core`` does not
            depend on the trading layer.
    """

    def __init__(
        self, decimals_resolver: Callable[[str], int | None] | None = None
    ) -> None:
        self._resolver = decimals_resolver
        # Per-process dedup so the cache-miss fallback DEBUG line fires at
        # most once per symbol — never on the exact-tick hit path (that
        # would be per-render spam at 5s/60s render cadences).
        self._fallback_logged: set[str] = set()

    @property
    def has_tick_resolver(self) -> bool:
        """True when an exact-tick resolver is wired (for the boot sentinel)."""
        return self._resolver is not None

    def format(
        self, price: float, symbol: str = "", ref_price: float | None = None
    ) -> str:
        """Render *price* for *symbol* with a leading ``$``.

        Uses exact tick-size decimals when the resolver knows *symbol*;
        otherwise magnitude-aware precision. Trailing zeros are stripped and
        thousands separators added so the output matches the exchange's clean
        display (e.g. ``$70,000``, ``$0.0722``, ``$0.0001584``).

        Callers that show ``None`` SL/TP should guard before calling (as the
        existing handlers do); this method renders whatever numeric value it
        is given.
        """
        decimals = self._resolve_decimals(symbol)
        body = format_price(
            price,
            ref_price,
            decimals=decimals,
            grouped=True,
            strip_zeros=True,
        )
        return f"${body}"

    def _resolve_decimals(self, symbol: str) -> int | None:
        if not self._resolver or not symbol:
            return None
        try:
            decimals = self._resolver(symbol)
        except Exception as e:
            # A resolver fault must never break a render — fall back quietly
            # (deduped) and let magnitude precision take over.
            if symbol not in self._fallback_logged:
                self._fallback_logged.add(symbol)
                log.debug(
                    f"{PRICE_FMT_FALLBACK} | sym={symbol} reason=resolver_error "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )
            return None
        if decimals is None and symbol not in self._fallback_logged:
            self._fallback_logged.add(symbol)
            log.debug(
                f"{PRICE_FMT_FALLBACK} | sym={symbol} reason=tick_unavailable | {ctx()}"
            )
        return decimals
