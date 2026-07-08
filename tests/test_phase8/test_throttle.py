"""Tests for AlertThrottle."""

import time

import pytest

from src.core.types import AlertLevel
from src.alerts.throttle import AlertThrottle


class TestThrottle:
    def test_allows_under_limit(self):
        t = AlertThrottle(max_per_hour=10)
        assert t.can_send() is True

    def test_blocks_over_limit(self):
        t = AlertThrottle(max_per_hour=3)
        for _ in range(3):
            t.record_send()
        assert t.can_send() is False

    def test_critical_bypasses(self):
        t = AlertThrottle(max_per_hour=1)
        t.record_send()
        assert t.can_send(AlertLevel.INFO) is False
        assert t.can_send(AlertLevel.CRITICAL) is True

    def test_dedup_blocks_same_content(self):
        t = AlertThrottle()
        h = t.content_hash("same message")
        t.record_content(h)
        assert t.is_duplicate(h) is True

    def test_dedup_allows_different_content(self):
        t = AlertThrottle()
        t.record_content(t.content_hash("message A"))
        assert t.is_duplicate(t.content_hash("message B")) is False

    def test_dedup_expires(self):
        t = AlertThrottle()
        t.dedup_window = 0  # Expire immediately
        h = t.content_hash("old message")
        t.dedup_cache[h] = time.time() - 1
        assert t.is_duplicate(h) is False

    def test_queue(self):
        t = AlertThrottle()
        t.queue_alert({"message": "queued"})
        assert len(t.get_queued()) == 1
        assert len(t.get_queued()) == 0  # Cleared after get

    def test_content_hash_deterministic(self):
        h1 = AlertThrottle.content_hash("hello")
        h2 = AlertThrottle.content_hash("hello")
        assert h1 == h2

    def test_stats(self):
        t = AlertThrottle(max_per_hour=10)
        t.record_send()
        stats = t.get_stats()
        assert stats["alerts_this_hour"] == 1
        assert stats["throttled"] is False
