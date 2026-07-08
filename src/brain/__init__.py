"""Claude Brain — Autonomous Trading Intelligence.

The Brain calls the Claude API automatically to analyze markets,
make trading decisions, and execute trades.
"""

from src.brain.claude_client import ClaudeClient
from src.brain.cost_tracker import CostTracker
from src.brain.decision_parser import DecisionParser

# Legacy v1 imports (deprecated — kept for backward compatibility)
try:
    from src.brain.executor import BrainExecutor
except ImportError:
    BrainExecutor = None
try:
    from src.brain.prompt_builder import PromptBuilder
except ImportError:
    PromptBuilder = None
try:
    from src.brain.scheduler import BrainScheduler
except ImportError:
    BrainScheduler = None
from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations

log = get_logger("brain")


class BrainManager:
    """Top-level manager that wires all Brain components together.

    Args:
        settings: Application settings.
        db: Database manager.
    """

    def __init__(self, settings: Settings, db: DatabaseManager) -> None:
        self.settings = settings
        self.db = db
        self._services: dict = {}
        self.cost_tracker: CostTracker | None = None
        self.scheduler: BrainScheduler | None = None

    async def initialize(self) -> None:
        """Create all services and brain components."""
        await self.db.connect()
        await run_migrations(self.db)

        # Create services (same pattern as WorkerManager)
        try:
            from src.trading.client import BybitClient
            from src.trading.services.account_service import AccountService
            from src.trading.services.market_service import MarketService
            from src.trading.services.order_service import OrderService
            from src.trading.services.position_service import PositionService

            bybit = BybitClient(self.settings, self.db)
            await bybit.connect()
            self._services["account"] = AccountService(bybit, self.db)
            self._services["market"] = MarketService(
                bybit,
                self.db,
                kline_save_chunk_size=self.settings.database.kline_save_chunk_size,
            )
            self._services["order"] = OrderService(bybit, self.db, self.settings)
            self._services["position"] = PositionService(bybit, self.db, self.settings)
        except Exception as e:
            log.warning("Trading services unavailable: {err}", err=str(e))

        try:
            from src.intelligence.sentiment.scorer import SentimentScorer
            from src.intelligence.news.finnhub_client import FinnhubClient
            from src.intelligence.news.news_service import NewsService
            from src.intelligence.sentiment.aggregator import SentimentAggregator
            from src.intelligence.altdata.fear_greed import FearGreedClient
            from src.intelligence.altdata.funding_rates import FundingRateTracker

            scorer = SentimentScorer()
            finnhub = FinnhubClient(self.settings)
            self._services["news"] = NewsService(finnhub, scorer, self.db, self.settings)
            # CALL_B Framing Fix Phase 5B (2026-05-06) — pass settings so
            # the aggregator reads `[sentiment].consumption_enabled` and
            # behaves consistently across the deprecated brain.py entry
            # point + the production workers/manager.py path. The legacy
            # 2-arg signature is back-compat in the aggregator __init__.
            self._services["aggregator"] = SentimentAggregator(self.db, scorer, self.settings)
            self._services["fear_greed"] = FearGreedClient(self.settings, self.db)
            bybit_ref = self._services.get("_bybit_client")
            if "order" in self._services:
                from src.intelligence.altdata.funding_rates import FundingRateTracker
                # Reuse bybit client from trading
                pass
        except Exception as e:
            log.warning("Intelligence services unavailable: {err}", err=str(e))

        try:
            from src.analysis.engine import TAEngine
            self._services["ta"] = TAEngine(self.db, settings=self.settings)
        except Exception as e:
            log.warning("TA Engine unavailable: {err}", err=str(e))

        # Alert Manager — create but do NOT connect bot here.
        # The bot connection is managed by the unified InteractiveTelegramBot.
        # AlertManager will send via the shared bot instance set by the interactive bot.
        alert_manager = None
        try:
            from src.alerts.alert_manager import AlertManager
            alert_manager = AlertManager(self.settings, self.db)
            alert_manager.enabled = self.settings.alerts.telegram_enabled
            log.info("Brain AlertManager created (bot will connect via unified bot)")
        except Exception as e:
            log.warning("AlertManager unavailable in Brain: {err}", err=str(e))

        # Brain components
        self.cost_tracker = CostTracker(daily_budget_usd=1.00)
        claude_client = ClaudeClient(self.settings, self.cost_tracker)
        builder = PromptBuilder(self.db, self.settings, self._services)
        parser = DecisionParser()
        executor = BrainExecutor(self.settings, self._services)

        self.scheduler = BrainScheduler(
            self.settings, claude_client, builder, parser, executor, self.cost_tracker,
            alert_manager=alert_manager,
        )

        log.info("Brain initialized")

    async def start(self) -> None:
        """Start the Brain scheduler loop."""
        if self.scheduler:
            await self.scheduler.start()

    async def run_once(self) -> dict:
        """Run a single analysis cycle."""
        if self.scheduler:
            return await self.scheduler.run_once()
        return {"error": "Brain not initialized"}

    async def run_daily_summary(self) -> dict:
        """Generate a daily summary (placeholder for future enhancement)."""
        return {"summary": "Daily summary generation requires brain scheduler to be running."}

    async def shutdown(self) -> None:
        """Stop scheduler and close connections."""
        if self.scheduler:
            await self.scheduler.stop()
        await self.db.disconnect()
        log.info("Brain shutdown complete")


__all__ = [
    "BrainManager", "BrainScheduler", "ClaudeClient",
    "PromptBuilder", "DecisionParser", "BrainExecutor", "CostTracker",
]
