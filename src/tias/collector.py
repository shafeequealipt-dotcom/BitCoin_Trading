"""TIAS Trade Context Collector — gathers all available context at trade close.

Design principles:
- Ephemeral data (ProfitSniper._profit_states) must be captured SYNCHRONOUSLY
  in the callback wrapper before this async coroutine is created, then passed
  in via m4_snapshot. The profit state is cleaned up in ProfitSniper's tick
  cycle, which may run before this coroutine executes.
- Each data group is wrapped in try/except so a single failing source never
  prevents saving a partial record. Partial records are far more useful than
  no record at all.
- All DB queries use parameterized statements to prevent SQL injection.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame
from src.database.connection import DatabaseManager
from src.tias.models import TradeIntelligence
from src.tias.repository import TradeIntelligenceRepo

log = get_logger("tias")


class TradeContextCollector:
    """Collects all available context when a trade closes and persists it.

    Designed to be scheduled as an asyncio task from the TradeCoordinator
    close callback wrapper. Ephemeral profit-state data must be captured
    synchronously and passed in via ``m4_snapshot`` before scheduling.

    Args:
        services: The shared services dict from WorkerManager.
        db: Active DatabaseManager for direct DB queries.
    """

    def __init__(self, services: dict, db: DatabaseManager) -> None:
        self._services = services
        self._db = db

    async def collect_and_save(
        self,
        record: dict,
        repo: TradeIntelligenceRepo,
        m4_snapshot: Optional[dict] = None,
    ) -> "tuple[int, TradeIntelligence | None]":
        """Collect all context for a closed trade and save to DB.

        Args:
            record: Close callback record dict from TradeCoordinator.
            repo: TradeIntelligenceRepo for persistence.
            m4_snapshot: Synchronously-captured ProfitSniper state
                         (peak_pnl_pct, ticks_in_profit, ticks_total).

        Returns:
            (row_id, trade) on success, (0, None) on failure.
            row_id is used by Phase 2 to UPDATE the DeepSeek analysis fields.
        """
        symbol = record.get("symbol", "")

        try:
            group_a = self._extract_group_a(record)
            group_b = await self._collect_group_b(symbol, record)
            group_c = await self._collect_group_c(symbol)
            group_d = await self._collect_group_d(symbol, record)
            group_e = await self._collect_group_e(symbol, m4_snapshot)
            apex_data = self._collect_apex_data(record)

            # Enrich APEX final values from thesis DB and group_b data
            if apex_data.get("apex_optimized"):
                apex_data["apex_final_size"] = group_b.get("position_size_usd")
                try:
                    thesis_row = await self._db.fetch_one(
                        "SELECT stop_loss_price, take_profit_price FROM trade_thesis "
                        # Durable-open: exclude pre-order 'reserving' placeholders
                        # and 'voided' (rejected/raised) reservations — they carry
                        # intent-only data and must not become the entry context.
                        "WHERE symbol = ? AND status NOT IN ('reserving', 'voided') "
                        "ORDER BY opened_at DESC LIMIT 1",
                        (symbol,),
                    )
                    if thesis_row:
                        apex_data["apex_final_sl"] = thesis_row.get("stop_loss_price")
                        apex_data["apex_final_tp"] = thesis_row.get("take_profit_price")
                except Exception:
                    pass

            trade = TradeIntelligence(
                # Group A
                **group_a,
                # Group B
                **group_b,
                # Group C
                **group_c,
                # Group D
                **group_d,
                # Group E
                **group_e,
                # Group APEX — optimization tracking
                **apex_data,
                # Group G — metadata
                trade_id=record.get("trade_id", ""),
                trade_closed_at=record.get("closed_at", ""),
                captured_at=datetime.now(timezone.utc).isoformat(),
            )

            row_id = await repo.save(trade)
            log.info(
                "TIAS_SAVE | id={id} sym={sym} dir={dir} pnl={pnl:+.2f}% "
                "win={win} regime={regime} rsi={rsi} | {ctx}",
                id=row_id,
                sym=symbol,
                dir=record.get("direction", ""),
                pnl=record.get("pnl_pct", 0.0),
                win=record.get("was_win", False),
                regime=group_c.get("regime"),
                rsi=group_d.get("rsi"),
                ctx=ctx(),
            )
            return row_id, trade

        except Exception as e:
            log.error(
                "TIAS_COLLECT_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:200],
                ctx=ctx(),
            )
            return 0, None

    # ------------------------------------------------------------------ #
    #  Group A: Trade Outcome                                              #
    # ------------------------------------------------------------------ #

    def _extract_group_a(self, record: dict) -> dict:
        """Extract trade outcome fields directly from the callback record."""
        # T2-3 (2026-05-12) — exchange_mode threaded from the coordinator's
        # transformer.current_mode read at on_trade_closed. Empty-string
        # fallback when the coordinator was unable to resolve the mode
        # (transformer not yet attached, mode label unknown). Pre-fix this
        # field was never populated and the trade_intelligence column
        # silently defaulted to 'shadow' for every row.
        _exchange_mode = str(record.get("exchange_mode", "") or "")
        if _exchange_mode:
            log.info(
                "TIAS_MODE_RESOLVED | sym={sym} mode={mode} source=transformer | {ctx}",
                sym=record.get("symbol", ""),
                mode=_exchange_mode,
                ctx=ctx(),
            )
        else:
            # Loud signal that the upstream wiring lost the mode label.
            # This should be empty in steady state — the coordinator
            # always reads transformer.current_mode, so an empty value
            # implies transformer.current_mode itself returned "".
            log.warning(
                "TIAS_MODE_RESOLVED | sym={sym} mode='' source=transformer "
                "fallback=record_unset | {ctx}",
                sym=record.get("symbol", ""),
                ctx=ctx(),
            )
        return {
            "symbol": record.get("symbol", ""),
            "direction": record.get("direction", ""),
            "strategy_name": record.get("strategy_name", ""),
            "strategy_category": record.get("strategy_category", ""),
            "source": record.get("source", ""),
            "closed_by": record.get("closed_by", ""),
            "entry_price": float(record.get("entry_price", 0.0) or 0.0),
            "exit_price": float(record.get("close_price", 0.0) or 0.0),  # record uses close_price
            "pnl_pct": float(record.get("pnl_pct", 0.0) or 0.0),
            "pnl_usd": float(record.get("pnl_usd", 0.0) or 0.0),
            "win": bool(record.get("was_win", False)),
            "hold_seconds": float(record.get("hold_seconds", 0.0) or 0.0),
            "exchange_mode": _exchange_mode,
        }

    # ------------------------------------------------------------------ #
    #  Group B: Entry Decision Context                                     #
    # ------------------------------------------------------------------ #

    async def _collect_group_b(self, symbol: str, record: dict) -> dict:
        """Query trade_thesis and strategy_trades for entry context."""
        result: dict[str, Any] = {
            "leverage": None,
            "position_size_usd": None,
            "claude_thesis": None,
            "claude_signal": None,
            "claude_confidence": None,
            "entry_score": None,
            "ensemble_votes": None,
            # Layer 2 Defect 6 — numeric per-trade vote counts from
            # EnsembleStateCache at register_trade time. Populated below
            # via the Phase 3 override block reading the record dict.
            "supporting_count": None,
            "opposing_count": None,
            # Layer 2 Defect 1 — per-cycle-per-symbol setup_id join key.
            "setup_id": None,
            # Entry-time market snapshot (populated from record in Phase 3 override below)
            "entry_regime": None,
            "entry_rsi": None,
            "entry_macd_hist": None,
            "entry_atr_pct": None,
        }

        # Claude's thesis and trade sizing from trade_thesis
        # Layer 2 Defect 5 (2026-05-22) — claude_confidence wired from
        # trade_thesis.entry_xray_confidence (the only numeric confidence
        # value available pre-trade per the forensic finding). Pre-fix the
        # column was 100% NULL because the original collector read
        # thesis_row.consensus (a string label like "STRONG") into the
        # numeric claude_confidence column — invalid. Reading the actual
        # numeric field closes that dead column with real data.
        try:
            thesis_row = await self._db.fetch_one(
                """
                SELECT leverage, size_usd, thesis, consensus, entry_xray_confidence
                FROM trade_thesis
                WHERE symbol = ?
                  -- Durable-open: skip 'reserving'/'voided' placeholder rows
                  -- so intent-only data never becomes the entry context.
                  AND status NOT IN ('reserving', 'voided')
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                (symbol,),
            )
            if thesis_row:
                result["leverage"] = thesis_row.get("leverage")
                result["position_size_usd"] = thesis_row.get("size_usd")
                result["claude_thesis"] = thesis_row.get("thesis")
                result["claude_signal"] = thesis_row.get("consensus")
                _xray_conf = thesis_row.get("entry_xray_confidence")
                if _xray_conf is not None:
                    try:
                        _val = float(_xray_conf)
                        # Reject zeros as "no XRAY anchor" per Rule 5.
                        if _val > 0.0:
                            result["claude_confidence"] = _val
                    except (TypeError, ValueError):
                        pass
        except Exception as e:
            log.warning(
                "TIAS_B_THESIS_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        # Entry score and ensemble votes from strategy_trades
        try:
            trade_id = record.get("trade_id", "")
            if trade_id:
                st_row = await self._db.fetch_one(
                    """
                    SELECT score, ensemble_strength, ensemble_votes_for,
                           ensemble_votes_against
                    FROM strategy_trades
                    WHERE trade_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (trade_id,),
                )
            else:
                st_row = await self._db.fetch_one(
                    """
                    SELECT score, ensemble_strength, ensemble_votes_for,
                           ensemble_votes_against
                    FROM strategy_trades
                    WHERE symbol = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (symbol,),
                )
            if st_row:
                result["entry_score"] = st_row.get("score")
                votes_for = st_row.get("ensemble_votes_for")
                votes_against = st_row.get("ensemble_votes_against")
                strength = st_row.get("ensemble_strength") or ""
                if votes_for is not None and votes_against is not None:
                    total = int(votes_for + votes_against)
                    result["ensemble_votes"] = f"{int(votes_for)}/{total} ({strength})"
                elif strength:
                    result["ensemble_votes"] = strength
        except Exception as e:
            log.warning(
                "TIAS_B_STRAT_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        # Phase 3: Override with entry-time data forwarded from TradeState via record.
        # TradeCoordinator stores these at register_trade() time (before close), so this
        # data reflects the actual conditions when the trade was placed.
        if record.get("claude_directive"):
            result["claude_thesis"] = record["claude_directive"]
        if record.get("claude_plan_view"):
            result["claude_signal"] = record["claude_plan_view"]
        if record.get("signal_score") is not None:
            result["entry_score"] = record["signal_score"]
        # Layer 2 Defect 4 (2026-05-22) — entry_score fallback to apex_confidence.
        # Pre-fix entry_score was 100% NULL across 2,345 trade_intelligence rows
        # because the upstream signal_score path is broken (strategy_trades.score
        # is hardcoded 100 at strategy_worker.py:2904; trade.get("score") is never
        # set). apex_confidence is already computed by the APEX optimizer at
        # decision time (0-1 scale), plumbed through TradeCoordinator.TradeState
        # since the original APEX feedback-loop work, and forwarded on the
        # close-record dict — so a real entry-quality score is available
        # without any new compute. Apply only as a FALLBACK so any legitimate
        # signal_score that gets plumbed later still wins (no behavior
        # regression for callers that already populate it).
        if result["entry_score"] is None:
            _apex_conf = record.get("apex_confidence")
            if _apex_conf is not None:
                try:
                    _val = float(_apex_conf)
                    # Reject suspicious zeros — apex_confidence=0 indicates
                    # APEX never ran for this trade. NULL is the honest
                    # value in that case (Rule 5).
                    if _val > 0.0:
                        result["entry_score"] = _val
                except (TypeError, ValueError):
                    pass
        if record.get("ensemble_score"):
            result["ensemble_votes"] = record["ensemble_score"]
        # Layer 2 Defect 6 — supporting/opposing vote counts forwarded from
        # TradeCoordinator.on_trade_closed record dict. Sourced upstream by
        # strategy_worker from EnsembleStateCache.get_current_consensus at
        # register_trade time. None when no cache record existed (rare;
        # logged at register site).
        if record.get("supporting_count") is not None:
            result["supporting_count"] = int(record["supporting_count"])
        if record.get("opposing_count") is not None:
            result["opposing_count"] = int(record["opposing_count"])
        # Layer 2 Defect 1 — setup_id forwarded from TradeCoordinator record
        # dict (sourced upstream by strategy_worker from EnsembleStateCache
        # at register_trade time). Empty string means no cache record existed
        # (trade opened via a path that bypassed the ensemble); persist as
        # NULL in that case per Rule 5 (honest absence beats a fake join key).
        _sid = record.get("setup_id")
        if _sid:
            result["setup_id"] = str(_sid)
        # Entry-time market snapshot (captured by strategy_worker before order placement)
        if record.get("entry_regime"):
            result["entry_regime"] = record["entry_regime"]
        if record.get("entry_rsi") is not None:
            result["entry_rsi"] = record["entry_rsi"]
        if record.get("entry_macd_hist") is not None:
            result["entry_macd_hist"] = record["entry_macd_hist"]
        if record.get("entry_atr_pct") is not None:
            result["entry_atr_pct"] = record["entry_atr_pct"]

        return result

    # ------------------------------------------------------------------ #
    #  Group C: Market Conditions                                          #
    # ------------------------------------------------------------------ #

    async def _collect_group_c(self, symbol: str) -> dict:
        """Collect regime state and fear/greed index."""
        result: dict[str, Any] = {
            # Per-coin-authority Phase 2 follow-up (2026-05-29): default to the
            # explicit honest "unknown" (not None) so every non-success path
            # (missing detector, raised collection) yields the same explicit
            # value as the cache-miss branch below — mirrors apex/assembler.py.
            "regime": "unknown",
            "fear_greed_value": None,
            "fear_greed_label": None,
            "regime_verified": 0,
        }

        # Per-coin regime ONLY (per-coin-authority: NO fall back to the global
        # regime — a cache miss yields the explicit "unknown" default above).
        # get_coin_regime() returns a RegimeState dataclass, NOT a MarketRegime enum.
        # RegimeState.regime is the inner MarketRegime enum whose .value is the string.
        try:
            regime_detector = self._services.get("regime_detector")
            if regime_detector:
                coin_regime = regime_detector.get_coin_regime(symbol)
                # Definitive-fix Phase 7 (2026-04-28) — REGIME_CACHE_QUERY
                # telemetry mirrors apex/gate.py and apex/assembler.py.
                _hit = coin_regime is not None
                _cache_size = (
                    len(getattr(regime_detector, "_per_coin_regimes", {}) or {})
                )
                _ready = bool(getattr(regime_detector, "is_ready", lambda: True)())
                log.info(
                    f"REGIME_CACHE_QUERY | sym={symbol} reader=tias_collector "
                    f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
                )
                if coin_regime is not None:
                    # RegimeState dataclass: .regime is the MarketRegime enum
                    result["regime"] = str(coin_regime.regime.value)
                    result["regime_verified"] = 1  # Confirmed per-coin regime
                else:
                    # Per-coin-authority Phase 2 (2026-05-29): per-coin regime
                    # unavailable -> UNKNOWN, NEVER the global BTC regime. Record
                    # it honestly and mark regime_verified=0 so the learning loop
                    # does not treat a cold-stamp as a confirmed per-coin regime.
                    result["regime"] = "unknown"  # MarketRegime.UNKNOWN.value
                    result["regime_verified"] = 0
                    log.warning(
                        "REGIME_FALLBACK | sym={sym} source=tias | "
                        "per-coin unavailable, using UNKNOWN (not global) | {ctx}",
                        sym=symbol, ctx=ctx(),
                    )
        except Exception as e:
            log.warning(
                "TIAS_C_REGIME_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        # Fear & Greed index
        try:
            fg_row = await self._db.fetch_one(
                "SELECT value, classification FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1"
            )
            if fg_row:
                result["fear_greed_value"] = fg_row.get("value")
                result["fear_greed_label"] = fg_row.get("classification")
        except Exception as e:
            log.warning(
                "TIAS_C_FG_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        return result

    # ------------------------------------------------------------------ #
    #  Group D: Technical Indicators                                       #
    # ------------------------------------------------------------------ #

    async def _collect_group_d(self, symbol: str, record: dict) -> dict:
        """Call TACache.analyze() and extract the required indicator fields.

        Field paths (verified against src/analysis/engine.py output):
          rsi            → result['momentum']['rsi_14']
          macd_hist      → result['trend']['macd']['histogram']
          macd_signal    → result['trend']['macd']['signal_line']
          bollinger_pct  → computed: (close - bb_lower) / (bb_upper - bb_lower) * 100
          ema_20         → result['_raw']['ema_20'][-1]  (numpy array)
          ema_50         → result['trend']['sma_50']     (already float via _last_valid)
          stochastic_k   → result['momentum']['stochastic']['k']
          stochastic_d   → result['momentum']['stochastic']['d']
          adx            → result['trend']['adx']['adx']
          atr_value      → result['volatility']['atr_14']
          atr_pct        → result['volatility']['natr_14']
          volume_ratio   → result['volume']['volume_sma_ratio']
          price_vs_vwap  → computed: (close - vwap) / vwap * 100
        """
        result: dict[str, Any] = {
            "rsi": None,
            "macd_hist": None,
            "macd_signal": None,
            "bollinger_pct": None,
            "ema_20": None,
            "ema_50": None,
            "stochastic_k": None,
            "stochastic_d": None,
            "adx": None,
            "atr_value": None,
            "atr_pct": None,
            "volume_ratio": None,
            "price_vs_vwap": None,
        }

        ta_cache = self._services.get("ta_cache") or self._services.get("ta")
        if not ta_cache:
            return result

        try:
            ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)
            if not ta:
                return result

            momentum = ta.get("momentum") or {}
            trend = ta.get("trend") or {}
            vol = ta.get("volatility") or {}
            volume = ta.get("volume") or {}
            raw = ta.get("_raw") or {}

            result["rsi"] = momentum.get("rsi_14")

            macd = trend.get("macd") or {}
            result["macd_hist"] = macd.get("histogram")
            result["macd_signal"] = macd.get("signal_line")

            stoch = momentum.get("stochastic") or {}
            result["stochastic_k"] = stoch.get("k")
            result["stochastic_d"] = stoch.get("d")

            result["adx"] = (trend.get("adx") or {}).get("adx")
            result["atr_value"] = vol.get("atr_14")
            result["atr_pct"] = vol.get("natr_14")
            result["volume_ratio"] = volume.get("volume_sma_ratio")

            # sma_50 from processed trend dict (already a float via _last_valid)
            result["ema_50"] = trend.get("sma_50")

            # ema_20 from _raw numpy array — must use _last_valid_arr helper
            ema_20_arr = raw.get("ema_20")
            if ema_20_arr is not None:
                result["ema_20"] = _last_valid_arr(ema_20_arr)

            # Bollinger %B: position within band (0 = at lower, 100 = at upper)
            bb = vol.get("bollinger") or {}
            bb_upper = bb.get("upper")
            bb_lower = bb.get("lower")
            close_price = float(record.get("close_price", 0.0) or 0.0)
            if bb_upper and bb_lower and (bb_upper - bb_lower) > 0 and close_price:
                result["bollinger_pct"] = round(
                    (close_price - bb_lower) / (bb_upper - bb_lower) * 100, 4
                )

            # VWAP-relative price position (positive = price above VWAP)
            vwap = volume.get("vwap")
            if vwap and vwap > 0 and close_price:
                result["price_vs_vwap"] = round(
                    (close_price - vwap) / vwap * 100, 4
                )

        except Exception as e:
            log.warning(
                "TIAS_D_TA_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        return result

    # ------------------------------------------------------------------ #
    #  Group E: Mode4 Data                                                 #
    # ------------------------------------------------------------------ #

    async def _collect_group_e(self, symbol: str, m4_snapshot: Optional[dict]) -> dict:
        """Combine synchronous profit-state snapshot with sniper_log DB query."""
        result: dict[str, Any] = {
            "m4_peak_pnl_pct": None,
            "m4_ticks_in_profit": None,
            "m4_ticks_total": None,
            "m4_composite_score": None,
            "m4_hurst_value": None,
            "m4_momentum_decay": None,
            "m4_extension_score": None,
            "m4_ev_ratio": None,
            "m4_volume_div_score": None,
        }

        # Ephemeral data from the synchronously-captured snapshot
        if m4_snapshot:
            result["m4_peak_pnl_pct"] = m4_snapshot.get("peak_pnl_pct")
            result["m4_ticks_in_profit"] = m4_snapshot.get("ticks_in_profit")
            result["m4_ticks_total"] = m4_snapshot.get("ticks_total")

        # Computed Mode4 scores from the most recent sniper_log entry
        try:
            sl_row = await self._db.fetch_one(
                """
                SELECT composite_score, hurst_value, momentum_decay_score,
                       extension_score, ev_ratio, volume_div_score, peak_pnl_pct
                FROM sniper_log
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            )
            if sl_row:
                result["m4_composite_score"] = sl_row.get("composite_score")
                result["m4_hurst_value"] = sl_row.get("hurst_value")
                result["m4_momentum_decay"] = sl_row.get("momentum_decay_score")
                result["m4_extension_score"] = sl_row.get("extension_score")
                result["m4_ev_ratio"] = sl_row.get("ev_ratio")
                result["m4_volume_div_score"] = sl_row.get("volume_div_score")
                # Only use DB peak if we couldn't capture it synchronously
                if result["m4_peak_pnl_pct"] is None:
                    result["m4_peak_pnl_pct"] = sl_row.get("peak_pnl_pct")
        except Exception as e:
            log.warning(
                "TIAS_E_SNIPER_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

        return result

    # ------------------------------------------------------------------ #
    #  APEX: Optimization tracking                                        #
    # ------------------------------------------------------------------ #

    def _collect_apex_data(self, record: dict) -> dict:
        """Extract APEX optimization data from the close callback record.

        These fields were attached to the directive by layer_manager, stored in
        TradeState by strategy_worker, and forwarded in the close record by
        TradeCoordinator.on_trade_closed(). They complete the feedback loop:
        DeepSeek sees what APEX changed and can evaluate whether it helped.
        """
        result: dict[str, Any] = {
            "apex_optimized": False,
            "apex_flipped": False,
            "apex_original_direction": None,
            "apex_final_direction": None,
            "apex_original_sl": None,
            "apex_final_sl": None,
            "apex_original_tp": None,
            "apex_final_tp": None,
            "apex_original_size": None,
            "apex_final_size": None,
            "apex_confidence": None,
            "apex_tp_mode": None,
            "apex_reasoning": None,
            "apex_model": None,
            "apex_response_ms": None,
            "apex_cost_usd": None,
            "gate_adjustments": None,
        }

        try:
            if record.get("apex_optimized"):
                result["apex_optimized"] = True
                result["apex_flipped"] = bool(record.get("apex_was_flipped", False))
                result["apex_original_direction"] = record.get("apex_original_direction")
                result["apex_final_direction"] = record.get("direction")
                result["apex_original_sl"] = record.get("apex_original_sl")
                result["apex_original_tp"] = record.get("apex_original_tp")
                result["apex_original_size"] = record.get("apex_original_size")
                result["apex_confidence"] = record.get("apex_confidence")
                result["apex_tp_mode"] = record.get("apex_tp_mode")
                result["apex_reasoning"] = record.get("apex_reasoning")
                result["apex_model"] = record.get("apex_model")
                result["apex_response_ms"] = record.get("apex_response_ms")
                result["apex_cost_usd"] = record.get("apex_cost_usd")
                result["gate_adjustments"] = record.get("gate_adjustments")

                # Compute TP fill rate: actual capture / intended capture
                _entry_p = float(record.get("entry_price", 0) or 0)
                # Record may use close_price or exit_price depending on source
                _exit_p = float(
                    record.get("close_price", 0)
                    or record.get("exit_price", 0)
                    or 0
                )
                _orig_tp = float(record.get("apex_original_tp", 0) or 0)
                if _entry_p > 0 and _exit_p > 0 and _orig_tp > 0:
                    _intended = abs(_orig_tp - _entry_p)
                    _actual = abs(_exit_p - _entry_p)
                    if _intended > 0:
                        result["apex_tp_fill_rate"] = round(
                            min(_actual / _intended, 2.0) * 100, 1
                        )
        except Exception as e:
            log.debug(
                "TIAS_COLLECT_APEX | sym={sym} err='{err}' | {ctx}",
                sym=record.get("symbol", ""),
                err=str(e)[:60],
                ctx=ctx(),
            )

        return result


# ------------------------------------------------------------------ #
#  Utility                                                             #
# ------------------------------------------------------------------ #

def _last_valid_arr(arr: Any) -> Optional[float]:
    """Return the last non-NaN, non-inf value from a numpy array (or list).

    Used for extracting ema_20 from the TAEngine _raw numpy arrays.
    Returns None if the array is empty, all NaN, or any error occurs.
    """
    try:
        for i in range(len(arr) - 1, -1, -1):
            v = float(arr[i])
            if not math.isnan(v) and not math.isinf(v):
                return round(v, 6)
    except Exception:
        pass
    return None
