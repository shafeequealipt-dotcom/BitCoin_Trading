"""Real-time auto-refresh dashboard for Telegram.

ACCESSIBILITY-FIRST DESIGN:
  The user is blind and uses a screen reader.
  Every command must be PACKED with detailed, text-based content.
  Emojis are semantic markers (screen readers announce them).
  No visual-only data. No "see the chart". Every metric spelled out.

BEHAVIOR:
  /control -> sends dashboard with buttons
  Every 60 seconds -> DELETE old dashboard, SEND new one (silent)
  Button press -> execute action, DELETE old, SEND new with result
  Trade alerts -> SEPARATE messages that stay in chat history

Only ONE dashboard message exists at any time.
Dashboard is always the LATEST message (at bottom of chat).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot
    from telegram.ext import (
        CommandHandler, CallbackQueryHandler, ContextTypes, Application,
    )
    from telegram.error import BadRequest, TelegramError
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("dashboard")
# Separate logger for exchange-switch lifecycle events. Routes to
# workers.log (component "worker") so the operator's grep across
# EXCHANGE_SWITCH_* tags returns one continuous trace from button
# press through ExchangeSwitcher's phase events through post-restart
# verification. The dashboard component routes to general.log, which
# would split that trace across two files.
_switch_log = get_logger("worker")

# -- Constants --
REFRESH_INTERVAL = 60  # seconds between auto-refresh
STARTUP_TIME = datetime.now(timezone.utc)

# Phase 5 session-stability — safe wrapper constants. Mirror the
# values in src/alerts/telegram_bot.py so dashboard refreshes share
# the same timeout/retry policy as AlertManager-routed sends.
_DASH_READ_TIMEOUT = 15.0
_DASH_WRITE_TIMEOUT = 15.0
_DASH_CONNECT_TIMEOUT = 10.0
_DASH_RETRY_SLEEPS_S: tuple[float, ...] = (2.0, 5.0, 10.0)
_DASH_SAFE_BODY_CHARS = 3800


async def _safe_bot_send(bot, chat_id, text, *, parse_mode=None, reply_markup=None,
                         disable_notification=False):
    """Send a Telegram message with explicit timeouts + retry/backoff.

    Phase 5 session-stability: the 2026-04-24 observability log showed
    two "dashboard refresh loop error, timed out" entries at 02:38:05
    and 02:41:36. Those came from ``dashboard_handler`` direct calls to
    ``bot.send_message`` without an explicit timeout. The python-telegram-
    bot default is ~10 min per call, so one slow API window blocks the
    refresh loop long enough to miss multiple intervals. This wrapper
    gives dashboard sends the same hardened behaviour as AlertManager's
    ``TelegramBot.send_message`` without routing everything through the
    alert bot (which would bind the dashboard to alert-system lifecycle).

    Returns the Telegram Message object on success, ``None`` on failure.
    Never raises.
    """
    # Lazy telegram error imports so this module stays importable even
    # when python-telegram-bot is not installed (HAS_TELEGRAM branch).
    try:
        from telegram.error import TimedOut, NetworkError, Forbidden, BadRequest as _BR
    except ImportError:
        TimedOut = NetworkError = Forbidden = _BR = Exception  # type: ignore

    # Payload-length guard before send. Telegram's hard cap is 4096;
    # leave headroom for the truncation marker and for HTML entity
    # expansion (`&` → `&amp;` etc).
    if isinstance(text, str) and len(text) > _DASH_SAFE_BODY_CHARS:
        _orig_len = len(text)
        text = (
            text[: _DASH_SAFE_BODY_CHARS - 60]
            + f"\n\n[dashboard truncated — original was {_orig_len} chars]"
        )

    last_err: Exception | None = None
    for attempt, delay in enumerate([0.0, *_DASH_RETRY_SLEEPS_S]):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_notification=disable_notification,
                read_timeout=_DASH_READ_TIMEOUT,
                write_timeout=_DASH_WRITE_TIMEOUT,
                connect_timeout=_DASH_CONNECT_TIMEOUT,
            )
            if attempt > 0:
                log.info(
                    f"TG_DASH_RETRY_OK | attempt={attempt} delay={delay:.1f}s "
                    f"chat={chat_id} | {ctx()}"
                )
            return msg
        except (TimedOut, NetworkError) as e:
            last_err = e
            log.warning(
                f"TG_DASH_RETRY | attempt={attempt + 1}/{len(_DASH_RETRY_SLEEPS_S) + 1} "
                f"chat={chat_id} err_type={type(e).__name__} err='{str(e)[:120]}' | {ctx()}"
            )
            continue
        except Forbidden as e:
            log.warning(
                f"TG_DASH_FORBIDDEN | chat={chat_id} err='{str(e)[:120]}' | {ctx()}"
            )
            return None
        except _BR as e:
            last_err = e
            # Parse-mode failures: retry once without parse_mode.
            err_str = str(e).lower()
            if ("parse" in err_str or "can't" in err_str) and parse_mode is not None:
                try:
                    msg = await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=reply_markup,
                        disable_notification=disable_notification,
                        read_timeout=_DASH_READ_TIMEOUT,
                        write_timeout=_DASH_WRITE_TIMEOUT,
                        connect_timeout=_DASH_CONNECT_TIMEOUT,
                    )
                    log.info(
                        f"TG_DASH_PARSEMODE_FALLBACK | chat={chat_id} | {ctx()}"
                    )
                    return msg
                except Exception as e2:
                    last_err = e2
            log.error(
                f"TG_DASH_BADREQUEST | chat={chat_id} "
                f"err='{str(e)[:160]}' | {ctx()}"
            )
            break
        except Exception as e:
            last_err = e
            log.error(
                f"TG_DASH_ERR | chat={chat_id} "
                f"err='{str(e)[:160]}' | {ctx()}"
            )
            break

    log.warning(
        f"TG_DASH_ABANDONED | chat={chat_id} "
        f"err_type={type(last_err).__name__ if last_err else 'None'} "
        f"err='{str(last_err)[:120] if last_err else ''}' | {ctx()}"
    )
    return None


def _svc(context, name):
    """Get service from bot_data."""
    if hasattr(context, "bot_data"):
        return context.bot_data.get(name)
    return None


def _fmt_price(context, price, symbol: str = "") -> str:
    """Render *price* for *symbol* at exact exchange tick precision.

    Uses the wired PriceFormatter (services dict -> bot_data) so dashboard
    prices match the exchange digit-for-digit; falls back to the
    magnitude-aware Telegram UI formatter when the service is unavailable
    (e.g. instrument_service down at boot). Returns a $-prefixed string.
    Callers must guard ``None`` prices (as they already do) before calling.
    """
    pf = _svc(context, "price_formatter")
    if pf is not None:
        return pf.format(price, symbol)
    from src.telegram.ui.formatters import format_price as _ui_fp
    return _ui_fp(price)


def _safe_getattr(obj, attr, default=None):
    """Safe getattr that handles None objects and exceptions."""
    if obj is None:
        return default
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except Exception:
        return default


def _format_money(amount, decimals=2):
    """Format money with sign and commas."""
    if amount is None:
        amount = 0
    return f"${amount:+,.{decimals}f}" if amount != 0 else f"${amount:,.{decimals}f}"


def _format_pct(pct, decimals=2):
    """Format percentage with sign."""
    if pct is None:
        pct = 0
    return f"{pct:+.{decimals}f}%"


def _format_duration(minutes):
    """Format minutes into human readable duration."""
    if minutes is None or minutes == 0:
        return "0min"
    if minutes < 60:
        return f"{int(minutes)}min"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours < 24:
        return f"{hours}h {mins}m"
    days = hours // 24
    hours = hours % 24
    return f"{days}d {hours}h"


def _uptime_str():
    """Get bot uptime as string."""
    delta = datetime.now(timezone.utc) - STARTUP_TIME
    total_mins = delta.total_seconds() / 60
    return _format_duration(total_mins)


# ---------------------------------------------------------------
# MODE 4 PROFIT SNIPER SECTION
# ---------------------------------------------------------------

async def _mode4_section(context) -> str:
    """Build the Mode 4 Profit Sniper stats section for the dashboard."""
    sniper = _svc(context, "profit_sniper")
    if not sniper:
        return ""

    db = _svc(context, "db")
    section = "🎯 <b>Profit Sniper (Mode 4)</b>\n"

    try:
        tracked_count = len(sniper._tracked) if hasattr(sniper, "_tracked") else 0
        section += f"   Status: Active | {tracked_count} positions tracked\n"

        if db:
            stats = await db.fetch_one(
                "SELECT "
                "COUNT(*) as total, "
                "SUM(CASE WHEN action NOT IN ('no_action','blocked_immunity','blocked_cooldown') THEN 1 ELSE 0 END) as acted, "
                "SUM(CASE WHEN spike_direction='PROFIT' AND action NOT IN ('no_action','blocked_immunity','blocked_cooldown') THEN 1 ELSE 0 END) as captures, "
                "SUM(CASE WHEN spike_direction='LOSS' AND action NOT IN ('no_action','blocked_immunity','blocked_cooldown') THEN 1 ELSE 0 END) as cuts, "
                "SUM(CASE WHEN mode4_was_right=1 THEN 1 ELSE 0 END) as right_count, "
                "ROUND(AVG(CASE WHEN sniper_value_pct IS NOT NULL THEN sniper_value_pct END), 2) as avg_value "
                "FROM sniper_log WHERE date(created_at) = date('now')"
            )
            if stats and stats["total"] and stats["total"] > 0:
                total = stats["total"]
                acted = stats["acted"] or 0
                captures = stats["captures"] or 0
                cuts = stats["cuts"] or 0
                right = stats["right_count"] or 0
                avg_val = stats["avg_value"] or 0
                accuracy = round(right / max(acted, 1) * 100)
                section += f"   Today: {captures} captures, {cuts} loss cuts\n"
                section += f"   Accuracy: {accuracy}% ({right}/{acted}) | Avg: {avg_val:+.2f}%\n"
            else:
                section += "   Today: No spikes detected\n"
    except Exception:
        section += "   Stats unavailable\n"

    section += "\n"
    return section


# ---------------------------------------------------------------
# APEX OPTIMIZER SECTION
# ---------------------------------------------------------------

async def _apex_section(context) -> str:
    """Build the APEX optimizer stats section for the dashboard."""
    optimizer = _svc(context, "apex_optimizer")
    if not optimizer:
        return ""

    stats = optimizer.get_stats()
    optimized = stats.get("optimized", 0)
    fallbacks = stats.get("fallbacks", 0)
    flips = stats.get("flips", 0)
    avg_ms = stats.get("avg_time_ms", 0)
    qwen = stats.get("qwen_stats", {})

    section = "⚡ <b>APEX Optimizer</b>\n"
    total = optimized + fallbacks
    if total == 0:
        section += "   Waiting for first optimization...\n"
    else:
        opt_pct = optimized / total * 100 if total > 0 else 0
        section += f"   Session: {optimized} optimized / {fallbacks} fallback ({opt_pct:.0f}% hit rate)\n"
        if flips > 0:
            section += f"   Direction flips: {flips} ({stats.get('flip_rate', 0):.0%})\n"
        section += f"   Avg latency: {avg_ms}ms │ Cost: ${qwen.get('cost', 0):.4f}\n"

    section += "\n"
    return section


# ---------------------------------------------------------------
# DASHBOARD TEXT BUILDER (MAIN /control DISPLAY)
# ---------------------------------------------------------------

async def _build_dashboard(context, action_result: str = "") -> str:
    """Build the full dashboard text with ALL live data."""
    now = datetime.now(timezone.utc)

    # -- Mode (Transformer-aware) --
    transformer = _svc(context, "transformer")
    if transformer:
        mode = transformer.mode_label
        is_testnet = transformer.is_shadow
    else:
        trading_mode_mgr = _svc(context, "trading_mode")
        if trading_mode_mgr:
            mode_obj = trading_mode_mgr.mode
            is_testnet = mode_obj.is_testnet
            mode = f"{mode_obj.indicator} {'TESTNET' if is_testnet else 'LIVE MAINNET'}"
        else:
            mode = "? UNKNOWN"
            is_testnet = True

    # -- Account --
    equity = 0.0
    available = 0.0
    margin_used = 0.0
    account_service = _svc(context, "account_service")
    if account_service:
        try:
            account = await account_service.get_wallet_balance()
            equity = getattr(account, "total_equity", 0) or 0
            available = getattr(account, "available_balance", 0) or 0
            margin_used = getattr(account, "used_margin", 0) or (equity - available)
        except Exception:
            pass

    margin_pct = (margin_used / equity * 100) if equity > 0 else 0

    # -- Tier / Capital --
    tier_pct = 20
    usable = 0.0
    deployed = 0.0
    tier_label = "?"
    tiered_capital = _svc(context, "tiered_capital")
    if tiered_capital:
        try:
            position_service = _svc(context, "position_service")
            if position_service:
                _positions = await position_service.get_positions()
                for _p in _positions:
                    deployed += abs(
                        _p.size * _p.entry_price / max(getattr(_p, "leverage", 1) or 1, 1)
                    )
            limits = tiered_capital.get_limits(equity, deployed)
            tier_pct = limits.tier_pct * 100
            usable = limits.usable_capital
            tier_label = f"Tier {limits.tier} ({tier_pct:.0f}%)"
        except Exception:
            tier_label = "?"
            usable = equity * 0.20

    # -- Positions --
    positions = []
    total_unrealized = 0.0
    total_notional = 0.0
    position_service = _svc(context, "position_service")
    trade_coordinator = _svc(context, "trade_coordinator")
    if position_service:
        try:
            positions = await position_service.get_positions()
        except Exception:
            pass

    pos_lines = []
    for pos in positions:
        pnl_pct = 0.0
        if pos.entry_price > 0:
            pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
            if hasattr(pos, "side") and pos.side.value in ("Sell", "Short"):
                pnl_pct = -pnl_pct

        unrealized_usd = getattr(pos, "unrealized_pnl", 0) or 0
        total_unrealized += unrealized_usd
        qty = getattr(pos, "size", 0) or 0
        leverage = getattr(pos, "leverage", 1) or 1
        notional = abs(qty * pos.mark_price)
        total_notional += notional

        plan = None
        if trade_coordinator:
            try:
                plan = trade_coordinator.get_trade_plan(pos.symbol)
            except Exception:
                pass

        age = f"{plan.age_minutes:.0f}" if plan and hasattr(plan, "age_minutes") else "?"
        max_h = f"{plan.max_hold_minutes}" if plan and hasattr(plan, "max_hold_minutes") else "?"
        trail = " Trail" if plan and getattr(plan, "trailing_active", False) else ""
        sl = _fmt_price(context, plan.stop_loss_price, pos.symbol) if plan and hasattr(plan, "stop_loss_price") else "---"
        tp = _fmt_price(context, plan.target_price, pos.symbol) if plan and hasattr(plan, "target_price") else "---"

        rr = "---"
        if plan and hasattr(plan, "stop_loss_price") and hasattr(plan, "target_price"):
            risk = abs(pos.entry_price - plan.stop_loss_price)
            reward = abs(plan.target_price - pos.entry_price)
            if risk > 0:
                rr = f"1:{reward / risk:.1f}"

        emoji = "🟢" if pnl_pct > 0.5 else "🔴" if pnl_pct < -0.5 else "⚪"
        side_str = pos.side.value if hasattr(pos, "side") else "?"
        sym_clean = pos.symbol.replace("USDT", "")

        pos_lines.append(
            f"{emoji} <b>{sym_clean}</b> {side_str} {leverage}x\n"
            f"   📍 {_fmt_price(context, pos.entry_price, pos.symbol)} → {_fmt_price(context, pos.mark_price, pos.symbol)}\n"
            f"   💵 PnL <b>{pnl_pct:+.2f}%</b> ({_format_money(unrealized_usd)})\n"
            f"   🛑 SL {sl} │ 🎯 TP {tp} │ ⚖️ R:R {rr}\n"
            f"   ⏱ Age {age}/{max_h}min{trail}"
        )

    positions_text = "\n".join(pos_lines) if pos_lines else "   — No open positions —"

    # -- Today stats --
    trades_today = 0
    wins = 0
    losses = 0
    daily_pnl_pct = 0.0
    daily_pnl_usd = 0.0
    current_streak = "---"

    pnl_manager = _svc(context, "pnl_manager")
    if pnl_manager:
        daily_pnl_pct = _safe_getattr(pnl_manager, "current_pnl_pct", 0)
        daily_pnl_usd = _safe_getattr(pnl_manager, "current_pnl_usd", 0)
        trades_today = _safe_getattr(pnl_manager, "_trades_today", 0)
        wins = _safe_getattr(pnl_manager, "_wins_today", 0)
        losses = _safe_getattr(pnl_manager, "_losses_today", 0)
        streak_count = _safe_getattr(pnl_manager, "_streak_count", 0)
        streak_type = _safe_getattr(pnl_manager, "_streak_type", "")
        if streak_count and streak_type:
            current_streak = f"{streak_count}{streak_type[0].upper()}"

    win_rate = f"{wins / trades_today * 100:.0f}%" if trades_today > 0 else "---"

    # -- Risk --
    daily_loss_limit = _safe_getattr(pnl_manager, "_daily_loss_limit_pct", -5) if pnl_manager else -5
    risk_status = "Normal"
    if daily_pnl_pct < -2:
        risk_status = "Caution"
    if daily_loss_limit and daily_pnl_pct < daily_loss_limit * 0.7:
        risk_status = "WARNING"
    if daily_loss_limit and daily_pnl_pct <= daily_loss_limit:
        risk_status = "HALTED"

    # -- Claude + Layers --
    claude_calls = 0
    claude_failures = 0
    claude_client = _svc(context, "claude_client")
    if claude_client and hasattr(claude_client, "get_stats"):
        stats = claude_client.get_stats()
        claude_calls = stats.get("calls_today", 0)
        claude_failures = stats.get("consecutive_failures", 0)

    next_review = "---"
    layer_summary = ""
    layer_manager = _svc(context, "layer_manager")
    if layer_manager:
        try:
            status = layer_manager.get_status()
            parts = []
            for key in ["layer_1", "layer_2", "layer_3"]:
                ld = status.get(key, {})
                active = ld.get("active", False)
                name = ld.get("name", key.replace("_", " ").title())
                ind = "ON" if active else "OFF"
                parts.append(f"[{ind}] {name}")
            layer_summary = " | ".join(parts)

            l2 = status.get("layer_2", {})
            nr = l2.get("next_review_in", 0)
            if nr:
                next_review = f"{int(nr)}s"
        except Exception:
            layer_summary = "Cannot read layers"

    # -- Risk emoji --
    risk_emoji = "🟢"
    if risk_status == "Caution":
        risk_emoji = "🟡"
    elif risk_status == "WARNING":
        risk_emoji = "🔴"
    elif risk_status == "HALTED":
        risk_emoji = "🚨"

    # -- Claude interval --
    claude_interval = "—"
    if claude_client and hasattr(claude_client, "get_stats"):
        claude_interval = f"{stats.get('adaptive_interval', '?')}s"

    # -- Best/Worst trades --
    best_trade_pct = _safe_getattr(pnl_manager, "_best_trade_pct", 0) if pnl_manager else 0
    worst_trade_pct = _safe_getattr(pnl_manager, "_worst_trade_pct", 0) if pnl_manager else 0

    # -- Build --
    result_line = f"\n⚡ <b>{action_result}</b>\n" if action_result else ""

    # Exchange status line — full word labels for screen-reader accessibility
    # (the operator is blind; emoji-only mode_label is not enough on its own).
    # Phase 5.C of the bybit_demo_adapter project.
    if transformer:
        if transformer.is_shadow:
            exchange_name = "Shadow (paper)"
        elif getattr(transformer, "is_bybit_demo", False):
            exchange_name = "Bybit Demo (paper)"
        elif transformer.is_bybit:
            exchange_name = "Bybit LIVE (real money)"
        else:
            exchange_name = transformer.current_mode
    else:
        exchange_name = "unknown"

    text = (
        f"{'━' * 30}\n"
        f"  {mode} │ <b>TRADING DASHBOARD</b>\n"
        f"  Exchange: <b>{exchange_name}</b>\n"
        f"{'━' * 30}\n"
        f"{result_line}"
        f"\n"
        f"💰 <b>ACCOUNT</b>\n"
        f"   Equity: <b>${equity:,.2f}</b>\n"
        f"   Available: ${available:,.2f}\n"
        f"   Margin used: ${margin_used:,.2f} ({margin_pct:.1f}%)\n"
        f"   📊 {tier_label} → Usable: <b>${usable:,.2f}</b>\n"
        f"\n"
        f"{'─' * 28}\n"
        f"📊 <b>POSITIONS</b> ({len(positions)}) — Notional ${total_notional:,.0f}\n"
        f"{positions_text}\n"
        f"\n"
        f"   💵 Unrealized total: <b>{_format_money(total_unrealized)}</b>\n"
        f"{'─' * 28}\n"
        f"\n"
        f"📈 <b>TODAY'S TRADING</b>\n"
        f"   Daily PnL: <b>{_format_pct(daily_pnl_pct)}</b> ({_format_money(daily_pnl_usd)})\n"
        f"   Trades: {trades_today} │ Wins: {wins} │ Losses: {losses} │ WR: {win_rate}\n"
        f"   Best: {_format_pct(best_trade_pct)} │ Worst: {_format_pct(worst_trade_pct)}\n"
        f"   🔥 Streak: {current_streak}\n"
        f"\n"
        f"{risk_emoji} <b>Risk:</b> {risk_status} (limit: {daily_loss_limit}%)\n"
        f"\n"
        f"{'─' * 28}\n"
        f"{await _mode4_section(context)}"
        f"{await _apex_section(context)}"
        f"{'─' * 28}\n"
        f"⚙️ <b>SYSTEM</b>\n"
        f"   {layer_summary}\n"
        f"   🧠 Claude: {claude_calls} calls │ Failures: {claude_failures} │ Interval: {claude_interval}\n"
        f"   ⏱ Next review: {next_review}\n"
        f"   🕐 Uptime: {_uptime_str()}\n"
        f"\n"
        f"<i>🔄 Auto-refresh {REFRESH_INTERVAL}s │ {now.strftime('%H:%M:%S')} UTC</i>"
    )

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>(truncated)</i>"

    return text


def _build_keyboard(context) -> InlineKeyboardMarkup:
    """Build inline keyboard with control buttons."""
    layer_manager = _svc(context, "layer_manager")
    trading_active = False
    if layer_manager:
        try:
            # Trading is "active" when both Brain (L2) and Execution (L3) are on
            trading_active = layer_manager.is_layer_active(2) and layer_manager.is_layer_active(3)
        except Exception:
            pass

    trading_mode_mgr = _svc(context, "trading_mode")
    is_testnet = True
    if trading_mode_mgr:
        is_testnet = trading_mode_mgr.mode.is_testnet

    keyboard = []

    # Row 1: Start/Stop
    if trading_active:
        keyboard.append([
            InlineKeyboardButton("⏹ Stop Trading", callback_data="dash_stop_trading"),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("▶️ Start Trading", callback_data="dash_start_trading"),
        ])

    # Row 2: Emergency + Exchange Switch (Transformer)
    transformer = _svc(context, "transformer")
    if transformer and not transformer.is_switching:
        if transformer.is_shadow:
            switch_btn = InlineKeyboardButton("🔴 Switch to Bybit", callback_data="dash_switch_bybit")
        else:
            switch_btn = InlineKeyboardButton("🟡 Switch to Shadow", callback_data="dash_switch_shadow")
        keyboard.append([
            InlineKeyboardButton("🚨 CLOSE ALL", callback_data="dash_emergency_close"),
            switch_btn,
        ])
    else:
        mode_label = f"{'🟡TEST' if is_testnet else '🟢LIVE'}→{'LIVE' if is_testnet else 'TEST'}"
        keyboard.append([
            InlineKeyboardButton("🚨 CLOSE ALL", callback_data="dash_emergency_close"),
            InlineKeyboardButton(mode_label, callback_data="dash_toggle_mode"),
        ])

    # Row 2.5: Bybit Demo switch (Phase 5 of bybit_demo_adapter project).
    # Restart-based path — additive to the existing live-bybit hot-swap
    # button above. Shown when current mode is shadow or bybit_demo.
    # Hidden when already on live "bybit" to avoid encouraging
    # live → demo at runtime (the demo ↔ shadow flow is the trial flow).
    if transformer and not transformer.is_switching:
        if transformer.is_shadow:
            keyboard.append([
                InlineKeyboardButton(
                    "🟣 Switch to Bybit Demo (Paper)",
                    callback_data="dash_switch_bybit_demo",
                ),
            ])
        elif getattr(transformer, "is_bybit_demo", False):
            keyboard.append([
                InlineKeyboardButton(
                    "🟡 Switch to Shadow (from Demo)",
                    callback_data="dash_switch_shadow_from_demo",
                ),
            ])

    # Row 3: Tier
    keyboard.append([
        InlineKeyboardButton("10%", callback_data="dash_tier_10"),
        InlineKeyboardButton("20%", callback_data="dash_tier_20"),
        InlineKeyboardButton("30%", callback_data="dash_tier_30"),
        InlineKeyboardButton("50%", callback_data="dash_tier_50"),
    ])

    # Row 4: Per-position close buttons
    cached_positions = context.bot_data.get("_cached_positions", [])
    if cached_positions:
        close_row = []
        for pos in cached_positions[:4]:
            sym_short = pos.symbol.replace("USDT", "")
            close_row.append(
                InlineKeyboardButton(f"❌{sym_short}", callback_data=f"dash_close_{pos.symbol}")
            )
        if close_row:
            keyboard.append(close_row)

    # Row 5: Layer controls — always visible on main dashboard
    if layer_manager:
        try:
            l1 = layer_manager.is_layer_active(1)
            l2 = layer_manager.is_layer_active(2)
            l3 = layer_manager.is_layer_active(3)
            keyboard.append([
                InlineKeyboardButton(f"{'🟢' if l1 else '🔴'} Data", callback_data="dash_toggle_l1"),
                InlineKeyboardButton(f"{'🟢' if l2 else '🔴'} Brain", callback_data="dash_toggle_l2"),
                InlineKeyboardButton(f"{'🟢' if l3 else '🔴'} Exec", callback_data="dash_toggle_l3"),
            ])
        except Exception as e:
            log.warning("Layer buttons build failed: {err}", err=str(e))
    else:
        # Fallback: show buttons without live state if layer_manager unavailable
        keyboard.append([
            InlineKeyboardButton("⚪ Data", callback_data="dash_toggle_l1"),
            InlineKeyboardButton("⚪ Brain", callback_data="dash_toggle_l2"),
            InlineKeyboardButton("⚪ Exec", callback_data="dash_toggle_l3"),
        ])

    # Row 6: Navigation
    keyboard.append([
        InlineKeyboardButton("📋 Plan", callback_data="dash_view_plan"),
        InlineKeyboardButton("📈 Perf", callback_data="dash_view_performance"),
        InlineKeyboardButton("🔧 Workers", callback_data="dash_view_workers"),
    ])

    # Row 7: APEX + Positions + Refresh
    keyboard.append([
        InlineKeyboardButton("⚡ APEX", callback_data="dash_view_apex"),
        InlineKeyboardButton("📊 Positions", callback_data="dash_view_positions"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dash_back"),
    ])

    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------
# SEND / DELETE / REFRESH
# ---------------------------------------------------------------

async def _send_dashboard(bot, context_or_bot_data, chat_id: int,
                          action_result: str = "") -> int:
    """Send a fresh dashboard message. Returns message_id."""
    class _Ctx:
        def __init__(self, bd):
            self.bot_data = bd

    if isinstance(context_or_bot_data, dict):
        ctx = _Ctx(context_or_bot_data)
    else:
        ctx = context_or_bot_data

    # Cache positions for keyboard builder (sync function)
    position_service = _svc(ctx, "position_service")
    if position_service:
        try:
            positions = await position_service.get_positions()
            ctx.bot_data["_cached_positions"] = positions
            ctx.bot_data["_cached_pos_count"] = len(positions)
        except Exception:
            ctx.bot_data["_cached_positions"] = []
            ctx.bot_data["_cached_pos_count"] = 0

    text = await _build_dashboard(ctx, action_result)
    keyboard = _build_keyboard(ctx)

    msg = await _safe_bot_send(
        bot,
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_notification=True,
    )

    # _safe_bot_send returns None on abandoned failure; callers should
    # handle a missing message_id gracefully (next tick will re-send).
    return msg.message_id if msg else None


async def _delete_dashboard(bot, chat_id: int, message_id: int) -> None:
    """Delete the old dashboard message. Silently ignore errors."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _refresh_dashboard(bot, bot_data: dict, chat_id: int,
                             action_result: str = "") -> None:
    """Delete old dashboard -> send new one."""
    old_msg_id = bot_data.get("dashboard_msg_id")
    await _delete_dashboard(bot, chat_id, old_msg_id)
    new_msg_id = await _send_dashboard(bot, bot_data, chat_id, action_result)
    bot_data["dashboard_msg_id"] = new_msg_id
    bot_data["dashboard_active"] = True
    bot_data["dashboard_chat_id"] = chat_id


