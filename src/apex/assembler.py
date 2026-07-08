"""APEX IntelligenceAssembler — gathers the 4-section data package for one coin.

Assembles an IntelligencePackage by querying existing services and the TIAS
repository. Each data source is independently try/excepted: a partial package
is always returned — DeepSeek is designed to work with whatever is available.

Service access patterns mirror src/tias/collector.py (TIAS TradeContextCollector).
The key difference: the collector captures data at trade CLOSE; the assembler
captures data BEFORE trade OPEN for optimization.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from src.apex.models import (
    CoinData,
    DirectiveContext,
    IntelligencePackage,
    StructuralData,
    SymbolFlipEvidence,
    TIASSituationData,
    TIASSymbolHistory,
)
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame

log = get_logger("apex")


class IntelligenceAssembler:
    """Builds the complete 4-section IntelligencePackage for APEX optimization.

    Each of the 4 sections is gathered from a different data source:
      Section 1 — Claude's directive (passed in from the call site)
      Section 2 — Current coin state: TA indicators, Mode4, orderbook
      Section 3 — TIAS symbol history: all past trades + DeepSeek analyses
      Section 4 — TIAS situation data: performance in similar conditions

    Failures in any single source are caught and logged; the section is
    populated with safe defaults. DeepSeek always receives SOMETHING.

    Args:
        services: The shared services dict from WorkerManager._services.
        tias_repo: TradeIntelligenceRepo instance for TIAS DB queries.
        db: DatabaseManager for direct DB queries (Fear & Greed index).
    """

    def __init__(self, services: dict, tias_repo: Any, db: Any = None) -> None:
        self._services = services
        self._tias_repo = tias_repo
        self._db = db

    async def assemble(self, directive: dict) -> IntelligencePackage:
        """Build the complete 5-section intelligence package for one coin.

        Args:
            directive: Claude's trade directive dict. Expected keys:
                symbol, direction, sl, tp, leverage, size_usd,
                reasoning, plan_view, signal_score, strategy_name.

        Returns:
            IntelligencePackage with all 5 sections (partially populated
            on data source failures — never raises).
        """
        symbol = directive.get("symbol", "")

        # ======= SECTION 1: Claude's directive ==============================
        section1 = self._build_directive_context(directive)

        # ======= SECTION 2: Current coin state ==============================
        section2 = await self._gather_coin_data(symbol)

        # Get market conditions first (needed for regime-filtered history)
        regime_str, fg_value = await self._get_market_conditions(symbol)

        # ======= SECTION 3: TIAS symbol history (regime-filtered) ==========
        section3 = await self._gather_symbol_history(symbol, regime=regime_str)

        # ======= SECTION 4: TIAS situation data =============================
        section4 = await self._gather_situation_data(regime_str, fg_value)

        # ======= SECTION 5: X-RAY structural intelligence ===================
        section5 = self._gather_structural_data(symbol)

        # ======= E26: per-coin, per-venue directional evidence ==============
        # Isolated by the LIVE exchange_mode so the flip gate (optimizer) and
        # the APEX prompt use venue-consistent history instead of pooling
        # demo/live/paper trades. Fail-permissive: an unknown live mode yields
        # exchange_mode="" (pooled), which the gate treats as non-authoritative
        # and falls back to the regime-filtered trades list.
        flip_evidence = await self._gather_flip_evidence(symbol, regime_str)

        # Phase 12.3 (lifecycle-logging-audit Gap 3.2-G1): per-coin
        # APEX_ASSEMBLE_DONE rollup. Pre-fix, all 7 sub-populator success
        # paths logged at DEBUG (invisible). A silent partial assembly
        # (e.g. M4 row missing, X-RAY cache empty) yielded APEX_OK with
        # no signal that the optimizer ran on degraded context. This
        # rollup surfaces which sub-fields were populated per coin.
        _populated = []
        if getattr(section2, "rsi", None) is not None:
            _populated.append("ta")
        if getattr(section2, "m4_composite", None) is not None:
            _populated.append("m4")
        if getattr(section2, "book_imbalance_pct", None) is not None:
            _populated.append("ob")
        if getattr(section2, "volatility_class", None):
            _populated.append("vol")
        if section5 is not None:
            _populated.append("xray")
        if section3 is not None and getattr(section3, "trades", None):
            _populated.append("tias_sym")
        if section4 is not None and getattr(section4, "regime_count", 0) > 0:
            _populated.append("tias_sit")
        log.info(
            "APEX_ASSEMBLE_DONE | sym={sym} populated=[{p}] count={n}/7 | {c}",
            sym=symbol, p=",".join(_populated), n=len(_populated), c=ctx(),
        )

        return IntelligencePackage(
            directive=section1,
            coin_data=section2,
            symbol_history=section3,
            situation_data=section4,
            structural_data=section5,
            flip_evidence=flip_evidence,
        )

    # =========================================================================
    # Section 1: Directive
    # =========================================================================

    def _build_directive_context(self, directive: dict) -> DirectiveContext:
        """Map the Claude directive dict to a DirectiveContext dataclass."""
        return DirectiveContext(
            symbol=directive.get("symbol", ""),
            direction=directive.get("direction", ""),
            sl=float(directive.get("stop_loss_price") or directive.get("sl") or 0.0),
            tp=float(directive.get("take_profit_price") or directive.get("tp") or 0.0),
            leverage=float(directive.get("leverage") or 3),
            size_usd=float(directive.get("size_usd") or 600),
            reasoning=directive.get("reasoning", ""),
            plan_view=directive.get("plan_view") or directive.get("market_view") or "",
            signal_score=directive.get("signal_score") or directive.get("score"),
            strategy_name=directive.get("strategy_name"),
        )

    # =========================================================================
    # Section 2: Coin data (TA + Mode4 + orderbook)
    # =========================================================================

    async def _gather_coin_data(self, symbol: str) -> CoinData:
        """Gather current real-time state for the symbol.

        Three sub-sections, each independently try/excepted:
          A. TA indicators from TACache
          B. Mode4 metrics from sniper_log DB table
          C. Orderbook snapshot from MarketService
        """
        data = CoinData(symbol=symbol, current_price=0.0)

        # --- A: Technical Analysis indicators --------------------------------
        await self._populate_ta(data, symbol)

        # Price source tracking (Phase 6): APEX_PRICE_SOURCE is emitted once
        # per optimization with ta | ws | ticker so ops can see at a glance
        # which coins consistently miss the hot path.
        price_source = "ta" if data.current_price > 0 else "none"

        # --- B: WS quote cache (Phase 6) — check before REST -----------------
        # PriceWorker maintains a {sym: (price, ts)} dict updated on every
        # WebSocket tick. A 5 s freshness bound is enough for APEX (which
        # runs once per Claude cycle). Missing or stale → fall through to
        # REST. Never fatal.
        if data.current_price <= 0:
            try:
                price_worker = (
                    self._services.get("price_worker")
                    if isinstance(self._services, dict) else None
                )
                if price_worker and hasattr(price_worker, "get_ws_quote"):
                    q = price_worker.get_ws_quote(symbol, max_age_s=5.0)
                    if q and q > 0:
                        data.current_price = q
                        price_source = "ws"
            except Exception as e:
                log.debug(
                    "APEX_WS_QUOTE_FAIL | sym={sym} err='{err}'",
                    sym=symbol, err=str(e)[:80],
                )

        # --- C: Final fallback to MarketService REST ticker -----------------
        if data.current_price <= 0:
            try:
                market_svc = (
                    self._services.get("market_service")
                    or self._services.get("market")
                )
                if market_svc:
                    ticker = await market_svc.get_ticker(symbol)
                    if ticker and ticker.last_price > 0:
                        data.current_price = ticker.last_price
                        price_source = "ticker"
                        log.warning(
                            "APEX_PRICE_FALLBACK | sym={sym} source=ticker price={p} | {ctx}",
                            sym=symbol, p=data.current_price, ctx=ctx(),
                        )
            except Exception as e:
                log.warning(
                    "APEX_PRICE_FALLBACK_FAIL | sym={sym} err='{err}' | {ctx}",
                    sym=symbol, err=str(e)[:100], ctx=ctx(),
                )
        if data.current_price <= 0:
            log.error(
                "APEX_NO_PRICE | sym={sym} | Cannot optimize without price | {ctx}",
                sym=symbol, ctx=ctx(),
            )

        # One line per APEX assembly — grep histogram reveals the hot path.
        log.info(
            f"APEX_PRICE_SOURCE | sym={symbol} source={price_source} "
            f"price={data.current_price} | {ctx()}"
        )

        # --- B: Mode4 profit-sniper state ------------------------------------
        await self._populate_mode4(data, symbol)

        # --- C: Orderbook snapshot -------------------------------------------
        await self._populate_orderbook(data, symbol)

        # --- D: Volatility profile (per-coin adaptive parameters) -----------
        await self._populate_volatility_profile(data, symbol)

        return data

    async def _populate_ta(self, data: CoinData, symbol: str) -> None:
        """Populate TA indicators on data in-place. Tag: APEX_ASSEMBLE_TA."""
        ta_cache = self._services.get("ta_cache") or self._services.get("ta")
        if not ta_cache:
            return
        try:
            ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)
            if not ta:
                return

            momentum = ta.get("momentum") or {}
            trend = ta.get("trend") or {}
            vol = ta.get("volatility") or {}
            volume = ta.get("volume") or {}
            raw = ta.get("_raw") or {}

            # Current price from close array
            close_arr = raw.get("close")
            if close_arr is not None:
                price = _last_valid_arr(close_arr)
                if price:
                    data.current_price = price

            data.rsi = momentum.get("rsi_14")

            macd = trend.get("macd") or {}
            data.macd_hist = macd.get("histogram")
            data.macd_signal = macd.get("signal_line")
            data.macd_line = macd.get("macd_line")

            stoch = momentum.get("stochastic") or {}
            data.stochastic_k = stoch.get("k")
            data.stochastic_d = stoch.get("d")

            data.adx = (trend.get("adx") or {}).get("adx")
            data.atr = vol.get("atr_14")
            data.atr_pct = vol.get("natr_14")
            data.volume_ratio = volume.get("volume_sma_ratio")
            data.ema_50 = trend.get("sma_50")

            ema_20_arr = raw.get("ema_20")
            if ema_20_arr is not None:
                data.ema_20 = _last_valid_arr(ema_20_arr)

            if data.ema_20 and data.ema_50:
                data.ema_trend = "bullish" if data.ema_20 > data.ema_50 else "bearish"

            # Bollinger %B: position within band (0=at lower, 100=at upper)
            bb = vol.get("bollinger") or {}
            bb_upper = bb.get("upper")
            bb_lower = bb.get("lower")
            if bb_upper and bb_lower and (bb_upper - bb_lower) > 0 and data.current_price:
                data.bollinger_pct = round(
                    (data.current_price - bb_lower) / (bb_upper - bb_lower) * 100, 4
                )

            # ATR as % of price
            if data.atr and data.current_price and data.current_price > 0:
                data.atr_pct = round((data.atr / data.current_price) * 100, 4)

            log.debug(
                "APEX_ASSEMBLE_TA | sym={sym} price={price} rsi={rsi} adx={adx} | {ctx}",
                sym=symbol,
                price=data.current_price,
                rsi=data.rsi,
                adx=data.adx,
                ctx=ctx(),
            )

        except Exception as e:
            log.warning(
                "APEX_ASSEMBLE_TA | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:500],
                ctx=ctx(),
            )

    async def _populate_mode4(self, data: CoinData, symbol: str) -> None:
        """Populate Mode4 metrics from sniper_log DB table. Tag: APEX_ASSEMBLE_M4.

        Uses the most recent sniper_log entry for the symbol. At the time of
        APEX assembly (before trade open), there may or may not be an active
        position. If no sniper_log data exists, fields remain None.
        """
        if not self._db:
            return
        try:
            sl_row = await self._db.fetch_one(
                """
                SELECT composite_score, hurst_value, momentum_decay_score,
                       extension_score, ev_ratio, volume_div_score
                FROM sniper_log
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol,),
            )
            if sl_row:
                data.m4_composite = sl_row.get("composite_score")
                data.m4_hurst = sl_row.get("hurst_value")
                data.m4_momentum = sl_row.get("momentum_decay_score")
                data.m4_extension = sl_row.get("extension_score")
                data.m4_ev = sl_row.get("ev_ratio")
                data.m4_volume_div = sl_row.get("volume_div_score")

                log.debug(
                    "APEX_ASSEMBLE_M4 | sym={sym} composite={c} hurst={h} | {ctx}",
                    sym=symbol,
                    c=data.m4_composite,
                    h=data.m4_hurst,
                    ctx=ctx(),
                )

        except Exception as e:
            log.warning(
                "APEX_ASSEMBLE_M4 | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:500],
                ctx=ctx(),
            )

    async def _populate_orderbook(self, data: CoinData, symbol: str) -> None:
        """Populate orderbook imbalance from MarketService. Tag: APEX_ASSEMBLE_OB.

        Orderbook is optional — logged at DEBUG level (not WARNING) if unavailable.
        Computes: bid_depth (sum top-5 bid qty), ask_depth (sum top-5 ask qty),
        and book_imbalance_pct = (bid-ask)/(bid+ask)*100.

        Phase 12.3 (lifecycle-logging-audit Gap 3.2-G2): DEBUG severity for
        the failure path is INTENTIONAL design — orderbook is an optional
        signal (not all symbols have liquid orderbooks; some adapters don't
        expose get_orderbook). The aggregate APEX_ASSEMBLE_DONE rollup
        surfaces ob=N when this fails, so per-call DEBUG silence is OK.
        """
        try:
            market_svc = (
                self._services.get("market_service")
                or self._services.get("market")
            )
            if not market_svc or not hasattr(market_svc, "get_orderbook"):
                return
            book = await market_svc.get_orderbook(symbol, depth=10)
            if not book:
                return

            bids = sum(float(b[1]) for b in (book.get("bids") or [])[:5])
            asks = sum(float(a[1]) for a in (book.get("asks") or [])[:5])
            data.bid_depth = bids
            data.ask_depth = asks
            total = bids + asks
            if total > 0:
                data.book_imbalance_pct = round(((bids - asks) / total) * 100, 2)

            log.debug(
                "APEX_ASSEMBLE_OB | sym={sym} imbalance={i:+.1f}% | {ctx}",
                sym=symbol,
                i=data.book_imbalance_pct or 0,
                ctx=ctx(),
            )

        except Exception as e:
            log.debug(
                "APEX_ASSEMBLE_OB | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:100],
                ctx=ctx(),
            )

    async def _populate_volatility_profile(self, data: CoinData, symbol: str) -> None:
        """Populate per-coin volatility profile on data in-place. Tag: APEX_ASSEMBLE_VOL."""
        try:
            profiler = self._services.get("volatility_profiler")
            if not profiler:
                return
            profile = await profiler.get_profile(symbol)
            if not profile:
                return
            data.volatility_class = profile.volatility_class
            data.recommended_tp_pct = profile.recommended_tp_pct
            data.recommended_sl_pct = profile.recommended_sl_pct
            data.recommended_hold_min = profile.recommended_hold_min
            data.recommended_strategy = profile.recommended_strategy
            log.debug(
                "APEX_ASSEMBLE_VOL | sym={sym} class={cls} tp={tp:.1f}% sl={sl:.1f}% | {ctx}",
                sym=symbol, cls=profile.volatility_class,
                tp=profile.recommended_tp_pct, sl=profile.recommended_sl_pct, ctx=ctx(),
            )
        except Exception as e:
            log.debug(
                "APEX_ASSEMBLE_VOL | sym={sym} err='{err}' | {ctx}",
                sym=symbol, err=str(e)[:100], ctx=ctx(),
            )

    # =========================================================================
    # Section 3: TIAS symbol history
    # =========================================================================

    async def _gather_symbol_history(self, symbol: str, regime: str = "") -> TIASSymbolHistory:
        """Gather TIAS historical performance for this symbol, filtered by current regime.

        Falls back to all-regime data when zero regime-specific trades exist.
        Tag: APEX_ASSEMBLE_TIAS_SYM.
        """
        try:
            _regime_filter = regime if regime and regime != "unknown" else None
            data = await self._tias_repo.get_symbol_full_history(
                symbol, limit=15, regime=_regime_filter,
            )

            # Fallback: if regime-filtered data is too sparse, use all-regime data
            _all_regime_fallback = False
            if data.get("total", 0) < 3 and _regime_filter:
                data = await self._tias_repo.get_symbol_full_history(symbol, limit=15)
                _all_regime_fallback = True

            # Build a human-readable pattern summary from the trades
            pattern_lines: list[str] = []
            if _all_regime_fallback:
                pattern_lines.append(
                    f"WARNING: No {symbol} trades found in {regime} regime. "
                    f"All-regime data shown below as fallback. "
                    f"Respect the regime direction bias from Section 4 situation "
                    f"data — do NOT flip against the regime direction."
                )
            elif _regime_filter:
                pattern_lines.append(f"(regime-filtered: {regime})")
            total = data.get("total", 0)
            if total > 0:
                pattern_lines.append(
                    f"{symbol}: {total} trades, "
                    f"{data['wins']}W/{data['losses']}L "
                    f"({data['win_rate']:.0f}% WR), "
                    f"EV={data['ev_per_trade']:+.2f} USD/trade"
                )
                # Category breakdown
                categories: dict[str, int] = {}
                for t in data.get("trades", []):
                    cat = t.get("ds_category") or "unknown"
                    categories[cat] = categories.get(cat, 0) + 1
                if categories:
                    cat_str = ", ".join(
                        f"{c}={n}"
                        for c, n in sorted(categories.items(), key=lambda x: -x[1])
                        if c != "unknown"
                    )
                    if cat_str:
                        pattern_lines.append(f"DeepSeek categories: {cat_str}")

                # Direction-specific breakdown for DeepSeek
                _dir_buy = [t for t in data.get("trades", []) if t.get("direction") == "Buy"]
                _dir_sell = [t for t in data.get("trades", []) if t.get("direction") == "Sell"]
                _bw = sum(1 for t in _dir_buy if t.get("win"))
                _sw = sum(1 for t in _dir_sell if t.get("win"))
                if _dir_buy or _dir_sell:
                    _bwr = (_bw / len(_dir_buy) * 100) if _dir_buy else 0.0
                    _swr = (_sw / len(_dir_sell) * 100) if _dir_sell else 0.0
                    pattern_lines.append(
                        f"Direction: Buy {len(_dir_buy)}t/{_bw}w ({_bwr:.0f}% WR), "
                        f"Sell {len(_dir_sell)}t/{_sw}w ({_swr:.0f}% WR)"
                    )

            # Compute profit-factor metrics from individual trades
            _trades = data.get("trades", [])
            _wins = data.get("wins", 0)
            _losses = data.get("losses", 0)
            _total_won_usd = sum(
                float(t.get("pnl_usd", 0) or 0)
                for t in _trades
                if float(t.get("pnl_usd", 0) or 0) > 0
            )
            _total_lost_usd = abs(sum(
                float(t.get("pnl_usd", 0) or 0)
                for t in _trades
                if float(t.get("pnl_usd", 0) or 0) < 0
            ))
            _profit_factor = _total_won_usd / max(_total_lost_usd, 0.01)
            _avg_win_usd = _total_won_usd / max(_wins, 1)
            _avg_loss_usd = _total_lost_usd / max(_losses, 1)

            return TIASSymbolHistory(
                symbol=symbol,
                total_trades=data.get("total", 0),
                wins=_wins,
                losses=_losses,
                win_rate=data.get("win_rate", 0.0),
                avg_win_pct=data.get("avg_win_pct", 0.0),
                avg_loss_pct=data.get("avg_loss_pct", 0.0),
                total_pnl_usd=data.get("total_pnl_usd", 0.0),
                ev_per_trade=data.get("ev_per_trade", 0.0),
                profit_factor=round(_profit_factor, 2),
                avg_win_usd=round(_avg_win_usd, 2),
                avg_loss_usd=round(_avg_loss_usd, 2),
                trades=_trades,
                pattern_summary="\n".join(pattern_lines),
                regime=_regime_filter or "",
            )

        except Exception as e:
            log.warning(
                "APEX_ASSEMBLE_TIAS_SYM | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:500],
                ctx=ctx(),
            )
            return TIASSymbolHistory(
                symbol=symbol,
                total_trades=0, wins=0, losses=0,
                win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
                total_pnl_usd=0.0, ev_per_trade=0.0, trades=[],
                pattern_summary="No TIAS history available",
                regime="",
            )

    # =========================================================================
    # Section 4: TIAS situation data
    # =========================================================================

    async def _gather_situation_data(
        self, regime: str, fear_greed: int
    ) -> TIASSituationData:
        """Gather cross-coin performance for similar conditions. Tag: APEX_ASSEMBLE_TIAS_SIT."""
        try:
            data = await self._tias_repo.get_situation_stats(regime, fear_greed)

            summary_lines = [
                f"Regime: {regime} | F&G: {fear_greed}",
                f"Trades in similar conditions: {data['total']}",
            ]
            if data["total"] > 0:
                summary_lines.append(
                    f"Buy WR: {data['buy_win_rate']:.1f}% (avg {data['avg_buy_pnl']:+.2f}%)"
                )
                summary_lines.append(
                    f"Sell WR: {data['sell_win_rate']:.1f}% (avg {data['avg_sell_pnl']:+.2f}%)"
                )
                summary_lines.append(f"Bias: {data['direction_bias']}")
                if data["common_categories"]:
                    summary_lines.append(
                        f"Common failures: {', '.join(data['common_categories'][:3])}"
                    )

            return TIASSituationData(
                regime=regime,
                fear_greed=fear_greed,
                total_trades_in_condition=data["total"],
                buy_win_rate=data["buy_win_rate"],
                sell_win_rate=data["sell_win_rate"],
                avg_buy_pnl=data["avg_buy_pnl"],
                avg_sell_pnl=data["avg_sell_pnl"],
                direction_bias=data["direction_bias"],
                tp_performance=[],          # populated in later phases
                common_categories=data["common_categories"],
                condition_summary="\n".join(summary_lines),
            )

        except Exception as e:
            log.warning(
                "APEX_ASSEMBLE_TIAS_SIT | regime={r} fg={fg} err='{err}' | {ctx}",
                r=regime,
                fg=fear_greed,
                err=str(e)[:500],
                ctx=ctx(),
            )
            return TIASSituationData(
                regime=regime,
                fear_greed=fear_greed,
                total_trades_in_condition=0,
                buy_win_rate=0.0, sell_win_rate=0.0,
                avg_buy_pnl=0.0, avg_sell_pnl=0.0,
                direction_bias="neutral",
                tp_performance=[], common_categories=[],
                condition_summary="No TIAS situation data available",
            )

    async def _gather_flip_evidence(
        self, symbol: str, regime: str = "",
    ) -> SymbolFlipEvidence | None:
        """Per-coin, per-venue directional evidence (E26). Tag: APEX_ASSEMBLE_FLIP_EV.

        Resolves the LIVE exchange_mode from the transformer (fail-permissive:
        "" when unavailable), queries venue-isolated directional counts/win
        rates, and returns a SymbolFlipEvidence. Returns None only on a hard
        failure so the optimizer's flip gate cleanly falls back to the
        pooled trades list.
        """
        try:
            _xfm = self._services.get("transformer")
            exchange_mode = (
                str(getattr(_xfm, "current_mode", "") or "") if _xfm else ""
            )
            data = await self._tias_repo.get_symbol_flip_evidence(
                symbol, regime=regime, exchange_mode=exchange_mode,
            )
            ev = SymbolFlipEvidence(
                symbol=symbol,
                exchange_mode=str(data.get("exchange_mode", "") or ""),
                regime=str(data.get("regime", "") or ""),
                buy_count=int(data.get("buy_count") or 0),
                sell_count=int(data.get("sell_count") or 0),
                buy_win_rate=float(data.get("buy_win_rate") or 0.0),
                sell_win_rate=float(data.get("sell_win_rate") or 0.0),
                total=int(data.get("total") or 0),
            )
            log.info(
                "APEX_ASSEMBLE_FLIP_EV | sym={sym} venue={v} regime={r} "
                "total={t} buy={b} sell={s} | {c}",
                sym=symbol, v=ev.exchange_mode or "-", r=ev.regime or "-",
                t=ev.total, b=ev.buy_count, s=ev.sell_count, c=ctx(),
            )
            return ev
        except Exception as e:
            log.warning(
                "APEX_ASSEMBLE_FLIP_EV | sym={sym} err='{err}' | {c}",
                sym=symbol, err=str(e)[:200], c=ctx(),
            )
            return None

    # =========================================================================
    # Market condition helpers
    # =========================================================================

    async def _get_market_conditions(self, symbol: str) -> tuple[str, int]:
        """Get current regime string and Fear & Greed value.

        Mirrors the TIAS collector's _collect_group_c() pattern exactly.
        Returns ("unknown", 50) as safe defaults on any failure.
        """
        regime = "unknown"
        fear_greed = 50

        # Regime from RegimeDetector
        try:
            detector = self._services.get("regime_detector")
            if detector:
                coin_regime = detector.get_coin_regime(symbol)
                # Definitive-fix Phase 7 (2026-04-28) — REGIME_CACHE_QUERY
                # telemetry mirrors apex/gate.py.
                _hit = coin_regime is not None
                _cache_size = (
                    len(getattr(detector, "_per_coin_regimes", {}) or {})
                )
                _ready = bool(getattr(detector, "is_ready", lambda: True)())
                log.info(
                    f"REGIME_CACHE_QUERY | sym={symbol} reader=apex_assembler "
                    f"hit={_hit} ready={_ready} cache_size={_cache_size} | {ctx()}"
                )
                if coin_regime is not None:
                    # RegimeState.regime is a MarketRegime enum, .value is the string
                    regime = str(coin_regime.regime.value)
                else:
                    # Per-coin-authority Phase 2 (2026-05-29): per-coin regime
                    # unavailable -> UNKNOWN, NEVER the global BTC regime. The
                    # direction-lock queries the WR pool by this regime string;
                    # 'unknown' has no rows so no lock fires — a cold coin keeps
                    # the brain's direction rather than inheriting BTC's bias.
                    regime = "unknown"  # MarketRegime.UNKNOWN.value
                    log.warning(
                        "REGIME_FALLBACK | sym={sym} source=assembler | "
                        "per-coin unavailable, using UNKNOWN (not global) | {ctx}",
                        sym=symbol, ctx=ctx(),
                    )
        except Exception as e:
            log.debug(
                "APEX_REGIME_FAIL | sym={sym} err='{err}' | {ctx}",
                sym=symbol,
                err=str(e)[:100],
                ctx=ctx(),
            )

        # Fear & Greed from DB — with 24h staleness check
        try:
            if self._db:
                fg_row = await self._db.fetch_one(
                    "SELECT value FROM fear_greed_index "
                    "WHERE timestamp > datetime('now', '-24 hours') "
                    "ORDER BY timestamp DESC LIMIT 1"
                )
                if fg_row and fg_row.get("value") is not None:
                    fear_greed = int(fg_row["value"])
                else:
                    log.warning(
                        "FG_STALE | sym={sym} | No F&G data within 24h — "
                        "using default=50 (neutral) | {ctx}",
                        sym=symbol, ctx=ctx(),
                    )
        except Exception as e:
            log.warning(
                "FG_DEFAULT | sym={sym} value=50(default) | "
                "F&G unavailable — sentiment assumed neutral | {ctx}",
                sym=symbol,
                ctx=ctx(),
            )

        return regime, fear_greed

    # =========================================================================
    # Section 5: X-RAY Structural Intelligence
    # =========================================================================

    def _gather_structural_data(self, symbol: str) -> StructuralData | None:
        """Gather X-RAY structural analysis for this symbol.

        Reads from the structure cache (synchronous, O(1) in-memory lookup).
        Returns None if no structural data is available.

        Tag: APEX_ASSEMBLE_XRAY
        """
        sd = _gather_structural_data_from_cache(self._services, symbol)
        if sd:
            log.debug(
                "APEX_ASSEMBLE_XRAY | sym={sym} quality={q} smc={smc} | {ctx}",
                sym=symbol, q=sd.setup_quality, smc=sd.smc_confluence, ctx=ctx(),
            )
        return sd


