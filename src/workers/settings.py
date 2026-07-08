"""Configuration loader: reads config.toml + .env and maps to typed dataclasses.

Environment variables override config.toml values. Provides a singleton via
Settings.load() for convenience, while keeping constructors injectable for testing.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# tomli is in stdlib as tomllib from Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from src.core.exceptions import ConfigError


def _env(key: str, default: str = "") -> str:
    """Read an environment variable with fallback."""
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    """Read an environment variable as boolean."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() in ("true", "1", "yes")


@dataclass
class GeneralSettings:
    """Top-level general configuration."""
    mode: str = "paper"
    shadow_api_url: str = "http://127.0.0.1:9090"
    timezone: str = "UTC"
    log_level: str = "INFO"
    log_dir: str = "data/logs"


@dataclass
class BybitSettings:
    """Bybit exchange connection settings."""
    testnet: bool = True
    default_symbols: list[str] = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
    )
    rate_limit_per_second: int = 10
    ws_ping_interval: int = 20
    ws_reconnect_delay: int = 5
    recv_window: int = 5000
    api_key: str = ""
    api_secret: str = ""

    @property
    def base_url(self) -> str:
        """REST API base URL based on testnet flag."""
        if self.testnet:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    @property
    def ws_url(self) -> str:
        """WebSocket URL based on testnet flag."""
        if self.testnet:
            return "wss://stream-testnet.bybit.com"
        return "wss://stream.bybit.com"


@dataclass
class FinnhubSettings:
    """Finnhub news API settings."""
    enabled: bool = True
    rate_limit_per_minute: int = 60
    news_categories: list[str] = field(default_factory=lambda: ["crypto", "general"])
    max_articles_per_fetch: int = 50
    api_key: str = ""


@dataclass
class RedditSettings:
    """Reddit/PRAW sentiment settings."""
    enabled: bool = True
    subreddits: list[str] = field(
        default_factory=lambda: ["cryptocurrency", "bitcoin", "ethtrader"]
    )
    max_posts_per_sub: int = 25
    min_score: int = 10
    rate_limit_per_minute: int = 60
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    password: str = ""


@dataclass
class AltDataSettings:
    """Alternative data source settings."""
    enabled: bool = True
    fear_greed_interval: int = 3600
    funding_rate_interval: int = 300
    open_interest_interval: int = 600
    coingecko_rate_limit_per_minute: int = 10


@dataclass
class DatabaseSettings:
    """SQLite / future PostgreSQL settings."""
    path: str = "data/trading.db"
    wal_mode: bool = True
    pool_size: int = 5
    query_timeout: int = 30
    vacuum_interval: int = 24


@dataclass
class WorkerSettings:
    """Background worker configuration."""
    enabled: bool = True
    market_data_interval: int = 60
    news_interval: int = 300
    reddit_interval: int = 600
    altdata_interval: int = 300
    health_check_interval: int = 60
    max_consecutive_failures: int = 5
    restart_delay: int = 10


@dataclass
class BrainSettings:
    """Claude Brain autonomous trading configuration."""
    enabled: bool = False
    use_claude_code: bool = True
    strategic_interval: int = 180
    watchdog_interval: int = 30
    analysis_interval: int = 1800
    signal_triggered: bool = True
    min_signal_confidence: float = 0.7
    max_calls_per_hour: int = 10
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.3
    api_key: str = ""


@dataclass
class RiskSettings:
    """Risk management parameters (non-negotiable)."""
    max_leverage: int = 3
    mandatory_stop_loss: bool = True
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    max_position_size_pct: float = 10.0
    max_open_positions: int = 5
    daily_loss_limit_pct: float = 5.0
    max_total_exposure_pct: float = 50.0
    max_drawdown_pct: float = 15.0
    min_order_value_usdt: float = 10.0
    loss_cooldown_seconds: int = 300


@dataclass
class AlertSettings:
    """Telegram alert configuration."""
    telegram_enabled: bool = False
    alert_levels: list[str] = field(default_factory=lambda: ["WARNING", "CRITICAL"])
    daily_summary: bool = True
    daily_summary_time: str = "00:00"
    max_alerts_per_minute: int = 10
    trade_alerts: bool = True
    signal_alerts: bool = True
    error_alerts: bool = True
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class WatchdogSettings:
    """Position watchdog configuration."""
    enabled: bool = True
    check_interval_seconds: float = 10.0
    loss_warning_pct: float = 1.0
    trailing_loss_pct: float = 0.5
    sl_proximity_pct: float = 30.0
    rapid_move_pct: float = 0.5
    brain_trigger_loss_pct: float = 1.5
    brain_cooldown_seconds: int = 120
    partial_close_pct: float = 50.0
    max_brain_calls_per_hour: int = 10
    timeout_threshold_pct: float = 95.0  # % of max_hold_minutes before timeout close
    early_exit_enabled: bool = False  # 0% historical win rate (24/24 losses) — SL handles exits; flip true to re-enable


@dataclass
class ScannerSettings:
    """Market scanner configuration."""
    enabled: bool = True
    scan_interval_seconds: int = 300
    min_volume_24h: float = 50_000_000
    max_coins: int = 15
    max_spread_pct: float = 0.1


@dataclass
class RegimeSettings:
    """Market regime detector configuration."""
    detection_interval_seconds: int = 300
    primary_symbol: str = "BTCUSDT"
    trending_adx_threshold: float = 25.0
    ranging_adx_threshold: float = 20.0
    ranging_choppiness_threshold: float = 60.0
    volatile_atr_percentile: float = 150.0
    dead_adx_threshold: float = 15.0
    dead_volume_ratio: float = 0.5


@dataclass
class StrategyEngineSettings:
    """4-layer strategy engine configuration."""
    scan_interval_seconds: int = 60
    min_score_threshold: float = 70.0
    min_ensemble_agreement: float = 5.0
    max_ensemble_opposition: float = 1.0
    max_setups_to_brain: int = 3
    max_brain_calls_per_hour: int = 12


