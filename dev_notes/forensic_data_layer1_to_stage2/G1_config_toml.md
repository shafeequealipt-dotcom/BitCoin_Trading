# G1 — config.toml (Verbatim)

## Capture metadata

- **Source file:** `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml`
- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **File size:** 42755 bytes
- **Line count:** 1094 (last newline only)
- **mtime:** 2026-04-27 20:25:42 UTC
- **md5:** `d5c308beb5441fb193217013e3f3a545`
- **Secret redaction:** scanned for `api_key|API_KEY|secret|SECRET|password|PASSWORD|token|TOKEN` — only matches are unrelated config keys (`max_tokens`, `advisor_max_tokens`) and one comment about OAuth refresh. No literal credentials present in the file. Nothing redacted.

## Verbatim contents (config.toml lines 1-1094)

```toml
# =============================================================================
# Trading Intelligence MCP — Master Configuration
# =============================================================================
# All settings for the entire system. Env vars override values here.
# Copy .env.example → .env and fill in your API keys.
# =============================================================================

[general]
# Trading mode: "shadow" (Shadow virtual exchange), "paper" (Bybit testnet), "live" (real funds)
mode = "shadow"
# Shadow API URL (only used when mode = "shadow")
shadow_api_url = "http://127.0.0.1:9090"
# Timezone for display (internal always UTC)
timezone = "UTC"
# How verbose: DEBUG, INFO, WARNING, ERROR
log_level = "INFO"
# Directory for log files (relative to project root)
log_dir = "data/logs"

[bybit]
# Bybit mainnet for REAL market data. Orders routed via Transformer to Shadow (paper).
testnet = false
# Fallback symbols (used when bulk ticker API fails). Scanner dynamically
# selects top 20 by score from all Bybit USDT perps on each scan cycle.
default_symbols = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "1000PEPEUSDT",
    "WIFUSDT", "HYPEUSDT", "AAVEUSDT", "NEARUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "LTCUSDT", "BCHUSDT", "TONUSDT",
]
# Rate limit: max requests per second to Bybit REST API
rate_limit_per_second = 10
# WebSocket ping interval in seconds
ws_ping_interval = 20
# Reconnect delay on WS disconnect (seconds)
ws_reconnect_delay = 5
# Order receive window in milliseconds
recv_window = 5000

[finnhub]
# Enable Finnhub news + calendar integration
enabled = true
# Rate limit: Finnhub free tier allows 60 calls/min
rate_limit_per_minute = 60
# Categories to fetch: general, forex, crypto, merger
news_categories = ["crypto", "general"]
# Max articles to fetch per poll
max_articles_per_fetch = 50

[reddit]
# Enable Reddit sentiment analysis via PRAW
enabled = false
# Subreddits to monitor for crypto sentiment
subreddits = ["cryptocurrency", "bitcoin", "ethtrader", "CryptoMarkets", "solana"]
# Max posts to fetch per subreddit per poll
max_posts_per_sub = 25
# Minimum score threshold to consider a post relevant
min_score = 10
# Rate limit: Reddit allows ~60 requests/min with OAuth
rate_limit_per_minute = 60

[altdata]
# Enable alternative data collection (Fear & Greed, funding rates, etc.)
enabled = true
# Fear & Greed index poll interval in seconds (API updates ~daily)
fear_greed_interval = 3600
# Funding rate poll interval in seconds
funding_rate_interval = 300
# Open interest poll interval in seconds
open_interest_interval = 600
# CoinGecko rate limit (free tier: 10-30 calls/min)
coingecko_rate_limit_per_minute = 10

[database]
# SQLite database path (relative to project root)
path = "data/trading.db"
# WAL mode for concurrent reads during writes
wal_mode = true
# Connection pool size (for future PostgreSQL migration)
pool_size = 5
# Query timeout in seconds
query_timeout = 30
# Auto-vacuum interval in hours
vacuum_interval = 24
# Phase 1 (D-3 fix) — chunk size for MarketRepository.save_klines.
# A single executemany over the full per-(symbol, timeframe) batch held
# the DatabaseManager lock for 12-20 s during heavy ticks, queueing every
# other worker behind it. Chunking + yielding the loop between chunks
# eliminates that contention without changing total wall-clock work.
kline_save_chunk_size = 500
# Phase 1 (D-3 fix) — WAL checkpoint scheduler cadence (in kline_worker
# ticks). PASSIVE checkpoints never block, so it's safe to schedule
# them often. The historical 100 MiB pinned -wal file disappeared once
# we stopped relying on opportunistic auto-checkpoint + hourly cleanup.
wal_checkpoint_every_n_kline_ticks = 50
# Phase 1 (D-3 fix) — escalate to TRUNCATE if PASSIVE checkpoints come
# back busy this many times in a row. TRUNCATE briefly blocks writers
# but fully reclaims WAL space when readers consistently pin snapshots.
wal_checkpoint_truncate_after_busy_count = 3
# Phase 1 (D-3 fix) — DB_LOCK_WAIT warn threshold (ms). Drop to 500 ms
# during verification to see finer-grained contention, then raise back.
db_lock_wait_threshold_ms = 1000

[workers]
# Enable background data collection workers
enabled = true
# Market data worker: OHLCV + ticker polling interval (seconds)
market_data_interval = 45
# News worker: Finnhub polling interval (seconds)
news_interval = 300
# Reddit worker: sentiment polling interval (seconds)
reddit_interval = 600
# Alt data worker: funding rates, OI, Fear & Greed interval (seconds)
altdata_interval = 300
# Health check interval: how often workers report status (seconds)
health_check_interval = 120
# Max consecutive failures before worker restarts
max_consecutive_failures = 5
# Worker restart delay (seconds)
restart_delay = 10

# ─────────────────────────────────────────────────────────────────────
# Sweet-spot scheduling — corrected Layer 1 architecture (Phase 1).
#
# The 7 data workers fire at these MM:SS offsets within every 5-minute
# window. Chain ordering is enforced (kline → structure → signal →
# regime → strategy → scanner) so each downstream worker reads warm
# upstream data. PriceWorker is continuous (no sweet spot needed).
# Bad MM:SS values or out-of-order chain → ConfigError at startup.
#
# Reference: LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §8.
# ─────────────────────────────────────────────────────────────────────
[workers.sweet_spots]
window_minutes = 5
kline_worker = "0:30"
structure_worker = "0:45"
signal_worker = "1:00"
regime_worker = "1:15"
strategy_worker = "1:30"
scanner_worker = "4:00"

[workers.sweet_spots.altdata]
# Funding rates: MM:SS within window, between regime (1:15) and scanner (4:00).
funding_rates = "1:45"
# Open interest: every N minutes, independent of window.
open_interest_minutes = 5
# Fear & Greed: every M minutes, hourly default.
fear_greed_minutes = 60

[brain]
# Claude Code CLI — no API key needed, no budget limit
# Uses existing Claude Max subscription ($0 per call)
enabled = true
use_claude_code = true
# Layer 1 restructure Phase 7 — when true, the strategist reads
# per-coin sections from layer_manager._coin_packages instead of
# querying 12 services per cycle. Set false to fall back to the
# legacy service-query path during Phase 9 observation if a
# regression is detected.
use_packages = true
# Strategic review interval (seconds) — alternating Call A (trades) / Call B (positions)
# 150s = 2.5 min between calls, giving 5 min per call type
strategic_interval = 150
# Watchdog Claude review interval (seconds) — reviews positions every 30s
watchdog_interval = 30
# Legacy settings kept for backward compatibility
analysis_interval = 900
signal_triggered = true
min_signal_confidence = 0.45
max_calls_per_hour = 30
model = "claude-sonnet-4-20250514"
max_tokens = 4096
temperature = 0.3

# Claude CLI subprocess timing (Phase 2 session-stability fix — Y-22 + timeout retune)
# Hard cap on one Claude CLI invocation. Was hardcoded 300 in manager.py.
claude_cli_timeout_seconds = 300
# Retries after failure (non-retryable errors — auth, billing — still skip retry).
claude_cli_max_retries = 2
# Floor between consecutive Claude CLI invocations (adaptive interval).
claude_cli_min_interval = 2.0
# Backoff base for timeout-path retries: sleep = (attempt+1) * base seconds.
# 10 → ladder 10s/20s/30s. Was hardcoded 30 → 30s/60s/90s.
# Lowering halves the brain-outage window after a single timeout.
claude_cli_retry_timeout_backoff_base_seconds = 10
# Phase 3 (Brain credentials) — pre-flight refresh margin in seconds.
# Trigger an OAuth refresh if the access token expires within this window;
# if the refresh fails AND we are inside the margin, raise
# CredentialRefreshError instead of spawning a doomed subprocess.
credential_refresh_margin_seconds = 600
# Phase 3 (Brain credentials) — refresh attempt budget per call.
# 3 attempts with exponential backoff (1s/3s/7s) before giving up.
credential_refresh_max_attempts = 3
# Cap on watchdog events injected into the Call A URGENT prompt.
# Defence-in-depth — EventBuffer already truncates at 3000 chars.
prompt_event_buffer_max_events = 20

[risk]
# ===================== RISK MANAGEMENT — AGGRESSIVE (PAPER ONLY) =====================
max_leverage = 5
mandatory_stop_loss = true
default_stop_loss_pct = 3.0
default_take_profit_pct = 6.0
max_position_size_pct = 20.0
max_open_positions = 10
daily_loss_limit_pct = 10.0
max_total_exposure_pct = 80.0
max_drawdown_pct = 25.0
min_order_value_usdt = 5.0
loss_cooldown_seconds = 30

[alerts]
# Enable Telegram alert notifications
telegram_enabled = true
# Alert levels to send: INFO, WARNING, CRITICAL
alert_levels = ["WARNING", "CRITICAL"]
# Send daily performance summary
daily_summary = true
# Daily summary time (UTC, HH:MM format)
daily_summary_time = "00:00"
# Rate limit alerts: max messages per minute
max_alerts_per_minute = 10
# Include trade entry/exit alerts
trade_alerts = true
# Include signal alerts
signal_alerts = true
# Include error alerts
error_alerts = true

[mcp]
# MCP transport: "stdio" for Claude Code, "sse" for browser/claude.ai
transport = "stdio"
# SSE server host (only used when transport = "sse")
sse_host = "0.0.0.0"
# SSE server port
sse_port = 8080
# Authentication required for SSE transport
sse_auth_required = true
# Server name advertised via MCP
server_name = "trading-intelligence"
# Server version
server_version = "0.1.0"

[watchdog]
# Position Watchdog: code rules every 10s (timer, trailing, hard stops, duplicates)
# Claude reviews handled by LayerManager every 30s
enabled = true
# How often to check positions (seconds) — code rules
check_interval_seconds = 10
# Alert when position loses > X% from entry
loss_warning_pct = 0.5
# Alert when position drops > X% from its peak unrealized profit
trailing_loss_pct = 0.3
# Alert when price is within X% of the distance to stop-loss
sl_proximity_pct = 30.0
# Alert when price moves > X% against position in single check
rapid_move_pct = 0.5
# Trigger Claude Brain when position loses > X%
brain_trigger_loss_pct = 0.8
brain_cooldown_seconds = 60
partial_close_pct = 50.0
max_brain_calls_per_hour = 20
# Layer 3: early-exit disabled (0% historical win rate, 24/24 losses).
# SL handles exits cleanly. Set true to re-enable; monitoring log
# 'EARLY_EXIT_DISABLED_WOULD_FIRE' shows what it would have done.
early_exit_enabled = false
# Phase 2 (P0-1 Ghost Positions): fast set-diff reconcile cadence —
# independent of the 5-min thesis sweep. 0.0 disables (kill switch).
fast_reconcile_seconds = 30.0

[mcp_pool]
# Phase 23 (Y-22) — MCP client pool. Disabled by default; turn on per
# consumer to migrate off the one-shot stdio storm. Each consumer that
# imports MCP tools should:
#   1. Set ``enabled = true`` here.
#   2. Run ``python server.py --transport sse`` in the background
#      (workers manager can host this — add to manager.py if needed).
#   3. Acquire a client via ``MCPClientPool.acquire`` instead of
#      shelling out to ``server.py`` per call.
# When enabled=false, every consumer keeps using one-shot stdio.
enabled = false
sse_url = "http://127.0.0.1:8080"
min_warm = 1
max_warm = 2
health_check_interval_seconds = 60
acquire_timeout_seconds = 2.0

[price]
# Phase 3 (P0-2 Price Divergence) — local price freshness vs Shadow.
# - local_max_age_seconds: above this age the WebSocket-fed local price
#   is treated as stale and the consumer falls back to Shadow's 1 Hz
#   authoritative mark. Set to a large number (e.g. 999999) to disable.
# - divergence_override_pct: above this divergence (% of Shadow's price)
#   Shadow's price is preferred over local. Emits PRICE_OVERRIDE log +
#   MED-priority event_buffer entry so Claude sees the override.
# - divergence_block_prompt_pct: any open position with divergence >
#   this value blocks Claude's prompt build (PROMPT_DEFERRED). The
#   strategist re-tries on the next cycle, after the WS re-syncs.
local_max_age_seconds = 10.0
divergence_override_pct = 0.5
divergence_block_prompt_pct = 1.0

[sl_gateway]
# Single-entry-point stop-loss gateway (Layer 3 hardening).
# When enabled, every SL modification (Time-Decay, SENTINEL, Profit Sniper
# trail, watchdog trail, brain tighten) routes through one validator that
# enforces tighten-only + min-distance + max-step + rate-limit.
#
# Rollout protocol:
#   1. Start with enabled=false (symmetric pass-through, state tracked).
#   2. Verify SL_GATEWAY_PASSTHROUGH count equals SL_PROPAGATED count.
#   3. Flip enabled=true with log_only_global=true to observe rejects.
#   4. Flip log_only flags to false one at a time for staged enforcement.
# Dry-run enabled by the Prefetch-Performance Fix (2026-04-23): every rule
# is now evaluated but REJECTs are downgraded to SL_GATEWAY_REJECT_WOULD
# logs. No trades are blocked. Once SL_GATEWAY_STATS by_rsn distribution is
# validated over a session, set log_only_global=false for hard enforcement.
enabled = true
# Rule R2: minimum distance between new SL and current price (percent).
# Legacy static fallback; when the volatility profiler is wired (default
# since manager.py), R2 uses an ATR-scaled effective min — see the
# min_distance_atr_multiplier / _abs_floor_pct keys below.
min_distance_pct = 0.3
# Rule R3: maximum step size per single SL update (percent of previous SL).
max_step_pct = 0.5
# Rule R4: minimum seconds between SL updates per symbol.
rate_limit_seconds = 30
# Global log-only: every rule becomes a SL_GATEWAY_REJECT_WOULD log.
log_only_global = true
# Per-rule log-only flags (surgical rollout controls).
# Tighten-only MUST stay false — it is safety-critical.
log_only_tighten_only = false
log_only_min_distance = false
log_only_max_step = false
log_only_rate_limit = false

# ATR-scaled R2 min_distance (user spec: max(0.05%, atr_5m_pct * 0.5)).
# Aggressive to maximise accepted trail pushes on low-vol coins. The
# absolute floor prevents bid-ask strangulation. Class ceiling clamps
# freak spikes. See src/analysis/vol_scale.py.
min_distance_atr_multiplier = 0.5
min_distance_abs_floor_pct = 0.05

# Per-class ceiling for the ATR-scaled min_distance. Anything above these
# values would be an SL that's pathologically far from price (e.g. flash
# crash). Dead coins cap at 0.30% (their baseline noise); extreme coins
# can go up to 3.50% during real volatility.
[sl_gateway.min_distance_class_ceiling]
dead = 0.30
low = 0.50
medium = 1.00
high = 2.00
extreme = 3.50

[scanner]
# Market scanner — AGGRESSIVE TESTNET MODE
enabled = true
scan_interval_seconds = 300
min_volume_24h = 5000000
max_coins = 30
max_spread_pct = 0.15
# Phase 5 (Universe flapping fix) — re-entry cooldown bumped from the
# legacy hardcoded 300 s. A coin removed from the active universe
# cannot re-enter for this many seconds (force-included coins bypass).
reentry_cooldown_seconds = 600

# Phase 5 (Universe flapping fix) — consecutive-scan hysteresis on the
# active_universe membership decision. Without it, a coin oscillating
# around the cutoff score enters/exits every scan, triggering
# KLINE_BACKFILL → cold start → STRAT_SKIP_STALE storms (live obs:
# 14 rotations/hour on 2026-04-26).
[scanner.hysteresis]
enabled = true
entry_consecutive_scans = 2
exit_consecutive_scans = 3
entry_threshold_above_min = 5
exit_threshold_below_min = -5

# ─────────────────────────────────────────────────────────────────────
# Composite opportunity scoring (Phase 6 — corrected Layer 1).
#
# ScannerWorker reads warm caches from the 7 data workers and computes
# a per-coin opportunity score as a weighted sum of these 5 components.
# Tunable: re-balance based on observed trade outcomes.
#
# Reference: LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §9.3.
# ─────────────────────────────────────────────────────────────────────
[scanner.scoring_weights]
structure = 0.30   # X-RAY setup score (0-100, normalized)
strategy = 0.30    # StrategyWorker L2 total_score (normalized to 0-1)
signal = 0.15      # SignalWorker confidence (0-1)
regime = 0.15      # Regime alignment factor (-1..+1)
funding = 0.10     # Funding rate strength (|rate| > 0.01% counts)

# Layer 1 restructure Phase 5 — qualitative checklist applied BEFORE
# the quantitative ranking step. All five criteria must pass for a
# coin to be considered for selection (force-included open-position
# coins skip this gate per HR-2). See ScannerQualitativeSettings.
[scanner.qualitative]
min_rr_ratio = 2.0
min_consensus = "GOOD"            # STRONG | GOOD; LEAN/WEAK/CONFLICT always fail
require_regime_alignment = true
funding_blocker_threshold_pct = 0.001  # 0.1% — funding above this against direction blocks
recent_failure_blocker_hours = 1
max_selection = 15
min_selection = 0                  # output empty when nothing qualifies

# =============================================================================
# Universe — Layer 1 alignment (single source of truth for "what coins?")
# =============================================================================
# Operator-curated 50-coin watch list. The single source of truth for
# Layer 1: Shadow subscribes to these, ScannerWorker filters from these,
# all downstream workers operate on the 30-coin active subset selected
# from these every 5 minutes. Open-position coins are always
# force-included even if outside this list (HR-2 in the blueprint).
#
# Manual review recommended weekly. See WATCH_LIST_50.md for the full
# selection rationale (3-tier composition: 12 majors + 23 mid-caps +
# 15 aggressive opportunity hunters, calibrated for ~$100 capital with
# $0.50+/hour profit goal).
#
# Validation rules (enforced by UniverseSettings.__post_init__):
#   - non-empty, length ≥ 10
#   - every entry matches ^[A-Z0-9]+USDT$ (uppercase, USDT-quoted)
#   - no duplicates
#
# 2026-04-26 substitution per WATCH_LIST_50.md alternates protocol:
#   FETUSDT → AEROUSDT (FETUSDT is status=Closed on Bybit; AEROUSDT
#   is the top Tier B alternate listed in WATCH_LIST_50.md)

[universe]
watch_list = [
  # Tier A — Always-On Majors (12) — safety net + tight-spread scalping
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "XRPUSDT",
  "ADAUSDT",
  "DOGEUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "ARBUSDT",
  "NEARUSDT",
  "ATOMUSDT",
  # Tier B — Volatile Mid-Caps (23) — main hunting ground for daily exploitation
  "INJUSDT",
  "RENDERUSDT",
  "ONDOUSDT",
  "ENAUSDT",
  "PYTHUSDT",
  "SEIUSDT",
  "AEROUSDT",  # substituted for delisted FETUSDT (top Tier B alternate)
  "RUNEUSDT",
  "GALAUSDT",
  "MANAUSDT",
  "SANDUSDT",
  "AXSUSDT",
  "LDOUSDT",
  "CRVUSDT",
  "DYDXUSDT",
  "AAVEUSDT",
  "ICPUSDT",
  "IMXUSDT",
  "HBARUSDT",
  "HYPEUSDT",
  "GMTUSDT",
  "FILUSDT",
  "MNTUSDT",
  # Tier C — Aggressive Opportunity Hunters (15) — high-leverage opportunity strikes
  "MONUSDT",
  "SKRUSDT",
  "PLUMEUSDT",
  "EGLDUSDT",
  "ALGOUSDT",
  "BSBUSDT",
  "KATUSDT",
  "HYPERUSDT",
  "ORCAUSDT",
  "BLURUSDT",
  "OPUSDT",
  "APTUSDT",
  "LTCUSDT",
  "BCHUSDT",
  "ALICEUSDT",
]

[regime]
# Market regime detector
detection_interval_seconds = 600
primary_symbol = "BTCUSDT"
trending_adx_threshold = 25
ranging_adx_threshold = 20
ranging_choppiness_threshold = 60
volatile_atr_percentile = 150
dead_adx_threshold = 15
dead_volume_ratio = 0.5
# Phase 3 (output-quality): per-symbol confirm-N-readings hysteresis.
# Pre-fix this was hardcoded at 2 in src/strategies/regime.py:185.
# Higher → more sticky regimes (fewer false flips), lower → more
# responsive (potentially flapping). Validated >= 1 at config-load.
hysteresis_count = 2

[strategy_engine]
# 4-layer strategy execution engine — AGGRESSIVE
scan_interval_seconds = 45
min_score_threshold = 0
min_ensemble_agreement = 2.5
max_ensemble_opposition = 2.5
max_setups_to_brain = 10
max_brain_calls_per_hour = 30

[pnl_targets]
# Daily PnL — AGGRESSIVE (paper trading)
daily_target_pct = 10.0
protect_threshold_pct = 7.0
caution_threshold_pct = -3.0
survival_threshold_pct = -7.0
halt_threshold_pct = -10.0

[leverage]
# Smart leverage — AGGRESSIVE
max_leverage = 5
tier_1_max = 5
tier_2_max = 5
tier_3_max = 4
volatile_max = 4
dead_max = 3
min_confidence_for_5x = 0.65
min_confidence_for_4x = 0.55

[optimizer]
# Weekly adaptive optimizer
enabled = true
run_day = "sunday"
run_hour_utc = 0
weight_adjustment_pct = 10
max_param_change_pct = 20
min_trades_for_optimization = 20
underperform_threshold_pct = 10
disable_after_weeks = 3

[factory]
# Strategy Factory: AI-powered pattern discovery and strategy generation
enabled = false  # Disabled: 0 patterns discovered, 0 backtests run — wasting CPU
discovery_schedule_hour_utc = 2
discovery_lookback_days = 14
min_pattern_occurrences = 10
min_win_rate = 0.52
min_profit_factor = 1.1
min_statistical_significance = 0.05
max_strategies_per_batch = 10
max_generation_retries = 3
generation_cost_limit_usd = 0.20
live_monitor_interval_seconds = 300
hot_pattern_threshold_win_rate = 0.70
hot_pattern_threshold_occurrences = 3
emergency_generation_enabled = true

[backtesting]
# Backtesting engine configuration
initial_capital = 10000
default_leverage = 3
commission_pct = 0.06
slippage_pct = 0.02
funding_rate_pct = 0.01
walk_forward_enabled = true
train_pct = 0.70
monte_carlo_runs = 1000
min_trades_to_pass = 15
min_win_rate = 0.50
min_profit_factor = 1.1
max_drawdown_pct = 20.0
min_sharpe = 0.3
min_walk_forward_efficiency = 0.4
max_ruin_probability = 0.05

[trial]
# Paper trading trial configuration
trial_duration_days = 3
max_extensions = 1
extension_duration_days = 7
trial_position_size_pct = 50
min_trades_for_evaluation = 5
promotion_min_win_rate = 0.48
promotion_min_pnl = 0.0
promotion_max_drawdown = 10.0
max_active_strategies = 80
demotion_underperform_weeks = 2
demotion_win_rate_drop_pct = 15
quarterly_revival_enabled = true

[portfolio]
# Portfolio Optimizer — capital allocation and risk management
enabled = true
optimization_day = "sunday"
optimization_hour_utc = 0
kelly_fraction = 0.40
min_trades_for_kelly = 20
max_strategy_allocation_pct = 15.0
min_strategy_allocation_pct = 2.0
proven_strategies_budget_pct = 52.0
ai_strategies_budget_pct = 33.0
trial_strategies_budget_pct = 12.0
cash_reserve_pct = 3.0
correlation_lookback_days = 30
high_correlation_threshold = 0.7
daily_risk_budget_pct = 8.0
drawdown_reduction_threshold_1 = 8.0
drawdown_reduction_factor_1 = 0.7
drawdown_reduction_threshold_2 = 15.0
drawdown_reduction_factor_2 = 0.4
kelly_weight = 0.30
mean_variance_weight = 0.40
risk_parity_weight = 0.30
min_rebalance_change_pct = 2.0
stress_test_enabled = true

[telegram_interactive]
# Interactive Telegram Bot
enabled = true
ai_responses_enabled = true
max_ai_calls_per_hour = 20
trade_confirmation_required = true
morning_briefing_enabled = true
morning_briefing_hour_utc = 5
price_alert_check_interval = 10

[fund_manager]
# Intelligent Fund Manager — 22-module capital management
enabled = true
check_interval_seconds = 60
starting_unlock_pct = 20
active_pool_pct = 70
aplus_reserve_pct = 20
emergency_reserve_pct = 10
profit_lock_pct = 50
trade_profit_lock_pct = 25
max_correlation_bucket_pct = 30
min_profitable_trade_fee_pct = 0.12

# ─── Phase 5 (post-Layer-1 fix): FundReconciler ──────────────────────
# reconcile_enabled — master switch. When False, the FundReconciler
#   worker is not registered; balance drift will go undetected until a
#   trade is rejected by Bybit (ErrCode 110007). Strongly recommended
#   to keep True in any environment with a real Bybit wallet.
# reconcile_interval_seconds — heartbeat cadence. 60 s is the minimum
#   sensible cadence given Bybit's REST quota and the typical drift
#   evolution rate. Faster reconciliation does not help; slower means
#   drift can persist longer before alerting.
# reconcile_drift_alert_threshold_pct — absolute drift % between local
#   total_equity and exchange total_equity past which a WARNING +
#   Telegram alert fires. 5 % is conservative — typical post-trade
#   noise from fee deductions and unrealized PnL is well under 1 %.
# reconcile_auto_correct — when True, drift triggers an in-place
#   overwrite of local total_equity from exchange. OFF by default
#   because auto-correcting silently is not auditable; operators
#   must opt-in explicitly.
reconcile_enabled = true
reconcile_interval_seconds = 60
reconcile_drift_alert_threshold_pct = 5.0
reconcile_auto_correct = false

[enforcer]
# Enforcer v2 — PnL-Based Intelligent Throttling
enabled = true
check_interval_seconds = 60

# PnL-based thresholds (daily PnL %)
pnl_caution_pct = -2.0              # Below this → el=1 (capital preservation)
pnl_survival_pct = -5.0             # Below this → el=2 (survival)

# Size reduction for mild negative PnL
size_reduction_enabled = true
size_reduction_at_pnl_pct = 0.0     # Start reducing below this PnL %
size_reduction_factor = 0.75        # 25% smaller positions (0% to caution)

# Streak as secondary signal (only when PnL is negative)
streak_boost_threshold = -5         # 5-loss streak + negative PnL → immediate el=1

# Auto-recovery
max_enforcement_minutes = 45        # Auto-recover after stuck at el>=1
grace_period_minutes = 30           # Manual reset grace (full skip)

# Per-level restrictions
level_1_max_positions = 3
level_1_max_leverage = 3
level_1_min_score = 75
level_2_max_positions = 2
level_2_max_leverage = 3
level_2_min_score = 80
level_2_min_confluence = 7
level_2_min_rr = 3.0

# Legacy fields (kept for backward compatibility)
decay_minutes = 60
min_trades_per_hour = 20
min_profit_per_hour_pct = 5.0
min_win_rate = 0.45
min_signals_per_hour = 50
min_setups_to_brain_per_hour = 10
max_seconds_between_trades = 90
max_escalation_level = 5
force_trade_on_gap = true
rewards_enabled = true
hourly_report_enabled = true

[mode4]
# Mode 4: ProfitSniper — institutional-grade profit protection (Phase 1-10)
# 5 mathematical models: Hurst, Momentum Decay, ATR Extension,
# Volume Divergence, Risk/Reward. Regime-aware scoring + ATR trailing.
enabled = true
check_interval_seconds = 5

# Ring Buffer (Phase 1)
buffer_max_size = 720                    # 60 minutes at 5s (720 entries)
buffer_min_ready = 100                   # Need 8+ minutes for valid models

# Trailing System (Phase 8)
base_atr_multiplier = 2.5               # Chandelier Exit base width in ATR units
trail_min_change_pct = 0.1              # Min SL change % to avoid Shadow flooding
regime_factor_trending = 1.3            # Wider trail — let trends run
regime_factor_ranging = 0.7             # Tighter trail — reversion likely
regime_factor_volatile = 1.0            # Standard trail — volatility in ATR
regime_factor_dead = 0.6               # Tightest trail — no momentum, protect gains

# Anti-Greed (Phase 9) — pullback backstop
anti_greed_enabled = true
anti_greed_pullback_40_min_peak = 2.0   # Min peak % for 40% pullback → tighten
anti_greed_pullback_60_min_peak = 3.0   # Min peak % for 60% pullback → partial
anti_greed_pullback_75_min_peak = 5.0   # Min peak % for 75% pullback → full close

# Action cooldowns (Phase 9)
tighten_cooldown_seconds = 30
partial_close_cooldown_seconds = 120
partial_close_pct = 50                  # % of position to close on partial action

# Phase 4 (Sniper-loop fix) — type-agnostic per-position cooldowns.
# The legacy partial_close_cooldown_seconds only blocked the NEXT
# partial when the IMMEDIATELY-prior action was also a partial; an
# alternating tighten ↔ partial pattern defeated it (INJUSDT 21:48
# bug, 4× partials in 60s). The new gate is type-agnostic.
min_seconds_between_actions = 60        # any M4 action of any type starts this cooldown
min_seconds_before_close = 180          # full_close from score branch (anti-greed bypasses)
# Phase 4 (Sniper-loop fix) — PROFIT GATE on partials. The legacy
# P9_CLOSE_GATE only gates full_close; a partial could fire on a red
# position. Default 0.0 = require break-even before any partial fires.
min_profit_for_partial_pct = 0.0

# Phase 9 Sniper Stall Escape (P1-8) + Phase 4A de-escalation (session-stability)
# Escalation fires when the sniper is stuck at "actionable=True but action=hold".
stall_escape_partial_after_ticks = 20    # ~100s at 5s cadence → first escape
stall_escape_full_after_ticks = 40       # ~200s at 5s cadence → escalate to full
# After any escape emission the stall method waits this long before emitting again.
# Stops the 20x PARTIAL_CLOSE_UNSUPPORTED warning spam observed 2026-04-24.
stall_escape_cooldown_seconds = 30
# After this many tighten_agg downgrades without PnL recovery of at least
# stall_recovery_threshold_pct from the worst-observed PnL, escalate to full_close.
stall_tighten_max_applications = 3
stall_recovery_threshold_pct = 0.15

# Logging / DB write throttle (Phase 10)
log_every_n_ticks = 6                   # M4_EVAL log every 30s (6 × 5s)
log_always_above_score = 50             # Always log if composite score >= this
sniper_log_write_every_n_ticks = 6      # Write to DB every 30s minimum

# Legacy classification thresholds — used by _classify_score() for M7 labels
score_watch = 30
score_consult_claude = 50
score_auto_partial = 70
score_auto_full = 85

# Legacy profit/immunity filters — used by _classify_score()
min_profit_pct = 0.8
min_profit_for_action = 0.10             # Min PnL% before Mode4 Phase 9 takes action
profit_immunity_seconds = 60
loss_immunity_seconds = 30
full_rules_after_seconds = 300

# Legacy cooldowns — used by is_in_cooldown() / _is_safe_to_execute()
cooldown_extreme_seconds = 300
cooldown_strong_seconds = 180
cooldown_medium_seconds = 120

# Legacy Claude settings — kept for _consult_claude() method
claude_timeout_seconds = 15
max_claude_queries_per_hour = 10
claude_hold_recheck_seconds = 30

# Legacy model weights (must total 100) — used for M7 counterfactual snapshot
weight_zscore = 25
weight_velocity = 25
weight_volume = 20
weight_bollinger = 15
weight_momentum = 15

# Legacy flash crash protection
flash_crash_auto_score = 70

# TRADE LIBERATION: Trail distance floors + activation threshold
min_trail_atr_multiplier = 1.5              # Min trail = 1.5 × ATR (noise floor)
min_trail_pct = 0.30                        # Min trail as % of entry price
min_profit_for_trail_pct = 0.30             # Min peak PnL% before trail activates
min_profit_decay = 0.50                     # Floor for profit_decay factor

# =============================================================================
# TIAS Phase 2 — DeepSeek Post-Trade Analysis via OpenRouter
# =============================================================================
[tias]
enabled = true
primary_model = "deepseek/deepseek-chat-v3-0324"
fallback_model = "deepseek/deepseek-chat"
temperature = 0.3
max_tokens = 1500
timeout_seconds = 45
max_retries = 1
analysis_version = 1

# =============================================================================
# APEX — Aggressive Profit Extraction & Exploitation (via OpenRouter)
# =============================================================================
[apex]
enabled = true
model = "deepseek/deepseek-v3.2"
fallback_model = "deepseek/deepseek-chat"
# Layer 3: was 30s; DeepSeek frequently responds at 30-32s causing APEX_TIMEOUT.
timeout_seconds = 60
max_tokens = 800
temperature = 0.2
max_position_size_usd = 1200
max_leverage = 5
min_tias_trades_for_optimization = 3
min_regime_trades_for_fallback = 10

# Guardrails
min_tp_pct = 0.3
gate_tp_floor_enabled = true
gate_trail_activation_floor_pct_of_tp = 15.0
gate_trail_distance_floor_pct = 40.0
gate_mode_override_enabled = true
gate_confidence_floor = 0.50
# Hard size-cap: APEX/conviction inflation cannot exceed 1.5× Claude's
# pre-APEX directive size. Gate CHECK 0 enforces this and logs
# CONVICTION_SIZE_CAP when it binds. Set 0 to disable.
gate_apex_size_cap_mult = 1.5

# Conviction Allocator
conviction_enabled = true
conviction_min_trades = 3

# Per-class TP cap multiplier (× recommended_tp_pct from volatility profiler).
# Applied after DeepSeek responds; clamps absurd TPs back to class-appropriate
# room. Dead coins cap near 1.2× base (~0.36-0.60%); extreme coins stretch to
# 1.5× (~7.5%). See optimizer.py APEX_TP_CAP log.
[apex.tp_cap_multiplier_by_class]
dead = 1.2
low = 1.3
medium = 1.3
high = 1.4
extreme = 1.5

# =============================================================================
# SENTINEL — Exit Firewall + Smart Deadline + Portfolio Advisor
# =============================================================================
[sentinel]
enabled = true

# Part 1: Exit Firewall — blocks strategic review from closing positions
firewall_enabled = true

# Part 2: Deadline Engine — tiered expiry logic based on PnL
deadline_profit_pct = 0.5
deadline_breakeven_lower_pct = -0.3
deadline_small_loss_pct = -1.5
deadline_grace_minutes = 5.0
deadline_small_loss_sl_pct = 0.5

# Part 3: Portfolio Advisor — DeepSeek V3 risk assessment every 5 min
advisor_enabled = true
advisor_interval_seconds = 300
advisor_model = "deepseek/deepseek-chat-v3-0324"
advisor_temperature = 0.2
advisor_max_tokens = 800
advisor_timeout_seconds = 30
# TRADE LIBERATION: Min profit before allowing stop tightening (%)
advisor_min_profit_for_tighten_pct = 0.50

# =============================================================================
# X-RAY — Structural Market Intelligence Engine
# =============================================================================
# Detects support/resistance, market structure (BOS/CHoCH), structural SL/TP.
# Runs as a background worker refreshing structural analysis per coin.

[analysis.structure]
enabled = true
worker_interval_seconds = 60
cache_ttl_seconds = 300
min_candles = 50
swing_lookbacks = [3, 5, 10]
cluster_pct = 0.3
min_touches = 2
max_levels_per_side = 5
ms_swing_lookback = 5
ms_min_swing_points = 3
sl_buffer_pct = 0.15
tp_buffer_pct = 0.10
min_rr_ratio = 2.0
sl_fallback_pct = 2.0
tp_fallback_pct = 4.0
# Phase 2: Smart Money Concepts
fvg_min_gap_pct = 0.1
fvg_max_age_candles = 50
ob_displacement_min = 0.6
ob_max_age_candles = 50
liq_equal_tolerance_pct = 0.05
liq_min_equal_count = 2
liq_round_number_step = 100.0
sweep_max_age_candles = 10
sweep_min_wick_pct = 0.3
# Phase 4: Intelligence
setup_scanner_mode = "supplement"
# Layer 1 universe alignment (Phase 6 cleanup): structure_worker now
# reads scanner.get_active_universe() directly — CoinDiscovery, the
# scan_full_market gate, and coin_refresh_interval are removed.
batch_size = 25
shadow_db_path = "../shadow/data/shadow.db"

# Layer 1 restructure Phase 2 — categorical setup classification
# thresholds. Conservative defaults; relax via Phase 9 observation.
[analysis.structure.setup_types]
fvg_ob_min_confluence = 0.7
structural_break_require_retest = true
sweep_min_displacement_pct = 0.5
range_breakout_min_compression_bars = 20
mtf_alignment_required = true

[analysis.volatility_profile]
enabled = true
# Phase 5 (P0-4 DB Contention): TTL 120 s + per-symbol jitter window of
# +/- 30 s spreads expirations across a full 60 s window. With 30 coins
# and uniform hash distribution that's ~1 expiration every 2 s instead
# of 30 in a single second. Eliminates the thundering-herd recompute
# storm that pegged WD_TICK_SLOW > 5 s.
cache_ttl_seconds = 120.0
jitter_range_seconds = 30
dead_threshold = 0.05
low_threshold = 0.15
medium_threshold = 0.40
high_threshold = 1.00
min_tp_pct = 0.30
min_sl_pct = 0.20
max_tp_pct = 8.0
max_sl_pct = 5.0

# =============================================================================
# Time-Decay Loser-Lane SL (5-model institutional exit intelligence)
# =============================================================================
# Runs inside PositionWatchdog only when pnl_pct < 0. Combined formula:
#   allowed = atr_room × time_factor × recovery × momentum × probability
#   allowed = max(allowed, min_allowed_loss_pct)  # 0.15% floor
#   allowed = min(allowed, original_sl_pct)       # never widen SL
# Force-closes when p_win < p_win_force_close. Propagates tighter-only SL
# via _push_sl_to_shadow (source="time_decay"). All scalar defaults come
# from TimeDecaySettings in settings.py; per-class overrides below.
[time_decay]
enabled = true

# Absolute-PnL-depth penalty on Bayesian p_win update. Catches slow bleeders
# whose tick-over-tick deepening stays <1 ATR (so the ATR-relative penalty
# never fires). At |pnl| > 1.5% a mild 0.90 multiplier applies per tick the
# loss deepens; at |pnl| > 3.0% a strong 0.70 multiplier applies.
p_win_abs_depth_threshold_pct = 1.5
p_win_abs_depth_strong_pct = 3.0
p_win_abs_depth_penalty = 0.90
p_win_abs_depth_strong_penalty = 0.70

# Per-class grace window (seconds before Time-Decay can act on a new loser).
# Slow bleeders (dead/low) act sooner — the whole point of the fix.
# Fast movers get more settling room so normal bar noise doesn't force exit.
[time_decay.grace_seconds_by_class]
dead = 30
low = 45
medium = 120
high = 180
extreme = 240

# Per-class ATR room multiplier (Model 2 — base atr_room = atr × mult).
# Dead coins stay tight (1.0×); extreme coins get 3.0× so huge-ATR swings
# don't trigger premature exits. `min_allowed_loss_pct = 0.15%` floor still
# applies post-combine so SL never goes below floor.
[time_decay.atr_room_multiplier_by_class]
dead = 1.0
low = 1.2
medium = 2.0
high = 2.5
extreme = 3.0


# ─── Layer 1 restructure Phase 1: observability ─────────────────────
# Standardized log-tag and cycle-tracker knobs. The defaults match
# blueprint Section 14 — 100-cycle in-memory history (≈8h at 5-min
# cadence), hourly flush to cycle_metrics, tick markers at INFO.
[observability]
cycle_tracker_history = 100
cycle_metrics_flush_seconds = 3600
log_tick_done_at_info = true

# ─── Phase 2 (post-Layer-1 fix): LayerManager safety knobs ────────────
# lm_attach_deadline_sec — hard deadline (seconds since OrderService
#   init) before the gate flips to fail-close for ALL purposes when
#   layer_manager is still None. Layer 4 close/SL normally bypass during
#   the bootstrap window so a watchdog close can still execute, but
#   exceeding the deadline implies attachment failure (LayerManager
#   never constructed) — at that point even Layer 4 cannot be allowed.
#   Default 60 s comfortably covers the observed boot ordering window
#   (≤ 5 s in production).
# state_sync_interval_sec — disk/memory layer state sync heartbeat
#   cadence. Every interval the LayerManager reads data/layer_state.json
#   and compares to layer_active in memory; a mismatch triggers a
#   recovery action (see [layer_manager.state_sync] below). Default 60 s
#   — fine for catching drift within one Strategy/Scanner cycle.
# state_sync.on_drift_action — Phase 11 (dead-workers fix). What the
#   heartbeat does when disk and memory disagree.
#     "rewrite_disk" (default, post-fix): memory wins. Re-persist
#       memory to disk; emit LAYER_STATE_DRIFT_RECOVERED. Correct
#       semantics — persist failures should be RECOVERED by re-
#       attempting persist, not by undoing the in-memory state.
#     "reload_memory" (legacy, pre-fix): disk wins. Overwrite memory
#       from disk; emit LAYER_STATE_DRIFT. This is the exact behaviour
#       that produced the Layer 3 toggle revert regression observed
#       on 2026-04-27 — only set this for emergency rollback.
[layer_manager]
lm_attach_deadline_sec = 60.0
state_sync_interval_sec = 60.0

[layer_manager.state_sync]
on_drift_action = "rewrite_disk"

# ── SignalGenerator multi-source classification (Phase 1 output-quality) ─────
# Pre-fix, _evaluate_signal() used sentiment as a HARD gate: every BUY/SELL
# rule required abs(sentiment) > 0.2. With sentiment=0.0 in 97.9% of coins
# (Reddit disabled, Finnhub no altcoin coverage, aggregator.py:165 zero-coverage
# rule), all signals fell through to NEUTRAL by design.
# Post-fix evaluator computes a weighted direction_score across 4 components
# (sentiment, F&G contrarian, funding rate, OI change). Each component is
# "active" only if abs(score) >= its min threshold; INACTIVE components are
# dropped (don't pull toward NEUTRAL). A coin with sentiment=0.0 but F&G=15
# and funding=-0.012 will now correctly classify BUY via F&G+funding alone.
[signal_generator.multi_source]
sentiment_min_active = 0.05
fg_min_active = 0.10
funding_min_active = 0.20
oi_min_active = 0.20
sentiment_weight = 0.40
fg_weight = 0.25
funding_weight = 0.20
oi_weight = 0.15
strong_threshold = 0.55
buy_threshold = 0.25
fg_normalize_range = 30.0
funding_normalize = 0.005
oi_normalize_pct = 5.0

# ── CoinPackage validator (Phase 5 output-quality) ──────────────────
# Validates each CoinPackage produced by ScannerWorker before it lands
# in layer_manager._coin_packages (which Stage 2 reads). Packages with
# completeness < fail_below are QUARANTINED (not included). warn_below
# packages still flow but are flagged in the per-package log.
# Score formula: (sum_required + 0.5*sum_optional) / (count_required +
# 0.5*count_optional). See src/core/coin_package_validator.py docstring
# for the full rule list.
[coin_package_validator]
fail_below = 0.50
warn_below = 0.85
staleness_fail_seconds = 300.0

# ── Worker liveness watchdog (Phase 11 dead-workers fix) ────────────
# Watchdog probes the per-worker liveness tracker every interval and
# emits WORKER_NEVER_TICKED / WORKER_TICK_OVERDUE warnings when a
# worker has registered but produced no first tick within the grace
# window, or has gone quiet for overdue_multiplier × expected_interval
# after its first tick. WORKER_LIVENESS_HEARTBEAT INFO log fires every
# tick regardless so workers.log has a continuous trail.
#
# Cycle-gate aware: cycle_gated workers (1B/1C/1D) that haven't ticked
# while LayerManager.is_cycle_active() is False are NOT alarmed —
# they're intentionally silent. Without this awareness the watchdog
# would false-alarm on the 5 cycle_gated workers every L3=OFF window.
[worker_liveness]
watchdog_interval_sec = 30
first_tick_grace_sec = 90
overdue_multiplier = 2.0
alert_rate_limit_sec = 3600
```

