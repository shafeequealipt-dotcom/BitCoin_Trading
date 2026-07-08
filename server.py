"""Trading Intelligence MCP — Server Entry Point.

Usage:
    python server.py                     # stdio transport (Claude Code)
    python server.py --transport sse     # SSE transport (claude.ai)
    python server.py --transport sse --port 9090  # Custom port
"""

import asyncio
import argparse

from src.config.settings import Settings
from src.core.logging import setup_logging, get_logger
from src.mcp.server import MCPServer


async def main(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the MCP server with the specified transport."""
    settings = Settings._load_fresh()
    settings.mcp.transport = transport

    setup_logging(settings.general.log_level, settings.general.log_dir)
    log = get_logger("mcp")

    log.info("Starting MCP Server (transport={t})", t=transport)

    server = MCPServer(settings)
    await server.initialize()

    try:
        if transport == "stdio":
            await server.run_stdio()
        elif transport == "sse":
            log.info("SSE server on {h}:{p}", h=host, p=port)
            await server.run_sse(host, port)
        else:
            log.error("Unknown transport: {t}", t=transport)
    except KeyboardInterrupt:
        log.info("Server shutting down")
    finally:
        await server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Intelligence MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.transport, args.host, args.port))
    except KeyboardInterrupt:
        pass
