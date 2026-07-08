"""Factory repository: CRUD for discovered patterns, generated strategies, and occurrences."""

import json
from datetime import datetime, timezone

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.factory.models.factory_types import (
    DiscoveredPattern,
    GeneratedStrategy,
    PatternOccurrence,
)

log = get_logger("factory")


class FactoryRepository:
    """CRUD operations for Strategy Factory data.

    Args:
        db: Database manager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Discovered Patterns ---

    async def save_pattern(self, pattern: DiscoveredPattern) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO discovered_patterns "
            "(id, pattern_type, description, conditions_json, symbols_json, "
            "timeframe, direction, occurrences, wins, losses, win_rate, "
            "avg_profit_pct, avg_loss_pct, profit_factor, avg_hold_minutes, "
            "max_drawdown_pct, statistical_significance, regime_consistency_json, "
            "is_valid, data_start_date, data_end_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pattern.id, pattern.pattern_type, pattern.description,
                json.dumps(pattern.conditions), json.dumps(pattern.symbols),
                pattern.timeframe, pattern.direction,
                pattern.occurrences, pattern.wins, pattern.losses, pattern.win_rate,
                pattern.avg_profit_pct, pattern.avg_loss_pct, pattern.profit_factor,
                pattern.avg_hold_minutes, pattern.max_drawdown_pct,
                pattern.statistical_significance,
                json.dumps(pattern.regime_consistency),
                1 if pattern.is_valid else 0,
                pattern.data_start_date, pattern.data_end_date,
            ),
        )

    async def get_pattern(self, pattern_id: str) -> DiscoveredPattern | None:
        row = await self._db.fetch_one(
            "SELECT * FROM discovered_patterns WHERE id = ?", (pattern_id,),
        )
        if not row:
            return None
        return self._row_to_pattern(row)

    async def get_all_patterns(self, valid_only: bool = True) -> list[DiscoveredPattern]:
        query = "SELECT * FROM discovered_patterns"
        if valid_only:
            query += " WHERE is_valid = 1"
        query += " ORDER BY win_rate DESC"
        rows = await self._db.fetch_all(query)
        return [self._row_to_pattern(r) for r in rows]

    async def get_patterns_by_type(self, pattern_type: str) -> list[DiscoveredPattern]:
        rows = await self._db.fetch_all(
            "SELECT * FROM discovered_patterns WHERE pattern_type = ? ORDER BY win_rate DESC",
            (pattern_type,),
        )
        return [self._row_to_pattern(r) for r in rows]

    # --- Generated Strategies ---

    async def save_generated_strategy(self, strategy: GeneratedStrategy) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO generated_strategies "
            "(id, pattern_id, strategy_name, code, claude_model, "
            "generation_cost_usd, generation_attempts, "
            "syntax_valid, safety_valid, interface_valid, "
            "validation_errors_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                strategy.id, strategy.pattern_id, strategy.strategy_name,
                strategy.code, strategy.claude_model,
                strategy.generation_cost_usd, strategy.generation_attempts,
                1 if strategy.syntax_valid else 0,
                1 if strategy.safety_valid else 0,
                1 if strategy.interface_valid else 0,
                json.dumps(strategy.validation_errors),
                strategy.status,
            ),
        )

    async def get_generated_strategy(self, strategy_id: str) -> GeneratedStrategy | None:
        row = await self._db.fetch_one(
            "SELECT * FROM generated_strategies WHERE id = ?", (strategy_id,),
        )
        if not row:
            return None
        return self._row_to_strategy(row)

    async def get_strategies_by_status(self, status: str) -> list[GeneratedStrategy]:
        rows = await self._db.fetch_all(
            "SELECT * FROM generated_strategies WHERE status = ? ORDER BY generated_at DESC",
            (status,),
        )
        return [self._row_to_strategy(r) for r in rows]

    async def update_strategy_status(self, strategy_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE generated_strategies SET status = ?, validated_at = datetime('now') WHERE id = ?",
            (status, strategy_id),
        )

    # --- Pattern Occurrences ---

    async def save_occurrence(self, occ: PatternOccurrence) -> None:
        await self._db.execute(
            "INSERT INTO pattern_occurrences "
            "(pattern_id, symbol, timestamp, conditions_snapshot_json, "
            "price_at_detection, outcome, pnl_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                occ.pattern_id, occ.symbol,
                occ.timestamp.isoformat() if occ.timestamp else now_utc().isoformat(),
                json.dumps(occ.conditions_snapshot),
                occ.price_at_detection, occ.outcome, occ.pnl_pct,
            ),
        )

    async def get_recent_occurrences(
        self, pattern_id: str, hours: int = 48,
    ) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM pattern_occurrences "
            "WHERE pattern_id = ? AND timestamp > datetime('now', ? || ' hours') "
            "ORDER BY timestamp DESC",
            (pattern_id, f"-{hours}"),
        )
        return [dict(r) for r in rows] if rows else []

    async def get_occurrence_count(self, pattern_id: str, hours: int = 24) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM pattern_occurrences "
            "WHERE pattern_id = ? AND timestamp > datetime('now', ? || ' hours')",
            (pattern_id, f"-{hours}"),
        )
        return row["cnt"] if row else 0

    # --- Helpers ---

    @staticmethod
    def _row_to_pattern(row) -> DiscoveredPattern:
        return DiscoveredPattern(
            id=row["id"],
            pattern_type=row["pattern_type"],
            description=row["description"],
            conditions=json.loads(row.get("conditions_json", "{}")),
            symbols=json.loads(row.get("symbols_json", "[]")),
            timeframe=row.get("timeframe", "5"),
            direction=row.get("direction", "long"),
            occurrences=row.get("occurrences", 0),
            wins=row.get("wins", 0),
            losses=row.get("losses", 0),
            win_rate=row.get("win_rate", 0.0),
            avg_profit_pct=row.get("avg_profit_pct", 0.0),
            avg_loss_pct=row.get("avg_loss_pct", 0.0),
            profit_factor=row.get("profit_factor", 0.0),
            statistical_significance=row.get("statistical_significance", 1.0),
            regime_consistency=json.loads(row.get("regime_consistency_json", "{}")),
            is_valid=bool(row.get("is_valid", 0)),
        )

    @staticmethod
    def _row_to_strategy(row) -> GeneratedStrategy:
        return GeneratedStrategy(
            id=row["id"],
            pattern_id=row["pattern_id"],
            strategy_name=row["strategy_name"],
            code=row["code"],
            claude_model=row.get("claude_model", ""),
            generation_cost_usd=row.get("generation_cost_usd", 0.0),
            generation_attempts=row.get("generation_attempts", 1),
            syntax_valid=bool(row.get("syntax_valid", 0)),
            safety_valid=bool(row.get("safety_valid", 0)),
            interface_valid=bool(row.get("interface_valid", 0)),
            validation_errors=json.loads(row.get("validation_errors_json", "[]")),
            status=row.get("status", "generated"),
        )
