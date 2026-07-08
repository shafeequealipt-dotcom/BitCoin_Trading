"""Structure Worker: runs X-RAY structural analysis for the full watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins). With batch_size=25,
  a full sweep completes in 2 ticks (~10 min via two sweet-spot fires).
- Fires at the configured sweet spot (default 0:45) within every 5-min
  window, after KlineWorker's 0:30 finishes its writes. The 15-second gap
  gives kline writes time to land in trading.db before structure reads.
- ``ShadowKlineReader`` (Shadow DB fallback path, async-aiosqlite per the
  2026-04-25 fix) is unchanged.
"""

import time

from src.analysis.structure.structure_cache import (
    HigherTFStructureCache,
    StructureCache,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame, WorkerTier
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.workers.base_worker import SweetSpotWorker

log = get_logger("xray")


class StructureWorker(SweetSpotWorker):
    """Background worker that refreshes X-RAY structural cache.

    Reads ``config.universe.watch_list`` (50 coins) every tick. Fires at
    ``settings.workers.sweet_spots.structure_worker`` (default ``"0:45"``)
    within every ``settings.workers.sweet_spots.window_minutes`` window.

    Args:
        settings: Application settings.
        db: Database manager.
        engine: StructureEngine instance.
        cache: StructureCache instance.
        scanner: Retained as None-safe legacy injection (not read by tick);
            slated for removal in Phase 7 along with the rotation handler.
        shadow_kline_reader: Optional ShadowKlineReader for Shadow DB klines.
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1B
    # Phase 4 — skip tick when LayerManager.is_cycle_active() is False.
    cycle_gated = True

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        engine: StructureEngine,
        cache: StructureCache,
        scanner=None,
        shadow_kline_reader=None,
    ) -> None:
        super().__init__(
            name="structure_worker",
            sweet_spot=settings.workers.sweet_spots.structure_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self._engine = engine
        self._cache = cache
        self._scanner = scanner  # legacy injection; not read by tick()
        self._market_repo = MarketRepository(db)
        self._shadow_reader = shadow_kline_reader

        # Phase 4 sub-engines (lazy init)
        self._session_timer = None
        self._setup_scanner = None

        # Batching state — universe is the full watch_list (50), batched at
        # ``batch_size`` per tick. With batch_size=25, a full sweep completes
        # in 2 ticks (~10 min on the 5-min window). Wrap-around math at
        # _get_universe handles arbitrary universe sizes.
        self._full_universe: list[str] = []
        self._batch_start: int = 0
        self._batch_size = settings.structure.batch_size

        # Issue #5 (2026-05-31): higher-timeframe (H4/D1) MTF confluence. Gated
        # by settings.structure.mtf_multi_timeframe_enabled (default OFF -> the
        # engine/scorer behave byte-for-byte as today's H1-only logic). When on,
        # the worker batch-fetches H4/D1 klines (already produced hourly by
        # kline_worker but unused downstream), computes a cheap direction-only
        # structural view per TF (cached per the kline cooldowns), and passes
        # them into engine.analyze so the scorer can blend cross-TF agreement.
        _struct = settings.structure
        self._mtf_enabled = bool(getattr(_struct, "mtf_multi_timeframe_enabled", False))
        self._mtf_timeframes = list(getattr(_struct, "mtf_timeframes", ["240", "D"]))
        self._mtf_h4_ttl = float(getattr(_struct, "mtf_h4_cache_ttl_seconds", 300))
        self._mtf_d1_ttl = float(getattr(_struct, "mtf_d1_cache_ttl_seconds", 3600))
        self._mtf_htf_weight = float(getattr(_struct, "mtf_htf_weight", 0.25))
        self._mtf_htf_limit = int(getattr(_struct, "mtf_htf_limit", 120))
        self._htf_cache = HigherTFStructureCache()
        self._mtf_init_logged = False

    async def tick(self) -> None:
        """Analyze symbols (batched or full), calculate session, rank setups."""
        t0 = time.monotonic()

        # Determine universe for this tick
        universe = await self._get_universe()

        # Issue #5: refresh the higher-TF (H4/D1) structural views for this
        # batch's stale symbols BEFORE the per-symbol analyze loop, so each
        # analyze() call gets fresh cross-TF context. No-op when the flag is off.
        if self._mtf_enabled and universe:
            try:
                await self._refresh_htf_views(universe)
            except Exception as e:
                # Never let the (optional) HTF refresh break the H1 tick.
                log.warning(f"XRAY_MTF_REFRESH_ERR | err={str(e)[:80]} | {ctx()}")

        # Phase 12: Calculate session context (once per tick)
        session_context = None
        try:
            if self._session_timer is None:
                from src.analysis.structure.session_timing import SessionTimer
                self._session_timer = SessionTimer(self.settings.structure)
            # Use first coin's candles for Asian range
            first_candles = await self._fetch_klines(universe[0]) if universe else None
            session_context = self._session_timer.get_context(
                current_price=first_candles[-1].close if first_candles else 0,
                candles=first_candles,
            )
        except Exception as e:
            # Phase 11 (post-Layer-1 fix): promoted from DEBUG to WARNING
            # so operators see X-RAY session-context failures at the
            # default log level (INFO+). Pre-fix the line was emitted at
            # DEBUG and never appeared in default logs.
            log.warning(f"XRAY_SESSION_ERR | err={str(e)[:80]} | {ctx()}")

        # Analyze each symbol in this tick's batch
        analyzed = 0
        errors = 0
        # Layer 1 restructure Phase 2 — accumulate setup_type counts so
        # we can emit one XRAY_CLASSIFY_SUMMARY at end of tick instead of
        # 50 individual lines. Per-coin XRAY_CLASSIFY still emits at
        # DEBUG (or INFO when type != NONE) inside the per-symbol loop.
        setup_counts: dict[str, int] = {}
        # Phase 2 (output-quality): accumulate setup_type_confidence
        # values so the summary log carries p50/p95 — operators see when
        # classifications are mostly low-confidence ("borderline cases")
        # vs high-confidence ("strong patterns") at a glance.
        setup_confidences: list[float] = []
        # XRAY counter-setup Phase 2 — accumulate per-coin H1 NATR so
        # XRAY_CLASSIFY_SUMMARY can report ATR p50 + the resulting FVG
        # window p50 used by _find_nearest_fvg this tick. Operators can
        # then judge whether the ATR-scaled window is hitting the floor
        # (low-vol cluster) or expanding (high-vol cluster).
        atr_pcts: list[float] = []
        # R1 direction-fix (2026-05-17) — accumulate trade_direction
        # distribution so the summary line surfaces what the brain (and
        # post-R1 also APEX) actually reads. trade_direction is
        # counter-inverted relative to suggested_direction, so the two
        # distributions diverge meaningfully on sessions with many
        # counter setups (2026-05-16: 87 percent suggested short vs 62
        # percent trade short — a 25-pp gap masked from prior summary).
        trade_dir_counts: dict[str, int] = {}
        counter_count: int = 0

        for symbol in universe:
            try:
                candles = await self._fetch_klines(symbol)
                if not candles or len(candles) < self.settings.structure.min_candles:
                    continue

                current_price = candles[-1].close
                # Issue #5: pass cached H4/D1 views (None when flag off or no
                # usable HTF data -> scorer falls back to H1-only).
                _htf_views = self._build_htf_views(symbol) if self._mtf_enabled else None
                result = self._engine.analyze(
                    symbol, current_price, candles,
                    session_context=session_context,
                    higher_tf_views=_htf_views,
                )
                if result:
                    self._cache.set(symbol, result)
                    # Phase 6 (output-quality): record xray cache-write
                    # timestamp so the freshness aggregator can compute
                    # klines→xray latency at end of cycle.
                    try:
                        from src.core.cache_freshness import record_write
                        record_write("xray", symbol)
                    except Exception:  # pragma: no cover — defensive
                        pass
                    analyzed += 1
                    # Phase 2 — record + log the categorical classification.
                    type_name = result.setup_type.value
                    setup_counts[type_name] = setup_counts.get(type_name, 0) + 1
                    setup_confidences.append(float(result.setup_type_confidence))
                    atr_pcts.append(float(getattr(result, "atr_pct_h1", 0.0) or 0.0))
                    # R1 direction-fix — track counter-aware trade_direction.
                    _td = str(getattr(result, "trade_direction", "") or "")
                    _td_key = _td if _td else "na"
                    trade_dir_counts[_td_key] = trade_dir_counts.get(_td_key, 0) + 1
                    if "counter" in type_name:
                        counter_count += 1
                    # Phase 12.1 (lifecycle-logging-audit Gap 1.7-G1):
                    # XRAY_CLASSIFY (NONE) DEBUG removed — redundant with
                    # XRAY_NONE_REASON below (which fires at INFO with full
                    # diagnostic detail).
                    if result.setup_type.value == "none":
                        # Phase 2 (output-quality): emit XRAY_NONE_REASON
                        # at INFO so operators can tune
                        # [analysis.structure.setup_types] thresholds with
                        # evidence — "27 of 50 NONE because mtf<0.7" is
                        # actionable; "27 NONE" alone is not. The diagnostic
                        # is read-only and inexpensive (single tree walk).
                        try:
                            diag = self._engine.diagnose_none(result)
                            # XRAY counter-setup Phase 6 — emit structured
                            # evidence so calibration is data-driven. The
                            # original 8 fields are preserved; the new
                            # fields surface in/counter zone state, BoS
                            # detail, sweep + range presence, ATR, and the
                            # window percents the Phase 2/3 finders used.
                            log.info(
                                f"XRAY_NONE_REASON | sym={symbol} "
                                f"closest_type={diag['closest_type']} "
                                f"missed_by='{diag['missed_by']}' "
                                f"weakest_input={diag['weakest_input']} "
                                f"mtf={diag['mtf_score_01']:.2f} "
                                f"smc={diag['smc_01']:.2f} "
                                f"direction={diag['direction'] or 'na'} "
                                f"structure={diag['structure'] or 'na'} "
                                f"in_direction_fvg={diag.get('in_direction_fvg', 'na')} "
                                f"in_direction_ob={diag.get('in_direction_ob', 'na')} "
                                f"counter_direction_fvg={diag.get('counter_direction_fvg', 'na')} "
                                f"counter_direction_ob={diag.get('counter_direction_ob', 'na')} "
                                f"last_bos_significance={diag.get('last_bos_significance', 'none')} "
                                f"last_bos_age_bars={diag.get('last_bos_age_bars', -1)} "
                                f"recent_sweep={diag.get('recent_sweep', False)} "
                                f"range_compression={diag.get('range_compression', False)} "
                                f"atr_pct={diag.get('atr_pct_h1', 0.0):.3f} "
                                f"window_pct_fvg={diag.get('window_pct_fvg', 0.0):.2f} "
                                f"window_pct_ob={diag.get('window_pct_ob', 0.0):.2f} "
                                f"first_failure_branch={diag.get('first_failure_branch', 'no_match')} | {ctx()}"
                            )
                        except Exception as e:
                            # Non-fatal — diagnose_none is purely advisory.
                            # Log at DEBUG to avoid drowning the per-cycle
                            # signal-to-noise.
                            log.debug(
                                f"XRAY_NONE_REASON_FAIL | sym={symbol} "
                                f"err='{str(e)[:80]}' | {ctx()}"
                            )
                    else:
                        # XRAY counter-setup Phase 4 — surface trade_direction
                        # (may differ from suggested_direction for *_COUNTER
                        # setups) and a is_counter flag for quick log filters.
                        _is_counter = "counter" in result.setup_type.value
                        log.info(
                            f"XRAY_CLASSIFY | sym={symbol} "
                            f"setup_type={result.setup_type.value} "
                            f"confidence={result.setup_type_confidence:.2f} "
                            f"score={result.setup_score} "
                            f"trade_direction={result.trade_direction or 'n/a'} "
                            f"suggested_direction={result.suggested_direction or 'n/a'} "
                            f"is_counter={'true' if _is_counter else 'false'} | {ctx()}"
                        )
                        # R1 direction-fix Rule 6 (spec-mandated events) —
                        # emit per-coin counter-inversion + directional
                        # reasoning lines whenever a counter setup fires.
                        # The XRAY_CLASSIFY line above is the legacy
                        # observability; these are the new event names the
                        # operator's audit playbook greps for.
                        if _is_counter:
                            log.info(
                                f"XRAY_COUNTER_INVERSION_APPLIED | sym={symbol} "
                                f"setup_type={result.setup_type.value} "
                                f"suggested_direction={result.suggested_direction or 'n/a'} "
                                f"trade_direction={result.trade_direction or 'n/a'} "
                                f"confidence={result.setup_type_confidence:.2f} "
                                f"inversion=opposite_of_suggested | {ctx()}"
                            )
                            log.info(
                                f"XRAY_COUNTER_DECISION_DETAIL | sym={symbol} "
                                f"setup_type={result.setup_type.value} "
                                f"score={result.setup_score} "
                                f"confidence={result.setup_type_confidence:.2f} "
                                f"trade_direction={result.trade_direction or 'n/a'} "
                                f"brain_reads=trade_direction "
                                f"apex_reads=trade_direction_post_R1 | {ctx()}"
                            )
                        log.info(
                            f"XRAY_DIRECTIONAL_REASONING | sym={symbol} "
                            f"setup_type={result.setup_type.value} "
                            f"is_counter={'true' if _is_counter else 'false'} "
                            f"suggested_direction={result.suggested_direction or 'n/a'} "
                            f"trade_direction={result.trade_direction or 'n/a'} "
                            f"final_brain_direction={result.trade_direction or result.suggested_direction or 'n/a'} "
                            f"reasoning={'counter_inversion' if _is_counter else 'aligned_with_structure'} | {ctx()}"
                        )

            except Exception as e:
                errors += 1
                # Phase 11 (post-Layer-1 fix): promoted from DEBUG to
                # WARNING so per-symbol X-RAY failures appear in default
                # logs. The errors counter is already aggregated into
                # the tick summary, but the per-symbol detail (which
                # coin failed and why) was previously invisible.
                log.warning(f"XRAY_TICK_ERR | sym={symbol} err={str(e)[:80]} | {ctx()}")

        # Layer 1 restructure Phase 2 — emit cycle-level distribution of
        # categorical setup types. Operators can grep this to spot
        # over- or under-classification (e.g. all NONE = thresholds
        # too tight; all one type = bug).
        # Phase 2 (output-quality) extension: also report confidence
        # percentiles so operators see at a glance whether non-NONE
        # classifications are high-conf ("real patterns") or low-conf
        # ("borderline").
        if setup_counts:
            counts_str = " ".join(
                f"{k}={v}" for k, v in sorted(
                    setup_counts.items(), key=lambda kv: -kv[1],
                )
            )
            # Compute p50 / p95 across all setup_type_confidence values
            # this tick. Sort once; pick the right indices.
            if setup_confidences:
                _sorted = sorted(setup_confidences)
                _n = len(_sorted)
                _p50 = _sorted[max(0, int(0.50 * (_n - 1)))]
                _p95 = _sorted[max(0, int(0.95 * (_n - 1)))]
            else:
                _p50 = 0.0
                _p95 = 0.0
            # XRAY counter-setup Phase 2 — report ATR p50 + the FVG/OB
            # window p50 implied by the per-coin ATR so operators can
            # see whether ATR-scaling is hitting the floor or expanding.
            _setup_cfg = getattr(self.settings.structure, "setup_types", None)
            if atr_pcts and _setup_cfg is not None:
                _atr_sorted = sorted(atr_pcts)
                _atr_p50 = _atr_sorted[max(0, int(0.50 * (len(_atr_sorted) - 1)))]
                _fvg_window_p50 = max(
                    float(_setup_cfg.fvg_min_distance_pct),
                    float(_setup_cfg.fvg_atr_multiplier) * _atr_p50,
                )
                _ob_window_p50 = max(
                    float(_setup_cfg.ob_min_distance_pct),
                    float(_setup_cfg.ob_atr_multiplier) * _atr_p50,
                )
            else:
                _atr_p50 = 0.0
                _fvg_window_p50 = 0.0
                _ob_window_p50 = 0.0
            log.info(
                f"XRAY_CLASSIFY_SUMMARY | total={sum(setup_counts.values())} "
                f"{counts_str} conf_p50={_p50:.2f} conf_p95={_p95:.2f} "
                f"atr_p50={_atr_p50:.3f} window_p50_fvg={_fvg_window_p50:.2f} "
                f"window_p50_ob={_ob_window_p50:.2f} "
                f"trade_dir_long={trade_dir_counts.get('long', 0)} "
                f"trade_dir_short={trade_dir_counts.get('short', 0)} "
                f"counter_count={counter_count} | {ctx()}"
            )
            # R1 direction-fix (2026-05-17) — XRAY_DIRECTION_SPLIT is the
            # dedicated grep target for direction-bias monitoring. It
            # surfaces the gap between regime-label suggested_direction
            # and counter-aware trade_direction. Pre-fix on 2026-05-16
            # suggested was 87% short while trade was 62% short. APEX
            # now reads trade_direction; this line tracks that the brain
            # and APEX see the same balanced view.
            _tdc_total = sum(trade_dir_counts.values())
            _long_pct = (
                trade_dir_counts.get("long", 0) / _tdc_total * 100.0
                if _tdc_total
                else 0.0
            )
            _short_pct = (
                trade_dir_counts.get("short", 0) / _tdc_total * 100.0
                if _tdc_total
                else 0.0
            )
            log.info(
                f"XRAY_DIRECTION_SPLIT | total={_tdc_total} "
                f"trade_dir_long={trade_dir_counts.get('long', 0)} "
                f"trade_dir_short={trade_dir_counts.get('short', 0)} "
                f"trade_dir_na={trade_dir_counts.get('na', 0)} "
                f"long_pct={_long_pct:.1f} short_pct={_short_pct:.1f} "
                f"counter_count={counter_count} | {ctx()}"
            )

        # Phase 11: Run Setup Scanner after analysis (reads FULL cache)
        setup_count = 0
        skip_count = 0
        try:
            if self._setup_scanner is None:
                from src.analysis.structure.setup_scanner import SetupScanner
                self._setup_scanner = SetupScanner(self.settings.structure)
            all_analyses = self._cache.get_all()
            if all_analyses:
                ranked, skip_list = self._setup_scanner.scan(all_analyses, session_context)
                self._cache.set_ranked_setups(ranked, skip_list)
                setup_count = len(ranked)
                skip_count = len(skip_list)
        except Exception as e:
            # Phase 12.1 (lifecycle-logging-audit Gap 1.7-G2): promoted from
            # DEBUG to WARNING. SetupScanner exceptions affect downstream
            # `setups=N skips=N` numbers in XRAY_TICK_SUMMARY — silent
            # failure manifests as `setups=0` with no explanation.
            log.warning(f"XRAY_SCANNER_ERR | err='{str(e)[:120]}' | {ctx()}")

        elapsed_ms = (time.monotonic() - t0) * 1000
        stats = self._cache.get_stats()
        sess_tag = (
            f"session={session_context.current_session}({session_context.session_phase})"
            if session_context
            else "session=n/a"
        )
        # Phase 3: batching is always active (universe sourced from scanner each tick).
        # _batch_start has already been advanced for the NEXT tick in _get_universe(),
        # so the displayed index reflects the upcoming tick's slice.
        if self._full_universe:
            _batches_total = max(
                (len(self._full_universe) + self._batch_size - 1) // max(self._batch_size, 1),
                1,
            )
            _batch_idx = self._batch_start // max(self._batch_size, 1)
            batch_tag = f"batch={_batch_idx}/{_batches_total}"
        else:
            batch_tag = "batch=n/a"

        # Phase 3 (corrected-Layer-1): structured tick summary line. Replaces
        # the legacy XRAY_TICK with XRAY_TICK_SUMMARY — keeps every existing
        # field, adds universe size + sweet-spot drift_ms.
        log.info(
            f"XRAY_TICK_SUMMARY | universe={len(self._full_universe)} "
            f"{batch_tag} symbols={len(universe)} analyzed={analyzed} "
            f"errors={errors} cached={stats['cached_entries']} "
            f"{sess_tag} setups={setup_count} skips={skip_count} "
            f"el={elapsed_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

        # Phase 3 (corrected-Layer-1) + Definitive-fix Phase 1 (2026-04-28):
        # periodic cache health log. Reports cache size, oldest entry age,
        # the fresh-vs-stale breakdown against the cache's own TTL, the
        # configured batch_size for the tick, and tick elapsed_ms so
        # operators can tell at a glance whether the full-sweep-per-tick
        # contract is holding (batch_size >= universe → expected
        # fresh == universe and stale == 0). Uses StructureCache's
        # public accessor so this worker stays decoupled from cache
        # internals.
        try:
            if stats["cached_entries"] > 0:
                oldest_age_s = self._cache.get_oldest_entry_age_seconds()
                breakdown = self._cache.get_freshness_breakdown()
                log.info(
                    f"XRAY_CACHE_HEALTH | size={stats['cached_entries']} "
                    f"fresh={breakdown['fresh']} stale={breakdown['stale']} "
                    f"oldest_age_s={oldest_age_s:.0f} "
                    f"batch_size={self._batch_size} "
                    f"universe={len(self._full_universe)} "
                    f"tick_el_ms={elapsed_ms:.0f} "
                    f"hits={stats['hits']} misses={stats['misses']} "
                    f"hit_rate={stats['hit_rate']:.2f} | {ctx()}"
                )
        except Exception as e:
            log.debug(
                "XRAY_CACHE_HEALTH_SKIP | err='{err}'", err=str(e)[:120],
            )

    def get_setup_score(self, coin: str) -> float | None:
        """Return the most recent setup score for ``coin``, or None if uncached.

        Public accessor consumed by the new ScannerWorker (Phase 6) when
        computing each watch_list coin's composite opportunity score.
        Returns None if the coin has no analysis in the cache, OR if the
        cached analysis has no ``setup_score`` attribute (defensive).
        """
        analysis = self._cache.get(coin)
        if analysis is None:
            return None
        score = getattr(analysis, "setup_score", None)
        if score is None:
            return None
        try:
            return float(score)
        except (TypeError, ValueError):
            return None

    def get_setup_type_confidence(self, coin: str) -> float | None:
        """Return the categorical setup_type_confidence for ``coin``.

        XRAY counter-setup Phase 5b accessor — ScannerWorker uses this to
        downweight counter setups (≈0.35 confidence) vs in-direction
        setups (≈0.55–0.85) in the opportunity_score struct_norm
        component.

        Returns None if the coin has no analysis in the cache. Returns
        0.0 for NONE setups (the dataclass default).
        """
        analysis = self._cache.get(coin)
        if analysis is None:
            return None
        conf = getattr(analysis, "setup_type_confidence", None)
        if conf is None:
            return None
        try:
            return float(conf)
        except (TypeError, ValueError):
            return None

    async def _get_universe(self) -> list[str]:
        """Return this tick's batch of coins from the watch_list.

        Reads ``settings.universe.watch_list`` (50 coins, validated at
        startup) every tick, slices a ``batch_size`` window with wrap-around
        so a full sweep completes in ``ceil(len(watch_list) / batch_size)``
        ticks. With batch_size=25 and 50 coins, that's 2 ticks per sweep.

        The previous scanner-based path is removed under the corrected
        Layer 1 architecture (HR-1, HR-5).
        """
        universe = list(self.settings.universe.watch_list)
        if not universe:
            # Defensive: should not occur — UniverseSettings.__post_init__
            # rejects empty watch_list at startup.
            log.warning(
                f"XRAY_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return []

        # Refresh the in-process universe view each tick. We DO NOT reset
        # batch_start here; preserve the rolling sweep so we don't
        # perpetually re-analyze the first batch_size coins.
        self._full_universe = universe

        # Slice this tick's batch and advance the cursor with wrap-around.
        batch = self._full_universe[self._batch_start:self._batch_start + self._batch_size]
        self._batch_start += self._batch_size
        if self._batch_start >= len(self._full_universe):
            self._batch_start = 0  # wrap around to start of universe

        return batch if batch else self._full_universe[:self._batch_size]

    async def _refresh_htf_views(self, universe: list[str]) -> None:
        """Issue #5: refresh cached higher-TF (H4/D1) structural views for the
        STALE symbols of this batch, using ONE batched kline query per TF.

        Per-symbol staleness is gated by the cache TTL (H4 ~5min, D1 ~1h), so on
        a typical tick D1 is recomputed for almost no symbols and only this
        batch's H4 views refresh — keeping the added per-tick cost bounded.
        Missing/thin HTF data yields a has_data=False view (graceful H1-only)."""
        ttls = {"240": self._mtf_h4_ttl, "D": self._mtf_d1_ttl}
        any_refresh = False
        for tf in self._mtf_timeframes:
            ttl = ttls.get(tf, 300.0)
            stale = [s for s in universe if self._htf_cache.get(s, tf, ttl) is None]
            if not stale:
                continue
            any_refresh = True
            klines_by_sym: dict = {}
            try:
                klines_by_sym = await self._market_repo.get_klines_batch(
                    stale, tf, self._mtf_htf_limit,
                )
            except Exception as e:
                log.warning(
                    f"XRAY_MTF_BATCH_FETCH_FAIL | tf={tf} n={len(stale)} "
                    f"err={str(e)[:80]} | {ctx()}"
                )
            for sym in stale:
                candles = klines_by_sym.get(sym) or []
                # analyze_direction_only never raises and returns has_data=False
                # for thin/missing candles — safe to cache unconditionally.
                self._htf_cache.set(
                    sym, tf,
                    self._engine.analyze_direction_only(sym, candles, timeframe=tf),
                )
        if any_refresh and not self._mtf_init_logged:
            self._mtf_init_logged = True
            log.info(
                f"XRAY_MTF_MULTI_TF_INIT | enabled=True tfs={self._mtf_timeframes} "
                f"h4_ttl={self._mtf_h4_ttl:.0f} d1_ttl={self._mtf_d1_ttl:.0f} "
                f"htf_weight={self._mtf_htf_weight:.2f} limit={self._mtf_htf_limit} "
                f"| {ctx()}"
            )

    def _build_htf_views(self, symbol: str) -> dict | None:
        """Issue #5: assemble {tf: TFStructureView} for a symbol from the cache
        (populated by _refresh_htf_views). Returns None when no usable view is
        cached, so engine.analyze receives None and the scorer stays H1-only."""
        ttls = {"240": self._mtf_h4_ttl, "D": self._mtf_d1_ttl}
        views: dict = {}
        for tf in self._mtf_timeframes:
            v = self._htf_cache.get(symbol, tf, ttls.get(tf, 300.0))
            if v is not None:
                views[tf] = v
        return views or None

    async def _fetch_klines(self, symbol: str) -> list | None:
        """Fetch H1 klines — try trading.db first, fall back to Shadow DB."""
        try:
            candles = await self._market_repo.get_klines(
                symbol, TimeFrame.H1.value, 200,
            )
            if candles and len(candles) >= self.settings.structure.min_candles:
                return candles
        except Exception:
            pass

        # Fall back to Shadow DB kline reader (async, persistent connection)
        if self._shadow_reader:
            try:
                candles = await self._shadow_reader.get_klines(symbol, "60", 200)
                if candles and len(candles) >= self.settings.structure.min_candles:
                    return candles
            except Exception:
                pass

        return None
