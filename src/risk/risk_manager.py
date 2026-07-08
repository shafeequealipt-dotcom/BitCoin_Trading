"""Central Risk Manager: orchestrates all risk components."""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import AccountInfo, Position, Side
from src.database.connection import DatabaseManager
from src.risk.drawdown import DrawdownTracker
from src.risk.portfolio import PortfolioAnalyzer
from src.risk.position_sizer import PositionSizer
from src.risk.stop_loss import StopLossCalculator
from src.risk.validators import TradeValidator

log = get_logger("risk")


class RiskManager:
    """Central risk orchestrator. Single entry point for all risk checks.

    Args:
        settings: Application settings.
        db: Database manager.
        services: Dict of service instances (account, position, market, instrument).
        alert_manager: Optional alert manager for risk warnings.
    """

    def __init__(self, settings: Settings, db: DatabaseManager,
                 services: dict | None = None, alert_manager=None) -> None:
        self.settings = settings
        self.db = db
        self._services = services or {}
        self.alert_manager = alert_manager
        self.position_sizer = PositionSizer(settings)
        self.stop_loss_calc = StopLossCalculator(settings)
        self.portfolio = PortfolioAnalyzer(settings, db)
        self.drawdown = DrawdownTracker(settings, db)
        self.validator = TradeValidator(settings)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize with current account state."""
        account_svc = self._services.get("account")
        if account_svc:
            try:
                account = await account_svc.get_wallet_balance()
                await self.drawdown.initialize(account)
            except Exception as e:
                log.warning("Could not fetch account for risk init: {e}", e=str(e))

        issues = self.validator.validate_risk_params(self.settings)
        for issue in issues:
            log.warning("Risk config issue: {i}", i=issue)

        self._initialized = True
        log.info("RiskManager initialized")

    async def validate_trade(self, symbol: str, side: Side, qty: float,
                             price: float | None = None, stop_loss: float | None = None,
                             take_profit: float | None = None, leverage: int = 1) -> tuple[bool, list[str]]:
        """Validate a proposed trade against all risk rules.

        Returns:
            Tuple of (is_valid, list_of_issues).
        """
        log.info(f"RISK_VALIDATE | sym={symbol} | dir={side.value} | qty={qty} | lev={leverage} | {ctx()}")

        # Circuit breakers first
        is_safe, halt_reason = self.drawdown.check_circuit_breakers()
        if not is_safe:
            log.critical(f"RISK_CIRCUIT | sym={symbol} | rsn={halt_reason} | {ctx()}")
            return False, [halt_reason]

        # Get account and positions
        account = AccountInfo(total_equity=10000, available_balance=8000, used_margin=2000, unrealized_pnl=0)
        positions: list[Position] = []

        account_svc = self._services.get("account")
        position_svc = self._services.get("position")
        if account_svc:
            try:
                account = await account_svc.get_wallet_balance()
            except Exception:
                pass
        if position_svc:
            try:
                positions = await position_svc.get_positions()
            except Exception:
                pass

        # Get instrument info
        instrument = None
        inst_svc = self._services.get("instrument")
        if inst_svc:
            try:
                instrument = await inst_svc.get_instrument_info(symbol)
            except Exception:
                pass

        valid, issues = self.validator.validate_order(
            symbol, side, qty, price, stop_loss, take_profit, leverage,
            account, positions, instrument,
        )

        if not valid:
            log.warning(f"RISK_BLOCK | sym={symbol} | dir={side.value} | rsn={'; '.join(issues[:3])} | {ctx()}")

        if not valid and self.alert_manager:
            try:
                await self.alert_manager.send_risk_warning(
                    "Trade validation failed", {"symbol": symbol, "issues": "; ".join(issues[:3])}
                )
            except Exception:
                pass

        return valid, issues

    async def calculate_position_size(self, symbol: str, side: Side, entry_price: float,
                                      stop_loss_price: float | None = None) -> dict:
        """Calculate recommended position size."""
        account_svc = self._services.get("account")
        equity = 10000.0
        if account_svc:
            try:
                info = await account_svc.get_wallet_balance()
                equity = info.total_equity
            except Exception:
                pass

        step = 0.001
        inst_svc = self._services.get("instrument")
        if inst_svc:
            try:
                inst = await inst_svc.get_instrument_info(symbol)
                step = inst.qty_step
            except Exception:
                pass

        sl = stop_loss_price
        if sl is None:
            pct = self.settings.risk.default_stop_loss_pct
            sl = entry_price * (1 - pct / 100) if side == Side.BUY else entry_price * (1 + pct / 100)

        return self.position_sizer.recommend(
            equity, self.settings.risk.default_stop_loss_pct, entry_price,
            stop_loss_price=sl, symbol_step_size=step, side=side,
        )

    async def calculate_stop_loss(self, symbol: str, side: Side, entry_price: float) -> dict:
        """Calculate recommended SL/TP levels."""
        return self.stop_loss_calc.recommend(entry_price, side)

    async def get_portfolio_risk(self) -> dict:
        """Get comprehensive portfolio risk report."""
        positions: list[Position] = []
        account = AccountInfo(total_equity=10000, available_balance=8000, used_margin=2000, unrealized_pnl=0)

        position_svc = self._services.get("position")
        account_svc = self._services.get("account")
        if position_svc:
            try:
                positions = await position_svc.get_positions()
            except Exception:
                pass
        if account_svc:
            try:
                account = await account_svc.get_wallet_balance()
            except Exception:
                pass

        exposure = await self.portfolio.get_exposure(positions, account)
        daily = self.drawdown.get_daily_pnl(account.unrealized_pnl)
        dd = self.drawdown.get_current_drawdown(account.total_equity)

        return {"exposure": exposure, "daily_pnl": daily, "drawdown": dd}

    async def get_risk_status(self) -> dict:
        """Quick risk health check."""
        portfolio = await self.get_portfolio_risk()
        is_safe, reason = self.drawdown.check_circuit_breakers()

        statuses = []
        if portfolio["daily_pnl"]["limit_status"] != "safe":
            statuses.append(portfolio["daily_pnl"]["limit_status"])
        if portfolio["exposure"]["exposure_status"] != "safe":
            statuses.append(portfolio["exposure"]["exposure_status"])
        if portfolio["drawdown"]["status"] != "safe":
            statuses.append(portfolio["drawdown"]["status"])

        if self.drawdown.trading_halted:
            overall = "halted"
        elif "exceeded" in statuses or "critical" in statuses:
            overall = "critical"
        elif "warning" in statuses:
            overall = "warning"
        else:
            overall = "safe"

        return {
            "overall_status": overall,
            "trading_allowed": is_safe,
            "daily_pnl": portfolio["daily_pnl"],
            "drawdown": portfolio["drawdown"],
            "portfolio_exposure": portfolio["exposure"],
            "circuit_breakers": {
                "daily_loss_limit": portfolio["daily_pnl"]["limit_status"],
                "max_drawdown": portfolio["drawdown"]["status"],
                "consecutive_losses": f"{self.drawdown.consecutive_losses}/5",
                "cooldown": "active" if self.drawdown.is_in_cooldown() else "inactive",
            },
            "warnings": portfolio["exposure"].get("warnings", []),
            "halt_reason": self.drawdown.halt_reason,
        }

    async def on_trade_closed(self, pnl: float) -> None:
        """Called after a trade is closed to update risk state."""
        self.drawdown.record_trade_result(pnl)
        is_safe, reason = self.drawdown.check_circuit_breakers()

        if not is_safe and self.alert_manager:
            try:
                await self.alert_manager.send_risk_warning(
                    "Circuit breaker triggered", {"reason": reason}
                )
            except Exception:
                pass

    async def on_price_update(self, equity: float) -> None:
        """Called periodically with latest equity."""
        self.drawdown.update_equity(equity)