# =============================================================================
# Utility
# =============================================================================

def _last_valid_arr(arr: Any) -> Optional[float]:
    """Return the last non-NaN, non-inf value from a numpy array (or list).

    Identical to the helper in src/tias/collector.py — reproduced here
    to keep the apex package independent of the tias package.
    """
    try:
        for i in range(len(arr) - 1, -1, -1):
            v = float(arr[i])
            if not math.isnan(v) and not math.isinf(v):
                return round(v, 8)
    except Exception:
        pass
    return None


def _gather_structural_data_from_cache(services: dict, symbol: str) -> StructuralData | None:
    """Gather X-RAY structural analysis from the structure cache.

    Standalone function called by IntelligenceAssembler._gather_structural_data().
    Returns None if no structural data is available.
    """
    try:
        structure_cache = services.get("structure_cache")
        if not structure_cache:
            return None
        analysis = structure_cache.get(symbol)
        if not analysis:
            return None

        sd = StructuralData(
            symbol=symbol,
            current_price=analysis.current_price,
            setup_quality=analysis.setup_quality,
            setup_score=analysis.setup_score,
            suggested_direction=analysis.suggested_direction,
            position_in_range=analysis.position_in_range,
            # Element 3 (2026-06-11) — pre-clamp range truth. getattr
            # with defaults so an old cached StructuralAnalysis object
            # (built before the fields existed) degrades to in-range.
            range_breakout=str(getattr(analysis, "range_breakout", "") or ""),
            range_overshoot_pct=float(
                getattr(analysis, "range_overshoot_pct", 0.0) or 0.0
            ),
        )

        # PRIMARY Sell-Bias Fix (2026-05-11) — propagate setup_type so
        # APEX can detect deliberate counter-trade setups (e.g.
        # BULLISH_FVG_OB_COUNTER) and refuse to flip them. The scanner
        # surfaces COUNTER_TRADE_LONG / COUNTER_TRADE_SHORT secondary
        # labels (91 events in the 2026-05-11 9-h window); the structure
        # engine renders them as setup_type values containing "counter".
        # ``analysis.setup_type`` is a SetupType enum; we serialize to
        # its .value string for downstream substring matching.
        try:
            _stype = getattr(analysis, "setup_type", None)
            if _stype is not None:
                sd.setup_type = str(getattr(_stype, "value", _stype) or "")
        except Exception:
            # Best-effort: a missing setup_type just leaves the empty
            # default in place; the counter-trade gate becomes a no-op.
            sd.setup_type = ""

        # R1 direction-fix (2026-05-17) — propagate the counter-aware
        # ``trade_direction``. classify_setup() inverts trade_direction
        # vs suggested_direction for counter setups; the brain prompt
        # already reads trade_direction. Pre-fix APEX read only
        # suggested_direction (regime label) and was blind to the
        # counter signal — that cross-layer information loss is the
        # actual mechanism behind the 87% suggested-short vs 62%
        # trade-short gap on 2026-05-16.
        try:
            sd.trade_direction = str(getattr(analysis, "trade_direction", "") or "")
        except Exception:
            sd.trade_direction = ""

        if analysis.nearest_support:
            sd.nearest_support = analysis.nearest_support.price
            sd.nearest_support_strength = analysis.nearest_support.strength
        if analysis.nearest_resistance:
            sd.nearest_resistance = analysis.nearest_resistance.price
            sd.nearest_resistance_strength = analysis.nearest_resistance.strength
        if analysis.market_structure:
            sd.structure = analysis.market_structure.structure
            sd.structure_strength = analysis.market_structure.strength
            if analysis.market_structure.last_bos:
                sd.last_bos = analysis.market_structure.last_bos.direction
        if analysis.structural_placement:
            sp = analysis.structural_placement
            sd.rr_ratio = sp.rr_ratio        # = rr_best (backward compat)
            sd.rr_quality = sp.rr_quality
            sd.rr_long = sp.rr_long
            sd.rr_short = sp.rr_short
            sd.rr_best_direction = sp.rr_best_direction

        # Smart Money
        if analysis.nearest_fvg:
            sd.nearest_fvg_direction = analysis.nearest_fvg.direction
            sd.nearest_fvg_range = f"${analysis.nearest_fvg.bottom:,.2f}-${analysis.nearest_fvg.top:,.2f}"
        if analysis.nearest_ob:
            sd.nearest_ob_direction = analysis.nearest_ob.direction
            sd.nearest_ob_range = f"${analysis.nearest_ob.low:,.2f}-${analysis.nearest_ob.high:,.2f}"
            sd.nearest_ob_fresh = analysis.nearest_ob.fresh
            sd.nearest_ob_score = analysis.nearest_ob.strength_score
        if analysis.active_sweep_signal:
            sd.active_sweep_signal = analysis.active_sweep_signal.signal
        if analysis.nearest_unswept_liquidity:
            sd.unswept_liquidity_level = analysis.nearest_unswept_liquidity.level
        sd.smc_confluence = analysis.smc_confluence

        # Phase 3: Confluence
        sd.poc_price = analysis.poc_price
        if analysis.volume_profile:
            sd.poc_vs_current = analysis.volume_profile.current_vs_poc
        sd.fib_key_level = analysis.fib_key_level
        if analysis.fibonacci:
            sd.fib_confluence = analysis.fibonacci.confluence_with
        if analysis.mtf_confluence:
            sd.mtf_score = analysis.mtf_confluence.score
            sd.mtf_quality = analysis.mtf_confluence.quality
        sd.total_confluence_factors = analysis.total_confluence_factors

        # Phase 4: Session + Scanner
        if analysis.session_context:
            sd.session = analysis.session_context.current_session
            sd.session_phase = analysis.session_context.session_phase
            sd.session_recommendation = analysis.session_context.trading_recommendation
        sd.setup_rank = analysis.setup_rank

        return sd

    except Exception as e:
        log.warning(
            "APEX_ASSEMBLE_XRAY | sym={sym} err='{err}' | {ctx}",
            sym=symbol, err=str(e)[:500], ctx=ctx(),
        )
        return None
