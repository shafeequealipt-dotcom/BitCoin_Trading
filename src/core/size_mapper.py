"""Maps setup quality to position size. The heart of the new philosophy:
score determines HOW MUCH, not WHETHER.

Used by Fund Manager and Brain to translate quality signals into dollar amounts.
"""

# Score-based base size (percentage of trading capital)
SCORE_SIZE_MAP = [
    (80, 100, 10.0),    # Exceptional setup
    (70, 79, 7.0),      # Very strong setup
    (60, 69, 5.0),      # Strong setup
    (50, 59, 3.5),      # Decent setup
    (40, 49, 2.0),      # Weak but tradeable
    (30, 39, 1.0),      # Very weak — micro position
    (0, 29, 0.5),       # Minimal — data collection trade
]

# Consensus multiplier (applied to base size)
CONSENSUS_MULTIPLIER = {
    "STRONG": 1.0,
    "GOOD": 0.75,
    "LEAN": 0.50,
    "WEAK": 0.35,
    "CONFLICT": 0.20,
}

# Regime multiplier (applied after consensus)
REGIME_MULTIPLIER = {
    "trending_up": 1.0,
    "trending_down": 1.0,
    "ranging": 0.8,
    "volatile": 0.6,
    "dead": 0.4,
}


def calculate_position_pct(
    score: float,
    consensus: str,
    regime: str = "ranging",
    urgency: int = 0,
) -> float:
    """Calculate position size as percentage of trading capital.

    NEVER returns 0. Minimum is 0.1%.

    Args:
        score: 0-100 from TradeScorer.
        consensus: STRONG/GOOD/LEAN/WEAK/CONFLICT.
        regime: Current market regime string.
        urgency: 0-5 from enforcer (higher = more eager to trade).

    Returns:
        Percentage of trading capital (e.g., 5.0 = 5%).
    """
    # Base size from score
    base_pct = 0.5
    for low, high, pct in SCORE_SIZE_MAP:
        if low <= score <= high:
            base_pct = pct
            break

    # Apply consensus multiplier
    consensus_mult = CONSENSUS_MULTIPLIER.get(consensus, 0.3)

    # Apply regime multiplier
    regime_mult = REGIME_MULTIPLIER.get(regime, 0.7)

    # Apply urgency boost
    urgency_mult = 1.0
    if urgency >= 3:
        urgency_mult = 1.3
    elif urgency >= 2:
        urgency_mult = 1.15

    # Final calculation
    final_pct = base_pct * consensus_mult * regime_mult * urgency_mult

    # Floor: never below 0.1%
    final_pct = max(final_pct, 0.1)

    # Ceiling: never above 15%
    final_pct = min(final_pct, 15.0)

    return round(final_pct, 2)
