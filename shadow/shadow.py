"""Shadow — Market Data Warehouse & Virtual Exchange Simulator.

Entry point that initializes config, logging, database, data collectors,
and runs the main event loop. Handles graceful shutdown on SIGTERM/SIGINT.

Usage:
    python shadow.py                        # Start with defaults
    python shadow.py --config path.toml     # Custom config path
    python shadow.py --log-level DEBUG      # Override log level
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

# Ensure project root is on sys.path for 'from src...' imports
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config import load_config, ShadowConfig
from src.utils.logging import setup_logging, get_logger
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations, initialize_wallet
from src.collector.coin_selector import CoinSelector
from src.collector.websocket import WebSocketManager
from src.collector.kline_collector import KlineCollector
from src.collector.ticker_collector import TickerCollector
from src.collector.funding_collector import FundingCollector
from src.collector.oi_collector import OICollector
from src.exchange.wallet import VirtualWallet
from src.exchange.order_engine import OrderEngine
from src.exchange.position_monitor import PositionMonitor
from src.exchange.trade_recorder import TradeRecorder
from src.exchange.wallet_snapshotter import WalletSnapshotter
from src.exchange.daily_rollup import DailyRollup
from src.api.shadow_client import create_api_app
from src.telegram.bot import create_bot, start_bot, stop_bot, send_trade_open_alert, send_trade_close_alert


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Shadow — Virtual Exchange Simulator")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.toml (default: config.toml in project root)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    return parser.parse_args()


async def main() -> None:
    """Initialize all systems and run the main event loop."""
    args = parse_args()

    # 1. Load configuration
    config = load_config(args.config)

    # 2. Apply log level override if provided
    log_level = args.log_level or config.general.log_level

    # 3. Initialize logging
    setup_logging(log_level, config.general.log_dir)
    log = get_logger("shadow")

    # 4. Startup banner
    log.info("=" * 60)
    log.info("Shadow — Market Data Warehouse & Virtual Exchange")
    log.info("=" * 60)
    log.info("Config    : {path}", path=PROJECT_ROOT / "config.toml")
    log.info("Database  : {path}", path=config.database.path)
    log.info("Log dir   : {path}", path=config.general.log_dir)

    # 5. Initialize database
    db = DatabaseManager(config.database.path, config.database.wal_mode)
    await db.connect()

    ws_manager = None
    tasks: list[asyncio.Task] = []

    try:
        # 6. Run migrations + initialize wallet
        await run_migrations(db)
        await initialize_wallet(db, config.exchange.starting_balance)

        # 7. Log Phase 1 verification info
        ver = await db.fetch_one("SELECT MAX(version) as v FROM schema_version")
        schema_version = ver["v"] if ver else 0
        table_rows = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        wallet = await db.fetch_one("SELECT starting_balance FROM virtual_wallet WHERE id=1")
        balance = wallet["starting_balance"] if wallet else 0.0

        log.info("Schema    : v{ver}", ver=schema_version)
        log.info("Tables    : {cnt}", cnt=len(table_rows))
        log.info("Wallet    : ${bal:,.2f}", bal=balance)

        # ─── Phase 2: Data Collector ────────────────────────────────

        # 8. Select top coins by volume
        coin_selector = CoinSelector(db=db, config=config)
        symbols = await coin_selector.select_top_coins(config.collector.coin_count)

        if not symbols:
            log.error("No coins selected — cannot start collectors")
            return

        log.info("Coins     : {n}", n=len(symbols))
        log.info("WS URL    : {url}", url=config.bybit.ws_url)
        log.info("=" * 60)

        # 9. Create WebSocket manager and collectors
        ws_manager = WebSocketManager(config)
        ws_manager.set_symbols(symbols)

        kline_collector = KlineCollector(db, config)
        ticker_collector = TickerCollector(db, config, ws_manager)
        funding_collector = FundingCollector(db, config, ws_manager)
        oi_collector = OICollector(db, config, ws_manager)

        # 10. Register kline callback with WS manager
        ws_manager.on_kline(kline_collector.on_kline)

        # 11. Backfill missing klines before starting live stream
        await kline_collector.backfill(symbols)

        # ─── Phase 3: Virtual Exchange ──────────────────────────────

        # 12. Create price function that reads from WS manager
        def get_price_data(symbol: str):
            ticker = ws_manager.get_latest_ticker(symbol)
            if ticker is None:
                return None
            return {
                "last": ticker.get("lastPrice", 0),
                "bid": ticker.get("bid1Price"),
                "ask": ticker.get("ask1Price"),
                "volume": ticker.get("volume24h"),
                "funding": ticker.get("fundingRate"),
            }

        # 13. Initialize wallet and order engine
        wallet = VirtualWallet(db=db, config=config, price_fn=get_price_data)
        await wallet.initialize()

        # ─── Phase 5: Record Keeping ────────────────────────────────

        trade_recorder = TradeRecorder(db=db)
        order_engine = OrderEngine(
            db=db, config=config, wallet=wallet,
            price_fn=get_price_data, trade_recorder=trade_recorder,
        )

        # Phase 4: Position Monitor
        position_monitor = PositionMonitor(
            db=db, order_engine=order_engine,
            price_fn=get_price_data, config=config,
        )

        # Phase 5: Wallet Snapshotter + Daily Rollup
        wallet_snapshotter = WalletSnapshotter(db=db, wallet=wallet, config=config)
        daily_rollup = DailyRollup(db=db, wallet=wallet, config=config)

        log.info("Exchange  : All components initialized (Phase 1-5)")

        # ─── Phase 6: HTTP API Server ───────────────────────────────

        from aiohttp import web
        api_app = create_api_app(
            wallet=wallet, order_engine=order_engine,
            position_monitor=position_monitor, price_fn=get_price_data,
            db=db, ws_manager=ws_manager,
        )
        api_runner = web.AppRunner(api_app)
        await api_runner.setup()
        api_site = web.TCPSite(api_runner, config.api.host, config.api.port)
        await api_site.start()
        log.info("API       : http://{host}:{port}", host=config.api.host, port=config.api.port)

        # ─── Phase 7: Telegram Bot ──────────────────────────────────

        telegram_app = await create_bot(
            config=config, wallet=wallet, order_engine=order_engine,
            position_monitor=position_monitor, ws_manager=ws_manager, db=db,
        )
        if telegram_app:
            await start_bot(telegram_app)

            # Wire trade alerts — order engine calls these on open/close
            async def _on_trade_open(trade_data):
                await send_trade_open_alert(telegram_app, trade_data)

            async def _on_trade_close(close_data):
                await send_trade_close_alert(telegram_app, close_data)

            order_engine._on_trade_open = _on_trade_open
            order_engine._on_trade_close = _on_trade_close

            log.info("Telegram  : Bot active")
        else:
            log.info("Telegram  : Skipped (no token)")

        # 15. Setup graceful shutdown
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _signal_handler(sig_name: str) -> None:
            log.info("Received {sig}, initiating shutdown...", sig=sig_name)
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler, sig.name)

        # 16. Start all collector tasks
        log.info("Starting data collectors...")

        tasks = [
            asyncio.create_task(ws_manager.run(), name="websocket"),
            asyncio.create_task(kline_collector.run(), name="kline_collector"),
            asyncio.create_task(ticker_collector.run(), name="ticker_collector"),
            asyncio.create_task(funding_collector.run(), name="funding_collector"),
            asyncio.create_task(oi_collector.run(), name="oi_collector"),
            asyncio.create_task(position_monitor.run(), name="position_monitor"),
            asyncio.create_task(wallet_snapshotter.run(), name="wallet_snapshotter"),
            asyncio.create_task(daily_rollup.run(), name="daily_rollup"),
        ]

        log.info("Shadow is running. {n} tasks active.", n=len(tasks))

        # 15. Wait for shutdown signal
        await shutdown_event.wait()

    except Exception as e:
        log.error("Fatal error: {err}", err=str(e))
        import traceback
        log.error("{tb}", tb=traceback.format_exc())
    finally:
        # 16. Graceful shutdown: stop bot, cancel tasks, stop API, disconnect, close DB
        log.info("Shutting down...")

        # Stop Telegram bot first
        if telegram_app:
            try:
                await stop_bot(telegram_app)
            except Exception:
                pass

        # Stop API server
        try:
            await api_runner.cleanup()
        except Exception:
            pass

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if ws_manager:
            await ws_manager.disconnect()

        await db.close()
        log.info("Shadow shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