# ---------------------------------------------------------------
# AUTO-REFRESH
# ---------------------------------------------------------------

async def _auto_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every REFRESH_INTERVAL seconds by JobQueue (if available)."""
    bot_data = context.bot_data
    if not bot_data.get("dashboard_active"):
        return
    chat_id = bot_data.get("dashboard_chat_id")
    if not chat_id:
        return
    try:
        await _refresh_dashboard(context.bot, bot_data, chat_id)
    except Exception as e:
        log.error("Dashboard auto-refresh failed: {err}", err=str(e))


async def _auto_refresh_loop(app) -> None:
    """Fallback auto-refresh using asyncio when JobQueue is unavailable.

    The task reference is stored in ``app.bot_data["_refresh_task"]`` so it
    can be cancelled by ``/control`` or ``/stopdash``.

    Phase 5 session-stability: added ±10 s jitter so restart storms or
    temporarily flaky Telegram windows don't produce synchronous
    refresh bursts that all time out together. Exceptions still keep
    the loop alive — the prior behaviour is preserved.
    """
    import random as _random

    await asyncio.sleep(20)
    log.info("Dashboard auto-refresh loop started ({sec}s interval)", sec=REFRESH_INTERVAL)
    while True:
        try:
            bot_data = app.bot_data
            if not bot_data.get("dashboard_active"):
                await asyncio.sleep(REFRESH_INTERVAL)
                continue
            chat_id = bot_data.get("dashboard_chat_id")
            if not chat_id:
                await asyncio.sleep(REFRESH_INTERVAL)
                continue
            await _refresh_dashboard(app.bot, bot_data, chat_id)
        except asyncio.CancelledError:
            log.info("Dashboard asyncio refresh loop cancelled")
            break
        except Exception as e:
            log.error("Dashboard refresh loop error: {err}", err=str(e))
        # Jitter the next tick by ±10 s (bounded at 10 s floor). The sleep
        # is outside the try so one transient failure doesn't collapse to
        # an immediate retry.
        _jitter = _random.uniform(-10.0, 10.0)
        _sleep_s = max(10.0, REFRESH_INTERVAL + _jitter)
        await asyncio.sleep(_sleep_s)


def _cancel_refresh(bot_data: dict, job_queue=None) -> None:
    """Cancel ALL running refresh mechanisms (JobQueue + asyncio task)."""
    # Cancel JobQueue jobs
    if job_queue:
        try:
            existing = job_queue.get_jobs_by_name("dashboard_refresh")
            for job in existing:
                job.schedule_removal()
        except Exception:
            pass

    # Cancel asyncio fallback task
    task = bot_data.get("_refresh_task")
    if task and not task.done():
        task.cancel()
        bot_data["_refresh_task"] = None


def _start_refresh(bot_data: dict, job_queue=None, app=None) -> None:
    """Start a single refresh mechanism (prefers JobQueue, falls back to asyncio)."""
    # Always cancel existing first to prevent duplicates
    _cancel_refresh(bot_data, job_queue)

    if job_queue:
        try:
            job_queue.run_repeating(
                _auto_refresh_job,
                interval=REFRESH_INTERVAL,
                first=REFRESH_INTERVAL,
                name="dashboard_refresh",
            )
            return
        except Exception:
            pass

    # Asyncio fallback — store task reference for later cancellation
    if app:
        task = asyncio.ensure_future(_auto_refresh_loop(app))
        bot_data["_refresh_task"] = task


# ---------------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------------

async def control_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/control or /dashboard -- start the real-time dashboard."""
    try:
        chat_id = update.effective_chat.id

        # Cancel any existing refresh loop FIRST to prevent race
        _cancel_refresh(context.bot_data, context.application.job_queue)

        old_msg_id = context.bot_data.get("dashboard_msg_id")
        if old_msg_id:
            await _delete_dashboard(context.bot, chat_id, old_msg_id)

        msg_id = await _send_dashboard(context.bot, context, chat_id)
        context.bot_data["dashboard_msg_id"] = msg_id
        context.bot_data["dashboard_active"] = True
        context.bot_data["dashboard_chat_id"] = chat_id

        # Start a single refresh mechanism
        _start_refresh(
            context.bot_data,
            job_queue=context.application.job_queue,
            app=context.application,
        )

        log.info("Dashboard activated for chat {cid}", cid=chat_id)

    except Exception as e:
        log.error("Control command failed: {err}", err=str(e))
        try:
            await update.message.reply_text(f"Dashboard error: {str(e)[:200]}")
        except Exception:
            pass


