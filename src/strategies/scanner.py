"""Market Scanner: scans Bybit USDT perpetuals filtered by watch_list and selects top coins to trade.

Layer 1 universe alignment (Phase 2): the scanner's input set is now bounded
to ``watch_list ∪ open_position_symbols`` instead of all ~300 Bybit USDT
linear perpetuals. The 30-coin active focus stays the same — it's just
selected from the 50-coin watch list now (HR-1 in the blueprint). Open-
position coins are always included (HR-2) even if outside the watch list.
"""

import time

from src.config.settings import Settings
from src.core.logging import get_logger
from src.trading.services.market_service import MarketService

log = get_logger("strategies")

CACHE_TTL_SECONDS = 300


class MarketScanner:
    """Scans Bybit USDT perps (watch_list ∪ positions) and ranks the top coins by opportunity score.

    Args:
        settings: Application settings.
        market_service: For fetching ticker data.
        instrument_service: For pre-caching instrument specs (qty_step, etc.).
        watch_list: Optional set of symbols (e.g. ``{"BTCUSDT", ...}``) to
            constrain the scoring input to. When provided (Phase 2+),
            ``scan_market()`` filters all-tickers to ``watch_list ∪
            open_position_symbols`` before scoring. When None or empty
            (legacy / testnet), the scanner scores all Bybit tickers as
            before — preserves backward compatibility.
    """

    def __init__(self, settings: Settings, market_service: MarketService,
                 instrument_service=None,
                 watch_list: set[str] | None = None) -> None:
        self.settings = settings
        self.market_service = market_service
        self.instrument_service = instrument_service
        # Watch list — Phase 2 introduces this bound on the scoring input set.
        # An empty/None set falls back to the legacy "score all Bybit tickers"
        # behavior so the constructor is backward-compatible.
        self._watch_list: set[str] = set(watch_list) if watch_list else set()
        self.regime_detector = None  # Late-wired from WorkerManager
        self._position_service = None  # Late-wired from WorkerManager
        self._removed_cooldown: dict[str, float] = {}  # {symbol: removal_timestamp}
        self._cache: list[dict] = []
        self._cache_time: float = 0.0
        self._active_universe: list[str] = []
        self._universe_version: int = 0
        self._subscribers: list = []
        # Phase 5 (Universe flapping fix). Per-coin streak counters used by
        # the hysteresis gate in ``_update_universe``. ``_above_cutoff_streak``
        # increments on consecutive scans where the coin scored at least
        # ``entry_threshold_above_min`` above the bottom-N cutoff;
        # ``_below_cutoff_streak`` increments on consecutive scans where the
        # coin scored at least ``exit_threshold_below_min`` below it. Both
        # reset when the coin lands in the dead-band between the two
        # thresholds, so transient noise does not advance either gate.
        self._above_cutoff_streak: dict[str, int] = {}
        self._below_cutoff_streak: dict[str, int] = {}
        # Once-per-process startup log so operator can confirm watch_list wired through.
        if self._watch_list:
            log.info(
                "SCANNER_WATCH_LIST | size={n} source=config.universe.watch_list",
                n=len(self._watch_list),
            )

    def set_watch_list(self, new: set[str]) -> None:
        """Replace the watch_list at runtime (daily universe refresh, Phase 2).

        The watch_list is set once at construction and only READ in
        ``scan_market`` (the HR-1 input bound), so a refresh must call this to
        make the new universe take effect on the next scan. Open-position
        protection (HR-2) is unaffected — ``scan_market`` always unions in the
        live open positions regardless of this list. Per-coin hysteresis
        streak state for coins that LEAVE (and are not in the active universe)
        is pruned so the dicts do not grow unbounded; state for coins that
        stay carries forward, and the re-entry cooldown is left intact.
        """
        old = self._watch_list
        self._watch_list = set(new)
        removed = old - self._watch_list
        for sym in removed:
            if sym not in self._active_universe:
                self._above_cutoff_streak.pop(sym, None)
                self._below_cutoff_streak.pop(sym, None)
        log.info(
            "SCANNER_WATCH_LIST_UPDATED | old={o} new={n} removed={r} active_universe={au}",
            o=len(old), n=len(self._watch_list), r=len(removed), au=len(self._active_universe),
        )

    def subscribe(self, callback) -> None:
        """Subscribe to universe changes. Callback: async fn(symbols, added, removed)."""
        self._subscribers.append(callback)

    async def _update_universe(
        self,
        results: list[dict],
        protected_symbols: set[str] | None = None,
        all_scored: list[dict] | None = None,
    ) -> None:
        """Update the active universe from scan results and notify subscribers.

        Includes position protection (coins with open positions never removed),
        re-entry cooldown, hysteresis (Phase 5), and subscriber notification.

        Args:
            results: Scored coin dicts (already top-N).
            protected_symbols: Open-position symbols already fetched by the
                caller (``scan_market``). When provided, this method skips
                its own fetch — avoids the double-fetch that would otherwise
                happen now that ``scan_market`` also needs positions for
                input filtering. When None (legacy / direct callers), this
                method falls back to fetching positions itself.
            all_scored: FULL scored list (not just top-N) used to compute the
                bottom-N cutoff and update per-coin streak counters for the
                Phase 5 hysteresis gate. When None or empty, hysteresis is
                bypassed (preserves legacy / testnet behaviour).
        """
        import asyncio

        now_ts = time.time()
        scanner_cfg = self.settings.scanner
        hyst_cfg = scanner_cfg.hysteresis

        new_symbols = [c["symbol"] for c in results[:scanner_cfg.max_coins]]

        # ═══ Phase 5 (Universe flapping fix) — HYSTERESIS GATE ═══
        # Skip when disabled or when no full scored list is provided
        # (testnet path / legacy callers). Otherwise, compute the cutoff
        # = score of the bottom-N coin, update per-coin streak counters,
        # and apply the entry/exit gates. Force-include for BTC/ETH and
        # protected symbols still wins (handled below this block).
        hysteresis_blocked: list[str] = []
        if hyst_cfg.enabled and all_scored:
            self._apply_hysteresis_gate(
                all_scored=all_scored,
                new_symbols=new_symbols,
                hysteresis_blocked=hysteresis_blocked,
                hyst_cfg=hyst_cfg,
                max_coins=scanner_cfg.max_coins,
            )

        # NOTE: BTC/ETH reference-pair unconditional force-include was
        # removed 2026-04-29 — see ``scanner_worker.py`` for the full
        # rationale. This legacy ``_update_universe`` only runs at boot
        # via ``scan_market``; ScannerWorker takes over thereafter via
        # ``set_active_universe``. Removing here keeps the boot-time
        # universe consistent with steady-state cycles. HR-2 is
        # preserved by the position-protection block immediately below.

        # ═══ POSITION PROTECTION ═══
        # Coins with open positions can NEVER be removed from the universe.
        # Phase 2: prefer the caller-supplied set (fetched by scan_market) to
        # avoid a redundant Bybit call. Legacy / direct callers can still
        # invoke this method without protected_symbols and we fetch ourselves.
        if protected_symbols is None:
            protected_symbols = set()
            if self._position_service:
                try:
                    positions = await self._position_service.get_positions()
                    protected_symbols = {p.symbol for p in positions}
                except Exception as e:
                    log.error(
                        "Scanner: FAILED to fetch positions for protection — "
                        "refusing to remove ANY coins this tick: {err}",
                        err=str(e)[:100],
                    )
                    protected_symbols = set(self._active_universe)

        # Force-include protected symbols even if not in top N
        new_set_check = set(new_symbols)
        for sym in protected_symbols:
            if sym not in new_set_check:
                new_symbols.append(sym)
                log.info(
                    "Scanner: PROTECTING {sym} (has open position, kept in universe)",
                    sym=sym,
                )

        # ═══ COOLDOWN FILTER ═══
        # Phase 5 (Universe flapping fix). The threshold is now read from
        # ``scanner.reentry_cooldown_seconds`` (default 600 s, was a
        # hardcoded 300 s). Force-included coins (BTC/ETH + open
        # positions) bypass per the existing logic.
        cooldown_s = scanner_cfg.reentry_cooldown_seconds
        cooled_out = []
        for sym in list(new_symbols):
            if sym in self._removed_cooldown:
                elapsed = now_ts - self._removed_cooldown[sym]
                if elapsed < cooldown_s and sym not in protected_symbols:
                    cooled_out.append(sym)
                    new_symbols.remove(sym)
        if cooled_out:
            log.info(
                "Scanner: {n} coins blocked by re-entry cooldown: {coins}",
                n=len(cooled_out), coins=cooled_out[:10],
            )

        old_set = set(self._active_universe)
        new_set = set(new_symbols)
        added = new_set - old_set
        removed = old_set - new_set

        # Record removals in cooldown dict
        for sym in removed:
            self._removed_cooldown[sym] = now_ts

        # Cleanup cooldown entries older than 1 hour
        self._removed_cooldown = {
            s: t for s, t in self._removed_cooldown.items()
            if now_ts - t < 3600
        }

        # Update the global SymbolRegistry so all validators accept these coins
        from src.config.constants import SUPPORTED_SYMBOLS
        SUPPORTED_SYMBOLS.update(new_set)

        if added or removed:
            self._active_universe = new_symbols
            self._universe_version += 1
            log.info(
                "Scanner universe UPDATED v{ver}: {n} coins "
                "(added: {a}, removed: {r}, protected: {p})",
                ver=self._universe_version, n=len(new_symbols),
                a=added or "none", r=removed or "none",
                p=len(protected_symbols),
            )
            for cb in self._subscribers:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(new_symbols, added, removed)
                    else:
                        cb(new_symbols, added, removed)
                except Exception as e:
                    log.warning("Scanner subscriber failed: {err}", err=str(e))
        elif not self._active_universe:
            self._active_universe = new_symbols
            log.info(
                "Scanner universe INITIALIZED: {n} coins",
                n=len(new_symbols),
            )

        # Pre-cache instrument info for all selected coins (qty_step, min_qty, etc.)
        if self.instrument_service and added:
            for sym in added:
                try:
                    await self.instrument_service.get_instrument_info(sym)
                except Exception:
                    pass  # non-critical — will be fetched lazily on first order

    def _apply_hysteresis_gate(
        self,
        *,
        all_scored: list[dict],
        new_symbols: list[str],
        hysteresis_blocked: list[str],
        hyst_cfg,
        max_coins: int,
    ) -> None:
        """Apply Phase 5 consecutive-scan hysteresis to the entry decision.

        Called from ``_update_universe``. Mutates ``new_symbols`` in place
        — removes any coin whose above-cutoff streak has not yet reached
        ``entry_consecutive_scans``. Removed coins are appended to
        ``hysteresis_blocked`` for the tick-summary log.

        Coins ALREADY in the active universe are not subject to the entry
        streak (only the exit streak; they leave only after
        ``exit_consecutive_scans`` consecutive below-cutoff scans). New
        entrants must clear the entry streak gate.

        Per-symbol ``SCANNER_HYSTERESIS`` log fires for any coin in a
        transitional state (pending or just confirmed) so operators can
        see why each coin entered or didn't.
        """
        if not all_scored:
            return

        # Score map for fast lookup, and the cutoff = score of the
        # bottom-N coin in the full scored list (sorted descending).
        sorted_scored = sorted(
            all_scored, key=lambda x: x.get("score", 0), reverse=True
        )
        cutoff = (
            sorted_scored[max_coins - 1].get("score", 0)
            if len(sorted_scored) >= max_coins
            else (sorted_scored[-1].get("score", 0) if sorted_scored else 0)
        )
        score_map = {row.get("symbol"): row.get("score", 0) for row in sorted_scored}
        active_set = set(self._active_universe)
        entry_floor = cutoff + hyst_cfg.entry_threshold_above_min
        exit_ceiling = cutoff + hyst_cfg.exit_threshold_below_min

        # Update streaks for every scored coin and log transitions.
        for sym, score in score_map.items():
            if score >= entry_floor:
                self._above_cutoff_streak[sym] = (
                    self._above_cutoff_streak.get(sym, 0) + 1
                )
                self._below_cutoff_streak[sym] = 0
            elif score <= exit_ceiling:
                self._below_cutoff_streak[sym] = (
                    self._below_cutoff_streak.get(sym, 0) + 1
                )
                self._above_cutoff_streak[sym] = 0
            else:
                # Dead-band — reset both so transient noise does not
                # advance either gate.
                if (
                    self._above_cutoff_streak.get(sym, 0)
                    or self._below_cutoff_streak.get(sym, 0)
                ):
                    self._above_cutoff_streak[sym] = 0
                    self._below_cutoff_streak[sym] = 0

        # Apply ENTRY gate: prune coins from new_symbols if not yet
        # streak-confirmed AND they are NOT already in the active
        # universe (incumbents are subject only to the exit gate).
        for sym in list(new_symbols):
            if sym in active_set:
                continue
            streak = self._above_cutoff_streak.get(sym, 0)
            if streak < hyst_cfg.entry_consecutive_scans:
                hysteresis_blocked.append(sym)
                new_symbols.remove(sym)
                log.info(
                    "SCANNER_HYSTERESIS | coin={s} action=entry_pending "
                    "streak={streak}/{req} score={sc} cutoff={cf} "
                    "entry_floor={ef}",
                    s=sym, streak=streak,
                    req=hyst_cfg.entry_consecutive_scans,
                    sc=score_map.get(sym, 0), cf=cutoff, ef=entry_floor,
                )
            elif streak == hyst_cfg.entry_consecutive_scans:
                # First scan that confirmed entry — emit a one-shot log.
                log.info(
                    "SCANNER_HYSTERESIS | coin={s} action=entry_confirmed "
                    "streak={streak}/{req} score={sc} cutoff={cf}",
                    s=sym, streak=streak,
                    req=hyst_cfg.entry_consecutive_scans,
                    sc=score_map.get(sym, 0), cf=cutoff,
                )

        # Apply EXIT gate: incumbents that fell out of new_symbols but
        # have NOT yet hit the exit streak get re-added. Force-include
        # for protected symbols still applies separately (handled in
        # the caller).
        new_set = set(new_symbols)
        for sym in active_set:
            if sym in new_set:
                continue
            below = self._below_cutoff_streak.get(sym, 0)
            if below < hyst_cfg.exit_consecutive_scans:
                # Below-cutoff streak not yet confirmed — keep the coin.
                new_symbols.append(sym)
                log.info(
                    "SCANNER_HYSTERESIS | coin={s} action=exit_pending "
                    "streak={streak}/{req} score={sc} cutoff={cf} "
                    "exit_ceiling={xc}",
                    s=sym, streak=below,
                    req=hyst_cfg.exit_consecutive_scans,
                    sc=score_map.get(sym, 0), cf=cutoff, xc=exit_ceiling,
                )
            elif below == hyst_cfg.exit_consecutive_scans:
                log.info(
                    "SCANNER_HYSTERESIS | coin={s} action=exit_confirmed "
                    "streak={streak}/{req} score={sc} cutoff={cf}",
                    s=sym, streak=below,
                    req=hyst_cfg.exit_consecutive_scans,
                    sc=score_map.get(sym, 0), cf=cutoff,
                )

    async def scan_market(self) -> list[dict]:
        """Scan all USDT perpetuals and return top coins by opportunity score.

        Bulk-fetches all Bybit linear tickers in one API call (~300 coins),
        scores each on volume/volatility/momentum/spread, and returns the
        top ``max_coins`` (default 20).

        Returns:
            List of dicts with: symbol, score, volume_24h, change_24h_pct,
            funding_rate, spread_pct, coin_tier — sorted by score descending.
        """
        cfg = self.settings.scanner

        # Testnet mode: use hardcoded symbols since volume is low
        if self.settings.bybit.testnet:
            return await self._scan_testnet(cfg)

        # ═══ HR-2 PROTECTED-SYMBOLS FETCH (Phase 2) ═══
        # Fetch open positions ONCE per scan cycle. Used both as the input
        # filter (so position-coins outside watch_list still get scored) and
        # passed to _update_universe to avoid a second redundant fetch.
        protected_symbols: set[str] = set()
        if self._position_service:
            try:
                positions = await self._position_service.get_positions()
                protected_symbols = {p.symbol for p in positions}
            except Exception as e:
                # Match the existing _update_universe failure semantics: refuse
                # to remove ANY coins this tick by treating the current
                # universe as protected. Conservative — preserves data flow.
                log.error(
                    "Scanner: FAILED to fetch positions in scan_market — "
                    "refusing to remove ANY coins this tick: {err}",
                    err=str(e)[:100],
                )
                protected_symbols = set(self._active_universe)

        try:
            tickers = await self.market_service.get_all_linear_tickers()
        except Exception as e:
            log.error("Bulk ticker fetch failed, falling back to defaults: {err}", err=str(e))
            try:
                tickers = await self.market_service.get_tickers(
                    symbols=self.settings.bybit.default_symbols,
                )
            except Exception as e2:
                log.error("Fallback scan also failed: {err}", err=str(e2))
                return self._cache

        # ═══ HR-1 WATCH-LIST FILTER (Phase 2) ═══
        # Bound the scoring input set to ``watch_list ∪ protected_symbols``
        # so the 30-coin active focus is always selected from a known pool.
        # Empty watch_list (legacy) → no filter, score all tickers as before.
        total_in = len(tickers)
        if self._watch_list:
            input_set = self._watch_list | protected_symbols
            tickers = [t for t in tickers if t.symbol in input_set]
            log.info(
                "SCANNER_INPUT | watch_list={w} protected={p} input_set={i} "
                "all_tickers={a} filtered={f}",
                w=len(self._watch_list),
                p=len(protected_symbols),
                i=len(input_set),
                a=total_in,
                f=len(tickers),
            )

        scored: list[dict] = []
        for ticker in tickers:
            vol = ticker.volume_24h
            price = ticker.last_price
            change_abs = abs(ticker.change_24h_pct)

            # ═══ HARD DISQUALIFIERS ═══
            if vol < 5_000_000:
                continue
            if price < 0.0001:
                continue

            # Spread calculation (needed for disqualifier and scoring)
            spread_pct = 0.0
            if ticker.bid > 0:
                spread_pct = (ticker.ask - ticker.bid) / ticker.bid * 100
            if spread_pct > 0.5:
                continue

            # Daily range — true volatility from high/low
            daily_range_pct = 0.0
            if price > 0 and ticker.high_24h > 0 and ticker.low_24h > 0:
                daily_range_pct = (ticker.high_24h - ticker.low_24h) / price * 100

            # Trend strength ratio: directional move / total range
            trend_ratio = 0.0
            if daily_range_pct > 0:
                trend_ratio = change_abs / daily_range_pct

            score = 0

            # ═══ COMPONENT 1: MOMENTUM (0-30) ═══
            if change_abs >= 10:
                score += 30
            elif change_abs >= 5:
                score += 25
            elif change_abs >= 3:
                score += 20
            elif change_abs >= 1.5:
                score += 15
            elif change_abs >= 0.8:
                score += 10
            else:
                score += 0

            # ═══ COMPONENT 2: VOLATILITY (0-25) ═══
            if daily_range_pct >= 8:
                score += 25
            elif daily_range_pct >= 5:
                score += 20
            elif daily_range_pct >= 3:
                score += 15
            elif daily_range_pct >= 1.5:
                score += 10
            else:
                score += 0

            # ═══ COMPONENT 3: TREND STRENGTH (0-15) ═══
            if trend_ratio >= 0.6:
                score += 15
            elif trend_ratio >= 0.4:
                score += 10
            elif trend_ratio >= 0.25:
                score += 5
            else:
                score += 0

            # ═══ COMPONENT 4: VOLUME (0-20) ═══
            if vol >= 500_000_000:
                score += 20
            elif vol >= 100_000_000:
                score += 18
            elif vol >= 50_000_000:
                score += 15
            elif vol >= 20_000_000:
                score += 10
            elif vol >= 5_000_000:
                score += 5

            # ═══ COMPONENT 5: SPREAD (0-10) ═══
            if spread_pct <= 0.02:
                score += 10
            elif spread_pct <= 0.05:
                score += 8
            elif spread_pct <= 0.10:
                score += 5
            elif spread_pct <= 0.20:
                score += 2
            else:
                score += 0

            # ═══ BONUS: REGIME ALIGNMENT (+10/+5/0/-10) ═══
            regime_bonus = 0
            regime_name = "unknown"
            if self.regime_detector:
                try:
                    _cr = self.regime_detector.get_coin_regime(ticker.symbol)
                    if _cr:
                        regime_name = _cr.regime.value
                        if regime_name in ("trending_up", "trending_down"):
                            regime_bonus = 10
                        elif regime_name == "volatile":
                            regime_bonus = 5
                        elif regime_name == "dead":
                            regime_bonus = -10
                except Exception:
                    regime_bonus = 0
            score += regime_bonus

            # ═══ PENALTY: CHOP DETECTION (-15) ═══
            chop_penalty = 0
            if daily_range_pct > 5 and trend_ratio < 0.25:
                chop_penalty = -15
            score += chop_penalty

            # Floor at 0
            score = max(0, score)

            log.debug(
                "SCAN_SCORE | sym={sym} final={sc} | "
                "chg={chg:.1f}% rng={rng:.1f}% ts={ts:.2f} "
                "vol=${vm:.0f}M spd={spd:.3f}% "
                "regime={rgm}({rb:+d}) chop={ch:+d}",
                sym=ticker.symbol, sc=score,
                chg=change_abs, rng=daily_range_pct, ts=trend_ratio,
                vm=vol / 1_000_000, spd=spread_pct,
                rgm=regime_name, rb=regime_bonus,
                ch=chop_penalty,
            )

            scored.append({
                "symbol": ticker.symbol,
                "score": score,
                "volume_24h": vol,
                "change_24h_pct": ticker.change_24h_pct,
                "funding_rate": 0.0,
                "spread_pct": round(spread_pct, 4),
                "coin_tier": self.get_coin_tier(
                    ticker.symbol, vol, daily_range_pct,
                ),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        result = scored[:cfg.max_coins]

        self._cache = result
        self._cache_time = time.monotonic()

        top5 = ", ".join(c["symbol"] for c in result[:5])
        log.info(
            "Market scan: {n} coins scored, top {t} selected. Best: {top}",
            n=len(scored), t=len(result), top=top5,
        )

        # Pass the already-fetched protected_symbols into _update_universe to
        # avoid the redundant Bybit get_positions call. Phase 5 also
        # passes the FULL scored list (``scored``) so the hysteresis gate
        # can compute the bottom-N cutoff and update per-coin streak
        # counters.
        await self._update_universe(
            result, protected_symbols=protected_symbols, all_scored=scored,
        )
        return result

    async def _scan_testnet(self, cfg) -> list[dict]:
        """Testnet mode: only scan SUPPORTED_SYMBOLS (verified on Bybit testnet)."""
        from src.config.constants import SUPPORTED_SYMBOLS, TESTNET_EXCLUDED_SYMBOLS
        testnet_symbols = sorted(SUPPORTED_SYMBOLS - TESTNET_EXCLUDED_SYMBOLS)
        results = []
        for symbol in testnet_symbols:
            try:
                ticker = await self.market_service.get_ticker(symbol)
                if ticker and ticker.last_price > 0:
                    results.append({
                        "symbol": symbol,
                        "score": 80,
                        "volume_24h": ticker.volume_24h or 1_000_000,
                        "change_24h_pct": ticker.change_24h_pct or 0,
                        "funding_rate": 0.0,
                        "spread_pct": 0.05,
                        "coin_tier": self.get_coin_tier(symbol),
                    })
            except Exception:
                continue

        self._cache = results
        self._cache_time = time.monotonic()
        log.info("Testnet scan: {n} coins available", n=len(results))
        await self._update_universe(results)
        return results

    async def get_active_universe(self) -> list[str]:
        """Return the current active trading universe symbol list.

        Uses the dedicated _active_universe list (always includes BTC/ETH).
        Falls back to config defaults if scanner hasn't run yet.
        """
        if self._active_universe:
            return list(self._active_universe)
        if not self._cache or time.monotonic() - self._cache_time > CACHE_TTL_SECONDS:
            await self.scan_market()
        return [c["symbol"] for c in self._cache]

    def set_active_universe(self, symbols: list[str]) -> None:
        """Set the active universe list and bump the version counter.

        Phase 6 (corrected-Layer-1): public setter consumed by ScannerWorker
        when it commits its top-N selection. Replaces direct mutation of
        ``_active_universe`` so the worker stays decoupled from the
        scanner's internals.
        """
        self._active_universe = list(symbols)
        self._universe_version += 1

    def get_subscribers_snapshot(self) -> list:
        """Return a SHALLOW COPY of the current subscriber list.

        Phase 6 (corrected-Layer-1): public reader consumed by ScannerWorker
        when it fans out universe-change notifications. Returns a copy so
        the caller can iterate without mutation hazards if a subscriber
        registers/unregisters mid-iteration. Phase 7 removed all worker
        subscribers; the list is empty in normal operation but the API
        is preserved for future non-worker subscribers.
        """
        return list(self._subscribers)

    @staticmethod
    def get_coin_tier(symbol: str, volume_24h: float = 0,
                      daily_range_pct: float = 0.0) -> int:
        """Return tier for leverage decisions (1=safest, 4=riskiest).

        Tiers:
            1 (STABLE):   volume > $500M AND range < 3%
            2 (ACTIVE):   volume > $50M AND range <= 8%
            3 (VOLATILE): volume > $10M AND range > 8%
            4 (EXTREME):  range > 20%

        Falls back to symbol name for BTC/ETH (always tier 1).
        """
        if symbol in {"BTCUSDT", "ETHUSDT"}:
            return 1
        if daily_range_pct > 20:
            return 4
        if volume_24h > 500_000_000 and daily_range_pct < 3:
            return 1
        if volume_24h > 50_000_000 and daily_range_pct <= 8:
            return 2
        if volume_24h > 10_000_000:
            return 3
        if symbol in {"SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"}:
            return 2
        return 3
