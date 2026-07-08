"""Shadow HTTP API server — REST interface for the main trading project.

Exposes Shadow's internals (wallet, order engine, positions, prices) via
JSON endpoints on localhost. The main project's service adapters call
these endpoints to place orders, query positions, and check balance.

Endpoints:
    POST /api/order         — Place a new order
    POST /api/close         — Close a position
    POST /api/set-sl        — Set stop loss
    POST /api/set-tp        — Set take profit
    GET  /api/positions     — Get all open positions
    GET  /api/position/{sym} — Get position for specific symbol
    GET  /api/balance       — Get wallet balance
    GET  /api/ticker/{sym}  — Get latest ticker
    GET  /api/health        — System health status
"""

import os
import time
from typing import Any

from aiohttp import web

from src.utils.logging import get_logger

log = get_logger("api")


def create_api_app(
    wallet: Any,
    order_engine: Any,
    position_monitor: Any,
    price_fn: Any,
    db: Any,
    ws_manager: Any = None,
) -> web.Application:
    """Create and configure the aiohttp web application.

    Args:
        wallet: VirtualWallet instance.
        order_engine: OrderEngine instance.
        position_monitor: PositionMonitor instance.
        price_fn: Callable for price lookups.
        db: DatabaseManager instance.
        ws_manager: WebSocketManager instance (for health stats).

    Returns:
        Configured aiohttp Application.
    """
    app = web.Application()

    # Store references for handlers
    app["wallet"] = wallet
    app["engine"] = order_engine
    app["monitor"] = position_monitor
    app["price_fn"] = price_fn
    app["db"] = db
    app["ws_manager"] = ws_manager
    app["start_time"] = time.time()

    # Request logging middleware
    @web.middleware
    async def log_requests(request: web.Request, handler):
        start = time.time()
        try:
            response = await handler(request)
            elapsed = (time.time() - start) * 1000
            log.info("{method} {path} → {status} ({ms:.0f}ms)",
                     method=request.method, path=request.path,
                     status=response.status, ms=elapsed)
            return response
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            log.error("{method} {path} → 500 ({ms:.0f}ms) {err}",
                      method=request.method, path=request.path,
                      ms=elapsed, err=str(e))
            raise

    app.middlewares.append(log_requests)

    # Register routes
    app.router.add_post("/api/order", handle_place_order)
    app.router.add_post("/api/close", handle_close_position)
    app.router.add_post("/api/set-sl", handle_set_sl)
    app.router.add_post("/api/set-tp", handle_set_tp)
    app.router.add_get("/api/positions", handle_get_positions)
    app.router.add_get("/api/position/{symbol}", handle_get_position)
    app.router.add_get("/api/balance", handle_get_balance)
    app.router.add_get("/api/ticker/{symbol}", handle_get_ticker)
    app.router.add_get("/api/health", handle_health)

    return app


# ─── Route handlers ─────────────────────────────────────────────────────