@dataclass
class PnLTargetSettings:
    """Daily PnL target and risk scaling."""
    daily_target_pct: float = 5.0
    protect_threshold_pct: float = 3.0
    caution_threshold_pct: float = -1.0
    survival_threshold_pct: float = -3.0
    halt_threshold_pct: float = -5.0


@dataclass
class LeverageSettings:
    """Smart leverage configuration."""
    max_leverage: int = 5
    tier_1_max: int = 5
    tier_2_max: int = 4
    tier_3_max: int = 3
    volatile_max: int = 3
    dead_max: int = 2
    min_confidence_for_5x: float = 0.85
    min_confidence_for_4x: float = 0.75


@dataclass
class OptimizerSettings:
    """Weekly adaptive optimizer configuration."""
    enabled: bool = True
    run_day: str = "sunday"
    run_hour_utc: int = 0
    weight_adjustment_pct: float = 10.0
    max_param_change_pct: float = 20.0
    min_trades_for_optimization: int = 20
    underperform_threshold_pct: float = 10.0
    disable_after_weeks: int = 3


@dataclass
class FactorySettings:
    """Strategy Factory configuration."""
    enabled: bool = True
    discovery_schedule_hour_utc: int = 2
    discovery_lookback_days: int = 30
    min_pattern_occurrences: int = 20
    min_win_rate: float = 0.55
    min_profit_factor: float = 1.2
    min_statistical_significance: float = 0.05
    max_strategies_per_batch: int = 5
    max_generation_retries: int = 3
    generation_cost_limit_usd: float = 0.20
    live_monitor_interval_seconds: int = 300
    hot_pattern_threshold_win_rate: float = 0.70
    hot_pattern_threshold_occurrences: int = 5
    emergency_generation_enabled: bool = True


@dataclass
class BacktestSettings:
    """Backtesting engine configuration."""
    initial_capital: float = 10000.0
    default_leverage: int = 3
    commission_pct: float = 0.06
    slippage_pct: float = 0.02
    funding_rate_pct: float = 0.01
    walk_forward_enabled: bool = True
    train_pct: float = 0.70
    monte_carlo_runs: int = 1000
    min_trades_to_pass: int = 30
    min_win_rate: float = 0.52
    min_profit_factor: float = 1.3
    max_drawdown_pct: float = 15.0
    min_sharpe: float = 0.5
    min_walk_forward_efficiency: float = 0.5
    max_ruin_probability: float = 0.05


@dataclass
class TrialSettings:
    """Paper trading trial configuration."""
    trial_duration_days: int = 14
    max_extensions: int = 1
    extension_duration_days: int = 7
    trial_position_size_pct: float = 25.0
    min_trades_for_evaluation: int = 10
    promotion_min_win_rate: float = 0.50
    promotion_min_pnl: float = 0.0
    promotion_max_drawdown: float = 10.0
    max_active_strategies: int = 60
    demotion_underperform_weeks: int = 2
    demotion_win_rate_drop_pct: float = 15.0
    quarterly_revival_enabled: bool = True


@dataclass
class PortfolioSettings:
    """Portfolio optimizer configuration."""
    enabled: bool = True
    optimization_day: str = "sunday"
    optimization_hour_utc: int = 0
    kelly_fraction: float = 0.25
    min_trades_for_kelly: int = 20
    max_strategy_allocation_pct: float = 10.0
    min_strategy_allocation_pct: float = 1.0
    proven_strategies_budget_pct: float = 55.0
    ai_strategies_budget_pct: float = 30.0
    trial_strategies_budget_pct: float = 10.0
    cash_reserve_pct: float = 5.0
    correlation_lookback_days: int = 30
    high_correlation_threshold: float = 0.7
    daily_risk_budget_pct: float = 5.0
    drawdown_reduction_threshold_1: float = 5.0
    drawdown_reduction_factor_1: float = 0.7
    drawdown_reduction_threshold_2: float = 10.0
    drawdown_reduction_factor_2: float = 0.4
    kelly_weight: float = 0.30
    mean_variance_weight: float = 0.40
    risk_parity_weight: float = 0.30
    min_rebalance_change_pct: float = 2.0
    stress_test_enabled: bool = True


@dataclass
class TelegramInteractiveSettings:
    """Interactive Telegram bot configuration."""
    enabled: bool = True
    ai_responses_enabled: bool = True
    max_ai_calls_per_hour: int = 20
    trade_confirmation_required: bool = True
    morning_briefing_enabled: bool = True
    morning_briefing_hour_utc: int = 5
    price_alert_check_interval: int = 10


@dataclass
class MCPSettings:
    """MCP server transport configuration."""
    transport: str = "stdio"
    sse_host: str = "0.0.0.0"
    sse_port: int = 8080
    sse_auth_required: bool = True
    server_name: str = "trading-intelligence"
    server_version: str = "0.1.0"
    auth_token: str = ""


