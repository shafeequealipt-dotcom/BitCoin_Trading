"""X-RAY Phase 3c: Multi-Timeframe Confluence Scorer.

Scores how many independent structural factors align on the same trade
direction. Combines Phase 1 (S/R, structure), Phase 2 (FVG, OB, sweep),
and Phase 3 (VP, Fib) into a 0-10 confluence score.

Base score is computed on single-timeframe (H1) factors. Issue #5 (2026-05-31)
added optional higher-timeframe agreement: when ``score()`` is called with
``higher_tf_views`` (H4/D1 structural summaries from
``StructureEngine.analyze_direction_only``), ``_blend_higher_tf`` blends a
bounded cross-timeframe agreement signal into the 0-10 score. With no views
passed (the default / flag-off path) the score is the legacy H1-only result.
"""

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    FibSwing,
    MarketStructureResult,
    MTFConfluence,
    OrderBlock,
    PriceLevel,
    StructuralPlacement,
    VolumeProfile,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")


class MTFConfluenceScorer:
    """Scores multi-factor confluence from all X-RAY phases.

    Scoring factors (0-10):
      - Structure direction alignment (+2)
      - At structural S/R level (+1)
      - At Order Block (+1)
      - At FVG (+1)
      - Fibonacci confluence (+1)
      - Volume POC supports direction (+1)
      - Entry trigger (BOS/CHoCH) (+1)
      - SMC confluence >= 40 (+1)
      - R:R >= 2.0 (+1)

    Quality classification:
      8-10: "maximum", 5-7: "good", 3-4: "weak", 0-2: "none"

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def score(
        self,
        symbol: str,
        current_price: float,
        direction: str,
        market_structure: MarketStructureResult,
        supports: list[PriceLevel],
        resistances: list[PriceLevel],
        placement: StructuralPlacement | None,
        fvgs: list[FairValueGap],
        order_blocks: list[OrderBlock],
        smc_confluence: int,
        fibonacci: FibSwing | None,
        volume_profile: VolumeProfile | None,
        higher_tf_views: dict | None = None,
    ) -> MTFConfluence:
        """Score multi-factor confluence (0-10).

        Args:
            symbol: Trading pair.
            current_price: Current price.
            direction: Suggested trade direction ("long"/"short").
            market_structure: Phase 1 structure result.
            supports: Phase 1 support levels.
            resistances: Phase 1 resistance levels.
            placement: Phase 1 SL/TP placement.
            fvgs: Phase 2 FVGs.
            order_blocks: Phase 2 OBs.
            smc_confluence: Phase 2 SMC score.
            fibonacci: Phase 3b Fib result.
            volume_profile: Phase 3a VP result.
            higher_tf_views: Issue #5 — optional {tf: TFStructureView} for the
                higher timeframes (H4="240", D1="D"). When None or empty the
                scorer is byte-identical to the legacy H1-only behaviour. When
                present, a bounded cross-TF agreement signal is blended into the
                0-10 score (see _blend_higher_tf).

        Returns:
            MTFConfluence with score, quality, and missing factors.
        """
        score = 0
        missing = []
        contributions = {}

        # Factor 1: Structure direction alignment (+2)
        struct = market_structure.structure
        if direction == "long" and struct == "uptrend":
            score += 2
            contributions["structure"] = 2
        elif direction == "short" and struct == "downtrend":
            score += 2
            contributions["structure"] = 2
        elif struct == "ranging":
            score += 1
            contributions["structure"] = 1
        else:
            missing.append("structure_against")
            contributions["structure"] = 0

        # Factor 2: At structural S/R level (+1)
        at_level = False
        if direction == "long" and supports:
            dist = abs(current_price - supports[0].price) / current_price * 100
            if dist < 2.0:
                score += 1
                at_level = True
                contributions["sr_level"] = 1
        elif direction == "short" and resistances:
            dist = abs(resistances[0].price - current_price) / current_price * 100
            if dist < 2.0:
                score += 1
                at_level = True
                contributions["sr_level"] = 1
        if not at_level:
            missing.append("not_at_sr_level")
            contributions["sr_level"] = 0

        # Factor 3: At Order Block (+1)
        at_ob = False
        expected_dir = "bullish" if direction == "long" else "bearish"
        for ob in order_blocks:
            if ob.direction == expected_dir and ob.fresh:
                dist = abs(ob.midpoint - current_price) / current_price * 100
                if dist < 3.0:
                    score += 1
                    at_ob = True
                    contributions["order_block"] = 1
                    break
        if not at_ob:
            missing.append("no_ob_at_price")
            contributions["order_block"] = 0

        # Factor 4: At FVG (+1)
        at_fvg = False
        for fvg in fvgs:
            if fvg.filled:
                continue
            if fvg.direction == expected_dir:
                dist = abs(fvg.midpoint - current_price) / current_price * 100
                if dist < 2.0:
                    score += 1
                    at_fvg = True
                    contributions["fvg"] = 1
                    break
        if not at_fvg:
            missing.append("no_fvg_at_price")
            contributions["fvg"] = 0

        # Factor 5: Fibonacci confluence (+1)
        if fibonacci and fibonacci.confluence_with:
            score += 1
            contributions["fibonacci"] = 1
        else:
            missing.append("no_fib_confluence")
            contributions["fibonacci"] = 0

        # Factor 6: Volume POC supports direction (+1)
        if volume_profile:
            if (direction == "long" and volume_profile.current_vs_poc == "below_poc") or \
               (direction == "short" and volume_profile.current_vs_poc == "above_poc"):
                score += 1
                contributions["volume_poc"] = 1
            else:
                missing.append("poc_not_supporting")
                contributions["volume_poc"] = 0
        else:
            missing.append("no_volume_profile")
            contributions["volume_poc"] = 0

        # Factor 7: Entry trigger — BOS or CHoCH (+1)
        has_trigger = False
        if market_structure.last_bos:
            bos_dir = market_structure.last_bos.direction
            if (direction == "long" and bos_dir == "bullish") or \
               (direction == "short" and bos_dir == "bearish"):
                score += 1
                has_trigger = True
                contributions["entry_trigger"] = 1
        if not has_trigger:
            missing.append("no_entry_trigger")
            contributions["entry_trigger"] = 0

        # Factor 8: SMC confluence >= 40 (+1)
        if smc_confluence >= 40:
            score += 1
            contributions["smc"] = 1
        else:
            missing.append("smc_low")
            contributions["smc"] = 0

        # Factor 9: R:R >= 2.0 (+1)
        if placement and placement.rr_ratio >= 2.0:
            score += 1
            contributions["rr_ratio"] = 1
        else:
            missing.append("rr_below_2")
            contributions["rr_ratio"] = 0

        # Clamp score (this is the single-timeframe "factor score").
        score = max(0, min(10, score))

        # ====== Issue #5: blend higher-timeframe agreement (gated) ======
        # higher_tf_views is None/empty in the legacy path -> _blend_higher_tf
        # returns the factor score unchanged, so behaviour is byte-identical.
        score, _, _htf_missing, _htf_analyses = self._blend_higher_tf(
            direction, score, higher_tf_views,
        )
        # HTF markers are surfaced additively: they go to missing_factors and to
        # timeframe_analyses (transparency), but NOT into `contributions`, so the
        # legacy factor-based direction_alignment / strongest / weakest are
        # unchanged and nothing downstream that reads them shifts.
        missing.extend(_htf_missing)

        # Quality classification
        if score >= 8:
            quality = "maximum"
        elif score >= 5:
            quality = "good"
        elif score >= 3:
            quality = "weak"
        else:
            quality = "none"

        # Direction alignment
        contributing_factors = sum(1 for v in contributions.values() if v > 0)
        total_factors = len(contributions)
        if contributing_factors == total_factors:
            direction_alignment = "fully_aligned"
        elif contributing_factors >= total_factors * 0.7:
            direction_alignment = "mostly_aligned"
        elif contributing_factors >= total_factors * 0.4:
            direction_alignment = "mixed"
        else:
            direction_alignment = "conflicting"

        # Strongest and weakest
        strongest = max(contributions, key=contributions.get) if contributions else ""
        weakest = min(contributions, key=contributions.get) if contributions else ""

        # Issue 3 (structure confluence, 2026-06-06) — surface each higher TF's OWN
        # directional bias so the scanner's interestingness confluence anchor-count
        # can include H4 and D1 agreement (it previously saw only H1). Empty when
        # the higher-TF feature is off (higher_tf_views None) -> no anchor added.
        _h4_bias = ""
        _d1_bias = ""
        if higher_tf_views:
            _h4_bias = self._tf_bias(higher_tf_views.get("240"))
            _d1_bias = self._tf_bias(higher_tf_views.get("D"))

        result = MTFConfluence(
            timeframe_analyses={**contributions, **_htf_analyses},
            direction_alignment=direction_alignment,
            aligned_direction=direction if score >= 3 else None,
            score=score,
            quality=quality,
            missing_factors=missing,
            strongest_timeframe=strongest,
            weakest_timeframe=weakest,
            h4_bias=_h4_bias,
            d1_bias=_d1_bias,
        )

        log.debug(
            f"XRAY_MTF | sym={symbol} score={score}/10 quality={quality} "
            f"align={direction_alignment} dir={direction} "
            f"factors={contributing_factors}/{total_factors} "
            f"missing={len(missing)}"
        )

        return result

    @staticmethod
    def _tf_bias(view) -> str:
        """Issue 3 — the higher-TF's OWN directional bias, independent of the trade
        direction: uptrend -> 'long', downtrend -> 'short', else its last BOS, else
        '' (ranging/unknown/no-data). Used to add H4/D1 anchors to interestingness."""
        if view is None or not getattr(view, "has_data", False):
            return ""
        struct = getattr(view, "structure", "unknown")
        if struct == "uptrend":
            return "long"
        if struct == "downtrend":
            return "short"
        bos = getattr(view, "last_bos_direction", "") or ""
        if bos == "bullish":
            return "long"
        if bos == "bearish":
            return "short"
        return ""

    @staticmethod
    def _tf_alignment(direction: str, view) -> float:
        """Issue #5: agreement of one higher-TF structure with the H1 trade
        direction. +1 aligned, -1 conflicting; when the higher TF is ranging/
        unknown its last-BOS breaks the tie weakly (+/-0.5); else 0 (neutral)."""
        if direction not in ("long", "short"):
            return 0.0
        struct = getattr(view, "structure", "unknown")
        if struct == "uptrend":
            return 1.0 if direction == "long" else -1.0
        if struct == "downtrend":
            return 1.0 if direction == "short" else -1.0
        bos = getattr(view, "last_bos_direction", "") or ""
        if bos == "bullish":
            return 0.5 if direction == "long" else -0.5
        if bos == "bearish":
            return 0.5 if direction == "short" else -0.5
        return 0.0

    def _blend_higher_tf(
        self, direction: str, factor_score: int, higher_tf_views: dict | None,
    ) -> tuple[int, float, list[str], dict]:
        """Issue #5: blend a BOUNDED higher-timeframe agreement signal into the
        0-10 single-TF factor score.

        Returns (blended_score, htf_agreement, missing_markers, tf_analyses).

        Regression-safety contract: when ``higher_tf_views`` is None/empty, or
        no higher TF carries usable data, the factor score is returned UNCHANGED
        with htf_agreement=0 — byte-identical to the legacy H1-only behaviour.
        D1 is weighted above H4 (the deliberate daily extension). The blend is
        ``round(factor_score * (1 + alpha * agreement))`` clamped to [0,10],
        with alpha = settings.mtf_htf_weight (default 0.25 -> +/-25% max), so the
        score stays on-scale and the calibrated classify_setup thresholds remain
        meaningful — a refinement, not a regime change."""
        tf_analyses: dict = {}
        missing: list[str] = []
        if not higher_tf_views:
            return factor_score, 0.0, missing, tf_analyses
        weights = {"D": 1.0, "240": 0.7}
        agree_num = 0.0
        agree_den = 0.0
        for tf, view in higher_tf_views.items():
            label = "d1" if tf == "D" else ("h4" if tf == "240" else tf)
            if view is None or not getattr(view, "has_data", False):
                missing.append(f"{label}_data_missing")
                continue
            w = weights.get(tf, 0.5)
            a = self._tf_alignment(direction, view)
            agree_num += w * a
            agree_den += w
            tf_analyses[f"htf_{label}"] = a
            if a < 0:
                missing.append(f"{label}_conflict")
        if agree_den <= 0:
            # No usable higher-TF data -> identical to legacy H1-only.
            return factor_score, 0.0, missing, tf_analyses
        htf_agreement = max(-1.0, min(1.0, agree_num / agree_den))
        alpha = float(getattr(self._settings, "mtf_htf_weight", 0.25) or 0.0)
        # The MTF score is an integer 0-10 by contract (classify_setup/scanner
        # thresholds compare against ints). At very low factor scores the bounded
        # +/-alpha adjustment is sub-1.0 and Python's round-half-to-even can absorb
        # it (e.g. fs=2, full agreement -> round(2.5)=2 — no lift); this is an
        # inherent granularity of the integer scale, not a sign error. The blend
        # moves the score at every tier boundary that matters (3/5/8).
        blended = int(round(factor_score * (1.0 + alpha * htf_agreement)))
        blended = max(0, min(10, blended))
        return blended, htf_agreement, missing, tf_analyses
