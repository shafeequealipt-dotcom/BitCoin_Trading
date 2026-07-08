# Trading Intelligence MCP — Complete System Blueprint

## 1. System Overview

**Project**: Enterprise-grade AI-powered Crypto Trading System with MCP integration
**Python**: 3.11+ | **Schema**: v23 | **Source Files**: ~356 modules across 21 packages
**Architecture**: 3-Layer (Data → Brain → Execution) with DI container

### Entry Points

| Entry | Command | Purpose |
|-------|---------|---------|
| Workers | `python workers.py` | Start all background workers (production) |
| MCP Server | `python server.py` | MCP tools for Claude Code (stdio) or browser (SSE) |
| Brain v1 | `python brain.py --once` | Single Claude analysis (legacy) |

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ (async/await throughout) |
| Database | SQLite (WAL mode, aiosqlite) |
| Exchange | Bybit (pybit REST + WebSocket) |
| AI - Strategic | Claude Code CLI ($0, Max subscription) |
| AI - Optimization | Qwen/DeepSeek V3 via OpenRouter |
| AI - Analysis | DeepSeek V3 via OpenRouter (TIAS) |
| Logging | Loguru (structured, context-aware) |
| Telegram | python-telegram-bot (interactive terminal) |
| MCP | mcp>=1.0.0 (stdio + SSE transport) |
| Math | NumPy (indicators, volume profile, ring buffers) |

---

## 2. Architecture — 3-Layer Model

```
                    ┌─────────────────────────────────────────┐
                    │            LAYER 1: DATA                │
                    │  KlineWorker (45s) | PriceWorker (45s)  │
                    │  NewsWorker (300s) | AltDataWorker(300s) │
                    │  ScannerWorker(300s)| RegimeWorker(600s) │
                    │  StructureWorker(60s)| SignalWorker(120s)│
                    └─────────────────┬───────────────────────┘
                                      │ requires Layer 1
                    ┌─────────────────▼───────────────────────┐
                    │            LAYER 2: BRAIN               │
                    │  ClaudeStrategist (every 300s / 5 min)  │
                    │  Builds context → Calls Claude Code CLI │
                    │  Outputs: StrategicPlan (cached)        │
                    └─────────────────┬───────────────────────┘
                                      │ requires Layers 1+2
                    ┌─────────────────▼───────────────────────┐
                    │          LAYER 3: EXECUTION             │
                    │  StrategyWorker(45s) → RuleEngine       │
                    │  PositionWatchdog(10s) → Claude(30s)    │
                    │  ProfitSniper/Mode4(5s) → trailing      │
                    │  EnforcerWorker(60s) → quality gates    │
                    └─────────────────────────────────────────┘
```

**Rules**: Layer N cannot start without Layer N-1 active. Stopping cascades downward. State persisted to `data/layer_state.json`.

### DI Container (ServiceContainer + WorkerManager)

Services created once, stored in shared dict, passed to all workers:

```
Layer 0: Database + Migrations
Layer 1: BybitClient → MarketService, OrderService, PositionService, AccountService
Layer 2: TAEngine → TACache
Layer 3: ClaudeCodeClient, DecisionParser, CostTracker
Layer 4: RiskManager, AlertManager
Layer 5: StrategyRegistry, PnLManager, FundManager, APEX, TIAS, Sentinel
```

---

## 3. Subsystem Map

| # | Subsystem | Package | Purpose | Key Interval |
|---|-----------|---------|---------|-------------|
| 1 | **Core** | `src/core/` | DI, layers, types, thesis, coordinator, data lake | — |
| 2 | **Brain** | `src/brain/` | Claude strategic review, watchdog decisions | 300s |
| 3 | **APEX** | `src/apex/` | Qwen trade optimization (direction, SL/TP, size) | per-trade |
| 4 | **TIAS** | `src/tias/` | Post-trade AI analysis + feedback loop | at-close |
| 5 | **X-RAY** | `src/analysis/structure/` | 10-phase structural intelligence | 60s |
| 6 | **Strategies** | `src/strategies/` | 41 strategies, scoring, ensemble, regime | 45s |
| 7 | **Trading** | `src/trading/` | Bybit API, order execution, position management | on-demand |
| 8 | **Fund Mgr** | `src/fund_manager/` | 22-module capital allocation, profit ratchet | 60s |
| 9 | **Intelligence** | `src/intelligence/` | News, Reddit, F&G, funding, OI, signals | 300-600s |
| 10 | **Risk** | `src/risk/` | Drawdown, circuit breakers, position limits | on-demand |
| 11 | **Sentinel** | `src/sentinel/` | Exit firewall, smart deadline, portfolio advisor | 300s |
| 12 | **Telegram** | `src/telegram/` | Interactive bot, alerts, AI chat | 60s |
| 13 | **MCP** | `src/mcp/` | Claude Code/browser tool interface (8 modules) | on-demand |

