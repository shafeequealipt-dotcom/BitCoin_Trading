"""Account service: wallet balance, equity, and margin information."""

from src.core.decorators import retry, timed
from src.core.logging import get_logger
from src.core.types import AccountInfo
from src.core.utils import now_utc, safe_divide
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository
from src.trading.client import BybitClient

log = get_logger("trading")


class AccountService:
    """Service for account balance and margin operations.

    Args:
        client: Connected BybitClient.
        db: Database manager for persistence.
    """

    def __init__(self, client: BybitClient, db: DatabaseManager) -> None:
        self._client = client
        self._db = db
        self._trading_repo = TradingRepository(db)

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_wallet_balance(self) -> AccountInfo:
        """Fetch unified account balance from Bybit.

        Maps the response to AccountInfo, saves a snapshot to the database.

        Returns:
            AccountInfo with equity, balance, margin, and PnL.
        """
        result = await self._client.call(
            "get_wallet_balance",
            accountType="UNIFIED",
        )

        accounts = result.get("list", [])
        if not accounts:
            return AccountInfo(
                total_equity=0.0,
                available_balance=0.0,
                used_margin=0.0,
                unrealized_pnl=0.0,
            )

        account = accounts[0]
        total_equity = float(account.get("totalEquity", "0"))
        available = float(account.get("totalAvailableBalance", "0"))
        used_margin = float(account.get("totalInitialMargin", "0"))
        unrealized = float(account.get("totalPerpUPL", "0"))
        margin_level = safe_divide(total_equity, used_margin, 0.0) * 100 if used_margin > 0 else 0.0

        info = AccountInfo(
            total_equity=total_equity,
            available_balance=available,
            used_margin=used_margin,
            unrealized_pnl=unrealized,
            margin_level_pct=margin_level,
            updated_at=now_utc(),
        )

        await self._trading_repo.save_account_snapshot(info)
        log.info(
            "Account: equity={eq:.2f} available={av:.2f} margin_used={mu:.2f}",
            eq=total_equity,
            av=available,
            mu=used_margin,
        )
        return info

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_available_balance(self) -> float:
        """Get just the available USDT balance.

        Returns:
            Available balance in USDT.
        """
        info = await self.get_wallet_balance()
        return info.available_balance

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_equity(self) -> float:
        """Get total account equity including unrealized PnL.

        Returns:
            Total equity in USDT.
        """
        info = await self.get_wallet_balance()
        return info.total_equity

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_margin_usage(self) -> dict[str, float]:
        """Get margin usage breakdown.

        Returns:
            Dict with used_margin, free_margin, margin_ratio_pct.
        """
        info = await self.get_wallet_balance()
        return {
            "used_margin": info.used_margin,
            "free_margin": info.available_balance,
            "margin_ratio_pct": info.margin_level_pct,
            "total_equity": info.total_equity,
            "unrealized_pnl": info.unrealized_pnl,
        }
