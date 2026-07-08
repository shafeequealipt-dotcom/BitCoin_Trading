"""Trade Recorder: records which strategy generated each trade.

Shared by all 3 trade execution paths:
- StrategyWorker (brain bypass)
- BrainV2._execute_trade()
- PerformanceEnforcer._force_trade_now()

Writes to the `strategy_trades` table so the optimizer and ensemble voter
can track per-strategy performance over time.

Also exposes ``recent_loss_symbols`` — used by ScannerWorker's Phase 5
qualitative filter to block coins that just lost within a configurable
lookback window. Single batched query per cycle keeps the cost O(1)
per cycle rather than O(50) per cycle.
"""

from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("trade_recorder")


async def recent_loss_symbols(
    db: DatabaseManager, *, hours: int,
) -> set[str]:
    """Return the set of symbols that closed at a loss in the last ``hours``.

    Reads the ``trade_intelligence`` table (the canonical post-close
    record). One query, indexed scan over ``trade_closed_at``. Returns
    an empty set on any error so a recorder hiccup never crashes the
    scanner cycle.

    Args:
        db: Active DatabaseManager.
        hours: Lookback window (must be > 0).

    Returns:
        Set of symbols with at least one losing close inside the window.
    """
    if hours <= 0:
        return set()
    try:
        rows = await db.fetch_all(
            "SELECT DISTINCT symbol FROM trade_intelligence "
            "WHERE win = 0 AND trade_closed_at >= datetime('now', ?)",
            (f"-{int(hours)} hours",),
        )
        return {r["symbol"] for r in rows if r.get("symbol")}
    except Exception as e:
        log.debug(f"recent_loss_symbols query failed: {e}")
        return set()


async def recent_losses_for_setup(
    db: DatabaseManager,
    *,
    symbol: str,
    side: str,
    regime: str | None = None,
    hours: int = 336,
    limit: int = 2,
) -> list[dict]:
    """Return the most recent losing closes that match a (symbol, side[,
    regime]) setup.

    Brain-prompt-enrichment Phase 3.5 (E6) — used by the strategist's
    CALL_A per-coin block (under RECENT_LOSER_COOLDOWN flag) and the
    CALL_B per-position block to bridge TIAS-analyzed lessons into the
    brain prompt. Filters to rows whose DeepSeek analysis populated
    ``ds_why`` so the brain receives the actionable cause excerpt,
    not just the raw outcome.

    The default 14-day (336-hour) window matches the typical setup
    half-life observed in trade_intelligence — older lessons are
    discarded because regime, account size, and APEX behaviour have
    likely shifted enough to make the lesson stale.

    Args:
        db: Active DatabaseManager.
        symbol: Trading pair (case-sensitive — matches the DB
            convention, e.g. ``"BNBUSDT"``).
        side: Direction of the candidate / open position (``"Buy"``
            or ``"Sell"``). Matched directly against
            ``trade_intelligence.direction``.
        regime: Optional regime label to require a match against
            ``trade_intelligence.regime``. ``None`` skips the regime
            filter so the helper still returns same-symbol-same-side
            lessons even when the regime label has no recent peer.
        hours: Lookback window (default 336 = 14 days). Trades older
            than the cutoff are excluded. Values <= 0 yield an empty
            list (safe-default sentinel).
        limit: Maximum number of lesson rows to return (default 2 —
            calibrated against the per-coin char budget the brain
            prompt enforces).

    Returns:
        List of dict rows (sorted newest first), each with keys:
        ``trade_closed_at``, ``direction``, ``pnl_pct``, ``hold_seconds``,
        ``closed_by``, ``regime``, ``ds_why``, ``ds_category``,
        ``ds_what_should_done``. Empty list on any DB error so a
        recorder hiccup never crashes the prompt-build cycle.
    """
    if hours <= 0 or limit <= 0 or not symbol or not side:
        return []
    try:
        if regime:
            rows = await db.fetch_all(
                "SELECT trade_closed_at, direction, pnl_pct, hold_seconds, "
                "closed_by, regime, ds_why, ds_category, ds_what_should_done "
                "FROM trade_intelligence "
                "WHERE symbol = ? AND direction = ? AND win = 0 "
                "AND regime = ? "
                "AND trade_closed_at >= datetime('now', ?) "
                "AND ds_why IS NOT NULL "
                "ORDER BY trade_closed_at DESC LIMIT ?",
                (symbol, side, regime, f"-{int(hours)} hours", int(limit)),
            )
        else:
            rows = await db.fetch_all(
                "SELECT trade_closed_at, direction, pnl_pct, hold_seconds, "
                "closed_by, regime, ds_why, ds_category, ds_what_should_done "
                "FROM trade_intelligence "
                "WHERE symbol = ? AND direction = ? AND win = 0 "
                "AND trade_closed_at >= datetime('now', ?) "
                "AND ds_why IS NOT NULL "
                "ORDER BY trade_closed_at DESC LIMIT ?",
                (symbol, side, f"-{int(hours)} hours", int(limit)),
            )
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug(f"recent_losses_for_setup query failed: {e}")
        return []


