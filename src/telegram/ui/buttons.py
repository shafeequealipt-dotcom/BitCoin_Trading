"""Inline keyboard button builders for Telegram."""


def position_buttons(symbol: str) -> list[list[dict]]:
    """Buttons for managing a position."""
    return [
        [
            {"text": "Close", "callback_data": f"close_pos:{symbol}"},
            {"text": "Close 50%", "callback_data": f"close_half:{symbol}"},
        ],
        [
            {"text": "Move SL", "callback_data": f"move_sl:{symbol}"},
            {"text": "Move TP", "callback_data": f"move_tp:{symbol}"},
        ],
        [
            {"text": "Analyze", "callback_data": f"analyze:{symbol}"},
        ],
    ]


def analysis_buttons(symbol: str) -> list[list[dict]]:
    """Buttons after analysis."""
    return [
        [
            {"text": "1H Chart", "callback_data": f"chart_1h:{symbol}"},
            {"text": "4H Chart", "callback_data": f"chart_4h:{symbol}"},
            {"text": "Daily", "callback_data": f"chart_daily:{symbol}"},
        ],
        [
            {"text": f"Buy {symbol.replace('USDT','')}", "callback_data": f"buy_now:{symbol}"},
            {"text": "Set Alert", "callback_data": f"set_alert:{symbol}"},
        ],
    ]


def portfolio_buttons() -> list[list[dict]]:
    """Buttons on portfolio summary."""
    return [
        [
            {"text": "Opportunities", "callback_data": "show_setups"},
            {"text": "Full Report", "callback_data": "full_report"},
        ],
        [
            {"text": "Leaderboard", "callback_data": "leaderboard"},
            {"text": "News", "callback_data": "news"},
        ],
    ]


def trade_approval_buttons(symbol: str, side: str, amount: float, leverage: int, suggested_lev: int | None = None) -> list[list[dict]]:
    """Buttons for trade risk check approval."""
    row = [{"text": f"Execute {leverage}x", "callback_data": f"risk_accept:{symbol}:{side}:{amount}:{leverage}"}]
    if suggested_lev and suggested_lev != leverage:
        row.append({"text": f"Safer {suggested_lev}x", "callback_data": f"risk_accept:{symbol}:{side}:{amount}:{suggested_lev}"})
    row.append({"text": "Cancel", "callback_data": "risk_cancel"})
    return [row]


def emergency_buttons() -> list[list[dict]]:
    """Double-confirmation emergency buttons."""
    return [
        [
            {"text": "CONFIRM CLOSE ALL", "callback_data": "confirm_emergency"},
            {"text": "Cancel", "callback_data": "cancel_emergency"},
        ],
    ]