---

## 4. Pipeline Flowcharts

### 4.1 Trade Execution Pipeline

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │                    TRADE EXECUTION PIPELINE                         │
  └──────────────────────────────────────────────────────────────────────┘

  KlineWorker + PriceWorker (45s)
       │
       ▼
  ┌─────────────┐
  │ 41 Strategy  │  Each emits RawSignal(symbol, direction, score, reason)
  │ Evaluators   │  Categories A-K: momentum, trend, mean-reversion,
  │              │  derivatives, sentiment, structure, advanced, timing
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ TradeScorer  │  4-component scoring: Base(40) + Confluence(25)
  │ (Layer 2)    │  + Context(20) + Quality/X-RAY(20) = 0-105
  │              │  Grades: A+(80) A(68) B(56) C(45) D(<45)
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ Ensemble     │  All strategies vote: BUY / SELL / NEUTRAL
  │ Voter        │  Weighted by win rate. Consensus: STRONG→CONFLICT
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ Rule Engine  │  Checks StrategicPlan (from Claude, cached)
  │ (Layer 3)    │  Validates: max_positions, cooldowns, regime,
  │              │  risk limits, enforcer halt, blacklists
  └──────┬──────┘
         ▼
  ┌─────────────┐     ┌──────────────┐
  │ APEX        │────▶│ Intelligence │  5-section package:
  │ Assembler   │     │ Package      │  1)Directive 2)CoinData 3)TIAS History
  └──────┬──────┘     └──────────────┘  4)Situation Data 5)X-RAY
         ▼
  ┌─────────────┐
  │ APEX        │  Calls Qwen/DeepSeek via OpenRouter
  │ Optimizer   │  Returns: OptimizedTrade (direction, SL%, TP%, size, lev)
  │ (Qwen)      │  If Qwen fails → fallback to Claude's original params
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ TradeGate   │  12 hard safety checks (size, leverage, capital,
  │             │  duplicates, cooldowns, TP floor, trail floor)
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ Transformer │  Routes to Shadow (paper) or Bybit (live)
  │ → Order     │  Places MARKET order, sets leverage
  │   Service   │  Returns: Order with fill confirmation
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │ Post-Trade  │  Save thesis → Register in TradeCoordinator
  │ Recording   │  → Write to data lake → Send Telegram alert
  └─────────────┘
```

### 4.2 Claude Strategic Review Pipeline

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │              CLAUDE STRATEGIC REVIEW (every 300s)                    │
  └──────────────────────────────────────────────────────────────────────┘

  LayerManager Brain Task (Layer 2)
       │
       ▼
  ┌─────────────────────────────────┐
  │ ClaudeStrategist builds context │
  │                                 │
  │  - Open positions + PnL         │
  │  - Account balance + equity     │
  │  - Market regime (BTC-driven)   │
  │  - Fear & Greed Index           │
  │  - Open theses (with APEX ctx)  │
  │  - Recent trade lessons (TIAS)  │
  │  - Top signals from strategies  │
  │  - News sentiment summary       │
  └────────────┬────────────────────┘
               ▼
  ┌─────────────────────────────────┐
  │ Claude Code CLI ($0)            │
  │ Model: claude-sonnet-4          │
  │ Timeout: 90s | Temp: 0.3       │
  │ Returns: JSON StrategicPlan     │
  └────────────┬────────────────────┘
               ▼
  ┌─────────────────────────────────┐
  │ StrategicPlan (cached)          │
  │                                 │
  │  market_view, risk_level        │
  │  max_positions, coin_directives │
  │  position_actions (hold/close)  │
  │  new_trades[] (direct entries)  │
  │  focus_coins, avoid_coins       │
  └────────────┬────────────────────┘
               ▼
  Rule Engine reads plan every 45s for trade approval
```

