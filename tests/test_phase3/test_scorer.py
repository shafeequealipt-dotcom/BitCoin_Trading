"""Tests for SentimentScorer: keyword scoring, level mapping, multi-text scoring."""

from src.core.types import SentimentLevel
from src.intelligence.sentiment.scorer import SentimentScorer


class TestScoreText:
    def test_bullish_text(self, sample_scorer):
        score = sample_scorer.score_text("Bitcoin is going to the moon! Bullish breakout!")
        assert score > 0.3

    def test_bearish_text(self, sample_scorer):
        score = sample_scorer.score_text("Crypto crash imminent, dump everything, bearish")
        assert score < -0.3

    def test_neutral_text(self, sample_scorer):
        score = sample_scorer.score_text("Bitcoin traded sideways today with low volume")
        assert -0.2 <= score <= 0.2

    def test_mixed_text(self, sample_scorer):
        score = sample_scorer.score_text("Rally expected but crash possible too")
        # Mixed signals should partially cancel
        assert -0.5 < score < 0.5

    def test_extreme_bullish_clamped(self, sample_scorer):
        text = "moon bullish breakout pump rally ath accumulate hodl parabolic skyrocket"
        score = sample_scorer.score_text(text)
        assert score == 1.0

    def test_extreme_bearish_clamped(self, sample_scorer):
        text = "crash bearish dump rug pull scam dead liquidation capitulation collapse plummet"
        score = sample_scorer.score_text(text)
        assert score == -1.0

    def test_empty_text(self, sample_scorer):
        assert sample_scorer.score_text("") == 0.0

    def test_case_insensitive(self, sample_scorer):
        s1 = sample_scorer.score_text("BULLISH")
        s2 = sample_scorer.score_text("bullish")
        assert s1 == s2


class TestScoreToLevel:
    def test_very_bullish(self, sample_scorer):
        assert sample_scorer.score_to_level(0.7) == SentimentLevel.VERY_BULLISH

    def test_bullish(self, sample_scorer):
        assert sample_scorer.score_to_level(0.3) == SentimentLevel.BULLISH

    def test_neutral(self, sample_scorer):
        assert sample_scorer.score_to_level(0.0) == SentimentLevel.NEUTRAL

    def test_bearish(self, sample_scorer):
        assert sample_scorer.score_to_level(-0.3) == SentimentLevel.BEARISH

    def test_very_bearish(self, sample_scorer):
        assert sample_scorer.score_to_level(-0.7) == SentimentLevel.VERY_BEARISH

    def test_boundary_bullish(self, sample_scorer):
        assert sample_scorer.score_to_level(0.5) == SentimentLevel.VERY_BULLISH

    def test_boundary_bearish(self, sample_scorer):
        # -0.5 >= -0.5 so it's BEARISH; need < -0.5 for VERY_BEARISH
        assert sample_scorer.score_to_level(-0.5) == SentimentLevel.BEARISH
        assert sample_scorer.score_to_level(-0.51) == SentimentLevel.VERY_BEARISH


class TestScoreMultiple:
    def test_multiple_bullish(self, sample_scorer):
        texts = ["bullish breakout!", "moon incoming!", "rally continues"]
        score = sample_scorer.score_multiple(texts)
        assert score > 0.2

    def test_multiple_mixed(self, sample_scorer):
        texts = ["bullish rally", "crash dump"]
        score = sample_scorer.score_multiple(texts)
        assert -0.5 < score < 0.5

    def test_empty_list(self, sample_scorer):
        assert sample_scorer.score_multiple([]) == 0.0


class TestKeywordsFound:
    def test_finds_bullish_keywords(self, sample_scorer):
        found = sample_scorer.get_keywords_found("Bitcoin bullish breakout to the moon!")
        assert "strong_bullish" in found
        assert "moon" in found["strong_bullish"]
        assert "bullish" in found["strong_bullish"]
        assert "breakout" in found["strong_bullish"]

    def test_finds_bearish_keywords(self, sample_scorer):
        found = sample_scorer.get_keywords_found("Crypto crash and dump ahead")
        assert "strong_bearish" in found
        assert "crash" in found["strong_bearish"]

    def test_no_keywords(self, sample_scorer):
        found = sample_scorer.get_keywords_found("Normal market day with trading")
        assert len(found) == 0
