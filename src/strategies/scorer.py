"""Trade Scorer (Layer 2): scores each RawSignal from 0-105.

Four scoring components:
  Base (0-40):       Conditions strength
  Confluence (0-25): Multiple indicator agreement
  Context (0-20):    Higher TF, sentiment, F&G, funding, regime
  Quality (0-20):    Spread, volume, S/R + X-RAY structure, clean setup
"""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OHLCV, Side
from src.strategies.models.regime_types import RegimeState
from src.strategies.models.signal_types import RawSignal, ScoredSetup

log = get_logger("strategies")


class TradeScorer:
    """Scores raw signals from strategies using a 4-component system.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Candidate-Block Data Integrity Fix — Issue 5 (2026-06-09) — boot
        # sentinel for the centralized grade composition so operators can grep
        # BOOT_SCORER_GRADING to confirm the cutoffs / quality floor / cap state.
        _se = getattr(settings, "strategy_engine", None)
        log.info(
            f"BOOT_SCORER_GRADING | "
            f"cutoffs=A+>={getattr(_se, 'grade_threshold_a_plus', 80)}/"
            f"A>={getattr(_se, 'grade_threshold_a', 68)}/"
            f"B>={getattr(_se, 'grade_threshold_b', 56)}/"
            f"C>={getattr(_se, 'grade_threshold_c', 45)} "
            f"quality_floor={getattr(_se, 'grade_quality_floor', 10.0)} "
            f"cap_enabled={getattr(_se, 'grade_quality_cap_enabled', False)} "
            f"cap_max={getattr(_se, 'grade_quality_cap_max_grade', 'B')} | {ctx()}"
        )

    def score(
        self,
        signal: RawSignal,
        candles: list[OHLCV],
        ta_data: dict,
        sentiment_data: dict | None,
        altdata: dict | None,
        regime: RegimeState,
        structural_data: dict | None = None,
    ) -> ScoredSetup:
        """Score a single raw signal.

        Args:
            structural_data: Optional X-RAY StructuralAnalysis.to_dict() output.

        Returns:
            ScoredSetup with component scores and grade.
        """
        base = self._score_base(signal)
        confluence = self._score_confluence(signal, ta_data)
        context = self._score_context(signal, ta_data, sentiment_data, altdata, regime)
        quality = self._score_quality(signal, candles, ta_data, structural_data)

        total = base + confluence + context + quality

        if total >= 70:
            log.debug(f"SCORER | sym={signal.symbol} dir={signal.direction} base={base:.0f} conf={confluence:.0f} ctx={context:.0f} qual={quality:.0f} total={total:.0f} | {ctx()}")

        # Candidate-Block Data Integrity Fix — Issue 5 (2026-06-09): grade
        # cutoffs are centralized (were hardcoded 80/68/56/45). Same defaults.
        _se = getattr(self.settings, "strategy_engine", None)
        _t_aplus = int(getattr(_se, "grade_threshold_a_plus", 80))
        _t_a = int(getattr(_se, "grade_threshold_a", 68))
        _t_b = int(getattr(_se, "grade_threshold_b", 56))
        _t_c = int(getattr(_se, "grade_threshold_c", 45))
        if total >= _t_aplus:
            grade = "A+"
        elif total >= _t_a:
            grade = "A"
        elif total >= _t_b:
            grade = "B"
        elif total >= _t_c:
            grade = "C"
        else:
            grade = "D"

        # Issue 5 — quality-floor grade cap (config-gated, default OFF). A setup
        # whose quality sub-score is below the floor has weak underlying
        # quality/cleanness even when base/confluence/context push the total high
        # (e.g. BSB total=84 grade=A+ on quality=7/20). When the cap is enabled,
        # such a setup cannot CARRY a grade above the configured ceiling — the
        # canonical grade is lowered so neither the brain nor any grade-driven
        # sizing is misled. Default OFF: the always-on candidate-block annotation
        # surfaces the weak quality regardless; this cap is the opt-in
        # behavioural lever. A floor of 0 disables it entirely.
        _q_floor = float(getattr(_se, "grade_quality_floor", 10.0))
        _cap_enabled = bool(getattr(_se, "grade_quality_cap_enabled", False))
        # Issue 5 follow-up (2026-06-09): normalize case/whitespace so a config
        # typo like "b " cannot silently disable the cap (mirrors the
        # grade.upper().strip() pattern in fund_manager/position_sizer.py).
        _cap_max = str(getattr(_se, "grade_quality_cap_max_grade", "B")).strip().upper()
        _quality_capped = False
        if _cap_enabled and quality < _q_floor:
            _order = ["D", "C", "B", "A", "A+"]
            if (
                _cap_max in _order
                and grade in _order
                and _order.index(grade) > _order.index(_cap_max)
            ):
                log.info(
                    f"SCORER_QUALITY_CAP | sym={signal.symbol} "
                    f"dir={signal.direction} total={total:.0f} "
                    f"grade_uncapped={grade} quality={quality:.1f} "
                    f"floor={_q_floor:.0f} capped_to={_cap_max} | {ctx()}"
                )
                grade = _cap_max
                _quality_capped = True

        # XRAY counter-setup Phase 5c — surface setup_type_confidence and
        # trade_direction in scoring_details so ensemble.vote() can read
        # them off the ScoredSetup without a parallel lookup. Counter
        # setups (Phase 4) carry confidence ≈ 0.35 vs in-direction ≈ 0.85;
        # Phase 5c uses this in size_mult scaling.
        _setup_type_confidence = (
            float(structural_data.get("setup_type_confidence", 0.85))
            if structural_data and structural_data.get("setup_type_confidence") is not None
            else 0.85
        )
        _trade_direction = (
            structural_data.get("trade_direction", "")
            if structural_data
            else ""
        )

        return ScoredSetup(
            raw_signal=signal,
            base_score=base,
            confluence_score=confluence,
            context_score=context,
            quality_score=quality,
            total_score=total,
            grade=grade,
            scoring_details={
                "base": round(base, 2),
                "confluence": round(confluence, 2),
                "context": round(context, 2),
                "quality": round(quality, 2),
                "setup_type_confidence": round(_setup_type_confidence, 4),
                "trade_direction": _trade_direction,
                # Issue 5 (2026-06-09): True when the gated quality-floor cap
                # lowered this grade (observability; the grade field already
                # carries the capped value).
                "quality_capped": _quality_capped,
            },
        )

    def score_batch(
        self,
        signals: list[RawSignal],
        candles_map: dict[str, list[OHLCV]],
        ta_map: dict[str, dict],
        sentiment_data: dict | None,
        altdata: dict | None,
        regime: RegimeState,
        structural_map: dict[str, dict] | None = None,
        coin_regimes: dict[str, RegimeState] | None = None,
    ) -> list[ScoredSetup]:
        """Score ALL signals. No threshold filtering — score determines size, not eligibility.

        Args:
            structural_map: Optional dict of symbol → StructuralAnalysis.to_dict().
            coin_regimes: Per-coin-authority Phase 4 (2026-05-29). When provided,
                each signal is scored under ITS OWN coin's regime (the +2 context
                bonus tests that coin's active categories), else an explicit
                UNKNOWN — NEVER the global ``regime``. The ``regime`` arg is kept
                as the legacy/default for callers that pass no per-coin map.
        """
        scored: list[ScoredSetup] = []

        for signal in signals:
            candles = candles_map.get(signal.symbol, [])
            ta = ta_map.get(signal.symbol, {})
            struct = structural_map.get(signal.symbol) if structural_map else None
            # Phase 4: per-coin regime, else UNKNOWN, never the global regime.
            _sig_regime = regime
            if coin_regimes is not None:
                _sig_regime = coin_regimes.get(signal.symbol) or RegimeState.unknown()
            setup = self.score(signal, candles, ta, sentiment_data, altdata, _sig_regime, struct)
            scored.append(setup)

        scored.sort(key=lambda s: s.total_score, reverse=True)
        return scored

    # --- Component scorers ---

    @staticmethod
    def _score_base(signal: RawSignal) -> float:
        """Base score (0-40): starting 25 + condition strength bonuses."""
        score = 30.0
        for _name, strength in signal.conditions_strength.items():
            if strength > 0.8:
                score += 3
            elif strength > 0.6:
                score += 2
            elif strength > 0.4:
                score += 1
        return min(score, 40.0)

    @staticmethod
    def _score_confluence(signal: RawSignal, ta_data: dict) -> float:
        """Confluence score (0-25): how many indicator types agree."""
        score = 0.0
        direction = signal.direction
        is_buy = direction == Side.BUY

        trend = ta_data.get("trend", {})
        momentum = ta_data.get("momentum", {})
        vol = ta_data.get("volume", {})

        # Trend agreement
        trend_sum = trend.get("trend_summary", "NEUTRAL")
        if (is_buy and trend_sum == "BULLISH") or (not is_buy and trend_sum == "BEARISH"):
            score += 5
        elif (is_buy and trend_sum == "BEARISH") or (not is_buy and trend_sum == "BULLISH"):
            score -= 3

        # Momentum agreement
        mom_sum = momentum.get("momentum_summary", "NEUTRAL")
        if (is_buy and mom_sum == "BULLISH") or (not is_buy and mom_sum == "BEARISH"):
            score += 5
        elif (is_buy and mom_sum == "BEARISH") or (not is_buy and mom_sum == "BULLISH"):
            score -= 3

        # Volume confirmation
        vol_sum = vol.get("volume_summary", "AVERAGE")
        if vol_sum in ("ABOVE_AVERAGE", "SPIKE"):
            score += 5

        # Pattern presence (from TA overall)
        overall = ta_data.get("overall", {})
        overall_signal = overall.get("signal", "NEUTRAL")
        if (is_buy and overall_signal in ("BUY", "STRONG_BUY")) or \
           (not is_buy and overall_signal in ("SELL", "STRONG_SELL")):
            score += 5
        elif (is_buy and overall_signal in ("SELL", "STRONG_SELL")) or \
             (not is_buy and overall_signal in ("BUY", "STRONG_BUY")):
            score -= 3

        # Volatility favorable
        vol_summary = ta_data.get("volatility", {}).get("volatility_summary", "MODERATE")
        if vol_summary in ("MODERATE", "HIGH"):
            score += 5

        return max(0.0, min(score, 25.0))

    @staticmethod
    def _score_context(
        signal: RawSignal,
        ta_data: dict,
        sentiment_data: dict | None,
        altdata: dict | None,
        regime: RegimeState,
    ) -> float:
        """Context score (0-20): higher TF, sentiment, F&G, funding, regime match."""
        score = 0.0
        is_buy = signal.direction == Side.BUY

        # Higher TF signal agreement (from TA overall which uses multiple TFs)
        overall = ta_data.get("overall", {})
        ta_conf = overall.get("confidence", 0)
        ta_signal = overall.get("signal", "NEUTRAL")
        if ta_conf > 0.6:
            if (is_buy and ta_signal in ("BUY", "STRONG_BUY")) or \
               (not is_buy and ta_signal in ("SELL", "STRONG_SELL")):
                score += 10
            else:
                score += 3

        # Sentiment
        if sentiment_data:
            sent_score = sentiment_data.get("overall_score", 0)
            if (is_buy and sent_score > 0.2) or (not is_buy and sent_score < -0.2):
                score += 3

        # Fear & Greed (0-8 points — amplified for extremes)
        if altdata:
            fg = altdata.get("fear_greed", {})
            fg_val = fg.get("value", altdata.get("fear_greed_value", 50))
            if isinstance(fg_val, (int, float)):
                if fg_val < 15:
                    score += 8 if is_buy else 3  # Extreme fear: contrarian buy = 8pts
                elif fg_val < 25:
                    score += 5 if is_buy else 2  # Fear: buy = 5pts
                elif fg_val < 35:
                    score += 3 if is_buy else 1
                elif fg_val > 85:
                    score += 8 if not is_buy else 3  # Extreme greed: contrarian sell = 8pts
                elif fg_val > 75:
                    score += 5 if not is_buy else 2
                elif fg_val > 65:
                    score += 3 if not is_buy else 1
                else:
                    score += 1  # Neutral F&G = minimal context

            # Funding rate (0-4 points)
            fr = altdata.get("funding_rate", 0)
            if not fr and altdata.get("funding"):
                # Try to get funding from nested dict
                funding_dict = altdata.get("funding", {})
                if isinstance(funding_dict, dict):
                    fr = next(iter(funding_dict.values()), 0) if funding_dict else 0
            if isinstance(fr, (int, float)):
                if (is_buy and fr < -0.01) or (not is_buy and fr > 0.01):
                    score += 4
                elif abs(fr) > 0.005:
                    score += 2

        # Regime match
        if signal.strategy_category in regime.active_strategy_categories:
            score += 2

        return min(score, 20.0)

    @staticmethod
    def _score_quality(
        signal: RawSignal,
        candles: list[OHLCV],
        ta_data: dict,
        structural_data: dict | None = None,
    ) -> float:
        """Quality score (0-20): spread, volume, S/R+X-RAY structure, clean setup.

        When X-RAY structural_data is available, uses richer structural
        scoring for the S/R component (0-8 pts, replaces basic TAEngine 0-3).
        """
        score = 0.0
        is_buy = signal.direction == Side.BUY

        # Volume strength (0-3 pts)
        vol_ratio = ta_data.get("volume", {}).get("volume_sma_ratio")
        if vol_ratio and vol_ratio > 2.0:
            score += 3
        elif vol_ratio and vol_ratio > 1.3:
            score += 2

        # S/R proximity (0-8 pts with X-RAY, 0-3 basic) — use X-RAY if available
        sr_score = 0.0
        xray_breakdown: dict = {}
        if structural_data:
            sr_score, xray_breakdown = _xray_sr_score(structural_data, is_buy)
        else:
            sr = ta_data.get("support_resistance", {})
            current = sr.get("current_price", 0)
            supports = sr.get("support_levels", [])
            resistances = sr.get("resistance_levels", [])

            if is_buy and supports and current > 0:
                nearest_support = supports[0] if supports else 0
                if nearest_support > 0:
                    dist_pct = abs(current - nearest_support) / current * 100
                    if dist_pct < 1.0:
                        sr_score = 3
            elif not is_buy and resistances and current > 0:
                nearest_resistance = resistances[0] if resistances else 0
                if nearest_resistance > 0:
                    dist_pct = abs(nearest_resistance - current) / current * 100
                    if dist_pct < 1.0:
                        sr_score = 3
        score += sr_score

        if structural_data and sr_score != 0.0:
            bd = xray_breakdown
            log.info(
                f"XRAY_SCORE | sym={signal.symbol} dir={signal.direction} "
                f"entry={bd.get('entry',0):+.1f} rr={bd.get('rr',0):+.1f} "
                f"struct={bd.get('struct',0):+.1f} "
                f"fvg={bd.get('fvg',0):+.1f} ob={bd.get('ob',0):+.1f} "
                f"smc={bd.get('smc',0):+.1f} sweep={bd.get('sweep',0):+.1f} "
                f"poc={bd.get('poc',0):+.1f} fib={bd.get('fib',0):+.1f} "
                f"mtf={bd.get('mtf',0):+.1f} "
                f"total={sr_score:+.1f} quality={score:.0f} | {ctx()}"
            )

        # Clean candle structure (0-3 pts)
        if len(candles) >= 5:
            recent = candles[-5:]
            body_to_range_ratios = []
            for c in recent:
                full_range = c.high - c.low
                body = abs(c.close - c.open)
                if full_range > 0:
                    body_to_range_ratios.append(body / full_range)
            if body_to_range_ratios:
                avg_ratio = sum(body_to_range_ratios) / len(body_to_range_ratios)
                if avg_ratio > 0.5:
                    score += 3

        # Baseline quality
        score += 3

        return min(score, 20.0)


def _xray_sr_score(structural_data: dict, is_buy: bool) -> tuple[float, dict]:
    """Score S/R proximity using X-RAY structural data (0-8 pts).

    Provides richer scoring than basic TAEngine S/R by considering
    entry quality, R:R ratio, structure alignment, and Phase 2/3
    smart money concepts (FVG, OB, sweeps, POC, Fib, MTF).

    Returns:
        (clamped_score, breakdown_dict) where breakdown tracks each modifier.
    """
    try:
        placement = structural_data.get("structural_placement")
        ms = structural_data.get("market_structure")
        sr_pts = 0.0

        # Track individual modifier contributions for INFO log
        _m = {
            "entry": 0.0, "rr": 0.0, "struct": 0.0, "bos": 0.0,
            "choch": 0.0, "fvg": 0.0, "ob": 0.0, "smc": 0.0,
            "sweep": 0.0, "poc": 0.0, "fib": 0.0, "mtf": 0.0,
            "sess": 0.0, "rr_pen": 0.0,
        }

        if placement:
            # Entry quality (0-3 pts — replaces basic S/R proximity)
            eq = placement.get("entry_quality", "mid_range")
            if eq == "ideal":
                _m["entry"] = 3.0
            elif eq == "good":
                _m["entry"] = 2.0
            elif eq == "poor":
                _m["entry"] = -1.0
            sr_pts += _m["entry"]

            # R:R quality bonus (small bonus on top of entry)
            rr_q = placement.get("rr_quality", "skip")
            if placement.get("is_fallback_rr"):
                rr_q = "skip"  # Don't reward arbitrary fallback R:R
            if rr_q == "excellent":
                _m["rr"] = 2.0
            elif rr_q == "good":
                _m["rr"] = 1.0
            sr_pts += _m["rr"]

        # Structure direction alignment
        if ms:
            struct_trend = ms.get("structure", "unknown")
            if is_buy and struct_trend == "uptrend":
                _m["struct"] = 2.0
            elif not is_buy and struct_trend == "downtrend":
                _m["struct"] = 2.0
            elif struct_trend not in ("unknown", "ranging"):
                _m["struct"] = -2.0  # against structure
            sr_pts += _m["struct"]

            # BOS confirmation bonus
            bos = ms.get("last_bos")
            if bos:
                bos_dir = bos.get("direction", "")
                if (is_buy and bos_dir == "bullish") or (not is_buy and bos_dir == "bearish"):
                    _m["bos"] = 1.0
            sr_pts += _m["bos"]

            # CHoCH against penalty
            choch = ms.get("last_choch")
            if choch:
                choch_dir = choch.get("direction", "")
                if (is_buy and choch_dir == "bearish") or (not is_buy and choch_dir == "bullish"):
                    _m["choch"] = -2.0
            sr_pts += _m["choch"]

        # Phase 2: Smart Money Concepts bonuses
        fvg = structural_data.get("nearest_fvg")
        ob = structural_data.get("nearest_ob")
        smc = structural_data.get("smc_confluence", 0)
        sweep = structural_data.get("active_sweep_signal")

        if fvg:
            fvg_dir = fvg.get("direction", "")
            if (is_buy and fvg_dir == "bullish") or (not is_buy and fvg_dir == "bearish"):
                _m["fvg"] = 1.0
        sr_pts += _m["fvg"]

        if ob and ob.get("fresh"):
            ob_dir = ob.get("direction", "")
            if (is_buy and ob_dir == "bullish") or (not is_buy and ob_dir == "bearish"):
                _m["ob"] = 1.2
        sr_pts += _m["ob"]

        if smc >= 70:
            _m["smc"] = 0.8
        sr_pts += _m["smc"]

        if sweep:
            sig = sweep.get("signal", "")
            if (is_buy and "long" in sig) or (not is_buy and "short" in sig):
                if "high_probability" in sig:
                    _m["sweep"] = 1.5
                elif "moderate" in sig:
                    _m["sweep"] = 0.8
        sr_pts += _m["sweep"]

        # Phase 3: Confluence bonuses
        vp = structural_data.get("volume_profile")
        if vp:
            poc_pos = vp.get("current_vs_poc", "")
            if (is_buy and poc_pos == "below_poc") or (not is_buy and poc_pos == "above_poc"):
                _m["poc"] = 0.5
        sr_pts += _m["poc"]

        fib = structural_data.get("fibonacci")
        if fib and fib.get("confluence_with"):
            _m["fib"] = 0.8
        sr_pts += _m["fib"]

        mtf = structural_data.get("mtf_confluence")
        if mtf:
            mtf_q = mtf.get("quality", "none")
            if mtf_q in ("maximum", "good"):
                _m["mtf"] = 1.0
        sr_pts += _m["mtf"]

        # Phase 4: Session modifier
        sess = structural_data.get("session_context")
        if sess:
            if sess.get("manipulation_likely"):
                _m["sess"] = -0.5
            elif sess.get("current_session") == "new_york" and sess.get("session_phase") == "mid":
                _m["sess"] = 0.3
        sr_pts += _m["sess"]

        # Hard penalty for bad real R:R (non-fallback, non-unknown)
        if placement:
            _rr_ratio = placement.get("rr_ratio", 0)
            _is_fb = placement.get("is_fallback_rr", False)
            _rr_q = placement.get("rr_quality", "skip")
            if _rr_q == "skip" and not _is_fb:
                _m["rr_pen"] = -3.0
        sr_pts += _m["rr_pen"]

        # XRAY counter-setup Phase 5a — multiply the structural component by
        # the categorical setup_type_confidence so counter setups (×0.7
        # base) don't out-score in-direction setups when raw structural
        # features are similar. Floor at 0.5 so a low-confidence setup
        # still keeps half its structural points (we never zero out
        # legitimate structure). Default 0.85 preserves pre-fix behavior
        # when structural_data is from a legacy producer that doesn't
        # populate setup_type_confidence. Explicit None check so a real
        # 0.0 confidence is honored (and gets the floor) instead of
        # falling back to 0.85 via boolean coercion.
        _raw_confidence = structural_data.get("setup_type_confidence")
        _structural_confidence = (
            float(_raw_confidence) if _raw_confidence is not None else 0.85
        )
        _confidence_factor = max(0.5, min(1.0, _structural_confidence))
        _sr_pts_pre = sr_pts
        sr_pts *= _confidence_factor

        # Clamp to 0-8 range (expanded from 0-3 to let Phase 2/3 bonuses through)
        _clamped = max(0.0, min(8.0, sr_pts))

        # XRAY counter-setup Phase 5a — DEBUG-level visibility into the
        # confidence weighting decision so operators can verify counter
        # setups are being downweighted vs in-direction setups.
        if _structural_confidence < 0.85:
            log.debug(
                "SCORER_QUALITY_DETAIL | structural_confidence={conf:.3f} "
                "factor={factor:.3f} sr_pts_pre={pre:.2f} sr_pts_post={post:.2f}",
                conf=_structural_confidence,
                factor=_confidence_factor,
                pre=_sr_pts_pre,
                post=_clamped,
            )

        return _clamped, _m

    except Exception:
        return 0.0, {}
