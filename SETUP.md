# Setup & Run Guide — Trading Intelligence MCP

This bundle is a **sanitized source distribution**. It contains the full codebase
but **no secrets and no database**:

- `.env` (real API keys) has been removed — use `.env.example` as your template.
- `data/trading.db` and all runtime data/logs are removed — the database is
  **created automatically on first run**.
- Telegram chat ID has been redacted from configs/docs.

Everything below gets you from this zip to a running system.

---

## 1. Prerequisites

- **Python 3.11+** (`requires-python = ">=3.11"`)
- **Linux** (developed/deployed on Ubuntu 22.04). macOS works for development.
- System packages: `sqlite3`, `logrotate` (the setup script installs these on
  Ubuntu via `apt`).
- Optional for service deployment: `systemd` (unit files are in `systemd/`).

Check your Python:

```bash
python3 --version   # must be 3.11 or newer
```

---

## 2. Install

### Option A — one command (recommended)

```bash
cd trading-intelligence-mcp
bash scripts/setup.sh
```

`scripts/setup.sh` verifies Python, installs system deps, creates a `.venv`,
and installs all Python dependencies.

### Option B — manual

```bash
cd trading-intelligence-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> For a byte-for-byte reproducible environment, use the pinned lockfile instead:
> `pip install -r requirements.freeze.txt`

---

## 3. Configure your keys

Copy the template and fill in your own credentials:

```bash
cp .env.example .env
# then edit .env
```

Keys used by the system (all read from `.env`):

| Variable | Needed for | Where to get it |
|---|---|---|
| `BYBIT_DEMO_API_KEY` / `BYBIT_DEMO_API_SECRET` | **Paper trading (default mode)** — Bybit demo exec | https://www.bybit.com → API Management (demo) |
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Live market data / live trading (advanced) | https://www.bybit.com/app/user/api-management |
| `ANTHROPIC_API_KEY` | Claude "brain" analysis via API | https://console.anthropic.com/settings/keys |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude brain via Claude Code CLI (alt to API key) | `claude` CLI login |
| `MCP_AUTH_TOKEN` | Auth for the MCP SSE transport | generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram alerts (optional) | @BotFather / @userinfobot |
| `FINNHUB_API_KEY` | News enrichment (optional) | https://finnhub.io/register |
| `REDDIT_CLIENT_ID` / `_SECRET` / `_USERNAME` / `_PASSWORD` | Reddit sentiment (optional) | https://www.reddit.com/prefs/apps |
| `OPENROUTER_API_KEY` | TIAS DeepSeek analysis (optional) | https://openrouter.ai/settings/keys |

**Minimal set to start paper trading:** `BYBIT_DEMO_API_KEY` + `BYBIT_DEMO_API_SECRET`,
plus a Claude credential (`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`) if you
want the brain to make decisions. Optional data-source keys can be left blank.

### Non-secret settings

All behavioural settings live in **`config.toml`** (env vars in `.env` override
where they overlap). The default is **safe paper trading**:

```toml
[general]
mode = "bybit_demo"   # paper money; no real funds at risk
```

Do not change `mode` to `bybit` (live) unless you intend to trade real money.

---

## 4. Run

The system has three processes. Run each (typically in its own terminal, or as
systemd services):

```bash
source .venv/bin/activate

python server.py    # MCP server (stdio for Claude Code + SSE transport)
python workers.py   # background data collection & trade management
python brain.py     # Claude AI decision engine (start when ready)
```

### Via the Makefile (uses `.venv` automatically)

```bash
make start      # start workers/brain/mcp
make status     # process + health status
make logs       # tail logs
make stop
make restart
```

### As systemd services (production)

```bash
make install    # installs unit files from systemd/ (needs sudo)
# services: trading-workers, trading-brain, trading-mcp-sse, trading-backup.timer
```

The SQLite database and log files are created under `data/` on first run.

---

## 5. Test / lint / typecheck

```bash
make test        # pytest tests/ -v
make test-quick  # fast subset
make lint        # ruff
make typecheck   # mypy
```

Or directly: `.venv/bin/pytest tests/ -v --tb=short`.

---

## 6. Project layout (orientation)

```
server.py / workers.py / brain.py   entry points
src/
  config/      settings, constants, validation
  core/        exceptions, logging, types, utilities
  connectors/  Bybit, Finnhub, Reddit clients
  workers/     background collection + trade management
  brain/       Claude analysis engine
  mcp/         MCP server tools & resources
shadow/        Shadow paper-execution component (its own config.toml)
scripts/       setup / install / maintenance scripts
systemd/       service + timer units
tests/         test suite
config.toml    all runtime configuration (paper mode by default)
.env.example   copy to .env and fill in your keys
```

---

## 7. Safety notes

- Ships in **paper-trading mode** — no real money moves until you deliberately
  switch `mode` to live and provide live Bybit keys.
- **Never commit your `.env`** — it is gitignored for a reason.
- Rotate any key that is ever exposed.
