#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Uninstall Systemd Services
# Cleanly removes all services and timers.
# Usage: sudo bash scripts/uninstall_services.sh
# =============================================================================

set -e

echo "=========================================="
echo " Trading Intelligence MCP — Uninstall Services"
echo "=========================================="
echo ""

# Stop services
echo "Stopping services..."
sudo systemctl stop trading-brain.service 2>/dev/null || true
sudo systemctl stop trading-mcp-sse.service 2>/dev/null || true
sudo systemctl stop trading-workers.service 2>/dev/null || true
sudo systemctl stop trading-backup.timer 2>/dev/null || true

# Disable services
echo "Disabling services..."
sudo systemctl disable trading-brain.service 2>/dev/null || true
sudo systemctl disable trading-mcp-sse.service 2>/dev/null || true
sudo systemctl disable trading-workers.service 2>/dev/null || true
sudo systemctl disable trading-backup.timer 2>/dev/null || true

# Remove service files
echo "Removing service files..."
sudo rm -f /etc/systemd/system/trading-workers.service
sudo rm -f /etc/systemd/system/trading-mcp-sse.service
sudo rm -f /etc/systemd/system/trading-brain.service
sudo rm -f /etc/systemd/system/trading-backup.service
sudo rm -f /etc/systemd/system/trading-backup.timer

# Remove logrotate config
echo "Removing logrotate config..."
sudo rm -f /etc/logrotate.d/trading-mcp

# Reload systemd
sudo systemctl daemon-reload
sudo systemctl reset-failed 2>/dev/null || true

echo ""
echo "All services removed."
echo "Data, logs, and backups are preserved."
echo ""
