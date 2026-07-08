"""PROTECTED tables — defense-in-depth runtime guard.

These tables hold APEX/TIAS cumulative learning data, the trade audit log,
strategic theses, and the Shadow virtual-exchange position record. The
previous cleanup fix wiped TIAS data and caused $19 in losses from blind
trading. This module exists so that any future regression that attempts a
destructive operation (DELETE / TRUNCATE / DROP) on these tables aborts
with a loud, debuggable exception instead of silently destroying state.

Usage (called inside DatabaseManager.execute / executemany):

    from src.database.protected_tables import (
        PROTECTED_TABLES,
        ProtectedTableViolation,
        assert_not_protected_destructive,
    )

    assert_not_protected_destructive(sql)   # raises if violated

A `force=True` escape hatch is provided for documented maintenance
scenarios. It MUST be passed at the call site — there is no default.
"""

from __future__ import annotations

import re
import traceback

from src.core.exceptions import TradingMCPError
from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("database")

# Tables that MUST NEVER be DELETE/TRUNCATE/DROP-targeted.
# Source of truth: CRITICAL_29_ISSUES_COMPLETE_OVERHAUL.md "PROTECTED TABLES" rule.
#
# Phase 3 (post-Layer-1 fix). ``trade_thesis`` was removed from this set
# (commit phase3-post-layer1-fixes) so the hourly CleanupWorker tick can
# prune theses past their TTL. Theses are short-lived journals (max
# hold ~30 min); 60-day retention preserves a 2-month learning window
# while bounding storage growth. Safety is preserved by a per-row TTL
# fence + ``status='closed'`` filter at the query site (see
# ``src/workers/cleanup_worker.py`` ``_cleanup_trade_thesis``); only
# closed theses past 60 days are eligible for deletion. See
# ``dev_notes/phase0_post_layer1_fixes/issue_3_db_protect_blocked.md``.
PROTECTED_TABLES: frozenset[str] = frozenset({
    "tias_results",
    "tias_analyses",
    "trade_intelligence",
    "trade_log",
    "trade_history",
    "thesis_store",
    "virtual_positions",
    "sniper_log",
})

# Destructive SQL kinds we guard against.
_DESTRUCTIVE_KINDS: tuple[str, ...] = ("DELETE", "TRUNCATE", "DROP")


class ProtectedTableViolation(TradingMCPError):
    """Raised when a destructive SQL targets a PROTECTED table without `force=True`."""


