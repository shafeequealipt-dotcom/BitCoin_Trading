"""PnL reconciler — the indexer-lag safety net for the PnL-truth fix (2026-06-07).

Phase 1 of IMPLEMENT_PNL_TRUTH_AND_BUG_CALIBRATION_FIX. Parts A/B/C make the
dominant WS close path and the watchdog/sniper self-close paths book the
exchange-authoritative net whenever Bybit's ``/v5/position/closed-pnl`` row is
already indexed at close time (the adapter's in-call retry covers the common
short lag). This worker is the TAIL safety net: when a close booked from the
local ws fallback because the exchange row was not yet indexed, it is captured
here, retried against the exchange on a bounded schedule, and — once the
authoritative figure arrives — the corrected net is fanned out to the
idempotent sinks via ``coordinator.fire_reconcile``.

Design guarantees:

* No double-count. The reconcile channel only carries the idempotent sinks
  (data_lake/trade_history upsert-by-trade_id, thesis update-by-order_id, TIAS
  update_outcome-by-trade_id). The enforcer streak, pnl-manager running total
  and re-entry cooldown are NOT on that channel, so a correction never
  re-mutates them.
* Going-forward only. The reconcile UPDATE corrects the one just-closed row by
  its stable key; it never rewrites history or touches other rows.
* Never paint a missing-exchange close as final truth. A provisional close
  carries ``price_source`` other than ``exchange_authoritative`` until it is
  reconciled (then ``exchange_authoritative_reconciled``); the
  ``PNL_PROVISIONAL_BOOKED`` / ``PNL_RECONCILE_DONE`` / ``PNL_RECONCILE_EXHAUSTED``
  logs make the lifecycle auditable.
* Bybit-demo only. Shadow commits closes synchronously (no indexer lag), so its
  closes are already authoritative and are skipped here.
"""
from __future__ import annotations

import time
from collections import deque

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("pnl_reconciler")

_AUTH_PREFIX = "exchange_authoritative"


