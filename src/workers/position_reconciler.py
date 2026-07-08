"""Position-count and margin-allocation reconciliation worker.

J1 Phase 3 Step B (2026-05-14) — sibling to :mod:`fund_reconciler`.
Closes the H4 reconciler-dimension gap identified in the J1 Phase 1
investigation (``dev_notes/seven_fixes/j1_phase1_reconciler_gaps.md``):
the existing fund_reconciler watches ``total_equity`` only, so a
position-count or margin-in-use divergence between local cache and
Bybit truth is structurally invisible.

This worker compares, every ``settings.fund_manager.reconcile_interval_seconds``
seconds (default 60s):

  1. ``positions`` table row count for the active ``exchange_mode``
     against the live ``get_positions_with_confirmation`` length.
  2. Bybit ``in_use`` margin (total_equity - available_balance)
     against the local view (local_total - local_available).

Emits ``POSITION_RECONCILE`` per tick (INFO) and
``POSITION_RECONCILE_DRIFT`` / ``FUND_INUSE_DRIFT`` at WARNING when a
drift persists for two consecutive ticks. The two-tick dwell guards
against fast open/close churn that would briefly flap the count.

Why a sibling worker instead of folding into ``fund_reconciler``:
``fund_reconciler``'s header explicitly advocates single-responsibility
per worker (``src/workers/fund_reconciler.py:12-23``); adding
position-count comparison to that worker would couple three concerns
(equity drift, position-count drift, margin drift) in one method.

Why pure observability (no auto-correct):
Per master prompt Rule 3, an auto-prune in the reconciler would be a
band-aid sweeper. The adapter-level prune (Step A) is the structural
fix. This worker surfaces signal for operator action; cleanup remains
operator-supervised via ``scripts/backfill_orphan_positions.py``.

Investigation: ``dev_notes/seven_fixes/j1_phase1_reconciler_gaps.md``.
"""

from __future__ import annotations

from typing import Any

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


# Dwell-time threshold (ticks). A drift must persist for this many
# consecutive ticks before WARNING-level alert fires; one-shot churn
# during fast open/close cycles does not alarm.
_DRIFT_DWELL_TICKS = 2

# Minimum absolute dollar gap that justifies a FUND_INUSE_DRIFT alert.
# A few thousand dollars of available-balance skew is typical during
# steady-state Bybit accounting and is not actionable.
_INUSE_DRIFT_MIN_USD = 1000.0

# Minimum proportional gap (fraction of bybit_total) that justifies
# a FUND_INUSE_DRIFT alert in addition to the absolute floor.
_INUSE_DRIFT_MIN_FRACTION = 0.005  # 0.5%


