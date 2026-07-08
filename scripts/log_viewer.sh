#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Log Viewer
# Quick log viewing with filtering.
# Usage: bash scripts/log_viewer.sh [workers|brain|mcp|general|errors|all]
# =============================================================================

PROJECT_DIR="/home/inshadaliqbal786/trading-intelligence-mcp"
LOG_DIR="$PROJECT_DIR/data/logs"

case "${1:-all}" in
    workers)
        echo "=== Workers Log (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR/workers.log"
        ;;
    brain)
        echo "=== Brain Log (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR/brain.log"
        ;;
    mcp)
        echo "=== MCP Log (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR/mcp.log"
        ;;
    general)
        echo "=== General Log (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR/general.log"
        ;;
    errors)
        echo "=== Errors Only — All Logs (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR"/*.log | grep --color=always "ERROR\|CRITICAL"
        ;;
    last)
        # Show last 50 lines of each log
        for log in workers.log brain.log mcp.log general.log; do
            if [ -f "$LOG_DIR/$log" ]; then
                echo "========== $log (last 20 lines) =========="
                tail -20 "$LOG_DIR/$log"
                echo ""
            fi
        done
        ;;
    all)
        echo "=== All Logs (Ctrl+C to exit) ==="
        tail -f "$LOG_DIR"/*.log
        ;;
    *)
        echo "Trading Intelligence MCP — Log Viewer"
        echo ""
        echo "Usage: bash scripts/log_viewer.sh [command]"
        echo ""
        echo "Commands:"
        echo "  workers   Follow workers.log"
        echo "  brain     Follow brain.log"
        echo "  mcp       Follow mcp.log"
        echo "  general   Follow general.log"
        echo "  errors    Follow all logs, show only ERROR/CRITICAL"
        echo "  last      Show last 20 lines of each log"
        echo "  all       Follow all logs (default)"
        ;;
esac
