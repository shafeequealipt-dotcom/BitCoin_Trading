"""Telegram command handlers for Shadow bot.

All handlers are screen-reader friendly: text-based, labeled values,
no ASCII art, no tables. Emojis paired with text labels.
"""

import csv
import io
import os
import time
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.utils.logging import get_logger

log = get_logger("telegram")


def _authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the message is from the authorized chat."""
    chat_id = context.bot_data.get("chat_id", 0)
    return update.effective_chat and update.effective_chat.id == chat_id


# ─── /start ─────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    await update.message.reply_text(
        "Shadow Virtual Exchange\n\n"
        "Commands:\n"
        "/wallet — Wallet dashboard\n"
        "/positions — Open positions\n"
        "/history — Trade history\n"
        "/stats — Performance stats\n"
        "/market — Market overview\n"
        "/daily — Today's summary\n"
        "/health — System health\n"
        "/reset — Reset wallet\n"
        "/pause, /resume — Pause/resume exchange\n"
        "/settings — Exchange settings\n"
        "/export — Export trades as CSV",
        parse_mode="HTML",
    )


# ─── /wallet ─────────────────────────────────────────────────────────────

async def wallet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /wallet from chat {id}", id=update.effective_chat.id)
    try:
        wallet = context.bot_data["wallet"]
        db = context.bot_data["db"]
        balance = await wallet.get_balance()

        # Today's stats
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        today = await db.fetch_one(
            """SELECT COUNT(*) as trades,
               COALESCE(SUM(CASE WHEN result='win' THEN 1 ELSE 0 END), 0) as wins,
               COALESCE(SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END), 0) as losses,
               COALESCE(SUM(net_pnl_usd), 0) as pnl
            FROM trade_history WHERE closed_at >= ?""",
            (today_start,),
        )
        today = today or {"trades": 0, "wins": 0, "losses": 0, "pnl": 0}

        eq = balance["total_equity"]
        starting = balance["starting_balance"]
        total_return = ((eq - starting) / starting * 100) if starting > 0 else 0
        today_pct = (today["pnl"] / eq * 100) if eq > 0 else 0

        text = (
            f"💰 <b>Shadow Wallet</b>\n\n"
            f"Equity: ${eq:,.2f}\n"
            f"Available: ${balance['available_balance']:,.2f}\n"
            f"Margin in use: ${balance['margin_in_use']:,.2f}\n\n"
            f"Today PnL: ${today['pnl']:+,.2f} ({today_pct:+.2f}%)\n"
            f"Today trades: {today['trades']} ({today['wins']}W {today['losses']}L)\n\n"
            f"Total realized PnL: ${balance['total_realized_pnl']:+,.2f}\n"
            f"Total fees paid: ${balance['total_fees_paid']:,.2f}\n\n"
            f"Starting balance: ${starting:,.2f}\n"
            f"Total return: {total_return:+.2f}%\n"
            f"Total trades: {balance['total_trades']} ({balance['total_wins']}W {balance['total_losses']}L)"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        log.error("Handler /wallet failed: {err}", err=str(e))
        await update.message.reply_text(f"Error loading wallet: {e}")


# ─── /positions ──────────────────────────────────────────────────────────

async def positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /positions from chat {id}", id=update.effective_chat.id)
    try:
        engine = context.bot_data["order_engine"]
        positions = await engine.get_positions()

        if not positions:
            wallet = context.bot_data["wallet"]
            bal = await wallet.get_balance()
            await update.message.reply_text(
                f"📊 No open positions\nAvailable: ${bal['available_balance']:,.2f}",
                parse_mode="HTML",
            )
            return

        lines = [f"📊 <b>Open positions ({len(positions)})</b>\n"]
        total_margin = 0
        total_unrealized = 0

        for i, p in enumerate(positions, 1):
            coin = p["symbol"].replace("USDT", "")
            direction = "long" if p["side"] == "Buy" else "short"
            emoji = "🟢" if p["unrealized_pnl_usd"] >= 0 else "🔴"
            lev = p.get("leverage", 1)
            hold = _format_duration(p.get("hold_duration_seconds", 0))

            sl = p.get("stop_loss_price")
            tp = p.get("take_profit_price")
            sl_str = f"${sl:,.2f}" if sl else "none"
            tp_str = f"${tp:,.2f}" if tp else "none"

            lines.append(
                f"{emoji} #{i} {coin} {direction} {lev}x\n"
                f"Entry: ${p['entry_price']:,.2f}\n"
                f"Current: ${p['current_price']:,.2f}\n"
                f"PnL: {p['unrealized_pnl_pct']:+.2f}% (${p['unrealized_pnl_usd']:+,.2f})\n"
                f"Margin: ${p['margin_used']:,.2f}\n"
                f"SL: {sl_str} | TP: {tp_str}\n"
                f"Held: {hold}\n"
            )
            total_margin += p["margin_used"]
            total_unrealized += p["unrealized_pnl_usd"]

        lines.append(f"Total margin: ${total_margin:,.2f}")
        lines.append(f"Total unrealized: ${total_unrealized:+,.2f}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        log.error("Handler /positions failed: {err}", err=str(e))
        await update.message.reply_text(f"Error loading positions: {e}")


# ─── /history ────────────────────────────────────────────────────────────

async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    await _send_history_page(update.message, context, page=0)


async def history_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _authorized(update, context):
        return
    await query.answer()
    page = int(query.data.split("_")[-1])
    await _send_history_page(query.message, context, page=page, edit=True)


async def _send_history_page(message, context, page: int = 0, edit: bool = False) -> None:
    db = context.bot_data["db"]
    per_page = 10
    offset = page * per_page

    total_row = await db.fetch_one("SELECT COUNT(*) as cnt FROM trade_history")
    total = total_row["cnt"] if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page)

    trades = await db.fetch_all(
        "SELECT * FROM trade_history ORDER BY closed_at DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )

    if not trades:
        text = "📜 No trade history yet"
    else:
        lines = [f"📜 <b>Trade history</b> (page {page+1}/{total_pages}, {total} total)\n"]
        for t in trades:
            coin = t["symbol"].replace("USDT", "")
            direction = "long" if t["side"] == "Buy" else "short"
            emoji = "🟢 WIN" if t["result"] == "win" else "🔴 LOSS"
            hold = _format_duration(t.get("hold_duration_seconds", 0))
            trigger = t.get("close_trigger", "?")

            lines.append(
                f"{emoji} — {coin} {direction}\n"
                f"${t['entry_price']:,.2f} → ${t['exit_price']:,.2f}\n"
                f"PnL: {t['net_pnl_pct']:+.2f}% (${t['net_pnl_usd']:+,.2f})\n"
                f"Fees: ${t['total_fees_usd']:,.2f} | {hold} | {trigger}\n"
            )
        text = "\n".join(lines)

    # Navigation buttons
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("← Prev", callback_data=f"history_page_{page-1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next →", callback_data=f"history_page_{page+1}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await message.reply_text(text, parse_mode="HTML", reply_markup=markup)


# ─── /stats ──────────────────────────────────────────────────────────────

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /stats from chat {id}", id=update.effective_chat.id)
    try:
        db = context.bot_data["db"]
        wallet = context.bot_data["wallet"]
        bal = await wallet.get_balance()

        s = await db.fetch_one(
            """SELECT
               COUNT(*) as total,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN result='win' THEN net_pnl_usd ELSE 0 END) as gross_profit,
               SUM(CASE WHEN result='loss' THEN net_pnl_usd ELSE 0 END) as gross_loss,
               SUM(net_pnl_usd) as net_profit,
               SUM(total_fees_usd) as total_fees,
               SUM(total_slippage_usd) as total_slippage,
               AVG(CASE WHEN result='win' THEN net_pnl_pct END) as avg_win,
               AVG(CASE WHEN result='loss' THEN net_pnl_pct END) as avg_loss,
               MAX(net_pnl_pct) as best_pnl,
               MIN(net_pnl_pct) as worst_pnl,
               AVG(CASE WHEN result='win' THEN hold_duration_seconds END) as avg_hold_win,
               AVG(CASE WHEN result='loss' THEN hold_duration_seconds END) as avg_hold_loss,
               SUM(CASE WHEN side='Buy' THEN 1 ELSE 0 END) as longs,
               SUM(CASE WHEN side='Sell' THEN 1 ELSE 0 END) as shorts,
               SUM(CASE WHEN side='Buy' AND result='win' THEN 1 ELSE 0 END) as long_wins,
               SUM(CASE WHEN side='Sell' AND result='win' THEN 1 ELSE 0 END) as short_wins
            FROM trade_history"""
        )

        if not s or s["total"] == 0:
            await update.message.reply_text("📈 No trades yet — no stats to show")
            return

        total = s["total"]
        wins = s["wins"] or 0
        losses = s["losses"] or 0
        wr = (wins / total * 100) if total > 0 else 0
        gp = s["gross_profit"] or 0
        gl = s["gross_loss"] or 0
        pf = abs(gp / gl) if gl and gl != 0 else 999.0
        net = s["net_profit"] or 0
        starting = bal["starting_balance"]
        total_return = ((bal["total_equity"] - starting) / starting * 100) if starting > 0 else 0
        avg_win = s["avg_win"] or 0
        avg_loss = s["avg_loss"] or 0
        longs = s["longs"] or 0
        shorts = s["shorts"] or 0
        long_wr = (s["long_wins"] / longs * 100) if longs > 0 else 0
        short_wr = (s["short_wins"] / shorts * 100) if shorts > 0 else 0

        # Per-coin top 5
        coins = await db.fetch_all(
            """SELECT symbol, COUNT(*) as trades,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(net_pnl_usd) as pnl
            FROM trade_history GROUP BY symbol ORDER BY pnl DESC LIMIT 5"""
        )
        coin_lines = []
        for c in coins:
            cn = c["symbol"].replace("USDT", "")
            cwr = (c["wins"] / c["trades"] * 100) if c["trades"] > 0 else 0
            coin_lines.append(f"{cn}: {c['trades']}T WR {cwr:.0f}% ${c['pnl']:+,.2f}")

        text = (
            f"📈 <b>Performance Report</b>\n\n"
            f"Total trades: {total}\n"
            f"Wins: {wins} | Losses: {losses}\n"
            f"Win rate: {wr:.1f}%\n"
            f"Profit factor: {pf:.2f}\n"
            f"Total return: {total_return:+.2f}%\n\n"
            f"Gross profit: ${gp:+,.2f}\n"
            f"Gross loss: ${gl:+,.2f}\n"
            f"Net profit: ${net:+,.2f}\n"
            f"Total fees: ${s['total_fees'] or 0:,.2f}\n\n"
            f"Avg win: {avg_win:+.2f}%\n"
            f"Avg loss: {avg_loss:+.2f}%\n"
            f"Best: {s['best_pnl'] or 0:+.2f}%\n"
            f"Worst: {s['worst_pnl'] or 0:+.2f}%\n\n"
            f"Longs: {longs} | WR {long_wr:.0f}%\n"
            f"Shorts: {shorts} | WR {short_wr:.0f}%\n\n"
            f"<b>Top coins:</b>\n" + "\n".join(coin_lines)
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        log.error("Handler /stats failed: {err}", err=str(e))
        await update.message.reply_text(f"Error loading stats: {e}")


# ─── /market ─────────────────────────────────────────────────────────────

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /market from chat {id}", id=update.effective_chat.id)
    try:
        ws = context.bot_data["ws_manager"]
        tickers = ws._latest_tickers

        if not tickers:
            await update.message.reply_text("🌍 No market data yet")
            return

        # Build list with 24h change
        coins = []
        for sym, t in tickers.items():
            try:
                price = float(t.get("lastPrice", 0))
                change = float(t.get("price24hPcnt", 0)) * 100
                coins.append((sym, price, change))
            except (ValueError, TypeError):
                continue

        coins.sort(key=lambda x: x[2], reverse=True)
        gainers = coins[:5]
        losers = coins[-5:][::-1]

        gain_count = sum(1 for _, _, c in coins if c > 0)
        loss_count = sum(1 for _, _, c in coins if c <= 0)

        lines = [f"🌍 <b>Market overview ({len(coins)} coins)</b>\n"]
        lines.append("📈 Top 5 gainers (24h)")
        for sym, price, change in gainers:
            cn = sym.replace("USDT", "")
            lines.append(f"{cn}: {change:+.2f}% | ${_smart_price(price)}")

        lines.append("\n📉 Top 5 losers (24h)")
        for sym, price, change in losers:
            cn = sym.replace("USDT", "")
            lines.append(f"{cn}: {change:+.2f}% | ${_smart_price(price)}")

        # BTC/ETH
        btc = tickers.get("BTCUSDT", {})
        eth = tickers.get("ETHUSDT", {})
        lines.append(f"\nGainers: {gain_count} | Losers: {loss_count}")
        if btc:
            lines.append(f"BTC: ${_smart_price(float(btc.get('lastPrice', 0)))}")
        if eth:
            lines.append(f"ETH: ${_smart_price(float(eth.get('lastPrice', 0)))}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        log.error("Handler /market failed: {err}", err=str(e))
        await update.message.reply_text(f"Error loading market: {e}")


# ─── /daily ──────────────────────────────────────────────────────────────

async def daily_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /daily from chat {id}", id=update.effective_chat.id)
    try:
        db = context.bot_data["db"]
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

        s = await db.fetch_one(
            """SELECT COUNT(*) as trades,
               SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
               SUM(net_pnl_usd) as pnl,
               SUM(total_fees_usd) as fees,
               MAX(net_pnl_pct) as best,
               MIN(net_pnl_pct) as worst
            FROM trade_history WHERE closed_at >= ?""",
            (today_start,),
        )

        if not s or s["trades"] == 0:
            await update.message.reply_text(f"📅 {today_str}\nNo trades today yet")
            return

        wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
        text = (
            f"📅 <b>Daily summary — {today_str}</b>\n\n"
            f"Trades: {s['trades']} ({s['wins']}W {s['losses']}L)\n"
            f"Win rate: {wr:.0f}%\n"
            f"Net PnL: ${s['pnl']:+,.2f}\n"
            f"Fees: ${s['fees'] or 0:,.2f}\n"
            f"Best: {s['best'] or 0:+.2f}%\n"
            f"Worst: {s['worst'] or 0:+.2f}%"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        log.error("Handler /daily failed: {err}", err=str(e))
        await update.message.reply_text(f"Error loading daily summary: {e}")


# ─── /health ─────────────────────────────────────────────────────────────

async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /health from chat {id}", id=update.effective_chat.id)
    ws = context.bot_data["ws_manager"]
    monitor = context.bot_data["monitor"]
    db = context.bot_data["db"]
    config = context.bot_data["config"]

    ws_health = ws.get_health() if ws else {}
    mon_stats = monitor.get_stats() if monitor else {}
    uptime = ws_health.get("uptime_seconds", 0)
    db_size = os.path.getsize(config.database.path) / (1024 * 1024) if os.path.exists(config.database.path) else 0

    kline_count = await db.fetch_one("SELECT COUNT(*) as cnt FROM klines")
    ticker_count = await db.fetch_one("SELECT COUNT(*) as cnt FROM ticker_snapshots")

    # Phase 9: Enhanced DB stats via retention engine
    from src.utils.retention import RetentionEngine
    retention = RetentionEngine(db=db, config=config)
    metrics = await retention.get_health_metrics()
    rows = metrics.get("table_rows", {})

    text = (
        f"🏥 <b>Shadow Health</b>\n\n"
        f"WebSocket: {'connected' if ws_health.get('coins_with_data', 0) > 0 else 'disconnected'}\n"
        f"Coins tracked: {ws_health.get('coins_with_data', 0)}\n"
        f"Messages total: {ws_health.get('total_messages', 0):,}\n"
        f"Reconnects: {ws_health.get('reconnect_count', 0)}\n\n"
        f"💾 Database: {metrics.get('db_size_mb', 0):.1f} MB"
        f" (WAL: {metrics.get('wal_size_mb', 0):.1f} MB)\n"
        f"Klines: {rows.get('klines', 0):,}\n"
        f"Tickers: {rows.get('ticker_snapshots', 0):,}\n"
        f"OI: {rows.get('open_interest_history', 0):,}\n"
        f"Trades: {rows.get('trade_history', 0):,}\n"
        f"Wallet snaps: {rows.get('wallet_snapshots', 0):,}\n"
        f"Daily summaries: {rows.get('daily_summary', 0):,}\n"
        f"Last VACUUM: {metrics.get('last_vacuum', 'never')}\n\n"
        f"Monitor: {'active' if mon_stats.get('running') else 'inactive'}\n"
        f"Positions monitored: {mon_stats.get('positions_monitored', 0)}\n"
        f"SL triggered: {mon_stats.get('sl_triggered', 0)}\n"
        f"TP triggered: {mon_stats.get('tp_triggered', 0)}\n\n"
        f"Uptime: {_format_duration(int(uptime))}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ─── /reset ──────────────────────────────────────────────────────────────

async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    wallet = context.bot_data["wallet"]
    bal = await wallet.get_balance()

    buttons = [
        [InlineKeyboardButton("$1,000", callback_data="reset_1000"),
         InlineKeyboardButton("$5,000", callback_data="reset_5000")],
        [InlineKeyboardButton("$10,000", callback_data="reset_10000"),
         InlineKeyboardButton("$25,000", callback_data="reset_25000")],
        [InlineKeyboardButton("$50,000", callback_data="reset_50000"),
         InlineKeyboardButton("Cancel", callback_data="reset_cancel")],
    ]
    await update.message.reply_text(
        f"Reset wallet?\nCurrent equity: ${bal['total_equity']:,.2f}\n\nChoose new balance:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _authorized(update, context):
        return
    await query.answer()

    data = query.data
    if data == "reset_cancel":
        await query.edit_message_text("Reset cancelled.")
        return

    if data.startswith("reset_confirm_"):
        amount = float(data.split("_")[-1])
        wallet = context.bot_data["wallet"]
        await wallet.reset(amount)
        log.warning("Wallet RESET to ${amt:,.2f} via Telegram by chat {id}", amt=amount, id=update.effective_chat.id)
        await query.edit_message_text(f"Wallet reset to ${amount:,.2f}\nAll positions closed. History preserved.")
        return

    # First click — ask confirmation
    amount = data.replace("reset_", "")
    buttons = [[
        InlineKeyboardButton("Confirm reset", callback_data=f"reset_confirm_{amount}"),
        InlineKeyboardButton("Cancel", callback_data="reset_cancel"),
    ]]
    await query.edit_message_text(
        f"Reset wallet to ${float(amount):,.2f}?\nAll open positions will be closed.\nTrade history preserved.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ─── /pause, /resume ────────────────────────────────────────────────────

async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    engine = context.bot_data["order_engine"]
    engine._paused = True
    log.warning("Exchange PAUSED via Telegram by chat {id}", id=update.effective_chat.id)
    await update.message.reply_text(
        "⏸ Exchange paused\nNo new orders accepted.\nExisting positions still monitored.\nUse /resume to resume."
    )


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    engine = context.bot_data["order_engine"]
    engine._paused = False
    log.warning("Exchange RESUMED via Telegram by chat {id}", id=update.effective_chat.id)
    await update.message.reply_text("▶️ Exchange resumed\nOrders accepted.")


# ─── /settings ───────────────────────────────────────────────────────────

async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    config = context.bot_data["config"]
    text = (
        f"⚙️ <b>Shadow Settings</b>\n\n"
        f"Slippage: {config.exchange.slippage_pct}% ({config.exchange.slippage_mode})\n"
        f"Taker fee: {config.exchange.taker_fee_rate * 100:.3f}%\n"
        f"Maker fee: {config.exchange.maker_fee_rate * 100:.3f}%\n"
        f"Monitor interval: {config.exchange.position_monitor_interval}s\n"
        f"Starting balance: ${config.exchange.starting_balance:,.2f}\n"
        f"Coins tracked: {config.collector.coin_count}\n"
        f"API port: {config.api.port}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ─── /export ─────────────────────────────────────────────────────────────

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    log.debug("Command: /export from chat {id}", id=update.effective_chat.id)
    try:
        db = context.bot_data["db"]
        trades = await db.fetch_all("SELECT * FROM trade_history ORDER BY closed_at DESC")

        if not trades:
            await update.message.reply_text("No trades to export")
            return

        # Build CSV in memory
        output = io.StringIO()
        columns = ["symbol", "side", "entry_price", "exit_price", "net_pnl_pct",
                   "net_pnl_usd", "total_fees_usd", "hold_duration_seconds",
                   "close_trigger", "result", "opened_at", "closed_at",
                   "peak_pnl_pct", "max_drawdown_pct", "leverage"]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow(t)

        csv_bytes = output.getvalue().encode("utf-8")
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = f"shadow_trades_{date_str}.csv"

        await update.message.reply_document(
            document=io.BytesIO(csv_bytes),
            filename=filename,
            caption=f"📎 {filename} ({len(trades)} trades)",
        )
        log.info("Exported {n} trades as CSV via Telegram", n=len(trades))
    except Exception as e:
        log.error("Handler /export failed: {err}", err=str(e))
        await update.message.reply_text(f"Error exporting trades: {e}")


# ─── /cleanup (Phase 9) ───────────────────────────────────────────────────

async def cleanup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update, context):
        return
    await update.message.reply_text("🧹 Running cleanup... (may take a few seconds)")

    db = context.bot_data["db"]
    config = context.bot_data["config"]

    from src.utils.retention import RetentionEngine
    retention = RetentionEngine(db=db, config=config)

    try:
        result = await retention.run_cleanup()
        total = sum(v for k, v in result.items() if isinstance(v, int))
        text = (
            f"🧹 Cleanup complete\n\n"
            f"Ticker compressed: {result.get('ticker_hourly', 0) + result.get('ticker_daily', 0):,} rows\n"
            f"Wallet compressed: {result.get('wallet_hourly', 0) + result.get('wallet_daily', 0):,} rows\n"
            f"OI deleted: {result.get('oi_deleted', 0):,} rows\n"
            f"VACUUM: {'yes' if result.get('vacuum_run') else 'no'}\n"
            f"DB: {result.get('db_size_before_mb', 0):.1f} MB → {result.get('db_size_after_mb', 0):.1f} MB\n"
            f"Took: {result.get('duration_seconds', 0):.1f}s"
        )
    except Exception as e:
        text = f"Cleanup failed: {str(e)[:200]}"

    await update.message.reply_text(text)


# ─── Helpers ─────────────────────────────────────────────────────────────

def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h {mins}m"
    days = hours // 24
    hrs = hours % 24
    return f"{days}d {hrs}h"


def _smart_price(price: float) -> str:
    """Format price with appropriate decimal places."""
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    if price >= 0.01:
        return f"{price:.4f}"
    return f"{price:.8f}"