### 4.3 Position Monitoring Pipeline

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │              POSITION MONITORING (multi-layer)                       │
  └──────────────────────────────────────────────────────────────────────┘

  PositionWatchdog (every 10s — code rules)
       │
       ├─── Check SL (hard stop, never violated)
       ├─── Check trailing SL (from Mode 4)
       ├─── Detect duplicate positions (same symbol)
       ├─── Rapid price move alerts (>0.5% in 10s)
       └─── Loss proximity warnings

  PositionWatchdog (every 30s — Claude review)
       │
       ├─── Build position context for Claude
       ├─── Call Claude Code CLI → WatchdogDecision
       └─── Execute: hold / tighten_stop / partial_close / full_close

  ProfitSniper / Mode 4 (every 5s — high-frequency)
       │
       ├─── Ring buffer: 720 ticks (60 min window)
       ├─── 5 mathematical models:
       │    Hurst | Momentum Decay | ATR Extension | Volume Div | R:R
       ├─── Regime-aware trailing (1.5-2.5x ATR base)
       ├─── Anti-greed pullback detection (40%/60%/75% thresholds)
       └─── Execute: tighten (30s cooldown) / partial close (120s cooldown)

  Sentinel (every 300s — protection layer)
       │
       ├─── Exit Firewall: blocks Claude from panic-closing
       ├─── Smart Deadline: tiered expiry based on PnL
       └─── Portfolio Advisor (DeepSeek): recommends SL tightening
```

### 4.4 TIAS Feedback Loop

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │              TIAS FEEDBACK LOOP (at trade close)                     │
  └──────────────────────────────────────────────────────────────────────┘

  Trade Closes (any reason)
       │
       ▼
  TradeCoordinator close callback
       │
       ▼
  ┌───────────────────────────┐
  │ TradeContextCollector     │  Captures 7 groups:
  │ (Phase 1 — immediate)    │  A) Trade outcome (PnL, direction, hold time)
  │                           │  B) Entry context (thesis, signal, leverage)
  │                           │  C) Market conditions (regime, F&G)
  │                           │  D) TA at close (RSI, MACD, ATR, volume)
  │                           │  E) Mode4 data (peak PnL, composite score)
  │                           │  F) APEX tracking (was_flipped, confidence)
  │                           │  G) Metadata (timestamps)
  └───────────┬───────────────┘
              ▼
  ┌───────────────────────────┐
  │ trade_intelligence table  │  INSERT row with all context
  └───────────┬───────────────┘
              ▼ (later, async)
  ┌───────────────────────────┐
  │ DeepSeek V3 Analysis      │  Analyzes: why won/lost, what worked,
  │ (Phase 2)                 │  optimal direction/SL/TP, lessons learned
  │                           │  Stores: ds_* columns (UPDATE same row)
  └───────────┬───────────────┘
              ▼ (next trade cycle)
  ┌───────────────────────────┐
  │ APEX IntelligenceAssembler│  Reads Section 3 (symbol history) and
  │ (Phase 3 — consumption)   │  Section 4 (situation stats) from TIAS repo
  │                           │  Qwen sees: "in past downtrends, sells won 75%"
  └───────────────────────────┘
```

### 4.5 X-RAY Structural Analysis Pipeline

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │              X-RAY STRUCTURAL ANALYSIS (10 phases)                   │
  └──────────────────────────────────────────────────────────────────────┘

  StructureWorker (every 60s, batch of 25 coins)
       │
       ▼
  StructureEngine.analyze(symbol, price, candles)
       │
       ├─ Phase 1:  Support & Resistance (swing detection, clustering)
       │             → support_levels[], resistance_levels[]
       │             → position_in_range (0.0 = support, 1.0 = resistance)
       │
       ├─ Phase 2:  Market Structure (BOS, CHoCH, trend identification)
       │             → uptrend / downtrend / ranging / unknown
       │
       ├─ Phase 3:  Structural SL/TP (placement with R:R calculation)
       │             → StructuralPlacement (SL, TP, rr_ratio, entry_quality)
       │
       ├─ Phase 4:  Fair Value Gaps (3-candle gap detection)
       │             → fvgs[] with direction, fill status, displacement
       │
       ├─ Phase 5:  Order Blocks (institutional entry zones)
       │             → order_blocks[] with freshness, strength score
       │
       ├─ Phase 6:  Liquidity Zones (equal highs/lows, round numbers)
       │             → liquidity_zones[] with type, strength
       │
       ├─ Phase 7:  Liquidity Sweeps (stop hunts + reversals)
       │             → recent_sweeps[] with signal classification
       │
       ├─ Phase 8:  Volume Profile (POC, value area, HVN/LVN)
       │             → poc_price, value_area_high/low
       │
       ├─ Phase 9:  Fibonacci (retracement/extension + confluence)
       │             → fib_key_level, confluence_with
       │
       ├─ Phase 10: MTF Confluence (multi-factor scoring 0-10)
       │             → quality: maximum / good / weak / none
       │
       └─ Setup Score: 0-100 → Quality: A+ / A / B / C / SKIP
            │          R:R Hard Caps:
            │          - placement=None → max B
            │          - rr < 0.5 → SKIP
            │          - rr < 1.0 → max C
            │          - rr < 1.5 → max B
            ▼
       StructureCache.set(symbol, StructuralAnalysis)  [TTL: 300s]
            │
            ├─── Scorer reads for quality component (0-8 pts)
            ├─── Assembler reads for APEX Section 5
            ├─── Strategist reads for Claude context
            └─── SetupScanner ranks top 12 setups
