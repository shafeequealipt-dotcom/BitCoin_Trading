#!/usr/bin/env python3
"""Force a trade immediately — bypasses Brain. For testnet data generation only.

Usage:
    python scripts/force_trade.py BUY BTCUSDT 100 3
    python scripts/force_trade.py SELL ETHUSDT 50 5
"""

import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    if len(sys.argv) < 4:
        print("Usage: python scripts/force_trade.py BUY BTCUSDT 100 [leverage]")
        sys.exit(1)

    side_str = sys.argv[1].capitalize()
    symbol = sys.argv[2].upper()
    amount = float(sys.argv[3])
    leverage = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    from src.config.settings import Settings
    from src.core.types import OrderType, Side
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.trading.client import BybitClient
    from src.trading.services.account_service import AccountService
    from src.trading.services.market_service import MarketService
    from src.trading.services.order_service import OrderService

    Settings.reset()
    s = Settings._load_fresh()
    db = DatabaseManager(s.database.path)
    await db.connect()
    await run_migrations(db)

    bybit = BybitClient(s, db)
    await bybit.connect()

    market = MarketService(bybit, db)
    order_svc = OrderService(bybit, db, s)

    ticker = await market.get_ticker(symbol)
    side = Side.BUY if side_str == "Buy" else Side.SELL
    qty = (amount * leverage) / ticker.last_price

    if side == Side.BUY:
        sl = ticker.last_price * 0.97
        tp = ticker.last_price * 1.05
    else:
        sl = ticker.last_price * 1.03
        tp = ticker.last_price * 0.95

    print(f"Placing: {side_str} {symbol} ${amount} at {leverage}x")
    print(f"  Price: ${ticker.last_price:,.2f}")
    print(f"  Qty: {qty:.6f}")
    print(f"  SL: ${sl:,.2f} | TP: ${tp:,.2f}")

    order = await order_svc.place_order(
        symbol=symbol, side=side, order_type=OrderType.MARKET,
        qty=qty, leverage=leverage, stop_loss=sl, take_profit=tp,
    )

    print(f"  Order ID: {order.order_id}")
    print("  DONE")

    await db.disconnect()
    Settings.reset()


if __name__ == "__main__":
    asyncio.run(main())
