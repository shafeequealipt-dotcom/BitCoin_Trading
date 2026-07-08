"""Data Lake Writer — writes to the 6 new data lake tables.

Tables:
  1. market_snapshots — 60s compressed market state
  2. trade_log — every trade, forever
  3. position_snapshots — 60s position state, 7-day retention
  4. claude_decisions — every Claude call, compressed
  5. event_log — unified event timeline
  6. daily_summary — rolled up daily, forever
"""

import json
import time

from src.core.log_context import ctx, get_tid
from src.core.logging import get_logger

log = get_logger("data_lake")


class DataLakeWriter:
    """Writes data to the 6 data lake tables."""

    def __init__(self, db):
        self.db = db
        # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G2): optional
        # AlertManager wired by WorkerManager so DL_TRADE_SUSPECT data
        # integrity violations surface as Telegram alerts. Set via
        # set_alert_manager() to keep DataLakeWriter constructor stable.
        self._alert_manager = None

    def set_alert_manager(self, alert_manager) -> None:
        """Wire AlertManager so DL_TRADE_SUSPECT can fire alerts."""
        self._alert_manager = alert_manager

    async def write_market_snapshot(
        self, btc_price: float = 0, eth_price: float = 0, sol_price: float = 0,
        regime: str = "", fear_greed: int = 0, full_data: dict | None = None,
    ) -> None:
        try:
            await self.db.execute(
                """INSERT INTO market_snapshots
                   (ts_epoch, btc_price, eth_price, sol_price, regime, fear_greed, full_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), btc_price, eth_price, sol_price, regime, fear_greed,
                 json.dumps(full_data) if full_data else "{}"),
            )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL):
            # promoted from DEBUG to WARNING with structured tag. Silent
            # write failures masked the trade-data integrity risk.
            log.warning(
                f"DL_MARKET_SNAPSHOT_WRITE_FAIL | err='{str(e)[:200]}' | {ctx()}"
            )

    async def write_trade(
        self, trade_id: str, symbol: str, direction: str,
        entry_price: float = 0, exit_price: float = 0,
        size_usd: float = 0, leverage: int = 1,
        pnl_pct: float = 0, pnl_usd: float = 0,
        strategy: str = "", thesis: str = "",
        close_reason: str = "", hold_minutes: float = 0,
        opened_at: str = "", closed_at: str = "",
        exchange_mode: str = "",
    ) -> None:
        """Persist a closed trade to the trade_log data-lake table.

        P8 of P1-P10: ``exchange_mode`` is now required to be passed by
        callers (defaults to empty string when not provided — column
        DEFAULT 'shadow' applies, but the audit-flagged 116-row mistag
        would re-occur if a caller forgets). The coordinator's data-lake
        close callback resolves it from ``transformer.current_mode``
        and forwards it here. Empty string falls through to the column
        default ('shadow') — preserves backward compat for any test
        fixture not yet updated, but emits a WARNING so the gap is
        visible in logs.
        """
        if not exchange_mode:
            log.warning(
                f"DL_TRADE_NO_MODE | tid={trade_id} sym={symbol} "
                f"caller_did_not_pass_exchange_mode_falling_back_to_column_default | {ctx()}"
            )
        log.info(
            f"DL_TRADE | tid={trade_id} sym={symbol} dir={direction} "
            f"ent={entry_price} ext={exit_price} pnl={pnl_pct:+.4f}% "
            f"pnl$={pnl_usd:+.4f} rsn={close_reason} held={hold_minutes:.1f}min "
            f"mode={exchange_mode or 'default'} | {ctx()}"
        )
        # 0.00% PnL bug diagnostic
        # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G2): wire to
        # AlertManager.send_risk_warning so data integrity violations
        # surface as CRITICAL Telegram alerts (not just an ERROR log line).
        if pnl_pct == 0 and entry_price > 0 and exit_price > 0 and entry_price != exit_price:
            log.error(
                f"DL_TRADE_SUSPECT | tid={trade_id} sym={symbol} ent={entry_price} "
                f"ext={exit_price} pnl=0.00 — DATA INTEGRITY ISSUE | {ctx()}"
            )
            if self._alert_manager is not None:
                try:
                    await self._alert_manager.send_risk_warning(
                        "DL_TRADE_SUSPECT",
                        {
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "entry": entry_price,
                            "exit": exit_price,
                            "pnl_pct": pnl_pct,
                            "issue": "pnl_zero_with_price_delta",
                        },
                    )
                except Exception as alert_err:
                    log.warning(
                        f"DL_TRADE_SUSPECT_ALERT_FAIL | tid={trade_id} "
                        f"err='{str(alert_err)[:120]}' | {ctx()}"
                    )
        if exit_price == 0 or exit_price is None:
            log.error(
                f"DL_TRADE_SUSPECT | tid={trade_id} sym={symbol} ext={exit_price} "
                f"— zero exit price, PnL will be wrong | {ctx()}"
            )
            if self._alert_manager is not None:
                try:
                    await self._alert_manager.send_risk_warning(
                        "DL_TRADE_SUSPECT",
                        {
                            "trade_id": trade_id,
                            "symbol": symbol,
                            "exit": exit_price,
                            "issue": "zero_exit_price",
                        },
                    )
                except Exception as alert_err:
                    log.warning(
                        f"DL_TRADE_SUSPECT_ALERT_FAIL | tid={trade_id} "
                        f"err='{str(alert_err)[:120]}' | {ctx()}"
                    )
        try:
            if exchange_mode:
                # P8: explicit mode tagging — overrides the column default.
                await self.db.execute(
                    """INSERT OR REPLACE INTO trade_log
                       (trade_id, symbol, direction, entry_price, exit_price,
                        size_usd, leverage, pnl_pct, pnl_usd,
                        strategy, thesis, close_reason, hold_minutes,
                        opened_at, closed_at, exchange_mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (trade_id, symbol, direction, entry_price, exit_price,
                     size_usd, leverage, pnl_pct, pnl_usd,
                     strategy, thesis[:500], close_reason, hold_minutes,
                     opened_at, closed_at, exchange_mode),
                )
            else:
                # Backward-compat path: column DEFAULT 'shadow' applies.
                # Triggers the WARNING above so this path is observable
                # and not silently mistagging.
                await self.db.execute(
                    """INSERT OR REPLACE INTO trade_log
                       (trade_id, symbol, direction, entry_price, exit_price,
                        size_usd, leverage, pnl_pct, pnl_usd,
                        strategy, thesis, close_reason, hold_minutes,
                        opened_at, closed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (trade_id, symbol, direction, entry_price, exit_price,
                     size_usd, leverage, pnl_pct, pnl_usd,
                     strategy, thesis[:500], close_reason, hold_minutes,
                     opened_at, closed_at),
                )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL):
            # promoted from DEBUG to WARNING with structured tag. The
            # audit prompt's #1 named gap — silent trade_log write failures
            # masked data-integrity risk for TIAS / strategy-edge measurement.
            log.warning(
                f"DL_TRADE_WRITE_FAIL | tid={trade_id} sym={symbol} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

    async def write_position_snapshot(
        self, symbol: str, direction: str = "",
        entry_price: float = 0, mark_price: float = 0,
        pnl_pct: float = 0, unrealized_pnl: float = 0,
        age_minutes: float = 0,
    ) -> None:
        try:
            await self.db.execute(
                """INSERT INTO position_snapshots
                   (ts_epoch, symbol, direction, entry_price, mark_price,
                    pnl_pct, unrealized_pnl, age_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), symbol, direction, entry_price, mark_price,
                 pnl_pct, unrealized_pnl, age_minutes),
            )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL).
            log.warning(
                f"DL_POSITION_SNAPSHOT_WRITE_FAIL | sym={symbol} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

    async def write_claude_decision(
        self, decision_type: str = "strategic_review",
        new_trades_count: int = 0, position_actions_count: int = 0,
        market_view: str = "", risk_level: str = "",
        response_time_ms: int = 0, prompt_length: int = 0,
        full_response: str = "",
        # Layer 2 Defect 2 (2026-05-22) — per-trade enrichment fields.
        # Populated when ``decision_type == "trade_directive"`` to record
        # one row per individual trade directive Claude returned. Legacy
        # strategic_review rows pass these as defaults so the new columns
        # remain NULL — backward-compatible with all existing callers.
        symbol: str = "",
        trade_directive_id: str = "",
        conviction: float | None = None,
    ) -> None:
        log.info(f"DL_DECISION | type={decision_type} trades={new_trades_count} acts={position_actions_count} el={response_time_ms}ms prompt={prompt_length} sym={symbol or '-'} | {ctx()}")
        try:
            await self.db.execute(
                """INSERT INTO claude_decisions
                   (ts_epoch, decision_type, new_trades_count, position_actions_count,
                    market_view, risk_level, response_time_ms, prompt_length, full_response,
                    symbol, trade_directive_id, conviction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), decision_type, new_trades_count, position_actions_count,
                 market_view[:200], risk_level, response_time_ms, prompt_length,
                 full_response[:2000],
                 (symbol or None), (trade_directive_id or None), conviction),
            )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL).
            log.warning(
                f"DL_DECISION_WRITE_FAIL | type={decision_type} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

    async def write_event(
        self, event_type: str, priority: str = "LOW",
        symbol: str = "", data: dict | None = None, source: str = "",
    ) -> None:
        try:
            await self.db.execute(
                """INSERT INTO event_log
                   (ts_epoch, event_type, priority, symbol, data, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (time.time(), event_type, priority, symbol,
                 json.dumps(data) if data else "{}", source),
            )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL).
            log.warning(
                f"DL_EVENT_WRITE_FAIL | event_type={event_type} sym={symbol} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

    async def write_daily_summary(
        self, date: str, total_trades: int = 0,
        wins: int = 0, losses: int = 0,
        total_pnl_pct: float = 0, total_pnl_usd: float = 0,
        best_trade_pct: float = 0, worst_trade_pct: float = 0,
        avg_hold_minutes: float = 0,
        starting_equity: float = 0, ending_equity: float = 0,
        regime_summary: str = "", trades_json: str = "[]",
    ) -> None:
        try:
            await self.db.execute(
                """INSERT OR REPLACE INTO daily_summary
                   (date, total_trades, wins, losses,
                    total_pnl_pct, total_pnl_usd,
                    best_trade_pct, worst_trade_pct, avg_hold_minutes,
                    starting_equity, ending_equity,
                    regime_summary, trades_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (date, total_trades, wins, losses,
                 total_pnl_pct, total_pnl_usd,
                 best_trade_pct, worst_trade_pct, avg_hold_minutes,
                 starting_equity, ending_equity,
                 regime_summary, trades_json),
            )
        except Exception as e:
            # Phase 12.9 (lifecycle-logging-audit Gap 9.3-G1, CRITICAL).
            log.warning(
                f"DL_DAILY_SUMMARY_WRITE_FAIL | date={date} "
                f"err='{str(e)[:200]}' | {ctx()}"
            )
