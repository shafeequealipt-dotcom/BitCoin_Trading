#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Restart All Services
# Usage: bash scripts/restart_all.sh
# =============================================================================

echo "Restarting all Trading MCP services..."
echo ""

sudo systemctl restart trading-workers.service
echo "  Workers restarting... waiting 5s for initialization"
sleep 5

sudo systemctl restart trading-mcp-sse.service
echo "  MCP SSE server restarted"

sudo systemctl restart trading-brain.service
echo "  Claude Brain restarted"

echo ""
echo "All services restarted. Run 'bash scripts/status.sh' to verify."
