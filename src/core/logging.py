"""Loguru-based file-only logging setup.

CRITICAL: MCP uses stdio protocol. Any stdout/stderr output breaks the protocol.
This module removes Loguru's default stderr handler and routes ALL logs to files.
"""

import os

from loguru import logger

# Log format used across all file sinks
LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
    "{name}:{function}:{line} | {message}"
)

# Rotation and retention defaults
LOG_ROTATION = "10 MB"
LOG_RETENTION = "7 days"

# Component-to-file routing (many components can share one file)
#
# Every component string passed to get_logger in src/ MUST appear as a key
# below, otherwise its output falls through _default_filter and leaks to
# general.log. tests/test_logging_routing.py enforces this at CI-time.
COMPONENT_ROUTING: dict[str, str] = {
    "mcp": "mcp.log",
    "worker": "workers.log",
    "brain": "brain.log",
    "claude_code": "brain.log",
    "strategist": "brain.log",
    "rule_engine": "workers.log",
    "trading": "workers.log",
    "sl_tp_validator": "workers.log",
    "sl_gateway": "workers.log",
    "coordinator": "workers.log",
    "pnl_reconciler": "workers.log",     # Phase 1D PnL-truth lifecycle (PNL_PROVISIONAL_BOOKED / PNL_RECONCILE_DONE / PNL_RECONCILE_EXHAUSTED)
    "data_lake": "workers.log",
    "thesis_manager": "workers.log",
    "enforcer": "workers.log",
    "strategies": "workers.log",
    "intelligence": "workers.log",
    "analysis": "workers.log",
    "fund_manager": "workers.log",
    "tiered_capital": "workers.log",
    "risk": "workers.log",
    # ── Previously unrouted logical-module components. Each one had emitted
    # INFO logs that silently landed in general.log, invisible to any
    # verification script that greps workers.log. Keeping component names
    # as their logical module (not flattening to the parent directory name)
    # matches the time_decay_sl / xray precedent.
    "time_decay_sl": "workers.log",       # risk submodule
    "layer4_protection": "workers.log",   # risk submodule — Layer 4 Realignment Phase 4.1 (2026-05-06): SNIPER_PROTECTED, L4_PROT_AGE_ERR
    "volatility_profile": "workers.log",  # analysis submodule — VOL_PROFILE tag
    "factory": "workers.log",             # AI strategy discovery (20 files)
    "portfolio": "workers.log",           # Kelly / allocator / correlation (8 files)
    "trade_recorder": "workers.log",      # core trade lifecycle events
    "trading_mode": "workers.log",        # core mode-transition events
    "shadow": "workers.log",              # virtual exchange adapter
    "bybit_demo": "workers.log",          # Bybit demo (paper) execution adapter — same role as shadow
    "strategy": "workers.log",            # AI-generated strategies per src/factory/prompts/generation_prompt.py:8
    # ── Other workers
    "event_buffer": "workers.log",
    "urgent_queue": "workers.log",
    "layer_manager": "workers.log",
    "core": "workers.log",
    "tias": "workers.log",
    "apex": "workers.log",
    "sentinel": "workers.log",
    "xray": "workers.log",
    # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — SizingDerivation
    # logger emits SIZE_DERIVATION events from src/core/sizing_orchestrator.py
    # right after the enforcer runs in strategy_worker. Routes alongside
    # the other sizing-pipeline telemetry (apex, fund_manager, tiered_capital)
    # so operators can grep one file for the full size derivation chain.
    "sizing": "workers.log",
    # Layer 1 restructure Phase 1 — CycleTracker emits CYCLE_*,
    # CYCLE_METRICS_*, CYCLE_RESUME_* tags. Routes alongside other
    # Layer 1 telemetry into workers.log so operators can grep one
    # file for the full per-cycle latency story.
    "cycle_tracker": "workers.log",
    # Phase 11 (dead-workers fix) — WorkerLivenessTracker /
    # WorkerLivenessWatchdog. Emits WORKER_NEVER_TICKED,
    # WORKER_TICK_OVERDUE, WORKER_LIVENESS_HEARTBEAT, plus
    # registration / deregister INFO. Same destination as the rest
    # of the worker telemetry so operators have one grep target.
    "worker_liveness": "workers.log",
    # Price-display precision fix — PriceFormatter emits PRICE_FORMATTER_WIRED
    # (boot sentinel) and PRICE_FMT_FALLBACK (deduped DEBUG). Routes with the
    # rest of the sizing/display telemetry so operators grep one file.
    "price_formatter": "workers.log",
    # System 2 (observability) — per-second open-trade price path logger. Its
    # PRICE_PATH data points get a dedicated, rotated single-purpose file so the
    # exit-calibration replay tool reads one clean source (PricePathLogger's
    # operational lines still route to workers.log via the "worker" component).
    "price_path": "price_path.log",
    # PLACEMENT_FORENSIC lines get a dedicated, rotated single-purpose file so the
    # placeability-mechanism analysis reads one clean source (the gateway's
    # operational lines still route to workers.log via the "sl_gateway" component).
    "placement_forensic": "placement_forensic.log",
    # ── Infrastructure / operator-facing (general.log by design)
    "database": "general.log",
    "alerts": "general.log",
    "telegram": "general.log",
    "control_handler": "general.log",     # telegram handler submodule
    "dashboard": "general.log",           # telegram handler submodule
}
DEFAULT_LOG_FILE = "general.log"

# Back-compat alias
COMPONENT_FILES = COMPONENT_ROUTING


def _grouped_file_filter(components: frozenset[str]):
    """Create a filter that matches ANY component routed to the same file."""
    def _filter(record: dict) -> bool:
        return record["extra"].get("component") in components
    return _filter


def _default_filter(record: dict) -> bool:
    """Filter for logs that don't match any routed component."""
    component = record["extra"].get("component", "")
    return component not in COMPONENT_ROUTING


def setup_logging(log_level: str = "INFO", log_dir: str = "data/logs") -> None:
    """Configure file-only logging with component-based routing.

    MUST be called before any logging occurs. Removes all default handlers.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        log_dir: Directory for log files (created if missing).
    """
    # FIRST: Remove default stderr handler — protects MCP stdio protocol
    logger.remove()

    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Group components by target file → one sink per unique file
    file_groups: dict[str, set[str]] = {}
    for comp, fname in COMPONENT_ROUTING.items():
        file_groups.setdefault(fname, set()).add(comp)

    for filename, components in file_groups.items():
        logger.add(
            os.path.join(log_dir, filename),
            level=log_level.upper(),
            format=LOG_FORMAT,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            filter=_grouped_file_filter(frozenset(components)),
            enqueue=True,  # Thread-safe
            backtrace=True,
            diagnose=False,  # Don't leak variable values in production
        )

    # Add catch-all sink for general/untagged logs
    logger.add(
        os.path.join(log_dir, DEFAULT_LOG_FILE),
        level=log_level.upper(),
        format=LOG_FORMAT,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        filter=_default_filter,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )


def get_logger(component: str) -> logger.__class__:
    """Return a logger bound to a specific component.

    Usage:
        log = get_logger("worker")
        log.info("Fetching market data...")

    Args:
        component: Component name (e.g. "mcp", "worker", "brain").

    Returns:
        Loguru logger instance bound to the given component.
    """
    return logger.bind(component=component)
