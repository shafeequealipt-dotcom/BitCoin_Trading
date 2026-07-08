"""Correlation ID system for enterprise diagnostic logging.

Three async-safe correlation IDs link events across the entire trade lifecycle:
  - Decision ID (did): One per Claude strategic review cycle
  - Trade ID (tid): One per trade from execution to close
  - Watchdog ID (wid): One per watchdog tick

Usage:
    from src.core.log_context import ctx, new_decision_id, new_trade_id

    did = new_decision_id()           # At start of strategist cycle
    tid = new_trade_id("BTCUSDT")     # When rule engine approves a trade
    log.info(f"ORDER_OK | sym=BTCUSDT | {ctx()}")
    # Output: ORDER_OK | sym=BTCUSDT | did=d-1711641600123 tid=t-BTCUSDT-1711641605000
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar

# Phase 12 (post-Layer-1 fix): centralised truncation caps for log
# fields. Pre-fix, error messages embedded in logs were truncated to
# inconsistent widths (40, 80, 100, 120, 150, 200) across the codebase
# — when a log line wrapped or got dropped at a sink boundary, the
# specific cap was hard to identify. These constants document the
# intent at the field level so new call sites pick the right one.
#
# Usage:
#   from src.core.log_context import MAX_ERR_LEN
#   log.warning(f"...err={str(e)[:MAX_ERR_LEN]}...")
#
# Migration is opportunistic — existing call sites with literals
# stay as-is until they're touched for another reason.
MAX_ERR_LEN_SHORT = 80     # tight: per-symbol per-tick logs (kline, regime)
MAX_ERR_LEN = 120          # default: most warning/error lines
MAX_ERR_LEN_LONG = 200     # detail: subprocess stderr tail, prompt rejects

# ── Context variables (async-safe, per-coroutine isolation) ──

_decision_id: ContextVar[str] = ContextVar("decision_id", default="")
_trade_id: ContextVar[str] = ContextVar("trade_id", default="")
_watchdog_id: ContextVar[str] = ContextVar("watchdog_id", default="")
_strategy_id: ContextVar[str] = ContextVar("strategy_id", default="")


# ── Generators ──

def new_decision_id() -> str:
    """Generate a new decision ID for a Claude strategic review cycle."""
    did = f"d-{int(time.time() * 1000)}"
    _decision_id.set(did)
    return did


def new_trade_id(symbol: str) -> str:
    """Generate a new trade ID when rule engine approves a trade."""
    tid = f"t-{symbol}-{int(time.time() * 1000)}"
    _trade_id.set(tid)
    return tid


def new_watchdog_id() -> str:
    """Generate a new watchdog ID at the start of each watchdog tick."""
    wid = f"w-{int(time.time() * 1000)}"
    _watchdog_id.set(wid)
    return wid


def new_strategy_id() -> str:
    """Generate a new strategy cycle ID at the start of each strategy worker tick."""
    sid = f"s-{int(time.time() * 1000)}"
    _strategy_id.set(sid)
    return sid


# ── Getters ──

def get_did() -> str:
    """Return current decision ID or empty string."""
    return _decision_id.get("")


def get_tid() -> str:
    """Return current trade ID or empty string."""
    return _trade_id.get("")


def get_wid() -> str:
    """Return current watchdog ID or empty string."""
    return _watchdog_id.get("")


def get_sid() -> str:
    """Return current strategy cycle ID or empty string."""
    return _strategy_id.get("")


# ── Setters (for propagation across coroutines) ──

def set_did(value: str) -> None:
    """Set decision ID (propagate from parent coroutine)."""
    _decision_id.set(value)


def set_tid(value: str) -> None:
    """Set trade ID (propagate from parent coroutine)."""
    _trade_id.set(value)


def set_wid(value: str) -> None:
    """Set watchdog ID (propagate from parent coroutine)."""
    _watchdog_id.set(value)


def set_sid(value: str) -> None:
    """Set strategy cycle ID (propagate from parent coroutine)."""
    _strategy_id.set(value)


# ── Context string for log lines ──

# ── HIGH-9 fix (2026-05-09) — scoped tid context manager ──

@contextmanager
def tid_scope(symbol: str, role: str = ""):
    """Temporarily set the trade ID for this block; restore on exit.

    HIGH-9 fix: workers that iterate over multiple symbols within one
    tick (profit_sniper, position_watchdog) historically called set_tid
    inside Loop 1 but not Loop 2/3, leaving the LAST symbol's tid
    persistent. Subsequent loops' log lines inherited the stale tid,
    producing the audit's RENDERUSDT events tagged tid=t-ATOMUSDT-sniper.

    Use as:

        for pos in positions:
            with tid_scope(pos.symbol, "wd"):
                await self.position_service.close_position(...)
                # logs inside the block see tid=t-{pos.symbol}-wd

    The token-restore semantics ensure that on exit (normal or
    exception), the tid reverts to whatever was set BEFORE entering the
    scope. This eliminates leakage past loop iterations and past worker
    tick boundaries.

    Args:
        symbol: The symbol the work in this block is for.
        role: Optional suffix (e.g., "sniper", "wd", "ext"). Empty role
              produces tid="t-{symbol}".
    """
    suffix = f"-{role}" if role else ""
    new_tid = f"t-{symbol}{suffix}"
    token = _trade_id.set(new_tid)
    try:
        yield new_tid
    finally:
        _trade_id.reset(token)


def ctx() -> str:
    """Return compact context string for log line suffixes.

    Only includes non-empty IDs. Returns 'no_ctx' if all empty.

    Examples:
        'did=d-1711641600123'
        'did=d-1711641600123 tid=t-BTCUSDT-1711641605000'
        'wid=w-1711641610000 tid=t-ETHUSDT-1711641605000'
        'no_ctx'
    """
    parts = []
    d = _decision_id.get("")
    t = _trade_id.get("")
    w = _watchdog_id.get("")
    s = _strategy_id.get("")
    if d:
        parts.append(f"did={d}")
    if t:
        parts.append(f"tid={t}")
    if w:
        parts.append(f"wid={w}")
    if s:
        parts.append(f"sid={s}")
    return " ".join(parts) if parts else "no_ctx"
