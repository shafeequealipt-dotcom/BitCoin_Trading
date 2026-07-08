"""Tests for ConfidenceCalculator."""

from src.intelligence.signals.confidence import ConfidenceCalculator


class TestConfidenceCalculator:
    def setup_method(self):
        self.calc = ConfidenceCalculator()

    def test_all_agree_bullish(self):
        components = {
            "news_sentiment": 0.5,
            "reddit_sentiment": 0.6,
            "fear_greed": 0.3,
            "funding_rate": 0.2,
            "open_interest": 0.4,
            "news_count": 20,
            "reddit_count": 30,
            "data_age_hours": 1.0,
        }
        conf = self.calc.calculate(components)
        assert conf > 0.6

    def test_all_agree_bearish(self):
        components = {
            "news_sentiment": -0.5,
            "reddit_sentiment": -0.6,
            "fear_greed": -0.3,
            "funding_rate": -0.2,
            "open_interest": -0.4,
            "news_count": 20,
            "reddit_count": 30,
            "data_age_hours": 1.0,
        }
        conf = self.calc.calculate(components)
        assert conf > 0.6

    def test_disagreement_lowers_confidence(self):
        agree = {
            "news_sentiment": 0.5,
            "reddit_sentiment": 0.5,
            "fear_greed": 0.5,
            "news_count": 10,
            "reddit_count": 10,
            "data_age_hours": 1.0,
        }
        disagree = {
            "news_sentiment": 0.5,
            "reddit_sentiment": -0.5,
            "fear_greed": 0.1,
            "news_count": 10,
            "reddit_count": 10,
            "data_age_hours": 1.0,
        }
        c_agree = self.calc.calculate(agree)
        c_disagree = self.calc.calculate(disagree)
        assert c_agree > c_disagree

    def test_no_data_low_confidence(self):
        components = {
            "news_sentiment": 0.3,
            "news_count": 0,
            "reddit_count": 0,
            "data_age_hours": 48.0,
        }
        conf = self.calc.calculate(components)
        assert conf < 0.5

    def test_empty_components(self):
        assert self.calc.calculate({}) == 0.0

    def test_old_data_reduces_confidence(self):
        # Fix 3 (sentiment removal, 2026-06-10): confidence no longer reads
        # news_sentiment/reddit_sentiment, so the freshness test uses the
        # genuine direction inputs (fear-greed, funding, open-interest).
        fresh = {
            "fear_greed": 0.5,
            "funding_rate": 0.5,
            "open_interest": 0.5,
            "data_age_hours": 1.0,
        }
        stale = {
            "fear_greed": 0.5,
            "funding_rate": 0.5,
            "open_interest": 0.5,
            "data_age_hours": 48.0,
        }
        assert self.calc.calculate(fresh) > self.calc.calculate(stale)

    def test_result_clamped_0_1(self):
        components = {
            "news_sentiment": 1.0,
            "reddit_sentiment": 1.0,
            "fear_greed": 1.0,
            "funding_rate": 1.0,
            "open_interest": 1.0,
            "news_count": 100,
            "reddit_count": 100,
            "data_age_hours": 0.5,
        }
        conf = self.calc.calculate(components)
        assert 0.0 <= conf <= 1.0
