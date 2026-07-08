"""Context Builder: gathers rich market data for Claude AI responses."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("telegram")


class ContextBuilder:
    """Builds comprehensive context from DB for Claude AI responses.

    Args:
        db: Database manager.
        services: Dict of system services.
    """

    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.services = services

    def _fmt_price(self, price, symbol: str = "") -> str:
        """Exact exchange tick-size price (magnitude fallback). $-prefixed."""
        pf = self.services.get("price_formatter")
        if pf is not None:
            return pf.format(price, symbol)
        from src.telegram.ui.formatters import format_price as _ui_fp
        return _ui_fp(price)

    async def build(self, symbol: str | None = None) -> str:
        """Build context string with current market data."""
        sections: list[str] = []

        # Account status
        try:
            account = await self.services["account_service"].get_wallet_balance()
            sections.append(
                f"ACCOUNT: Equity=${account.total_equity:.2f}, "
                f"Available=${account.available_balance:.2f}"
            )
        except Exception:
            pass

        # Open positions
        try:
            positions = await self.services["position_service"].get_positions()
            if positions:
                lines = []
                for p in positions:
                    side = "LONG" if p.side.value == "Buy" else "SHORT"
                    pnl_pct = ((p.mark_price - p.entry_price) / p.entry_price * 100) if p.side.value == "Buy" else ((p.entry_price - p.mark_price) / p.entry_price * 100)
                    lines.append(f"{p.symbol} {side} entry={self._fmt_price(p.entry_price, p.symbol)} pnl={pnl_pct:+.2f}%")
                sections.append("POSITIONS:\n" + "\n".join(lines))
            else:
                sections.append("POSITIONS: None")
        except Exception:
            pass

        # Specific symbol analysis
        if symbol:
            try:
                ticker = await self.services["market_service"].get_ticker(symbol)
                sections.append(
                    f"{symbol}: price={self._fmt_price(ticker.last_price, symbol)} "
                    f"24h={ticker.change_24h_pct:+.2f}%"
                )
            except Exception:
                pass

        return "\n\n".join(sections) if sections else "No data available"
