"""Morning Briefing: daily auto-sent summary of account, positions, and market."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.ui.formatters import format_price, format_timestamp

log = get_logger("telegram")


class MorningBriefing:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def generate(self) -> str:
        """Generate morning briefing message."""
        msg = f"\u2600\ufe0f <b>MORNING BRIEFING</b>\n{format_timestamp()}\n\n"

        try:
            account = await self.s["account_service"].get_wallet_balance()
            msg += f"\U0001f4b0 Equity: <b>{format_price(account.total_equity)}</b>\n"
            msg += f"\U0001f4b5 Available: {format_price(account.available_balance)}\n\n"
        except Exception:
            msg += "Account data unavailable\n\n"

        try:
            positions = await self.s["position_service"].get_positions()
            msg += f"\U0001f4cb <b>Positions:</b> {len(positions)} open\n"
            for p in positions:
                side = "L" if p.side.value == "Buy" else "S"
                msg += f"  {p.symbol} {side} ${p.unrealized_pnl:+,.2f}\n"
            msg += "\n"
        except Exception:
            pass

        try:
            detector = self.s.get("regime_detector")
            if detector:
                state = await detector.detect()
                msg += f"\U0001f30d Regime: <b>{state.regime.value}</b> (conf {state.confidence:.0%})\n"
        except Exception:
            pass

        msg += "\nGood luck today! \U0001f3af"
        return msg