# Pre-compiled patterns: extract the targeted table name for each destructive kind.
# Matches the first identifier following the verb (and FROM/TABLE if present),
# allowing optional schema qualifier ("main."), backticks, double-quotes,
# and square brackets. Whitespace-tolerant.
_RE_DELETE = re.compile(
    r"""^\s*DELETE\s+FROM\s+
        (?:["`\[]?[A-Za-z_][\w]*["`\]]?\s*\.\s*)?     # optional schema.
        ["`\[]?(?P<table>[A-Za-z_][\w]*)["`\]]?       # table
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RE_TRUNCATE = re.compile(
    r"""^\s*TRUNCATE\s+(?:TABLE\s+)?
        (?:["`\[]?[A-Za-z_][\w]*["`\]]?\s*\.\s*)?
        ["`\[]?(?P<table>[A-Za-z_][\w]*)["`\]]?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_RE_DROP = re.compile(
    r"""^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?
        (?:["`\[]?[A-Za-z_][\w]*["`\]]?\s*\.\s*)?
        ["`\[]?(?P<table>[A-Za-z_][\w]*)["`\]]?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _classify(sql: str) -> tuple[str, str] | None:
    """Return (kind, table) if SQL is destructive, else None.

    Only the FIRST statement is considered (semicolons inside string
    literals are not parsed; this is the same convention as
    ``DatabaseManager.execute`` which executes a single statement).
    """
    stripped = sql.lstrip()
    if not stripped:
        return None
    head = stripped[:8].upper()
    if head.startswith("DELETE"):
        m = _RE_DELETE.match(stripped)
        return ("DELETE", m.group("table")) if m else ("DELETE", "")
    if head.startswith("TRUNCAT"):
        m = _RE_TRUNCATE.match(stripped)
        return ("TRUNCATE", m.group("table")) if m else ("TRUNCATE", "")
    if head.startswith("DROP"):
        m = _RE_DROP.match(stripped)
        return ("DROP", m.group("table")) if m else ("DROP", "")
    return None


def is_protected(table: str) -> bool:
    """Return True if `table` is in the PROTECTED set (case-insensitive)."""
    return table.lower() in PROTECTED_TABLES


# Phase 3 (post-Layer-1 fix). Modules walked over when attributing the
# caller of a blocked destructive query. The traceback module returns the
# full stack including this file and the database manager that hosts
# ``execute()``; both are uninteresting to operators trying to identify
# which scheduler emitted the offending statement.
_INTERNAL_FRAME_MARKERS: tuple[str, ...] = (
    "database/protected_tables.py",
    "database/connection.py",
)


def _extract_caller_frame() -> tuple[str, int, str]:
    """Return ``(file, line, method)`` for the first non-internal stack frame.

    Phase 3 (post-Layer-1 fix). When a blocked DELETE fires, operators
    need to know WHICH scheduler emitted it. Walks the traceback once on
    the slow path (only when blocking) and returns the first frame
    outside this module and ``connection.py``. Defensive: never raises;
    returns ``("unknown", 0, "unknown")`` on any failure.
    """
    try:
        stack = traceback.extract_stack(limit=20)
        # Walk from deepest upward; skip our own module + connection.py.
        for frame in reversed(stack):
            if not frame.filename:
                continue
            if any(marker in frame.filename for marker in _INTERNAL_FRAME_MARKERS):
                continue
            fname = frame.filename.rsplit("/", 1)[-1]
            return (fname, frame.lineno or 0, frame.name or "unknown")
    except Exception:
        pass
    return ("unknown", 0, "unknown")


def assert_not_protected_destructive(sql: str, *, force: bool = False) -> None:
    """Raise ProtectedTableViolation if SQL destructively targets a PROTECTED table.

    Phase 3 (post-Layer-1 fix). The blocked log line now carries
    ``caller_file``, ``caller_line``, ``caller_method`` so operators can
    instantly attribute a future block to its scheduler. Caller frame
    extraction runs only on the failing path (zero cost for accepted
    queries).

    Args:
        sql: The SQL statement about to be executed.
        force: When True, the guard logs the override but allows the call.
            Reserved for documented maintenance scripts ONLY.

    Raises:
        ProtectedTableViolation: when the SQL is DELETE/TRUNCATE/DROP on a
            protected table and ``force`` is False.
    """
    classified = _classify(sql)
    if classified is None:
        return
    kind, table = classified
    if not table or not is_protected(table):
        return
    if force:
        caller_file, caller_line, caller_method = _extract_caller_frame()
        log.warning(
            f"DB_PROTECT_FORCE | sql_kind={kind} table={table} "
            f"caller_file={caller_file} caller_line={caller_line} "
            f"caller_method={caller_method} "
            f"sql='{sql[:120].strip()}' | {ctx()}"
        )
        return
    caller_file, caller_line, caller_method = _extract_caller_frame()
    log.error(
        f"DB_PROTECT_BLOCKED | sql_kind={kind} table={table} "
        f"caller_file={caller_file} caller_line={caller_line} "
        f"caller_method={caller_method} "
        f"sql='{sql[:120].strip()}' | {ctx()}"
    )
    raise ProtectedTableViolation(
        f"Destructive {kind} on PROTECTED table '{table}' refused. "
        f"This table holds cumulative learning / audit data and must "
        f"never be wiped. Pass force=True only with explicit authorization.",
        details={
            "sql_kind": kind,
            "table": table,
            "sql_excerpt": sql[:200],
            "caller_file": caller_file,
            "caller_line": caller_line,
            "caller_method": caller_method,
        },
    )