```

---

## 5. Workers Reference

| Worker | Interval | Layer | Dependencies | Output |
|--------|----------|-------|-------------|--------|
| KlineWorker | 45s | 1 | BybitClient | klines table |
| PriceWorker | 45s | 1 | BybitClient | ticker_cache table |
| NewsWorker | 300s | 1 | FinnhubClient | news_articles table |
| RedditWorker | 600s | 1 | RedditClient (PRAW) | reddit_posts table |
| AltDataWorker | 300s | 1 | F&G, Funding, OI APIs | fear_greed, funding_rates, OI tables |
| ScannerWorker | 300s | 1 | MarketService | active_universe table (top 20-30 coins) |
| RegimeWorker | 600s | 1 | TACache (BTCUSDT) | regime_history table, RegimeDetector state |
| SignalWorker | 120s | 1 | SentimentAggregator | signals table |
| StructureWorker | 60s | 1 | StructureEngine | StructureCache (in-memory, 300s TTL) |
| StrategyWorker | 45s | 3 | Registry, Scorer, RuleEngine, APEX | Executed trades |
| PositionWatchdog | 10s/30s | 3 | PositionService, Claude | SL adjustments, closes |
| ProfitSniper (Mode4) | 5s | 3 | PositionService, Ring buffer | Trailing stops, partials |
| EnforcerWorker | 60s | 3 | ThesisManager, PnLManager | Enforcement level (0/1/2) |
| FundManagerWorker | 60s | 3 | AccountService | AccountState, SizingDecisions |
| CleanupWorker | 3600s | 1 | Database | Retention cleanup (7d snapshots) |
| TelegramBotWorker | 60s | 1 | Telegram API | Interactive responses |
| ScheduledReportWorker | 300s | 1 | PnLManager, DataLake | Daily summary reports |
| OptimizationWorker | 3600s | 1 | StrategyRegistry | Weekly weight adjustments |
| TrialMonitorWorker | 3600s | 1 | StrategyRegistry | Trial strategy lifecycle |
| DiscoveryWorker | 7200s | 1 | PatternEngine | AI-generated strategy candidates |
| BacktestWorker | 3600s | 1 | BacktestEngine | Backtest results |
| AllocationWorker | 300s | 1 | PortfolioOptimizer | Portfolio rebalancing |
| PriceAlertWorker | 10s | 1 | MarketService | Telegram price alerts |
| LiveMonitorWorker | varies | 1 | StrategyRegistry | Factory strategy monitoring |

---

## 6. Subsystem Details

### 6.1 APEX — Trade Optimization

```
Directive (Claude) ──▶ IntelligenceAssembler ──▶ Qwen/DeepSeek ──▶ TradeGate ──▶ Execute
                           │
                    5-section package:
                    1. Claude's directive (symbol, dir, SL, TP)
                    2. Coin state (TA, Mode4, orderbook)
                    3. TIAS symbol history (regime-filtered)
                    4. TIAS situation data (cross-coin, regime+F&G)
                    5. X-RAY structural intelligence
