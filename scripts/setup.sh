#!/usr/bin/env bash
# =============================================================================
# Trading Intelligence MCP — One-Command Setup
# Sets up everything from scratch on a fresh Ubuntu 22.04 server.
# Usage: bash scripts/setup.sh
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " Trading Intelligence MCP — Setup"
echo "=========================================="
echo "Project: $PROJECT_DIR"
echo ""

# ---- 1. Check Python version ----
echo "[1/8] Checking Python version..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "ERROR: Python 3.11+ required, found $PY_VERSION"
    exit 1
fi
echo "  Python $PY_VERSION OK"

# ---- 2. Install system dependencies ----
echo "[2/8] Checking system dependencies..."
NEED_INSTALL=""
for pkg in sqlite3 logrotate; do
    if ! command -v "$pkg" &>/dev/null; then
        NEED_INSTALL="$NEED_INSTALL $pkg"
    fi
done
if [ -n "$NEED_INSTALL" ]; then
    echo "  Installing:$NEED_INSTALL"
    sudo apt-get update -qq && sudo apt-get install -y -qq $NEED_INSTALL
else
    echo "  All system dependencies present"
fi

# ---- 3. Create virtual environment ----
echo "[3/8] Setting up Python virtual environment..."
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
    echo "  Created .venv"
else
    echo "  .venv already exists"
fi

# ---- 4. Install Python dependencies ----
echo "[4/8] Installing Python dependencies..."
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip -q
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
echo "  Dependencies installed"

# ---- 5. Create .env from example ----
echo "[5/8] Checking environment file..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    echo "  Created .env from .env.example"
    echo "  >>> IMPORTANT: Edit .env and add your API keys! <<<"
else
    echo "  .env already exists"
fi

# ---- 6. Create data directories ----
echo "[6/8] Creating data directories..."
mkdir -p "$PROJECT_DIR/data/logs"
mkdir -p "$PROJECT_DIR/backups"
echo "  data/logs/ and backups/ ready"

# ---- 7. Run database migrations ----
echo "[7/8] Running database migrations..."
"$PROJECT_DIR/.venv/bin/python" -c "
import asyncio
import sys
sys.path.insert(0, '$PROJECT_DIR')
from src.config.settings import Settings
from src.database.connection import DatabaseManager
from src.database.migrations import run_migrations

async def migrate():
    settings = Settings._load_fresh()
    db = DatabaseManager(settings.database.path)
    await db.connect()
    await run_migrations(db)
    await db.disconnect()
    print('  Migrations complete')

asyncio.run(migrate())
"

# ---- 8. Validate config ----
echo "[8/8] Validating configuration..."
"$PROJECT_DIR/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from src.config.settings import Settings
from src.config.validators import validate_config
s = Settings._load_fresh()
warnings = validate_config(s)
for w in warnings:
    print(f'  WARNING: {w}')
if not warnings:
    print('  Config OK')
print('  Validation complete')
"

echo ""
echo "=========================================="
echo " Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Install systemd services: sudo bash scripts/install_services.sh"
echo ""
echo "Or run manually:"
echo "  python server.py                  # MCP server (stdio)"
echo "  python server.py --transport sse  # MCP server (SSE on :8080)"
echo "  python workers.py                 # Background workers"
echo "  python brain.py                   # Claude Brain"
echo ""
echo "Useful commands:"
echo "  make status    # Service status"
echo "  make health    # Health check"
echo "  make monitor   # Live dashboard"
echo "  make logs      # Follow all logs"
echo ""
