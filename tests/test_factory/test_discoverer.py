"""Tests for PatternDiscoverer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.factory.discoverer import PatternDiscoverer
from src.factory.models.factory_types import DiscoveredPattern


class TestValidation:
    def test_validate_pattern_passes(self, factory_settings):
        d = PatternDiscoverer.__new__(PatternDiscoverer)
        d.settings = factory_settings
        pattern = DiscoveredPattern(
            id="test", pattern_type="single_var", description="test",
            conditions={}, occurrences=25, win_rate=0.65, profit_factor=1.5,
        )
        assert d._validate_pattern(pattern, factory_settings.factory) is True

    def test_validate_pattern_low_occurrences(self, factory_settings):
        d = PatternDiscoverer.__new__(PatternDiscoverer)
        d.settings = factory_settings
        pattern = DiscoveredPattern(
            id="test", pattern_type="single_var", description="test",
            conditions={}, occurrences=3, win_rate=0.8, profit_factor=2.0,
        )
        assert d._validate_pattern(pattern, factory_settings.factory) is False

    def test_validate_pattern_low_winrate(self, factory_settings):
        d = PatternDiscoverer.__new__(PatternDiscoverer)
        d.settings = factory_settings
        pattern = DiscoveredPattern(
            id="test", pattern_type="single_var", description="test",
            conditions={}, occurrences=30, win_rate=0.45, profit_factor=0.8,
        )
        assert d._validate_pattern(pattern, factory_settings.factory) is False


class TestRanking:
    def test_rank_score(self):
        p1 = DiscoveredPattern(
            id="1", pattern_type="t", description="high",
            conditions={}, occurrences=100, win_rate=0.8, profit_factor=3.0,
        )
        p2 = DiscoveredPattern(
            id="2", pattern_type="t", description="low",
            conditions={}, occurrences=10, win_rate=0.55, profit_factor=1.1,
        )
        score1 = PatternDiscoverer._rank_score(p1)
        score2 = PatternDiscoverer._rank_score(p2)
        assert score1 > score2


class TestDeduplication:
    def test_removes_duplicates(self):
        p1 = DiscoveredPattern(
            id="1", pattern_type="t", description="A",
            conditions={"rsi": 20, "vol": 3}, timeframe="5", direction="long",
        )
        p2 = DiscoveredPattern(
            id="2", pattern_type="t", description="B",
            conditions={"rsi": 20, "vol": 3}, timeframe="5", direction="long",
        )
        deduped = PatternDiscoverer._deduplicate([p1, p2])
        assert len(deduped) == 1

    def test_keeps_different_patterns(self):
        p1 = DiscoveredPattern(
            id="1", pattern_type="t", description="A",
            conditions={"rsi": 20}, timeframe="5", direction="long",
        )
        p2 = DiscoveredPattern(
            id="2", pattern_type="t", description="B",
            conditions={"macd": True, "volume": 5}, timeframe="15", direction="short",
        )
        deduped = PatternDiscoverer._deduplicate([p1, p2])
        assert len(deduped) == 2


class TestModelSerialization:
    def test_pattern_to_dict(self, sample_pattern):
        d = sample_pattern.to_dict()
        assert d["id"] == "pat_test123"
        assert d["win_rate"] == 0.72
        assert d["is_valid"] is True

    def test_pattern_from_dict(self):
        data = {"id": "pat_1", "pattern_type": "micro", "description": "test",
                "conditions": {"x": 1}, "win_rate": 0.7, "occurrences": 50}
        p = DiscoveredPattern.from_dict(data)
        assert p.id == "pat_1"
        assert p.win_rate == 0.7
