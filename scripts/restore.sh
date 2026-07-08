#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Restore from Backup
# Stops services, restores database and config, restarts services.
# Usage: bash scripts/restore.sh <backup_filename>
# =============================================================================

set -e

PROJECT_DIR="/home/inshadaliqbal786/trading-intelligence-mcp"
BACKUP_DIR="$PROJECT_DIR/backups"
DB_PATH="$PROJECT_DIR/data/trading.db"

if [ -z "$1" ]; then
    echo "=========================================="
    echo " Trading Intelligence MCP — Restore"
    echo "=========================================="
    echo ""
    echo "Available backups:"
    echo ""
    if ls "$BACKUP_DIR"/*.tar.gz 1>/dev/null 2>&1; then
        ls -lht "$BACKUP_DIR"/*.tar.gz | awk '{print "  " $NF " (" $5 ")"}'
    else
        echo "  No backups found in $BACKUP_DIR"
    fi
    echo ""
    echo "Usage: bash scripts/restore.sh <backup_filename>"
    echo "Example: bash scripts/restore.sh 20260322_020000.tar.gz"
    exit 1
fi

BACKUP_FILE="$BACKUP_DIR/$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "=========================================="
echo " Trading Intelligence MCP — Restore"
echo "=========================================="
echo ""
echo "Restoring from: $1"
echo ""
read -p "This will OVERWRITE the current database and config. Continue? [y/N] " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Restore cancelled."
    exit 0
fi

# Stop services
echo ""
echo "Stopping services..."
sudo systemctl stop trading-brain.service 2>/dev/null || true
sudo systemctl stop trading-mcp-sse.service 2>/dev/null || true
sudo systemctl stop trading-workers.service 2>/dev/null || true
sleep 2

# Extract backup
echo "Extracting backup..."
TEMP_DIR=$(mktemp -d)
tar -xzf "$BACKUP_FILE" -C "$TEMP_DIR"

# Find the extracted directory (name matches timestamp)
EXTRACTED=$(ls "$TEMP_DIR")

# Restore database
if [ -f "$TEMP_DIR/$EXTRACTED/trading.db" ]; then
    echo "Restoring database..."
    # Keep a safety copy of current DB
    if [ -f "$DB_PATH" ]; then
        cp "$DB_PATH" "$DB_PATH.pre-restore"
        echo "  Current DB saved as trading.db.pre-restore"
    fi
    cp "$TEMP_DIR/$EXTRACTED/trading.db" "$DB_PATH"
    # Remove WAL/SHM files to force clean state
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
    echo "  Database restored"
else
    echo "  WARNING: No database in backup, keeping current"
fi

# Restore config
if [ -f "$TEMP_DIR/$EXTRACTED/config.toml" ]; then
    echo "Restoring config.toml..."
    cp "$TEMP_DIR/$EXTRACTED/config.toml" "$PROJECT_DIR/config.toml"
fi

if [ -f "$TEMP_DIR/$EXTRACTED/.env" ]; then
    echo "Restoring .env..."
    cp "$TEMP_DIR/$EXTRACTED/.env" "$PROJECT_DIR/.env"
fi

# Cleanup temp
rm -rf "$TEMP_DIR"

# Restart services
echo ""
echo "Restarting services..."
sudo systemctl start trading-workers.service
sleep 5
sudo systemctl start trading-mcp-sse.service
sudo systemctl start trading-brain.service

echo ""
echo "Restore complete! Run 'bash scripts/status.sh' to verify."
echo ""
