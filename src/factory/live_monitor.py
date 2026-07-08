"""Live Pattern Monitor: watches for emerging patterns in real-time data."""

from src.config.settings import Settings
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.database.repositories.factory_repo import FactoryRepository
from src.factory.models.factory_types import EmergingPattern

log = get_logger("factory")


class LivePatternMonitor:
    """Monitors real-time data for patterns appearing more frequently than expected.

    Args:
        db: Database manager.
        settings: Application settings.
    """

    def __init__(self, db: DatabaseManager, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = FactoryRepository(db)

    async def check_emerging(self) -> list[EmergingPattern]:
        """Check for emerging patterns in recent data.

        Compares recent pattern occurrence frequency against historical baseline.
        Flags patterns with significantly higher frequency as "emerging".
        """
        cfg = self.settings.factory
        emerging: list[EmergingPattern] = []

        # Get all valid patterns
        try:
            patterns = await self.repo.get_all_patterns(valid_only=True)
        except Exception:
            return emerging

        for pattern in patterns:
            try:
                # Recent occurrences (last 48 hours)
                recent_count = await self.repo.get_occurrence_count(pattern.id, hours=48)

                # Historical baseline: occurrences / days * 2 = expected per 48h
                days_of_data = max(pattern.occurrences / 10, 1)  # rough estimate
                expected_48h = (pattern.occurrences / days_of_data) * 2

                if expected_48h <= 0:
                    continue

                frequency_ratio = recent_count / expected_48h

                # Emerging if firing 3x more than expected
                if frequency_ratio < 3.0 or recent_count < cfg.hot_pattern_threshold_occurrences:
                    continue

                # Calculate recent win rate
                recent = await self.repo.get_recent_occurrences(pattern.id, hours=48)
                wins = sum(1 for o in recent if o.get("outcome") == "win")
                total_resolved = sum(1 for o in recent if o.get("outcome") in ("win", "loss"))
                recent_wr = wins / total_resolved if total_resolved > 0 else pattern.win_rate

                # Determine urgency
                if recent_wr >= cfg.hot_pattern_threshold_win_rate and recent_count >= 8:
                    urgency = "critical"
                elif recent_wr >= 0.65 and recent_count >= 5:
                    urgency = "high"
                elif frequency_ratio >= 5:
                    urgency = "medium"
                else:
                    urgency = "low"

                emerging.append(EmergingPattern(
                    description=f"{pattern.description} (firing {frequency_ratio:.1f}x normal)",
                    conditions=pattern.conditions,
                    recent_occurrences=recent_count,
                    recent_win_rate=recent_wr,
                    urgency=urgency,
                    detected_at=now_utc(),
                ))

            except Exception as e:
                log.debug("Error checking pattern {p}: {err}", p=pattern.id, err=str(e))

        # Sort by urgency
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        emerging.sort(key=lambda e: urgency_order.get(e.urgency, 4))

        if emerging:
            log.info(
                "LiveMonitor: {n} emerging patterns detected ({hot} hot)",
                n=len(emerging),
                hot=sum(1 for e in emerging if e.urgency in ("critical", "high")),
            )

        return emerging
