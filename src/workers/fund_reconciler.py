"""Fund balance reconciliation worker.

Phase 5 (post-Layer-1 fix). Reconciles the local
``FundManager.AccountState`` view against Bybit's authoritative wallet
state every ``settings.fund_manager.reconcile_interval_seconds`` seconds.
Drift greater than the configured threshold raises a Telegram alert and
optionally (when explicit operator opt-in via ``reconcile_auto_correct``
is True) overwrites the local view from exchange.

Why a separate worker instead of folding the comparison into
``FundManager.update_state``?

  - Single-responsibility: ``update_state`` exists to refresh the local
    view from exchange + position service. Adding drift detection there
    would make a hot-path method also responsible for cross-source
    reconciliation, alerting, and operator opt-in semantics. Three
    concerns in one method invites future regressions.
  - Cadence independence: ``update_state`` runs once per ``check_interval``
    (60 s default) but the drift-detection cadence is logically separate
    — operators may want a slower or faster reconciler than the live
    state refresh.
  - Auditable wiring: a dedicated worker makes the reconciliation visible
    in WorkerManager + heartbeat census; folding it inside
    ``update_state`` hides it from operator dashboards.

Investigation: ``dev_notes/phase0_post_layer1_fixes/issue_5_balance_reconcile.md``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class FundReconciler(BaseWorker):
    """Periodic disk-vs-exchange wallet drift detector.

    Args:
        settings: Application settings. Reads:
            ``fund_manager.reconcile_interval_seconds`` (cadence),
            ``fund_manager.reconcile_drift_alert_threshold_pct`` (alert),
            ``fund_manager.reconcile_auto_correct`` (opt-in overwrite).
        db: DatabaseManager.
        services: ServiceContainer dict. Must contain ``account_service``
            (or ``account``) for the Bybit wallet read AND
            ``fund_manager`` for the local view.

    Tick produces:
        FUND_RECONCILE | bybit_total={t} bybit_available={a}
            local_cap={c} local_avail={la} drift_pct={d:.2f}
            auto_correct={true|false}
        FUND_RECONCILE_DRIFT (WARNING) on absolute drift > threshold
        FUND_RECONCILE_AUTO_CORRECT (WARNING) when auto_correct=True
            applies an overwrite
    """

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        services: dict[str, Any],
    ) -> None:
        interval = float(getattr(settings.fund_manager, "reconcile_interval_seconds", 60))
        super().__init__(
            name="fund_reconciler",
            interval_seconds=interval,
            settings=settings,
            db=db,
        )
        self._services = services
        self._threshold_pct = float(
            getattr(settings.fund_manager, "reconcile_drift_alert_threshold_pct", 5.0)
        )
        self._auto_correct = bool(
            getattr(settings.fund_manager, "reconcile_auto_correct", False)
        )
        # Phase 5 (post-Layer-1 fix). Daily summary roll-forward state.
        # We track the UTC date of the last summary emission so the
        # FUND_DAILY_SUMMARY line fires once per day on the first tick
        # crossing the date boundary.
        self._last_summary_date: str | None = None
        self._reconcile_corrections_today: int = 0
        self._day_start_balance: float | None = None

    async def tick(self) -> None:
        """One reconciliation cycle.

        Resilient to:
          - Missing account_service (paper-only): skip with INFO once
          - Missing fund_manager: skip with INFO once
          - Bybit API exception: emit FUND_RECONCILE_FAIL at WARNING
          - Position service exception: still emit FUND_RECONCILE for
            the wallet view; do not block on positions

        Never raises — failures degrade observability, not stability.
        """
        account_svc = self._services.get("account_service") or self._services.get("account")
        fund_manager = self._services.get("fund_manager")

        if not account_svc:
            log.debug(
                f"FUND_RECONCILE_SKIP | reason=no_account_service | {ctx()}"
            )
            return
        if not fund_manager:
            log.debug(
                f"FUND_RECONCILE_SKIP | reason=no_fund_manager | {ctx()}"
            )
            return

        # 1. Authoritative read from Bybit.
        try:
            bybit_account = await account_svc.get_wallet_balance()
            bybit_total = float(bybit_account.total_equity)
            bybit_available = float(bybit_account.available_balance)
        except Exception as e:
            log.warning(
                f"FUND_RECONCILE_FAIL | source=bybit err='{str(e)[:120]}' "
                f"| {ctx()}"
            )
            return

        # 2. Local view.
        local_state = getattr(fund_manager, "_account_state", None)
        if local_state is None:
            log.warning(
                f"FUND_RECONCILE_FAIL | source=local reason=no_account_state "
                f"| {ctx()}"
            )
            return
        local_cap = float(getattr(local_state, "trading_capital", 0.0))
        local_avail = float(getattr(local_state, "available", 0.0))
        local_total = float(getattr(local_state, "total_equity", 0.0))

        # 3. Drift = (local_total - bybit_total) / bybit_total in %.
        # Use total_equity as the comparison axis — that's the single
        # authoritative number both sides should agree on. Available
        # diverges by design (local subtracts unlock_pct + in_use).
        if bybit_total > 0:
            drift_pct = ((local_total - bybit_total) / bybit_total) * 100.0
        else:
            drift_pct = 0.0

        log.info(
            f"FUND_RECONCILE | bybit_total={bybit_total:.2f} "
            f"bybit_available={bybit_available:.2f} "
            f"local_total={local_total:.2f} local_cap={local_cap:.2f} "
            f"local_avail={local_avail:.2f} drift_pct={drift_pct:+.2f} "
            f"auto_correct={str(self._auto_correct).lower()} | {ctx()}"
        )

        # 4. Drift alerting.
        if abs(drift_pct) > self._threshold_pct:
            log.warning(
                f"FUND_RECONCILE_DRIFT | drift_pct={drift_pct:+.2f} "
                f"threshold_pct={self._threshold_pct} "
                f"local_total={local_total:.2f} bybit_total={bybit_total:.2f} "
                f"action={'auto_correct' if self._auto_correct else 'alert_only'} "
                f"| {ctx()}"
            )
            # Optional Telegram alert. Defensive: alert path failure must
            # not break the reconcile tick.
            try:
                tg = self._services.get("telegram") or self._services.get(
                    "telegram_bot"
                )
                if tg and hasattr(tg, "send_alert"):
                    await tg.send_alert(
                        f"FUND DRIFT detected: local total ${local_total:.2f} "
                        f"vs Bybit ${bybit_total:.2f} (drift {drift_pct:+.2f}% > "
                        f"{self._threshold_pct}%). "
                        f"{'Auto-correcting' if self._auto_correct else 'Alert only — manual review needed'}."
                    )
            except Exception as e:
                log.debug(
                    f"FUND_RECONCILE_ALERT_SEND_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

            # 5. Optional auto-correct (opt-in via config).
            if self._auto_correct:
                try:
                    local_state.total_equity = bybit_total
                    self._reconcile_corrections_today += 1
                    log.warning(
                        f"FUND_RECONCILE_AUTO_CORRECT | "
                        f"old_total={local_total:.2f} new_total={bybit_total:.2f} "
                        f"corrections_today={self._reconcile_corrections_today} "
                        f"| {ctx()}"
                    )
                except Exception as e:
                    log.error(
                        f"FUND_RECONCILE_AUTO_CORRECT_FAIL | err='{str(e)[:120]}' "
                        f"| {ctx()}"
                    )

        # 6. Daily summary roll-forward.
        self._maybe_emit_daily_summary(local_state, bybit_total)

    def _maybe_emit_daily_summary(self, local_state: Any, bybit_total: float) -> None:
        """Emit FUND_DAILY_SUMMARY once per UTC day on first tick of the day.

        Defensive: never raises. Failure degrades observability without
        affecting reconciliation correctness.
        """
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_summary_date is None:
                # First tick after worker start — seed without emitting
                # so the summary represents a full day rather than the
                # partial post-restart window.
                self._last_summary_date = today
                self._day_start_balance = bybit_total
                self._reconcile_corrections_today = 0
                return

            if today != self._last_summary_date:
                start = self._day_start_balance or bybit_total
                pnl = bybit_total - start
                log.info(
                    f"FUND_DAILY_SUMMARY | date={self._last_summary_date} "
                    f"start_balance={start:.2f} end_balance={bybit_total:.2f} "
                    f"pnl_realized={pnl:+.2f} "
                    f"reconcile_corrections={self._reconcile_corrections_today} "
                    f"| {ctx()}"
                )
                self._last_summary_date = today
                self._day_start_balance = bybit_total
                self._reconcile_corrections_today = 0
        except Exception as e:
            log.debug(
                f"FUND_DAILY_SUMMARY_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )
