"""Price Alert Worker: checks custom price alerts every 10 seconds."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.features.price_alerts import PriceAlertEngine
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class PriceAlertWorker(BaseWorker):
    """Checks user-defined price alerts against current prices.

    Args:
        settings: Application settings.
        db: Database manager.
        alert_engine: Price alert engine.
        market_service: For fetching current prices.
        telegram_bot: For sending alert notifications.
    """

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        alert_engine: PriceAlertEngine, market_service=None, telegram_bot=None,
    ) -> None:
        interval = getattr(settings, 'telegram_interactive', None)
        check_interval = interval.price_alert_check_interval if interval else 10
        super().__init__(
            name="price_alert_worker",
            interval_seconds=float(check_interval),
            settings=settings,
            db=db,
        )
        self.alert_engine = alert_engine
        self.market_service = market_service
        self.telegram_bot_ref = telegram_bot

    async def tick(self) -> None:
        """Check all active price alerts."""
        if not self.market_service:
            return

        # Phase conn-pool/p5-3 (2026-05-14) — gate the per-10 s DB poll on
        # the engine's in-memory active-alerts count. ``price_alerts`` has
        # had 0 rows across all observed sessions; with no in-memory gate
        # the worker queried the DB every 10 s for nothing, contributing
        # to lock pressure pre-refactor and to log noise post-refactor.
        # ``has_active()`` reads from a cached count maintained by
        # create/trigger/cancel and re-probed periodically (every 30 min)
        # for self-healing.
        if not await self.alert_engine.has_active():
            return

        # Get current prices for all symbols with alerts
        active = await self.alert_engine.repo.get_active_alerts()
        if not active:
            return

        symbols = list(set(a["symbol"] for a in active))
        prices: dict[str, float] = {}

        for symbol in symbols:
            try:
                ticker = await self.market_service.get_ticker(symbol)
                prices[symbol] = ticker.last_price
            except Exception:
                pass

        triggered = await self.alert_engine.check_alerts(prices)

        for alert in triggered:
            log.info(
                "Price alert triggered: {sym} {cond} {target} (current={cur})",
                sym=alert.symbol, cond=alert.condition,
                target=alert.target_price, cur=prices.get(alert.symbol, 0),
            )
