"""Confidence score calculator for trading signals.

Evaluates agreement/disagreement across intelligence sources and
adjusts confidence based on data freshness and volume.
"""

from src.core.logging import get_logger
from src.core.utils import clamp

log = get_logger("intelligence")


class ConfidenceCalculator:
    """Calculates confidence scores for trading signals.

    Considers source agreement, data freshness, and data volume.
    """

    def calculate(self, components: dict) -> float:
        """Calculate a confidence score from component scores.

        Args:
            components: Dict with keys:
                - fear_greed: float [-1, 1] (normalized from 0-100)
                - funding_rate: float [-1, 1] (normalized)
                - open_interest: float [-1, 1] (normalized)
                - data_age_hours: float (age in hours of the OLDEST
                  confidence input — conservative "weakest-link" freshness
                  semantic; populated by SignalGenerator._compute_data_age_hours)
                - volume_surge_ratio: float (trading-volume last-5-min /
                  20-period-avg; populated by
                  SignalGenerator._compute_volume_surge_ratio)

        Returns:
            Confidence score from 0.0 to 1.0.
        """
        scores = []
        # Fix 3 (sentiment removal, 2026-06-10): news_sentiment/reddit_sentiment
        # dropped — sentiment is severed from the signal. fear-greed, funding and
        # open-interest are the genuine direction-agreement inputs.
        for key in ("fear_greed", "funding_rate", "open_interest"):
            val = components.get(key)
            if val is not None:
                scores.append(val)

        if not scores:
            return 0.0

        # Agreement factor: how much sources agree on direction
        agreement = self._agreement_factor(scores)

        # Magnitude factor: stronger signals = higher confidence
        magnitude = self._magnitude_factor(scores)

        # Data volume factor: more data = higher confidence
        volume = self._volume_factor(components)

        # Data freshness factor: older data = lower confidence
        freshness = self._freshness_factor(components)

        # Weighted combination
        confidence = (
            agreement * 0.40
            + magnitude * 0.25
            + volume * 0.20
            + freshness * 0.15
        )

        return clamp(confidence, 0.0, 1.0)

    def _agreement_factor(self, scores: list[float]) -> float:
        """How much the sources agree on direction.

        All positive or all negative = high agreement.
        Mixed signals = low agreement.
        """
        if len(scores) < 2:
            return 0.5

        positive = sum(1 for s in scores if s > 0.05)
        negative = sum(1 for s in scores if s < -0.05)
        total = len(scores)

        if total == 0:
            return 0.5

        # Ratio of dominant direction
        dominant = max(positive, negative)
        return dominant / total

    def _magnitude_factor(self, scores: list[float]) -> float:
        """Average absolute magnitude of scores.

        Stronger scores in any direction = more confidence in the signal.
        """
        if not scores:
            return 0.0
        avg_abs = sum(abs(s) for s in scores) / len(scores)
        return min(avg_abs, 1.0)

    def _volume_factor(self, components: dict) -> float:
        """Trading volume factor: last 5-min volume vs 20-period average.

        Phase 3 (Stage-1/2 fix): prior to this the method consumed
        ``news_count + reddit_count`` — a sentiment-data-availability
        proxy. With Reddit globally disabled and Finnhub covering only
        majors, 29 of 32 coins scored ``total == 0`` and floored to
        0.3, pinning 20 % of the confidence formula at a near-constant
        for altcoins. The method now reads ``volume_surge_ratio``
        populated by ``SignalGenerator._compute_volume_surge_ratio``:
        a real measurement of market activity.

        Thresholds:
            < 0.5  -> 0.3  (volume contraction — low conviction)
            < 1.5  -> 0.5  (normal / slightly elevated)
            < 2.5  -> 0.7  (meaningful surge)
            >= 2.5 -> 1.0  (extreme surge — high conviction)

        Falls back to 0.5 (neutral) when the ratio is missing (e.g. the
        signal was computed before the first 5-min kline landed).
        """
        ratio = components.get("volume_surge_ratio")
        if ratio is None:
            return 0.5
        try:
            r = float(ratio)
        except (TypeError, ValueError):
            return 0.5
        if r < 0.5:
            return 0.3
        if r < 1.5:
            return 0.5
        if r < 2.5:
            return 0.7
        return 1.0

    def _freshness_factor(self, components: dict) -> float:
        """Data freshness: recent data = higher confidence.

        Scales from 0.3 (very old) to 1.0 (very fresh).
        """
        age_hours = components.get("data_age_hours", 24.0)

        if age_hours <= 1:
            return 1.0
        if age_hours <= 6:
            return 0.8
        if age_hours <= 12:
            return 0.6
        if age_hours <= 24:
            return 0.4
        return 0.3
