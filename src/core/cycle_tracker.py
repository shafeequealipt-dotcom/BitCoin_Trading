"""Per-cycle latency tracker (Phase 1 of Layer 1 restructure).

Aggregates the elapsed time of each Layer 1 sub-layer (1A/1B/1C/1D) for
the same 5-minute window and emits a single ``CYCLE_COMPLETE`` line at
the end of each cycle. Holds the last N cycles in memory for the
``/health`` Telegram command and periodically flushes hourly aggregates
to the ``cycle_metrics`` SQLite table.

Design notes
------------
* All workers operate on a single asyncio event loop, so the in-memory
  state needs no lock. The hourly flush uses ``asyncio.create_task`` and
  awaits the database under the existing ``DatabaseManager`` lock.
* ``cycle_id`` is minute-aligned (``c-YYYY-MM-DD-HH:MM``). All four
  sub-layers in the same window share the same id, so cross-layer
  joins are trivially correlated.
* Stale cycles (started but never ended within ``_STALE_CYCLE_AGE_S``)
  are emitted with ``status=stale`` so a missing ``end_cycle`` call
  shows up rather than silently dropping the cycle.
* This module is intentionally additive: it produces NO behavior change
  on its own; it's a recorder, not a barrier.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from src.core.log_context import ctx
from src.core.log_tags import (
    CYCLE_COMPLETE,
    LAYER1B_CYCLE_DONE,
    LAYER1B_CYCLE_START,
    LAYER1C_CYCLE_DONE,
    LAYER1C_CYCLE_START,
    LAYER1D_CYCLE_DONE,
    LAYER1D_CYCLE_START,
)
from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("cycle_tracker")

# Stale cycle threshold — a cycle started > N seconds ago without an
# end is considered abandoned and rolled into ``CYCLE_COMPLETE`` with
# ``status=stale``. 600s = two 5-minute windows; longer than any healthy
# cycle should ever take (target total is <30s per blueprint Section 13).
_STALE_CYCLE_AGE_S: float = 600.0


@dataclass
class CycleSummary:
    """One cycle's aggregated breakdown.

    Attributes:
        cycle_id: Minute-aligned identifier shared across sub-layers.
        layer1a_ms: 1A elapsed (None if not recorded — 1A often runs
            independently of cycle in steady state).
        layer1b_ms: 1B elapsed (analyzers).
        layer1c_ms: 1C elapsed (strategy pipeline).
        layer1d_ms: 1D elapsed (selector + package builder).
        total_ms: Sum of the recorded sub-layers.
        packages_ready: Count of CoinPackages produced (Phase 6+; 0 today).
        qualified_pct: Percentage of universe that passed the qualitative
            filter (Phase 5+; 0.0 today).
        status: ``"ok"`` for normal completion or ``"stale"`` for cycles
            that never received their ``end_cycle`` call.
        completed_at_unix: ``time.time()`` of completion for downstream
            ``cycle_metrics`` flushes.
        interestingness_score: Per-cycle interestingness (mean across
            briefed coins; None until Phase 4 of the 1D briefing rewrite
            populates it via ``record_briefing``).
        state_label_counts: Per-cycle label distribution as a dict from
            label name to count (None until Phase 3 wires it).
        briefing_packages_count: Coins selected into the top-N briefing
            for the cycle (None until Phase 5 wires the briefing-mode
            scanner path).
    """

    cycle_id: str
    layer1a_ms: int | None = None
    layer1b_ms: int | None = None
    layer1c_ms: int | None = None
    layer1d_ms: int | None = None
    packages_ready: int = 0
    qualified_pct: float = 0.0
    status: str = "ok"
    completed_at_unix: float = field(default_factory=time.time)
    # ── Phase 1 of the 1D briefing rewrite: additive observability fields.
    # All default to None so existing tests and consumers see unchanged
    # behavior; populated by Phases 3/4/5 callers via ``record_briefing``.
    interestingness_score: float | None = None
    state_label_counts: dict[str, int] | None = None
    briefing_packages_count: int | None = None

    @property
    def total_ms(self) -> int:
        """Sum of recorded sub-layer elapsed times (in milliseconds)."""
        return sum(
            v for v in (
                self.layer1a_ms, self.layer1b_ms, self.layer1c_ms, self.layer1d_ms,
            ) if v is not None
        )


class CycleTracker:
    """Records per-cycle latencies and emits ``CYCLE_COMPLETE``.

    Args:
        db: DatabaseManager — used by the hourly flush task.
        max_history: How many cycles to retain in memory for ``/health``.
            Default 100 ≈ 8 hours at one cycle per 5 min.
    """

    _CYCLE_LAYERS = ("layer1a", "layer1b", "layer1c", "layer1d")

    def __init__(self, db: DatabaseManager, max_history: int = 100) -> None:
        self._db = db
        self._max_history = int(max_history)
        # Active starts keyed by (cycle_id, layer) → t_start_monotonic.
        self._active: dict[tuple[str, str], float] = {}
        # Completed cycles, indexed by cycle_id for fast lookup until the
        # CYCLE_COMPLETE line is emitted, then moved into history.
        self._completed: dict[str, CycleSummary] = {}
        self._history: deque[CycleSummary] = deque(maxlen=self._max_history)
        # Periodic flush task handle (asyncio.create_task return value).
        self._flush_task: asyncio.Task | None = None

    # ── Public API ───────────────────────────────────────────────────

    @staticmethod
    def make_cycle_id(now: datetime | None = None) -> str:
        """Minute-aligned cycle identifier shared across sub-layers.

        Args:
            now: Defaults to current UTC time. Tests inject a fixed time.

        Returns:
            ``c-YYYY-MM-DD-HH:MM`` (UTC, minute-aligned to 5-min window
            via integer division on minute).
        """
        n = now or datetime.now(timezone.utc)
        # Floor to the 5-minute window boundary.
        floored_minute = (n.minute // 5) * 5
        return f"c-{n.year:04d}-{n.month:02d}-{n.day:02d}-{n.hour:02d}:{floored_minute:02d}"

    def start_cycle(self, layer: str, *, cycle_id: str | None = None) -> str:
        """Begin recording one sub-layer's elapsed time.

        Args:
            layer: One of ``layer1a``, ``layer1b``, ``layer1c``, ``layer1d``.
            cycle_id: Optional explicit id (callers within the same
                window pass the same id from the first ``start_cycle``).
                Defaults to the current minute-aligned id.

        Returns:
            The cycle_id used.

        Raises:
            ValueError: If ``layer`` is not a recognized sub-layer name.
        """
        if layer not in self._CYCLE_LAYERS:
            raise ValueError(
                f"unknown layer {layer!r}; expected one of {self._CYCLE_LAYERS}"
            )
        cid = cycle_id or self.make_cycle_id()
        self._active[(cid, layer)] = time.monotonic()
        # Emit cycle-start markers for 1B/1C/1D (1A is fire-and-forget per
        # blueprint Section 7 — it can run when trading is off, no cycle).
        if layer == "layer1b":
            log.info(f"{LAYER1B_CYCLE_START} | cycle_id={cid} | {ctx()}")
        elif layer == "layer1c":
            log.info(f"{LAYER1C_CYCLE_START} | cycle_id={cid} | {ctx()}")
        elif layer == "layer1d":
            log.info(f"{LAYER1D_CYCLE_START} | cycle_id={cid} | {ctx()}")
        return cid

    def end_cycle(self, layer: str, cycle_id: str) -> int:
        """Record a sub-layer's completion. Returns elapsed ms.

        If the cycle's ``start`` was never called for this layer, the
        method still returns 0 and logs a debug line so a missing start
        doesn't crash the worker — but the row in cycle_metrics will
        show 0 for that layer.
        """
        key = (cycle_id, layer)
        t_start = self._active.pop(key, None)
        if t_start is None:
            log.debug(
                f"CYCLE_TRACKER_NO_START | layer={layer} cycle_id={cycle_id} | {ctx()}"
            )
            elapsed_ms = 0
        else:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)

        summary = self._completed.setdefault(cycle_id, CycleSummary(cycle_id=cycle_id))
        if layer == "layer1a":
            summary.layer1a_ms = elapsed_ms
        elif layer == "layer1b":
            summary.layer1b_ms = elapsed_ms
            log.info(
                f"{LAYER1B_CYCLE_DONE} | cycle_id={cycle_id} elapsed_ms={elapsed_ms} | {ctx()}"
            )
        elif layer == "layer1c":
            summary.layer1c_ms = elapsed_ms
            log.info(
                f"{LAYER1C_CYCLE_DONE} | cycle_id={cycle_id} elapsed_ms={elapsed_ms} | {ctx()}"
            )
        elif layer == "layer1d":
            summary.layer1d_ms = elapsed_ms
            log.info(
                f"{LAYER1D_CYCLE_DONE} | cycle_id={cycle_id} elapsed_ms={elapsed_ms} | {ctx()}"
            )

        # 1D is the cycle terminator — once 1D ends we emit the rollup.
        if layer == "layer1d":
            self._emit_complete(cycle_id)
        return elapsed_ms

    def record_qualified(
        self, cycle_id: str, qualified: int, selected: int, packages: int,
    ) -> None:
        """Stamp ScannerWorker selection counts onto an active cycle.

        Phase 5/6 callers stamp these so the ``CYCLE_COMPLETE`` rollup
        carries qualified_pct and packages_ready without the tracker
        needing direct access to ScannerWorker internals.
        """
        summary = self._completed.setdefault(cycle_id, CycleSummary(cycle_id=cycle_id))
        # qualified_pct is informational; if scanner reports 50 universe
        # → 14 qualified, qualified_pct = 28.0.
        summary.packages_ready = int(packages)
        if qualified > 0:
            # Caller is responsible for passing the universe-relative
            # percentage if available; we accept the absolute count and
            # leave the conversion to the emitter when universe size is
            # known. Default: store the qualified count itself in pct
            # and leave the sender to multiply by 100/universe_size.
            summary.qualified_pct = float(qualified)

    def record_briefing(
        self,
        cycle_id: str,
        *,
        interestingness_score: float | None = None,
        state_label_counts: dict[str, int] | None = None,
        briefing_packages_count: int | None = None,
    ) -> None:
        """Stamp briefing-pipeline aggregates onto an active cycle.

        Phase 1 of the 1D briefing rewrite registers the recorder; the
        callers (StateLabeler, InterestingnessRanker, briefing-mode
        ScannerWorker) wire in Phases 3-5. All arguments are optional —
        callers stamp whichever signals are available without forcing the
        tracker to know the call order.

        Args:
            cycle_id: Active cycle identifier.
            interestingness_score: Mean interestingness across the briefed
                coins for this cycle. Pass None to leave unchanged.
            state_label_counts: Per-label count map for this cycle. Pass
                None to leave unchanged.
            briefing_packages_count: Number of coins selected into the
                top-N briefing for this cycle. Pass None to leave unchanged.
        """
        summary = self._completed.setdefault(cycle_id, CycleSummary(cycle_id=cycle_id))
        if interestingness_score is not None:
            summary.interestingness_score = float(interestingness_score)
        if state_label_counts is not None:
            summary.state_label_counts = dict(state_label_counts)
        if briefing_packages_count is not None:
            summary.briefing_packages_count = int(briefing_packages_count)

    def get_recent(self, n: int = 10) -> list[CycleSummary]:
        """Return up to ``n`` most recent completed cycles (newest last)."""
        if n <= 0:
            return []
        return list(self._history)[-n:]

    # ── Internal — emission, stale-sweep, hourly flush ───────────────

    def _emit_complete(self, cycle_id: str) -> None:
        """Emit ``CYCLE_COMPLETE`` and move the summary into history."""
        summary = self._completed.pop(cycle_id, None)
        if summary is None:
            return
        self._sweep_stale(now=time.time())
        log.info(
            f"{CYCLE_COMPLETE} | cycle_id={cycle_id} "
            f"layer1a_ms={summary.layer1a_ms or 0} "
            f"layer1b_ms={summary.layer1b_ms or 0} "
            f"layer1c_ms={summary.layer1c_ms or 0} "
            f"layer1d_ms={summary.layer1d_ms or 0} "
            f"total_ms={summary.total_ms} "
            f"packages_ready={summary.packages_ready} "
            f"qualified_pct={summary.qualified_pct:.1f} "
            f"status={summary.status} | {ctx()}"
        )
        self._history.append(summary)

    def _sweep_stale(self, *, now: float) -> None:
        """Roll stale (started-but-never-ended) cycles into history.

        Any cycle whose oldest active layer started > ``_STALE_CYCLE_AGE_S``
        ago is closed out with ``status=stale``. Prevents memory leaks
        when a worker crashes mid-cycle.
        """
        # Find stale cycle_ids: any active layer with t_start older than threshold.
        stale_cids: set[str] = set()
        for (cid, _layer), t_start in list(self._active.items()):
            if (time.monotonic() - t_start) > _STALE_CYCLE_AGE_S:
                stale_cids.add(cid)
        for cid in stale_cids:
            summary = self._completed.setdefault(cid, CycleSummary(cycle_id=cid))
            summary.status = "stale"
            # Drop the active entries for this cycle.
            for key in [k for k in self._active if k[0] == cid]:
                self._active.pop(key, None)
            log.warning(
                f"CYCLE_TRACKER_STALE | cycle_id={cid} | {ctx()}"
            )
            self._history.append(self._completed.pop(cid))

    async def start_hourly_flush_task(
        self, *, flush_seconds: int = 3600,
    ) -> None:
        """Begin periodic flush of aggregates to ``cycle_metrics``.

        Idempotent: starting twice replaces the existing task.
        """
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = asyncio.create_task(
            self._flush_loop(flush_seconds=flush_seconds),
            name="cycle_tracker_flush",
        )

    async def _flush_loop(self, *, flush_seconds: int) -> None:
        """Sleep N seconds, compute aggregates, INSERT into cycle_metrics."""
        try:
            while True:
                await asyncio.sleep(flush_seconds)
                try:
                    await self._flush_once()
                except Exception as e:
                    log.warning(
                        f"CYCLE_METRICS_FLUSH_FAIL | err='{str(e)[:120]}' | {ctx()}"
                    )
        except asyncio.CancelledError:
            return

    async def _flush_once(self) -> None:
        """Compute p50/p95 across the in-memory history and insert one row."""
        if not self._history:
            return
        # Aggregate over the last hour's worth of cycles. cycles fire at
        # most every 5 min, so 12 cycles ≈ 1 hour. Use whatever's in
        # history — at most max_history (100, ≈ 8h).
        cycles = list(self._history)
        hour_ts = int(cycles[-1].completed_at_unix // 3600 * 3600)

        def _q(values: Iterable[int | None], *, q: float) -> int:
            xs = [v for v in values if v is not None]
            if not xs:
                return 0
            xs.sort()
            # Simple linear-interpolation percentile, not numpy-grade —
            # fine for ops dashboards.
            k = (len(xs) - 1) * q
            lo = int(k)
            hi = min(lo + 1, len(xs) - 1)
            return int(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))

        def _qf(values: Iterable[float | None], *, q: float) -> float | None:
            """Float-valued percentile; returns None when no data so the
            briefing columns stay NULL until callers populate them."""
            xs = [float(v) for v in values if v is not None]
            if not xs:
                return None
            xs.sort()
            k = (len(xs) - 1) * q
            lo = int(k)
            hi = min(lo + 1, len(xs) - 1)
            return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)

        l1a = [c.layer1a_ms for c in cycles]
        l1b = [c.layer1b_ms for c in cycles]
        l1c = [c.layer1c_ms for c in cycles]
        l1d = [c.layer1d_ms for c in cycles]
        totals = [c.total_ms for c in cycles]
        qualified_avg = (
            statistics.fmean([c.qualified_pct for c in cycles]) if cycles else 0.0
        )
        packages_avg = (
            statistics.fmean([float(c.packages_ready) for c in cycles]) if cycles else 0.0
        )

        # ── Phase 1 of the 1D briefing rewrite — additive aggregates ──
        # interestingness_p50/p95 from the per-cycle mean scores; remains
        # None (→ SQL NULL) until Phase 4 populates ``record_briefing``.
        # state_label_distribution_json: union the per-cycle maps and
        # serialize as a stable {label: total_count} JSON object.
        # briefing_packages_count: average of per-cycle counts (rounded
        # to int) so the column matches the integer schema; None when
        # no cycle has stamped a briefing count yet.
        interestingness_values = [
            c.interestingness_score for c in cycles
        ]
        interestingness_p50 = _qf(interestingness_values, q=0.50)
        interestingness_p95 = _qf(interestingness_values, q=0.95)

        label_totals: dict[str, int] = {}
        for c in cycles:
            if not c.state_label_counts:
                continue
            for label, n in c.state_label_counts.items():
                label_totals[label] = label_totals.get(label, 0) + int(n)
        # JSON-encode only when there is at least one stamped cycle so
        # the column stays NULL during the dormant period (Phases 1-2).
        if label_totals:
            import json as _json
            state_label_distribution_json: str | None = _json.dumps(
                label_totals, sort_keys=True,
            )
        else:
            state_label_distribution_json = None

        briefing_counts = [
            c.briefing_packages_count for c in cycles
            if c.briefing_packages_count is not None
        ]
        briefing_packages_count_avg: int | None = (
            int(round(statistics.fmean(briefing_counts)))
            if briefing_counts else None
        )

        # Layer 2 Defect 5 (2026-05-22) — wire the previously-dead cycle_metrics
        # aggregate columns by SQL-querying the source tables for the hour-
        # window. Each is one indexed read once per hour — negligible
        # overhead vs. the value of having queryable per-hour distributions
        # for Layer 3-4 analysis. Wrapped in best-effort try/except so any
        # individual query failure leaves that column NULL without breaking
        # the per-cycle metrics that already work.
        #
        # CRITICAL — timestamp format duality (D5 follow-up fix, 2026-05-22):
        # The source tables store timestamps in TWO DIFFERENT formats:
        #   - signals.created_at: Python isoformat "YYYY-MM-DDTHH:MM:SS.ffffff+00:00"
        #     (set explicitly by altdata_repo.save_signal via signal.created_at.isoformat())
        #   - trade_intelligence.captured_at: Python isoformat (same as signals; set by
        #     tias/collector.py via datetime.now(timezone.utc).isoformat())
        #   - coin_regime_history.timestamp: SQLite datetime() "YYYY-MM-DD HH:MM:SS"
        #     (T-replaced with space, no timezone, no microseconds — produced by the
        #     ``DEFAULT (datetime('now'))`` column default)
        # SQLite string comparison is lexicographic, so an ISO-format WHERE bind
        # ('...T08:00:00+00:00') would never match a space-format stored value
        # ('...  08:00:00') — T (ASCII 84) > space (32), so every stored row
        # appears "less than" every ISO bind, and the query returns zero rows
        # for tables that use the SQLite default format. The fix produces both
        # bind formats and uses the right one per query.
        _hour_iso_start = datetime.fromtimestamp(hour_ts, tz=timezone.utc).isoformat()
        _hour_iso_end = datetime.fromtimestamp(hour_ts + 3600, tz=timezone.utc).isoformat()
        _hour_sql_start = datetime.fromtimestamp(hour_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _hour_sql_end = datetime.fromtimestamp(hour_ts + 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        _signal_buy_pct: float | None = None
        _signal_sell_pct: float | None = None
        _signal_neutral_pct: float | None = None
        try:
            _sig_rows = await self._db.fetch_all(
                "SELECT signal_type, COUNT(*) AS n FROM signals "
                "WHERE created_at >= ? AND created_at < ? GROUP BY signal_type",
                (_hour_iso_start, _hour_iso_end),
            )
            _counts = {r["signal_type"]: int(r["n"]) for r in _sig_rows}
            _total = sum(_counts.values())
            if _total > 0:
                _signal_buy_pct = round(
                    100.0 * (_counts.get("buy", 0) + _counts.get("strong_buy", 0)) / _total,
                    2,
                )
                _signal_sell_pct = round(
                    100.0 * (_counts.get("sell", 0) + _counts.get("strong_sell", 0)) / _total,
                    2,
                )
                _signal_neutral_pct = round(
                    100.0 * _counts.get("neutral", 0) / _total, 2,
                )
        except Exception as _e:
            log.debug(f"D5_SIGNAL_PCT_QUERY_FAIL | err='{str(_e)[:80]}'")

        # Layer 2 Defect 5 follow-up note (2026-05-22) — xray_setup_type_count
        # left NULL in this commit. trade_intelligence currently has no
        # entry_setup_type column (it lives on TradeState in memory but is
        # not persisted). Wiring it requires a schema migration on
        # trade_intelligence + collector plumbing, deferred to a follow-up
        # so this D5 commit stays focused on the columns whose source data
        # already exists in queryable form.
        _xray_setup_type_count: int | None = None

        _regime_distribution_json: str | None = None
        try:
            # coin_regime_history.timestamp uses SQLite datetime() format
            # (space-separated, no T) — bind with _hour_sql_* not _hour_iso_*
            # to avoid lexicographic mismatch that would always return 0 rows.
            _reg_rows = await self._db.fetch_all(
                "SELECT regime, COUNT(*) AS n FROM coin_regime_history "
                "WHERE timestamp >= ? AND timestamp < ? GROUP BY regime",
                (_hour_sql_start, _hour_sql_end),
            )
            if _reg_rows:
                import json as _json
                _reg_dict = {r["regime"]: int(r["n"]) for r in _reg_rows}
                _regime_distribution_json = _json.dumps(_reg_dict, sort_keys=True)
        except Exception as _e:
            log.debug(f"D5_REGIME_DIST_QUERY_FAIL | err='{str(_e)[:80]}'")

        _l2_score_p50: float | None = None
        try:
            # L2 score sourced from trade_intelligence.entry_score (now
            # populated by D4 from apex_confidence). Percentile via SQL
            # avoids hauling the full list into Python.
            _l2_rows = await self._db.fetch_all(
                "SELECT entry_score FROM trade_intelligence "
                "WHERE captured_at >= ? AND captured_at < ? "
                "AND entry_score IS NOT NULL "
                "ORDER BY entry_score",
                (_hour_iso_start, _hour_iso_end),
            )
            _l2_values = [float(r["entry_score"]) for r in _l2_rows]
            _l2_score_p50 = _qf(_l2_values, q=0.50)
        except Exception as _e:
            log.debug(f"D5_L2_SCORE_QUERY_FAIL | err='{str(_e)[:80]}'")

        await self._db.execute(
            "INSERT OR REPLACE INTO cycle_metrics "
            "(hour_ts, cycles_count, "
            " layer1a_p50_ms, layer1a_p95_ms, layer1b_p50_ms, layer1b_p95_ms, "
            " layer1c_p50_ms, layer1c_p95_ms, layer1d_p50_ms, layer1d_p95_ms, "
            " total_p50_ms, total_p95_ms, qualified_pct_avg, packages_count_avg, "
            " interestingness_p50, interestingness_p95, "
            " state_label_distribution_json, briefing_packages_count, "
            " signal_buy_pct, signal_sell_pct, signal_neutral_pct, "
            " xray_setup_type_count, regime_distribution_json, l2_score_p50) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hour_ts, len(cycles),
                _q(l1a, q=0.50), _q(l1a, q=0.95),
                _q(l1b, q=0.50), _q(l1b, q=0.95),
                _q(l1c, q=0.50), _q(l1c, q=0.95),
                _q(l1d, q=0.50), _q(l1d, q=0.95),
                _q(totals, q=0.50), _q(totals, q=0.95),
                round(qualified_avg, 2),
                round(packages_avg, 2),
                interestingness_p50,
                interestingness_p95,
                state_label_distribution_json,
                briefing_packages_count_avg,
                _signal_buy_pct, _signal_sell_pct, _signal_neutral_pct,
                _xray_setup_type_count, _regime_distribution_json, _l2_score_p50,
            ),
        )
        log.info(
            f"CYCLE_METRICS_FLUSHED | hour_ts={hour_ts} cycles={len(cycles)} "
            f"l1a_p95={_q(l1a, q=0.95)} l1b_p95={_q(l1b, q=0.95)} "
            f"l1c_p95={_q(l1c, q=0.95)} l1d_p95={_q(l1d, q=0.95)} "
            f"total_p95={_q(totals, q=0.95)} "
            f"interestingness_p50={interestingness_p50} "
            f"interestingness_p95={interestingness_p95} "
            f"briefing_packages_count={briefing_packages_count_avg} | {ctx()}"
        )

        # Layer 2 Rule 16 — periodic persistence consistency self-check.
        # The IMPLEMENT_LAYER2_PERSISTENCE prompt's Rule 16 requires:
        # "A periodic consistency check that asserts: every closed trade
        # in trade_log has a joinable decision/votes record. If any of
        # these diverge, log loudly." This guards against silent
        # regressions where the persistence wiring breaks (e.g., a
        # future refactor drops the setup_id plumbing on the brain_v2
        # path) without anyone noticing until the data is missing for
        # Layer 3/4 analysis.
        #
        # The check runs once per hour as part of the same flush cadence:
        #   - count trade_intelligence rows in the hour with setup_id
        #     IS NOT NULL (i.e., trades opened from the strategy_worker
        #     path that plumbs setup_id)
        #   - for each such setup_id, count matching ensemble_votes rows
        #   - any setup_id with ZERO matching votes = orphan trade =
        #     loud RULE16_CONSISTENCY_FAIL
        #
        # Wrapped in try/except so the check itself never crashes the
        # flush. Logs loud (ERROR) on orphans; quiet (INFO) on success.
        try:
            _orphan_rows = await self._db.fetch_all(
                """
                SELECT ti.setup_id, COUNT(ev.id) AS vote_rows
                FROM trade_intelligence ti
                LEFT JOIN ensemble_votes ev ON ev.setup_id = ti.setup_id
                WHERE ti.captured_at >= ? AND ti.captured_at < ?
                  AND ti.setup_id IS NOT NULL AND ti.setup_id != ''
                GROUP BY ti.setup_id
                HAVING COUNT(ev.id) = 0
                """,
                (_hour_iso_start, _hour_iso_end),
            )
            _total_with_setup_id = await self._db.fetch_one(
                """
                SELECT COUNT(*) AS n FROM trade_intelligence
                WHERE captured_at >= ? AND captured_at < ?
                  AND setup_id IS NOT NULL AND setup_id != ''
                """,
                (_hour_iso_start, _hour_iso_end),
            )
            _n_trades = int((_total_with_setup_id or {}).get("n", 0))
            _n_orphans = len(_orphan_rows)
            if _n_orphans > 0:
                _orphan_ids = ",".join(
                    str(r["setup_id"]) for r in _orphan_rows[:5]
                )
                log.error(
                    f"RULE16_CONSISTENCY_FAIL | hour_ts={hour_ts} "
                    f"trades_with_setup_id={_n_trades} "
                    f"orphan_trades_without_votes={_n_orphans} "
                    f"sample_orphan_ids={_orphan_ids} | {ctx()}"
                )
            else:
                log.info(
                    f"RULE16_CONSISTENCY_OK | hour_ts={hour_ts} "
                    f"trades_with_setup_id={_n_trades} "
                    f"all_join_ensemble_votes=True | {ctx()}"
                )
        except Exception as _e:
            log.debug(
                f"RULE16_CONSISTENCY_CHECK_FAIL | err='{str(_e)[:120]}'"
            )

        # Layer 4 Rule 16 periodic self-checks (2026-05-22).
        # IMPLEMENT_LAYER4_CONSENSUS_TRUTH.md Rule 16 requires:
        #   (a) periodic measurement of brain-chosen size on
        #       high-agreement vs low-agreement trades, logged so the
        #       operator can see whether the truthful framing changes
        #       the brain's sizing (success signal: avg size on 6+
        #       agreeing trades drops below avg on 4-5 agreeing)
        #   (b) a herding-effect monitor that re-measures, on an
        #       ongoing basis, whether broad agreement still
        #       correlates with losses
        # Both checks read trade_intelligence rows captured in the
        # last hour, group by supporting_count bucket, and emit a
        # single INFO log line. Best-effort; failure logs at DEBUG.
        # Bucket scheme matches the master plan's narrow/moderate/
        # broad agreement split (1-3 / 4-5 / 6+).
        try:
            _bucket_rows = await self._db.fetch_all(
                """
                SELECT
                    CASE
                        WHEN supporting_count BETWEEN 1 AND 3 THEN 'narrow_1_3'
                        WHEN supporting_count BETWEEN 4 AND 5 THEN 'moderate_4_5'
                        WHEN supporting_count >= 6 THEN 'broad_6_plus'
                        ELSE 'unknown'
                    END AS bucket,
                    COUNT(*) AS n,
                    AVG(position_size_usd) AS avg_size_usd,
                    AVG(pnl_pct) AS avg_pnl_pct
                FROM trade_intelligence
                WHERE captured_at >= ? AND captured_at < ?
                  AND supporting_count IS NOT NULL
                GROUP BY bucket
                """,
                (_hour_iso_start, _hour_iso_end),
            )
            _by_bucket: dict[str, dict[str, float | int]] = {}
            for r in _bucket_rows:
                _by_bucket[str(r["bucket"])] = {
                    "n": int(r["n"]),
                    "avg_size_usd": (
                        float(r["avg_size_usd"])
                        if r["avg_size_usd"] is not None else 0.0
                    ),
                    "avg_pnl_pct": (
                        float(r["avg_pnl_pct"])
                        if r["avg_pnl_pct"] is not None else 0.0
                    ),
                }
            _narrow = _by_bucket.get("narrow_1_3", {})
            _moderate = _by_bucket.get("moderate_4_5", {})
            _broad = _by_bucket.get("broad_6_plus", {})

            # (a) brain-chosen size by agreement
            log.info(
                f"L4_BRAIN_SIZE_BY_AGREEMENT | hour_ts={hour_ts} "
                f"narrow_1_3=n{int(_narrow.get('n', 0))}/"
                f"${float(_narrow.get('avg_size_usd', 0.0)):.0f} "
                f"moderate_4_5=n{int(_moderate.get('n', 0))}/"
                f"${float(_moderate.get('avg_size_usd', 0.0)):.0f} "
                f"broad_6_plus=n{int(_broad.get('n', 0))}/"
                f"${float(_broad.get('avg_size_usd', 0.0)):.0f} | "
                f"{ctx()}"
            )
            # Loud signal when brain still sizes UP on crowded trades
            # (the truthful framing isn't moving it). Per Rule 11 this
            # is a finding for the operator, not a license to hardcode.
            _broad_size = float(_broad.get("avg_size_usd", 0.0) or 0.0)
            _moderate_size = float(
                _moderate.get("avg_size_usd", 0.0) or 0.0
            )
            if (
                int(_broad.get("n", 0)) >= 3
                and int(_moderate.get("n", 0)) >= 3
                and _broad_size > _moderate_size
            ):
                log.warning(
                    f"L4_BRAIN_INVERTED_SIZING | hour_ts={hour_ts} "
                    f"broad_avg_size=${_broad_size:.0f} > "
                    f"moderate_avg_size=${_moderate_size:.0f} | "
                    f"signal=brain_not_responding_to_truthful_framing | "
                    f"{ctx()}"
                )

            # (b) herding monitor — per-bucket avg pnl
            log.info(
                f"L4_HERDING_MONITOR | hour_ts={hour_ts} "
                f"narrow_1_3_pnl={float(_narrow.get('avg_pnl_pct', 0.0)):+.3f}% "
                f"moderate_4_5_pnl={float(_moderate.get('avg_pnl_pct', 0.0)):+.3f}% "
                f"broad_6_plus_pnl={float(_broad.get('avg_pnl_pct', 0.0)):+.3f}% | "
                f"{ctx()}"
            )
        except Exception as _e:
            log.debug(
                f"L4_RULE16_SELF_CHECK_FAIL | err='{str(_e)[:120]}'"
            )

    async def stop(self) -> None:
        """Cancel the flush task on shutdown. Safe if not started."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        self._flush_task = None
