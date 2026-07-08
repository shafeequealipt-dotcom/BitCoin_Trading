#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — System Status Dashboard
# Usage: bash scripts/status.sh
# =============================================================================

PROJECT_DIR="/home/inshadaliqbal786/trading-intelligence-mcp"
DB_PATH="$PROJECT_DIR/data/trading.db"
LOG_DIR="$PROJECT_DIR/data/logs"

echo "=========================================="
echo " Trading Intelligence MCP — System Status"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="
echo ""

# ---- Service Status ----
echo "--- Services ---"
for service in trading-workers trading-mcp-sse trading-brain; do
    status=$(systemctl is-active "$service.service" 2>/dev/null)
    if [ "$status" = "active" ]; then
        pid=$(systemctl show -p MainPID "$service.service" 2>/dev/null | cut -d= -f2)
        mem=""
        if [ "$pid" != "0" ] && [ -n "$pid" ]; then
            mem=$(ps -p "$pid" -o rss= 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
        fi
        printf "  %-22s  RUNNING   (PID: %s, Mem: %s)\n" "$service" "$pid" "$mem"
    elif [ "$status" = "inactive" ]; then
        printf "  %-22s  STOPPED\n" "$service"
    else
        printf "  %-22s  %s\n" "$service" "$status"
    fi
done

# Backup timer
timer_status=$(systemctl is-active trading-backup.timer 2>/dev/null)
next_run=$(systemctl show trading-backup.timer -p NextElapseUSecRealtime 2>/dev/null | cut -d= -f2)
printf "  %-22s  %s" "trading-backup.timer" "$timer_status"
if [ -n "$next_run" ] && [ "$next_run" != "n/a" ]; then
    printf "  (next: %s)" "$next_run"
fi
echo ""

echo ""

# ---- Database ----
echo "--- Database ---"
if [ -f "$DB_PATH" ]; then
    size=$(du -h "$DB_PATH" | cut -f1)
    echo "  Path: $DB_PATH"
    echo "  Size: $size"

    # WAL mode check
    wal=$(sqlite3 "$DB_PATH" "PRAGMA journal_mode;" 2>/dev/null || echo "unknown")
    echo "  Mode: $wal"

    # Row counts for key tables
    for table in ticker_cache klines news_articles fear_greed_index signals trade_history brain_decisions; do
        count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM $table;" 2>/dev/null || echo "?")
        printf "  %-22s  %s rows\n" "$table" "$count"
    done
else
    echo "  Database not found at $DB_PATH"
fi

echo ""

# ---- Memory ----
echo "--- Memory ---"
free -h | head -2

echo ""

# ---- Disk ----
echo "--- Disk ---"
df -h / | tail -1

echo ""

# ---- Logs ----
echo "--- Logs (error counts) ---"
for log in workers.log brain.log mcp.log general.log; do
    if [ -f "$LOG_DIR/$log" ]; then
        errors=$(grep -c "ERROR\|CRITICAL" "$LOG_DIR/$log" 2>/dev/null || echo "0")
        size=$(du -h "$LOG_DIR/$log" | cut -f1)
        printf "  %-18s  %s errors  (%s)\n" "$log" "$errors" "$size"
    else
        printf "  %-18s  not found\n" "$log"
    fi
done

echo ""

# ---- Backups ----
echo "--- Backups ---"
BACKUP_DIR="$PROJECT_DIR/backups"
if [ -d "$BACKUP_DIR" ]; then
    count=$(find "$BACKUP_DIR" -name "*.tar.gz" 2>/dev/null | wc -l)
    total_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
    latest=$(ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null | head -1)
    echo "  Count: $count backups ($total_size)"
    if [ -n "$latest" ]; then
        echo "  Latest: $(basename "$latest")"
    fi
else
    echo "  No backups yet"
fi

echo ""

# ---- Uptime ----
echo "--- System ---"
uptime
echo ""
