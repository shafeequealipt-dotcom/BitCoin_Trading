"""Strategy Worker: runs the full Layer 1-4 pipeline for the watch_list.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md):
- Operates on ``config.universe.watch_list`` (50 coins).
- Fires at sweet spot ``settings.workers.sweet_spots.strategy_worker``
  (default ``"1:30"``) within every 5-min window — last in the chain
  before ScannerWorker reads everyone's outputs at 4:00.

Flow: PnL check -> get universe -> get regime -> prefetch data ->
      Layer 1 (scan) -> Layer 2 (score) -> Layer 3 (ensemble) ->
      apply restrictions -> Layer 4 (Rule Engine — uses cached Claude plan)
"""

from src.analysis.engine import TAEngine
from src.config.settings import EntryVolumeGateSettings, FlipTPSettings, Settings
from src.core.entry_volume_gate import evaluate_entry_volume_gate
from src.core.flip_tp_capper import (
    METHOD_DISABLED,
    METHOD_STRUCTURAL_KEPT,
    compute_capped_flip_tp,
)
from src.core.log_context import ctx, new_strategy_id
from src.core.logging import get_logger
from src.core.trade_plan import TradePlan
from src.core.types import AlertLevel, OrderStatus, OrderType, Side, TimeFrame, WorkerTier
from src.core.utils import format_price
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.strategies.ensemble import EnsembleVoter
from src.strategies.pnl_manager import DailyPnLManager
from src.strategies.models.regime_types import MarketRegime, RegimeState
from src.strategies.regime import RegimeDetector
from src.strategies.registry import StrategyRegistry
from src.strategies.scanner import MarketScanner
from src.strategies.scorer import TradeScorer
from src.workers.base_worker import SweetSpotWorker

log = get_logger("worker")


def compute_volatility_scaled_stop(
    *,
    sl: float,
    current_price: float,
    direction: str,
    size_usd: float,
    recommended_sl_pct: float,
    reference_stop_pct: float,
    max_cap_pct: float,
) -> tuple[float, float, float, float]:
    """Pure Fix-7 (2026-06-10) math: widen the entry stop to the coin's
    volatility target and apply the tighten-only size haircut.

    The target stop distance is the profiler's ``recommended_sl_pct`` floored at
    ``reference_stop_pct`` (so a quiet coin keeps the existing minimum — the stop
    is never tightened below it) and capped at ``max_cap_pct``. If the placed
    stop is already at/beyond the target, it is unchanged. The size is then cut
    so the dollar risk AT the (possibly wider) stop equals what it would have
    been at the reference distance with the original size — i.e.
    ``size * leverage * stop_fraction`` is held at the reference budget. The cut
    is tighten-only (never scales size up), so the per-trade margin cap still
    binds.

    ``recommended_sl_pct <= 0`` means "no profiler input" -> the target defaults
    to the reference floor (no widening, no haircut).

    Returns ``(new_sl, new_size_usd, target_pct, final_pct)``.
    """
    if current_price <= 0 or sl <= 0:
        return sl, size_usd, 0.0, 0.0
    target_pct = recommended_sl_pct if recommended_sl_pct > 0.0 else reference_stop_pct
    target_pct = max(reference_stop_pct, min(target_pct, max_cap_pct))
    placed_pct = abs(current_price - sl) / current_price * 100.0
    new_sl = sl
    if placed_pct + 1e-9 < target_pct:
        if direction == "Buy":
            new_sl = round(current_price * (1.0 - target_pct / 100.0), 8)
        else:
            new_sl = round(current_price * (1.0 + target_pct / 100.0), 8)
    final_pct = abs(current_price - new_sl) / current_price * 100.0
    new_size = size_usd
    if final_pct > reference_stop_pct > 0.0:
        new_size = size_usd * (reference_stop_pct / final_pct)
    return new_sl, new_size, target_pct, final_pct


