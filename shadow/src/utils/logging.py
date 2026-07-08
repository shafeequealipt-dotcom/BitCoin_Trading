"""Loguru-based structured logging for Shadow.

Unlike the main trading project (which suppresses stdout for MCP protocol),
Shadow outputs to BOTH console and file since it runs as a standalone service.
"""

import os
import sys

from loguru import logger

# Log format — matches main project style
LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[component]}:{function}:{line} | {message}"
)


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure console + file logging. Must be called once at startup.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        log_dir: Absolute path to log directory (created if missing).
    """
    # Remove default Loguru handler
    logger.remove()

    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Console handler — stdout, colorized
    logger.add(
        sys.stdout,
        level=log_level.upper(),
        format=LOG_FORMAT,
        colorize=True,
    )

    # File handler — shadow.log, 50MB rotation, 7 days retention, zip
    logger.add(
        os.path.join(log_dir, "shadow.log"),
        level=log_level.upper(),
        format=LOG_FORMAT,
        rotation="50 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )


def get_logger(component: str):
    """Return a logger bound to a specific component name.

    Usage:
        log = get_logger("collector.websocket")
        log.info("Connected to Bybit WebSocket")

    Args:
        component: Component name for log identification.

    Returns:
        Loguru logger instance bound to the given component.
    """
    return logger.bind(component=component)