async def stop_dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stopdash -- stop the auto-refresh."""
    context.bot_data["dashboard_active"] = False
    _cancel_refresh(context.bot_data, context.application.job_queue)
    await update.message.reply_text(
        "Dashboard Stopped\n\nAuto-refresh disabled. Send /control to restart.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------
# /positions -- DETAILED POSITION VIEW (FULL CONTENT)
# ---------------------------------------------------------------

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/positions -- comprehensive position details."""
    position_service = _svc(context, "position_service")
    trade_coordinator = _svc(context, "trade_coordinator")
    account_service = _svc(context, "account_service")

    if not position_service:
        await update.message.reply_text("Position service not available")
        return

    try:
        positions = await position_service.get_positions()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
        return

    now = datetime.now(timezone.utc)

    if not positions:
        await update.message.reply_text(
            f"{'━' * 30}\n"
            f"📊 <b>POSITIONS — EMPTY</b>\n"
            f"{'━' * 30}\n\n"
            f"No open positions at this time.\n\n"
            f"<b>What next?</b>\n"
            f"   📋 Send /plan to see Claude's market view\n"
            f"   🔧 Send /control for the main dashboard\n"
            f"   📈 Send /performance for today's stats\n\n"
            f"<i>🕐 {now.strftime('%H:%M:%S')} UTC</i>",
            parse_mode="HTML",
        )
        return

    equity = 0.0
    if account_service:
        try:
            account = await account_service.get_wallet_balance()
            equity = getattr(account, "total_equity", 0) or 0
        except Exception:
            pass

    total_unrealized = 0.0
    total_notional = 0.0
    total_risk = 0.0

    lines = [
        f"{'━' * 30}",
        f"📊 <b>OPEN POSITIONS — {len(positions)} Active</b>",
        f"{'━' * 30}\n",
    ]

    for i, pos in enumerate(positions, 1):
        pnl_pct = 0.0
        if pos.entry_price > 0:
            pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
            if hasattr(pos, "side") and pos.side.value in ("Sell", "Short"):
                pnl_pct = -pnl_pct

        unrealized = getattr(pos, "unrealized_pnl", 0) or 0
        total_unrealized += unrealized
        qty = getattr(pos, "size", 0) or 0
        leverage = getattr(pos, "leverage", 1) or 1
        notional = abs(qty * pos.mark_price)
        total_notional += notional
        liq_price = getattr(pos, "liq_price", None) or getattr(pos, "liquidation_price", None)

        plan = None
        trade_info = {}
        if trade_coordinator:
            try:
                plan = trade_coordinator.get_trade_plan(pos.symbol)
            except Exception:
                pass
            try:
                if hasattr(trade_coordinator, "get_trade_info"):
                    trade_info = trade_coordinator.get_trade_info(pos.symbol) or {}
            except Exception:
                pass

        age = _format_duration(plan.age_minutes) if plan and hasattr(plan, "age_minutes") else "?"
        max_h = f"{plan.max_hold_minutes}min" if plan and hasattr(plan, "max_hold_minutes") else "?"
        remain = _format_duration(plan.remaining_minutes) if plan and hasattr(plan, "remaining_minutes") else "?"
        trail_status = "Trailing ACTIVE" if plan and getattr(plan, "trailing_active", False) else "Trailing off"
        sl = _fmt_price(context, plan.stop_loss_price, pos.symbol) if plan and hasattr(plan, "stop_loss_price") else "---"
        tp = _fmt_price(context, plan.target_price, pos.symbol) if plan and hasattr(plan, "target_price") else "---"
        strategy = trade_info.get("strategy_name", "---") if isinstance(trade_info, dict) else "---"
        thesis = plan.reasoning[:120] if plan and hasattr(plan, "reasoning") and plan.reasoning else "No thesis recorded"

        rr = "---"
        risk_usd = 0
        if plan and hasattr(plan, "stop_loss_price") and hasattr(plan, "target_price"):
            risk = abs(pos.entry_price - plan.stop_loss_price)
            reward = abs(plan.target_price - pos.entry_price)
            if risk > 0:
                rr = f"1:{reward / risk:.1f}"
            risk_usd = risk * abs(qty) * leverage
            total_risk += risk_usd

        sl_dist = ""
        tp_dist = ""
        if plan and hasattr(plan, "stop_loss_price") and pos.mark_price > 0:
            sl_pct = abs(pos.mark_price - plan.stop_loss_price) / pos.mark_price * 100
            sl_dist = f"({sl_pct:.1f}% away)"
        if plan and hasattr(plan, "target_price") and pos.mark_price > 0:
            tp_pct = abs(plan.target_price - pos.mark_price) / pos.mark_price * 100
            tp_dist = f"({tp_pct:.1f}% away)"

        emoji = "🟢" if pnl_pct > 0.5 else "🔴" if pnl_pct < -0.5 else "⚪"
        side_str = pos.side.value if hasattr(pos, "side") else "?"
        sym = pos.symbol.replace("USDT", "")
        weight = f"{(notional / equity * 100):.1f}%" if equity > 0 else "—"

        lines.append(
            f"{emoji} <b>#{i} — {sym} {side_str} {leverage}x</b>\n"
            f"{'─' * 26}\n"
            f"   📍 Entry: {_fmt_price(context, pos.entry_price, pos.symbol)}\n"
            f"   📍 Current: {_fmt_price(context, pos.mark_price, pos.symbol)}\n"
            f"   💵 PnL: <b>{pnl_pct:+.2f}%</b> ({_format_money(unrealized)})\n"
            f"\n"
            f"   🛑 Stop Loss: {sl} {sl_dist}\n"
            f"   🎯 Take Profit: {tp} {tp_dist}\n"
            f"   ⚖️ Risk:Reward: {rr}\n"
            f"   🔄 {trail_status}\n"
            f"\n"
            f"   📦 Size: {qty} │ Notional: ${notional:,.2f}\n"
            f"   📊 Portfolio weight: {weight}\n"
            f"   💀 Max risk: {_format_money(risk_usd)}\n"
            f"{'   🔻 Liq price: ' + _fmt_price(context, liq_price, pos.symbol) if liq_price else ''}\n"
            f"\n"
            f"   ⏱ Held: {age} │ Max: {max_h} │ Left: {remain}\n"
            f"   🎯 Strategy: {strategy}\n"
            f"   🧠 Thesis: <i>{thesis}</i>\n"
        )

    lines.append(f"\n{'━' * 30}")
    lines.append(f"📋 <b>PORTFOLIO SUMMARY</b>")
    lines.append(f"   📊 Total positions: {len(positions)}")
    lines.append(f"   💰 Total notional: ${total_notional:,.2f}")
    lines.append(f"   💵 Total unrealized: <b>{_format_money(total_unrealized)}</b>")
    lines.append(f"   💀 Total at risk: {_format_money(total_risk)}")
    if equity > 0:
        lines.append(f"  Exposure: {total_notional / equity * 100:.1f}% of equity")
    lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard_rows = []
    for pos in positions:
        sym_short = pos.symbol.replace("USDT", "")
        keyboard_rows.append([
            InlineKeyboardButton(f"X Close {sym_short}", callback_data=f"dash_close_{pos.symbol}")
        ])
    keyboard_rows.append([
        InlineKeyboardButton("<- Back to Dashboard", callback_data="dash_back")
    ])

    await update.message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


