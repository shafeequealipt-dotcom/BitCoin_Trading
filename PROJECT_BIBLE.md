# PROJECT BIBLE -- Trading Intelligence MCP

> **Version:** 0.1.0 | **Last Updated:** 2026-03-23 | **Schema Version:** 9
> **Total Source Files:** 398 Python files | **Entry Points:** 3 (`workers.py`, `brain.py`, `server.py`)
> **Exchange:** Bybit (testnet/mainnet) | **AI:** Anthropic Claude Sonnet | **Database:** SQLite (aiosqlite)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Complete File Map](#2-complete-file-map)
3. [Configuration](#3-configuration)
4. [Database Schema](#4-database-schema)
5. [Core Types](#5-core-types)
6. [Trading Services](#6-trading-services)
7. [Analysis Engine](#7-analysis-engine)
8. [Strategy System](#8-strategy-system)
9. [Brain System](#9-brain-system)
10. [Risk Management](#10-risk-management)
11. [Factory System](#11-factory-system)
12. [Portfolio Optimizer](#12-portfolio-optimizer)
13. [Telegram Bot](#13-telegram-bot)
14. [Alert System](#14-alert-system)
15. [Workers](#15-workers)
16. [MCP Tools](#16-mcp-tools)
17. [Deployment](#17-deployment)
18. [Data Flows](#18-data-flows)
19. [Performance Enforcer](#19-performance-enforcer)
20. [Glossary](#20-glossary)

---

## 1. Project Overview

### Summary

Trading Intelligence MCP is an autonomous cryptocurrency trading system that combines real-time market data, technical analysis, sentiment intelligence, and AI-powered decision-making via Anthropic's Claude API. It trades perpetual futures on Bybit, exposes its capabilities through the Model Context Protocol (MCP) for Claude Code/claude.ai, and provides a two-way interactive Telegram bot for monitoring and control.

The system is built across 10 architectural phases:

| Phase | Name | Purpose |
|-------|------|---------|
| 0 | Foundation | Core types, config, DB, logging, exceptions |
| 1 | Exchange | Bybit REST/WS client, trading services |
| 2 | Intelligence | News (Finnhub), Reddit sentiment, alt data |
| 3 | Brain | Claude AI client, prompts, decision parser, executor |
| 4 | Strategy Engine | 40 strategies, 4-layer pipeline, regime detection |
| 5 | Strategy Factory | AI pattern discovery, code generation, validation |
| 6 | Backtesting | Walk-forward, Monte Carlo, lifecycle management |
| 7 | Portfolio | Kelly criterion, correlation, risk budgets, stress tests |
| 8 | Telegram | Interactive bot, 10 handler modules, AI chat |
| 9 | Performance | Enforcer, hourly targets, escalation, rewards |
| 10 | Deployment | systemd services, scripts, monitoring, backups |

### Architecture Diagram

```
+------------------------------------------------------------------+
|                     ENTRY POINTS                                  |
|   workers.py          brain.py            server.py               |
|   (Background)        (AI Trading)        (MCP Server)            |
+--------+-----------------+-------------------+-------------------+
         |                 |                   |
    +----v----+       +----v----+        +----v----+
    | Worker  |       | Brain   |        | MCP     |
    | Manager |       | Manager |        | Server  |
    +---------+       +---------+        +---------+
         |                 |                   |
    +----v-----------------v-------------------v----+
    |              SERVICE CONTAINER                 |
    |  Bybit Client | Market | Order | Position     |
    |  Account | TA Engine | Risk Manager           |
    |  Alert Manager | Strategy Registry            |
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+---+
       |  |  |  |  |  |  |  |  |  |  |  |  |  |
  +----v--v--v--v--v--v--v--v--v--v--v--v--v--v----+
  |              WORKER FLEET (24 workers)          |
  | Price | Kline | News | Reddit | AltData | Signal|
  | Watchdog | Scanner | Regime | Strategy          |
  | Discovery | LiveMonitor | Backtest | Trial      |
  | Allocation | Optimization | Telegram | Alerts   |
  | Enforcer | Cleanup | ScheduledReport            |
  +-----+------------------------------------------+
        |                                     |
   +----v---------+                    +------v------+
   |   SQLite DB  |                    |  Bybit API  |
   | (aiosqlite)  |                    | REST + WS   |
   | WAL mode     |                    +------+------+
   +--------------+                           |
                                        +-----v-----+
                                        | Exchange   |
                                        | (Testnet/  |
                                        |  Mainnet)  |
                                        +-----------+
```

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Exchange API | pybit (Bybit V5 unified) |
| AI | anthropic SDK (Claude Sonnet) |
| Database | SQLite via aiosqlite, WAL mode |
| Async | asyncio, aiohttp |
| TA Math | numpy (vectorized, no talib) |
| Logging | loguru (file-only, no stdout) |
| Config | TOML (tomllib) + .env (dotenv) |
| Telegram | python-telegram-bot |
| MCP | mcp SDK (stdio + SSE) |
| Sentiment | PRAW (Reddit), finnhub-python |
| Process Mgmt | systemd |

---

## 2. Complete File Map

### Entry Points
| File | Description |
|------|-------------|
| `workers.py` | Starts all background workers (data collection, strategy, monitoring) |
| `brain.py` | Starts the Claude Brain scheduler (autonomous analysis + trading) |
| `server.py` | Starts the MCP server (stdio for Claude Code, SSE for claude.ai) |
| `config.toml` | Master configuration file (all settings for entire system) |

### `src/core/` -- Foundation
| File | Description |
|------|-------------|
| `types.py` | All enums (Side, OrderType, SignalType, etc.) and dataclasses (OHLCV, Order, Position, etc.) |
| `utils.py` | Pure utility functions: ID generation, timestamps, rounding, percentage change |
| `logging.py` | Loguru file-only logging with component routing (mcp.log, workers.log, brain.log) |
| `exceptions.py` | Complete exception hierarchy (30+ exception classes) under TradingMCPError |
| `decorators.py` | Reusable decorators: @retry, @rate_limit, @timed, @validate_input |
| `container.py` | ServiceContainer: initializes ALL services in dependency order |

### `src/config/` -- Configuration
| File | Description |
|------|-------------|
| `settings.py` | 23 typed dataclass sections, TOML + .env loader, singleton pattern |
| `constants.py` | Frozen constants: supported symbols, rate limits, table names, MCP tools |
| `validators.py` | Pre-startup validation: mode, risk params, API keys, paths, MCP transport |

### `src/trading/` -- Exchange Integration
| File | Description |
|------|-------------|
| `client.py` | BybitClient: pybit HTTP wrapper with rate limiting, retry, safety assertions |
| `auth.py` | BybitAuth: HMAC-SHA256 signing and credential validation |
| `websocket.py` | BybitWebSocket: public + private WS streams with auto-reconnect |
| `services/market_service.py` | MarketService: get_ticker, get_tickers, get_klines, get_orderbook, get_recent_trades |
| `services/order_service.py` | OrderService: place_order, modify_order, cancel_order, cancel_all with safety checks |
| `services/position_service.py` | PositionService: get_positions, close_position, reduce_position, set SL/TP/leverage |
| `services/account_service.py` | AccountService: wallet balance, equity, margin usage |
| `services/instrument_service.py` | InstrumentService: instrument info (tick size, qty step, min/max) |
| `models/instrument.py` | InstrumentInfo dataclass |

### `src/analysis/` -- Technical Analysis
| File | Description |
|------|-------------|
| `engine.py` | TAEngine: orchestrates all indicators + patterns, produces comprehensive report |
| `indicators/trend.py` | SMA, EMA, WMA, DEMA, TEMA, MACD, ADX, Supertrend, Ichimoku, Parabolic SAR |
| `indicators/momentum.py` | RSI, Stochastic, CCI, Williams %R, ROC, Momentum, Awesome Oscillator, TSI, Stoch RSI |
| `indicators/volatility.py` | Bollinger Bands, ATR, NATR, Keltner Channels, Donchian, Choppiness Index, Std Dev |
| `indicators/volume.py` | OBV, VWAP, MFI, A/D Line, Chaikin Money Flow, Volume SMA, Force Index |
| `patterns/candlestick.py` | 16 candlestick patterns: doji, hammer, engulfing, morning/evening star, etc. |
| `patterns/chart_patterns.py` | Chart patterns: double top/bottom, head & shoulders, triangles, wedges |

### `src/intelligence/` -- News, Sentiment, Alt Data
| File | Description |
|------|-------------|
| `news/finnhub_client.py` | FinnhubClient: news article and economic calendar fetching |
| `news/news_service.py` | NewsService: fetch, score sentiment, persist articles |
| `news/calendar_service.py` | CalendarService: economic events from Finnhub |
| `sentiment/reddit_client.py` | RedditClient: PRAW wrapper for subreddit scraping |
| `sentiment/reddit_service.py` | RedditService: fetch posts, score sentiment, extract symbols |
| `sentiment/scorer.py` | SentimentScorer: keyword-based sentiment scoring |
| `sentiment/aggregator.py` | SentimentAggregator: combines news + Reddit + F&G into unified score |
| `altdata/fear_greed.py` | FearGreedClient: Crypto Fear & Greed Index from alternative.me |
| `altdata/funding_rates.py` | FundingRateTracker: perpetual funding rates from Bybit |
| `altdata/open_interest.py` | OpenInterestTracker: aggregate OI from Bybit |
| `altdata/onchain.py` | OnChainClient: on-chain metrics (CoinGecko) |
| `signals/signal_generator.py` | SignalGenerator: combines TA + sentiment into trading signals |

### `src/brain/` -- Claude AI Brain
| File | Description |
|------|-------------|
| `__init__.py` | BrainManager: top-level wiring of all brain components |
| `claude_client.py` | ClaudeClient: Anthropic SDK wrapper with cost tracking |
| `cost_tracker.py` | CostTracker: daily budget enforcement ($1/day default) |
| `decision_parser.py` | DecisionParser: extracts JSON from Claude responses, builds BrainDecision/WatchdogDecision |
| `executor.py` | BrainExecutor: executes parsed decisions through trading services |
| `prompt_builder.py` | PromptBuilder: gathers live data from DB/services, formats for Claude |
| `scheduler.py` | BrainScheduler: scheduled + signal-triggered Claude calls with dedup |
| `brain_v2.py` | BrainV2: enhanced brain for 4-layer strategy architecture |
| `prompts/__init__.py` | Exports all prompt templates |
| `prompts/market_analysis.py` | SYSTEM_PROMPT: Claude's role and response rules |
| `prompts/trade_decision.py` | TRADE_DECISION_PROMPT: template with market data placeholders |
| `prompts/risk_review.py` | RISK_REVIEW_PROMPT: portfolio risk assessment |
| `prompts/daily_summary.py` | DAILY_SUMMARY_PROMPT: end-of-day performance summary |
| `prompts/position_review.py` | WATCHDOG_SYSTEM_PROMPT + POSITION_REVIEW_PROMPT: position watchdog prompts |
| `prompts/setup_review.py` | SETUP_REVIEW_SYSTEM_PROMPT + SETUP_REVIEW_PROMPT: BrainV2 setup evaluation |
| `prompts/weekly_optimization.py` | OPTIMIZATION_SYSTEM_PROMPT + OPTIMIZATION_REVIEW_PROMPT: strategy parameter tuning |

### `src/risk/` -- Risk Management
| File | Description |
|------|-------------|
| `risk_manager.py` | RiskManager: central orchestrator for all risk checks |
| `position_sizer.py` | PositionSizer: fixed %, ATR-based, Kelly position sizing |
| `stop_loss.py` | StopLossCalculator: fixed %, ATR-based, S/R-based SL/TP |
| `drawdown.py` | DrawdownTracker: peak equity tracking, circuit breakers, daily loss limit |
| `portfolio.py` | PortfolioAnalyzer: exposure analysis, correlation, concentration risk |
| `validators.py` | TradeValidator: 15+ pre-trade validation checks with absolute hard limits |

### `src/strategies/` -- Strategy System
| File | Description |
|------|-------------|
| `base_strategy.py` | BaseStrategy ABC: name, category, applicable_regimes, scan(), vote() |
| `registry.py` | StrategyRegistry: register, get_active_for_regime, performance tracking |
| `scanner.py` | MarketScanner: discovers tradeable coins by volume, spread, tier |
| `regime.py` | RegimeDetector: classifies market into 5 regimes via ADX/ATR/choppiness |
| `scorer.py` | TradeScorer (Layer 2): 0-100 score from base + confluence + context + quality |
| `ensemble.py` | EnsembleVoter (Layer 3): weighted consensus polling across all strategies |
| `pnl_manager.py` | DailyPnLManager: 7 modes from TARGET_HIT to HALTED with aggression scaling |
| `smart_leverage.py` | SmartLeverage: dynamic 1-5x based on confidence, tier, regime, volatility |
| `optimizer.py` | Weekly adaptive optimizer: adjusts weights and parameters |
| `performance_enforcer.py` | PerformanceEnforcer: hourly targets, 5-level escalation, forced trades |
| `register_all.py` | Registers all 40 strategies (A1-K4 + X1) into registry |
| `models/regime_types.py` | MarketRegime enum, RegimeState dataclass, REGIME_ACTIVE_CATEGORIES map |
| `models/signal_types.py` | RawSignal, ScoredSetup, EnsembleVote, EnsembleResult, TradeDecision, StrategyPerformance |
| `categories/_helpers.py` | Shared helper functions for strategy implementations |
| `categories/a1..k4,x1` | 41 strategy implementation files (see Strategy System section) |

### `src/factory/` -- AI Strategy Factory
| File | Description |
|------|-------------|
| `discoverer.py` | PatternDiscoverer: orchestrates 7 analyzers across all symbols |
| `generator.py` | StrategyGenerator: uses Claude to generate Python strategy code |
| `validator.py` | CodeValidator: syntax, safety, interface validation of generated code |
| `backtester.py` | BacktestEngine: event-driven backtesting with realistic costs |
| `simulator.py` | TradeSimulator: simulates order execution with slippage/commission |
| `metrics.py` | BacktestMetrics: Sharpe, Sortino, Calmar, profit factor, max drawdown |
| `walk_forward.py` | WalkForwardAnalyzer: train/test split validation |
| `monte_carlo.py` | MonteCarloSimulator: probability of profit/ruin estimation |
| `lifecycle.py` | StrategyLifecycleManager: state machine (generated -> validated -> trial -> promoted) |
| `trial_manager.py` | TrialManager: paper trading evaluation with promotion/kill decisions |
| `live_monitor.py` | LivePatternMonitor: real-time pattern occurrence tracking |
| `analyzers/single_variable.py` | Single-variable pattern analysis (e.g., RSI extremes) |
| `analyzers/multi_variable.py` | Multi-variable pattern analysis (e.g., RSI + volume) |
| `analyzers/sequential.py` | Sequential pattern analysis (e.g., A then B then C) |
| `analyzers/cross_asset.py` | Cross-asset pattern analysis (e.g., BTC leads ETH) |
| `analyzers/temporal.py` | Time-based pattern analysis (e.g., hour of day) |
| `analyzers/news_reactive.py` | News-reactive pattern analysis (e.g., post-announcement moves) |
| `analyzers/micro_patterns.py` | Micro-structure patterns (e.g., order flow imbalances) |

### `src/portfolio/` -- Portfolio Optimization
| File | Description |
|------|-------------|
| `kelly.py` | KellyCalculator: full, fractional, and dynamic Kelly criterion |
| `correlation.py` | CorrelationTracker: strategy return correlation matrix |
| `allocator.py` | DynamicAllocator: combines Kelly + mean-variance + risk parity |
| `risk_budget.py` | RiskBudgetManager: category-level budgets (proven/AI/trial) |
| `optimizer.py` | PortfolioOptimizer: weekly rebalancing with approval workflow |
| `stress_test.py` | StressTester: crash scenarios, correlation spike, liquidity crisis |
| `analytics.py` | Performance attribution: strategy contributions, regime/timing/sizing factors |

### `src/telegram/` -- Interactive Telegram Bot
| File | Description |
|------|-------------|
| `bot.py` | InteractiveTelegramBot: main router with 10 handler classes |
| `auth.py` | TelegramAuth: chat_id-based authorization |
| `router.py` | MessageRouter: maps commands/text to handlers |
| `conversation.py` | ConversationManager: multi-turn conversation state |
| `handlers/trading.py` | /buy, /sell, /close, /orders -- trade execution with confirmation |
| `handlers/portfolio.py` | /portfolio, /pnl, /balance -- account overview |
| `handlers/analysis.py` | /analyze, /ta, /chart -- technical analysis on demand |
| `handlers/brain.py` | /brain, /ask -- AI analysis and free-form questions |
| `handlers/alerts.py` | /alert -- price alert management |
| `handlers/watchlist.py` | /watchlist -- symbol watchlist CRUD |
| `handlers/journal.py` | /journal -- trade journal entries |
| `handlers/schedule.py` | /schedule -- report scheduling (morning briefing) |
| `handlers/system.py` | /status, /health, /workers -- system monitoring |
| `handlers/emergency.py` | /emergency, /closeall, /halt -- emergency controls |
| `ai/chat.py` | AI-powered natural language responses via Claude |
| `ai/intent.py` | Intent detection for free-form messages |
| `ai/context.py` | Context building for AI conversations |
| `ui/keyboards.py` | Inline keyboard builders |
| `ui/charts.py` | Chart rendering helpers |
| `ui/formatters.py` | Message formatting utilities |
| `ui/pagination.py` | Paginated message helpers |
| `features/price_alerts.py` | PriceAlertEngine: condition monitoring (above/below/cross) |
| `features/trade_journal.py` | Journal storage and retrieval |
| `features/scheduled_reports.py` | ScheduledReportEngine: morning briefings, hourly summaries |
| `features/portfolio_tracker.py` | Portfolio value tracking |
| `features/watchlist_manager.py` | Watchlist CRUD operations |
| `features/morning_briefing.py` | Morning briefing content generation |
| `features/export.py` | Data export functionality |

### `src/alerts/` -- Alert System
| File | Description |
|------|-------------|
| `alert_manager.py` | AlertManager: central hub for routing, throttling, scheduling |
| `telegram_bot.py` | TelegramBot: low-level Telegram API message sending |
| `templates.py` | AlertTemplates: HTML-formatted message templates for every alert type |
| `formatter.py` | AlertFormatter: price, PnL, signal, confidence formatting helpers |
| `throttle.py` | AlertThrottle: rate limiting (per hour), deduplication (5-min window), queuing |

### `src/mcp/` -- MCP Server
| File | Description |
|------|-------------|
| `server.py` | MCPServer: dual-transport (stdio/SSE), tool registration, handler dispatch |
| `auth.py` | MCP SSE authentication middleware |
| `tools/trading_tools.py` | 12 tools: account, ticker, klines, orderbook, place/modify/cancel orders, positions |
| `tools/analysis_tools.py` | 5 tools: technical analysis, single indicator, patterns, signal, recommendation |
| `tools/news_tools.py` | 4 tools: latest news, news by symbol, search, economic calendar |
| `tools/sentiment_tools.py` | 5 tools: Reddit sentiment, subreddit hot, social buzz, aggregated, history |
| `tools/altdata_tools.py` | 5 tools: fear & greed, funding rates, open interest, funding history, overview |
| `tools/risk_tools.py` | 5 tools: position size, risk exposure, stop-loss calc, daily PnL, risk status |
| `tools/memory_tools.py` | 4 tools: trade history, strategy performance, pattern outcomes, brain decisions |
| `tools/system_tools.py` | 3 tools: system status, worker status, update preference |

### `src/database/` -- Data Persistence
| File | Description |
|------|-------------|
| `connection.py` | DatabaseManager: async SQLite with WAL mode, lock, execute/fetch helpers |
| `migrations.py` | Schema migrations: 40+ tables across 9 schema versions |
| `repositories/market_repo.py` | MarketRepository: klines, tickers, orderbook CRUD |
| `repositories/trading_repo.py` | TradingRepository: orders, positions, trades, account snapshots |
| `repositories/news_repo.py` | NewsRepository: articles, sentiment scores |
| `repositories/sentiment_repo.py` | SentimentRepository: aggregated sentiment, Reddit posts |
| `repositories/altdata_repo.py` | AltDataRepository: fear & greed, funding rates, open interest |
| `repositories/context_repo.py` | ContextRepository: user preferences, watchlists, session log |
| `repositories/learning_repo.py` | LearningRepository: strategy performance, signal accuracy, patterns |
| `repositories/factory_repo.py` | FactoryRepository: discovered patterns, generated strategies |
| `repositories/backtest_repo.py` | BacktestRepository: results, trades, lifecycle transitions, trials |
| `repositories/portfolio_repo.py` | PortfolioRepository: allocations, correlations, risk budgets, stress tests |
| `repositories/telegram_repo.py` | TelegramRepository: price alerts, journal, reports, conversations |

### `src/workers/` -- Background Workers
| File | Description |
|------|-------------|
| `manager.py` | WorkerManager: creates, starts, monitors all workers with dependency injection |
| `base_worker.py` | BaseWorker ABC: run loop, error recovery with exponential backoff, heartbeat |
| `health.py` | WorkerHealthMonitor: tracks status of all workers |
| `price_worker.py` | PriceWorker: WebSocket ticker streaming |
| `kline_worker.py` | KlineWorker: periodic OHLCV fetching for all symbols/timeframes |
| `news_worker.py` | NewsWorker: Finnhub news + calendar polling |
| `reddit_worker.py` | RedditWorker: subreddit sentiment polling |
| `altdata_worker.py` | AltDataWorker: fear & greed, funding rates, OI, on-chain |
| `signal_worker.py` | SignalWorker: runs TA engine + signal generator |
| `cleanup_worker.py` | CleanupWorker: database maintenance, old data pruning |
| `position_watchdog.py` | PositionWatchdog: real-time position monitoring with Claude trigger |
| `scanner_worker.py` | ScannerWorker: market universe scanning |
| `regime_worker.py` | RegimeWorker: market regime detection |
| `strategy_worker.py` | StrategyWorker: 4-layer strategy pipeline execution |
| `discovery_worker.py` | DiscoveryWorker: pattern discovery + strategy generation |
| `live_monitor_worker.py` | LiveMonitorWorker: real-time pattern occurrence tracking |
| `backtest_worker.py` | BacktestWorker: backtesting generated strategies |
| `trial_monitor_worker.py` | TrialMonitorWorker: paper trading trial evaluation |
| `allocation_worker.py` | AllocationWorker: daily risk budget management |
| `optimization_worker.py` | OptimizationWorker: weekly portfolio rebalancing |
| `telegram_bot_worker.py` | TelegramBotWorker: runs the interactive Telegram bot |
| `price_alert_worker.py` | PriceAlertWorker: checks and triggers user price alerts |
| `scheduled_report_worker.py` | ScheduledReportWorker: sends scheduled reports/briefings |
| `enforcer_worker.py` | EnforcerWorker: runs performance enforcer checks |

### `scripts/` -- Operational Scripts
| File | Description |
|------|-------------|
| `setup.sh` | Initial project setup (venv, deps, dirs, .env template) |
| `install_services.sh` | Install systemd service files |
| `uninstall_services.sh` | Remove systemd service files |
| `start_all.sh` | Start all systemd services |
| `stop_all.sh` | Stop all systemd services |
| `restart_all.sh` | Restart all systemd services |
| `status.sh` | Show status of all services |
| `backup.sh` | Backup database + config + logs |
| `restore.sh` | Restore from backup |
| `log_viewer.sh` | Tail/view log files |
| `health_check.py` | Python health check script |
| `monitor.py` | Live monitoring dashboard |
| `verify_integration.py` | Integration test suite |
| `force_trade.py` | Force a test trade (development tool) |

### `systemd/` -- Service Definitions
| File | Description |
|------|-------------|
| `trading-workers.service` | Background workers (MemoryMax=400M, CPUQuota=80%) |
| `trading-brain.service` | Claude Brain (depends on workers, MemoryMax=200M) |
| `trading-mcp-sse.service` | MCP SSE server for web access |
| `trading-backup.service` | Scheduled backup service |

### `tests/` -- 94 Test Files
Tests mirror the source tree structure with `test_` prefix. Includes unit tests for all core modules, integration tests for service interactions, and mock-based tests for external APIs.

---

## 3. Configuration

All configuration lives in `config.toml` with environment variable overrides via `.env`.

### [general]
| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | `"paper"` | Trading mode: `"paper"` (testnet) or `"live"` (mainnet) |
| `timezone` | `"UTC"` | Display timezone (internal always UTC) |
| `log_level` | `"INFO"` | Minimum log level: DEBUG, INFO, WARNING, ERROR |
| `log_dir` | `"data/logs"` | Directory for rotated log files |

### [bybit]
| Setting | Default | Description |
|---------|---------|-------------|
| `testnet` | `true` | Use Bybit testnet (paper trading) |
| `default_symbols` | `["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT"]` | Default tracked symbols |
| `rate_limit_per_second` | `10` | Max REST API calls per second |
| `ws_ping_interval` | `20` | WebSocket ping interval (seconds) |
| `ws_reconnect_delay` | `5` | Reconnect delay on WS disconnect |
| `recv_window` | `5000` | Order receive window (ms) |

Env overrides: `BYBIT_API_KEY`, `BYBIT_API_SECRET`

### [finnhub]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable Finnhub news integration |
| `rate_limit_per_minute` | `60` | Free tier rate limit |
| `news_categories` | `["crypto","general"]` | Categories to fetch |
| `max_articles_per_fetch` | `50` | Max articles per poll |

Env override: `FINNHUB_API_KEY`

### [reddit]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable Reddit sentiment |
| `subreddits` | `["cryptocurrency","bitcoin","ethtrader","CryptoMarkets","solana"]` | Monitored subreddits |
| `max_posts_per_sub` | `25` | Posts per subreddit per poll |
| `min_score` | `10` | Minimum post score threshold |

Env overrides: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`

### [altdata]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable alt data collection |
| `fear_greed_interval` | `3600` | Fear & Greed poll interval (seconds) |
| `funding_rate_interval` | `300` | Funding rate poll interval |
| `open_interest_interval` | `600` | OI poll interval |

### [database]
| Setting | Default | Description |
|---------|---------|-------------|
| `path` | `"data/trading.db"` | SQLite database path |
| `wal_mode` | `true` | WAL mode for concurrent reads |
| `pool_size` | `5` | Future PostgreSQL pool size |
| `query_timeout` | `30` | Query timeout (seconds) |
| `vacuum_interval` | `24` | Auto-vacuum interval (hours) |

### [workers]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable background workers |
| `market_data_interval` | `60` | Market data polling (seconds) |
| `news_interval` | `300` | News polling (5 min) |
| `reddit_interval` | `600` | Reddit polling (10 min) |
| `altdata_interval` | `300` | Alt data polling (5 min) |
| `health_check_interval` | `60` | Worker health check (1 min) |
| `max_consecutive_failures` | `5` | Max failures before worker stops |
| `restart_delay` | `10` | Base restart delay (seconds, exponential backoff) |

### [brain]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable Claude Brain |
| `analysis_interval` | `900` | Scheduled analysis interval (15 min) |
| `signal_triggered` | `true` | Trigger on strong signals |
| `min_signal_confidence` | `0.45` | Minimum confidence to act |
| `max_calls_per_hour` | `30` | Rate limit for Claude calls |
| `model` | `"claude-sonnet-4-20250514"` | Claude model |
| `max_tokens` | `4096` | Max response tokens |
| `temperature` | `0.3` | Lower = more deterministic |

Env override: `ANTHROPIC_API_KEY`

### [risk]
| Setting | Default | Description |
|---------|---------|-------------|
| `max_leverage` | `5` | Maximum leverage multiplier |
| `mandatory_stop_loss` | `true` | **Cannot be disabled** -- every order must have SL |
| `default_stop_loss_pct` | `3.0` | Default SL distance from entry |
| `default_take_profit_pct` | `6.0` | Default TP distance from entry |
| `max_position_size_pct` | `20.0` | Max single position as % of equity |
| `max_open_positions` | `10` | Max concurrent positions |
| `daily_loss_limit_pct` | `10.0` | Daily loss limit (halts trading) |
| `max_total_exposure_pct` | `80.0` | Max total exposure as % of equity |
| `max_drawdown_pct` | `25.0` | Max drawdown from peak (emergency stop) |
| `min_order_value_usdt` | `5.0` | Minimum order value |
| `loss_cooldown_seconds` | `30` | Cooldown after consecutive losses |

### [alerts]
| Setting | Default | Description |
|---------|---------|-------------|
| `telegram_enabled` | `true` | Enable Telegram notifications |
| `alert_levels` | `["WARNING","CRITICAL"]` | Which levels to send |
| `daily_summary` | `true` | Send daily performance summary |
| `max_alerts_per_minute` | `10` | Rate limit |
| `trade_alerts` | `true` | Alert on trade execution |
| `signal_alerts` | `true` | Alert on strong signals |
| `error_alerts` | `true` | Alert on errors |

Env overrides: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### [watchdog]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable position watchdog |
| `check_interval_seconds` | `10` | Position check frequency |
| `loss_warning_pct` | `0.5` | Warn when position loses X% from entry |
| `trailing_loss_pct` | `0.3` | Warn when X% drop from peak unrealized profit |
| `sl_proximity_pct` | `30.0` | Warn when within X% distance to stop-loss |
| `rapid_move_pct` | `0.5` | Warn on X% adverse move in single check |
| `brain_trigger_loss_pct` | `0.8` | Trigger Claude review at X% loss |
| `brain_cooldown_seconds` | `60` | Min time between Brain triggers per symbol |
| `partial_close_pct` | `50.0` | % of position to close on watchdog action |
| `max_brain_calls_per_hour` | `20` | Watchdog Claude call rate limit |

### [scanner]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable market scanner |
| `scan_interval_seconds` | `120` | Scan frequency |
| `min_volume_24h` | `1000000` | Minimum 24h volume (USDT) |
| `max_coins` | `15` | Max coins in active universe |
| `max_spread_pct` | `1.0` | Maximum bid-ask spread |

### [regime]
| Setting | Default | Description |
|---------|---------|-------------|
| `detection_interval_seconds` | `300` | Detection frequency |
| `primary_symbol` | `"BTCUSDT"` | Primary regime indicator |
| `trending_adx_threshold` | `25` | ADX above this = trending |
| `ranging_adx_threshold` | `20` | ADX below this + high choppiness = ranging |
| `volatile_atr_percentile` | `150` | ATR above 150% of normal = volatile |
| `dead_adx_threshold` | `15` | Very low ADX + low volume = dead |

### [strategy_engine]
| Setting | Default | Description |
|---------|---------|-------------|
| `scan_interval_seconds` | `30` | Strategy scan frequency |
| `min_score_threshold` | `55` | Minimum score to pass Layer 2 |
| `min_ensemble_agreement` | `2.5` | Min weighted buy votes for Layer 3 |
| `max_ensemble_opposition` | `2.5` | Max weighted sell votes |
| `max_setups_to_brain` | `5` | Max setups per Brain call |
| `max_brain_calls_per_hour` | `30` | Brain call rate limit |

### [pnl_targets]
| Setting | Default | Description |
|---------|---------|-------------|
| `daily_target_pct` | `10.0` | Daily profit target |
| `protect_threshold_pct` | `7.0` | Switch to protective mode |
| `caution_threshold_pct` | `-3.0` | Switch to cautious mode |
| `survival_threshold_pct` | `-7.0` | Switch to survival mode |
| `halt_threshold_pct` | `-10.0` | Halt all trading |

### [leverage]
| Setting | Default | Description |
|---------|---------|-------------|
| `max_leverage` | `5` | Absolute maximum |
| `tier_1_max` | `5` | BTC/ETH max leverage |
| `tier_2_max` | `5` | Major alts max |
| `tier_3_max` | `4` | Small alts max |
| `volatile_max` | `4` | Volatile regime max |
| `dead_max` | `3` | Dead market max |
| `min_confidence_for_5x` | `0.65` | Confidence needed for 5x |
| `min_confidence_for_4x` | `0.55` | Confidence needed for 4x |

### [optimizer]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable weekly optimizer |
| `run_day` | `"sunday"` | Day to run |
| `weight_adjustment_pct` | `10` | Max weight change per cycle |
| `min_trades_for_optimization` | `20` | Min trades before optimizing |
| `underperform_threshold_pct` | `10` | Disable strategy after X% underperformance |
| `disable_after_weeks` | `3` | Consecutive underperforming weeks to disable |

### [factory]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable strategy factory |
| `discovery_schedule_hour_utc` | `2` | Daily discovery run hour |
| `discovery_lookback_days` | `14` | Data lookback for pattern discovery |
| `min_pattern_occurrences` | `10` | Min occurrences to validate a pattern |
| `min_win_rate` | `0.52` | Min win rate for valid pattern |
| `max_strategies_per_batch` | `10` | Max strategies generated per batch |
| `generation_cost_limit_usd` | `0.20` | Max cost per generation attempt |
| `hot_pattern_threshold_win_rate` | `0.70` | Win rate for emergency generation |
| `emergency_generation_enabled` | `true` | Generate on hot pattern detection |

### [backtesting]
| Setting | Default | Description |
|---------|---------|-------------|
| `initial_capital` | `10000` | Backtest starting capital |
| `commission_pct` | `0.06` | Commission per trade |
| `slippage_pct` | `0.02` | Simulated slippage |
| `walk_forward_enabled` | `true` | Enable walk-forward analysis |
| `train_pct` | `0.70` | Train/test split ratio |
| `monte_carlo_runs` | `1000` | MC simulation iterations |
| `min_trades_to_pass` | `15` | Min trades to pass backtest |
| `min_win_rate` | `0.50` | Min win rate to pass |
| `min_profit_factor` | `1.1` | Min PF to pass |
| `max_drawdown_pct` | `20.0` | Max DD to pass |
| `min_sharpe` | `0.3` | Min Sharpe to pass |
| `max_ruin_probability` | `0.05` | Max ruin prob to pass (5%) |

### [trial]
| Setting | Default | Description |
|---------|---------|-------------|
| `trial_duration_days` | `3` | Paper trading trial length |
| `max_extensions` | `1` | Max trial extensions |
| `trial_position_size_pct` | `50` | Position size during trial (% of normal) |
| `min_trades_for_evaluation` | `5` | Min trades before evaluating |
| `promotion_min_win_rate` | `0.48` | Win rate to promote |
| `promotion_min_pnl` | `0.0` | Min PnL to promote |
| `max_active_strategies` | `80` | Max total active strategies |

### [portfolio]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable portfolio optimizer |
| `kelly_fraction` | `0.40` | Fraction of full Kelly |
| `max_strategy_allocation_pct` | `15.0` | Max allocation to any single strategy |
| `proven_strategies_budget_pct` | `55.0` | Budget for proven strategies |
| `ai_strategies_budget_pct` | `35.0` | Budget for AI-generated strategies |
| `trial_strategies_budget_pct` | `12.0` | Budget for trial strategies |
| `cash_reserve_pct` | `3.0` | Cash reserve |
| `high_correlation_threshold` | `0.7` | Penalize correlated strategies above this |
| `kelly_weight` | `0.30` | Weight of Kelly in final allocation |
| `mean_variance_weight` | `0.40` | Weight of mean-variance |
| `risk_parity_weight` | `0.30` | Weight of risk parity |
| `stress_test_enabled` | `true` | Run stress tests during optimization |

### [telegram_interactive]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable interactive Telegram bot |
| `ai_responses_enabled` | `true` | Allow Claude-powered responses |
| `max_ai_calls_per_hour` | `20` | Rate limit for AI responses |
| `trade_confirmation_required` | `true` | Require button confirm before trades |
| `morning_briefing_enabled` | `true` | Send daily morning briefing |
| `morning_briefing_hour_utc` | `5` | Briefing hour (UTC) |

### [enforcer]
| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable performance enforcer |
| `check_interval_seconds` | `300` | Check frequency (5 min) |
| `min_trades_per_hour` | `50` | Target: minimum trades per hour |
| `min_profit_per_hour_pct` | `10.0` | Target: minimum hourly profit % |
| `min_win_rate` | `0.55` | Target: minimum win rate |
| `min_signals_per_hour` | `100` | Target: minimum signals generated |
| `max_seconds_between_trades` | `180` | Max idle time before forced trade |
| `max_escalation_level` | `5` | Maximum escalation level |
| `force_trade_on_gap` | `true` | Force trades when idle too long |
| `rewards_enabled` | `true` | Track achievements and rewards |

### [mcp]
| Setting | Default | Description |
|---------|---------|-------------|
| `transport` | `"stdio"` | `"stdio"` for Claude Code, `"sse"` for browser |
| `sse_port` | `8080` | SSE server port |
| `sse_auth_required` | `true` | Require auth token for SSE |
| `server_name` | `"trading-intelligence"` | MCP server name |
| `server_version` | `"0.1.0"` | MCP server version |

---

## 4. Database Schema

Schema version 9. 40+ tables organized in layers. All tables use SQLite with WAL mode.

### Market Data Layer

**`klines`** -- OHLCV candlestick data
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| symbol | TEXT | e.g. "BTCUSDT" |
| timeframe | TEXT | e.g. "1", "5", "60", "D" |
| timestamp | TEXT | ISO datetime |
| open, high, low, close | REAL | OHLC prices |
| volume | REAL | Base currency volume |
| turnover | REAL | Quote currency turnover |
| created_at | TEXT | Insert timestamp |
Index: `idx_klines_symbol_tf_ts` on (symbol, timeframe, timestamp DESC)
UNIQUE: (symbol, timeframe, timestamp)

**`ticker_cache`** -- Latest ticker per symbol (PK: symbol)

**`orderbook_snapshots`** -- Orderbook depth snapshots (bids/asks as JSON TEXT)

### Trading Layer

**`orders`** -- All orders placed (PK: order_id TEXT)
| Column | Type | Notes |
|--------|------|-------|
| order_id | TEXT PK | Exchange-assigned |
| symbol, side, order_type | TEXT | Trade params |
| price, qty | REAL | Order details |
| status | TEXT | New, Filled, Cancelled, Rejected |
| filled_qty, avg_fill_price | REAL | Execution details |
| stop_loss, take_profit | REAL | Risk levels |

**`positions`** -- Open positions (PK: symbol TEXT)
Columns: side, size, entry_price, mark_price, unrealized_pnl, realized_pnl, leverage, liquidation_price, stop_loss, take_profit

**`trade_history`** -- Completed trades (PK: trade_id TEXT)
Columns: symbol, side, entry_price, exit_price, qty, pnl, pnl_pct, strategy, signal_confidence, entry_time, exit_time

**`account_snapshots`** -- Periodic equity snapshots
Columns: total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct

### Intelligence Layer

**`news_articles`** (PK: id TEXT) -- Finnhub articles with sentiment_score
**`reddit_posts`** (PK: id TEXT) -- Reddit posts with sentiment_score, symbols_mentioned
**`aggregated_sentiment`** -- Combined sentiment per symbol: overall_score, level, news_score, reddit_score, fear_greed_value, momentum
**`economic_calendar`** -- Upcoming economic events with impact level
**`fear_greed_index`** -- Crypto Fear & Greed readings (0-100)
**`funding_rates`** -- Perpetual funding rates per symbol
**`open_interest`** -- Open interest values per symbol
**`signals`** -- Generated trading signals: signal_type, confidence, source, components JSON

### Learning Layer

**`strategy_performance`** -- Per-strategy stats: total_trades, win_rate, avg_pnl, sharpe_ratio, profit_factor. UNIQUE(strategy, symbol, timeframe)
**`signal_accuracy`** -- Signal outcome tracking: predicted vs actual direction, price after 1h/4h/24h
**`pattern_log`** -- Pattern detection log with context and outcomes
**`brain_decisions`** -- Every Claude Brain call: prompt_hash, market_state, response, decision, outcome, tokens, cost, trigger

### Strategy Engine Layer (v4)

**`active_universe`** (PK: symbol) -- Scanner's active coin universe with opportunity_score, volume, tier
**`regime_history`** -- Historical regime classifications with ADX/ATR/choppiness metrics
**`strategy_trades`** -- Trades with full strategy context: score, ensemble_strength, votes, leverage, regime
**`ensemble_votes`** -- Individual strategy votes on each setup
**`strategy_params`** -- Optimizer-managed parameters per strategy (PK: strategy_name, param_name)
**`daily_pnl`** (PK: date) -- Daily performance: starting/ending equity, realized PnL, trades, target_hit, halted

### Strategy Factory Layer (v5)

**`discovered_patterns`** (PK: id TEXT) -- Patterns found by 7 analyzers: conditions, win_rate, profit_factor, statistical_significance
**`generated_strategies`** (PK: id TEXT) -- AI-generated strategy code: pattern_id, code, validation status, cost
**`pattern_occurrences`** -- Real-time pattern detections with price outcomes
**`strategy_code_history`** -- Version history of generated strategy code

### Backtesting + Lifecycle Layer (v6)

**`backtest_results`** (PK: id TEXT) -- Full backtest results: win_rate, PF, Sharpe, Sortino, Calmar, walk-forward efficiency, MC probabilities, grade (A+ to F)
**`backtest_trades`** -- Individual backtest trades with regime, hour, day metadata
**`strategy_lifecycle`** -- State transition log: from_status -> to_status with reason
**`trial_performance`** -- Daily trial strategy metrics: trades, wins, PnL, drawdown

### Portfolio Optimizer Layer (v7)

**`portfolio_allocations`** (PK: strategy_name) -- Current allocations: Kelly %, allocated %, max position USD, risk contribution
**`correlation_matrix`** -- Pairwise strategy correlations. UNIQUE(strategy_a, strategy_b, period_days)
**`risk_budget_log`** -- Daily risk budget snapshots: total, proven/AI/trial breakdown
**`rebalance_history`** -- Rebalance events: old/new allocations, reason, approved_by
**`stress_test_results`** -- Scenario results: portfolio impact, survival, margin call risk
**`performance_attribution`** -- Period performance breakdown by strategy/category with regime/timing/sizing factors

### Interactive Telegram Layer (v8)

**`price_alerts`** (PK: id TEXT) -- User price alerts: symbol, condition, target_price, triggered
**`trade_journal`** (PK: id TEXT) -- User journal entries with mood tracking
**`scheduled_reports`** (PK: id TEXT) -- Scheduled report configurations
**`conversation_log`** -- Chat history for AI context

### Performance Enforcer Layer (v9)

**`hourly_performance`** -- Hourly metrics: grade, trades, profit, win_rate, escalation level, signals, rewards

### System

**`schema_version`** (PK: version INTEGER) -- Current schema version (9)
**`user_preferences`** (PK: key TEXT) -- Key-value user settings
**`watchlists`** -- Named symbol watchlists
**`active_strategies`** -- Strategy activation per symbol
**`session_log`** -- System event log

---

## 5. Core Types

### Enums (all inherit `str, Enum` for JSON compatibility)

```python
class Side(str, Enum):         BUY = "Buy", SELL = "Sell"
class OrderType(str, Enum):    MARKET, LIMIT, STOP_MARKET, STOP_LIMIT, TAKE_PROFIT
class OrderStatus(str, Enum):  NEW, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED
class TimeFrame(str, Enum):    M1="1", M5="5", M15="15", M30="30", H1="60", H4="240", D1="D", W1="W"
class SignalType(str, Enum):   STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
class SentimentLevel(str, Enum): VERY_BULLISH, BULLISH, NEUTRAL, BEARISH, VERY_BEARISH
class TradingMode(str, Enum):  PAPER, LIVE
class WorkerStatus(str, Enum): RUNNING, STOPPED, ERROR, RESTARTING
class AlertLevel(str, Enum):   INFO, WARNING, CRITICAL
class MarketRegime(str, Enum): TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE, DEAD
```

### Dataclasses (all extend SerializableMixin with to_dict/from_dict)

| Dataclass | Key Fields |
|-----------|-----------|
| `OHLCV` | symbol, timeframe, timestamp, open, high, low, close, volume, turnover |
| `Ticker` | symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct |
| `Order` | order_id, symbol, side, order_type, price, qty, status, stop_loss, take_profit |
| `Position` | symbol, side, size, entry_price, mark_price, unrealized_pnl, leverage, liquidation_price |
| `NewsArticle` | id, headline, source, url, summary, sentiment_score, symbols, category |
| `RedditPost` | id, subreddit, title, score, num_comments, upvote_ratio, sentiment_score, symbols_mentioned |
| `Signal` | symbol, signal_type, confidence (0-1), source, components dict, reasoning |
| `TradeRecord` | trade_id, symbol, side, entry/exit prices, qty, pnl, pnl_pct, strategy |
| `AccountInfo` | total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct |
| `FearGreedData` | value (0-100), classification |
| `FundingRate` | symbol, funding_rate, next_funding_time, predicted_rate |
| `BrainDecision` | id, action (buy/sell/hold/close), symbol, confidence, order_type, reasoning, risk_notes |
| `WatchdogDecision` | id, action (hold/tighten_stop/partial_close/full_close), symbol, new_stop_loss |

### Strategy Pipeline Types

| Type | Layer | Key Fields |
|------|-------|-----------|
| `RawSignal` | 1 (Scan) | strategy_name, symbol, direction, entry_price, SL, TP, conditions_met |
| `ScoredSetup` | 2 (Score) | raw_signal, base/confluence/context/quality scores, total_score (0-100), grade |
| `EnsembleVote` | 3 | strategy_name, vote (BUY/SELL/NEUTRAL), confidence, weight |
| `EnsembleResult` | 3 (Consensus) | scored_setup, votes, buy/sell/neutral weighted counts, consensus_strength, passed |
| `TradeDecision` | 4 (Brain) | ensemble_result, action (execute/skip/modify), leverage, SL, TP levels, position_size_pct |

---

## 6. Trading Services

### BybitClient (`src/trading/client.py`)
Central REST API wrapper around `pybit.unified_trading.HTTP`.

- **Safety assertion**: Prevents accidental live trading when `testnet=false` but `mode != "live"`
- **`call(method, **kwargs)`**: Runs any pybit method in a thread (`asyncio.to_thread`), with `@retry(3)`, `@rate_limit(10/s)`, `@timed`
- **Response handling**: Translates retCode into typed exceptions (BybitAPIError, RateLimitError, AuthenticationError)

### MarketService (`src/trading/services/market_service.py`)
| Method | Description | Returns |
|--------|-------------|---------|
| `get_ticker(symbol)` | Current price, bid/ask, 24h stats | `Ticker` |
| `get_tickers(symbols)` | Batch ticker fetch | `list[Ticker]` |
| `get_klines(symbol, interval, limit)` | OHLCV candles (max 200) | `list[OHLCV]` |
| `get_orderbook(symbol, depth)` | Bid/ask depth | `dict` |
| `get_recent_trades(symbol, limit)` | Public trade history | `list[dict]` |
| `get_24h_stats(symbol)` | 24-hour statistics | `dict` |

### OrderService (`src/trading/services/order_service.py`)
| Method | Description |
|--------|-------------|
| `place_order(symbol, side, order_type, qty, price, stop_loss, take_profit, leverage)` | Full validation -> exchange submission |
| `modify_order(symbol, order_id, qty, price)` | Amend open order |
| `cancel_order(symbol, order_id)` | Cancel specific order |
| `cancel_all_orders(symbol)` | Cancel all open orders |
| `get_open_orders(symbol)` | List open orders |
| `get_order_history(symbol, limit)` | Historical orders |

Pre-order validations: symbol support, mandatory stop-loss, leverage caps, instrument precision rounding, min/max quantity checks.

### PositionService (`src/trading/services/position_service.py`)
| Method | Description |
|--------|-------------|
| `get_positions(symbol)` | All open positions (size > 0 only) |
| `get_position(symbol)` | Single position or None |
| `close_position(symbol)` | Close via opposite market order, creates TradeRecord |
| `reduce_position(symbol, qty)` | Partial close with reduceOnly=True |
| `close_all_positions()` | Emergency close all |
| `set_leverage(symbol, leverage)` | Set position leverage |
| `set_stop_loss(symbol, stop_loss)` | Update position stop-loss |
| `set_take_profit(symbol, take_profit)` | Update position take-profit |
| `get_pnl_summary()` | Aggregate PnL across all positions |

### AccountService (`src/trading/services/account_service.py`)
| Method | Returns |
|--------|---------|
| `get_wallet_balance()` | `AccountInfo` with equity, balance, margin, PnL |
| `get_available_balance()` | `float` USDT available |
| `get_equity()` | `float` total equity |
| `get_margin_usage()` | `dict` with used/free margin and ratio |

---

## 7. Analysis Engine

### TAEngine (`src/analysis/engine.py`)

The engine takes OHLCV data (minimum 50 candles) and produces a comprehensive analysis report with zero API calls -- pure computation on numpy arrays.

**`analyze(candles, symbol, timeframe, limit)`** returns:

```python
{
    "symbol": "BTCUSDT",
    "timeframe": "60",
    "candles_analyzed": 200,
    "current_price": 67543.21,
    "trend": { ... },        # All trend indicators
    "momentum": { ... },     # All momentum oscillators
    "volatility": { ... },   # All volatility measures
    "volume": { ... },       # All volume indicators
    "patterns": {
        "candlestick": [...], # Detected candlestick patterns
        "chart": [...]        # Detected chart patterns
    },
    "support_resistance": { "support_levels": [...], "resistance_levels": [...] },
    "overall": {
        "signal": "buy",       # SignalType value
        "score": 65.5,         # -100 to +100
        "confidence": 0.72,    # 0 to 1
        "components": { ... }  # Per-category scores
    }
}
```

### Trend Indicators (`src/analysis/indicators/trend.py`)
| Function | Description |
|----------|-------------|
| `sma(close, period)` | Simple Moving Average |
| `ema(close, period)` | Exponential Moving Average |
| `wma(close, period)` | Weighted Moving Average |
| `dema(close, period)` | Double EMA |
| `tema(close, period)` | Triple EMA |
| `macd(close, fast, slow, signal)` | MACD line, signal line, histogram |
| `adx(high, low, close, period)` | ADX, +DI, -DI |
| `supertrend(high, low, close, period, mult)` | Supertrend line and direction |
| `ichimoku(high, low, close)` | Tenkan, Kijun, Senkou A/B, Chikou |
| `parabolic_sar(high, low, close)` | Parabolic SAR |
| `linear_regression(close, period)` | Linear regression line |

### Momentum Indicators (`src/analysis/indicators/momentum.py`)
| Function | Description |
|----------|-------------|
| `rsi(close, period)` | Relative Strength Index (Wilder smoothing) |
| `stochastic(high, low, close, k, d)` | Stochastic %K, %D |
| `stochastic_rsi(close)` | Stochastic RSI |
| `cci(high, low, close, period)` | Commodity Channel Index |
| `williams_r(high, low, close, period)` | Williams %R |
| `roc(close, period)` | Rate of Change |
| `momentum_indicator(close, period)` | Raw momentum |
| `awesome_oscillator(high, low)` | Awesome Oscillator |
| `tsi(close)` | True Strength Index |
| `ultimate_oscillator(high, low, close)` | Ultimate Oscillator |

### Volatility Indicators (`src/analysis/indicators/volatility.py`)
| Function | Description |
|----------|-------------|
| `bollinger_bands(close, period, std)` | Upper, middle, lower, bandwidth |
| `atr(high, low, close, period)` | Average True Range |
| `natr(high, low, close, period)` | Normalized ATR (% of price) |
| `keltner_channels(high, low, close)` | Upper, middle, lower |
| `donchian_channels(high, low, period)` | Upper, middle, lower |
| `historical_volatility(close, period)` | Annualized HV |
| `choppiness_index(high, low, close)` | Choppiness Index (0-100) |

### Volume Indicators (`src/analysis/indicators/volume.py`)
| Function | Description |
|----------|-------------|
| `obv(close, volume)` | On-Balance Volume |
| `vwap(high, low, close, volume)` | Volume-Weighted Average Price |
| `mfi(high, low, close, volume, period)` | Money Flow Index |
| `ad_line(high, low, close, volume)` | Accumulation/Distribution |
| `chaikin_money_flow(high, low, close, volume)` | Chaikin Money Flow |
| `force_index(close, volume, period)` | Force Index |
| `volume_sma(volume, period)` | Volume moving average |

### Pattern Detection

**Candlestick Patterns** (16 patterns):
Bullish: hammer, inverted hammer, bullish engulfing, morning star, three white soldiers, piercing line, dragonfly doji
Bearish: hanging man, shooting star, bearish engulfing, evening star, three black crows, dark cloud cover, gravestone doji
Neutral: doji, spinning top

**Chart Patterns**: double top/bottom, head & shoulders (regular + inverse), ascending/descending/symmetrical triangles, rising/falling wedges

### Overall Signal Scoring
The engine counts bullish vs bearish signals across all indicators:
- **Score**: -100 (all bearish) to +100 (all bullish)
- **Confidence**: Based on indicator agreement (0 to 1)
- **Signal**: Maps score to SignalType enum (strong_buy > buy > neutral > sell > strong_sell)

---

## 8. Strategy System

### Architecture: 4-Layer Pipeline

```
Layer 1: SCAN        Layer 2: SCORE       Layer 3: CONSENSUS    Layer 4: BRAIN
+-----------+        +----------+         +-----------+         +---------+
| 40 strats |------->| TradeScor|-------->| Ensemble  |-------->| Claude  |
| scan each |  Raw   | 0-100    | Scored  | Voter     | Passed  | BrainV2 |
| symbol    |  Signal| A+ to D  | Setup   | All vote  | Setups  | Evaluate|
+-----------+        +----------+         +-----------+         +---------+
                                                                     |
                                                                Trade Decision
                                                                (execute/skip)
```

### BaseStrategy (`src/strategies/base_strategy.py`)

Every strategy implements:

```python
class BaseStrategy(ABC):
    @property
    def name(self) -> str: ...           # e.g. "A1_rsi_reversal"
    @property
    def category(self) -> str: ...       # e.g. "scalping"
    @property
    def applicable_regimes(self) -> list[MarketRegime]: ...
    @property
    def timeframe(self) -> TimeFrame: ...

    async def scan(self, symbol, candles, ticker, ta_data,
                   sentiment_data, altdata) -> RawSignal | None: ...
    def vote(self, symbol, direction, candles, ta_data,
             sentiment_data, altdata) -> tuple[str, float, str]: ...
```

### StrategyRegistry (`src/strategies/registry.py`)
- `register(strategy)` -- Add strategy
- `get_active_for_regime(regime)` -- Filter by regime + enabled status
- `get_by_category(category)` -- Filter by category
- `update_performance(name, pnl_pct, was_win)` -- Track win rate, PF, streak
- `set_enabled(name, enabled)` -- Enable/disable by optimizer

### MarketScanner (`src/strategies/scanner.py`)
Discovers tradeable coins by filtering:
- 24h volume > `min_volume_24h`
- Bid-ask spread < `max_spread_pct`
- Assigns coin tiers: Tier 1 (BTC/ETH), Tier 2 (SOL/XRP/ADA), Tier 3 (others)
- Limits to `max_coins` (15 default)

### RegimeDetector (`src/strategies/regime.py`)
Uses BTC (primary symbol) H1 data to classify market:

| Regime | Condition | Active Categories |
|--------|-----------|-------------------|
| TRENDING_UP | ADX > 25, +DI > -DI | scalping, momentum, advanced, predatory, cross_market, time_based, ai_enhanced |
| TRENDING_DOWN | ADX > 25, -DI > +DI | Same as above |
| RANGING | ADX < 20, choppiness > 60 | scalping, mean_reversion, funding_arb, advanced, microstructure, time_based, ai_enhanced |
| VOLATILE | ATR > 150% of normal | scalping, sentiment, predatory, microstructure, time_based, ai_enhanced |
| DEAD | ADX < 15, volume < 50% of normal | funding_arb, microstructure |

### TradeScorer (`src/strategies/scorer.py`) -- Layer 2

Four scoring components (total 0-100):

| Component | Points | What It Measures |
|-----------|--------|-----------------|
| Base | 0-40 | Conditions strength from the strategy's scan |
| Confluence | 0-25 | Multiple indicator agreement (RSI + MACD + Supertrend + ...) |
| Context | 0-20 | Higher TF alignment, sentiment, F&G, funding, regime fit |
| Quality | 0-15 | Spread, volume, S/R proximity, clean candle structure |

Grades: A+ (75+), A (65+), B (55+), C (45+), D (<45)

### EnsembleVoter (`src/strategies/ensemble.py`) -- Layer 3

- Polls all active strategies (except the originator) for BUY/SELL/NEUTRAL vote
- Each vote is weighted by the strategy's historical win rate
- Consensus strengths: STRONG (>5 weighted agreement), GOOD (>3), WEAK (>1), CONFLICT
- Must pass: `buy_votes >= min_ensemble_agreement` AND `sell_votes <= max_ensemble_opposition`

### DailyPnLManager (`src/strategies/pnl_manager.py`)

7 operational modes based on daily P&L:

| Mode | PnL Threshold | Behavior |
|------|--------------|----------|
| TARGET_HIT | > daily_target_pct (10%) | Protect profits, tighten SL, reduce new trades |
| PROTECT | > protect_threshold (7%) | Higher score thresholds, reduce leverage |
| NORMAL | Between -3% and +7% | Standard operation |
| CAUTION | < caution_threshold (-3%) | Higher confidence required, fewer positions |
| SURVIVAL | < survival_threshold (-7%) | Only A+ setups, tier 1 coins, 1x leverage |
| HALTED | < halt_threshold (-10%) | No new trades, close losers |

### SmartLeverage (`src/strategies/smart_leverage.py`)

Dynamic leverage 1-5x based on:
1. **Confidence**: <0.55 = max 2x, <0.65 = max 3x, <0.9 = max 4x
2. **Coin tier**: Tier 1 (BTC/ETH) up to 5x, Tier 3 (small alts) capped at 4x
3. **Regime**: Volatile -> max 4x, Dead -> max 3x
4. **Ensemble strength**: WEAK -> reduce by 1
5. **PnL mode**: Caution/Survival -> reduce further

### All 41 Strategies

| ID | Name | Category | Description |
|----|------|----------|-------------|
| A1 | RSIReversalScalp | scalping | RSI oversold/overbought reversal on M5 |
| A2 | VWAPBounceScalp | scalping | Price bouncing off VWAP |
| A3 | BBSqueezeScalp | scalping | Bollinger Band squeeze breakout |
| A4 | EMACrossoverMomentum | scalping | EMA 12/26 crossover on M5 |
| B1 | VolumeBreakout | momentum | High-volume breakout above resistance |
| B2 | SupertrendFollower | momentum | Supertrend direction change |
| B3 | IchimokuBreakout | momentum | Ichimoku cloud breakout |
| B4 | DoubleBottomTop | momentum | Double bottom/top pattern completion |
| C1 | BBMeanReversion | mean_reversion | Price returning to BB middle from extremes |
| C2 | RSIDivergence | mean_reversion | Price/RSI divergence |
| D1 | FundingRateFade | funding_arb | Fade extreme funding rates |
| D2 | OIDivergence | funding_arb | Open interest vs price divergence |
| E1 | FearGreedExtreme | sentiment | Trade extreme Fear & Greed readings |
| E2 | NewsBreakout | sentiment | Trade on high-impact news |
| E3 | SentimentMomentum | sentiment | Follow strong sentiment momentum |
| F1 | SupportResistanceBounce | advanced | Trade S/R level bounces |
| F2 | MultiTFAlignment | advanced | Multi-timeframe trend alignment |
| F3 | LiquidationHunt | advanced | Trade liquidation cascade zones |
| F4 | GridRecovery | advanced | Grid-based recovery from losses |
| G1 | StopHuntSniper | predatory | Detect and trade stop hunts |
| G2 | RetailSentimentFade | predatory | Fade extreme retail sentiment |
| G3 | LiquidationFrontrunner | predatory | Front-run liquidation cascades |
| G4 | WhaleShadow | predatory | Follow whale order flow |
| H1 | FundingPrediction | microstructure | Predict funding rate changes |
| H2 | SpreadBasisExploit | microstructure | Exploit spread/basis anomalies |
| H3 | VolatilitySwitch | microstructure | Trade volatility regime changes |
| H4 | OrderFlowImbalance | microstructure | Order flow imbalance detection |
| I1 | KillZoneTrading | time_based | Trade London/NY session opens |
| I2 | WeekendGapExploit | time_based | Trade weekend gaps |
| I3 | OptionsExpiryPlay | time_based | Trade options expiry volatility |
| I4 | HourlyCloseMomentum | time_based | Hourly candle close momentum |
| J1 | BTCDominanceRotation | cross_market | BTC dominance rotation signals |
| J2 | CorrelationBreakdown | cross_market | Trade correlation breakdowns |
| J3 | CrossExchangeLag | cross_market | Cross-exchange price lag |
| J4 | AltcoinBetaAmplification | cross_market | Altcoin beta amplification |
| K1 | ClaudeConviction | ai_enhanced | High-conviction Claude Brain signals |
| K2 | PatternMemory | ai_enhanced | Pattern recognition from history |
| K3 | MultiStrategyEnsemble | ai_enhanced | Ensemble of ensemble signals |
| K4 | AdaptiveOptimizer | ai_enhanced | Self-optimizing parameter adjustment |
| X1 | AlwaysTradeStrategy | testnet_only | Testnet kickstart (always generates signals) |

---

## 9. Brain System

### Architecture

```
PromptBuilder -> ClaudeClient -> DecisionParser -> BrainExecutor
      |              |                |                |
 Gathers data   Calls Claude API   Extracts JSON    Places trades
 from DB/svc    with cost tracking  into decision   with safety
```

### ClaudeClient (`src/brain/claude_client.py`)
- Wraps `anthropic.AsyncAnthropic`
- Model: `claude-sonnet-4-20250514` (configurable)
- `send_message(prompt, system_prompt)` -> response dict with text, tokens, cost, model, message_id
- `@retry(2, delay=5s)` on API errors
- Budget enforcement: checks `CostTracker.can_afford_call()` before every call

### CostTracker (`src/brain/cost_tracker.py`)
- Pricing: Input $3.00/M tokens, Output $15.00/M tokens (Claude Sonnet)
- Daily budget: $1.00 (configurable)
- `record_call(input_tokens, output_tokens)` -> cost in USD
- `can_afford_call()` -> estimates max cost of one call, checks against remaining budget
- Auto-resets daily counters at midnight UTC

### DecisionParser (`src/brain/decision_parser.py`)
Extracts JSON from Claude's response using 3 strategies:
1. Direct `json.loads()`
2. Markdown code fence extraction (`\`\`\`json ... \`\`\``)
3. First `{` to last `}` extraction

Builds `BrainDecision` with extra fields: `_limit_price`, `_qty_pct`, `_stop_loss`, `_take_profit`, `_leverage`

Also parses `WatchdogDecision` with actions: hold, tighten_stop, partial_close, full_close

### BrainExecutor (`src/brain/executor.py`)
Pre-execution safety checks:
- Brain enabled in config
- Confidence above minimum
- Stop-loss present (if mandatory)
- Leverage within limits
- Position service and order service available

Execution: buy/sell -> `_place_trade()`, close -> `position_service.close_position()`

### BrainScheduler (`src/brain/scheduler.py`)
Two trigger modes:
1. **Scheduled**: Every `analysis_interval` seconds (default 900 = 15 min)
2. **Signal-triggered**: When strong signals detected (requires `signal_triggered = true`)

Guards: budget check, interval minimum, prompt deduplication (hash comparison)

### BrainV2 (`src/brain/brain_v2.py`)
Enhanced brain for the 4-layer strategy architecture:
- Receives `EnsembleResult` setups from Layer 3
- Batches up to `max_setups_to_brain` setups in a single Claude call
- Returns `TradeDecision` list (execute/skip/modify)
- Uses `SETUP_REVIEW_PROMPT` template

### Prompt Templates

**SYSTEM_PROMPT**: "You are an expert cryptocurrency trading analyst... Respond with ONLY a valid JSON object. Be conservative. Always include stop_loss. Consider ALL data. If uncertain, hold."

**TRADE_DECISION_PROMPT**: Template with placeholders for prices, TA, sentiment, positions, account balance

**POSITION_REVIEW_PROMPT**: Watchdog template with position details, price action, risk metrics

**SETUP_REVIEW_PROMPT**: Strategy setup evaluation with ensemble votes and scoring details

**OPTIMIZATION_REVIEW_PROMPT**: Weekly strategy parameter optimization review

Required JSON response format:
```json
{
    "action": "buy|sell|hold|close",
    "symbol": "BTCUSDT",
    "confidence": 0.85,
    "qty_pct": 5,
    "stop_loss": 66500,
    "take_profit": 69000,
    "leverage": 3,
    "order_type": "Market",
    "reasoning": "...",
    "risk_notes": "..."
}
```

---

## 10. Risk Management

### RiskManager (`src/risk/risk_manager.py`)
Central orchestrator that delegates to specialized components:

```python
class RiskManager:
    position_sizer: PositionSizer      # How much to trade
    stop_loss_calc: StopLossCalculator  # Where to place SL/TP
    portfolio: PortfolioAnalyzer        # Portfolio-level risk
    drawdown: DrawdownTracker           # Circuit breakers
    validator: TradeValidator           # Pre-trade validation
```

### TradeValidator (`src/risk/validators.py`)
15+ validation checks with absolute hard limits that cannot be overridden:

| Check | Description | Hard Limit |
|-------|-------------|-----------|
| Symbol | Must be in SUPPORTED_SYMBOLS | -- |
| Quantity | Must be positive, within instrument min/max | -- |
| Notional | Must exceed minimum order value | -- |
| Price | Must be positive (for limit orders) | -- |
| Stop-loss | **Mandatory** -- cannot be disabled | -- |
| SL sanity | Must be correct side, 0.1% - 20% distance | -- |
| TP sanity | Must be correct side | -- |
| Leverage | Within config max | **Absolute max: 10x** |
| Position count | Within max_open_positions | -- |
| Position size | Within max_position_size_pct | **Absolute max: 25%** |
| Exposure | Total exposure within max_total_exposure_pct | -- |
| Daily loss | Within daily_loss_limit_pct | **Absolute max: 10%** |
| Drawdown | Within max_drawdown_pct | -- |
| Duplicate | No existing position in same symbol/direction | -- |
| Cooldown | After consecutive losses | -- |

### PositionSizer (`src/risk/position_sizer.py`)

Three sizing methods:

1. **Fixed Percentage**: Risk X% of equity per trade. Position = risk_amount / (stop_distance / entry_price). Capped at max_position_size_pct.

2. **ATR-based**: Uses ATR for stop distance calculation, adjusts position size accordingly.

3. **Kelly Criterion**: Uses win rate and avg win/loss to compute optimal fraction.

All methods: round to instrument step size, enforce maximum position size cap.

### StopLossCalculator (`src/risk/stop_loss.py`)

Three methods:

1. **Fixed Percentage**: SL = entry * (1 +/- sl_pct/100). Default: 3% SL, 6% TP.
2. **ATR-based**: SL = entry +/- (ATR * multiplier). Default: 2x ATR for SL, 3x for TP.
3. **S/R-based**: Place SL beyond nearest support/resistance level.

Returns risk/reward ratio with every calculation.

### DrawdownTracker (`src/risk/drawdown.py`)

Circuit breakers (checked before every trade):

| Breaker | Condition | Action |
|---------|-----------|--------|
| Daily loss limit | Today's realized PnL < -daily_loss_limit_pct | Halt trading for the day |
| Max drawdown | Current equity < peak * (1 - max_drawdown_pct/100) | Halt trading indefinitely |
| Consecutive losses | 5+ consecutive losses | Cooldown period |
| Loss cooldown | Recent loss within loss_cooldown_seconds | Wait |

Auto-resets daily counters at midnight UTC. Max drawdown halt requires manual reset.

### PortfolioAnalyzer (`src/risk/portfolio.py`)
- Total exposure calculation (sum of all positions as % of equity)
- Correlation analysis between positions
- Concentration risk (single asset, single direction)
- Margin usage and leverage analysis

---

## 11. Factory System

### Pipeline

```
Discovery -> Generation -> Validation -> Backtest -> Trial -> Promotion
    |            |             |            |          |         |
 7 analyzers  Claude AI    Syntax/Safety  Walk-fwd  Paper    Production
 scan data    writes code  + interface    + Monte   trading  deployment
                                          Carlo
```

### PatternDiscoverer (`src/factory/discoverer.py`)

Runs 7 specialized analyzers across all symbols:

| Analyzer | What It Finds |
|----------|--------------|
| SingleVariableAnalyzer | Single indicator extremes (e.g., RSI < 20 -> bounce) |
| MultiVariableAnalyzer | Multi-condition patterns (e.g., RSI + volume + BB) |
| SequentialAnalyzer | Sequence patterns (e.g., A then B within N candles) |
| CrossAssetAnalyzer | Cross-asset relationships (e.g., BTC leads ETH by 5 min) |
| TemporalAnalyzer | Time-based patterns (e.g., 2 AM UTC rallies) |
| NewsReactiveAnalyzer | Post-event price reactions |
| MicroPatternAnalyzer | Microstructure patterns (order flow, funding spikes) |

Validation requirements: min occurrences, min win rate, min profit factor, statistical significance (p < 0.05).

### StrategyGenerator (`src/factory/generator.py`)
- Uses Claude to generate Python strategy code from a DiscoveredPattern
- Generates a class that extends BaseStrategy with proper scan() and vote() methods
- Retries up to `max_generation_retries` (3) times
- Cost-limited per generation attempt ($0.20 default)

### CodeValidator (`src/factory/validator.py`)
Three validation layers:
1. **Syntax**: Python AST parsing, no syntax errors
2. **Safety**: No imports of os/sys/subprocess, no network calls, no file I/O
3. **Interface**: Implements BaseStrategy properly, has name/category/scan/vote

### BacktestEngine (`src/factory/backtester.py`)
Event-driven backtesting with realistic costs:
- Commission: 0.06% per trade
- Slippage: 0.02%
- Funding rate: 0.01%

### Metrics (`src/factory/metrics.py`)
Computed metrics: win rate, profit factor, total return %, max drawdown %, Sharpe ratio, Sortino ratio, Calmar ratio

### WalkForwardAnalyzer (`src/factory/walk_forward.py`)
- Splits data into train (70%) and test (30%)
- Strategy must perform on test data at efficiency >= `min_walk_forward_efficiency` (40%)
- Prevents overfitting

### MonteCarloSimulator (`src/factory/monte_carlo.py`)
- 1000 simulations (configurable)
- Randomly reorders trades to compute probability distributions
- Outputs: probability of profit, probability of ruin (< 5% required)

### StrategyLifecycleManager (`src/factory/lifecycle.py`)

Valid state transitions:
```
generated -> validated -> backtested_pass -> trial_active -> promoted
                     \-> backtested_fail -> killed
                                           trial_active -> trial_extended -> promoted
                                                       \-> killed
                                           promoted -> demoted -> promoted (or killed)
                                           killed -> generated (quarterly revival)
```

### TrialManager (`src/factory/trial_manager.py`)
Paper trading trial evaluation:
- Duration: 3 days (configurable)
- Position size: 50% of normal
- Promotion criteria: min_trades (5), win_rate (48%), positive PnL, max drawdown (10%)
- Extension: 1 allowed (7 days)

---

## 12. Portfolio Optimizer

### KellyCalculator (`src/portfolio/kelly.py`)
- **Full Kelly**: f* = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
- **Fractional Kelly**: Full Kelly * fraction (default 40%)
- **Dynamic Kelly**: Adjusted for losing streaks (-30%), drawdown (-20% to -40%), winning streaks (+20%)

### CorrelationTracker (`src/portfolio/correlation.py`)
- Computes pairwise correlation of daily returns between strategies
- Lookback: 30 days
- High correlation (> 0.7) results in allocation penalty

### DynamicAllocator (`src/portfolio/allocator.py`)
Combines three allocation methods:
1. **Kelly** (30% weight): Position sizing based on edge
2. **Mean-variance** (40% weight): Markowitz-style optimization
3. **Risk parity** (30% weight): Equal risk contribution

Category budget constraints:
- Proven strategies: 55% of capital
- AI-generated: 35%
- Trial: 12%
- Cash reserve: 3%

Per-strategy caps: max 15%, min 2% allocation.

### RiskBudgetManager (`src/portfolio/risk_budget.py`)
- Daily risk budget: 8% of equity
- Drawdown reduction: at 8% drawdown -> budget * 0.7, at 15% drawdown -> budget * 0.4
- Category-level budget enforcement

### StressTester (`src/portfolio/stress_test.py`)
Scenarios tested:
| Scenario | Description |
|----------|-------------|
| Flash crash | -20% in 1 hour |
| Black swan | -40% in 24 hours |
| Correlation spike | All correlations go to 1.0 |
| Liquidity crisis | 50% reduction in volume |
| Funding squeeze | Extreme funding rates |

Outputs: portfolio impact %, loss USD, survival flag, margin call risk.

### PortfolioOptimizer (`src/portfolio/optimizer.py`)
- Runs weekly (Sunday, configurable)
- Rebalances allocations across strategies
- Minimum rebalance change: 2% to avoid churn
- Approval workflow: Claude reviews proposed changes

---

## 13. Telegram Bot

### InteractiveTelegramBot (`src/telegram/bot.py`)
Thin router delegating to 10 handler classes. Uses python-telegram-bot with long-polling.

### Commands and Handlers

| Handler | Commands | Description |
|---------|----------|-------------|
| TradingHandler | `/buy`, `/sell`, `/close`, `/orders` | Trade execution with confirmation buttons |
| PortfolioHandler | `/portfolio`, `/pnl`, `/balance`, `/equity` | Account overview and P&L |
| AnalysisHandler | `/analyze`, `/ta`, `/chart`, `/price` | On-demand technical analysis |
| BrainHandler | `/brain`, `/ask <question>` | Trigger Claude analysis, free-form AI Q&A |
| AlertHandler | `/alert`, `/alerts` | Price alert CRUD (above/below/cross conditions) |
| WatchlistHandler | `/watchlist` | Symbol watchlist management |
| JournalHandler | `/journal` | Trade journal entries with mood tracking |
| ScheduleHandler | `/schedule` | Report scheduling (morning briefing, hourly) |
| SystemHandler | `/status`, `/health`, `/workers`, `/config` | System monitoring |
| EmergencyHandler | `/emergency`, `/closeall`, `/halt`, `/resume` | Emergency controls |

### Features
- **Trade confirmation**: Inline keyboard buttons (Confirm/Cancel) before executing trades
- **AI chat**: Free-form text messages answered by Claude with trading context
- **Morning briefing**: Daily automated market summary at configured hour
- **Price alerts**: Set alerts for price above/below/crossing levels, monitored every 10s
- **Journal**: Timestamped trade journal with mood tracking

### Authentication
- Chat ID-based authorization
- Only configured `TELEGRAM_CHAT_ID` can use the bot
- Unauthorized users get a rejection message

---

## 14. Alert System

### AlertManager (`src/alerts/alert_manager.py`)

Central hub for all notifications. Every component calls AlertManager methods.

| Method | Trigger | Priority |
|--------|---------|----------|
| `send_trade_alert(order, balance)` | Trade executed | INFO |
| `send_position_closed_alert(...)` | Position closed | INFO |
| `send_signal_alert(signal)` | Signal confidence > 0.7 | INFO |
| `send_brain_decision_alert(decision, trigger, cost)` | Every Brain call | INFO |
| `send_error_alert(component, error, severity)` | Errors | WARNING/CRITICAL |
| `send_worker_crash_alert(worker, error, count, max)` | Worker crash | WARNING/CRITICAL |
| `send_risk_warning(type, details)` | Risk limit approach | CRITICAL |
| `send_watchdog_alert(symbol, alerts, position)` | Watchdog detection | WARNING |
| `send_system_startup(mode, symbols, workers)` | System boot | INFO |

### Alert Templates (`src/alerts/templates.py`)
HTML-formatted messages for Telegram with:
- Trade executed: side, symbol, qty, price, SL, TP, balance
- Position closed: entry/exit, PnL, percentage
- Signal detected: type, confidence, components
- Brain decision: action, confidence, reasoning, cost
- Error alert: component, message, severity
- Risk warning: type, details

### AlertThrottle (`src/alerts/throttle.py`)
- **Rate limiting**: Max alerts per rolling hour (default: 600)
- **Deduplication**: SHA256 hash of content, 5-minute window
- **CRITICAL bypass**: CRITICAL alerts always bypass throttle
- **Queuing**: Throttled alerts queued for batch sending later

---

## 15. Workers

### BaseWorker (`src/workers/base_worker.py`)
Abstract base with run loop, error recovery, and heartbeat:
- `tick()` -- subclasses implement one cycle
- Exponential backoff on failure: `restart_delay * 2^(attempt-1)`, capped at 60s
- Max consecutive failures: 5 (configurable), then permanent stop
- Heartbeat log every 5 minutes

### Worker Fleet

| Worker | Interval | Dependencies | Description |
|--------|----------|-------------|-------------|
| PriceWorker | WS streaming | BybitWebSocket | Real-time ticker via WebSocket |
| KlineWorker | 60s | MarketService | OHLCV polling for all symbols/timeframes |
| NewsWorker | 300s | NewsService, CalendarService | Finnhub news + economic calendar |
| RedditWorker | 600s | RedditService | Reddit sentiment polling |
| AltDataWorker | 300s | FearGreed, Funding, OI, OnChain | Alternative data collection |
| SignalWorker | per cycle | TAEngine, Aggregator, SignalGen | TA + signal generation |
| PositionWatchdog | 10s | Position, Market, Claude, Risk | Real-time position monitoring |
| ScannerWorker | 120s | MarketScanner | Active universe discovery |
| RegimeWorker | 300s | RegimeDetector | Market regime classification |
| StrategyWorker | 30s | Registry, Scanner, Regime, Scorer, Ensemble, BrainV2 | 4-layer strategy pipeline |
| DiscoveryWorker | daily (2 AM) | PatternDiscoverer, Generator, Validator | Pattern discovery + code generation |
| LiveMonitorWorker | 300s | LivePatternMonitor | Real-time pattern tracking |
| BacktestWorker | on demand | BacktestEngine, Lifecycle, Trial | Backtest generated strategies |
| TrialMonitorWorker | daily | TrialManager | Evaluate trial strategies |
| AllocationWorker | daily | RiskBudgetManager | Daily risk budget management |
| OptimizationWorker | weekly | PortfolioOptimizer, StressTester | Portfolio rebalancing |
| TelegramBotWorker | continuous | InteractiveTelegramBot | Interactive Telegram bot |
| PriceAlertWorker | 10s | PriceAlertEngine, MarketService | User price alert checking |
| ScheduledReportWorker | per schedule | ScheduledReportEngine | Scheduled report delivery |
| EnforcerWorker | 300s | PerformanceEnforcer | Hourly performance enforcement |
| CleanupWorker | periodic | -- | Database maintenance, old data pruning |

### WorkerManager (`src/workers/manager.py`)
- Creates all services in dependency order
- Instantiates workers with injected dependencies
- Starts all as concurrent asyncio tasks
- Signal handlers (SIGTERM, SIGINT) for graceful shutdown
- One worker crash does not stop others

---

## 16. MCP Tools

43 tools registered across 8 tool modules:

### Trading Tools (12)
| Tool | Description |
|------|-------------|
| `get_account_info` | Wallet balance, equity, margin |
| `get_ticker` | Current price for a symbol |
| `get_tickers` | Prices for multiple symbols |
| `get_klines` | OHLCV candlestick data |
| `get_orderbook` | Orderbook depth |
| `place_order` | Place buy/sell order with SL/TP |
| `modify_order` | Amend order qty/price |
| `cancel_order` | Cancel specific order |
| `cancel_all_orders` | Cancel all open orders |
| `get_open_orders` | List open orders |
| `get_positions` | List open positions |
| `close_position` | Close a position |

### Analysis Tools (5)
| Tool | Description |
|------|-------------|
| `get_technical_analysis` | Full TA report (all indicators + patterns) |
| `get_indicator` | Single indicator value |
| `get_patterns` | Pattern detection results |
| `get_signal` | Current trading signal |
| `get_trade_recommendation` | AI-powered trade recommendation |

### News Tools (4)
| Tool | Description |
|------|-------------|
| `get_latest_news` | Recent crypto news articles |
| `get_news_for_symbol` | News filtered by symbol |
| `search_news` | Keyword news search |
| `get_economic_calendar` | Upcoming economic events |

### Sentiment Tools (5)
| Tool | Description |
|------|-------------|
| `get_reddit_sentiment` | Reddit sentiment for a symbol |
| `get_subreddit_hot` | Hot posts from a subreddit |
| `get_social_buzz` | Social media buzz metrics |
| `get_aggregated_sentiment` | Combined sentiment score |
| `get_sentiment_history` | Historical sentiment data |

### Alt Data Tools (5)
| Tool | Description |
|------|-------------|
| `get_fear_greed_index` | Crypto Fear & Greed Index |
| `get_funding_rates` | Current funding rates |
| `get_open_interest` | Open interest data |
| `get_funding_history` | Historical funding rates |
| `get_market_overview` | Comprehensive market overview |

### Risk Tools (5)
| Tool | Description |
|------|-------------|
| `calculate_position_size` | Optimal position size calculation |
| `get_risk_exposure` | Current portfolio risk exposure |
| `calculate_stop_loss` | SL/TP level recommendations |
| `get_daily_pnl` | Today's P&L summary |
| `get_risk_status` | Overall risk status and circuit breakers |

### Memory Tools (4)
| Tool | Description |
|------|-------------|
| `get_trade_history` | Historical trade records |
| `get_strategy_performance` | Per-strategy performance stats |
| `get_pattern_outcomes` | Pattern detection outcomes |
| `get_brain_decisions` | Historical Brain decisions and costs |

### System Tools (3)
| Tool | Description |
|------|-------------|
| `get_system_status` | Overall system health |
| `get_worker_status` | Worker statuses and uptimes |
| `update_preference` | Update user preferences |

---

## 17. Deployment

### systemd Services

4 service files in `systemd/`:

| Service | Entry Point | Memory Limit | CPU Quota | Restart |
|---------|-------------|-------------|-----------|---------|
| `trading-workers.service` | `workers.py` | 400M | 80% | always (15s) |
| `trading-brain.service` | `brain.py` | 200M | 50% | always (30s) |
| `trading-mcp-sse.service` | `server.py --transport sse` | 200M | 50% | always (15s) |
| `trading-backup.service` | `scripts/backup.sh` | -- | -- | -- |

All services:
- Run as user `inshadaliqbal786`
- Read `.env` via `EnvironmentFile`
- Security: `NoNewPrivileges=true`, `ProtectSystem=strict`, `PrivateTmp=true`
- Stdout/stderr null (app handles own logging)
- Brain depends on workers (`After=trading-workers.service`)

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/setup.sh` | Initial setup: create venv, install deps, create data dirs, .env template |
| `scripts/install_services.sh` | Copy service files to /etc/systemd/system, enable |
| `scripts/uninstall_services.sh` | Stop, disable, remove service files |
| `scripts/start_all.sh` | `systemctl start` all services |
| `scripts/stop_all.sh` | `systemctl stop` all services |
| `scripts/restart_all.sh` | `systemctl restart` all services |
| `scripts/status.sh` | `systemctl status` all services |
| `scripts/backup.sh` | Backup database + config + logs to timestamped archive |
| `scripts/restore.sh` | Restore from backup archive |
| `scripts/log_viewer.sh` | Tail/view rotated log files |
| `scripts/health_check.py` | Python health check (DB, API connectivity, worker status) |
| `scripts/monitor.py` | Live monitoring dashboard |
| `scripts/verify_integration.py` | Integration test suite |
| `scripts/force_trade.py` | Force a test trade (development) |

### Log Files

All logs in `data/logs/` with 10 MB rotation, 7-day retention:
- `mcp.log` -- MCP server logs
- `workers.log` -- All worker logs
- `brain.log` -- Brain/Claude logs
- `general.log` -- Everything else

---

## 18. Data Flows

### Trade Execution Flow

```
1. Strategy scan() detects conditions -> RawSignal
2. TradeScorer scores signal -> ScoredSetup (0-100)
3. EnsembleVoter polls all strategies -> EnsembleResult (consensus)
4. BrainV2 evaluates with Claude -> TradeDecision (execute/skip)
5. SmartLeverage calculates leverage (1-5x)
6. RiskManager.validate_trade() -> pass/fail with issues
7. OrderService.place_order() with SL/TP/leverage
8. Bybit exchange confirms order -> Order ID
9. AlertManager sends trade alert via Telegram
10. TradeRecord saved to trade_history
```

### Signal Generation Flow

```
1. KlineWorker fetches OHLCV for all symbols/timeframes
2. TAEngine.analyze() computes all indicators
3. SentimentAggregator combines news + Reddit + F&G
4. SignalGenerator creates overall signal
5. Signal saved to DB (signals table)
6. If confidence >= min_signal_confidence -> trigger Brain analysis
```

### Position Watchdog Flow

```
Every 10 seconds:
1. Fetch all open positions from exchange
2. For each position:
   a. Check loss from entry -> loss_warning_pct alert
   b. Check drop from peak unrealized -> trailing_loss_pct alert
   c. Check proximity to stop-loss -> sl_proximity_pct alert
   d. Check rapid adverse move -> rapid_move_pct alert
   e. If loss > brain_trigger_loss_pct -> trigger Claude review
3. Claude review returns WatchdogDecision:
   - hold: do nothing
   - tighten_stop: move SL closer
   - partial_close: reduce position by partial_close_pct
   - full_close: close entire position
4. Execute decision through PositionService
5. Alert via Telegram
```

### Strategy Factory Pipeline

```
Daily at 2 AM UTC:
1. PatternDiscoverer runs 7 analyzers on 14-day data
2. Validated patterns saved to discovered_patterns
3. StrategyGenerator uses Claude to write Python code
4. CodeValidator checks syntax, safety, interface
5. BacktestEngine runs walk-forward + Monte Carlo
6. Passing strategies enter trial (paper trading, 3 days)
7. TrialManager evaluates: promote, extend, or kill
8. Promoted strategies join production registry

Real-time:
- LivePatternMonitor tracks pattern occurrences
- Hot patterns (>70% win rate, >3 occurrences) trigger emergency generation
```

---

## 19. Performance Enforcer

### Overview

The Performance Enforcer runs every 5 minutes and monitors hourly performance against aggressive targets. When targets are not met, it escalates through 5 levels of increasing aggression.

### Hourly Targets

| Metric | Target |
|--------|--------|
| Minimum trades per hour | 50 |
| Minimum profit per hour | 10% |
| Minimum win rate | 55% |
| Minimum signals per hour | 100 |
| Minimum setups to Brain per hour | 20 |
| Maximum idle time between trades | 180 seconds |

### Escalation Levels

| Level | Name | Actions |
|-------|------|---------|
| 0 | Normal | Standard operation |
| 1 | Encouraged | Lower score thresholds, increase scan frequency |
| 2 | Aggressive | Accept B-grade setups, reduce ensemble requirements |
| 3 | Urgent | Accept C-grade setups, maximum leverage |
| 4 | Desperate | Accept any signal above minimum confidence |
| 5 | Maximum | Force trades, use all available strategies |

### Forced Trades

When idle time exceeds `max_seconds_between_trades` (180s):
- The enforcer triggers `_force_trade_now()`
- Scans all symbols for the best available setup
- Bypasses normal quality filters
- Places trade immediately

### Rewards System

Achievements tracked:
- Trade milestones (10, 50, 100 trades/hour)
- Profit milestones
- Winning streaks
- Clean hours (no escalation)

### Hourly Report

At the end of each hour, the enforcer:
1. Finalizes hour metrics
2. Assigns a grade (A+ through F)
3. Saves to `hourly_performance` table
4. Resets counters for the next hour

---

## 20. Glossary

### Trading Terms

| Term | Definition |
|------|-----------|
| **Perpetual Futures** | Futures contracts with no expiry date, settled in USDT |
| **Long / Buy** | Buying a contract expecting price to rise |
| **Short / Sell** | Selling a contract expecting price to fall |
| **Leverage** | Multiplier for position size (e.g., 3x means $100 margin controls $300) |
| **Margin** | Collateral required to hold a leveraged position |
| **Liquidation** | Forced position close when margin is insufficient |
| **Stop-Loss (SL)** | Order that closes position at a preset loss level |
| **Take-Profit (TP)** | Order that closes position at a preset profit level |
| **PnL** | Profit and Loss (realized = closed, unrealized = open) |
| **Drawdown** | Decline from peak equity to current equity |
| **Risk/Reward (R:R)** | Ratio of potential profit to potential loss |
| **Slippage** | Difference between expected and actual fill price |
| **Spread** | Difference between best bid and best ask price |
| **Funding Rate** | Periodic payment between longs and shorts (perpetual futures) |
| **Open Interest (OI)** | Total number of outstanding derivative contracts |

### Technical Analysis Terms

| Term | Definition |
|------|-----------|
| **OHLCV** | Open, High, Low, Close, Volume -- one candlestick |
| **SMA/EMA** | Simple/Exponential Moving Average |
| **RSI** | Relative Strength Index (0-100, >70 overbought, <30 oversold) |
| **MACD** | Moving Average Convergence Divergence (trend + momentum) |
| **Bollinger Bands** | Volatility envelope: middle SMA +/- 2 standard deviations |
| **ATR** | Average True Range (volatility measure) |
| **ADX** | Average Directional Index (trend strength, >25 = trending) |
| **Supertrend** | Trend-following overlay based on ATR |
| **VWAP** | Volume-Weighted Average Price |
| **OBV** | On-Balance Volume (cumulative volume flow) |
| **Stochastic** | Momentum oscillator comparing close to recent range |
| **Ichimoku** | Multi-component trend/support/resistance system |
| **Support/Resistance** | Price levels where buying/selling pressure concentrates |

### Crypto-Specific Terms

| Term | Definition |
|------|-----------|
| **Fear & Greed Index** | Market sentiment indicator (0 = Extreme Fear, 100 = Extreme Greed) |
| **Funding Rate** | Payment mechanism to keep perpetual futures price near spot |
| **Whale** | Trader or entity controlling a very large position |
| **Liquidation Cascade** | Chain reaction of forced liquidations driving price further |
| **Kill Zone** | High-activity trading hours (London open, NY open) |
| **Altcoin** | Any cryptocurrency that is not Bitcoin |
| **BTC Dominance** | Bitcoin's market cap as a percentage of total crypto market cap |
| **DeFi** | Decentralized Finance protocols |
| **Testnet** | Simulated exchange environment with fake money for testing |
| **Mainnet** | Real exchange environment with real money |

### System-Specific Terms

| Term | Definition |
|------|-----------|
| **MCP** | Model Context Protocol -- Anthropic's standard for tool-using AI |
| **Brain** | The Claude-powered autonomous trading decision engine |
| **Watchdog** | Position monitoring system that triggers Claude on adverse moves |
| **Registry** | Central repository of all registered trading strategies |
| **Regime** | Current market classification (trending, ranging, volatile, dead) |
| **Ensemble** | Multi-strategy consensus voting mechanism |
| **Factory** | AI-powered system for discovering patterns and generating strategies |
| **Trial** | Paper trading evaluation period for new strategies |
| **Kelly Criterion** | Mathematical formula for optimal bet sizing |
| **Walk-Forward** | Backtest validation: train on past data, test on unseen data |
| **Monte Carlo** | Statistical simulation randomizing trade order to estimate risk |
| **Escalation** | Enforcer's progressive increase in trading aggression |
| **Circuit Breaker** | Safety mechanism that halts trading on excessive losses |

---

*This document was generated from the actual source code of the Trading Intelligence MCP system. All class names, method signatures, configuration values, and database schemas are accurate as of the latest codebase state.*
