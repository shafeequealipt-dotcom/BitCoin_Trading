"""Regime Worker: detects global + per-coin market regimes for the watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins).
- Fires at sweet spot ``settings.workers.sweet_spots.regime_worker`` (default
  ``"1:15"``) within every 5-min window — after signal_worker (1:00).
"""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import WorkerTier
from src.database.connection import DatabaseManager
from src.strategies.models.regime_types import RegimeState
from src.strategies.regime import RegimeDetector
from src.workers.base_worker import SweetSpotWorker

log = get_logger("worker")


class RegimeWorker(SweetSpotWorker):
    """Detects market regime at global and per-coin levels for the watch_list.

    Reads ``config.universe.watch_list`` (50 coins). Fires at sweet spot
    ``settings.workers.sweet_spots.regime_worker`` (default ``"1:15"``).

    Args:
        settings: Application settings.
        db: Database manager.
        detector: RegimeDetector instance.
        scanner: Retained as None-safe legacy injection (not read by tick);
            slated for removal in Phase 7.
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1B
    # Phase 4 — skip tick when LayerManager.is_cycle_active() is False.
    cycle_gated = True

    def __init__(
        self, settings: Settings, db: DatabaseManager,
        detector: RegimeDetector, scanner=None,
    ) -> None:
        super().__init__(
            name="regime_worker",
            sweet_spot=settings.workers.sweet_spots.regime_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.detector = detector
        self._scanner = scanner  # legacy injection; not read by tick()

    async def tick(self) -> None:
        """Detect global regime and per-coin overrides for the watch_list.

        Universe handling (corrected Layer 1, HR-1 / HR-5): direct read of
        ``settings.universe.watch_list`` (50 coins). UniverseSettings.__post_init__
        validates at startup. The first-tick restore filters its DB query
        by the watch_list so historical rows for de-listed coins don't
        repopulate the in-memory cache.
        """
        t0 = time.monotonic()
        universe: list[str] = list(self.settings.universe.watch_list)

        # On first tick, restore per-coin regimes from DB (survives restarts)
        if not self.detector._per_coin_regimes and not getattr(self, '_restored', False):
            self._restored = True
            try:
                from src.strategies.models.regime_types import (
                    REGIME_ACTIVE_CATEGORIES,
                    MarketRegime,
                    RegimeState,
                )
                # HR-1: filter restore to the current universe so departed
                # coins do not re-enter the in-memory cache. SQLite's
                # SQLITE_MAX_VARIABLE_NUMBER default is 999; the universe
                # is ≈ 30 symbols — well within bounds. Empty universe →
                # empty IN clause (which we handle explicitly to avoid
                # SQLite's "syntax error near ')'" on `IN ()`).
                if not universe:
                    log.info(
                        f"REGIME_RESTORE_SKIP | reason=empty_universe | {ctx()}"
                    )
                    rows = []
                else:
                    placeholders = ",".join("?" for _ in universe)
                    # Layer 1 Defect 9 — added volume_ratio and atr_percentile
                    # to the projected columns so the restore no longer
                    # fabricates them. Pre-fix rows return NULL for both
                    # (handled below as "metric not available").
                    rows = await self.db.fetch_all(
                        f"""SELECT symbol, regime, confidence, adx,
                                  choppiness, volume_ratio, atr_percentile
                           FROM coin_regime_history
                           WHERE timestamp > datetime('now', '-30 minutes')
                           AND symbol IN ({placeholders})
                           AND id IN (
                               SELECT MAX(id) FROM coin_regime_history
                               WHERE timestamp > datetime('now', '-30 minutes')
                               AND symbol IN ({placeholders})
                               GROUP BY symbol
                           )""",
                        (*universe, *universe),
                    )
                if rows:
                    _full = 0
                    _partial = 0
                    for row in rows:
                        try:
                            rgm = MarketRegime(row["regime"])
                            conf = float(row["confidence"])
                            adx_val = float(row["adx"] or 0)
                            chop_val = float(row["choppiness"] or 50)
                            # Layer 1 Defect 9 — read persisted values; treat
                            # NULL (pre-fix rows) as "not available" with
                            # neutral defaults documented in the comment at
                            # the INSERT site below.
                            vr_raw = row["volume_ratio"]
                            atrp_raw = row["atr_percentile"]
                            if vr_raw is None or atrp_raw is None:
                                _partial += 1
                                vr_val = (
                                    float(vr_raw) if vr_raw is not None else 1.0
                                )
                                atrp_val = (
                                    float(atrp_raw)
                                    if atrp_raw is not None
                                    else 0.0
                                )
                            else:
                                _full += 1
                                vr_val = float(vr_raw)
                                atrp_val = float(atrp_raw)
                            td = 1 if "up" in row["regime"] else (-1 if "down" in row["regime"] else 0)
                            self.detector._per_coin_regimes[row["symbol"]] = RegimeState(
                                regime=rgm, confidence=conf, adx=adx_val,
                                atr_percentile=atrp_val, choppiness=chop_val,
                                # Issue #3B: a NULL persisted volume_ratio (pre-fix
                                # rows, or rows written while volume was unknown)
                                # restores as "unknown" rather than a fabricated
                                # healthy 1.0 — honest, since those rows never held
                                # a real ratio. vr_val stays neutral for arithmetic.
                                volume_ratio=vr_val, volume_ratio_known=(vr_raw is not None),
                                trend_direction=td,
                                active_strategy_categories=list(
                                    REGIME_ACTIVE_CATEGORIES.get(rgm, [])
                                ),
                            )
                        except (ValueError, KeyError):
                            continue
                    log.info(
                        f"REGIME_RESTORE_OK | "
                        f"loaded={len(self.detector._per_coin_regimes)} "
                        f"full_metrics={_full} partial_metrics={_partial} "
                        f"universe={len(universe)} | {ctx()}"
                    )
            except Exception as e:
                # Phase 12 (post-Layer-1 fix): promoted from DEBUG to
                # WARNING so a failed regime restore on boot appears in
                # default INFO+ logs. A silent restore failure was the
                # exact pattern that hid divergent-regime gaps after a
                # restart.
                # Phase 9 Gap A9 (output-quality obs): include the count
                # already loaded into _per_coin_regimes (if any) and the
                # universe size so the failure is contextualised — was it
                # a partial restore (some loaded, then crashed) or a full
                # init failure?
                _loaded = len(getattr(self.detector, "_per_coin_regimes", {}) or {})
                log.warning(
                    f"REGIME_RESTORE_FAIL | err='{str(e)[:120]}' "
                    f"loaded_so_far={_loaded} universe={len(universe)} | {ctx()}"
                )

        # Global regime (BTC — backward compatible)
        state = await self.detector.detect()

        # Per-coin-authority Phase 1b (2026-05-29): close the BTC per-coin
        # coverage hole. detect_per_coin() explicitly EXCLUDES primary_symbol
        # (see below), and detect() writes only _last_regime — so
        # get_coin_regime('BTCUSDT') always returned None. Harmless while global
        # == BTC, but once per-coin is the sole authority BTC would be the only
        # watch-list coin with no per-coin entry, silently falling back to
        # None/global everywhere. Mirror the SINGLE BTC detection we just ran
        # into the per-coin cache (do NOT add BTC to detect_per_coin's batch —
        # that would detect BTC twice and re-introduce a hysteresis double-advance).
        if self.detector._per_coin_regimes is None:
            self.detector._per_coin_regimes = {}
        self.detector._per_coin_regimes[self.settings.regime.primary_symbol] = state

        await self.db.execute(
            "INSERT INTO regime_history "
            "(symbol, regime, confidence, adx, atr_percentile, choppiness, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                self.settings.regime.primary_symbol,
                state.regime.value,
                state.confidence,
                state.adx,
                state.atr_percentile,
                state.choppiness,
            ),
        )

        log.info(
            f"REGIME_GLOBAL | rgm={state.regime.value} conf={state.confidence:.2f} "
            f"adx={state.adx:.1f} chop={state.choppiness:.1f} "
            f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )
        # Phase 12.1 (lifecycle-logging-audit Gap 1.8-G1): deleted prose
        # duplicate "Regime: {r} ..." — same fields already in REGIME_GLOBAL above.

        # Per-coin regime detection for the watch_list (corrected-Layer-1):
        # scanner is no longer the universe source. The watch_list-driven
        # universe was fetched at the top of tick().
        if universe:
            try:
                # Detect for ALL coins (not just top 10) — uses cached DB klines, no API cost
                coins_to_check = [
                    s for s in universe
                    if s != self.settings.regime.primary_symbol
                ]

                if not coins_to_check:
                    log.warning(
                        f"REGIME_PERCOIN_EMPTY | "
                        f"reason={'scanner_returned_empty' if not universe else 'no_coins_after_primary_filter'} "
                        f"| {ctx()}"
                    )

                if coins_to_check:
                    per_coin = await self.detector.detect_per_coin(coins_to_check)

                    # Merge into existing dict (preserve coins from previous ticks)
                    if not hasattr(self.detector, '_per_coin_regimes') or self.detector._per_coin_regimes is None:
                        self.detector._per_coin_regimes = {}
                    self.detector._per_coin_regimes.update(per_coin)

                    # Count how many diverge from global
                    divergent = sum(
                        1 for r in per_coin.values()
                        if r.regime != state.regime
                    )

                    log.info(
                        f"REGIME_PERCOIN | detected={len(per_coin)} "
                        f"total_cached={len(self.detector._per_coin_regimes)} "
                        f"universe={len(coins_to_check)} divergent={divergent} | {ctx()}"
                    )

                    # Phase 3 (output-quality): per-cycle per-coin regime
                    # distribution. Operators see at a glance whether the
                    # universe is dominated by one regime ("trending_down
                    # for everything" was the pre-fix observation) or has
                    # a healthy mix. Counts the merged cache (not just
                    # this tick's batch) so a stable distribution is
                    # visible across ticks.
                    _dist: dict[str, int] = {}
                    for _rs in self.detector._per_coin_regimes.values():
                        _r = (
                            _rs.regime.value if hasattr(_rs.regime, "value")
                            else str(_rs.regime)
                        )
                        _dist[_r] = _dist.get(_r, 0) + 1
                    if _dist:
                        _dist_str = " ".join(
                            f"{k}={v}" for k, v in sorted(
                                _dist.items(), key=lambda kv: -kv[1],
                            )
                        )
                        log.info(
                            f"REGIME_PERCOIN_SUMMARY | "
                            f"total={sum(_dist.values())} {_dist_str} "
                            f"global={state.regime.value} "
                            f"divergent={divergent} | {ctx()}"
                        )

                    if divergent > 0:
                        # Log which coins diverge for visibility
                        divergent_names = [
                            f"{sym}={r.regime.value}"
                            for sym, r in per_coin.items()
                            if r.regime != state.regime
                        ]
                        log.info(
                            "REGIME_DIVERGE | global={g} divergent=[{coins}] | {ctx}",
                            g=state.regime.value,
                            coins=", ".join(divergent_names[:15]),
                            ctx=ctx(),
                        )

                    # Persist to DB for restart recovery
                    # Layer 1 Defect 9 — added volume_ratio and atr_percentile
                    # to the column list so the restore path can read real
                    # values instead of fabricating 1.0 / 0.0. RegimeState
                    # produces both metrics live; persisting them closes
                    # the post-restart information loss documented in
                    # MASTER_SITUATION_REPORT.md discrepancy 16.
                    for sym, rs in per_coin.items():
                        try:
                            await self.db.execute(
                                """INSERT INTO coin_regime_history
                                   (symbol, regime, confidence, adx,
                                    choppiness, volume_ratio, atr_percentile)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (sym, rs.regime.value, rs.confidence,
                                 rs.adx, rs.choppiness,
                                 # Issue #3B: persist NULL when the ratio is not a
                                 # real measurement, so the restore path reads it
                                 # back as unknown instead of a fabricated 1.0.
                                 # (column is a nullable REAL — no migration.)
                                 (rs.volume_ratio
                                  if getattr(rs, "volume_ratio_known", True)
                                  else None),
                                 rs.atr_percentile),
                            )
                        except Exception as e:
                            # Phase 11 follow-up (post-Layer-1 fix): per-coin
                            # DB persistence failures are now WARNING (was DEBUG)
                            # and use the structured REGIME_PERCOIN_FAIL tag so
                            # operators can correlate "X coins missing in
                            # coin_regime_history after restart" with the
                            # specific failures that produced the gap.
                            log.warning(
                                f"REGIME_PERCOIN_FAIL | sym={sym} "
                                f"err={str(e)[:120]} | {ctx()}"
                            )

            except Exception as e:
                # Phase 11 follow-up: structured tag for the top-level fail.
                log.warning(
                    f"REGIME_PERCOIN_FAIL | scope=detector "
                    f"err={str(e)[:120]} | {ctx()}"
                )

        # Cleanup old regime history (once per ~100 ticks to avoid DB spam)
        if not hasattr(self, '_cleanup_counter'):
            self._cleanup_counter = 0
        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            self._cleanup_counter = 0
            try:
                await self.db.execute(
                    "DELETE FROM coin_regime_history WHERE timestamp < datetime('now', '-24 hours')"
                )
            except Exception as e:
                # Phase 12.1 (lifecycle-logging-audit Gap 1.8-G2): structured
                # tag replacing silent except-pass. coin_regime_history would
                # grow unbounded if cleanup keeps failing silently.
                log.warning(
                    f"REGIME_CLEANUP_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

        # Phase 4 (corrected-Layer-1): tick summary for observability.
        _el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"REGIME_TICK_SUMMARY | universe={len(universe)} "
            f"global={state.regime.value} "
            f"per_coin_size={len(getattr(self.detector, '_per_coin_regimes', {}))} "
            f"el={_el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

    def get_regime(self, coin: str) -> RegimeState | None:
        """Return the most recent RegimeState for ``coin``, or None if uncached.

        Public accessor consumed by Phase 6's new ScannerWorker. Thin
        wrapper around ``RegimeDetector.get_coin_regime`` so the worker
        exposes a stable API even if the detector internals change.
        """
        try:
            return self.detector.get_coin_regime(coin)
        except Exception:
            # Defensive: detector may not have a get_coin_regime; fall back
            # to the in-memory dict directly.
            return getattr(self.detector, "_per_coin_regimes", {}).get(coin)

