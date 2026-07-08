"""Portfolio-level risk analysis: exposure, concentration, correlation."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.types import AccountInfo, Position, Side
from src.core.utils import safe_divide
from src.database.connection import DatabaseManager

log = get_logger("risk")

ALT_CORRELATED = {"SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT"}


class PortfolioAnalyzer:
    """Analyzes portfolio-level risk: exposure, concentration, correlation.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db

    async def get_exposure(self, positions: list[Position], account: AccountInfo) -> dict:
        """Calculate total portfolio exposure including leverage."""
        total_val = sum(abs(p.size * p.mark_price) for p in positions)
        effective_val = sum(abs(p.size * p.mark_price * p.leverage) for p in positions)
        equity = account.total_equity or 1

        total_pct = safe_divide(total_val, equity, 0) * 100
        effective_pct = safe_divide(effective_val, equity, 0) * 100
        max_allowed = self.settings.risk.max_total_exposure_pct

        if total_pct > max_allowed:
            status = "exceeded"
        elif total_pct > max_allowed * 0.8:
            status = "warning"
        else:
            status = "safe"

        pos_details = []
        largest = {"symbol": "", "pct": 0}
        for p in positions:
            val = abs(p.size * p.mark_price)
            pct = safe_divide(val, equity, 0) * 100
            pos_details.append({
                "symbol": p.symbol, "side": p.side.value,
                "value_usd": round(val, 2), "pct_of_equity": round(pct, 2),
                "leverage": p.leverage, "unrealized_pnl": round(p.unrealized_pnl, 2),
            })
            if pct > largest["pct"]:
                largest = {"symbol": p.symbol, "pct": round(pct, 2)}

        warnings = await self.check_concentration(positions, account)
        warnings.extend(await self.check_correlation(positions))

        return {
            "total_positions": len(positions),
            "total_position_value_usd": round(total_val, 2),
            "total_exposure_pct": round(total_pct, 2),
            "effective_exposure_pct": round(effective_pct, 2),
            "max_allowed_exposure_pct": max_allowed,
            "exposure_status": status,
            "available_exposure_pct": round(max(0, max_allowed - total_pct), 2),
            "positions": pos_details,
            "largest_position": largest,
            "warnings": warnings,
        }

    async def check_concentration(self, positions: list[Position], account: AccountInfo) -> list[str]:
        """Check for position concentration risk."""
        warnings = []
        equity = account.total_equity or 1
        max_pos_pct = self.settings.risk.max_position_size_pct

        for p in positions:
            val = abs(p.size * p.mark_price)
            pct = safe_divide(val, equity, 0) * 100
            if pct > max_pos_pct:
                warnings.append(f"{p.symbol} is {pct:.1f}% of equity (max {max_pos_pct}%)")

        total_val = sum(abs(p.size * p.mark_price) for p in positions)
        if total_val > 0:
            for p in positions:
                val = abs(p.size * p.mark_price)
                if safe_divide(val, total_val, 0) > 0.5 and len(positions) > 1:
                    warnings.append(f"{p.symbol} is >50% of total exposure — concentrated")
        return warnings

    async def check_correlation(self, positions: list[Position]) -> list[str]:
        """Check for correlation risk among positions."""
        warnings = []
        alt_count = sum(1 for p in positions if p.symbol in ALT_CORRELATED)
        if alt_count >= 3:
            warnings.append(f"{alt_count} positions in correlated altcoins — diversification risk")

        sides = {p.side for p in positions}
        if len(positions) >= 2 and len(sides) == 1:
            direction = "long" if Side.BUY in sides else "short"
            warnings.append(f"All {len(positions)} positions are {direction} — directional exposure risk")
        return warnings

    async def get_portfolio_summary(self, positions: list[Position], account: AccountInfo) -> dict:
        """Comprehensive portfolio summary."""
        exposure = await self.get_exposure(positions, account)
        total_pnl = sum(p.unrealized_pnl for p in positions)
        avg_lev = safe_divide(sum(p.leverage for p in positions), len(positions), 1) if positions else 1

        best = max(positions, key=lambda p: p.unrealized_pnl) if positions else None
        worst = min(positions, key=lambda p: p.unrealized_pnl) if positions else None

        return {
            **exposure,
            "total_unrealized_pnl": round(total_pnl, 2),
            "avg_leverage": round(avg_lev, 1),
            "best_position": best.symbol if best else None,
            "worst_position": worst.symbol if worst else None,
        }
