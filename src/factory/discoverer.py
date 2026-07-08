"""Pattern Discoverer: orchestrates all analyzers and validates results."""

import math

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.factory_repo import FactoryRepository
from src.factory.analyzers.cross_asset import CrossAssetAnalyzer
from src.factory.analyzers.micro_patterns import MicroPatternAnalyzer
from src.factory.analyzers.multi_variable import MultiVariableAnalyzer
from src.factory.analyzers.news_reactive import NewsReactiveAnalyzer
from src.factory.analyzers.sequential import SequentialAnalyzer
from src.factory.analyzers.single_variable import SingleVariableAnalyzer
from src.factory.analyzers.temporal import TemporalAnalyzer
from src.factory.models.factory_types import DiscoveredPattern

log = get_logger("factory")


class PatternDiscoverer:
    """Orchestrates all pattern analyzers and validates discoveries.

    Args:
        db: Database manager.
        settings: Application settings.
    """

    def __init__(self, db: DatabaseManager, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = FactoryRepository(db)
        self.single_var = SingleVariableAnalyzer(db)
        self.multi_var = MultiVariableAnalyzer(db)
        self.sequential = SequentialAnalyzer(db)
        self.cross_asset = CrossAssetAnalyzer(db)
        self.temporal = TemporalAnalyzer(db)
        self.news_reactive = NewsReactiveAnalyzer(db)
        self.micro = MicroPatternAnalyzer(db)

    async def run_full_discovery(
        self, symbols: list[str] | None = None, days: int | None = None,
    ) -> list[DiscoveredPattern]:
        """Run all 7 analyzers on all symbols and return validated patterns."""
        cfg = self.settings.factory
        days = days or cfg.discovery_lookback_days
        symbols = symbols or self.settings.bybit.default_symbols

        all_patterns: list[DiscoveredPattern] = []

        # Run analyzers per symbol
        for symbol in symbols:
            try:
                all_patterns.extend(await self.single_var.analyze(symbol, days))
            except Exception as e:
                log.warning("SingleVar failed for {s}: {err}", s=symbol, err=str(e))

            try:
                all_patterns.extend(await self.multi_var.analyze(symbol, days))
            except Exception as e:
                log.warning("MultiVar failed for {s}: {err}", s=symbol, err=str(e))

            try:
                all_patterns.extend(await self.sequential.analyze(symbol, days))
            except Exception as e:
                log.warning("Sequential failed for {s}: {err}", s=symbol, err=str(e))

            try:
                all_patterns.extend(await self.temporal.analyze(symbol, days))
            except Exception as e:
                log.warning("Temporal failed for {s}: {err}", s=symbol, err=str(e))

            try:
                all_patterns.extend(await self.micro.analyze(symbol, min(days, 7)))
            except Exception as e:
                log.warning("Micro failed for {s}: {err}", s=symbol, err=str(e))

        # Cross-asset analyzer
        try:
            all_patterns.extend(await self.cross_asset.analyze(symbols, days))
        except Exception as e:
            log.warning("CrossAsset failed: {err}", err=str(e))

        # News-reactive analyzer
        try:
            all_patterns.extend(await self.news_reactive.analyze(days))
        except Exception as e:
            log.warning("NewsReactive failed: {err}", err=str(e))

        # Validate patterns
        validated = [p for p in all_patterns if self._validate_pattern(p, cfg)]

        # Rank by composite score
        for p in validated:
            p.is_valid = True

        validated.sort(key=lambda p: self._rank_score(p), reverse=True)

        # Deduplicate similar patterns
        deduped = self._deduplicate(validated)

        # Save to database
        for pattern in deduped[:20]:
            try:
                await self.repo.save_pattern(pattern)
            except Exception as e:
                log.warning("Failed to save pattern: {err}", err=str(e))

        log.info(
            "Discovery: {total} raw, {valid} validated, {dedup} after dedup, saved top {saved}",
            total=len(all_patterns), valid=len(validated),
            dedup=len(deduped), saved=min(len(deduped), 20),
        )
        return deduped[:20]

    async def run_quick_discovery(
        self, symbols: list[str] | None = None, days: int = 7,
    ) -> list[DiscoveredPattern]:
        """Fast version: only micro + single_var + temporal."""
        symbols = symbols or self.settings.bybit.default_symbols
        all_patterns: list[DiscoveredPattern] = []

        for symbol in symbols:
            try:
                all_patterns.extend(await self.single_var.analyze(symbol, days))
                all_patterns.extend(await self.temporal.analyze(symbol, days))
                all_patterns.extend(await self.micro.analyze(symbol, days))
            except Exception as e:
                log.warning("Quick discovery failed for {s}: {err}", s=symbol, err=str(e))

        cfg = self.settings.factory
        validated = [p for p in all_patterns if self._validate_pattern(p, cfg)]
        for p in validated:
            p.is_valid = True

        return validated[:10]

    def _validate_pattern(self, pattern: DiscoveredPattern, cfg) -> bool:
        """Check if a pattern passes validation criteria."""
        if pattern.occurrences < cfg.min_pattern_occurrences:
            return False
        if pattern.win_rate < cfg.min_win_rate:
            return False
        if pattern.profit_factor > 0 and pattern.profit_factor < cfg.min_profit_factor:
            return False
        return True

    @staticmethod
    def _rank_score(pattern: DiscoveredPattern) -> float:
        """Compute a ranking score for sorting patterns."""
        wr = pattern.win_rate
        pf = max(pattern.profit_factor, 0.1)
        occ = max(pattern.occurrences, 1)
        return wr * pf * math.log(occ + 1)

    @staticmethod
    def _deduplicate(patterns: list[DiscoveredPattern]) -> list[DiscoveredPattern]:
        """Remove patterns with >80% overlapping conditions."""
        if not patterns:
            return patterns

        kept: list[DiscoveredPattern] = []
        for p in patterns:
            is_dup = False
            for existing in kept:
                if p.direction == existing.direction and p.timeframe == existing.timeframe:
                    # Check condition overlap
                    p_keys = set(p.conditions.keys())
                    e_keys = set(existing.conditions.keys())
                    if p_keys and e_keys:
                        overlap = len(p_keys & e_keys) / max(len(p_keys | e_keys), 1)
                        if overlap > 0.8:
                            is_dup = True
                            break
            if not is_dup:
                kept.append(p)
        return kept
