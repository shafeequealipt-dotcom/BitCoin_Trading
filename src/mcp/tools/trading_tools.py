"""Trading tools: account, prices, orders, positions (12 tools)."""

import json
from typing import Any, Callable

from mcp.types import Tool, TextContent

from src.core.logging import get_logger
from src.core.types import AlertLevel, OrderType, Side, TimeFrame
from src.core.utils import format_price

log = get_logger("mcp")


def register_trading_tools(services: dict, alert_manager=None) -> tuple[list[Tool], dict[str, Callable]]:
    """Register all 12 trading tools."""
    account = services.get("account")
    market = services.get("market")
    order = services.get("order")
    position = services.get("position")

    tools: list[Tool] = []
    handlers: dict[str, Callable] = {}

    # 1. get_account_info
    tools.append(Tool(
        name="get_account_info",
        description="Get your Bybit account balance, equity, available margin, and unrealized PnL. Use this to check how much money you have before placing trades.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ))

    async def _get_account_info(args: dict) -> list[TextContent]:
        try:
            if not account:
                return [TextContent(type="text", text="Account service not available")]
            info = await account.get_wallet_balance()
            text = (
                f"Account Summary:\n"
                f"  Total Equity:     ${info.total_equity:,.2f}\n"
                f"  Available Balance: ${info.available_balance:,.2f}\n"
                f"  Used Margin:      ${info.used_margin:,.2f}\n"
                f"  Unrealized PnL:   ${info.unrealized_pnl:+,.2f}\n"
                f"  Margin Level:     {info.margin_level_pct:.1f}%"
            )
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting account info: {e}")]

    handlers["get_account_info"] = _get_account_info

    # 2. get_ticker
    tools.append(Tool(
        name="get_ticker",
        description="Get the current price, bid/ask, 24h high/low, volume, and price change for a cryptocurrency.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string", "description": "Trading pair, e.g. 'BTCUSDT'"}}, "required": ["symbol"]},
    ))

    async def _get_ticker(args: dict) -> list[TextContent]:
        try:
            if not market:
                return [TextContent(type="text", text="Market service not available")]
            t = await market.get_ticker(args["symbol"])
            text = (
                f"{t.symbol} Ticker:\n"
                f"  Price:    ${format_price(t.last_price)}\n"
                f"  Bid/Ask:  ${format_price(t.bid)} / ${format_price(t.ask)}\n"
                f"  24h High: ${t.high_24h:,.2f}\n"
                f"  24h Low:  ${t.low_24h:,.2f}\n"
                f"  24h Vol:  {t.volume_24h:,.2f}\n"
                f"  24h Chg:  {t.change_24h_pct:+.2f}%"
            )
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_ticker"] = _get_ticker

    # 3. get_tickers
    tools.append(Tool(
        name="get_tickers",
        description="Get current prices for multiple cryptocurrencies at once for a market overview.",
        inputSchema={"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}, "description": "List of symbols (optional, defaults to all)"}}, "required": []},
    ))

    async def _get_tickers(args: dict) -> list[TextContent]:
        try:
            if not market:
                return [TextContent(type="text", text="Market service not available")]
            symbols = args.get("symbols")
            tickers = await market.get_tickers(symbols)
            lines = ["Symbol       Price          24h Change"]
            lines.append("-" * 45)
            for t in tickers:
                lines.append(f"{t.symbol:<12} ${t.last_price:>12,.2f}  {t.change_24h_pct:>+7.2f}%")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_tickers"] = _get_tickers

    # 4. get_klines
    tools.append(Tool(
        name="get_klines",
        description="Get historical price candles (OHLCV) for a cryptocurrency. Returns open, high, low, close, volume for each candle.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "15"},
            "limit": {"type": "integer", "default": 50}
        }, "required": ["symbol"]},
    ))

    async def _get_klines(args: dict) -> list[TextContent]:
        try:
            if not market:
                return [TextContent(type="text", text="Market service not available")]
            tf = TimeFrame(args.get("timeframe", "15"))
            klines = await market.get_klines(args["symbol"], tf, args.get("limit", 50))
            if not klines:
                return [TextContent(type="text", text="No kline data available")]
            last5 = klines[-5:]
            lines = [f"{args['symbol']} {tf.value} Candles (last {len(last5)} of {len(klines)}):"]
            for k in last5:
                lines.append(f"  {k.timestamp.strftime('%m-%d %H:%M')} O:{k.open:.2f} H:{k.high:.2f} L:{k.low:.2f} C:{k.close:.2f} V:{k.volume:.1f}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_klines"] = _get_klines

    # 5. get_orderbook
    tools.append(Tool(
        name="get_orderbook",
        description="Get the current order book showing buy and sell orders and market depth.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "depth": {"type": "integer", "default": 10}}, "required": ["symbol"]},
    ))

    async def _get_orderbook(args: dict) -> list[TextContent]:
        try:
            if not market:
                return [TextContent(type="text", text="Market service not available")]
            ob = await market.get_orderbook(args["symbol"], args.get("depth", 10))
            lines = [f"{args['symbol']} Order Book:"]
            lines.append("  Asks (Sell):")
            for a in ob.get("asks", [])[:5]:
                lines.append(f"    ${a[0]:>12,.2f}  qty: {a[1]}")
            lines.append("  Bids (Buy):")
            for b in ob.get("bids", [])[:5]:
                lines.append(f"    ${b[0]:>12,.2f}  qty: {b[1]}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_orderbook"] = _get_orderbook

    # 6. place_order
    tools.append(Tool(
        name="place_order",
        description="Place a new order to buy or sell a cryptocurrency. ALWAYS include a stop_loss for risk management. Executes on testnet (paper trading) unless live mode is enabled.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["Buy", "Sell"]},
            "order_type": {"type": "string", "enum": ["Market", "Limit"]},
            "qty": {"type": "number"}, "price": {"type": "number"},
            "stop_loss": {"type": "number"}, "take_profit": {"type": "number"},
            "leverage": {"type": "integer", "default": 1}
        }, "required": ["symbol", "side", "order_type", "qty"]},
    ))

    async def _place_order(args: dict) -> list[TextContent]:
        try:
            if not order:
                return [TextContent(type="text", text="Order service not available")]
            result = await order.place_order(
                symbol=args["symbol"], side=Side(args["side"]),
                order_type=OrderType(args["order_type"]), qty=args["qty"],
                price=args.get("price"), stop_loss=args.get("stop_loss"),
                take_profit=args.get("take_profit"), leverage=args.get("leverage"),
                purpose="mcp_tool",
            )
            text = (
                f"Order Placed Successfully:\n"
                f"  Order ID:  {result.order_id}\n"
                f"  Symbol:    {result.symbol}\n"
                f"  Side:      {result.side.value}\n"
                f"  Type:      {result.order_type.value}\n"
                f"  Qty:       {result.qty}\n"
                f"  Price:     {result.price or 'Market'}\n"
                f"  Stop Loss: {result.stop_loss or 'None'}\n"
                f"  Status:    {result.status.value}"
            )
            if alert_manager:
                try:
                    await alert_manager.send_trade_alert(result)
                except Exception as e:
                    log.error("Alert send failed after order placement: {err}", err=str(e))
            return [TextContent(type="text", text=text)]
        except Exception as e:
            log.error("Order placement failed: {err}", err=str(e))
            return [TextContent(type="text", text=f"Order failed: {e}")]

    handlers["place_order"] = _place_order

    # 7. modify_order
    tools.append(Tool(
        name="modify_order",
        description="Modify an existing open order's price or quantity.",
        inputSchema={"type": "object", "properties": {
            "symbol": {"type": "string"}, "order_id": {"type": "string"},
            "price": {"type": "number"}, "qty": {"type": "number"}
        }, "required": ["symbol", "order_id"]},
    ))

    async def _modify_order(args: dict) -> list[TextContent]:
        try:
            if not order:
                return [TextContent(type="text", text="Order service not available")]
            result = await order.modify_order(args["symbol"], args["order_id"], args.get("qty"), args.get("price"))
            return [TextContent(type="text", text=f"Order {result.order_id} modified successfully")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error modifying order: {e}")]

    handlers["modify_order"] = _modify_order

    # 8. cancel_order
    tools.append(Tool(
        name="cancel_order",
        description="Cancel a specific open order by its order ID.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}, "order_id": {"type": "string"}}, "required": ["symbol", "order_id"]},
    ))

    async def _cancel_order(args: dict) -> list[TextContent]:
        try:
            if not order:
                return [TextContent(type="text", text="Order service not available")]
            await order.cancel_order(args["symbol"], args["order_id"])
            return [TextContent(type="text", text=f"Order {args['order_id']} cancelled")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["cancel_order"] = _cancel_order

    # 9. cancel_all_orders
    tools.append(Tool(
        name="cancel_all_orders",
        description="EMERGENCY: Cancel ALL open orders immediately. Can filter by symbol.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []},
    ))

    async def _cancel_all(args: dict) -> list[TextContent]:
        try:
            if not order:
                return [TextContent(type="text", text="Order service not available")]
            count = await order.cancel_all_orders(args.get("symbol"))
            log.warning("All orders cancelled: {n} orders", n=count)
            if alert_manager:
                try:
                    await alert_manager.send_error_alert(
                        "trading", "All orders cancelled (emergency)", AlertLevel.WARNING,
                    )
                except Exception as e:
                    log.error("Alert send failed after cancel-all: {err}", err=str(e))
            return [TextContent(type="text", text=f"Cancelled {count} orders")]
        except Exception as e:
            log.error("Cancel all orders failed: {err}", err=str(e))
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["cancel_all_orders"] = _cancel_all

    # 10. get_open_orders
    tools.append(Tool(
        name="get_open_orders",
        description="List all currently open (pending) orders.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []},
    ))

    async def _get_open_orders(args: dict) -> list[TextContent]:
        try:
            if not order:
                return [TextContent(type="text", text="Order service not available")]
            orders = await order.get_open_orders(args.get("symbol"))
            if not orders:
                return [TextContent(type="text", text="No open orders")]
            lines = [f"Open Orders ({len(orders)}):"]
            for o in orders:
                lines.append(f"  {o.order_id[:12]}  {o.symbol} {o.side.value} {o.order_type.value} qty={o.qty} price={o.price}")
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_open_orders"] = _get_open_orders

    # 11. get_positions
    tools.append(Tool(
        name="get_positions",
        description="List all open trading positions with entry price, PnL, leverage, and liquidation price.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": []},
    ))

    async def _get_positions(args: dict) -> list[TextContent]:
        try:
            if not position:
                return [TextContent(type="text", text="Position service not available")]
            positions = await position.get_positions(args.get("symbol"))
            if not positions:
                return [TextContent(type="text", text="No open positions")]
            lines = ["Open Positions:"]
            for p in positions:
                lines.append(
                    f"  {p.symbol} {p.side.value} size={p.size} entry=${format_price(p.entry_price)} "
                    f"mark=${format_price(p.mark_price)} PnL=${p.unrealized_pnl:+,.2f} lev={p.leverage}x"
                )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    handlers["get_positions"] = _get_positions

    # 12. close_position
    tools.append(Tool(
        name="close_position",
        description="Close an open trading position by placing an opposite market order.",
        inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
    ))

    async def _close_position(args: dict) -> list[TextContent]:
        try:
            if not position:
                return [TextContent(type="text", text="Position service not available")]
            # Capture position data before closing for alert
            pos = await position.get_position(args["symbol"])
            result = await position.close_position(args["symbol"])
            if alert_manager and pos:
                try:
                    from src.core.utils import pct_change
                    exit_price = pos.mark_price
                    pnl = pos.unrealized_pnl
                    pnl_pct = pct_change(pos.entry_price, exit_price)
                    if pos.side == Side.SELL:
                        pnl_pct = -pnl_pct
                    await alert_manager.send_position_closed_alert(
                        pos.symbol, pos.side, pos.entry_price, exit_price, pnl, pnl_pct,
                    )
                except Exception as e:
                    log.error("Alert send failed after position close: {err}", err=str(e))
            return [TextContent(type="text", text=f"Position {args['symbol']} closed. Order ID: {result.order_id}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error closing position: {e}")]

    handlers["close_position"] = _close_position

    return tools, handlers