# ---------------------------------------------------------------
# /performance -- COMPREHENSIVE TRADING STATS (FULL CONTENT)
# ---------------------------------------------------------------

async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/performance -- Full 24h trading statistics."""
    now = datetime.now(timezone.utc)

    lines = [
        f"{'━' * 30}",
        f"📈 <b>PERFORMANCE REPORT</b>",
        f"{'━' * 30}\n",
    ]

    pnl_manager = _svc(context, "pnl_manager")
    trades_today = _safe_getattr(pnl_manager, "_trades_today", 0) if pnl_manager else 0
    wins = _safe_getattr(pnl_manager, "_wins_today", 0) if pnl_manager else 0
    losses = _safe_getattr(pnl_manager, "_losses_today", 0) if pnl_manager else 0
    daily_pnl_pct = _safe_getattr(pnl_manager, "current_pnl_pct", 0) if pnl_manager else 0
    daily_pnl_usd = _safe_getattr(pnl_manager, "current_pnl_usd", 0) if pnl_manager else 0
    best_trade = _safe_getattr(pnl_manager, "_best_trade_pct", 0) if pnl_manager else 0
    worst_trade = _safe_getattr(pnl_manager, "_worst_trade_pct", 0) if pnl_manager else 0
    avg_win = _safe_getattr(pnl_manager, "_avg_win_pct", 0) if pnl_manager else 0
    avg_loss = _safe_getattr(pnl_manager, "_avg_loss_pct", 0) if pnl_manager else 0
    max_dd = _safe_getattr(pnl_manager, "_max_drawdown_pct", 0) if pnl_manager else 0

    win_rate = f"{wins / trades_today * 100:.0f}%" if trades_today > 0 else "---"

    expectancy = 0
    if trades_today > 0 and avg_loss != 0:
        w_rate = wins / trades_today
        l_rate = losses / trades_today
        expectancy = (w_rate * avg_win) + (l_rate * avg_loss)

    pf = "---"
    if avg_loss != 0 and losses > 0:
        gp = wins * abs(avg_win)
        gl = losses * abs(avg_loss)
        if gl > 0:
            pf = f"{gp / gl:.2f}"

    pnl_emoji = "🟢" if daily_pnl_pct > 0 else "🔴" if daily_pnl_pct < 0 else "⚪"
    lines.extend([
        f"{pnl_emoji} <b>DAILY PnL</b>",
        f"   Total: <b>{_format_pct(daily_pnl_pct)}</b> ({_format_money(daily_pnl_usd)})",
        f"   📉 Max drawdown: {_format_pct(max_dd)}",
        f"",
        f"{'─' * 28}",
        f"📊 <b>TRADE STATISTICS</b>",
        f"   Trades: <b>{trades_today}</b>",
        f"   ✅ Wins: {wins} ({win_rate}) │ ❌ Losses: {losses}",
        f"   🏆 Best: {_format_pct(best_trade)} │ 💔 Worst: {_format_pct(worst_trade)}",
        f"   📈 Avg win: {_format_pct(avg_win)} │ 📉 Avg loss: {_format_pct(avg_loss)}",
        f"",
        f"{'─' * 28}",
        f"📐 <b>RISK METRICS</b>",
        f"   Expectancy: {_format_pct(expectancy)} per trade",
        f"   Profit factor: {pf}",
    ])

    # Streaks
    streak_count = _safe_getattr(pnl_manager, "_streak_count", 0) if pnl_manager else 0
    streak_type = _safe_getattr(pnl_manager, "_streak_type", "") if pnl_manager else ""
    lines.extend([
        f"",
        f"{'─' * 28}",
        f"🔥 <b>STREAKS</b>",
        f"   Current: {streak_count} {streak_type or '—'}{'🔥' if streak_count >= 3 else ''}",
    ])

    # Per-coin
    per_coin = _safe_getattr(pnl_manager, "_per_coin_stats", {}) if pnl_manager else {}
    if per_coin and isinstance(per_coin, dict) and len(per_coin) > 0:
        lines.extend(["", f"{'─' * 28}", f"🪙 <b>PER-COIN BREAKDOWN</b>"])
        for symbol, cs in per_coin.items():
            sym = symbol.replace("USDT", "")
            if isinstance(cs, dict):
                c_pnl = cs.get("pnl_pct", 0)
                c_trades = cs.get("trades", 0)
                c_wins = cs.get("wins", 0)
                c_emoji = "🟢" if c_pnl > 0 else "🔴" if c_pnl < 0 else "⚪"
                lines.append(f"   {c_emoji} {sym}: {_format_pct(c_pnl)} │ {c_trades}T {c_wins}W")

    # Claude stats
    claude_client = _svc(context, "claude_client")
    lines.extend(["", f"{'─' * 28}", f"🧠 <b>CLAUDE AI USAGE</b>"])
    if claude_client and hasattr(claude_client, "get_stats"):
        stats = claude_client.get_stats()
        lines.extend([
            f"   📞 Calls today: {stats.get('calls_today', 0)}",
            f"   ❌ Failures: {stats.get('consecutive_failures', 0)}",
            f"   ⏱ Interval: {stats.get('adaptive_interval', '?')}s",
        ])
    else:
        lines.append("   ❌ Not available")

    # Risk limits
    daily_limit = _safe_getattr(pnl_manager, "_daily_loss_limit_pct", -5) if pnl_manager else -5
    risk_used = abs(daily_pnl_pct / daily_limit * 100) if daily_limit != 0 else 0
    r_emoji = "🟢" if risk_used < 50 else "🟡" if risk_used < 80 else "🔴" if risk_used < 100 else "🚨"
    r_label = "Normal" if risk_used < 50 else "Caution" if risk_used < 80 else "Danger" if risk_used < 100 else "HALTED"

    lines.extend([
        f"",
        f"{'─' * 28}",
        f"{r_emoji} <b>RISK LIMITS</b>",
        f"   Status: <b>{r_label}</b>",
        f"   Daily limit: {daily_limit}%",
        f"   Used: {risk_used:.1f}% of limit",
        f"   Remaining before halt: {_format_pct(daily_limit - daily_pnl_pct)}",
        f"",
        f"{'─' * 28}",
        f"⏱ <b>SESSION</b>",
        f"   🕐 Uptime: {_uptime_str()}",
        f"   🕐 Time: {now.strftime('%H:%M:%S')} UTC",
    ])

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("<- Back to Dashboard", callback_data="dash_back")
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ---------------------------------------------------------------
# /plan -- CLAUDE'S STRATEGIC PLAN (FULL CONTENT)
# ---------------------------------------------------------------

async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/plan -- Claude's current strategic plan."""
    now = datetime.now(timezone.utc)
    layer_manager = _svc(context, "layer_manager")

    lines = [
        f"{'━' * 30}",
        f"📋 <b>STRATEGIC PLAN</b>",
        f"{'━' * 30}\n",
    ]

    if not layer_manager:
        lines.append("❌ Layer manager not available. Check /workers.")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    plan = None
    try:
        plan = layer_manager.get_plan()
    except Exception as e:
        lines.append(f"Error: {str(e)[:100]}")

    if plan and hasattr(plan, "to_telegram_text"):
        lines.append(plan.to_telegram_text())
    elif plan:
        mv = getattr(plan, "market_view", None)
        if mv:
            lines.extend([f"🌍 <b>MARKET VIEW</b>", f"   {mv[:300]}", ""])

        risk_lvl = getattr(plan, "risk_level", None)
        if risk_lvl:
            r_emoji = "🟢" if risk_lvl == "conservative" else "🟡" if risk_lvl == "normal" else "🔴"
            lines.extend([f"{r_emoji} <b>Risk Level:</b> {risk_lvl}", ""])

        focus = getattr(plan, "focus_coins", None)
        if focus:
            lines.extend([f"👁 <b>FOCUS COINS:</b> {', '.join(focus[:8])}", ""])

        avoid = getattr(plan, "avoid_coins", None)
        if avoid:
            lines.extend([f"🚫 <b>AVOID:</b> {', '.join(avoid[:5])}", ""])

        notes = getattr(plan, "raw_reasoning", None) or getattr(plan, "reasoning", None)
        if notes:
            lines.extend([f"🧠 <b>STRATEGY NOTES</b>", f"   {str(notes)[:400]}", ""])

        created = getattr(plan, "created_at_dt", None)
        if created and isinstance(created, datetime):
            age = (now - created).total_seconds() / 60
            lines.append(f"📅 <b>Updated:</b> {created.strftime('%H:%M:%S')} UTC ({_format_duration(age)} ago)")
    else:
        lines.extend([
            "⚠️ <b>No strategic plan available yet.</b>",
            "",
            "Claude generates a plan every 5 minutes.",
            "This happens after Layer 2 (Brain) completes its first cycle.",
            "",
            "   📋 Send /workers to check layer status",
            "   🔧 Send /control for the main dashboard",
        ])

    # Layer footer
    lines.extend(["", f"{'_' * 28}", f"<b>PLAN ENGINE</b>"])
    try:
        status = layer_manager.get_status()
        for key in ["layer_1", "layer_2", "layer_3"]:
            ld = status.get(key, {})
            ind = "ON" if ld.get("active") else "OFF"
            name = ld.get("name", key)
            lines.append(f"  [{ind}] {name}")
    except Exception:
        pass

    lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("<- Back to Dashboard", callback_data="dash_back")
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ---------------------------------------------------------------
# /workers -- DETAILED SYSTEM HEALTH (FULL CONTENT)
# ---------------------------------------------------------------