async def handle_place_order(request: web.Request) -> web.Response:
    """POST /api/order — Place a new virtual order."""
    try:
        data = await request.json()
        engine = request.app["engine"]

        result = await engine.place_order(
            symbol=data["symbol"],
            side=data["side"],
            qty=float(data["qty"]),
            leverage=int(data.get("leverage", 1)),
            sl_price=data.get("sl"),
            tp_price=data.get("tp"),
        )

        status = 200 if result.get("status") == "Filled" else 400
        return web.json_response(result, status=status)
    except Exception as e:
        log.error("API /order error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_close_position(request: web.Request) -> web.Response:
    """POST /api/close — Close an open position."""
    try:
        data = await request.json()
        engine = request.app["engine"]

        result = await engine.close_position(
            symbol=data["symbol"],
            close_trigger=data.get("trigger", "manual"),
        )

        status = 200 if result.get("status") != "Rejected" else 400
        return web.json_response(result, status=status)
    except Exception as e:
        log.error("API /close error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_set_sl(request: web.Request) -> web.Response:
    """POST /api/set-sl — Set stop loss on an open position."""
    try:
        data = await request.json()
        engine = request.app["engine"]

        result = await engine.set_stop_loss(
            symbol=data["symbol"],
            new_sl=float(data["sl_price"]),
        )

        status = 200 if result.get("status") != "Rejected" else 400
        return web.json_response(result, status=status)
    except Exception as e:
        log.error("API /set-sl error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_set_tp(request: web.Request) -> web.Response:
    """POST /api/set-tp — Set take profit on an open position."""
    try:
        data = await request.json()
        engine = request.app["engine"]

        result = await engine.set_take_profit(
            symbol=data["symbol"],
            new_tp=float(data["tp_price"]),
        )

        status = 200 if result.get("status") != "Rejected" else 400
        return web.json_response(result, status=status)
    except Exception as e:
        log.error("API /set-tp error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_positions(request: web.Request) -> web.Response:
    """GET /api/positions — Get all open positions with live PnL."""
    try:
        engine = request.app["engine"]
        positions = await engine.get_positions()
        return web.json_response({"positions": positions})
    except Exception as e:
        log.error("API /positions error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_position(request: web.Request) -> web.Response:
    """GET /api/position/{symbol} — Get a single position."""
    try:
        symbol = request.match_info["symbol"]
        engine = request.app["engine"]
        position = await engine.get_position(symbol)

        if position is None:
            return web.json_response(
                {"error": f"No open position for {symbol}"}, status=404
            )
        return web.json_response(position)
    except Exception as e:
        log.error("API /position error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_balance(request: web.Request) -> web.Response:
    """GET /api/balance — Get wallet balance."""
    try:
        wallet = request.app["wallet"]
        balance = await wallet.get_balance()
        return web.json_response(balance)
    except Exception as e:
        log.error("API /balance error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_get_ticker(request: web.Request) -> web.Response:
    """GET /api/ticker/{symbol} — Get latest ticker data."""
    try:
        symbol = request.match_info["symbol"]
        price_fn = request.app["price_fn"]
        price_data = price_fn(symbol)

        if price_data is None:
            return web.json_response(
                {"error": f"No price data for {symbol}"}, status=404
            )

        return web.json_response({
            "symbol": symbol,
            "last_price": price_data.get("last"),
            "bid": price_data.get("bid"),
            "ask": price_data.get("ask"),
            "volume_24h": price_data.get("volume"),
            "funding_rate": price_data.get("funding"),
        })
    except Exception as e:
        log.error("API /ticker error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/health — System health status."""
    try:
        monitor = request.app["monitor"]
        db = request.app["db"]
        ws = request.app["ws_manager"]

        uptime = time.time() - request.app["start_time"]

        # DB size
        db_path = db.db_path
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0

        # Position count
        pos_count = await db.fetch_one(
            "SELECT COUNT(*) as cnt FROM virtual_positions WHERE status='open'"
        )

        # Coin count
        coin_count = await db.fetch_one(
            "SELECT COUNT(*) as cnt FROM tracked_coins WHERE is_active=1"
        )

        # WS health
        ws_health = ws.get_health() if ws else {}

        return web.json_response({
            "status": "running",
            "uptime_seconds": int(uptime),
            "websocket": "connected" if ws_health.get("coins_with_data", 0) > 0 else "disconnected",
            "coins_tracked": coin_count["cnt"] if coin_count else 0,
            "positions_open": pos_count["cnt"] if pos_count else 0,
            "monitor_active": monitor.get_stats()["running"] if monitor else False,
            "monitor_stats": monitor.get_stats() if monitor else {},
            "db_size_mb": round(db_size_mb, 1),
            "ws_messages_total": ws_health.get("total_messages", 0),
        })
    except Exception as e:
        log.error("API /health error: {err}", err=str(e))
        return web.json_response({"error": str(e)}, status=500)
