"""X-RAY Structure Engine — orchestrates all structural analysis.

Takes OHLCV candles, runs S/R detection, market structure detection,
structural SL/TP placement, FVG detection, order block identification,
liquidity zone mapping, and sweep detection. Returns a StructuralAnalysis
dataclass ready for caching and downstream consumption.
"""

import time

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    LiquiditySweep,
    LiquidityZone,
    MarketStructureResult,
    NearestFVGResult,
    NearestOBResult,
    OrderBlock,
    SetupType,
    StructuralAnalysis,
    StructuralPlacement,
)
from src.config.settings import SetupTypesSettings, StructureSettings
from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


def _fmt_price(p: float | None) -> str:
    """Format a price with dynamic decimal precision for log display."""
    if p is None:
        return "n/a"
    if p >= 100:
        return f"${p:.0f}"
    elif p >= 1:
        return f"${p:.2f}"
    elif p >= 0.01:
        return f"${p:.4f}"
    else:
        return f"${p:.8f}"


def _compute_range_position(
    current_price: float, nearest_support, nearest_resistance,
    swing_lows=None, swing_highs=None,
) -> tuple[float, str, float]:
    """Range position with pre-clamp truth (Element 3, 2026-06-11;
    break detection corrected by the real-pipeline cross-check the same
    day).

    Returns ``(position_in_range, range_breakout, range_overshoot_pct)``.

    ``position_in_range`` stays CLAMPED to [0, 1] and byte-identical to
    the legacy formula for every bounds-assuming consumer (setup score,
    interestingness extremity, breakout classifier, SL/TP placement,
    fade labels).

    THE LOAD-BEARING FACT, proven by driving real candles through the
    real engine: SupportResistanceEngine.calculate FILTERS supports to
    strictly BELOW the current price and resistances to strictly ABOVE
    it. Therefore on live data the two-level branch can never see an
    out-of-range price, and a genuine breakdown (price under every
    detected swing-low cluster) arrives here with an EMPTY supports
    list — the clamp then pins the position at 0.00 cycle after cycle
    (June-11 DYDX: 32 appearances pinned at 0.00 across a 2.7 percent
    price band) while the broken boundary itself was discarded by the
    filter. The original marker logic keyed on raw out-of-range values
    and so could never fire on real data.

    The truth therefore comes from the UNFILTERED swing structure that
    the same SR call already returns:

    - supports empty + swing lows known: the price sits below every
      detected swing-low cluster; the range low it broke is the LOWEST
      detected swing low. Price strictly below it reads
      ``range_breakout="below"`` with the overshoot as a percent of
      that broken low.
    - resistances empty + swing highs known: mirror — price strictly
      above the HIGHEST detected swing high reads ``"above"``.
    - both levels present: in range by construction (the filter
      guarantees support < price < resistance); a defensive raw
      out-of-range check is kept for non-engine callers.

    ``range_overshoot_pct`` is the unsigned magnitude of the break as a
    percent of the BROKEN BOUNDARY'S price — price-denominated like
    ATR%/recSL so the brain can weigh it against the vol-stop floor.

    Pure function: ``nearest_support`` / ``nearest_resistance`` need
    only a ``.price`` attribute (or be None); ``swing_lows`` /
    ``swing_highs`` are the SR engine's ``(index, price)`` tuple lists
    (or None).
    """
    position_in_range = 0.5
    range_breakout = ""
    range_overshoot_pct = 0.0
    if nearest_support and nearest_resistance:
        rng = nearest_resistance.price - nearest_support.price
        if rng > 0:
            _raw_pos = (current_price - nearest_support.price) / rng
            position_in_range = max(0.0, min(1.0, _raw_pos))
            # Defensive only: unreachable through the real engine (the
            # SR filter guarantees support < price < resistance), kept
            # so a non-engine caller with unfiltered levels still gets
            # the truthful read.
            if _raw_pos < 0.0 and nearest_support.price > 0:
                range_breakout = "below"
                range_overshoot_pct = (
                    (nearest_support.price - current_price)
                    / nearest_support.price * 100.0
                )
            elif _raw_pos > 1.0 and nearest_resistance.price > 0:
                range_breakout = "above"
                range_overshoot_pct = (
                    (current_price - nearest_resistance.price)
                    / nearest_resistance.price * 100.0
                )
    elif nearest_support:
        # Only support found (no resistance above price): legacy
        # synthetic 5% range above the real level, clamp unchanged.
        _syn_range = nearest_support.price * 0.05
        if _syn_range > 0:
            _dist = current_price - nearest_support.price
            position_in_range = max(0.0, min(1.0, _dist / _syn_range))
    elif nearest_resistance:
        # Only resistance found (no support below price): mirror.
        _syn_range = nearest_resistance.price * 0.05
        if _syn_range > 0:
            _dist = nearest_resistance.price - current_price
            position_in_range = max(
                0.0, min(1.0, 1.0 - _dist / _syn_range),
            )
    # Break detection from the unfiltered swing structure — the only
    # place the broken boundary survives the SR filter.
    if not range_breakout and not nearest_support and swing_lows:
        try:
            _lowest_low = min(
                float(p) for _, p in swing_lows if float(p) > 0
            )
        except ValueError:
            _lowest_low = 0.0
        if _lowest_low > 0 and 0 < current_price < _lowest_low:
            range_breakout = "below"
            range_overshoot_pct = (
                (_lowest_low - current_price) / _lowest_low * 100.0
            )
    if not range_breakout and not nearest_resistance and swing_highs:
        try:
            _highest_high = max(
                float(p) for _, p in swing_highs if float(p) > 0
            )
        except ValueError:
            _highest_high = 0.0
        if _highest_high > 0 and current_price > _highest_high:
            range_breakout = "above"
            range_overshoot_pct = (
                (current_price - _highest_high) / _highest_high * 100.0
            )
    return position_in_range, range_breakout, range_overshoot_pct