## End of file

Total sections present in config.toml:
- `[general]`, `[bybit]`, `[finnhub]`, `[reddit]`, `[altdata]`, `[database]`,
- `[workers]`, `[workers.sweet_spots]`, `[workers.sweet_spots.altdata]`,
- `[brain]`, `[risk]`, `[alerts]`, `[mcp]`, `[watchdog]`, `[mcp_pool]`, `[price]`,
- `[sl_gateway]`, `[sl_gateway.min_distance_class_ceiling]`,
- `[scanner]`, `[scanner.hysteresis]`, `[scanner.scoring_weights]`, `[scanner.qualitative]`,
- `[universe]`, `[regime]`, `[strategy_engine]`, `[pnl_targets]`, `[leverage]`,
- `[optimizer]`, `[factory]`, `[backtesting]`, `[trial]`, `[portfolio]`,
- `[telegram_interactive]`, `[fund_manager]`, `[enforcer]`, `[mode4]`,
- `[tias]`, `[apex]`, `[apex.tp_cap_multiplier_by_class]`,
- `[sentinel]`, `[analysis.structure]`, `[analysis.structure.setup_types]`,
- `[analysis.volatility_profile]`, `[time_decay]`, `[time_decay.grace_seconds_by_class]`,
- `[time_decay.atr_room_multiplier_by_class]`, `[observability]`,
- `[layer_manager]`, `[layer_manager.state_sync]`,
- `[signal_generator.multi_source]`, `[coin_package_validator]`, `[worker_liveness]`

There is NO `[news]`, `[stage2]`, or `[layer1c]` block in the file.
