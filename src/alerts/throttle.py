"""Alert throttling: rate limiting, deduplication, and queuing."""

import hashlib
import re
import time

from src.core.logging import get_logger
from src.core.types import AlertLevel

log = get_logger("alerts")

# CRITICAL-4 fix (2026-05-09) — pre-compiled normalization patterns.
# Order matters: floats first (decimal-anchored) so the integer pattern
# doesn't pre-eat the integer portion of a float.
_NUMERIC_FLOAT_RE = re.compile(r"\d+\.\d+(?:[eE][+-]?\d+)?")
_NUMERIC_INT_RE = re.compile(r"\d+")


class AlertThrottle:
    """Rate-limits alerts to prevent spam and Telegram API bans.

    Args:
        max_per_hour: Maximum alerts allowed per rolling hour.
    """

    def __init__(self, max_per_hour: int = 30) -> None:
        self.max_per_hour = max_per_hour
        self.timestamps: list[float] = []
        self.queued: list[dict] = []
        self.dedup_cache: dict[str, float] = {}
        self.dedup_window: int = 300  # 5 minutes

    def can_send(self, priority: AlertLevel = AlertLevel.INFO) -> bool:
        """Check if an alert can be sent now.

        CRITICAL alerts always bypass the throttle.

        Args:
            priority: Alert priority level.

        Returns:
            True if allowed to send.
        """
        if priority == AlertLevel.CRITICAL:
            return True
        self._clean_old_timestamps()
        return len(self.timestamps) < self.max_per_hour

    def record_send(self) -> None:
        """Record that an alert was sent."""
        self.timestamps.append(time.time())

    def is_duplicate(self, content_hash: str) -> bool:
        """Check if this content was sent recently.

        Args:
            content_hash: Hash of message content.

        Returns:
            True if duplicate within dedup_window.
        """
        self._clean_dedup_cache()
        return content_hash in self.dedup_cache

    def record_content(self, content_hash: str) -> None:
        """Record content hash for dedup tracking."""
        self.dedup_cache[content_hash] = time.time()

    def queue_alert(self, alert: dict) -> None:
        """Queue an alert for later batch sending."""
        self.queued.append(alert)

    def get_queued(self) -> list[dict]:
        """Return and clear the alert queue."""
        q = self.queued[:]
        self.queued.clear()
        return q

    def get_stats(self) -> dict:
        """Get throttle statistics."""
        self._clean_old_timestamps()
        return {
            "alerts_this_hour": len(self.timestamps),
            "max_per_hour": self.max_per_hour,
            "queued_count": len(self.queued),
            "throttled": len(self.timestamps) >= self.max_per_hour,
        }

    @staticmethod
    def content_hash(text: str) -> str:
        """SHA256 hash of message content for dedup (raw, no normalization).

        Kept for back-compatibility and unit tests. Production dedup in
        alert_manager._send uses normalized_content_hash to catch retry
        storms that differ only in numeric details.
        """
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    @staticmethod
    def normalized_content_hash(text: str) -> str:
        """SHA256 hash of message content with numeric values normalized.

        CRITICAL-4 fix (2026-05-09): the audit observed KATUSDT
        BYBIT_DEMO_SET_SL_FAIL retries firing 5x in 28s because the
        Bybit error string includes the changing base_price
        (1017000 → 1017100 → ...). The raw content_hash differed per
        retry so the 5-min dedup window missed all of them.

        This normalized variant replaces every float and integer in the
        message with the literal "#NUM" before hashing, making
        retry-style alerts that differ only in numeric details dedup
        correctly. Tag prefixes, symbol names, and structural keys stay
        intact so genuinely-different alerts produce different hashes.

        Order: floats first (so the integer pattern doesn't pre-eat the
        integer portion of a float like "0.01015569").
        """
        normalized = _NUMERIC_FLOAT_RE.sub("#NUM", text)
        normalized = _NUMERIC_INT_RE.sub("#NUM", normalized)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _clean_old_timestamps(self) -> None:
        """Remove timestamps older than 1 hour."""
        cutoff = time.time() - 3600
        self.timestamps = [t for t in self.timestamps if t > cutoff]

    def _clean_dedup_cache(self) -> None:
        """Remove expired dedup entries."""
        cutoff = time.time() - self.dedup_window
        self.dedup_cache = {k: v for k, v in self.dedup_cache.items() if v > cutoff}
