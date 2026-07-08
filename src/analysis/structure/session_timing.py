"""X-RAY Phase 12: Institutional Session Timing.

Determines which institutional trading session is active (Asian, London,
New York, Late NY) and its implications for trading — including Asian
range tracking, manipulation assessment, and session-aware recommendations.
"""

from datetime import datetime, timezone

from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    SessionContext,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

# Session boundaries (all UTC hours)
SESSIONS = {
    "asian":   {"start": 0,  "end": 8,  "duration": 480},
    "london":  {"start": 8,  "end": 13, "duration": 300},
    "new_york": {"start": 13, "end": 21, "duration": 480},
    "late_ny": {"start": 21, "end": 24, "duration": 180},
}

NEXT_SESSION = {
    "asian": "london",
    "london": "new_york",
    "new_york": "late_ny",
    "late_ny": "asian",
}


class SessionTimer:
    """Determines current institutional session and trading implications.

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def get_context(
        self,
        current_price: float,
        candles: list | None = None,
        market_structure: MarketStructureResult | None = None,
    ) -> SessionContext:
        """Calculate current session context.

        Args:
            current_price: Current market price.
            candles: OHLCV candles (for Asian range calculation).
            market_structure: Phase 1 market structure (for BOS check).

        Returns:
            SessionContext with session state, timing, and recommendations.
        """
        now = datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute

        # Determine current session
        if 0 <= hour < 8:
            session = "asian"
        elif 8 <= hour < 13:
            session = "london"
        elif 13 <= hour < 21:
            session = "new_york"
        else:
            session = "late_ny"

        sess_info = SESSIONS[session]
        elapsed_min = (hour - sess_info["start"]) * 60 + minute
        remaining_min = sess_info["duration"] - elapsed_min
        pct = elapsed_min / sess_info["duration"] if sess_info["duration"] > 0 else 0

        if pct < 0.30:
            phase = "early"
        elif pct < 0.70:
            phase = "mid"
        else:
            phase = "late"

        # Asian range from candle data
        asian_high, asian_low = self._calc_asian_range(candles, now)

        # Check if Asian range broken
        asian_broken = None
        if asian_high and asian_low and current_price > 0:
            above = current_price > asian_high
            below = current_price < asian_low
            if above and below:
                asian_broken = "both_broken"
            elif above:
                asian_broken = "broken_above"
            elif below:
                asian_broken = "broken_below"

        # Manipulation assessment
        manipulation = False
        if session == "london" and phase == "early" and asian_broken:
            # Manipulation likely if range broken but no BOS confirmation
            has_bos = market_structure and market_structure.last_bos is not None
            if not has_bos:
                manipulation = True

        # Trading recommendation
        recommendation = self._get_recommendation(session, phase, manipulation)

        # Next session countdown
        next_sess = NEXT_SESSION[session]
        next_start = SESSIONS[next_sess]["start"]
        if next_start <= hour:
            next_start += 24
        next_in_min = (next_start - hour) * 60 - minute

        result = SessionContext(
            current_session=session,
            session_phase=phase,
            session_start_utc=f"{sess_info['start']:02d}:00 UTC",
            session_elapsed_minutes=elapsed_min,
            session_remaining_minutes=max(0, remaining_min),
            asian_range_high=asian_high,
            asian_range_low=asian_low,
            asian_range_broken=asian_broken,
            manipulation_likely=manipulation,
            trading_recommendation=recommendation,
            next_session=next_sess,
            next_session_starts_in_minutes=max(0, next_in_min),
        )

        log.debug(
            f"XRAY_SESSION | session={session} phase={phase} "
            f"elapsed={elapsed_min}min remaining={remaining_min}min "
            f"| asian={'${:.0f}-${:.0f}'.format(asian_low, asian_high) if asian_low and asian_high else 'n/a'} "
            f"broken={asian_broken or 'no'} manipulation={'likely' if manipulation else 'no'} "
            f"| rec=\"{recommendation[:60]}\""
        )

        return result

    @staticmethod
    def _calc_asian_range(
        candles: list | None,
        now: datetime,
    ) -> tuple[float | None, float | None]:
        """Calculate today's Asian session range (00:00-08:00 UTC) from candles."""
        if not candles:
            return None, None

        asian_high = None
        asian_low = None

        for c in candles:
            # Check if candle has a timestamp we can use
            ts = getattr(c, 'timestamp', None)
            if ts is None:
                continue

            # Parse timestamp if string
            if isinstance(ts, str):
                try:
                    from datetime import datetime as dt
                    candle_time = dt.fromisoformat(ts.replace('Z', '+00:00'))
                except Exception:
                    continue
            elif isinstance(ts, (int, float)):
                from datetime import datetime as dt
                candle_time = dt.fromtimestamp(ts, tz=timezone.utc)
            elif isinstance(ts, datetime):
                candle_time = ts
            else:
                continue

            # Only today's Asian session (00:00-08:00 UTC)
            if candle_time.date() != now.date():
                continue
            if candle_time.hour >= 8:
                continue

            h = getattr(c, 'high', 0)
            l = getattr(c, 'low', 0)
            if h > 0:
                asian_high = max(asian_high or 0, h)
            if l > 0:
                asian_low = min(asian_low or float('inf'), l)

        if asian_low == float('inf'):
            asian_low = None

        return asian_high, asian_low

    @staticmethod
    def _get_recommendation(session: str, phase: str, manipulation: bool) -> str:
        """Generate trading recommendation based on session state."""
        if session == "asian":
            return "Asian session — low volume, range building. Avoid directional bets."
        elif session == "london":
            if phase == "early":
                if manipulation:
                    return "London early — Asian range broken without BOS. Manipulation likely. Wait for reversal."
                return "London early — manipulation window. Wait for confirmation."
            elif phase == "mid":
                return "London mid — manipulation likely complete. Entries acceptable."
            else:
                return "London late — approaching NY overlap. Manage positions."
        elif session == "new_york":
            if phase == "early":
                return "New York opening — expect volatility increase. Real move forming."
            elif phase == "mid":
                return "New York active — prime trading window. Exploit momentum."
            else:
                return "Late New York — move maturing. Tighten stops."
        else:
            return "Dead zone — very low volume. Avoid trading."
