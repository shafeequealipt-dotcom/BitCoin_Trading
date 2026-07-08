"""M2: Quality-Based Position Sizer.

Determines position size as a percentage of trading capital using trade
quality grade, win/loss streak, daily PnL, and consensus strength.
"""

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountLevel

log = get_logger("fund_manager")

# ── Base percentages by grade and level ──────────────────────────────────────
# Structure: grade -> {level: base_pct}
BASE_PCT_TABLE: dict[str, dict[AccountLevel, float]] = {
    "A+": {
        AccountLevel.ROOKIE: 5.0,
        AccountLevel.PROVEN: 8.0,
        AccountLevel.VETERAN: 10.0,
        AccountLevel.ELITE: 12.0,
        AccountLevel.MASTER: 15.0,
    },
    "A": {
        AccountLevel.ROOKIE: 4.0,
        AccountLevel.PROVEN: 6.0,
        AccountLevel.VETERAN: 8.0,
        AccountLevel.ELITE: 10.0,
        AccountLevel.MASTER: 12.0,
    },
    "B": {
        AccountLevel.ROOKIE: 2.0,
        AccountLevel.PROVEN: 3.0,
        AccountLevel.VETERAN: 5.0,
        AccountLevel.ELITE: 6.0,
        AccountLevel.MASTER: 8.0,
    },
    "C": {
        AccountLevel.ROOKIE: 1.0,
        AccountLevel.PROVEN: 2.0,
        AccountLevel.VETERAN: 3.0,
        AccountLevel.ELITE: 4.0,
        AccountLevel.MASTER: 5.0,
    },
    "D": {
        AccountLevel.ROOKIE: 0.5,
        AccountLevel.PROVEN: 1.0,
        AccountLevel.VETERAN: 1.5,
        AccountLevel.ELITE: 2.0,
        AccountLevel.MASTER: 3.0,
    },
}


class PositionSizer:
    """Quality-based position sizing with streak and PnL adjustments."""

    def __init__(self, settings=None) -> None:
        self.settings = settings

    def get_base_pct(self, grade: str, level: AccountLevel) -> float:
        """Return base position size percent for a grade at a given level.

        Args:
            grade: Trade quality grade (A+, A, B, C, D).
            level: Current account level.

        Returns:
            Base position size as percent of trading capital.
        """
        grade_upper = grade.upper().strip()
        grade_table = BASE_PCT_TABLE.get(grade_upper)

        if grade_table is None:
            log.debug(
                "Unknown grade {grade}, defaulting to D sizing",
                grade=grade_upper,
            )
            grade_table = BASE_PCT_TABLE["D"]

        pct = grade_table.get(level, grade_table[AccountLevel.ROOKIE])
        log.debug(
            "Base sizing: grade={grade} level={level} -> {pct}%",
            grade=grade_upper,
            level=level.value,
            pct=pct,
        )
        return pct

    def get_streak_multiplier(self, streak: int) -> float:
        """Return sizing multiplier based on win/loss streak.

        Positive streak = consecutive wins, negative = consecutive losses.

        Args:
            streak: Current streak (positive=wins, negative=losses).

        Returns:
            Multiplier to apply to position size.
        """
        if streak >= 5:
            mult = 1.3
        elif streak >= 3:
            mult = 1.15
        elif streak <= -5:
            mult = 0.4
        elif streak <= -3:
            mult = 0.6
        elif streak <= -1:
            mult = 0.85
        else:
            mult = 1.0

        if streak != 0:
            log.debug(
                "Streak multiplier: streak={streak} -> {mult}",
                streak=streak,
                mult=mult,
            )
        return mult

    def get_pnl_multiplier(self, daily_pnl_pct: float) -> float:
        """Return sizing multiplier based on today's PnL percentage.

        Scales down when deep in profit (protect gains) or loss (reduce risk).

        Args:
            daily_pnl_pct: Today's cumulative PnL as percentage.

        Returns:
            Multiplier to apply to position size.
        """
        # Thresholds rescaled for a 1%/day target + capital preservation.
        # In profit but under target -> keep pushing (1.1x). Target hit ->
        # protect gains (0.6x). In any loss -> cut risk hard, scaled to how
        # close we are to the -1% daily halt.
        if daily_pnl_pct >= 1.0:
            mult = 0.6
        elif daily_pnl_pct >= 0.0:
            mult = 1.1
        elif daily_pnl_pct <= -1.0:
            mult = 0.2
        elif daily_pnl_pct <= -0.6:
            mult = 0.4
        elif daily_pnl_pct <= -0.3:
            mult = 0.6
        else:
            mult = 0.9

        log.debug(
            "PnL multiplier: daily_pnl={pnl:.1f}% -> {mult}",
            pnl=daily_pnl_pct,
            mult=mult,
        )
        return mult

    def get_consensus_multiplier(self, consensus: str) -> float:
        """Return sizing multiplier based on signal consensus strength.

        Args:
            consensus: Consensus strength (STRONG, GOOD, WEAK, LEAN, CONFLICT).

        Returns:
            Multiplier to apply to position size. CONFLICT returns 0.0 (no trade).
        """
        consensus_upper = consensus.upper().strip()

        multipliers = {
            "STRONG": 1.25,
            "GOOD": 1.0,
            "WEAK": 0.7,
            "LEAN": 0.5,
            "CONFLICT": 0.0,
        }

        mult = multipliers.get(consensus_upper, 1.0)

        if consensus_upper == "CONFLICT":
            log.warning("Consensus is CONFLICT, sizing multiplier set to 0.0 (no trade)")
        else:
            log.debug(
                "Consensus multiplier: {consensus} -> {mult}",
                consensus=consensus_upper,
                mult=mult,
            )

        return mult
