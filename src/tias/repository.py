"""TIAS repository: save and query trade intelligence records."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.tias.models import TradeIntelligence

log = get_logger("tias")


class TradeIntelligenceRepo:
    """Repository for trade_intelligence table persistence.

    Args:
        db: Active DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def save(self, trade: TradeIntelligence) -> int:
        """INSERT a new trade intelligence record. Returns the row ID.

        Args:
            trade: Fully-populated (or partially-populated) TradeIntelligence instance.

        Returns:
            The SQLite rowid of the inserted row.
        """
        data = asdict(trade)
        # SQLite stores booleans as INTEGER (0/1)
        data["win"] = 1 if data["win"] else 0
        data["apex_optimized"] = 1 if data.get("apex_optimized") else 0
        data["apex_flipped"] = 1 if data.get("apex_flipped") else 0

        cols = list(data.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        values = tuple(data[c] for c in cols)

        cursor = await self._db.execute(
            f"INSERT INTO trade_intelligence ({col_names}) VALUES ({placeholders})",
            values,
        )
        return cursor.lastrowid or 0

    async def update_analysis(self, row_id: int, analysis: dict) -> bool:
        """UPDATE the Phase 2 DeepSeek analysis fields for a given row.

        Args:
            row_id: Primary key of the row to update.
            analysis: Dict with any subset of ds_* fields.

        Returns:
            True if the row was found and updated.
        """
        allowed = {
            # Phase 1 placeholders
            "ds_why", "ds_what_worked", "ds_what_failed",
            "ds_lessons", "ds_category", "ds_confidence", "ds_analyzed_at",
            # Phase 2 — actionable analysis fields
            "ds_correct_direction", "ds_what_should_done", "ds_how_to_exploit",
            "ds_optimal_direction", "ds_optimal_sl_pct", "ds_optimal_tp_pct",
            "ds_optimal_size_usd", "ds_optimal_leverage",
            # Phase 2 — API response metadata
            "ds_raw_response", "ds_response_time_ms",
            "ds_input_tokens", "ds_output_tokens", "ds_cost_usd",
            "ds_model", "analysis_version",
            # Phase 3 — APEX optimization tracking
            "apex_optimized", "apex_flipped",
            "apex_original_direction", "apex_final_direction",
            "apex_original_sl", "apex_final_sl",
            "apex_original_tp", "apex_final_tp",
            "apex_original_size", "apex_final_size",
            "apex_confidence", "apex_tp_mode", "apex_reasoning",
            "apex_model", "apex_response_ms", "apex_cost_usd",
            "gate_adjustments",
            "apex_tp_fill_rate",
        }
        fields = {k: v for k, v in analysis.items() if k in allowed}
        if not fields:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = tuple(fields.values()) + (row_id,)

        await self._db.execute(
            f"UPDATE trade_intelligence SET {set_clause} WHERE id = ?",
            values,
        )
        return True

    async def update_outcome(
        self, trade_id: str, *, pnl_usd: float, pnl_pct: float, win: bool
    ) -> bool:
        """UPDATE a single trade's realized outcome by trade_id (PnL-truth reconcile).

        Used by the PnL reconciler to correct a provisionally-booked row to the
        exchange-authoritative net once the closed-pnl indexer catches up. This
        is a going-forward correction of ONE row (keyed by the unique trade_id),
        never a history rewrite or a bulk update — the protected trade_intelligence
        table is otherwise untouched. Returns True if a row was updated.
        """
        if not trade_id:
            return False
        cursor = await self._db.execute(
            "UPDATE trade_intelligence SET pnl_usd = ?, pnl_pct = ?, win = ? "
            "WHERE trade_id = ?",
            (float(pnl_usd), float(pnl_pct), 1 if win else 0, trade_id),
        )
        updated = (getattr(cursor, "rowcount", 0) or 0) > 0
        if not updated:
            log.warning(
                "TIAS_UPDATE_OUTCOME_NOROW | trade_id={tid} pnl_usd={u} | "
                "no trade_intelligence row matched (capture may be pending)",
                tid=trade_id, u=round(float(pnl_usd), 4),
            )
        return updated

    async def get_unanalyzed(
        self, limit: int = 10, max_attempts: int = 3
    ) -> list[dict[str, Any]]:
        """Fetch records where DeepSeek analysis has not been run yet.

        Only returns rows where analysis_attempts < max_attempts so permanently
        failed rows (>= 3 attempts) are not retried.

        Args:
            limit: Maximum number of rows to return.
            max_attempts: Skip rows that have already failed this many times.

        Returns:
            List of row dicts ordered by trade_closed_at ASC (oldest first).
        """
        return await self._db.fetch_all(
            """
            SELECT * FROM trade_intelligence
            WHERE ds_why IS NULL
              AND (analysis_attempts IS NULL OR analysis_attempts < ?)
            ORDER BY trade_closed_at ASC
            LIMIT ?
            """,
            (max_attempts, limit),
        )

    async def increment_attempts(self, row_id: int) -> None:
        """Increment the analysis_attempts counter for a row.

        Args:
            row_id: Primary key of the row to increment.
        """
        await self._db.execute(
            """
            UPDATE trade_intelligence
            SET analysis_attempts = COALESCE(analysis_attempts, 0) + 1
            WHERE id = ?
            """,
            (row_id,),
        )

    async def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch the most recently captured trade intelligence records.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            List of row dicts ordered by id DESC.
        """
        return await self._db.fetch_all(
            "SELECT * FROM trade_intelligence ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    async def get_by_symbol(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch trade intelligence records for a specific symbol.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            limit: Maximum number of rows to return.

        Returns:
            List of row dicts ordered by id DESC.
        """
        return await self._db.fetch_all(
            """
            SELECT * FROM trade_intelligence
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol, limit),
        )

    async def count(self) -> dict[str, int]:
        """Return summary counts for the trade_intelligence table.

        Returns:
            Dict with keys: total, wins, losses, analyzed, unanalyzed.
        """
        row = await self._db.fetch_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(win) AS wins,
                SUM(1 - win) AS losses,
                SUM(CASE WHEN ds_why IS NOT NULL THEN 1 ELSE 0 END) AS analyzed,
                SUM(CASE WHEN ds_why IS NULL THEN 1 ELSE 0 END) AS unanalyzed
            FROM trade_intelligence
            """
        )
        if not row:
            return {"total": 0, "wins": 0, "losses": 0, "analyzed": 0, "unanalyzed": 0}
        return {
            "total": row.get("total") or 0,
            "wins": row.get("wins") or 0,
            "losses": row.get("losses") or 0,
            "analyzed": row.get("analyzed") or 0,
            "unanalyzed": row.get("unanalyzed") or 0,
        }

    async def get_stats(self) -> dict[str, Any]:
        """Get summary statistics for the TIAS dashboard (/tias_cost, /tias_patterns).

        Returns:
            Dict with total, analyzed, pending, failed, wins, losses,
            total_cost, avg_response_ms.
        """
        row = await self._db.fetch_one(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN ds_why IS NOT NULL THEN 1 ELSE 0 END) AS analyzed,
                SUM(CASE WHEN ds_why IS NULL
                    AND (analysis_attempts IS NULL OR analysis_attempts < 3)
                    THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN ds_why IS NULL AND analysis_attempts >= 3
                    THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END) AS losses,
                ROUND(SUM(COALESCE(ds_cost_usd, 0)), 4) AS total_cost,
                ROUND(AVG(CASE WHEN ds_response_time_ms IS NOT NULL
                    THEN ds_response_time_ms END), 0) AS avg_response_ms
            FROM trade_intelligence
            """
        )
        if not row:
            return {
                "total": 0, "analyzed": 0, "pending": 0, "failed": 0,
                "wins": 0, "losses": 0, "total_cost": 0.0, "avg_response_ms": 0,
            }
        return {
            "total": row.get("total") or 0,
            "analyzed": row.get("analyzed") or 0,
            "pending": row.get("pending") or 0,
            "failed": row.get("failed") or 0,
            "wins": row.get("wins") or 0,
            "losses": row.get("losses") or 0,
            "total_cost": row.get("total_cost") or 0.0,
            "avg_response_ms": row.get("avg_response_ms") or 0,
        }

    async def get_category_breakdown(self) -> list[dict[str, Any]]:
        """Get category distribution from analyzed trades (for /tias_patterns).

        Returns:
            List of dicts with ds_category, count, win_pct, avg_pnl, ordered by count DESC.
        """
        return await self._db.fetch_all(
            """
            SELECT
                ds_category,
                COUNT(*) AS count,
                ROUND(AVG(CASE WHEN win = 1 THEN 1.0 ELSE 0.0 END) * 100, 0) AS win_pct,
                ROUND(AVG(pnl_pct), 2) AS avg_pnl
            FROM trade_intelligence
            WHERE ds_category IS NOT NULL
            GROUP BY ds_category
            ORDER BY count DESC
            """
        )

    async def get_symbol_intelligence(self, limit: int = 15) -> list[dict[str, Any]]:
        """Get per-symbol intelligence summary (for /tias_symbols).

        Args:
            limit: Maximum number of symbols to return.

        Returns:
            List of dicts with symbol, trades, wins, losses, avg_pnl,
            total_pnl_usd, categories, ordered by trade count DESC.
        """
        return await self._db.fetch_all(
            """
            SELECT
                symbol,
                COUNT(*) AS trades,
                SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END) AS losses,
                ROUND(AVG(pnl_pct), 3) AS avg_pnl,
                ROUND(SUM(pnl_usd), 2) AS total_pnl_usd,
                GROUP_CONCAT(DISTINCT ds_category) AS categories
            FROM trade_intelligence
            GROUP BY symbol
            ORDER BY trades DESC
            LIMIT ?
            """,
            (limit,),
        )

    async def get_recent_analyses(self, limit: int = 5) -> list[dict[str, Any]]:
        """Get the most recently analyzed trades with DeepSeek verdict (for /tias_last).

        Args:
            limit: Maximum number of rows to return.

        Returns:
            List of dicts with trade outcome + truncated DeepSeek fields,
            ordered by ds_analyzed_at DESC.
        """
        return await self._db.fetch_all(
            """
            SELECT
                id, symbol, direction, pnl_pct, pnl_usd, win,
                closed_by, entry_score, strategy_name, regime,
                ds_category, ds_correct_direction, ds_confidence,
                substr(ds_why, 1, 200) AS ds_why_short,
                substr(ds_what_should_done, 1, 200) AS should_done_short,
                trade_closed_at, ds_analyzed_at
            FROM trade_intelligence
            WHERE ds_why IS NOT NULL
            ORDER BY ds_analyzed_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    # =========================================================================
    # APEX-specific query methods
    # These methods serve the IntelligenceAssembler in src/apex/assembler.py.
    # They query the same trade_intelligence table but shape data for DeepSeek
    # optimization rather than TIAS internal reporting.
    # =========================================================================

    async def get_symbol_full_history(
        self, symbol: str, limit: int = 20, regime: str | None = None,
    ) -> dict[str, Any]:
        """Comprehensive trade history for a symbol — used by APEX IntelligenceAssembler.

        Returns aggregate performance stats plus the individual trade records
        (with ds_* DeepSeek analysis columns) so DeepSeek can learn from both the
        numbers and the qualitative analysis for this specific coin.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT").
            limit: Maximum number of individual trades to include.

        Returns:
            Dict with keys: total, wins, losses, win_rate, avg_win_pct,
            avg_loss_pct, total_pnl_usd, ev_per_trade, trades (list of dicts).
            Returns safe empty defaults on any error.
        """
        empty: dict[str, Any] = {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "total_pnl_usd": 0.0,
            "ev_per_trade": 0.0,
            "trades": [],
        }
        try:
            # Build WHERE clause — optionally filter by regime
            _where = "WHERE symbol = ?"
            _params: list = [symbol]
            if regime:
                _where += " AND regime = ?"
                _params.append(regime)

            stats = await self._db.fetch_one(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN win = 0 THEN 1 ELSE 0 END) AS losses,
                    ROUND(AVG(CASE WHEN win = 1 THEN 1.0 ELSE 0.0 END) * 100, 1)
                        AS win_rate,
                    ROUND(AVG(CASE WHEN win = 1 THEN pnl_pct ELSE NULL END), 3)
                        AS avg_win_pct,
                    ROUND(AVG(CASE WHEN win = 0 THEN pnl_pct ELSE NULL END), 3)
                        AS avg_loss_pct,
                    ROUND(SUM(pnl_usd), 2) AS total_pnl_usd,
                    ROUND(AVG(pnl_usd), 4) AS ev_per_trade
                FROM trade_intelligence
                {_where}
                """,
                tuple(_params),
            )
            if not stats or not stats.get("total"):
                return empty

            _trades_params: list = [symbol]
            if regime:
                _trades_params.append(regime)
            _trades_params.append(limit)

            trades = await self._db.fetch_all(
                f"""
                SELECT
                    id, symbol, direction, pnl_pct, pnl_usd, win,
                    strategy_name, closed_by, regime, hold_seconds,
                    ds_category, ds_correct_direction, ds_optimal_direction,
                    ds_optimal_sl_pct, ds_optimal_tp_pct,
                    ds_optimal_size_usd, ds_confidence,
                    ds_what_should_done, ds_how_to_exploit,
                    trade_closed_at
                FROM trade_intelligence
                {_where}
                ORDER BY COALESCE(regime_verified, 0) DESC, id DESC
                LIMIT ?
                """,
                tuple(_trades_params),
            )

            return {
                "total": stats.get("total") or 0,
                "wins": stats.get("wins") or 0,
                "losses": stats.get("losses") or 0,
                "win_rate": stats.get("win_rate") or 0.0,
                "avg_win_pct": stats.get("avg_win_pct") or 0.0,
                "avg_loss_pct": stats.get("avg_loss_pct") or 0.0,
                "total_pnl_usd": stats.get("total_pnl_usd") or 0.0,
                "ev_per_trade": stats.get("ev_per_trade") or 0.0,
                "trades": trades,
            }

        except Exception as e:
            log.warning(
                "TIAS_REPO_SYM_HIST_FAIL | sym={sym} err='{err}'",
                sym=symbol,
                err=str(e)[:200],
            )
            return empty

    async def get_symbol_flip_evidence(
        self,
        symbol: str,
        regime: str | None = None,
        exchange_mode: str = "",
    ) -> dict[str, Any]:
        """Per-symbol, per-venue directional evidence — used by APEX (E26).

        Unlike ``get_situation_stats`` (ALL coins) and
        ``get_symbol_full_history`` (this coin but venue-POOLED), this counts
        trades for THIS symbol in the given regime, optionally filtered to ONE
        ``exchange_mode``, broken down by direction. Pooling demo/live/paper
        history could license a direction flip on the wrong venue's record;
        isolating by venue closes that latent gap (E26, 2026-05-28).

        Fail-permissive: an empty ``exchange_mode`` applies NO venue filter
        (pooled) so a missing live mode never blocks; callers treat a pooled
        result as non-authoritative.

        Args:
            symbol: trading pair (e.g. "BTCUSDT").
            regime: optional regime filter (None/"" = all regimes).
            exchange_mode: optional venue filter ("" = pooled, no filter).

        Returns:
            Dict with keys: symbol, exchange_mode, regime, total, buy_count,
            sell_count, buy_win_rate (0-100), sell_win_rate (0-100).
            Safe empty defaults (counts 0, rates 0.0) on any error.
        """
        empty: dict[str, Any] = {
            "symbol": symbol,
            "exchange_mode": exchange_mode,
            "regime": regime or "",
            "total": 0,
            "buy_count": 0,
            "sell_count": 0,
            "buy_win_rate": 0.0,
            "sell_win_rate": 0.0,
        }
        try:
            _where = "WHERE symbol = ?"
            _params: list = [symbol]
            if regime:
                _where += " AND regime = ?"
                _params.append(regime)
            if exchange_mode:
                _where += " AND exchange_mode = ?"
                _params.append(exchange_mode)

            row = await self._db.fetch_one(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction = 'Buy' THEN 1 ELSE 0 END)
                        AS buy_count,
                    SUM(CASE WHEN direction = 'Sell' THEN 1 ELSE 0 END)
                        AS sell_count,
                    ROUND(AVG(CASE
                        WHEN direction = 'Buy' AND win = 1 THEN 1.0
                        WHEN direction = 'Buy' AND win = 0 THEN 0.0
                        ELSE NULL END) * 100, 1) AS buy_win_rate,
                    ROUND(AVG(CASE
                        WHEN direction = 'Sell' AND win = 1 THEN 1.0
                        WHEN direction = 'Sell' AND win = 0 THEN 0.0
                        ELSE NULL END) * 100, 1) AS sell_win_rate
                FROM trade_intelligence
                {_where}
                """,
                tuple(_params),
            )
            if not row or not row.get("total"):
                return empty
            return {
                "symbol": symbol,
                "exchange_mode": exchange_mode,
                "regime": regime or "",
                "total": row.get("total") or 0,
                "buy_count": row.get("buy_count") or 0,
                "sell_count": row.get("sell_count") or 0,
                "buy_win_rate": row.get("buy_win_rate") or 0.0,
                "sell_win_rate": row.get("sell_win_rate") or 0.0,
            }
        except Exception as e:
            log.warning(
                "TIAS_REPO_FLIP_EVIDENCE_FAIL | sym={sym} mode={m} err='{err}'",
                sym=symbol,
                m=exchange_mode or "-",
                err=str(e)[:200],
            )
            return empty

    async def get_situation_stats(
        self, regime: str, fear_greed: int
    ) -> dict[str, Any]:
        """Performance stats across ALL coins in similar market conditions — used by APEX.

        Queries trades in the same regime with a ±10 point Fear & Greed window.
        Computes directional bias (buy vs sell), average PnL by direction, and
        the most common DeepSeek failure categories in these conditions. The
        common-categories list is filtered to LOSING trades (win = 0) so it
        reflects genuine failures only — issue #2 fix (2026-05-25). Pre-fix the
        list had no outcome filter, so success categories (e.g. CORRECT_ENTRY)
        dominated it and were rendered to the optimizer as "common issues".

        DeepSeek uses this data to understand what historically works in the
        CURRENT market conditions across all coins, not just the target symbol.

        Args:
            regime: Market regime string (e.g. "trending", "ranging", "dead").
            fear_greed: Current Fear & Greed index value (0-100).

        Returns:
            Dict with keys: total, buy_win_rate, sell_win_rate, avg_buy_pnl,
            avg_sell_pnl, direction_bias, common_categories (list of strings).
            Returns safe empty defaults on any error.
        """
        empty: dict[str, Any] = {
            "total": 0,
            "buy_win_rate": 0.0,
            "sell_win_rate": 0.0,
            "avg_buy_pnl": 0.0,
            "avg_sell_pnl": 0.0,
            "direction_bias": "neutral",
            "common_categories": [],
        }
        try:
            fg_low = max(0, fear_greed - 10)
            fg_high = min(100, fear_greed + 10)

            row = await self._db.fetch_one(
                """
                SELECT
                    COUNT(*) AS total,
                    ROUND(AVG(CASE
                        WHEN direction = 'Buy' AND win = 1 THEN 1.0
                        WHEN direction = 'Buy' AND win = 0 THEN 0.0
                        ELSE NULL END) * 100, 1) AS buy_win_rate,
                    ROUND(AVG(CASE
                        WHEN direction = 'Sell' AND win = 1 THEN 1.0
                        WHEN direction = 'Sell' AND win = 0 THEN 0.0
                        ELSE NULL END) * 100, 1) AS sell_win_rate,
                    ROUND(AVG(CASE WHEN direction = 'Buy'
                        THEN pnl_pct ELSE NULL END), 3) AS avg_buy_pnl,
                    ROUND(AVG(CASE WHEN direction = 'Sell'
                        THEN pnl_pct ELSE NULL END), 3) AS avg_sell_pnl
                FROM trade_intelligence
                WHERE regime = ?
                  AND fear_greed_value BETWEEN ? AND ?
                """,
                (regime, fg_low, fg_high),
            )
            if not row or not row.get("total"):
                return empty

            buy_wr = row.get("buy_win_rate") or 0.0
            sell_wr = row.get("sell_win_rate") or 0.0

            # Direction bias: >10pp difference is considered meaningful
            if buy_wr > sell_wr + 10:
                bias = "buy"
            elif sell_wr > buy_wr + 10:
                bias = "sell"
            else:
                bias = "neutral"

            cats_rows = await self._db.fetch_all(
                """
                SELECT ds_category
                FROM trade_intelligence
                WHERE regime = ?
                  AND fear_greed_value BETWEEN ? AND ?
                  AND win = 0
                  AND ds_category IS NOT NULL
                GROUP BY ds_category
                ORDER BY COUNT(*) DESC
                LIMIT 5
                """,
                (regime, fg_low, fg_high),
            )
            common_categories = [r["ds_category"] for r in cats_rows if r.get("ds_category")]

            # Issue #2 sentinel: the list is now failure-filtered (win = 0). Logged
            # at debug to keep per-call volume low; the manager boot sentinel
            # (TIAS_CATEGORY_CONTRACT) is the headline confirmation the fix is live.
            log.debug(
                "APEX_SITUATION_FAILCATS | regime={r} fg={fg} filter=win0 failcats={c}",
                r=regime, fg=fear_greed, c=common_categories,
            )

            return {
                "total": row.get("total") or 0,
                "buy_win_rate": buy_wr,
                "sell_win_rate": sell_wr,
                "avg_buy_pnl": row.get("avg_buy_pnl") or 0.0,
                "avg_sell_pnl": row.get("avg_sell_pnl") or 0.0,
                "direction_bias": bias,
                "common_categories": common_categories,
            }

        except Exception as e:
            log.warning(
                "TIAS_REPO_SIT_STATS_FAIL | regime={r} fg={fg} err='{err}'",
                r=regime,
                fg=fear_greed,
                err=str(e)[:200],
            )
            return empty
