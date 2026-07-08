"""Central Service Container — initializes ALL system components in dependency order.

Every entry point (workers.py, server.py, brain.py) uses this container
as the single source of truth for component initialization.
"""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations

log = get_logger("core")


class ServiceContainer:
    """Initializes and holds references to all system services.

    Initialization is layered — each layer depends only on previous layers.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db
        self.services: dict = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize ALL services in dependency order."""
        if self._initialized:
            return

        settings = self.settings
        db = self.db

        await db.connect()
        await run_migrations(db)
        self.services["db"] = db

        # Layer 1: Bybit client + trading services
        try:
            from src.trading.client import BybitClient
            from src.trading.services.account_service import AccountService
            from src.trading.services.market_service import MarketService
            from src.trading.services.order_service import OrderService
            from src.trading.services.position_service import PositionService

            bybit = BybitClient(settings, db)
            await bybit.connect()
            market_svc = MarketService(
                bybit, db, kline_save_chunk_size=settings.database.kline_save_chunk_size
            )
            pos_svc = PositionService(bybit, db, settings)
            ord_svc = OrderService(bybit, db, settings)
            acc_svc = AccountService(bybit, db)

            self.services.update({
                "bybit": bybit,
                "market": market_svc, "market_service": market_svc,
                "position": pos_svc, "position_service": pos_svc,
                "order": ord_svc, "order_service": ord_svc,
                "account": acc_svc, "account_service": acc_svc,
            })
        except Exception as e:
            log.warning("Trading services unavailable: {err}", err=str(e))

        # Layer 2: Analysis engine
        try:
            from src.analysis.engine import TAEngine
            ta = TAEngine(db, settings=self.settings)
            self.services["ta"] = ta
            self.services["ta_engine"] = ta
        except Exception as e:
            log.warning("TA Engine unavailable: {err}", err=str(e))

        # Layer 3: Brain services — Claude Code CLI ($0 cost)
        try:
            from src.brain.claude_code_client import ClaudeCodeClient, ClaudeCodeCostTracker
            from src.brain.decision_parser import DecisionParser

            cost_tracker = ClaudeCodeCostTracker()
            claude_client = ClaudeCodeClient(
                timeout_seconds=90,
                max_retries=2,
                min_interval=2.0,
            )
            decision_parser = DecisionParser()

            self.services.update({
                "cost_tracker": cost_tracker,
                "claude_client": claude_client,
                "decision_parser": decision_parser,
            })
            log.info("Using Claude Code CLI client ($0 cost — included in Max subscription)")
        except Exception as e:
            log.warning("Brain services unavailable: {err}", err=str(e))

        # Layer 4: Risk + Alerts
        try:
            from src.alerts.alert_manager import AlertManager
            alert_mgr = AlertManager(settings, db)
            interactive = hasattr(settings, 'telegram_interactive') and settings.telegram_interactive.enabled
            if not interactive:
                await alert_mgr.initialize()
            else:
                alert_mgr.enabled = settings.alerts.telegram_enabled
            self.services["alert_manager"] = alert_mgr
        except Exception as e:
            log.warning("AlertManager unavailable: {err}", err=str(e))

        try:
            from src.risk.risk_manager import RiskManager
            risk_mgr = RiskManager(settings, db, self.services)
            await risk_mgr.initialize()
            self.services["risk_manager"] = risk_mgr
            # Phase 12.4 (lifecycle-logging-audit Gap 4.X follow-up):
            # RiskManager.validate_trade is BYPASSED in production (only
            # called from legacy brain_v2.py:418, not the active strategist
            # path). Safety contract preserved by apex/gate.py::TradeGate.
            # Startup log makes the inactive state explicit so operators
            # don't grep RISK_BLOCK and assume something's broken.
            from src.core.log_context import ctx as _ctx
            log.info(
                "RISK_MANAGER_INACTIVE | reason=brain_v2_legacy_path_unused "
                "replaced_by=apex_gate validation=delegated | " + _ctx()
            )
        except Exception as e:
            log.warning("RiskManager unavailable: {err}", err=str(e))

        # Layer 5: Strategy system
        try:
            from src.strategies.pnl_manager import DailyPnLManager
            from src.strategies.registry import StrategyRegistry
            from src.strategies.register_all import register_all_strategies

            registry = StrategyRegistry(
                regime_filter_enabled=settings.strategy_engine.strategy_regime_filter_enabled,
            )
            register_all_strategies(registry)
            pnl_mgr = DailyPnLManager(
                settings,
                account_service=self.services.get("account"),
                position_service=self.services.get("position"),
            )
            self.services["registry"] = registry
            self.services["pnl_manager"] = pnl_mgr
        except Exception as e:
            log.warning("Strategy system unavailable: {err}", err=str(e))

        self._initialized = True
        log.info(
            "ServiceContainer initialized: {n} services",
            n=len(self.services),
        )

    def get(self, name: str, default=None):
        return self.services.get(name, default)

    def get_all(self) -> dict:
        return dict(self.services)

    async def shutdown(self) -> None:
        """Graceful shutdown of all services."""
        log.info("ServiceContainer shutting down...")

        # Flush pending alerts
        alert_mgr = self.services.get("alert_manager")
        if alert_mgr and hasattr(alert_mgr, "shutdown"):
            try:
                await alert_mgr.shutdown()
            except Exception as e:
                log.warning("Alert manager shutdown: {err}", err=str(e))

        # Disconnect WebSocket
        ws = self.services.get("ws")
        if ws and hasattr(ws, "disconnect"):
            try:
                await ws.disconnect()
            except Exception as e:
                log.warning("WebSocket disconnect: {err}", err=str(e))

        # Close Bybit REST client
        bybit = self.services.get("bybit")
        if bybit and hasattr(bybit, "disconnect"):
            try:
                await bybit.disconnect()
            except Exception as e:
                log.warning("Bybit disconnect: {err}", err=str(e))

        # Close database last
        await self.db.disconnect()
        log.info("ServiceContainer shutdown complete")
