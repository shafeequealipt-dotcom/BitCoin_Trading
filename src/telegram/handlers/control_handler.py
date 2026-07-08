"""Control dashboard support: /capital, /mode and the inline-button callback.

This module backs commands that dashboard_handler.register_dashboard_handlers()
imports (`capital_command`, `mode_command`, `control_callback`). The
top-level commands `/control`, `/dashboard`, `/plan`, `/positions`,
`/performance` all live in dashboard_handler.py — don't duplicate them here.

Inline-button callbacks handled by `control_callback`:
  layer_start_1/2/3 / layer_stop_1/2/3  -> start/stop layers
  emergency_close                       -> close all positions
  view_plan / view_positions            -> show plan / positions (helpers below)
  brain_interval_60/180/300             -> set brain review interval
  refresh_dashboard                     -> refresh the dashboard display
  capital_* / mode_*                    -> tiered capital / mode callbacks
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.core.logging import get_logger

log = get_logger("control_handler")


# ─── Helpers ──────────────────────────────────────────────────────────

def _svc(context, name):
    """Get a service from bot_data. Returns None if unavailable."""
    return context.bot_data.get(name)


def _fmt_price(context, price, symbol: str = "") -> str:
    """Render *price* for *symbol* at exact exchange tick precision via the
    wired PriceFormatter, with a magnitude-aware fallback when the service
    is unavailable. Returns a $-prefixed string. Callers guard ``None``."""
    pf = _svc(context, "price_formatter")
    if pf is not None:
        return pf.format(price, symbol)
    from src.telegram.ui.formatters import format_price as _ui_fp
    return _ui_fp(price)


# ─── Dashboard helpers (used by control_callback button presses) ─────

async def _build_dashboard_text(context) -> str:
    """Build the full dashboard text with ALL parameters."""
    layer_manager = _svc(context, "layer_manager")

    # Defaults
    l1_active = l2_active = l3_active = False
    market_view = "No data"
    risk_level = "normal"
    max_pos = "?"
    sl_pct = tp_pct = hold_min = leverage = trailing = "?"
    focus = "none"
    brain_interval = 180
    watchdog_interval = 10
    plan_age = "?"

    if layer_manager:
        status = layer_manager.get_status()
        l1 = status["layer_1"]
        l2 = status["layer_2"]
        l3 = status["layer_3"]
        p = status["plan"]
        d = p["defaults"]

        l1_active = l1["active"]
        l2_active = l2["active"]
        l3_active = l3["active"]
        market_view = p.get("market_view", "No data")
        risk_level = p.get("risk_level", "normal")
        max_pos = p.get("max_positions", "?")
        sl_pct = d.get("sl_pct", "?")
        tp_pct = d.get("tp_pct", "?")
        hold_min = d.get("hold_min", "?")
        leverage = d.get("leverage", "?")
        trailing = d.get("trailing", "?")
        focus = ", ".join(p.get("focus_coins", [])[:5]) or "none"
        brain_interval = l2.get("review_interval", 180)
        watchdog_interval = l3.get("watchdog_interval", 10)
        plan_age_s = l2.get("plan_age_seconds", 0)
        plan_age = f"{int(plan_age_s)}s" if plan_age_s < 120 else f"{int(plan_age_s // 60)}min"

    # Position count + unrealized PnL
    pos_count = 0
    total_pnl = 0.0
    position_service = _svc(context, "position_service")
    if position_service:
        try:
            positions = await position_service.get_positions()
            pos_count = len(positions)
            for pos in positions:
                if pos.entry_price > 0:
                    pnl = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
                    side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                    if side_val in ("Sell", "Short"):
                        pnl = -pnl
                    total_pnl += pnl
        except Exception:
            pass

    # Claude stats
    claude_calls = 0
    claude_failures = 0
    claude_client = _svc(context, "claude_client")
    if claude_client and hasattr(claude_client, "get_stats"):
        stats = claude_client.get_stats()
        claude_calls = stats.get("calls_today", 0)
        claude_failures = stats.get("consecutive_failures", 0)

    # Daily PnL + trades
    daily_pnl = "?"
    trades_today = wins = losses = 0
    pnl_manager = _svc(context, "pnl_manager")
    if pnl_manager:
        daily_pnl = f"{pnl_manager.current_pnl_pct:+.2f}%"
        trades_today = getattr(pnl_manager, "_trades_today", 0)
        wins = getattr(pnl_manager, "_wins_today", 0)
        losses = getattr(pnl_manager, "_losses_today", 0)

    win_rate = f"{wins / trades_today * 100:.0f}%" if trades_today > 0 else "N/A"

    # Profit floor
    floor_text = ""
    fund_manager = _svc(context, "fund_manager")
    if fund_manager and getattr(fund_manager, "_account_state", None):
        try:
            state = fund_manager._account_state
            floor = state.profit_floor or 0
            equity = state.total_equity or 0
            if floor > 0:
                diff = equity - floor
                label = "Above" if diff >= 0 else "BELOW"
                floor_text = (
                    f"\n<b>Profit floor:</b> ${floor:,.0f} | "
                    f"Equity: ${equity:,.0f} | {label}: ${abs(diff):,.0f}"
                )
            else:
                floor_text = f"\n<b>Profit floor:</b> None | Equity: ${equity:,.0f}"
        except Exception:
            pass

    # Coin directives
    directives_text = ""
    if layer_manager:
        plan_obj = layer_manager.get_plan()
        if plan_obj and hasattr(plan_obj, "coin_directives") and plan_obj.coin_directives:
            dir_lines = []
            dir_markers = {"buy_only": "+", "sell_only": "-", "both": "~", "avoid": "X"}
            for sym, cd in list(plan_obj.coin_directives.items())[:8]:
                m = dir_markers.get(cd.direction, "?")
                dir_lines.append(
                    f"  [{m}] {sym}: {cd.direction} | "
                    f"{cd.leverage}x | SL={cd.sl_pct}% TP={cd.tp_pct}%"
                )
            if dir_lines:
                directives_text = "\n<b>Coin directives:</b>\n" + "\n".join(dir_lines)

    # Avoid coins
    avoid_text = ""
    if layer_manager:
        plan_obj = layer_manager.get_plan()
        if plan_obj and plan_obj.avoid_coins:
            avoid_text = f"\n<b>Avoid:</b> {', '.join(plan_obj.avoid_coins)}"

    text = (
        f"{'=' * 32}\n"
        f"  <b>TRADING INTELLIGENCE CONTROL</b>\n"
        f"{'=' * 32}\n\n"
        f"<b>LAYERS:</b>\n"
        f"  [{'ON' if l1_active else 'OFF'}] Data collection\n"
        f"  [{'ON' if l2_active else 'OFF'}] Claude brain (every {brain_interval // 60}min)\n"
        f"  [{'ON' if l3_active else 'OFF'}] Trading + watchdog\n\n"
        f"<b>MARKET:</b> {str(market_view)[:100]}\n"
        f"<b>Risk:</b> {risk_level}\n"
        f"<b>Plan age:</b> {plan_age}\n\n"
        f"<b>POSITIONS:</b> {pos_count}/{max_pos} open\n"
        f"<b>Unrealized PnL:</b> {total_pnl:+.2f}%\n"
        f"<b>Daily PnL:</b> {daily_pnl}\n"
        f"<b>Today:</b> {trades_today} trades | {wins}W {losses}L | {win_rate}\n\n"
        f"<b>PARAMETERS:</b>\n"
        f"  SL: {sl_pct}% | TP: {tp_pct}%\n"
        f"  Hold: {hold_min}min | Leverage: {leverage}x\n"
        f"  Trailing: +{trailing}%\n"
        f"  Focus: {focus}\n"
        f"{directives_text}"
        f"{avoid_text}"
        f"{floor_text}\n\n"
        f"<b>CLAUDE:</b> {claude_calls} calls today ($0)"
    )
    if claude_failures > 0:
        text += f" | {claude_failures} consecutive failures"
    text += (
        f"\n<b>Watchdog:</b> code rules every {watchdog_interval}s\n"
        f"<b>Brain:</b> Claude every {brain_interval}s"
    )

    return text


def _build_dashboard_keyboard(context) -> InlineKeyboardMarkup:
    """Build the inline keyboard for the dashboard."""
    layer_manager = _svc(context, "layer_manager")

    l1 = layer_manager.is_layer_active(1) if layer_manager else False
    l2 = layer_manager.is_layer_active(2) if layer_manager else False
    l3 = layer_manager.is_layer_active(3) if layer_manager else False

    keyboard = [
        [InlineKeyboardButton(
            f"{'Stop' if l1 else 'Start'} Data",
            callback_data=f"layer_{'stop' if l1 else 'start'}_1",
        )],
        [InlineKeyboardButton(
            f"{'Stop' if l2 else 'Start'} Brain",
            callback_data=f"layer_{'stop' if l2 else 'start'}_2",
        )],
        [InlineKeyboardButton(
            f"{'Stop' if l3 else 'Start'} Trading",
            callback_data=f"layer_{'stop' if l3 else 'start'}_3",
        )],
        [InlineKeyboardButton("EMERGENCY CLOSE ALL", callback_data="emergency_close")],
        [
            InlineKeyboardButton("View plan", callback_data="view_plan"),
            InlineKeyboardButton("Positions", callback_data="view_positions"),
        ],
        [
            InlineKeyboardButton("1min", callback_data="brain_interval_60"),
            InlineKeyboardButton("3min", callback_data="brain_interval_180"),
            InlineKeyboardButton("5min", callback_data="brain_interval_300"),
        ],
        [InlineKeyboardButton("Refresh", callback_data="refresh_dashboard")],
    ]

    return InlineKeyboardMarkup(keyboard)


# ─── Callback Handler ─────────────────────────────────────────────────

async def control_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ALL dashboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        # Refresh dashboard
        if data == "refresh_dashboard":
            text = await _build_dashboard_text(context)
            keyboard = _build_dashboard_keyboard(context)
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=keyboard
            )
            return

        # Layer start
        if data.startswith("layer_start_"):
            layer = int(data[-1])
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                _user = getattr(query.from_user, "id", "unknown") if query else "unknown"
                success, msg = await layer_manager.start_layer(
                    layer,
                    reason="telegram_control_start",
                    actor=f"telegram_user:{_user}",
                )
                result = f"{'Started' if success else 'Failed'}: {msg}"
            else:
                result = "Layer manager not available."
            text = await _build_dashboard_text(context)
            keyboard = _build_dashboard_keyboard(context)
            await query.edit_message_text(
                f"{result}\n\n{text}", parse_mode="HTML", reply_markup=keyboard
            )
            return

        # Layer stop
        if data.startswith("layer_stop_"):
            layer = int(data[-1])
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                cascade = []
                if layer == 1 and (
                    layer_manager.is_layer_active(2) or layer_manager.is_layer_active(3)
                ):
                    cascade = ["Brain", "Trading"]
                elif layer == 2 and layer_manager.is_layer_active(3):
                    cascade = ["Trading"]

                _user = getattr(query.from_user, "id", "unknown") if query else "unknown"
                success, msg = await layer_manager.stop_layer(
                    layer,
                    reason="telegram_control_stop",
                    actor=f"telegram_user:{_user}",
                )
                warning = f"\nAlso stopped: {', '.join(cascade)}" if cascade else ""
                result = f"Stopped: {msg}{warning}"
            else:
                result = "Layer manager not available."
            text = await _build_dashboard_text(context)
            keyboard = _build_dashboard_keyboard(context)
            await query.edit_message_text(
                f"{result}\n\n{text}", parse_mode="HTML", reply_markup=keyboard
            )
            return

        # Emergency close
        if data == "emergency_close":
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                _user = getattr(query.from_user, "id", "unknown") if query else "unknown"
                result = await layer_manager.emergency_close_all(
                    reason="telegram_control_emergency",
                    actor=f"telegram_user:{_user}",
                )
            else:
                position_service = _svc(context, "position_service")
                if position_service:
                    positions = await position_service.get_positions()
                    closed = []
                    for pos in positions:
                        try:
                            await position_service.close_position(pos.symbol)
                            closed.append(pos.symbol)
                        except Exception as e:
                            log.error("Emergency close {sym}: {err}", sym=pos.symbol, err=str(e))
                    result = f"Closed {len(closed)} positions: {', '.join(closed) or 'none'}"
                else:
                    result = "Position service not available"
            await query.edit_message_text(
                f"<b>EMERGENCY CLOSE</b>\n\n{result}\n\nUse /control for dashboard.",
                parse_mode="HTML",
            )
            return

        # View plan
        if data == "view_plan":
            await _show_plan(query, context)
            return

        # View positions
        if data == "view_positions":
            await _show_positions(query, context)
            return

        # Brain interval
        if data.startswith("brain_interval_"):
            seconds = int(data.split("_")[-1])
            layer_manager = _svc(context, "layer_manager")
            if layer_manager:
                layer_manager.brain_interval_seconds = seconds
                result = f"Brain review interval: {seconds}s ({seconds // 60}min)"
            else:
                result = "Layer manager not available"
            text = await _build_dashboard_text(context)
            keyboard = _build_dashboard_keyboard(context)
            await query.edit_message_text(
                f"{result}\n\n{text}", parse_mode="HTML", reply_markup=keyboard
            )
            return

        # Capital tier actions (#4)
        if data.startswith("capital_"):
            await _handle_capital_callback(query, context, data)
            return

        # Mode switch actions (#6)
        if data.startswith("mode_"):
            await _handle_mode_callback(query, context, data)
            return

        await query.edit_message_text(f"Unknown action: {data}")

    except Exception as e:
        log.error("Callback {data} failed: {err}", data=data, err=str(e))
        try:
            await query.edit_message_text(
                f"Action failed: {str(e)[:200]}\n\nUse /control to retry."
            )
        except Exception:
            pass


# ─── Plan helper (used by control_callback view_plan button) ─────────

async def _show_plan(query, context) -> None:
    """Show plan via inline button."""
    layer_manager = _svc(context, "layer_manager")
    if not layer_manager:
        text = "Layer manager not available."
    else:
        plan = layer_manager.get_plan()
        if plan and hasattr(plan, "to_telegram_text"):
            text = plan.to_telegram_text()
        elif plan and plan.market_view:
            text = f"<b>Plan:</b> {plan.market_view[:300]}"
        else:
            text = "No strategic plan yet."

    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("Back to dashboard", callback_data="refresh_dashboard")]
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_btn)


# ─── Positions helper (used by control_callback view_positions button) ─

async def _show_positions(query, context) -> None:
    """Show positions via inline button."""
    position_service = _svc(context, "position_service")
    if not position_service:
        await query.edit_message_text("Position service not available.")
        return

    try:
        positions = await position_service.get_positions()
    except Exception as e:
        await query.edit_message_text(f"Error: {e}")
        return

    if not positions:
        back_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to dashboard", callback_data="refresh_dashboard")]
        ])
        await query.edit_message_text("No open positions.", reply_markup=back_btn)
        return

    text = _build_positions_text(positions, context)
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (truncated)"

    back_btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Back to dashboard", callback_data="refresh_dashboard"),
            InlineKeyboardButton("Refresh", callback_data="view_positions"),
        ]
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_btn)


def _build_positions_text(positions, context) -> str:
    """Build rich positions text with trade coordinator data."""
    coordinator = _svc(context, "trade_coordinator")
    lines = [f"<b>Open positions ({len(positions)})</b>\n"]

    for pos in positions:
        pnl_pct = 0.0
        if pos.entry_price > 0:
            pnl_pct = (pos.mark_price - pos.entry_price) / pos.entry_price * 100
            side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            if side_val in ("Sell", "Short"):
                pnl_pct = -pnl_pct
        else:
            side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)

        emoji = "+" if pnl_pct > 0 else "-" if pnl_pct < 0 else "~"

        plan = None
        trade_info = {}
        if coordinator:
            try:
                plan = coordinator.get_trade_plan(pos.symbol)
                trade_info = coordinator.get_trade_info(pos.symbol)
            except Exception:
                pass

        age = f"{plan.age_minutes:.0f}" if plan else "?"
        max_hold = f"{plan.max_hold_minutes}" if plan else "?"
        remain = f"{plan.remaining_minutes:.0f}" if plan else "?"

        trail_status = "off"
        if plan and plan.trailing_active:
            trail_status = f"ACTIVE at {_fmt_price(context, plan.trailing_stop_price, pos.symbol)}"

        sl = _fmt_price(context, plan.stop_loss_price, pos.symbol) if plan and plan.stop_loss_price else "?"
        tp = _fmt_price(context, plan.target_price, pos.symbol) if plan and plan.target_price else "?"
        strategy = trade_info.get("strategy_name", "?") if isinstance(trade_info, dict) else "?"
        score = trade_info.get("score", "?") if isinstance(trade_info, dict) else "?"
        consensus = trade_info.get("consensus", "?") if isinstance(trade_info, dict) else "?"
        lev = trade_info.get("leverage", pos.leverage) if isinstance(trade_info, dict) else pos.leverage

        lines.append(
            f"[{emoji}] <b>{pos.symbol}</b> {side_val}\n"
            f"  Entry: {_fmt_price(context, pos.entry_price, pos.symbol)} | Now: {_fmt_price(context, pos.mark_price, pos.symbol)}\n"
            f"  PnL: {pnl_pct:+.2f}%\n"
            f"  SL: {sl} | TP: {tp}\n"
            f"  Time: {age}min / {max_hold}min | Left: {remain}min\n"
            f"  Trailing: {trail_status}\n"
            f"  Leverage: {lev}x | Strategy: {strategy}\n"
            f"  Score: {score} | Consensus: {consensus}\n"
        )

    return "\n".join(lines)


# ─── /capital — tiered capital management (#4) ───────────────────────

async def capital_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show capital tier status with override buttons."""
    tiered_capital = _svc(context, "tiered_capital")
    if not tiered_capital:
        await update.message.reply_text("Tiered capital system not available.")
        return

    # Get current equity
    equity = 168000.0
    account_svc = _svc(context, "account_service")
    if account_svc:
        try:
            account = await account_svc.get_wallet_balance()
            equity = account.total_equity
        except Exception:
            pass

    # Get deployed capital
    deployed = 0.0
    position_svc = _svc(context, "position_service")
    if position_svc:
        try:
            positions = await position_svc.get_positions()
            for pos in positions:
                deployed += abs(pos.size * pos.entry_price / max(pos.leverage or 1, 1))
        except Exception:
            pass

    limits = tiered_capital.get_limits(equity, deployed)
    text = limits.to_telegram_text()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10%", callback_data="capital_10"),
            InlineKeyboardButton("20%", callback_data="capital_20"),
            InlineKeyboardButton("30%", callback_data="capital_30"),
        ],
        [
            InlineKeyboardButton("40%", callback_data="capital_40"),
            InlineKeyboardButton("50%", callback_data="capital_50"),
            InlineKeyboardButton("Auto (tiers)", callback_data="capital_auto"),
        ],
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_capital_callback(query, context, data: str) -> None:
    """Handle capital tier override buttons."""
    tiered_capital = _svc(context, "tiered_capital")
    if not tiered_capital:
        await query.edit_message_text("Tiered capital not available.")
        return

    if data == "capital_auto":
        await tiered_capital.set_user_override(None)
        await query.edit_message_text(
            "<b>Capital override CLEARED</b>\n\n"
            "Using automatic tiers (20%/30%/40%).\n"
            "Use /capital to see current status.",
            parse_mode="HTML",
        )
    else:
        pct_str = data.replace("capital_", "")
        pct = int(pct_str) / 100.0
        await tiered_capital.set_user_override(pct)
        await query.edit_message_text(
            f"<b>Capital override set to {int(pct * 100)}%</b>\n\n"
            f"Use /capital to see updated status.",
            parse_mode="HTML",
        )


