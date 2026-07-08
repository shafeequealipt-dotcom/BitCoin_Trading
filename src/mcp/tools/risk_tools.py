"""Risk management tools: position sizing, exposure, stop-loss, PnL (5 tools)."""

from typing import Callable
from mcp.types import Tool, TextContent
from src.core.logging import get_logger
from src.config.settings import Settings
from src.core.utils import format_price, safe_divide

log = get_logger("mcp")


def register_risk_tools(services: dict, settings: Settings) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 5 risk tools."""
    account = services.get("account")
    position = services.get("position")
    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 32. calculate_position_size
    tools.append(Tool(name="calculate_position_size",
        description="Calculate optimal position size based on account balance, risk tolerance, and stop-loss distance.",
        inputSchema={"type": "object", "properties": {
            "entry_price": {"type": "number"}, "stop_loss_price": {"type": "number"},
            "risk_pct": {"type": "number", "default": 2.0}
        }, "required": ["entry_price", "stop_loss_price"]}))

    async def _pos_size(args):
        try:
            if not account: return [TextContent(type="text", text="Account service not available")]
            balance = await account.get_available_balance()
            risk_pct = args.get("risk_pct", settings.risk.default_stop_loss_pct) / 100
            risk_amount = balance * risk_pct
            entry = args["entry_price"]
            sl = args["stop_loss_price"]
            sl_distance = abs(entry - sl)
            if sl_distance == 0: return [TextContent(type="text", text="Stop loss cannot equal entry price")]
            qty = risk_amount / sl_distance
            text = (f"Position Size Calculation:\n"
                    f"  Balance:      ${balance:,.2f}\n"
                    f"  Risk ({args.get('risk_pct', 2.0)}%): ${risk_amount:,.2f}\n"
                    f"  Entry:        ${format_price(entry)}\n"
                    f"  Stop Loss:    ${format_price(sl)}\n"
                    f"  SL Distance:  ${sl_distance:,.2f}\n"
                    f"  Position Size: {qty:.6f}\n"
                    f"  Position Value: ${qty * entry:,.2f}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["calculate_position_size"] = _pos_size

    # 33. get_risk_exposure
    tools.append(Tool(name="get_risk_exposure",
        description="Get current portfolio risk breakdown: total exposure, per-position risk, and limit usage.",
        inputSchema={"type": "object", "properties": {}, "required": []}))

    async def _exposure(args):
        try:
            if not position or not account:
                return [TextContent(type="text", text="Services not available")]
            positions = await position.get_positions()
            info = await account.get_wallet_balance()
            total_exposure = sum(abs(p.size * p.entry_price) for p in positions)
            exposure_pct = safe_divide(total_exposure, info.total_equity, 0) * 100
            lines = [f"Risk Exposure:",
                     f"  Total Equity:   ${info.total_equity:,.2f}",
                     f"  Total Exposure: ${total_exposure:,.2f} ({exposure_pct:.1f}%)",
                     f"  Max Allowed:    {settings.risk.max_total_exposure_pct}%",
                     f"  Positions:      {len(positions)} / {settings.risk.max_open_positions}",
                     f"  Unrealized PnL: ${info.unrealized_pnl:+,.2f}"]
            for p in positions:
                lines.append(f"    {p.symbol} {p.side.value} size={p.size} PnL=${p.unrealized_pnl:+,.2f}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_risk_exposure"] = _exposure

    # 34. calculate_stop_loss
    tools.append(Tool(name="calculate_stop_loss",
        description="Calculate recommended stop-loss and take-profit levels using ATR-based and percentage-based methods.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["Buy", "Sell"]},
            "entry_price": {"type": "number"}
        }, "required": ["symbol", "side", "entry_price"]}))

    async def _calc_sl(args):
        try:
            entry = args["entry_price"]
            side = args["side"]
            sl_pct = settings.risk.default_stop_loss_pct / 100
            tp_pct = settings.risk.default_take_profit_pct / 100
            if side == "Buy":
                sl_fixed = entry * (1 - sl_pct)
                tp_fixed = entry * (1 + tp_pct)
            else:
                sl_fixed = entry * (1 + sl_pct)
                tp_fixed = entry * (1 - tp_pct)
            text = (f"Stop-Loss / Take-Profit for {args['symbol']} ({side}):\n"
                    f"  Entry: ${format_price(entry)}\n"
                    f"  Fixed % SL ({settings.risk.default_stop_loss_pct}%): ${format_price(sl_fixed)}\n"
                    f"  Fixed % TP ({settings.risk.default_take_profit_pct}%): ${format_price(tp_fixed)}\n"
                    f"  Risk/Reward: 1:{tp_pct/sl_pct:.1f}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["calculate_stop_loss"] = _calc_sl

    # 35. get_daily_pnl
    tools.append(Tool(name="get_daily_pnl",
        description="Get today's profit and loss: realized from closed trades, unrealized from open positions.",
        inputSchema={"type": "object", "properties": {}, "required": []}))

    async def _daily_pnl(args):
        try:
            if not position or not account:
                return [TextContent(type="text", text="Services not available")]
            info = await account.get_wallet_balance()
            pnl_data = await position.get_pnl_summary()
            text = (f"Daily PnL Summary:\n"
                    f"  Unrealized PnL: ${pnl_data.get('total_unrealized_pnl', 0):+,.2f}\n"
                    f"  Realized PnL:   ${pnl_data.get('total_realized_pnl', 0):+,.2f}\n"
                    f"  Open Positions: {pnl_data.get('position_count', 0)}\n"
                    f"  Total Equity:   ${info.total_equity:,.2f}")
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_daily_pnl"] = _daily_pnl

    # 36. get_risk_status
    tools.append(Tool(name="get_risk_status",
        description="Check if you're within all risk management limits with green/yellow/red indicators.",
        inputSchema={"type": "object", "properties": {}, "required": []}))

    async def _risk_status(args):
        try:
            if not position or not account:
                return [TextContent(type="text", text="Services not available")]
            positions = await position.get_positions()
            info = await account.get_wallet_balance()
            total_exp = sum(abs(p.size * p.entry_price) for p in positions)
            exp_pct = safe_divide(total_exp, info.total_equity, 0) * 100
            pos_ok = len(positions) < settings.risk.max_open_positions
            exp_ok = exp_pct < settings.risk.max_total_exposure_pct
            lines = ["Risk Status:"]
            lines.append(f"  Positions: {len(positions)}/{settings.risk.max_open_positions} {'OK' if pos_ok else 'LIMIT REACHED'}")
            lines.append(f"  Exposure:  {exp_pct:.1f}%/{settings.risk.max_total_exposure_pct}% {'OK' if exp_ok else 'OVER LIMIT'}")
            lines.append(f"  Max Leverage: {settings.risk.max_leverage}x")
            lines.append(f"  Mandatory SL: {'Yes' if settings.risk.mandatory_stop_loss else 'No'}")
            overall = "OK" if pos_ok and exp_ok else "WARNING"
            lines.insert(1, f"  Overall: {overall}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]
    handlers["get_risk_status"] = _risk_status

    return tools, handlers
