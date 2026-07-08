#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — Backup Database & Config
# Creates an atomic, compressed backup. Keeps last 7 backups.
# Usage: bash scripts/backup.sh
# =============================================================================

set -e

PROJECT_DIR="/home/inshadaliqbal786/trading-intelligence-mcp"
DB_PATH="$PROJECT_DIR/data/trading.db"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"

echo "=========================================="
echo " Trading Intelligence MCP — Backup"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="
echo ""

# Create backup directory
mkdir -p "$BACKUP_PATH"

# Backup database atomically using sqlite3 .backup
if [ -f "$DB_PATH" ]; then
    echo "Backing up database..."
    sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH/trading.db'"
    echo "  Database backed up ($(du -h "$BACKUP_PATH/trading.db" | cut -f1))"
else
    echo "  WARNING: Database not found at $DB_PATH"
fi

# Backup config files
echo "Backing up configuration..."
cp "$PROJECT_DIR/config.toml" "$BACKUP_PATH/" 2>/dev/null || true
cp "$PROJECT_DIR/.env" "$BACKUP_PATH/" 2>/dev/null || true
echo "  Config files copied"

# Compress
echo "Compressing..."
cd "$BACKUP_DIR"
tar -czf "$TIMESTAMP.tar.gz" "$TIMESTAMP"
rm -rf "$BACKUP_PATH"
ARCHIVE_SIZE=$(du -h "$BACKUP_DIR/$TIMESTAMP.tar.gz" | cut -f1)
echo "  Archive: $TIMESTAMP.tar.gz ($ARCHIVE_SIZE)"

# Retention: keep only last 7 backups
REMOVED=$(ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -n +8)
if [ -n "$REMOVED" ]; then
    echo ""
    echo "Cleaning old backups..."
    echo "$REMOVED" | xargs rm -f
    echo "  Removed $(echo "$REMOVED" | wc -l) old backup(s)"
fi

echo ""
echo "Backup complete: $BACKUP_DIR/$TIMESTAMP.tar.gz"
TOTAL=$(ls "$BACKUP_DIR"/*.tar.gz 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
echo "Total backups: $TOTAL ($TOTAL_SIZE)"
echo ""
