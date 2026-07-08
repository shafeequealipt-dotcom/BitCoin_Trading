"""Trading Intelligence MCP — Background Workers Entry Point.

Starts all background workers that continuously collect market data,
news, sentiment, and compute trading signals 24/7.

Usage:
    python workers.py                      # Start all workers
    python workers.py --workers price,news # Start specific workers only
"""

import asyncio
import atexit
import datetime
import os
import signal
import sys

from src.config.settings import Settings
from src.config.validators import validate_config
from src.core.logging import setup_logging, get_logger
from src.database.connection import DatabaseManager
from src.workers.manager import WorkerManager


# Phase 30 (Y-29) — module-level shutdown hooks.
# The brief observed an unexplained 2h56m outage with no shutdown line
# in the log. Pre-fix: a SIGTERM (e.g., systemctl stop) bypassed the
# Python try/except/finally and the process exited silently. Post-fix:
# both atexit (any normal exit, including unhandled exceptions) and
# explicit SIGTERM/SIGINT handlers emit a CRITICAL line so post-mortem
# always knows WHY the worker stopped.
#
# Loguru file sinks are configured with ``enqueue=True`` for thread
# safety, which means writes go through a background queue. SIGTERM
# can kill the process before the queue flushes — so the WORKER_SIGNAL
# line would never reach disk via loguru alone. We therefore mirror
# the message via a synchronous fd write to BOTH stderr (unbuffered)
# and the routed workers.log file. Belt-and-braces: the log call still
# runs (best-effort), and the fd write guarantees the operator sees
# the line even if loguru's queue thread is killed mid-flight.
_SHUTDOWN_LOG_PATH: str | None = None


def _install_shutdown_hooks() -> None:
    log = get_logger("worker")

    # Resolve the workers.log path so we can append synchronously when the
    # loguru queue might be unavailable. Fall back to stderr-only if the
    # log dir isn't writable.
    global _SHUTDOWN_LOG_PATH
    try:
        _settings = Settings._load_fresh()
        _SHUTDOWN_LOG_PATH = os.path.join(
            _settings.general.log_dir, "workers.log",
        )
    except Exception:
        _SHUTDOWN_LOG_PATH = None

    def _sync_emit(level: str, msg: str) -> None:
        """Synchronous fd write — survives loguru queue shutdown."""
        ts = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        line = f"{ts} | {level:<8} | workers:_sync_emit | {msg}\n"
        # stderr — visible to systemd / supervisor regardless of file IO.
        try:
            os.write(2, line.encode())
        except Exception:
            pass
        # workers.log — append synchronously, bypasses loguru's queue.
        if _SHUTDOWN_LOG_PATH is not None:
            try:
                with open(_SHUTDOWN_LOG_PATH, "a", buffering=1) as f:
                    f.write(line)
            except Exception:
                pass

    def _atexit_log() -> None:
        # atexit fires during clean interpreter shutdown; the loguru queue
        # has time to flush, so we use both paths for redundancy.
        try:
            log.critical(
                "WORKER_SHUTDOWN | reason=atexit | clean exit recorded"
            )
        except Exception:
            pass
        _sync_emit(
            "CRITICAL",
            "WORKER_SHUTDOWN | reason=atexit | clean exit recorded",
        )

    atexit.register(_atexit_log)

    def _sig_handler(signum: int, _frame) -> None:
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = str(signum)
        msg = (
            f"WORKER_SIGNAL | sig={sig_name} signum={signum} | "
            f"shutdown initiated by external signal"
        )
        # Sync-write FIRST so the line lands even if the loguru queue
        # gets dropped during interpreter shutdown.
        _sync_emit("CRITICAL", msg)
        try:
            log.critical(msg)
        except Exception:
            pass
        # Re-raise as KeyboardInterrupt so the main try/finally still
        # runs manager.stop_all(). KeyboardInterrupt is the canonical
        # asyncio-friendly way to unwind cleanly from inside the loop.
        raise KeyboardInterrupt(f"signal {sig_name}")

    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_s, _sig_handler)
        except (ValueError, OSError):
            # Some environments (subinterpreters / non-main threads)
            # forbid signal.signal() — fall back silently. atexit still
            # records the eventual exit either way.
            pass


def _load_persisted_universe(settings, log, state_path: str = "data/universe_state.json") -> None:
    """Phase 2 (daily universe refresh): at boot, if the feature is enabled
    and a previously-refreshed universe was persisted, load it so the dynamic
    universe survives restarts. The hand-curated config.toml [universe]
    watch_list stays the seed and fallback. Mirrors the data/layer_state.json
    read pattern. No-op when the feature is disabled, the file is absent, or
    the saved list is invalid (validation in UniverseSettings.__post_init__).
    """
    import json
    from pathlib import Path
    from src.core.universe_refresh import rebuild_universe_settings

    try:
        if not settings.universe.refresh.enabled:
            return
        state_file = Path(state_path)
        if not state_file.exists():
            return
        data = json.loads(state_file.read_text())
        wl = data.get("watch_list", [])
        if not isinstance(wl, list) or len(wl) < 10:
            log.warning(
                "UNIVERSE_BOOT_LOAD_SKIP | reason=invalid_or_too_small n={n}",
                n=(len(wl) if isinstance(wl, list) else -1),
            )
            return
        settings.universe = rebuild_universe_settings(settings.universe, wl)
        log.info(
            "UNIVERSE_BOOT_LOAD | loaded {n} coins from persisted state ts={ts}",
            n=len(wl), ts=data.get("timestamp", "?"),
        )
    except Exception as e:
        log.warning("UNIVERSE_BOOT_LOAD_FAIL | err={err}", err=str(e)[:150])


async def main() -> None:
    """Initialize and start all background workers."""
    settings = Settings._load_fresh()

    setup_logging(settings.general.log_level, settings.general.log_dir)
    log = get_logger("worker")

    # Phase 30 (Y-29): wire shutdown hooks AFTER logging is configured
    # so the WORKER_SHUTDOWN / WORKER_SIGNAL lines hit the routed
    # workers.log instead of falling through to general.log.
    _install_shutdown_hooks()

    log.info("=" * 60)
    log.info("Trading Intelligence MCP — Workers Starting")
    log.info("Mode: {mode}", mode=settings.general.mode)
    log.info("Symbols: {syms}", syms=settings.bybit.default_symbols)
    log.info("=" * 60)

    warnings = validate_config(settings)
    for w in warnings:
        log.warning("Config: {w}", w=w)

    # Phase 2: load any persisted dynamic universe BEFORE workers start, so
    # KlineWorker/ScannerWorker (which read settings.universe each tick) and
    # the scanner pick it up from the first cycle.
    _load_persisted_universe(settings, log)

    db = DatabaseManager(
        settings.database.path,
        lock_wait_warn_ms=settings.database.db_lock_wait_threshold_ms,
        concurrency_model=settings.database.concurrency_model,
        reader_pool_size=settings.database.reader_pool_size,
    )
    manager = WorkerManager(settings, db)

    try:
        await manager.initialize()
        await manager.start_all()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    except Exception as e:
        import traceback
        log.error("Worker manager error: {err}\n{tb}", err=str(e), tb=traceback.format_exc())
    finally:
        await manager.stop_all()
        log.info("Workers shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
