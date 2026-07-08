"""Cross-process Transformer state reader (P9 of P1-P10).

The MCP server is a separate process from the worker process. The
worker process owns the canonical Transformer instance; the MCP server
constructs its own services. Pre-P9, MCP exchange-tools queried
``services["transformer"]`` which was never populated — every
read-state tool returned ``"Transformer not available"`` and trading
tools (``get_account_info``, ``get_positions``) silently routed to the
live Bybit cluster regardless of the worker process's actual mode
(audit L10-G1, L10-G2).

This module provides a thin read-only adapter that satisfies the
exchange-tools' expected interface:

- ``current_mode`` — read from the ``transformer_state`` SQLite table
  (WAL-mode safe across processes), 5s in-memory cache to avoid a
  per-tool-call DB hit.
- ``mode_label`` — derived from current_mode + cached
  ``transformer_state.is_switching`` flag.
- ``is_switching`` — derived from the same state row.
- ``get_current_equity()`` / ``get_open_positions_summary()`` /
  ``get_target_equity()`` — async; route to the appropriate
  AccountService / PositionService instance based on cached mode.

Construction in ``src/mcp/server.py:_init_services`` is one call; the
adapter holds references to the per-mode services (Shadow + BybitDemo
+ Bybit) so it can route reads without re-creating them per call.

Out of scope (intentionally): write paths (``set_switching_state``,
``record_switch``, ``persist_target_mode``). Those are owned by the
worker process's Transformer; MCP-driven switches go through the
``ExchangeSwitcher`` which writes through ``transformer_state``
directly without needing to call into a Transformer instance from MCP.
"""

from __future__ import annotations

import time
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("worker")

# 5-second cache TTL. Operator switching is restart-based + relatively
# rare (minutes-to-hours apart); a 5-second staleness window is
# acceptable for MCP read paths and saves a per-tool-call DB hit.
_STATE_CACHE_TTL_S: float = 5.0


