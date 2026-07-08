"""Layer 3 — regime-conditional data-derived per-strategy weighting.

The aim, per IMPLEMENT_LAYER3_ENSEMBLE_WEIGHTING.md Part A.4: find the best
trade for each situation by letting each strategy's vote weight follow its
actual recorded performance IN THE CURRENT REGIME — not a hardcoded constant,
not a permanent bias, not silenced forever. A strategy that wins in trending
regimes carries more weight there; the same strategy in chop carries less.
The asymmetry emerges from the data, never from a typed-in number.

Architecture:

  StrategyWeightDeriver pulls per-(strategy, regime) supporting-trade
  performance from the persisted Layer 2 data (ensemble_votes JOIN
  trade_intelligence by setup_id, grouped by strategy_name + entry_regime).
  It computes a multiplicative factor per cell, bounded in [floor, ceil]
  with a cold-start floor (cell stays at 1.0 until ``cold_start_n``
  supporting trades exist). Smoothed across refreshes by EMA so single
  bad cycles don't whipsaw weights.

  ``get_factor(regime, strategy_name)`` is called from
  ``EnsembleVoter.vote()`` at the per-strategy weight read. The factor
  multiplies the base ``StrategyPerformance.ensemble_weight``. Cold-start
  cells return 1.0 so the system degrades cleanly to today's equal
  weighting until evidence accumulates.

Self-checks (Rule 16):
  - ``audit_no_permanent_silence``: every strategy must have non-zero
    weight in at least one regime (no strategy permanently dead).
  - ``audit_regime_dependence``: the weight vector must differ across
    regimes (if regime_weights are identical for every regime, the
    mechanism has degenerated to flat — log loudly).
  - ``audit_cold_start_count``: how many (strategy, regime) cells are
    still on equal weight vs data-derived — visibility into maturation.
  - ``audit_drift``: how much weights changed between recomputes; high
    drift on thin data signals overfitting (operator can tune smoothing).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("strategies")


@dataclass
class CellPerformance:
    """Per-(strategy, regime) cell — the unit Layer 3 weights over.

    Fields are the raw inputs to the factor formula; kept on the object so
    audit and observability logs can show the WHY behind each factor.
    """

    strategy_name: str
    regime: str
    sample_size: int = 0           # COUNT(DISTINCT setup_id) supporting trades
    supporting_pnl_pct: float = 0.0  # SUM of pnl_pct for supporting trades
    supporting_wins: int = 0       # COUNT of wins among supporting trades
    avg_pnl_pct: float = 0.0       # supporting_pnl_pct / sample_size
    win_rate: float = 0.0          # supporting_wins / sample_size
    factor_computed: float = 1.0   # this refresh's raw factor (pre-smoothing)
    factor_smoothed: float = 1.0   # post-EMA factor that the voter uses


class StrategyWeightDeriver:
    """Derives per-(strategy, regime) weight factors from Layer 2 persistence.

    Refreshed periodically (operator-configured cadence; default per
    5-minute scan cycle via strategy_worker). Reads are O(1) lookups from
    an in-memory dict; voters never hit the DB on the hot path.

    Cold-start: every cell starts at factor 1.0 and only diverges once
    ``sample_size >= cold_start_n``. At ship time, ALL cells are below
    threshold, so behaviour is byte-equivalent to today's equal weighting.
    As Layer 2 data flows in, cells mature one by one.

    The instance holds the cached factor table AND the raw CellPerformance
    rows behind it, so the audit/observability surface can show the data
    behind each factor.
    """

    def __init__(
        self,
        cold_start_n: int = 20,
        floor: float = 0.3,
        ceil: float = 3.0,
        sensitivity: float = 0.3,
        ema_alpha: float = 0.3,
    ) -> None:
        """Construct with operator-configured weighting parameters.

        Args:
            cold_start_n: Per-cell supporting-trade threshold below which
                the cell stays at factor=1.0 (equal weight). Operator
                approved 20 at the design gate.
            floor: Minimum factor — guarantees no strategy is ever
                silenced (Rule 5). Operator approved 0.3.
            ceil: Maximum factor — caps any single strategy's amplification.
                Operator approved 3.0.
            sensitivity: Multiplier on supporting_avg_pnl_pct in the
                factor formula. Operator approved 0.3 (a +3% strategy
                gets factor ~1.9; -3% gets clamped to floor).
            ema_alpha: Smoothing weight for new vs previous factor:
                new = alpha * computed + (1 - alpha) * previous.
                Operator approved 0.3 (slow adaptation; resilient to
                single-cycle noise).
        """
        self._cold_start_n: int = int(cold_start_n)
        self._floor: float = float(floor)
        self._ceil: float = float(ceil)
        self._sensitivity: float = float(sensitivity)
        self._ema_alpha: float = float(ema_alpha)

        # The cached factor table — get_factor() reads this.
        # Shape: {regime_value: {strategy_name: factor_float}}
        self._regime_weights: dict[str, dict[str, float]] = {}

        # The raw cell evidence behind each factor (for audit + observability).
        # Shape: {(regime_value, strategy_name): CellPerformance}
        self._cells: dict[tuple[str, str], CellPerformance] = {}

        # Monotonic counter for refresh observability + drift tracking.
        self._refresh_count: int = 0
        self._last_refresh_ts: float = 0.0
        self._last_drift_avg: float = 0.0  # mean abs change in factors

    def get_factor(self, regime: str, strategy_name: str) -> float:
        """Return the multiplicative weight factor for a (regime, strategy).

        Cold-start cells return 1.0 (equal weight); past-threshold cells
        return their EMA-smoothed data-derived factor in [floor, ceil].

        This is the per-vote read path — must be O(1) and never touch
        the DB. Defensively returns 1.0 if the cell is unknown.

        Args:
            regime: The MarketRegime value string (e.g., "trending_up").
            strategy_name: The strategy's registered name.

        Returns:
            Factor in [floor, ceil] for past-threshold cells, or 1.0
            for cold-start / unknown cells.
        """
        return self._regime_weights.get(regime, {}).get(strategy_name, 1.0)

    async def refresh(self, db: Any) -> int:
        """Recompute per-cell factors from the persisted Layer 2 data.

        Issues one SQL query that JOINs ensemble_votes ↔ trade_intelligence
        by setup_id and groups by (strategy_name, entry_regime). For each
        cell:
          - sample_size < cold_start_n  → factor = 1.0 (kept on equal weight)
          - sample_size >= cold_start_n → factor = clamp(1.0 + sensitivity *
                                              avg_pnl_pct, floor, ceil)
          - Smoothed by EMA against the previous factor for this cell.

        Failure is loud-but-non-fatal per Rule 7: on DB error, the cached
        table is left unchanged (last good factors keep applying); a
        RW_REFRESH_FAIL log line fires at ERROR with the truncated reason.

        Args:
            db: DatabaseManager — must expose fetch_all matching the
                project's J11 concurrency contract.

        Returns:
            Number of cells refreshed (0 on failure or no data).
        """
        try:
            # Grain-mismatch fix (2026-06-09): the prior query summed ti.win and
            # ti.pnl_pct over the JOINED rows but divided by COUNT(DISTINCT
            # setup_id). Both tables carry DUPLICATE rows per setup —
            # trade_intelligence holds multiple analysis rows per setup_id (re-
            # analysis; some with conflicting win), and ensemble_votes re-writes
            # a strategy's vote for a setup every cycle — so the JOIN multiplied
            # the numerator while the denominator stayed distinct, inflating
            # win_rate / avg_pnl ~1.6-1.9x (e.g. A2/trending_down read 0.90 vs a
            # true ~0.51). That fed inflated factors into the consensus weighting
            # whenever regime_weighting_enabled is on. The fix collapses BOTH
            # sides to one row per setup before aggregating, so numerator and
            # denominator share the same grain:
            #   - ti_latest: the CANONICAL (latest by rowid) trade_intelligence
            #     row per setup_id (the most recent analysis = the final outcome;
            #     resolves the conflicting-win duplicates).
            #   - the inner DISTINCT (strategy_name, setup_id) collapses the
            #     ensemble_votes duplicates to one (strategy, setup) pair where
            #     the strategy voted the taken direction; the indexed setup_id
            #     join is bounded by the small trade_intelligence set, so this
            #     stays fast enough for the per-tick refresh.
            # Case-normalize the vote/direction join (ensemble_votes.vote is
            # uppercase per EnsembleVote; trade_intelligence.direction is the
            # mixed-case Side enum value).
            rows = await db.fetch_all(
                """
                WITH ti_latest AS (
                    SELECT setup_id, entry_regime, direction, win, pnl_pct
                    FROM trade_intelligence
                    WHERE rowid IN (
                        SELECT MAX(rowid) FROM trade_intelligence
                        WHERE setup_id IS NOT NULL GROUP BY setup_id
                    )
                      AND entry_regime IS NOT NULL
                      AND entry_regime != ''
                ),
                pairs AS (
                    SELECT DISTINCT ev.strategy_name AS strategy_name,
                           ev.setup_id              AS setup_id
                    FROM ensemble_votes ev
                    JOIN ti_latest ti ON ev.setup_id = ti.setup_id
                    WHERE ev.strategy_name IS NOT NULL
                      AND UPPER(ev.vote) = UPPER(ti.direction)
                )
                SELECT p.strategy_name              AS strategy_name,
                       ti.entry_regime              AS regime,
                       COUNT(*)                     AS sample_size,
                       SUM(ti.pnl_pct)              AS sum_pnl_pct,
                       SUM(CASE WHEN ti.win = 1 THEN 1 ELSE 0 END) AS wins
                FROM pairs p
                JOIN ti_latest ti ON p.setup_id = ti.setup_id
                GROUP BY p.strategy_name, ti.entry_regime
                """
            )
        except Exception as e:
            log.error(
                f"RW_REFRESH_FAIL | err='{str(e)[:120]}' "
                f"cached_cells={len(self._cells)} | {ctx()}"
            )
            return 0

        if not rows:
            # No persisted joinable data yet — cold-start. Cached factors
            # stay at whatever they were (initially nothing, so callers
            # get the 1.0 fallback from get_factor).
            self._refresh_count += 1
            self._last_refresh_ts = time.time()
            log.info(
                f"RW_REFRESH_COLD_START | rows=0 cached_cells={len(self._cells)} "
                f"refresh_n={self._refresh_count} | {ctx()}"
            )
            return 0

        # Recompute every cell present in the query result. Cells absent
        # from the new query (no longer have supporting trades) keep their
        # previous factor — we don't reset to 1.0 just because the join
        # window happened to not include them this cycle.
        new_regime_weights: dict[str, dict[str, float]] = {}
        new_cells: dict[tuple[str, str], CellPerformance] = {}
        drift_sum = 0.0
        drift_count = 0

        for row in rows:
            strategy_name = str(row["strategy_name"])
            regime = str(row["regime"])
            sample_size = int(row["sample_size"] or 0)
            sum_pnl_pct = float(row["sum_pnl_pct"] or 0.0)
            wins = int(row["wins"] or 0)
            avg_pnl_pct = (sum_pnl_pct / sample_size) if sample_size > 0 else 0.0
            win_rate = (wins / sample_size) if sample_size > 0 else 0.0

            # Compute this refresh's raw factor.
            if sample_size < self._cold_start_n:
                factor_raw = 1.0  # cold-start: equal weight until evidence
            else:
                factor_raw = max(
                    self._floor,
                    min(
                        self._ceil,
                        1.0 + self._sensitivity * avg_pnl_pct,
                    ),
                )

            # EMA smoothing against the previous factor for this cell.
            previous = self.get_factor(regime, strategy_name)
            factor_smoothed = (
                self._ema_alpha * factor_raw
                + (1.0 - self._ema_alpha) * previous
            )
            # Re-clamp post-smoothing as the EMA can drift slightly out
            # of the bounds if previous was near an edge.
            factor_smoothed = max(self._floor, min(self._ceil, factor_smoothed))

            new_regime_weights.setdefault(regime, {})[strategy_name] = factor_smoothed
            new_cells[(regime, strategy_name)] = CellPerformance(
                strategy_name=strategy_name,
                regime=regime,
                sample_size=sample_size,
                supporting_pnl_pct=sum_pnl_pct,
                supporting_wins=wins,
                avg_pnl_pct=avg_pnl_pct,
                win_rate=win_rate,
                factor_computed=factor_raw,
                factor_smoothed=factor_smoothed,
            )

            drift_sum += abs(factor_smoothed - previous)
            drift_count += 1

        # Carry forward any cells that disappeared from the query result
        # (no supporting trades in this window) so they're not lost from
        # the cache. They keep their previous factor — only matters when
        # the cache outlives the window of trades that produced it.
        for (regime, strategy_name), prev_cell in self._cells.items():
            if (regime, strategy_name) not in new_cells:
                new_regime_weights.setdefault(regime, {})[strategy_name] = (
                    prev_cell.factor_smoothed
                )
                new_cells[(regime, strategy_name)] = prev_cell

        self._regime_weights = new_regime_weights
        self._cells = new_cells
        self._refresh_count += 1
        self._last_refresh_ts = time.time()
        self._last_drift_avg = (drift_sum / drift_count) if drift_count > 0 else 0.0

        cold_count = sum(
            1 for c in self._cells.values()
            if c.sample_size < self._cold_start_n
        )
        # Issue #19 observability (2026-05-27): surface the live weight spread
        # among data-derived cells so the operator can see weights now differ
        # from 1.0 and by how much. A range tight around 1.0 confirms the gradual
        # ramp; a widening range as track record accumulates is expected.
        _dd = [c.factor_smoothed for c in self._cells.values()
               if c.sample_size >= self._cold_start_n]
        _fmin = min(_dd) if _dd else 1.0
        _fmax = max(_dd) if _dd else 1.0
        # Grain-mismatch fix observability (2026-06-09): surface the average
        # data-derived win_rate so operators can confirm the de-dup is live — a
        # sane ~0.5 (near the trade base rate) rather than the pre-fix ~0.9
        # inflation. A win_rate_avg back near 0.9 would mean the de-dup regressed.
        _wr = [c.win_rate for c in self._cells.values()
               if c.sample_size >= self._cold_start_n]
        _wr_avg = (sum(_wr) / len(_wr)) if _wr else 0.0
        log.info(
            f"RW_REFRESH_OK | cells={len(self._cells)} "
            f"data_derived={len(self._cells) - cold_count} cold_start={cold_count} "
            f"factor_range=[{_fmin:.2f},{_fmax:.2f}] "
            f"win_rate_avg={_wr_avg:.3f} "
            f"drift_avg={self._last_drift_avg:.3f} "
            f"refresh_n={self._refresh_count} | {ctx()}"
        )
        return len(self._cells)

    def audit(self) -> dict[str, Any]:
        """Rule 16 self-check summary.

        Returns:
            Dict with keys:
              - permanent_silence_violations: list[str] — strategies whose
                factor is at the floor in EVERY regime they appear in
                (effectively silenced; should be empty).
              - regime_independent: bool — True if every regime's weight
                vector is identical (mechanism has degenerated to flat).
              - cold_start_cells: int — count of cells still on equal weight.
              - data_derived_cells: int — count of cells past threshold.
              - last_drift_avg: float — mean |new - prev| factor change at
                the last refresh (high values on thin data signal noise).
              - refresh_count: int — total refreshes since boot.
        """
        # Permanent silence: a strategy is at floor in EVERY regime it appears
        per_strategy_factors: dict[str, list[float]] = {}
        for cell in self._cells.values():
            per_strategy_factors.setdefault(cell.strategy_name, []).append(
                cell.factor_smoothed,
            )
        silenced = [
            name for name, factors in per_strategy_factors.items()
            if factors and all(abs(f - self._floor) < 1e-6 for f in factors)
        ]

        # Regime independence: all regime weight vectors identical
        regime_vectors = [tuple(sorted(w.items())) for w in self._regime_weights.values()]
        regime_independent = len(set(regime_vectors)) <= 1 if regime_vectors else False

        cold_count = sum(
            1 for c in self._cells.values()
            if c.sample_size < self._cold_start_n
        )

        return {
            "permanent_silence_violations": silenced,
            "regime_independent": regime_independent,
            "cold_start_cells": cold_count,
            "data_derived_cells": len(self._cells) - cold_count,
            "last_drift_avg": self._last_drift_avg,
            "refresh_count": self._refresh_count,
            "last_refresh_ts": self._last_refresh_ts,
        }

    def log_audit(self) -> None:
        """Emit the audit summary as a structured log event.

        Loud-on-violation: RULE16_RW_PERMANENT_SILENCE at ERROR if any
        strategy is silenced in every regime; RULE16_RW_REGIME_INDEPENDENT
        at ERROR if the mechanism has degenerated to flat weighting.
        Otherwise RW_AUDIT_OK at INFO for visibility.
        """
        a = self.audit()
        if a["permanent_silence_violations"]:
            log.error(
                f"RULE16_RW_PERMANENT_SILENCE | "
                f"silenced_count={len(a['permanent_silence_violations'])} "
                f"sample={a['permanent_silence_violations'][:5]} | {ctx()}"
            )
        if a["regime_independent"] and len(self._regime_weights) > 1:
            log.error(
                f"RULE16_RW_REGIME_INDEPENDENT | "
                f"regime_count={len(self._regime_weights)} "
                f"effect=mechanism_degenerated_to_flat_weighting | {ctx()}"
            )
        log.info(
            f"RW_AUDIT_OK | data_derived={a['data_derived_cells']} "
            f"cold_start={a['cold_start_cells']} "
            f"drift={a['last_drift_avg']:.3f} "
            f"refresh_n={a['refresh_count']} | {ctx()}"
        )
