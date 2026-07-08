"""Watchlist command handlers: /watch, /unwatch, /watchlist."""

from src.core.logging import get_logger
from src.core.utils import format_price
from src.database.connection import DatabaseManager
from src.telegram.router import MessageRouter

log = get_logger("telegram")


class WatchlistHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def add(self, update, context) -> None:
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /watch BTC")
            return
        symbol = MessageRouter._normalize_symbol(args[0])
        try:
            await self.db.execute(
                "INSERT OR IGNORE INTO watchlists (name, symbols_json) VALUES ('default', '[]')",
            )
            row = await self.db.fetch_one("SELECT symbols_json FROM watchlists WHERE name = 'default'")
            import json
            symbols = json.loads(row["symbols_json"]) if row else []
            if symbol not in symbols:
                symbols.append(symbol)
                await self.db.execute(
                    "UPDATE watchlists SET symbols_json = ? WHERE name = 'default'",
                    (json.dumps(symbols),),
                )
            await update.message.reply_text(f"\u2705 Added {symbol} to watchlist")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def remove(self, update, context) -> None:
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /unwatch BTC")
            return
        symbol = MessageRouter._normalize_symbol(args[0])
        try:
            row = await self.db.fetch_one("SELECT symbols_json FROM watchlists WHERE name = 'default'")
            import json
            symbols = json.loads(row["symbols_json"]) if row else []
            if symbol in symbols:
                symbols.remove(symbol)
                await self.db.execute(
                    "UPDATE watchlists SET symbols_json = ? WHERE name = 'default'",
                    (json.dumps(symbols),),
                )
            await update.message.reply_text(f"\u274c Removed {symbol} from watchlist")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def show(self, update, context) -> None:
        try:
            row = await self.db.fetch_one("SELECT symbols_json FROM watchlists WHERE name = 'default'")
            import json
            symbols = json.loads(row["symbols_json"]) if row else []
            if not symbols:
                await update.message.reply_text("\U0001f4cb Watchlist empty. Add with /watch BTC")
                return
            msg = "\U0001f4cb <b>WATCHLIST</b>\n\n"
            for sym in symbols:
                try:
                    ticker = await self.s["market_service"].get_ticker(sym)
                    msg += f"\u2022 <b>{sym}</b>: ${format_price(ticker.last_price)} ({ticker.change_24h_pct:+.2f}%)\n"
                except Exception:
                    msg += f"\u2022 {sym}\n"
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
