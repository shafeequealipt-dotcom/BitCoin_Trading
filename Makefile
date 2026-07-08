# =============================================================================
# Trading Intelligence MCP — Makefile
# Convenient shorthand commands for managing the trading system.
# =============================================================================

.PHONY: start stop restart status logs logs-workers logs-brain logs-mcp logs-errors \
        health monitor backup setup install uninstall test claude \
        bulk-cleanup bulk-cleanup-dry

# ---- Service Management ----
start:
	bash scripts/start_all.sh

stop:
	bash scripts/stop_all.sh

restart:
	bash scripts/restart_all.sh

status:
	bash scripts/status.sh

# ---- Logging ----
logs:
	bash scripts/log_viewer.sh all

logs-workers:
	bash scripts/log_viewer.sh workers

logs-brain:
	bash scripts/log_viewer.sh brain

logs-mcp:
	bash scripts/log_viewer.sh mcp

logs-errors:
	bash scripts/log_viewer.sh errors

logs-last:
	bash scripts/log_viewer.sh last

# ---- Monitoring ----
health:
	.venv/bin/python scripts/health_check.py

health-json:
	.venv/bin/python scripts/health_check.py --json

monitor:
	.venv/bin/python scripts/monitor.py

# ---- Backup & Restore ----
backup:
	bash scripts/backup.sh

restore:
	@echo "Usage: bash scripts/restore.sh <backup_filename>"
	@bash scripts/restore.sh

# ---- Setup & Installation ----
setup:
	bash scripts/setup.sh

install:
	sudo bash scripts/install_services.sh

uninstall:
	sudo bash scripts/uninstall_services.sh

# ---- Development ----
test:
	.venv/bin/pytest tests/ -v --tb=short

test-quick:
	.venv/bin/pytest tests/test_phase0/ -v --tb=short

lint:
	.venv/bin/ruff check src/ tests/

typecheck:
	.venv/bin/mypy src/

claude:
	source .venv/bin/activate && claude

# ---- One-time bulk retention (prefetch-performance fix) ----
# Runs RETENTION_POLICIES immediately + VACUUM on trading.db, plus shadow
# RetentionEngine.run_cleanup() (which includes the new
# _delete_closed_positions). The periodic workers remain idempotent; this is
# just to avoid waiting for the next hourly/daily tick after deploy.
bulk-cleanup:
	.venv/bin/python scripts/bulk_cleanup.py --db both --verbose

bulk-cleanup-dry:
	.venv/bin/python scripts/bulk_cleanup.py --db both --dry-run --verbose