```

| Component | File | Purpose |
|-----------|------|---------|
| IntelligenceAssembler | `assembler.py` | Build 5-section IntelligencePackage |
| TradeOptimizer | `optimizer.py` | 10-step pipeline, Qwen calls, 3-tier threshold |
| TradeGate | `gate.py` | 12 hard safety checks (never blocks, only adjusts) |
| QwenClient | `qwen_client.py` | OpenRouter API (30s timeout, 800 max tokens) |
| OptimizedTrade | `models.py` | Result: direction, SL%, TP%, size, leverage, confidence |

**Three-Tier Threshold** (optimizer decides data quality):
- Tier 1: Coin has sufficient TIAS history → optimize normally
- Tier 2: Coin sparse but regime has data → inject regime context
- Tier 3: No data anywhere → fallback to Claude's original params

### 6.2 TIAS — Trade Intelligence Analysis

```
Trade closes ──▶ TradeContextCollector ──▶ trade_intelligence INSERT
                                               │
                                     (async) DeepSeek V3 Phase 2
                                               │
                                     ds_* columns UPDATE
                                               │
                            Next trade: APEX reads TIAS for learning
```

| Component | File | Purpose |
|-----------|------|---------|
| TradeContextCollector | `collector.py` | Capture 7 groups of context at close |
| TradeIntelligenceRepo | `repository.py` | CRUD + APEX query methods |
| DeepSeekClient | `deepseek_client.py` | Post-trade AI analysis (45s timeout) |

### 6.3 X-RAY — Structural Intelligence

| Phase | Engine | Output |
|-------|--------|--------|
| 1 | SupportResistanceEngine | support[], resistance[], position_in_range |
| 2 | MarketStructureDetector | structure (uptrend/downtrend/ranging), BOS, CHoCH |
| 3 | StructuralLevelCalculator | SL, TP, R:R ratio, entry quality |
| 4 | FairValueGapDetector | FVGs with direction, fill status, displacement |
| 5 | OrderBlockDetector | OBs with freshness, strength score (0-100) |
| 6 | LiquidityMapper | Zones (equal highs/lows, round numbers) |
| 7 | LiquidityMapper | Sweeps with signal (high_probability/moderate) |
| 8 | VolumeProfileCalculator | POC price, value area, HVN/LVN nodes |
| 9 | FibonacciCalculator | Retracement/extension levels, confluence |
| 10 | MTFConfluenceScorer | Score 0-10, quality (maximum/good/weak/none) |

**Setup Score (0-100)**:
- Base: 50
- Entry position: +25 (ideal) to -10 (poor)
- Structure alignment: +20 (aligned) to -15 (against)
- R:R modifier: +20 (excellent) to -40 (terrible)
- BOS/CHoCH: +10 / -15
- SMC confluence: +4 to +15
- Phase 3 bonuses: VP +5, Fib +4/+8, MTF +2/+7/+12

### 6.4 Strategies — Signal Generation

**41 strategies across 11 categories:**

| Cat | Name | Examples |
|-----|------|---------|
| A | Momentum Reversals | RSI Reversal, VWAP Bounce, BB Squeeze, EMA Cross |
| B | Trend Following | Volume Breakout, Supertrend, Ichimoku, Double Bottom |
| C | Mean Reversion | Bollinger Mean Reversion, RSI Divergence |
| D | Derivatives | Funding Rate Fade, OI Divergence |
| E | Sentiment | Fear & Greed Extreme, News Breakout, Sentiment Momentum |
| F | Market Structure | S/R, Multi-TF, Liquidation Hunt, Grid Recovery |
| G | Advanced | Stop Hunt Sniper, Retail Fade, Whale Shadow |
| H | Quantitative | Spread/Basis, Volatility Switch, Order Flow |
| I | Time-Based | Kill Zone, Weekend Gap, Options Expiry |
| J | Cross-Asset | BTC Dominance, Correlation, Altcoin Beta |
| K | AI & Hybrid | Claude Conviction, Pattern Memory, Ensemble, Adaptive |

**Scorer (4 components)**:
- Base (0-40): Strategy conditions strength
- Confluence (0-25): Multiple indicator agreement
- Context (0-20): Higher TF, sentiment, F&G, regime
- Quality (0-20): Volume, S/R proximity, X-RAY structure (0-8 expanded)

**Ensemble Voting**: All strategies vote BUY/SELL/NEUTRAL. Weighted by win rate. Consensus: STRONG / GOOD / LEAN / WEAK / CONFLICT → determines size multiplier.

**Regime Detection** (BTCUSDT-driven):
- TRENDING_UP: ADX > 25, +DI > -DI
- TRENDING_DOWN: ADX > 25, -DI > +DI
- RANGING: ADX < 20, Choppiness > 60
- VOLATILE: ATR percentile > 150
- DEAD: ADX < 15, low volume

### 6.5 Brain — Claude Integration

| Component | File | Purpose |
|-----------|------|---------|
| ClaudeStrategist | `strategist.py` | Builds context, calls Claude every 5 min |
| ClaudeCodeClient | `claude_code_client.py` | CLI wrapper ($0, Max subscription) |
| DecisionParser | `decision_parser.py` | Extract JSON from Claude response |
| CostTracker | `cost_tracker.py` | Track calls, tokens, budget |

**Claude sees every cycle**: positions, PnL, regime, F&G, open theses (with APEX context), recent trade lessons, news sentiment, signals.

### 6.6 Trading — Exchange Interface

| Service | File | Key Methods |
|---------|------|-------------|
| BybitClient | `client.py` | REST wrapper, rate limiting, error mapping |
| MarketService | `market_service.py` | get_ticker (5s cache), get_all_tickers |
| OrderService | `order_service.py` | place_order, cancel_order, close_position |
| PositionService | `position_service.py` | get_positions, update SL/TP, close |
| AccountService | `account_service.py` | get_wallet_balance, get_fees |
| Transformer | `transformer.py` | Shadow ↔ Bybit routing state machine |

### 6.7 Fund Manager — Capital Allocation

22 modules orchestrated by IntelligentFundManager:

| Module | Purpose |
|--------|---------|
| M1 CapitalAllocator | Base allocation, unlocked % |
| M3 CapitalReserves | Emergency reserves, tiered pools |
| M4 CorrelationGuard | Prevent correlated positions |
| M6 VolatilityScaler | Scale by ATR percentile |
| M10 RiskWeatherAssessor | Calm / Warning / Alert / Panic |
| M14 ProfitRatchet | Lock 50% equity highs + 25% trade profits |

Every trade asks `fund_manager.size_trade()` → SizingDecision (amount, leverage, pool, reasoning).

### 6.8 Intelligence — Data Collection

| Source | Client | Interval | Output |
|--------|--------|----------|--------|
| Finnhub News | FinnhubClient | 300s | news_articles (headline, sentiment -1 to 1) |
| Reddit | RedditClient (PRAW) | 600s | reddit_posts (title, score, sentiment) |
| Fear & Greed | FearGreedClient | 3600s | fear_greed_index (0-100) |
| Funding Rates | FundingRateTracker | 300s | funding_rates (rate, next time) |
| Open Interest | OpenInterestTracker | 600s | open_interest (value, 24h change) |
| On-Chain | OnChainClient | varies | whale movements, inflows/outflows |

**SentimentAggregator** combines: News (35%) + Reddit (30%) + F&G (20%) + Momentum (15%). Override: F&G extreme (<20 or >80) boosts to 60% weight.

### 6.9 Risk — Safety Layer

- **DrawdownTracker**: Peak equity tracking, circuit breakers
- **Daily loss limit**: 10% (configurable via pnl_targets.halt_threshold_pct)
- **Max leverage**: 5x (configurable)
- **Mandatory stops**: Always (configurable)
- **Max positions**: 10 concurrent (configurable)

### 6.10 Sentinel — Protection Layer

| Component | Purpose |
|-----------|---------|
| Exit Firewall | Blocks Claude from panic-closing profitable/young positions |
| Smart Deadline | Tiered expiry: profit→hold 5m, breakeven→hold, loss→close after grace |
| Portfolio Advisor | DeepSeek V3 assesses risk every 5 min, recommends SL tightening only |

### 6.11 Telegram — User Terminal

14 handler classes: Portfolio, Analysis, Trading, Brain, System, Alert, Watchlist, Journal, Schedule, Emergency, FundManager, TIAS, APEX. Supports slash commands, inline buttons, AI free-text Q&A.

### 6.12 MCP Server — Claude Integration

8 tool modules: trading_tools, analysis_tools, risk_tools, news_tools, altdata_tools, sentiment_tools, memory_tools, system_tools. Transports: stdio (Claude Code) + SSE (browser).

---

## 7. Database Schema (44 tables)

### Market Data
| Table | Key Columns | Retention |
|-------|------------|-----------|
| klines | symbol, timeframe, OHLCV, volume | Forever |
| ticker_cache | symbol, last_price, bid/ask, volume_24h | Current |
| orderbook_snapshots | symbol, bids/asks JSON | 7 days |

### Trading
| Table | Key Columns | Retention |
|-------|------------|-----------|
| orders | symbol, side, type, price, qty, status, SL/TP | Forever |
| positions | symbol, side, size, entry_price, mark_price, PnL | Active |
| trade_history | entry/exit price, PnL %, strategy, confidence | Forever |
| account_snapshots | total_equity, available, margin, unrealized_PnL | Forever |
| strategy_trades | strategy, symbol, direction, score, PnL, regime | Forever |

### Intelligence
| Table | Key Columns | Retention |
|-------|------------|-----------|
| news_articles | headline, source, sentiment (-1 to 1), symbols | Forever |
| reddit_posts | subreddit, title, score, sentiment | Forever |
| aggregated_sentiment | symbol, overall_score, level, components | Forever |
| fear_greed_index | value (0-100), classification | Forever |
| funding_rates | symbol, rate, next_funding_time | Forever |
| open_interest | symbol, value, change_24h_pct | Forever |
| signals | symbol, type, confidence, source, reasoning | Forever |

### Strategy Engine
| Table | Key Columns | Retention |
|-------|------------|-----------|
| active_universe | symbol, opportunity_score, volume | Current |
| regime_history | symbol, regime, confidence, ADX, ATR | Forever |
| ensemble_votes | setup_id, symbol, strategy, vote, confidence | Forever |
| daily_pnl | date, equity, realized_PnL, wins, losses, halted | Forever |

### TIAS
| Table | Key Columns | Retention |
|-------|------------|-----------|
| trade_intelligence | symbol, direction, pnl_pct, win, regime, ds_* analysis | Forever |

### Data Lake
| Table | Key Columns | Retention |
|-------|------------|-----------|
| market_snapshots | BTC/ETH/SOL prices, regime, F&G, full_data JSON | Forever |
| trade_log | symbol, direction, entry/exit, PnL, thesis, reason | Forever |
| position_snapshots | symbol, entry, mark_price, PnL %, age | 7 days |
| claude_decisions | type, trades_count, market_view, risk_level, response_time | Forever |
| daily_summary | total_pnl, trades, wins, best/worst, equity | Forever |

### Thesis & Coordinator
| Table | Key Columns | Retention |
|-------|------------|-----------|
| trade_thesis | symbol, direction, SL/TP, thesis, apex_flipped, apex_reason | Forever |

### Fund Manager & Risk
| Table | Key Columns | Retention |
|-------|------------|-----------|
| fund_manager_state | key (JSON), value | Persistent |
| profit_ratchet_log | locked_amount, total_locked, profit_floor | Forever |
| hourly_performance | hour, profit_pct, wins, losses, win_rate | Forever |

---

## 8. Configuration Reference (config.toml)

| Section | Key Settings | Defaults |
|---------|-------------|----------|
| `[general]` | mode, log_level, timezone | shadow, INFO, UTC |
| `[bybit]` | testnet, rate_limit, ws_ping | false, 10/s, 20s |
| `[brain]` | strategic_interval, max_calls_per_hour | 300s, 30 |
| `[workers]` | market_data_interval, news/reddit/altdata | 45s, 300/600/300s |
| `[risk]` | max_leverage, daily_loss_limit_pct, max_positions | 5, 10%, 10 |
| `[scanner]` | min_volume_24h, max_coins, max_spread_pct | 50M, 30, 0.15% |
| `[regime]` | detection_interval, trending_adx_threshold | 600s, 25 |
| `[strategy_engine]` | scan_interval, min_ensemble_agreement | 45s, 2.5 |
| `[pnl_targets]` | daily_target, halt_threshold | 10%, -10% |
| `[mode4]` | check_interval, buffer_size, base_atr_mult | 5s, 720, 2.5x |
| `[apex]` | model, timeout, max_size, conviction_enabled | deepseek-v3, 30s, $1200, true |
| `[tias]` | model, timeout, max_tokens | deepseek-v3, 45s, 1500 |
| `[sentinel]` | firewall, deadline_grace, advisor_interval | true, 5min, 300s |
| `[analysis.structure]` | worker_interval, cache_ttl, min_candles | 60s, 300s, 50 |
| `[enforcer]` | check_interval, pnl_caution, pnl_survival | 60s, -2%, -5% |
| `[fund_manager]` | starting_unlock_pct, active/aplus/emergency | 20%, 70/20/10% |
| `[watchdog]` | check_interval, brain_trigger_loss_pct | 10s, 0.8% |

---

## 9. Timing Matrix

```
Every 5s:    Mode4 ProfitSniper (trailing + anti-greed)
Every 10s:   PositionWatchdog code rules (SL, duplicates, rapid moves)
             PriceAlertWorker (Telegram alerts)
