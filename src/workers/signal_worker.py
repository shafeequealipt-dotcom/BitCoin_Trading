"""Sentiment aggregation worker: pre-computes sentiment scores for the watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins).
- Fires at sweet spot ``settings.workers.sweet_spots.signal_worker`` (default
  ``"1:00"``) within every 5-min window — after structure_worker (0:45) so
  signal generation reads a fresh structure cache when it needs to.
- Maintains an in-memory ``_signal_cache: dict[symbol -> SignalResult]``
  updated during tick(). Phase 6's ScannerWorker reads from it via
  ``get_signal(coin)`` for the composite opportunity score.
"""

import time

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import Signal, WorkerTier
from src.database.connection import DatabaseManager
from src.intelligence.sentiment.aggregator import SentimentAggregator
from src.intelligence.signals.signal_generator import SignalGenerator
from src.workers.base_worker import SweetSpotWorker

log = get_logger("worker")


class SignalWorker(SweetSpotWorker):
    """Aggregates sentiment data + generates signals for the watch_list.

    Reads ``config.universe.watch_list`` (50 coins). Fires at sweet spot
    ``settings.workers.sweet_spots.signal_worker`` (default ``"1:00"``).

    Args:
        settings: Application settings.
        db: Database manager.
        ta_engine: Unused (kept for backward-compatible constructor).
        aggregator: Sentiment aggregator.
        signal_generator: Signal generator.
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1B
    # Phase 4 — skip tick when LayerManager.is_cycle_active() is False.
    cycle_gated = True

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        ta_engine=None,
        aggregator: SentimentAggregator = None,
        signal_generator: SignalGenerator = None,
    ) -> None:
        super().__init__(
            name="signal_worker",
            sweet_spot=settings.workers.sweet_spots.signal_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.aggregator = aggregator
        self.signal_generator = signal_generator
        self._scanner = None  # legacy injection; not read by tick()
        # Phase 4 (corrected-Layer-1): per-symbol signal cache. Updated each
        # tick from generate_signal() output. Consumed by Phase 6's
        # ScannerWorker via the public get_signal(coin) accessor.
        self._signal_cache: dict[str, Signal] = {}

    async def tick(self) -> None:
        """Aggregate sentiment + generate signals for the full watch_list.

        Universe handling (corrected Layer 1, HR-1 / HR-5): direct read of
        ``settings.universe.watch_list`` (50 coins). UniverseSettings
        validates at startup so an empty watch_list never reaches here.
        """
        t0 = time.monotonic()
        symbols = list(self.settings.universe.watch_list)
        if not symbols:
            log.warning(
                f"SIGNAL_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return

        signals_generated = 0
        best_symbol = ""
        best_confidence = 0.0
        best_signal = ""
        # Phase 3 (Stage-1/2 fix): capture per-signal confidence so the
        # batch-level distribution check (SIG_BATCH_STATS) can surface
        # "every coin got ~0.30" or "spread looks healthy" in one line.
        _confidences: list[float] = []
        # Definitive-fix Phase 5 (2026-04-28): track per-component active
        # counts AND the raw signal-type distribution so we know whether
        # 100% NEUTRAL is upstream (no inputs active for any coin) or
        # downstream (inputs present but direction_score doesn't cross
        # buy_threshold). One INFO line per cycle (SIG_INPUT_AVAILABILITY)
        # makes the gap visible without grepping 50 SIG_GEN_INPUT lines.
        _input_active: dict[str, int] = {
            "fg": 0, "funding": 0, "oi": 0,
        }
        _signal_type_dist: dict[str, int] = {}

        # Fix 3 (sentiment removal, 2026-06-10): the per-coin sentiment
        # aggregation is the origin of the SENT_UNKNOWN_CACHE_HIT spam and is
        # severed from the signal. Run it only when sentiment consumption is
        # explicitly re-enabled (default False); otherwise the pipeline is silent.
        _sent_on = bool(
            getattr(getattr(self.settings, "sentiment", None), "consumption_enabled", False)
        )
        for symbol in symbols:
            try:
                # Sentiment aggregation (only when consumption re-enabled)
                if self.aggregator and _sent_on:
                    try:
                        await self.aggregator.aggregate_for_symbol(symbol)
                    except Exception as e:
                        # Phase 12.1 (lifecycle-logging-audit Gap 1.6-G2):
                        # structured tag replacing tag-less prose.
                        log.warning(
                            f"SIG_SENT_AGG_FAIL | sym={symbol} "
                            f"err='{str(e)[:120]}' | {ctx()}"
                        )

                # Signal generation (combines sentiment scores)
                if self.signal_generator:
                    signal = await self.signal_generator.generate_signal(symbol)
                    signals_generated += 1
                    _confidences.append(float(signal.confidence))
                    # Phase 4 (corrected-Layer-1): cache the signal so
                    # ScannerWorker (Phase 6) can read it via get_signal()
                    # without re-running generate_signal per-coin.
                    self._signal_cache[symbol] = signal

                    # Definitive-fix Phase 5: per-cycle aggregate
                    # accounting. We re-derive component activity from
                    # the per-coin generator inputs by looking at each
                    # numeric component the signal carries (sentiment,
                    # fg, funding, oi). The signal generator emits its
                    # SIG_GEN_INPUT log per coin; here we just count.
                    _comps = getattr(signal, "components", {}) or {}
                    if _comps:
                        # Same activity gates the generator uses; we
                        # only need the boolean, not the score, so a
                        # cheap abs() check per component is enough.
                        # Fix 3 (sentiment removal, 2026-06-10): overall_sentiment
                        # is no longer carried in the signal components, so the
                        # per-cycle activity rollup tracks only the genuine inputs.
                        if int(_comps.get("fear_greed", 50)) != 50:
                            _input_active["fg"] += 1
                        if abs(float(_comps.get("funding_rate", 0.0))) > 0.0:
                            _input_active["funding"] += 1
                        # Five-Fix Follow-Up — Fix 2 (2026-06-10): the key was
                        # renamed oi_change_pct -> oi_change_24h_pct when the
                        # 15m/1h driver windows joined the components. The OI
                        # input counts as active when ANY window moved.
                        if (
                            abs(float(_comps.get("oi_change_24h_pct", 0.0))) > 0.0
                            or abs(float(_comps.get("oi_change_1h_pct", 0.0))) > 0.0
                            or abs(float(_comps.get("oi_change_15m_pct", 0.0))) > 0.0
                        ):
                            _input_active["oi"] += 1
                    _stype = signal.signal_type.value
                    _signal_type_dist[_stype] = _signal_type_dist.get(_stype, 0) + 1

                    # Phase 12.1 (lifecycle-logging-audit Gap 1.6-G1):
                    # demoted from INFO to DEBUG. Per-coin per-cycle = ~50
                    # lines/cycle of unstructured prose. Aggregate
                    # distribution already in SIG_BATCH_STATS.
                    log.debug(
                        "Signal for {s}: {type} (confidence: {c:.2f})",
                        s=symbol, type=signal.signal_type.value, c=signal.confidence,
                    )

                    if signal.confidence > best_confidence:
                        best_confidence = signal.confidence
                        best_symbol = symbol
                        best_signal = signal.signal_type.value

            except Exception as e:
                # Phase 12.1 (lifecycle-logging-audit Gap 1.6-G3):
                # structured tag replacing tag-less prose.
                log.error(
                    f"SIG_GEN_FAIL | sym={symbol} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )

        _sig_el_ms = (time.monotonic() - t0) * 1000
        log.info(
            f"SIG_BATCH | n={signals_generated} coins={len(symbols)} "
            f"strongest={best_symbol or '-'} type={best_signal or '-'} "
            f"conf={best_confidence:.2f} el={_sig_el_ms:.0f}ms "
            f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

        # Phase 4 (corrected-Layer-1): structured tick summary for the
        # corrected architecture. Mean confidence is mathematically
        # 0 when no signals are generated; emit -1 in that case so log
        # consumers can distinguish "no data" from "everything is at 0".
        _mean_conf = (
            sum(_confidences) / len(_confidences) if _confidences else -1.0
        )
        log.info(
            f"SIG_TICK_SUMMARY | universe={len(symbols)} "
            f"signals={signals_generated} mean_conf={_mean_conf:.2f} "
            f"el={_sig_el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

        # Definitive-fix Phase 5 (2026-04-28) — input-availability +
        # signal-type distribution rollup. Tells the operator at a
        # glance whether 100% NEUTRAL is "no inputs active" (fix at
        # source: altdata, sentiment) vs "inputs present but threshold
        # too tight" (fix at calibration: buy_threshold). The
        # _signal_type_dist is sorted descending so the dominant
        # category appears first.
        _n = len(symbols)
        _dist_str = " ".join(
            f"{k.upper()}={v}" for k, v in sorted(
                _signal_type_dist.items(), key=lambda kv: -kv[1],
            )
        ) if _signal_type_dist else "none"
        log.info(
            f"SIG_INPUT_AVAILABILITY | universe={_n} "
            f"fg_active={_input_active['fg']}/{_n} "
            f"funding_active={_input_active['funding']}/{_n} "
            f"oi_active={_input_active['oi']}/{_n} "
            f"types=[{_dist_str}] | {ctx()}"
        )

        # Phase 3 (Stage-1/2 fix): batch-level distribution diagnostic.
        # Post-fix expectation (from the brief): mean ~0.4-0.6,
        # std >= 0.1, min < 0.3, max > 0.6. A distribution still
        # clustered near 0.30 indicates the data-age or volume-surge
        # inputs aren't arriving and confidence has collapsed back to
        # agreement+magnitude alone — the operator sees it in one line
        # instead of having to grep 30 SIG_GEN lines.
        if _confidences:
            n = len(_confidences)
            c_min = min(_confidences)
            c_max = max(_confidences)
            c_mean = sum(_confidences) / n
            c_var = sum((c - c_mean) ** 2 for c in _confidences) / n
            c_std = c_var ** 0.5
            log.info(
                f"SIG_BATCH_STATS | n={n} conf_min={c_min:.3f} "
                f"conf_max={c_max:.3f} conf_mean={c_mean:.3f} "
                f"conf_std={c_std:.3f} | {ctx()}"
            )

    def get_signal(self, coin: str) -> Signal | None:
        """Return the most recent ``Signal`` for ``coin``, or ``None`` if uncached.

        Public accessor consumed by Phase 6's new ScannerWorker for the
        composite opportunity score. The cache is populated each tick at
        sweet spot 1:00; ScannerWorker fires at 4:00 of the same window so
        the read is at most 3 minutes old.
        """
        return self._signal_cache.get(coin)