class TransformerStateSnapshot:
    """Cached point-in-time view of Transformer state.

    Mode + is_switching + last_switched_at, refreshed from the
    ``transformer_state`` SQLite table on read when the cache age
    exceeds ``_STATE_CACHE_TTL_S``. Read-only; the worker process owns
    the write path.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._cached_mode: str = "shadow"
        self._cached_is_switching: bool = False
        self._cached_last_switched_at: str | None = None
        self._cached_at: float = 0.0  # monotonic seconds; 0 → cold

    async def refresh_if_stale(self) -> None:
        """Refresh the cache if the TTL has elapsed.

        Defensive: a DB read failure leaves the previous cache intact
        rather than flipping to a default. For MCP read paths a
        fractionally-stale answer is preferable to a misleading
        "shadow" fallback.
        """
        if time.monotonic() - self._cached_at < _STATE_CACHE_TTL_S:
            return
        try:
            row = await self._db.fetch_one(
                "SELECT current_mode, is_switching, last_switched_at "
                "FROM transformer_state WHERE id = 1"
            )
            if row:
                self._cached_mode = str(row["current_mode"] or "shadow")
                self._cached_is_switching = bool(row["is_switching"])
                self._cached_last_switched_at = (
                    str(row["last_switched_at"])
                    if row["last_switched_at"] is not None
                    else None
                )
            self._cached_at = time.monotonic()
        except Exception as e:
            log.debug(
                f"TRANSFORMER_STATE_READ_FAIL | err='{str(e)[:120]}' "
                f"using_cache age_s={time.monotonic() - self._cached_at:.1f} | {ctx()}"
            )

    @property
    def current_mode(self) -> str:
        return self._cached_mode

    @property
    def is_switching(self) -> bool:
        return self._cached_is_switching

    @property
    def last_switched_at(self) -> str | None:
        return self._cached_last_switched_at


class MCPTransformerAdapter:
    """Read-only Transformer-shaped adapter for the MCP server.

    Exposes the subset of Transformer's public interface that the MCP
    exchange-tools (and downstream trading tools) rely on. Routes
    account / position reads to the appropriate service instance based
    on the cached mode read from ``transformer_state``.

    Args:
        db: DatabaseManager pointing at trading.db (WAL-mode required
            for cross-process reads — verified at Phase 0).
        services_per_mode: Dict mapping mode name (``"shadow"``,
            ``"bybit_demo"``, ``"bybit"``) → dict of service instances
            (``"account"``, ``"position"``, ``"order"``). Populated by
            the MCP server's _init_services. Modes without configured
            services fall back to the live Bybit account (matches
            pre-P9 behaviour for unwired modes — degraded but not
            broken).
    """

    _MODE_LABELS: dict[str, str] = {
        "shadow": "Shadow",
        "bybit_demo": "Bybit Demo",
        "bybit": "Bybit Live",
    }

    def __init__(
        self,
        db: DatabaseManager,
        services_per_mode: dict[str, dict[str, Any]],
    ) -> None:
        self._snapshot = TransformerStateSnapshot(db)
        self._services_per_mode = services_per_mode

    # ─── Cached state properties ──────────────────────────────────────

    @property
    def current_mode(self) -> str:
        return self._snapshot.current_mode

    @property
    def is_switching(self) -> bool:
        return self._snapshot.is_switching

    @property
    def mode_label(self) -> str:
        if self._snapshot.is_switching:
            return f"{self._MODE_LABELS.get(self._snapshot.current_mode, self._snapshot.current_mode)} (switching)"
        return self._MODE_LABELS.get(self._snapshot.current_mode, self._snapshot.current_mode)

    @property
    def is_shadow(self) -> bool:
        return self._snapshot.current_mode == "shadow"

    @property
    def is_bybit_demo(self) -> bool:
        return self._snapshot.current_mode == "bybit_demo"

    @property
    def is_bybit(self) -> bool:
        return self._snapshot.current_mode == "bybit"

    # ─── Async read methods (route by cached mode) ────────────────────

    async def get_current_equity(self) -> dict[str, Any]:
        await self._snapshot.refresh_if_stale()
        mode = self._snapshot.current_mode
        try:
            services = self._services_per_mode.get(mode, {})
            acc_svc = services.get("account")
            if acc_svc is None:
                return {"equity": None, "mode": mode}
            bal = await acc_svc.get_wallet_balance()
            return {
                "equity": getattr(bal, "total_equity", None),
                "available": getattr(bal, "available_balance", None),
                "mode": mode,
            }
        except Exception as e:
            return {"equity": None, "error": str(e), "mode": mode}

    async def get_open_positions_summary(self) -> dict[str, Any]:
        await self._snapshot.refresh_if_stale()
        mode = self._snapshot.current_mode
        try:
            services = self._services_per_mode.get(mode, {})
            pos_svc = services.get("position")
            if pos_svc is None:
                return {"count": 0, "positions": []}
            positions = await pos_svc.get_positions()
            return {
                "count": len(positions),
                "positions": [
                    {
                        "symbol": p.symbol,
                        "side": str(p.side),
                        "pnl_usd": getattr(p, "unrealized_pnl", None),
                    }
                    for p in positions
                ],
            }
        except Exception as e:
            return {"count": 0, "positions": [], "error": str(e)}

    async def get_target_equity(self, target_mode: str) -> dict[str, Any]:
        """Get equity from a specific (possibly non-active) mode.

        Used by validate_switch to probe target reachability before a
        switch. No state refresh needed — target_mode is the caller's
        explicit choice, not derived from cached state.
        """
        try:
            services = self._services_per_mode.get(target_mode, {})
            acc_svc = services.get("account")
            if acc_svc is None:
                return {"equity": None, "mode": target_mode, "error": "service_not_configured"}
            bal = await acc_svc.get_wallet_balance()
            return {
                "equity": getattr(bal, "total_equity", None),
                "mode": target_mode,
            }
        except Exception as e:
            return {"equity": None, "error": str(e), "mode": target_mode}