class PositionReconciler(BaseWorker):
    """Position-count and margin-in-use drift detector.

    Args:
        settings: Application settings. Reads
            ``fund_manager.reconcile_interval_seconds`` for cadence.
        db: DatabaseManager — used to query the positions table for
            the active mode's row count.
        services: ServiceContainer dict. Must contain at minimum:
            * ``position_service`` (or ``position``) for the live
              ``get_positions_with_confirmation`` call.
            * ``account_service`` (or ``account``) for wallet balance.
            * ``transformer`` for the active ``exchange_mode``.

    Tick produces:
        POSITION_RECONCILE               every tick (INFO)
        POSITION_RECONCILE_DRIFT         when count drift dwells (WARNING)
        FUND_INUSE_DRIFT                 when margin drift dwells (WARNING)
        POSITION_RECONCILE_SKIP          when prerequisites missing (DEBUG)
        POSITION_RECONCILE_FAIL          on exception (WARNING)
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        services: dict[str, Any],
    ) -> None:
        # Mirror fund_reconciler's cadence — they audit the same axis
        # at the same rate so operators can correlate the two streams.
        interval = float(
            getattr(settings.fund_manager, "reconcile_interval_seconds", 60),
        )
        super().__init__(
            name="position_reconciler",
            interval_seconds=interval,
            settings=settings,
            db=db,
        )
        self._services = services
        # Per-dimension dwell counters. Reset on any tick where the
        # corresponding drift is zero / within tolerance.
        self._count_drift_streak: int = 0
        self._inuse_drift_streak: int = 0

    async def tick(self) -> None:
        """One reconciliation cycle.

        Resilient to:
          * Missing position_service: skip with DEBUG once per absence
          * Missing account_service: skip with DEBUG once per absence
          * confirmed=False from the position service: skip count
            comparison this tick (unknown state)
          * Bybit API exception: emit POSITION_RECONCILE_FAIL at WARNING
          * DB query exception: emit POSITION_RECONCILE_FAIL at WARNING

        Never raises — failures degrade observability, not stability.
        """
        position_svc = (
            self._services.get("position_service")
            or self._services.get("position")
        )
        account_svc = (
            self._services.get("account_service")
            or self._services.get("account")
        )
        transformer = self._services.get("transformer")

        if position_svc is None:
            log.debug(
                f"POSITION_RECONCILE_SKIP | reason=no_position_service "
                f"| {ctx()}"
            )
            return

        # Resolve active exchange mode for the DB scope. When the
        # transformer is not wired (early boot), fall back to the
        # process-wide default tag so the reconciler still emits a
        # baseline signal rather than silently doing nothing.
        mode = ""
        if transformer is not None:
            try:
                mode = str(getattr(transformer, "current_mode", "") or "")
            except Exception:
                mode = ""
        if not mode:
            mode = "bybit_demo"

        # 1. Bybit truth — live position list with confirmation flag.
        try:
            live_result = await position_svc.get_positions_with_confirmation()
        except Exception as e:
            log.warning(
                f"POSITION_RECONCILE_FAIL | stage=live_fetch "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return

        if not getattr(live_result, "confirmed", False):
            log.debug(
                f"POSITION_RECONCILE_SKIP | reason=live_unknown_state "
                f"mode={mode} | {ctx()}"
            )
            return

        live_symbols = {p.symbol for p in (live_result.positions or ())}
        live_count = len(live_symbols)

        # 2. Local cache row count, scoped to the active mode.
        try:
            row = await self.db.fetch_one(
                "SELECT COUNT(*) AS n FROM positions "
                "WHERE size > 0 AND exchange_mode = ?",
                (mode,),
            )
        except Exception as e:
            log.warning(
                f"POSITION_RECONCILE_FAIL | stage=db_count "
                f"err='{str(e)[:120]}' | {ctx()}"
            )
            return

        db_count = int(row["n"]) if row and "n" in row else 0
        count_diff = db_count - live_count

        # 3. Optional margin-in-use comparison when account_service is
        # available. Wallet read failure is non-fatal — we still emit
        # the position-count reconcile line.
        bybit_total: float | None = None
        bybit_avail: float | None = None
        local_total: float | None = None
        local_avail: float | None = None
        inuse_diff: float | None = None
        if account_svc is not None:
            try:
                wallet = await account_svc.get_wallet_balance()
                bybit_total = float(wallet.total_equity)
                bybit_avail = float(wallet.available_balance)
            except Exception as e:
                log.debug(
                    f"POSITION_RECONCILE_WALLET_FAIL | "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )

            fund_manager = self._services.get("fund_manager")
            local_state = getattr(fund_manager, "_account_state", None)
            if local_state is not None:
                try:
                    local_total = float(getattr(local_state, "total_equity", 0.0))
                    local_avail = float(getattr(local_state, "available", 0.0))
                except Exception:
                    local_total = None
                    local_avail = None

            if (
                bybit_total is not None
                and bybit_avail is not None
                and local_total is not None
                and local_avail is not None
            ):
                inuse_bybit = bybit_total - bybit_avail
                inuse_local = local_total - local_avail
                inuse_diff = inuse_bybit - inuse_local

        # 4. Per-tick INFO line so operators see the steady-state values.
        _inuse_str = (
            f"{inuse_diff:+.2f}" if inuse_diff is not None else "n/a"
        )
        log.info(
            f"POSITION_RECONCILE | mode={mode} db_count={db_count} "
            f"live_count={live_count} count_diff={count_diff:+d} "
            f"inuse_diff={_inuse_str} "
            f"count_streak={self._count_drift_streak} "
            f"inuse_streak={self._inuse_drift_streak} | {ctx()}"
        )

        # 5. Count-drift dwell + alert.
        if count_diff != 0:
            self._count_drift_streak += 1
            if self._count_drift_streak >= _DRIFT_DWELL_TICKS:
                log.warning(
                    f"POSITION_RECONCILE_DRIFT | mode={mode} "
                    f"db_count={db_count} live_count={live_count} "
                    f"diff={count_diff:+d} streak={self._count_drift_streak} "
                    f"action=alert_only | {ctx()}"
                )
        else:
            self._count_drift_streak = 0

        # 6. Margin-in-use drift dwell + alert. Skipped when wallet read
        # failed or fund_manager lacks state.
        if inuse_diff is not None and bybit_total is not None and bybit_total > 0:
            min_abs = max(
                _INUSE_DRIFT_MIN_USD, bybit_total * _INUSE_DRIFT_MIN_FRACTION,
            )
            if abs(inuse_diff) > min_abs:
                self._inuse_drift_streak += 1
                if self._inuse_drift_streak >= _DRIFT_DWELL_TICKS:
                    log.warning(
                        f"FUND_INUSE_DRIFT | mode={mode} "
                        f"inuse_bybit={(bybit_total - bybit_avail):.2f} "
                        f"inuse_local={(local_total - local_avail):.2f} "
                        f"diff={inuse_diff:+.2f} threshold={min_abs:.2f} "
                        f"streak={self._inuse_drift_streak} "
                        f"action=alert_only | {ctx()}"
                    )
            else:
                self._inuse_drift_streak = 0