class StructureEngine:
    """Orchestrator for X-RAY structural analysis (Phases 1-7).

    Delegates to sub-engines for each phase, assembles the final
    StructuralAnalysis output. Each phase is independently try/excepted
    so a failure in one doesn't block others.

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

        # Phase 1-3 sub-engines
        self._sr_engine = None
        self._ms_engine = None
        self._sl_engine = None

        # Phase 4-7 sub-engines (Smart Money Concepts)
        self._fvg_engine = None
        self._ob_engine = None
        self._liq_engine = None

        # Phase 8-10 sub-engines (Confluence)
        self._vp_engine = None
        self._fib_engine = None
        self._mtf_engine = None

        # Phase 11-12 sub-engines (Intelligence)
        self._session_timer = None

        log.info("XRAY_INIT | engine=structure_engine")
        # Issue 1 of 2026-05-19 direction-bias fix Phase C — boot
        # sentinel for the new min-edge floor + symmetric min_touches
        # resistance filter. Mirrors STRAT_REGIME_INSTR_REFRAMED
        # (Phase A) / STATE_LABELLER_REGIME_HAIRCUT_INIT (Phase B). Lets
        # log-tail monitoring verify the active config without reading
        # config.toml.
        try:
            log.info(
                f"XRAY_FLIP_CONFIG | "
                f"tp_min_distance_pct={self._settings.tp_min_distance_pct:.2f} "
                f"min_touches_support={self._settings.min_touches} "
                f"min_touches_resistance={self._settings.min_touches_resistance} "
                f"min_touches_symmetric="
                f"{self._settings.min_touches == self._settings.min_touches_resistance}"
            )
        except Exception as _e:
            log.debug(f"XRAY_FLIP_CONFIG_FAIL | err='{str(_e)[:80]}'")
        # Issue 3 (CALL_A exploit/fetch, 2026-06-05) — directional-RR setup
        # scoring boot sentinel. Confirms the scorer grades the chosen
        # direction's RR (not rr_best) and the loaded range-position penalty.
        try:
            log.info(
                f"XRAY_DIRECTIONAL_SCORE_CONFIG | directional_rr=on "
                f"range_floor_threshold="
                f"{getattr(self._settings, 'range_floor_threshold', 0.05):.3f} "
                f"range_no_room_penalty="
                f"{getattr(self._settings, 'range_no_room_penalty', 25)} "
                f"| spent-side shorts/longs now score on the traded side's RR"
            )
        except Exception as _e:
            log.debug(f"XRAY_DIRECTIONAL_SCORE_CONFIG_FAIL | err='{str(_e)[:80]}'")

        # Issue 3 (structure confluence, 2026-06-06) — graded SMC confluence boot
        # sentinel. Confirms the loaded per-component maxima and windows that now
        # spread smc_confluence per coin instead of pinning ~81% of coins at 70.
        try:
            log.info(
                f"XRAY_SMC_GRADED_CONFIG | graded=on "
                f"w_fvg={int(getattr(self._settings, 'smc_weight_fvg', 25.0))} "
                f"w_ob={int(getattr(self._settings, 'smc_weight_ob', 30.0))} "
                f"w_liq={int(getattr(self._settings, 'smc_weight_liq', 15.0))} "
                f"w_sweep={int(getattr(self._settings, 'smc_weight_sweep', 30.0))} "
                f"fvg_prox={getattr(self._settings, 'smc_fvg_proximity_pct', 2.0):.1f} "
                f"ob_prox={getattr(self._settings, 'smc_ob_proximity_pct', 3.0):.1f} "
                f"sweep_recency={getattr(self._settings, 'smc_sweep_recency_candles', 20)} "
                f"| smc_confluence now scales by per-coin zone quality"
            )
        except Exception as _e:
            log.debug(f"XRAY_SMC_GRADED_CONFIG_FAIL | err='{str(_e)[:80]}'")

        # Issue 2 (X-RAY de-saturation, 2026-06-06) — setup_score headroom boot
        # sentinel. Confirms the modifier scale (<1.0 spreads the score off the
        # ceiling) and the grade thresholds that are now loaded.
        try:
            log.info(
                f"XRAY_SETUP_SCORE_CONFIG | "
                f"modifier_scale={getattr(self._settings, 'setup_score_modifier_scale', 1.0):.2f} "
                f"grade_thresholds="
                f"A+>={getattr(self._settings, 'setup_grade_a_plus_min', 80)}/"
                f"A>={getattr(self._settings, 'setup_grade_a_min', 65)}/"
                f"B>={getattr(self._settings, 'setup_grade_b_min', 50)}/"
                f"C>={getattr(self._settings, 'setup_grade_c_min', 35)} "
                f"| scale<1.0 spreads setup_score off the 100 ceiling"
            )
        except Exception as _e:
            log.debug(f"XRAY_SETUP_SCORE_CONFIG_FAIL | err='{str(_e)[:80]}'")

    def _wire_sub_engines(self) -> None:
        """Lazily import and wire sub-engines on first use.

        This avoids circular imports and allows stubs to be replaced
        with real implementations incrementally.
        """
        # Phase 1: Support & Resistance
        if self._sr_engine is None:
            try:
                from src.analysis.structure.support_resistance import SupportResistanceEngine
                self._sr_engine = SupportResistanceEngine(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | sr_engine unavailable: {e}")

        # Phase 2: Market Structure
        if self._ms_engine is None:
            try:
                from src.analysis.structure.market_structure import MarketStructureDetector
                self._ms_engine = MarketStructureDetector(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | ms_engine unavailable: {e}")

        # Phase 3: Structural SL/TP
        if self._sl_engine is None:
            try:
                from src.analysis.structure.structural_levels import StructuralLevelCalculator
                self._sl_engine = StructuralLevelCalculator(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | sl_engine unavailable: {e}")

        # Phase 4: Fair Value Gaps
        if self._fvg_engine is None:
            try:
                from src.analysis.structure.fair_value_gap import FairValueGapDetector
                self._fvg_engine = FairValueGapDetector(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | fvg_engine unavailable: {e}")

        # Phase 5: Order Blocks
        if self._ob_engine is None:
            try:
                from src.analysis.structure.order_blocks import OrderBlockDetector
                self._ob_engine = OrderBlockDetector(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | ob_engine unavailable: {e}")

        # Phase 6-7: Liquidity Zones + Sweeps
        if self._liq_engine is None:
            try:
                from src.analysis.structure.liquidity import LiquidityMapper
                self._liq_engine = LiquidityMapper(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | liq_engine unavailable: {e}")

        # Phase 8: Volume Profile
        if self._vp_engine is None:
            try:
                from src.analysis.structure.volume_profile import VolumeProfileCalculator
                self._vp_engine = VolumeProfileCalculator(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | vp_engine unavailable: {e}")

        # Phase 9: Fibonacci
        if self._fib_engine is None:
            try:
                from src.analysis.structure.fibonacci import FibonacciCalculator
                self._fib_engine = FibonacciCalculator(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | fib_engine unavailable: {e}")

        # Phase 10: MTF Confluence
        if self._mtf_engine is None:
            try:
                from src.analysis.structure.mtf_confluence import MTFConfluenceScorer
                self._mtf_engine = MTFConfluenceScorer(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | mtf_engine unavailable: {e}")

        # Phase 12: Session Timing
        if self._session_timer is None:
            try:
                from src.analysis.structure.session_timing import SessionTimer
                self._session_timer = SessionTimer(self._settings)
            except Exception as e:
                log.debug(f"XRAY_WIRE | session_timer unavailable: {e}")

    def analyze_direction_only(
        self, symbol: str, candles: list, timeframe: str = "",
    ):
        """Issue #5: a CHEAP higher-timeframe structural read for MTF agreement.

        Runs only Phase 1 (S/R) + Phase 2 (market structure) — NOT the full
        10-phase analyze (no FVG/OB/liquidity/VP/Fib), since cross-TF agreement
        only needs the structure word, suggested direction, last-BOS direction
        and nearest levels. Returns a TFStructureView; has_data=False when the
        candle history is too thin (mirrors analyze's min_candles guard) or on
        any error, so the scorer degrades gracefully to H1-only for new/thin
        coins and never crashes the tick.
        """
        from src.analysis.structure.models.structure_types import (
            MarketStructureResult,
            TFStructureView,
        )
        if not candles or len(candles) < self._settings.min_candles:
            return TFStructureView(timeframe=timeframe, has_data=False)
        try:
            self._wire_sub_engines()
            highs = np.array([c.high for c in candles], dtype=np.float64)
            lows = np.array([c.low for c in candles], dtype=np.float64)
            closes = np.array([c.close for c in candles], dtype=np.float64)
            current_price = float(closes[-1])
            supports: list = []
            resistances: list = []
            swing_data = None
            if self._sr_engine:
                supports, resistances, swing_data = self._sr_engine.calculate(
                    highs, lows, closes, current_price,
                )
            market_structure = MarketStructureResult()
            if self._ms_engine:
                market_structure = self._ms_engine.detect(
                    highs, lows, closes, swing_data=swing_data,
                )
            struct = market_structure.structure
            direction = (
                "long" if struct == "uptrend"
                else "short" if struct == "downtrend"
                else ""
            )
            bos_dir = ""
            if market_structure.last_bos:
                bos_dir = getattr(market_structure.last_bos, "direction", "") or ""
            return TFStructureView(
                timeframe=timeframe,
                structure=struct,
                direction=direction,
                last_bos_direction=bos_dir,
                nearest_support=supports[0].price if supports else 0.0,
                nearest_resistance=resistances[0].price if resistances else 0.0,
                current_price=current_price,
                has_data=True,
            )
        except Exception as e:
            log.debug(f"XRAY_HTF_VIEW_FAIL | sym={symbol} tf={timeframe} err={str(e)[:80]}")
            return TFStructureView(timeframe=timeframe, has_data=False)

    def analyze(
        self,
        symbol: str,
        current_price: float,
        candles: list,
        session_context=None,
        higher_tf_views: dict | None = None,
    ) -> StructuralAnalysis | None:
        """Run full structural analysis pipeline for one symbol.

        Phases 1-3: S/R, Market Structure, Structural SL/TP
        Phases 4-7: FVG, Order Blocks, Liquidity Zones, Sweeps

        Args:
            symbol: Trading pair (e.g., "BTCUSDT").
            current_price: Current market price.
            candles: List of OHLCV dataclass instances.

        Returns:
            StructuralAnalysis or None if insufficient data.
        """
        if len(candles) < self._settings.min_candles:
            log.debug(
                f"XRAY_SKIP | sym={symbol} reason=insufficient_candles "
                f"count={len(candles)} min={self._settings.min_candles}"
            )
            return None

        t0 = time.monotonic()
        self._wire_sub_engines()

        # Extract numpy arrays from OHLCV candles
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        closes = np.array([c.close for c in candles], dtype=np.float64)
        opens = np.array([c.open for c in candles], dtype=np.float64)
        phases_ok = 0

        # XRAY counter-setup Phase 2 — H1 NATR captured up-front so
        # _find_nearest_fvg/ob can size its distance window by per-coin
        # volatility instead of a fixed 2%/3%. Computed from the same
        # candle array we just unpacked — no dependency on the
        # volatility_profile worker (which is a separate cache with its
        # own cold-start path). The 14-bar lookback matches conventional
        # ATR sizing on H1.
        atr_pct_h1 = self._compute_h1_natr_pct(highs, lows, closes, lookback=14)

        # ====== PHASE 1: Support & Resistance ======
        support_levels = []
        resistance_levels = []
        swing_data = None
        if self._sr_engine:
            try:
                support_levels, resistance_levels, swing_data = (
                    self._sr_engine.calculate(highs, lows, closes, current_price)
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE1_FAIL | sym={symbol} err={str(e)[:100]}")

        # Nearest support/resistance
        nearest_support = None
        nearest_resistance = None
        if support_levels:
            nearest_support = support_levels[0]  # sorted by proximity
        if resistance_levels:
            nearest_resistance = resistance_levels[0]

        # Position in range — clamped value plus pre-clamp truth
        # (Element 3, 2026-06-11; see _compute_range_position). The
        # swing structure rides along because the SR filter strips the
        # broken boundary from the level lists on a genuine break.
        position_in_range, range_breakout, range_overshoot_pct = (
            _compute_range_position(
                current_price, nearest_support, nearest_resistance,
                swing_lows=(swing_data or {}).get("swing_lows"),
                swing_highs=(swing_data or {}).get("swing_highs"),
            )
        )

        # ====== PHASE 2: Market Structure ======
        market_structure = MarketStructureResult()
        if self._ms_engine:
            try:
                market_structure = self._ms_engine.detect(
                    highs, lows, closes, swing_data=swing_data
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE2_FAIL | sym={symbol} err={str(e)[:100]}")

        # Determine suggested direction from market structure
        suggested_direction = ""
        if market_structure.structure == "uptrend":
            suggested_direction = "long"
        elif market_structure.structure == "downtrend":
            suggested_direction = "short"

        # ====== PHASE 3: Structural SL/TP Placement ======
        # ALWAYS compute BOTH directions — the best R:R may be opposite to structure
        structural_placement = None
        if self._sl_engine and (support_levels or resistance_levels):
            try:
                long_pl = self._sl_engine.calculate(
                    current_price=current_price,
                    direction="long",
                    support_levels=support_levels,
                    resistance_levels=resistance_levels,
                    market_structure=market_structure,
                    position_in_range=position_in_range,
                    atr_pct_h1=atr_pct_h1,  # Fix2 — with-trend continuation TP
                )
                short_pl = self._sl_engine.calculate(
                    current_price=current_price,
                    direction="short",
                    support_levels=support_levels,
                    resistance_levels=resistance_levels,
                    market_structure=market_structure,
                    position_in_range=position_in_range,
                    atr_pct_h1=atr_pct_h1,  # Fix2 — with-trend continuation TP
                )
                long_rr = long_pl.rr_ratio if long_pl else 0.0
                short_rr = short_pl.rr_ratio if short_pl else 0.0
                rr_best = max(long_rr, short_rr)
                rr_best_direction = "long" if long_rr >= short_rr else "short"

                # RR/direction-conflict fix Phase 2 (2026-05-31, flag-gated,
                # default OFF). In a TREND, suggested_direction is the trend side
                # (uptrend->long, downtrend->short) and is selected below even
                # when the opposite side has far better reward-to-risk — which
                # surfaced setups the brain is then told to skip. When enabled,
                # if the opposite side's RR is materially better AND that side is
                # structurally valid (not clamped), re-point suggested_direction
                # to it. Placed BEFORE the placement selection and BEFORE the
                # FVG/OB nearest-detection + classify_setup, so setup_type,
                # trade_direction, label, and structural_placement all follow the
                # re-pointed direction. Pure direction re-point; level math is
                # unchanged. Ranging markets are untouched (they already pick the
                # better-RR side in the else-branch below).
                if (
                    getattr(self._settings, "rr_aware_direction_enabled", False)
                    and suggested_direction
                    and long_pl is not None
                    and short_pl is not None
                ):
                    _cur_rr = long_rr if suggested_direction == "long" else short_rr
                    _opp_dir = "short" if suggested_direction == "long" else "long"
                    _opp_rr = short_rr if suggested_direction == "long" else long_rr
                    _opp_pl = short_pl if _opp_dir == "short" else long_pl
                    _opp_invalid = bool(
                        getattr(_opp_pl, "is_structurally_invalid", False)
                    )
                    _ratio_min = float(
                        getattr(self._settings, "rr_aware_direction_ratio", 2.0)
                    )
                    if (
                        (not _opp_invalid)
                        and _opp_rr > 0
                        and _opp_rr >= _cur_rr * _ratio_min
                    ):
                        log.info(
                            f"XRAY_RR_REPOINT | sym={symbol} "
                            f"from={suggested_direction}(rr={_cur_rr:.2f}) "
                            f"to={_opp_dir}(rr={_opp_rr:.2f}) "
                            f"ratio_min={_ratio_min:.1f} "
                            f"struct={market_structure.structure}"
                        )
                        suggested_direction = _opp_dir

                if suggested_direction == "long":
                    # Trending up: use long placement for SL/TP
                    structural_placement = long_pl
                elif suggested_direction == "short":
                    # Trending down: use short placement for SL/TP
                    structural_placement = short_pl
                else:
                    # Ranging/unknown: pick the direction with better R:R
                    if long_rr >= short_rr and long_rr > 0:
                        structural_placement = long_pl
                        suggested_direction = "long"
                    elif short_rr > 0:
                        structural_placement = short_pl
                        suggested_direction = "short"
                    else:
                        structural_placement = long_pl
                        log.debug(
                            f"XRAY_NO_DIRECTION | sym={symbol} "
                            f"long_rr={long_rr:.2f} short_rr={short_rr:.2f} "
                            f"struct={market_structure.structure}"
                        )

                # Populate dual-direction R:R on the chosen placement
                if structural_placement:
                    structural_placement.rr_long = round(long_rr, 2)
                    structural_placement.rr_short = round(short_rr, 2)
                    structural_placement.rr_best = round(rr_best, 2)
                    structural_placement.rr_best_direction = rr_best_direction
                    # Backward compat: rr_ratio = best achievable R:R
                    structural_placement.rr_ratio = round(rr_best, 2)
                    structural_placement.rr_quality = self._sl_engine._classify_rr(rr_best)
                    # Store SL/TP prices for both directions (context for Claude/APEX)
                    if long_pl:
                        structural_placement.long_sl_price = long_pl.structural_sl
                        structural_placement.long_tp_price = long_pl.structural_tp
                    if short_pl:
                        structural_placement.short_sl_price = short_pl.structural_sl
                        structural_placement.short_tp_price = short_pl.structural_tp
                    # Gap 2 fix (2026-05-19) — surface bidirectional clamp
                    # flags so the brain prompt can show INVALID_LONG=Y/N
                    # and INVALID_SHORT=Y/N per coin. Both placements
                    # already exist in scope (long_pl + short_pl); this
                    # is pure data marshalling, zero new compute. The
                    # chosen placement's legacy ``is_structurally_invalid``
                    # remains as-is for backward compatibility with the
                    # XRAY_LEVELS debug log + any future single-direction
                    # consumer.
                    structural_placement.is_long_invalid = (
                        bool(long_pl.is_structurally_invalid) if long_pl else False
                    )
                    structural_placement.is_short_invalid = (
                        bool(short_pl.is_structurally_invalid) if short_pl else False
                    )
                    # Gap 1 fix (2026-05-19) — Path B logging-only consumer.
                    # Per spec Anti-pattern 10 + Rule 4: trial data n=2 for
                    # definitive clamp activations is insufficient to ship
                    # a behavioral consumer (sizing reduction / skip). The
                    # operator gains observability without behavior change:
                    # `grep XRAY_CLAMP_DETECTED data/logs/workers.log` shows
                    # every cycle where either direction hit the math-safety
                    # floor. Cross-reference with DL_TRADE outcomes for
                    # future data-driven Path C/D decisions.
                    if (
                        structural_placement.is_long_invalid
                        or structural_placement.is_short_invalid
                    ):
                        log.info(
                            f"XRAY_CLAMP_DETECTED | sym={symbol} "
                            f"long_invalid={structural_placement.is_long_invalid} "
                            f"short_invalid={structural_placement.is_short_invalid} "
                            f"rr_long={structural_placement.rr_long:.2f} "
                            f"rr_short={structural_placement.rr_short:.2f} "
                            f"chosen_dir={structural_placement.direction or 'n/a'}"
                        )

                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE3_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 4: Fair Value Gaps ======
        fvgs: list[FairValueGap] = []
        if self._fvg_engine:
            try:
                fvgs = self._fvg_engine.detect(
                    highs, lows, closes, opens, current_price,
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE4_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 5: Order Blocks ======
        order_blocks: list[OrderBlock] = []
        if self._ob_engine:
            try:
                order_blocks = self._ob_engine.detect(
                    highs, lows, closes, opens, current_price,
                    fvgs=fvgs,
                    market_structure=market_structure,
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE5_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 6: Liquidity Zones ======
        liquidity_zones: list[LiquidityZone] = []
        if self._liq_engine:
            try:
                sh = swing_data.get("swing_highs", []) if swing_data else []
                sl_pts = swing_data.get("swing_lows", []) if swing_data else []
                liquidity_zones = self._liq_engine.detect_zones(
                    highs, lows, closes, current_price,
                    swing_highs=sh,
                    swing_lows=sl_pts,
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE6_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 7: Liquidity Sweeps ======
        recent_sweeps: list[LiquiditySweep] = []
        if self._liq_engine and liquidity_zones:
            try:
                recent_sweeps = self._liq_engine.detect_sweeps(
                    highs, lows, closes, opens, current_price,
                    zones=liquidity_zones,
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE7_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== Compute nearest/active Smart Money signals ======
        # XRAY counter-setup Phase 2 — pass atr_pct_h1 + setup_types config
        # so the finders can size their proximity window by per-coin
        # volatility (max(min_distance_pct, atr_multiplier * atr_pct_h1)).
        # Phase 3 — finders now return NearestFVGResult/NearestOBResult
        # carrying both in-direction and counter-direction zones; Phase 4
        # consumes ``nearest_fvg_counter`` / ``nearest_ob_counter`` to
        # emit *_FVG_OB_COUNTER setups when in-direction is missing.
        _setup_cfg = getattr(self._settings, "setup_types", None)
        _fvg_result = self._find_nearest_fvg(
            fvgs, current_price, suggested_direction, atr_pct_h1, _setup_cfg, symbol=symbol,
        )
        _ob_result = self._find_nearest_ob(
            order_blocks, current_price, suggested_direction, atr_pct_h1, _setup_cfg, symbol=symbol,
        )
        nearest_fvg = _fvg_result.in_direction
        nearest_ob = _ob_result.in_direction
        nearest_fvg_counter = _fvg_result.counter_direction
        nearest_ob_counter = _ob_result.counter_direction
        nearest_unswept = next(
            (z for z in liquidity_zones if not z.swept),
            None,
        )
        active_sweep = recent_sweeps[0] if recent_sweeps else None

        # SMC confluence score + per-component breakdown.
        # Breakdown is propagated to ``StructuralAnalysis.smc_breakdown``
        # so ``classify_setup`` and the forensic log line can reference
        # each component's contribution without re-iterating lists.
        smc_confluence, smc_breakdown = self._compute_smc_confluence(
            fvgs, order_blocks, liquidity_zones, recent_sweeps,
            current_price, suggested_direction, self._settings,
        )

        # ====== PHASE 8: Volume Profile ======
        volume_profile = None
        poc_price = None
        if self._vp_engine:
            try:
                volume_profile = self._vp_engine.calculate(candles, current_price)
                poc_price = (
                    volume_profile.poc
                    if volume_profile and volume_profile.poc > 0
                    else None
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE8_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 9: Fibonacci ======
        fibonacci = None
        fib_key_level = None
        if self._fib_engine and swing_data:
            try:
                sh = swing_data.get("swing_highs", [])
                sl_pts = swing_data.get("swing_lows", [])
                fibonacci = self._fib_engine.calculate(
                    candles, sh, sl_pts,
                    support_levels, resistance_levels,
                    order_blocks, current_price,
                )
                fib_key_level = (
                    fibonacci.key_level
                    if fibonacci and fibonacci.key_level and fibonacci.key_level > 0
                    else None
                )
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE9_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== PHASE 10: MTF Confluence ======
        mtf_confluence = None
        confluence_quality = "none"
        mtf_score = 0
        if self._mtf_engine:
            try:
                mtf_confluence = self._mtf_engine.score(
                    symbol=symbol,
                    current_price=current_price,
                    direction=suggested_direction or "long",
                    market_structure=market_structure,
                    supports=support_levels,
                    resistances=resistance_levels,
                    placement=structural_placement,
                    fvgs=fvgs,
                    order_blocks=order_blocks,
                    smc_confluence=smc_confluence,
                    fibonacci=fibonacci,
                    volume_profile=volume_profile,
                    # Issue #5: forward the higher-TF structure views (H4/D1) so
                    # the scorer can blend cross-timeframe agreement. None in the
                    # legacy/flag-off path -> scorer is byte-identical to today.
                    higher_tf_views=higher_tf_views,
                )
                confluence_quality = mtf_confluence.quality if mtf_confluence else "none"
                mtf_score = mtf_confluence.score if mtf_confluence else 0
                phases_ok += 1
            except Exception as e:
                log.error(f"XRAY_PHASE10_FAIL | sym={symbol} err={str(e)[:100]}")

        # ====== Count total confluence factors ======
        total_confluence_factors = self._count_confluence_factors(
            nearest_support, nearest_resistance, market_structure,
            nearest_fvg, nearest_ob, nearest_unswept, active_sweep,
            volume_profile, fibonacci, mtf_confluence, suggested_direction,
        )

        # ====== Setup Score (Phase 1+2+3 combined) ======
        setup_score, setup_quality = self._compute_setup_score(
            position_in_range=position_in_range,
            market_structure=market_structure,
            structural_placement=structural_placement,
            suggested_direction=suggested_direction,
            smc_confluence=smc_confluence,
            volume_profile=volume_profile,
            fibonacci=fibonacci,
            mtf_confluence=mtf_confluence,
            symbol=symbol,
        )

        elapsed_ms = (time.monotonic() - t0) * 1000

        analysis = StructuralAnalysis(
            symbol=symbol,
            current_price=current_price,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            position_in_range=position_in_range,
            range_breakout=range_breakout,
            range_overshoot_pct=range_overshoot_pct,
            market_structure=market_structure,
            structural_placement=structural_placement,
            setup_score=setup_score,
            setup_quality=setup_quality,
            suggested_direction=suggested_direction,
            # Phase 2: Smart Money Concepts
            fvgs=fvgs,
            order_blocks=order_blocks,
            liquidity_zones=liquidity_zones,
            recent_sweeps=recent_sweeps,
            nearest_fvg=nearest_fvg,
            nearest_ob=nearest_ob,
            nearest_fvg_counter=nearest_fvg_counter,
            nearest_ob_counter=nearest_ob_counter,
            nearest_unswept_liquidity=nearest_unswept,
            active_sweep_signal=active_sweep,
            smc_confluence=smc_confluence,
            smc_breakdown=smc_breakdown,
            # Phase 3: Confluence
            volume_profile=volume_profile,
            poc_price=poc_price,
            fibonacci=fibonacci,
            fib_key_level=fib_key_level,
            mtf_confluence=mtf_confluence,
            mtf_confluence_score=mtf_score,
            confluence_quality=confluence_quality,
            total_confluence_factors=total_confluence_factors,
            # Phase 4: Session Timing
            session_context=session_context,
            # XRAY counter-setup Phase 2 — H1 NATR captured for downstream
            # consumers (Phase 4 classifier, Phase 6 NONE reason enrichment).
            atr_pct_h1=atr_pct_h1,
        )

        # Layer 1 restructure Phase 2 — categorical setup classification.
        # Pure function reading already-computed fields on ``analysis``;
        # populates ``setup_type`` + ``setup_type_confidence`` before the
        # analysis is returned to the worker for caching.
        try:
            stype, sconf = self.classify_setup(analysis)
            analysis.setup_type = stype
            analysis.setup_type_confidence = sconf
        except Exception as _e:
            # Defensive: classification failure must NOT break the
            # existing analysis path. Default fields already set NONE/0.0.
            log.warning(
                f"XRAY_CLASSIFY_FAIL | sym={symbol} err='{str(_e)[:80]}' | {ctx()}"
            )

        # Issue #7 fix (2026-05-27): X-RAY score/confidence coherence gate.
        # setup_score (an additive heuristic) and setup_type_confidence (a strict
        # pattern-matcher) are computed independently, so a coin could score
        # A+/100 yet be classified NONE/0.0 — a contradictory signal the brain
        # saw and APEX could upsize as a structureless A+. Gate the grade by the
        # classifier at the producer (mirrors the R:R / SMC hard caps inside
        # _compute_setup_score): a NONE-classified setup is capped to C, and a
        # matched setup whose confidence is below the floor is capped to B, so a
        # structureless or weak-pattern coin can never present as top grade. This
        # is the producer-level root fix; the APEX-gate hardening (audit E17/E18)
        # is a separate downstream task (though capping the score here also
        # prevents APEX's score>=80 A+ size boost from firing on NONE coins).
        _XRAY_MIN_SETUP_CONFIDENCE = 0.30
        _q0, _s0 = analysis.setup_quality, analysis.setup_score
        if analysis.setup_type == SetupType.NONE:
            if analysis.setup_quality in ("A+", "A", "B"):
                analysis.setup_quality = "C"
                analysis.setup_score = min(analysis.setup_score, 49)
        elif analysis.setup_type_confidence < _XRAY_MIN_SETUP_CONFIDENCE:
            if analysis.setup_quality in ("A+", "A"):
                analysis.setup_quality = "B"
                analysis.setup_score = min(analysis.setup_score, 64)
        if analysis.setup_quality != _q0 or analysis.setup_score != _s0:
            log.info(
                f"XRAY_SCORE_GATED | sym={symbol} "
                f"setup_type={analysis.setup_type.value} "
                f"conf={analysis.setup_type_confidence:.2f} "
                f"score={_s0}->{analysis.setup_score} "
                f"quality={_q0}->{analysis.setup_quality} | {ctx()}"
            )

        _sp_log = structural_placement
        log.info(
            f"XRAY_ANALYZE | sym={symbol} phases={phases_ok}/10 el={elapsed_ms:.0f}ms "
            f"| sup={len(support_levels)} res={len(resistance_levels)} "
            f"struct={market_structure.structure} pos={position_in_range:.2f} "
            f"rr_l={(_sp_log.rr_long if _sp_log else 0):.1f} "
            f"rr_s={(_sp_log.rr_short if _sp_log else 0):.1f} "
            f"rr={(_sp_log.rr_best if _sp_log else 0):.1f}"
            f"({(_sp_log.rr_best_direction if _sp_log else '?')}) "
            f"fvg={len(fvgs)} ob={len(order_blocks)} liq={len(liquidity_zones)} "
            f"sweep={len(recent_sweeps)} smc={smc_confluence} "
            f"poc={_fmt_price(poc_price)} "
            f"fib={_fmt_price(fib_key_level)} "
            f"mtf={mtf_score}/10({confluence_quality}) "
            f"confl={total_confluence_factors} quality={setup_quality}"
        )

        return analysis

    @staticmethod
    def _compute_h1_natr_pct(
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        lookback: int = 14,
    ) -> float:
        """Normalised ATR as percent of current price, on H1 candles.

        Pure function — operates on the same numpy arrays already extracted
        for the structural pipeline so we don't depend on the
        volatility_profile worker cache. Returns 0.0 when there aren't
        enough candles for a stable lookback. Output is the conventional
        14-bar mean true range divided by current price, expressed as a
        percentage (typical range 0.3% - 2.0%).

        XRAY counter-setup Phase 2 introduced this so ``_find_nearest_*``
        can scale its distance window by per-coin volatility rather than
        a fixed 2%/3%.

        Args:
            highs: numpy array of high prices.
            lows: numpy array of low prices.
            closes: numpy array of close prices (length must match highs/lows).
            lookback: number of trailing bars to average TR over (default 14).

        Returns:
            ATR percent (0.0 - ~5.0 in normal markets), 0.0 when
            ``len(closes) < lookback + 1``.
        """
        if len(closes) < lookback + 1 or len(highs) < lookback + 1 or len(lows) < lookback + 1:
            return 0.0
        highs_w = highs[-lookback:]
        lows_w = lows[-lookback:]
        # Aligned previous-close window: closes[i-1] for i in the lookback range.
        closes_prev = closes[-lookback - 1: -1]
        tr = np.maximum.reduce([
            highs_w - lows_w,
            np.abs(highs_w - closes_prev),
            np.abs(lows_w - closes_prev),
        ])
        atr = float(tr.mean())
        last_close = float(closes[-1])
        if last_close <= 0:
            return 0.0
        return (atr / last_close) * 100.0

    @staticmethod
    def _find_nearest_fvg(
        fvgs: list[FairValueGap],
        current_price: float,
        direction: str,
        atr_pct: float,
        cfg: SetupTypesSettings | None,
        *,
        symbol: str = "",
    ) -> NearestFVGResult:
        """Find nearest unfilled FVG in BOTH the suggested and counter directions.

        XRAY counter-setup Phase 3 — contract extended from
        ``Optional[FairValueGap]`` to ``NearestFVGResult`` carrying both
        in-direction and counter-direction zones. Phase 4's classifier
        uses ``counter_direction`` to emit ``*_FVG_OB_COUNTER`` setups
        when in-direction structure is missing but the OPPOSITE direction
        has tradeable structure near price.

        Distance window (from Phase 2) is ATR-scaled with a fixed-percent
        floor:

            window_pct = max(cfg.fvg_min_distance_pct,
                             cfg.fvg_atr_multiplier * atr_pct)

        Selection rule (changed from Phase 2): **closest within window
        wins** in each direction slot. Pre-Phase-3 the function returned
        on the first iterated FVG inside the window — since FVGs arrive
        ordered by ``created_index`` DESC that biased toward the most-
        recent zone. The new closest-match rule matches the semantic
        intent of "nearest" and removes the ordering coupling. In
        practice the live universe rarely has multiple in-direction
        unfilled FVGs within a 2-5% window so the behavioral delta is
        small.

        Args:
            fvgs: full FVG list (any ordering). Filled FVGs are skipped.
            current_price: live mid for the symbol.
            direction: ``"long"`` or ``"short"``. Empty string returns
                NearestFVGResult with both slots None (caller had no
                suggested direction).
            atr_pct: H1 NATR as percent of price; 0.0 falls back to the
                floor.
            cfg: SetupTypesSettings; ``None`` falls back to fixed 2.0%
                window for backward-compat with test fixtures.
            symbol: optional symbol for the XRAY_NEAREST_DETAIL log.

        Returns:
            ``NearestFVGResult`` with ``in_direction`` and
            ``counter_direction`` independently set. Either may be None
            when no zone of that direction exists within the window.
        """
        if cfg is None:
            window_pct = 2.0
        else:
            window_pct = max(
                float(cfg.fvg_min_distance_pct),
                float(cfg.fvg_atr_multiplier) * float(atr_pct or 0.0),
            )
        # Without a direction we can't classify in vs counter — return empty.
        if not direction:
            return NearestFVGResult(suggested_direction="")

        expected_in = "bullish" if direction == "long" else "bearish"
        expected_counter = "bearish" if direction == "long" else "bullish"

        in_zone: FairValueGap | None = None
        counter_zone: FairValueGap | None = None
        in_dist: float | None = None
        counter_dist: float | None = None

        for fvg in fvgs:
            if fvg.filled:
                continue
            if current_price <= 0:
                continue
            dist = abs(fvg.midpoint - current_price) / current_price * 100
            if dist >= window_pct:
                continue
            if fvg.direction == expected_in:
                if in_zone is None or (in_dist is not None and dist < in_dist):
                    in_zone = fvg
                    in_dist = dist
            elif fvg.direction == expected_counter:
                if counter_zone is None or (counter_dist is not None and dist < counter_dist):
                    counter_zone = fvg
                    counter_dist = dist

        # Two log records — one per slot — so operators can see what's
        # available in each direction without parsing both into a single
        # line. DEBUG by default; per-cycle aggregate stays at INFO.
        for slot, zone, dist in (
            ("in_direction", in_zone, in_dist),
            ("counter", counter_zone, counter_dist),
        ):
            log.debug(
                "XRAY_NEAREST_DETAIL | sym={sym} kind=fvg slot={slot} "
                "direction={dir} found={found} distance_pct={dist} "
                "atr_pct={atr:.3f} window_pct={win:.3f} reason={rsn}",
                sym=symbol or "?",
                slot=slot,
                dir=(direction if slot == "in_direction" else (
                    "long" if direction == "short" else "short"
                )),
                found=("true" if zone else "false"),
                dist=(f"{dist:.3f}" if dist is not None else "-"),
                atr=float(atr_pct or 0.0),
                win=window_pct,
                rsn=("found" if zone else "no_match_in_window"),
            )

        return NearestFVGResult(
            in_direction=in_zone,
            counter_direction=counter_zone,
            in_distance_pct=in_dist,
            counter_distance_pct=counter_dist,
            suggested_direction=direction,
        )

    @staticmethod
    def _find_nearest_ob(
        order_blocks: list[OrderBlock],
        current_price: float,
        direction: str,
        atr_pct: float,
        cfg: SetupTypesSettings | None,
        *,
        symbol: str = "",
    ) -> NearestOBResult:
        """Find nearest fresh OB in BOTH the suggested and counter directions.

        XRAY counter-setup Phase 3 — contract mirrors ``_find_nearest_fvg``
        for fresh OBs. See that docstring for the full rationale.

        Distance window:

            window_pct = max(cfg.ob_min_distance_pct,
                             cfg.ob_atr_multiplier * atr_pct)

        Default OB multiplier is 4.0 (vs 3.0 for FVG) because OBs are
        typically further from price than gaps formed during the same
        displacement candle.
        """
        if cfg is None:
            window_pct = 3.0
        else:
            window_pct = max(
                float(cfg.ob_min_distance_pct),
                float(cfg.ob_atr_multiplier) * float(atr_pct or 0.0),
            )
        if not direction:
            return NearestOBResult(suggested_direction="")

        expected_in = "bullish" if direction == "long" else "bearish"
        expected_counter = "bearish" if direction == "long" else "bullish"

        in_zone: OrderBlock | None = None
        counter_zone: OrderBlock | None = None
        in_dist: float | None = None
        counter_dist: float | None = None

        for ob in order_blocks:
            if not ob.fresh:
                continue
            if current_price <= 0:
                continue
            dist = abs(ob.midpoint - current_price) / current_price * 100
            if dist >= window_pct:
                continue
            if ob.direction == expected_in:
                if in_zone is None or (in_dist is not None and dist < in_dist):
                    in_zone = ob
                    in_dist = dist
            elif ob.direction == expected_counter:
                if counter_zone is None or (counter_dist is not None and dist < counter_dist):
                    counter_zone = ob
                    counter_dist = dist

        for slot, zone, dist in (
            ("in_direction", in_zone, in_dist),
            ("counter", counter_zone, counter_dist),
        ):
            log.debug(
                "XRAY_NEAREST_DETAIL | sym={sym} kind=ob slot={slot} "
                "direction={dir} found={found} distance_pct={dist} "
                "atr_pct={atr:.3f} window_pct={win:.3f} reason={rsn}",
                sym=symbol or "?",
                slot=slot,
                dir=(direction if slot == "in_direction" else (
                    "long" if direction == "short" else "short"
                )),
                found=("true" if zone else "false"),
                dist=(f"{dist:.3f}" if dist is not None else "-"),
                atr=float(atr_pct or 0.0),
                win=window_pct,
                rsn=("found" if zone else "no_match_in_window"),
            )

        return NearestOBResult(
            in_direction=in_zone,
            counter_direction=counter_zone,
            in_distance_pct=in_dist,
            counter_distance_pct=counter_dist,
            suggested_direction=direction,
        )

    @staticmethod
    def _compute_smc_confluence(
        fvgs: list[FairValueGap],
        order_blocks: list[OrderBlock],
        liquidity_zones: list[LiquidityZone],
        sweeps: list[LiquiditySweep],
        current_price: float,
        direction: str,
        settings=None,
    ) -> tuple[int, dict[str, int]]:
        """Compute Smart Money Concepts confluence score (0-100) plus per-component breakdown.

        Issue 3 (structure confluence, 2026-06-06) — GRADED. Each component now
        contributes a CONTINUOUS value in ``[0, its weight]`` scaled by that coin's
        own zone quality (proximity to price plus the zone's strength / freshness),
        instead of the legacy flat lump on a binary present/absent test. The flat
        version pinned ~81% of coins at the same 70 (FVG + OB + liquidity present,
        no sweep — the "structure confluence constant"); the graded version spreads
        the score per coin so it differentiates setups and feeds true relative
        quality into both the setup_score SMC bonus and the prompt.

        Component maxima and the proximity / recency windows are config
        (``StructureSettings.smc_*``), read via ``settings`` — passed by the live
        caller as ``self._settings`` and ``None`` (defaults) for the static
        unit-test call form. The strong / moderate / weak ladders are fixed
        semantics, mirroring the rest of structure_engine.

        Components and max contributions (graded within each cap):
            fvg   — Best in-direction unfilled FVG, by proximity + displacement + freshness (0..25)
            ob    — Best fresh in-direction order block, by proximity + strength_score (0..30)
            liq   — Best unswept target-side liquidity zone, by strength + equal-count (0..15)
            sweep — Latest in-direction sweep, by signal tier + reversal + recency (0..30)

        Returns:
            Tuple of ``(score, breakdown)``. ``score`` is the integer sum capped at
            100. ``breakdown`` maps each component to its (rounded) contribution.
        """
        # Config (operator-tunable maxima + windows); defaults match the legacy caps
        # so an absent ``settings`` (static test call) keeps the same ceiling.
        w_fvg = float(getattr(settings, "smc_weight_fvg", 25.0))
        w_ob = float(getattr(settings, "smc_weight_ob", 30.0))
        w_liq = float(getattr(settings, "smc_weight_liq", 15.0))
        w_sweep = float(getattr(settings, "smc_weight_sweep", 30.0))
        thr_fvg = float(getattr(settings, "smc_fvg_proximity_pct", 2.0)) or 2.0
        thr_ob = float(getattr(settings, "smc_ob_proximity_pct", 3.0)) or 3.0
        recency = float(getattr(settings, "smc_sweep_recency_candles", 20)) or 20.0
        # Fixed strong/moderate/weak ladder (matches structure_engine's other uses).
        strength_ladder = {"strong": 1.0, "moderate": 0.6, "weak": 0.3}

        px = max(current_price, 1e-9)
        expected = "bullish" if direction == "long" else "bearish"
        breakdown: dict[str, int] = {"fvg": 0, "ob": 0, "liq": 0, "sweep": 0}

        # FVG — best (highest-quality) in-direction unfilled gap within the window.
        # Quality blends proximity (closer = stronger), displacement strength, and
        # freshness (less filled = stronger).
        best = 0.0
        for fvg in fvgs:
            if fvg.filled or not direction or fvg.direction != expected:
                continue
            dist = abs(fvg.midpoint - current_price) / px * 100.0
            if dist >= thr_fvg:
                continue
            prox = 1.0 - min(dist / thr_fvg, 1.0)
            disp = strength_ladder.get(fvg.displacement_strength, 0.3)
            fresh = 1.0 - max(0.0, min(1.0, float(fvg.fill_percentage)))
            best = max(best, 0.45 * prox + 0.35 * disp + 0.20 * fresh)
        breakdown["fvg"] = int(round(w_fvg * best))

        # OB — best fresh in-direction order block within the window. ``strength_score``
        # (0-100) is the OB's own composite quality and dominates the blend.
        best = 0.0
        for ob in order_blocks:
            if not ob.fresh or not direction or ob.direction != expected:
                continue
            dist = abs(ob.midpoint - current_price) / px * 100.0
            if dist >= thr_ob:
                continue
            prox = 1.0 - min(dist / thr_ob, 1.0)
            strg = max(0.0, min(1.0, float(ob.strength_score) / 100.0))
            best = max(best, 0.45 * prox + 0.55 * strg)
        breakdown["ob"] = int(round(w_ob * best))

        # Liquidity — best unswept zone on the target side, by zone strength (0-5)
        # and how many equal highs/lows form the cluster.
        best = 0.0
        for lz in liquidity_zones:
            if lz.swept:
                continue
            if (direction == "long" and lz.zone_type == "sell_side") or \
               (direction == "short" and lz.zone_type == "buy_side"):
                strg = max(0.0, min(1.0, float(lz.strength) / 5.0))
                eqc = max(0.0, min(1.0, float(lz.equal_count) / 5.0))
                best = max(best, 0.6 * strg + 0.4 * eqc)
        breakdown["liq"] = int(round(w_liq * best))

        # Sweep — latest in-direction sweep, by its signal tier, reversal strength,
        # and recency. Post XRAY phase-1 the signal always carries direction
        # (high_probability_/moderate_/weak_{long,short}), so weak-but-real sweeps
        # still contribute, now scaled instead of a flat lump.
        if sweeps:
            latest = sweeps[0]
            sig_label = latest.signal or ""
            if (direction == "long" and "long" in sig_label) or \
               (direction == "short" and "short" in sig_label):
                if "high_probability" in sig_label:
                    sig = 1.0
                elif "moderate" in sig_label:
                    sig = 0.6
                else:
                    sig = 0.3
                rev = strength_ladder.get(latest.reversal_strength, 0.3)
                rec = 1.0 - min(max(0.0, float(latest.age_candles)) / recency, 1.0)
                breakdown["sweep"] = int(round(w_sweep * (0.5 * sig + 0.3 * rev + 0.2 * rec)))

        score = min(sum(breakdown.values()), 100)
        return score, breakdown

    @staticmethod
    def _count_confluence_factors(
        nearest_support, nearest_resistance, market_structure,
        nearest_fvg, nearest_ob, nearest_unswept, active_sweep,
        volume_profile, fibonacci, mtf_confluence, direction,
    ) -> int:
        """Count independent structural factors that contribute positively."""
        count = 0
        if nearest_support:
            count += 1
        if nearest_resistance:
            count += 1
        if market_structure and market_structure.structure not in ("unknown", "ranging"):
            count += 1
        if nearest_fvg:
            count += 1
        if nearest_ob:
            count += 1
        if nearest_unswept:
            count += 1
        if active_sweep:
            count += 1
        if volume_profile:
            if (direction == "long" and volume_profile.current_vs_poc == "below_poc") or \
               (direction == "short" and volume_profile.current_vs_poc == "above_poc"):
                count += 1
        if fibonacci and fibonacci.confluence_with:
            count += 1
        if mtf_confluence and mtf_confluence.score >= 5:
            count += 1
        return count

    @staticmethod
    def _counter_alignment(
        trade_direction: str,
        struct: str,
        cfg: SetupTypesSettings | None,
    ) -> bool:
        """Check whether a COUNTER setup is acceptable given current structure.

        XRAY counter-setup Phase 4. Counter setups fire when in-direction
        structure is missing but opposite-direction zones exist near
        price. The counter trade direction is always OPPOSITE to
        ``suggested_direction``. The acceptance gate must reject counter
        trades that fight a strong same-side trend.

        Specifically:
        - ``BULLISH_*_COUNTER`` (trade_direction=long, suggested=short):
          accept when structure is downtrend (counter trades against the
          fading trend), ranging (no trend bias), or volatile (chop-mode
          characterization). Reject when structure is uptrend — long-side
          counter on an already-uptrending coin doesn't add information.
        - Mirror for BEARISH counter.

        ``counter_alignment_strict=true`` removes ``volatile`` from the
        accept set, restricting counter setups to opposite-direction-trend
        or ranging structure only.

        Args:
            trade_direction: ``"long"`` or ``"short"`` — the counter
                trade direction (opposite to suggested).
            struct: market_structure.structure value, lowercased.
            cfg: SetupTypesSettings; ``None`` falls back to permissive
                defaults (allows downtrend + ranging + volatile for
                bullish counter and mirror for bearish).

        Returns:
            True when the structure permits a counter trade in the given
            direction, False otherwise.
        """
        strict = getattr(cfg, "counter_alignment_strict", False) if cfg else False
        if trade_direction == "long":
            # Long counter trade: accept when structure is NOT a strong
            # uptrend. The suggested_direction was "short" (which is why
            # we're in the counter branch); structure is therefore
            # something other than uptrend — but defensively re-check.
            if struct == "downtrend":
                return True
            if struct == "ranging":
                return True
            if struct == "volatile" and not strict:
                return True
            return False
        if trade_direction == "short":
            if struct == "uptrend":
                return True
            if struct == "ranging":
                return True
            if struct == "volatile" and not strict:
                return True
            return False
        return False

    def classify_setup(
        self, analysis: StructuralAnalysis,
    ) -> tuple[SetupType, float]:
        """Classify the structural setup categorically (Phase 2 of Layer 1 restructure).

        Pure with respect to the structural fields read off ``analysis``.
        Decision tree (top-down, first match wins). Conservative: returns
        ``(NONE, 0.0)`` whenever no pattern combination meets the
        configured threshold. Confidence is the bounded minimum of the
        contributing scores so a chain of weakly-supporting evidence
        does not falsely produce a high-confidence label.

        SIDE EFFECT (XRAY counter-setup Phase 4): writes
        ``analysis.trade_direction`` reflecting the trade direction
        implied by the chosen branch. For in-direction setups
        ``trade_direction == suggested_direction``. For
        ``BULLISH_FVG_OB_COUNTER`` / ``BEARISH_FVG_OB_COUNTER`` it's the
        OPPOSITE of suggested (the counter-trade payoff). Set to ``""``
        for NONE. The 2-tuple return is unchanged to preserve
        backward-compat with existing test fixtures.

        Thresholds live in ``settings.structure.setup_types`` so they can
        be tuned in Phase 9 without code changes.

        Args:
            analysis: A populated StructuralAnalysis (after the analyze
                pipeline ran for one symbol). Phase 4 expects
                ``nearest_fvg_counter`` and ``nearest_ob_counter`` to be
                populated by ``_find_nearest_*`` (Phase 3 contract);
                missing fields default to None and counter branches
                naturally fail.

        Returns:
            Tuple of ``(SetupType, confidence_0_to_1)``.
        """
        cfg = getattr(self._settings, "setup_types", None)
        # Per-knob defaults (fallback when config absent — keeps the
        # method usable in tests that construct a bare StructureSettings).
        fvg_ob_min = getattr(cfg, "fvg_ob_min_confluence", 0.7) if cfg else 0.7
        require_retest = (
            getattr(cfg, "structural_break_require_retest", True) if cfg else True
        )
        sweep_min_pct = (
            getattr(cfg, "sweep_min_displacement_pct", 0.5) if cfg else 0.5
        )
        breakout_min_bars = (
            getattr(cfg, "range_breakout_min_compression_bars", 20) if cfg else 20
        )
        # Definitive-fix Phase 3 (2026-04-28): the alignment helpers below
        # used to require strict uptrend / downtrend match. With
        # STRAT_REGIME_DIST showing ~46% of the universe in ``ranging``
        # (forensic), strict alignment guarantees zero directional setups
        # for half the watch_list every cycle. Allow ``ranging`` only
        # when higher-timeframe confluence (mtf_score_01) clears
        # ``ranging_market_mtf_threshold`` so we don't accept a directional
        # bet without higher-TF support. Volatile and downtrend-vs-long
        # (and mirror) stay rejected.
        ranging_market_mtf_threshold = (
            getattr(cfg, "ranging_market_mtf_threshold", 0.55) if cfg else 0.55
        )
        # Issue 6 (2026-06-08) — FVG-OB-in-ranging confidence discount. The
        # FVG-OB setup in a ranging regime at low confidence was the single
        # largest loss driver; down-weight its confidence when ranging so it
        # scores lower (selected less by the rank-only funnel) and sizes smaller.
        # A multiplier (a down-weighting), not a gate. Clamp defensively to
        # (0, 1] since SetupTypesSettings has no __post_init__ validation; 1.0
        # disables the discount.
        fvg_ob_ranging_disc = (
            getattr(cfg, "fvg_ob_ranging_confidence_discount", 1.0) if cfg else 1.0
        )
        if not (0.0 < fvg_ob_ranging_disc <= 1.0):
            fvg_ob_ranging_disc = 1.0

        # XRAY counter-setup Phase 4 — counter knobs.
        counter_enabled = getattr(cfg, "counter_setup_enabled", True) if cfg else True
        counter_mult = getattr(cfg, "counter_confidence_multiplier", 0.7) if cfg else 0.7
        counter_mtf_min = getattr(cfg, "counter_mtf_threshold", 0.40) if cfg else 0.40
        # XRAY counter-setup Phase 6 — minor BoS confidence multiplier.
        # Applied when require_retest is False AND last_bos.significance
        # is not "major" — i.e. a minor BoS that pre-Phase-6 would have
        # been rejected entirely.
        bos_minor_mult = (
            getattr(cfg, "structural_break_minor_confidence_multiplier", 0.8)
            if cfg else 0.8
        )

        # Convenience handles.
        direction = (analysis.suggested_direction or "").lower()
        struct = (analysis.market_structure.structure or "").lower()
        last_bos = analysis.market_structure.last_bos
        nearest_fvg = analysis.nearest_fvg
        nearest_ob = analysis.nearest_ob
        # XRAY counter-setup Phase 3 fields (default None; Phase 3 finder
        # populates them via the new NearestFVGResult/NearestOBResult contract).
        nearest_fvg_counter = analysis.nearest_fvg_counter
        nearest_ob_counter = analysis.nearest_ob_counter
        active_sweep = analysis.active_sweep_signal
        mtf = analysis.mtf_confluence
        # MTF confluence score is 0-10 in MTFConfluence model — normalize to 0-1.
        mtf_score_01 = (
            float(getattr(mtf, "score", 0)) / 10.0 if mtf is not None else 0.0
        )
        # smc_confluence is 0-100 — normalize.
        smc_01 = max(0.0, min(1.0, analysis.smc_confluence / 100.0))

        # Phase 4: trade_direction defaults to suggested_direction; only
        # counter branches override it. NONE branch resets to "" at the
        # bottom. We mutate analysis directly here — see method docstring.
        analysis.trade_direction = direction

        def _bull_alignment() -> bool:
            """Return True iff the proposed long direction has structural support.

            Uptrend always qualifies. Ranging qualifies ONLY when the
            higher-timeframe confluence is at least
            ``ranging_market_mtf_threshold`` so we never take a long bet
            in a ranging market without HTF backing. Volatile / dead /
            downtrend never qualify.
            """
            if direction != "long":
                return False
            if struct == "uptrend":
                return True
            if struct == "ranging":
                return mtf_score_01 >= ranging_market_mtf_threshold
            return False

        def _bear_alignment() -> bool:
            """Mirror of ``_bull_alignment`` for the short direction."""
            if direction != "short":
                return False
            if struct == "downtrend":
                return True
            if struct == "ranging":
                return mtf_score_01 >= ranging_market_mtf_threshold
            return False

        # ── Bullish FVG + OB confluence ────────────────────────────
        if (
            nearest_fvg is not None and nearest_fvg.direction == "bullish"
            and not nearest_fvg.filled
            and nearest_ob is not None and nearest_ob.direction == "bullish"
            and nearest_ob.fresh
            and _bull_alignment()
            and mtf_score_01 >= fvg_ob_min
        ):
            # XRAY phase-1 fix: dropped the historical max(smc_01, 0.5)
            # floor so confidence reflects actual SMC weakness when smc_01
            # is below 0.5. Path C trusts Claude with truthful values.
            conf = min(mtf_score_01, smc_01)
            if struct == "ranging" and fvg_ob_ranging_disc < 1.0:
                _pre = conf
                conf *= fvg_ob_ranging_disc
                log.info(
                    f"FVG_OB_RANGING_DISCOUNT | sym={getattr(analysis, 'symbol', '?')} "
                    f"setup=BULLISH_FVG_OB conf={_pre:.4f}->{conf:.4f} "
                    f"discount={fvg_ob_ranging_disc:.2f} | down-weight the "
                    f"low-quality FVG-OB-in-ranging archetype (Issue 6) | {ctx()}"
                )
            self._log_confidence_detail(
                analysis, SetupType.BULLISH_FVG_OB, mtf_score_01, smc_01, conf,
            )
            return SetupType.BULLISH_FVG_OB, round(conf, 4)

        # ── Bearish FVG + OB confluence (mirror) ────────────────────
        if (
            nearest_fvg is not None and nearest_fvg.direction == "bearish"
            and not nearest_fvg.filled
            and nearest_ob is not None and nearest_ob.direction == "bearish"
            and nearest_ob.fresh
            and _bear_alignment()
            and mtf_score_01 >= fvg_ob_min
        ):
            conf = min(mtf_score_01, smc_01)
            if struct == "ranging" and fvg_ob_ranging_disc < 1.0:
                _pre = conf
                conf *= fvg_ob_ranging_disc
                log.info(
                    f"FVG_OB_RANGING_DISCOUNT | sym={getattr(analysis, 'symbol', '?')} "
                    f"setup=BEARISH_FVG_OB conf={_pre:.4f}->{conf:.4f} "
                    f"discount={fvg_ob_ranging_disc:.2f} | down-weight the "
                    f"low-quality FVG-OB-in-ranging archetype (Issue 6) | {ctx()}"
                )
            self._log_confidence_detail(
                analysis, SetupType.BEARISH_FVG_OB, mtf_score_01, smc_01, conf,
            )
            return SetupType.BEARISH_FVG_OB, round(conf, 4)

        # ── Bullish FVG + OB COUNTER (suggested_direction=short, but
        #    bullish in-direction structure is unusable; bullish counter
        #    zones near price → trade LONG against the structural bias) ──
        # XRAY counter-setup Phase 4 — characterize-don't-reject. Reaches
        # this branch only when the in-direction bear branch above failed
        # (because nearest_fvg/ob were not bearish, OR because the
        # in-direction bullish branch didn't fire — i.e. suggested_direction
        # is "short" but no in-direction bear FVG_OB triggered, AND a
        # bullish counter pair sits inside the window).
        if (
            counter_enabled
            and direction == "short"
            and nearest_fvg_counter is not None
            and nearest_fvg_counter.direction == "bullish"
            and not nearest_fvg_counter.filled
            and nearest_ob_counter is not None
            and nearest_ob_counter.direction == "bullish"
            and nearest_ob_counter.fresh
            and self._counter_alignment("long", struct, cfg)
            and mtf_score_01 >= counter_mtf_min
        ):
            base_conf = min(mtf_score_01, smc_01)
            conf = round(base_conf * counter_mult, 4)
            analysis.trade_direction = "long"  # counter trade is LONG
            self._log_confidence_detail(
                analysis, SetupType.BULLISH_FVG_OB_COUNTER,
                mtf_score_01, smc_01, conf,
            )
            return SetupType.BULLISH_FVG_OB_COUNTER, conf

        # ── Bearish FVG + OB COUNTER (mirror) ───────────────────────
        if (
            counter_enabled
            and direction == "long"
            and nearest_fvg_counter is not None
            and nearest_fvg_counter.direction == "bearish"
            and not nearest_fvg_counter.filled
            and nearest_ob_counter is not None
            and nearest_ob_counter.direction == "bearish"
            and nearest_ob_counter.fresh
            and self._counter_alignment("short", struct, cfg)
            and mtf_score_01 >= counter_mtf_min
        ):
            base_conf = min(mtf_score_01, smc_01)
            conf = round(base_conf * counter_mult, 4)
            analysis.trade_direction = "short"  # counter trade is SHORT
            self._log_confidence_detail(
                analysis, SetupType.BEARISH_FVG_OB_COUNTER,
                mtf_score_01, smc_01, conf,
            )
            return SetupType.BEARISH_FVG_OB_COUNTER, conf

        # ── Bullish structural break (BOS with optional retest) ─────
        if (
            last_bos is not None and last_bos.direction == "bullish"
            and direction == "long"
            and (not require_retest or last_bos.significance == "major")
        ):
            # Retest proxy: a "major" BOS implies the structure was
            # confirmed; we can't see retest fills directly without
            # candle re-scan, so treat major == retested.
            # XRAY counter-setup Phase 6: minor BoS gets the minor
            # confidence multiplier (default 0.8) — it represents
            # in-direction structure but with weaker confirmation than
            # a major BoS.
            # XRAY phase-1 fix: dropped the 0.5 floor so weak BOS shows
            # truthful confidence (caller weighs structural strength
            # itself, not just a floored composite).
            conf = max(mtf_score_01, smc_01)
            if last_bos.significance != "major":
                conf *= bos_minor_mult
            self._log_confidence_detail(
                analysis, SetupType.BULLISH_STRUCTURAL_BREAK,
                mtf_score_01, smc_01, conf,
            )
            return SetupType.BULLISH_STRUCTURAL_BREAK, round(conf, 4)

        # ── Bearish structural break ───────────────────────────────
        if (
            last_bos is not None and last_bos.direction == "bearish"
            and direction == "short"
            and (not require_retest or last_bos.significance == "major")
        ):
            conf = max(mtf_score_01, smc_01)
            if last_bos.significance != "major":
                conf *= bos_minor_mult
            self._log_confidence_detail(
                analysis, SetupType.BEARISH_STRUCTURAL_BREAK,
                mtf_score_01, smc_01, conf,
            )
            return SetupType.BEARISH_STRUCTURAL_BREAK, round(conf, 4)

        # ── Liquidity sweep + reclaim ──────────────────────────────
        # XRAY phase-1 fix: dropped the 0.5 floor on mtf_score_01 so a
        # genuine sweep on a coin with weak HTF confluence shows truthful
        # confidence (weak HTF means the trade should be smaller, not
        # promoted to "looks like 0.5").
        if active_sweep is not None and active_sweep.sweep_depth_pct >= sweep_min_pct:
            if active_sweep.sweep_type == "bullish_sweep" and direction == "long":
                conf = mtf_score_01
                self._log_confidence_detail(
                    analysis, SetupType.BULLISH_LIQUIDITY_SWEEP,
                    mtf_score_01, smc_01, conf,
                )
                return SetupType.BULLISH_LIQUIDITY_SWEEP, round(conf, 4)
            if active_sweep.sweep_type == "bearish_sweep" and direction == "short":
                conf = mtf_score_01
                self._log_confidence_detail(
                    analysis, SetupType.BEARISH_LIQUIDITY_SWEEP,
                    mtf_score_01, smc_01, conf,
                )
                return SetupType.BEARISH_LIQUIDITY_SWEEP, round(conf, 4)

        # ── Range breakout/breakdown (compression release) ─────────
        # Approximation: position_in_range > 0.95 with prior compression
        # + bullish alignment ⇒ breakout. Without a per-bar compression
        # series, we use total_confluence_factors >= breakout_min_bars/2
        # as a soft proxy; tightened in a future phase if too lax.
        # XRAY phase-1 fix: dropped the 0.5 mtf floor for the same reason
        # as the sweep branches above — preserve truthful confidence.
        if (
            analysis.position_in_range >= 0.95 and direction == "long"
            and analysis.total_confluence_factors >= breakout_min_bars // 2
        ):
            self._log_confidence_detail(
                analysis, SetupType.BULLISH_RANGE_BREAKOUT,
                mtf_score_01, smc_01, mtf_score_01,
            )
            return SetupType.BULLISH_RANGE_BREAKOUT, round(mtf_score_01, 4)
        if (
            analysis.position_in_range <= 0.05 and direction == "short"
            and analysis.total_confluence_factors >= breakout_min_bars // 2
        ):
            self._log_confidence_detail(
                analysis, SetupType.BEARISH_RANGE_BREAKDOWN,
                mtf_score_01, smc_01, mtf_score_01,
            )
            return SetupType.BEARISH_RANGE_BREAKDOWN, round(mtf_score_01, 4)

        # No tradeable structure of any kind — clear the trade_direction
        # we tentatively set at the top so downstream consumers see "" (no
        # trade implied) instead of the suggested_direction echo.
        analysis.trade_direction = ""
        return SetupType.NONE, 0.0

    @staticmethod
    def _log_confidence_detail(
        analysis: StructuralAnalysis,
        setup_type: SetupType,
        mtf_score_01: float,
        smc_01: float,
        final_conf: float,
    ) -> None:
        """Forensic single-line log of the XRAY confidence breakdown.

        Emitted at every non-NONE return from ``classify_setup`` so
        operators can grep one line per coin per cycle and see exactly
        which SMC components contributed and how the formula combined
        ``mtf`` and ``smc_01`` into the final confidence. Additive
        observability — the existing ``XRAY_ANALYZE`` line at end of
        ``analyze()`` is unchanged.

        Best-effort: a logging failure here must never break the
        classification pipeline (the caller is at the structural-
        analysis hot path).
        """
        try:
            br = analysis.smc_breakdown or {}
            log.info(
                f"XRAY_CONFIDENCE_DETAIL | sym={analysis.symbol} "
                f"setup={setup_type.value} "
                f"fvg={br.get('fvg', 0)} ob={br.get('ob', 0)} "
                f"liq={br.get('liq', 0)} sweep={br.get('sweep', 0)} "
                f"smc_total={analysis.smc_confluence} "
                f"mtf={mtf_score_01:.3f} smc_01={smc_01:.3f} "
                f"final_conf={final_conf:.3f} | {ctx()}"
            )
        except Exception:
            pass

    def diagnose_none(self, analysis: StructuralAnalysis) -> dict[str, object]:
        """Phase 2 (output-quality) — explain why classify_setup returned NONE.

        Pure read-only inspection of the same fields ``classify_setup``
        consults; reports which inputs were available and which were
        missing/weak, plus identifies the closest-matching branch in the
        decision tree. Operators use this to tune
        ``[analysis.structure.setup_types]`` thresholds with evidence
        instead of guesswork.

        Does NOT re-classify — caller must already have determined that
        ``analysis.setup_type == NONE``.

        Returns:
            Dict with keys:
                ``closest_type``: SetupType enum value (the branch that
                    came closest to firing), or "none" if no branch had
                    any meaningful match.
                ``missed_by``: short string explaining the specific
                    threshold/condition that wasn't met for the closest
                    branch (e.g. ``"mtf_score=0.40 < fvg_ob_min=0.70"``).
                ``weakest_input``: name of the contributing field with
                    the lowest score (mtf, smc, direction, structure).
                ``mtf_score_01``: normalised MTF confluence (0-1).
                ``smc_01``: normalised SMC confluence (0-1).
                ``direction``: ``"long"`` / ``"short"`` / ``""``.
                ``structure``: ``"uptrend"`` / ``"downtrend"`` / ``""``.
                ``has_fvg``, ``has_ob``, ``has_active_sweep``: bool.
        """
        cfg = getattr(self._settings, "setup_types", None)
        fvg_ob_min = getattr(cfg, "fvg_ob_min_confluence", 0.7) if cfg else 0.7
        sweep_min_pct = (
            getattr(cfg, "sweep_min_displacement_pct", 0.5) if cfg else 0.5
        )
        # Definitive-fix Phase 3: alignment now accepts ``ranging`` when
        # mtf clears this threshold. Mirror the value from classify_setup
        # so the miss-reason text reports the same gate the classifier
        # actually applies.
        ranging_market_mtf_threshold = (
            getattr(cfg, "ranging_market_mtf_threshold", 0.55) if cfg else 0.55
        )

        direction = (analysis.suggested_direction or "").lower()
        struct = (analysis.market_structure.structure or "").lower()
        nearest_fvg = analysis.nearest_fvg
        nearest_ob = analysis.nearest_ob
        active_sweep = analysis.active_sweep_signal
        mtf = analysis.mtf_confluence
        mtf_score_01 = (
            float(getattr(mtf, "score", 0)) / 10.0 if mtf is not None else 0.0
        )
        smc_01 = max(0.0, min(1.0, analysis.smc_confluence / 100.0))
        last_bos = analysis.market_structure.last_bos

        # Score each branch by how many of its conditions are present.
        # Each condition contributes 1; the branch with the highest count
        # is "closest". Ties broken by mtf_score.
        branches: list[tuple[str, int, str]] = []  # (branch_name, score, miss_reason)

        # FVG_OB bullish
        fvg_ob_b_score = 0
        fvg_ob_b_miss: list[str] = []
        fresh_bull_fvg = (
            nearest_fvg is not None
            and nearest_fvg.direction == "bullish"
            and not nearest_fvg.filled
        )
        if fresh_bull_fvg:
            fvg_ob_b_score += 1
        else:
            fvg_ob_b_miss.append("no_fresh_bullish_fvg")
        if nearest_ob is not None and nearest_ob.direction == "bullish" and nearest_ob.fresh:
            fvg_ob_b_score += 1
        else:
            fvg_ob_b_miss.append("no_fresh_bullish_ob")
        # Definitive-fix Phase 3: alignment also accepts ranging+long when
        # mtf clears ranging_market_mtf_threshold. Mirror the classifier
        # logic so this miss-reason matches the gate that actually fired.
        _bull_aligned = (
            direction == "long"
            and (
                struct == "uptrend"
                or (struct == "ranging" and mtf_score_01 >= ranging_market_mtf_threshold)
            )
        )
        if _bull_aligned:
            fvg_ob_b_score += 1
        else:
            d_, s_ = direction or "na", struct or "na"
            if direction == "long" and struct == "ranging":
                fvg_ob_b_miss.append(
                    f"ranging_long_mtf={mtf_score_01:.2f}<{ranging_market_mtf_threshold:.2f}"
                )
            else:
                fvg_ob_b_miss.append(f"no_long_uptrend_align(dir={d_},struct={s_})")
        if mtf_score_01 >= fvg_ob_min:
            fvg_ob_b_score += 1
        else:
            fvg_ob_b_miss.append(f"mtf_score={mtf_score_01:.2f}<fvg_ob_min={fvg_ob_min:.2f}")
        branches.append(("BULLISH_FVG_OB", fvg_ob_b_score, ";".join(fvg_ob_b_miss)))

        # FVG_OB bearish — mirror
        fvg_ob_s_score = 0
        fvg_ob_s_miss: list[str] = []
        fresh_bear_fvg = (
            nearest_fvg is not None
            and nearest_fvg.direction == "bearish"
            and not nearest_fvg.filled
        )
        if fresh_bear_fvg:
            fvg_ob_s_score += 1
        else:
            fvg_ob_s_miss.append("no_fresh_bearish_fvg")
        if nearest_ob is not None and nearest_ob.direction == "bearish" and nearest_ob.fresh:
            fvg_ob_s_score += 1
        else:
            fvg_ob_s_miss.append("no_fresh_bearish_ob")
        # Definitive-fix Phase 3: mirror of bullish alignment-broadening.
        _bear_aligned = (
            direction == "short"
            and (
                struct == "downtrend"
                or (struct == "ranging" and mtf_score_01 >= ranging_market_mtf_threshold)
            )
        )
        if _bear_aligned:
            fvg_ob_s_score += 1
        else:
            d_, s_ = direction or "na", struct or "na"
            if direction == "short" and struct == "ranging":
                fvg_ob_s_miss.append(
                    f"ranging_short_mtf={mtf_score_01:.2f}<{ranging_market_mtf_threshold:.2f}"
                )
            else:
                fvg_ob_s_miss.append(f"no_short_downtrend_align(dir={d_},struct={s_})")
        if mtf_score_01 >= fvg_ob_min:
            fvg_ob_s_score += 1
        else:
            fvg_ob_s_miss.append(f"mtf_score={mtf_score_01:.2f}<fvg_ob_min={fvg_ob_min:.2f}")
        branches.append(("BEARISH_FVG_OB", fvg_ob_s_score, ";".join(fvg_ob_s_miss)))

        # Structural break bullish
        sb_b_score = 0
        sb_b_miss: list[str] = []
        if last_bos is not None and last_bos.direction == "bullish":
            sb_b_score += 2  # heavy weight — BOS is the primary input
        else:
            sb_b_miss.append("no_bullish_bos")
        if direction == "long":
            sb_b_score += 1
        else:
            sb_b_miss.append(f"direction={direction or 'na'}_not_long")
        branches.append(("BULLISH_STRUCTURAL_BREAK", sb_b_score, ";".join(sb_b_miss)))

        # Structural break bearish — mirror
        sb_s_score = 0
        sb_s_miss: list[str] = []
        if last_bos is not None and last_bos.direction == "bearish":
            sb_s_score += 2
        else:
            sb_s_miss.append("no_bearish_bos")
        if direction == "short":
            sb_s_score += 1
        else:
            sb_s_miss.append(f"direction={direction or 'na'}_not_short")
        branches.append(("BEARISH_STRUCTURAL_BREAK", sb_s_score, ";".join(sb_s_miss)))

        # Liquidity sweep
        if active_sweep is not None:
            sw_score = 1
            sw_miss: list[str] = []
            if active_sweep.sweep_depth_pct >= sweep_min_pct:
                sw_score += 1
            else:
                sw_miss.append(
                    f"sweep_depth={active_sweep.sweep_depth_pct:.2f}<min={sweep_min_pct:.2f}"
                )
            label = (
                "BULLISH_LIQUIDITY_SWEEP" if active_sweep.sweep_type == "bullish_sweep"
                else "BEARISH_LIQUIDITY_SWEEP"
            )
            branches.append((label, sw_score, ";".join(sw_miss) or "ok"))
        else:
            branches.append(("LIQUIDITY_SWEEP", 0, "no_active_sweep"))

        # Pick the closest (highest-scoring) branch.
        branches.sort(key=lambda b: -b[1])
        closest_type, closest_score, miss_reason = branches[0]

        # Identify the weakest contributing input.
        input_scores = {
            "mtf": mtf_score_01,
            "smc": smc_01,
            "direction_alignment": (
                1.0
                if direction in ("long", "short") and struct in ("uptrend", "downtrend")
                else 0.0
            ),
            "fvg_present": 1.0 if nearest_fvg is not None else 0.0,
            "ob_present": 1.0 if nearest_ob is not None else 0.0,
            "sweep_present": 1.0 if active_sweep is not None else 0.0,
        }
        weakest_input = min(input_scores.items(), key=lambda kv: kv[1])[0]

        # XRAY counter-setup Phase 6 — structured evidence fields. After
        # Phase 4 ships the counter branches, NONE only fires when neither
        # in-direction nor counter has structure AND no BoS / sweep / range.
        # The enriched evidence makes it possible to tell whether the coin
        # is truly cold (no zones either direction) vs the counter branch
        # legitimately rejected the available zones (alignment, MTF, etc.).
        nearest_fvg_counter = getattr(analysis, "nearest_fvg_counter", None)
        nearest_ob_counter = getattr(analysis, "nearest_ob_counter", None)

        def _fvg_state(f) -> str:
            if f is None:
                return "missing"
            if f.filled:
                return "filled"
            return "available"  # was within window but classifier rejected it

        def _ob_state(o) -> str:
            if o is None:
                return "missing"
            if not o.fresh:
                return "stale"
            return "available"

        last_bos_significance = (
            last_bos.significance if last_bos is not None else "none"
        )
        # last_bos_age_bars not stored — emit -1 sentinel until the
        # market_structure module surfaces it.
        last_bos_age_bars = -1

        def _range_compression(pir) -> bool:
            try:
                p = float(pir)
                return p >= 0.95 or p <= 0.05
            except (TypeError, ValueError):
                return False

        atr_pct_h1 = float(getattr(analysis, "atr_pct_h1", 0.0) or 0.0)
        # Window pcts mirror the values _find_nearest_* would have used.
        fvg_window = max(
            getattr(cfg, "fvg_min_distance_pct", 2.0) if cfg else 2.0,
            (getattr(cfg, "fvg_atr_multiplier", 3.0) if cfg else 3.0) * atr_pct_h1,
        )
        ob_window = max(
            getattr(cfg, "ob_min_distance_pct", 3.0) if cfg else 3.0,
            (getattr(cfg, "ob_atr_multiplier", 4.0) if cfg else 4.0) * atr_pct_h1,
        )

        return {
            "closest_type": closest_type if closest_score > 0 else "none",
            "missed_by": miss_reason,
            "weakest_input": weakest_input,
            "mtf_score_01": round(mtf_score_01, 3),
            "smc_01": round(smc_01, 3),
            "direction": direction,
            "structure": struct,
            "has_fvg": nearest_fvg is not None,
            "has_ob": nearest_ob is not None,
            "has_active_sweep": active_sweep is not None,
            # XRAY counter-setup Phase 6 — structured evidence fields.
            "in_direction_fvg": _fvg_state(nearest_fvg),
            "in_direction_ob": _ob_state(nearest_ob),
            "counter_direction_fvg": _fvg_state(nearest_fvg_counter),
            "counter_direction_ob": _ob_state(nearest_ob_counter),
            "last_bos_significance": last_bos_significance,
            "last_bos_age_bars": last_bos_age_bars,
            "recent_sweep": active_sweep is not None,
            "range_compression": _range_compression(
                getattr(analysis, "position_in_range", 0.5),
            ),
            "atr_pct_h1": round(atr_pct_h1, 3),
            "window_pct_fvg": round(fvg_window, 3),
            "window_pct_ob": round(ob_window, 3),
            "first_failure_branch": (
                closest_type if closest_score > 0 else "no_match"
            ),
        }

    def _compute_setup_score(
        self,
        position_in_range: float,
        market_structure: MarketStructureResult,
        structural_placement: StructuralPlacement | None,
        suggested_direction: str,
        smc_confluence: int = 0,
        volume_profile=None,
        fibonacci=None,
        mtf_confluence=None,
        symbol: str = "",
    ) -> tuple[int, str]:
        """Compute overall setup score 0-100 and quality grade.

        Base 50 + Phase 1 modifiers + Phase 2 SMC + Phase 3 confluence.

        Returns:
            (score, quality) tuple.
        """
        score = 50  # Base

        # Issue 3 (CALL_A exploit/fetch, 2026-06-05) — grade reward-to-risk on the
        # CHOSEN direction's RR, not rr_best. structure_engine overwrites
        # structural_placement.rr_ratio to rr_best = max(long_rr, short_rr) for
        # downstream display/APEX/scanner, but the score must reflect the side
        # actually being traded: a downtrend short at the range floor has
        # rr_short ~0.1 (reward spent) while rr_long ~20, so rr_best graded it A+.
        # rr_short / rr_long are already populated on the placement before
        # rr_ratio is overwritten; read them by the final suggested_direction.
        # Falls back to rr_ratio when the directional value is unavailable, so the
        # no-placement path and any caller without the fields are byte-identical.
        chosen_rr = None
        if structural_placement is not None:
            chosen_rr = getattr(structural_placement, "rr_ratio", None)
            if suggested_direction in ("long", "short"):
                _dir_rr = getattr(
                    structural_placement,
                    "rr_short" if suggested_direction == "short" else "rr_long",
                    None,
                )
                if isinstance(_dir_rr, (int, float)):
                    chosen_rr = float(_dir_rr)
        _rr_best_legacy = (
            getattr(structural_placement, "rr_ratio", None)
            if structural_placement is not None else None
        )

        # Entry position modifier
        if suggested_direction == "long":
            if position_in_range < 0.15:
                score += 25
            elif position_in_range < 0.30:
                score += 15
            elif position_in_range > 0.85:
                score -= 10
        elif suggested_direction == "short":
            if position_in_range > 0.85:
                score += 25
            elif position_in_range > 0.70:
                score += 15
            elif position_in_range < 0.15:
                score -= 10

        # Issue 3 (CALL_A exploit/fetch) — range-position no-room penalty.
        # A short at the very bottom of the range (no room left below) or a long
        # at the very top (no room left above) is geometrically spent regardless
        # of pattern quality; dock it so spent geometry cannot present as a top
        # grade even before the directional-RR cap. Symmetric, additive, tunable
        # (range_no_room_penalty = 0 disables; threshold and weight are config).
        _range_floor = float(getattr(self._settings, "range_floor_threshold", 0.05))
        _range_pen = int(getattr(self._settings, "range_no_room_penalty", 25))
        if _range_pen > 0:
            if suggested_direction == "short" and position_in_range <= _range_floor:
                score -= _range_pen
            elif suggested_direction == "long" and position_in_range >= (1.0 - _range_floor):
                score -= _range_pen

        # Structure alignment
        struct = market_structure.structure
        if suggested_direction == "long" and struct == "uptrend":
            score += 20
        elif suggested_direction == "short" and struct == "downtrend":
            score += 20
        elif struct == "ranging":
            pass
        elif suggested_direction and struct not in ("unknown", "ranging"):
            score -= 15

        # R:R ratio (Issue 3 — graded on the CHOSEN direction's RR, not rr_best)
        if structural_placement:
            rr = chosen_rr if chosen_rr is not None else structural_placement.rr_ratio
            is_fallback = getattr(structural_placement, 'is_fallback_rr', False)
            rr_quality = getattr(structural_placement, 'rr_quality', 'skip')
            if is_fallback or rr_quality == "unknown":
                pass  # Don't reward or penalize arbitrary/unknown R:R
            elif rr >= 3.0:
                score += 20   # Excellent structural R:R
            elif rr >= 2.0:
                score += 10   # Good structural R:R
            elif rr < 1.0:
                score -= 40   # Terrible: guaranteed loss territory → prevents A+
            elif rr < 1.5:
                score -= 20   # Bad R:R
            elif rr < 2.0:
                score -= 10   # Below target

        # BOS in direction
        if market_structure.last_bos:
            bos_dir = market_structure.last_bos.direction
            if (suggested_direction == "long" and bos_dir == "bullish") or \
               (suggested_direction == "short" and bos_dir == "bearish"):
                score += 10

        # CHoCH against direction
        if market_structure.last_choch:
            choch_dir = market_structure.last_choch.direction
            if (suggested_direction == "long" and choch_dir == "bearish") or \
               (suggested_direction == "short" and choch_dir == "bullish"):
                score -= 15

        # Structure strength
        if market_structure.strength == "strong":
            score += 5
        elif market_structure.strength == "weak":
            score -= 5

        # Phase 2: Smart Money Concepts bonus
        if smc_confluence >= 70:
            score += 15
        elif smc_confluence >= 40:
            score += 8
        elif smc_confluence >= 20:
            score += 4

        # Phase 3: Confluence bonuses
        if volume_profile:
            if (suggested_direction == "long" and volume_profile.current_vs_poc == "below_poc") or \
               (suggested_direction == "short" and volume_profile.current_vs_poc == "above_poc"):
                score += 5

        if fibonacci:
            if fibonacci.confluence_with:
                score += 8
            else:
                score += 4

        if mtf_confluence:
            if mtf_confluence.quality == "maximum":
                score += 12
            elif mtf_confluence.quality == "good":
                score += 7
            elif mtf_confluence.quality == "weak":
                score += 2

        # Issue 2 (X-RAY de-saturation, 2026-06-06) — headroom scale. The additive
        # modifiers above sum to well past the 0-100 space for a good setup (a
        # strong long reaches base 50 + ~120 in bonuses), so they overflowed and
        # the clamp pinned the majority of coins at exactly 100 — grade A+ only,
        # with the middle of the range empty (live: 76 A+ all at 100, zero at A/C).
        # Compressing the NET modifier around the neutral base 50 by a single
        # tunable factor gives the score headroom: relative ordering is preserved
        # EXACTLY (every modifier scales the same), but a maxed setup now lands near
        # the top, a typical one mid-range, a weak one low, so the grades spread
        # across A+/A/B/C. scale=1.0 reproduces the pre-fix overflow. The
        # directional-RR hard caps and the SMC+MTF floor cap below run AFTER this
        # and are NOT scaled, so the spent-short de-grading stays intact.
        _scale = float(getattr(self._settings, "setup_score_modifier_scale", 1.0))
        score = 50.0 + _scale * (score - 50.0)

        # Clamp (back to int — the score is contracted as int 0-100 and rendered
        # as an integer in the prompt).
        score = int(round(max(0.0, min(100.0, score))))

        # Quality mapping (thresholds centralized — Issue 2)
        _t_aplus = int(getattr(self._settings, "setup_grade_a_plus_min", 80))
        _t_a = int(getattr(self._settings, "setup_grade_a_min", 65))
        _t_b = int(getattr(self._settings, "setup_grade_b_min", 50))
        _t_c = int(getattr(self._settings, "setup_grade_c_min", 35))
        if score >= _t_aplus:
            quality = "A+"
        elif score >= _t_a:
            quality = "A"
        elif score >= _t_b:
            quality = "B"
        elif score >= _t_c:
            quality = "C"
        else:
            quality = "SKIP"

        # Unconditional R:R hard cap — applies to ALL cases including no placement
        # A coin without structural SL/TP CANNOT be A+ (no risk/reward basis)
        if structural_placement is None:
            # No S/R levels at all → no structural basis for the trade → cap at B
            if quality in ("A+", "A"):
                quality = "B"
                score = min(score, 64)
        else:
            # Issue 3 — cap on the CHOSEN direction's RR (rr_short for a short,
            # rr_long for a long), not rr_best. This is what makes a spent
            # downtrend short (rr_short ~0.1) hit the <0.5 -> SKIP cap instead of
            # riding the long side's rr_best ~20 to A+.
            _rr = chosen_rr if chosen_rr is not None else structural_placement.rr_ratio
            if _rr < 0.5:
                # Terrible R:R (including 0.0) — force SKIP
                quality = "SKIP"
                score = min(score, 30)
            elif _rr < 1.0:
                # Bad R:R — cap at C
                if quality in ("A+", "A", "B"):
                    quality = "C"
                    score = min(score, 49)
            elif _rr < 1.5:
                # Mediocre R:R — cap at B
                if quality in ("A+", "A"):
                    quality = "B"
                    score = min(score, 64)

        # Phase 25 (Y-24): SMC + MTF must contribute to A-grade.
        # The pre-Phase25 formula treated SMC (+15 max) and MTF (+12 max)
        # as bonuses on top of base score from S/R + R:R. A coin with
        # smc=0, mtf=none could still achieve A grade if entry position
        # and trend alignment carried it. The brief: smc=0, mtf=none,
        # confl=3 → quality=A — operators trust A as "structurally
        # supported" but the structure under it is empty. Hard cap:
        #   smc < 10 AND mtf_score < 3 → cap at B
        #   total_confluence_factors < 3 → cap at C
        # These thresholds match the brief's spec exactly.
        _mtf_score = 0
        if mtf_confluence is not None:
            try:
                _mtf_score = int(getattr(mtf_confluence, "score", 0) or 0)
            except Exception:
                _mtf_score = 0
        if smc_confluence < 10 and _mtf_score < 3:
            if quality in ("A+", "A"):
                quality = "B"
                score = min(score, 64)
                log.info(
                    f"XRAY_GRADE_CAPPED | reason=smc_mtf_below_threshold "
                    f"smc={smc_confluence} mtf_score={_mtf_score} cap=B | {ctx()}"
                )

        # Issue 3 (CALL_A exploit/fetch) — grade-change sentinel. Fires when the
        # directional-RR grading docked this setup that the old rr_best grading
        # would have left high (chosen side's RR is in cap territory while
        # rr_best sat above 1.5). This is the observable proof that spent
        # downtrend shorts (and spent range-top longs) now score low instead of
        # presenting A+. Throttled implicitly by how rare a downgrade is.
        try:
            if (
                chosen_rr is not None
                and isinstance(_rr_best_legacy, (int, float))
                and chosen_rr < _rr_best_legacy
                and chosen_rr < 1.5
                and quality in ("C", "SKIP", "B")
                and _rr_best_legacy >= 1.5
            ):
                log.info(
                    f"XRAY_DIRECTIONAL_RR_GATED | sym={symbol or '?'} "
                    f"dir={suggested_direction or '?'} "
                    f"pos={position_in_range:.2f} rr_chosen={chosen_rr:.2f} "
                    f"rr_best={_rr_best_legacy:.2f} now={quality} score={score} "
                    f"| spent-side RR capped the grade (was riding rr_best) | "
                    f"{ctx()}"
                )
        except Exception:
            pass

        return score, quality