@dataclass
class EnforcerSettings:
    """Enforcer v2 — PnL-Based Intelligent Throttling."""
    enabled: bool = True
    check_interval_seconds: int = 300

    # PnL-based thresholds (daily PnL %)
    pnl_caution_pct: float = -2.0       # Below this → el=1 (capital preservation)
    pnl_survival_pct: float = -5.0      # Below this → el=2 (survival)

    # Size reduction for mild negative PnL
    size_reduction_enabled: bool = True   # Toggle size reduction on/off
    size_reduction_at_pnl_pct: float = 0.0  # Start reducing below this PnL %
    size_reduction_factor: float = 0.75  # Multiplier when PnL is between 0% and caution

    # Streak as secondary signal (only when PnL is negative)
    streak_boost_threshold: int = -5     # 5-loss streak + negative PnL → immediate el=1

    # Auto-recovery
    max_enforcement_minutes: int = 45    # Auto-recover after stuck at el>=1 for this long
    grace_period_minutes: int = 30       # Manual reset grace period (full skip)

    # Per-level restrictions (configurable)
    level_1_max_positions: int = 3
    level_1_max_leverage: int = 3
    level_1_min_score: int = 75
    level_2_max_positions: int = 2
    level_2_max_leverage: int = 3
    level_2_min_score: int = 80
    level_2_min_confluence: int = 7
    level_2_min_rr: float = 3.0

    # Legacy fields (kept for backward compatibility with config.toml)
    decay_minutes: int = 60
    min_trades_per_hour: int = 50
    min_profit_per_hour_pct: float = 10.0
    min_win_rate: float = 0.55
    min_signals_per_hour: int = 100
    min_setups_to_brain_per_hour: int = 20
    max_seconds_between_trades: int = 180
    max_escalation_level: int = 5
    force_trade_on_gap: bool = True
    rewards_enabled: bool = True
    hourly_report_enabled: bool = True


@dataclass
class Mode4Settings:
    """Mode 4 ProfitSniper — institutional-grade profit protection (Phase 1-10)."""

    enabled: bool = True
    check_interval_seconds: int = 5

    # Ring Buffer (Phase 1)
    buffer_max_size: int = 720          # 60 minutes at 5s intervals
    buffer_min_ready: int = 100         # Minimum points for model validity (8+ min)

    # Trailing System (Phase 8)
    base_atr_multiplier: float = 2.5    # Chandelier Exit base width in ATR units
    trail_min_change_pct: float = 0.1   # Min SL change % to avoid Shadow flooding

    # Regime trail factors (Phase 8) — must match REGIME_TRAIL_FACTORS constant
    regime_factor_trending: float = 1.3
    regime_factor_ranging: float = 0.7
    regime_factor_volatile: float = 1.0
    regime_factor_dead: float = 0.6

    # Anti-Greed (Phase 9) — pullback backstop
    anti_greed_enabled: bool = True
    anti_greed_pullback_40_min_peak: float = 2.0   # Min peak % for 40% pullback → tighten
    anti_greed_pullback_60_min_peak: float = 3.0   # Min peak % for 60% pullback → partial
    anti_greed_pullback_75_min_peak: float = 5.0   # Min peak % for 75% pullback → full close

    # Action cooldowns (Phase 9)
    tighten_cooldown_seconds: int = 30
    partial_close_cooldown_seconds: int = 120
    partial_close_pct: int = 50         # % of position to close on partial action

    # Logging / DB write throttle (Phase 10)
    log_every_n_ticks: int = 6          # M4_EVAL log every 30s (6 × 5s)
    log_always_above_score: int = 50    # Always log if composite score >= this
    sniper_log_write_every_n_ticks: int = 6  # DB write every 30s minimum

    # Legacy classification thresholds — used by _classify_score() for M7 labels
    score_watch: int = 30
    score_consult_claude: int = 50
    score_auto_partial: int = 70
    score_auto_full: int = 85

    # Legacy profit/immunity filters — used by _classify_score()
    min_profit_pct: float = 0.8
    min_profit_for_action: float = 0.10  # Min PnL% before Mode4 Phase 9 takes any action
    min_profit_for_close: float = 0.50  # Min PnL% before P9 can full_close (prevents killing tiny winners)
    profit_immunity_seconds: int = 60
    loss_immunity_seconds: int = 30
    full_rules_after_seconds: int = 300

    # Legacy cooldowns — used by is_in_cooldown() / _is_safe_to_execute()
    cooldown_extreme_seconds: int = 300
    cooldown_strong_seconds: int = 180
    cooldown_medium_seconds: int = 120

    # Legacy Claude settings — kept for _consult_claude() method
    claude_timeout_seconds: int = 15
    max_claude_queries_per_hour: int = 10
    claude_hold_recheck_seconds: int = 30

    # Legacy model weights — used for z_pts/vel_pts in last_score snapshot (M7)
    weight_zscore: int = 25
    weight_velocity: int = 25
    weight_volume: int = 20
    weight_bollinger: int = 15
    weight_momentum: int = 15

    # Legacy flash crash protection
    flash_crash_auto_score: int = 70

    # TRADE LIBERATION: Trail distance floors + activation threshold
    min_trail_atr_multiplier: float = 1.5   # Min trail = this × ATR (noise floor)
    min_trail_pct: float = 0.30             # Min trail as % of entry price (absolute floor)
    min_profit_for_trail_pct: float = 0.30  # Min peak PnL% before trail activates
    min_profit_decay: float = 0.50          # Floor for profit_decay factor


@dataclass
class FundManagerSettings:
    """Intelligent Fund Manager configuration."""
    enabled: bool = True
    check_interval_seconds: int = 60
    starting_unlock_pct: float = 20.0
    active_pool_pct: float = 70.0
    aplus_reserve_pct: float = 20.0
    emergency_reserve_pct: float = 10.0
    profit_lock_pct: float = 50.0
    trade_profit_lock_pct: float = 25.0
    max_correlation_bucket_pct: float = 30.0
    min_profitable_trade_fee_pct: float = 0.12


@dataclass
class TIASSettings:
    """TIAS Phase 2 — DeepSeek post-trade analysis via OpenRouter."""
    enabled: bool = False
    api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    primary_model: str = "deepseek/deepseek-chat-v3-0324"
    fallback_model: str = "deepseek/deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 1500
    timeout_seconds: int = 45
    max_retries: int = 1
    http_referer: str = "https://github.com/trading-intelligence-mcp"
    x_title: str = "TIAS-TradeAnalysis"
    analysis_version: int = 1
    api_key: str = ""