Every 30s:   PositionWatchdog Claude review
Every 45s:   KlineWorker, PriceWorker, StrategyWorker
Every 60s:   StructureWorker (X-RAY), EnforcerWorker, FundManagerWorker, TelegramBot
Every 120s:  SignalWorker
Every 300s:  Claude Strategic Review (Brain Layer 2)
             ScannerWorker, AltDataWorker, NewsWorker
             SentinelAdvisor, ScheduledReportWorker, AllocationWorker
Every 600s:  RegimeWorker, RedditWorker
Every 3600s: CleanupWorker, OptimizationWorker, TrialMonitor, BacktestWorker
Every 7200s: DiscoveryWorker
```

---

## 10. Dependency Graph

```
                         ┌──────────┐
                         │  Bybit   │
                         │   API    │
                         └────┬─────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │ Market   │   │  Order   │   │ Position │
        │ Service  │   │ Service  │   │ Service  │
        └────┬─────┘   └────┬─────┘   └────┬─────┘
             │              │              │
             ▼              │              │
    ┌────────────────┐      │              │
    │   TAEngine     │      │              │
    │   TACache      │      │              │
    └───────┬────────┘      │              │
            │               │              │
     ┌──────┼───────┐       │              │
     ▼      ▼       ▼       │              │
  ┌─────┐┌─────┐┌───────┐   │              │
  │Strat││X-RAY││Regime │   │              │
  │egies││     ││Detect.│   │              │
  └──┬──┘└──┬──┘└───┬───┘   │              │
     │      │       │       │              │
     ▼      ▼       ▼       │              │
  ┌──────────────────────┐  │              │
  │ Scorer + Ensemble    │  │              │
  └──────────┬───────────┘  │              │
             ▼              │              │
  ┌──────────────────────┐  │              │
  │ Claude Strategist    │  │              │
  │ (Brain Layer 2)      │  │              │
  └──────────┬───────────┘  │              │
             ▼              │              │
  ┌──────────────────────┐  │              │
  │ Rule Engine          │──┘              │
  │ (Layer 3)            │                 │
  └──────────┬───────────┘                 │
             ▼                             │
  ┌──────────────────────┐                 │
  │ APEX Optimizer       │                 │
  │ (Qwen + Gate)        │                 │
  └──────────┬───────────┘                 │
             ▼                             │
  ┌──────────────────────┐                 │
  │ Transformer          │─────────────────┘
  │ (Shadow ↔ Bybit)     │
  └──────────┬───────────┘
             ▼
  ┌──────────────────────┐
  │ TIAS Collector       │
  │ → DeepSeek Analysis  │
  │ → Feedback to APEX   │
  └──────────────────────┘
