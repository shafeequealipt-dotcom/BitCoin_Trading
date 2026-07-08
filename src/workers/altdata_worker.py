"""Alt data worker: fetches Fear & Greed, funding rates, OI, on-chain metrics.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins) for funding/OI.
- Sweet-spot wakeup at ``settings.workers.sweet_spots.altdata.funding_rates``
  (default ``"1:45"``) — funding rates fire every wake-up. OI fires every
  ``open_interest_minutes`` (default 5 min) and F&G every
  ``fear_greed_minutes`` (default 60 min) using internal monotonic
  deadlines so each source has its own cadence even though the worker
  shares a single sweet-spot wake-up.
"""

import asyncio
import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import WorkerTier
from src.database.connection import DatabaseManager
from src.intelligence.altdata.fear_greed import FearGreedClient
from src.intelligence.altdata.funding_rates import FundingRateTracker
from src.intelligence.altdata.onchain import OnChainClient
from src.intelligence.altdata.open_interest import OpenInterestTracker
from src.workers.base_worker import SweetSpotWorker

log = get_logger("worker")


class AltDataWorker(SweetSpotWorker):
    """Fetches alternative data with three independent sub-cadences.

    Graceful degradation: if one source fails, others continue.

    Sub-cadences (each tracked independently via monotonic deadlines):
      - funding_rates: every tick (worker wakes at sweet spot 1:45 once per
        5-min window).
      - open_interest: every ``open_interest_minutes`` (default 5).
      - fear_greed: every ``fear_greed_minutes`` (default 60).
      - onchain: shares the funding cadence (low-frequency upstream).

    Args:
        settings: Application settings.
        db: Database manager.
        fear_greed: Fear & Greed client (optional).
        funding: Funding rate tracker (optional).
        oi_tracker: Open interest tracker (optional).
        onchain: CoinGecko on-chain client (optional).
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1A

    # Tolerance applied to the OI/F&G monotonic deadline gate. Absorbs the
    # ±drift of the master sweet-spot scheduler so a tick that fires a
    # millisecond early doesn't get bounced into the next window — which
    # would silently double the effective sub-cadence (the original
    # 300s-becomes-600s OI bug). Mirrors the value used by
    # ``sweet_spot_scheduler.is_at_sweet_spot`` (1.0s) so jitter assumptions
    # stay consistent across the scheduling stack. Observed master-tick
    # drift is single-digit milliseconds, so 1.0s is a 100×+ safety margin
    # while remaining negligible (<0.4%) against the smallest sub-cadence
    # (300s OI).
    _DEADLINE_JITTER_TOLERANCE_S: float = 1.0

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        fear_greed: FearGreedClient | None,
        funding: FundingRateTracker | None,
        oi_tracker: OpenInterestTracker | None,
        onchain: OnChainClient | None,
    ) -> None:
        super().__init__(
            name="altdata_worker",
            sweet_spot=settings.workers.sweet_spots.altdata.funding_rates,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.fear_greed = fear_greed
        self.funding = funding
        self.oi_tracker = oi_tracker
        self.onchain = onchain
        # Universe is the watch_list — refreshed each tick.
        self.symbols: list[str] = list(settings.universe.watch_list)
        self._scanner = None  # legacy injection; not read by tick()
        # Phase 5 (corrected-Layer-1): per-source monotonic deadlines for
        # cadences that differ from the funding sweet-spot cadence.
        # Initialized to 0 so the first tick fires every source once.
        self._next_oi_mono: float = 0.0
        self._next_fg_mono: float = 0.0
        self._oi_interval_s: float = float(
            settings.workers.sweet_spots.altdata.open_interest_minutes * 60
        )
        self._fg_interval_s: float = float(
            settings.workers.sweet_spots.altdata.fear_greed_minutes * 60
        )
        # Phase 5 (corrected-Layer-1): per-symbol funding-rate cache for
        # Phase 6's ScannerWorker. Updated each funding fetch.
        self._funding_cache: dict[str, float] = {}
        # Per-symbol OI cache. Mirrors funding cache shape so StrategyWorker
        # can build per-symbol altdata views without DB roundtrips. Stores
        # the dicts emitted by ``oi_tracker.fetch_current()`` keyed by symbol.
        self._oi_cache: dict[str, dict] = {}
        # One-time guard for the cold-start OI history backfill — seeds the
        # 24h/1h/15m delta anchors so they read true values from the first
        # fetch rather than 0.0 for ~23h on a fresh deployment.
        self._oi_backfilled: bool = False

    async def tick(self) -> None:
        """Fetch alt data with per-source independent cadences.

        Universe handling (corrected Layer 1, HR-1 / HR-5): direct read of
        ``settings.universe.watch_list`` (50 coins). Each tick fires
        funding (every tick), OI (when ``_next_oi_mono`` deadline passes),
        and F&G (when ``_next_fg_mono`` deadline passes). Onchain is a
        single global metric — fetched alongside funding.
        """
        t0 = time.monotonic()
        universe = list(self.settings.universe.watch_list)
        if not universe:
            log.warning(
                f"ALTDATA_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return
        self.symbols = universe

        # Decide which sub-sources to fire this tick. The deadline gate
        # subtracts a small jitter tolerance so a tick that fires a
        # millisecond early (sweet-spot scheduler can drift ±) still
        # crosses the deadline and fires — without the tolerance the
        # tick would be skipped and the effective cadence would silently
        # double. See ``_DEADLINE_JITTER_TOLERANCE_S`` for the rationale.
        fire_funding = self.funding is not None
        fire_oi = (
            self.oi_tracker is not None
            and t0 >= self._next_oi_mono - self._DEADLINE_JITTER_TOLERANCE_S
        )
        fire_fg = (
            self.fear_greed is not None
            and t0 >= self._next_fg_mono - self._DEADLINE_JITTER_TOLERANCE_S
        )
        fire_onchain = self.onchain is not None  # cheap; piggybacks funding cadence

        # One-time OI history backfill (cold-start seed). Runs once, before
        # the first OI fetch, so the 24h/1h/15m deltas computed inside
        # ``fetch_current`` land on seeded prior snapshots instead of reading
        # 0.0 for the first ~23h. Mirrors Shadow's startup kline backfill.
        # Awaited (not fire-and-forget) so the very first delta is correct;
        # the one-time latency is bounded by a single 200-row page per symbol.
        if fire_oi and not self._oi_backfilled:
            self._oi_backfilled = True
            try:
                await self.oi_tracker.backfill_history(self.symbols)
            except Exception as e:
                log.warning(f"OI_BACKFILL_ERROR | err='{str(e)[:120]}' | {ctx()}")

        # Phase 9 (post-Layer-1 fix). Per-feed timing: wrap each fetch in
        # a measurement helper so the gather yields (label, el_ms,
        # result_or_exc) tuples. The previous implementation reported the
        # gather wall-clock for every source, masking which feed actually
        # contributed to slow ticks. Now ALTDATA_TICK_DONE shows per-feed
        # latencies so operators can see which REST endpoint is the
        # bottleneck.
        async def _timed(label: str, coro):
            t_sub = time.monotonic()
            try:
                result = await coro
                return (label, (time.monotonic() - t_sub) * 1000.0, result, None)
            except Exception as e:
                return (label, (time.monotonic() - t_sub) * 1000.0, None, e)

        tasks: list = []
        if fire_funding:
            tasks.append(_timed("funding", self._fetch_funding_rates()))
        if fire_oi:
            tasks.append(_timed("oi", self._fetch_open_interest()))
        if fire_fg:
            tasks.append(_timed("fear_greed", self._fetch_fear_greed()))
        if fire_onchain:
            tasks.append(_timed("onchain", self._fetch_onchain()))

        if not tasks:
            # Phase 12.1 (lifecycle-logging-audit Gap 1.3-G1): structured
            # tag for grep-ability. Replaces tag-less prose.
            log.warning(
                f"ALTDATA_NO_SOURCES_DUE | reason=all_disabled "
                f"funding_enabled={self.funding is not None} "
                f"oi_enabled={self.oi_tracker is not None} "
                f"fg_enabled={self.fear_greed is not None} "
                f"onchain_enabled={self.onchain is not None} | {ctx()}"
            )
            return

        # ``return_exceptions=True`` is unnecessary now: ``_timed`` swallows
        # the exception and returns it as the 4th tuple element, so gather
        # gets a uniform success-shape per task.
        results = await asyncio.gather(*tasks)

        fg_val = None
        funding_count = 0
        oi_count = 0
        funding_el_ms = 0.0
        oi_el_ms = 0.0
        fg_el_ms = 0.0
        onchain_el_ms = 0.0

        gather_el_ms = (time.monotonic() - t0) * 1000

        for label, sub_el_ms, result, err in results:
            if err is not None:
                log.warning(
                    f"ALTDATA_SOURCE_FAIL | src={label} el_ms={sub_el_ms:.0f} "
                    f"err={str(err)[:120]} | {ctx()}"
                )
                # Record the elapsed even on failure so the per-feed
                # summary attributes the time correctly.
                if label == "funding":
                    funding_el_ms = sub_el_ms
                elif label == "oi":
                    oi_el_ms = sub_el_ms
                elif label == "fear_greed":
                    fg_el_ms = sub_el_ms
                elif label == "onchain":
                    onchain_el_ms = sub_el_ms
                continue
            if label == "fear_greed" and result:
                fg_val = result.value
                fg_el_ms = sub_el_ms
            elif label == "funding" and isinstance(result, list):
                funding_count = len(result)
                funding_el_ms = sub_el_ms
                # Phase 5 (corrected-Layer-1): populate per-symbol funding
                # cache for Phase 6's ScannerWorker.
                for fr in result:
                    sym = getattr(fr, "symbol", None)
                    rate = getattr(fr, "funding_rate", None)
                    if sym and rate is not None:
                        try:
                            self._funding_cache[sym] = float(rate)
                        except (TypeError, ValueError):
                            continue
            elif label == "oi" and isinstance(result, list):
                oi_count = len(result)
                oi_el_ms = sub_el_ms
                # Populate per-symbol OI cache so StrategyWorker can build
                # flat per-symbol altdata views (D2/F3/G3 strategies read
                # ``altdata.get("oi_change_24h_pct", 0)``).
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    sym = item.get("symbol")
                    if not sym:
                        continue
                    try:
                        self._oi_cache[sym] = {
                            "change_24h_pct": float(item.get("change_24h_pct", 0.0) or 0.0),
                            "open_interest": float(item.get("open_interest", 0.0) or 0.0),
                        }
                    except (TypeError, ValueError):
                        continue
            elif label == "onchain":
                onchain_el_ms = sub_el_ms

        # Advance deadlines for sources that fired — each on its own
        # independent cadence regardless of whether the fetch succeeded
        # (a transient failure shouldn't double-fire).
        #
        # Anchor the next deadline to ``t0`` (tick start), NOT to the
        # post-fetch monotonic now. Using post-fetch time additively pushed
        # the deadline forward by the fetch latency on every fire (~9 s for
        # OI), so within a few cycles the deadline drifted past the next
        # master-tick boundary and the gate skipped every other tick — the
        # observed 300s-becomes-600s OI bug. Anchoring to ``t0`` puts the
        # deadline on a clean ``t0 + N × interval`` grid, which the
        # sweet-spot master tick (also wall-clock-anchored) will cross
        # reliably every interval.
        if fire_oi:
            self._next_oi_mono = t0 + self._oi_interval_s
        if fire_fg:
            self._next_fg_mono = t0 + self._fg_interval_s

        # Phase 5 (corrected-Layer-1): per-source structured tick summaries.
        if fire_funding:
            log.info(
                f"ALTDATA_FUNDING_TICK | universe={len(universe)} "
                f"fetched={funding_count} cached_size={len(self._funding_cache)} "
                f"el={funding_el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
            )
        # Compute time-to-next-fire honestly: distance from "now" to the
        # newly-set deadline. The legacy ``next_in_s`` field used to print
        # the configured interval, which masked the cadence-drift bug — an
        # operator reading ``next_in_s=300`` would assume the worker would
        # fire OI in 300s when in fact the deadline had drifted and the
        # next fire was 600s away. Now ``next_in_s`` is the actual
        # remaining time and ``interval_s`` exposes the configured target,
        # so both values can be cross-checked.
        _log_now_mono = time.monotonic()
        if fire_oi:
            _oi_next_in_s = max(0.0, self._next_oi_mono - _log_now_mono)
            log.info(
                f"ALTDATA_OI_TICK | universe={len(universe)} "
                f"fetched={oi_count} cached_size={len(self._oi_cache)} "
                f"el={oi_el_ms:.0f}ms "
                f"next_in_s={_oi_next_in_s:.0f} "
                f"interval_s={self._oi_interval_s:.0f} | {ctx()}"
            )
        if fire_fg:
            _fg_next_in_s = max(0.0, self._next_fg_mono - _log_now_mono)
            log.info(
                f"ALTDATA_FG_TICK | value={fg_val} el={fg_el_ms:.0f}ms "
                f"next_in_s={_fg_next_in_s:.0f} "
                f"interval_s={self._fg_interval_s:.0f} | {ctx()}"
            )

        # Legacy ALTDATA aggregate line preserved for any existing log parsers.
        log.info(
            f"ALTDATA | fg={fg_val} funding={funding_count} oi={oi_count} "
            f"el={gather_el_ms:.0f}ms | {ctx()}"
        )

        # Phase 9 (post-Layer-1 fix). ALTDATA_TICK_DONE is the new
        # operator-facing aggregate. Carries per-feed elapsed_ms so a
        # slow tick can be instantly attributed to the offending feed
        # (e.g. Bybit OI hangs while funding+F&G are fast).
        ran = ",".join(
            label
            for label, fired in (
                ("funding", fire_funding),
                ("oi", fire_oi),
                ("fear_greed", fire_fg),
                ("onchain", fire_onchain),
            )
            if fired
        )
        log.info(
            f"ALTDATA_TICK_DONE | funding_ms={funding_el_ms:.0f} "
            f"oi_ms={oi_el_ms:.0f} fg_ms={fg_el_ms:.0f} "
            f"onchain_ms={onchain_el_ms:.0f} total_ms={gather_el_ms:.0f} "
            f"ran=[{ran}] | {ctx()}"
        )

    def get_funding(self, coin: str) -> float | None:
        """Return the most recent funding rate for ``coin``, or None if uncached.

        Public accessor consumed by Phase 6's new ScannerWorker for the
        composite opportunity score. Cache populated each funding fetch
        (every 5-min wake-up at sweet spot 1:45 by default).
        """
        return self._funding_cache.get(coin)

    def get_oi(self, coin: str) -> dict | None:
        """Return the most recent OI snapshot for ``coin``, or None if uncached.

        Shape: ``{"change_24h_pct": float, "open_interest": float}``.
        Populated each OI fetch (cadence per
        ``settings.workers.sweet_spots.altdata.open_interest_minutes``).
        Consumed by StrategyWorker to build per-symbol altdata for L1
        strategies (D2/F3/G3 read ``oi_change_24h_pct`` directly).
        """
        return self._oi_cache.get(coin)


    async def _fetch_fear_greed(self):
        return await self.fear_greed.fetch_current()

    async def _fetch_funding_rates(self):
        return await self.funding.fetch_current_rates(self.symbols)

    async def _fetch_open_interest(self):
        return await self.oi_tracker.fetch_current(self.symbols)

    async def _fetch_onchain(self):
        return await self.onchain.get_global_metrics()