@dataclass
class APEXSettings:
    """APEX — DeepSeek-based post-decision trade optimization via OpenRouter."""
    enabled: bool = False
    api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    model: str = "deepseek/deepseek-v3.2"
    fallback_model: str = "deepseek/deepseek-v3.2"
    timeout_seconds: int = 60
    max_tokens: int = 800
    temperature: float = 0.2
    max_position_size_usd: float = 1200.0
    max_leverage: int = 5
    min_tias_trades_for_optimization: int = 3
    min_regime_trades_for_fallback: int = 10  # Regime-wide fallback threshold
    http_referer: str = "https://github.com/trading-intelligence-mcp"
    x_title: str = "APEX-TradeOptimizer"
    api_key: str = ""

    # Guardrails (Phase A3)
    min_tp_pct: float = 0.3
    gate_tp_floor_enabled: bool = True
    gate_trail_activation_floor_pct_of_tp: float = 15.0
    gate_trail_distance_floor_pct: float = 40.0
    gate_mode_override_enabled: bool = True
    gate_confidence_floor: float = 0.50

    # Conviction Allocator (Phase B)
    conviction_enabled: bool = True
    conviction_min_trades: int = 3


@dataclass
class SentinelSettings:
    """SENTINEL — Exit Firewall + Deadline Engine + Portfolio Advisor."""
    enabled: bool = True

    # Part 1: Exit Firewall — blocks strategic review from closing positions
    firewall_enabled: bool = True

    # Part 2: Deadline Engine — tiered expiry logic based on PnL
    deadline_profit_pct: float = 0.5
    deadline_breakeven_lower_pct: float = -0.3
    deadline_small_loss_pct: float = -1.5
    deadline_grace_minutes: float = 5.0
    deadline_small_loss_sl_pct: float = 0.5

    # Part 3: Portfolio Advisor — DeepSeek V3 risk assessment
    advisor_enabled: bool = False
    advisor_interval_seconds: int = 300
    advisor_model: str = "deepseek/deepseek-chat-v3-0324"
    advisor_temperature: float = 0.2
    advisor_max_tokens: int = 800
    advisor_timeout_seconds: int = 30
    advisor_api_key: str = ""

    # TRADE LIBERATION: Min profit before allowing stop tightening
    advisor_min_profit_for_tighten_pct: float = 0.50


@dataclass
class StructureSettings:
    """X-RAY Structural Intelligence configuration."""
    enabled: bool = True
    worker_interval_seconds: int = 60
    cache_ttl_seconds: int = 300
    min_candles: int = 50
    swing_lookbacks: list[int] = field(default_factory=lambda: [3, 5, 10])
    cluster_pct: float = 0.3
    min_touches: int = 2
    max_levels_per_side: int = 5
    ms_swing_lookback: int = 5
    ms_min_swing_points: int = 3
    sl_buffer_pct: float = 0.15
    tp_buffer_pct: float = 0.10
    min_rr_ratio: float = 2.0
    sl_fallback_pct: float = 2.0
    tp_fallback_pct: float = 4.0
    # Phase 2: Smart Money Concepts
    fvg_min_gap_pct: float = 0.1
    fvg_max_age_candles: int = 50
    ob_displacement_min: float = 0.6
    ob_max_age_candles: int = 50
    liq_equal_tolerance_pct: float = 0.05
    liq_min_equal_count: int = 2
    liq_round_number_step: float = 100.0
    sweep_max_age_candles: int = 10
    sweep_min_wick_pct: float = 0.3
    # Phase 4: Intelligence
    setup_scanner_mode: str = "supplement"  # "supplement" or "replace"
    # Market Dominance: Full market scanning
    scan_full_market: bool = True
    batch_size: int = 25
    coin_refresh_interval: int = 600
    shadow_db_path: str = "../shadow/data/shadow.db"


@dataclass
class VolatilityProfileSettings:
    """Per-coin volatility profiling — adaptive TP/SL/hold per coin's ATR."""
    enabled: bool = True
    cache_ttl_seconds: float = 60.0
    # Volatility class boundaries (ATR% on 5-min candles)
    dead_threshold: float = 0.05
    low_threshold: float = 0.15
    medium_threshold: float = 0.40
    high_threshold: float = 1.00
    # TP/SL floors and caps
    min_tp_pct: float = 0.30
    min_sl_pct: float = 0.20
    max_tp_pct: float = 8.0
    max_sl_pct: float = 5.0


