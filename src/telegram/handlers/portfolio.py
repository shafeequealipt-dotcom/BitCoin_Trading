"""Portfolio command handlers: /portfolio, /positions, /pnl, /balance."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.telegram.ui.cards import position_card
from src.telegram.ui.formatters import calc_pnl_pct, format_pnl, format_price, format_timestamp

log = get_logger("telegram")


class PortfolioHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def summary(self, update, context) -> None:
        try:
            account = await self.s["account_service"].get_wallet_balance()
            positions = await self.s["position_service"].get_positions()
            pnl_mgr = self.s.get("pnl_manager")
            if pnl_mgr:
                await pnl_mgr.update()

            msg = f"\U0001f4ca <b>PORTFOLIO SUMMARY</b>\n\n"
            msg += f"\U0001f4b0 Equity: <b>{format_price(account.total_equity)}</b>\n"
            msg += f"\U0001f4b5 Available: {format_price(account.available_balance)}\n"
            if pnl_mgr:
                msg += f"\U0001f4c8 Today: <b>{pnl_mgr.current_pnl_pct:+.2f}%</b> (${pnl_mgr.realized_pnl:+,.2f})\n"
                msg += f"\U0001f3af Mode: {pnl_mgr.get_current_mode()['mode']}\n"
            msg += f"\U0001f4cb Positions: {len(positions)}\n\n"

            if positions:
                for pos in positions:
                    pnl_pct = calc_pnl_pct(pos.entry_price, pos.mark_price, pos.side.value)
                    emoji = "\U0001f7e2" if pnl_pct >= 0 else "\U0001f534"
                    side = "LONG" if pos.side.value == "Buy" else "SHORT"
                    msg += f"{emoji} <b>{pos.symbol}</b> {side} {pnl_pct:+.2f}% (${pos.unrealized_pnl:+,.2f})\n"
            else:
                msg += "No open positions.\n"

            msg += f"\n\U0001f550 {format_timestamp()}"
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def positions(self, update, context) -> None:
        try:
            positions = await self.s["position_service"].get_positions()
            if not positions:
                await update.message.reply_text("\U0001f4cb No open positions.")
                return
            for pos in positions:
                card = position_card(pos)
                await update.message.reply_text(card, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def pnl(self, update, context) -> None:
        pnl_mgr = self.s.get("pnl_manager")
        if pnl_mgr:
            await pnl_mgr.update()
            summary = pnl_mgr.get_summary()
            msg = (
                f"\U0001f4b0 <b>TODAY'S PnL</b>\n\n"
                f"Total: <b>{summary['total_pnl_pct']:+.2f}%</b>\n"
                f"Realized: ${summary['realized_pnl']:+,.2f}\n"
                f"Unrealized: ${summary['unrealized_pnl']:+,.2f}\n"
                f"Mode: {summary['mode']}\n"
                f"Target hit: {'Yes' if summary['target_hit'] else 'No'}\n"
                f"\n\U0001f550 {format_timestamp()}"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text("PnL manager not available")

    async def balance(self, update, context) -> None:
        try:
            account = await self.s["account_service"].get_wallet_balance()
            msg = (
                f"\U0001f4b0 <b>ACCOUNT BALANCE</b>\n\n"
                f"Equity: <b>{format_price(account.total_equity)}</b>\n"
                f"Available: {format_price(account.available_balance)}\n"
                f"Margin used: {format_price(account.used_margin)}\n"
                f"Unrealized: {format_pnl(account.unrealized_pnl)}"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def trade_history(self, update, context) -> None:
        """Show recent closed trades from trade_intelligence table."""
        n = 10
        if context.args:
            try:
                n = min(int(context.args[0]), 20)
            except ValueError:
                pass

        # P4 of P1-P10: filter by current_mode so /history doesn't mix
        # shadow + bybit_demo trades. Schema v29 added exchange_mode to
        # trade_intelligence (with backfill); the filter is safe on any
        # post-migration DB. Falls back to unfiltered query when
        # transformer is unavailable.
        _xfm = self.s.get("transformer")
        _mode = None
        if _xfm is not None:
            try:
                _mode = str(_xfm.current_mode) if _xfm.current_mode else None
            except Exception:
                _mode = None
        try:
            if _mode:
                rows = await self.db.fetch_all(
                    "SELECT symbol, direction, pnl_pct, pnl_usd, win, strategy_name, "
                    "hold_seconds, leverage, trade_closed_at "
                    "FROM trade_intelligence WHERE exchange_mode = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (_mode, n),
                )
            else:
                rows = await self.db.fetch_all(
                    "SELECT symbol, direction, pnl_pct, pnl_usd, win, strategy_name, "
                    "hold_seconds, leverage, trade_closed_at "
                    "FROM trade_intelligence ORDER BY id DESC LIMIT ?",
                    (n,),
                )
        except Exception as e:
            await update.message.reply_text(f"Error fetching history: {e}")
            return

        if not rows:
            await update.message.reply_text(
                "\U0001f4cb No trade history yet. Trades appear here after they close."
            )
            return

        wins = sum(1 for r in rows if r.get("win"))
        losses = len(rows) - wins
        total_pnl = sum(float(r.get("pnl_usd") or 0) for r in rows)

        lines = [
            f"\U0001f4cb <b>TRADE HISTORY</b> (last {len(rows)})\n",
            f"Record: {wins}W / {losses}L | Total: ${total_pnl:+,.2f}\n",
        ]

        for r in rows:
            icon = "\U0001f7e2" if r.get("win") else "\U0001f534"
            pnl_pct = float(r.get("pnl_pct") or 0)
            pnl_usd = float(r.get("pnl_usd") or 0)
            hold = int(float(r.get("hold_seconds") or 0) / 60)
            strat = (r.get("strategy_name") or "?")[:20]
            lev = r.get("leverage") or "?"
            lines.append(
                f"{icon} <b>{r['symbol']}</b> {r['direction']} {lev}x "
                f"{pnl_pct:+.2f}% (${pnl_usd:+,.2f}) {hold}min — {strat}"
            )

        lines.append(f"\n\U0001f550 {format_timestamp()}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
