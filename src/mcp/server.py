"""MCP Server: dual-transport server exposing all system functionality as tools.

Supports stdio (Claude Code) and SSE (claude.ai) transports.
CRITICAL: Zero stdout/stderr output — MCP stdio protocol uses these channels.
"""

import json
from typing import Any, Callable

from mcp.server import Server
from mcp.types import Tool, TextContent

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations

log = get_logger("mcp")


class MCPServer:
    """MCP Server that exposes trading intelligence tools.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.app = Server(settings.mcp.server_name)
        self.db: DatabaseManager | None = None
        self._all_tools: list[Tool] = []
        self._all_handlers: dict[str, Callable] = {}
        # Service references (set during initialize)
        self._services: dict[str, Any] = {}

    async def initialize(self) -> None:
        """Initialize database, services, and register all tools.

        Phase 23 (Y-22): emits ``MCP_INIT`` so the operator can
        correlate the 43-tool registration cost with how often this
        path runs. Pre-Phase23 the only signal that initialize had
        run was a quiet "MCP Server initialized" line indistinguishable
        from a long-lived startup. Now every call logs the count and
        timing — restart storms (300+/day from one-shot stdio) become
        immediately visible in the log aggregate.
        """
        import time as _t
        _init_t0 = _t.time()
        log.info("Initializing MCP Server...")

        # Database
        self.db = DatabaseManager(
            self.settings.database.path,
            lock_wait_warn_ms=self.settings.database.db_lock_wait_threshold_ms,
            concurrency_model=self.settings.database.concurrency_model,
            reader_pool_size=self.settings.database.reader_pool_size,
        )
        await self.db.connect()
        await run_migrations(self.db)

        # Create services with graceful fallback
        await self._init_services()

        # Register tools
        self._register_tools()

        # Wire up MCP handlers
        all_tools = self._all_tools
        all_handlers = self._all_handlers

        @self.app.list_tools()
        async def list_tools() -> list[Tool]:
            return all_tools

        @self.app.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            handler = all_handlers.get(name)
            if handler:
                return await handler(arguments)
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        # Send startup alert
        alert_manager = self._services.get("alert_manager")
        if alert_manager:
            await alert_manager.send_system_startup(
                mode="testnet" if getattr(self._services.get("bybit"), "is_testnet", True) else "mainnet",
                symbols=["BTCUSDT", "ETHUSDT"],
                workers=len(self._all_tools),
            )

        _init_ms = (_t.time() - _init_t0) * 1000
        log.info(
            f"MCP_INIT | tools={len(self._all_tools)} init_ms={_init_ms:.0f} "
            f"transport={self.settings.mcp.transport}"
        )
        log.info("MCP Server initialized with {n} tools", n=len(self._all_tools))

    async def _init_services(self) -> None:
        """Create all service dependencies."""
        db = self.db
        s = self.settings

        # Trading services
        try:
            from src.trading.client import BybitClient
            from src.trading.services.account_service import AccountService
            from src.trading.services.market_service import MarketService
            from src.trading.services.order_service import OrderService
            from src.trading.services.position_service import PositionService
            from src.trading.services.instrument_service import InstrumentService

            bybit = BybitClient(s, db)
            await bybit.connect()
            self._services["account"] = AccountService(bybit, db)
            self._services["market"] = MarketService(
                bybit, db, kline_save_chunk_size=s.database.kline_save_chunk_size
            )
            self._services["order"] = OrderService(bybit, db, s)
            self._services["position"] = PositionService(bybit, db, s)
            self._services["instrument"] = InstrumentService(bybit)
            self._services["bybit"] = bybit
        except Exception as e:
            log.warning("Trading services unavailable: {err}", err=str(e))

        # P9 of P1-P10: state-snapshot Transformer adapter so the MCP
        # exchange-tools (get_current_exchange / validate_switch) and
        # any downstream tool that reads services["transformer"] sees
        # the worker process's actual mode (read from the
        # transformer_state SQLite table, WAL-mode safe). Pre-P9, MCP's
        # services["transformer"] was never populated; tools returned
        # "Transformer not available" 100% of calls and trading paths
        # silently routed to live Bybit regardless of actual mode.
        try:
            services_per_mode: dict[str, dict[str, Any]] = {
                "bybit": {
                    "account": self._services.get("account"),
                    "position": self._services.get("position"),
                    "order": self._services.get("order"),
                },
            }
            # Construct bybit_demo services if configured (audit's focus).
            bd_settings = getattr(s, "bybit_demo", None)
            if bd_settings is not None and bd_settings.enabled:
                try:
                    import aiohttp
                    from src.bybit_demo import (
                        BybitDemoAccountService,
                        BybitDemoClient,
                        BybitDemoPositionService,
                    )
                    bd_session = aiohttp.ClientSession()
                    bd_client = BybitDemoClient(
                        session=bd_session,
                        base_url=bd_settings.base_url,
                        api_key=bd_settings.api_key,
                        api_secret=bd_settings.api_secret,
                        recv_window=bd_settings.recv_window,
                        timeout_seconds=bd_settings.timeout_seconds,
                        retry_attempts=bd_settings.retry_attempts,
                        retry_base_delay_seconds=bd_settings.retry_base_delay_seconds,
                    )
                    services_per_mode["bybit_demo"] = {
                        "account": BybitDemoAccountService(bd_client),
                        "position": BybitDemoPositionService(bd_client),
                        # No "order" — MCP-side place/close go through
                        # ExchangeSwitcher's restart-based flow, not
                        # direct order_service routing.
                    }
                except Exception as e:
                    log.warning(
                        "bybit_demo services not constructed for MCP: {err}",
                        err=str(e),
                    )
            # Construct Shadow services for read paths (best-effort).
            try:
                import aiohttp
                from src.shadow import (
                    ShadowAccountService,
                    ShadowPositionService,
                )
                if not hasattr(self, "_shadow_session"):
                    self._shadow_session = aiohttp.ClientSession()
                shadow_url = getattr(s.general, "shadow_api_url", "http://127.0.0.1:9090")
                services_per_mode["shadow"] = {
                    "account": ShadowAccountService(self._shadow_session, shadow_url),
                    "position": ShadowPositionService(self._shadow_session, shadow_url),
                }
            except Exception as e:
                log.warning("shadow services not constructed for MCP: {err}", err=str(e))

            from src.core.transformer_state_reader import MCPTransformerAdapter
            self._services["transformer"] = MCPTransformerAdapter(
                db=db,
                services_per_mode=services_per_mode,
            )
            log.info(
                "MCP transformer adapter wired (modes: {modes})",
                modes=",".join(sorted(services_per_mode.keys())),
            )
        except Exception as e:
            log.warning("MCP Transformer adapter wiring failed: {err}", err=str(e))

        # Intelligence services
        try:
            from src.intelligence.sentiment.scorer import SentimentScorer
            from src.intelligence.news.finnhub_client import FinnhubClient
            from src.intelligence.news.news_service import NewsService
            from src.intelligence.news.calendar_service import CalendarService
            from src.intelligence.sentiment.reddit_client import RedditClient
            from src.intelligence.sentiment.reddit_service import RedditService
            from src.intelligence.sentiment.aggregator import SentimentAggregator
            from src.intelligence.altdata.fear_greed import FearGreedClient
            from src.intelligence.altdata.funding_rates import FundingRateTracker
            from src.intelligence.altdata.open_interest import OpenInterestTracker
            from src.intelligence.altdata.onchain import OnChainClient
            from src.intelligence.signals.signal_generator import SignalGenerator

            scorer = SentimentScorer()
            finnhub = FinnhubClient(s)
            self._services["news"] = NewsService(finnhub, scorer, db, s)
            self._services["calendar"] = CalendarService(finnhub, db)
            reddit = RedditClient(s)
            self._services["reddit"] = RedditService(reddit, scorer, db, s)
            # CALL_B Framing Fix Phase 5B (2026-05-06) — pass settings so
            # the aggregator reads `[sentiment].consumption_enabled` and
            # suppresses per-coin SENT_DEGRADED_MODE log spam uniformly
            # in the MCP path (matching workers/manager.py:164).
            self._services["aggregator"] = SentimentAggregator(db, scorer, s)
            self._services["fear_greed"] = FearGreedClient(s, db)
            bybit_for_funding = self._services.get("bybit")
            self._services["funding"] = FundingRateTracker(bybit_for_funding, db) if bybit_for_funding else None
            self._services["oi"] = OpenInterestTracker(bybit_for_funding, db) if bybit_for_funding else None
            self._services["onchain"] = OnChainClient(s, db)
            # Phase 1 (output-quality): pass settings so the multi-source
            # classifier reads thresholds from [signal_generator.multi_source]
            # in config.toml. Falls back to dataclass defaults if missing.
            self._services["signal_gen"] = SignalGenerator(
                self._services["aggregator"], db, settings=s,
            )
        except Exception as e:
            log.warning("Intelligence services unavailable: {err}", err=str(e))

        # TA Engine
        try:
            from src.analysis.engine import TAEngine
            self._services["ta"] = TAEngine(db, settings=self.settings)
        except Exception as e:
            log.warning("TA Engine unavailable: {err}", err=str(e))

        # Alert Manager
        try:
            from src.alerts import AlertManager
            alert_manager = AlertManager(s, db)
            await alert_manager.initialize()
            self._services["alert_manager"] = alert_manager
        except Exception as e:
            log.warning("Alert Manager unavailable: {err}", err=str(e))

    def _register_tools(self) -> None:
        """Register all tool modules."""
        from src.mcp.tools.trading_tools import register_trading_tools
        from src.mcp.tools.news_tools import register_news_tools
        from src.mcp.tools.sentiment_tools import register_sentiment_tools
        from src.mcp.tools.altdata_tools import register_altdata_tools
        from src.mcp.tools.analysis_tools import register_analysis_tools
        from src.mcp.tools.risk_tools import register_risk_tools
        from src.mcp.tools.memory_tools import register_memory_tools
        from src.mcp.tools.system_tools import register_system_tools
        from src.mcp.tools.exchange_tools import register_exchange_tools

        alert_manager = self._services.get("alert_manager")

        modules = [
            (register_trading_tools, [self._services, alert_manager]),
            (register_news_tools, [self._services]),
            (register_sentiment_tools, [self._services]),
            (register_altdata_tools, [self._services]),
            (register_analysis_tools, [self._services, self.db]),
            (register_risk_tools, [self._services, self.settings]),
            (register_memory_tools, [self._services, self.db]),
            (register_system_tools, [self._services, self.db, alert_manager]),
            # Exchange switching tools (Phase 4.C of bybit_demo_adapter
            # project). Adds get_current_exchange, validate_switch, and
            # switch_exchange_with_restart. Additive — does not modify
            # any existing tool's signature or behaviour.
            (register_exchange_tools, [self._services, alert_manager]),
        ]

        for register_fn, args in modules:
            try:
                tools, handlers = register_fn(*args)
                self._all_tools.extend(tools)
                self._all_handlers.update(handlers)
            except Exception as e:
                log.warning("Failed to register tools: {err}", err=str(e))

    async def run_stdio(self) -> None:
        """Run with stdio transport for Claude Code."""
        from mcp.server.stdio import stdio_server
        async with stdio_server() as (read_stream, write_stream):
            await self.app.run(
                read_stream, write_stream,
                self.app.create_initialization_options(),
            )

    async def run_sse(self, host: str, port: int) -> None:
        """Run with SSE transport for claude.ai."""
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        import uvicorn

        from src.mcp.auth import MCPAuth

        sse = SseServerTransport("/messages/")
        auth = MCPAuth(self.settings.mcp.auth_token)
        app_ref = self.app

        async def handle_sse(request):
            token = auth.extract_token(request)
            if not auth.validate_token(token or ""):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await app_ref.run(
                    streams[0], streams[1],
                    app_ref.create_initialization_options(),
                )

        async def handle_messages(request):
            await sse.handle_post_message(request.scope, request.receive, request._send)

        async def health_endpoint(request):
            return JSONResponse({
                "status": "ok",
                "server": self.settings.mcp.server_name,
                "tools": len(self._all_tools),
            })

        starlette_app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
            Route("/health", endpoint=health_endpoint),
        ])

        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()

    async def shutdown(self) -> None:
        """Clean shutdown of all connections."""
        bybit = self._services.get("bybit")
        if bybit and hasattr(bybit, "disconnect"):
            try:
                await bybit.disconnect()
            except Exception:
                pass
        if self.db:
            await self.db.disconnect()
        log.info("MCP Server shut down")