# ─── /mode — testnet/mainnet toggle (#6) ─────────────────────────────

async def mode_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show current trading mode with toggle button."""
    trading_mode = _svc(context, "trading_mode")
    if not trading_mode:
        await update.message.reply_text("Trading mode not available.")
        return

    mode = trading_mode.mode
    indicator = getattr(mode, "indicator", "?")
    label = getattr(mode, "label", "[UNKNOWN]")
    is_testnet = getattr(mode, "is_testnet", True)

    text = (
        f"<b>Trading Mode</b>\n\n"
        f"<b>Current:</b> {indicator} {label}\n"
        f"<b>SL sanity:</b> +/-{mode.sl_sanity_pct}%\n"
        f"<b>Headspace:</b> {mode.headspace_pct}%\n"
    )

    if is_testnet:
        btn_text = "Switch to MAINNET"
        btn_data = "mode_to_mainnet"
    else:
        btn_text = "Switch to TESTNET"
        btn_data = "mode_to_testnet"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_text, callback_data=btn_data)],
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _handle_mode_callback(query, context, data: str) -> None:
    """Handle mode switch buttons."""
    trading_mode_mgr = _svc(context, "trading_mode")
    if not trading_mode_mgr:
        await query.edit_message_text("Trading mode not available.")
        return

    if data == "mode_to_mainnet":
        # Require confirmation for mainnet
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("YES - Switch to MAINNET", callback_data="mode_confirm_mainnet"),
                InlineKeyboardButton("Cancel", callback_data="refresh_dashboard"),
            ],
        ])
        await query.edit_message_text(
            "<b>Switch to MAINNET?</b>\n\n"
            "This will use REAL money.\n"
            "Claude will double-check all values.\n"
            "SL sanity tightens to +/-5%.\n\n"
            "Are you sure?",
            parse_mode="HTML", reply_markup=keyboard,
        )

    elif data == "mode_to_testnet":
        from src.core.trading_mode import TradingModeType
        await trading_mode_mgr.set_mode(TradingModeType.TESTNET)
        await query.edit_message_text(
            "<b>Switched to TESTNET</b>\n\nUse /mode to see status.",
            parse_mode="HTML",
        )

    elif data == "mode_confirm_mainnet":
        from src.core.trading_mode import TradingModeType
        await trading_mode_mgr.set_mode(TradingModeType.MAINNET)
        await query.edit_message_text(
            "<b>Switched to MAINNET</b>\n\n"
            "Real money mode active. Maximum caution.\n"
            "Use /mode to see status.",
            parse_mode="HTML",
        )