async def session_attempts_today(
    db: DatabaseManager, *, symbols: list[str], exchange_mode: str,
) -> dict[str, dict]:
    """Per-coin executed-attempt count and net PnL for the current UTC
    day (Four-Element Prompt Recalibration, Element 2, 2026-06-11).

    READ-ONLY query against the protected ``trade_log`` table — the
    truthful ledger (``pnl_usd`` carries the authoritative net figure
    written from the COORD_AUTH close path). One query for the whole
    candidate list (IN clause).

    Semantics, verified against the live schema and writers:
    - An "attempt" is one position ENTRY whose close has been recorded
      today. Partial-close rows share the entry's ``opened_at`` to the
      microsecond (same ``state.opened_at_dt``), so
      ``COUNT(DISTINCT opened_at)`` collapses them to one attempt while
      ``SUM(pnl_usd)`` keeps every booked portion.
    - The UTC-day window is a LEXICAL range on the ISO-8601 text
      (``opened_at >= date('now') AND < date('now','+1 day')``) —
      parser-independent, served by ``idx_trade_log_opened``, and it
      excludes legacy empty-string rows. ``date('now')`` is UTC in
      SQLite; ``opened_at`` is written as
      ``datetime.now(timezone.utc).isoformat()``.
    - ``exchange_mode`` MUST be the active adapter's mode (resolved by
      the caller from ``transformer.current_mode``, never hardcoded) so
      shadow rows never pollute a bybit_demo count or vice versa.
    - Currently-open positions have no ``trade_log`` row yet and are
      not counted; open coins carry [POS] and are position-gated from
      new trades anyway.
    - Known narrow boundary (cross-check audit, 2026-06-11): state
      recovery after a restart re-parses ``opened_at`` from the
      persisted thesis row, so entry identity normally SURVIVES a
      restart; only when that row's timestamp is missing or unparseable
      does the recovered position get a recovery-time ``opened_at``,
      in which case a partial close booked before the restart and the
      final close after it could count as two attempts. Accepted as-is:
      rarer than the partial-collapse case this query already handles,
      and the trade-id root has the same recovery dependency so
      switching keys would not remove it.

    Returns ``{symbol: {"attempts": int, "net_usd": float}}`` with only
    symbols that have at least one row today. Empty dict on any error
    or empty input so a hiccup never crashes the prompt build (and the
    renderer then shows NOTHING rather than a guessed value — Rule 4:
    a wrong awareness line is worse than none).
    """
    if not symbols or not exchange_mode:
        return {}
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = await db.fetch_all(
            "SELECT symbol, COUNT(DISTINCT opened_at) AS attempts, "
            "SUM(pnl_usd) AS net_usd FROM trade_log "
            f"WHERE symbol IN ({placeholders}) AND exchange_mode = ? "
            "AND opened_at >= date('now') "
            "AND opened_at < date('now', '+1 day') "
            "GROUP BY symbol",
            (*symbols, exchange_mode),
        )
        return {
            r["symbol"]: {
                "attempts": int(r["attempts"] or 0),
                "net_usd": float(r["net_usd"] or 0.0),
            }
            for r in rows
            if r.get("symbol")
        }
    except Exception as e:
        log.debug(f"session_attempts_today query failed: {e}")
        return {}


async def record_strategy_trade(
    db: DatabaseManager,
    *,
    symbol: str,
    strategy_name: str,
    direction: str,
    score: float = 0.0,
    ensemble_strength: str = "",
    ensemble_votes_for: float = 0.0,
    ensemble_votes_against: float = 0.0,
    leverage_used: int = 1,
    regime: str = "",
    source: str = "unknown",
) -> None:
    """Record a trade entry to strategy_trades.

    Called immediately after a successful place_order() in any execution path.
    PnL fields (pnl, pnl_pct, was_win, exit_time) are updated later when
    the trade closes via TradeCoordinator callbacks.

    Args:
        db: Database manager.
        symbol: Trading pair (e.g. BTCUSDT).
        strategy_name: Name of the strategy that generated this trade.
        direction: Buy or Sell.
        score: Trade score from TradeScorer (0-100).
        ensemble_strength: STRONG/GOOD/WEAK/LEAN/CONFLICT.
        ensemble_votes_for: Weighted buy votes.
        ensemble_votes_against: Weighted sell votes.
        leverage_used: Leverage multiplier.
        regime: Market regime at time of trade.
        source: Which execution path (brain_v2, brain_bypass, enforcer).
    """
    try:
        trade_id = f"{symbol}_{direction}_{now_utc().strftime('%Y%m%d%H%M%S')}"
        await db.execute(
            """INSERT INTO strategy_trades
               (trade_id, strategy_name, symbol, direction, score,
                ensemble_strength, ensemble_votes_for, ensemble_votes_against,
                leverage_used, regime, entry_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_id,
                strategy_name,
                symbol,
                direction,
                round(score, 2),
                ensemble_strength,
                round(ensemble_votes_for, 2),
                round(ensemble_votes_against, 2),
                leverage_used,
                regime,
                now_utc().isoformat(),
                now_utc().isoformat(),
            ),
        )
        log.info(
            "Recorded strategy trade: {strat} {dir} {sym} score={score:.0f} via {src}",
            strat=strategy_name, dir=direction, sym=symbol,
            score=score, src=source,
        )
    except Exception as e:
        log.error("Failed to record strategy trade: {err}", err=str(e))