async def workers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/workers -- comprehensive system health."""
    now = datetime.now(timezone.utc)

    lines = [
        f"{'━' * 30}",
        f"🔧 <b>SYSTEM STATUS</b>",
        f"{'━' * 30}\n",
    ]

    # Layers
    layer_manager = _svc(context, "layer_manager")
    lines.append(f"⚙️ <b>TRADING LAYERS</b>")
    if layer_manager:
        try:
            status = layer_manager.get_status()
            for key in ["layer_1", "layer_2", "layer_3"]:
                ld = status.get(key, {})
                name = ld.get("name", key.replace("_", " ").title())
                active = ld.get("active", False)
                uptime_sec = ld.get("uptime_seconds", 0)
                uptime = _format_duration(uptime_sec / 60) if uptime_sec else "---"
                emoji = "🟢" if active else "🔴"
                lines.extend([
                    f"",
                    f"   {emoji} <b>{name}</b>",
                    f"      {'🟢 Running' if active else '🔴 Stopped'} │ ⏱ Uptime {uptime}",
                ])
        except Exception as e:
            lines.append(f"  Error: {str(e)[:80]}")
    else:
        lines.append("  Not available")

    # Claude
    claude_client = _svc(context, "claude_client")
    lines.extend(["", f"{'─' * 28}", f"🧠 <b>CLAUDE AI ENGINE</b>"])
    if claude_client and hasattr(claude_client, "get_stats"):
        stats = claude_client.get_stats()
        cf = stats.get("consecutive_failures", 0)
        c_status = "🟢 Connected" if cf == 0 else "🟡 Degraded" if cf < 3 else "🔴 Failing"
        lines.extend([
            f"   Status: {c_status}",
            f"   📞 Calls today: {stats.get('calls_today', 0)}",
            f"   ❌ Consecutive failures: {cf}",
            f"   ⏱ Adaptive interval: {stats.get('adaptive_interval', '?')}s",
        ])
    else:
        lines.append("   ❌ Not available")

    # Services
    lines.extend(["", f"{'─' * 28}", f"🔌 <b>SERVICE CONNECTIONS</b>"])
    for svc_key, svc_name, svc_desc in [
        ("position_service", "Position Service", "Tracks open positions"),
        ("trade_coordinator", "Trade Coordinator", "Manages trade lifecycle"),
        ("pnl_manager", "PnL Manager", "Tracks profit and loss"),
        ("fund_manager", "Fund Manager", "Capital allocation"),
        ("account_service", "Account Service", "Bybit account data"),
        ("market_service", "Market Service", "Price and market data"),
    ]:
        svc = _svc(context, svc_key)
        emoji = "🟢" if svc else "🔴"
        lines.append(f"   {emoji} {svc_name} — {svc_desc}")

    # System resources
    lines.extend(["", f"{'─' * 28}", f"💻 <b>SYSTEM RESOURCES</b>"])
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        proc = psutil.Process(os.getpid())
        cpu_e = "🟢" if cpu < 50 else "🟡" if cpu < 80 else "🔴"
        mem_e = "🟢" if mem.percent < 70 else "🟡" if mem.percent < 85 else "🔴"
        disk_e = "🟢" if disk.percent < 70 else "🟡" if disk.percent < 85 else "🔴"
        lines.extend([
            f"   {cpu_e} CPU: {cpu:.1f}%",
            f"   {mem_e} RAM: {mem.used // (1024 * 1024)}MB / {mem.total // (1024 * 1024)}MB ({mem.percent}%)",
            f"   {disk_e} Disk: {disk.used / (1024 ** 3):.1f}GB / {disk.total / (1024 ** 3):.1f}GB ({disk.percent}%)",
            f"   📊 Load: {os.getloadavg()[0]:.2f} / {os.getloadavg()[1]:.2f} / {os.getloadavg()[2]:.2f}",
            f"   🐍 Bot PID {os.getpid()} │ {proc.memory_info().rss // (1024 * 1024)}MB │ {proc.num_threads()} threads",
        ])
    except Exception:
        try:
            load = os.getloadavg()
            lines.append(f"  Load: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")
        except Exception:
            pass
        lines.append(f"  PID: {os.getpid()}")

    # Dashboard status
    dash_active = context.bot_data.get("dashboard_active", False)
    lines.extend([
        f"",
        f"{'─' * 28}",
        f"📺 <b>DASHBOARD</b>",
        f"   Auto-refresh: {'🟢 Active' if dash_active else '🔴 Stopped'} │ {REFRESH_INTERVAL}s interval",
        f"   🕐 Uptime: {_uptime_str()} │ {now.strftime('%H:%M:%S')} UTC",
        f"",
        f"<i>Tap a layer button to toggle it on/off.</i>",
    ])

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    # Individual layer control buttons
    keyboard_rows = []
    if layer_manager:
        try:
            l1 = layer_manager.is_layer_active(1)
            l2 = layer_manager.is_layer_active(2)
            l3 = layer_manager.is_layer_active(3)
            keyboard_rows.append([
                InlineKeyboardButton(f"{'🟢' if l1 else '🔴'} Data", callback_data="dash_toggle_l1"),
                InlineKeyboardButton(f"{'🟢' if l2 else '🔴'} Brain", callback_data="dash_toggle_l2"),
                InlineKeyboardButton(f"{'🟢' if l3 else '🔴'} Execution", callback_data="dash_toggle_l3"),
            ])
        except Exception:
            pass
    keyboard_rows.append([
        InlineKeyboardButton("<- Back to Dashboard", callback_data="dash_back"),
    ])

    await update.message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


# ---------------------------------------------------------------
# CALLBACK HANDLER
# ---------------------------------------------------------------

async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ALL dashboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    try:
        # -- Start/Stop --
        if data == "dash_start_trading":
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                started = []
                for layer in [1, 2, 3]:
                    if not layer_manager.is_layer_active(layer):
                        ok, msg = await layer_manager.start_layer(
                            layer, reason="telegram_dash_start_trading",
                            actor=f"telegram_user:{chat_id}",
                        )
                        if ok:
                            started.append(f"L{layer}")
                        else:
                            log.warning("Layer {l} start failed: {m}", l=layer, m=msg)
                result = f"Trading started ({', '.join(started) if started else 'all running'})"
            else:
                result = "Layer manager not available"
            await _refresh_after_action(context, chat_id, result)
            return

        if data == "dash_stop_trading":
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                await layer_manager.stop_layer(
                    2,
                    reason="telegram_dash_stop_trading",
                    actor=f"telegram_user:{chat_id}",
                )
                result = "Trading stopped — Brain + Execution off. Data still collecting."
            else:
                result = "Layer manager not available"
            await _refresh_after_action(context, chat_id, result)
            return

        # -- Emergency --
        if data == "dash_emergency_close":
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                result = await layer_manager.emergency_close_all(
                    reason="telegram_dash_emergency",
                    actor=f"telegram_user:{chat_id}",
                )
            else:
                result = "Layer manager not available"
            await _refresh_after_action(context, chat_id, result)
            return

        # -- Mode toggle --
        if data == "dash_toggle_mode":
            trading_mode_mgr = _svc(context, "trading_mode")
            if trading_mode_mgr:
                from src.core.trading_mode import TradingModeType
                if trading_mode_mgr.mode.is_testnet:
                    await trading_mode_mgr.set_mode(TradingModeType.MAINNET)
                    result = "Switched to MAINNET"
                else:
                    await trading_mode_mgr.set_mode(TradingModeType.TESTNET)
                    result = "Switched to TESTNET"
            else:
                result = "Trading mode not available"
            await _refresh_after_action(context, chat_id, result)
            return

        # -- Transformer Switch (T5) --
        if data == "dash_switch_bybit":
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not configured.")
                return
            positions = await transformer.get_open_positions_summary()
            equity = await transformer.get_current_equity()
            eq_str = f"${equity['equity']:,.2f}" if equity.get("equity") else "N/A"
            pos_text = ""
            if positions["count"] > 0:
                for p in positions["positions"]:
                    pos_text += f"\n  {p['symbol']} {p['side']}"
            else:
                pos_text = "\n  None"

            text = (
                "⚠️ Switch to BYBIT MAINNET?\n\n"
                "WARNING: This will use REAL MONEY.\n\n"
                f"Shadow equity: {eq_str}\n"
                f"Open positions: {positions['count']}"
                f"{pos_text}\n"
                "\nAll positions will be closed before switching.\n"
                "\nPress CONFIRM to proceed."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("CONFIRM — Switch to Bybit", callback_data="dash_confirm_bybit")],
                [InlineKeyboardButton("Cancel", callback_data="dash_switch_cancel")],
            ])
            await query.edit_message_text(text=text, reply_markup=keyboard)
            return

        if data == "dash_switch_shadow":
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not configured.")
                return
            positions = await transformer.get_open_positions_summary()
            text = (
                "Switch to Shadow (paper trading)?\n\n"
                f"Open positions on Bybit: {positions['count']}\n"
                "(All will be closed before switching)\n\n"
                "Press to confirm."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, switch to Shadow", callback_data="dash_confirm_shadow")],
                [InlineKeyboardButton("Cancel", callback_data="dash_switch_cancel")],
            ])
            await query.edit_message_text(text=text, reply_markup=keyboard)
            return

        if data == "dash_confirm_bybit":
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not available.")
                return
            await query.edit_message_text("🔄 Switching to Bybit LIVE...\nClosing positions...")
            result = await transformer.switch_to("bybit", confirmed=True)
            if result["success"]:
                text = (
                    f"✅ Switched to BYBIT LIVE\n\n"
                    f"Positions closed: {result['positions_closed']}\n"
                    f"Now trading with REAL MONEY.\n"
                    f"Shadow equity: ${result.get('shadow_equity') or 0:,.2f}"
                )
            else:
                text = f"❌ Switch FAILED\n\n{result.get('error', 'Unknown error')}\n\nStill on Shadow."
            await query.edit_message_text(text=text)
            return

        if data == "dash_confirm_shadow":
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not available.")
                return
            await query.edit_message_text("🔄 Switching to Shadow...")
            result = await transformer.switch_to("shadow")
            if result["success"]:
                text = (
                    f"✅ Switched to Shadow\n\n"
                    f"Now paper trading. Zero risk.\n"
                    f"Shadow equity: ${result.get('shadow_equity') or 0:,.2f}"
                )
            else:
                text = f"❌ Switch FAILED\n\n{result.get('error', 'Unknown error')}\n\nStill on Bybit."
            await query.edit_message_text(text=text)
            return

        if data == "dash_switch_cancel":
            await _refresh_after_action(context, chat_id, "Switch cancelled.")
            return

        # ── Bybit Demo switch (Phase 5 of bybit_demo_adapter) ───────
        # Restart-based path. Preview → Confirm → ExchangeSwitcher.
        # Existing dash_switch_bybit / dash_confirm_bybit / dash_switch_shadow
        # / dash_confirm_shadow callbacks above are UNTOUCHED — they
        # still drive the live-bybit hot-swap via Transformer.switch_to.
        if data == "dash_switch_bybit_demo":
            _switch_log.info(
                f"EXCHANGE_SWITCH_REQUESTED | direction=bybit_demo "
                f"source=telegram_button user_id={getattr(query.from_user, 'id', 0)} "
                f"| {ctx()}"
            )
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not configured.")
                return
            positions = await transformer.get_open_positions_summary()
            equity = await transformer.get_current_equity()
            eq_str = f"${equity['equity']:,.2f}" if equity.get("equity") else "N/A"
            pos_text = ""
            if positions["count"] > 0:
                for p in positions["positions"]:
                    pos_text += f"\n  {p['symbol']} {p['side']}"
            else:
                pos_text = "\n  None"
            text = (
                "Switch to Bybit Demo (Paper Money)?\n\n"
                "This is paper-money execution against api-demo.bybit.com.\n"
                "No real money at risk.\n\n"
                f"Current exchange: {transformer.current_mode}\n"
                f"Current equity: {eq_str}\n"
                f"Open positions: {positions['count']}"
                f"{pos_text}\n\n"
                "All open positions will be closed at market.\n"
                "The system will RESTART. Estimated downtime: about 60 seconds.\n"
                "You will receive a confirmation once services are back up."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "Yes, switch to Bybit Demo",
                    callback_data="dash_confirm_bybit_demo",
                )],
                [InlineKeyboardButton("Cancel", callback_data="dash_switch_cancel")],
            ])
            await query.edit_message_text(text=text, reply_markup=keyboard)
            return

        if data == "dash_switch_shadow_from_demo":
            _switch_log.info(
                f"EXCHANGE_SWITCH_REQUESTED | direction=shadow "
                f"source=telegram_button user_id={getattr(query.from_user, 'id', 0)} "
                f"| {ctx()}"
            )
            transformer = _svc(context, "transformer")
            if not transformer:
                await query.edit_message_text("Transformer not configured.")
                return
            positions = await transformer.get_open_positions_summary()
            text = (
                "Switch back to Shadow (from Bybit Demo)?\n\n"
                f"Open positions on Bybit Demo: {positions['count']}\n"
                "All will be closed at market before switching.\n"
                "The system will RESTART. Estimated downtime: about 60 seconds."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "Yes, switch to Shadow",
                    callback_data="dash_confirm_shadow_from_demo",
                )],
                [InlineKeyboardButton("Cancel", callback_data="dash_switch_cancel")],
            ])
            await query.edit_message_text(text=text, reply_markup=keyboard)
            return

        if data in ("dash_confirm_bybit_demo", "dash_confirm_shadow_from_demo"):
            target_mode = (
                "bybit_demo" if data == "dash_confirm_bybit_demo" else "shadow"
            )
            _switch_log.info(
                f"EXCHANGE_SWITCH_CONFIRMED | target={target_mode} "
                f"source=telegram_button user_id={getattr(query.from_user, 'id', 0)} "
                f"| {ctx()}"
            )
            transformer = _svc(context, "transformer")
            alert_manager = _svc(context, "alert_manager")
            if not transformer:
                await query.edit_message_text("Transformer not available.")
                return
            await query.edit_message_text(
                f"Closing positions and switching to {target_mode}...\n"
                "System will restart. ETA about 60 seconds.\n"
                "You will receive a confirmation once services are up."
            )
            try:
                from src.exchanges.switching import ExchangeSwitcher
                switcher = ExchangeSwitcher(transformer, alert_manager)
                result = await switcher.execute_switch_with_restart(
                    target_mode,
                    force=True,
                    reason="telegram_dashboard_button",
                )
            except Exception as e:
                await query.edit_message_text(
                    f"Switch FAILED: {e}\n\nSystem unchanged."
                )
                return

            if result.get("success"):
                await query.edit_message_text(
                    f"Closing complete (closed {result.get('positions_closed', 0)} "
                    f"position(s)). Restart triggered.\n"
                    "Wait about 60 seconds for the system to come back up."
                )
            else:
                await query.edit_message_text(
                    f"Switch FAILED: {result.get('error', 'unknown error')}\n\n"
                    f"System unchanged (still on {transformer.current_mode})."
                )
            return

        # -- Tier --
        if data.startswith("dash_tier_"):
            pct = int(data.split("_")[-1])
            tiered_capital = _svc(context, "tiered_capital")
            if tiered_capital:
                await tiered_capital.set_user_override(pct / 100.0)
                result = f"Capital override: {pct}%"
            else:
                result = "Tiered capital not available"
            await _refresh_after_action(context, chat_id, result)
            return

        # -- Individual Layer Toggle --
        if data.startswith("dash_toggle_l"):
            layer_num = int(data[-1])  # 1, 2, or 3
            layer_names = {1: "Data", 2: "Brain", 3: "Execution"}
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                if layer_manager.is_layer_active(layer_num):
                    ok, msg = await layer_manager.stop_layer(
                        layer_num,
                        reason="telegram_dash_toggle",
                        actor=f"telegram_user:{chat_id}",
                    )
                    result = f"{layer_names[layer_num]} stopped. {msg}"
                else:
                    ok, msg = await layer_manager.start_layer(
                        layer_num,
                        reason="telegram_dash_toggle",
                        actor=f"telegram_user:{chat_id}",
                    )
                    if ok:
                        result = f"{layer_names[layer_num]} started."
                    else:
                        result = f"Cannot start {layer_names[layer_num]}: {msg}"
            else:
                result = "Layer manager not available"
            # Re-show workers view instead of main dashboard
            # Simulate pressing Workers button again
            await _refresh_after_action(context, chat_id, result)
            return

        # -- Close position --
        if data.startswith("dash_close_"):
            symbol = data.replace("dash_close_", "")
            sym_clean = symbol.replace("USDT", "")
            position_service = _svc(context, "position_service")
            if position_service:
                try:
                    await position_service.close_position(symbol)
                    result = f"{sym_clean} closed"
                except Exception as e:
                    result = f"Close {sym_clean} failed: {str(e)[:80]}"
            else:
                result = "Position service not available"
            await _refresh_after_action(context, chat_id, result)
            return

        # -- APEX Views --
        if data == "dash_view_apex":
            text, keyboard = await _build_apex_view(context)
            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML", reply_markup=keyboard,
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        if data == "dash_apex_flips":
            text, keyboard = await _build_apex_flips_view(context)
            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML", reply_markup=keyboard,
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        if data == "dash_apex_symbols":
            text, keyboard = await _build_apex_symbols_view(context)
            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML", reply_markup=keyboard,
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        # -- View Plan (button = SAME as /plan command) --
        if data == "dash_view_plan":
            now = datetime.now(timezone.utc)
            layer_manager = _svc(context, "layer_manager")
            lines = [f"{'=' * 30}", f"<b>STRATEGIC PLAN</b>", f"{'=' * 30}\n"]

            if layer_manager:
                try:
                    plan = layer_manager.get_plan()
                    if plan and hasattr(plan, "to_telegram_text"):
                        lines.append(plan.to_telegram_text())
                    elif plan:
                        mv = getattr(plan, "market_view", "")
                        if mv:
                            lines.extend([f"<b>MARKET VIEW</b>", f"  {mv[:300]}", ""])
                        risk_lvl = getattr(plan, "risk_level", "")
                        if risk_lvl:
                            lines.append(f"<b>Risk:</b> {risk_lvl}")
                        focus = getattr(plan, "focus_coins", [])
                        if focus:
                            lines.append(f"<b>Focus:</b> {', '.join(focus[:8])}")
                        notes = getattr(plan, "raw_reasoning", "") or getattr(plan, "reasoning", "")
                        if notes:
                            lines.extend(["", f"<b>REASONING</b>", f"  {str(notes)[:400]}"])
                    else:
                        lines.append("No plan yet. Wait for first 5-min review.")
                except Exception as e:
                    lines.append(f"Error: {str(e)[:100]}")
            else:
                lines.append("Layer manager not available")

            lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3950] + "\n<i>(truncated)</i>"

            back_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("<- Dashboard", callback_data="dash_back")
            ]])
            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML", reply_markup=back_btn,
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        # -- View Performance (button = SAME as /performance) --
        if data == "dash_view_performance":
            now = datetime.now(timezone.utc)
            pnl_manager = _svc(context, "pnl_manager")
            lines = [f"{'=' * 30}", f"<b>PERFORMANCE REPORT</b>", f"{'=' * 30}\n"]

            t = _safe_getattr(pnl_manager, "_trades_today", 0) if pnl_manager else 0
            w = _safe_getattr(pnl_manager, "_wins_today", 0) if pnl_manager else 0
            lo = _safe_getattr(pnl_manager, "_losses_today", 0) if pnl_manager else 0
            dp = _safe_getattr(pnl_manager, "current_pnl_pct", 0) if pnl_manager else 0
            du = _safe_getattr(pnl_manager, "current_pnl_usd", 0) if pnl_manager else 0
            bst = _safe_getattr(pnl_manager, "_best_trade_pct", 0) if pnl_manager else 0
            wst = _safe_getattr(pnl_manager, "_worst_trade_pct", 0) if pnl_manager else 0
            aw = _safe_getattr(pnl_manager, "_avg_win_pct", 0) if pnl_manager else 0
            al = _safe_getattr(pnl_manager, "_avg_loss_pct", 0) if pnl_manager else 0

            wr = f"{w / t * 100:.0f}%" if t > 0 else "---"
            exp = 0
            if t > 0 and al != 0:
                exp = (w / t * aw) + (lo / t * al)
            pf_val = "---"
            if al != 0 and lo > 0:
                gp = w * abs(aw)
                gl = lo * abs(al)
                if gl > 0:
                    pf_val = f"{gp / gl:.2f}"

            lines.extend([
                f"<b>DAILY PnL:</b> {_format_pct(dp)} ({_format_money(du)})",
                f"Trades: {t} | W: {w} ({wr}) | L: {lo}",
                f"Best: {_format_pct(bst)} | Worst: {_format_pct(wst)}",
                f"Avg W: {_format_pct(aw)} | Avg L: {_format_pct(al)}",
                f"Expectancy: {_format_pct(exp)}/trade | PF: {pf_val}",
            ])

            claude_client = _svc(context, "claude_client")
            if claude_client and hasattr(claude_client, "get_stats"):
                stats = claude_client.get_stats()
                lines.extend([
                    f"",
                    f"Claude: {stats.get('calls_today', 0)} calls | "
                    f"{stats.get('consecutive_failures', 0)} failures",
                ])

            lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3950] + "\n<i>(truncated)</i>"

            back_btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("<- Dashboard", callback_data="dash_back")
            ]])
            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML", reply_markup=back_btn,
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        # -- View Workers (button = SAME as /workers) --
        if data == "dash_view_workers":
            now = datetime.now(timezone.utc)
            layer_manager = _svc(context, "layer_manager")
            claude_client = _svc(context, "claude_client")

            lines = [f"{'=' * 30}", f"<b>SYSTEM STATUS</b>", f"{'=' * 30}\n"]
            lines.append(f"<b>LAYERS</b>")
            if layer_manager:
                try:
                    status = layer_manager.get_status()
                    for key in ["layer_1", "layer_2", "layer_3"]:
                        ld = status.get(key, {})
                        name = ld.get("name", key.replace("_", " ").title())
                        active = ld.get("active", False)
                        uptime_sec = ld.get("uptime_seconds", 0)
                        uptime = _format_duration(uptime_sec / 60) if uptime_sec else "---"
                        ind = "ON" if active else "OFF"
                        lines.append(f"  [{ind}] {name} | Uptime {uptime}")
                except Exception:
                    lines.append("  Error reading layers")
            else:
                lines.append("  Not available")

            lines.extend(["", f"<b>CLAUDE AI</b>"])
            if claude_client and hasattr(claude_client, "get_stats"):
                stats = claude_client.get_stats()
                lines.append(
                    f"  Calls: {stats.get('calls_today', 0)} | "
                    f"Failures: {stats.get('consecutive_failures', 0)} | "
                    f"Interval: {stats.get('adaptive_interval', '?')}s"
                )
            else:
                lines.append("  Not available")

            lines.extend(["", f"<b>SERVICES</b>"])
            for sk, sn in [
                ("position_service", "Positions"), ("trade_coordinator", "Coordinator"),
                ("pnl_manager", "PnL"), ("account_service", "Account"),
                ("market_service", "Market"),
            ]:
                sv = _svc(context, sk)
                lines.append(f"  [{'OK' if sv else '--'}] {sn}")

            try:
                load = os.getloadavg()
                lines.extend(["", f"Load: {load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f} | PID {os.getpid()}"])
            except Exception:
                pass

            dash_active = context.bot_data.get("dashboard_active", False)
            lines.append(f"Dashboard: {'Active' if dash_active else 'Stopped'} | Uptime: {_uptime_str()}")
            lines.append(f"\n<i>Tap a layer button to toggle it on/off.</i>")
            lines.append(f"<i>{now.strftime('%H:%M:%S')} UTC</i>")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3950] + "\n<i>(truncated)</i>"

            # Individual layer control buttons
            layer_buttons = []
            if layer_manager:
                try:
                    l1 = layer_manager.is_layer_active(1)
                    l2 = layer_manager.is_layer_active(2)
                    l3 = layer_manager.is_layer_active(3)
                    layer_buttons.append([
                        InlineKeyboardButton(
                            f"{'🟢' if l1 else '🔴'} Data",
                            callback_data="dash_toggle_l1",
                        ),
                        InlineKeyboardButton(
                            f"{'🟢' if l2 else '🔴'} Brain",
                            callback_data="dash_toggle_l2",
                        ),
                        InlineKeyboardButton(
                            f"{'🟢' if l3 else '🔴'} Execution",
                            callback_data="dash_toggle_l3",
                        ),
                    ])
                except Exception:
                    pass
            layer_buttons.append([
                InlineKeyboardButton("<- Dashboard", callback_data="dash_back"),
            ])

            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(layer_buttons),
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        # -- View Positions (button) --
        if data == "dash_view_positions":
            now = datetime.now(timezone.utc)
            position_service = _svc(context, "position_service")
            trade_coordinator = _svc(context, "trade_coordinator")
            account_service = _svc(context, "account_service")

            lines = [f"{'=' * 30}", f"<b>POSITIONS DETAIL</b>", f"{'=' * 30}\n"]

            positions = []
            if position_service:
                try:
                    positions = await position_service.get_positions()
                except Exception as e:
                    lines.append(f"Error: {str(e)[:80]}")

            equity = 0.0
            if account_service:
                try:
                    account = await account_service.get_wallet_balance()
                    equity = getattr(account, "total_equity", 0) or 0
                except Exception:
                    pass

            if not positions:
                lines.extend(["  --- No open positions ---", "", "  Send /plan for market view."])
            else:
                total_unr = 0.0
                total_not = 0.0
                for i, pos in enumerate(positions, 1):
                    pnl_pct = 0.0
                    if pos.entry_price > 0:
                        pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
                        if hasattr(pos, "side") and pos.side.value in ("Sell", "Short"):
                            pnl_pct = -pnl_pct
                    unr = getattr(pos, "unrealized_pnl", 0) or 0
                    total_unr += unr
                    qty = getattr(pos, "size", 0) or 0
                    lev = getattr(pos, "leverage", 1) or 1
                    not_val = abs(qty * pos.mark_price)
                    total_not += not_val

                    plan = None
                    if trade_coordinator:
                        try:
                            plan = trade_coordinator.get_trade_plan(pos.symbol)
                        except Exception:
                            pass

                    sl = _fmt_price(context, plan.stop_loss_price, pos.symbol) if plan and hasattr(plan, "stop_loss_price") else "---"
                    tp = _fmt_price(context, plan.target_price, pos.symbol) if plan and hasattr(plan, "target_price") else "---"
                    age = _format_duration(plan.age_minutes) if plan and hasattr(plan, "age_minutes") else "?"
                    max_h = f"{plan.max_hold_minutes}min" if plan and hasattr(plan, "max_hold_minutes") else "?"
                    trail = " Trail" if plan and getattr(plan, "trailing_active", False) else ""
                    thesis = plan.reasoning[:100] if plan and hasattr(plan, "reasoning") and plan.reasoning else "---"

                    rr = "---"
                    if plan and hasattr(plan, "stop_loss_price") and hasattr(plan, "target_price"):
                        risk = abs(pos.entry_price - plan.stop_loss_price)
                        reward = abs(plan.target_price - pos.entry_price)
                        if risk > 0:
                            rr = f"1:{reward / risk:.1f}"

                    side_str = pos.side.value if hasattr(pos, "side") else "?"
                    sym = pos.symbol.replace("USDT", "")
                    wt = f"{(not_val / equity * 100):.1f}%" if equity > 0 else "---"

                    lines.extend([
                        f"<b>#{i} {sym} {side_str} {lev}x</b>",
                        f"  {_fmt_price(context, pos.entry_price, pos.symbol)} -> {_fmt_price(context, pos.mark_price, pos.symbol)}",
                        f"  PnL <b>{pnl_pct:+.2f}%</b> ({_format_money(unr)})",
                        f"  SL {sl} | TP {tp} | R:R {rr}",
                        f"  Size {qty} | ${not_val:,.2f} | Weight {wt}",
                        f"  Held {age} | Max {max_h}{trail}",
                        f"  <i>{thesis}</i>",
                        f"",
                    ])

                lines.extend([
                    f"{'_' * 28}",
                    f"<b>Totals:</b> {len(positions)} pos | ${total_not:,.2f} notional | {_format_money(total_unr)} unrealized",
                ])
                if equity > 0:
                    lines.append(f"Exposure: {total_not / equity * 100:.1f}%")

            lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3950] + "\n<i>(truncated)</i>"

            keyboard_rows = []
            for pos in positions:
                sym_short = pos.symbol.replace("USDT", "")
                keyboard_rows.append([
                    InlineKeyboardButton(f"X Close {sym_short}", callback_data=f"dash_close_{pos.symbol}")
                ])
            keyboard_rows.append([
                InlineKeyboardButton("<- Dashboard", callback_data="dash_back")
            ])

            old_id = context.bot_data.get("dashboard_msg_id")
            await _delete_dashboard(context.bot, chat_id, old_id)
            msg = await _safe_bot_send(
                context.bot, chat_id, text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard_rows),
            )
            if msg:
                context.bot_data["dashboard_msg_id"] = msg.message_id
            return

        # -- Back to Dashboard --
        if data == "dash_back":
            await _refresh_after_action(context, chat_id, "")
            return

    except Exception as e:
        log.error("Dashboard callback {data} failed: {err}", data=data, err=str(e))
        try:
            await query.edit_message_text(
                f"Action failed: {str(e)[:200]}\n\nSend /control to restart dashboard."
            )
        except Exception:
            pass


async def _refresh_after_action(context, chat_id: int, result: str) -> None:
    """After a button action: delete old, send new dashboard with result."""
    old_id = context.bot_data.get("dashboard_msg_id")
    await _delete_dashboard(context.bot, chat_id, old_id)
    new_id = await _send_dashboard(context.bot, context, chat_id, action_result=result)
    context.bot_data["dashboard_msg_id"] = new_id


# ---------------------------------------------------------------
# APEX DASHBOARD VIEWS
# ---------------------------------------------------------------

async def _build_apex_view(context) -> tuple[str, InlineKeyboardMarkup]:
    """Build the full APEX optimizer dashboard sub-view."""
    now = datetime.now(timezone.utc)

    lines = [
        f"{'━' * 30}",
        f"⚡ <b>APEX OPTIMIZER DASHBOARD</b>",
        f"{'━' * 30}\n",
    ]

    # -- Section 1: Live Session Stats --
    optimizer = _svc(context, "apex_optimizer")
    if optimizer:
        stats = optimizer.get_stats()
        optimized = stats.get("optimized", 0)
        fallbacks = stats.get("fallbacks", 0)
        flips = stats.get("flips", 0)
        total = optimized + fallbacks
        qwen = stats.get("qwen_stats", {})

        lines.append("<b>📡 Live Session</b>")
        if total > 0:
            opt_pct = optimized / total * 100
            lines.append(f"   Optimized: {optimized} / {total} ({opt_pct:.0f}% hit rate)")
            lines.append(f"   Fallbacks: {fallbacks}")
            lines.append(f"   Direction flips: {flips} ({stats.get('flip_rate', 0):.0%})")
            lines.append(f"   Avg latency: {stats.get('avg_time_ms', 0)}ms")
            lines.append(f"   API calls: {qwen.get('calls', 0)} │ Cost: ${qwen.get('cost', 0):.4f}")
        else:
            lines.append("   No optimizations this session yet")
        lines.append("")
    else:
        lines.append("⚠️ APEX optimizer not active\n")

    # -- Section 2: Historical Performance (DB) --
    db = _svc(context, "db")
    if db:
        try:
            row = await db.fetch_one("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN apex_optimized = 1 THEN 1 ELSE 0 END) AS opt,
                    SUM(CASE WHEN apex_optimized = 1 AND win = 1 THEN 1 ELSE 0 END) AS opt_w,
                    SUM(CASE WHEN apex_optimized = 1 AND win = 0 THEN 1 ELSE 0 END) AS opt_l,
                    SUM(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0) AND win = 1
                        THEN 1 ELSE 0 END) AS no_w,
                    SUM(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0) AND win = 0
                        THEN 1 ELSE 0 END) AS no_l,
                    SUM(CASE WHEN apex_flipped = 1 THEN 1 ELSE 0 END) AS flipped,
                    SUM(CASE WHEN apex_flipped = 1 AND win = 1 THEN 1 ELSE 0 END) AS flip_w,
                    ROUND(AVG(CASE WHEN apex_optimized = 1 THEN pnl_pct END), 3) AS opt_pnl,
                    ROUND(AVG(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0)
                        THEN pnl_pct END), 3) AS no_pnl,
                    ROUND(SUM(CASE WHEN apex_optimized = 1 THEN pnl_usd ELSE 0 END), 2) AS opt_usd,
                    ROUND(SUM(CASE WHEN (apex_optimized IS NULL OR apex_optimized = 0)
                        THEN pnl_usd ELSE 0 END), 2) AS no_usd,
                    ROUND(SUM(COALESCE(apex_cost_usd, 0)), 4) AS api_cost,
                    ROUND(AVG(CASE WHEN apex_optimized = 1 THEN apex_response_ms END), 0) AS avg_ms
                FROM trade_intelligence
            """)
            if row:
                r = dict(row)
                opt_total = (r.get("opt_w") or 0) + (r.get("opt_l") or 0)
                no_total = (r.get("no_w") or 0) + (r.get("no_l") or 0)
                opt_wr = ((r.get("opt_w") or 0) / opt_total * 100) if opt_total > 0 else 0
                no_wr = ((r.get("no_w") or 0) / no_total * 100) if no_total > 0 else 0
                flip_total = r.get("flipped") or 0
                flip_wr = ((r.get("flip_w") or 0) / flip_total * 100) if flip_total > 0 else 0

                lines.append("<b>📊 Historical Performance</b>")

                # APEX vs Non-APEX comparison
                delta_wr = opt_wr - no_wr
                delta_icon = "📈" if delta_wr > 0 else "📉" if delta_wr < 0 else "➡️"
                lines.append(f"   APEX trades: {opt_total} ({opt_wr:.0f}% WR, avg {r.get('opt_pnl') or 0:+.3f}%)")
                lines.append(f"   Non-APEX:    {no_total} ({no_wr:.0f}% WR, avg {r.get('no_pnl') or 0:+.3f}%)")
                lines.append(f"   {delta_icon} APEX edge: {delta_wr:+.1f}pp win rate")

                # PnL comparison
                opt_usd = r.get("opt_usd") or 0
                no_usd = r.get("no_usd") or 0
                lines.append(f"\n   💰 APEX PnL: {_format_money(opt_usd)}")
                lines.append(f"   💰 Non-APEX PnL: {_format_money(no_usd)}")

                if flip_total > 0:
                    lines.append(f"\n   🔄 Flips: {flip_total} ({flip_wr:.0f}% WR)")

                lines.append(f"\n   🔧 API cost: ${r.get('api_cost') or 0:.4f} │ Avg: {r.get('avg_ms') or 0:.0f}ms")
                lines.append("")
        except Exception as e:
            lines.append(f"   DB query error: {str(e)[:80]}\n")

    # -- Section 3: Recent Optimizations --
    if db:
        try:
            trades = await db.fetch_all("""
                SELECT symbol, direction, pnl_pct, pnl_usd, win,
                       apex_original_direction, apex_flipped,
                       apex_original_size, apex_final_size,
                       apex_confidence, apex_tp_mode,
                       gate_adjustments,
                       substr(apex_reasoning, 1, 80) AS reason
                FROM trade_intelligence
                WHERE apex_optimized = 1
                ORDER BY id DESC LIMIT 5
            """)
            if trades:
                lines.append("<b>🕐 Recent APEX Trades</b>")
                for t in trades:
                    icon = "🟢" if t.get("win") else "🔴"
                    pnl = t.get("pnl_pct") or 0
                    sym = (t.get("symbol") or "?").replace("USDT", "")
                    d = t.get("direction") or "?"
                    flip = " 🔄" if t.get("apex_flipped") else ""
                    conf = t.get("apex_confidence") or 0
                    mode = t.get("apex_tp_mode") or "fixed"

                    orig_sz = t.get("apex_original_size") or 0
                    final_sz = t.get("apex_final_size") or 0
                    sz_str = ""
                    if orig_sz and final_sz and orig_sz != final_sz:
                        sz_str = f" sz${orig_sz:.0f}→${final_sz:.0f}"

                    gate = t.get("gate_adjustments") or ""
                    gate_str = f" gate:{gate}" if gate else ""

                    lines.append(
                        f"   {icon} <b>{sym}</b> {d}{flip} {pnl:+.2f}% "
                        f"(${(t.get('pnl_usd') or 0):+.2f})"
                    )
                    lines.append(
                        f"      conf={conf:.0%} mode={mode}{sz_str}{gate_str}"
                    )
                    reason = t.get("reason") or ""
                    if reason:
                        lines.append(f"      <i>{reason}</i>")
                lines.append("")
        except Exception:
            pass

    lines.append(f"<i>{now.strftime('%H:%M:%S')} UTC</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Flips", callback_data="dash_apex_flips"),
            InlineKeyboardButton("📊 Symbols", callback_data="dash_apex_symbols"),
        ],
        [InlineKeyboardButton("← Back to Dashboard", callback_data="dash_back")],
    ])

    return text, keyboard


