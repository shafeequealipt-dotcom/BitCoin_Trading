"""Card builders for Telegram messages — position cards, analysis cards, etc."""

from src.telegram.ui.formatters import calc_pnl_pct, format_pnl, format_price, format_timestamp


def position_card(pos) -> str:
    """Build a rich position card."""
    direction = "\U0001f4c8 LONG" if pos.side.value == "Buy" else "\U0001f4c9 SHORT"
    pnl_pct = calc_pnl_pct(pos.entry_price, pos.mark_price, pos.side.value)
    pnl_str = format_pnl(pos.unrealized_pnl, pnl_pct)
    sl = format_price(pos.stop_loss) if pos.stop_loss else "None"
    tp = format_price(pos.take_profit) if pos.take_profit else "None"

    return (
        f"{direction} <b>{pos.symbol}</b>\n"
        f"Entry: {format_price(pos.entry_price)} \u2192 Now: {format_price(pos.mark_price)}\n"
        f"PnL: {pnl_str}\n"
        f"Leverage: {pos.leverage}x | Size: {pos.size}\n"
        f"SL: {sl} | TP: {tp}\n"
        f"Liq: {format_price(pos.liquidation_price)}"
    )


def analysis_card(symbol: str, ticker, ta_data: dict) -> str:
    """Build a comprehensive analysis card."""
    overall = ta_data.get("overall", {})
    trend = ta_data.get("trend", {})
    momentum = ta_data.get("momentum", {})
    vol = ta_data.get("volatility", {})

    signal = overall.get("signal", "N/A")
    signal_emoji = {"STRONG_BUY": "\U0001f7e2\U0001f7e2", "BUY": "\U0001f7e2", "NEUTRAL": "\u26aa", "SELL": "\U0001f534", "STRONG_SELL": "\U0001f534\U0001f534"}.get(signal, "\u26aa")
    conf = overall.get("confidence", 0)
    rsi = momentum.get("rsi_14", 0)
    macd_h = trend.get("macd", {}).get("histogram", 0)
    adx = trend.get("adx", {}).get("adx", 0)
    st_dir = trend.get("supertrend", {}).get("direction", 0)
    bb_bw = vol.get("bollinger", {}).get("bandwidth", 0)
    atr = vol.get("atr_14", 0)

    rsi_note = ""
    if rsi and rsi < 30:
        rsi_note = " (OVERSOLD \U0001f7e2)"
    elif rsi and rsi > 70:
        rsi_note = " (OVERBOUGHT \U0001f534)"

    macd_str = "\U0001f7e2 Bull" if macd_h and macd_h > 0 else "\U0001f534 Bear"
    st_str = "\U0001f7e2 Up" if st_dir == 1 else "\U0001f534 Down"

    msg = (
        f"\U0001f4ca <b>ANALYSIS \u2014 {symbol}</b>\n\n"
        f"\U0001f4b0 Price: <b>{format_price(ticker.last_price)}</b> ({ticker.change_24h_pct:+.2f}%)\n\n"
        f"\U0001f4e1 Signal: {signal_emoji} <b>{signal}</b>\n"
        f"\U0001f3af Confidence: {conf*100:.0f}%\n\n"
        f"<b>\U0001f4c8 Trend</b>\n"
        f"  MACD: {macd_str}\n"
        f"  Supertrend: {st_str}\n"
        f"  ADX: {adx:.1f}\n\n"
        f"<b>\U0001f4aa Momentum</b>\n"
        f"  RSI: {rsi:.1f}{rsi_note}\n\n"
        f"<b>\U0001f4ca Volatility</b>\n"
        f"  BB Width: {bb_bw:.2f}%\n"
        f"  ATR: {format_price(atr)}\n"
    )

    # Key reasons
    reasons = overall.get("key_reasons", [])
    if reasons:
        msg += f"\n<b>\U0001f4a1 Key Factors</b>\n"
        for r in reasons[:5]:
            msg += f"  \u2022 {r}\n"

    # S/R levels
    sr = ta_data.get("support_resistance", {})
    supports = sr.get("support_levels", [])
    resistances = sr.get("resistance_levels", [])
    if supports:
        msg += f"\n\U0001f7e2 Support: {', '.join(format_price(s) for s in supports[:2])}"
    if resistances:
        msg += f"\n\U0001f534 Resistance: {', '.join(format_price(r) for r in resistances[:2])}"

    msg += f"\n\n\U0001f550 {format_timestamp()}"
    return msg


def risk_check_card(symbol: str, side: str, amount: float, leverage: int, checks: list, warnings: list, rsi: str, signal: str, confidence: str, suggested_lev: int) -> str:
    """Build a risk check card."""
    msg = f"\u26a0\ufe0f <b>RISK CHECK</b> \u2014 {side.upper()} {symbol}\n\n"
    msg += f"\U0001f4b0 Amount: ${amount:,.0f}\n"
    msg += f"\u26a1 Leverage: {leverage}x\n"
    msg += f"\U0001f4ca Position value: ${amount * leverage:,.2f}\n\n"

    for check in checks:
        emoji = "\u2705" if check["passed"] else "\u274c"
        msg += f"{emoji} {check['name']}: {check['detail']}\n"

    if warnings:
        msg += "\n"
        for w in warnings:
            msg += f"\u26a0\ufe0f {w}\n"

    if suggested_lev != leverage:
        msg += f"\n\U0001f4a1 Suggested leverage: {suggested_lev}x (you chose {leverage}x)\n"

    msg += f"\n\U0001f4c8 RSI: {rsi} | Signal: {signal}\n"
    msg += f"\U0001f9e0 Confidence: {confidence}%"
    return msg