```

---

## 11. Key Design Patterns

| Pattern | Where | Purpose |
|---------|-------|---------|
| Dependency Injection | ServiceContainer | Single source of truth for all services |
| 3-Layer Architecture | LayerManager | Enforced dependency chain with persistence |
| State Machine | Transformer | Shadow ↔ Bybit routing with crash recovery |
| Ring Buffer | ProfitSniper | 720-tick window for mathematical models |
| Cache Layers | TACache (45s), StructureCache (300s), Ticker (5s) | Deduplicate expensive computations |
| Async Throughout | All I/O | Non-blocking database, HTTP, WebSocket |
| Error Recovery | BaseWorker | Exponential backoff, max restarts |
| Auth Recovery | ClaudeCodeClient | 3-layer: OAuth refresh → hot-reload → Telegram alert |
| Thesis Learning | ThesisManager + TIAS | Every trade gets thesis → close → analyze → learn |
| APEX Failsafe | TradeOptimizer | Qwen failure → fallback to Claude params (never blocks) |
| Hard Caps | StructureEngine, TradeGate | Unconditional quality/safety limits |
| Data Lake | DataLakeWriter | 6 tables for complete audit trail |

---

*Generated: 2026-04-12 | Schema: v23 | ~356 Python modules across 21 packages*
