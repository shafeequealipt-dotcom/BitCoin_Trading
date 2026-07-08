"""X-RAY Phase 11: Smart Coin Selection (Setup Scanner).

Ranks all analyzed coins by structural setup quality, producing a list
of the best tradeable setups and a skip list. Replaces "scan everything
equally" with "find the best setups RIGHT NOW."
"""

from src.analysis.structure.models.structure_types import (
    SessionContext,
    StructuralAnalysis,
    StructuralSetup,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

MAX_SETUPS = 12  # Maximum setups to present to Claude (increased for full market)
MIN_QUALIFYING_CRITERIA = 3  # Must pass at least 3 of 6 criteria


class SetupScanner:
    """Ranks coins by structural setup quality.

    Reads all StructuralAnalysis from cache, evaluates each against
    qualification criteria, ranks by composite score, returns top N
    setups and a skip list.

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def scan(
        self,
        analyses: dict[str, StructuralAnalysis],
        session: SessionContext | None = None,
    ) -> tuple[list[StructuralSetup], list[str]]:
        """Scan all analyses and rank by setup quality.

        Args:
            analyses: {symbol: StructuralAnalysis} from cache.
            session: Current session context (for timing penalties).

        Returns:
            (ranked_setups, skip_list) — top setups sorted by score,
            and list of skipped symbols.
        """
        if not analyses:
            return [], []

        setups: list[StructuralSetup] = []
        skip_list: list[str] = []

        for symbol, analysis in analyses.items():
            qualification = self._evaluate_qualification(analysis, session)
            criteria_passed = sum(1 for v in qualification.values() if v)

            if criteria_passed >= MIN_QUALIFYING_CRITERIA:
                setup = self._build_setup(symbol, analysis, qualification, session)
                setups.append(setup)
            else:
                skip_list.append(symbol)

        # Sort by ranking_score descending
        setups.sort(key=lambda s: s.ranking_score, reverse=True)

        # Assign ranks and limit
        for i, setup in enumerate(setups[:MAX_SETUPS]):
            setup.rank = i + 1

        ranked = setups[:MAX_SETUPS]
        # Remaining qualified but not top N go to skip
        for s in setups[MAX_SETUPS:]:
            skip_list.append(s.symbol)

        if ranked:
            top_desc = " ".join(f"#{s.rank}={s.symbol}({s.ranking_score:.0f})" for s in ranked[:3])
        else:
            top_desc = "none"

        log.info(
            f"XRAY_SCANNER | total={len(analyses)} qualified={len(setups)} "
            f"skipped={len(skip_list)} | {top_desc}"
        )

        return ranked, skip_list

    @staticmethod
    def _evaluate_qualification(
        analysis: StructuralAnalysis,
        session: SessionContext | None,
    ) -> dict[str, bool]:
        """Evaluate setup qualification criteria."""
        qual = {}

        # 1. Price at structural level (entry quality ideal or good)
        sp = analysis.structural_placement
        qual["at_level"] = sp is not None and sp.entry_quality in ("ideal", "good")

        # 2. Structure supports direction
        struct = analysis.market_structure.structure
        direction = analysis.suggested_direction
        qual["structure_aligned"] = (
            (direction == "long" and struct == "uptrend") or
            (direction == "short" and struct == "downtrend") or
            struct == "ranging"
        )

        # 3. R:R adequate (>= 2.0)
        qual["rr_adequate"] = sp is not None and sp.rr_ratio >= 2.0

        # 4. Smart Money presence (FVG or fresh OB or swept liquidity)
        qual["smc_present"] = bool(
            analysis.nearest_fvg or
            analysis.nearest_ob or
            analysis.active_sweep_signal
        )

        # 5. Confluence score >= 5
        mtf = analysis.mtf_confluence
        qual["confluence_good"] = mtf is not None and mtf.score >= 5

        # 6. Session favorable
        if session:
            qual["session_favorable"] = not (
                session.manipulation_likely or
                session.current_session == "late_ny"
            )
        else:
            qual["session_favorable"] = True  # neutral if no session data

        return qual

    def _build_setup(
        self,
        symbol: str,
        analysis: StructuralAnalysis,
        qualification: dict[str, bool],
        session: SessionContext | None,
    ) -> StructuralSetup:
        """Build a StructuralSetup from analysis + qualification."""
        sp = analysis.structural_placement
        mtf = analysis.mtf_confluence

        # Active signals
        active = []
        missing = []
        if qualification.get("at_level"):
            active.append("at_level")
        else:
            missing.append("not_at_level")
        if qualification.get("structure_aligned"):
            active.append("structure_aligned")
        else:
            missing.append("structure_against")
        if qualification.get("rr_adequate"):
            active.append(f"rr_{sp.rr_ratio:.1f}" if sp else "rr_ok")
        else:
            missing.append("poor_rr")
        if analysis.nearest_fvg:
            active.append("fvg_nearby")
        if analysis.nearest_ob and analysis.nearest_ob.fresh:
            active.append("ob_fresh")
        if analysis.active_sweep_signal:
            active.append("liquidity_swept")
        if analysis.fibonacci and analysis.fibonacci.confluence_with:
            active.append("fib_confluent")
        if analysis.volume_profile:
            active.append(f"poc_{analysis.volume_profile.current_vs_poc}")
        if not qualification.get("smc_present"):
            missing.append("no_smc")
        if not qualification.get("confluence_good"):
            missing.append("low_confluence")

        # Ranking score
        ranking = self._calc_ranking_score(analysis, qualification, session)

        # Session favorable
        sess_fav = qualification.get("session_favorable", True)

        # Description
        desc_parts = [f"{symbol} {analysis.suggested_direction or '?'}"]
        if active:
            desc_parts.append(f"signals=[{','.join(active[:5])}]")
        desc_parts.append(f"RR=1:{sp.rr_ratio:.1f}" if sp else "")
        desc_parts.append(f"confl={mtf.score}/10" if mtf else "")
        description = " ".join(p for p in desc_parts if p)

        return StructuralSetup(
            symbol=symbol,
            rank=0,  # assigned after sorting
            setup_score=analysis.setup_score,
            setup_quality=analysis.setup_quality,
            confluence_score=mtf.score if mtf else 0,
            confluence_quality=mtf.quality if mtf else "none",
            total_confluence_factors=analysis.total_confluence_factors,
            suggested_direction=analysis.suggested_direction,
            entry_quality=sp.entry_quality if sp else "mid_range",
            rr_ratio=sp.rr_ratio if sp else 0.0,
            rr_quality=sp.rr_quality if sp else "skip",
            structural_sl=sp.structural_sl if sp else 0.0,
            structural_tp=sp.structural_tp if sp else 0.0,
            active_signals=active,
            missing_signals=missing,
            setup_description=description,
            session_favorable=sess_fav,
            ranking_score=ranking,
        )

    @staticmethod
    def _calc_ranking_score(
        analysis: StructuralAnalysis,
        qualification: dict[str, bool],
        session: SessionContext | None,
    ) -> float:
        """Calculate composite ranking score for sorting."""
        score = 0.0

        # Base structural quality (0-25)
        score += analysis.setup_score * 0.25

        # MTF confluence (0-25)
        mtf = analysis.mtf_confluence
        if mtf:
            score += mtf.score * 2.5

        # SMC bonus (0-25)
        smc = 0.0
        if analysis.nearest_ob and analysis.nearest_ob.fresh:
            smc += 10
        if analysis.nearest_fvg:
            smc += 8
        if analysis.active_sweep_signal:
            smc += 7
        score += min(25.0, smc)

        # R:R bonus (0-15)
        sp = analysis.structural_placement
        if sp:
            if sp.rr_ratio >= 4.0:
                score += 15
            elif sp.rr_ratio >= 3.0:
                score += 10
            elif sp.rr_ratio >= 2.0:
                score += 5

        # Session modifier (-10 to +5)
        if session:
            sess = session.current_session
            phase = session.session_phase
            if sess == "new_york" and phase == "mid":
                score += 5
            elif sess == "london" and phase == "mid":
                score += 5
            elif sess == "late_ny":
                score -= 5
            elif sess == "london" and phase == "early" and session.manipulation_likely:
                score -= 10

        return round(score, 2)
