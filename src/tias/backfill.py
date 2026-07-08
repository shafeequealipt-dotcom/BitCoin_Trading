"""TIAS Phase 4 — TIASBackfillWorker: retry failed DeepSeek analyses.

Runs on a 30-minute background loop. Finds rows where ds_why IS NULL
and analysis_attempts < max_attempts, retries the DeepSeek call, and
increments the attempt counter on failure.

After max_attempts (default 3) failed tries the row is silently skipped
forever, preventing endless retry loops for permanently-broken rows.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.config.settings import TIASSettings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.tias.models import TradeIntelligence
from src.tias.repository import TradeIntelligenceRepo

if TYPE_CHECKING:
    from src.tias.analyzer import TradeAnalyzer

log = get_logger("tias")


class TIASBackfillWorker:
    """Periodically retries DeepSeek analysis for trades where the initial
    analysis failed (ds_why IS NULL).

    Runs every 30 minutes. Processes up to 10 unanalyzed rows per cycle.
    After max_attempts failed attempts per row, stops retrying that row.

    Non-critical: if the worker raises, the background loop catches it,
    waits 60 s, and continues — trading is never affected.

    Args:
        repo: Active TradeIntelligenceRepo.
        analyzer: Initialised TradeAnalyzer (with DeepSeek client).
        settings: TIASSettings from application config.
    """

    _MAX_ATTEMPTS: int = 3
    _BATCH_SIZE: int = 10
    _INTER_CALL_SLEEP: float = 2.0  # seconds between API calls to avoid rate-limits

    def __init__(
        self,
        repo: TradeIntelligenceRepo,
        analyzer: "TradeAnalyzer",
        settings: TIASSettings,
    ) -> None:
        self._repo = repo
        self._analyzer = analyzer
        self._settings = settings

    async def run_once(self) -> None:
        """Execute one backfill cycle.

        Fetches up to _BATCH_SIZE unanalyzed rows, retries DeepSeek for each,
        and updates the DB on success or increments attempts on failure.
        Silent no-op when there is nothing to backfill.
        """
        if not self._settings.enabled:
            return

        pending = await self._repo.get_unanalyzed(
            limit=self._BATCH_SIZE,
            max_attempts=self._MAX_ATTEMPTS,
        )
        if not pending:
            return  # Nothing to backfill — no log spam

        log.info(
            "TIAS_BACKFILL_START | pending={n} | {c}",
            n=len(pending),
            c=ctx(),
        )

        success_count = 0
        fail_count = 0

        for row in pending:
            row_id: int = row["id"]
            symbol: str = row.get("symbol", "?")
            current_attempts: int = row.get("analysis_attempts") or 0

            try:
                trade = self._row_to_trade_intelligence(row)
                analysis = await self._analyzer.analyze(trade)
                await self._repo.update_analysis(row_id, analysis)
                success_count += 1

                log.info(
                    "TIAS_BACKFILL_OK | row={id} sym={sym} "
                    "cat={cat} attempt={att} | {c}",
                    id=row_id,
                    sym=symbol,
                    cat=analysis.get("ds_category", "?"),
                    att=current_attempts + 1,
                    c=ctx(),
                )

            except Exception as e:
                fail_count += 1
                await self._repo.increment_attempts(row_id)
                new_attempts = current_attempts + 1

                if new_attempts >= self._MAX_ATTEMPTS:
                    log.warning(
                        "TIAS_BACKFILL_GIVE_UP | row={id} sym={sym} "
                        "attempts={att}/{max} err='{err}' | {c}",
                        id=row_id,
                        sym=symbol,
                        att=new_attempts,
                        max=self._MAX_ATTEMPTS,
                        err=str(e)[:100],
                        c=ctx(),
                    )
                else:
                    log.warning(
                        "TIAS_BACKFILL_RETRY_FAIL | row={id} sym={sym} "
                        "attempts={att}/{max} err='{err}' | {c}",
                        id=row_id,
                        sym=symbol,
                        att=new_attempts,
                        max=self._MAX_ATTEMPTS,
                        err=str(e)[:80],
                        c=ctx(),
                    )

            await asyncio.sleep(self._INTER_CALL_SLEEP)

        log.info(
            "TIAS_BACKFILL_END | processed={n} success={ok} failed={fail} | {c}",
            n=len(pending),
            ok=success_count,
            fail=fail_count,
            c=ctx(),
        )

    def _row_to_trade_intelligence(self, row: dict) -> TradeIntelligence:
        """Reconstruct a TradeIntelligence object from a DB row dict.

        Maps DB column names (which match the dataclass field names) back to
        a TradeIntelligence so the analyzer can build the full DeepSeek prompt.
        Only the fields stored in the DB are reconstructed; Group F (ds_*)
        fields are intentionally left at their defaults (None) so the analyzer
        treats this as an unanalyzed record.

        Args:
            row: Row dict from TradeIntelligenceRepo.get_unanalyzed().

        Returns:
            TradeIntelligence with all Group A–E fields populated from the row.
        """
        return TradeIntelligence(
            # Group A — Trade Outcome (required, always populated)
            symbol=row.get("symbol", ""),
            direction=row.get("direction", ""),
            strategy_name=row.get("strategy_name", ""),
            strategy_category=row.get("strategy_category", "default"),
            source=row.get("source", ""),
            closed_by=row.get("closed_by", ""),
            entry_price=float(row.get("entry_price") or 0.0),
            exit_price=float(row.get("exit_price") or 0.0),
            pnl_pct=float(row.get("pnl_pct") or 0.0),
            pnl_usd=float(row.get("pnl_usd") or 0.0),
            win=bool(row.get("win", 0)),
            hold_seconds=float(row.get("hold_seconds") or 0.0),
            # Group B — Entry Decision Context
            leverage=row.get("leverage"),
            position_size_usd=row.get("position_size_usd"),
            claude_thesis=row.get("claude_thesis"),
            claude_signal=row.get("claude_signal"),
            claude_confidence=row.get("claude_confidence"),
            entry_score=row.get("entry_score"),
            ensemble_votes=row.get("ensemble_votes"),
            entry_regime=row.get("entry_regime"),
            entry_rsi=row.get("entry_rsi"),
            entry_macd_hist=row.get("entry_macd_hist"),
            entry_atr_pct=row.get("entry_atr_pct"),
            # Group C — Market Conditions at Close
            regime=row.get("regime"),
            fear_greed_value=row.get("fear_greed_value"),
            fear_greed_label=row.get("fear_greed_label"),
            # Group D — Technical Indicators at Close
            rsi=row.get("rsi"),
            macd_hist=row.get("macd_hist"),
            macd_signal=row.get("macd_signal"),
            bollinger_pct=row.get("bollinger_pct"),
            ema_20=row.get("ema_20"),
            ema_50=row.get("ema_50"),
            stochastic_k=row.get("stochastic_k"),
            stochastic_d=row.get("stochastic_d"),
            adx=row.get("adx"),
            atr_value=row.get("atr_value"),
            atr_pct=row.get("atr_pct"),
            volume_ratio=row.get("volume_ratio"),
            price_vs_vwap=row.get("price_vs_vwap"),
            # Group E — Mode4 Profit Tracking Data
            m4_peak_pnl_pct=row.get("m4_peak_pnl_pct"),
            m4_ticks_in_profit=row.get("m4_ticks_in_profit"),
            m4_ticks_total=row.get("m4_ticks_total"),
            m4_composite_score=row.get("m4_composite_score"),
            m4_hurst_value=row.get("m4_hurst_value"),
            m4_momentum_decay=row.get("m4_momentum_decay"),
            m4_extension_score=row.get("m4_extension_score"),
            m4_ev_ratio=row.get("m4_ev_ratio"),
            m4_volume_div_score=row.get("m4_volume_div_score"),
            # Group G — Metadata
            trade_id=row.get("trade_id") or "",
            trade_closed_at=row.get("trade_closed_at") or "",
            captured_at=row.get("captured_at") or "",
            # Group F (ds_*) intentionally left at defaults (None) —
            # this is an unanalyzed row and the analyzer fills these in.
        )