async def _build_apex_flips_view(context) -> tuple[str, InlineKeyboardMarkup]:
    """Build the APEX direction flips detail view."""
    now = datetime.now(timezone.utc)
    lines = [
        f"{'━' * 30}",
        f"🔄 <b>APEX DIRECTION FLIPS</b>",
        f"{'━' * 30}\n",
    ]

    db = _svc(context, "db")
    if db:
        try:
            # Summary
            summary = await db.fetch_one("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_pct), 3) AS avg_pnl,
                    ROUND(SUM(pnl_usd), 2) AS total_usd
                FROM trade_intelligence WHERE apex_flipped = 1
            """)
            if summary:
                s = dict(summary)
                total = s.get("total") or 0
                wins = s.get("wins") or 0
                wr = (wins / total * 100) if total > 0 else 0
                lines.append(f"<b>Summary:</b> {total} flips │ {wins}W/{total - wins}L │ {wr:.0f}% WR")
                lines.append(f"   Avg PnL: {s.get('avg_pnl') or 0:+.3f}% │ Total: {_format_money(s.get('total_usd') or 0)}")
                lines.append("")

            # Individual flips
            flips = await db.fetch_all("""
                SELECT symbol, direction, apex_original_direction,
                       pnl_pct, pnl_usd, win, apex_confidence,
                       substr(apex_reasoning, 1, 100) AS reason,
                       trade_closed_at
                FROM trade_intelligence
                WHERE apex_flipped = 1
                ORDER BY id DESC LIMIT 10
            """)
            if flips:
                for f in flips:
                    icon = "🟢" if f.get("win") else "🔴"
                    sym = (f.get("symbol") or "?").replace("USDT", "")
                    orig = f.get("apex_original_direction") or "?"
                    final = f.get("direction") or "?"
                    pnl = f.get("pnl_pct") or 0
                    conf = f.get("apex_confidence") or 0

                    lines.append(
                        f"{icon} <b>{sym}</b>: {orig} → {final} "
                        f"{pnl:+.2f}% (${(f.get('pnl_usd') or 0):+.2f}) conf={conf:.0%}"
                    )
                    reason = f.get("reason") or ""
                    if reason:
                        lines.append(f"   <i>{reason}</i>")
            else:
                lines.append("No direction flips recorded yet.")
        except Exception as e:
            lines.append(f"Error: {str(e)[:100]}")
    else:
        lines.append("Database not available.")

    lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("← APEX Dashboard", callback_data="dash_view_apex")],
        [InlineKeyboardButton("← Main Dashboard", callback_data="dash_back")],
    ])

    return text, keyboard


async def _build_apex_symbols_view(context) -> tuple[str, InlineKeyboardMarkup]:
    """Build per-symbol APEX performance breakdown."""
    now = datetime.now(timezone.utc)
    lines = [
        f"{'━' * 30}",
        f"📊 <b>APEX PER-SYMBOL PERFORMANCE</b>",
        f"{'━' * 30}\n",
    ]

    db = _svc(context, "db")
    if db:
        try:
            symbols = await db.fetch_all("""
                SELECT
                    symbol,
                    COUNT(*) AS trades,
                    SUM(CASE WHEN win = 1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_pct), 3) AS avg_pnl,
                    ROUND(SUM(pnl_usd), 2) AS total_usd,
                    SUM(CASE WHEN apex_flipped = 1 THEN 1 ELSE 0 END) AS flips,
                    ROUND(AVG(apex_confidence), 2) AS avg_conf,
                    ROUND(AVG(apex_response_ms), 0) AS avg_ms
                FROM trade_intelligence
                WHERE apex_optimized = 1
                GROUP BY symbol
                ORDER BY trades DESC
                LIMIT 15
            """)
            if symbols:
                for s in symbols:
                    sym = (s.get("symbol") or "?").replace("USDT", "")
                    trades = s.get("trades") or 0
                    wins = s.get("wins") or 0
                    wr = (wins / trades * 100) if trades > 0 else 0
                    avg_pnl = s.get("avg_pnl") or 0
                    total_usd = s.get("total_usd") or 0
                    flips = s.get("flips") or 0
                    avg_conf = s.get("avg_conf") or 0

                    icon = "🟢" if avg_pnl > 0 else "🔴" if avg_pnl < 0 else "⚪"
                    flip_str = f" 🔄{flips}" if flips > 0 else ""

                    lines.append(
                        f"{icon} <b>{sym}</b>: {trades} trades │ {wr:.0f}% WR │ "
                        f"avg {avg_pnl:+.3f}%{flip_str}"
                    )
                    lines.append(
                        f"   PnL: {_format_money(total_usd)} │ "
                        f"conf={avg_conf:.0%} │ {s.get('avg_ms') or 0:.0f}ms"
                    )
            else:
                lines.append("No APEX-optimized trades yet.")
        except Exception as e:
            lines.append(f"Error: {str(e)[:100]}")
    else:
        lines.append("Database not available.")

    lines.append(f"\n<i>{now.strftime('%H:%M:%S')} UTC</i>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n<i>(truncated)</i>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("← APEX Dashboard", callback_data="dash_view_apex")],
        [InlineKeyboardButton("← Main Dashboard", callback_data="dash_back")],
    ])

    return text, keyboard


# ---------------------------------------------------------------
# REGISTRATION
# ---------------------------------------------------------------

def register_dashboard_handlers(app: Application, services: dict = None) -> None:
    """Register ALL dashboard handlers with the Telegram Application."""
    if not HAS_TELEGRAM:
        log.error("python-telegram-bot not installed")
        return

    # Store services in bot_data
    if services:
        for key, value in services.items():
            if value is not None:
                app.bot_data[key] = value
        stored = sum(1 for v in services.values() if v is not None)
        log.info("Dashboard: stored {n} services in bot_data", n=stored)

    # Initialize state
    app.bot_data.setdefault("dashboard_msg_id", None)
    app.bot_data.setdefault("dashboard_active", False)
    app.bot_data.setdefault("dashboard_chat_id", None)
    app.bot_data.setdefault("_cached_positions", [])
    app.bot_data.setdefault("_cached_pos_count", 0)

    # Commands
    app.add_handler(CommandHandler("control", control_command))
    app.add_handler(CommandHandler("dashboard", control_command))
    app.add_handler(CommandHandler("stopdash", stop_dashboard_command))
    app.add_handler(CommandHandler("positions", positions_command))
    app.add_handler(CommandHandler("performance", performance_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("workers", workers_command))

    # Control commands from control_handler (/capital, /mode + their callbacks)
    try:
        from src.telegram.handlers.control_handler import (
            capital_command, mode_command, control_callback,
        )
        app.add_handler(CommandHandler("capital", capital_command))
        app.add_handler(CommandHandler("mode", mode_command))
        app.add_handler(CallbackQueryHandler(
            control_callback,
            pattern="^(layer_|emergency_|view_|brain_interval_|refresh_|capital_|mode_)",
        ))
        log.info("Control handlers registered: /capital, /mode + layer/capital/mode callbacks")
    except Exception as e:
        log.warning("Control handlers unavailable: {err}", err=str(e))

    # Callbacks
    app.add_handler(CallbackQueryHandler(
        dashboard_callback,
        pattern="^dash_",
    ))

    # Auto-start on boot
    settings_obj = services.get("settings") if services else None
    chat_id = None
    if settings_obj and hasattr(settings_obj, "alerts"):
        _cid = getattr(settings_obj.alerts, "chat_id", "")
        if _cid:
            chat_id = int(_cid)
    if not chat_id:
        _env_cid = os.environ.get("TELEGRAM_CHAT_ID", "")
        if _env_cid:
            chat_id = int(_env_cid)

    if chat_id:
        app.bot_data["dashboard_active"] = True
        app.bot_data["dashboard_chat_id"] = chat_id
        app.bot_data["_refresh_task"] = None
        log.info("Dashboard auto-start configured for chat {cid}", cid=chat_id)

        # Start a single refresh mechanism (JobQueue preferred, asyncio fallback)
        if app.job_queue:
            try:
                app.job_queue.run_repeating(
                    _auto_refresh_job, interval=REFRESH_INTERVAL, first=20,
                    name="dashboard_refresh",
                )
                log.info("Dashboard auto-refresh via JobQueue ({sec}s)", sec=REFRESH_INTERVAL)
            except Exception:
                task = asyncio.ensure_future(_auto_refresh_loop(app))
                app.bot_data["_refresh_task"] = task
                log.info("Dashboard auto-refresh via asyncio ({sec}s)", sec=REFRESH_INTERVAL)
        else:
            task = asyncio.ensure_future(_auto_refresh_loop(app))
            app.bot_data["_refresh_task"] = task
            log.info("Dashboard auto-refresh via asyncio ({sec}s)", sec=REFRESH_INTERVAL)

    log.info(
        "Dashboard handlers registered: "
        "/control, /dashboard, /stopdash, /positions, /performance, /plan, /workers, "
        "/capital, /mode + buttons"
    )
