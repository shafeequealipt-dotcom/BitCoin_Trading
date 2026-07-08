"""Trading Intelligence MCP — Claude Brain Entry Point.

The Brain automatically analyzes markets and makes trading decisions
by calling the Claude API on a schedule and when signals are detected.

Usage:
    python brain.py                # Start brain scheduler (runs forever)
    python brain.py --once         # Run single analysis and exit
    python brain.py --summary      # Generate daily summary and exit
"""

import asyncio
import argparse

from src.config.settings import Settings
from src.core.logging import setup_logging, get_logger
from src.database.connection import DatabaseManager
from src.brain import BrainManager


async def main(mode: str = "scheduler") -> None:
    """Start the Claude Brain.

    NOTE: Brain v1 (this file) is DEPRECATED. Brain v2 runs inside workers.py.
    This entry point is kept for manual one-off analysis only.
    Use 'sudo systemctl start trading-workers' for production.
    """
    if mode == "scheduler":
        print("WARNING: brain.py runs Brain v1 (legacy). Brain v2 runs inside workers.py.")
        print("Use 'sudo systemctl start trading-workers' instead.")
        print("brain.py is for manual one-off analysis only:")
        print("  python brain.py --once     # Run one analysis cycle")
        print("  python brain.py --summary  # Generate daily summary")
        print()

    settings = Settings._load_fresh()
    setup_logging(settings.general.log_level, settings.general.log_dir)
    log = get_logger("brain")

    if not settings.brain.enabled and mode == "scheduler":
        log.warning("Brain is DISABLED. Set [brain] enabled = true in config.toml")
        return

    log.info("=" * 60)
    log.info("Trading Intelligence MCP — Claude Brain Starting")
    log.info("Mode: {m}", m=mode)
    log.info("Model: {model}", model=settings.brain.model)
    log.info("=" * 60)

    db = DatabaseManager(
        settings.database.path,
        lock_wait_warn_ms=settings.database.db_lock_wait_threshold_ms,
        concurrency_model=settings.database.concurrency_model,
        reader_pool_size=settings.database.reader_pool_size,
    )
    manager = BrainManager(settings, db)
    await manager.initialize()

    try:
        if mode == "scheduler":
            await manager.start()
        elif mode == "once":
            result = await manager.run_once()
            log.info("Single analysis result: {r}", r=result)
        elif mode == "summary":
            result = await manager.run_daily_summary()
            log.info("Daily summary: {r}", r=result)
    except KeyboardInterrupt:
        log.info("Brain interrupted")
    finally:
        await manager.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Intelligence Brain")
    parser.add_argument("--once", action="store_true", help="Run single analysis and exit")
    parser.add_argument("--summary", action="store_true", help="Generate daily summary")
    args = parser.parse_args()

    mode = "once" if args.once else "summary" if args.summary else "scheduler"
    try:
        asyncio.run(main(mode))
    except KeyboardInterrupt:
        pass
