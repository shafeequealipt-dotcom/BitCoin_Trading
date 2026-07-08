#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Install Systemd Services
# Must be run with sudo or as root.
# Usage: sudo bash scripts/install_services.sh
# =============================================================================

set -e

PROJECT_DIR="/home/inshadaliqbal786/trading-intelligence-mcp"
SYSTEMD_DIR="/etc/systemd/system"

echo "=========================================="
echo " Trading Intelligence MCP — Install Services"
echo "=========================================="
echo ""

# ---- Pre-flight checks ----
if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    echo "ERROR: This script requires sudo privileges."
    echo "Usage: sudo bash scripts/install_services.sh"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "ERROR: Python venv not found at $PROJECT_DIR/.venv"
    echo "Run 'bash scripts/setup.sh' first."
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and add API keys."
    exit 1
fi

# ---- Kill any existing nohup processes ----
echo "[1/6] Stopping any existing nohup processes..."
pkill -f "python workers.py" 2>/dev/null || true
pkill -f "python server.py" 2>/dev/null || true
pkill -f "python brain.py" 2>/dev/null || true
sleep 2

# ---- Copy service files ----
echo "[2/6] Installing systemd service files..."
sudo cp "$PROJECT_DIR/systemd/trading-workers.service" "$SYSTEMD_DIR/"
sudo cp "$PROJECT_DIR/systemd/trading-mcp-sse.service" "$SYSTEMD_DIR/"
sudo cp "$PROJECT_DIR/systemd/trading-brain.service" "$SYSTEMD_DIR/"
sudo cp "$PROJECT_DIR/systemd/trading-backup.service" "$SYSTEMD_DIR/"
sudo cp "$PROJECT_DIR/systemd/trading-backup.timer" "$SYSTEMD_DIR/"

# ---- Configure log rotation ----
echo "[3/6] Configuring log rotation..."
sudo tee /etc/logrotate.d/trading-mcp > /dev/null << 'EOF'
/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    size 50M
}
EOF

# ---- Reload systemd ----
echo "[4/6] Reloading systemd daemon..."
sudo systemctl daemon-reload

# ---- Enable services (auto-start on boot) ----
echo "[5/6] Enabling services for auto-start on boot..."
sudo systemctl enable trading-workers.service
sudo systemctl enable trading-mcp-sse.service
sudo systemctl enable trading-brain.service
sudo systemctl enable trading-backup.timer

# ---- Start services ----
echo "[6/6] Starting all services..."
sudo systemctl start trading-workers.service
echo "  Workers started. Waiting 5s for initialization..."
sleep 5
sudo systemctl start trading-mcp-sse.service
sudo systemctl start trading-brain.service
sudo systemctl start trading-backup.timer

echo ""
echo "=========================================="
echo " All services installed and started!"
echo "=========================================="
echo ""
echo "Commands:"
echo "  Status:      bash scripts/status.sh"
echo "  Logs:        bash scripts/log_viewer.sh workers"
echo "  Stop:        bash scripts/stop_all.sh"
echo "  Restart:     bash scripts/restart_all.sh"
echo "  Health:      python scripts/health_check.py"
echo "  Monitor:     python scripts/monitor.py"
echo "  Uninstall:   sudo bash scripts/uninstall_services.sh"
echo ""
