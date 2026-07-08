"""Database schema migrations: creates all tables needed by the system.

Standard SQL syntax only for PostgreSQL migration readiness.
"""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("database")

SCHEMA_VERSION = 40  # layer2/D2: v40 claude_decisions adds symbol + trade_directive_id + conviction (per-trade granularity)

MIGRATIONS: list[str] = [
    # --- Market Data Layer ---
    """
    CREATE TABLE IF NOT EXISTS klines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        turnover REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(symbol, timeframe, timestamp)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_klines_symbol_tf_ts
    ON klines(symbol, timeframe, timestamp DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ticker_cache (
        symbol TEXT PRIMARY KEY,
        last_price REAL NOT NULL,
        bid REAL NOT NULL DEFAULT 0,
        ask REAL NOT NULL DEFAULT 0,
        high_24h REAL NOT NULL DEFAULT 0,
        low_24h REAL NOT NULL DEFAULT 0,
        volume_24h REAL NOT NULL DEFAULT 0,
        change_24h_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        bids TEXT NOT NULL,
        asks TEXT NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # --- Trading Layer ---
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT NOT NULL,
        price REAL NOT NULL DEFAULT 0,
        qty REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'New',
        filled_qty REAL NOT NULL DEFAULT 0,
        avg_fill_price REAL NOT NULL DEFAULT 0,
        stop_loss REAL,
        take_profit REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
    ON orders(symbol, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT PRIMARY KEY,
        side TEXT NOT NULL,
        size REAL NOT NULL,
        entry_price REAL NOT NULL,
        mark_price REAL NOT NULL DEFAULT 0,
        unrealized_pnl REAL NOT NULL DEFAULT 0,
        realized_pnl REAL NOT NULL DEFAULT 0,
        leverage INTEGER NOT NULL DEFAULT 1,
        liquidation_price REAL NOT NULL DEFAULT 0,
        stop_loss REAL,
        take_profit REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_history (
        trade_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        qty REAL NOT NULL,
        pnl REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        strategy TEXT NOT NULL DEFAULT '',
        signal_confidence REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        entry_time TEXT NOT NULL,
        exit_time TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trade_history_symbol
    ON trade_history(symbol, exit_time DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS account_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_equity REAL NOT NULL,
        available_balance REAL NOT NULL,
        used_margin REAL NOT NULL,
        unrealized_pnl REAL NOT NULL,
        margin_level_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # --- Intelligence Layer ---
    """
    CREATE TABLE IF NOT EXISTS news_articles (
        id TEXT PRIMARY KEY,
        headline TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        sentiment_score REAL NOT NULL DEFAULT 0,
        symbols TEXT NOT NULL DEFAULT '[]',
        category TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT (datetime('now')),
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_news_published
    ON news_articles(published_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_news_symbols
    ON news_articles(symbols)
    """,
    """
    CREATE TABLE IF NOT EXISTS reddit_posts (
        id TEXT PRIMARY KEY,
        subreddit TEXT NOT NULL,
        title TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        num_comments INTEGER NOT NULL DEFAULT 0,
        upvote_ratio REAL NOT NULL DEFAULT 0,
        sentiment_score REAL NOT NULL DEFAULT 0,
        symbols_mentioned TEXT NOT NULL DEFAULT '[]',
        permalink TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reddit_created
    ON reddit_posts(created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS aggregated_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        overall_score REAL NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT 'neutral',
        news_score REAL NOT NULL DEFAULT 0,
        news_count INTEGER NOT NULL DEFAULT 0,
        reddit_score REAL NOT NULL DEFAULT 0,
        reddit_count INTEGER NOT NULL DEFAULT 0,
        fear_greed_value INTEGER NOT NULL DEFAULT 50,
        momentum REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agg_sentiment_symbol
    ON aggregated_sentiment(symbol, created_at DESC)
    """,
    # economic_calendar: schema kept for forward compatibility.
    # CalendarService exists but is not integrated into any worker yet.
    """
    CREATE TABLE IF NOT EXISTS economic_calendar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT NOT NULL,
        country TEXT NOT NULL DEFAULT '',
        impact TEXT NOT NULL DEFAULT 'low',
        actual TEXT NOT NULL DEFAULT '',
        estimate TEXT NOT NULL DEFAULT '',
        previous TEXT NOT NULL DEFAULT '',
        event_time TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fear_greed_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        value INTEGER NOT NULL,
        classification TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fear_greed_ts
    ON fear_greed_index(timestamp DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS funding_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        funding_rate REAL NOT NULL,
        next_funding_time TEXT NOT NULL,
        predicted_rate REAL NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_funding_symbol
    ON funding_rates(symbol, fetched_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS open_interest (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        open_interest_value REAL NOT NULL,
        timestamp TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_oi_symbol
    ON open_interest(symbol, timestamp DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        signal_type TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        components TEXT NOT NULL DEFAULT '{}',
        reasoning TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_signals_symbol
    ON signals(symbol, created_at DESC)
    """,
    # --- Learning Layer ---
    """
    CREATE TABLE IF NOT EXISTS strategy_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL DEFAULT 'all',
        total_trades INTEGER NOT NULL DEFAULT 0,
        winning_trades INTEGER NOT NULL DEFAULT 0,
        losing_trades INTEGER NOT NULL DEFAULT 0,
        win_rate REAL NOT NULL DEFAULT 0,
        avg_pnl REAL NOT NULL DEFAULT 0,
        avg_pnl_pct REAL NOT NULL DEFAULT 0,
        max_drawdown REAL NOT NULL DEFAULT 0,
        sharpe_ratio REAL,
        profit_factor REAL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy, symbol, timeframe)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_accuracy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        predicted_direction TEXT NOT NULL,
        actual_direction TEXT,
        confidence REAL NOT NULL DEFAULT 0,
        price_at_signal REAL NOT NULL DEFAULT 0,
        price_after_1h REAL,
        price_after_4h REAL,
        price_after_24h REAL,
        was_correct INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_accuracy_lookup
    ON signal_accuracy(signal_type, symbol)
    """,
    """
    CREATE TABLE IF NOT EXISTS pattern_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        outcome_json TEXT,
        confidence REAL NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        detected_at TEXT NOT NULL DEFAULT (datetime('now')),
        resolved_at TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pattern_lookup
    ON pattern_log(pattern_type, symbol)
    """,
    """
    CREATE TABLE IF NOT EXISTS brain_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt_hash TEXT NOT NULL,
        market_state_json TEXT NOT NULL DEFAULT '{}',
        claude_response TEXT NOT NULL DEFAULT '',
        decision_json TEXT NOT NULL DEFAULT '{}',
        action_taken TEXT NOT NULL DEFAULT '',
        outcome_json TEXT NOT NULL DEFAULT '{}',
        tokens_used INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        trigger TEXT NOT NULL DEFAULT 'scheduled',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_brain_created
    ON brain_decisions(created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watchlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        symbols_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS active_strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        params_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(strategy_name, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_type
    ON session_log(event_type, created_at DESC)
    """,
    # --- Strategy Engine Layer (v4) ---
    """
    CREATE TABLE IF NOT EXISTS active_universe (
        symbol TEXT PRIMARY KEY,
        opportunity_score REAL NOT NULL,
        volume_24h REAL,
        change_24h_pct REAL,
        funding_rate REAL,
        spread_pct REAL,
        coin_tier INTEGER DEFAULT 3,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
        regime TEXT NOT NULL,
        confidence REAL,
        adx REAL,
        atr_percentile REAL,
        choppiness REAL,
        detected_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_regime_time
    ON regime_history(detected_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        score REAL,
        ensemble_strength TEXT,
        ensemble_votes_for REAL,
        ensemble_votes_against REAL,
        leverage_used INTEGER,
        regime TEXT,
        pnl REAL,
        pnl_pct REAL,
        was_win INTEGER,
        entry_time TEXT,
        exit_time TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_strat_trades_name
    ON strategy_trades(strategy_name, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_strat_trades_symbol
    ON strategy_trades(symbol, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ensemble_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        setup_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        vote TEXT NOT NULL,
        confidence REAL,
        weight REAL,
        reasoning TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_params (
        strategy_name TEXT NOT NULL,
        param_name TEXT NOT NULL,
        param_value TEXT NOT NULL,
        previous_value TEXT,
        changed_at TEXT DEFAULT (datetime('now')),
        changed_by TEXT DEFAULT 'optimizer',
        PRIMARY KEY (strategy_name, param_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        date TEXT PRIMARY KEY,
        starting_equity REAL,
        ending_equity REAL,
        realized_pnl REAL,
        total_trades INTEGER,
        wins INTEGER,
        losses INTEGER,
        max_drawdown_pct REAL,
        target_hit INTEGER DEFAULT 0,
        halted INTEGER DEFAULT 0,
        brain_calls INTEGER DEFAULT 0,
        brain_cost_usd REAL DEFAULT 0
    )
    """,
    # --- Strategy Factory Layer (v5) ---
    """
    CREATE TABLE IF NOT EXISTS discovered_patterns (
        id TEXT PRIMARY KEY,
        pattern_type TEXT NOT NULL,
        description TEXT NOT NULL,
        conditions_json TEXT NOT NULL DEFAULT '{}',
        symbols_json TEXT DEFAULT '[]',
        timeframe TEXT DEFAULT '5',
        direction TEXT DEFAULT 'long',
        occurrences INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        avg_profit_pct REAL DEFAULT 0.0,
        avg_loss_pct REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        avg_hold_minutes INTEGER DEFAULT 0,
        max_drawdown_pct REAL DEFAULT 0.0,
        statistical_significance REAL DEFAULT 0.0,
        regime_consistency_json TEXT DEFAULT '{}',
        is_valid INTEGER DEFAULT 0,
        data_start_date TEXT,
        data_end_date TEXT,
        discovered_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_patterns_type ON discovered_patterns(pattern_type)",
    "CREATE INDEX IF NOT EXISTS idx_patterns_valid ON discovered_patterns(is_valid)",
    """
    CREATE TABLE IF NOT EXISTS generated_strategies (
        id TEXT PRIMARY KEY,
        pattern_id TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        code TEXT NOT NULL,
        claude_model TEXT DEFAULT '',
        generation_prompt_hash TEXT DEFAULT '',
        generation_cost_usd REAL DEFAULT 0.0,
        generation_attempts INTEGER DEFAULT 1,
        syntax_valid INTEGER DEFAULT 0,
        safety_valid INTEGER DEFAULT 0,
        interface_valid INTEGER DEFAULT 0,
        validation_errors_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'generated',
        generated_at TEXT DEFAULT (datetime('now')),
        validated_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gen_strat_status ON generated_strategies(status)",
    "CREATE INDEX IF NOT EXISTS idx_gen_strat_pattern ON generated_strategies(pattern_id)",
    """
    CREATE TABLE IF NOT EXISTS pattern_occurrences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        conditions_snapshot_json TEXT DEFAULT '{}',
        price_at_detection REAL NOT NULL,
        price_after_1h REAL,
        price_after_4h REAL,
        price_after_24h REAL,
        outcome TEXT,
        pnl_pct REAL,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_occ_pattern ON pattern_occurrences(pattern_id, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_occ_symbol ON pattern_occurrences(symbol, timestamp DESC)",
    """
    CREATE TABLE IF NOT EXISTS strategy_code_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        version INTEGER DEFAULT 1,
        code TEXT NOT NULL,
        change_reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Backtesting + Lifecycle Layer (v6) ---
    """
    CREATE TABLE IF NOT EXISTS backtest_results (
        id TEXT PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        total_trades INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        total_return_pct REAL DEFAULT 0.0,
        max_drawdown_pct REAL DEFAULT 0.0,
        sharpe_ratio REAL DEFAULT 0.0,
        sortino_ratio REAL DEFAULT 0.0,
        calmar_ratio REAL DEFAULT 0.0,
        walk_forward_efficiency REAL DEFAULT 0.0,
        mc_probability_of_profit REAL DEFAULT 0.0,
        mc_probability_of_ruin REAL DEFAULT 0.0,
        overall_grade TEXT DEFAULT 'F',
        passed INTEGER DEFAULT 0,
        pass_reasons_json TEXT DEFAULT '[]',
        fail_reasons_json TEXT DEFAULT '[]',
        regime_performance_json TEXT DEFAULT '{}',
        monthly_returns_json TEXT DEFAULT '{}',
        equity_curve_json TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bt_strategy ON backtest_results(strategy_id)",
    "CREATE INDEX IF NOT EXISTS idx_bt_passed ON backtest_results(passed)",
    """
    CREATE TABLE IF NOT EXISTS backtest_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        entry_time TEXT NOT NULL,
        exit_time TEXT NOT NULL,
        exit_reason TEXT NOT NULL,
        pnl_usd REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        commission_usd REAL DEFAULT 0,
        hold_minutes INTEGER DEFAULT 0,
        leverage INTEGER DEFAULT 1,
        regime TEXT DEFAULT '',
        hour_utc INTEGER DEFAULT 0,
        day_of_week INTEGER DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_backtest ON backtest_trades(backtest_id)",
    """
    CREATE TABLE IF NOT EXISTS strategy_lifecycle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        reason TEXT DEFAULT '',
        performance_snapshot_json TEXT DEFAULT '{}',
        transitioned_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lifecycle_strategy ON strategy_lifecycle(strategy_id)",
    """
    CREATE TABLE IF NOT EXISTS trial_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id TEXT NOT NULL,
        date TEXT NOT NULL,
        trades_today INTEGER DEFAULT 0,
        wins_today INTEGER DEFAULT 0,
        pnl_today REAL DEFAULT 0.0,
        cumulative_trades INTEGER DEFAULT 0,
        cumulative_pnl REAL DEFAULT 0.0,
        cumulative_win_rate REAL DEFAULT 0.0,
        max_drawdown REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trial_perf ON trial_performance(strategy_id, date)",
    # --- Portfolio Optimizer Layer (v7) ---
    """
    CREATE TABLE IF NOT EXISTS portfolio_allocations (
        strategy_name TEXT PRIMARY KEY,
        category TEXT NOT NULL DEFAULT '',
        full_kelly_pct REAL DEFAULT 0.0,
        fractional_kelly_pct REAL DEFAULT 0.0,
        allocated_pct REAL DEFAULT 0.0,
        allocated_usd REAL DEFAULT 0.0,
        max_position_usd REAL DEFAULT 0.0,
        max_leverage INTEGER DEFAULT 3,
        performance_score REAL DEFAULT 0.0,
        correlation_penalty REAL DEFAULT 0.0,
        risk_contribution_pct REAL DEFAULT 0.0,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS correlation_matrix (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_a TEXT NOT NULL,
        strategy_b TEXT NOT NULL,
        correlation REAL NOT NULL,
        sample_size INTEGER DEFAULT 0,
        period_days INTEGER DEFAULT 30,
        computed_at TEXT DEFAULT (datetime('now')),
        UNIQUE(strategy_a, strategy_b, period_days)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_corr_strategies ON correlation_matrix(strategy_a, strategy_b)",
    """
    CREATE TABLE IF NOT EXISTS risk_budget_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        total_budget_pct REAL NOT NULL,
        used_pct REAL DEFAULT 0.0,
        proven_budget_pct REAL DEFAULT 0.0,
        ai_budget_pct REAL DEFAULT 0.0,
        trial_budget_pct REAL DEFAULT 0.0,
        reserve_pct REAL DEFAULT 0.0,
        strategy_budgets_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_risk_budget_date ON risk_budget_log(date)",
    """
    CREATE TABLE IF NOT EXISTS rebalance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        old_allocation_pct REAL,
        new_allocation_pct REAL,
        change_pct REAL,
        reason TEXT,
        approved_by TEXT DEFAULT 'claude',
        applied INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stress_test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_name TEXT NOT NULL,
        description TEXT,
        portfolio_impact_pct REAL,
        loss_usd REAL,
        survival INTEGER DEFAULT 1,
        margin_call_risk INTEGER DEFAULT 0,
        details_json TEXT DEFAULT '{}',
        tested_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS performance_attribution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        total_pnl_usd REAL,
        total_pnl_pct REAL,
        strategy_contributions_json TEXT DEFAULT '[]',
        category_contributions_json TEXT DEFAULT '{}',
        best_strategy TEXT,
        worst_strategy TEXT,
        regime_factor REAL DEFAULT 0.0,
        timing_factor REAL DEFAULT 0.0,
        sizing_factor REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_attribution_period ON performance_attribution(period, created_at DESC)",
    # --- Interactive Telegram Bot Layer (v8) ---
    """
    CREATE TABLE IF NOT EXISTS price_alerts (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        condition TEXT NOT NULL,
        target_price REAL NOT NULL,
        current_price_at_set REAL DEFAULT 0,
        indicator TEXT DEFAULT 'price',
        triggered INTEGER DEFAULT 0,
        triggered_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_price_alerts_chat ON price_alerts(chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts(triggered, symbol)",
    """
    CREATE TABLE IF NOT EXISTS trade_journal (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        trade_id TEXT DEFAULT '',
        symbol TEXT DEFAULT '',
        entry_type TEXT DEFAULT '',
        content TEXT NOT NULL,
        mood TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_journal_chat ON trade_journal(chat_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS scheduled_reports (
        id TEXT PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        report_type TEXT NOT NULL,
        schedule TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_sent TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        intent TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_conv_log_chat ON conversation_log(chat_id, created_at DESC)",
    # --- Performance Enforcer (v9) ---
    """
    CREATE TABLE IF NOT EXISTS hourly_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hour TEXT NOT NULL,
        grade TEXT NOT NULL,
        trades INTEGER DEFAULT 0,
        target_trades INTEGER DEFAULT 50,
        profit_pct REAL DEFAULT 0.0,
        target_profit_pct REAL DEFAULT 10.0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        max_escalation INTEGER DEFAULT 0,
        signals INTEGER DEFAULT 0,
        setups_to_brain INTEGER DEFAULT 0,
        rewards INTEGER DEFAULT 0,
        summary_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hourly_perf ON hourly_performance(hour DESC)",
    # --- Schema Version ---
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    # --- Fund Manager (v10) ---
    """
    CREATE TABLE IF NOT EXISTS fund_manager_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_manager_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        symbol TEXT DEFAULT '',
        details_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fm_log ON fund_manager_log(event_type, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS capital_level_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level TEXT NOT NULL,
        equity REAL NOT NULL,
        direction TEXT NOT NULL,
        reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profit_ratchet_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        locked_amount REAL NOT NULL,
        total_locked REAL NOT NULL,
        equity_at_lock REAL NOT NULL,
        profit_floor REAL NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # NOTE: user_preferences already defined earlier in schema — removed duplicate here.

    # --- v11: Missing indexes for frequently queried tables ---
    "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_account_snapshots_time ON account_snapshots(updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_backtest_results_time ON backtest_results(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol ON price_alerts(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_daily_pnl_date ON daily_pnl(date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_strategy_perf_name ON strategy_performance(strategy);",

    # --- v12: Trade Thesis + Data Lake Tables ---
    # trade_thesis: Claude's reasoning for every trade (Data A system)
    """
    CREATE TABLE IF NOT EXISTS trade_thesis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        stop_loss_price REAL NOT NULL,
        take_profit_price REAL NOT NULL,
        size_usd REAL NOT NULL,
        leverage INTEGER NOT NULL DEFAULT 2,
        max_hold_minutes INTEGER NOT NULL DEFAULT 30,
        trailing_activation_pct REAL NOT NULL DEFAULT 1.0,
        thesis TEXT NOT NULL,
        market_context TEXT DEFAULT '',
        strategy_hints TEXT DEFAULT '',
        consensus TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        opened_at TEXT NOT NULL DEFAULT (datetime('now')),
        closed_at TEXT,
        close_price REAL,
        actual_pnl_pct REAL,
        actual_pnl_usd REAL,
        close_reason TEXT,
        lesson TEXT,
        order_id TEXT,
        bybit_position_idx TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_thesis_symbol_status ON trade_thesis(symbol, status);",
    "CREATE INDEX IF NOT EXISTS idx_trade_thesis_status ON trade_thesis(status);",
    "CREATE INDEX IF NOT EXISTS idx_trade_thesis_opened ON trade_thesis(opened_at DESC);",

    # Data Lake: market_snapshots — 60s compressed market state
    """
    CREATE TABLE IF NOT EXISTS market_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        btc_price REAL,
        eth_price REAL,
        sol_price REAL,
        regime TEXT DEFAULT '',
        fear_greed INTEGER DEFAULT 0,
        full_data TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_market_snapshots_ts ON market_snapshots(ts_epoch DESC);",

    # Data Lake: trade_log — every trade with full context, forever
    """
    CREATE TABLE IF NOT EXISTS trade_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id TEXT UNIQUE,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL DEFAULT 0,
        exit_price REAL DEFAULT 0,
        size_usd REAL DEFAULT 0,
        leverage INTEGER DEFAULT 1,
        pnl_pct REAL DEFAULT 0,
        pnl_usd REAL DEFAULT 0,
        strategy TEXT DEFAULT '',
        thesis TEXT DEFAULT '',
        close_reason TEXT DEFAULT '',
        hold_minutes REAL DEFAULT 0,
        opened_at TEXT DEFAULT '',
        closed_at TEXT DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON trade_log(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_trade_log_opened ON trade_log(opened_at DESC);",

    # Data Lake: position_snapshots — 60s position state, 7-day retention
    """
    CREATE TABLE IF NOT EXISTS position_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT DEFAULT '',
        entry_price REAL DEFAULT 0,
        mark_price REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        unrealized_pnl REAL DEFAULT 0,
        age_minutes REAL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_position_snapshots_ts ON position_snapshots(ts_epoch DESC);",
    "CREATE INDEX IF NOT EXISTS idx_position_snapshots_symbol ON position_snapshots(symbol);",

    # Data Lake: claude_decisions — every Claude call with compressed context
    """
    CREATE TABLE IF NOT EXISTS claude_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        decision_type TEXT NOT NULL DEFAULT 'strategic_review',
        new_trades_count INTEGER DEFAULT 0,
        position_actions_count INTEGER DEFAULT 0,
        market_view TEXT DEFAULT '',
        risk_level TEXT DEFAULT '',
        response_time_ms INTEGER DEFAULT 0,
        prompt_length INTEGER DEFAULT 0,
        full_response TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_claude_decisions_ts ON claude_decisions(ts_epoch DESC);",

    # Data Lake: event_log — unified event timeline
    """
    CREATE TABLE IF NOT EXISTS event_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_epoch REAL NOT NULL,
        event_type TEXT NOT NULL,
        priority TEXT DEFAULT 'LOW',
        symbol TEXT DEFAULT '',
        data TEXT DEFAULT '{}',
        source TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_event_log_ts ON event_log(ts_epoch DESC);",
    "CREATE INDEX IF NOT EXISTS idx_event_log_type ON event_log(event_type);",

    # Data Lake: daily_summary — rolled up daily stats, forever
    """
    CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        total_pnl_pct REAL DEFAULT 0,
        total_pnl_usd REAL DEFAULT 0,
        best_trade_pct REAL DEFAULT 0,
        worst_trade_pct REAL DEFAULT 0,
        avg_hold_minutes REAL DEFAULT 0,
        starting_equity REAL DEFAULT 0,
        ending_equity REAL DEFAULT 0,
        regime_summary TEXT DEFAULT '',
        trades_json TEXT DEFAULT '[]'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_summary_date ON daily_summary(date DESC);",
    # --- Transformer Layer ---
    """
    CREATE TABLE IF NOT EXISTS transformer_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        current_mode TEXT NOT NULL DEFAULT 'shadow',
        last_switched_at TEXT,
        is_switching INTEGER NOT NULL DEFAULT 0,
        switching_to TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS switch_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        from_mode TEXT NOT NULL,
        to_mode TEXT NOT NULL,
        positions_closed INTEGER NOT NULL DEFAULT 0,
        close_results_json TEXT,
        reason TEXT NOT NULL DEFAULT 'user_initiated',
        success INTEGER NOT NULL,
        error_message TEXT,
        shadow_equity REAL,
        bybit_equity REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_switch_history_ts ON switch_history(timestamp DESC)",
    """
    INSERT OR IGNORE INTO transformer_state (id, current_mode, is_switching, updated_at)
    VALUES (1, 'shadow', 0, datetime('now'))
    """,
    # --- T7: Trade Tagging — exchange_mode column ---
    "ALTER TABLE trade_thesis ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    "ALTER TABLE trade_log ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    "ALTER TABLE strategy_trades ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    # --- M1: Mode 4 Profit Sniper — sniper_log table ---
    """
    CREATE TABLE IF NOT EXISTS sniper_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        spike_direction TEXT NOT NULL,
        entry_price REAL,
        detection_price REAL,
        pnl_at_detection_pct REAL,
        pnl_at_detection_usd REAL,
        hold_duration_seconds INTEGER,
        exploit_score INTEGER,
        z_score REAL,
        velocity REAL,
        acceleration REAL,
        volume_ratio REAL,
        bb_position REAL,
        speed_factor REAL,
        consecutive_direction_count INTEGER,
        action TEXT,
        close_percentage REAL,
        close_price REAL,
        profit_captured_pct REAL,
        profit_captured_usd REAL,
        claude_consulted INTEGER DEFAULT 0,
        claude_response TEXT,
        claude_response_time_ms INTEGER,
        price_after_10s REAL,
        price_after_30s REAL,
        price_after_60s REAL,
        counterfactual_pnl_pct REAL,
        sniper_value_pct REAL,
        mode4_was_right INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sniper_log_ts ON sniper_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_sniper_log_symbol_ts ON sniper_log(symbol, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sniper_log_action ON sniper_log(action)",
    # --- M10: Phase 10 — sniper_log new model columns ---
    # Model 1: Hurst Exponent (Phase 2)
    "ALTER TABLE sniper_log ADD COLUMN hurst_value REAL",
    "ALTER TABLE sniper_log ADD COLUMN hurst_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN hurst_regime TEXT",
    "ALTER TABLE sniper_log ADD COLUMN hurst_confidence REAL",
    # Model 2: Momentum Decay (Phase 3)
    "ALTER TABLE sniper_log ADD COLUMN momentum_decay_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN momentum_consec_decel INTEGER",
    "ALTER TABLE sniper_log ADD COLUMN momentum_reversed INTEGER",
    "ALTER TABLE sniper_log ADD COLUMN slope_short REAL",
    "ALTER TABLE sniper_log ADD COLUMN slope_long REAL",
    # Model 3: ATR Extension (Phase 4)
    "ALTER TABLE sniper_log ADD COLUMN extension_atr REAL",
    "ALTER TABLE sniper_log ADD COLUMN extension_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN atr_value REAL",
    "ALTER TABLE sniper_log ADD COLUMN vol_ratio REAL",
    # Model 4: Volume Divergence (Phase 5)
    "ALTER TABLE sniper_log ADD COLUMN volume_div_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN price_obv_corr REAL",
    "ALTER TABLE sniper_log ADD COLUMN volume_trend_ratio REAL",
    "ALTER TABLE sniper_log ADD COLUMN divergence_type TEXT",
    # Model 5: Risk/Reward (Phase 6)
    "ALTER TABLE sniper_log ADD COLUMN risk_reward_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN ev_ratio REAL",
    "ALTER TABLE sniper_log ADD COLUMN profit_amplifier REAL",
    # Composite & Regime (Phase 7)
    "ALTER TABLE sniper_log ADD COLUMN composite_score REAL",
    "ALTER TABLE sniper_log ADD COLUMN composite_base REAL",
    "ALTER TABLE sniper_log ADD COLUMN regime TEXT",
    "ALTER TABLE sniper_log ADD COLUMN consensus_boost REAL",
    "ALTER TABLE sniper_log ADD COLUMN urgency_boost REAL",
    # Trail (Phase 8)
    "ALTER TABLE sniper_log ADD COLUMN trail_stop_price REAL",
    "ALTER TABLE sniper_log ADD COLUMN trail_distance_pct REAL",
    # Action & Anti-Greed (Phase 9)
    "ALTER TABLE sniper_log ADD COLUMN action_source TEXT",
    "ALTER TABLE sniper_log ADD COLUMN peak_pnl_pct REAL",
    "ALTER TABLE sniper_log ADD COLUMN pullback_from_peak REAL",
    "ALTER TABLE sniper_log ADD COLUMN anti_greed_rule TEXT",

    # --- v17: TIAS — Trade Intelligence Autopsy System ---
    # Captures full market context at trade close for post-trade DeepSeek analysis.
    """
    CREATE TABLE IF NOT EXISTS trade_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Group A: Trade Outcome (always populated)
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        strategy_category TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        closed_by TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        pnl_pct REAL NOT NULL,
        pnl_usd REAL NOT NULL,
        win INTEGER NOT NULL,
        hold_seconds REAL NOT NULL,

        -- Group B: Entry Decision Context
        leverage REAL,
        position_size_usd REAL,
        claude_thesis TEXT,
        claude_signal TEXT,
        claude_confidence REAL,
        entry_score REAL,
        ensemble_votes TEXT,

        -- Group C: Market Conditions at Close
        regime TEXT,
        fear_greed_value INTEGER,
        fear_greed_label TEXT,

        -- Group D: Technical Indicators at Close
        rsi REAL,
        macd_hist REAL,
        macd_signal REAL,
        bollinger_pct REAL,
        ema_20 REAL,
        ema_50 REAL,
        stochastic_k REAL,
        stochastic_d REAL,
        adx REAL,
        atr_value REAL,
        atr_pct REAL,
        volume_ratio REAL,
        price_vs_vwap REAL,

        -- Group E: Mode4 Profit Tracking Data
        m4_peak_pnl_pct REAL,
        m4_ticks_in_profit INTEGER,
        m4_ticks_total INTEGER,
        m4_composite_score REAL,
        m4_hurst_value REAL,
        m4_momentum_decay REAL,
        m4_extension_score REAL,
        m4_ev_ratio REAL,
        m4_volume_div_score REAL,

        -- Group F: DeepSeek Analysis (Phase 2 — NULL until analyzed)
        ds_why TEXT,
        ds_what_worked TEXT,
        ds_what_failed TEXT,
        ds_lessons TEXT,
        ds_category TEXT,
        ds_confidence REAL,
        ds_analyzed_at TEXT,

        -- Group G: Metadata
        trade_id TEXT,
        trade_closed_at TEXT NOT NULL,
        captured_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ti_symbol ON trade_intelligence (symbol)",
    "CREATE INDEX IF NOT EXISTS idx_ti_win ON trade_intelligence (win)",
    "CREATE INDEX IF NOT EXISTS idx_ti_ds_why ON trade_intelligence (ds_why)",
    "CREATE INDEX IF NOT EXISTS idx_ti_trade_closed_at ON trade_intelligence (trade_closed_at)",
    "CREATE INDEX IF NOT EXISTS idx_ti_ds_category ON trade_intelligence (ds_category)",

    # --- v18: TIAS Phase 2 — DeepSeek analysis fields ---
    # run_migrations() safely ignores "duplicate column" errors for idempotence.
    "ALTER TABLE trade_intelligence ADD COLUMN ds_correct_direction TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_what_should_done TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_how_to_exploit TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_optimal_direction TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_optimal_sl_pct REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_optimal_tp_pct REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_optimal_size_usd REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_optimal_leverage INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_raw_response TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_response_time_ms INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_input_tokens INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_output_tokens INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_cost_usd REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN ds_model TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN analysis_version INTEGER",
    # --- v19: TIAS Phase 4 — backfill retry counter ---
    "ALTER TABLE trade_intelligence ADD COLUMN analysis_attempts INTEGER DEFAULT 0",
    # --- v20: TIAS Phase 3 completion — entry-time market snapshot ---
    # Completes the data pipeline: entry_regime/rsi/macd/atr were captured in
    # strategy_worker and stored in TradeState, but had no DB destination until now.
    # DeepSeek can compare entry vs close conditions to analyse regime shifts.
    "ALTER TABLE trade_intelligence ADD COLUMN entry_regime TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN entry_rsi REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN entry_macd_hist REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN entry_atr_pct REAL",
    # --- v21: APEX Phase 3 — optimization tracking + feedback loop ---
    # Records WHAT APEX changed for each trade so DeepSeek can evaluate
    # whether optimizations helped, closing the self-improving loop.
    "ALTER TABLE trade_intelligence ADD COLUMN apex_optimized INTEGER DEFAULT 0",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_flipped INTEGER DEFAULT 0",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_original_direction TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_final_direction TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_original_sl REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_final_sl REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_original_tp REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_final_tp REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_original_size REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_final_size REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_confidence REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_tp_mode TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_reasoning TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_model TEXT",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_response_ms INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN apex_cost_usd REAL",
    "ALTER TABLE trade_intelligence ADD COLUMN gate_adjustments TEXT",
    "CREATE INDEX IF NOT EXISTS idx_ti_apex_optimized ON trade_intelligence (apex_optimized)",
    # APEX Recalibration: TP fill rate feedback
    "ALTER TABLE trade_intelligence ADD COLUMN apex_tp_fill_rate REAL",
    # --- v23: APEX flip context on trade_thesis ---
    "ALTER TABLE trade_thesis ADD COLUMN apex_flipped INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE trade_thesis ADD COLUMN apex_original_direction TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE trade_thesis ADD COLUMN apex_reason TEXT NOT NULL DEFAULT ''",
    # --- v24: Per-coin regime pipeline fix ---
    # coin_regime_history: persists per-coin regimes across restarts
    """
    CREATE TABLE IF NOT EXISTS coin_regime_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        regime TEXT NOT NULL,
        confidence REAL NOT NULL,
        adx REAL,
        choppiness REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_coin_regime_symbol
    ON coin_regime_history(symbol, timestamp DESC)
    """,
    # regime_verified: distinguishes pre-fix (contaminated) vs post-fix (correct) regime tags
    "ALTER TABLE trade_intelligence ADD COLUMN regime_verified INTEGER DEFAULT 0",
    # Layer 1 restructure Phase 1 — hourly cycle latency aggregates.
    # Populated by ``src/core/cycle_tracker.py:CycleTracker._flush_once``;
    # read by Telegram ``/health`` and the Phase 9 observation harness.
    """
    CREATE TABLE IF NOT EXISTS cycle_metrics (
        hour_ts INTEGER PRIMARY KEY,
        cycles_count INTEGER,
        layer1a_p50_ms INTEGER, layer1a_p95_ms INTEGER,
        layer1b_p50_ms INTEGER, layer1b_p95_ms INTEGER,
        layer1c_p50_ms INTEGER, layer1c_p95_ms INTEGER,
        layer1d_p50_ms INTEGER, layer1d_p95_ms INTEGER,
        total_p50_ms   INTEGER, total_p95_ms   INTEGER,
        qualified_pct_avg REAL,
        packages_count_avg REAL,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """,
    # Phase 15 (output-quality obs): cycle_metrics columns capturing the
    # NEW per-cycle aggregates introduced in Phases 1-7 of this work.
    # Population is wired in a follow-up commit when the per-cycle
    # aggregator subscribes to SIG_TICK_SUMMARY / XRAY_CLASSIFY_SUMMARY /
    # REGIME_PERCOIN_SUMMARY / STRAT_L*_DONE / PACKAGE_VALIDATE_SUMMARY /
    # CYCLE_FRESHNESS / SENT_DEGRADED_MODE counts. Until then, columns
    # default to NULL so existing flushes succeed unchanged.
    "ALTER TABLE cycle_metrics ADD COLUMN signal_buy_pct REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN signal_sell_pct REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN signal_neutral_pct REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN xray_setup_type_count INTEGER DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN regime_distribution_json TEXT DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN l1_strategies_fired_avg REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN l2_score_p50 REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN l3_consensus_dist_json TEXT DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN package_completeness_avg REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN freshness_klines_to_xray_p50 INTEGER DEFAULT NULL",
    # Phase 1 of the Layer 1D briefing-pack rewrite: per-cycle aggregates
    # for the briefing pipeline. Population is wired in Phase 4 when the
    # interestingness ranker fires per cycle. Until then, columns default
    # NULL so existing flushes succeed unchanged. The state label
    # distribution is stored as a JSON {label_name: count} map; the
    # briefing_packages_count is the count of coins selected into the
    # top-N briefing for the cycle.
    "ALTER TABLE cycle_metrics ADD COLUMN interestingness_p50 REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN interestingness_p95 REAL DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN state_label_distribution_json TEXT DEFAULT NULL",
    "ALTER TABLE cycle_metrics ADD COLUMN briefing_packages_count INTEGER DEFAULT NULL",
    # --- v27: Time-Decay Force-Close Definitive Fix Phase 3 ---
    # Entry-time XRAY/regime anchors on `trade_thesis` so the position
    # watchdog can detect structural invalidation (XRAY confidence drop,
    # setup-type drift, regime inversion) by comparing current state
    # against the at-open snapshot. These four columns are the durable
    # restart-resilient half of the Hybrid anchor design — the runtime
    # path reads from `TradeCoordinator.TradeState` first and only
    # falls back to `trade_thesis` when state was lost across a
    # watchdog process restart for an in-flight position.
    # Naming: `entry_regime_at_open` (not `entry_regime`) to avoid
    # cognitive collision with the existing `trade_intelligence.entry_regime`
    # column at v20 above. Defaults are neutral (0.0/'') so pre-fix
    # rows remain readable and the watchdog falls through to the
    # fail-safe "no_data:no_entry_anchor" branch for them.
    "ALTER TABLE trade_thesis ADD COLUMN entry_xray_confidence REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE trade_thesis ADD COLUMN entry_setup_type TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE trade_thesis ADD COLUMN entry_regime_at_open TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE trade_thesis ADD COLUMN entry_regime_confidence REAL NOT NULL DEFAULT 0.0",
    # --- v28: CALL_B Framing Fix Phase 1E (2026-05-06) ---
    # XRAY-driven direction flips (strategy_worker.py:1650-1696) carried
    # `_flip_source` and `_xray_flip_ratio` on the in-memory trade dict
    # but never persisted them — when a position outlived the worker
    # tick, CALL_B could not differentiate XRAY-driven from APEX-driven
    # flips and could not quote concrete RR justification in the
    # FLIPPED notice. The four columns below close that gap:
    #
    #   xray_flip_source   — 'xray' / 'apex' / '' (empty = no flip)
    #   xray_flip_ratio    — RR_chosen / RR_rejected at the time of flip
    #   xray_flip_rr_long  — RR for the long direction at flip time
    #   xray_flip_rr_short — RR for the short direction at flip time
    #
    # The CALL_B prompt uses these to render lines like:
    #   "FLIPPED via XRAY from Buy to Sell: RR_chosen=3.6 vs
    #    RR_rejected=0.5 (7.2x better)"
    # giving Claude concrete evidence the flip was the better choice.
    # Defaults are neutral ('' / 0.0) so legacy callers and rows
    # pre-dating v28 are still well-formed.
    "ALTER TABLE trade_thesis ADD COLUMN xray_flip_source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE trade_thesis ADD COLUMN xray_flip_ratio REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE trade_thesis ADD COLUMN xray_flip_rr_long REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE trade_thesis ADD COLUMN xray_flip_rr_short REAL NOT NULL DEFAULT 0.0",
    # P4 of P1-P10 (schema v29). Audit found trade_intelligence had no
    # exchange_mode column; the cross-mode filter at performance_enforcer
    # / telegram /history / /errors / thesis_manager.get_open_theses
    # requires it to avoid contaminating bybit_demo enforcement state
    # with shadow-session PnL. Default 'shadow' for legacy rows; the
    # P8 backfill cut-over (2026-05-08 11:27:00) is applied in a
    # follow-up UPDATE below for symmetry with trade_log's backfill.
    "ALTER TABLE trade_intelligence ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    # Idempotent backfill of trade_intelligence rows that close after
    # the bybit_demo enable cut-over. Safe because (a) ADD COLUMN sets
    # every existing row to default 'shadow', so re-running the UPDATE
    # is a no-op once already applied (rows post-cut-over will have
    # 'bybit_demo' which the WHERE filter excludes from a second pass).
    # Mirrors the standalone backfill script for trade_log
    # (scripts/backfill_p8_trade_log_exchange_mode.py).
    "UPDATE trade_intelligence SET exchange_mode='bybit_demo' "
    "WHERE exchange_mode='shadow' AND trade_closed_at >= '2026-05-08 11:27:00'",
    # ── HIGH-2 of CRITICAL/HIGH series (schema v30, 2026-05-09) ──
    # Audit found three more tables lacking exchange_mode. Without it,
    # cross-mode reads cannot filter cleanly: DeepSeek learns from a
    # mode-blind dataset (trade_history), MCP get_trade_history mixes
    # modes, and equity-curve dashboards sourcing account_snapshots
    # have no way to disambiguate Shadow vs bybit_demo equity history.
    #
    # Defaults are 'shadow' for legacy rows; backfill UPDATEs below
    # apply the cut-over heuristic per table. Cut-over timestamp
    # (2026-05-08T11:19:26) sourced from transformer_state.last_switched_at.
    "ALTER TABLE orders ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    "ALTER TABLE account_snapshots ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    "ALTER TABLE trade_history ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    # Idempotent backfill — orders post-cutover are bybit_demo. The
    # only orders writer pre-cutover was Shadow's adapter; post-cutover
    # is exclusively bybit_demo's adapter. WHERE filter excludes rows
    # already tagged so a second pass is a no-op.
    "UPDATE orders SET exchange_mode='bybit_demo' "
    "WHERE exchange_mode='shadow' AND created_at >= '2026-05-08T11:19:26'",
    # account_snapshots: the writer was shadow-only pre-HIGH-1 fix;
    # post-fix both modes write. Pre-cutover rows = shadow (no-op since
    # column DEFAULT covers them). Post-cutover rows produced by the
    # HIGH-1 fix are bybit_demo. There MAY be no rows in the post-cutover
    # window before HIGH-1 ships (account_snapshots was dormant); the
    # WHERE filter is a defensive forward-looking statement.
    "UPDATE account_snapshots SET exchange_mode='bybit_demo' "
    "WHERE exchange_mode='shadow' AND updated_at >= '2026-05-08T11:19:26'",
    # trade_history: all 30 existing rows have bd-{symbol}-close prefix
    # (CRITICAL-3 audit confirmed). The bd- prefix is the unambiguous
    # bybit_demo marker. Pre-fix Shadow had no trade_history writer at
    # all so pre-cutover rows are also implicitly bybit_demo (none
    # actually exist; the WHERE LIKE filter is the safe identifier).
    "UPDATE trade_history SET exchange_mode='bybit_demo' "
    "WHERE exchange_mode='shadow' AND trade_id LIKE 'bd-%'",
    # ── I1 of cascade-fix series (schema v31, 2026-05-10) ──
    # AltDataRepository.get_fear_greed_history orders ASC, but the only
    # existing index on this table is DESC (idx_fear_greed_ts at v ≤ 30).
    # The ASC ordering against TEXT-typed timestamps forces SQLite to
    # scan and sort under the global connection mutex. Phase 0 baseline
    # of the cascade-fix series found 21,516 rows; without an ASC index
    # the worst case is bounded only by row count.
    # Defensive cleanup: even though Phase 0 also confirmed the
    # fear_greed_index query is NOT the dominant DB_LOCK_WAIT holder
    # (ticker_cache is 99.7%), removing the only unbounded scan in the
    # codebase eliminates a latent footgun. Index creation is fast
    # (~21k rows) and IF NOT EXISTS makes the migration re-runnable.
    "CREATE INDEX IF NOT EXISTS idx_fear_greed_ts_asc "
    "ON fear_greed_index(timestamp ASC)",
    # ── I4 of cascade-fix series (schema v32, 2026-05-10) ──
    # Phase 0 baseline confirmed positions table is permanently empty
    # in bybit_demo mode: BybitDemoPositionService.get_positions returns
    # parsed positions but does NOT call save_position, while the live
    # PositionService.get_positions does (line 76). Watchdog sees
    # positions in memory but DB consumers (Telegram /positions, MCP
    # tools, post-mortem queries) see 0 rows. CRITICAL/HIGH series
    # (schema v30) added exchange_mode to orders / account_snapshots /
    # trade_history but missed positions — this column closes the gap
    # so the audit-trail tagging is symmetric across exchange modes.
    # Default 'shadow' for legacy rows; the bybit_demo backfill is
    # vacuous because pre-fix bybit_demo never wrote any rows
    # (Phase 0: positions row count = 0). The pre-flight column-exists
    # check in run_migrations makes this ALTER idempotent.
    "ALTER TABLE positions ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'",
    # Mode-filtered queries are how consumers (Telegram /positions,
    # MCP get_positions, dashboards) will tell shadow rows from
    # bybit_demo rows. Index keeps the WHERE exchange_mode = ? lookup
    # cheap as the table grows.
    "CREATE INDEX IF NOT EXISTS idx_positions_mode "
    "ON positions(exchange_mode)",
    # Phase conn-pool/p5-1 (db-concurrency-refactor 2026-05-14): drop
    # duplicate fear_greed_index timestamp DESC index. The audit
    # identified two indexes on the same column differing only by
    # direction (idx_fear_greed_ts DESC + idx_fear_greed_ts_asc ASC).
    # SQLite can walk a B-tree index in either direction at O(1) for
    # ``LIMIT 1`` queries; EXPLAIN QUERY PLAN confirms every observed
    # query path uses idx_fear_greed_ts_asc. The DESC index is dead
    # weight at insert time. ``DROP INDEX IF EXISTS`` is idempotent.
    "DROP INDEX IF EXISTS idx_fear_greed_ts",
    # Phase conn-pool/p5-2: drop duplicate position_snapshots ts_epoch
    # ASC index. ``idx_pos_snapshots_ts`` (default ASC, declared in an
    # earlier migration) and ``idx_position_snapshots_ts`` (DESC, the
    # later canonical) both exist on the same column. Production queries
    # use ORDER BY ts_epoch DESC LIMIT N, served optimally by the DESC
    # index; the ASC variant has no observed consumers. Dropping the
    # legacy index halves the per-INSERT B-tree maintenance on this
    # high-write-rate table (~360 rows/hour at steady state).
    "DROP INDEX IF EXISTS idx_pos_snapshots_ts",
    # --- v34: Mid-Hold Trade Management Fix (Phase 3.1, 2026-05-19) ---
    # Four columns on `trade_thesis` to persist the entry-time thesis
    # invalidation contract:
    #
    #   thesis_invalidation — JSON {type, value}. type in
    #     {price_close_above, price_close_below, signal, none}; value is
    #     a numeric price or a known signal keyword. When the brain
    #     provides it in the CALL_A response, source='brain_stated'.
    #     When brain omits/returns invalid, source='heuristic_fallback'
    #     and the watchdog uses the snapshot below.
    #   thesis_source — 'brain_stated' / 'heuristic_fallback'. Lets the
    #     CALL_A/CALL_B prompt clearly label which path is in use so
    #     Claude weighs the criterion accordingly (operator decision:
    #     brain-stated treated as authoritative; heuristic fallback
    #     treated as situational only).
    #   thesis_snapshot — JSON of the nearest aligned OB/FVG at entry
    #     (operator decision: nearest aligned level only). Watchdog
    #     monitors this level for close-beyond invalidation when
    #     source='heuristic_fallback'. Empty/{} when no aligned level
    #     existed at entry (e.g. trend-pullback or APEX flip path).
    #   thesis_state — VALID / DEGRADING / INVALIDATED. In-memory
    #     watchdog state mirrors this column; DB row is authoritative
    #     across restarts. Transitions are durable so a restart
    #     during INVALIDATED state preserves the brain-surfacing
    #     contract on next CALL_A/CALL_B.
    #
    # Defaults are neutral ('' / '{}' / 'VALID') so pre-fix rows remain
    # well-formed under the existing SELECT statements in
    # ThesisManager.get_open_theses and reconcile_with_shadow.
    "ALTER TABLE trade_thesis ADD COLUMN thesis_invalidation TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE trade_thesis ADD COLUMN thesis_source TEXT NOT NULL DEFAULT 'brain_stated'",
    "ALTER TABLE trade_thesis ADD COLUMN thesis_snapshot TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE trade_thesis ADD COLUMN thesis_state TEXT NOT NULL DEFAULT 'VALID'",
    # --- v35: Mid-Hold Trade Management Fix (Phase 3.1, 2026-05-19) ---
    # `thesis_events` queue table — surfaces mid-hold ensemble flips
    # (1A) and structural-thesis invalidations (2A) to the brain via
    # the next scheduled CALL_A or CALL_B prompt. Per-position DB-backed
    # queue (operator decision: survive process restarts).
    #
    # Lifecycle:
    #   - Watchdog detects event -> INSERT row (consumed_at=NULL)
    #   - Strategist builds CALL_A or CALL_B -> reads unseen events for
    #     open-position symbols -> renders in prompt -> on Claude
    #     response, UPDATE consumed_at=now, consumed_by=CALL_A|CALL_B
    #   - On position close -> DELETE rows for the closed order_id
    #
    # `thesis_id` is nullable so an event arriving before the thesis row
    # exists (rare race) is still persisted. The index on
    # (symbol, consumed_at) accelerates the canonical SELECT
    # "WHERE consumed_at IS NULL AND symbol IN (...)".
    """
    CREATE TABLE IF NOT EXISTS thesis_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        order_id TEXT NOT NULL,
        thesis_id INTEGER,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at TEXT,
        consumed_by TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_thesis_events_symbol_unconsumed "
    "ON thesis_events(symbol, consumed_at)",
    "CREATE INDEX IF NOT EXISTS idx_thesis_events_order_id "
    "ON thesis_events(order_id)",
    # --- v36: Layer 1 Defect 9 — restore-loss fix ---
    # coin_regime_history was missing volume_ratio and atr_percentile,
    # so the cold-start restore at regime_worker._restore_per_coin
    # fabricated atr_percentile=0 and volume_ratio=1.0 for every coin.
    # Live regime computation produces both metrics; persisting them
    # closes the post-restart information loss. Existing rows have NULL
    # for both columns and are treated as "metric not available" by the
    # restore path (no fabrication) so the only post-fix consequence is
    # that pre-fix rows contribute slightly less information until the
    # next regime tick refreshes their values.
    "ALTER TABLE coin_regime_history ADD COLUMN volume_ratio REAL",
    "ALTER TABLE coin_regime_history ADD COLUMN atr_percentile REAL",
    # --- v37: Layer 2 Defect 6 — per-trade supporting/opposing strategy counts ---
    # The herding finding (broad agreement correlates with losses) depends on
    # knowing how many of the 36 strategies supported the trade direction at
    # decision time. Pre-fix the count was reconstructable only via
    # STRAT_VOTE_TRACE logs, which fire ONLY for STRONG-consensus trades and
    # are lost on log rotation. Layer 2 D6 persists supporting_count and
    # opposing_count on the trade_intelligence row so they join cleanly to
    # trade_log outcomes for Layer 3-4 strategy weighting + herding analysis.
    # Source: EnsembleStateCache.get_current_consensus(symbol).agreeing /
    # .opposing at register_trade time (already in memory; zero new compute).
    # Existing rows get NULL — treated as "metric not available" per Rule 5
    # (an honest null beats a fake zero).
    "ALTER TABLE trade_intelligence ADD COLUMN supporting_count INTEGER",
    "ALTER TABLE trade_intelligence ADD COLUMN opposing_count INTEGER",
    # --- v38: Layer 2 Defect 3 — pre-downgrade signal label as queryable columns ---
    # The SIG_CLASSIFY label that flows to the brain is computed by the
    # multi-source classifier (e.g., strong_buy) THEN downgraded by the Phase 29
    # confidence gate at signal_generator.py:230-278 (e.g., strong_buy at conf=0.43
    # → NEUTRAL because below the 0.40 buy floor). Pre-fix the persisted signals
    # row carried only the post-downgrade label; the pre-downgrade label lived
    # in components JSON as ``original_signal_type`` (Phase 4B fix at line 296).
    # Layer 2 D3 promotes BOTH values to top-level columns so Layer 4 label-
    # quality analysis can query directly without JSON parsing:
    #   - signal_type_pre_downgrade TEXT — what the classifier actually emitted
    #   - confidence_floor_failed INTEGER — 0/1 flag for the downgrade event
    # Existing rows have NULL for both (honest absence per Rule 5; the data
    # IS already in components JSON for any pre-v38 row that needs it).
    "ALTER TABLE signals ADD COLUMN signal_type_pre_downgrade TEXT",
    "ALTER TABLE signals ADD COLUMN confidence_floor_failed INTEGER",
    # --- v39: Layer 2 Defect 1 — per-cycle setup_id join key ---
    # ensemble_votes table (created in v24) was schema-only — zero INSERTs
    # anywhere in src/. The schema requires ``setup_id TEXT NOT NULL`` but
    # no setup_id was ever generated. Layer 2 D1 wires the batched per-cycle
    # vote write: EnsembleVoter.vote generates
    # setup_id = f"{cycle_iso}_{symbol}" and stores it on the EnsembleResult;
    # strategy_worker writes the per-strategy votes via executemany; the
    # same setup_id is plumbed onto the trade_intelligence row so a join
    # ON setup_id gives Layer 3 the per-trade vote breakdown.
    "ALTER TABLE trade_intelligence ADD COLUMN setup_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_ensemble_votes_setup ON ensemble_votes(setup_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_intel_setup ON trade_intelligence(setup_id)",
    # --- v40: Layer 2 Defect 2 — claude_decisions per-trade enrichment ---
    # The legacy claude_decisions schema (data_lake.write_claude_decision)
    # writes ONE row per strategic review with a count of trades — not
    # per-trade. Per-trade decision context is genuinely missing from
    # queryable form. The operator chose to enrich claude_decisions with
    # per-trade fields rather than retire it or wire the dead brain_decisions
    # table. After v40 the table holds:
    #   - one strategic_review row per Claude call (legacy contract; new
    #     per-trade fields NULL on these rows)
    #   - N per-trade-directive rows per call (decision_type='trade_directive',
    #     symbol/trade_directive_id/conviction populated)
    # Layer 3-4 join: claude_decisions.trade_directive_id ↔ trade_log.trade_id
    # (when the strategy_worker plumbs the directive_id forward at register
    # time; out of scope for this commit — the per-directive rows are written
    # immediately at parse time so the data exists, joinable by ts+symbol
    # for now and by trade_directive_id once strategy_worker plumbs it).
    "ALTER TABLE claude_decisions ADD COLUMN symbol TEXT",
    "ALTER TABLE claude_decisions ADD COLUMN trade_directive_id TEXT",
    "ALTER TABLE claude_decisions ADD COLUMN conviction REAL",
    "CREATE INDEX IF NOT EXISTS idx_claude_decisions_directive ON claude_decisions(trade_directive_id)",
    "CREATE INDEX IF NOT EXISTS idx_claude_decisions_symbol ON claude_decisions(symbol, ts_epoch DESC)",
]


async def run_migrations(db: DatabaseManager) -> None:
    """Execute all migrations to create/update the database schema.

    Checks current schema version and only logs upgrade if version changed.

    Args:
        db: Active DatabaseManager instance.
    """
    # Check current schema version
    current_version = 0
    try:
        result = await db.fetch_one("SELECT MAX(version) as v FROM schema_version")
        if result and result["v"]:
            current_version = result["v"]
    except Exception:
        pass  # schema_version table may not exist yet

    if current_version >= SCHEMA_VERSION:
        log.debug("Schema version {v} is current — skipping migrations", v=current_version)
        return  # All migrations already applied; nothing to do
    else:
        log.info(
            "Schema upgrade: {old} -> {new}",
            old=current_version, new=SCHEMA_VERSION,
        )

    # Phase 13 (P1-12): pre-flight column-exists check for ALTER TABLE
    # ADD COLUMN statements. SQLite has no IF NOT EXISTS for columns, so
    # the original code relied on catching the exception and matching the
    # error text. The brief observed 10,666 ERROR logs per restart —
    # likely because the SQLite error format varied across versions and
    # not all "duplicate column" variants were matched. The PRAGMA-based
    # check is authoritative: it inspects the actual schema instead of
    # parsing error text. The exception handler is kept as a safety net
    # for the rare case where the PRAGMA returns a stale view.
    import re as _re
    _ALTER_RE = _re.compile(
        r"^\s*ALTER\s+TABLE\s+([\w]+)\s+ADD\s+COLUMN\s+([\w]+)\b",
        _re.IGNORECASE,
    )
    _column_cache: dict[str, set[str]] = {}

    async def _existing_columns(table: str) -> set[str]:
        if table in _column_cache:
            return _column_cache[table]
        try:
            rows = await db.fetch_all(f"PRAGMA table_info({table})")
            cols = {r["name"] for r in rows}
        except Exception:
            cols = set()
        _column_cache[table] = cols
        return cols

    skipped_existing = 0
    for i, sql in enumerate(MIGRATIONS):
        sql_stripped = sql.strip()
        m = _ALTER_RE.match(sql_stripped)
        if m:
            table, column = m.group(1), m.group(2)
            existing = await _existing_columns(table)
            if column in existing:
                # Skip without invoking the slow execute path. Logged at
                # DEBUG so investigators can confirm the no-op happened.
                log.debug(
                    f"MIGRATION_SKIP_EXISTING | table={table} column={column} | {ctx()}"
                )
                skipped_existing += 1
                continue
            # Pre-emptively register so subsequent migrations adding the
            # same column don't re-query PRAGMA.
            existing.add(column)

        try:
            await db.execute(sql_stripped)
        except Exception as e:
            # Belt-and-braces: a duplicate-column error here means the
            # PRAGMA missed it (race or out-of-sync table_info). Always
            # downgrade to DEBUG; never re-raise.
            err_lower = str(e).lower()
            if (
                "duplicate column" in err_lower
                or "already exists" in err_lower
            ):
                log.debug(
                    f"MIGRATION_SKIP_EXISTING | i={i} via_exception=Y "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )
                skipped_existing += 1
            else:
                log.error("Migration {i} failed: {err}", i=i, err=str(e))
                raise

    if skipped_existing > 0:
        log.info(
            f"MIGRATIONS_SUMMARY | total={len(MIGRATIONS)} "
            f"skipped_existing={skipped_existing} | {ctx()}"
        )

    # Record schema version
    await db.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    if current_version < SCHEMA_VERSION:
        log.info("Migrations complete. Schema version: {v}", v=SCHEMA_VERSION)
