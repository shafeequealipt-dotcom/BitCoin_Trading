"""Database schema migrations: creates all tables needed by Shadow.

Phase 1 (v1) creates 12 tables:
  - 5 data collector tables: klines, ticker_snapshots, funding_rates,
    open_interest_history, tracked_coins
  - 5 virtual exchange tables: virtual_wallet, virtual_positions,
    trade_history, wallet_snapshots, daily_summary
  - 2 system tables: schema_version, shadow_settings

Phase 3 (v2) adds 17 exit columns to virtual_positions for close tracking.
"""

from src.database.connection import DatabaseManager
from src.utils.logging import get_logger

log = get_logger("database")

SCHEMA_VERSION = 3

# Each entry is one SQL statement. All use IF NOT EXISTS for idempotency.
MIGRATIONS_V1: list[str] = [

    # =========================================================================
    # TABLE 1: klines — 1-minute candles for all tracked coins
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS klines (
        symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        turnover REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (symbol, timestamp)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_klines_timestamp
    ON klines(timestamp DESC)
    """,

    # =========================================================================
    # TABLE 2: ticker_snapshots — full ticker state every 60 seconds
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS ticker_snapshots (
        symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        last_price REAL,
        mark_price REAL,
        index_price REAL,
        bid1_price REAL,
        bid1_size REAL,
        ask1_price REAL,
        ask1_size REAL,
        high_24h REAL,
        low_24h REAL,
        volume_24h REAL,
        turnover_24h REAL,
        price_change_24h_pct REAL,
        funding_rate REAL,
        open_interest REAL,
        open_interest_value REAL,
        PRIMARY KEY (symbol, timestamp)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ticker_timestamp
    ON ticker_snapshots(timestamp DESC)
    """,

    # =========================================================================
    # TABLE 3: funding_rates — funding rate history every 8 hours
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS funding_rates (
        symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        funding_rate REAL NOT NULL,
        PRIMARY KEY (symbol, timestamp)
    )
    """,

    # =========================================================================
    # TABLE 4: open_interest_history — OI snapshots every 5 minutes
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS open_interest_history (
        symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        open_interest REAL NOT NULL,
        open_interest_value REAL NOT NULL,
        PRIMARY KEY (symbol, timestamp)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_oi_timestamp
    ON open_interest_history(timestamp DESC)
    """,

    # =========================================================================
    # TABLE 5: tracked_coins — the 100 coins currently being tracked
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS tracked_coins (
        symbol TEXT PRIMARY KEY,
        added_at TEXT NOT NULL DEFAULT (datetime('now')),
        volume_24h_usd REAL DEFAULT 0,
        rank_by_volume INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        last_tick_at TEXT
    )
    """,

    # =========================================================================
    # TABLE 6: virtual_wallet — single row wallet state
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS virtual_wallet (
        id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        starting_balance REAL NOT NULL,
        total_realized_pnl REAL NOT NULL DEFAULT 0,
        total_fees_paid REAL NOT NULL DEFAULT 0,
        total_trades INTEGER NOT NULL DEFAULT 0,
        total_wins INTEGER NOT NULL DEFAULT 0,
        total_losses INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_reset_at TEXT,
        last_updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # =========================================================================
    # TABLE 7: virtual_positions — currently open paper positions
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS virtual_positions (
        position_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        quantity REAL NOT NULL,
        leverage INTEGER NOT NULL,
        notional_value REAL NOT NULL,
        margin_used REAL NOT NULL,
        stop_loss_price REAL,
        take_profit_price REAL,
        status TEXT NOT NULL DEFAULT 'open',
        opened_at TEXT NOT NULL DEFAULT (datetime('now')),
        entry_fee_usd REAL NOT NULL DEFAULT 0,
        entry_bid_price REAL,
        entry_ask_price REAL,
        entry_spread_pct REAL,
        entry_slippage_pct REAL,
        entry_slippage_usd REAL,
        entry_funding_rate REAL,
        entry_volume_24h REAL,
        peak_pnl_pct REAL DEFAULT 0,
        max_drawdown_pct REAL DEFAULT 0,
        time_in_profit_seconds INTEGER DEFAULT 0,
        time_in_loss_seconds INTEGER DEFAULT 0,
        sl_modification_count INTEGER DEFAULT 0,
        tp_modification_count INTEGER DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_positions_status
    ON virtual_positions(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
    ON virtual_positions(symbol, status)
    """,

    # =========================================================================
    # TABLE 8: trade_history — complete record of every closed trade (NEVER deleted)
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS trade_history (
        trade_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        quantity REAL NOT NULL,
        leverage INTEGER NOT NULL,
        notional_value REAL NOT NULL,
        margin_used REAL NOT NULL,
        initial_stop_loss REAL,
        initial_take_profit REAL,
        final_stop_loss REAL,
        final_take_profit REAL,
        opened_at TEXT NOT NULL,
        closed_at TEXT NOT NULL,
        hold_duration_seconds INTEGER NOT NULL,
        entry_fee_usd REAL NOT NULL DEFAULT 0,
        exit_fee_usd REAL NOT NULL DEFAULT 0,
        total_fees_usd REAL NOT NULL DEFAULT 0,
        entry_slippage_usd REAL DEFAULT 0,
        exit_slippage_usd REAL DEFAULT 0,
        total_slippage_usd REAL DEFAULT 0,
        gross_pnl_pct REAL NOT NULL,
        gross_pnl_usd REAL NOT NULL,
        net_pnl_pct REAL NOT NULL,
        net_pnl_usd REAL NOT NULL,
        peak_pnl_pct REAL DEFAULT 0,
        max_drawdown_pct REAL DEFAULT 0,
        time_in_profit_seconds INTEGER DEFAULT 0,
        time_in_loss_seconds INTEGER DEFAULT 0,
        sl_modification_count INTEGER DEFAULT 0,
        tp_modification_count INTEGER DEFAULT 0,
        close_trigger TEXT NOT NULL,
        result TEXT NOT NULL,
        entry_bid_price REAL,
        entry_ask_price REAL,
        exit_bid_price REAL,
        exit_ask_price REAL,
        entry_funding_rate REAL,
        exit_funding_rate REAL,
        entry_volume_24h REAL,
        exit_volume_24h REAL,
        wallet_balance_after REAL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trades_symbol
    ON trade_history(symbol)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trades_closed_at
    ON trade_history(closed_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_trades_result
    ON trade_history(result)
    """,

    # =========================================================================
    # TABLE 9: wallet_snapshots — minute-by-minute equity curve
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS wallet_snapshots (
        timestamp TEXT PRIMARY KEY,
        total_equity REAL NOT NULL,
        available_balance REAL NOT NULL,
        margin_in_use REAL NOT NULL,
        unrealized_pnl REAL NOT NULL DEFAULT 0,
        realized_pnl_today REAL NOT NULL DEFAULT 0,
        open_position_count INTEGER NOT NULL DEFAULT 0,
        total_trades_today INTEGER NOT NULL DEFAULT 0,
        wins_today INTEGER NOT NULL DEFAULT 0,
        losses_today INTEGER NOT NULL DEFAULT 0,
        fees_today REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_wallet_snap_timestamp
    ON wallet_snapshots(timestamp DESC)
    """,

    # =========================================================================
    # TABLE 10: daily_summary — one row per day, full stats (NEVER deleted)
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS daily_summary (
        date TEXT PRIMARY KEY,
        starting_equity REAL,
        ending_equity REAL,
        daily_pnl_usd REAL,
        daily_pnl_pct REAL,
        total_trades INTEGER DEFAULT 0,
        trades_opened INTEGER DEFAULT 0,
        trades_closed INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0,
        gross_profit_usd REAL DEFAULT 0,
        gross_loss_usd REAL DEFAULT 0,
        net_profit_usd REAL DEFAULT 0,
        total_fees_usd REAL DEFAULT 0,
        total_slippage_usd REAL DEFAULT 0,
        total_volume_usd REAL DEFAULT 0,
        best_trade_pnl_pct REAL,
        best_trade_symbol TEXT,
        worst_trade_pnl_pct REAL,
        worst_trade_symbol TEXT,
        avg_win_pnl_pct REAL,
        avg_loss_pnl_pct REAL,
        avg_hold_winners_seconds INTEGER,
        avg_hold_losers_seconds INTEGER,
        max_consecutive_wins INTEGER DEFAULT 0,
        max_consecutive_losses INTEGER DEFAULT 0,
        max_drawdown_pct REAL,
        profit_factor REAL,
        long_trades INTEGER DEFAULT 0,
        short_trades INTEGER DEFAULT 0,
        long_win_rate REAL,
        short_win_rate REAL,
        coins_traded TEXT
    )
    """,

    # =========================================================================
    # TABLE 11: schema_version — migration tracking
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # =========================================================================
    # TABLE 12: shadow_settings — key-value runtime settings
    # =========================================================================
    """
    CREATE TABLE IF NOT EXISTS shadow_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
]


# =========================================================================
# Migration v2: Add exit columns to virtual_positions (Phase 3)
# =========================================================================
MIGRATIONS_V2: list[str] = [
    "ALTER TABLE virtual_positions ADD COLUMN exit_price REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_fee_usd REAL DEFAULT 0",
    "ALTER TABLE virtual_positions ADD COLUMN gross_pnl_pct REAL",
    "ALTER TABLE virtual_positions ADD COLUMN gross_pnl_usd REAL",
    "ALTER TABLE virtual_positions ADD COLUMN net_pnl_pct REAL",
    "ALTER TABLE virtual_positions ADD COLUMN net_pnl_usd REAL",
    "ALTER TABLE virtual_positions ADD COLUMN close_trigger TEXT",
    "ALTER TABLE virtual_positions ADD COLUMN closed_at TEXT",
    "ALTER TABLE virtual_positions ADD COLUMN hold_duration_seconds INTEGER",
    "ALTER TABLE virtual_positions ADD COLUMN exit_bid_price REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_ask_price REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_slippage_pct REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_slippage_usd REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_funding_rate REAL",
    "ALTER TABLE virtual_positions ADD COLUMN exit_volume_24h REAL",
    "ALTER TABLE virtual_positions ADD COLUMN result TEXT",
    "ALTER TABLE virtual_positions ADD COLUMN wallet_balance_after REAL",
]


# =========================================================================
# Migration v3: Add initial SL/TP columns to virtual_positions (Phase 5)
# =========================================================================
MIGRATIONS_V3: list[str] = [
    "ALTER TABLE virtual_positions ADD COLUMN initial_stop_loss REAL",
    "ALTER TABLE virtual_positions ADD COLUMN initial_take_profit REAL",
]


async def run_migrations(db: DatabaseManager) -> None:
    """Run all pending migrations to bring the database schema up to date.

    Safe to call multiple times — uses IF NOT EXISTS and version checks.

    Args:
        db: Connected DatabaseManager instance.
    """
    # Check current schema version
    current_version = 0
    try:
        row = await db.fetch_one("SELECT MAX(version) as v FROM schema_version")
        if row and row["v"] is not None:
            current_version = row["v"]
    except Exception:
        # Table doesn't exist yet — this is a fresh database
        pass

    if current_version >= SCHEMA_VERSION:
        log.debug(
            "Schema up to date (v{ver})", ver=current_version
        )
        return

    log.info(
        "Running migrations: v{cur} → v{target}",
        cur=current_version,
        target=SCHEMA_VERSION,
    )

    # v1: Create all 12 tables
    if current_version < 1:
        for sql in MIGRATIONS_V1:
            await db.execute(sql)
        await db.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (1,)
        )
        log.info("Migration v1 applied — 12 tables created")

    # v2: Add exit columns to virtual_positions
    if current_version < 2:
        for sql in MIGRATIONS_V2:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column may already exist (idempotent)
        await db.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (2,)
        )
        log.info("Migration v2 applied — exit columns added to virtual_positions")

    # v3: Add initial SL/TP columns to virtual_positions (Phase 5)
    if current_version < 3:
        for sql in MIGRATIONS_V3:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column may already exist (idempotent)
        await db.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (3,)
        )
        log.info("Migration v3 applied — initial SL/TP columns added")

    log.info("Migrations complete — schema v{ver}", ver=SCHEMA_VERSION)


async def initialize_wallet(db: DatabaseManager, starting_balance: float) -> None:
    """Insert the initial wallet row if it does not already exist.

    Args:
        db: Connected DatabaseManager instance.
        starting_balance: Initial wallet balance in USD.
    """
    existing = await db.fetch_one("SELECT id FROM virtual_wallet WHERE id = 1")
    if existing is None:
        await db.execute(
            "INSERT INTO virtual_wallet (id, starting_balance) VALUES (1, ?)",
            (starting_balance,),
        )
        log.info("Virtual wallet initialized: ${bal:,.2f}", bal=starting_balance)
    else:
        log.debug("Virtual wallet already exists, skipping initialization")
