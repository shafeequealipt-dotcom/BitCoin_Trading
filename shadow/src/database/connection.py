"""SQLite database connection manager with async support via aiosqlite.

Mirrors the main project's DatabaseManager pattern: WAL mode, foreign keys,
async lock for thread safety, dict-style row access, transaction context manager.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import aiosqlite

from src.utils.logging import get_logger

log = get_logger("database")


class ShadowDatabaseError(Exception):
    """Base exception for Shadow database operations."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


class DatabaseManager:
    """Async SQLite connection manager.

    Args:
        db_path: Absolute path to the SQLite database file.
        wal_mode: Enable WAL mode for concurrent reads during writes.
    """

    def __init__(self, db_path: str, wal_mode: bool = True) -> None:
        self.db_path = db_path
        self.wal_mode = wal_mode
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the database connection and configure pragmas."""
        # Ensure the data/ directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        try:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row

            if self.wal_mode:
                await self._db.execute("PRAGMA journal_mode=WAL")
                await self._db.execute("PRAGMA journal_size_limit=67108864")  # 64MB
            await self._db.execute("PRAGMA foreign_keys=ON")

            log.info("Database connected: {path}", path=self.db_path)
        except Exception as e:
            raise ShadowDatabaseError(
                f"Failed to connect to database: {e}",
                details={"path": self.db_path},
            ) from e

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            log.info("Database disconnected")

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the active connection, raising if not connected."""
        if self._db is None:
            raise ShadowDatabaseError("Database not connected. Call connect() first.")
        return self._db

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement and commit.

        Args:
            sql: SQL query string.
            params: Query parameters.

        Returns:
            The cursor after execution.
        """
        try:
            async with self._lock:
                cursor = await self.db.execute(sql, params)
                await self.db.commit()
                return cursor
        except ShadowDatabaseError:
            raise
        except Exception as e:
            raise ShadowDatabaseError(
                f"Execute failed: {e}", details={"sql": sql[:200]}
            ) from e

    async def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        """Execute a SQL statement for each set of params.

        Args:
            sql: SQL query string with placeholders.
            params_list: List of parameter tuples.
        """
        try:
            async with self._lock:
                await self.db.executemany(sql, params_list)
                await self.db.commit()
        except ShadowDatabaseError:
            raise
        except Exception as e:
            raise ShadowDatabaseError(
                f"Executemany failed: {e}", details={"sql": sql[:200]}
            ) from e

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dict.

        Args:
            sql: SELECT query.
            params: Query parameters.

        Returns:
            Dict of column_name -> value, or None if no row found.
        """
        try:
            cursor = await self.db.execute(sql, params)
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)
        except ShadowDatabaseError:
            raise
        except Exception as e:
            raise ShadowDatabaseError(
                f"Fetch one failed: {e}", details={"sql": sql[:200]}
            ) from e

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dicts.

        Args:
            sql: SELECT query.
            params: Query parameters.

        Returns:
            List of dicts.
        """
        try:
            cursor = await self.db.execute(sql, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except ShadowDatabaseError:
            raise
        except Exception as e:
            raise ShadowDatabaseError(
                f"Fetch all failed: {e}", details={"sql": sql[:200]}
            ) from e

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for explicit transactions.

        Commits on success, rolls back on exception.
        """
        async with self._lock:
            try:
                yield self.db
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise
