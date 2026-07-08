"""System tools: health status, worker status, preferences (3 tools)."""

import os
from typing import Callable
from mcp.types import Tool, TextContent
from src.database.connection import DatabaseManager


def register_system_tools(services: dict, db: DatabaseManager, alert_manager=None) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 3 system tools."""
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 41. get_system_status
    tools.append(Tool(name="get_system_status",
        description="Get overall system health: worker status, database status, API connections, and errors.",
        inputSchema={"type": "object", "properties": {}, "required": []}))

    async def _sys_status(args):
        try:
            lines = ["System Status:"]
            # DB check
            db_ok = db is not None and db._db is not None
            lines.append(f"  Database: {'Connected' if db_ok else 'Disconnected'}")
            if db_ok:
                try:
                    db_path = db.db_path
                    if os.path.exists(db_path):
                        size_mb = os.path.getsize(db_path) / (1024 * 1024)
                        lines.append(f"  DB Size: {size_mb:.1f} MB")
                except Exception:
                    pass
            # Bybit check
            bybit = services.get("bybit")
            lines.append(f"  Bybit: {'Connected' if bybit and hasattr(bybit, 'is_connected') and bybit.is_connected else 'Not connected'}")
            if bybit and hasattr(bybit, 'is_testnet'):
                lines.append(f"  Network: {'Testnet' if bybit.is_testnet else 'MAINNET'}")
            # Alert Manager check
            am_status = "Enabled" if alert_manager and alert_manager.enabled else "Disabled"
            lines.append(f"  Alerts: {am_status}")
            # Services check
            available = [k for k, v in services.items() if v is not None]
            lines.append(f"  Services: {len(available)} available ({', '.join(available[:8])})")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_system_status"] = _sys_status

    # 42. get_worker_status
    tools.append(Tool(name="get_worker_status",
        description="Get status of background workers: tick count, errors, uptime.",
        inputSchema={"type": "object", "properties": {"worker_name": {"type": "string"}}, "required": []}))

    async def _worker_status(args):
        try:
            return [TextContent(type="text", text="Worker status is available when workers are running in the same process. Start workers with 'python workers.py' and check data/logs/workers.log for status.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_worker_status"] = _worker_status

    # 43. update_preference
    tools.append(Tool(name="update_preference",
        description="Update a user preference or system setting (e.g., risk tolerance, watchlist).",
        inputSchema={"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}))

    async def _update_pref(args):
        try:
            key = args["key"]
            value = args["value"]
            # Store in DB
            if db:
                try:
                    await db.execute(
                        "CREATE TABLE IF NOT EXISTS user_preferences (key TEXT PRIMARY KEY, value TEXT)"
                    )
                    await db.execute(
                        "INSERT OR REPLACE INTO user_preferences (key, value) VALUES (?, ?)",
                        (key, value),
                    )
                    return [TextContent(type="text", text=f"Preference '{key}' set to '{value}'")]
                except Exception as e:
                    return [TextContent(type="text", text=f"Error saving preference: {e}")]
            return [TextContent(type="text", text="Database not available")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["update_preference"] = _update_pref

    return tools, handlers
