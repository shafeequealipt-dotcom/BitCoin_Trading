# Trading Intelligence MCP

Enterprise-grade Crypto Trading Intelligence system built as an MCP (Model Context Protocol) server. Combines Bybit trading, Finnhub news, Reddit sentiment, alternative data, and Claude AI analysis into a unified trading intelligence platform.

## Features

- **Bybit Integration** — Spot + USDT perpetual trading with paper mode via testnet
- **Market Intelligence** — Finnhub news, Reddit sentiment, Fear & Greed Index, funding rates
- **Claude Brain** — Autonomous AI analysis with scheduled and signal-triggered decisions
- **Risk Management** — Mandatory stop-loss, position sizing, daily loss limits, exposure caps
- **Dual MCP Transport** — stdio for Claude Code + SSE for claude.ai browser
- **Telegram Alerts** — Trade notifications, signal alerts, daily summaries
- **Memory System** — 4-layer SQLite storage for market data, intelligence, trades, and learning

## Quick Start

```bash
# Clone and setup
git clone <repo-url>
cd trading-intelligence-mcp
bash scripts/setup.sh

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run MCP server (for Claude Code)
python server.py

# Run background workers (separate terminal)
python workers.py

# Run Claude Brain (separate terminal, when ready)
python brain.py
```

## Configuration

All settings in `config.toml`. Environment variables in `.env` override config values.

**Default mode is paper trading** — no real money at risk. Live trading requires explicit config change.

## Architecture

- **Async-first** — All I/O uses asyncio
- **No stdout** — MCP protocol uses stdio; all logging goes to files only
- **Memory efficient** — Targets 1GB RAM (GCP e2-micro). No pandas.
- **Config-driven** — Zero hardcoded magic numbers

## Project Structure

```
src/
  config/     — Settings, constants, validation
  core/       — Exceptions, logging, types, decorators, utilities
  connectors/ — Bybit, Finnhub, Reddit API clients (Phase 1+)
  workers/    — Background data collection (Phase 2+)
  brain/      — Claude AI analysis engine (Phase 3+)
  mcp/        — MCP server tools and resources (Phase 4+)
```

## Testing

```bash
pytest tests/ -v
```
