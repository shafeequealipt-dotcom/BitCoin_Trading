"""M16 — Market emotional state detection.

Maps the Crypto Fear & Greed Index to a discrete MarketEmotion and
provides contrarian sizing multipliers:

  Index ranges:
    <15  = PANIC      15-30 = FEAR       30-60 = NEUTRAL
    60-75 = OPTIMISM   75-90 = GREED      >90  = EUPHORIA

  Contrarian sizing:
    PANIC   + BUY  = 1.3  (buy when others panic)
    PANIC   + SELL = 0.5  (don't sell into panic)
    EUPHORIA + BUY  = 0.5  (don't chase euphoria)
    EUPHORIA + SELL = 1.3  (sell into euphoria)
    NEUTRAL         = 1.0

Results are cached for 5 minutes to avoid excessive API calls.
"""

from __future__ import annotations

import time

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import MarketEmotion

log = get_logger("fund_manager")

# ── Fear & Greed → Emotion mapping ─────────────────────────────────
_EMOTION_THRESHOLDS: list[tuple[int, MarketEmotion]] = [
    (15, MarketEmotion.PANIC),
    (30, MarketEmotion.FEAR),
    (60, MarketEmotion.NEUTRAL),
    (75, MarketEmotion.OPTIMISM),
    (90, MarketEmotion.GREED),
]
# Anything above 90 → EUPHORIA (handled as default)

# ── Contrarian multipliers: (emotion, side) → multiplier ───────────
_CONTRARIAN_TABLE: dict[tuple[MarketEmotion, str], float] = {
    (MarketEmotion.PANIC, "BUY"): 1.3,
    (MarketEmotion.PANIC, "SELL"): 0.5,
    (MarketEmotion.FEAR, "BUY"): 1.15,
    (MarketEmotion.FEAR, "SELL"): 0.7,
    (MarketEmotion.NEUTRAL, "BUY"): 1.0,
    (MarketEmotion.NEUTRAL, "SELL"): 1.0,
    (MarketEmotion.OPTIMISM, "BUY"): 0.85,
    (MarketEmotion.OPTIMISM, "SELL"): 1.1,
    (MarketEmotion.GREED, "BUY"): 0.7,
    (MarketEmotion.GREED, "SELL"): 1.15,
    (MarketEmotion.EUPHORIA, "BUY"): 0.5,
    (MarketEmotion.EUPHORIA, "SELL"): 1.3,
}

# Cache TTL
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _index_to_emotion(value: int) -> MarketEmotion:
    """Convert a Fear & Greed index value (0-100) to MarketEmotion."""
    for threshold, emotion in _EMOTION_THRESHOLDS:
        if value < threshold:
            return emotion
    return MarketEmotion.EUPHORIA


class MarketEmotionDetector:
    """Detects market emotional state from Fear & Greed Index."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}
        self._cached_emotion: MarketEmotion = MarketEmotion.NEUTRAL
        self._cached_value: int = 50
        self._cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    async def detect(self) -> MarketEmotion:
        """Detect the current market emotion from Fear & Greed Index.

        Returns:
            MarketEmotion enum value.  Defaults to NEUTRAL on failure.
        """
        # Return cached if fresh enough
        now = time.monotonic()
        if now - self._cache_time < _CACHE_TTL_SECONDS and self._cache_time > 0:
            return self._cached_emotion

        try:
            fg_client = self._services.get("fear_greed")
            if fg_client is None:
                log.debug("EmotionDetector: fear_greed service unavailable, defaulting NEUTRAL")
                return MarketEmotion.NEUTRAL

            fg_data = await fg_client.get_latest()
            if fg_data is None:
                log.debug("EmotionDetector: no F&G data available, defaulting NEUTRAL")
                return MarketEmotion.NEUTRAL

            value = fg_data.value
            emotion = _index_to_emotion(value)

            # Update cache
            self._cached_emotion = emotion
            self._cached_value = value
            self._cache_time = now

            log.info(
                "EmotionDetector: F&G={val}, emotion={emo}",
                val=value, emo=emotion.value,
            )
            return emotion

        except Exception:
            log.warning("EmotionDetector: error fetching F&G, using cached={emo}",
                        emo=self._cached_emotion.value)
            return self._cached_emotion

    # ------------------------------------------------------------------
    # Multiplier
    # ------------------------------------------------------------------

    @staticmethod
    def get_multiplier(emotion: MarketEmotion, side: str) -> float:
        """Get a contrarian sizing multiplier for the given emotion and trade side.

        Args:
            emotion: Current market emotion.
            side: Trade direction — "BUY" or "SELL" (case-insensitive).

        Returns:
            Sizing multiplier (0.5 - 1.3).
        """
        side_upper = side.upper()
        # Normalize "Buy"/"Sell" from the Side enum to "BUY"/"SELL"
        if side_upper in ("BUY", "SELL"):
            key = (emotion, side_upper)
        else:
            key = (emotion, "BUY")  # safe default

        mult = _CONTRARIAN_TABLE.get(key, 1.0)
        return mult

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return current cached state."""
        return {
            "emotion": self._cached_emotion.value,
            "fear_greed_value": self._cached_value,
            "cache_age_seconds": round(time.monotonic() - self._cache_time, 1)
            if self._cache_time > 0 else None,
        }
