"""Scanner Worker: cycle trigger that selects the cycle's 30 from the warm 50.

Corrected Layer 1 architecture (LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md §9):

  ScannerWorker is NOT one of the 7 data workers. It is a SEPARATE cycle
  trigger that wakes once per 5-min window at sweet spot 4:00, reads the
  warm caches the 7 workers maintain for all 50 coins, computes a
  composite opportunity score per coin, picks the 30 with highest scores
  (force-including any coin with an open position per HR-3), writes those
  30 to the ``active_universe`` table, and updates ``MarketScanner._active_universe``
  so Stage 2's ``await scanner.get_active_universe()`` reads them.

The previous implementation called ``MarketScanner.scan_market()`` which
issued a fresh Bybit REST ``get_all_linear_tickers`` call and scored on
raw market data (volume, spread, change). That path is bypassed here.
"""

import time

from src.config.settings import Settings
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import WorkerTier
from src.database.connection import DatabaseManager
from src.strategies.scanner import MarketScanner
from src.workers.base_worker import SweetSpotWorker
from src.workers.scanner.interestingness import (
    InterestingnessWeights,
    compute_interestingness,
)
from src.workers.scanner.state_labeler import label_state

log = get_logger("worker")


class ScannerWorker(SweetSpotWorker):
    """Cycle trigger: reads warm worker caches, picks top-N, updates active_universe.

    Args:
        settings: Application settings.
        db: Database manager.
        scanner: MarketScanner — exposes ``get_active_universe()`` to Stage 2
            and owns the in-memory ``_active_universe`` list this worker
            writes to.
        services: WorkerManager._services dict, used to look up the 7 data
            workers' accessors at scoring time. Late-bound: the worker
            tolerates missing services (returns ``None`` from the relevant
            accessor) so a partial wiring degrades gracefully rather than
            crashing the cycle.
    """

    # Sub-layer assignment via WorkerTier enum (single source of truth).
    worker_tier = WorkerTier.LAYER1D
    # Phase 4 — skip tick when LayerManager.is_cycle_active() is False.
    cycle_gated = True

    def __init__(
        self,
        settings: Settings,
        db: DatabaseManager,
        scanner: MarketScanner,
        services: dict | None = None,
    ) -> None:
        super().__init__(
            name="scanner_worker",
            sweet_spot=settings.workers.sweet_spots.scanner_worker,
            settings=settings,
            db=db,
            window_minutes=settings.workers.sweet_spots.window_minutes,
        )
        self.scanner = scanner
        self.services = services or {}
        # Issue 3 of 2026-05-19 direction-bias fix Phase B — emit the
        # current state-labeller haircut at boot so log-tail monitoring
        # can verify the reframed regime gate is in memory. Mirrors the
        # STRAT_CALL_B_REFRAMED / STRAT_REGIME_INSTR_REFRAMED precedent
        # from Issue 4 (Phase A). semantics: haircut=0 reproduces legacy
        # hard-kill; 0<haircut<1 fires labels at reduced confidence in
        # mismatched regime; haircut>=1 removes the regime gate entirely.
        try:
            from src.workers.scanner.state_labeler import (
                LABELLER_REGIME_HAIRCUT_VERSION,
            )

            _haircut = float(
                settings.scanner.labeller.counter_regime_confidence_haircut
            )
            if _haircut <= 0.0:
                _mode = "legacy_hard_kill"
            elif _haircut < 1.0:
                _mode = "soft_haircut"
            else:
                _mode = "no_regime_gate"
            _esc_floor = float(
                settings.scanner.labeller.extreme_sentiment_conviction_floor
            )
            _esc_offtrend = bool(
                settings.scanner.labeller.extreme_sentiment_offtrend_haircut
            )
            log.info(
                f"STATE_LABELLER_REGIME_HAIRCUT_INIT | "
                f"version={LABELLER_REGIME_HAIRCUT_VERSION} "
                f"haircut={_haircut:.2f} mode={_mode} "
                f"extreme_conviction_floor={_esc_floor:.2f} "
                f"extreme_offtrend_haircut={_esc_offtrend} | {ctx()}"
            )
        except Exception as _e:
            log.debug(
                f"STATE_LABELLER_REGIME_HAIRCUT_INIT_FAIL | err='{str(_e)[:80]}' "
                f"| {ctx()}"
            )

        # Issue E12 (2026-05-27) boot sentinel — confirm the validator's four
        # failure-default ("fabrication") checks are live after restart, and
        # report the cold-start thresholds they feed (relaxed for E12 so the
        # honest lower scores cannot block the batch). Per-package proof is the
        # existing PACKAGE_VALIDATE line, which now carries the new field names.
        try:
            _csp = getattr(self.settings.brain, "cold_start_protection", None)
            _avg = float(getattr(_csp, "min_avg_completeness", 0.70)) if _csp else 0.70
            _bg = float(getattr(_csp, "boot_grace_completeness", 0.80)) if _csp else 0.80
            log.info(
                f"PACKAGE_VALIDATOR_FABRICATION_CHECKS_ACTIVE | checks=4 "
                f"fields=[ensemble_consensus,direction,funding_rate,confidence_zero] "
                f"blocker_gated=Y cold_start_min_avg={_avg:.2f} "
                f"cold_start_boot_grace={_bg:.2f} | {ctx()}"
            )
        except Exception as _e:
            log.debug(
                f"PACKAGE_VALIDATOR_FABRICATION_CHECKS_ACTIVE_FAIL | "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )

        # Layer 1 Defect 8 boot self-check — the state-labeller's
        # _FUNDING_EXTREME_DECIMAL constant must mirror the qualitative
        # gate's settings value so the FUNDING_EXTREME_FADE labels fire
        # at the same boundary the with-crowd direction is blocked.
        # Loud-error on divergence; the constant is module-level so a
        # future drift goes undetected without this check.
        try:
            from src.workers.scanner.state_labeler import (
                _FUNDING_EXTREME_DECIMAL,
            )

            _gate_thr = float(
                settings.scanner.qualitative.funding_blocker_threshold_pct
            )
            if abs(_FUNDING_EXTREME_DECIMAL - _gate_thr) < 1e-9:
                log.info(
                    f"BOOT_FUNDING_BOUNDARY_OK | "
                    f"labeller={_FUNDING_EXTREME_DECIMAL:.5f} "
                    f"gate={_gate_thr:.5f} | {ctx()}"
                )
            else:
                log.error(
                    f"BOOT_FUNDING_BOUNDARY_MISMATCH | "
                    f"labeller={_FUNDING_EXTREME_DECIMAL:.5f} "
                    f"gate={_gate_thr:.5f} "
                    f"gap={(_FUNDING_EXTREME_DECIMAL - _gate_thr):+.5f} "
                    f"| {ctx()}"
                )
        except Exception as _e:
            log.debug(
                f"BOOT_FUNDING_BOUNDARY_CHECK_FAIL | "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )

        # Four-Element Prompt Recalibration, Element 3 (2026-06-11) — boot
        # sentinel for the range-fade breakout guard (Rule 12). When ON,
        # the labeller receives range_breakout and the fade labels cannot
        # fire against a genuine break.
        _rfg_on = bool(getattr(
            getattr(getattr(settings, "scanner", None), "labeller", None),
            "range_fade_breakout_guard_enabled", True,
        ))
        log.info(
            f"BOOT_RANGE_FADE_GUARD_{'ON' if _rfg_on else 'OFF'} | "
            f"flag=range_fade_breakout_guard_enabled={_rfg_on} "
            f"scope=range_fade+funding_fade_labels_suppressed_on_genuine_break "
            f"| {ctx()}"
        )

        # Layer 1 Defect 3 — label-liveness boot audit. The state-
        # labeller's trigger dispatcher fires every trigger every cycle
        # but several depend on enrichment fields the scanner does not
        # populate today (scanner_worker.py:821-824 explicitly notes
        # "Phase 5 will plumb the enriched XRAY engine fields ...
        # Until then they default to safe values inside label_state").
        # Layer 1 does not change the dispatch or retire labels; it
        # simply makes the dormancy visible at boot so the operator
        # knows which labels are reachable, which are degraded, and
        # which never fire. Reviving by wiring scanner enrichment is
        # a separate task with operator sign-off per label.
        try:
            _hard_dead = [
                ("OB_MITIGATED_FVG_ONLY_LONG",
                 "needs in_direction_fvg_present + in_direction_ob_present"),
                ("OB_MITIGATED_FVG_ONLY_SHORT",
                 "needs in_direction_fvg_present + in_direction_ob_present"),
                ("BREAKOUT_PENDING_2ND_BRANCH",
                 "needs range_compression (first branch wired by Defect 2)"),
            ]
            _degraded_gate_bypassed = [
                ("MOMENTUM_BURST_LONG/SHORT",
                 "volume_ratio gate bypassed when input is None"),
                ("RANGE_FADE_LONG/SHORT",
                 "position_in_range gate bypassed when input is None"),
                ("FUNDING_EXTREME_FADE_LONG/SHORT",
                 "position_in_range gate bypassed when input is None"),
            ]
            log.info(
                f"BOOT_LABELLER_LIVENESS | "
                f"hard_dead={len(_hard_dead)} "
                f"degraded_gate_bypassed={len(_degraded_gate_bypassed)} "
                f"reason=scanner_phase5_enrichment_pending | {ctx()}"
            )
            for _lbl, _why in _hard_dead:
                log.info(
                    f"BOOT_LABELLER_DEAD | label={_lbl} reason={_why} | {ctx()}"
                )
            for _lbl, _why in _degraded_gate_bypassed:
                log.info(
                    f"BOOT_LABELLER_DEGRADED | label={_lbl} reason={_why} | {ctx()}"
                )
        except Exception as _e:
            log.debug(
                f"BOOT_LABELLER_LIVENESS_FAIL | err='{str(_e)[:80]}' | {ctx()}"
            )

    # ----- accessor lookups (defensive — services may be missing) ---------

    def _get_setup_score(self, coin: str) -> float | None:
        sw = self.services.get("structure_worker")
        if sw and hasattr(sw, "get_setup_score"):
            try:
                return sw.get_setup_score(coin)
            except Exception as _e:
                # Phase 9 Gap A1 (output-quality obs): silent except → DEBUG
                # log so accessor failures surface when an operator runs at
                # DEBUG level. Sparse — accessors run per-coin per-cycle but
                # the exception path fires only on real upstream issues.
                log.debug(
                    f"SERVICE_ACCESSOR_FAIL | accessor=_get_setup_score "
                    f"sym={coin} err='{str(_e)[:80]}' | {ctx()}"
                )
                return None
        return None

    def _get_setup_type_confidence(self, coin: str) -> float | None:
        """XRAY counter-setup Phase 5b — categorical setup confidence.

        Used in ``_compute_opportunity_score`` to multiply struct_norm so
        counter setups (typically 0.35) don't out-rank in-direction
        setups (typically 0.55-0.85) when their setup_score numerics
        happen to land close. Returns None when the structure_worker
        accessor isn't wired or the cache miss; callers default to 0.85
        (matches the scorer.py:5a default).
        """
        sw = self.services.get("structure_worker")
        if sw and hasattr(sw, "get_setup_type_confidence"):
            try:
                return sw.get_setup_type_confidence(coin)
            except Exception as _e:
                log.debug(
                    f"SERVICE_ACCESSOR_FAIL | accessor=_get_setup_type_confidence "
                    f"sym={coin} err='{str(_e)[:80]}' | {ctx()}"
                )
                return None
        return None

    def _get_strategy_score(self, coin: str) -> float | None:
        sw = self.services.get("strategy_worker")
        if sw and hasattr(sw, "get_score"):
            try:
                return sw.get_score(coin)
            except Exception as _e:
                log.debug(
                    f"SERVICE_ACCESSOR_FAIL | accessor=_get_strategy_score "
                    f"sym={coin} err='{str(_e)[:80]}' | {ctx()}"
                )
                return None
        return None

    def _get_signal_confidence(self, coin: str) -> float | None:
        sw = self.services.get("signal_worker")
        if sw and hasattr(sw, "get_signal"):
            try:
                sig = sw.get_signal(coin)
                if sig is None:
                    return None
                return float(getattr(sig, "confidence", 0.0))
            except Exception as _e:
                log.debug(
                    f"SERVICE_ACCESSOR_FAIL | accessor=_get_signal_confidence "
                    f"sym={coin} err='{str(_e)[:80]}' | {ctx()}"
                )
                return None
        return None

    def _get_regime_alignment(self, coin: str) -> float:
        """Return -1..+1 alignment factor from the per-coin regime.

        trending_up / trending_down → +1
        volatile                    → +0.5
        ranging                     → 0
        unknown / None (uncached)   → 0  (per-coin-authority Phase 6 follow-up,
                                          2026-05-29: UNKNOWN coins trade on their
                                          OWN TA/structure — neutral, NOT penalized,
                                          mirroring _regime_aligns which allows them)
        dead                        → -1
        """
        rw = self.services.get("regime_worker")
        if rw is None or not hasattr(rw, "get_regime"):
            return 0.0
        try:
            state = rw.get_regime(coin)
            if state is None:
                return 0.0  # genuinely uncached coin -> neutral, do not penalize
            regime_name = (
                state.regime.value if hasattr(state.regime, "value") else str(state.regime)
            ).lower()
        except Exception:
            return 0.0
        if "up" in regime_name or "down" in regime_name:
            return 1.0
        if "volat" in regime_name:
            return 0.5
        if "rang" in regime_name:
            return 0.0
        if "unknown" in regime_name:
            return 0.0  # operator decision 1: do not penalize UNKNOWN
        return -1.0  # dead

    def _get_funding_strength(self, coin: str) -> float | None:
        """Return |funding_rate| as a strength signal (0..N).

        Funding rate magnitude correlates with directional positioning
        pressure — large positive funding suggests longs are paying shorts
        (potential mean-reversion or short squeeze setup).
        """
        adw = self.services.get("altdata_worker")
        if adw and hasattr(adw, "get_funding"):
            try:
                rate = adw.get_funding(coin)
                if rate is None:
                    return None
                return float(abs(rate))
            except Exception:
                return None
        return None

    def _get_directional_rr(self, coin: str) -> float | None:
        """Return RR for the consensus direction.

        Definitive-fix Phase 4 (2026-04-28): the legacy path read
        ``sp.rr_ratio`` (which is ``rr_best`` — max of long & short)
        regardless of which side the consensus actually wanted to take.
        That meant a Sell-consensus coin with ``rr_long=2.5, rr_short=0.8``
        passed the gate even though the side we'd actually trade had an
        unprofitable RR. This accessor reads ``rr_long`` for long
        consensus, ``rr_short`` for short, and falls back to ``rr_ratio``
        when the StructuralPlacement model lacks the new fields (defensive
        — the dataclass at structure_types.py:112-113 has them today).
        Returns None when no structure is cached or no consensus is
        available; callers can decide how to treat the absence.
        """
        sw = self.services.get("structure_worker")
        cache = getattr(sw, "_cache", None) if sw else None
        if cache is None or not hasattr(cache, "get"):
            return None
        try:
            structure = cache.get(coin)
        except Exception:
            return None
        if structure is None:
            return None
        sp = getattr(structure, "structural_placement", None)
        if sp is None:
            return None
        # Try to read the consensus direction so we know which RR to take.
        direction = ""
        lm = self.services.get("layer_manager")
        if lm is not None and hasattr(lm, "get_strategy_consensus"):
            try:
                consensus = lm.get_strategy_consensus(coin) or {}
                direction = (consensus.get("direction") or "").lower()
            except Exception:
                direction = ""
        try:
            if direction == "long":
                rr = getattr(sp, "rr_long", None)
            elif direction == "short":
                rr = getattr(sp, "rr_short", None)
            else:
                rr = None
            if rr is None or rr == 0.0:
                # Fallback to legacy ``rr_ratio`` (= rr_best). Better than
                # 0.0 when direction isn't yet known (e.g. cold-start).
                rr = getattr(sp, "rr_ratio", 0.0)
            return float(rr or 0.0)
        except (TypeError, ValueError):
            return None

    # ----- composite scoring -----------------------------------------------

    def _compute_opportunity_score(self, coin: str) -> tuple[float, dict]:
        """Compute the composite opportunity score for ``coin``.

        Definitive-fix Phase 4 (2026-04-28): added 6th ``rr`` component.
        RR is now both a (relaxed) gate AND a ranking signal — coins
        with marginal RR (just over ``min_rr_ratio``) still qualify but
        rank below coins with strong RR. The component reads the
        direction-aware RR (``rr_long`` for long, ``rr_short`` for short)
        and saturates at RR=3.0 so extreme RR (which can indicate too-
        tight SL = stop-hunt risk) doesn't over-rank a coin.

        Returns:
            ``(score, breakdown)`` — the float score and a dict of
            normalized component values keyed by source name. Breakdown is
            used for the ``SCANNER_SELECTED`` per-coin DEBUG log.
        """
        weights = self.settings.scanner.scoring_weights

        # Each component normalized into a 0-1 range so weights produce
        # a sensible composite. Missing components contribute 0.
        struct_raw = self._get_setup_score(coin)  # 0-100 expected
        struct_norm_raw = max(0.0, min(1.0, (struct_raw or 0.0) / 100.0))

        # XRAY counter-setup Phase 5b — multiply the structural norm by
        # the categorical setup_type_confidence (clamped to [0.5, 1.0])
        # so counter setups (≈0.35 confidence) don't out-rank in-direction
        # setups (≈0.55-0.85) when their setup_score numerics happen to
        # land close. Floor at 0.5 mirrors the scorer.py:5a logic — never
        # zero out legitimate structure. Default 0.85 when accessor
        # returns None matches the scorer's pre-fix default.
        struct_conf = self._get_setup_type_confidence(coin)
        if struct_conf is None:
            struct_conf = 0.85
        struct_conf_factor = max(0.5, min(1.0, float(struct_conf)))
        struct_norm = struct_norm_raw * struct_conf_factor

        strat_raw = self._get_strategy_score(coin)  # ~0-100 expected (TradeScorer total_score)
        strat_norm = max(0.0, min(1.0, (strat_raw or 0.0) / 100.0))

        sig_norm = max(0.0, min(1.0, self._get_signal_confidence(coin) or 0.0))

        regime_align = self._get_regime_alignment(coin)  # -1..+1, scaled to 0..1
        regime_norm = (regime_align + 1.0) / 2.0  # 0..1

        funding_raw = self._get_funding_strength(coin)
        # Funding rate is typically 0.01% = 0.0001. Saturate at 0.001 (0.1%)
        # so anything stronger than that pegs the component at 1.0.
        funding_norm = max(0.0, min(1.0, (funding_raw or 0.0) / 0.001))

        # Phase 4 — direction-aware RR component, saturated at RR=3.0.
        rr_raw = self._get_directional_rr(coin)
        rr_norm = max(0.0, min(1.0, (rr_raw or 0.0) / 3.0))

        score = (
            weights.structure * struct_norm
            + weights.strategy * strat_norm
            + weights.signal * sig_norm
            + weights.regime * regime_norm
            + weights.funding * funding_norm
            + getattr(weights, "rr", 0.0) * rr_norm
        )
        return score, {
            "structure": round(struct_norm, 3),
            "structure_raw": round(struct_norm_raw, 3),
            "structure_conf": round(struct_conf_factor, 3),
            "strategy": round(strat_norm, 3),
            "signal": round(sig_norm, 3),
            "regime": round(regime_norm, 3),
            "funding": round(funding_norm, 3),
            "rr": round(rr_norm, 3),
        }

    async def _open_position_symbols(self) -> set[str]:
        """Return the set of symbols with currently open positions.

        Used to force-include positions in the cycle's focus (HR-3). On
        any error, returns the empty set — the cycle continues with the
        score-based selection only, which is the conservative fallback.
        """
        pos_svc = self.services.get("position") or self.services.get("position_service")
        if pos_svc is None:
            return set()
        try:
            positions = await pos_svc.get_positions()
        except Exception as e:
            log.warning(
                f"SCANNER_POSITIONS_FAIL | err={str(e)[:100]} | {ctx()}"
            )
            return set()
        return {p.symbol for p in positions}

    # ----- Phase 5 qualitative filter --------------------------------------

    @staticmethod
    def _regime_aligns(regime: str, direction: str) -> bool:
        """Check that the proposed direction matches the per-coin regime.

        Long is aligned with trending_up or ranging; short is aligned with
        trending_down or ranging. Volatile/dead fail by default — too noisy for a
        directional bet.

        Per-coin-authority Phase 6g (2026-05-29): UNKNOWN (no usable per-coin
        regime — cold start / new listing) is ALLOWED in BOTH directions, per the
        operator decision that such coins still trade on their OWN TA/structure
        rather than being silently blocked. Previously UNKNOWN fell through to the
        False default and was auto-blocked from selection.
        """
        regime = (regime or "").lower()
        direction = (direction or "").lower()
        # Per-coin-authority Phase 6g follow-up (2026-05-29): an explicit UNKNOWN
        # OR an empty/uncached regime (the caller leaves regime_label="" when the
        # per-coin cache has no entry for a brand-new/never-detected coin) are both
        # ALLOWED — operator decision 1: such coins trade on their own TA/structure,
        # never silently blocked.
        if not regime or "unknown" in regime:
            return True
        if direction == "long":
            return "trending_up" in regime or "ranging" in regime or "rang" in regime
        if direction == "short":
            return "trending_down" in regime or "ranging" in regime or "rang" in regime
        return False

    def _check_blockers(
        self,
        symbol: str,
        structure,
        consensus: dict | None,
        *,
        recent_loss_set: set[str] | None = None,
    ) -> list[str]:
        """Return list of blockers (Phase 5).

        Empty list = pass; non-empty = block the coin from selection.

        Args:
            symbol: The coin under evaluation.
            structure: Structural analysis from XRAY (or None).
            consensus: Per-coin ensemble consensus dict (or None).
            recent_loss_set: Symbols with a losing close within
                ``cfg.recent_failure_blocker_hours``. Pre-computed once
                per tick by ``tick`` to avoid an O(50) DB hit per cycle.
                None disables the recent-loss check (defensive default).
        """
        cfg = self.settings.scanner.qualitative
        blockers: list[str] = []

        # Funding rate against direction
        try:
            adw = self.services.get("altdata_worker")
            if adw and hasattr(adw, "get_funding") and consensus:
                rate = adw.get_funding(symbol)
                if rate is not None:
                    direction = consensus.get("direction", "")
                    if direction == "long" and rate > cfg.funding_blocker_threshold_pct:
                        blockers.append(
                            f"funding_against_long_rate={rate:.4f}"
                        )
                    elif direction == "short" and rate < -cfg.funding_blocker_threshold_pct:
                        blockers.append(
                            f"funding_against_short_rate={rate:.4f}"
                        )
        except Exception:
            pass

        # Manipulation_likely session warning
        try:
            session = getattr(structure, "session_context", None) if structure else None
            if session and getattr(session, "manipulation_likely", False):
                blockers.append("manipulation_likely_session")
        except Exception:
            pass

        # Recent failure within configured lookback. Set membership is
        # O(1); the actual DB hit is amortized once per tick by the
        # caller (see ``tick`` for the prefetch).
        if recent_loss_set is not None and symbol in recent_loss_set:
            blockers.append(
                f"recent_loss_within_{cfg.recent_failure_blocker_hours}h"
            )

        return blockers

    # ----- Phase 6 package builder -----------------------------------------

    async def _prefetch_fear_greed(self) -> int | None:
        """Resolve the global Fear & Greed reading once per cycle.

        ``FearGreedClient.get_latest`` is ``async def`` (DB-backed lookup +
        optional REST refresh). The previous implementation called it from
        the sync ``_build_package`` without ``await``; the coroutine then
        leaked through ``getattr(coro, "value", 0)`` whose default silently
        zeroed every package's ``alt_data.fear_greed``. Validator rule
        (``coin_package_validator.py:34``) treats ``fear_greed > 0`` as the
        only "present" state, so the bug capped package completeness at
        ~0.94 — below the 0.95 brain boot-grace threshold — and dropped
        every CALL_A trade for the first 10 minutes after every restart.

        F&G is a single global market metric, not per-coin, so one resolve
        per cycle replaces what was previously one (broken) call per coin.
        Failures fail loud once via ``SCANNER_FG_PREFETCH_FAIL`` rather than
        being swallowed per-coin.

        Returns:
            The integer F&G value (0..100) on success, or ``None`` if the
            service is unwired, returns no data, or raises. Caller must
            translate ``None`` to whatever default the package expects (we
            keep ``alt.fear_greed = 0`` for the None case so the validator
            still flags missing data — visible, not silent).
        """
        fg = self.services.get("fear_greed")
        if fg is None or not hasattr(fg, "get_latest"):
            return None
        try:
            data = await fg.get_latest()
        except Exception as e:
            log.warning(
                f"SCANNER_FG_PREFETCH_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )
            return None
        if data is None:
            return None
        try:
            return int(getattr(data, "value", 0) or 0)
        except (TypeError, ValueError):
            return None

    async def _prefetch_open_positions(
        self, symbols: list[str],
    ) -> dict[str, dict]:
        """Resolve open-position dicts for force-included symbols.

        ``PositionService.get_position`` is ``async def`` (calls the async
        ``get_positions`` underneath). The previous implementation in
        ``_build_package`` called it sync, leaking a coroutine into
        ``open_position``; the package's ``open_position`` field then
        landed as ``None`` even when an open position existed. Currently
        masked in production because the operator runs with no open
        positions, but it would silently break HR-2 ("force-include open
        positions so Claude can decide hold/close") the moment a real
        position opens.

        Only forced symbols are looked up — the qualified path's packages
        always carry ``open_position=None`` (line 540 contract), so doing
        50 lookups per cycle would be wasted work.

        Args:
            symbols: The forced-include symbols this cycle (BTC/ETH
                reference pairs and any open-position coins). Empty list
                short-circuits to an empty dict — no service call.

        Returns:
            ``{symbol: position_dict}`` for every symbol with an open
            position. Symbols without a position (or with a service error)
            are absent from the dict. Per-symbol failures log
            ``SCANNER_POS_PREFETCH_FAIL`` and continue — one bad lookup
            does not block the others.
        """
        if not symbols:
            return {}
        pos_svc = (
            self.services.get("position")
            or self.services.get("position_service")
        )
        if pos_svc is None or not hasattr(pos_svc, "get_position"):
            return {}
        out: dict[str, dict] = {}
        for sym in symbols:
            try:
                p = await pos_svc.get_position(sym)
            except Exception as e:
                log.warning(
                    f"SCANNER_POS_PREFETCH_FAIL | sym={sym} "
                    f"err='{str(e)[:120]}' | {ctx()}"
                )
                continue
            if p is None:
                continue
            try:
                out[sym] = (
                    p.to_dict() if hasattr(p, "to_dict")
                    else dict(getattr(p, "__dict__", {}))
                )
            except Exception as e:
                log.warning(
                    f"SCANNER_POS_PREFETCH_FAIL | sym={sym} "
                    f"err='to_dict_failed: {str(e)[:80]}' | {ctx()}"
                )
        return out

    def _build_package(
        self,
        symbol: str,
        score: float,
        record: dict,
        forced: bool,
        *,
        fg_value: int | None = None,
        position: dict | None = None,
    ) -> CoinPackage:
        """Layer 1 restructure Phase 6 — build self-contained CoinPackage.

        Reads existing caches with defensive ``getattr/get`` patterns so a
        missing service degrades to sensible defaults rather than crashing
        the cycle. Missing fields contribute a note to ``blockers_observed``
        rather than failing the whole package.

        Async-context data (F&G value, open positions) is resolved upstream
        in ``tick`` via ``_prefetch_fear_greed`` / ``_prefetch_open_positions``
        and passed in as kw-only arguments. This keeps ``_build_package``
        sync (no awaits) and concentrates async I/O at the cycle level —
        one fetch per cycle for the global F&G metric, one per forced
        symbol for positions, instead of per-coin async-mis-calls.

        Args:
            symbol: Coin symbol.
            score: Composite opportunity score from ``_compute_opportunity_score``.
            record: Qualification record (reasons_passed/failed/blockers).
            forced: True iff this coin is force-included (open position or
                BTC/ETH reference pair) rather than passing the qualitative
                filter on merit.
            fg_value: Pre-resolved Fear & Greed value (0..100), or ``None``
                if the prefetch failed / the service is unwired. ``None``
                propagates as ``alt_data.fear_greed = 0`` so the validator
                visibly marks the field as missing rather than silently
                serving stale data.
            position: Pre-resolved open-position dict for forced symbols,
                or ``None``. Only consulted when ``forced=True``; the
                qualified path always carries ``open_position=None``.

        Returns:
            Fully-populated CoinPackage matching blueprint Section 11.2.
        """
        blockers_observed: list[str] = list(record.get("blockers", []))

        # ── XRAY block ────────────────────────────────────────────────
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None

        levels = StructuralLevels()
        xray = XrayBlock(setup_type="none")
        if structure is not None:
            try:
                levels.current_price = float(getattr(structure, "current_price", 0.0) or 0.0)
                sp = getattr(structure, "structural_placement", None)
                if sp is not None:
                    direction = getattr(sp, "direction", "") or ""
                    if direction == "long":
                        levels.suggested_sl = float(getattr(sp, "long_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "long_tp_price", 0.0) or 0.0)
                    elif direction == "short":
                        levels.suggested_sl = float(getattr(sp, "short_sl_price", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "short_tp_price", 0.0) or 0.0)
                    else:
                        levels.suggested_sl = float(getattr(sp, "structural_sl", 0.0) or 0.0)
                        levels.suggested_tp = float(getattr(sp, "structural_tp", 0.0) or 0.0)
                    levels.rr_ratio = float(getattr(sp, "rr_ratio", 0.0) or 0.0)

                setup_type_obj = getattr(structure, "setup_type", None)
                setup_type_value = (
                    setup_type_obj.value if setup_type_obj is not None else "none"
                )
                session = getattr(structure, "session_context", None)
                # XRAY counter-setup Phase 5d — surface trade_direction
                # alongside setup_type so Stage 2 brain prompt can render
                # COUNTER context (trade direction OPPOSITE to suggested
                # for *_FVG_OB_COUNTER setups). Empty when setup_type is
                # "none". Falls back to suggested_direction when
                # trade_direction wasn't populated (legacy producer that
                # didn't run through Phase 4 classify_setup).
                _trade_direction = (
                    getattr(structure, "trade_direction", "")
                    or getattr(structure, "suggested_direction", "")
                    or ""
                )
                xray = XrayBlock(
                    setup_type=setup_type_value,
                    setup_score=float(getattr(structure, "setup_score", 0) or 0),
                    setup_type_confidence=float(
                        getattr(structure, "setup_type_confidence", 0.0) or 0.0
                    ),
                    trade_direction=str(_trade_direction),
                    structural_levels=levels,
                    mtf_confluence=str(getattr(structure, "confluence_quality", "")),
                    session=str(getattr(session, "current_session", "")) if session else "",
                    session_phase=str(getattr(session, "session_phase", "")) if session else "",
                    key_features=[],
                )
            except Exception:
                blockers_observed.append("xray_extract_failed")
        else:
            blockers_observed.append("xray_missing")

        # ── Strategies block ──────────────────────────────────────────
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            try:
                consensus = lm.get_strategy_consensus(symbol)
            except Exception:
                consensus = None
        score_total = 0.0
        try:
            stw = self.services.get("strategy_worker")
            if stw and hasattr(stw, "get_score"):
                score_total = float(stw.get_score(symbol) or 0.0)
        except Exception:
            pass
        strategies = StrategiesBlock(
            fired_count=int((consensus or {}).get("vote_count", 0)),
            fired_strategies=[],  # detail kept in _strategy_hints, not packaged here
            ensemble_consensus=(consensus or {}).get("consensus", "NONE"),
            consensus_score=float((consensus or {}).get("consensus_score", 0.0)),
            total_score=score_total,
            # Issue E25 (2026-05-28): carry the regime the strategy worker
            # scored this coin under (tagged in the same consensus dict) so the
            # brain renders the regime that matches these scores. Empty when the
            # coin was not scored this cycle -> brain falls back to the cache.
            scoring_regime=str((consensus or {}).get("scoring_regime", "") or ""),
            # Issue #2 (2026-05-31): carry the scored regime's own metrics (tagged
            # in the same consensus dict by strategy_worker) so the brain renders
            # the scoring word WITH matching numbers. Neutral defaults when the
            # coin was not scored this cycle -> brain falls back to the live cache.
            scoring_regime_confidence=float((consensus or {}).get("scoring_regime_confidence", 0.0) or 0.0),
            scoring_regime_adx=float((consensus or {}).get("scoring_regime_adx", 0.0) or 0.0),
            scoring_regime_atr_percentile=float((consensus or {}).get("scoring_regime_atr_percentile", 0.0) or 0.0),
            scoring_regime_choppiness=float((consensus or {}).get("scoring_regime_choppiness", 0.0) or 0.0),
            scoring_regime_volume_ratio=float((consensus or {}).get("scoring_regime_volume_ratio", 0.0) or 0.0),
            scoring_regime_volume_ratio_known=bool((consensus or {}).get("scoring_regime_volume_ratio_known", True)),
            scoring_regime_trend_direction=int((consensus or {}).get("scoring_regime_trend_direction", 0) or 0),
        )

        # ── Signals block ────────────────────────────────────────────
        sigw = self.services.get("signal_worker")
        signals = SignalsBlock(
            direction=(consensus or {}).get("direction", "neutral"),
        )
        try:
            if sigw and hasattr(sigw, "get_signal"):
                sig = sigw.get_signal(symbol)
                if sig is not None:
                    signals.confidence = float(getattr(sig, "confidence", 0.0) or 0.0)
                    if getattr(sig, "direction", None):
                        signals.direction = str(getattr(sig, "direction"))
        except Exception:
            blockers_observed.append("signal_missing")

        # ── Alt data block ───────────────────────────────────────────
        adw = self.services.get("altdata_worker")
        alt = AltDataBlock()
        try:
            if adw and hasattr(adw, "get_funding"):
                rate = adw.get_funding(symbol)
                if rate is not None:
                    alt.funding_rate = float(rate)
                    alt.funding_signal = (
                        "longs_paying" if rate > 0
                        else "shorts_paying" if rate < 0
                        else "neutral"
                    )
        except Exception:
            blockers_observed.append("funding_missing")
        # Issue #8 fix (2026-05-27): populate the brain's OI field (previously
        # never set -> always rendered 0.00%; companion E7). get_oi now returns
        # the correct ~24h delta (the open_interest fetch sources it from the DB
        # repo, the same value the signal generator uses).
        try:
            if adw and hasattr(adw, "get_oi"):
                _oi = adw.get_oi(symbol)
                if _oi is not None:
                    alt.oi_change_24h_pct = float(_oi.get("change_24h_pct", 0.0) or 0.0)
        except Exception:
            blockers_observed.append("oi_missing")
        # F&G is prefetched once per cycle by ``_prefetch_fear_greed``
        # (called from ``tick``) and passed in as ``fg_value``. The
        # previous inline ``fg.get_latest()`` was async-called-from-sync,
        # which silently zeroed every package's fear_greed; see helper
        # docstring for the failure mode.
        if fg_value is not None:
            alt.fear_greed = int(fg_value)

        # ── Price data block ─────────────────────────────────────────
        market = self.services.get("market") or self.services.get("market_service")
        price = PriceDataBlock()
        try:
            if market and hasattr(market, "get_ticker_cached"):
                t = market.get_ticker_cached(symbol)
                if t is not None:
                    price.current = float(getattr(t, "last_price", 0.0) or 0.0)
                    price.change_24h_pct = float(getattr(t, "change_24h_pct", 0.0) or 0.0)
                    price.volume_24h_usd = float(getattr(t, "volume_24h_usd", 0.0) or 0.0)
        except Exception:
            blockers_observed.append("ticker_missing")
        if structure is not None and price.current == 0.0:
            price.current = levels.current_price
        regime_worker = self.services.get("regime_worker")
        # E9 (2026-05-28): initialise to None BEFORE the try so the
        # RegimeState is always bound at the interestingness/labeler call
        # sites below. Without this, an absent/raising regime_worker would
        # leave `state` unbound; a bare state.confidence would then raise,
        # be swallowed by the outer try, and SILENTLY zero the entire
        # interestingness score. Mirrors how `structure` is initialised above.
        state = None
        try:
            if regime_worker and hasattr(regime_worker, "get_regime"):
                state = regime_worker.get_regime(symbol)
                if state is not None:
                    price.regime = (
                        state.regime.value
                        if hasattr(state.regime, "value")
                        else str(state.regime)
                    )
        except Exception:
            pass

        # ── E9/E8: real ranker inputs (Phase 5 rollout completion) ───
        # The interestingness ranker and the state-labeler were scoring on
        # a hardcoded zero regime confidence and blank structure/OI inputs.
        # Derive the real values ONCE from the now-bound RegimeState (`state`),
        # the structure analysis (`structure`), and the alt block (`alt`,
        # OI populated above from the #8/E7 source). Each is guarded so a
        # missing source degrades to the computation's neutral default
        # (no crash, no fabricated non-zero).
        _rc = float(state.confidence) if state is not None else 0.0
        _adx = float(state.adx) if state is not None else None
        _chop = float(state.choppiness) if state is not None else None
        # Issue #3B: treat a not-known volume ratio as missing (None) rather than
        # reconstituting the neutral placeholder 1.0 into a fake real value. The
        # downstream briefing chain already tolerates None (gates bypass on None;
        # interestingness handles None) — so this is the honest propagation.
        _vr = (
            float(state.volume_ratio)
            if state is not None and getattr(state, "volume_ratio_known", True)
            else None
        )
        _pir = float(structure.position_in_range) if structure is not None else None
        _mtf_dir = ""
        _mtf_h4 = ""
        _mtf_d1 = ""
        if structure is not None:
            _mtf_obj = getattr(structure, "mtf_confluence", None)
            if _mtf_obj is not None:
                # The pipeline exposes one collapsed MTF direction
                # (aligned_direction, set only when the MTF score>=3, else None)
                # as the H1 anchor. Issue 3 (2026-06-06) additionally surfaces each
                # higher TF's OWN bias (h4_bias / d1_bias) so the confluence
                # anchor-count below can count H4 and D1 agreement, not only H1.
                # These stay "" when the higher-TF feature
                # (structure.mtf_multi_timeframe_enabled) is off -> no extra anchor.
                # mtf_aligned_count is still left at its default (deriving it from
                # direction_alignment would double-count the structural-quality
                # component).
                _mtf_dir = str(getattr(_mtf_obj, "aligned_direction", "") or "")
                _mtf_h4 = str(getattr(_mtf_obj, "h4_bias", "") or "")
                _mtf_d1 = str(getattr(_mtf_obj, "d1_bias", "") or "")
        _oi_chg = float(alt.oi_change_24h_pct or 0.0)

        # ── Open position (force-included only) ──────────────────────
        # Position dict is prefetched once per cycle by
        # ``_prefetch_open_positions`` (called from ``tick`` for the
        # forced-symbol set only) and passed in as ``position``. The
        # previous inline ``pos_svc.get_position(symbol)`` was
        # async-called-from-sync; ``open_position`` would silently land
        # as ``None`` even when an open position existed, breaking HR-2.
        open_pos: dict | None = position if forced else None

        # ── State label (Phase 3 of the 1D briefing rewrite) ─────────
        # Pure-function classifier turns the assembled per-coin state
        # into one or more opportunity labels. Surface only — the
        # briefing-mode ranker (Phase 4) and brain prompt (Phase 6)
        # consume the field; Phase 3 just populates it.
        # Defensive: never raises; on any failure the package keeps
        # the default NO_TRADEABLE_STATE label and a blocker entry is
        # logged so operators see the labeler degraded.
        state_label = StateLabelBlock()
        label_result = None
        try:
            session_ctx = (
                getattr(structure, "session_context", None)
                if structure is not None else None
            )
            manipulation_likely = bool(
                getattr(session_ctx, "manipulation_likely", False)
                if session_ctx is not None else False
            )
            asian_range_broken_val = (
                getattr(session_ctx, "asian_range_broken", None)
                if session_ctx is not None else None
            )
            asian_range_broken_bool = bool(asian_range_broken_val) if (
                asian_range_broken_val not in (None, "", False)
            ) else False
            # Recent-loss flag is recorded by _check_blockers as a
            # ``recent_loss_within_<N>h`` token in record["blockers"].
            # Lift it to the labeler so RECENT_LOSER_COOLDOWN fires.
            is_recent_loser = any(
                isinstance(b, str) and b.startswith("recent_loss")
                for b in record.get("blockers", [])
            )
            consensus_dict = consensus or {}
            label_result = label_state(
                setup_type=str(getattr(xray, "setup_type", "none") or "none"),
                setup_type_confidence=float(
                    getattr(xray, "setup_type_confidence", 0.0) or 0.0
                ),
                trade_direction=str(getattr(xray, "trade_direction", "") or ""),
                suggested_direction=str(
                    getattr(structure, "suggested_direction", "") or ""
                    if structure is not None else ""
                ),
                regime=str(price.regime or ""),
                regime_confidence=_rc,  # E9: real regime confidence (was hardcoded 0.0)
                consensus=str(consensus_dict.get("consensus", "") or ""),
                consensus_direction=str(consensus_dict.get("direction", "") or ""),
                funding_rate=float(alt.funding_rate or 0.0),
                fear_greed=int(alt.fear_greed or 0),
                change_24h_pct=float(price.change_24h_pct or 0.0),
                session=str(getattr(xray, "session", "") or ""),
                session_phase=str(getattr(xray, "session_phase", "") or ""),
                manipulation_likely=manipulation_likely,
                asian_range_broken=asian_range_broken_bool,
                # Phase 5 will plumb the enriched XRAY engine fields:
                # range_compression, atr_pct_h1, in/counter direction
                # FVG/OB presence, position_in_range, volume_ratio.
                # Until then they default to safe values inside label_state.
                # Element 3 (2026-06-11) — pre-clamp range truth, passed
                # ALONE (deliberately NOT position_in_range, which stays
                # unplumbed so the dormant in-range gates keep their
                # legacy behaviour byte-identical). A contradicting break
                # suppresses the range-fade and funding-fade labels whose
                # mean-reversion premise it falsifies (June-11 DYDX wore
                # RANGE_FADE_LONG through a 24-submission breakdown).
                # Gated by [scanner.labeller]
                # range_fade_breakout_guard_enabled; "" reproduces legacy.
                range_breakout=(
                    str(getattr(structure, "range_breakout", "") or "")
                    if structure is not None and bool(getattr(
                        self.settings.scanner.labeller,
                        "range_fade_breakout_guard_enabled", True,
                    )) else ""
                ),
                has_open_position=bool(open_pos is not None),
                is_recent_loser=is_recent_loser,
                # Issue 3 of 2026-05-19 direction-bias fix Phase B —
                # operator-tunable regime-haircut multiplier replaces
                # the 8 per-trigger hard-kill predicates inside the
                # labeller. Default 0.5 (active). Plumbed from
                # [scanner.labeller] counter_regime_confidence_haircut.
                regime_haircut=float(
                    self.settings.scanner.labeller.counter_regime_confidence_haircut
                ),
                # Phase 1 calibration (2026-06-08) — extreme-sentiment label
                # conviction scaling + broadened off-trend haircut.
                extreme_conviction_floor=float(
                    self.settings.scanner.labeller.extreme_sentiment_conviction_floor
                ),
                extreme_offtrend_haircut=bool(
                    self.settings.scanner.labeller.extreme_sentiment_offtrend_haircut
                ),
            )
            state_label = StateLabelBlock(
                primary=label_result.primary,
                secondary=list(label_result.secondary),
                confidence=float(label_result.confidence),
            )
        except Exception as _e:
            blockers_observed.append("state_labeler_failed")
            log.debug(
                f"STATE_LABELER_FAIL | sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
            )

        # ── Interestingness score (Phase 4) ──────────────────────────
        # Continuous [0, 1] score from the assembled state + labels.
        # Defensive: failure logs and returns zeroed score so the
        # legacy opportunity_score path keeps working untouched.
        interestingness_score = 0.0
        interestingness_breakdown: dict = {}
        state_cleanness = 0.0
        confluence_count = 0
        try:
            cfg_briefing = getattr(self.settings.scanner, "briefing", None)
            weights = (
                InterestingnessWeights(
                    cleanness=cfg_briefing.interestingness_weights.cleanness,
                    confluence=cfg_briefing.interestingness_weights.confluence,
                    extremity=cfg_briefing.interestingness_weights.extremity,
                    label_strength=cfg_briefing.interestingness_weights.label_strength,
                    structural_quality=cfg_briefing.interestingness_weights.structural_quality,
                    mtf_alignment=cfg_briefing.interestingness_weights.mtf_alignment,
                    open_position_floor=cfg_briefing.interestingness_weights.open_position_floor,
                )
                if cfg_briefing is not None
                else None
            )
            consensus_dict_safe = consensus or {}
            interestingness = compute_interestingness(
                weights=weights,
                setup_type=str(getattr(xray, "setup_type", "none") or "none"),
                setup_type_confidence=float(
                    getattr(xray, "setup_type_confidence", 0.0) or 0.0
                ),
                setup_score=float(getattr(xray, "setup_score", 0.0) or 0.0),
                trade_direction=str(getattr(xray, "trade_direction", "") or ""),
                suggested_direction=(
                    str(getattr(structure, "suggested_direction", "") or "")
                    if structure is not None else ""
                ),
                rr_ratio=float(levels.rr_ratio or 0.0),
                mtf_quality=str(getattr(xray, "mtf_confluence", "") or ""),
                regime=str(price.regime or ""),
                # E9: real regime confidence (was hardcoded 0.0).
                regime_confidence=_rc,
                consensus=str(consensus_dict_safe.get("consensus", "") or ""),
                consensus_direction=str(
                    consensus_dict_safe.get("direction", "") or ""
                ),
                signal_direction=str(signals.direction or ""),
                funding_rate=float(alt.funding_rate or 0.0),
                fear_greed=int(alt.fear_greed or 0),
                has_open_position=bool(open_pos is not None),
                primary_label=state_label.primary,
                secondary_labels=list(state_label.secondary),
                # E9 (2026-05-28): plumb the real structure/regime inputs the
                # incomplete Phase 5 rollout left as stubs — so cleanness,
                # confluence, and extremity score on reality, not zeros.
                adx=_adx,
                choppiness=_chop,
                volume_ratio=_vr,
                position_in_range=_pir,
                mtf_h1_bias=_mtf_dir,  # H1 anchor (see derivation above)
                mtf_h4_bias=_mtf_h4,   # Issue 3 — H4 anchor (empty when HTF off)
                mtf_d1_bias=_mtf_d1,   # Issue 3 — D1 anchor (empty when HTF off)
                # E8: the corrected ~24h OI delta (was never passed -> always 0).
                oi_change_24h_pct=_oi_chg,
            )
            interestingness_score = float(interestingness.score)
            interestingness_breakdown = dict(interestingness.breakdown)
            state_cleanness = float(interestingness.state_cleanness)
            confluence_count = int(interestingness.confluence_count)
            # E9/E8 observability (activates the BRIEFING_INTERESTINGNESS tag,
            # previously declared but never emitted): proves the ranker now
            # scores on real inputs — regime confidence is no longer a constant
            # zero and the structure/OI inputs are populated per coin.
            log.debug(
                f"BRIEFING_INTERESTINGNESS | sym={symbol} "
                f"score={interestingness_score:.3f} rc={_rc:.2f} "
                f"adx={_adx} chop={_chop} pir={_pir} vr={_vr} "
                f"oi={_oi_chg:+.2f} mtf_dir={_mtf_dir or '-'} "
                f"breakdown={interestingness_breakdown} | {ctx()}"
            )
        except Exception as _e:
            blockers_observed.append("interestingness_failed")
            log.debug(
                f"INTERESTINGNESS_FAIL | sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
            )

        return CoinPackage(
            symbol=symbol,
            qualified=not forced,
            opportunity_score=float(score),
            qualification_reasons=list(record.get("reasons_passed", [])),
            price_data=price,
            xray=xray,
            strategies=strategies,
            signals=signals,
            alt_data=alt,
            open_position=open_pos,
            blockers_observed=blockers_observed,
            state_label=state_label,
            interestingness_score=interestingness_score,
            interestingness_breakdown=interestingness_breakdown,
            state_cleanness=state_cleanness,
            confluence_count=confluence_count,
        )

    def _qualifies(
        self,
        symbol: str,
        *,
        recent_loss_set: set[str] | None = None,
    ) -> tuple[bool, dict]:
        """Layer 1 restructure Phase 5 — apply 5-criterion qualitative checklist.

        Order matters — the implementation short-circuits at the first
        failed check so that ``record["reasons_failed"]`` shows the
        first failing criterion (the most useful debugging hint).

        Args:
            symbol: Watch_list coin to evaluate.
            recent_loss_set: Optional pre-computed set of symbols that
                closed at a loss within ``cfg.recent_failure_blocker_hours``.
                Forwarded to ``_check_blockers`` so the recent-loss
                blocker is one set membership lookup, not a per-coin DB
                query. ``tick`` populates this once per cycle.

        Returns:
            ``(qualified, record)`` where record has keys
            ``reasons_passed``, ``reasons_failed``, and ``blockers``.
        """
        cfg = self.settings.scanner.qualitative
        record: dict = {
            "reasons_passed": [],
            "reasons_failed": [],
            "blockers": [],
        }

        # Criterion 1 — XRAY setup type identified.
        sw = self.services.get("structure_worker")
        structure = None
        try:
            cache = getattr(sw, "_cache", None) if sw else None
            structure = cache.get(symbol) if cache and hasattr(cache, "get") else None
        except Exception:
            structure = None
        if structure is None:
            record["reasons_failed"].append("no_xray_analysis")
            return False, record
        setup_type = getattr(structure, "setup_type", None)
        if setup_type is None or getattr(setup_type, "value", "none") == "none":
            record["reasons_failed"].append("no_xray_setup_type")
            return False, record
        record["reasons_passed"].append(f"xray_setup={setup_type.value}")

        # Criterion 2 — ensemble consensus.
        lm = self.services.get("layer_manager")
        consensus = None
        if lm and hasattr(lm, "get_strategy_consensus"):
            consensus = lm.get_strategy_consensus(symbol)
        accept = {"STRONG"} if cfg.min_consensus == "STRONG" else {"STRONG", "GOOD"}
        if consensus is None or consensus.get("consensus") not in accept:
            label = consensus.get("consensus") if consensus else "NONE"
            record["reasons_failed"].append(f"consensus={label}")
            return False, record
        record["reasons_passed"].append(f"consensus={consensus['consensus']}")

        # Criterion 3 — regime alignment.
        if cfg.require_regime_alignment:
            rw = self.services.get("regime_worker")
            regime_label = ""
            if rw and hasattr(rw, "get_regime"):
                try:
                    state = rw.get_regime(symbol)
                    if state is not None:
                        regime_label = (
                            state.regime.value
                            if hasattr(state.regime, "value")
                            else str(state.regime)
                        )
                except Exception:
                    regime_label = ""
            direction = consensus.get("direction", "")
            if not self._regime_aligns(regime_label, direction):
                record["reasons_failed"].append(
                    f"regime={regime_label or 'unknown'}_vs_{direction}"
                )
                return False, record
            record["reasons_passed"].append(f"regime={regime_label}_aligns_{direction}")

        # Criterion 4 — RR ratio (direction-aware as of Definitive-fix
        # Phase 4). Read ``rr_long`` for long consensus, ``rr_short`` for
        # short, falling back to ``rr_ratio`` (= rr_best) when the
        # placement model lacks the per-direction fields. The previous
        # code unconditionally read ``rr_ratio`` which let coins pass
        # the gate on their *better* direction's RR even if the
        # consensus direction's RR was unprofitable.
        rr = 0.0
        try:
            sp = getattr(structure, "structural_placement", None)
            if sp is not None:
                direction = (consensus.get("direction") or "").lower()
                if direction == "long":
                    rr_field = getattr(sp, "rr_long", None)
                elif direction == "short":
                    rr_field = getattr(sp, "rr_short", None)
                else:
                    rr_field = None
                if rr_field is None or rr_field == 0.0:
                    rr_field = getattr(sp, "rr_ratio", 0.0)
                rr = float(rr_field or 0.0)
        except Exception:
            rr = 0.0
        if rr < cfg.min_rr_ratio:
            record["reasons_failed"].append(f"rr={rr:.2f}_below_{cfg.min_rr_ratio}")
            return False, record
        record["reasons_passed"].append(f"rr={rr:.2f}")

        # Criterion 5 — no blockers.
        blockers = self._check_blockers(
            symbol, structure, consensus, recent_loss_set=recent_loss_set,
        )
        if blockers:
            record["blockers"] = blockers
            record["reasons_failed"].append(f"blockers={','.join(blockers)}")
            return False, record

        return True, record

    @staticmethod
    def _derive_ab_mode_from_cycle_id(cycle_id: str) -> str:
        """Phase 8 of the 1D briefing rewrite — deterministic mode parity.

        Cycle IDs are minute-aligned to 5-min slots:
            c-2026-05-01-00:00  → slot 0 → exclusion
            c-2026-05-01-00:05  → slot 1 → briefing
            c-2026-05-01-00:10  → slot 2 → exclusion
            c-2026-05-01-00:15  → slot 3 → briefing
            ...

        The slot is computed from the minute portion of the cycle_id
        (``MM`` in ``c-YYYY-MM-DD-HH:MM``) divided by 5. When the
        cycle_id can't be parsed, defaults to "exclusion" (safe path).

        Returns:
            ``"exclusion"`` for even slots, ``"briefing"`` for odd
            slots. Static method so tests can call without a
            ScannerWorker instance.
        """
        try:
            # Format: c-YYYY-MM-DD-HH:MM
            tail = cycle_id.rsplit("-", 1)[-1]   # "HH:MM"
            mm = int(tail.split(":")[1])
            slot = mm // 5
            return "briefing" if (slot % 2) == 1 else "exclusion"
        except Exception:
            return "exclusion"

    def _derive_ab_mode(self) -> str:
        """Return the A/B-derived mode for the cycle that's about to fire."""
        from src.core.cycle_tracker import CycleTracker
        cid = CycleTracker.make_cycle_id()
        return self._derive_ab_mode_from_cycle_id(cid)

    # ----- briefing-mode tick (Phase 5 of the 1D briefing rewrite) ---------

    async def _tick_briefing_mode(self) -> None:
        """Briefing-mode tick — produces >=12 enriched briefings per cycle.

        The exploit-not-exclude flow:
          1. Read watch_list (50 coins) + open positions (force-include).
          2. Prefetch async data once per cycle (F&G, positions, recent loss).
          3. For EVERY coin: compute opportunity_score (legacy breakdown
             keys preserved for SCANNER_SELECTED log compat) AND build a
             CoinPackage. The package already carries state_label and
             interestingness_score from the Phase 3/4 wiring inside
             ``_build_package``.
          4. Sort by interestingness_score descending. Take
             ``top_n_packages`` (default 15). Apply soft floor:
             pad up to ``min_briefing_packages`` (default 12) from the
             top of the unselected tail so the brain ALWAYS sees >=12.
          5. Force-include open positions (HR-2). Forced symbols
             always present in the selection regardless of rank.
          6. Validate each package; quarantine FAILs (same semantics as
             exclusion mode). Write surviving packages to
             ``lm._coin_packages``.
          7. Stamp CycleTracker briefing aggregates so the hourly
             ``cycle_metrics`` flush populates the new columns from
             Phase 1.
          8. Same active_universe DB write + ``set_active_universe``
             handoff. Same SCANNER_SELECTED per-coin INFO log
             (preserves legacy breakdown keys for grep compatibility).
             New SCANNER_BRIEFING_SUMMARY tag carries the per-cycle
             label distribution + interestingness mean.

        The legacy SCANNER_FILTER_AGGREGATE log fires with all fail_*
        buckets at 0 under briefing mode (nothing is filtered out at
        the gate level) so log scrapers and dashboards built against
        the legacy tag continue working without changes.
        """
        t0 = time.monotonic()
        cycle_id = None
        ct = self.services.get("cycle_tracker")
        if ct and hasattr(ct, "start_cycle"):
            try:
                cycle_id = ct.start_cycle("layer1d")
            except Exception:
                cycle_id = None

        cfg_briefing = self.settings.scanner.briefing
        cfg_q = self.settings.scanner.qualitative
        watch_list = list(self.settings.universe.watch_list)
        if not watch_list:
            log.warning(
                f"SCANNER_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return

        protected = await self._open_position_symbols()

        # Prefetch recent losers (used by the labeler RECENT_LOSER_COOLDOWN
        # advisory; matches exclusion mode's prefetch pattern).
        recent_loss_set: set[str] = set()
        try:
            from src.core.trade_recorder import recent_loss_symbols
            recent_loss_set = await recent_loss_symbols(
                self.db, hours=cfg_q.recent_failure_blocker_hours,
            )
        except Exception as e:
            log.debug(
                f"SCANNER_RECENT_LOSS_FETCH_FAIL | err='{str(e)[:80]}' | {ctx()}"
            )

        # Prefetch async-context data ONCE per cycle (same pattern as
        # exclusion mode — see lines ~1245 below for the rationale).
        fg_value = await self._prefetch_fear_greed()
        position_lookup = await self._prefetch_open_positions(list(protected))

        # ── Score every coin (watch_list ∪ protected) ─────────────────
        # Each entry: (symbol, opportunity_score, breakdown,
        #              record_dict, forced, interestingness_score, package).
        scored: list[tuple[str, float, dict, dict, bool, float, CoinPackage]] = []
        all_symbols: list[str] = list(watch_list)
        for s in protected:
            if s not in all_symbols:
                all_symbols.append(s)

        # F9 (2026-06-09): loss-only cooldown selection exclusion. When enabled
        # ([apex].loss_cooldown_enabled), a symbol in an active loss cooldown is
        # held OUT of the candidate list so a fresh coin takes its slot (via the
        # reserve_slots_union cut below); it reappears exactly when the cooldown
        # expires (is_symbol_in_any_cooldown lazy-cleans on read). Open positions
        # (forced) are never excluded — they must stay surfaced for management.
        # Resolved once per cycle. Default OFF reproduces the prior behaviour.
        _f9_coord = self.services.get("trade_coordinator")
        _f9_loss_cd = bool(
            getattr(getattr(self.settings, "apex", None),
                    "loss_cooldown_enabled", False)
        )
        _f9_excluded: list[str] = []

        for coin in all_symbols:
            forced = (coin in protected)
            if (
                _f9_loss_cd and not forced and _f9_coord is not None
                and hasattr(_f9_coord, "is_symbol_in_any_cooldown")
                and _f9_coord.is_symbol_in_any_cooldown(coin)
            ):
                _f9_excluded.append(coin)
                log.info(
                    f"SCANNER_LOSS_COOLDOWN_EXCLUDED | sym={coin} | F9: in active "
                    f"loss cooldown — held out of the candidate list, reappears "
                    f"after expiry | {ctx()}"
                )
                continue
            # Per-coin opportunity_score with legacy breakdown keys —
            # preserved so SCANNER_SELECTED log shape stays identical.
            try:
                score, breakdown = self._compute_opportunity_score(coin)
            except Exception:
                score, breakdown = 0.0, {
                    "structure": 0.0, "structure_raw": 0.0,
                    "structure_conf": 0.85, "strategy": 0.0,
                    "signal": 0.0, "regime": 0.5,
                    "funding": 0.0, "rr": 0.0,
                }

            # Briefing mode never excludes; the record dict keeps the
            # exclusion-mode shape so existing _build_package consumers
            # (and the active_universe DB writer's _enrich_for) read
            # familiar fields. Reasons-passed records the LABEL the
            # state labeller fired so operators can grep per-coin
            # provenance even without the per-coin DEBUG line.
            record: dict = {
                "reasons_passed": [],
                "reasons_failed": [],
                "blockers": [],
            }
            if coin in recent_loss_set:
                # Surface to the labeler via the same channel exclusion
                # mode uses — _build_package lifts this into the
                # RECENT_LOSER_COOLDOWN advisory label.
                record["blockers"].append(
                    f"recent_loss_within_{cfg_q.recent_failure_blocker_hours}h"
                )

            try:
                pkg = self._build_package(
                    coin, score, record, forced,
                    fg_value=fg_value,
                    position=position_lookup.get(coin) if forced else None,
                )
            except Exception as e:
                log.warning(
                    f"SCANNER_PACKAGE_BUILD_FAIL | sym={coin} "
                    f"err='{str(e)[:100]}' | {ctx()}"
                )
                continue

            interest = float(getattr(pkg, "interestingness_score", 0.0) or 0.0)
            # Briefing-mode "qualified" repurposes the boolean to mean
            # "non-advisory primary label AND interestingness above the
            # qualified_threshold". This drives the cold-start gate's
            # ``qualified_count`` (Phase 7 reads).
            try:
                from src.workers.scanner.state_labeler import ADVISORY_LABELS
                primary = pkg.state_label.primary if pkg.state_label else ""
                pkg.qualified = (
                    bool(primary) and primary not in ADVISORY_LABELS
                    and interest >= cfg_briefing.qualified_threshold
                )
            except Exception:
                pkg.qualified = False
            # Reasons-passed reflects the label provenance for transparency.
            if pkg.state_label and pkg.state_label.primary:
                record["reasons_passed"].append(
                    f"label={pkg.state_label.primary}"
                )

            scored.append((coin, score, breakdown, record, forced, interest, pkg))

        # ── Sort + top-N + soft floor ─────────────────────────────────
        # Force-included symbols always present regardless of rank.
        forced_records = [r for r in scored if r[4]]
        candidate_records = [r for r in scored if not r[4]]

        top_n = int(cfg_briefing.top_n_packages)
        min_pkgs = int(cfg_briefing.min_briefing_packages)
        budget_for_candidates = max(0, top_n - len(forced_records))
        # Issue #2 fix (2026-05-27): RESERVE SLOTS at the scanner cut too, so
        # the 50->15 stage no longer drops high-opportunity coins purely for
        # low interestingness. Draw alternately from top-by-opportunity (r[1])
        # and top-by-interestingness (r[5]); forced rows are always kept; the
        # soft floor still pads to min_pkgs.
        from src.core.ranking import reserve_slots_union
        _picked_cands, _c_from_opp, _c_from_int = reserve_slots_union(
            candidate_records, budget_for_candidates,
            opp_key=lambda r: r[1],
            int_key=lambda r: r[5],
        )
        selected = list(forced_records) + _picked_cands
        # Soft floor — pad up to min_pkgs from any not-yet-selected candidates
        # (by interestingness desc, opportunity desc).
        if len(selected) < min_pkgs:
            already = {r[0] for r in selected}
            _rest = sorted(
                [r for r in candidate_records if r[0] not in already],
                key=lambda r: (r[5], r[1]), reverse=True,
            )
            for r in _rest:
                selected.append(r)
                if len(selected) >= min_pkgs:
                    break

        log.info(
            f"SCANNER_RESERVE_SLOTS | budget={budget_for_candidates} "
            f"from_opportunity={_c_from_opp} from_interestingness={_c_from_int} "
            f"forced={len(forced_records)} selected={len(selected)} | {ctx()}"
        )
        # Final sort for SCANNER_SELECTED ordering: forced first (by
        # interestingness desc among them), then non-forced by
        # interestingness desc.
        selected.sort(key=lambda r: (r[4], r[5], r[1]), reverse=True)

        # ── Validate + write packages ─────────────────────────────────
        from src.core.coin_package_validator import (
            VERDICT_FAIL,
            VERDICT_WARN,
            validate_package,
        )
        _vld_cfg = getattr(self.settings, "coin_package_validator", None)
        _fail_below = float(getattr(_vld_cfg, "fail_below", 0.50)) if _vld_cfg else 0.50
        _warn_below = float(getattr(_vld_cfg, "warn_below", 0.85)) if _vld_cfg else 0.85
        _staleness = float(
            getattr(_vld_cfg, "staleness_fail_seconds", 300.0)
        ) if _vld_cfg else 300.0

        t_pkg = time.monotonic()
        log.info(
            f"SCANNER_PACKAGE_BUILD_START | cycle_id={cycle_id} "
            f"packages_to_build={len(selected)} | {ctx()}"
        )

        packages: dict[str, CoinPackage] = {}
        _vld_ok = 0
        _vld_warn = 0
        _vld_fail = 0
        for coin, score, _bd, _record, forced, interest, pkg in selected:
            try:
                vr = validate_package(
                    pkg,
                    fail_below=_fail_below,
                    warn_below=_warn_below,
                    staleness_fail_seconds=_staleness,
                )
                log.info(
                    f"PACKAGE_VALIDATE | cycle_id={cycle_id} sym={coin} "
                    f"completeness={vr.completeness:.2f} verdict={vr.verdict} "
                    f"missing={vr.missing_fields} stale={vr.stale_fields} | {ctx()}"
                )
                if vr.verdict == VERDICT_FAIL:
                    _vld_fail += 1
                    log.warning(
                        f"PACKAGE_QUARANTINED | cycle_id={cycle_id} sym={coin} "
                        f"completeness={vr.completeness:.2f} "
                        f"missing={vr.missing_fields} stale={vr.stale_fields} | {ctx()}"
                    )
                    continue
                if vr.verdict == VERDICT_WARN:
                    _vld_warn += 1
                else:
                    _vld_ok += 1
                pkg.completeness = float(vr.completeness)
                # Issue #12 fix: carry the validator's provenance onto the
                # package so the brain prompt can surface it (these fields were
                # computed then discarded before). Also log the build-time
                # source-failure blockers, which previously went unrecorded.
                pkg.missing_fields = list(vr.missing_fields)
                pkg.stale_fields = list(vr.stale_fields)
                if pkg.blockers_observed:
                    log.info(
                        f"PACKAGE_BLOCKERS | cycle_id={cycle_id} sym={coin} "
                        f"blockers={pkg.blockers_observed} "
                        f"completeness={vr.completeness:.2f} | {ctx()}"
                    )
                packages[coin] = pkg
            except Exception as e:
                log.warning(
                    f"SCANNER_PACKAGE_BUILD_FAIL | sym={coin} "
                    f"err='{str(e)[:100]}' | {ctx()}"
                )

        lm = self.services.get("layer_manager")
        if lm is not None:
            lm._coin_packages = packages
            try:
                from src.core.cache_freshness import record_write
                record_write("packages")
                for _sym in packages:
                    record_write("packages", _sym)
            except Exception:  # pragma: no cover
                pass

        total_bytes = sum(p.size_bytes() for p in packages.values())
        log.info(
            f"SCANNER_PACKAGE_BUILD_DONE | cycle_id={cycle_id} "
            f"packages={len(packages)} total_size_bytes={total_bytes} "
            f"elapsed_ms={(time.monotonic() - t_pkg) * 1000:.0f} | {ctx()}"
        )
        log.info(
            f"PACKAGE_VALIDATE_SUMMARY | cycle_id={cycle_id} "
            f"packages_built={_vld_ok + _vld_warn + _vld_fail} "
            f"ok={_vld_ok} warn={_vld_warn} fail_quarantined={_vld_fail} | {ctx()}"
        )
        # Issue E7 (2026-05-28): per-cycle confirmation that the brain's
        # open-interest field is wired to a real value (completes #8's E7
        # companion at the OI assignment above). Pre-#8 every package rendered
        # OI at exactly 0.00%; this coverage line proves the brain now sees
        # real, varying OI across the delivered packages. Observability-only,
        # one line per cycle (mirrors the E11 heat-map cadence below).
        _oi_nonzero = sum(
            1 for _p in packages.values()
            if abs(float(getattr(_p.alt_data, "oi_change_24h_pct", 0.0) or 0.0)) > 1e-9
        )
        log.info(
            f"OI_BRAIN_WIRED | cycle_id={cycle_id} packages={len(packages)} "
            f"oi_nonzero={_oi_nonzero} oi_zero={len(packages) - _oi_nonzero} | {ctx()}"
        )

        # Issue E11 (2026-05-27): per-cycle source-failure heat-map. The
        # per-package PACKAGE_BLOCKERS line above fires once per package that
        # has blockers; this aggregates the blocker labels across ALL coins
        # scanned this cycle (including quarantined ones) so operators see
        # WHICH sources fail and HOW OFTEN — a failure heat-map, not a stream
        # of per-coin lines. Fires once per cycle, only when something failed.
        # Observability-only; distinct tag from the per-package PACKAGE_BLOCKERS.
        from collections import Counter as _Counter
        _blocker_heat: _Counter = _Counter()
        for _sel in selected:
            for _b in (_sel[-1].blockers_observed or []):
                _blocker_heat[_b] += 1
        if _blocker_heat:
            _by_label = ", ".join(f"{k}={v}" for k, v in _blocker_heat.most_common())
            log.info(
                f"PACKAGE_BLOCKER_HEATMAP | cycle_id={cycle_id} "
                f"packages={len(selected)} blockers_total={sum(_blocker_heat.values())} "
                f"by_label=[{_by_label}] | {ctx()}"
            )

        # ── Per-cycle aggregates: legacy SCANNER_FILTER_AGGREGATE shape ──
        # All fail_* counts are 0 in briefing mode — nothing is excluded.
        # Pass counts reflect what XRAY/consensus would have shown.
        agg = {
            "fail_no_xray": 0, "fail_setup_none": 0, "fail_consensus": 0,
            "fail_regime": 0, "fail_rr": 0, "fail_blockers": 0,
            "pass_xray": 0, "pass_consensus_strong": 0, "pass_consensus_good": 0,
        }
        qualified_count = 0
        for coin, _score, _bd, _r, _f, _i, pkg in scored:
            if (pkg.xray.setup_type or "none") != "none":
                agg["pass_xray"] += 1
            ec = pkg.strategies.ensemble_consensus
            if ec == "STRONG":
                agg["pass_consensus_strong"] += 1
            elif ec == "GOOD":
                agg["pass_consensus_good"] += 1
            if pkg.qualified:
                qualified_count += 1
        log.info(
            f"SCANNER_FILTER_AGGREGATE | cycle_id={cycle_id} "
            f"total={len(watch_list)} qualified={qualified_count} "
            f"fail_no_xray=0 fail_setup_none=0 fail_consensus=0 "
            f"fail_regime=0 fail_rr=0 fail_blockers=0 "
            f"pass_xray={agg['pass_xray']} "
            f"pass_consensus_strong={agg['pass_consensus_strong']} "
            f"pass_consensus_good={agg['pass_consensus_good']} | {ctx()}"
        )

        # ── New per-cycle SCANNER_BRIEFING_SUMMARY tag ────────────────
        from src.workers.scanner.state_labeler import (
            ADVISORY_LABELS,
            LABEL_NO_TRADEABLE_STATE,
        )
        label_counts: dict[str, int] = {}
        interest_values: list[float] = []
        for _coin, _s, _bd, _r, _f, interest, pkg in selected:
            primary = pkg.state_label.primary if pkg.state_label else LABEL_NO_TRADEABLE_STATE
            label_counts[primary] = label_counts.get(primary, 0) + 1
            interest_values.append(float(interest))
        with_label = sum(
            1 for name in label_counts
            if name and name not in ADVISORY_LABELS
            and name != LABEL_NO_TRADEABLE_STATE
        )
        advisory_only = sum(
            label_counts[k] for k in label_counts
            if k in ADVISORY_LABELS or k == LABEL_NO_TRADEABLE_STATE
        )
        mean_interest = (
            sum(interest_values) / len(interest_values) if interest_values else 0.0
        )
        top_label = (
            max(label_counts.items(), key=lambda kv: kv[1])[0]
            if label_counts else "—"
        )
        log.info(
            f"SCANNER_BRIEFING_SUMMARY | cycle_id={cycle_id} "
            f"total={len(selected)} with_label={with_label} "
            f"advisory_only={advisory_only} "
            f"mean_interestingness={mean_interest:.3f} "
            f"top_label={top_label} "
            f"loss_cooldown_excluded={len(_f9_excluded)} "  # F9: held out of the list
            f"| {ctx()}"
        )

        # ── Stamp briefing aggregates onto CycleTracker (Phase 1 plumbing) ──
        if ct and hasattr(ct, "record_briefing"):
            try:
                ct.record_briefing(
                    cycle_id,
                    interestingness_score=mean_interest,
                    state_label_counts=label_counts,
                    briefing_packages_count=len(packages),
                )
            except Exception as e:
                log.debug(
                    f"CYCLE_TRACKER_RECORD_BRIEFING_FAIL | "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )
        if ct and hasattr(ct, "record_qualified"):
            try:
                ct.record_qualified(
                    cycle_id,
                    qualified=qualified_count,
                    selected=len(selected),
                    packages=len(packages),
                )
            except Exception:
                pass

        # ── Same active_universe DB write as exclusion mode ──────────
        new_symbols = [r[0] for r in selected]

        def _enrich_for(coin: str) -> tuple[float, float, float, float]:
            pkg = packages.get(coin)
            if pkg is None:
                return (0.0, 0.0, 0.0, 0.0)
            try:
                vol = float(getattr(pkg.price_data, "volume_24h_usd", 0.0) or 0.0)
                chg = float(getattr(pkg.price_data, "change_24h_pct", 0.0) or 0.0)
                funding = float(getattr(pkg.alt_data, "funding_rate", 0.0) or 0.0)
            except Exception:
                vol, chg, funding = 0.0, 0.0, 0.0
            return (vol, chg, funding, 0.0)

        try:
            await self.db.execute("DELETE FROM active_universe")
            insert_rows = []
            for coin, score, _bd, _r, _f, _i, _pkg in selected:
                vol, chg, funding, spread = _enrich_for(coin)
                insert_rows.append((
                    coin, round(float(score), 4),
                    vol, chg, funding, spread,
                    MarketScanner.get_coin_tier(coin),
                ))
            if insert_rows:
                await self.db.executemany(
                    "INSERT OR REPLACE INTO active_universe "
                    "(symbol, opportunity_score, volume_24h, change_24h_pct, "
                    "funding_rate, spread_pct, coin_tier) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    insert_rows,
                )
        except Exception as e:
            log.warning(f"SCANNER_DB_WRITE_FAIL | err={str(e)[:100]} | {ctx()}")

        self.scanner.set_active_universe(new_symbols)
        empty_set: set[str] = set()
        for cb in self.scanner.get_subscribers_snapshot():
            try:
                await cb(new_symbols, set(new_symbols), empty_set)
            except Exception as cb_e:
                log.debug(
                    f"SCANNER_SUBSCRIBER_FAIL | err={str(cb_e)[:80]} | {ctx()}"
                )

        # ── Per-coin SCANNER_SELECTED + new SCANNER_LABELED tags ─────
        for rank, (coin, score, breakdown, _record, forced, interest, pkg) in enumerate(
            selected, start=1,
        ):
            log.info(
                f"SCANNER_SELECTED | rank={rank} coin={coin} score={score:.4f} "
                f"forced={forced} "
                f"src=structure:{breakdown['structure']},"
                f"strategy:{breakdown['strategy']},"
                f"signal:{breakdown['signal']},"
                f"regime:{breakdown['regime']},"
                f"funding:{breakdown['funding']},"
                f"rr:{breakdown.get('rr', 0.0)} "
                f"struct_raw:{breakdown.get('structure_raw', 0.0)} "
                f"struct_conf:{breakdown.get('structure_conf', 1.0)} | {ctx()}"
            )
            secondary_str = ",".join(pkg.state_label.secondary) if pkg.state_label else ""
            log.info(
                f"SCANNER_LABELED | rank={rank} coin={coin} "
                f"primary={pkg.state_label.primary if pkg.state_label else '—'} "
                f"secondary={secondary_str or '—'} "
                f"label_conf={(pkg.state_label.confidence if pkg.state_label else 0.0):.2f} "
                f"interestingness={interest:.3f} | {ctx()}"
            )

        # ── Cycle-end bookkeeping ────────────────────────────────────
        el_ms = (time.monotonic() - t0) * 1000
        forced_in_count = sum(1 for r in selected if r[4])
        log.info(
            f"SCANNER_SELECT | cycle_id={cycle_id} "
            f"qualified={qualified_count} selected={len(selected)} "
            f"forced={forced_in_count} watch_list={len(watch_list)} | {ctx()}"
        )
        if selected:
            top_str = f"top={selected[0][0]}({selected[0][5]:.3f})"
        else:
            top_str = "top=-"
        log.info(
            f"SCANNER_TICK_SUMMARY | watch_list={len(watch_list)} "
            f"protected={len(protected)} scored={len(scored)} "
            f"selected={len(selected)} top_n={top_n} "
            f"forced_in={forced_in_count} "
            f"mean_score={mean_interest:.3f} {top_str} "
            f"el={el_ms:.0f}ms drift_ms=0 | {ctx()}"
        )
        if ct and hasattr(ct, "end_cycle") and cycle_id is not None:
            try:
                ct.end_cycle("layer1d", cycle_id)
            except Exception:
                pass

    # ----- main tick -------------------------------------------------------

    async def tick(self) -> None:
        """Layer 1D selector — branches on ``settings.scanner.mode``.

        Phase 5 of the 1D briefing rewrite introduces a mode flag:

        * ``"exclusion"`` (production default; this method's body below)
          runs the legacy 5-criterion qualitative gate followed by
          ranking of survivors. Behavior is byte-identical to pre-Phase-5
          production.

        * ``"briefing"`` calls ``_tick_briefing_mode`` instead — the
          briefing pipeline scores every coin by interestingness, takes
          top-N with a soft floor, and surfaces packages for ALL of
          them (no exclusion). Phase 9 flips the default after Phase 8
          A/B observation.

        Flow (exclusion mode — preserved):
          1. Read watch_list (50 coins).
          2. Fetch open positions (force-include, HR-2).
          3. For each coin, run ``_qualifies()``:
             - XRAY setup_type != NONE
             - Ensemble consensus in {STRONG, GOOD}
             - Regime aligns with proposed direction
             - RR ratio ≥ ``min_rr_ratio``
             - No blockers (funding, manipulation, recent loss)
          4. For every qualifier OR force-included coin, compute the
             composite ``_compute_opportunity_score`` (reused; matches
             the old scoring formula exactly).
          5. Sort survivors descending. Take top ``max_selection``; if
             fewer than ``min_selection`` qualified, output what we have
             (could be 0 — Stage 2 handles "no setups this cycle").
          6. Persist active_universe + MarketScanner._active_universe.
        """
        mode = getattr(self.settings.scanner, "mode", "exclusion") or "exclusion"
        # Phase 8 of the 1D briefing rewrite — A/B harness override.
        # When ab_mode == "alternating", derive the effective mode from
        # the cycle_id's 5-min slot parity (deterministic per cycle).
        # Even-indexed cycle (slot 0/2/4/...) → exclusion mode.
        # Odd-indexed cycle (slot 1/3/5/...) → briefing mode.
        # The ``mode`` config value is ignored under "alternating".
        ab_mode = getattr(self.settings.scanner, "ab_mode", "off") or "off"
        if ab_mode == "alternating":
            mode = self._derive_ab_mode()
            log.info(
                f"BRIEFING_AB_COMPARE | ab_mode=alternating "
                f"effective_mode={mode} | {ctx()}"
            )
        if mode == "briefing":
            return await self._tick_briefing_mode()
        # Legacy exclusion-mode body continues below — byte-identical to
        # pre-Phase-5 production.
        t0 = time.monotonic()
        cycle_id = None
        ct = self.services.get("cycle_tracker")
        if ct and hasattr(ct, "start_cycle"):
            try:
                cycle_id = ct.start_cycle("layer1d")
            except Exception:
                cycle_id = None

        cfg_q = self.settings.scanner.qualitative
        watch_list = list(self.settings.universe.watch_list)
        if not watch_list:
            log.warning(
                f"SCANNER_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
            )
            return

        protected = await self._open_position_symbols()

        # Phase 5 audit fix — prefetch recent losers ONCE per tick. The
        # blocker check inside _check_blockers does a set-membership
        # lookup against this set instead of an O(50) DB hit per cycle.
        # Failure-tolerant: recent_loss_symbols returns an empty set on
        # any error so a recorder hiccup does not break the scanner.
        recent_loss_set: set[str] = set()
        try:
            from src.core.trade_recorder import recent_loss_symbols
            recent_loss_set = await recent_loss_symbols(
                self.db, hours=cfg_q.recent_failure_blocker_hours,
            )
        except Exception as e:
            log.debug(
                f"SCANNER_RECENT_LOSS_FETCH_FAIL | err='{str(e)[:80]}' | {ctx()}"
            )

        # Phase 5 — qualitative gate on every watch_list coin. Open
        # positions are force-included even if they fail the gate (HR-2).
        qualified_records: list[tuple[str, float, dict, dict, bool]] = []
        qualified_count = 0

        # Phase 6 (post-Layer-1 fix). Per-cycle aggregate counters.
        # SCANNER_FILTER_AGGREGATE is the operator-facing summary that
        # collapses 50 per-coin DEBUG lines into one INFO line per
        # cycle. The 5 fail buckets cover the 5 criteria in
        # ``_qualifies``; the 3 pass buckets surface what's actually
        # passing each criterion (especially helpful when consensus
        # cache is sparse — see Phase 4 fix).
        agg = {
            "fail_no_xray": 0,        # criterion 1 — no analysis or none type
            "fail_setup_none": 0,     # criterion 1 — setup_type=none
            "fail_consensus": 0,      # criterion 2 — consensus not in {STRONG,GOOD}
            "fail_regime": 0,         # criterion 3 — regime mis-aligned
            "fail_rr": 0,             # criterion 4 — RR below threshold
            "fail_blockers": 0,       # criterion 5 — funding/manip/recent-loss
            "pass_xray": 0,           # symbols that cleared criterion 1
            "pass_consensus_strong": 0,
            "pass_consensus_good": 0,
        }

        for coin in watch_list:
            qualified, record = self._qualifies(coin, recent_loss_set=recent_loss_set)

            # Phase 6 (post-Layer-1 fix). Bucket by the FIRST failure
            # reason — same semantics as the per-coin DEBUG line. Pass
            # counters look at reasons_passed and tally specific
            # categories that operators care about.
            failed = record.get("reasons_failed") or []
            if failed:
                first = failed[0]
                if first == "no_xray_analysis":
                    agg["fail_no_xray"] += 1
                elif first == "no_xray_setup_type":
                    agg["fail_setup_none"] += 1
                elif first.startswith("consensus="):
                    agg["fail_consensus"] += 1
                elif first.startswith("regime="):
                    agg["fail_regime"] += 1
                elif first.startswith("rr="):
                    agg["fail_rr"] += 1
                elif first.startswith("blockers="):
                    agg["fail_blockers"] += 1
            for r in record.get("reasons_passed") or []:
                if r.startswith("xray_setup="):
                    agg["pass_xray"] += 1
                elif r == "consensus=STRONG":
                    agg["pass_consensus_strong"] += 1
                elif r == "consensus=GOOD":
                    agg["pass_consensus_good"] += 1

            if qualified:
                qualified_count += 1
            forced = (coin in protected) and not qualified
            if not (qualified or forced):
                # Per-coin trace at DEBUG so operators can grep failures.
                log.debug(
                    f"SCANNER_FILTER_RESULT | sym={coin} qualified=false "
                    f"reasons={','.join(record['reasons_failed'])} | {ctx()}"
                )
                continue
            score, breakdown = self._compute_opportunity_score(coin)
            qualified_records.append((coin, score, breakdown, record, forced))
            log.debug(
                f"SCANNER_FILTER_RESULT | sym={coin} qualified={qualified} "
                f"forced={forced} score={score:.4f} "
                f"reasons={','.join(record['reasons_passed']) or 'force_included'} "
                f"| {ctx()}"
            )

        # Phase 6 (post-Layer-1 fix). Single per-cycle aggregate at INFO.
        # Counts sum to len(watch_list) modulo a small forced-include
        # delta — operators can verify "qualified + sum(fail_*) ≈ 50"
        # to spot accounting bugs.
        log.info(
            f"SCANNER_FILTER_AGGREGATE | cycle_id={cycle_id} "
            f"total={len(watch_list)} qualified={qualified_count} "
            f"fail_no_xray={agg['fail_no_xray']} "
            f"fail_setup_none={agg['fail_setup_none']} "
            f"fail_consensus={agg['fail_consensus']} "
            f"fail_regime={agg['fail_regime']} "
            f"fail_rr={agg['fail_rr']} "
            f"fail_blockers={agg['fail_blockers']} "
            f"pass_xray={agg['pass_xray']} "
            f"pass_consensus_strong={agg['pass_consensus_strong']} "
            f"pass_consensus_good={agg['pass_consensus_good']} | {ctx()}"
        )

        # Open-position coins outside the watch_list still need force-inclusion
        # so we don't orphan a position. Score them with whatever data we have.
        for coin in protected:
            if coin in watch_list:
                continue  # already considered
            if any(r[0] == coin for r in qualified_records):
                continue
            score, breakdown = self._compute_opportunity_score(coin)
            qualified_records.append(
                (coin, score, breakdown,
                 {"reasons_passed": ["force_open_position_off_watch_list"],
                  "reasons_failed": [], "blockers": []},
                 True)
            )

        # Rank survivors by composite score.
        qualified_records.sort(key=lambda r: r[1], reverse=True)

        n_max = int(cfg_q.max_selection)
        n_min = int(cfg_q.min_selection)
        # Take top n_max; if fewer survived than min_selection, output
        # ALL survivors (could be empty — Stage 2 handles no-setup cycles).
        if len(qualified_records) >= n_max:
            selected = qualified_records[:n_max]
        elif len(qualified_records) >= n_min:
            selected = qualified_records
        else:
            selected = qualified_records  # min_selection often 0 today

        forced_in_count = sum(1 for r in selected if r[4])
        final = selected
        new_symbols = [c for c, _, _, _, _ in final]

        # NOTE: BTC/ETH reference-pair unconditional force-include was
        # removed 2026-04-29. Rationale: the legacy add caused brain
        # CALL_A to dispatch hallucinated trades on BTC/ETH every cycle
        # (their packages went into the prompt under "TRADE CANDIDATES"
        # despite ``qualified=False``). HR-2 (force-include open positions)
        # is preserved via the protected-symbols path above (see lines
        # ~870-885); only the unconditional ref-pair add is removed.
        # The ``_active_universe`` and DB ``active_universe`` table
        # therefore reflect the qualified set + open positions only,
        # matching scanner intent.

        # Layer 1 restructure Phase 6 — build self-contained CoinPackages
        # for every selected coin. ScannerWorker writes to
        # layer_manager._coin_packages; Phase 7 wires Stage 2 to read from
        # this cache instead of querying 12 services per cycle.
        t_pkg = time.monotonic()
        log.info(
            f"SCANNER_PACKAGE_BUILD_START | cycle_id={cycle_id} "
            f"packages_to_build={len(final)} | {ctx()}"
        )

        # Async-correctness fix (Layer 1D root cause). Resolve all
        # async-context data ONCE here, before the sync ``_build_package``
        # loop, instead of letting the builder mis-call async methods
        # without ``await``. Two prefetches:
        #
        #   1. F&G — a single global market reading. The previous
        #      ``fg.get_latest()`` inside ``_build_package`` was
        #      async-called-from-sync, leaking a coroutine through
        #      ``getattr(coro, "value", 0)`` whose default silently set
        #      every package's ``alt_data.fear_greed = 0``. Validator
        #      treated 0 as missing, capping completeness at ~0.94 and
        #      tripping the brain boot-grace gate (0.95). Live trace:
        #      every BRAIN_CYCLE_A within 10 min of restart dropped
        #      every trade.
        #
        #   2. Open positions — same pattern at the per-symbol level.
        #      ``pos_svc.get_position`` is async; the previous sync call
        #      left ``open_position=None`` even when a real position
        #      existed, masking HR-2 (force-include open positions so
        #      Claude can decide hold/close).
        #
        # See ``_prefetch_fear_greed`` and ``_prefetch_open_positions``
        # for the detailed failure-mode write-ups.
        fg_value = await self._prefetch_fear_greed()
        forced_symbols = [coin for coin, _, _, _, forced in final if forced]
        position_lookup = await self._prefetch_open_positions(forced_symbols)

        packages: dict[str, CoinPackage] = {}
        # Phase 5 (output-quality): per-cycle validation counters for
        # the summary log. Quarantined packages are NOT inserted into
        # the cache so Stage 2 never operates on them.
        _vld_ok = 0
        _vld_warn = 0
        _vld_fail = 0
        # Read validator thresholds from settings (with safe defaults).
        _vld_cfg = getattr(self.settings, "coin_package_validator", None)
        _fail_below = float(getattr(_vld_cfg, "fail_below", 0.50)) if _vld_cfg else 0.50
        _warn_below = float(getattr(_vld_cfg, "warn_below", 0.85)) if _vld_cfg else 0.85
        _staleness = float(
            getattr(_vld_cfg, "staleness_fail_seconds", 300.0)
        ) if _vld_cfg else 300.0
        from src.core.coin_package_validator import (
            VERDICT_FAIL,
            VERDICT_WARN,
            validate_package,
        )
        for coin, score, _breakdown, record, forced in final:
            try:
                pkg = self._build_package(
                    coin, score, record, forced,
                    fg_value=fg_value,
                    position=position_lookup.get(coin) if forced else None,
                )
                # Phase 5 (output-quality): validate before emit.
                vr = validate_package(
                    pkg,
                    fail_below=_fail_below,
                    warn_below=_warn_below,
                    staleness_fail_seconds=_staleness,
                )
                # Per-package log — operators can grep PACKAGE_VALIDATE
                # to see verdict + completeness + missing fields per coin.
                log.info(
                    f"PACKAGE_VALIDATE | cycle_id={cycle_id} sym={coin} "
                    f"completeness={vr.completeness:.2f} verdict={vr.verdict} "
                    f"missing={vr.missing_fields} stale={vr.stale_fields} | {ctx()}"
                )
                if vr.verdict == VERDICT_FAIL:
                    _vld_fail += 1
                    log.warning(
                        f"PACKAGE_QUARANTINED | cycle_id={cycle_id} sym={coin} "
                        f"completeness={vr.completeness:.2f} "
                        f"missing={vr.missing_fields} stale={vr.stale_fields} | {ctx()}"
                    )
                    continue  # quarantine — do NOT insert into packages
                if vr.verdict == VERDICT_WARN:
                    _vld_warn += 1
                else:
                    _vld_ok += 1
                # Definitive-fix Phase 6: persist the validator's
                # completeness score on the package so downstream gates
                # (brain cold-start protection) read it directly instead
                # of re-validating.
                pkg.completeness = float(vr.completeness)
                # Issue #12 fix: carry the validator's provenance onto the
                # package so the brain prompt can surface it (these fields were
                # computed then discarded before). Also log the build-time
                # source-failure blockers, which previously went unrecorded.
                pkg.missing_fields = list(vr.missing_fields)
                pkg.stale_fields = list(vr.stale_fields)
                if pkg.blockers_observed:
                    log.info(
                        f"PACKAGE_BLOCKERS | cycle_id={cycle_id} sym={coin} "
                        f"blockers={pkg.blockers_observed} "
                        f"completeness={vr.completeness:.2f} | {ctx()}"
                    )
                packages[coin] = pkg
            except Exception as e:
                log.warning(
                    f"SCANNER_PACKAGE_BUILD_FAIL | sym={coin} "
                    f"err='{str(e)[:100]}' | {ctx()}"
                )
        lm = self.services.get("layer_manager")
        if lm is not None:
            lm._coin_packages = packages
            # Phase 6 (output-quality): record packages-cache write so the
            # next stage (Brain CALL_A) can measure scanner→brain latency.
            try:
                from src.core.cache_freshness import record_write
                record_write("packages")
                # Also record per-symbol so /health can show per-coin
                # freshness if needed.
                for _sym in packages:
                    record_write("packages", _sym)
            except Exception:  # pragma: no cover — defensive
                pass
        total_bytes = sum(p.size_bytes() for p in packages.values())
        log.info(
            f"SCANNER_PACKAGE_BUILD_DONE | cycle_id={cycle_id} "
            f"packages={len(packages)} total_size_bytes={total_bytes} "
            f"elapsed_ms={(time.monotonic() - t_pkg) * 1000:.0f} | {ctx()}"
        )
        # Phase 5 (output-quality): per-cycle validation rollup.
        log.info(
            f"PACKAGE_VALIDATE_SUMMARY | cycle_id={cycle_id} "
            f"packages_built={_vld_ok + _vld_warn + _vld_fail} "
            f"ok={_vld_ok} warn={_vld_warn} fail_quarantined={_vld_fail} | {ctx()}"
        )

        # Issue E11 (2026-05-27): per-cycle source-failure heat-map (exclusion
        # path mirror of the briefing aggregate), so the heat-map is available
        # in either scanner mode. Aggregates blocker labels across the built
        # packages this cycle. Observability-only.
        from collections import Counter as _Counter
        _blocker_heat: _Counter = _Counter()
        for _pkg in packages.values():
            for _b in (_pkg.blockers_observed or []):
                _blocker_heat[_b] += 1
        if _blocker_heat:
            _by_label = ", ".join(f"{k}={v}" for k, v in _blocker_heat.most_common())
            log.info(
                f"PACKAGE_BLOCKER_HEATMAP | cycle_id={cycle_id} "
                f"packages={len(packages)} blockers_total={sum(_blocker_heat.values())} "
                f"by_label=[{_by_label}] | {ctx()}"
            )

        # Phase 6 (output-quality): cycle-level freshness rollup. Reads
        # the per-cache write timestamps recorded by upstream workers and
        # emits one structured event per cycle. Computes p50/p95 across
        # symbols for each handoff. Empty handoffs (no upstream writes
        # yet) emit `unknown` so the operator sees the gap.
        try:
            from src.core.cache_freshness import get_snapshot
            _snap = get_snapshot()
            _now_unix = time.time()

            def _ages_for_cache(cache_name: str) -> list[float]:
                return [
                    (_now_unix - ts) * 1000.0
                    for (cn, _key), ts in _snap.items()
                    if cn == cache_name
                ]

            def _pct_or_unk(ages: list[float], p: float) -> str:
                if not ages:
                    return "unknown"
                _s = sorted(ages)
                return f"{int(_s[max(0, int(p * (len(_s) - 1)))]):d}"

            klines_ages = _ages_for_cache("klines")
            xray_ages = _ages_for_cache("xray")
            packages_ages = _ages_for_cache("packages")
            log.info(
                f"CYCLE_FRESHNESS | cycle_id={cycle_id} "
                f"klines_age_p50_ms={_pct_or_unk(klines_ages, 0.50)} "
                f"klines_age_p95_ms={_pct_or_unk(klines_ages, 0.95)} "
                f"xray_age_p50_ms={_pct_or_unk(xray_ages, 0.50)} "
                f"xray_age_p95_ms={_pct_or_unk(xray_ages, 0.95)} "
                f"packages_age_p50_ms={_pct_or_unk(packages_ages, 0.50)} "
                f"packages_age_p95_ms={_pct_or_unk(packages_ages, 0.95)} "
                f"klines_keys={len(klines_ages)} xray_keys={len(xray_ages)} "
                f"packages_keys={len(packages_ages)} | {ctx()}"
            )
        except Exception as _e:  # pragma: no cover — advisory log
            log.debug(
                f"CYCLE_FRESHNESS_FAIL | cycle_id={cycle_id} "
                f"err='{str(_e)[:80]}' | {ctx()}"
            )

        # Persist to the active_universe table — schema unchanged.
        # Phase 8 (post-Layer-1 fix). The Phase-5 rewrite intentionally
        # wrote 0.0 placeholders for the four auxiliary columns because
        # the scanner no longer makes its own market-data calls. But the
        # data IS already on hand — _build_package above populates each
        # CoinPackage with funding_rate (altdata cache), change_24h_pct
        # and volume_24h (market ticker cache). Reading those values
        # back out of ``packages`` here adds zero extra network calls
        # while making the operator-visible Telegram /status surface
        # show real numbers instead of misleading zeros.
        #
        # spread_pct still writes 0.0 — no scanner-side source today.
        # (Cleanest follow-up would be to surface ticker bid/ask in
        # MarketScanner; out of scope for this issue.)
        #
        # Two operations:
        #   1. DELETE FROM active_universe (one lock acquisition)
        #   2. executemany INSERT (one lock acquisition for the batch,
        #      not N individual locks — important under D-3 contention).
        def _enrich_for(coin: str) -> tuple[float, float, float, float]:
            """Return (volume_24h, change_24h_pct, funding_rate, spread_pct).

            All four come from the freshly-built CoinPackage when
            available; fall through to 0.0 (matching the legacy default)
            when the coin has no package — e.g. forced-include BTC/ETH
            slipping past the package builder for any reason.
            """
            pkg = packages.get(coin)
            if pkg is None:
                return (0.0, 0.0, 0.0, 0.0)
            try:
                vol = float(getattr(pkg.price_data, "volume_24h_usd", 0.0) or 0.0)
                chg = float(getattr(pkg.price_data, "change_24h_pct", 0.0) or 0.0)
                funding = float(getattr(pkg.alt_data, "funding_rate", 0.0) or 0.0)
            except Exception:
                vol, chg, funding = 0.0, 0.0, 0.0
            return (vol, chg, funding, 0.0)

        try:
            await self.db.execute("DELETE FROM active_universe")
            insert_rows = []
            for coin, score, _, _, _ in final:
                vol, chg, funding, spread = _enrich_for(coin)
                insert_rows.append((
                    coin,
                    round(float(score), 4),
                    vol,
                    chg,
                    funding,
                    spread,
                    MarketScanner.get_coin_tier(coin),
                ))
            if insert_rows:
                await self.db.executemany(
                    "INSERT OR REPLACE INTO active_universe "
                    "(symbol, opportunity_score, volume_24h, change_24h_pct, "
                    "funding_rate, spread_pct, coin_tier) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    insert_rows,
                )
        except Exception as e:
            log.warning(
                f"SCANNER_DB_WRITE_FAIL | err={str(e)[:100]} | {ctx()}"
            )

        # Update MarketScanner's in-memory list so Stage 2's
        # ``await scanner.get_active_universe()`` returns the new
        # selection. BTC/ETH are already in ``new_symbols`` (appended
        # before the DB write so the table and the in-memory list
        # stay consistent).
        self.scanner.set_active_universe(new_symbols)

        # Notify any registered subscribers via the public accessor. Phase 7
        # removed all worker subscribers, so this list is normally empty.
        # The loop is preserved for forward-compat with any future non-worker
        # consumers that might subscribe to universe changes.
        empty_set: set[str] = set()
        for cb in self.scanner.get_subscribers_snapshot():
            try:
                await cb(new_symbols, set(new_symbols), empty_set)
            except Exception as cb_e:
                log.debug(
                    f"SCANNER_SUBSCRIBER_FAIL | err={str(cb_e)[:80]} | {ctx()}"
                )

        # Per-coin INFO breakdown for full traceability. Definitive-fix
        # Phase 4 (2026-04-28): promoted from DEBUG to INFO so the
        # selected slate is visible at the default log level (operators
        # complained the per-coin selection trace was hidden behind
        # DEBUG verbosity). Includes the new ``rr`` composite component.
        for rank, (coin, score, breakdown, _record, _forced) in enumerate(final, start=1):
            log.info(
                f"SCANNER_SELECTED | rank={rank} coin={coin} score={score:.4f} "
                f"forced={_forced} "
                f"src=structure:{breakdown['structure']},"
                f"strategy:{breakdown['strategy']},"
                f"signal:{breakdown['signal']},"
                f"regime:{breakdown['regime']},"
                f"funding:{breakdown['funding']},"
                f"rr:{breakdown.get('rr', 0.0)} "
                # XRAY counter-setup Phase 5b — surface struct_raw vs
                # struct_norm so operators can see the confidence
                # multiplier's impact per coin (struct_norm = struct_raw
                # × structure_conf with floor 0.5).
                f"struct_raw:{breakdown.get('structure_raw', 0.0)} "
                f"struct_conf:{breakdown.get('structure_conf', 1.0)} | {ctx()}"
            )

        el_ms = (time.monotonic() - t0) * 1000
        scores_only = [s for _, s, _, _, _ in final]
        mean_score = (
            sum(scores_only) / len(scores_only) if scores_only else 0.0
        )

        # Phase 5 — selection summary at INFO. Distinct from
        # SCANNER_TICK_SUMMARY (kept for backward-compat) so the
        # qualified/selected/forced counts are easy to grep.
        log.info(
            f"SCANNER_SELECT | cycle_id={cycle_id} "
            f"qualified={qualified_count} selected={len(final)} "
            f"forced={forced_in_count} watch_list={len(watch_list)} | {ctx()}"
        )

        if final:
            top_str = f"top={final[0][0]}({final[0][1]:.3f})"
        else:
            top_str = "top=-"
        log.info(
            f"SCANNER_TICK_SUMMARY | watch_list={len(watch_list)} "
            f"protected={len(protected)} scored={len(qualified_records)} "
            f"selected={len(final)} top_n={n_max} forced_in={forced_in_count} "
            f"mean_score={mean_score:.3f} {top_str} "
            f"el={el_ms:.0f}ms drift_ms={self._last_drift_ms:.0f} | {ctx()}"
        )

        # End the cycle in the tracker (Phase 1 instrumentation).
        if ct and cycle_id and hasattr(ct, "end_cycle"):
            try:
                if hasattr(ct, "record_qualified"):
                    ct.record_qualified(
                        cycle_id,
                        qualified=qualified_count,
                        selected=len(final),
                        packages=len(packages),
                    )
                ct.end_cycle("layer1d", cycle_id)
            except Exception:
                pass
