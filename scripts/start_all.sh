#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Start All Services
# Usage: bash scripts/start_all.sh
# =============================================================================

echo "Starting all Trading MCP services..."
echo ""

sudo systemctl start trading-workers.service
echo "  Workers starting... waiting 5s for initialization"
sleep 5

sudo systemctl start trading-mcp-sse.service
echo "  MCP SSE server started"

# trading-brain.service is disabled — Brain v2 runs inside workers
echo "  Note: Brain v2 runs inside workers (trading-brain.service disabled)"

echo ""
echo "All services started. Run 'bash scripts/status.sh' to verify."
