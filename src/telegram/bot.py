"""Interactive Telegram Trading Bot — two-way AI-powered terminal.

Thin router that delegates all commands to dedicated handler classes.
Handles: slash commands, inline buttons, free-form AI questions, trade execution.
"""

import asyncio
from datetime import datetime, timezone

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import format_price
from src.database.connection import DatabaseManager
from src.telegram.auth import TelegramAuth
from src.telegram.conversation import ConversationManager
from src.telegram.handlers.alerts import AlertHandler
from src.telegram.handlers.analysis import AnalysisHandler
from src.telegram.handlers.brain import BrainHandler
from src.telegram.handlers.emergency import EmergencyHandler
from src.telegram.handlers.journal import JournalHandler
from src.telegram.handlers.portfolio import PortfolioHandler
from src.telegram.handlers.schedule import ScheduleHandler
from src.telegram.handlers.system import SystemHandler
from src.telegram.handlers.tias_handler import TIASHandler
from src.telegram.handlers.apex_handler import APEXHandler
from src.telegram.handlers.trading import TradingHandler
from src.telegram.handlers.watchlist import WatchlistHandler
from src.telegram.router import MessageRouter

log = get_logger("telegram")


class InteractiveTelegramBot:
    """Main interactive Telegram bot delegating to dedicated handler classes.

    Args:
        settings: Application settings.
        db: Database manager.
        services: Dict of all system services.
    """

    def __init__(self, settings: Settings, db: DatabaseManager, services: dict) -> None:
        self.settings = settings
        self.db = db
        self.services = services
        self.auth = TelegramAuth(settings)
        self.router = MessageRouter()
        self.conversation = ConversationManager()

        # Initialize handler classes with full service access
        self.portfolio_handler = PortfolioHandler(db, services)
        self.analysis_handler = AnalysisHandler(db, services)
        self.trading_handler = TradingHandler(db, services)
        self.brain_handler = BrainHandler(db, services)
        self.system_handler = SystemHandler(db, services)
        self.alert_handler = AlertHandler(db, services)
        self.watchlist_handler = WatchlistHandler(db, services)
        self.journal_handler = JournalHandler(db, services)
        self.schedule_handler = ScheduleHandler(db, services)
        self.emergency_handler = EmergencyHandler(db, services)
        from src.telegram.handlers.fund import FundManagerHandler
        self.fund_handler = FundManagerHandler(db, services)
        self.tias_handler = TIASHandler(db, services)
        self.apex_handler = APEXHandler(db, services)
        from src.telegram.handlers.universe import UniverseHandler
        self.universe_handler = UniverseHandler(db, services)

        self._running = False
        self._app = None

    async def start(self) -> None:
        """Start the bot with long-polling."""
        token = self.settings.alerts.bot_token
        if not token:
            log.warning("Telegram bot token not set — interactive bot disabled")
            return

        try:
            from telegram import BotCommand
            from telegram.ext import (
                Application, CallbackQueryHandler, CommandHandler,
                MessageHandler, filters,
            )
        except ImportError:
            log.warning("python-telegram-bot not installed — interactive bot disabled")
            return

        app = Application.builder().token(token).build()

        # Portfolio commands → PortfolioHandler
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("portfolio", self.portfolio_handler.summary))
        # /positions handled by control_handler (richer view with trade coordinator data)
        app.add_handler(CommandHandler("pnl", self.portfolio_handler.pnl))
        app.add_handler(CommandHandler("balance", self.portfolio_handler.balance))
        app.add_handler(CommandHandler("history", self.portfolio_handler.trade_history))

        # Analysis commands → AnalysisHandler
        app.add_handler(CommandHandler("analyze", self.analysis_handler.analyze))
        app.add_handler(CommandHandler("signals", self.analysis_handler.signals))
        app.add_handler(CommandHandler("regime", self.analysis_handler.regime))
        app.add_handler(CommandHandler("fear", self.analysis_handler.fear_greed))
        app.add_handler(CommandHandler("news", self.analysis_handler.news))
        app.add_handler(CommandHandler("opportunities", self.analysis_handler.opportunities))

        # Brain commands → BrainHandler
        app.add_handler(CommandHandler("brain", self.brain_handler.status))
        app.add_handler(CommandHandler("decisions", self.brain_handler.decisions))
        app.add_handler(CommandHandler("leaderboard", self.brain_handler.leaderboard))
        app.add_handler(CommandHandler("factory", self.brain_handler.factory_status))

        # Fund Manager → FundManagerHandler
        app.add_handler(CommandHandler("fund", self.fund_handler.status))
        app.add_handler(CommandHandler("setwallet", self.fund_handler.set_wallet))
        # /floor handled by control_handler (with inline action buttons)

        # System commands → SystemHandler
        app.add_handler(CommandHandler("status", self.system_handler.status))
        # /workers registered by dashboard_handler (rich version with layers, services, resources)
        app.add_handler(CommandHandler("errors", self.system_handler.errors))
        app.add_handler(CommandHandler("pause", self.system_handler.pause))
        app.add_handler(CommandHandler("resume", self.system_handler.resume))
        # Layer 1 restructure Phase 1 — /health: cycle latency dashboard.
        app.add_handler(CommandHandler("health", self.system_handler.health))

        # Floor command → FundManagerHandler
        app.add_handler(CommandHandler("floor", self.fund_handler.floor_status))

        # Manual universe refresh → UniverseHandler (Phase 4)
        app.add_handler(CommandHandler("universe_refresh", self.universe_handler.refresh_prompt))

        # Feature commands
        app.add_handler(CommandHandler("alert", self.alert_handler.set_alert))
        app.add_handler(CommandHandler("alerts", self.alert_handler.list_alerts))
        app.add_handler(CommandHandler("cancelalert", self.alert_handler.cancel_alert))
        app.add_handler(CommandHandler("watch", self.watchlist_handler.add))
        app.add_handler(CommandHandler("unwatch", self.watchlist_handler.remove))
        app.add_handler(CommandHandler("watchlist", self.watchlist_handler.show))
        app.add_handler(CommandHandler("journal", self.journal_handler.show))
        app.add_handler(CommandHandler("note", self.journal_handler.add_note))
        app.add_handler(CommandHandler("schedule", self.schedule_handler.manage))
        app.add_handler(CommandHandler("emergency", self.emergency_handler.execute))
        app.add_handler(CommandHandler("quicktrade", self._cmd_quicktrade))
        app.add_handler(CommandHandler("enforcer", self._cmd_enforcer))
        app.add_handler(CommandHandler("enforcer_reset", self._cmd_enforcer_reset))

        # TIAS commands → TIASHandler
        app.add_handler(CommandHandler("tias_last", self.tias_handler.tias_last))
        app.add_handler(CommandHandler("tias_patterns", self.tias_handler.tias_patterns))
        app.add_handler(CommandHandler("tias_symbols", self.tias_handler.tias_symbols))
        app.add_handler(CommandHandler("tias_cost", self.tias_handler.tias_cost))

        # APEX commands → APEXHandler
        app.add_handler(CommandHandler("apex_status", self.apex_handler.apex_status))
        app.add_handler(CommandHandler("apex_last", self.apex_handler.apex_last))
        app.add_handler(CommandHandler("apex_flips", self.apex_handler.apex_flips))

        # Auto-refresh dashboard handlers (must be before generic callback handler).
        # These back /control, /dashboard, /stopdash, /positions, /performance,
        # /plan, /workers, /capital, /mode. If this fails, those commands are
        # unavailable AND must be pruned from set_my_commands below so the
        # Telegram menu doesn't advertise broken slash commands.
        dashboard_ok = False
        try:
            from src.telegram.handlers.dashboard_handler import register_dashboard_handlers
            register_dashboard_handlers(app, self.services)
            dashboard_ok = True
        except Exception as e:
            log.error("Dashboard handlers unavailable — 9 commands disabled: {err}", err=str(e))

        # Inline button callbacks (generic — catches everything not caught above)
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Free-form text (MUST be last)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        # Set bot command menu (Telegram limits to 100 commands).
        # Commands served by register_dashboard_handlers are tagged below; if
        # that registration failed, they are pruned so the menu doesn't
        # advertise dead commands.
        dashboard_cmds = {
            "positions", "plan", "workers", "capital", "mode",
            "performance", "control", "stopdash", "dashboard",
        }
        try:
            all_commands = [
                # Portfolio & Trading
                BotCommand("portfolio", "Portfolio summary"),
                BotCommand("positions", "Open positions with management buttons"),
                BotCommand("pnl", "Today's PnL and mode"),
                BotCommand("balance", "Account balance"),
                BotCommand("history", "Recent trade history"),
                BotCommand("quicktrade", "Quick trade buttons"),
                # Analysis
                BotCommand("analyze", "Full coin analysis (e.g. /analyze BTC)"),
                BotCommand("signals", "Active trading signals"),
                BotCommand("regime", "Current market regime"),
                BotCommand("fear", "Fear & Greed Index"),
                BotCommand("news", "Latest crypto news"),
                BotCommand("opportunities", "Strategy opportunities & hints"),
                # Brain & AI
                BotCommand("brain", "Brain status and costs"),
                BotCommand("decisions", "Recent AI decisions"),
                BotCommand("leaderboard", "Strategy performance ranking"),
                BotCommand("factory", "Strategy Factory status"),
                BotCommand("plan", "View Claude strategic plan"),
                # APEX Optimizer
                BotCommand("apex_status", "APEX optimizer status"),
                BotCommand("apex_last", "Last APEX optimization"),
                BotCommand("apex_flips", "APEX direction flips"),
                # TIAS Intelligence
                BotCommand("tias_last", "Last N TIAS analyses (e.g. /tias_last 5)"),
                BotCommand("tias_patterns", "TIAS category pattern breakdown"),
                BotCommand("tias_symbols", "TIAS per-symbol intelligence"),
                BotCommand("tias_cost", "TIAS API cost and status"),
                # Fund Manager
                BotCommand("fund", "Intelligent Fund Manager status"),
                BotCommand("setwallet", "Set starting balance (e.g. /setwallet 5000)"),
                BotCommand("floor", "Profit floor status with controls"),
                BotCommand("capital", "Capital tier status with override"),
                BotCommand("mode", "Trading mode toggle (testnet/mainnet)"),
                # Performance
                BotCommand("performance", "Today's trading performance"),
                BotCommand("enforcer", "Performance enforcer status"),
                BotCommand("enforcer_reset", "Reset enforcer halt/throttle"),
                # Alerts & Watchlist
                BotCommand("alert", "Set price alert (e.g. /alert BTC above 72000)"),
                BotCommand("alerts", "Your active alerts"),
                BotCommand("cancelalert", "Cancel an alert"),
                BotCommand("watch", "Add to watchlist"),
                BotCommand("unwatch", "Remove from watchlist"),
                BotCommand("watchlist", "Your watchlist"),
                # Journal
                BotCommand("journal", "Trade journal"),
                BotCommand("note", "Add journal note"),
                BotCommand("schedule", "Manage scheduled reports"),
                # System
                BotCommand("status", "System health"),
                BotCommand("workers", "Worker and layer status"),
                BotCommand("errors", "Recent critical errors"),
                BotCommand("pause", "Pause all trading"),
                BotCommand("resume", "Resume trading"),
                BotCommand("universe_refresh", "Rebuild the trading universe around movers (manual)"),
                BotCommand("emergency", "Close ALL positions NOW"),
                # Dashboard
                BotCommand("control", "Live auto-refresh dashboard"),
                BotCommand("dashboard", "Alias of /control"),
                BotCommand("stopdash", "Stop dashboard auto-refresh"),
                BotCommand("help", "Show all commands"),
            ]
            if not dashboard_ok:
                all_commands = [c for c in all_commands if c.command not in dashboard_cmds]
            await app.bot.set_my_commands(all_commands)
        except Exception as e:
            log.warning("Failed to set bot commands: {err}", err=str(e))

        # Start bot manually (don't use run_polling — it conflicts with existing event loop)
        try:
            await app.initialize()

            # Wire AlertManager to use this bot's connection — ONE bot, ONE connection
            alert_mgr = self.services.get("alert_manager")
            if alert_mgr and hasattr(alert_mgr, "bot"):
                alert_mgr.bot.set_bot(app.bot)
                # Wire bot_data for dashboard repositioning after alerts
                alert_mgr._telegram_bot_instance = True
                alert_mgr._app_bot_data = app.bot_data
                log.info("AlertManager wired to interactive bot — unified connection + dashboard")

            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            self._running = True
            self._app = app
            log.info("Interactive Telegram Bot started with all handlers — polling active")

            # Keep alive until stopped
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            log.error("Telegram bot startup error: {err}", err=str(e))
        finally:
            self._running = False
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass

    async def stop(self) -> None:
        self._running = False

    # --- Start and Help (only these stay inline — they're trivial) ---

    async def _cmd_start(self, update, context) -> None:
        chat_id = update.effective_chat.id
        if not self.auth.is_authorized(chat_id):
            log.warning(f"TG_AUTH_FAIL | chat={chat_id} cmd=/start | {ctx()}")
            await update.message.reply_text("Unauthorized.")
            return
        log.info(f"TG_CMD | cmd=/start chat={chat_id} | {ctx()}")
        await update.message.reply_text(
            "\U0001f680 <b>Trading Intelligence MCP</b>\n\n"
            "I'm your AI-powered trading terminal.\n\n"
            "<b>Quick Start:</b>\n"
            "/portfolio \u2014 Portfolio overview\n"
            "/analyze BTC \u2014 Full coin analysis\n"
            "/leaderboard \u2014 Strategy ranking\n"
            "/brain \u2014 AI Brain status\n\n"
            "<b>Trading:</b>\n"
            "<code>buy BTC 100</code> \u2014 Buy $100 of BTC\n"
            "<code>sell ETH 50 5x</code> \u2014 Short $50 at 5x\n"
            "<code>close BTC</code> \u2014 Close position\n\n"
            "Or just ask me anything!",
            parse_mode="HTML",
        )

    async def _cmd_help(self, update, context) -> None:
        if not self.auth.is_authorized(update.effective_chat.id):
            return
        await update.message.reply_text(
            "<b>\U0001f4ca Portfolio</b>\n"
            "/portfolio /positions /pnl /balance /history\n\n"
            "<b>\U0001f4b0 Trading</b>\n"
            "<code>buy BTC 100</code> / <code>sell ETH 50</code> / <code>close BTC</code>\n"
            "/quicktrade \u2014 Quick trade buttons\n\n"
            "<b>\U0001f50d Analysis</b>\n"
            "/analyze BTC /signals /regime /fear /news /opportunities\n\n"
            "<b>\U0001f9e0 Brain & AI</b>\n"
            "/brain /decisions /leaderboard /factory /plan\n\n"
            "<b>\U0001f680 APEX Optimizer</b>\n"
            "/apex_status /apex_last /apex_flips\n\n"
            "<b>\U0001f4da TIAS Intelligence</b>\n"
            "/tias_last /tias_patterns /tias_symbols /tias_cost\n\n"
            "<b>\U0001f4b5 Fund Manager</b>\n"
            "/fund /setwallet /floor /capital /mode\n\n"
            "<b>\U0001f3af Performance</b>\n"
            "/performance /enforcer /enforcer_reset\n\n"
            "<b>\U0001f514 Alerts & Watchlist</b>\n"
            "/alert BTC above 72000 /alerts /cancelalert\n"
            "/watch /unwatch /watchlist\n\n"
            "<b>\U0001f4d3 Journal</b>\n"
            "/journal /note /schedule\n\n"
            "<b>\u2699\ufe0f System</b>\n"
            "/status /workers /errors /pause /resume\n"
            "/emergency \u2014 Close ALL positions\n\n"
            "<b>\U0001f4f1 Dashboard</b>\n"
            "/control /stopdash\n\n"
            "<b>\U0001f916 AI</b>\n"
            "Just type any question in plain English!",
            parse_mode="HTML",
        )

    async def _cmd_quicktrade(self, update, context) -> None:
        """Show instant trade buttons for quick execution."""
        if not self.auth.is_authorized(update.effective_chat.id):
            return
        prices = {}
        for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"]:
            try:
                t = await self.services["market_service"].get_ticker(symbol)
                prices[symbol] = t.last_price
            except Exception:
                pass

        msg = "\u26a1 <b>QUICK TRADE</b> — Tap to execute instantly\n\n"
        for sym, price in prices.items():
            coin = sym.replace("USDT", "")
            msg += f"<b>{coin}</b>: ${format_price(price)}\n"
        msg += "\nEach trade: $100 at 3x leverage with auto SL/TP\n"

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            rows = []
            for sym in prices:
                coin = sym.replace("USDT", "")
                rows.append([
                    InlineKeyboardButton(f"\U0001f7e2 BUY {coin}", callback_data=f"quickbuy:{sym}:100:3"),
                    InlineKeyboardButton(f"\U0001f534 SELL {coin}", callback_data=f"quicksell:{sym}:100:3"),
                ])
            kb = InlineKeyboardMarkup(rows)
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
        except ImportError:
            await update.message.reply_text(msg, parse_mode="HTML")

    async def _cmd_enforcer(self, update, context) -> None:
        """Show performance enforcer status with progress bars."""
        if not self.auth.is_authorized(update.effective_chat.id):
            return
        enforcer = self.services.get("enforcer")
        if not enforcer:
            await update.message.reply_text("Enforcer not active.")
            return
        s = enforcer.get_status()
        wr = s.get("win_rate", 0)
        streak = s.get("streak", 0)
        streak_label = f"+{streak}W" if streak > 0 else f"{streak}L" if streak < 0 else "0"
        urgency = s.get("urgency", 0)
        urgency_labels = ["CALM", "CAUTIOUS", "ALERT"]
        urgency_label = urgency_labels[min(urgency, 2)]
        hb = "OK" if s.get("heartbeat_ok", True) else "STALE"

        lines = [
            "\u26a1 <b>PERFORMANCE COACH</b>\n",
            f"\u23f0 {s.get('minutes_into_hour', 0)} min into hour",
            f"\U0001f6a6 Urgency: {urgency_label} ({urgency}/2)\n",
            f"<b>Today's Stats:</b>",
            f"  Trades: {s.get('trades_today', 0)} | W:{s.get('wins', 0)} L:{s.get('losses', 0)}",
            f"  Win Rate: {wr:.0%}",
            f"  PnL: {s.get('profit_today_pct', 0):+.2f}%",
            f"  Streak: {streak_label}\n",
            f"\U0001f4a1 Claude Heartbeat: {hb}",
        ]

        # Per-coin breakdown
        per_coin = s.get("per_coin", {})
        if per_coin:
            lines.append("\n<b>Per-Coin:</b>")
            for sym, stats in sorted(per_coin.items(), key=lambda x: x[1]["pnl"], reverse=True):
                w, l, p = stats["wins"], stats["losses"], stats["pnl"]
                lines.append(f"  {sym}: {w}W/{l}L ({p:+.2f}%)")

        # Per-direction
        pd = s.get("per_direction", {})
        if pd:
            buy = pd.get("Buy", {})
            sell = pd.get("Sell", {})
            bw = buy.get("wins", 0)
            bl = buy.get("losses", 0)
            sw = sell.get("wins", 0)
            sl = sell.get("losses", 0)
            lines.append(f"\n<b>Direction:</b>")
            lines.append(f"  Buy: {bw}W/{bl}L ({bw / max(bw + bl, 1) * 100:.0f}%)")
            lines.append(f"  Sell: {sw}W/{sl}L ({sw / max(sw + sl, 1) * 100:.0f}%)")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_enforcer_reset(self, update, context) -> None:
        """Manually reset enforcer enforcement level to 0 (NORMAL).

        Clears halt timer and post-halt grace window. Use when a halt was triggered
        incorrectly or when market conditions have changed and trading should resume.
        """
        if not self.auth.is_authorized(update.effective_chat.id):
            return
        enforcer = self.services.get("enforcer")
        if not enforcer:
            await update.message.reply_text("Enforcer not active.")
            return
        if not hasattr(enforcer, "reset"):
            await update.message.reply_text("Enforcer does not support reset.")
            return
        s_before = enforcer.get_status()
        prev_el = s_before.get("enforcement_level", "?")
        enforcer.reset()

        # Also reset DailyPnLManager to clear SURVIVAL/HALTED mode
        pnl_mgr = self.services.get("pnl_manager")
        prev_mode = "n/a"
        if pnl_mgr and hasattr(pnl_mgr, "reset"):
            prev_mode = pnl_mgr.get_current_mode()["mode"] if hasattr(pnl_mgr, "get_current_mode") else "?"
            pnl_mgr.reset()

        log.warning(
            f"ENFORCER_MANUAL_RESET | user={getattr(update.effective_user, 'username', None) or update.effective_chat.id} "
            f"| prev_el={prev_el} prev_pnl_mode={prev_mode} | {ctx()}"
        )
        await update.message.reply_text(
            f"⚠️ <b>Full System Reset</b>\n"
            f"Enforcer: level {prev_el} → 0 (NORMAL)\n"
            f"PnL Manager: {prev_mode} → NORMAL (PnL reset to 0%)\n"
            f"All restrictions lifted. Monitor with /enforcer.",
            parse_mode="HTML",
        )

    # --- Message Router ---

    async def _handle_message(self, update, context) -> None:
        """Route free-form text to the appropriate handler."""
        if not self.auth.is_authorized(update.effective_chat.id):
            return

        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        state = self.conversation.get_state(chat_id)

        # Check pending actions
        if state.pending_action:
            state.clear_pending()

        # Route
        intent = self.router.classify(text, state)

        if intent["type"] == "emergency":
            await self.emergency_handler.execute(update, context)
        elif intent["type"] == "trade_command":
            await self.trading_handler.handle(update, context, intent, state)
        elif intent["type"] == "quick_query":
            query = intent.get("query", "")
            handler_map = {
                "portfolio": self.portfolio_handler.summary,
                "positions": self.portfolio_handler.positions,
                "pnl": self.portfolio_handler.pnl,
                "balance": self.portfolio_handler.balance,
                "fear": self.analysis_handler.fear_greed,
                "status": self.system_handler.status,
            }
            handler = handler_map.get(query)
            if handler:
                await handler(update, context)
            else:
                await self._handle_ai_question(update, text, state)
        else:
            await self._handle_ai_question(update, text, state)

        state.add_message("user", text)
        if intent.get("symbol"):
            state.last_symbol = intent["symbol"]

    async def _handle_ai_question(self, update, text: str, state) -> None:
        """Route to Claude AI with FULL system context — like Claude Code but via Telegram."""
        claude = self.services.get("claude_client")
        cost_tracker = self.services.get("cost_tracker")

        if not claude or not cost_tracker:
            await update.message.reply_text("AI not available (Claude not configured)")
            return
        if not cost_tracker.can_afford_call():
            await update.message.reply_text("Daily AI budget exhausted.")
            return

        # Build rich context from LIVE system data
        context_parts = []

        # Account
        try:
            acc = await self.services["account_service"].get_wallet_balance()
            context_parts.append(
                f"ACCOUNT: Equity=${acc.total_equity:,.2f}, "
                f"Available=${acc.available_balance:,.2f}, "
                f"Unrealized PnL=${acc.unrealized_pnl:+,.2f}"
            )
        except Exception:
            pass

        # Open positions
        try:
            positions = await self.services["position_service"].get_positions()
            if positions:
                lines = []
                for p in positions:
                    side = "LONG" if p.side.value == "Buy" else "SHORT"
                    pnl_pct = ((p.mark_price - p.entry_price) / p.entry_price * 100) if p.side.value == "Buy" else ((p.entry_price - p.mark_price) / p.entry_price * 100)
                    sl = f"SL=${format_price(p.stop_loss)}" if p.stop_loss else "no SL"
                    lines.append(f"  {p.symbol} {side} {p.leverage}x | entry=${format_price(p.entry_price)} mark=${format_price(p.mark_price)} PnL={pnl_pct:+.2f}% (${p.unrealized_pnl:+,.2f}) | {sl}")
                context_parts.append("OPEN POSITIONS:\n" + "\n".join(lines))
            else:
                context_parts.append("OPEN POSITIONS: None")
        except Exception:
            pass

        # Daily PnL
        pnl_mgr = self.services.get("pnl_manager")
        if pnl_mgr:
            try:
                await pnl_mgr.update()
                s = pnl_mgr.get_summary()
                context_parts.append(f"TODAY: PnL={s['total_pnl_pct']:+.2f}%, Realized=${s['realized_pnl']:+,.2f}, Mode={s['mode']}")
            except Exception:
                pass

        # Market regime
        detector = self.services.get("regime_detector")
        if detector:
            try:
                # Per-coin-authority Phase 1a follow-up (2026-05-29): READ the
                # cached regime (display-only, operator-triggered) instead of
                # calling detect() — every detect() on the shared detector advances
                # BTC's hysteresis counter; RegimeWorker is the sole detect() caller.
                regime = detector.get_last_regime()
                if regime is None:
                    regime = await detector.detect()  # boot-race only
                context_parts.append(f"MARKET REGIME: {regime.regime.value} (conf={regime.confidence:.0%}, ADX={regime.adx:.1f})")
            except Exception:
                pass

        # Specific symbol analysis if mentioned
        symbol = state.last_symbol if state else None
        if not symbol:
            symbol = self.router._extract_symbol(text)
        if symbol:
            try:
                ticker = await self.services["market_service"].get_ticker(symbol)
                context_parts.append(f"{symbol}: ${format_price(ticker.last_price)} ({ticker.change_24h_pct:+.2f}% 24h)")
            except Exception:
                pass
            try:
                ta = await self.services["ta_engine"].analyze(symbol=symbol, timeframe="60", limit=100)
                overall = ta.get("overall", {})
                momentum = ta.get("momentum", {})
                context_parts.append(
                    f"{symbol} TA: Signal={overall.get('signal','N/A')}, "
                    f"RSI={momentum.get('rsi_14', 'N/A')}, "
                    f"Trend={ta.get('trend',{}).get('trend_summary','N/A')}"
                )
            except Exception:
                pass

        # Conversation history
        conv_context = self.conversation.get_context_for_ai(update.effective_chat.id)

        # Strategy count
        registry = self.services.get("registry")
        if registry:
            context_parts.append(f"STRATEGIES: {registry.count} registered, engine scanning every 60s")

        system_context = "\n".join(context_parts) if context_parts else "No live data available"

        system_prompt = (
            "You are the AI trading assistant inside a LIVE crypto trading system. "
            "The system is connected to Bybit exchange with real account balance and positions. "
            "You receive LIVE system data in every message — account balance, open positions, market regime, technical analysis. "
            "This data is real and current, pulled directly from the exchange API seconds ago.\n\n"
            "RULES:\n"
            "- Use the LIVE DATA provided below to answer — these are real numbers, not examples\n"
            "- When user asks about their portfolio, positions, or PnL — read it from the data\n"
            "- When user wants to trade, tell them to type: buy BTC 100 / sell ETH 50 / close BTC\n"
            "- When user asks for analysis, use the TA data provided or suggest /analyze BTC\n"
            "- Be concise — this is Telegram, max 300 words\n"
            "- Never say you can't see data or aren't connected — you ARE connected, the data is right below\n"
            "- Never mention MCP, tools, or API connections — just answer using the data"
        )

        prompt = f"LIVE SYSTEM STATE:\n{system_context}\n\n{conv_context}\n\nUser message: {text}"

        try:
            response = await claude.send_message(
                prompt=prompt,
                system_prompt=system_prompt,
            )
            # ClaudeCodeClient returns str; old ClaudeClient returned dict
            answer = (response if isinstance(response, str) else response.get("text", str(response)))[:4000]
            state.add_message("assistant", answer)
            await update.message.reply_text(answer)
        except Exception as e:
            await update.message.reply_text(f"AI error: {e}")

    # --- Callback Router ---

    async def _handle_callback(self, update, context) -> None:
        """Route inline button presses to the correct handler."""
        query = update.callback_query
        await query.answer()

        if not self.auth.is_authorized(query.message.chat_id):
            return

        data = query.data

        parts = data.split(":")
        action = parts[0]
        params = parts[1:] if len(parts) > 1 else []

        # Floor callbacks → FundManagerHandler
        if action.startswith("floor_"):
            await self.fund_handler.handle_floor_callback(query)
            return

        callback_map = {
            "risk_accept": self.trading_handler.execute_after_risk_check,
            "risk_cancel": self.trading_handler.cancel_trade,
            "close_pos": lambda u, c, p: self.trading_handler.close_position_callback(u, p[0] if p else ""),
            "confirm_emergency": self.emergency_handler.confirm_execute,
            "cancel_emergency": self.emergency_handler.cancel,
            "universe_refresh_confirm": self.universe_handler.refresh_confirm,
            "universe_refresh_cancel": self.universe_handler.refresh_cancel,
            "quickbuy": lambda u, c, p: self._quick_execute(u, c, "Buy", p),
            "quicksell": lambda u, c, p: self._quick_execute(u, c, "Sell", p),
        }

        handler = callback_map.get(action)
        if handler:
            await handler(update, context, params)
        else:
            await query.edit_message_text(f"Action: {action}")

    async def _quick_execute(self, update, context, side_str: str, params: list) -> None:
        """Execute a quick trade from inline button."""
        if len(params) < 3:
            return
        symbol, amount, leverage = params[0], float(params[1]), int(params[2])
        from src.core.types import OrderType, Side
        side = Side.BUY if side_str == "Buy" else Side.SELL
        try:
            ticker = await self.services["market_service"].get_ticker(symbol)
            qty = (amount * leverage) / ticker.last_price
            if side == Side.BUY:
                sl = ticker.last_price * 0.97
                tp = ticker.last_price * 1.05
            else:
                sl = ticker.last_price * 1.03
                tp = ticker.last_price * 0.95
            order = await self.services["order_service"].place_order(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                qty=qty, leverage=leverage, stop_loss=sl, take_profit=tp,
                purpose="telegram_manual",
            )
            msg = (
                f"\u2705 <b>{side_str.upper()} {symbol}</b>\n"
                f"${amount} at {leverage}x | Entry: ${format_price(ticker.last_price)}\n"
                f"SL: ${format_price(sl)} | TP: ${format_price(tp)}\n"
                f"Order: {order.order_id[:12]}..."
            )
            await update.callback_query.edit_message_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.callback_query.edit_message_text(f"\u274c Trade failed: {e}")

    @property
    def is_running(self) -> bool:
        return self._running
