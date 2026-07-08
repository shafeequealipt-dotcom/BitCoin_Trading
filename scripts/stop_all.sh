#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Stop All Services
# Usage: bash scripts/stop_all.sh
# =============================================================================

echo "Stopping all Trading MCP services..."
echo ""

sudo systemctl stop trading-brain.service
echo "  Claude Brain stopped"

sudo systemctl stop trading-mcp-sse.service
echo "  MCP SSE server stopped"

sudo systemctl stop trading-workers.service
echo "  Workers stopped"

echo ""
echo "All services stopped."
