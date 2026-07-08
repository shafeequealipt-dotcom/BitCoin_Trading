"""Context repository: user preferences, watchlists, strategies, session log."""

import json

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")


class ContextRepository:
    """Repository for user context and session management.

    Args:
        db: Active DatabaseManager.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Preferences ---

    async def get_preference(self, key: str) -> str | None:
        """Get a user preference by key."""
        row = await self._db.fetch_one(
            "SELECT value FROM user_preferences WHERE key = ?", (key,)
        )
        return row["value"] if row else None

    async def set_preference(self, key: str, value: str) -> None:
        """Set a user preference (upsert)."""
        await self._db.execute(
            "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now_utc().isoformat()),
        )

    async def get_all_preferences(self) -> dict[str, str]:
        """Get all preferences as a dict."""
        rows = await self._db.fetch_all("SELECT key, value FROM user_preferences")
        return {r["key"]: r["value"] for r in rows}

    async def delete_preference(self, key: str) -> None:
        """Delete a preference."""
        await self._db.execute("DELETE FROM user_preferences WHERE key = ?", (key,))

    # --- Watchlists ---

    async def create_watchlist(self, name: str, symbols: list[str]) -> None:
        """Create a new watchlist."""
        await self._db.execute(
            "INSERT OR REPLACE INTO watchlists (name, symbols_json, updated_at) VALUES (?, ?, ?)",
            (name, json.dumps(symbols), now_utc().isoformat()),
        )

    async def get_watchlist(self, name: str) -> list[str] | None:
        """Get symbols in a watchlist."""
        row = await self._db.fetch_one(
            "SELECT symbols_json FROM watchlists WHERE name = ?", (name,)
        )
        if row is None:
            return None
        try:
            return json.loads(row["symbols_json"])
        except (json.JSONDecodeError, TypeError):
            return []

    async def get_all_watchlists(self) -> dict[str, list[str]]:
        """Get all watchlists."""
        rows = await self._db.fetch_all("SELECT name, symbols_json FROM watchlists")
        result = {}
        for r in rows:
            try:
                result[r["name"]] = json.loads(r["symbols_json"])
            except (json.JSONDecodeError, TypeError):
                result[r["name"]] = []
        return result

    async def update_watchlist(self, name: str, symbols: list[str]) -> None:
        """Update an existing watchlist."""
        await self._db.execute(
            "UPDATE watchlists SET symbols_json = ?, updated_at = ? WHERE name = ?",
            (json.dumps(symbols), now_utc().isoformat(), name),
        )

    async def delete_watchlist(self, name: str) -> None:
        """Delete a watchlist."""
        await self._db.execute("DELETE FROM watchlists WHERE name = ?", (name,))

    # --- Active Strategies ---

    async def set_active_strategy(self, strategy_name: str, symbol: str,
                                  enabled: bool = True, params: dict | None = None) -> None:
        """Enable/disable a strategy for a symbol."""
        await self._db.execute(
            """INSERT OR REPLACE INTO active_strategies
            (strategy_name, symbol, enabled, params_json) VALUES (?, ?, ?, ?)""",
            (strategy_name, symbol, 1 if enabled else 0, json.dumps(params or {})),
        )

    async def get_active_strategies(self, symbol: str | None = None) -> list[dict]:
        """Get active strategies, optionally filtered by symbol."""
        if symbol:
            rows = await self._db.fetch_all(
                "SELECT * FROM active_strategies WHERE symbol = ? AND enabled = 1", (symbol,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM active_strategies WHERE enabled = 1"
            )
        return [dict(r) for r in rows]

    async def disable_strategy(self, strategy_name: str, symbol: str) -> None:
        """Disable a strategy for a symbol."""
        await self._db.execute(
            "UPDATE active_strategies SET enabled = 0 WHERE strategy_name = ? AND symbol = ?",
            (strategy_name, symbol),
        )

    # --- Session Log ---

    async def log_session_event(self, event_type: str, summary: str, details: dict | None = None) -> None:
        """Log a system event."""
        await self._db.execute(
            "INSERT INTO session_log (event_type, summary, details_json) VALUES (?, ?, ?)",
            (event_type, summary, json.dumps(details or {})),
        )

    async def get_session_log(self, event_type: str | None = None, limit: int = 50) -> list[dict]:
        """Get session log entries."""
        if event_type:
            rows = await self._db.fetch_all(
                "SELECT * FROM session_log WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM session_log ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in rows]
