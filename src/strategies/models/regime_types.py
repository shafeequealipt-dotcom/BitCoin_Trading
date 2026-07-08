"""Market regime classification types."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class MarketRegime(str, Enum):
    """Market regime classification."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"
    DEAD = "dead"
    # Per-coin-authority Phase 0b (2026-05-29): an explicit "we could not
    # classify this coin" state, distinct from a real RANGING reading. It is
    # emitted when a coin has insufficient klines (<50), when a core
    # classification input (ADX / choppiness) is genuinely absent from the TA
    # payload, or when per-coin detection raised. Downstream consumers can now
    # tell "no data" from "genuinely ranging" instead of trusting a fabricated
    # RANGING/0.30 label. Operator decision: UNKNOWN coins are still tradeable
    # on their own TA/structure (a broad, non-restrictive strategy roster — see
    # REGIME_ACTIVE_CATEGORIES below), not blocked.
    UNKNOWN = "unknown"


# Maps each regime to the strategy categories that should be active.
#
# Layer 1 Defect 1 follow-up (2026-05-21) — the ``kickstart`` category
# is added to every regime entry because the only strategy in that
# category (X1_always_trade, src/strategies/categories/x1_always_trade.py)
# declares ``applicable_regimes = list(MarketRegime)`` and is the
# testnet data-generation kickstart that must fire on every coin
# regardless of regime. Without it in every regime's list,
# StrategyRegistry.get_active_for_regime (post-Defect-1, flag default
# True) silently dropped X1 from voting in every regime, breaking the
# testnet kickstart contract.
REGIME_ACTIVE_CATEGORIES: dict[MarketRegime, list[str]] = {
    MarketRegime.TRENDING_UP: [
        "scalping", "momentum", "advanced", "predatory",
        "cross_market", "time_based", "ai_enhanced", "kickstart",
    ],
    MarketRegime.TRENDING_DOWN: [
        "scalping", "momentum", "advanced", "predatory",
        "cross_market", "time_based", "ai_enhanced", "kickstart",
    ],
    MarketRegime.RANGING: [
        "scalping", "mean_reversion", "funding_arb", "advanced",
        "microstructure", "time_based", "ai_enhanced", "kickstart",
    ],
    # Per-coin-authority Phase 0e (2026-05-29, operator decision): widened to
    # ALSO include ``momentum`` and ``mean_reversion``. Rationale — with
    # per-coin regime as the sole authority and the Phase-0a fix that stops
    # genuinely-ranging coins being mislabelled VOLATILE, a VOLATILE label now
    # means real, tradeable volatility; silencing momentum (ride the move) and
    # mean_reversion (fade the move) on exactly those high-movement coins
    # throttled the aggression goal. They are re-enabled here.
    MarketRegime.VOLATILE: [
        "scalping", "sentiment", "predatory", "microstructure",
        "time_based", "ai_enhanced", "kickstart",
        "momentum", "mean_reversion",
    ],
    MarketRegime.DEAD: [
        "funding_arb", "microstructure", "kickstart",
    ],
    # Per-coin-authority Phase 0b (2026-05-29, operator decision): UNKNOWN gets
    # a BROAD, non-restrictive roster (the union of all other regimes' tradeable
    # categories) so a coin we cannot classify is still allowed to trade on its
    # own TA/structure evidence rather than being silenced. It is never empty
    # (always includes ``kickstart``). UNKNOWN imposes NO regime discipline by
    # design — the trade still requires a real setup (X-RAY / signal / consensus)
    # to fire; this list only declines to filter by regime.
    MarketRegime.UNKNOWN: [
        "scalping", "momentum", "mean_reversion", "advanced", "predatory",
        "cross_market", "microstructure", "sentiment", "funding_arb",
        "time_based", "ai_enhanced", "kickstart",
    ],
}


@dataclass
class RegimeState:
    """Current market regime with supporting metrics."""
    regime: MarketRegime
    confidence: float
    adx: float
    atr_percentile: float
    choppiness: float
    volume_ratio: float
    trend_direction: int  # +1 uptrend, -1 downtrend, 0 none
    # Issue #3B (2026-05-31): whether `volume_ratio` is a REAL measurement or a
    # neutral placeholder because the TA payload had no volume_sma_ratio (thin/
    # new coin, or a cold-start row). Kept as a companion flag rather than making
    # volume_ratio Optional, because three consumers treat it as a bare float and
    # would crash on None (to_dict round(), telegram handler :.2f, scanner float()).
    # Default True preserves every existing construction site's behaviour.
    volume_ratio_known: bool = True
    active_strategy_categories: list[str] = field(default_factory=list)
    detected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 4),
            "adx": round(self.adx, 2),
            "atr_percentile": round(self.atr_percentile, 2),
            "choppiness": round(self.choppiness, 2),
            # Issue #3B: serialize a genuinely-missing ratio as None (the
            # coin_regime_history.volume_ratio column is a nullable REAL, so this
            # round-trips truthfully and the restore path reads NULL -> unknown).
            "volume_ratio": (
                round(self.volume_ratio, 2) if self.volume_ratio_known else None
            ),
            "volume_ratio_known": self.volume_ratio_known,
            "trend_direction": self.trend_direction,
            "active_strategy_categories": self.active_strategy_categories,
            "detected_at": self.detected_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RegimeState":
        return cls(
            regime=MarketRegime(data["regime"]),
            confidence=data.get("confidence", 0.5),
            adx=data.get("adx", 0),
            atr_percentile=data.get("atr_percentile", 100),
            choppiness=data.get("choppiness", 50),
            # Issue #3B: a missing/None volume_ratio means "unknown" — keep the
            # numeric field at a neutral 1.0 so downstream float consumers never
            # crash, but mark it not-known so renderers/regime logic can tell.
            volume_ratio=(
                float(_vr) if (_vr := data.get("volume_ratio")) is not None else 1.0
            ),
            volume_ratio_known=(
                bool(data["volume_ratio_known"])
                if "volume_ratio_known" in data
                else data.get("volume_ratio") is not None
            ),
            trend_direction=data.get("trend_direction", 0),
            active_strategy_categories=data.get("active_strategy_categories", []),
        )

    @classmethod
    def unknown(cls) -> "RegimeState":
        """Per-coin-authority Phase 2 (2026-05-29): the canonical 'could not
        classify / no per-coin data' state. This is the ONLY sanctioned
        cold-start fallback for a coin's regime — consumers must fall back to
        THIS, never to the global BTC regime (that back-door coupling is what
        per-coin authority removes). Zero-confidence neutral metrics; broad
        non-restrictive roster so an UNKNOWN coin still trades on its own
        TA/structure (operator decision), never silenced.
        """
        return cls(
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,
            adx=0.0, atr_percentile=0.0, choppiness=0.0,
            # Issue #3B: UNKNOWN genuinely has no measured volume.
            volume_ratio=0.0, volume_ratio_known=False, trend_direction=0,
            active_strategy_categories=list(
                REGIME_ACTIVE_CATEGORIES.get(MarketRegime.UNKNOWN, [])
            ),
        )
