"""Learning repository: strategy performance, signal accuracy, patterns, brain decisions."""

import json

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")


class LearningRepository:
    """Repository for learning and performance tracking data.

    Args:
        db: Active DatabaseManager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Strategy Performance ---

    async def save_strategy_performance(self, strategy: str, symbol: str, stats: dict) -> None:
        """Upsert strategy performance stats."""
        await self._db.execute(
            """INSERT OR REPLACE INTO strategy_performance
            (strategy, symbol, timeframe, total_trades, winning_trades, losing_trades,
             win_rate, avg_pnl, avg_pnl_pct, max_drawdown, sharpe_ratio, profit_factor, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy, symbol, stats.get("timeframe", "all"),
             stats.get("total_trades", 0), stats.get("winning_trades", 0),
             stats.get("losing_trades", 0), stats.get("win_rate", 0),
             stats.get("avg_pnl", 0), stats.get("avg_pnl_pct", 0),
             stats.get("max_drawdown", 0), stats.get("sharpe_ratio"),
             stats.get("profit_factor"), now_utc().isoformat()),
        )

    async def get_strategy_performance(self, strategy: str | None = None) -> list[dict]:
        """Get performance for one or all strategies."""
        if strategy:
            rows = await self._db.fetch_all(
                "SELECT * FROM strategy_performance WHERE strategy = ? ORDER BY updated_at DESC",
                (strategy,),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM strategy_performance ORDER BY win_rate DESC"
            )
        return [dict(r) for r in rows]

    async def update_strategy_stats(self, strategy: str, symbol: str, pnl: float, was_win: bool) -> None:
        """Increment trade count and recalculate win rate."""
        existing = await self._db.fetch_one(
            "SELECT * FROM strategy_performance WHERE strategy = ? AND symbol = ?",
            (strategy, symbol),
        )
        if existing:
            total = existing["total_trades"] + 1
            wins = existing["winning_trades"] + (1 if was_win else 0)
            losses = existing["losing_trades"] + (0 if was_win else 1)
            prev_avg = existing["avg_pnl"] or 0
            new_avg = ((prev_avg * (total - 1)) + pnl) / total
            win_rate = wins / total if total > 0 else 0

            await self._db.execute(
                """UPDATE strategy_performance SET total_trades=?, winning_trades=?, losing_trades=?,
                   win_rate=?, avg_pnl=?, updated_at=? WHERE strategy=? AND symbol=?""",
                (total, wins, losses, win_rate, new_avg, now_utc().isoformat(), strategy, symbol),
            )
        else:
            await self.save_strategy_performance(strategy, symbol, {
                "total_trades": 1, "winning_trades": 1 if was_win else 0,
                "losing_trades": 0 if was_win else 1,
                "win_rate": 1.0 if was_win else 0.0, "avg_pnl": pnl,
            })

    # --- Signal Accuracy ---

    async def save_signal_accuracy(self, signal_type: str, symbol: str, predicted: str,
                                   confidence: float, price_at_signal: float) -> int:
        """Record a signal for later accuracy tracking. Returns row ID."""
        cursor = await self._db.execute(
            """INSERT INTO signal_accuracy
            (signal_type, symbol, predicted_direction, confidence, price_at_signal)
            VALUES (?, ?, ?, ?, ?)""",
            (signal_type, symbol, predicted, confidence, price_at_signal),
        )
        return cursor.lastrowid or 0

    async def update_signal_outcome(self, signal_id: int, actual_direction: str, prices: dict) -> None:
        """Update signal with actual outcome."""
        was_correct = 1 if actual_direction == (await self._db.fetch_one(
            "SELECT predicted_direction FROM signal_accuracy WHERE id = ?", (signal_id,)
        ) or {}).get("predicted_direction") else 0

        await self._db.execute(
            """UPDATE signal_accuracy SET actual_direction=?, price_after_1h=?, price_after_4h=?,
               price_after_24h=?, was_correct=? WHERE id=?""",
            (actual_direction, prices.get("1h"), prices.get("4h"), prices.get("24h"),
             was_correct, signal_id),
        )

    async def get_signal_accuracy_stats(self, signal_type: str | None = None) -> dict:
        """Aggregate signal accuracy statistics."""
        if signal_type:
            rows = await self._db.fetch_all(
                "SELECT * FROM signal_accuracy WHERE signal_type = ? AND was_correct IS NOT NULL",
                (signal_type,),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM signal_accuracy WHERE was_correct IS NOT NULL"
            )
        total = len(rows)
        correct = sum(1 for r in rows if r.get("was_correct") == 1)
        return {
            "total_signals": total,
            "correct_count": correct,
            "accuracy_pct": round(correct / total * 100, 1) if total > 0 else 0,
            "avg_confidence": round(sum(r.get("confidence", 0) for r in rows) / total, 3) if total > 0 else 0,
        }

    # --- Pattern Log ---

    async def save_pattern(self, pattern_type: str, symbol: str, context: dict, confidence: float) -> int:
        """Log a detected pattern. Returns row ID."""
        cursor = await self._db.execute(
            "INSERT INTO pattern_log (pattern_type, symbol, context_json, confidence) VALUES (?, ?, ?, ?)",
            (pattern_type, symbol, json.dumps(context), confidence),
        )
        return cursor.lastrowid or 0

    async def update_pattern_outcome(self, pattern_id: int, outcome: dict) -> None:
        """Update pattern with outcome."""
        await self._db.execute(
            "UPDATE pattern_log SET outcome_json=?, resolved_at=? WHERE id=?",
            (json.dumps(outcome), now_utc().isoformat(), pattern_id),
        )

    async def get_pattern_outcomes(self, pattern_type: str | None = None, symbol: str | None = None) -> list[dict]:
        """Get pattern outcomes with optional filtering."""
        sql = "SELECT * FROM pattern_log WHERE outcome_json IS NOT NULL"
        params: list = []
        if pattern_type:
            sql += " AND pattern_type = ?"
            params.append(pattern_type)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY detected_at DESC LIMIT 50"
        rows = await self._db.fetch_all(sql, tuple(params))
        return [dict(r) for r in rows]

    # --- Brain Decisions ---

    async def save_brain_decision(self, prompt_hash: str, market_state: dict, claude_response: str,
                                  decision: dict, tokens_used: int = 0, cost_usd: float = 0.0,
                                  trigger: str = "scheduled") -> int:
        """Log a brain decision. Returns row ID."""
        cursor = await self._db.execute(
            """INSERT INTO brain_decisions
            (prompt_hash, market_state_json, claude_response, decision_json, tokens_used, cost_usd, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (prompt_hash, json.dumps(market_state), claude_response,
             json.dumps(decision), tokens_used, cost_usd, trigger),
        )
        return cursor.lastrowid or 0

    async def update_brain_decision_outcome(self, decision_id: int, action_taken: str, outcome: dict) -> None:
        """Update brain decision with execution outcome."""
        await self._db.execute(
            "UPDATE brain_decisions SET action_taken=?, outcome_json=? WHERE id=?",
            (action_taken, json.dumps(outcome), decision_id),
        )

    async def get_brain_decisions(self, limit: int = 20) -> list[dict]:
        """Get recent brain decisions."""
        rows = await self._db.fetch_all(
            "SELECT * FROM brain_decisions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    async def get_brain_cost_today(self) -> float:
        """Get total Claude API cost for today."""
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM brain_decisions WHERE DATE(created_at) = DATE('now')"
        )
        return float(row["total"]) if row else 0.0