@dataclass
class Settings:
    """Top-level settings container holding all sub-configurations."""
    general: GeneralSettings = field(default_factory=GeneralSettings)
    bybit: BybitSettings = field(default_factory=BybitSettings)
    finnhub: FinnhubSettings = field(default_factory=FinnhubSettings)
    reddit: RedditSettings = field(default_factory=RedditSettings)
    altdata: AltDataSettings = field(default_factory=AltDataSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    workers: WorkerSettings = field(default_factory=WorkerSettings)
    brain: BrainSettings = field(default_factory=BrainSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)
    watchdog: WatchdogSettings = field(default_factory=WatchdogSettings)
    scanner: ScannerSettings = field(default_factory=ScannerSettings)
    regime: RegimeSettings = field(default_factory=RegimeSettings)
    strategy_engine: StrategyEngineSettings = field(default_factory=StrategyEngineSettings)
    pnl_targets: PnLTargetSettings = field(default_factory=PnLTargetSettings)
    leverage: LeverageSettings = field(default_factory=LeverageSettings)
    optimizer: OptimizerSettings = field(default_factory=OptimizerSettings)
    factory: FactorySettings = field(default_factory=FactorySettings)
    backtesting: BacktestSettings = field(default_factory=BacktestSettings)
    trial: TrialSettings = field(default_factory=TrialSettings)
    portfolio: PortfolioSettings = field(default_factory=PortfolioSettings)
    telegram_interactive: TelegramInteractiveSettings = field(default_factory=TelegramInteractiveSettings)
    enforcer: EnforcerSettings = field(default_factory=EnforcerSettings)
    mode4: Mode4Settings = field(default_factory=Mode4Settings)
    fund_manager: FundManagerSettings = field(default_factory=FundManagerSettings)
    mcp: MCPSettings = field(default_factory=MCPSettings)
    tias: TIASSettings = field(default_factory=TIASSettings)
    apex: APEXSettings = field(default_factory=APEXSettings)
    sentinel: SentinelSettings = field(default_factory=SentinelSettings)
    structure: StructureSettings = field(default_factory=StructureSettings)
    volatility_profile: VolatilityProfileSettings = field(default_factory=VolatilityProfileSettings)

    _instance: "Settings | None" = field(default=None, init=False, repr=False)

    @classmethod
    def load(
        cls,
        config_path: str = "config.toml",
        env_path: str = ".env",
    ) -> "Settings":
        """Load configuration from config.toml and .env, returning a singleton.

        Args:
            config_path: Path to TOML config file.
            env_path: Path to .env file.

        Returns:
            Fully populated Settings instance.

        Raises:
            ConfigError: If config.toml cannot be read or parsed.
        """
        if cls._instance is not None:
            return cls._instance

        instance = cls._load_fresh(config_path, env_path)
        cls._instance = instance
        return instance

    @classmethod
    def _load_fresh(
        cls,
        config_path: str = "config.toml",
        env_path: str = ".env",
    ) -> "Settings":
        """Load config without caching (useful for testing).

        Args:
            config_path: Path to TOML config file.
            env_path: Path to .env file.

        Returns:
            New Settings instance.
        """
        # Load .env first so env vars are available
        load_dotenv(env_path, override=True)

        # Load TOML config
        toml_data: dict[str, Any] = {}
        config_file = Path(config_path)
        if config_file.exists():
            try:
                with open(config_file, "rb") as f:
                    toml_data = tomllib.load(f)
            except Exception as e:
                raise ConfigError(
                    f"Failed to parse {config_path}: {e}",
                    details={"path": config_path},
                )
        else:
            # No config file — use all defaults
            pass

        # Build each settings section from TOML + env overrides
        general = _build_general(toml_data.get("general", {}))
        bybit = _build_bybit(toml_data.get("bybit", {}))
        finnhub = _build_finnhub(toml_data.get("finnhub", {}))
        reddit = _build_reddit(toml_data.get("reddit", {}))
        altdata = _build_altdata(toml_data.get("altdata", {}))
        database = _build_database(toml_data.get("database", {}))
        workers = _build_workers(toml_data.get("workers", {}))
        brain = _build_brain(toml_data.get("brain", {}))
        risk = _build_risk(toml_data.get("risk", {}))
        alerts = _build_alerts(toml_data.get("alerts", {}))
        watchdog = _build_watchdog(toml_data.get("watchdog", {}))
        scanner = _build_scanner(toml_data.get("scanner", {}))
        regime = _build_regime(toml_data.get("regime", {}))
        strategy_engine = _build_strategy_engine(toml_data.get("strategy_engine", {}))
        pnl_targets = _build_pnl_targets(toml_data.get("pnl_targets", {}))
        leverage_cfg = _build_leverage(toml_data.get("leverage", {}))
        optimizer = _build_optimizer(toml_data.get("optimizer", {}))
        factory = _build_factory(toml_data.get("factory", {}))
        backtesting = _build_backtesting(toml_data.get("backtesting", {}))
        trial_cfg = _build_trial(toml_data.get("trial", {}))
        portfolio = _build_portfolio(toml_data.get("portfolio", {}))
        telegram_interactive = _build_telegram_interactive(toml_data.get("telegram_interactive", {}))
        enforcer_cfg = _build_enforcer(toml_data.get("enforcer", {}))
        mode4_cfg = _build_mode4(toml_data.get("mode4", {}))
        fund_manager_cfg = _build_fund_manager(toml_data.get("fund_manager", {}))
        mcp = _build_mcp(toml_data.get("mcp", {}))
        tias_cfg = _build_tias(toml_data.get("tias", {}))
        apex_cfg = _build_apex(toml_data.get("apex", {}))
        sentinel_cfg = _build_sentinel(toml_data.get("sentinel", {}))
        structure_cfg = _build_structure(toml_data.get("analysis", {}).get("structure", {}))
        volatility_profile_cfg = _build_volatility_profile(
            toml_data.get("analysis", {}).get("volatility_profile", {})
        )

        return cls(
            general=general,
            bybit=bybit,
            finnhub=finnhub,
            reddit=reddit,
            altdata=altdata,
            database=database,
            workers=workers,
            brain=brain,
            risk=risk,
            alerts=alerts,
            watchdog=watchdog,
            scanner=scanner,
            regime=regime,
            strategy_engine=strategy_engine,
            pnl_targets=pnl_targets,
            leverage=leverage_cfg,
            optimizer=optimizer,
            factory=factory,
            backtesting=backtesting,
            trial=trial_cfg,
            portfolio=portfolio,
            telegram_interactive=telegram_interactive,
            enforcer=enforcer_cfg,
            mode4=mode4_cfg,
            fund_manager=fund_manager_cfg,
            mcp=mcp,
            tias=tias_cfg,
            apex=apex_cfg,
            sentinel=sentinel_cfg,
            structure=structure_cfg,
            volatility_profile=volatility_profile_cfg,
        )

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton instance (for testing)."""
        cls._instance = None


# =============================================================================
# Section builders: TOML dict → dataclass, with env var overrides
# =============================================================================

def _build_general(data: dict[str, Any]) -> GeneralSettings:
    return GeneralSettings(
        mode=_env("TRADING_MODE", data.get("mode", "paper")),
        shadow_api_url=data.get("shadow_api_url", "http://127.0.0.1:9090"),
        timezone=data.get("timezone", "UTC"),
        log_level=_env("LOG_LEVEL", data.get("log_level", "INFO")),
        log_dir=data.get("log_dir", "data/logs"),
    )


def _build_bybit(data: dict[str, Any]) -> BybitSettings:
    return BybitSettings(
        testnet=data.get("testnet", True),
        default_symbols=data.get(
            "default_symbols",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"],
        ),
        rate_limit_per_second=data.get("rate_limit_per_second", 10),
        ws_ping_interval=data.get("ws_ping_interval", 20),
        ws_reconnect_delay=data.get("ws_reconnect_delay", 5),
        recv_window=data.get("recv_window", 5000),
        api_key=_env("BYBIT_API_KEY", data.get("api_key", "")),
        api_secret=_env("BYBIT_API_SECRET", data.get("api_secret", "")),
    )


def _build_finnhub(data: dict[str, Any]) -> FinnhubSettings:
    return FinnhubSettings(
        enabled=data.get("enabled", True),
        rate_limit_per_minute=data.get("rate_limit_per_minute", 60),
        news_categories=data.get("news_categories", ["crypto", "general"]),
        max_articles_per_fetch=data.get("max_articles_per_fetch", 50),
        api_key=_env("FINNHUB_API_KEY", data.get("api_key", "")),
    )


def _build_reddit(data: dict[str, Any]) -> RedditSettings:
    return RedditSettings(
        enabled=data.get("enabled", True),
        subreddits=data.get("subreddits", ["cryptocurrency", "bitcoin", "ethtrader"]),
        max_posts_per_sub=data.get("max_posts_per_sub", 25),
        min_score=data.get("min_score", 10),
        rate_limit_per_minute=data.get("rate_limit_per_minute", 60),
        client_id=_env("REDDIT_CLIENT_ID", data.get("client_id", "")),
        client_secret=_env("REDDIT_CLIENT_SECRET", data.get("client_secret", "")),
        username=_env("REDDIT_USERNAME", data.get("username", "")),
        password=_env("REDDIT_PASSWORD", data.get("password", "")),
    )


def _build_altdata(data: dict[str, Any]) -> AltDataSettings:
    return AltDataSettings(
        enabled=data.get("enabled", True),
        fear_greed_interval=data.get("fear_greed_interval", 3600),
        funding_rate_interval=data.get("funding_rate_interval", 300),
        open_interest_interval=data.get("open_interest_interval", 600),
        coingecko_rate_limit_per_minute=data.get("coingecko_rate_limit_per_minute", 10),
    )


def _build_database(data: dict[str, Any]) -> DatabaseSettings:
    return DatabaseSettings(
        path=_env("DATABASE_PATH", data.get("path", "data/trading.db")),
        wal_mode=data.get("wal_mode", True),
        pool_size=data.get("pool_size", 5),
        query_timeout=data.get("query_timeout", 30),
        vacuum_interval=data.get("vacuum_interval", 24),
    )


def _build_workers(data: dict[str, Any]) -> WorkerSettings:
    return WorkerSettings(
        enabled=data.get("enabled", True),
        market_data_interval=data.get("market_data_interval", 60),
        news_interval=data.get("news_interval", 300),
        reddit_interval=data.get("reddit_interval", 600),
        altdata_interval=data.get("altdata_interval", 300),
        health_check_interval=data.get("health_check_interval", 60),
        max_consecutive_failures=data.get("max_consecutive_failures", 5),
        restart_delay=data.get("restart_delay", 10),
    )


def _build_brain(data: dict[str, Any]) -> BrainSettings:
    return BrainSettings(
        enabled=data.get("enabled", False),
        analysis_interval=data.get("analysis_interval", 1800),
        signal_triggered=data.get("signal_triggered", True),
        min_signal_confidence=data.get("min_signal_confidence", 0.7),
        max_calls_per_hour=data.get("max_calls_per_hour", 10),
        model=data.get("model", "claude-sonnet-4-20250514"),
        max_tokens=data.get("max_tokens", 4096),
        temperature=data.get("temperature", 0.3),
        api_key=_env("ANTHROPIC_API_KEY", data.get("api_key", "")),
        strategic_interval=data.get("strategic_interval", 300),
        watchdog_interval=data.get("watchdog_interval", 30),
    )


def _build_risk(data: dict[str, Any]) -> RiskSettings:
    return RiskSettings(
        max_leverage=data.get("max_leverage", 3),
        mandatory_stop_loss=data.get("mandatory_stop_loss", True),
        default_stop_loss_pct=data.get("default_stop_loss_pct", 2.0),
        default_take_profit_pct=data.get("default_take_profit_pct", 4.0),
        max_position_size_pct=data.get("max_position_size_pct", 10.0),
        max_open_positions=data.get("max_open_positions", 5),
        daily_loss_limit_pct=data.get("daily_loss_limit_pct", 5.0),
        max_total_exposure_pct=data.get("max_total_exposure_pct", 50.0),
        max_drawdown_pct=data.get("max_drawdown_pct", 15.0),
        min_order_value_usdt=data.get("min_order_value_usdt", 10.0),
        loss_cooldown_seconds=data.get("loss_cooldown_seconds", 300),
    )


def _build_alerts(data: dict[str, Any]) -> AlertSettings:
    return AlertSettings(
        telegram_enabled=data.get("telegram_enabled", False),
        alert_levels=data.get("alert_levels", ["WARNING", "CRITICAL"]),
        daily_summary=data.get("daily_summary", True),
        daily_summary_time=data.get("daily_summary_time", "00:00"),
        max_alerts_per_minute=data.get("max_alerts_per_minute", 10),
        trade_alerts=data.get("trade_alerts", True),
        signal_alerts=data.get("signal_alerts", True),
        error_alerts=data.get("error_alerts", True),
        bot_token=_env("TELEGRAM_BOT_TOKEN", data.get("bot_token", "")),
        chat_id=_env("TELEGRAM_CHAT_ID", data.get("chat_id", "")),
    )


def _build_watchdog(data: dict[str, Any]) -> WatchdogSettings:
    return WatchdogSettings(
        enabled=data.get("enabled", True),
        check_interval_seconds=float(data.get("check_interval_seconds", 10.0)),
        loss_warning_pct=data.get("loss_warning_pct", 1.0),
        trailing_loss_pct=data.get("trailing_loss_pct", 0.5),
        sl_proximity_pct=data.get("sl_proximity_pct", 30.0),
        rapid_move_pct=data.get("rapid_move_pct", 0.5),
        brain_trigger_loss_pct=data.get("brain_trigger_loss_pct", 1.5),
        brain_cooldown_seconds=data.get("brain_cooldown_seconds", 120),
        partial_close_pct=data.get("partial_close_pct", 50.0),
        max_brain_calls_per_hour=data.get("max_brain_calls_per_hour", 10),
        timeout_threshold_pct=float(data.get("timeout_threshold_pct", 95.0)),
        early_exit_enabled=bool(data.get("early_exit_enabled", False)),
    )


def _build_scanner(data: dict[str, Any]) -> ScannerSettings:
    return ScannerSettings(
        enabled=data.get("enabled", True),
        scan_interval_seconds=data.get("scan_interval_seconds", 300),
        min_volume_24h=data.get("min_volume_24h", 50_000_000),
        max_coins=data.get("max_coins", 15),
        max_spread_pct=data.get("max_spread_pct", 0.1),
    )


def _build_regime(data: dict[str, Any]) -> RegimeSettings:
    return RegimeSettings(
        detection_interval_seconds=data.get("detection_interval_seconds", 300),
        primary_symbol=data.get("primary_symbol", "BTCUSDT"),
        trending_adx_threshold=data.get("trending_adx_threshold", 25.0),
        ranging_adx_threshold=data.get("ranging_adx_threshold", 20.0),
        ranging_choppiness_threshold=data.get("ranging_choppiness_threshold", 60.0),
        volatile_atr_percentile=data.get("volatile_atr_percentile", 150.0),
        dead_adx_threshold=data.get("dead_adx_threshold", 15.0),
        dead_volume_ratio=data.get("dead_volume_ratio", 0.5),
    )


def _build_strategy_engine(data: dict[str, Any]) -> StrategyEngineSettings:
    return StrategyEngineSettings(
        scan_interval_seconds=data.get("scan_interval_seconds", 60),
        min_score_threshold=data.get("min_score_threshold", 70.0),
        min_ensemble_agreement=data.get("min_ensemble_agreement", 5.0),
        max_ensemble_opposition=data.get("max_ensemble_opposition", 1.0),
        max_setups_to_brain=data.get("max_setups_to_brain", 3),
        max_brain_calls_per_hour=data.get("max_brain_calls_per_hour", 12),
    )


def _build_pnl_targets(data: dict[str, Any]) -> PnLTargetSettings:
    return PnLTargetSettings(
        daily_target_pct=data.get("daily_target_pct", 5.0),
        protect_threshold_pct=data.get("protect_threshold_pct", 3.0),
        caution_threshold_pct=data.get("caution_threshold_pct", -1.0),
        survival_threshold_pct=data.get("survival_threshold_pct", -3.0),
        halt_threshold_pct=data.get("halt_threshold_pct", -5.0),
    )


def _build_leverage(data: dict[str, Any]) -> LeverageSettings:
    return LeverageSettings(
        max_leverage=data.get("max_leverage", 5),
        tier_1_max=data.get("tier_1_max", 5),
        tier_2_max=data.get("tier_2_max", 4),
        tier_3_max=data.get("tier_3_max", 3),
        volatile_max=data.get("volatile_max", 3),
        dead_max=data.get("dead_max", 2),
        min_confidence_for_5x=data.get("min_confidence_for_5x", 0.85),
        min_confidence_for_4x=data.get("min_confidence_for_4x", 0.75),
    )


def _build_optimizer(data: dict[str, Any]) -> OptimizerSettings:
    return OptimizerSettings(
        enabled=data.get("enabled", True),
        run_day=data.get("run_day", "sunday"),
        run_hour_utc=data.get("run_hour_utc", 0),
        weight_adjustment_pct=data.get("weight_adjustment_pct", 10.0),
        max_param_change_pct=data.get("max_param_change_pct", 20.0),
        min_trades_for_optimization=data.get("min_trades_for_optimization", 20),
        underperform_threshold_pct=data.get("underperform_threshold_pct", 10.0),
        disable_after_weeks=data.get("disable_after_weeks", 3),
    )


def _build_factory(data: dict[str, Any]) -> FactorySettings:
    return FactorySettings(
        enabled=data.get("enabled", True),
        discovery_schedule_hour_utc=data.get("discovery_schedule_hour_utc", 2),
        discovery_lookback_days=data.get("discovery_lookback_days", 30),
        min_pattern_occurrences=data.get("min_pattern_occurrences", 20),
        min_win_rate=data.get("min_win_rate", 0.55),
        min_profit_factor=data.get("min_profit_factor", 1.2),
        min_statistical_significance=data.get("min_statistical_significance", 0.05),
        max_strategies_per_batch=data.get("max_strategies_per_batch", 5),
        max_generation_retries=data.get("max_generation_retries", 3),
        generation_cost_limit_usd=data.get("generation_cost_limit_usd", 0.20),
        live_monitor_interval_seconds=data.get("live_monitor_interval_seconds", 300),
        hot_pattern_threshold_win_rate=data.get("hot_pattern_threshold_win_rate", 0.70),
        hot_pattern_threshold_occurrences=data.get("hot_pattern_threshold_occurrences", 5),
        emergency_generation_enabled=data.get("emergency_generation_enabled", True),
    )


def _build_backtesting(data: dict[str, Any]) -> BacktestSettings:
    return BacktestSettings(
        initial_capital=data.get("initial_capital", 10000.0),
        default_leverage=data.get("default_leverage", 3),
        commission_pct=data.get("commission_pct", 0.06),
        slippage_pct=data.get("slippage_pct", 0.02),
        funding_rate_pct=data.get("funding_rate_pct", 0.01),
        walk_forward_enabled=data.get("walk_forward_enabled", True),
        train_pct=data.get("train_pct", 0.70),
        monte_carlo_runs=data.get("monte_carlo_runs", 1000),
        min_trades_to_pass=data.get("min_trades_to_pass", 30),
        min_win_rate=data.get("min_win_rate", 0.52),
        min_profit_factor=data.get("min_profit_factor", 1.3),
        max_drawdown_pct=data.get("max_drawdown_pct", 15.0),
        min_sharpe=data.get("min_sharpe", 0.5),
        min_walk_forward_efficiency=data.get("min_walk_forward_efficiency", 0.5),
        max_ruin_probability=data.get("max_ruin_probability", 0.05),
    )


def _build_trial(data: dict[str, Any]) -> TrialSettings:
    return TrialSettings(
        trial_duration_days=data.get("trial_duration_days", 14),
        max_extensions=data.get("max_extensions", 1),
        extension_duration_days=data.get("extension_duration_days", 7),
        trial_position_size_pct=data.get("trial_position_size_pct", 25.0),
        min_trades_for_evaluation=data.get("min_trades_for_evaluation", 10),
        promotion_min_win_rate=data.get("promotion_min_win_rate", 0.50),
        promotion_min_pnl=data.get("promotion_min_pnl", 0.0),
        promotion_max_drawdown=data.get("promotion_max_drawdown", 10.0),
        max_active_strategies=data.get("max_active_strategies", 60),
        demotion_underperform_weeks=data.get("demotion_underperform_weeks", 2),
        demotion_win_rate_drop_pct=data.get("demotion_win_rate_drop_pct", 15.0),
        quarterly_revival_enabled=data.get("quarterly_revival_enabled", True),
    )


def _build_portfolio(data: dict[str, Any]) -> PortfolioSettings:
    return PortfolioSettings(**{k: data[k] for k in data if hasattr(PortfolioSettings, k)}) if data else PortfolioSettings()


def _build_telegram_interactive(data: dict[str, Any]) -> TelegramInteractiveSettings:
    return TelegramInteractiveSettings(**{k: data[k] for k in data if hasattr(TelegramInteractiveSettings, k)}) if data else TelegramInteractiveSettings()


def _build_enforcer(data: dict[str, Any]) -> EnforcerSettings:
    return EnforcerSettings(**{k: data[k] for k in data if hasattr(EnforcerSettings, k)}) if data else EnforcerSettings()


def _build_mode4(data: dict[str, Any]) -> Mode4Settings:
    return Mode4Settings(**{k: data[k] for k in data if hasattr(Mode4Settings, k)}) if data else Mode4Settings()


def _build_fund_manager(data: dict[str, Any]) -> FundManagerSettings:
    return FundManagerSettings(**{k: data[k] for k in data if hasattr(FundManagerSettings, k)}) if data else FundManagerSettings()


def _build_mcp(data: dict[str, Any]) -> MCPSettings:
    return MCPSettings(
        transport=data.get("transport", "stdio"),
        sse_host=data.get("sse_host", "0.0.0.0"),
        sse_port=data.get("sse_port", 8080),
        sse_auth_required=data.get("sse_auth_required", True),
        server_name=data.get("server_name", "trading-intelligence"),
        server_version=data.get("server_version", "0.1.0"),
        auth_token=_env("MCP_AUTH_TOKEN", data.get("auth_token", "")),
    )


def _build_tias(data: dict[str, Any]) -> TIASSettings:
    base = TIASSettings(**{k: data[k] for k in data if hasattr(TIASSettings, k)}) if data else TIASSettings()
    env_key = _env("OPENROUTER_API_KEY")
    if env_key:
        base.api_key = env_key
    return base


def _build_apex(data: dict[str, Any]) -> APEXSettings:
    base = APEXSettings(**{k: data[k] for k in data if hasattr(APEXSettings, k)}) if data else APEXSettings()
    # APEX_API_KEY takes precedence over shared OPENROUTER_API_KEY
    apex_key = _env("APEX_API_KEY")
    if apex_key:
        base.api_key = apex_key
    else:
        env_key = _env("OPENROUTER_API_KEY")
        if env_key:
            base.api_key = env_key
    return base


def _build_sentinel(data: dict[str, Any]) -> SentinelSettings:
    base = SentinelSettings(**{k: data[k] for k in data if hasattr(SentinelSettings, k)}) if data else SentinelSettings()
    # SENTINEL_API_KEY takes precedence, then shared OPENROUTER_API_KEY
    sentinel_key = _env("SENTINEL_API_KEY")
    if sentinel_key:
        base.advisor_api_key = sentinel_key
    else:
        env_key = _env("OPENROUTER_API_KEY")
        if env_key:
            base.advisor_api_key = env_key
    return base


def _build_structure(data: dict[str, Any]) -> StructureSettings:
    """Build X-RAY StructureSettings from [analysis.structure] TOML section."""
    if not data:
        return StructureSettings()
    filtered = {k: data[k] for k in data if hasattr(StructureSettings, k)}
    return StructureSettings(**filtered)


def _build_volatility_profile(data: dict[str, Any]) -> VolatilityProfileSettings:
    """Build VolatilityProfileSettings from [analysis.volatility_profile] TOML section."""
    if not data:
        return VolatilityProfileSettings()
    filtered = {k: data[k] for k in data if hasattr(VolatilityProfileSettings, k)}
    return VolatilityProfileSettings(**filtered)