class StrategyWorker(SweetSpotWorker):
    """Main strategy execution worker running Layers 1-4.

    Args:
        settings: Application settings.
        db: Database manager.
        registry: Strategy registry with all registered strategies.
        scanner: MarketScanner for active universe.
        regime_detector: For current market regime.
        scorer: Trade scorer (Layer 2).
        ensemble: Ensemble voter (Layer 3).
        pnl_manager: Daily PnL manager for restrictions.
        ta_engine: For technical analysis data.
        market_repo: For fetching klines from DB.
        services: Dict of all system services (for rule engine, layer manager, etc.).
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1C
    # Phase 4 — skip tick when LayerManager.is_cycle_active() is False.
    cycle_gated = True

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        registry: StrategyRegistry,
        scanner: MarketScanner,
        regime_detector: RegimeDetector,
        scorer: TradeScorer,
        ensemble: EnsembleVoter,
        pnl_manager: DailyPnLManager,
        ta_engine: TAEngine,
        market_repo: MarketRepository,
        services: dict | None = None,
    ) -> None:
        super().__init__(
            name="strategy_worker",
            sweet_spot=settings.workers.sweet_spots.strategy_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.registry = registry
        self.scanner = scanner  # legacy injection; not used for universe lookup
        self.regime_detector = regime_detector
        self.scorer = scorer
        self.ensemble = ensemble
        self.pnl_manager = pnl_manager
        self.ta_engine = ta_engine
        self.market_repo = market_repo
        self.services = services or {}

        # Rolling timing history for 10-tick STRAT_HEALTH aggregate (observability only)
        self._tick_times: list[float] = []
        # Phase 4 (corrected-Layer-1): per-symbol score cache. Populated
        # after Layer 2 each tick. Phase 6's ScannerWorker reads via the
        # public get_score(coin) accessor for the composite opportunity
        # score. Stored as float total_score, keyed by symbol.
        self._score_cache: dict[str, float] = {}
        # Layer 1 restructure Phase 3 — track previous consensus category
        # per symbol so STRAT_CONSENSUS_CHANGE only fires on transitions.
        self._prev_consensus: dict[str, str] = {}

        # Layer 4 (2026-05-22) Rule 16 self-check — guard the aim from
        # silent regression. The Layer 4 fix is a TRUTH-FIX: it changes
        # only what the brain SEES (CALL_A prompt enrichment); it must
        # NOT introduce any code path that mutates size based on a
        # consensus count. A future refactor that adds e.g.
        # `if supporting_count > 5: size_usd *= 0.5` would betray the
        # aim. This boot check regex-scans strategy_worker.py and
        # layer_manager.py for the forbidden pattern (a non-comment
        # line that conditions on consensus count AND mutates size).
        # Loud-on-violation: BOOT_L4_HARDCODED_CAP_DETECTED at ERROR
        # with the offending file:line. Quiet on healthy:
        # BOOT_L4_NO_HARDCODED_CAP_OK.
        self._l4_boot_check_no_hardcoded_cap()

        # P0-2 fix (2026-05-22) — boot sentinel confirming the
        # direction-decision authority. Operator queries this single
        # line to verify high-conviction protection is active and the
        # canonical decision log is DIRECTION_DECISION (replacing the
        # pre-P0-2 dual APEX_DIR_LOCK + XRAY_DIR_FLIP pairing).
        _risk = getattr(settings, "risk", None)
        _p02_enabled = bool(getattr(
            _risk, "xray_high_conviction_protection_enabled", True,
        )) if _risk is not None else True
        _p02_flip_threshold = float(getattr(
            _risk, "xray_dir_flip_threshold_ratio", 3.0,
        )) if _risk is not None else 3.0
        log.info(
            f"P0_2_SENTINEL | high_conviction_protection={_p02_enabled} "
            f"flip_threshold={_p02_flip_threshold:.2f} "
            f"dual_logging=removed "
            f"canonical_event=DIRECTION_DECISION | no_ctx"
        )

        # X-RAY Direction-Flip Switch boot sentinel
        # (IMPLEMENT_XRAY_FLIP_SWITCH, 2026-05-25). Operator queries this
        # single line to confirm whether X-RAY may REVERSE trade direction
        # this session. When xray_dir_flip_enabled is False the
        # low-conviction structural-RR flip is gated off and the sanctioned
        # brain-then-APEX direction executes; the high-conviction veto is
        # preserved (gates=reversal_only). Reuses _risk and
        # _p02_flip_threshold computed for P0_2_SENTINEL above.
        _xray_flip_enabled = bool(getattr(
            _risk, "xray_dir_flip_enabled", False,
        )) if _risk is not None else False
        log.info(
            f"XRAY_FLIP_SWITCH_SENTINEL | "
            f"xray_dir_flip_enabled={_xray_flip_enabled} "
            f"flip_threshold={_p02_flip_threshold:.2f} "
            f"gates=reversal_only veto_preserved=true | no_ctx"
        )

        # X-RAY Trade-Suppression Switch boot sentinel
        # (IMPLEMENT_XRAY_SUPPRESS_SWITCH, 2026-05-25). Operator queries
        # this single line to confirm whether X-RAY may SUPPRESS (block /
        # skip) trades this session. When xray_trade_suppression_enabled is
        # False (operator default) all five X-RAY trade-blocks (xray_skip,
        # xray_conflict, xray_veto_high_conviction, xray_dir_block,
        # xray_dir_flip_blocked) are converted to XRAY_BOOKLOG journal lines
        # and the brain-then-APEX direction executes; X-RAY scoring /
        # grading / selection / structural analysis are unaffected. When
        # True, X-RAY blocks exactly as before. booklog=on iff suppression
        # is off. Reuses _risk computed for P0_2_SENTINEL above.
        _xray_suppression_enabled = bool(getattr(
            _risk, "xray_trade_suppression_enabled", False,
        )) if _risk is not None else False
        log.info(
            f"XRAY_SUPPRESS_SWITCH_SENTINEL | "
            f"xray_trade_suppression_enabled={_xray_suppression_enabled} "
            f"suppression_active={_xray_suppression_enabled} "
            f"booklog={'off' if _xray_suppression_enabled else 'on'} "
            f"analysis=preserved gates=all_5_xray_trade_blocks | no_ctx"
        )

        # Issue 4 (CALL_A exploit/fetch, 2026-06-05) — strategy-firing honesty
        # boot sentinel. Confirms the Layer-funnel drop check is active (proves
        # no coin with a raw signal is silently shown 0-fired) and the loaded
        # consensus-evidence freshness threshold used to flag a stale lean on the
        # zero-fired line.
        _brain_cfg = getattr(settings, "brain", None)
        _freshness_s = int(getattr(
            _brain_cfg, "consensus_freshness_seconds", 360,
        )) if _brain_cfg is not None else 360
        log.info(
            f"STRAT_FIRING_HONESTY_CONFIG | funnel_check=on "
            f"consensus_freshness_seconds={_freshness_s} "
            f"| 0-fired lines surface the two-sided poll lean (not 'no-signal') "
            f"| no_ctx"
        )

    def _l4_boot_check_no_hardcoded_cap(self) -> None:
        """Regex-scan the trade-dispatch source files for the Layer 4
        anti-pattern (size mutated by consensus count). Fires once at
        worker boot; failure is non-fatal."""
        try:
            import re
            from pathlib import Path
            # Two files where any hardcoded cap would have to live:
            # the brain-trade execution path and the layer manager.
            _src_root = Path(__file__).resolve().parent.parent
            _targets = [
                _src_root / "workers" / "strategy_worker.py",
                _src_root / "core" / "layer_manager.py",
            ]
            # Forbidden pattern: same non-comment line contains both a
            # consensus-count identifier (supporting_count / agreeing /
            # opposing) AND a sizing mutation (size_usd assignment,
            # *=, +=). Lines that only LOG both values are filtered
            # by skipping log.info / log.warning / log.error / log.debug
            # prefixes and string interpolations.
            _consensus_re = re.compile(
                r"\b(supporting_count|agreeing|opposing)\b"
            )
            _size_mutation_re = re.compile(
                r"(size_usd\s*[*+\-]?=|qty\s*[*+\-]?=|"
                r"size\s*[*+\-]?=\s*[^=]|_size_mult\s*=)"
            )
            _violations: list[str] = []
            for path in _targets:
                if not path.exists():
                    continue
                try:
                    for lineno, raw in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1,
                    ):
                        stripped = raw.lstrip()
                        # Skip pure-comment lines
                        if stripped.startswith("#"):
                            continue
                        # Skip docstrings + log-formatting lines (they
                        # are observability, not control flow)
                        if 'log.' in stripped:
                            continue
                        if '"""' in stripped or "'''" in stripped:
                            continue
                        if not _consensus_re.search(raw):
                            continue
                        if not _size_mutation_re.search(raw):
                            continue
                        # Both consensus-count and size-mutation on the
                        # same non-comment non-log line — investigate.
                        _violations.append(
                            f"{path.name}:{lineno}:{stripped[:120]}"
                        )
                except Exception as _read_e:
                    log.debug(
                        f"BOOT_L4_NO_HARDCODED_CAP_READ_FAIL | "
                        f"path={path.name} err='{str(_read_e)[:80]}'"
                    )
                    continue
            if _violations:
                log.error(
                    f"BOOT_L4_HARDCODED_CAP_DETECTED | "
                    f"count={len(_violations)} samples="
                    f"{_violations[:3]} | {ctx()}"
                )
            else:
                log.info(
                    f"BOOT_L4_NO_HARDCODED_CAP_OK | "
                    f"scanned={[p.name for p in _targets]} "
                    f"violations=0 | {ctx()}"
                )
        except Exception as _e:
            log.debug(
                f"BOOT_L4_NO_HARDCODED_CAP_CHECK_FAIL | "
                f"err='{str(_e)[:120]}'"
            )

    async def tick(self) -> None:
        """Execute the full Layer 1-4 pipeline."""
        import time as _time
        _cycle_t0 = _time.time()
        _sid = new_strategy_id()

        # Per-section wall-clock timings (ms). Populated as we go; surfaced in
        # STRAT_CYCLE_DONE at the end of the method. Early returns skip the
        # final log emission by design (matches pre-observability contract).
        _section_ms: dict[str, float] = {
            "gate": 0.0, "prefetch": 0.0, "l1": 0.0,
            "l2": 0.0, "l3": 0.0, "l4": 0.0,
        }

        # 1. Refresh + check PnL manager
        # Drive the daily PnL update from the strategy cycle so the mode
        # ladder, drawdown tracking, equity reconciliation and PNL_DAILY log
        # run autonomously. Previously update() only fired on Telegram
        # dashboard queries, leaving the ladder stale during live trading
        # (forensic gap "PNL_DAILY NOT FOUND in 24h").
        try:
            await self.pnl_manager.update()
        except Exception as _e:
            log.warning(
                f"STRAT_PNL_UPDATE_FAIL | err='{str(_e)[:120]}' | {ctx()}"
            )
        _t = _time.time()
        can_trade, reason = self.pnl_manager.can_trade()
        _section_ms["gate"] = (_time.time() - _t) * 1000
        # Phase 11 follow-up (post-Layer-1 fix): pnl + losses/wins on the
        # gate decision line so an operator reading the log knows WHY the
        # gate halted (or, if it's healthy, what the current numbers
        # are). Pulled defensively via getattr so a stale or missing
        # field doesn't crash a tick — strategy ticks are critical-path.
        _gate_pnl = float(getattr(self.pnl_manager, "current_pnl_pct", 0.0) or 0.0)
        _gate_losses = int(getattr(self.pnl_manager, "_losses_today", 0) or 0)
        _gate_wins = int(getattr(self.pnl_manager, "_wins_today", 0) or 0)
        log.info(
            f"STRAT_PNL_GATE | halted={'Y' if not can_trade else 'N'} "
            f"rsn={reason or 'ok'} pnl_pct={_gate_pnl:+.2f} "
            f"wins={_gate_wins} losses={_gate_losses} "
            f"el={_section_ms['gate']:.0f}ms | {ctx()}"
        )
        if not can_trade:
            log.info("Strategy worker: trading paused -- {reason}", reason=reason)
            return

        # Phase 6 (P0-5 Fix C): if the kline worker has just observed a
        # 100% fetch collapse, skip this entire TA cycle. The circuit is
        # open for 30 s — long enough for the next fetch to either
        # recover or stay collapsed (in which case staying out is the
        # right move). Without this, strategies vote on stale data and
        # produce signals operators have to clean up after.
        kline_worker = self.services.get("kline_worker") if hasattr(self, "services") else None
        if kline_worker is None:
            kline_worker = getattr(self, "_kline_worker", None)
        if kline_worker is not None and hasattr(kline_worker, "is_circuit_open"):
            try:
                if kline_worker.is_circuit_open():
                    log.warning(
                        f"STRAT_SKIP_CIRCUIT | rsn=kline_circuit_open | {ctx()}"
                    )
                    return
            except Exception:
                pass

        # 2. Get universe (corrected Layer 1, HR-1 / HR-5): direct read of
        # settings.universe.watch_list (50 coins). Validated at startup by
        # UniverseSettings.__post_init__ so an empty list never reaches
        # here under normal config; the defensive guard remains.
        universe = list(self.settings.universe.watch_list)
        if not universe:
            log.warning(
                f"STRAT_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return

        # 3. Get current regime (global + per-coin overrides)
        # Per-coin-authority Phase 1a (2026-05-29): READ RegimeWorker's cached
        # global detection instead of calling detect() again here. RegimeWorker
        # (sweet-spot 1:15, before this worker at 1:30) is now the SOLE detect()
        # caller per window. A second detect() on the shared detector advanced
        # BTC's hysteresis counter a second time in the same window — confirming
        # a regime change in ONE window instead of the configured two — and
        # re-ran ~150-200ms of BTC H1 TA on a CPU-starved box every cycle.
        # Boot-race only: if RegimeWorker's first tick hasn't landed, fall
        # through to a single guarded detect() so the first cycle has real data.
        regime = self.regime_detector.get_last_regime()
        if regime is None:
            regime = await self.regime_detector.detect()
        coin_regimes = getattr(self.regime_detector, '_per_coin_regimes', {})

        # Per-cycle regime-distribution diagnostic. Answers "why 95% Buy
        # trades?" in one line — when up=28 down=2, the bias is the universe,
        # not the prompt.
        _rdist = {"up": 0, "down": 0, "ranging": 0, "volatile": 0, "dead": 0, "other": 0}
        for _sym, _cr in coin_regimes.items():
            _r = (
                _cr.regime.value if hasattr(_cr.regime, "value") else str(_cr.regime)
            ).lower()
            if "up" in _r:
                _rdist["up"] += 1
            elif "down" in _r:
                _rdist["down"] += 1
            elif "rang" in _r:
                _rdist["ranging"] += 1
            elif "volat" in _r:
                _rdist["volatile"] += 1
            elif "dead" in _r:
                _rdist["dead"] += 1
            else:
                _rdist["other"] += 1
        log.info(
            f"STRAT_REGIME_DIST | up={_rdist['up']} down={_rdist['down']} "
            f"ranging={_rdist['ranging']} volatile={_rdist['volatile']} "
            f"dead={_rdist['dead']} other={_rdist['other']} "
            f"total={len(coin_regimes)} global={regime.regime.value} | {ctx()}"
        )

        # 4. Per-coin-authority Phase 3 (2026-05-29): the per-coin scan loop
        # below filters each coin by its OWN regime (symbol_strategies =
        # get_active_for_regime(symbol_regime)) — that loop is the SOLE roster
        # authority. The old global early-return here — active_strategies =
        # get_active_for_regime(GLOBAL regime); if empty: return — gated the
        # ENTIRE 50-coin universe on one coin's (BTC's) regime. It was inert only
        # because 'kickstart' sits in every regime's category list, but it was a
        # real universe-wide gate keyed on the global label, which the per-coin
        # mandate forbids. Removed; a genuinely empty cycle is still handled by
        # the 'no raw_signals' guard after the loop. Keep only a cheap fail-loud
        # check that the registry has ANY enabled strategy (a true misconfig,
        # not a per-coin decision).
        if not self.registry.get_enabled():
            log.warning(f"STRAT_NO_ENABLED_STRATEGIES | registry has 0 enabled | {ctx()}")
            return

        # 5. Pre-fetch data for all coins — ONE batch DB query, then serial TA
        # (TA is numpy-bound, so asyncio parallelism wouldn't help; the win is
        # collapsing 33 DB lock hops into 1).
        _t = _time.time()
        candles_map: dict[str, list] = {}
        ta_map: dict[str, dict] = {}
        _slow_coins: list[tuple[str, float]] = []

        _t_db = _time.time()
        try:
            all_klines = await self.market_repo.get_klines_batch(
                list(universe), TimeFrame.M5.value, 200,
            )
        except Exception as e:
            log.warning(
                f"STRAT_PREFETCH_DB_FAIL | err='{str(e)[:150]}' coins={len(universe)} | {ctx()}"
            )
            all_klines = {}
        _db_ms = (_time.time() - _t_db) * 1000

        # H1 kline batch — same universe, same depth. Feeds the H1 TA
        # pre-population loop below so the strategist's market_data section
        # (which requests H1 TA per coin) hits a warm cache instead of
        # computing ~30 serial H1 TAs from cold — the Call-A `market_data=52s`
        # bottleneck. Failure here is non-fatal: strategist falls through to
        # cold TA per coin as before.
        _t_db_h1 = _time.time()
        try:
            all_klines_h1 = await self.market_repo.get_klines_batch(
                list(universe), TimeFrame.H1.value, 200,
            )
        except Exception as e:
            log.debug(
                f"STRAT_PREFETCH_DB_H1_FAIL | err='{str(e)[:120]}' coins={len(universe)} | {ctx()}"
            )
            all_klines_h1 = {}
        _db_h1_ms = (_time.time() - _t_db_h1) * 1000

        _t_ta = _time.time()
        # Phase 6 (P0-5 Fix C): skip TA on symbols whose newest kline is
        # >5 min stale. Running TA on stale candles produces signals that
        # vote with the past — particularly dangerous on volatile coins
        # where 5+ min of price action has materially changed direction.
        from datetime import datetime, timezone as _tz
        # Candidate-Block Data Integrity Fix — Issue 2 (2026-06-09): the Layer 1
        # input-gate thresholds are now centralized in [strategy_engine] (were
        # hardcoded here). Defensive getattr keeps the prior defaults for any
        # caller with a partial settings stub.
        _se_cfg = getattr(self.settings, "strategy_engine", None)
        _kline_max_age_s = float(
            getattr(_se_cfg, "kline_max_age_seconds", 300.0)
        )
        _min_kline_count = int(getattr(_se_cfg, "min_kline_count", 50))
        if not getattr(self, "_l1_gates_sentinel_logged", False):
            log.info(
                f"BOOT_STRAT_L1_GATES | "
                f"kline_max_age_seconds={_kline_max_age_s:.0f} "
                f"min_kline_count={_min_kline_count} | {ctx()}"
            )
            self._l1_gates_sentinel_logged = True
        _stale_count = 0
        _short_hist_count = 0
        _short_hist_syms: list[str] = []
        # Phase 9 Gap A7 (output-quality obs): accumulate per-coin staleness
        # so we can emit one rollup STRAT_SKIP_STALE_AGG instead of N
        # individual lines flooding the log when many symbols stale at once.
        _stale_ages: list[float] = []
        _stale_syms: list[str] = []
        # Phase 9 Gap A8: count fast vs slow TA per cycle for aggregate log.
        _ta_fast = 0
        _ta_slow = 0
        _ta_max_ms = 0.0
        for symbol, klines in all_klines.items():
            try:
                # Per-symbol staleness gate.
                if klines:
                    _newest_ts = getattr(klines[-1], "timestamp", None)
                    if _newest_ts is not None:
                        try:
                            _now_dt = datetime.now(_tz.utc)
                            _age_s = (_now_dt - _newest_ts).total_seconds()
                            if _age_s > _kline_max_age_s:
                                log.warning(
                                    f"STRAT_SKIP_STALE | sym={symbol} "
                                    f"kline_age={_age_s:.0f}s max={_kline_max_age_s:.0f}s | {ctx()}"
                                )
                                _stale_count += 1
                                _stale_ages.append(_age_s)
                                if len(_stale_syms) < 5:
                                    _stale_syms.append(symbol)
                                continue
                        except Exception:
                            # Parse failure -> fall through and analyse anyway
                            # (better to over-include than under-include when
                            # timestamp parsing breaks for one row).
                            pass
                if len(klines) >= _min_kline_count:
                    candles_map[symbol] = klines
                    _t_coin = _time.time()
                    ta_data = await self.ta_engine.analyze(candles=klines)
                    _coin_ms = (_time.time() - _t_coin) * 1000
                    ta_map[symbol] = ta_data
                    if _coin_ms > 200:
                        _slow_coins.append((symbol, _coin_ms))
                        _ta_slow += 1
                    else:
                        _ta_fast += 1
                    if _coin_ms > _ta_max_ms:
                        _ta_max_ms = _coin_ms
                else:
                    # Candidate-Block Data Integrity Fix — Issue 2 (2026-06-09):
                    # the too-few-candles drop was previously silent — a coin
                    # absent from Layer 1 for short history left no trace, so a
                    # "0 fired" candidate could not be told apart from a genuine
                    # no-signal. Surface it per-coin (rolled up below) so the
                    # data-gap case is observable. No behaviour change — the same
                    # coins are dropped, they are just no longer silent.
                    _short_hist_count += 1
                    if len(_short_hist_syms) < 5:
                        _short_hist_syms.append(symbol)
            except Exception as e:
                log.debug("TA failed for {s}: {err}", s=symbol, err=str(e))
        _ta_ms = (_time.time() - _t_ta) * 1000

        # Phase 9 Gap A7 (output-quality obs): emit rollup if any coins
        # were skipped stale this tick. Replaces the N-per-coin blast at
        # WARNING with one aggregate.
        if _stale_count:
            _oldest = max(_stale_ages) if _stale_ages else 0.0
            _newest = min(_stale_ages) if _stale_ages else 0.0
            log.info(
                f"STRAT_SKIP_STALE_AGG | count={_stale_count} "
                f"oldest_age_s={_oldest:.0f} newest_age_s={_newest:.0f} "
                f"sample_syms={_stale_syms} | {ctx()}"
            )
        # Candidate-Block Data Integrity Fix — Issue 2 (2026-06-09): one rollup
        # for coins dropped from Layer 1 on too-few candles, mirroring the stale
        # rollup. A coin in this list was NOT scored this cycle (a data/history
        # gap), distinct from a coin that WAS scored and fired no strategy.
        if _short_hist_count:
            log.info(
                f"STRAT_SKIP_KLINE_COUNT_AGG | count={_short_hist_count} "
                f"min={_min_kline_count} sample_syms={_short_hist_syms} | {ctx()}"
            )
        # Phase 9 Gap A8: per-cycle TA aggregate.
        log.info(
            f"STRAT_TA_DONE | fast={_ta_fast} slow={_ta_slow} "
            f"max_ms={_ta_max_ms:.0f} total_ms={_ta_ms:.0f} | {ctx()}"
        )

        # H1 TA pre-population. self.ta_engine IS the TACache (manager.py
        # registers `ta`/`ta_engine`/`ta_cache` as the same TACache instance),
        # so this analyze() call lands the result in the cache keyed
        # `{symbol}:60:200` — exactly what strategist.py's market_data loop
        # reads. Result value is discarded here; the caching side-effect is
        # the whole point.
        _t_ta_h1 = _time.time()
        # Phase 7 (P0-6): take a snapshot of TACache counters BEFORE the
        # H1 pre-population loop so we can report (a) how many lookups
        # actually hit the cached value vs (b) how many recomputed. The
        # previous `_h1_hits` counter was dishonest — it counted "successful
        # analyze() calls" regardless of whether the result came from
        # cache. With the time-bucketed key (Phase 7 also), valid hits
        # within a 5 s bucket are now legitimately preserved.
        _stats_before = {}
        if hasattr(self.ta_engine, "get_stats"):
            try:
                _stats_before = self.ta_engine.get_stats()
            except Exception:
                _stats_before = {}
        _h1_calls = 0
        for symbol, klines_h1 in all_klines_h1.items():
            # Issue 2 follow-up (2026-06-09): the H1 pre-population shares the
            # same minimum-candles requirement as the M5 Layer 1 loop (both feed
            # the same TA engine), so it reads the centralized min_kline_count
            # too rather than a second hardcoded 50.
            if not klines_h1 or len(klines_h1) < _min_kline_count:
                continue
            try:
                await self.ta_engine.analyze(candles=klines_h1)
                _h1_calls += 1
            except Exception as e:
                log.debug(
                    "STRAT_PREFETCH_H1_ITEM_FAIL | sym={s} err='{err}'",
                    s=symbol, err=str(e)[:80],
                )
        _ta_h1_ms = (_time.time() - _t_ta_h1) * 1000

        _stats_after = {}
        if hasattr(self.ta_engine, "get_stats"):
            try:
                _stats_after = self.ta_engine.get_stats()
            except Exception:
                _stats_after = {}
        _h1_lookups = int(_stats_after.get("lookups", 0)) - int(_stats_before.get("lookups", 0))
        _h1_valid = int(_stats_after.get("valid_hits", 0)) - int(_stats_before.get("valid_hits", 0))
        _h1_recomputed = int(_stats_after.get("recomputed", 0)) - int(_stats_before.get("recomputed", 0))

        if not candles_map:
            log.debug("Strategy worker: no market data available")
            return

        # 5b. Pre-fetch sentiment and altdata for scoring context.
        #
        # Two views are produced:
        #   * ``altdata_context``  — global dict, passed to L2 scorer / L3
        #                            ensemble. Existing nested shape (fear_greed
        #                            global; funding nested {sym: rate}).
        #   * ``altdata_per_sym``  — flat per-symbol dict keyed by symbol,
        #                            passed to L1 strategies. Each value is a
        #                            FLAT dict: {"funding_rate", "fear_greed",
        #                            "oi_change_24h_pct", ...}. This is what
        #                            14 strategies (D1/D2/E1/F3/G3/H1/H2/...)
        #                            actually expect; passing the global
        #                            ``altdata_context`` to them would leave
        #                            their ``altdata.get("funding_rate")``
        #                            calls reading missing keys.
        #
        # Data source: AltDataWorker's in-memory caches (``_funding_cache``,
        # ``_oi_cache``), which are populated for the FULL universe each
        # altdata tick. No new API calls per strategy cycle.
        altdata_context: dict = {}
        sentiment_context: dict = {}
        altdata_per_sym: dict[str, dict] = {}

        # F&G — global value, shared across all symbols.
        _fg_value: int | None = None
        try:
            fg_svc = getattr(self, '_fear_greed', None)
            if fg_svc:
                fg_data = await fg_svc.get_latest()
                if fg_data:
                    _fg_value = int(getattr(fg_data, "value", 50) or 50)
                    altdata_context["fear_greed"] = {
                        "value": _fg_value,
                        "classification": getattr(fg_data, "classification", "neutral"),
                    }
                    altdata_context["fear_greed_value"] = _fg_value
        except Exception as e:
            log.debug("fetch fear_greed data failed: {err}", err=str(e))

        # Funding + OI — per-symbol, sourced from AltDataWorker caches.
        # Falls back to the legacy first-5 direct-fetch path only if the
        # AltDataWorker isn't reachable (defensive — same effective state
        # as pre-fix in that edge case).
        _altdata_worker = self.services.get("altdata_worker") if self.services else None
        _funding_filled = 0
        _oi_filled = 0
        if _altdata_worker is not None:
            altdata_context["funding"] = {}
            for sym in candles_map.keys():
                try:
                    rate = _altdata_worker.get_funding(sym)
                except Exception:
                    rate = None
                try:
                    oi_snap = _altdata_worker.get_oi(sym)
                except Exception:
                    oi_snap = None

                _per = {
                    "funding_rate": float(rate) if rate is not None else 0.0,
                    "fear_greed": _fg_value if _fg_value is not None else 50,
                    "oi_change_24h_pct": (
                        float(oi_snap.get("change_24h_pct", 0.0))
                        if isinstance(oi_snap, dict) else 0.0
                    ),
                }
                altdata_per_sym[sym] = _per

                # Mirror funding into global altdata_context for L2.
                if rate is not None:
                    altdata_context["funding"][sym] = float(rate)
                    _funding_filled += 1
                if isinstance(oi_snap, dict):
                    _oi_filled += 1
        else:
            # Legacy fallback: direct funding fetch for first 5 symbols.
            try:
                funding_svc = getattr(self, '_funding_tracker', None)
                if funding_svc:
                    for sym in list(candles_map.keys())[:5]:
                        try:
                            fr = await funding_svc.get_latest(sym)
                            if fr:
                                if "funding" not in altdata_context:
                                    altdata_context["funding"] = {}
                                altdata_context["funding"][sym] = getattr(fr, "funding_rate", 0)
                        except Exception as e:
                            log.debug("fetch funding rate for symbol failed: {err}", err=str(e))
            except Exception as e:
                log.debug("fetch funding data failed: {err}", err=str(e))

        # Bulk-fetch tickers via the existing canonical pattern (mirrors
        # ``strategist.py:1729-1742``). One HTTP call returns ~300 USDT-perp
        # tickers; ``market_service`` caches the bulk result for 30s, so
        # within a 5-min strategy cycle this is effectively free. Provides
        # ``ticker.last_price`` / ``change_24h_pct`` / ``bid`` / ``ask`` to
        # L1 strategies — D1/D2/H1 use ``change_24h_pct`` as a hard gate
        # and J3 requires ``ticker`` non-None. On any failure ``ticker_map``
        # stays empty and the L1 loop passes ``ticker=None`` (pre-fix
        # behaviour preserved).
        ticker_map: dict = {}
        _market_service = self.services.get("market_service") if self.services else None
        if _market_service is not None:
            try:
                _bulk_t = await _market_service.get_all_linear_tickers()
                ticker_map = {t.symbol: t for t in (_bulk_t or [])}
            except Exception as e:
                log.debug(
                    f"STRAT_BULK_TICKER_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

        # Tickers in our universe only (not the full ~300).
        _ticker_filled = sum(1 for s in candles_map if s in ticker_map)

        log.info(
            f"STRAT_ALTDATA_PER_SYM_BUILD | funding_filled={_funding_filled} "
            f"oi_filled={_oi_filled} ticker_filled={_ticker_filled} "
            f"total={len(candles_map)} "
            f"fg_value={_fg_value if _fg_value is not None else 'na'} "
            f"altdata_worker={'on' if _altdata_worker is not None else 'off'} "
            f"market_service={'on' if _market_service is not None else 'off'} | {ctx()}"
        )

        _section_ms["prefetch"] = (_time.time() - _t) * 1000
        # Expose the two halves so STRAT_CYCLE_DONE can split them. Altdata
        # fetches (fear_greed + funding) are counted inside ``prefetch`` minus
        # (db + ta) — negligible in practice but correctly accounted.
        _section_ms["prefetch_db"] = _db_ms
        _section_ms["prefetch_ta"] = _ta_ms
        # H1 pre-population timings (separate from M5). Phase 7 (P0-6):
        # ``prefetch_h1_hits`` previously counted "successful analyze()
        # calls" regardless of cache outcome. The honest replacement
        # exposes three counters delta'd around the H1 loop:
        #   prefetch_h1_lookups    = times analyze() ran with cacheable key
        #   prefetch_h1_valid      = of those, how many returned cached value
        #   prefetch_h1_recomputed = of those, how many fell through to engine
        # The legacy ``prefetch_h1_hits`` is kept as an alias of valid for
        # any downstream parser that still grep's for it.
        _section_ms["prefetch_db_h1"] = _db_h1_ms
        _section_ms["prefetch_ta_h1"] = _ta_h1_ms
        _section_ms["prefetch_h1_calls"] = float(_h1_calls)
        _section_ms["prefetch_h1_lookups"] = float(_h1_lookups)
        _section_ms["prefetch_h1_valid"] = float(_h1_valid)
        _section_ms["prefetch_h1_recomputed"] = float(_h1_recomputed)
        _section_ms["prefetch_h1_hits"] = float(_h1_valid)  # alias

        # Actionable diagnostic when prefetch is slow. Shows the db/ta split and
        # the 3 slowest TA coins, so we can tell in one line whether the cost is
        # the DB side (page-cache thrash / contention) or a specific pathological
        # coin's TA compute.
        #
        # Phase 7 session-stability: the 2026-04-24 window showed a single
        # prefetch cycle at 15,850 ms with h1_db=10,739 ms (the H1 kline
        # batch read) while TA was normal. The existing warning did not
        # break out the h1_db component, so the DB-contention diagnosis
        # required cross-referencing the section_ms snapshot. The log now
        # surfaces h1_db + h1 TA directly and keeps the 5000 ms threshold;
        # an additional ``STRAT_PREFETCH_CRITICAL`` tier fires above
        # 8000 ms so downstream alerting can distinguish "one-off slow"
        # from "pathological".
        # Phase 4 (corrected-Layer-1): always-emit prefetch summary, in
        # addition to the threshold-gated SLOW/CRITICAL warnings below.
        # Operators can now see prefetch latency per cycle without grepping
        # only for outliers — useful for tracking the D-3 lock-contention
        # pattern's improvement under sweet-spot scheduling.
        log.info(
            f"STRAT_PREFETCH | el={_section_ms['prefetch']:.0f}ms "
            f"db={_db_ms:.0f}ms ta={_ta_ms:.0f}ms "
            f"h1_db={_db_h1_ms:.0f}ms h1_ta={_ta_h1_ms:.0f}ms "
            f"src=market_repo+ta_engine coins={len(all_klines)} "
            f"h1_lookups={_h1_lookups} h1_valid={_h1_valid} "
            f"h1_recomputed={_h1_recomputed} | {ctx()}"
        )

        if _section_ms["prefetch"] > 5000:
            _slow_str = ",".join(
                f"{s}={ms:.0f}ms"
                for s, ms in sorted(_slow_coins, key=lambda x: -x[1])[:3]
            )
            log.warning(
                f"STRAT_PREFETCH_SLOW | el={_section_ms['prefetch']:.0f}ms "
                f"db={_db_ms:.0f}ms ta={_ta_ms:.0f}ms "
                f"h1_db={_db_h1_ms:.0f}ms h1_ta={_ta_h1_ms:.0f}ms "
                f"coins={len(all_klines)} "
                f"slow_coins=[{_slow_str}] | {ctx()}"
            )
            if _section_ms["prefetch"] > 8000:
                log.error(
                    f"STRAT_PREFETCH_CRITICAL | el={_section_ms['prefetch']:.0f}ms "
                    f"db={_db_ms:.0f}ms h1_db={_db_h1_ms:.0f}ms "
                    f"coins={len(all_klines)} | {ctx()}"
                )

        # 6. LAYER 1: Scan — run strategies on coins (per-coin regime aware)
        _t = _time.time()
        from src.strategies.models.signal_types import RawSignal
        raw_signals: list[RawSignal] = []
        # Per-strategy timing tally for STRAT_L1_SLOW_STRATEGY detection
        _strategy_ms_total: dict[str, float] = {}
        # Per-coin-authority Phase 2 (2026-05-29): count coins that fell back to
        # UNKNOWN because their per-coin regime cache was cold (boot race / new
        # listing). Should be ~0 after RegimeWorker warms; a sustained non-zero
        # count signals a per-coin coverage gap, not a real market state.
        _regime_fallback_unknown = 0

        for symbol in candles_map:
            # Per-coin-authority Phase 2 (2026-05-29): the per-coin regime is the
            # authority. A cold cache falls back to an explicit UNKNOWN (broad
            # roster — the coin still trades on its own TA/structure per operator
            # decision), NEVER to the global BTC regime — that back-door coupling
            # is exactly what per-coin authority removes.
            symbol_regime = coin_regimes.get(symbol)
            if symbol_regime is None:
                symbol_regime = RegimeState.unknown()
                _regime_fallback_unknown += 1
            symbol_strategies = self.registry.get_active_for_regime(symbol_regime.regime)

            for strategy in symbol_strategies:
                _strat_t0 = _time.time()
                try:
                    candles = candles_map[symbol]
                    ta_data = ta_map.get(symbol, {})
                    signal = await strategy.scan(
                        symbol=symbol,
                        candles=candles,
                        ticker=ticker_map.get(symbol),  # None if symbol not in bulk fetch
                        ta_data=ta_data,
                        sentiment_data=None,  # Phase 2 will wire sentiment
                        altdata=altdata_per_sym.get(symbol, {}),
                    )
                    if signal:
                        raw_signals.append(signal)
                except Exception as e:
                    log.debug(
                        "Scan error: {strat} on {sym}: {err}",
                        strat=strategy.name, sym=symbol, err=str(e),
                    )
                finally:
                    _strat_ms = (_time.time() - _strat_t0) * 1000
                    _strategy_ms_total[strategy.name] = _strategy_ms_total.get(strategy.name, 0.0) + _strat_ms
                    if _strat_ms > 2000:
                        log.warning(
                            f"STRAT_L1_SLOW_STRATEGY | strategy={strategy.name} sym={symbol} el={_strat_ms:.0f}ms | {ctx()}"
                        )

        # Capture L1 elapsed BEFORE the empty-signals early-return so
        # STRAT_L1_SLOW still fires on slow-but-empty L1 scans (otherwise a
        # degraded L1 that produces no setups would be invisible).
        _section_ms["l1"] = (_time.time() - _t) * 1000
        # Per-coin-authority Phase 2 (2026-05-29): observability for the cold
        # per-coin regime fallback. Expect ~0 after RegimeWorker's first tick;
        # a sustained non-zero count = a per-coin coverage gap to investigate.
        if _regime_fallback_unknown:
            log.info(
                f"STRAT_REGIME_FALLBACK | unknown_fallback={_regime_fallback_unknown} "
                f"coins={len(candles_map)} cached={len(coin_regimes)} | {ctx()}"
            )
        if _section_ms["l1"] > 5000:
            _slowest = sorted(_strategy_ms_total.items(), key=lambda kv: kv[1], reverse=True)[:3]
            _slow_str = ",".join(f"{n}={ms:.0f}ms" for n, ms in _slowest)
            log.warning(f"STRAT_L1_SLOW | el={_section_ms['l1']:.0f}ms coins={len(candles_map)} strategies={len(_strategy_ms_total)} top3={_slow_str} | {ctx()}")

        # Candidate-Block Data Integrity Fix — Issue 2 (2026-06-09): per-COIN
        # Layer 1 fire distribution. The existing STRAT_L1_DONE breaks down fire
        # rate per STRATEGY; this complements it with the per-COIN view that
        # answers the actual question behind a "0 fired" candidate — was the
        # coin PROCESSED in Layer 1 (in candles_map) but triggered by no
        # strategy (a genuine no-signal), or was it ABSENT from Layer 1 (a data
        # gap, with STRAT_SKIP_STALE / STRAT_SKIP_KLINE_COUNT naming why)?
        # Emitted before the no-signal early return so the all-zero cycle is
        # still reported. Observability only — no control-flow change.
        _coins_fired = {
            getattr(s, "symbol", None) for s in raw_signals
        } - {None}
        _coins_zero = [c for c in candles_map if c not in _coins_fired]
        log.info(
            f"STRAT_L1_COIN_FIRE_DIST | coins_processed={len(candles_map)} "
            f"coins_with_signal={len(_coins_fired)} "
            f"zero_fired={len(_coins_zero)} "
            f"zero_fired_sample={_coins_zero[:10]} | {ctx()}"
        )

        if not raw_signals:
            log.debug("Strategy worker: no raw signals from Layer 1")
            return

        # Phase 4 (output-quality): extend STRAT_L1 with per-strategy fire
        # rate distribution. If L1 is degenerate (all strategies fire 0
        # signals OR one strategy fires 100% of signals) operators can
        # see it directly without parsing 100+ STRAT_L1_SIG lines.
        # _sig_per_strat counts how many signals each strategy produced.
        # _strategy_ms_total covers ALL strategies that were SCANNED;
        # _sig_per_strat will only have keys for strategies that PRODUCED
        # signals. Both lists are needed: top firing = signal-producing,
        # bottom non-firing = scanned-but-zero-signals.
        _sig_per_strat: dict[str, int] = {}
        for _sig in raw_signals:
            _sig_per_strat[_sig.strategy_name] = _sig_per_strat.get(_sig.strategy_name, 0) + 1
        _top_firing = sorted(_sig_per_strat.items(), key=lambda kv: -kv[1])[:5]
        _scanned_no_signal = [
            n for n in _strategy_ms_total
            if n not in _sig_per_strat
        ][:5]
        _top_str = ",".join(f"{n}:{c}" for n, c in _top_firing) or "none"
        _bottom_str = ",".join(_scanned_no_signal) or "none"
        log.info(
            f"STRAT_L1_DONE | signals={len(raw_signals)} "
            f"strategies={len(_strategy_ms_total)} coins={len(candles_map)} "
            f"per_strategy_avg={len(raw_signals)/max(1,len(_strategy_ms_total)):.2f} "
            f"top_firing=[{_top_str}] non_firing=[{_bottom_str}] "
            f"el={_section_ms['l1']:.0f}ms | {ctx()}"
        )
        # Preserve the back-compat tag so any downstream parser keyed on
        # the original STRAT_L1 string keeps working.
        log.info(f"STRAT_L1 | signals={len(raw_signals)} strategies={len(_strategy_ms_total)} coins={len(candles_map)} el={_section_ms['l1']:.0f}ms | {ctx()}")
        for _sig in raw_signals[:15]:
            log.debug(f"STRAT_L1_SIG | sym={_sig.symbol} dir={_sig.direction} str={_sig.strategy_name} conf={getattr(_sig, 'confidence', 0):.2f} | {ctx()}")

        # Enforcer callback: signals generated
        enforcer = getattr(self, '_enforcer', None)
        if enforcer:
            for _ in raw_signals:
                enforcer.on_signal_generated()

        # 7. LAYER 2: Score (with sentiment + altdata + structural context)
        _t = _time.time()
        structural_map = None
        structure_cache = self.services.get("structure_cache") if self.services else None
        if structure_cache:
            try:
                fresh = structure_cache.get_all()
                if fresh:
                    structural_map = {
                        sym: analysis.to_dict()
                        for sym, analysis in fresh.items()
                    }
            except Exception:
                pass
        scored = self.scorer.score_batch(
            raw_signals, candles_map, ta_map,
            sentiment_context or None, altdata_context or None, regime,
            structural_map=structural_map,
            # Per-coin-authority Phase 4 (2026-05-29): score each signal under
            # its OWN coin's regime (else UNKNOWN), not the single global regime.
            coin_regimes=coin_regimes,
        )

        _section_ms["l2"] = (_time.time() - _t) * 1000
        if not scored:
            log.debug("Strategy worker: no signals passed scoring threshold")
            # Issue 4 cross-check (CALL_A exploit/fetch) — total-batch drop at
            # Layer 2: raw signals existed but NONE scored. The main funnel
            # sentinel below this early return would never see it, so surface
            # the total drop here (all coins would render 0-fired downstream).
            try:
                _l1n = len({getattr(s, "symbol", None) for s in raw_signals} - {None})
                if _l1n > 0:
                    log.warning(
                        f"STRAT_FUNNEL_DROP | l1_syms={_l1n} l2_syms=0 l3_syms=0 "
                        f"stage=layer2_total | every Layer-1 signal dropped at "
                        f"scoring (all coins would render 0-fired) | {ctx()}"
                    )
            except Exception as _fe:
                log.debug(f"STRAT_FUNNEL_CHECK_FAIL | err='{str(_fe)[:80]}'")
            return

        # Stage 2 phase 2 — hoist the layer_manager lookup above the
        # scored loop so the scorer-component cache write below sees the
        # same scored universe that fills ``_score_cache`` (parity is
        # required: ScannerWorker reads _score_cache for the
        # opportunity_score; the strategist reads _scorer_components for
        # the rich block, so both must reflect the same setups). The
        # legacy assignment further down (line ~791) is kept as
        # defensive re-fetch for the consensus-write block, mirroring
        # the codebase pattern of locally-scoped service lookups.
        layer_manager = self.services.get("layer_manager")

        # Phase 4 (corrected-Layer-1): populate per-symbol score cache for
        # Phase 6's ScannerWorker. Each scored entry's raw_signal carries
        # the symbol; total_score is the L2 composite.
        # Stage 2 phase 2: also populate LayerManager._scorer_components
        # so the strategist's _format_packages_for_prompt_full can render
        # the 4-component breakdown for the rich Layer 1B/1C block.
        _sc_written = 0
        for _ss in scored:
            try:
                _sym = _ss.raw_signal.symbol
                self._score_cache[_sym] = float(_ss.total_score)
                if layer_manager is not None:
                    comps = getattr(layer_manager, "_scorer_components", None)
                    if comps is None:
                        comps = {}
                        layer_manager._scorer_components = comps
                    comps[_sym] = {
                        "base": float(_ss.base_score),
                        "confluence": float(_ss.confluence_score),
                        "context": float(_ss.context_score),
                        "quality": float(_ss.quality_score),
                        "total": float(_ss.total_score),
                        "grade": _ss.grade,
                        "last_updated": _time.time(),
                    }
                    _sc_written += 1
            except Exception:
                continue

        # Stage 2 phase 2 — observability for the scorer-components
        # cache write. Lets operators confirm parity with _score_cache
        # (count==count) and detect drift (cache_size_after > N implies
        # stale entries from prior cycles, which is intentional —
        # dict.update merge preserves them).
        if layer_manager is not None:
            log.info(
                f"STRAT_SCORER_COMPONENTS_WRITE | written={_sc_written} "
                f"score_cache_size={len(self._score_cache)} "
                f"components_cache_size_after="
                f"{len(getattr(layer_manager, '_scorer_components', {}))} "
                f"| {ctx()}"
            )

        # Phase 4 (output-quality): score distribution percentiles +
        # component averages. Reveals whether scoring is degenerate
        # (all 0 / all 100) or has healthy spread. Component averages
        # show which scoring component dominates.
        _scores = [float(_s.total_score) for _s in scored]
        _scores_sorted = sorted(_scores)
        _n = len(_scores_sorted)

        def _pct(p: float) -> float:
            if _n == 0:
                return 0.0
            return _scores_sorted[max(0, int(p * (_n - 1)))]

        _p25 = _pct(0.25)
        _p50 = _pct(0.50)
        _p75 = _pct(0.75)
        _p95 = _pct(0.95)
        # Component averages — read from each ScoredSetup's component fields.
        _comp_sums = {"base": 0.0, "confluence": 0.0, "context": 0.0, "quality": 0.0}
        for _ss in scored:
            for _k in _comp_sums:
                _comp_sums[_k] += float(getattr(_ss, f"{_k}_score", 0.0) or 0.0)
        _comp_avgs = (
            {k: round(v / _n, 1) for k, v in _comp_sums.items()}
            if _n else _comp_sums
        )
        _comp_str = ",".join(
            f"{k}:{_comp_avgs[k]}" for k in ("base", "confluence", "context", "quality")
        )
        log.info(
            f"STRAT_L2_DONE | scored={len(scored)} "
            f"score_p25={_p25:.1f} score_p50={_p50:.1f} "
            f"score_p75={_p75:.1f} score_p95={_p95:.1f} "
            f"score_components_avg=[{_comp_str}] "
            f"el={_section_ms['l2']:.0f}ms | {ctx()}"
        )
        log.info(f"STRAT_L2 | scored={len(scored)} best={scored[0].total_score:.0f} grade={scored[0].grade} el={_section_ms['l2']:.0f}ms | {ctx()}")
        if _section_ms["l2"] > 2000:
            log.warning(f"STRAT_L2_SLOW | el={_section_ms['l2']:.0f}ms signals={len(raw_signals)} | {ctx()}")
        log.info(
            "Layer 2: {n} scored setups (best: {best:.0f} {grade})",
            n=len(scored), best=scored[0].total_score, grade=scored[0].grade,
        )

        # 8. LAYER 3: Ensemble
        _t = _time.time()
        consensus_setups = self.ensemble.vote_batch(
            scored, candles_map, ta_map,
            sentiment_context or None, altdata_context or None, regime,
            # Per-coin-authority Phase 4 (2026-05-29): vote each setup under its
            # OWN coin's regime (voter pool + regime-weighter), not the global.
            coin_regimes=coin_regimes,
        )
        _section_ms["l3"] = (_time.time() - _t) * 1000

        # Layer 2 Defect 1 (2026-05-22) — persist per-strategy votes per
        # cycle to ensemble_votes via batched executemany. Async fire-and-
        # forget pattern (await but non-blocking via best-effort try/except
        # inside the helper); per-cycle volume ~1,050 rows in a single DB
        # roundtrip. Failure logs D1_VOTES_PERSIST_FAIL but does NOT raise —
        # trading must continue even if the write fails (Rule 7).
        if consensus_setups:
            try:
                from src.strategies.ensemble import EnsembleVoter
                await EnsembleVoter.persist_votes(self.db, consensus_setups)
            except Exception as _e:
                log.error(
                    f"D1_VOTES_PERSIST_DISPATCH_FAIL | err='{str(_e)[:120]}' | {ctx()}"
                )

        # Layer 3 (2026-05-22) — operator-approved per-5min cadence:
        # refresh the regime-conditional weight cache from the votes that
        # were just persisted (above) joined to trade_intelligence
        # outcomes. The deriver's own refresh() guards Rule 7: on DB
        # failure the cached factors are left unchanged and an
        # RW_REFRESH_FAIL log fires; trading never blocks on it. Outer
        # try/except guards the dispatch itself per the same pattern as
        # persist_votes above. Read-only — does not write to DB.
        _regime_weighter = self.services.get("regime_weighter")
        if _regime_weighter is not None:
            try:
                await _regime_weighter.refresh(self.db)
                # Layer 3 Rule 16 — emit the audit log roughly hourly
                # (every 12 refreshes at the 5-minute cadence). Loud-on-
                # violation: RULE16_RW_PERMANENT_SILENCE at ERROR if any
                # strategy is at floor in every regime; same for
                # RULE16_RW_REGIME_INDEPENDENT if the weight vectors
                # have degenerated to flat. Quiet RW_AUDIT_OK on the
                # healthy path.
                _refresh_n = getattr(_regime_weighter, "_refresh_count", 0)
                if _refresh_n > 0 and (_refresh_n % 12) == 0:
                    _regime_weighter.log_audit()
            except Exception as _e:
                log.error(
                    f"L3_RW_REFRESH_DISPATCH_FAIL | err='{str(_e)[:120]}' | {ctx()}"
                )

        if not consensus_setups:
            log.debug("Strategy worker: no setups passed ensemble consensus")
            # Issue 4 cross-check (CALL_A exploit/fetch) — total-batch drop at
            # Layer 3: scored setups existed but NONE reached consensus. Surface
            # it here (the main funnel sentinel is past this early return).
            try:
                _l1n = len({getattr(s, "symbol", None) for s in raw_signals} - {None})
                _l2n = len({
                    getattr(getattr(s, "raw_signal", None), "symbol", None)
                    for s in scored
                } - {None})
                if _l2n > 0:
                    log.warning(
                        f"STRAT_FUNNEL_DROP | l1_syms={_l1n} l2_syms={_l2n} "
                        f"l3_syms=0 stage=layer3_total | every scored setup "
                        f"dropped at ensemble (all coins would render 0-fired) "
                        f"| {ctx()}"
                    )
            except Exception as _fe:
                log.debug(f"STRAT_FUNNEL_CHECK_FAIL | err='{str(_fe)[:80]}'")
            return

        # Phase 4 (output-quality): consensus category distribution +
        # size_mult average. If the distribution is 100% one category
        # (e.g. all CONFLICT), L3 is broken. Bell-curve-ish across
        # STRONG/GOOD/WEAK/LEAN/CONFLICT is healthy.
        _cons_dist: dict[str, int] = {}
        _size_mults: list[float] = []
        for _v in consensus_setups:
            _cs = _v.consensus_strength
            _cons_dist[_cs] = _cons_dist.get(_cs, 0) + 1
            _size_mults.append(float(getattr(_v, "size_multiplier", 0.0) or 0.0))
        _size_avg = (sum(_size_mults) / len(_size_mults)) if _size_mults else 0.0
        _cons_str = ",".join(
            f"{k}:{v}" for k, v in sorted(_cons_dist.items(), key=lambda kv: -kv[1])
        )
        log.info(
            f"STRAT_L3_DONE | consensus={len(consensus_setups)} "
            f"consensus_dist=[{_cons_str}] size_mult_avg={_size_avg:.2f} "
            f"el={_section_ms['l3']:.0f}ms | {ctx()}"
        )
        # Issue 4 (CALL_A exploit/fetch, 2026-06-05) — Layer-funnel safety
        # sentinel. The brain's per-coin "Strategies: N fired" is non-zero only
        # when a SCORED setup reached Layer 3 (where the full voter roster votes);
        # it is 0 when the coin produced no scored setup. Today the funnel is
        # lossless (every coin with a raw Layer-1 signal is scored and reaches
        # consensus), so a coin with signal is never silently dropped at Layer
        # 2/3 and shown as 0-fired. This sentinel proves that invariant every
        # cycle and WARNs loudly if a future scorer/ensemble threshold ever
        # starts dropping a coin that DID produce a raw signal — converting the
        # currently-silent early-returns into an observable event.
        try:
            _l1_syms = {getattr(s, "symbol", None) for s in raw_signals}
            _l1_syms.discard(None)
            _l2_syms = {
                getattr(getattr(s, "raw_signal", None), "symbol", None)
                for s in scored
            }
            _l2_syms.discard(None)
            _l3_syms = {
                getattr(
                    getattr(getattr(v, "scored_setup", None), "raw_signal", None),
                    "symbol", None,
                )
                for v in consensus_setups
            }
            _l3_syms.discard(None)
            _dropped_l2 = sorted(_l1_syms - _l2_syms)
            _dropped_l3 = sorted(_l2_syms - _l3_syms)
            if _dropped_l2 or _dropped_l3:
                log.warning(
                    f"STRAT_FUNNEL_DROP | l1_syms={len(_l1_syms)} "
                    f"l2_syms={len(_l2_syms)} l3_syms={len(_l3_syms)} "
                    f"dropped_l2={_dropped_l2[:10]} dropped_l3={_dropped_l3[:10]} "
                    f"| a coin with a raw signal lost its scored setup/consensus "
                    f"(silent drop) — would render as 0-fired | {ctx()}"
                )
            else:
                log.info(
                    f"STRAT_FUNNEL_OK | l1_syms={len(_l1_syms)} "
                    f"l2_syms={len(_l2_syms)} l3_syms={len(_l3_syms)} "
                    f"| every coin with a raw signal reached consensus | {ctx()}"
                )
        except Exception as _fe:
            log.debug(f"STRAT_FUNNEL_CHECK_FAIL | err='{str(_fe)[:80]}'")
        log.info(f"STRAT_L3 | consensus={len(consensus_setups)} top={consensus_setups[0].scored_setup.raw_signal.symbol} str={consensus_setups[0].consensus_strength} el={_section_ms['l3']:.0f}ms | {ctx()}")
        for _v in consensus_setups[:10]:
            _vs = _v.scored_setup.raw_signal
            log.debug(f"STRAT_L3_VOTE | sym={_vs.symbol} dir={_vs.direction} str={_v.consensus_strength} | {ctx()}")
        log.info(
            "Layer 3: {n} consensus setups (top: {str} {sym})",
            n=len(consensus_setups),
            str=consensus_setups[0].consensus_strength,
            sym=consensus_setups[0].scored_setup.raw_signal.symbol,
        )

        # 9. Apply PnL restrictions (start of L4)
        _t = _time.time()
        mode = self.pnl_manager.get_current_mode()
        filtered = self.pnl_manager.apply_restrictions(consensus_setups, mode)

        if not filtered:
            log.info(
                "Strategy worker: all setups filtered by PnL mode {m}",
                m=mode["mode"],
            )
            return

        # Enforcer callback: setups ready
        if enforcer:
            for _ in filtered:
                enforcer.on_setup_sent_to_brain()

        # ═══════════════ LAYER 4: STORE HINTS FOR CLAUDE ═══════════════
        # Trade execution moved to layer_manager._run_strategic_review()
        # strategy_worker is a DATA COLLECTOR only (locked decisions #3, #8)

        layer_manager = self.services.get("layer_manager")

        # Layer 1 restructure Phase 3 — build per-coin consensus FIRST and
        # write it to the cache BEFORE the Layer 3 active check. Consensus
        # is observability/data, not execution; ScannerWorker reads it
        # whether Layer 3 is on or off. Stale entries (coins not processed
        # this tick) are preserved via merge so a momentary gap doesn't
        # zero the entry the selector reads.
        #
        # Phase 4 (post-Layer-1 fix). The cache is built from
        # ``consensus_setups`` (full universe), NOT ``filtered`` (post-PnL
        # restrictions). The legacy code passed ``filtered`` here, which
        # caused 32-45 of 50 coins to be invisible to ScannerWorker every
        # cycle (live monitor 2026-04-27 showed cache size 5-18, against
        # the 50-coin watch list — qualified count stuck at 0-2 vs Phase-5
        # plan target of 5-25). The legacy summary at line 607 keeps
        # using ``filtered`` because legacy strategist reads at
        # strategist.py:1017 / 1587 expect the post-filter shape; commit
        # 0afd4e2 made those reads defensive but the contract is still
        # post-filter for the summary. See
        # ``dev_notes/phase0_post_layer1_fixes/issue_4_consensus_filter.md``.
        if layer_manager:
            # Issue E25: pass the SAME fresh per-cycle regime snapshot the
            # scoring loop used (captured at the top of this tick) so each
            # coin's consensus is tagged with the regime it was scored under.
            new_consensus = self._build_per_coin_consensus(
                consensus_setups, coin_regimes=coin_regimes, global_regime=regime,
            )
            _e25_tagged = sum(1 for _v in new_consensus.values() if _v.get("scoring_regime"))
            if new_consensus:
                _e25_sample = next(
                    (f"{_k}:{_v['scoring_regime']}" for _k, _v in new_consensus.items()
                     if _v.get("scoring_regime")), "none")
                log.info(
                    f"E25_SCORING_REGIME_TAGGED | coins={len(new_consensus)} "
                    f"tagged={_e25_tagged} sample={_e25_sample} | {ctx()}"
                )
            existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
            # Defensive migration from the legacy summary-shaped dict on
            # first run after this commit lands. Detect by structure: the
            # legacy summary's inner dict had {"buy", "sell", "total_score"};
            # the new shape's inner dict has {"consensus", ...}.
            if not isinstance(existing, dict) or any(
                isinstance(v, dict) and "consensus" not in v
                and {"buy", "sell", "total_score"} <= set(v.keys())
                for v in (existing.values() if hasattr(existing, "values") else [])
            ):
                existing = {}
            existing.update(new_consensus)  # only updates processed coins
            layer_manager._strategy_consensus = existing
            # Preserve summary alias for legacy strategist reads at
            # strategist.py:1017/1587. Both still .get(...) defensively.
            layer_manager._strategy_consensus_summary = self._build_consensus_summary(filtered)

            # Phase 2 of the 1D briefing rewrite — full vote distribution
            # cache. Built from the same ``consensus_setups`` collection
            # so the votes correspond to the same ScoredSetup the
            # ``_strategy_consensus`` aggregate was computed from.
            # ``dict.update`` semantics preserve stale entries for coins
            # not processed this tick — same protection model as
            # ``_strategy_consensus``. Failure is non-fatal (debug log,
            # cache stays at its previous state).
            try:
                new_votes = self._build_per_coin_votes(consensus_setups)
                existing_votes = getattr(layer_manager, "_strategy_votes", {}) or {}
                if not isinstance(existing_votes, dict):
                    existing_votes = {}
                existing_votes.update(new_votes)
                layer_manager._strategy_votes = existing_votes
            except Exception as _e:
                log.debug(
                    f"STRAT_VOTES_CACHE_FAIL | err='{str(_e)[:100]}' | {ctx()}"
                )

            # Phase 4 (post-Layer-1 fix). STRAT_CONSENSUS_WRITE makes the
            # full-vs-filtered gap observable per cycle. Operators can
            # confirm at INFO level that the cache fills with the full
            # universe (not only the post-PnL-restriction subset).
            log.info(
                f"STRAT_CONSENSUS_WRITE | full_count={len(new_consensus)} "
                f"filtered_count={len(filtered)} setups_in={len(consensus_setups)} "
                f"cache_size_after={len(existing)} "
                f"votes_cache_size={len(getattr(layer_manager, '_strategy_votes', {}) or {})} "
                f"mode={mode.get('mode', 'unknown')} "
                f"threshold={mode.get('max_score_threshold', 'na')} | {ctx()}"
            )

            # STRAT_CONSENSUS_CHANGE for transitions; track previous category
            # at worker level so we don't spam logs on stable consensus.
            for sym, entry in new_consensus.items():
                prev = self._prev_consensus.get(sym)
                if prev != entry["consensus"]:
                    log.info(
                        f"STRAT_CONSENSUS_CHANGE | sym={sym} "
                        f"from={prev or 'NONE'} to={entry['consensus']} "
                        f"votes={entry['vote_count']} score={entry['consensus_score']:.2f} | {ctx()}"
                    )
                    self._prev_consensus[sym] = entry["consensus"]

            # Cycle-level distribution.
            _categories: dict[str, int] = {}
            for entry in new_consensus.values():
                c = entry["consensus"]
                _categories[c] = _categories.get(c, 0) + 1
            if _categories:
                _cat_str = " ".join(
                    f"{k}={v}" for k, v in sorted(_categories.items())
                )
                log.info(
                    f"STRAT_CONSENSUS_SUMMARY | total={sum(_categories.values())} "
                    f"{_cat_str} | {ctx()}"
                )

        if not layer_manager or not layer_manager.is_layer_active(3):
            log.debug("Layer 3 not active -- skipping hints")
            return

        # ── Issue #3: Strategies as HINTS for Claude (no rule engine execution) ──
        # Compress top strategy signals to hints for Claude's next cycle
        hints = []
        for setup_wrapper in filtered[:20]:
            try:
                setup = setup_wrapper.scored_setup if hasattr(setup_wrapper, "scored_setup") else setup_wrapper
                signal = setup.raw_signal
                hints.append({
                    "symbol": signal.symbol,
                    "direction": signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction),
                    "strategy": signal.strategy_name,
                    "score": round(setup.total_score, 1),
                    "consensus": getattr(setup_wrapper, "consensus_strength", "GOOD") if hasattr(setup_wrapper, "consensus_strength") else "GOOD",
                })
            except Exception as e:
                log.debug("build strategy hint failed: {err}", err=str(e))

        # Save hints for strategist to read next cycle.
        # NOTE: _strategy_consensus already written above (Phase 3) outside
        # the is_layer_active(3) gate so ScannerWorker sees consensus even
        # when Layer 3 is off. _strategy_hints stays gated as before — it
        # exists for Claude's prompt, which only fires when L3 is on.
        if layer_manager:
            layer_manager._strategy_hints = hints

        _section_ms["l4"] = (_time.time() - _t) * 1000
        # Phase 4 (output-quality): per-cache size visibility. Operators
        # see at a glance whether the per-coin consensus cache is full
        # (~50 — healthy) or sparse (<10 — pipeline gap upstream).
        _score_cache_size = len(getattr(self, "_score_cache", {}) or {})
        _consensus_size = (
            len(getattr(layer_manager, "_strategy_consensus", {}) or {})
            if layer_manager else 0
        )
        _summary_size = (
            len(getattr(layer_manager, "_strategy_consensus_summary", {}) or {})
            if layer_manager else 0
        )
        _hints_size = len(hints)
        log.info(
            f"STRAT_L4_HANDOFF | "
            f"score_cache_size={_score_cache_size} "
            f"consensus_size={_consensus_size} "
            f"consensus_summary_size={_summary_size} "
            f"hints_top20_size={_hints_size} "
            f"el={_section_ms['l4']:.0f}ms | {ctx()}"
        )
        log.info(f"STRAT_L4 | hints={len(hints)} filtered_from={len(filtered)} el={_section_ms['l4']:.0f}ms | {ctx()}")
        # Phase 12.1 (lifecycle-logging-audit Gap 1.9-G2): deleted prose
        # duplicate of STRAT_L4 above.

        # Cycle summary
        _enf = getattr(self, '_enforcer', None)
        _urg = _enf.get_urgency_level() if _enf and hasattr(_enf, 'get_urgency_level') else 0
        _cycle_el = (_time.time() - _cycle_t0) * 1000
        # Phase 7 (P0-6): emit honest cache stats. cache_lookups counts
        # H1 analyze() calls with a cacheable key; cache_valid counts
        # those that hit a cached value; recomputed counts misses. They
        # sum to lookups. The pre-Phase7 `hits=N` metric is preserved as
        # an alias so existing dashboards keep working.
        # Phase 26 (Y-25): expose `misc` = total elapsed minus the sum of
        # accounted-for phases. The brief observed cycles where 87% of
        # time was unaccounted for (11s total, 1.4s in known phases).
        # `misc` makes the gap visible without breaking the existing
        # field layout.
        _accounted = (
            _section_ms.get('gate', 0)
            + _section_ms.get('prefetch', 0)
            + _section_ms.get('l1', 0)
            + _section_ms.get('l2', 0)
            + _section_ms.get('l3', 0)
            + _section_ms.get('l4', 0)
        )
        _misc_ms = max(0.0, _cycle_el - _accounted)
        log.info(
            f"STRAT_CYCLE_DONE | coins={len(universe)} signals={len(raw_signals)} scored={len(scored)} "
            f"hints={len(filtered)} urg={_urg} el={_cycle_el:.0f}ms | "
            f"gate={_section_ms['gate']:.0f}ms "
            f"prefetch={_section_ms['prefetch']:.0f}ms"
            f"(db={_section_ms.get('prefetch_db', 0):.0f}ms "
            f"ta={_section_ms.get('prefetch_ta', 0):.0f}ms "
            f"h1_db={_section_ms.get('prefetch_db_h1', 0):.0f}ms "
            f"h1_ta={_section_ms.get('prefetch_ta_h1', 0):.0f}ms"
            f"(cache_lookups={int(_section_ms.get('prefetch_h1_lookups', 0))} "
            f"cache_valid={int(_section_ms.get('prefetch_h1_valid', 0))} "
            f"recomputed={int(_section_ms.get('prefetch_h1_recomputed', 0))} "
            f"hits={int(_section_ms.get('prefetch_h1_hits', 0))})) "
            f"L1={_section_ms['l1']:.0f}ms L2={_section_ms['l2']:.0f}ms "
            f"L3={_section_ms['l3']:.0f}ms L4={_section_ms['l4']:.0f}ms "
            f"misc={_misc_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

        # Threshold alarm — rings only when a cycle takes > 30s (post-degradation territory)
        if _cycle_el > 30000:
            log.warning(
                f"STRAT_TICK_SLOW | el={_cycle_el:.0f}ms (>30s threshold) "
                f"coins={len(universe)} signals={len(raw_signals)} | {ctx()}"
            )

        # Rolling 10-tick aggregate — drift detector
        self._tick_times.append(_cycle_el)
        if len(self._tick_times) >= 10:
            _avg = sum(self._tick_times) / len(self._tick_times)
            _mn = min(self._tick_times)
            _mx = max(self._tick_times)
            _trend = "growing" if self._tick_times[-1] > self._tick_times[0] * 2 else "stable"
            log.info(
                f"STRAT_HEALTH | last_10_ticks avg={_avg:.0f}ms min={_mn:.0f}ms "
                f"max={_mx:.0f}ms trend={_trend} | {ctx()}"
            )
            self._tick_times.clear()

        # Per-cycle full output dump (observability). One JSON line per cycle
        # at data/logs/layer1c_full.jsonl. Defensive: any failure is swallowed
        # and logged at DEBUG — strategy ticks are critical-path and must
        # never break on observability code.
        self._dump_cycle_data(
            sid=_sid,
            cycle_el_ms=_cycle_el,
            section_ms=_section_ms,
            universe=universe,
            regime=regime,
            coin_regimes=coin_regimes,
            raw_signals=raw_signals,
            scored=scored,
            consensus_setups=consensus_setups,
            filtered=filtered,
            hints=hints,
            mode=mode,
            layer_manager=layer_manager,
        )

    def _dump_cycle_data(
        self,
        *,
        sid: str,
        cycle_el_ms: float,
        section_ms: dict,
        universe: list,
        regime,
        coin_regimes: dict,
        raw_signals: list,
        scored: list,
        consensus_setups: list,
        filtered: list,
        hints: list,
        mode: dict,
        layer_manager,
    ) -> None:
        """Append one JSON record per L1C cycle to layer1c_full.jsonl.

        Captures complete per-coin output of every sub-layer (L1 signals,
        L2 scores with components, L3 consensus votes, L4 hints + caches)
        plus full timing breakdown. Defensive — never raises.
        """
        try:
            import json as _json
            from datetime import datetime, timezone
            from pathlib import Path

            def _side(d):
                return d.value if hasattr(d, "value") else (str(d) if d is not None else None)

            def _regime_name(r):
                if r is None:
                    return None
                inner = getattr(r, "regime", r)
                return _side(inner)

            l1_signals = []
            for s in raw_signals or []:
                l1_signals.append({
                    "symbol": getattr(s, "symbol", None),
                    "direction": _side(getattr(s, "direction", None)),
                    "strategy": getattr(s, "strategy_name", None),
                    "confidence": float(getattr(s, "confidence", 0) or 0),
                })

            l2_scored = []
            for ss in scored or []:
                rs = getattr(ss, "raw_signal", None)
                l2_scored.append({
                    "symbol": getattr(rs, "symbol", None),
                    "direction": _side(getattr(rs, "direction", None)),
                    "strategy": getattr(rs, "strategy_name", None),
                    "total_score": float(getattr(ss, "total_score", 0) or 0),
                    "base_score": float(getattr(ss, "base_score", 0) or 0),
                    "confluence_score": float(getattr(ss, "confluence_score", 0) or 0),
                    "context_score": float(getattr(ss, "context_score", 0) or 0),
                    "quality_score": float(getattr(ss, "quality_score", 0) or 0),
                    "grade": getattr(ss, "grade", None),
                })

            l3_consensus = []
            for v in consensus_setups or []:
                ss = getattr(v, "scored_setup", None)
                rs = getattr(ss, "raw_signal", None) if ss is not None else None
                l3_consensus.append({
                    "symbol": getattr(rs, "symbol", None),
                    "direction": _side(getattr(rs, "direction", None)),
                    "strategy": getattr(rs, "strategy_name", None),
                    "consensus_strength": getattr(v, "consensus_strength", None),
                    "size_multiplier": float(getattr(v, "size_multiplier", 0) or 0),
                    "total_score": float(getattr(ss, "total_score", 0) or 0) if ss else 0.0,
                })

            consensus_cache = {}
            votes_cache_size = 0
            if layer_manager is not None:
                try:
                    consensus_cache = dict(getattr(layer_manager, "_strategy_consensus", {}) or {})
                    votes_cache_size = len(getattr(layer_manager, "_strategy_votes", {}) or {})
                except Exception:
                    pass

            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "sid": sid,
                "cycle_ms": round(float(cycle_el_ms), 1),
                "timing_ms": {
                    "gate": round(float(section_ms.get("gate", 0)), 1),
                    "prefetch": round(float(section_ms.get("prefetch", 0)), 1),
                    "prefetch_db": round(float(section_ms.get("prefetch_db", 0)), 1),
                    "prefetch_ta": round(float(section_ms.get("prefetch_ta", 0)), 1),
                    "prefetch_h1_db": round(float(section_ms.get("prefetch_db_h1", 0)), 1),
                    "prefetch_h1_ta": round(float(section_ms.get("prefetch_ta_h1", 0)), 1),
                    "l1": round(float(section_ms.get("l1", 0)), 1),
                    "l2": round(float(section_ms.get("l2", 0)), 1),
                    "l3": round(float(section_ms.get("l3", 0)), 1),
                    "l4": round(float(section_ms.get("l4", 0)), 1),
                },
                "input": {
                    "universe_size": len(universe or []),
                    "universe": list(universe or []),
                    "regime_global": _regime_name(regime),
                    "regime_per_coin": {
                        k: _regime_name(v) for k, v in (coin_regimes or {}).items()
                    },
                    "h1_cache_lookups": int(section_ms.get("prefetch_h1_lookups", 0)),
                    "h1_cache_valid": int(section_ms.get("prefetch_h1_valid", 0)),
                    "h1_cache_recomputed": int(section_ms.get("prefetch_h1_recomputed", 0)),
                },
                "l1_output": {
                    "signal_count": len(l1_signals),
                    "signals": l1_signals,
                },
                "l2_output": {
                    "scored_count": len(l2_scored),
                    "scored": l2_scored,
                },
                "l3_output": {
                    "consensus_count": len(l3_consensus),
                    "consensus": l3_consensus,
                },
                "l4_output": {
                    "pnl_mode": mode.get("mode") if isinstance(mode, dict) else str(mode),
                    "filtered_count": len(filtered or []),
                    "hints": list(hints or []),
                    "consensus_cache_size": len(consensus_cache),
                    "consensus_cache": consensus_cache,
                    "votes_cache_size": votes_cache_size,
                },
            }

            log_dir = getattr(getattr(self.settings, "general", None), "log_dir", "data/logs")
            path = Path(log_dir) / "layer1c_full.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                f.write(_json.dumps(record, default=str) + "\n")
            log.info(
                f"L1C_DUMP_WRITE | path={path} bytes_signals={len(l1_signals)} "
                f"scored={len(l2_scored)} consensus={len(l3_consensus)} "
                f"hints={len(hints or [])} | {ctx()}"
            )
        except Exception as _e:
            log.debug(f"L1C_DUMP_FAIL | err='{str(_e)[:200]}' | {ctx()}")

    def get_score(self, coin: str) -> float | None:
        """Return the most recent L2 total_score for ``coin``, or None.

        Public accessor consumed by Phase 6's new ScannerWorker for the
        composite opportunity score. The cache is populated each tick at
        sweet spot 1:30 after Layer 2 scoring. Coins that did not produce
        a raw signal in the current tick will not be in the cache —
        ``None`` is the truthful answer.
        """
        return self._score_cache.get(coin)

    # ─── Strategy Consensus Summary ──────────────────────────────────

    def _build_consensus_summary(self, setups) -> dict:
        """Build a per-coin consensus summary for Claude."""
        consensus = {}
        for sw in setups:
            try:
                setup = sw.scored_setup if hasattr(sw, "scored_setup") else sw
                symbol = setup.raw_signal.symbol
                direction = setup.raw_signal.direction.value if hasattr(setup.raw_signal.direction, "value") else str(setup.raw_signal.direction)
                if symbol not in consensus:
                    consensus[symbol] = {"buy": 0, "sell": 0, "total_score": 0}
                if direction.lower() in ("buy", "long"):
                    consensus[symbol]["buy"] += 1
                else:
                    consensus[symbol]["sell"] += 1
                consensus[symbol]["total_score"] += setup.total_score
            except Exception as e:
                log.debug("build consensus summary entry failed: {err}", err=str(e))
        return consensus

    def _build_per_coin_consensus(
        self, setups, coin_regimes=None, global_regime=None,
    ) -> dict[str, dict]:
        """Build per-coin consensus payload for Layer 1 Phase 3.

        Returns a dict keyed by symbol with the per-coin consensus
        category, score, vote count, direction, last-updated timestamp,
        and (Issue E25) the regime this coin was SCORED under this cycle.
        ScannerWorker's Phase 5 qualitative filter reads this cache via
        ``layer_manager.get_strategy_consensus(symbol)``.

        For each EnsembleResult-like wrapper, the highest-scoring setup
        per symbol wins (ties broken by first-seen). LEAN is preserved
        as a fifth category but Phase 5 maps it as failing the GOOD
        threshold by default.

        Issue E25 (2026-05-28): ``coin_regimes``/``global_regime`` are the
        SAME fresh per-cycle snapshot the scoring loop used
        (``symbol_regime = coin_regimes.get(symbol, regime)``), so the
        ``scoring_regime`` tagged here is, by construction, the exact
        regime the published consensus/votes were computed under. The
        scanner carries it onto the package and the brain renders it, so
        the regime LABEL the brain shows matches the scores beside it
        instead of a separately re-read (possibly drifted) cache value.
        """
        import time as _t
        coin_regimes = coin_regimes or {}
        out: dict[str, dict] = {}
        for sw in setups:
            try:
                setup = sw.scored_setup if hasattr(sw, "scored_setup") else sw
                symbol = setup.raw_signal.symbol
                direction = (
                    setup.raw_signal.direction.value
                    if hasattr(setup.raw_signal.direction, "value")
                    else str(setup.raw_signal.direction)
                ).lower()
                # Map "buy"/"long" → "long"; "sell"/"short" → "short"; else neutral.
                if direction in ("buy", "long"):
                    direction = "long"
                elif direction in ("sell", "short"):
                    direction = "short"
                else:
                    direction = "neutral"

                consensus = (
                    getattr(sw, "consensus_strength", "GOOD")
                    if hasattr(sw, "consensus_strength") else "GOOD"
                )
                consensus_score = float(getattr(sw, "size_multiplier", 0.5) or 0.5)
                vote_count = len(getattr(sw, "votes", []) or [])

                # Issue E25 + per-coin-authority Phase 2 (2026-05-29): the regime
                # this symbol was SCORED under — the SAME resolution the scan loop
                # uses now (per-coin, else explicit UNKNOWN, NEVER the global BTC
                # regime). Keeps the scoring_regime tag consistent with the roster
                # the coin actually voted under. (global_regime param retained for
                # caller compatibility; no longer used as the fallback.)
                _sr = coin_regimes.get(symbol) or RegimeState.unknown()
                _scoring_regime = ""
                # Issue #2 (2026-05-31): capture the SCORED regime's own metrics
                # beside the word, so the brain's candidate `Regime:` line can show
                # the scoring word WITH the numbers from the SAME snapshot (word and
                # metrics finally describe one regime), instead of pairing the
                # scoring word with the live-cache metrics of a possibly-drifted
                # regime. `_sr` already holds every metric here. Defaults are
                # neutral so an unscored/UNKNOWN coin renders cleanly and the
                # pre-#2/E25 live-cache fallback still applies.
                _sr_conf = _sr_adx = _sr_atr = _sr_chop = _sr_volr = 0.0
                _sr_trend = 0
                _sr_volr_known = True
                if _sr is not None:
                    _rv = getattr(_sr, "regime", None)
                    _scoring_regime = getattr(_rv, "value", None) or (str(_rv) if _rv else "")
                    _sr_conf = float(getattr(_sr, "confidence", 0.0) or 0.0)
                    _sr_adx = float(getattr(_sr, "adx", 0.0) or 0.0)
                    _sr_atr = float(getattr(_sr, "atr_percentile", 0.0) or 0.0)
                    _sr_chop = float(getattr(_sr, "choppiness", 0.0) or 0.0)
                    _sr_volr = float(getattr(_sr, "volume_ratio", 0.0) or 0.0)
                    _sr_trend = int(getattr(_sr, "trend_direction", 0) or 0)
                    _sr_volr_known = bool(getattr(_sr, "volume_ratio_known", True))

                existing = out.get(symbol)
                # Take the strongest (highest total_score) setup per symbol.
                if existing is None or setup.total_score > existing.get("_score_seed", 0):
                    out[symbol] = {
                        "consensus": consensus,
                        "consensus_score": round(consensus_score, 4),
                        "vote_count": vote_count,
                        "direction": direction,
                        "scoring_regime": _scoring_regime,
                        # Issue #2: scored-regime metrics (see comment above).
                        "scoring_regime_confidence": round(_sr_conf, 4),
                        "scoring_regime_adx": round(_sr_adx, 2),
                        "scoring_regime_atr_percentile": round(_sr_atr, 2),
                        "scoring_regime_choppiness": round(_sr_chop, 2),
                        "scoring_regime_volume_ratio": round(_sr_volr, 2),
                        "scoring_regime_volume_ratio_known": _sr_volr_known,
                        "scoring_regime_trend_direction": _sr_trend,
                        "last_updated": _t.time(),
                        "_score_seed": setup.total_score,
                    }
            except Exception as e:
                log.debug("build per-coin consensus entry failed: {err}", err=str(e))

        # Strip the internal scoring seed before publishing.
        for v in out.values():
            v.pop("_score_seed", None)
        return out

    def _build_per_coin_votes(self, setups) -> dict[str, dict]:
        """Phase 2 of the 1D briefing rewrite — per-coin vote distribution.

        Returns a dict keyed by symbol with the FULL ensemble vote
        distribution (one inner dict per voting strategy) plus the
        same aggregate fields ``_build_per_coin_consensus`` produces.

        Selection rule mirrors ``_build_per_coin_consensus``: the
        highest-``total_score`` setup per symbol wins, so the cached
        votes correspond to the same ScoredSetup the consensus aggregate
        was computed from. This guarantees the two caches stay
        consistent — the Phase 4 ranker can read either side without
        worrying about which setup variant the votes belong to.

        For each surviving symbol the inner ``"votes"`` dict is built
        via ``EnsembleResult.vote_distribution_dict()`` (truncates
        reasoning at 140 chars to keep the cache bounded — matches the
        ~320 KB / 50 coins × 25 strategies budget called out in
        ``layer_manager._strategy_votes`` documentation).

        Args:
            setups: Iterable of EnsembleResult-like wrappers (the same
                ``consensus_setups`` collection that
                ``_build_per_coin_consensus`` consumes).

        Returns:
            ``{symbol: {"votes", "buy_weighted", "sell_weighted",
            "neutral_weighted", "consensus", "consensus_direction",
            "size_multiplier", "last_updated"}}``. Empty when no
            wrapper exposes the ``votes`` attribute (e.g. a degraded
            mock or an unwrapped ``ScoredSetup``).
        """
        import time as _t
        out: dict[str, dict] = {}
        for sw in setups:
            try:
                setup = sw.scored_setup if hasattr(sw, "scored_setup") else sw
                symbol = setup.raw_signal.symbol

                # Skip wrappers that don't carry an EnsembleResult-shape
                # ``votes`` list — happens when the upstream pipeline
                # passes raw ScoredSetup wrappers (e.g. early-cycle
                # configurations). The aggregate consensus cache still
                # builds; only the full vote distribution is unavailable.
                if not hasattr(sw, "votes"):
                    continue

                # Reuse the public helper so the truncation policy
                # lives in one place. Falls back to a manual build only
                # when ``vote_distribution_dict`` isn't present (very
                # old wrappers). Either way, never raises.
                if hasattr(sw, "vote_distribution_dict"):
                    votes_dict = sw.vote_distribution_dict()
                else:
                    votes_dict = {}
                    for v in (getattr(sw, "votes", []) or []):
                        try:
                            reasoning = getattr(v, "reasoning", "") or ""
                            if len(reasoning) > 140:
                                reasoning = reasoning[:140]
                            votes_dict[v.strategy_name] = {
                                "vote": v.vote,
                                "confidence": float(v.confidence),
                                "weight": float(v.weight),
                                "reasoning": reasoning,
                            }
                        except Exception:
                            continue

                consensus = (
                    getattr(sw, "consensus_strength", "GOOD") or "GOOD"
                )
                consensus_dir = (
                    getattr(sw, "consensus_direction", "") or ""
                )
                buy_weighted = float(getattr(sw, "buy_votes", 0.0) or 0.0)
                sell_weighted = float(getattr(sw, "sell_votes", 0.0) or 0.0)
                neutral_weighted = float(getattr(sw, "neutral_votes", 0.0) or 0.0)
                size_multiplier = float(getattr(sw, "size_multiplier", 0.5) or 0.5)
                # P2 (2026-06-04) — honest opposite-direction tally + flag.
                opposing_weighted = float(
                    getattr(sw, "opposing_votes", 0.0) or 0.0
                )
                two_sided = bool(getattr(sw, "two_sided_active", False))

                existing = out.get(symbol)
                # Tie-break with the same rule as the consensus cache:
                # highest total_score wins.
                if existing is None or setup.total_score > existing.get("_score_seed", 0):
                    out[symbol] = {
                        "votes": votes_dict,
                        "buy_weighted": round(buy_weighted, 4),
                        "sell_weighted": round(sell_weighted, 4),
                        "neutral_weighted": round(neutral_weighted, 4),
                        "opposing_weighted": round(opposing_weighted, 4),
                        "two_sided": two_sided,
                        "consensus": consensus,
                        "consensus_direction": consensus_dir,
                        "size_multiplier": round(size_multiplier, 4),
                        "last_updated": _t.time(),
                        "_score_seed": setup.total_score,
                    }
            except Exception as e:
                log.debug(
                    "build per-coin votes entry failed: {err}", err=str(e),
                )

        # Strip the internal scoring seed before publishing.
        for v in out.values():
            v.pop("_score_seed", None)
        return out

    # ─── Claude Direct Trade Execution ─────────────────────────────────

    async def _derive_wr_aware_override_threshold(
        self, flipped_dir: str,
    ) -> tuple[float, dict]:
        """Compute the XRAY override threshold from per-direction WR.

        R3 direction-fix (2026-05-17). Queries trade_log for the last
        N closed trades and computes win rate per direction. The
        threshold for overriding INTO ``flipped_dir`` derives as:

            threshold = wr_base * (1 - flipped_dir_wr_fraction)

        bounded by [floor, ceiling]. With neutral 50% WR the threshold
        equals wr_base * 0.5 (the midpoint of the legacy 3-10x dead
        zone). With high flipped-dir WR the threshold drops (easier
        to override into the better-WR direction). Same direction-
        agnostic formula applies whether flipped_dir is Buy or Sell;
        the asymmetry between them comes from the data, not the code.

        Cold-start fallback: when fewer than wr_window_min recent
        trades exist for the relevant direction, return the legacy
        static ``xray_lock_override_ratio_threshold`` (default 10.0)
        and source='cold_start'.

        Args:
            flipped_dir: "Buy" or "Sell" — the direction the XRAY flip
                would override INTO.

        Returns:
            (threshold, meta) where meta is a dict carrying buy_wr,
            sell_wr, buy_n, sell_n, source ('wr' | 'cold_start').
            Read-only; never mutates DB.
        """
        risk = getattr(self.settings, "risk", None)
        wr_base = float(getattr(risk, "xray_lock_override_wr_base", 10.0))
        wr_floor = float(getattr(risk, "xray_lock_override_wr_floor", 2.0))
        wr_ceiling = float(getattr(risk, "xray_lock_override_wr_ceiling", 15.0))
        wr_window = int(getattr(risk, "xray_lock_override_wr_window_trades", 200))
        wr_window_min = int(getattr(risk, "xray_lock_override_wr_window_min", 30))
        legacy = float(getattr(risk, "xray_lock_override_ratio_threshold", 10.0))

        meta: dict = {
            "buy_wr": 0.0,
            "sell_wr": 0.0,
            "buy_n": 0,
            "sell_n": 0,
            "source": "cold_start",
        }

        db = self.services.get("db") if self.services else None
        if db is None:
            meta["source"] = "no_db"
            return legacy, meta

        try:
            # DatabaseManager.fetch_all is the project's canonical read
            # path — wraps aiosqlite's connection pool + returns
            # list[dict[str, Any]]. Same pattern used by
            # performance_enforcer.py, pnl_manager, portfolio/*, telegram
            # handlers, brain handlers (~10 production call sites).
            rows = await db.fetch_all(
                "SELECT direction, pnl_usd FROM trade_log "
                "WHERE closed_at != '' "
                "ORDER BY closed_at DESC LIMIT ?",
                (wr_window,),
            )
        except Exception as e:
            # Defensive: a DB error must not crash the override decision.
            # Fall back to legacy threshold; the existing 10.0 value still
            # admits extreme structural cases.
            log.warning(
                f"XRAY_OVERRIDE_WR_QUERY_FAIL | err='{str(e)[:80]}' "
                f"| {ctx()}"
            )
            meta["source"] = "query_fail"
            return legacy, meta

        # rows is a list[dict[str, Any]]; each dict has the SELECT columns.
        buy_trades = [r for r in rows if r.get("direction") == "Buy"]
        sell_trades = [r for r in rows if r.get("direction") == "Sell"]

        def _wr(trades: list[dict]) -> tuple[float, int]:
            """Compute win rate (percentage 0-100) and count over a list
            of trade_log rows. Defensive ``get("pnl_usd", 0.0)`` handles
            legacy rows where pnl_usd was historically NULL.
            """
            if not trades:
                return 0.0, 0
            wins = sum(1 for t in trades if (t.get("pnl_usd") or 0.0) > 0)
            return wins / len(trades) * 100.0, len(trades)

        buy_wr, buy_n = _wr(buy_trades)
        sell_wr, sell_n = _wr(sell_trades)
        meta["buy_wr"] = round(buy_wr, 2)
        meta["sell_wr"] = round(sell_wr, 2)
        meta["buy_n"] = buy_n
        meta["sell_n"] = sell_n

        flipped_dir_n = buy_n if flipped_dir == "Buy" else sell_n
        flipped_dir_wr = buy_wr if flipped_dir == "Buy" else sell_wr

        if flipped_dir_n < wr_window_min:
            meta["source"] = "cold_start"
            return legacy, meta

        derived = wr_base * (1.0 - flipped_dir_wr / 100.0)
        derived = max(wr_floor, min(wr_ceiling, derived))
        meta["source"] = "wr"
        return derived, meta

    async def _execute_claude_trade(
        self, trade: dict, position_symbols: set, plan,
    ) -> tuple[bool, str]:
        """Execute a single trade from Claude's new_trades list.

        Validates SL/TP against current price, rounds qty, places order,
        registers with coordinator, and sends Telegram alert.

        Returns:
            (success, reason_code). On success: (True, "ok"). On failure:
            (False, <reason_code>) where reason_code is one of a fixed enum
            ("sanity_reject", "enforcer_block", "survival_block", "xray_skip",
             "xray_conflict", "unsupported_symbol", "dup_position",
             "service_missing", "price_fetch_fail", "price_invalid",
             "sltp_skip", "entry_volume_gate_blocked", "qty_zero",
             "order_reject"). Caller logs TRADE_SKIP.
        """
        import math

        symbol = trade.get("symbol", "")
        direction = trade.get("direction", "")
        if not symbol or not direction:
            log.warning(
                f"TRADE_SKIP | sym={symbol or '?'} rsn=sanity_reject "
                f"detail='empty symbol or direction' | {ctx()}"
            )
            return (False, "sanity_reject")

        # Phase 2 (Layer 3 enforcement) — Approach C, capture-and-pass.
        # Capture the layer_active snapshot AT THE START of the directive
        # → execution chain, BEFORE the per-symbol enforcer / X-RAY /
        # price-fetch / qty-rounding work runs. Pass it through to
        # OrderService.place_order; if the live LayerManager view
        # disagrees with the snapshot at placement time AND purpose ==
        # layer3_entry, OrderService raises Layer3RaceError. Without this
        # capture, the live gate at OrderService still blocks (safety
        # preserved), but operators lose the ability to identify the
        # specific race condition.
        _lm = self.services.get("layer_manager") if self.services else None
        _layer_snapshot = (
            _lm.snapshot_layer_state()
            if _lm and hasattr(_lm, "snapshot_layer_state")
            else None
        )

        # Enforcer check — Phase 4 of dir-block-fix (2026-05-05).
        # Leverage limits are now CLAMPED, never BLOCKED, when the
        # enforcement level is elevated. Position-count, score, and
        # confluence/RR limits remain blocking via downstream gates
        # (qualify_survival_trade, layer_manager). The aggressive aim
        # requires defensive modes to slow trades, not stop them.
        enforcer = getattr(self, '_enforcer', None)
        if enforcer and hasattr(enforcer, 'clamp_leverage'):
            _lev_req = int(trade.get("leverage", 2))
            _lev_clamped, _clamp_reason = enforcer.clamp_leverage(_lev_req)
            if _lev_clamped < _lev_req:
                trade["leverage"] = _lev_clamped
                log.warning(
                    f"ENFORCER_LEV_CLAMP | sym={symbol} dir={direction} "
                    f"requested={_lev_req} clamped={_lev_clamped} "
                    f"reason='{_clamp_reason}' | {ctx()}"
                )
        # Preserve the should_allow_trade call for compatibility — it
        # now always returns (True, "ok") under Phase 4. If a future
        # change re-introduces a hard-block path the existing
        # STRAT_EXEC_BLOCKED / TRADE_SKIP handling kicks back in
        # automatically.
        if enforcer and hasattr(enforcer, 'should_allow_trade'):
            _lev = int(trade.get("leverage", 2))
            allowed, reason = enforcer.should_allow_trade(leverage=_lev)
            if not allowed:
                log.warning(
                    f"STRAT_EXEC_BLOCKED | sym={symbol} dir={direction} "
                    f"rsn='{reason}' | {ctx()}"
                )
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn=enforcer_block "
                    f"detail='{str(reason)[:80]}' | {ctx()}"
                )
                return (False, "enforcer_block")

        # X-RAY quality gates (SURVIVAL + ALL modes)
        _sc = self.services.get("structure_cache") if self.services else None

        # SURVIVAL mode quality restriction
        if enforcer and hasattr(enforcer, 'qualify_survival_trade'):
            qual_ok, qual_reason = enforcer.qualify_survival_trade(symbol, _sc)
            if not qual_ok:
                # CALL_B Framing Fix Phase 2B (2026-05-06) — when the
                # gate fires solely because the X-RAY's structural RR
                # is below `level_2_min_rr` (e.g., "rr_2.5_below_3.0"),
                # attempt to scale TP outward to achieve the floor
                # within a structural buffer (50% beyond structural
                # target). Adjustment-not-block matches the operator's
                # aggressive-exploitation aim. HALTED (level 3) is
                # untouched — the helper returns "halted" and we fall
                # through to the legacy block.
                _adjusted = False
                if (
                    qual_reason.startswith("rr_")
                    and not qual_reason.startswith("rr_scale_")
                    and qual_reason != "halted"
                    and hasattr(enforcer, "try_adjust_for_survival_rr")
                ):
                    _new_tp, _adjust_reason, _old_rr, _new_rr = (
                        enforcer.try_adjust_for_survival_rr(symbol, direction, _sc)
                    )
                    if _new_tp is not None:
                        _orig_tp = float(trade.get("take_profit_price", 0) or 0)
                        trade["take_profit_price"] = float(_new_tp)
                        if "tp" in trade:
                            trade["tp"] = float(_new_tp)
                        log.warning(
                            f"ENFORCER_RR_ADJUSTED | sym={symbol} dir={direction} "
                            f"requested_rr={_old_rr:.2f} floor={enforcer._l2_min_rr:.2f} "
                            f"final_rr={_new_rr:.2f} orig_tp={_orig_tp:.6f} "
                            f"new_tp={_new_tp:.6f} reason='{_adjust_reason}' | {ctx()}"
                        )
                        _adjusted = True
                    else:
                        log.warning(
                            f"ENFORCER_RR_ADJUST_FAIL | sym={symbol} dir={direction} "
                            f"old_rr={_old_rr:.2f} floor={enforcer._l2_min_rr:.2f} "
                            f"reason='{_adjust_reason}' | {ctx()}"
                        )
                if not _adjusted:
                    log.warning(
                        f"STRAT_EXEC_BLOCKED | sym={symbol} dir={direction} "
                        f"rsn='survival_quality: {qual_reason}' | {ctx()}"
                    )
                    log.warning(
                        f"TRADE_SKIP | sym={symbol} rsn=survival_block "
                        f"detail='{str(qual_reason)[:80]}' | {ctx()}"
                    )
                    return (False, "survival_block")

        # X-RAY Quality Gate — applies in ALL modes (not just SURVIVAL)
        if _sc:
            _structural = _sc.get(symbol)
            if _structural:
                # X-RAY Trade-Suppression Switch
                # (IMPLEMENT_XRAY_SUPPRESS_SWITCH, 2026-05-25). Master gate
                # for ALL five X-RAY trade-suppression points below. When
                # False (operator default), each would-be block is written
                # to the booklog (XRAY_BOOKLOG) and the brain-then-APEX
                # direction proceeds; X-RAY analysis is unchanged. Failure-
                # safe to False (no suppression) to match the OFF default.
                # Read once here so every block honors one value.
                _xray_suppression_enabled = bool(getattr(
                    getattr(self.settings, "risk", None),
                    "xray_trade_suppression_enabled",
                    False,
                ))
                # Block: SKIP quality + bad R:R (structurally invalid setup)
                _sp = _structural.structural_placement
                _rr = _sp.rr_ratio if _sp else None
                if _structural.setup_quality == "SKIP" and _rr is not None and _rr < 0.5:
                    if _xray_suppression_enabled:
                        log.warning(
                            f"XRAY_BLOCK | sym={symbol} quality={_structural.setup_quality} "
                            f"rr={_rr:.1f} | Trade rejected — structurally invalid | {ctx()}"
                        )
                        log.warning(
                            f"TRADE_SKIP | sym={symbol} rsn=xray_skip "
                            f"detail='quality=SKIP rr={_rr:.2f}' | {ctx()}"
                        )
                        return (False, "xray_skip")
                    log.warning(
                        f"XRAY_BOOKLOG | sym={symbol} intended={direction} "
                        f"decision={direction} would_rsn=xray_skip "
                        f"reason=structurally_invalid "
                        f"quality={_structural.setup_quality} rr={_rr:.2f} "
                        f"mode=booklog | X-RAY suppression disabled — "
                        f"analysis recorded, trade proceeds | {ctx()}"
                    )
                    trade["_xray_suppression_booklog"] = True
                    # fall through (no return)

                # Block: Direction conflicts with structural trend + weak quality
                _ms = _structural.market_structure
                if _ms and _ms.structure in ("uptrend", "downtrend"):
                    _conflict = (
                        (_ms.structure == "uptrend" and direction == "Sell") or
                        (_ms.structure == "downtrend" and direction == "Buy")
                    )
                    if _conflict and _structural.setup_quality in ("SKIP", "C"):
                        if _xray_suppression_enabled:
                            log.warning(
                                f"XRAY_CONFLICT | sym={symbol} dir={direction} "
                                f"struct={_ms.structure} quality={_structural.setup_quality} "
                                f"| Direction conflicts with structure — blocked | {ctx()}"
                            )
                            log.warning(
                                f"TRADE_SKIP | sym={symbol} rsn=xray_conflict "
                                f"detail='dir={direction} struct={_ms.structure} q={_structural.setup_quality}' | {ctx()}"
                            )
                            return (False, "xray_conflict")
                        log.warning(
                            f"XRAY_BOOKLOG | sym={symbol} intended={direction} "
                            f"decision={direction} would_rsn=xray_conflict "
                            f"reason=direction_conflicts_structure "
                            f"struct={_ms.structure} "
                            f"quality={_structural.setup_quality} "
                            f"mode=booklog | X-RAY suppression disabled — "
                            f"analysis recorded, trade proceeds | {ctx()}"
                        )
                        trade["_xray_suppression_booklog"] = True
                        # fall through (no return)

                # Warning: Claude chose the direction with worse R:R (not a block)
                if _sp and _sp.rr_long > 0 and _sp.rr_short > 0:
                    if direction == "Buy" and _sp.rr_long < 1.0 and _sp.rr_short >= 2.0:
                        log.warning(
                            f"XRAY_DIR_MISMATCH | sym={symbol} dir=Buy "
                            f"rr_long={_sp.rr_long:.1f} rr_short={_sp.rr_short:.1f} "
                            f"| Claude chose Buy but SHORT has better R:R | {ctx()}"
                        )
                    elif direction == "Sell" and _sp.rr_short < 1.0 and _sp.rr_long >= 2.0:
                        log.warning(
                            f"XRAY_DIR_MISMATCH | sym={symbol} dir=Sell "
                            f"rr_long={_sp.rr_long:.1f} rr_short={_sp.rr_short:.1f} "
                            f"| Claude chose Sell but LONG has better R:R | {ctx()}"
                        )

                    # P0-2 fix (2026-05-22) — direction-decision authority.
                    # Replaces the pre-P0-2 flip + suppress + override
                    # branches (which emitted dual logging: APEX_DIR_LOCK
                    # | dir=Buy + XRAY_DIR_FLIP | flipped_dir=Sell on the
                    # same trade, with the placed direction being XRAY's
                    # silent reversal of brain's high-conviction Buy).
                    #
                    # The new authority:
                    #   1. Brain emits direction. APEX may optimize/lock
                    #      but does not reverse. XRAY reads structural-rr.
                    #   2. If XRAY-rr disagrees (ratio > flip_threshold)
                    #      AND the brain's directive is HIGH-CONVICTION
                    #      (per-coin regime aligns AND structural_data
                    #      .trade_direction agrees), XRAY VETOES: trade
                    #      skipped with single DIRECTION_DECISION log
                    #      action=veto authority=XRAY. No flip, no order.
                    #   3. If XRAY-rr disagrees AND brain is LOW-CONVICTION
                    #      (volatile/ranging regime, or trade_direction
                    #      disagrees), XRAY may flip — but emits a single
                    #      DIRECTION_DECISION line covering the full
                    #      decision (no paired APEX_DIR_LOCK + XRAY_DIR_FLIP).
                    #   4. If XRAY-rr agrees (ratio <= flip_threshold),
                    #      the brain's direction proceeds unchanged with
                    #      no DIRECTION_DECISION log (no decision was made).
                    #
                    # Preserves the existing structural-validity blocks
                    # (XRAY_DIR_BLOCK on missing dual levels,
                    # XRAY_DIR_FLIP_BLOCKED on post-flip structural
                    # conflict) as veto branches. Anti-pattern guard:
                    # ratio is not clamped; XRAY is not disabled; threshold
                    # is unchanged (3.0); high-conviction protection is
                    # an authority redefinition only. See
                    # dev_notes/p0_fixes/02_p0_2_rootcause.md and
                    # config.toml [risk] xray_high_conviction_protection_
                    # enabled (kill-switch, default true).
                    _ratio = 0.0
                    if direction == "Buy" and _sp.rr_long > 0:
                        _ratio = _sp.rr_short / _sp.rr_long
                    elif direction == "Sell" and _sp.rr_short > 0:
                        _ratio = _sp.rr_long / _sp.rr_short

                    _flip_threshold = float(
                        getattr(
                            getattr(self.settings, "risk", None),
                            "xray_dir_flip_threshold_ratio",
                            3.0,
                        )
                    )
                    # X-RAY Direction-Flip Switch
                    # (IMPLEMENT_XRAY_FLIP_SWITCH, 2026-05-25). Master gate
                    # for the reversal below. Default False; failure-safe to
                    # off (off = defer to the sanctioned brain-then-APEX
                    # direction, which is the blueprint behavior).
                    _xray_flip_enabled = bool(getattr(
                        getattr(self.settings, "risk", None),
                        "xray_dir_flip_enabled",
                        False,
                    ))

                    _apex_locked = bool(trade.get("_apex_locked"))
                    _apex_lock_reason = str(
                        trade.get("_apex_lock_reason", "") or "",
                    )

                    # High-conviction definition: per-coin regime aligns
                    # with brain direction AND structural_data
                    # .trade_direction (R1 ALPHA plumbing) also agrees.
                    # Read regime from regime_detector cache (safe
                    # fallback to ""). trade_direction is stored on the
                    # StructureAnalysis at _structural.trade_direction
                    # (lowercase "long" / "short" / "").
                    _high_conviction_enabled = bool(getattr(
                        getattr(self.settings, "risk", None),
                        "xray_high_conviction_protection_enabled",
                        True,
                    ))
                    _coin_regime_str = ""
                    try:
                        _rd = getattr(self, "regime_detector", None)
                        if _rd is not None:
                            _cr_map = getattr(
                                _rd, "_per_coin_regimes", {},
                            ) or {}
                            _cr_obj = _cr_map.get(symbol)
                            if _cr_obj is not None:
                                _cr_val = getattr(_cr_obj, "regime", None)
                                _coin_regime_str = str(
                                    getattr(_cr_val, "value", _cr_val) or "",
                                ).lower()
                    except Exception:
                        _coin_regime_str = ""

                    _trade_direction_str = str(
                        getattr(_structural, "trade_direction", "") or "",
                    ).lower()

                    _regime_aligned = (
                        (_coin_regime_str == "trending_up"
                         and direction == "Buy")
                        or (_coin_regime_str == "trending_down"
                            and direction == "Sell")
                    )
                    _trade_direction_aligned = (
                        (_trade_direction_str == "long"
                         and direction == "Buy")
                        or (_trade_direction_str == "short"
                            and direction == "Sell")
                    )
                    _high_conviction = (
                        _high_conviction_enabled
                        and _regime_aligned
                        and _trade_direction_aligned
                    )

                    # WR-aware override threshold — kept for the
                    # low-conviction branch only. Always emit
                    # XRAY_OVERRIDE_RATIO_DETAIL so the operator can
                    # audit how WR drove the threshold even when no
                    # flip ultimately fires.
                    _flipped_dir_preview = (
                        "Sell" if direction == "Buy" else "Buy"
                    )
                    (
                        _lock_override_threshold,
                        _wr_meta,
                    ) = await self._derive_wr_aware_override_threshold(
                        _flipped_dir_preview,
                    )
                    log.info(
                        f"XRAY_OVERRIDE_RATIO_DETAIL | sym={symbol} "
                        f"flipped_dir={_flipped_dir_preview} "
                        f"buy_wr={_wr_meta.get('buy_wr', 0.0):.1f} "
                        f"sell_wr={_wr_meta.get('sell_wr', 0.0):.1f} "
                        f"buy_n={_wr_meta.get('buy_n', 0)} "
                        f"sell_n={_wr_meta.get('sell_n', 0)} "
                        f"derived_threshold={_lock_override_threshold:.2f} "
                        f"xray_ratio={_ratio:.2f} "
                        f"source={_wr_meta.get('source', 'na')} | {ctx()}"
                    )

                    _xray_disagrees = _ratio > _flip_threshold

                    # When the high-conviction veto is booklogged (suppression
                    # OFF), this flag keeps the trade OUT of the LOW-CONVICTION
                    # flip path below even if the flip switch is ON — a
                    # high-conviction directive executes in the brain's
                    # direction, never reversed (preserves the veto's intent
                    # and the operator's "execute Claude's direction").
                    _hc_veto_booklogged = False

                    if _xray_disagrees and _high_conviction:
                        if _xray_suppression_enabled:
                            # HIGH-CONVICTION VETO: skip the trade. Do not
                            # reverse the brain's direction. Single
                            # DIRECTION_DECISION line; the trade is not placed.
                            log.warning(
                                f"DIRECTION_DECISION | sym={symbol} "
                                f"intended={direction} decision=skip "
                                f"authority=XRAY action=veto "
                                f"reason=high_conviction_disagrees_with_structure "
                                f"coin_regime={_coin_regime_str or 'na'} "
                                f"trade_direction={_trade_direction_str or 'na'} "
                                f"rr_long={_sp.rr_long:.2f} "
                                f"rr_short={_sp.rr_short:.2f} "
                                f"ratio={_ratio:.1f}x "
                                f"apex_locked={int(_apex_locked)} "
                                f"apex_lock_reason='{_apex_lock_reason[:80]}' "
                                f"| {ctx()}"
                            )
                            log.warning(
                                f"TRADE_SKIP | sym={symbol} "
                                f"rsn=xray_veto_high_conviction "
                                f"detail='ratio={_ratio:.1f}x "
                                f"regime={_coin_regime_str or 'na'} "
                                f"trade_dir={_trade_direction_str or 'na'}' "
                                f"| {ctx()}"
                            )
                            trade["_xray_veto_high_conviction"] = True
                            return (False, "xray_veto_high_conviction")
                        # Suppression disabled: booklog the would-be veto and
                        # let the brain's high-conviction direction proceed.
                        # _hc_veto_booklogged below keeps it OUT of the
                        # low-conviction flip path regardless of the flip
                        # switch: flip OFF → the xray_flip_switch_off branch
                        # holds; flip ON → the high_conviction_booklog_no_flip
                        # branch holds. Either way the brain's direction
                        # executes — the veto is never converted into a
                        # reversal.
                        log.warning(
                            f"XRAY_BOOKLOG | sym={symbol} intended={direction} "
                            f"decision={direction} "
                            f"would_rsn=xray_veto_high_conviction "
                            f"reason=high_conviction_disagrees_with_structure "
                            f"coin_regime={_coin_regime_str or 'na'} "
                            f"trade_direction={_trade_direction_str or 'na'} "
                            f"rr_long={_sp.rr_long:.2f} "
                            f"rr_short={_sp.rr_short:.2f} "
                            f"ratio={_ratio:.1f}x "
                            f"mode=booklog | X-RAY suppression disabled — "
                            f"analysis recorded, trade proceeds | {ctx()}"
                        )
                        trade["_xray_suppression_booklog"] = True
                        _hc_veto_booklogged = True
                        # fall through (no return)

                    if _xray_disagrees and not _xray_flip_enabled:
                        # X-RAY Direction-Flip Switch OFF
                        # (IMPLEMENT_XRAY_FLIP_SWITCH, 2026-05-25). X-RAY's
                        # structural RR disagrees, but the operator has
                        # disabled the reversal. Do NOT flip: the sanctioned
                        # brain-then-APEX direction (and its SL/TP) flow
                        # through unchanged to execution and to every
                        # downstream consumer (sizing, thesis, coordinator,
                        # telegram, FlipTPCapper, watchdog). The
                        # high-conviction veto above is intentionally NOT
                        # gated by THIS (flip) switch (operator decision
                        # 2026-05-25: the flip switch gates the reversal
                        # only). The veto has its own kill-switch
                        # (xray_high_conviction_protection_enabled) and is
                        # ALSO gated by the trade-suppression switch
                        # (xray_trade_suppression_enabled,
                        # IMPLEMENT_XRAY_SUPPRESS_SWITCH) which booklogs the
                        # veto instead of skipping when off. One per-decision
                        # line records the suppressed flip so the switch is
                        # observably honored.
                        log.warning(
                            f"DIRECTION_DECISION | sym={symbol} "
                            f"intended={direction} "
                            f"decision={direction} "
                            f"authority=BRAIN_APEX action=hold "
                            f"reason=xray_flip_switch_off "
                            f"coin_regime={_coin_regime_str or 'na'} "
                            f"trade_direction={_trade_direction_str or 'na'} "
                            f"rr_long={_sp.rr_long:.2f} "
                            f"rr_short={_sp.rr_short:.2f} "
                            f"ratio={_ratio:.1f}x "
                            f"would_flip_to={_flipped_dir_preview} "
                            f"apex_locked={int(_apex_locked)} "
                            f"apex_lock_reason='{_apex_lock_reason[:80]}' "
                            f"| {ctx()}"
                        )
                        trade["_xray_flip_disabled_by_switch"] = True
                        # Fall through: no flip, no skip — the sanctioned
                        # direction proceeds through the rest of
                        # _execute_claude_trade unchanged.
                    elif _xray_disagrees and not _hc_veto_booklogged:
                        # LOW-CONVICTION: existing flip path with single
                        # DIRECTION_DECISION log replacing the dual
                        # APEX_DIR_LOCK / XRAY_DIR_FLIP pairing. Override
                        # gate preserved (lock holds at sub-override
                        # ratios where regime alignment legitimately wins).
                        _lock_override_active = (
                            _apex_locked
                            and _lock_override_threshold > _flip_threshold
                            and _ratio > _lock_override_threshold
                        )
                        _should_flip = (
                            _lock_override_active
                            or (not _apex_locked)
                        )

                        if not _should_flip:
                            # Lock holds, ratio below override threshold.
                            # Brain's direction stands; no flip.
                            log.warning(
                                f"DIRECTION_DECISION | sym={symbol} "
                                f"intended={direction} "
                                f"decision={direction} "
                                f"authority=APEX action=hold "
                                f"reason=lock_holds_below_override_threshold "
                                f"coin_regime={_coin_regime_str or 'na'} "
                                f"trade_direction={_trade_direction_str or 'na'} "
                                f"rr_long={_sp.rr_long:.2f} "
                                f"rr_short={_sp.rr_short:.2f} "
                                f"ratio={_ratio:.1f}x "
                                f"override_threshold={_lock_override_threshold:.2f} "
                                f"apex_locked=1 "
                                f"apex_lock_reason='{_apex_lock_reason[:80]}' "
                                f"| {ctx()}"
                            )
                            trade["_xray_flip_suppressed_by_lock"] = True
                            # Fall through to the rest of _execute_claude_trade
                            # (no flip, no skip — brain's direction proceeds).
                        else:
                            # Flip approved (no lock OR override active).
                            # Verify dual structural levels exist.
                            _flipped_dir = (
                                "Sell" if direction == "Buy" else "Buy"
                            )
                            _has_dual_levels = (
                                _sp.long_sl_price > 0
                                and _sp.long_tp_price > 0
                                and _sp.short_sl_price > 0
                                and _sp.short_tp_price > 0
                            )
                            # Suppression-switch fall-back marker. When the
                            # switch is OFF and a flip-path block below would
                            # fire, booklog and keep the brain's direction —
                            # the flip is NOT applied (it cannot be done
                            # safely without valid levels / against a
                            # conflicting structure). _flip_safe stays True
                            # only when the flip can actually proceed.
                            _flip_safe = True
                            if not _has_dual_levels:
                                if _xray_suppression_enabled:
                                    log.warning(
                                        f"DIRECTION_DECISION | sym={symbol} "
                                        f"intended={direction} decision=skip "
                                        f"authority=XRAY action=block "
                                        f"reason=missing_dual_structural_levels "
                                        f"rr_long={_sp.rr_long:.2f} "
                                        f"rr_short={_sp.rr_short:.2f} "
                                        f"ratio={_ratio:.1f}x | {ctx()}"
                                    )
                                    log.warning(
                                        f"TRADE_SKIP | sym={symbol} "
                                        f"rsn=xray_dir_block "
                                        f"detail='ratio={_ratio:.1f}x "
                                        f"no_dual_levels' | {ctx()}"
                                    )
                                    return (False, "xray_dir_block")
                                log.warning(
                                    f"XRAY_BOOKLOG | sym={symbol} "
                                    f"intended={direction} decision={direction} "
                                    f"would_rsn=xray_dir_block "
                                    f"reason=missing_dual_structural_levels "
                                    f"rr_long={_sp.rr_long:.2f} "
                                    f"rr_short={_sp.rr_short:.2f} "
                                    f"ratio={_ratio:.1f}x mode=booklog | X-RAY "
                                    f"suppression disabled — cannot flip "
                                    f"safely, keeping brain direction | {ctx()}"
                                )
                                trade["_xray_suppression_booklog"] = True
                                _flip_safe = False

                            # Post-flip structural conflict check.
                            if _ms and _ms.structure in (
                                "uptrend", "downtrend",
                            ):
                                _new_conflict = (
                                    (_ms.structure == "uptrend"
                                     and _flipped_dir == "Sell")
                                    or (_ms.structure == "downtrend"
                                        and _flipped_dir == "Buy")
                                )
                                if (
                                    _new_conflict
                                    and _structural.setup_quality
                                    in ("SKIP", "C")
                                ):
                                    if _xray_suppression_enabled:
                                        log.warning(
                                            f"DIRECTION_DECISION | sym={symbol} "
                                            f"intended={direction} decision=skip "
                                            f"authority=XRAY action=block "
                                            f"reason=post_flip_structural_conflict "
                                            f"struct={_ms.structure} "
                                            f"quality={_structural.setup_quality} "
                                            f"ratio={_ratio:.1f}x | {ctx()}"
                                        )
                                        log.warning(
                                            f"TRADE_SKIP | sym={symbol} "
                                            f"rsn=xray_dir_flip_blocked "
                                            f"detail='conflict {_flipped_dir}/"
                                            f"{_ms.structure} "
                                            f"q={_structural.setup_quality}' "
                                            f"| {ctx()}"
                                        )
                                        return (False, "xray_dir_flip_blocked")
                                    log.warning(
                                        f"XRAY_BOOKLOG | sym={symbol} "
                                        f"intended={direction} "
                                        f"decision={direction} "
                                        f"would_rsn=xray_dir_flip_blocked "
                                        f"reason=post_flip_structural_conflict "
                                        f"struct={_ms.structure} "
                                        f"quality={_structural.setup_quality} "
                                        f"ratio={_ratio:.1f}x mode=booklog | "
                                        f"X-RAY suppression disabled — flip "
                                        f"conflicts structure, keeping brain "
                                        f"direction | {ctx()}"
                                    )
                                    trade["_xray_suppression_booklog"] = True
                                    _flip_safe = False

                            # Apply the flip — mutate trade dict + local var.
                            # Guarded by _flip_safe: with the suppression
                            # switch OFF and a flip-path block above
                            # triggered, _flip_safe is False so the flip is
                            # skipped and the brain's direction stands (no
                            # skip, no unsafe flip). With the switch ON this
                            # guard is always True here (the blocks above
                            # return first); with no block triggered the
                            # flip proceeds exactly as before. Preserved from
                            # the pre-P0-2 path for downstream-consumer
                            # compatibility (thesis, coordinator, telegram).
                            if _flip_safe:
                                _orig_dir = direction
                                if _flipped_dir == "Sell":
                                    _new_sl = _sp.short_sl_price
                                    _new_tp = _sp.short_tp_price
                                    _new_rr = _sp.rr_short
                                    _orig_rr = _sp.rr_long
                                else:
                                    _new_sl = _sp.long_sl_price
                                    _new_tp = _sp.long_tp_price
                                    _new_rr = _sp.rr_long
                                    _orig_rr = _sp.rr_short
                                _orig_size = float(
                                    trade.get("size_usd", 0) or 0,
                                )
                                trade["direction"] = _flipped_dir
                                trade["stop_loss_price"] = _new_sl
                                if "sl" in trade:
                                    trade["sl"] = _new_sl
                                trade["take_profit_price"] = _new_tp
                                if not trade.get("_apex_original_direction"):
                                    trade["_apex_original_direction"] = _orig_dir
                                trade["_apex_was_flipped"] = True
                                trade["_flip_source"] = "xray"
                                trade["_xray_flip_ratio"] = round(_ratio, 2)
                                trade["_xray_flip_rr_long"] = round(
                                    float(_sp.rr_long), 2,
                                )
                                trade["_xray_flip_rr_short"] = round(
                                    float(_sp.rr_short), 2,
                                )
                                if _lock_override_active:
                                    trade["_xray_lock_overridden"] = True
                                direction = _flipped_dir

                                log.warning(
                                    f"DIRECTION_DECISION | sym={symbol} "
                                    f"intended={_orig_dir} "
                                    f"decision={_flipped_dir} "
                                    f"authority=XRAY action=flip "
                                    f"reason=low_conviction_structural_disagreement "
                                    f"coin_regime={_coin_regime_str or 'na'} "
                                    f"trade_direction={_trade_direction_str or 'na'} "
                                    f"rr_original={_orig_rr:.2f} "
                                    f"rr_flipped={_new_rr:.2f} "
                                    f"ratio={_ratio:.1f}x "
                                    f"override_threshold={_lock_override_threshold:.2f} "
                                    f"apex_locked={int(_apex_locked)} "
                                    f"apex_lock_reason='{_apex_lock_reason[:80]}' "
                                    f"size_usd=${_orig_size:.0f} "
                                    f"sl=${format_price(_new_sl)} tp=${format_price(_new_tp)} "
                                    f"| {ctx()}"
                                )
                    elif _xray_disagrees:
                        # HIGH-CONVICTION veto was booklogged (suppression OFF)
                        # AND the flip switch is ON. Do NOT enter the
                        # LOW-CONVICTION flip path above: a high-conviction
                        # directive must execute in the brain's direction,
                        # never be reversed (preserves the veto's intent and
                        # the operator's "execute Claude's direction"). The
                        # flip switch only authorizes reversal of LOW-conviction
                        # structural disagreements. Record the hold so the
                        # decision is observable.
                        log.warning(
                            f"DIRECTION_DECISION | sym={symbol} "
                            f"intended={direction} decision={direction} "
                            f"authority=BRAIN_APEX action=hold "
                            f"reason=high_conviction_booklog_no_flip "
                            f"coin_regime={_coin_regime_str or 'na'} "
                            f"trade_direction={_trade_direction_str or 'na'} "
                            f"rr_long={_sp.rr_long:.2f} "
                            f"rr_short={_sp.rr_short:.2f} "
                            f"ratio={_ratio:.1f}x "
                            f"would_flip_to={_flipped_dir_preview} "
                            f"flip_switch=on | {ctx()}"
                        )
                        # fall through: brain direction proceeds, no flip.

        # Issue #1: Validate symbol is supported on testnet
        from src.config.constants import SUPPORTED_SYMBOLS
        is_testnet = getattr(self.settings, "bybit", None) and self.settings.bybit.testnet
        if is_testnet and symbol not in SUPPORTED_SYMBOLS:
            log.warning("Skipping unsupported symbol: {sym}", sym=symbol)
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=unsupported_symbol "
                f"detail='testnet whitelist' | {ctx()}"
            )
            return (False, "unsupported_symbol")

        # Check not already in position
        if symbol in position_symbols:
            log.debug("Claude trade skipped: already in {sym}", sym=symbol)
            log.info(
                f"TRADE_SKIP | sym={symbol} rsn=dup_position "
                f"detail='already in position_symbols' | {ctx()}"
            )
            return (False, "dup_position")

        # Get services
        market_svc = self.services.get("market_service")
        order_svc = self.services.get("order_service")
        position_svc = self.services.get("position_service")
        coordinator = self.services.get("trade_coordinator")
        if not market_svc or not order_svc:
            log.warning("Missing market_service or order_service for Claude trade")
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=service_missing "
                f"detail='market_svc={bool(market_svc)} order_svc={bool(order_svc)}' | {ctx()}"
            )
            return (False, "service_missing")

        # Get CURRENT price
        try:
            ticker = await market_svc.get_ticker(symbol)
            current_price = ticker.last_price
        except Exception as e:
            log.error("Cannot get price for {sym}: {err}", sym=symbol, err=str(e))
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=price_fetch_fail "
                f"detail='{str(e)[:80]}' | {ctx()}"
            )
            return (False, "price_fetch_fail")

        if not current_price or current_price <= 0:
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=price_invalid "
                f"detail='current_price={current_price}' | {ctx()}"
            )
            return (False, "price_invalid")

        # Extract trade params
        sl = float(trade.get("stop_loss_price", 0))
        tp = float(trade.get("take_profit_price", 0))
        leverage = int(trade.get("leverage", 2))
        size_usd = float(trade.get("size_usd", 100))
        max_hold = int(trade.get("max_hold_minutes", 30))
        trail_pct = float(trade.get("trailing_activation_pct", 0.5))
        reasoning = str(trade.get("reasoning", ""))

        # Enrich reasoning with APEX/XRAY flip context when trade was flipped.
        # _flip_source distinguishes the XRAY-driven flip (Phase 1 of
        # dir-block-fix) from the APEX-driven flip (Qwen direction change).
        # Defaults to "apex" for backward compat with pre-Phase-1 trades.
        _apex_was_flipped = trade.get("_apex_was_flipped", False)
        _apex_reasoning = str(trade.get("_apex_reasoning", "") or "")
        _apex_original_dir = str(trade.get("_apex_original_direction", "") or "")
        _flip_source = str(trade.get("_flip_source", "apex") or "apex")

        if _apex_was_flipped and _apex_original_dir:
            if _flip_source == "xray":
                _ratio_str = f" ratio={trade.get('_xray_flip_ratio', 0)}x"
                reasoning = (
                    f"[XRAY FLIPPED {_apex_original_dir}->{direction}{_ratio_str}] "
                    f"{_apex_reasoning[:180]} "
                    f"| Claude original: {reasoning[:200]}"
                )[:500]
            else:
                reasoning = (
                    f"[APEX FLIPPED {_apex_original_dir}->{direction}] "
                    f"{_apex_reasoning[:200]} "
                    f"| Claude original: {reasoning[:200]}"
                )[:500]
        elif trade.get("_apex_optimized") and _apex_reasoning:
            reasoning = (
                f"[APEX OPTIMIZED] {_apex_reasoning[:150]} "
                f"| Claude: {reasoning[:200]}"
            )[:500]

        # Cap leverage to 5x max
        leverage = min(leverage, 5)
        # Cap size — testnet can go up to $5000, live stays conservative
        is_testnet = getattr(self.settings, "bybit", None) and self.settings.bybit.testnet
        max_size = 5000 if is_testnet else 1000
        # Brain-Authoritative Sizing (2026-05-31): size_usd is the MARGIN. This
        # venue cap must NOT re-clamp the brain's margin below what the APEX gate
        # already allowed (CHECK 4 per-trade MARGIN ceiling = usable/max_positions,
        # CHECK 1 backstop = usable) — otherwise the gate's work is silently undone
        # here. bybit_demo runs with bybit.testnet=false, so the legacy $1000 "live"
        # cap would pin every trade to $1000 and defeat the change. Cap at the whole
        # usable MARGIN pool (a single trade can't commit more margin than the pool;
        # the gate's per-trade ceiling is the binding rail). Flag off -> legacy
        # $5000/$1000 cap unchanged.
        _apex_cfg = getattr(self.settings, "apex", None)
        if _apex_cfg is not None and bool(
            getattr(_apex_cfg, "brain_authoritative_sizing_enabled", False)
        ):
            max_size = float(
                getattr(_apex_cfg, "max_position_size_usd", max_size) or max_size
            )
            # Derive the MARGIN backstop from the SAME single source (tiered_capital):
            # the whole usable pool. size_usd is margin, so NO x leverage here (that
            # was the double-leverage bug). Equity-tracking so it never clips a
            # legitimate per-trade-margin trade as the account grows.
            try:
                _tcm_v = self.services.get("tiered_capital") if self.services else None
                _fm_v = self.services.get("fund_manager") if self.services else None
                _eq_v = float(getattr(getattr(_fm_v, "_account_state", None), "total_equity", 0.0) or 0.0)
                if _tcm_v is not None and _eq_v > 0:
                    _usable_v = float(_tcm_v.get_limits(_eq_v, 0.0).usable_capital)
                    max_size = max(max_size, _usable_v)  # MARGIN backstop = whole usable pool
            except Exception:  # pragma: no cover — falls back to config absolute max
                pass
        # H3 (2026-05-30): respect the brain's risk-based size — do NOT floor a
        # deliberate small probe up to an arbitrary $100. Keep the venue cap and
        # a non-negative guard; a size genuinely below the EXCHANGE minimum is
        # SKIPPED downstream (qty<=0 -> TRADE_SKIP rsn=qty_zero), never oversized.
        size_usd = min(max(size_usd, 0.0), max_size)

        # Enforcer v2: apply PnL-based size multiplier (soft throttle).
        # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — capture the
        # multiplier and pre-multiplier size for the unified
        # SIZE_DERIVATION event below, even when the throttle is
        # passive (mult >= 1.0).
        enforcer = getattr(self, "_enforcer", None)
        _enforcer_mult: float | None = None
        _enforcer_pre_size: float | None = None
        if enforcer and hasattr(enforcer, "get_size_multiplier"):
            sz_mult = enforcer.get_size_multiplier()
            _enforcer_mult = float(sz_mult)
            _enforcer_pre_size = size_usd
            if sz_mult < 1.0:
                original_size = size_usd
                # H3 (2026-05-30): no $100 floor — the enforcer throttle is
                # MEANT to shrink size on poor performance; flooring it back up
                # defeats the throttle. Below-minimum sizes are skipped at the
                # exchange-minimum check (qty<=0), not oversized.
                size_usd = round(size_usd * sz_mult, 2)
                log.info(
                    f"ENFORCER_SIZE | sym={symbol} orig=${original_size:.0f} "
                    f"mult={sz_mult:.2f} final=${size_usd:.0f} | {ctx()}"
                )

        # Sniper-Latency-Size Fix Phase 3D (2026-05-07) — emit the
        # unified SIZE_DERIVATION event with the full per-layer chain
        # before the trade is dispatched. Best-effort; failure to log
        # never blocks the trade.
        try:
            from src.core.sizing_orchestrator import log_size_derivation

            log_size_derivation(
                trade=trade,
                symbol=symbol,
                final_size_usd=size_usd,
                final_leverage=leverage,
                enforcer_multiplier=_enforcer_mult,
                enforcer_pre_size_usd=_enforcer_pre_size,
            )
        except Exception as _se:
            log.debug(
                f"SIZE_DERIVATION_FAIL | sym={symbol} "
                f"err='{str(_se)[:80]}' | {ctx()}"
            )

        # Layer 4 (2026-05-22) — per-trade observability for the
        # Consensus-Truth fix. The Phase 4 trial needs to see whether
        # the brain weighed the truthful framing introduced in
        # strategist._format_consensus_context. Each trade emits one
        # L4_BRAIN_SIZE_DECISION line carrying the brain's chosen
        # size, the live consensus context (label, supporting count,
        # regime), and a reasoning excerpt. The operator can group
        # log lines by supporting_count bucket and see whether
        # brain-chosen size now tracks genuine edge rather than
        # herd size. Best-effort; never blocks the trade.
        try:
            _l4_claude_size = float(trade.get("size_usd", 100) or 100)
            _l4_cons_label = "?"
            _l4_support = -1
            _l4_oppose = -1
            _l4_regime = "?"
            _l4_ens_cache = self.services.get("ensemble_state_cache")
            if _l4_ens_cache is not None:
                _l4_consensus = _l4_ens_cache.get_current_consensus(symbol)
                if _l4_consensus is not None:
                    _l4_cons_label = str(_l4_consensus.get("consensus", "?"))
                    _l4_support = int(round(
                        float(_l4_consensus.get("agreeing", 0))
                    ))
                    _l4_oppose = int(round(
                        float(_l4_consensus.get("opposing", 0))
                    ))
            _l4_regime = str(trade.get("regime", "?") or "?")
            _l4_reasoning_excerpt = (reasoning or "")[:200].replace(
                "|", "/"
            ).replace("\n", " ")
            log.info(
                f"L4_BRAIN_SIZE_DECISION | sym={symbol} "
                f"claude_size=${_l4_claude_size:.0f} "
                f"final_size=${size_usd:.0f} "
                f"consensus={_l4_cons_label} "
                f"supporting={_l4_support} opposing={_l4_oppose} "
                f"regime={_l4_regime} "
                f"reasoning='{_l4_reasoning_excerpt}' | {ctx()}"
            )
        except Exception as _l4e:
            log.debug(
                f"L4_BRAIN_SIZE_DECISION_FAIL | sym={symbol} "
                f"err='{str(_l4e)[:80]}' | {ctx()}"
            )

        # ── XRAY-flip TP cap (TP-Volume-Closure fix Phase 1D, 2026-05-07) ──
        # When the XRAY direction-flip path (lines 1546-1742) overrides
        # Claude's chosen direction, it attaches the structural placement's
        # TP for the new direction (`_sp.short_tp_price` /
        # `_sp.long_tp_price`). For thinly-supported coins that target can
        # sit 15-20%+ from current price — the SLTPValidator below at
        # `validate_tp()` correctly rejects those as nonsensical and the
        # trade is lost. The cap consults the volatility profile (already
        # calibrated per class + regime) and bounds the structural target
        # to the strategy timeframe before the validator sees it. The cap
        # is a NO-OP for non-flipped trades (Claude's TP is already in
        # range). See `src/core/flip_tp_capper.py` for the math.
        _flip_tp_settings = getattr(
            getattr(self.settings, "risk", None), "flip_tp", None,
        ) or FlipTPSettings()
        if _flip_source == "xray" and _flip_tp_settings.enabled and tp > 0:
            _vp_svc = self.services.get("volatility_profiler") if self.services else None
            _vp_profile = None
            _vp_degraded = False
            _vp_error: str | None = None
            if _vp_svc is not None:
                try:
                    _vp_profile = await _vp_svc.get_profile(symbol)
                except Exception as _vp_exc:
                    # Narrow recovery for ONLY the get_profile call. We
                    # fall back to FlipTPSettings.fallback_tp_distance_pct
                    # (which compute_capped_flip_tp applies when
                    # vol_profile is None). Anything else propagates.
                    _vp_degraded = True
                    _vp_error = str(_vp_exc)[:80]
                    log.warning(
                        f"XRAY_FLIP_TP_DERIVATION_DEGRADED | sym={symbol} "
                        f"reason='vol_profile_fetch_failed: {_vp_error}' "
                        f"falling_back_to={_flip_tp_settings.fallback_tp_distance_pct:.2f}% "
                        f"| {ctx()}"
                    )

            _orig_flip_tp = tp
            _capped_tp, _cap_method, _cap_telem = compute_capped_flip_tp(
                symbol=symbol,
                direction=direction,
                current_price=current_price,
                structural_tp=tp,
                vol_profile=_vp_profile,
                settings=_flip_tp_settings,
            )

            # Mutate the local `tp` (consumed by SLTPValidator at line ~1890)
            # AND the trade dict (consumed by trade_coordinator,
            # save_thesis, telegram alerts, etc.). Mirror line 1519-1521's
            # dual-key pattern from the ENFORCER_RR_ADJUSTED helper so
            # downstream consumers all see the capped value.
            if _cap_method not in (METHOD_STRUCTURAL_KEPT, METHOD_DISABLED):
                tp = float(_capped_tp)
                trade["take_profit_price"] = float(_capped_tp)
                if "tp" in trade:
                    trade["tp"] = float(_capped_tp)
            trade["_xray_flip_tp_method"] = _cap_method
            trade["_xray_flip_tp_orig"] = float(_orig_flip_tp)
            trade["_xray_flip_tp_telem"] = _cap_telem

            # XRAY_FLIP_TP_DERIVATION (Phase 1E observability) — fires
            # for EVERY XRAY-flipped trade, regardless of whether the
            # cap was applied. Emit at INFO when the cap was a no-op
            # (`structural_kept` / `disabled`) and at WARNING when the
            # cap actually rebalanced the TP (`volatility_capped` /
            # `hard_ceiling` / `fallback`) so downstream alerting can
            # trigger on rebalances without filtering on the no-op
            # case. The event is the single source of truth for "was
            # the flip's TP bounded, and by what?".
            _is_noop = _cap_method in (METHOD_STRUCTURAL_KEPT, METHOD_DISABLED)
            _emit = log.info if _is_noop else log.warning
            _emit(
                f"XRAY_FLIP_TP_DERIVATION | sym={symbol} dir={direction} "
                f"orig_tp={_orig_flip_tp:.6f} capped_tp={tp:.6f} "
                f"structural_dist_pct={_cap_telem['structural_dist_pct']:.2f} "
                f"vol_aware_pct={_cap_telem['vol_aware_pct']:.2f} "
                f"vol_aware_capped_pct={_cap_telem['vol_aware_capped_pct']:.2f} "
                f"hard_ceiling_pct={_cap_telem['hard_ceiling_pct']:.2f} "
                f"chosen_cap_pct={_cap_telem['chosen_cap_pct']:.2f} "
                f"chosen_dist_pct={_cap_telem['chosen_dist_pct']:.2f} "
                f"method={_cap_method} "
                f"vol_profile_present={_vp_profile is not None} "
                f"degraded={_vp_degraded} "
                f"| {ctx()}"
            )

        # ── Validate SL/TP via SLTPValidator (Feature #5: headspace buffer) ──
        # Use X-RAY structural validation when available
        sl_validator = self.services.get("sl_validator")
        if sl_validator:
            _struct_data = None
            _sc = self.services.get("structure_cache") if self.services else None
            if _sc:
                try:
                    _sa = _sc.get(symbol)
                    if _sa:
                        _struct_data = _sa.to_dict()
                except Exception:
                    pass

            if _struct_data and hasattr(sl_validator, "validate_sl_structural"):
                sl_action, sl_adj, sl_reason = sl_validator.validate_sl_structural(
                    sl, current_price, direction, symbol, _struct_data,
                )
            else:
                sl_action, sl_adj, sl_reason = sl_validator.validate_sl(sl, current_price, direction, symbol)
            # Phase 12.4 (lifecycle-logging-audit Gap 1.9-G3 / 4.X-G1):
            # 4 prose lines for SL/TP adjust + validate replaced with
            # structured tags. Validation events are operationally
            # important for trade-safety grep-ability.
            if sl_action in ("SET", "ADJUST"):
                if sl_adj != sl:
                    log.info(
                        f"SLTP_ADJUST | sym={symbol} side={direction} type=SL "
                        f"old={sl} new={sl_adj} reason='{sl_reason}' | {ctx()}"
                    )
                sl = sl_adj
            elif sl_action == "SKIP":
                log.warning(
                    f"SLTP_VALIDATE_SKIP | sym={symbol} side={direction} type=SL "
                    f"reason='{sl_reason}' | {ctx()}"
                )
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn=sltp_skip "
                    f"detail='sl_validator: {str(sl_reason)[:80]}' | {ctx()}"
                )
                return (False, "sltp_skip")

            if _struct_data and hasattr(sl_validator, "validate_tp_structural"):
                tp_action, tp_adj, tp_reason = sl_validator.validate_tp_structural(
                    tp, current_price, direction, symbol, _struct_data,
                )
            else:
                tp_action, tp_adj, tp_reason = sl_validator.validate_tp(tp, current_price, direction, symbol)
            if tp_action in ("SET", "ADJUST"):
                if tp_adj != tp:
                    log.info(
                        f"SLTP_ADJUST | sym={symbol} side={direction} type=TP "
                        f"old={tp} new={tp_adj} reason='{tp_reason}' | {ctx()}"
                    )
                tp = tp_adj
            elif tp_action == "SKIP":
                log.warning(
                    f"SLTP_VALIDATE_SKIP | sym={symbol} side={direction} type=TP "
                    f"reason='{tp_reason}' | {ctx()}"
                )
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn=sltp_skip "
                    f"detail='tp_validator: {str(tp_reason)[:80]}' | {ctx()}"
                )
                return (False, "sltp_skip")

        # ── Final directional sanity check for SL/TP (Bug #3 fix) ──
        # Use volatility-adaptive defaults when available, else global risk defaults
        default_sl_pct = getattr(self.settings.risk, "default_stop_loss_pct", 3.0)
        default_tp_pct = getattr(self.settings.risk, "default_take_profit_pct", 6.0)
        _vp_svc = self.services.get("volatility_profiler")
        if _vp_svc:
            try:
                _vol_prof = await _vp_svc.get_profile(symbol)
                if _vol_prof:
                    default_sl_pct = _vol_prof.recommended_sl_pct
                    default_tp_pct = _vol_prof.recommended_tp_pct
            except Exception:
                pass
        # Phase 12.4 (lifecycle-logging-audit Gap 1.9-G3 / 4.X-G1):
        # 4 prose lines for SL/TP auto-correct replaced with structured
        # SLTP_AUTO_CORRECT tags.
        if direction == "Buy":
            if sl > 0 and sl >= current_price:
                sl = round(current_price * (1 - default_sl_pct / 100), 8)
                log.warning(
                    f"SLTP_AUTO_CORRECT | sym={symbol} side=Buy type=SL "
                    f"reason=wrong_side new={sl} default_pct={default_sl_pct} | {ctx()}"
                )
            if tp > 0 and tp <= current_price:
                tp = round(current_price * (1 + default_tp_pct / 100), 8)
                log.warning(
                    f"SLTP_AUTO_CORRECT | sym={symbol} side=Buy type=TP "
                    f"reason=wrong_side new={tp} default_pct={default_tp_pct} | {ctx()}"
                )
        elif direction == "Sell":
            if sl > 0 and sl <= current_price:
                sl = round(current_price * (1 + default_sl_pct / 100), 8)
                log.warning(
                    f"SLTP_AUTO_CORRECT | sym={symbol} side=Sell type=SL "
                    f"reason=wrong_side new={sl} default_pct={default_sl_pct} | {ctx()}"
                )
            if tp > 0 and tp >= current_price:
                tp = round(current_price * (1 - default_tp_pct / 100), 8)
                log.warning(
                    f"SLTP_AUTO_CORRECT | sym={symbol} side=Sell type=TP "
                    f"reason=wrong_side new={tp} default_pct={default_tp_pct} | {ctx()}"
                )

        # ── Phase 4 (P0-3 Fix B): SL≠TP and final straddle check ──
        # The legs were already auto-adjusted above; this final gate
        # catches the residual case where SL and TP collapse to within
        # 10 bps of each other (mechanically nonsensical) or one side
        # is still wrong-side after all adjustments. Skipping here makes
        # the failure visible as TRADE_SKIP instead of vanishing as
        # exec=0ms when Shadow rejects the order.
        if sl_validator and hasattr(sl_validator, "validate_pair"):
            pair_action, pair_reason = sl_validator.validate_pair(
                sl_price=sl,
                tp_price=tp,
                entry_price=current_price,
                current_price=current_price,
                direction=direction,
                symbol=symbol,
            )
            if pair_action == "SKIP":
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn={pair_reason} "
                    f"detail='sl={format_price(sl, current_price)} "
                    f"tp={format_price(tp, current_price)} "
                    f"entry={format_price(current_price)}' | {ctx()}"
                )
                return (False, pair_reason)

        # ── Entry Volume-Ratio Gate (2026-07-15, Phase 0 — observe-only) ──
        # 371-trade VM analysis found volume_ratio at entry (M5 current
        # volume vs SMA) separates winners from losers: vr>=0.4 kept
        # +$49.31 net vs dropped -$71.17 on the baseline window, surviving
        # 5 robustness checks. See IMPLEMENT_ENTRY_VOLUME_GATE.md. Phase 0
        # mode="observe" logs would_block on every proposed trade and
        # blocks nothing; Phase 1 flips to "enforce" only after a live
        # counterfactual confirms the split on fresh trade_intelligence
        # rows. Reads via ta_cache (TTL-cached, shared across consumers —
        # no extra TA computation cost).
        _evg_settings = getattr(self.settings, "entry_volume_gate", None) or (
            EntryVolumeGateSettings()
        )
        if _evg_settings.enabled:
            _evg_volume_ratio: float | None = None
            try:
                _evg_ta_cache = self.services.get("ta_cache") or self.services.get("ta")
                if _evg_ta_cache:
                    _evg_ta = await _evg_ta_cache.analyze(
                        symbol=symbol, timeframe=TimeFrame.M5, limit=100,
                    )
                    if _evg_ta:
                        _evg_volume_ratio = (_evg_ta.get("volume") or {}).get(
                            "volume_sma_ratio",
                        )
            except Exception as _evg_exc:
                # WARNING not debug: this exception decides whether the gate
                # ever sees a real volume_ratio. At log_level=INFO a debug
                # line here would be invisible, so a future bug in this path
                # would again produce zero diagnostic signal (2026-07-15: a
                # local `from src.core.types import TimeFrame` re-import
                # later in this same function shadowed the module-level
                # TimeFrame for the whole function scope, making every
                # reference before that line raise UnboundLocalError — fixed
                # by removing the redundant local imports; kept at WARNING
                # so a regression like it is never silent again).
                log.warning(
                    f"ENTRY_VOLUME_GATE_TA_FETCH_FAIL | sym={symbol} "
                    f"err_type={type(_evg_exc).__name__} "
                    f"err='{str(_evg_exc)[:200]}' | {ctx()}"
                )

            _evg_result = evaluate_entry_volume_gate(
                volume_ratio=_evg_volume_ratio,
                min_volume_ratio=_evg_settings.min_volume_ratio,
            )
            log.info(
                f"ENTRY_VOLUME_GATE | sym={symbol} "
                f"vr={_evg_result.volume_ratio if _evg_result.volume_ratio is not None else 'NA'} "
                f"thr={_evg_result.threshold:.2f} mode={_evg_settings.mode} "
                f"verdict={_evg_result.verdict} would_block={_evg_result.would_block} "
                f"reason={_evg_result.reason} | {ctx()}"
            )
            if _evg_settings.mode == "enforce" and _evg_result.would_block:
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn=entry_volume_gate_blocked "
                    f"detail='vr={_evg_result.volume_ratio} thr={_evg_result.threshold:.2f}' "
                    f"| {ctx()}"
                )
                return (False, "entry_volume_gate_blocked")

        # ── Fix 7 (volatility-scaled stop + size haircut, 2026-06-10) ──
        # The constant ~1.5% min stop sat INSIDE volatile coins' noise band (91%
        # chop stop-out). When enabled, widen the (now-finalized, correct-side)
        # stop to the coin's volatility-recommended distance — floored at the
        # reference so quiet coins keep the existing minimum, capped — and pair it
        # with a proportionally SMALLER size so the dollar risk AT the stop stays
        # within the reference budget. The live path sizes by margin x leverage,
        # NOT stop distance, so the haircut is explicit and tighten-only (never
        # scales size up); the sacred per-trade cap therefore still binds.
        _vss = getattr(
            getattr(self.settings, "risk", None), "volatility_stop_scaling", None,
        )
        if (
            _vss is not None and getattr(_vss, "enabled", False)
            and sl > 0 and current_price > 0
        ):
            _ref_pct = float(getattr(_vss, "reference_stop_pct", 1.5))
            _cap_pct = float(getattr(_vss, "max_cap_pct", 5.0))
            _scalar = float(getattr(_vss, "recommended_sl_scalar", 1.0))
            _rec_pct = 0.0
            _vss_cls = "na"
            if getattr(_vss, "use_profiler_recommended_sl", True):
                _vss_svc = (
                    self.services.get("volatility_profiler") if self.services else None
                )
                if _vss_svc is not None:
                    try:
                        _vss_prof = await _vss_svc.get_profile(symbol)
                        if _vss_prof is not None:
                            _rec_pct = (
                                float(getattr(_vss_prof, "recommended_sl_pct", 0.0))
                                * _scalar
                            )
                            _vss_cls = str(getattr(_vss_prof, "volatility_class", "na"))
                    except Exception:
                        _rec_pct = 0.0
            _placed_pct = abs(current_price - sl) / current_price * 100.0
            _sl_before, _size_before = sl, size_usd
            sl, size_usd, _target_pct, _final_pct = compute_volatility_scaled_stop(
                sl=sl, current_price=current_price, direction=direction,
                size_usd=size_usd, recommended_sl_pct=_rec_pct,
                reference_stop_pct=_ref_pct, max_cap_pct=_cap_pct,
            )
            log.info(
                f"STOP_SCALE_DERIVATION | sym={symbol} side={direction} "
                f"vol_class={_vss_cls} recommended_sl_pct={_rec_pct:.3f} "
                f"ref={_ref_pct:.2f} cap={_cap_pct:.2f} placed_pct={_placed_pct:.3f} "
                f"target_pct={_target_pct:.3f} final_pct={_final_pct:.3f} "
                f"sl_before={_sl_before} sl_after={sl} "
                f"size_before={_size_before:.2f} size_after={size_usd:.2f} | {ctx()}"
            )

        # ── Calculate and round quantity (Bug #2 fix: Decimal for precision) ──
        from decimal import Decimal, ROUND_DOWN
        qty = (size_usd * leverage) / current_price

        # Round to instrument step size (single source of truth: constants.py)
        from src.config.constants import TESTNET_QTY_STEPS
        _max_qty = 0.0
        _min_qty = 0.0
        _min_notional = 0.0
        try:
            inst_svc = self.services.get("instrument_service")
            if inst_svc:
                info = await inst_svc.get_instrument_info(symbol)
                step = float(info.qty_step) if info and info.qty_step else TESTNET_QTY_STEPS.get(symbol, 0.1)
                if info:
                    # Issue 2.2: capture the exchange order-size constraints so
                    # the order can be conformed before it is sent (data-driven,
                    # not hardcoded).
                    _max_qty = float(getattr(info, "max_qty", 0.0) or 0.0)
                    _min_qty = float(getattr(info, "min_qty", 0.0) or 0.0)
                    _min_notional = float(getattr(info, "min_notional", 0.0) or 0.0)
            else:
                step = TESTNET_QTY_STEPS.get(symbol, 0.1)
            d_qty = Decimal(str(qty))
            d_step = Decimal(str(step))
            qty = float((d_qty / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step)
        except Exception:
            step = TESTNET_QTY_STEPS.get(symbol, 0.1)
            d_qty = Decimal(str(qty))
            d_step = Decimal(str(step))
            qty = float((d_qty / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step)

        # ── Issue 2.2 (2026-06-07): conform qty to the symbol's exchange
        # constraints BEFORE sending, so a selected coin is not wasted on an
        # exchange rejection. The observed live failure was EGLD/ALGO orders
        # EXCEEDING maxOrderQty (Bybit retCode 10001 "the number of contracts
        # exceeds maximum limit allowed: too large"); clamp DOWN to max_qty
        # (rounded to step) so the trade still places, just smaller than
        # directed. Also respect min_qty / min_notional. If the order genuinely
        # cannot conform (e.g. min_qty above max_qty), skip cleanly with a logged
        # reason rather than letting the exchange reject it and waste the slot.
        if step > 0 and (_max_qty > 0 or _min_qty > 0 or _min_notional > 0):
            _dstep = Decimal(str(step))

            def _floor_to_step(_q: float) -> float:
                return float((Decimal(str(_q)) / _dstep).to_integral_value(rounding=ROUND_DOWN) * _dstep)

            def _ceil_to_step(_q: float) -> float:
                from decimal import ROUND_UP
                return float((Decimal(str(_q)) / _dstep).to_integral_value(rounding=ROUND_UP) * _dstep)

            if _max_qty > 0 and qty > _max_qty:
                _clamped = _floor_to_step(_max_qty)
                log.warning(
                    f"STRAT_QTY_CLAMP | sym={symbol} raw_qty={qty} max_qty={_max_qty} "
                    f"clamped_qty={_clamped} | exceeded exchange maxOrderQty; "
                    f"clamped down (position smaller than directed) | {ctx()}"
                )
                qty = _clamped
            if (
                _min_notional > 0 and current_price > 0
                and qty * current_price < _min_notional
            ):
                _need = _ceil_to_step(_min_notional / current_price)
                if _max_qty <= 0 or _need <= _max_qty:
                    qty = max(qty, _need)
            if (
                (_min_qty > 0 and qty < _min_qty)
                or (_max_qty > 0 and qty > _max_qty)
                # Issue 2.2 completeness (audit): also skip when the order still
                # falls below min-notional after the bump-up attempt (e.g. the
                # min-notional qty would exceed max_qty), so an undersized order
                # never reaches the exchange.
                or (_min_notional > 0 and current_price > 0 and qty * current_price < _min_notional)
            ):
                log.warning(
                    f"TRADE_SKIP | sym={symbol} rsn=qty_unconformable "
                    f"detail='qty={qty} min_qty={_min_qty} max_qty={_max_qty} "
                    f"min_notional={_min_notional} price={current_price}' | "
                    f"cannot meet exchange order-size constraints; skipped before "
                    f"the exchange would reject it | {ctx()}"
                )
                return (False, "qty_unconformable")

        if qty <= 0:
            min_size_usd = step * current_price / max(leverage, 1)
            log.warning(
                "Trade skipped: {sym} qty=0 (${size} at ${price}, step={step}). "
                "Min size=${min:.0f} at {lev}x",
                sym=symbol, size=size_usd, price=current_price,
                step=step, min=min_size_usd, lev=leverage,
            )
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=qty_zero "
                f"detail='size=${size_usd} price={current_price} step={step} min_usd=${min_size_usd:.0f}' | {ctx()}"
            )
            return (False, "qty_zero")

        # ── Set leverage ──
        if position_svc:
            try:
                await position_svc.set_leverage(symbol, leverage)
            except Exception as e:
                log.debug("set leverage failed: {err}", err=str(e))

        # ── Place order ──
        side_enum = Side.BUY if direction == "Buy" else Side.SELL

        # Issue 1 fix (2026-05-11) — unified DIRECTION_DECISION summary.
        # One log line per trade covering brain → APEX → XRAY → final
        # direction, with a reason field describing the path. Operators
        # and audit scripts have a single grep target instead of needing
        # to correlate STRAT_DIRECTIVE / APEX_OK / APEX_FLIP /
        # XRAY_DIR_FLIP across log files. ``reason`` enumeration:
        #   - ``clean``: no flips
        #   - ``apex_flip``: APEX flipped (was_flipped + flip_source!=xray)
        #   - ``xray_flip``: XRAY flipped, no APEX lock
        #   - ``xray_flip_overrode_apex_flip``: stacked flips (rare)
        #   - ``apex_dir_lock_held``: APEX locked, XRAY did not attempt
        #   - ``xray_flip_suppressed_by_lock``: APEX locked, XRAY would
        #     have flipped but was suppressed by phase 3b
        # The brain direction lives in ``_apex_original_direction`` once
        # any flip happens, else equals ``direction``.
        _brain_dir = str(trade.get("_apex_original_direction", "") or direction)
        _flip_source = str(trade.get("_flip_source", "") or "")
        _apex_locked = bool(trade.get("_apex_locked"))
        _flip_suppressed = bool(trade.get("_xray_flip_suppressed_by_lock"))
        _was_flipped = bool(trade.get("_apex_was_flipped"))
        if _flip_suppressed:
            _dir_reason = "xray_flip_suppressed_by_lock"
        elif _was_flipped and _flip_source == "xray":
            _dir_reason = "xray_flip"
        elif _was_flipped:
            _dir_reason = "apex_flip"
        elif _apex_locked:
            _dir_reason = "apex_dir_lock_held"
        else:
            _dir_reason = "clean"

        # T2-3 / F11 visibility (six-tier-fixes 2026-05-11). Call the
        # TA cache to capture the analysis.engine direction verdict at
        # decision time and surface (a) on the DIRECTION_DECISION log
        # for post-hoc audit and (b) as a structured
        # BRAIN_VS_ANALYSIS_DISAGREEMENT WARN when brain != analysis
        # AND no flip mechanism reconciled the difference. Visibility-
        # only by design: enforcement (downgrade / reject) deferred
        # pending 1-2 weeks of evidence on disagreement vs win-rate.
        _analysis_dir = "UNKNOWN"
        _analysis_score = 0.0
        _analysis_conf = 0.0
        try:
            _ta_cache_for_t23 = self.services.get("ta_cache") or self.services.get("ta")
            if _ta_cache_for_t23 is not None:
                _ta_for_t23 = await _ta_cache_for_t23.analyze(
                    symbol=symbol, timeframe=TimeFrame.M5, limit=100,
                )
                if _ta_for_t23:
                    _overall = _ta_for_t23.get("overall") or {}
                    _signal = str(_overall.get("signal", "NEUTRAL")).upper()
                    _analysis_score = float(_overall.get("score", 0) or 0)
                    _analysis_conf = float(_overall.get("confidence", 0) or 0)
                    # Map signal names to direction strings the rest of
                    # the system uses ("Buy" / "Sell"). NEUTRAL stays
                    # NEUTRAL so the disagreement check is skipped.
                    if _signal in ("BUY", "LONG"):
                        _analysis_dir = "Buy"
                    elif _signal in ("SELL", "SHORT"):
                        _analysis_dir = "Sell"
                    else:
                        _analysis_dir = "NEUTRAL"
        except Exception as _ae:
            log.debug(
                f"DIRECTION_TA_LOOKUP_FAIL | sym={symbol} "
                f"err='{str(_ae)[:120]}' | {ctx()}"
            )

        log.info(
            f"DIRECTION_DECISION | sym={symbol} brain_dir={_brain_dir} "
            f"final_dir={direction} flipped={'Y' if _was_flipped else 'N'} "
            f"flip_source={_flip_source or 'none'} "
            f"apex_locked={'Y' if _apex_locked else 'N'} "
            f"lock_reason='"
            f"{str(trade.get('_apex_lock_reason', ''))[:80]}' "
            f"xray_ratio={float(trade.get('_xray_flip_ratio', 0) or 0):.1f}x "
            f"reason={_dir_reason} "
            f"analysis_dir={_analysis_dir} "
            f"analysis_score={_analysis_score:+.2f} "
            f"analysis_conf={_analysis_conf:.2f} | {ctx()}"
        )

        # T2-3 disagreement event. Fires only when ALL of:
        #   - analysis verdict is non-NEUTRAL,
        #   - analysis direction != brain direction,
        #   - no flip has already reconciled the difference
        #     (final direction equals brain_dir; i.e. nothing flipped).
        # This lets operators build a counter-example dataset without
        # affecting trade frequency.
        if (
            _analysis_dir in ("Buy", "Sell")
            and _analysis_dir != _brain_dir
            and direction == _brain_dir
            and not _was_flipped
        ):
            log.warning(
                f"BRAIN_VS_ANALYSIS_DISAGREEMENT | sym={symbol} "
                f"brain_dir={_brain_dir} analysis_dir={_analysis_dir} "
                f"analysis_score={_analysis_score:+.2f} "
                f"analysis_conf={_analysis_conf:.2f} "
                f"flip_source={_flip_source or 'none'} "
                f"final_dir={direction} | {ctx()}"
            )

        # Orphan-prevention guard (2026-06-17): do NOT place a new order once
        # graceful shutdown has begun. A shutdown tears down the DB, which makes
        # the post-order save_thesis fail and orphans the trade (live on the
        # exchange, no local record, so its close never books PnL). Skipping new
        # opens during shutdown closes that window; open positions keep their
        # full exit management — only NEW entries are blocked.
        if self.services.get("shutting_down"):
            log.warning(
                f"STRAT_EXEC_SKIP | sym={symbol} dir={direction} "
                f"rsn='shutting_down' | no new opens during shutdown | {ctx()}"
            )
            return (False, "shutting_down")

        # ── Durable-open: RESERVE the thesis BEFORE placing the order ──
        # (2026-06-17) Root-cause fix for the orphan that lost UNIUSDT's green
        # close from PnL: previously the order was placed first and the thesis
        # saved afterward, so a DB failure between the two left the trade live
        # on the exchange with no local record. Now we persist a minimal thesis
        # row first (status='reserving', invisible to the brain / zombie
        # reconciler / restart-rehydrate); if that fails we do NOT place the
        # order (no orphan). finalize_thesis flips it to 'open' with the real
        # order_id after the fill; void_thesis voids it if the order is rejected
        # OR raises; sweep_reserving_theses resolves any leftover reservation.
        _thesis_mgr = self.services.get("thesis_manager")
        _reserved_thesis_id = -1
        if _thesis_mgr:
            _txf = self.services.get("transformer")
            _resv_mode = _txf.current_mode if _txf else "shadow"
            _reserved_thesis_id = await _thesis_mgr.save_thesis(
                symbol=symbol, direction=direction,
                entry_price=current_price, stop_loss_price=sl,
                take_profit_price=tp, size_usd=size_usd, leverage=leverage,
                max_hold_minutes=max_hold, trailing_activation_pct=trail_pct,
                thesis="(reserved — pending order fill)", market_context="",
                order_id="", exchange_mode=_resv_mode, thesis_source="intent",
                status="reserving",
            )
            if _reserved_thesis_id <= 0:
                log.critical(
                    f"STRAT_EXEC_ABORT | sym={symbol} dir={direction} "
                    f"rsn='thesis_reserve_failed' | order NOT placed to avoid an "
                    f"orphaned (unrecorded) trade | {ctx()}"
                )
                return (False, "thesis_reserve_failed")

        # ── Entry order type (win-rate enhancement Phase D, 2026-07-07) ──
        # [risk] entry_order_type: "market" (default, byte-identical taker
        # entry) or "limit" (passive GTC limit at current price offset
        # entry_limit_offset_bps toward the passive side — Buy below /
        # Sell above — for maker-fee entry). The limit path's fill-wait +
        # cancel-on-timeout lives after the REJECTED gate below. Price is
        # best-effort rounded via instrument_service.price_decimals so a
        # tick-misaligned limit is not rejected by the exchange.
        _entry_order_type = OrderType.MARKET
        _entry_limit_price: float | None = None
        _eo_cfg = str(getattr(
            self.settings.risk, "entry_order_type", "market",
        ) or "market").strip().lower()
        if _eo_cfg == "limit":
            _off_bps = float(getattr(
                self.settings.risk, "entry_limit_offset_bps", 0.0,
            ) or 0.0)
            _raw_px = (
                current_price * (1.0 - _off_bps / 10000.0)
                if direction == "Buy"
                else current_price * (1.0 + _off_bps / 10000.0)
            )
            _px_dec = 6
            try:
                _inst_svc = self.services.get("instrument_service")
                if _inst_svc is not None and hasattr(_inst_svc, "price_decimals"):
                    _d = _inst_svc.price_decimals(symbol)
                    if isinstance(_d, int) and 0 <= _d <= 10:
                        _px_dec = _d
            except Exception:
                _px_dec = 6
            _entry_limit_price = round(_raw_px, _px_dec)
            _entry_order_type = OrderType.LIMIT
            log.info(
                f"ENTRY_LIMIT_PLACE | sym={symbol} dir={direction} "
                f"px={_entry_limit_price} cur={current_price} "
                f"offset_bps={_off_bps:.1f} decimals={_px_dec} | {ctx()}"
            )

        # place_order can RAISE (Layer3 race/disabled/boot, risk-limit, order
        # errors), not just return REJECTED. Void the reservation on ANY
        # non-success outcome so a raise can never leave a phantom row, then
        # re-raise to preserve the caller's existing error handling.
        try:
            order = await order_svc.place_order(
                symbol=symbol,
                side=side_enum,
                order_type=_entry_order_type,
                qty=qty,
                price=_entry_limit_price,
                stop_loss=sl,
                take_profit=tp,
                leverage=leverage,
                purpose="layer3_entry",
                layer_snapshot=_layer_snapshot,
            )
        except Exception:
            if _thesis_mgr and _reserved_thesis_id > 0:
                await _thesis_mgr.void_thesis(_reserved_thesis_id, "place_order_raised")
            raise

        # T4-3 / Phase5 F-19 timing breadcrumbs (six-tier-fixes 2026-05-11).
        # The report cited a 20s gap between adapter PERSIST_OK and
        # strategy_worker STRAT_EXEC on the MONUSDT 14:00 trade. The
        # following _t_* timestamps + POST_PLACE_TIMING log lines let
        # operators measure each post-place step independently so the
        # actual bottleneck can be identified (current candidates:
        # save_thesis DB write, record_strategy_trade DB write,
        # alert_manager.send_custom Telegram POST, or a serialised
        # CAPITAL_TIER refresh fired from a different worker tick).
        import time as _t4_time
        _t_post_place_start = _t4_time.time()

        # ── Gate on order success — do NOT register rejected orders ──
        if order.status == OrderStatus.REJECTED:
            log.warning(
                f"STRAT_EXEC_SKIP | sym={symbol} dir={direction} "
                f"rsn='order_rejected' oid={order.order_id} | {ctx()}"
            )
            log.warning(
                f"TRADE_SKIP | sym={symbol} rsn=order_reject "
                f"detail='oid={order.order_id}' | {ctx()}"
            )
            # Order never went live — void the reserved thesis so it is not
            # mistaken for an open position (no inverse orphan).
            if _thesis_mgr and _reserved_thesis_id > 0:
                await _thesis_mgr.void_thesis(_reserved_thesis_id, "order_rejected")
            return (False, "order_reject")

        # ── Limit-entry fill wait + cancel-on-timeout (Phase D, 2026-07-07) ──
        # A passive GTC limit order rests on the book with status NEW (or
        # PARTIALLY_FILLED) rather than FILLED/REJECTED, so the gate above
        # does not catch it. Poll open orders until filled or the timeout
        # elapses; on timeout CANCEL and SKIP (no chase — a missed maker
        # entry is a skipped trade, not a market chase that defeats the
        # point of using a limit). A partial fill at cancel-time keeps the
        # filled portion (position/thesis reflect the real fill, not the
        # requested qty) — SL/TP were already attached order-side.
        if (
            _entry_order_type == OrderType.LIMIT
            and order.status not in (OrderStatus.FILLED,)
            and order.order_id
        ):
            import asyncio as _aio_fill_wait
            _fw_timeout = float(getattr(
                self.settings.risk, "entry_limit_timeout_seconds", 20.0,
            ) or 20.0)
            _fw_poll_s = 2.0
            _fw_deadline = _t4_time.time() + _fw_timeout
            _fw_still_open = True
            while _t4_time.time() < _fw_deadline:
                await _aio_fill_wait.sleep(_fw_poll_s)
                try:
                    _open = await order_svc.get_open_orders(symbol)
                except Exception as _fw_e:
                    log.warning(
                        f"ENTRY_LIMIT_POLL_FAIL | sym={symbol} "
                        f"oid={order.order_id} err='{str(_fw_e)[:100]}' | {ctx()}"
                    )
                    continue
                if not any(o.order_id == order.order_id for o in (_open or [])):
                    _fw_still_open = False
                    break
            if _fw_still_open:
                # Still resting at the deadline — cancel, then re-check
                # history once for a possible last-moment partial fill.
                try:
                    await order_svc.cancel_order(symbol, order.order_id)
                except Exception as _cancel_e:
                    log.warning(
                        f"ENTRY_LIMIT_CANCEL_FAIL | sym={symbol} "
                        f"oid={order.order_id} err='{str(_cancel_e)[:100]}' | {ctx()}"
                    )
                _filled_qty = 0.0
                _fill_px = 0.0
                try:
                    _hist = await order_svc.get_order_history(symbol, limit=10)
                    for _h in _hist or []:
                        if _h.order_id == order.order_id:
                            _filled_qty = float(getattr(_h, "filled_qty", 0.0) or 0.0)
                            _fill_px = float(getattr(_h, "avg_fill_price", 0.0) or 0.0)
                            break
                except Exception:
                    pass
                if _filled_qty <= 0:
                    log.warning(
                        f"STRAT_EXEC_SKIP | sym={symbol} dir={direction} "
                        f"rsn='limit_entry_timeout' oid={order.order_id} "
                        f"timeout_s={_fw_timeout} | {ctx()}"
                    )
                    log.warning(
                        f"TRADE_SKIP | sym={symbol} rsn=limit_entry_timeout "
                        f"detail='oid={order.order_id} timeout_s={_fw_timeout}' | {ctx()}"
                    )
                    if _thesis_mgr and _reserved_thesis_id > 0:
                        await _thesis_mgr.void_thesis(
                            _reserved_thesis_id, "limit_entry_timeout",
                        )
                    return (False, "limit_entry_timeout")
                # Partial fill survived the cancel — proceed with the real
                # filled amount instead of the originally-requested qty.
                log.warning(
                    f"ENTRY_LIMIT_PARTIAL_FILL | sym={symbol} "
                    f"requested={qty} filled={_filled_qty} px={_fill_px} "
                    f"oid={order.order_id} | {ctx()}"
                )
                order.status = OrderStatus.PARTIALLY_FILLED
                order.filled_qty = _filled_qty
                order.avg_fill_price = _fill_px or order.avg_fill_price
                qty = _filled_qty
            else:
                log.info(
                    f"ENTRY_LIMIT_FILLED | sym={symbol} oid={order.order_id} "
                    f"waited_s={_fw_timeout - max(0.0, _fw_deadline - _t4_time.time()):.1f} "
                    f"| {ctx()}"
                )

        # ── Create TradePlan ──
        trade_plan = TradePlan(
            symbol=symbol,
            direction=direction,
            entry_price=current_price,
            target_price=tp,
            stop_loss_price=sl,
            max_hold_minutes=max_hold,
            trailing_activation_pct=trail_pct,
            trailing_distance_pct=50,
            size_tier="claude_direct",
            reasoning=reasoning[:200],
        )

        # ── Phase 3: Capture entry-time market context for TIAS (best-effort) ──
        _entry_regime = ""
        _entry_rsi: float | None = None
        _entry_macd_hist: float | None = None
        _entry_atr_pct: float | None = None
        # Time-Decay Force-Close Definitive Fix Phase 3 (2026-05-06) —
        # entry-time XRAY confidence + setup_type + regime confidence,
        # paired with entry_regime_at_open for the structural-invalidation
        # detector. Best-effort: any None/missing data falls through to
        # neutral defaults; the watchdog's _compute_structural_invalidation
        # treats `entry_xray_confidence <= 0` as "no anchor" → fail-safe
        # block (no force-close).
        _entry_xray_confidence: float = 0.0
        _entry_setup_type: str = ""
        _entry_regime_at_open: str = ""
        _entry_regime_confidence: float = 0.0
        try:
            _regime_det = self.services.get("regime_detector")
            if _regime_det:
                _coin_regime = _regime_det.get_coin_regime(symbol)
                if _coin_regime is not None:
                    # RegimeState dataclass: .regime is the MarketRegime enum
                    _entry_regime = str(_coin_regime.regime.value)
                    _entry_regime_at_open = _entry_regime
                    _entry_regime_confidence = float(getattr(
                        _coin_regime, "confidence", 0.0,
                    ) or 0.0)
                else:
                    # Per-coin-authority Phase 2 (2026-05-29): stamp UNKNOWN when
                    # the per-coin regime is unavailable — NEVER the global BTC
                    # regime. This stamp feeds the time-decay p_win prior, the
                    # watchdog's structural-invalidation, AND the per-coin trial's
                    # acceptance queries; a BTC-stamped entry on a cold coin
                    # corrupted all three. UNKNOWN is the honest record.
                    _entry_regime_at_open = MarketRegime.UNKNOWN.value
                    _entry_regime_confidence = 0.0
        except Exception:
            pass

        try:
            _struct_cache = self.services.get("structure_cache")
            if _struct_cache:
                _xray = _struct_cache.get(symbol)
                if _xray is not None:
                    _entry_xray_confidence = float(getattr(
                        _xray, "setup_type_confidence", 0.0,
                    ) or 0.0)
                    _setup_type_obj = getattr(_xray, "setup_type", None)
                    if _setup_type_obj is not None:
                        # SetupType is an enum; use .value when available.
                        _entry_setup_type = str(getattr(
                            _setup_type_obj, "value", _setup_type_obj,
                        ) or "")
        except Exception:
            pass

        try:
            _ta_cache = self.services.get("ta_cache") or self.services.get("ta")
            if _ta_cache:
                _ta_entry = await _ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)
                if _ta_entry:
                    _entry_rsi = (_ta_entry.get("momentum") or {}).get("rsi_14")
                    _entry_macd_hist = (
                        (_ta_entry.get("trend") or {}).get("macd") or {}
                    ).get("histogram")
                    _entry_atr_pct = (_ta_entry.get("volatility") or {}).get("natr_14")
        except Exception:
            pass

        # Capture order_id at function scope (not inside `if coordinator:`) so
        # the later finalize_thesis(order_id=_order_id) in the `if thesis_mgr:`
        # block never depends on coordinator being wired. If coordinator is
        # truthy (always, in production) the assignment below sets the real id;
        # an empty default leaves the thesis to be matched by symbol on close.
        _order_id = str(getattr(order, "order_id", ""))[:50]

        # ── Register with coordinator ──
        if coordinator:
            _plan_view = str(
                getattr(plan, "market_view", "")
                or getattr(plan, "view", "")
                or ""
            )[:300]
            _signal_score = float(trade.get("score", 0) or 0) or None
            # Definitive-fix Phase 8 — capture order_id once and forward
            # it to BOTH register_trade (so close callback record
            # carries it) AND save_thesis (so the row is filterable).
            _order_id = str(getattr(order, "order_id", ""))[:50]
            # Layer 2 Defect 6 (2026-05-22) — lookup per-symbol vote counts
            # from the EnsembleStateCache (already populated live by
            # EnsembleVoter.vote per cycle). The cache returns dominant-
            # direction agreeing/opposing totals; we round to int for the
            # supporting_count/opposing_count columns. Best-effort — if
            # the cache has no record (rare: trade opened via a path that
            # bypassed the ensemble), the values stay None and the columns
            # get NULL (honest absence per Rule 5).
            _supporting_count: int | None = None
            _opposing_count: int | None = None
            _setup_id: str = ""
            try:
                _ens_cache = self.services.get("ensemble_state_cache")
                if _ens_cache is not None:
                    _consensus = _ens_cache.get_current_consensus(symbol)
                    if _consensus is not None:
                        _supporting_count = int(round(float(_consensus.get("agreeing", 0))))
                        _opposing_count = int(round(float(_consensus.get("opposing", 0))))
                        # Layer 2 Defect 1 — capture the join key for the
                        # trade_intelligence row so outcomes JOIN to
                        # ensemble_votes via setup_id.
                        _setup_id = str(_consensus.get("setup_id", "") or "")
            except Exception as _e:
                log.debug(
                    f"D6_VOTE_LOOKUP_FAIL | sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
                )
            coordinator.register_trade(
                symbol=symbol,
                strategy_category="claude_direct",
                strategy_name="claude_trader",
                entry_price=current_price,
                side=direction,
                source="claude_direct",
                size=qty,
                claude_directive=reasoning[:500],
                claude_plan_view=_plan_view,
                signal_score=_signal_score,
                ensemble_score=str(trade.get("ensemble_score", "") or ""),
                supporting_count=_supporting_count,
                opposing_count=_opposing_count,
                setup_id=_setup_id,
                entry_regime=_entry_regime,
                entry_rsi=_entry_rsi,
                entry_macd_hist=_entry_macd_hist,
                entry_atr_pct=_entry_atr_pct,
                # APEX optimization tracking (from layer_manager._apply_apex_optimization)
                apex_optimized=bool(trade.get("_apex_optimized", False)),
                apex_was_flipped=bool(trade.get("_apex_was_flipped", False)),
                apex_confidence=float(trade.get("_apex_confidence", 0) or 0),
                apex_tp_mode=str(trade.get("_apex_tp_mode", "") or ""),
                apex_reasoning=str(trade.get("_apex_reasoning", "") or "")[:200],
                apex_original_direction=str(trade.get("_apex_original_direction", "") or ""),
                apex_original_sl=float(trade.get("_apex_original_sl", 0) or 0),
                apex_original_tp=float(trade.get("_apex_original_tp", 0) or 0),
                apex_original_size=float(trade.get("_apex_original_size", 0) or 0),
                apex_model=str(trade.get("_apex_model", "") or ""),
                apex_response_ms=int(trade.get("_apex_response_ms", 0) or 0),
                apex_cost_usd=float(trade.get("_apex_cost_usd", 0) or 0),
                gate_adjustments=str(trade.get("_gate_adjustments", "") or ""),
                order_id=_order_id,
                # Time-Decay Force-Close Definitive Fix Phase 3 entry-anchors
                entry_xray_confidence=_entry_xray_confidence,
                entry_setup_type=_entry_setup_type,
                entry_regime_at_open=_entry_regime_at_open,
                entry_regime_confidence=_entry_regime_confidence,
                # Observability G6 — pass plan-level fields the audit
                # asked for in COORD_REG. trade_plan carries SL/TP;
                # leverage / size_usd are locals at this caller. These
                # are logging-only on the coordinator side.
                sl_price=float(getattr(trade_plan, "stop_loss_price", 0.0) or 0.0),
                tp_price=float(getattr(trade_plan, "target_price", 0.0) or 0.0),
                leverage=int(leverage or 0),
                size_usd=float(size_usd or 0.0),
            )
            coordinator.register_trade_plan(symbol, trade_plan)
            coordinator._trade_info[symbol] = {
                "strategy_name": "claude_trader",
                "strategy_category": "claude_direct",
                "score": 100,
                "consensus": "CLAUDE",
                "leverage": leverage,
                "amount_usd": size_usd,
                "source": "claude_direct",
                "plan_risk_level": getattr(plan, "risk_level", "normal"),
                "directive_reason": reasoning[:80],
            }

        # ── Save thesis (Issue #2) ──
        thesis_mgr = self.services.get("thesis_manager")
        if thesis_mgr:
            try:
                # (exchange_mode is now written at reserve time via _resv_mode;
                # the old post-order _exchange_mode tag was removed with the
                # thesis-before-order reorder.)
                # CALL_B Framing Fix Phase 1E (2026-05-06) — pull XRAY
                # flip metadata from the trade dict (stored at the flip
                # site lines 1650-1696) and forward to the v28 columns.
                # `_flip_source` defaults to '' (no flip) when not present;
                # apex flips set it to 'apex' when the apex pipeline ran;
                # xray flips set it to 'xray'. `_xray_flip_rr_long` and
                # `_xray_flip_rr_short` are populated only on the xray
                # branch — apex flips leave them at 0.0 which is the
                # column default. CALL_B's notice renderer reads
                # `xray_flip_source` first to decide whether to show the
                # XRAY-style "Nx better" line or the APEX free-text line.
                _xray_flip_source = str(trade.get("_flip_source", "") or "")
                # Defensive: only consider the source when the trade
                # was actually flipped, so a non-flipped trade with a
                # stale `_flip_source` set somewhere upstream doesn't
                # accidentally mark the row as flipped.
                if not _apex_was_flipped:
                    _xray_flip_source = ""
                _xray_flip_ratio = float(trade.get("_xray_flip_ratio", 0) or 0)
                _xray_flip_rr_long = float(trade.get("_xray_flip_rr_long", 0) or 0)
                _xray_flip_rr_short = float(trade.get("_xray_flip_rr_short", 0) or 0)
                # Mid-Hold Trade Management Fix Phase 3.3 (2026-05-19) —
                # parse and validate the brain's per-trade
                # ``thesis_invalidation`` field BEFORE save_thesis. Approach
                # C primary: brain states the criterion (returns
                # ('json', 'brain_stated')). Approach A fallback: brain
                # omitted/invalid (returns ('', 'heuristic_fallback')); the
                # snapshot column carries the XRAY anchor (Phase 3.5).
                # The parser emits the PARSED/MISSING/INVALID structured
                # log so operators can track brain compliance.
                _thesis_inv_json = ""
                _thesis_source = "brain_stated"
                try:
                    from src.brain.decision_parser import DecisionParser
                    _thesis_inv_json, _thesis_source = (
                        DecisionParser().parse_thesis_invalidation(
                            trade, entry_price=current_price, symbol=symbol,
                        )
                    )
                except Exception as _e:
                    # Belt-and-braces: a parser failure must not block the
                    # save. Fall back to heuristic with empty criterion so
                    # the thesis row is still well-formed.
                    log.warning(
                        f"BRAIN_THESIS_INVALIDATION_PARSE_FAIL | sym={symbol} "
                        f"err='{str(_e)[:120]}' | falling back to heuristic"
                    )
                    _thesis_inv_json = ""
                    _thesis_source = "heuristic_fallback"
                # Mid-Hold Trade Management Fix Audit Hotfix (2026-05-19) —
                # When APEX/XRAY flips the trade direction post-brain,
                # brain's thesis_invalidation criterion was authored for
                # the pre-flip direction and no longer matches the final
                # trade direction. Example caught live in production:
                # INJUSDT — brain said Buy with price_close_below 4.78
                # (Buy floor), XRAY flipped to Sell, but the criterion
                # stayed price_close_below — which is now the TP side
                # of the Sell, not the invalidation side. Watchdog would
                # have monitored a useless level (a Sell falling below
                # 4.78 is PROFIT, not invalidation).
                #
                # Fix: when any flip occurred (apex or xray), discard the
                # brain criterion and downgrade source to
                # heuristic_fallback. The XRAY snapshot captured below
                # IS direction-aware (uses the FINAL direction to pick
                # the aligned OB/FVG), so heuristic monitoring works.
                #
                # Rule 4 compliance: we don't invert brain's criterion
                # (that would be "telling brain what to do"); we just
                # acknowledge it no longer applies and switch to the
                # operator-decided Approach A fallback.
                _post_flip = bool(_apex_was_flipped) or bool(_xray_flip_source)
                if _post_flip and _thesis_source == "brain_stated" and _thesis_inv_json:
                    log.warning(
                        f"BRAIN_THESIS_INVALIDATION_DISCARDED_POST_FLIP | "
                        f"sym={symbol} orig_dir={_apex_original_dir or '?'} "
                        f"final_dir={direction} "
                        f"flip_source={_xray_flip_source or 'apex'} "
                        f"discarded_criterion_chars={len(_thesis_inv_json)} "
                        f"| falling back to heuristic_fallback (snapshot-driven)"
                    )
                    _thesis_inv_json = ""
                    _thesis_source = "heuristic_fallback"
                # Mid-Hold Trade Management Fix Phase 3.5 (2026-05-19) —
                # capture the XRAY structural snapshot for the symbol at
                # entry time. Used by the watchdog's evaluate_thesis_state
                # to monitor close-beyond invalidation on the nearest
                # aligned OB/FVG when source=heuristic_fallback. The
                # snapshot is also useful telemetry on brain_stated
                # rows (cross-check between brain's criterion and the
                # structural reality at entry).
                _thesis_snapshot_json = "{}"
                try:
                    structure_cache = self.services.get("structure_cache")
                    if structure_cache is not None:
                        analysis = None
                        try:
                            analysis = structure_cache.get(symbol)
                        except Exception:
                            analysis = None
                        if analysis is not None:
                            import json as _json
                            # Select only the nearest-aligned-level subset
                            # for compactness and to match operator
                            # decision (Approach A scope = nearest aligned
                            # level only).
                            nearest_aligned = {"type": "none"}
                            try:
                                _direction_up = str(direction).upper()
                                _nob = getattr(analysis, "nearest_ob", None)
                                _nfvg = getattr(analysis, "nearest_fvg", None)
                                # Sell entry → bearish level above entry.
                                if _direction_up == "SELL":
                                    if (
                                        _nob is not None
                                        and getattr(_nob, "direction", "") == "bearish"
                                        and float(getattr(_nob, "low", 0) or 0) > current_price
                                    ):
                                        nearest_aligned = {
                                            "type": "ob",
                                            "side": "bearish",
                                            "high": float(getattr(_nob, "high", 0) or 0),
                                            "low": float(getattr(_nob, "low", 0) or 0),
                                            "midpoint": float(getattr(_nob, "midpoint", 0) or 0),
                                        }
                                    elif (
                                        _nfvg is not None
                                        and getattr(_nfvg, "direction", "") == "bearish"
                                        and float(getattr(_nfvg, "bottom", 0) or 0) > current_price
                                    ):
                                        nearest_aligned = {
                                            "type": "fvg",
                                            "side": "bearish",
                                            "top": float(getattr(_nfvg, "top", 0) or 0),
                                            "bottom": float(getattr(_nfvg, "bottom", 0) or 0),
                                        }
                                elif _direction_up == "BUY":
                                    if (
                                        _nob is not None
                                        and getattr(_nob, "direction", "") == "bullish"
                                        and float(getattr(_nob, "high", 0) or 0) < current_price
                                    ):
                                        nearest_aligned = {
                                            "type": "ob",
                                            "side": "bullish",
                                            "high": float(getattr(_nob, "high", 0) or 0),
                                            "low": float(getattr(_nob, "low", 0) or 0),
                                            "midpoint": float(getattr(_nob, "midpoint", 0) or 0),
                                        }
                                    elif (
                                        _nfvg is not None
                                        and getattr(_nfvg, "direction", "") == "bullish"
                                        and float(getattr(_nfvg, "top", 0) or 0) < current_price
                                    ):
                                        nearest_aligned = {
                                            "type": "fvg",
                                            "side": "bullish",
                                            "top": float(getattr(_nfvg, "top", 0) or 0),
                                            "bottom": float(getattr(_nfvg, "bottom", 0) or 0),
                                        }
                            except Exception:
                                pass
                            _thesis_snapshot_json = _json.dumps({
                                "captured_at_price": current_price,
                                "direction": direction,
                                "nearest_aligned_level": nearest_aligned,
                            })
                except Exception as _e:
                    log.debug(
                        f"THESIS_SNAPSHOT_CAPTURE_FAIL | sym={symbol} "
                        f"err='{str(_e)[:120]}' | falling back to '{{}}'"
                    )
                    _thesis_snapshot_json = "{}"
                # Durable-open (2026-06-17): the thesis row was already RESERVED
                # before the order was placed. Here we ENRICH it (UPDATE) with
                # the real order_id (completing the (symbol, order_id) close
                # circuit) and the post-fill entry context. Was an INSERT via
                # save_thesis; the reserve+finalize split guarantees no orphan.
                if _reserved_thesis_id > 0:
                    await thesis_mgr.finalize_thesis(
                        _reserved_thesis_id,
                        order_id=_order_id,
                        thesis=reasoning,
                        market_context=getattr(plan, "market_view", "")[:200],
                        apex_flipped=bool(_apex_was_flipped),
                        apex_original_direction=_apex_original_dir,
                        apex_reason=_apex_reasoning[:200],
                        # Time-Decay Force-Close entry-anchors.
                        entry_xray_confidence=_entry_xray_confidence,
                        entry_setup_type=_entry_setup_type,
                        entry_regime_at_open=_entry_regime_at_open,
                        entry_regime_confidence=_entry_regime_confidence,
                        # CALL_B Framing Fix Phase 1E flip metadata.
                        xray_flip_source=_xray_flip_source,
                        xray_flip_ratio=_xray_flip_ratio,
                        xray_flip_rr_long=_xray_flip_rr_long,
                        xray_flip_rr_short=_xray_flip_rr_short,
                        # Mid-Hold Trade Management snapshot/invalidation.
                        thesis_invalidation=_thesis_inv_json,
                        thesis_source=_thesis_source,
                        thesis_snapshot=_thesis_snapshot_json,
                    )
            except Exception as e:
                log.debug("finalize thesis failed: {err}", err=str(e))

        # ── Record to DB ──
        try:
            from src.core.trade_recorder import record_strategy_trade
            await record_strategy_trade(
                db=self.db,
                symbol=symbol,
                strategy_name="claude_trader",
                direction=direction,
                score=100,
                ensemble_strength="CLAUDE",
                leverage_used=leverage,
                source="claude_direct",
            )
        except Exception as e:
            log.debug("record strategy trade to DB failed: {err}", err=str(e))

        # ── Telegram alert (AFTER order confirmed) ──
        alert_manager = self.services.get("alert_manager")
        if alert_manager:
            sl_dist = abs((sl - current_price) / current_price * 100)
            tp_dist = abs((tp - current_price) / current_price * 100)
            notional = size_usd * leverage
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            try:
                emoji = "UP" if direction == "Buy" else "DN"
                # Phase 4 (P0-7) — same precision rule applies to operator-
                # facing Telegram alerts so the quoted SL/TP match what
                # Shadow actually receives at full precision.
                await alert_manager.send_custom(
                    f"[{emoji}] <b>CLAUDE TRADE: {direction} {symbol}</b>\n\n"
                    f"<b>Entry:</b> ${format_price(current_price)}\n"
                    f"<b>Qty:</b> {qty:.4f} | ${size_usd:.0f} x {leverage}x = ${notional:.0f}\n\n"
                    f"<b>SL:</b> ${format_price(sl, current_price)} (-{sl_dist:.1f}%)\n"
                    f"<b>TP:</b> ${format_price(tp, current_price)} (+{tp_dist:.1f}%)\n"
                    f"<b>R:R:</b> 1:{rr:.1f}\n\n"
                    f"<b>Hold:</b> {max_hold}min | <b>Trail:</b> +{trail_pct}%\n"
                    f"<b>Reason:</b> {reasoning[:100]}",
                    AlertLevel.INFO,
                )
            except Exception as e:
                log.debug("send telegram trade alert failed: {err}", err=str(e))

        # T4-3 / Phase5 F-19 timing breadcrumb — post-place block total
        # elapsed (six-tier-fixes 2026-05-11). When this exceeds a few
        # seconds the next trade in a multi-trade directive is delayed.
        # The aggregate elapsed combined with the existing per-step
        # awaits in the block above (coordinator.register_trade /
        # register_trade_plan, thesis_mgr.save_thesis,
        # record_strategy_trade, alert_manager.send_custom) gives
        # operators the data to identify the specific bottleneck.
        _post_place_total_ms = (_t4_time.time() - _t_post_place_start) * 1000.0
        log.info(
            f"POST_PLACE_TIMING | sym={symbol} total_ms={_post_place_total_ms:.0f} | {ctx()}"
        )
        if _post_place_total_ms > 5000:
            log.warning(
                f"POST_PLACE_SLOW | sym={symbol} total_ms={_post_place_total_ms:.0f} | {ctx()}"
            )
        # Phase 4 (P0-7) — symbol-magnitude precision so sub-cent coins
        # show 6-8 decimals instead of $0.02 / $0.02. format_price uses
        # `current_price` as the magnitude reference so SL and TP align
        # with the symbol's tick scale.
        log.info(
            f"STRAT_EXEC | sym={symbol} dir={direction} qty={qty:.4f} "
            f"sz=${size_usd:.0f}x{leverage} "
            f"sl=${format_price(sl, current_price)} "
            f"tp=${format_price(tp, current_price)} | {ctx()}"
        )
        return (True, "ok")