class PnLReconciler(BaseWorker):
    """Bounded background reconciler for provisionally-booked closes."""

    def __init__(self, settings: Settings, db: DatabaseManager, services: dict) -> None:
        bd = getattr(settings, "bybit_demo", None)
        interval = float(getattr(bd, "close_pnl_reconcile_interval_s", 1.0) or 1.0)
        super().__init__("pnl_reconciler", interval, settings, db)
        self._services = services
        self._coord = services.get("trade_coordinator")
        self._enabled = bool(getattr(bd, "close_pnl_reconcile", True)) if bd else False
        self._provisional = bool(getattr(bd, "close_pnl_provisional", True)) if bd else False
        self._max_attempts = int(getattr(bd, "close_pnl_reconcile_max_attempts", 10) or 10)
        self._budget_s = float(getattr(bd, "close_pnl_reconcile_total_budget_s", 30.0) or 30.0)
        # Phase 1 residual fix (2026-06-08) — exit-plausibility gate tolerance.
        # The reconcile channel bypasses on_trade_closed's staleness gate, and
        # resolve_authoritative_pnl's qty gate is inert here (the trade state is
        # already popped by the time this background worker runs), so a stale
        # same-symbol closed-pnl row matched qty-only could be booked as a
        # phantom. A resolved row whose exit diverges from the provisional
        # close's exit by more than this percent is rejected. 0 disables.
        self._max_exit_div_pct = (
            float(getattr(bd, "close_pnl_reconcile_max_exit_divergence_pct", 3.0) or 0.0)
            if bd else 0.0
        )
        self._jobs: deque[dict] = deque()
        self._registered = False
        # Register the capture callback on the coordinator's normal close
        # channel so EVERY close (WS path and self-close path) is inspected.
        if self._coord is not None and self._enabled:
            try:
                self._coord.register_close_callback(self._capture)
                self._registered = True
            except Exception as e:  # pragma: no cover — defensive
                log.warning(f"PNL_RECONCILE_REGISTER_FAIL | err='{str(e)[:120]}'")
        # Boot sentinel (Rule 14) — make the reconciler's live config, and in
        # particular the new exit-plausibility gate, visible at startup.
        log.info(
            f"PNL_RECONCILER_CONFIG_LOADED | enabled={self._enabled} "
            f"provisional={self._provisional} registered={self._registered} "
            f"max_attempts={self._max_attempts} interval_s={interval} "
            f"budget_s={self._budget_s} "
            f"exit_plausibility_gate_pct={self._max_exit_div_pct}"
        )

    def _capture(self, record: dict) -> None:
        """Sync close-callback: enqueue a non-authoritative bybit_demo close.

        Fast and side-effect-free beyond the enqueue — runs inside the
        coordinator's close fan-out, so it must not block.
        """
        try:
            # close_pnl_reconcile gates the worker; close_pnl_provisional gates
            # whether a non-authoritative close is captured for provisional
            # booking + background correction at all. When provisional is off the
            # booked value simply stands (no reconcile job is created).
            if not self._enabled or not self._provisional or self._coord is None:
                return
            ps = str(record.get("price_source") or "")
            if ps.startswith(_AUTH_PREFIX):
                return  # already exchange-authoritative; nothing to reconcile
            if str(record.get("exchange_mode") or "") != "bybit_demo":
                return  # shadow is synchronous-authoritative; no indexer lag
            sym = record.get("symbol")
            if not sym:
                return
            self._jobs.append({
                "record": dict(record), "attempts": 0,
                "enqueued_at": time.monotonic(),
            })
            log.info(
                f"PNL_PROVISIONAL_BOOKED | sym={sym} "
                f"trade_id={record.get('trade_id')} "
                f"booked_usd={record.get('pnl_usd')} src={ps} "
                f"| awaiting exchange reconcile (max_attempts={self._max_attempts})"
            )
        except Exception as e:  # pragma: no cover — never break the close path
            log.warning(f"PNL_RECONCILE_CAPTURE_FAIL | err='{str(e)[:120]}'")

    def _exit_implausible(self, ref_exit: float, resolved_exit: float | None) -> bool:
        """Phase 1 residual fix (2026-06-08) — reconcile exit-plausibility gate.

        True when the resolved exchange exit price diverges from the provisional
        close's own exit (``rec["close_price"]``) by more than the configured
        tolerance — the signal that the resolver matched a stale/wrong
        same-symbol closed-pnl row (the reconcile channel has no other staleness
        guard once the trade state is popped). Lenient by design: returns False
        (treat as plausible) when the gate is disabled (``max_div_pct <= 0``) or
        either price is missing, since a wrong-trade row in practice always
        carries a divergent exit, so the gate need not over-reject on missing
        references. A fee-driven sign flip keeps the same exit and so passes.
        """
        if self._max_exit_div_pct <= 0:
            return False
        if not ref_exit or ref_exit <= 0:
            return False
        if not resolved_exit or resolved_exit <= 0:
            return False
        div_pct = abs(float(resolved_exit) - ref_exit) / ref_exit * 100.0
        return div_pct > self._max_exit_div_pct

    async def tick(self) -> None:
        if self._coord is None or not self._jobs:
            return
        log.info(f"PNL_RECONCILE_QUEUE | depth={len(self._jobs)}")
        # One resolve attempt per pending job this tick; survivors requeue.
        for _ in range(len(self._jobs)):
            job = self._jobs.popleft()
            rec = job["record"]
            sym = rec.get("symbol")
            booked = float(rec.get("pnl_usd") or 0.0)
            # Wall-clock budget (close_pnl_reconcile_total_budget_s): drop a job
            # that has been pending longer than the budget even if attempts
            # remain — the exchange row is evidently not going to index.
            if (time.monotonic() - job.get("enqueued_at", 0.0)) > self._budget_s:
                log.warning(
                    f"PNL_RECONCILE_EXHAUSTED | sym={sym} trade_id={rec.get('trade_id')} "
                    f"reason=budget_s budget={self._budget_s:.0f}s "
                    f"attempts={job['attempts']} booked_usd={booked:+.4f} "
                    f"| provisional value kept (best available)"
                )
                continue
            job["attempts"] += 1
            try:
                # F5-c (2026-06-08): the trade's OPEN time as a freshness floor
                # so the qty-only close-row match cannot accept a stale row from
                # a PRIOR same-symbol trade (the LDO reconcile-clobber: Trade A's
                # 23:14 row booked onto Trade B). A closed-pnl row for THIS trade
                # always post-dates its open, so this never rejects the real row;
                # it only rejects earlier re-entries' rows, making the reconciler
                # retry until this trade's own row indexes. tz-safe (assume UTC if
                # naive); None on any parse failure leaves the floor disabled.
                _open_floor_ms: float | None = None
                _opened = str(rec.get("opened_at") or "")
                if _opened:
                    try:
                        from datetime import datetime, timezone
                        _dt = datetime.fromisoformat(_opened)
                        if _dt.tzinfo is None:
                            _dt = _dt.replace(tzinfo=timezone.utc)
                        _open_floor_ms = _dt.timestamp() * 1000.0
                    except (ValueError, TypeError):
                        _open_floor_ms = None
                usd, pct, src, exit_px = await self._coord.reresolve_close_pnl(
                    sym,
                    fallback_pnl_usd=booked,
                    fallback_pnl_pct=float(rec.get("pnl_pct") or 0.0),
                    qty=(float(rec.get("size") or 0.0) or None),
                    order_id=(rec.get("order_id") or None),
                    min_row_ts_ms=_open_floor_ms,
                    # F5-b: entry price disambiguates same-qty re-entries.
                    entry_price=(float(rec.get("entry_price") or 0.0) or None),
                )
            except Exception as e:
                src, usd, pct, exit_px = "local_fallback", booked, 0.0, None
                log.warning(
                    f"PNL_RECONCILE_RESOLVE_FAIL | sym={sym} "
                    f"attempt={job['attempts']} err='{str(e)[:120]}'"
                )

            if src == _AUTH_PREFIX:
                # Phase 1 residual fix (2026-06-08) — exit-plausibility gate.
                # This channel bypasses on_trade_closed's staleness gate, and
                # resolve_authoritative_pnl's qty gate is inert here (trade state
                # already popped), so a stale same-symbol closed-pnl row matched
                # qty-only can be a phantom. Proven live: a NEAR reconcile
                # resolved exit 2.3379 vs the trade's ~2.07 close, flipping a
                # -$75.83 loss into a +$18.52 phantom win. Reject a resolved row
                # whose exit diverges implausibly from THIS close's provisional
                # exit; keep the provisional and keep retrying for the real row
                # (a fee-driven sign flip keeps the same exit, so it still
                # reconciles — only a wrong-trade exit is rejected).
                ref_exit = float(rec.get("close_price") or 0.0)
                if self._exit_implausible(ref_exit, exit_px):
                    log.warning(
                        f"PNL_RECONCILE_REJECTED_STALE | sym={sym} "
                        f"trade_id={rec.get('trade_id')} ref_exit={ref_exit} "
                        f"resolved_exit={exit_px} "
                        f"max_div_pct={self._max_exit_div_pct} "
                        f"booked_usd={booked:+.4f} rejected_exchange_usd={usd:+.4f} "
                        f"| stale/wrong-trade row; provisional kept, will retry"
                    )
                    # Fall through to the requeue/exhaust block below — do NOT
                    # book the stale row; a later attempt may find the real one.
                else:
                    corrected = dict(rec)
                    corrected["pnl_usd"] = usd
                    corrected["pnl_pct"] = pct
                    corrected["was_win"] = usd > 0
                    corrected["price_source"] = "exchange_authoritative_reconciled"
                    if exit_px:
                        corrected["close_price"] = exit_px
                    # F5 part 3 (2026-06-09 phantom-close follow-up): carry the
                    # PRIOR provisionally-booked value so fire_reconcile can detect
                    # an outcome FLIP (e.g. a phantom +win booked first, now
                    # authoritatively a loss) and push a correction to the stateful
                    # consumers (enforcer streak / daily / cooldown / learning) that
                    # the idempotent reconcile channel deliberately does not touch.
                    corrected["prior_pnl_usd"] = booked
                    corrected["prior_pnl_pct"] = float(rec.get("pnl_pct") or 0.0)
                    corrected["prior_was_win"] = bool(booked > 0)
                    self._coord.fire_reconcile(corrected)
                    log.info(
                        f"PNL_RECONCILE_DONE | sym={sym} trade_id={rec.get('trade_id')} "
                        f"booked_usd={booked:+.4f} exchange_usd={usd:+.4f} "
                        f"delta={usd - booked:+.4f} attempts={job['attempts']}"
                    )
                    continue

            # Not yet indexed (or a stale row was rejected) — requeue until the
            # attempt budget is spent.
            if job["attempts"] >= self._max_attempts:
                log.warning(
                    f"PNL_RECONCILE_EXHAUSTED | sym={sym} trade_id={rec.get('trade_id')} "
                    f"attempts={job['attempts']} booked_usd={booked:+.4f} src={src} "
                    f"| provisional value kept (best available)"
                )
            else:
                self._jobs.append(job)
