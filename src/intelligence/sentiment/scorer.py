"""Sentiment scoring engine: keyword-based text scoring.

Designed with a base class for extensibility — future ML or Claude API
scorers can inherit BaseSentimentScorer without changing any caller code.
"""

import re
from abc import ABC, abstractmethod

from src.core.logging import get_logger
from src.core.types import SentimentLevel
from src.core.utils import clamp

log = get_logger("intelligence")


class BaseSentimentScorer(ABC):
    """Abstract base class for sentiment scorers.

    All scorer implementations must provide score_text() and score_to_level().
    """

    @abstractmethod
    def score_text(self, text: str) -> float:
        """Score a text string for sentiment.

        Args:
            text: Input text.

        Returns:
            Score from -1.0 (very bearish) to 1.0 (very bullish).
        """
        ...

    @abstractmethod
    def score_to_level(self, score: float) -> SentimentLevel:
        """Map a numeric score to a SentimentLevel enum.

        Args:
            score: Numeric score [-1.0, 1.0].

        Returns:
            SentimentLevel enum value.
        """
        ...


class SentimentScorer(BaseSentimentScorer):
    """Keyword-based sentiment scorer for crypto text.

    Scans text for bullish/bearish keywords and computes a weighted score.
    """

    STRONG_BULLISH: list[str] = [
        "moon", "bullish", "breakout", "pump", "rally", "ath", "all time high",
        "accumulate", "buy the dip", "hodl", "to the moon", "massive gains",
        "parabolic", "skyrocket", "unstoppable",
    ]
    MODERATE_BULLISH: list[str] = [
        "uptrend", "support held", "bounce", "recovery", "growing", "adoption",
        "institutional buying", "etf approved", "halving", "golden cross",
        "higher highs",
    ]
    STRONG_BEARISH: list[str] = [
        "crash", "bearish", "dump", "rug pull", "scam", "dead",
        "sell everything", "short", "liquidation", "capitulation", "ponzi",
        "fraud", "collapse", "plummet",
    ]
    MODERATE_BEARISH: list[str] = [
        "downtrend", "resistance", "correction", "overvalued", "regulation",
        "ban", "sec lawsuit", "death cross", "lower lows", "whale selling",
        "exit scam",
    ]
    STRONG_FEAR: list[str] = [
        "fear", "panic", "blood", "disaster", "crisis", "bubble burst",
        "black swan",
    ]
    STRONG_GREED: list[str] = [
        "greed", "fomo", "euphoria", "lambo", "retire early",
        "generational wealth",
    ]

    WEIGHTS: dict[str, float] = {
        "strong_bullish": 0.3,
        "moderate_bullish": 0.15,
        "strong_bearish": -0.3,
        "moderate_bearish": -0.15,
        "strong_fear": -0.25,
        "strong_greed": 0.2,
    }

    def score_text(self, text: str) -> float:
        """Score a text string for crypto sentiment.

        Scans for all keyword categories, accumulates weighted score,
        and clamps to [-1.0, 1.0].

        Args:
            text: Input text (headline, post title, etc.).

        Returns:
            Sentiment score from -1.0 to 1.0.
        """
        lower = text.lower()
        score = 0.0

        for kw in self.STRONG_BULLISH:
            if kw in lower:
                score += self.WEIGHTS["strong_bullish"]
        for kw in self.MODERATE_BULLISH:
            if kw in lower:
                score += self.WEIGHTS["moderate_bullish"]
        for kw in self.STRONG_BEARISH:
            if kw in lower:
                score += self.WEIGHTS["strong_bearish"]
        for kw in self.MODERATE_BEARISH:
            if kw in lower:
                score += self.WEIGHTS["moderate_bearish"]
        for kw in self.STRONG_FEAR:
            if kw in lower:
                score += self.WEIGHTS["strong_fear"]
        for kw in self.STRONG_GREED:
            if kw in lower:
                score += self.WEIGHTS["strong_greed"]

        return clamp(score, -1.0, 1.0)

    def score_to_level(self, score: float) -> SentimentLevel:
        """Map a numeric score to a SentimentLevel.

        Args:
            score: Score from -1.0 to 1.0.

        Returns:
            SentimentLevel enum.
        """
        if score >= 0.5:
            return SentimentLevel.VERY_BULLISH
        if score >= 0.2:
            return SentimentLevel.BULLISH
        if score >= -0.2:
            return SentimentLevel.NEUTRAL
        if score >= -0.5:
            return SentimentLevel.BEARISH
        return SentimentLevel.VERY_BEARISH

    def score_multiple(self, texts: list[str]) -> float:
        """Score multiple texts and return a weighted average.

        Longer texts receive slightly more weight.

        Args:
            texts: List of text strings.

        Returns:
            Weighted average score [-1.0, 1.0].
        """
        if not texts:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for text in texts:
            score = self.score_text(text)
            weight = 1.0 + min(len(text) / 500.0, 1.0)  # 1.0 to 2.0
            weighted_sum += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0
        return clamp(weighted_sum / total_weight, -1.0, 1.0)

    def get_keywords_found(self, text: str) -> dict[str, list[str]]:
        """Return which keywords were found in the text.

        Useful for debugging and transparency.

        Args:
            text: Input text.

        Returns:
            Dict mapping category names to lists of matched keywords.
        """
        lower = text.lower()
        found: dict[str, list[str]] = {
            "strong_bullish": [],
            "moderate_bullish": [],
            "strong_bearish": [],
            "moderate_bearish": [],
            "strong_fear": [],
            "strong_greed": [],
        }

        for kw in self.STRONG_BULLISH:
            if kw in lower:
                found["strong_bullish"].append(kw)
        for kw in self.MODERATE_BULLISH:
            if kw in lower:
                found["moderate_bullish"].append(kw)
        for kw in self.STRONG_BEARISH:
            if kw in lower:
                found["strong_bearish"].append(kw)
        for kw in self.MODERATE_BEARISH:
            if kw in lower:
                found["moderate_bearish"].append(kw)
        for kw in self.STRONG_FEAR:
            if kw in lower:
                found["strong_fear"].append(kw)
        for kw in self.STRONG_GREED:
            if kw in lower:
                found["strong_greed"].append(kw)

        return {k: v for k, v in found.items() if v}
