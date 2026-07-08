"""Mathematical models for Mode 4 Profit Sniper — institutional-grade rebuild.

Five institutional models with regime-aware dynamic scoring:
  1. Hurst Exponent — trend persistence (replaces Z-Score, Phase 2)
  2. Momentum Decay — PnL deceleration detection (replaces Velocity, Phase 3)
  3. ATR Extension — volatility-normalized distance (replaces Bollinger, Phase 4)
  4. Volume Divergence — Wyckoff-derived OBV analysis (replaces Volume-Price, Phase 5)
  5. Risk/Reward Shift — forward Expected Value (replaces Momentum Exhaustion, Phase 6)

All compute_* methods are PURE FUNCTIONS:
  Input: numpy arrays from EnhancedRingBuffer
  Output: XxxResult dataclass with score (0-100) and component details

Scoring engine: regime-conditional weights + consensus boost + urgency boost (Phase 7).
"""

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(slots=True)
class HurstResult:
    """Result of Hurst Exponent computation for trend persistence scoring."""

    hurst_value: float        # Raw H value (0.0 to 1.0)
    score: float              # Exit pressure score (0-100, higher = more pressure)
    regime: str               # "trending" | "random_walk" | "mean_reverting"
    confidence: float         # R² of log-log regression (0-1)
    data_points_used: int     # Price points from buffer used
    tau_values_used: int      # τ sub-series lengths used in regression


@dataclass(slots=True)
class MomentumDecayResult:
    """Result of Momentum Decay detection for exit pressure scoring."""

    score: float                     # Total exit pressure (0-100)

    # Component A: Acceleration
    accel_short: float               # Short-term acceleration (2min scale)
    accel_medium: float              # Medium-term acceleration (6min scale)
    accel_score: float               # Sub-score A (0-40)

    # Component B: Consecutive deceleration
    consecutive_decelerations: int   # 0-5 count
    decel_score: float               # Sub-score B (0-35)

    # Component C: Slope degradation
    slope_short: float               # PnL slope over 2 min
    slope_medium: float              # PnL slope over 6 min
    slope_long: float                # PnL slope over 15 min
    degradation_ratio: float         # slope_short / slope_long
    degradation_score: float         # Sub-score C (0-25)

    # Component D: Reversal flag
    momentum_reversed: bool          # Is short-term direction reversed?

    # Meta
    data_points_used: int            # Buffer points available


@dataclass(slots=True)
class ExtensionResult:
    """Result of ATR-normalized extension scoring for exit pressure."""

    score: float                # Exit pressure (0-100, higher = more extended)

    # Core extension data
    extension_atr: float        # Distance from entry in ATR units (signed, positive = profit)
    extension_pct: float        # Distance from entry in % (for reference)
    peak_extension_atr: float   # Highest extension ever seen (ATR units)
    drawdown_atr: float         # Pullback from peak in ATR units (≥ 0)

    # Volatility context
    atr_current: float          # Current ATR value
    atr_at_entry: float         # ATR when position opened
    vol_ratio: float            # atr_current / atr_at_entry
    vol_adjustment: float       # Multiplier applied (0.9 / 1.0 / 1.15)

    # Scoring detail
    base_score: float           # Sigmoid score before vol adjustment

    # Meta
    atr_source: str             # "ta_cache" | "buffer_fallback" | "unavailable"


@dataclass(slots=True)
class VolumeDivergenceResult:
    """Result of Volume Profile Divergence analysis for exit pressure scoring."""

    score: float                  # Total exit pressure (0-100)

    # Component A: Price-OBV Correlation
    price_obv_correlation: float  # Pearson r (-1 to +1)
    correlation_score: float      # Sub-score A (0-40)

    # Component B: Volume Trend
    volume_trend_ratio: float     # Second half / First half volume
    volume_trend_score: float     # Sub-score B (0-25)

    # Component C: Buy/Sell Pressure
    buy_ratio: float              # Buy volume / Total volume (0-1)
    effective_ratio: float        # Direction-adjusted ratio
    pressure_score: float         # Sub-score C (0-20)

    # Component D: Volume Climax
    volume_climax_zscore: float   # Z-score of latest volume bar
    climax_score: float           # Sub-score D (0-15)

    # Classification
    divergence_type: str          # "confirming"|"weakening"|"diverging"|"opposing"

    # Meta
    data_points_used: int
    volume_data_quality: str      # "good"|"sparse"|"unavailable"


@dataclass(slots=True)
class RiskRewardResult:
    """Result of Risk/Reward Shift analysis (forward Expected Value)."""

    score: float                  # Exit pressure (0-100)

    # EV computation
    ev_hold: float                # Raw expected value of holding (% terms)
    ev_ratio: float               # EV normalized by current PnL

    # Probability estimates
    p_up: float                   # Probability of further upside (0-1)
    p_down: float                 # Probability of pullback (0-1)
    p_up_empirical: float         # Base P(up) from data before adjustments

    # Magnitude estimates
    avg_upside_per_tick: float
    avg_downside_per_tick: float
    expected_upside_5min: float
    expected_downside_5min: float

    # Distribution statistics
    mean_return: float
    std_return: float
    skewness: float

    # Amplification
    profit_amplifier: float       # Total amplifier applied
    base_score: float             # Score before amplification

    # Context
    hurst_used: float
    data_points_used: int


@dataclass(slots=True)
class CompositeScoreResult:
    """Result of regime-aware composite scoring across all 5 models."""

    score: float               # Final composite (0-100)
    base_score: float          # Weighted average before boosts

    # Weight selection
    regime_used: str           # "trending"|"ranging"|"volatile"|"dead"|"balanced"

    # Boosts
    consensus_count: int       # How many models scored > 50
    consensus_boost: float     # Points added (0/8/12)
    urgency_max_score: float   # Highest individual model score
    urgency_boost: float       # Points added (0-6)

    # Individual model scores
    hurst_score: float
    momentum_decay_score: float
    atr_extension_score: float
    volume_divergence_score: float
    risk_reward_score: float


@dataclass(slots=True)
class TrailResult:
    """Result of ATR-based dynamic trailing stop computation (Phase 8).

    trail_distance = base_atr_mult × ATR × regime_factor × profit_decay × momentum_factor
    trail_stop = peak_price ∓ trail_distance  (- for longs, + for shorts)

    Trail only moves in the protective direction (ratchet).
    Trail never goes worse than entry price (breakeven floor).
    """

    # Core output
    trail_stop_price: float       # Computed trail stop price
    trail_distance: float         # Distance from peak in price units
    trail_distance_pct: float     # Distance from peak as %

    # Factors used (for logging)
    base_atr_mult: float          # 2.5 default
    atr_used: float               # ATR in price units
    regime_factor: float          # 0.6-1.3 based on regime
    profit_decay: float           # 0.x-1.0, tightens as profit grows
    momentum_factor: float        # 0.6-1.1 based on momentum score

    # Context
    peak_price: float             # Direction-aware peak price
    entry_price: float            # Position entry (breakeven floor)
    direction: str                # "Buy" or "Sell"

    # Decision
    is_tighter_than_current: bool  # Would this trail improve the current SL?
    current_sl: float              # Current SL (0.0 if None)
    should_apply: bool             # All conditions met (in profit, tighter, min change)?


@dataclass(slots=True)
class LadderResult:
    """Result of the stepped break-even ladder computation (Profit-Fetching
    Exit System technique 1, 2026-05-29).

    As high-water profit climbs past successive levels (every ``step_pct``),
    the stop locks a rising guaranteed-profit floor a fixed ``lock_offset_pct``
    behind the level just crossed. Computed from peak_pnl_pct so the floor is
    monotonic (only rises). A candidate stop the Phase 4 spine reconciles with
    the Chandelier trail and the current SL under highest-stop-wins.
    """

    # Core output
    ladder_stop_price: float       # Candidate lock price
    level_crossed_pct: float       # Highest fully-crossed profit level (%)
    lock_pct: float                # Locked profit level minus offset (%)

    # Factors used (time-dialed, for logging)
    step_pct: float                # Step spacing at this trade age
    lock_offset_pct: float         # Lock offset behind each level at this age

    # Context
    peak_pnl_pct: float            # High-water profit driving the ladder
    entry_price: float
    direction: str                 # "Buy" or "Sell"

    # Decision
    is_tighter_than_current: bool  # Would the ladder improve the current SL?
    current_sl: float              # Current SL (0.0 if None)
    armed: bool                    # peak reached the first ladder level?
    should_apply: bool             # Armed, positive lock, and tighter?

    # Finding 6 (2026-06-02): True when the lock came from the zero-crossing
    # breakeven floor (a modest peak in [arm, first_step) that the step-based
    # level would have locked nothing for) rather than a crossed step rung.
    breakeven_floor: bool = False


@dataclass(slots=True)
class ActionResult:
    """Final action decision from Mode4 Phase 9 engine.

    Combines regime-aware score thresholds with anti-greed pullback backstop.
    Final action = max(score_action, greed_action) by ACTION_PRIORITY.
    """

    action: str                  # "hold"|"tighten"|"partial_close"|"full_close"
    source: str                  # "score"|"anti_greed"|"both"

    # Score-based decision
    score_action: str            # What score alone chose
    score_value: float           # Composite score used
    regime_used: str             # Regime name ("trending"|"ranging"|etc.)
    threshold_set: dict          # Actual thresholds applied

    # Anti-greed backstop
    greed_action: str            # What anti-greed pullback rules chose
    peak_pnl: float              # Peak PnL % seen
    current_pnl: float           # Current PnL %
    pullback_pct: float          # % of peak profit given back (0-100)
    greed_rule_triggered: str    # "none"|"40pct"|"60pct"|"75pct"

    # Cooldown
    cooled_down: bool            # Was action downgraded by cooldown?
    original_action: str         # Action before cooldown check


# ═══════════════════════════════════════════════════════════════════════════════
# Regime-Conditional Weight Tables (Phase 7)
# ═══════════════════════════════════════════════════════════════════════════════

TRENDING_WEIGHTS = {
    "hurst": 0.30, "momentum_decay": 0.20, "atr_extension": 0.10,
    "volume_divergence": 0.25, "risk_reward": 0.15,
}
RANGING_WEIGHTS = {
    "hurst": 0.15, "momentum_decay": 0.20, "atr_extension": 0.30,
    "volume_divergence": 0.15, "risk_reward": 0.20,
}
VOLATILE_WEIGHTS = {
    "hurst": 0.20, "momentum_decay": 0.25, "atr_extension": 0.15,
    "volume_divergence": 0.25, "risk_reward": 0.15,
}
DEAD_WEIGHTS = {
    "hurst": 0.10, "momentum_decay": 0.20, "atr_extension": 0.25,
    "volume_divergence": 0.20, "risk_reward": 0.25,
}
BALANCED_WEIGHTS = {
    "hurst": 0.20, "momentum_decay": 0.20, "atr_extension": 0.20,
    "volume_divergence": 0.20, "risk_reward": 0.20,
}


def _interpolate(value: float, breakpoints: list[tuple[float, float]]) -> float:
    """Linear interpolation between breakpoints."""
    if value <= breakpoints[0][0]:
        return breakpoints[0][1]
    if value >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for i in range(len(breakpoints) - 1):
        lo_x, lo_y = breakpoints[i]
        hi_x, hi_y = breakpoints[i + 1]
        if lo_x <= value <= hi_x:
            if hi_x == lo_x:
                return hi_y
            t = (value - lo_x) / (hi_x - lo_x)
            return lo_y + t * (hi_y - lo_y)
    return breakpoints[-1][1]


class SniperModels:
    """Institutional-grade mathematical models for Mode 4 Profit Sniper.

    Score points are scaled to each model's configured weight:
      Z-Score:    0 to weight_zscore    (default 25)
      Velocity:   0 to weight_velocity  (default 25)
      Volume:     0 to weight_volume    (default 20)
      Bollinger:  0 to weight_bollinger (default 15)
      Momentum:   0 to weight_momentum  (default 15)
      Total:      0 to 100

    Args:
        weights: Dict with keys 'zscore', 'velocity', 'volume',
                 'bollinger', 'momentum'. Values are max points per model.
    """

    def __init__(self, weights: dict[str, int]) -> None:
        self._w = weights

    # ─── Model 1: Hurst Exponent (trend persistence) ──

    def compute_hurst(self, prices: np.ndarray) -> HurstResult:
        """Compute Hurst Exponent using Rescaled Range (R/S) analysis.

        Measures trend persistence:
          H > 0.55 = trending (persistent moves, let position run)
          H ≈ 0.50 = random walk (no edge, tighten stop)
          H < 0.45 = mean-reverting (reversal likely, exit)

        Args:
            prices: numpy array of price observations from ring buffer.
                    Minimum 50 points for valid computation.

        Returns:
            HurstResult with H value, score, regime, and confidence.

        Computation: <5ms for 720 points (numpy vectorized).
        """
        _default = HurstResult(
            hurst_value=0.5, score=25.0, regime="random_walk",
            confidence=0.0, data_points_used=len(prices),
            tau_values_used=0,
        )

        # Guard: insufficient data
        if len(prices) < 50:
            return _default

        # Filter invalid prices (zero, negative, NaN, Inf)
        valid = prices[np.isfinite(prices) & (prices > 0)]
        if len(valid) < 50:
            return _default

        # Step 1: Log returns
        log_returns = np.diff(np.log(valid))
        n = len(log_returns)
        if n < 20:
            return _default

        # Step 2: Choose tau values
        max_tau = n // 4
        taus = [t for t in [10, 20, 40, 80, 160, 320] if t <= max_tau]

        # Fallback to smaller taus for short series
        if len(taus) < 3:
            taus = [t for t in [8, 12, 16, 24, 32, 48, 64] if t <= max_tau]
            if len(taus) < 3:
                return _default

        # Step 3: Compute R/S for each tau
        rs_averages: list[float] = []
        valid_taus: list[int] = []

        for tau in taus:
            n_chunks = n // tau
            rs_list: list[float] = []

            for i in range(n_chunks):
                chunk = log_returns[i * tau: (i + 1) * tau]
                mean_c = np.mean(chunk)
                deviations = chunk - mean_c
                cumdev = np.cumsum(deviations)
                R = float(np.max(cumdev) - np.min(cumdev))
                S = float(np.std(chunk, ddof=1))

                if S > 1e-12:
                    rs_list.append(R / S)

            if len(rs_list) >= 2:
                rs_avg = float(np.mean(rs_list))
                if rs_avg > 0 and not np.isnan(rs_avg):
                    rs_averages.append(rs_avg)
                    valid_taus.append(tau)

        # Guard: not enough valid RS values for regression
        if len(valid_taus) < 3:
            return HurstResult(
                hurst_value=0.5, score=25.0, regime="random_walk",
                confidence=0.0, data_points_used=len(prices),
                tau_values_used=len(valid_taus),
            )

        # Step 4: Log-log regression → H = slope
        log_taus = np.log(np.array(valid_taus, dtype=float))
        log_rs = np.log(np.array(rs_averages, dtype=float))

        slope, intercept = np.polyfit(log_taus, log_rs, 1)
        H = float(np.clip(slope, 0.0, 1.0))

        # R² (confidence measure)
        predicted = slope * log_taus + intercept
        ss_res = float(np.sum((log_rs - predicted) ** 2))
        ss_tot = float(np.sum((log_rs - np.mean(log_rs)) ** 2))
        r_squared = max(0.0, min(1.0, 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0))

        # Step 5: Score
        score = self._hurst_to_score(H, r_squared)

        # Step 6: Regime classification
        if H > 0.55:
            regime = "trending"
        elif H < 0.45:
            regime = "mean_reverting"
        else:
            regime = "random_walk"

        return HurstResult(
            hurst_value=H, score=score, regime=regime,
            confidence=r_squared, data_points_used=len(prices),
            tau_values_used=len(valid_taus),
        )

    def _hurst_to_score(self, H: float, r_squared: float) -> float:
        """Convert Hurst Exponent to exit pressure score (0-100).

        Higher score = more pressure to exit.
        Low H (mean-reverting) → high score. High H (trending) → low score.
        Confidence adjustment: if R² < 0.7, dampen toward neutral (25).
        """
        if H >= 0.65:
            score = max(0.0, (0.75 - H) / 0.10 * 5)
        elif H >= 0.55:
            score = 5.0 + (0.65 - H) / 0.10 * 20
        elif H >= 0.50:
            score = 25.0 + (0.55 - H) / 0.05 * 25
        elif H >= 0.45:
            score = 50.0 + (0.50 - H) / 0.05 * 25
        else:
            score = 75.0 + min(25.0, (0.45 - H) / 0.15 * 25)

        score = max(0.0, min(100.0, score))

        # Confidence adjustment — low R² dampens toward neutral
        if r_squared < 0.7:
            cf = r_squared / 0.7
            score = score * cf + 25.0 * (1.0 - cf)

        return round(score, 1)

    # ─── Model 2b: Momentum Decay Detector (replaces Velocity in Phase 3) ──

    @staticmethod
    def _compute_slope(series: np.ndarray) -> float:
        """Compute linear regression slope of a 1D numpy series.

        Returns slope in units of [value-per-index]. Positive = increasing.
        Returns 0.0 if series has fewer than 5 points.
        Uses direct formula (3x faster than np.polyfit for slope-only).
        """
        n = len(series)
        if n < 5:
            return 0.0
        x = np.arange(n, dtype=np.float64)
        x_mean = np.mean(x)
        y_mean = np.mean(series)
        num = np.mean(x * series) - x_mean * y_mean
        den = np.mean(x * x) - x_mean * x_mean
        if abs(den) < 1e-15:
            return 0.0
        return float(num / den)

    def compute_momentum_decay(self, pnl_series: np.ndarray) -> MomentumDecayResult:
        """Detect momentum decay in a position's PnL trajectory.

        Analyzes the rate-of-change of PnL at multiple timescales to detect
        when a profitable move is losing steam — before the actual reversal.

        Components:
          A. Multi-scale slopes (2min/6min/15min) + acceleration → 0-40 pts
          B. Consecutive deceleration count (1-min windows) → 0-35 pts
          C. Slope degradation ratio (short vs long) → 0-25 pts
          D. Momentum reversal flag

        Args:
            pnl_series: numpy array of PnL % values from ring buffer.
                        Each entry 5s apart. Already direction-adjusted.

        Returns:
            MomentumDecayResult with component scores and total exit pressure.
        """
        n = len(pnl_series)
        _slope = self._compute_slope

        # Default for insufficient data
        if n < 24:
            return MomentumDecayResult(
                score=0.0,
                accel_short=0.0, accel_medium=0.0, accel_score=0.0,
                consecutive_decelerations=0, decel_score=0.0,
                slope_short=0.0, slope_medium=0.0, slope_long=0.0,
                degradation_ratio=1.0, degradation_score=0.0,
                momentum_reversed=False, data_points_used=n,
            )

        # ── Multi-scale slopes ──
        slope_short = _slope(pnl_series[-24:])                              # Last 2 min
        slope_medium = _slope(pnl_series[-72:]) if n >= 72 else slope_short  # Last 6 min
        slope_long = _slope(pnl_series[-180:]) if n >= 180 else slope_medium # Last 15 min

        # ── Component A: Acceleration ──
        slope_recent_1m = _slope(pnl_series[-12:]) if n >= 12 else 0.0
        slope_prior_1m = _slope(pnl_series[-24:-12]) if n >= 24 else 0.0
        accel_short = slope_recent_1m - slope_prior_1m

        if n >= 72:
            slope_recent_3m = _slope(pnl_series[-36:])
            slope_prior_3m = _slope(pnl_series[-72:-36])
            accel_medium = slope_recent_3m - slope_prior_3m
        else:
            accel_medium = accel_short

        momentum_reversed = slope_short < 0

        # Acceleration sub-score (0-40)
        if accel_short > 0 and accel_medium > 0:
            accel_score = 0.0
        elif accel_short < 0 and momentum_reversed:
            accel_score = 40.0
        elif accel_short < 0 and accel_medium < 0:
            accel_score = 30.0
        elif accel_short < 0:
            accel_score = 15.0
        else:
            accel_score = 5.0

        # ── Component B: Consecutive Deceleration ──
        window_size = 12  # 12 × 5s = 1 minute
        n_windows = min(5, n // window_size)
        consecutive = 0

        if n_windows >= 2:
            window_slopes = []
            for i in range(n_windows):
                start = n - (n_windows - i) * window_size
                end = start + window_size
                window_slopes.append(_slope(pnl_series[start:end]))

            for i in range(len(window_slopes) - 1, 0, -1):
                if window_slopes[i] < window_slopes[i - 1]:
                    consecutive += 1
                else:
                    break

        decel_lookup = [0, 0, 10, 20, 30, 35]
        decel_score = float(decel_lookup[min(consecutive, 5)])

        # ── Component C: Slope Degradation ──
        if slope_long > 1e-8:
            degradation_ratio = slope_short / slope_long
        elif slope_short >= 0:
            degradation_ratio = 1.0
        else:
            degradation_ratio = -1.0

        if degradation_ratio >= 0.8:
            degradation_score = 0.0
        elif degradation_ratio >= 0.0:
            degradation_score = (0.8 - degradation_ratio) / 0.8 * 18.0
        else:
            degradation_score = 18.0 + min(7.0, abs(degradation_ratio) * 7.0)

        # ── Total Score ──
        total = max(0.0, min(100.0, accel_score + decel_score + degradation_score))

        return MomentumDecayResult(
            score=round(total, 1),
            accel_short=round(accel_short, 6),
            accel_medium=round(accel_medium, 6),
            accel_score=round(accel_score, 1),
            consecutive_decelerations=consecutive,
            decel_score=round(decel_score, 1),
            slope_short=round(slope_short, 6),
            slope_medium=round(slope_medium, 6),
            slope_long=round(slope_long, 6),
            degradation_ratio=round(degradation_ratio, 3),
            degradation_score=round(degradation_score, 1),
            momentum_reversed=momentum_reversed,
            data_points_used=n,
        )

    # ─── Model 3b: ATR Extension (replaces Bollinger in Phase 4) ──

    def compute_atr_extension(
        self,
        entry_price: float,
        current_price: float,
        direction: str,
        atr_current: float,
        atr_at_entry: float,
        peak_pnl_pct: float,
        prices: np.ndarray | None = None,
    ) -> ExtensionResult:
        """Compute ATR-normalized extension from entry with sigmoid scoring.

        Measures how far the position has moved from entry in volatility units.
        Uses a sigmoid curve to convert extension to exit pressure (0-100).
        Only creates exit pressure for PROFITABLE positions (extension_atr > 0).

        Args:
            entry_price: Position entry price.
            current_price: Current market price.
            direction: "Buy" or "Sell".
            atr_current: Current 14-period ATR on 5m candles.
            atr_at_entry: ATR when position was opened.
            peak_pnl_pct: Highest PnL % ever seen.
            prices: Recent price array (fallback ATR estimation if atr_current=0).

        Returns:
            ExtensionResult with sigmoid-scored exit pressure.
        """
        _neutral = ExtensionResult(
            score=25.0, extension_atr=0.0, extension_pct=0.0,
            peak_extension_atr=0.0, drawdown_atr=0.0,
            atr_current=0.0, atr_at_entry=atr_at_entry or 0.0,
            vol_ratio=1.0, vol_adjustment=1.0,
            base_score=25.0, atr_source="unavailable",
        )

        # ATR validation and fallback
        atr_source = "ta_cache"
        if atr_current is None or atr_current <= 0:
            if prices is not None and len(prices) >= 60:
                recent = prices[-60:]
                price_range = float(np.max(recent) - np.min(recent))
                atr_current = price_range / 4.0 if price_range > 0 else 0
                atr_source = "buffer_fallback"
            else:
                return _neutral

        if atr_current < 1e-12 or entry_price <= 0:
            return ExtensionResult(
                score=0.0, extension_atr=0.0, extension_pct=0.0,
                peak_extension_atr=0.0, drawdown_atr=0.0,
                atr_current=atr_current, atr_at_entry=atr_at_entry or 0.0,
                vol_ratio=1.0, vol_adjustment=1.0,
                base_score=0.0, atr_source=atr_source,
            )

        # Signed distance from entry (positive = in profit direction)
        if direction in ("Buy", "Long"):
            raw_distance = current_price - entry_price
        else:
            raw_distance = entry_price - current_price

        extension_atr = raw_distance / atr_current
        extension_pct = (raw_distance / entry_price) * 100

        # Peak extension in ATR units
        if peak_pnl_pct > 0 and entry_price > 0:
            peak_distance = (peak_pnl_pct / 100) * entry_price
            peak_extension_atr = peak_distance / atr_current
        else:
            peak_extension_atr = max(0.0, extension_atr)

        drawdown_atr = max(0.0, peak_extension_atr - extension_atr)

        # Volatility regime factor
        if atr_at_entry is not None and atr_at_entry > 1e-12:
            vol_ratio = atr_current / atr_at_entry
        else:
            vol_ratio = 1.0

        if vol_ratio > 1.3:
            vol_adjustment = 1.15
        elif vol_ratio < 0.7:
            vol_adjustment = 0.90
        else:
            vol_adjustment = 1.0

        # Sigmoid scoring (only for profitable positions)
        if extension_atr <= 0:
            base_score = 0.0
        else:
            exponent = max(-100.0, min(100.0, -1.5 * (extension_atr - 2.5)))
            base_score = 100.0 / (1.0 + np.exp(exponent))

        final_score = max(0.0, min(100.0, base_score * vol_adjustment))

        return ExtensionResult(
            score=round(final_score, 1),
            extension_atr=round(extension_atr, 3),
            extension_pct=round(extension_pct, 4),
            peak_extension_atr=round(peak_extension_atr, 3),
            drawdown_atr=round(drawdown_atr, 3),
            atr_current=atr_current,
            atr_at_entry=atr_at_entry or 0.0,
            vol_ratio=round(vol_ratio, 3),
            vol_adjustment=vol_adjustment,
            base_score=round(base_score, 1),
            atr_source=atr_source,
        )

    # ─── Model 4b: Volume Divergence (replaces Volume-Price in Phase 5) ──

    def compute_volume_divergence(
        self,
        prices: np.ndarray,
        volumes: np.ndarray,
        buy_volumes: np.ndarray,
        sell_volumes: np.ndarray,
        direction: str,
    ) -> VolumeDivergenceResult:
        """Detect volume divergence from price using Wyckoff-derived analysis.

        4 components: price-OBV correlation, volume trend, buy/sell pressure
        imbalance, and volume climax detection.

        Args:
            prices: Price array from buffer.
            volumes: Volume delta array (same length).
            buy_volumes: Estimated buy volume (Lee-Ready).
            sell_volumes: Estimated sell volume (same length).
            direction: "Buy" or "Sell".

        Returns:
            VolumeDivergenceResult with component scores and classification.
        """
        n = len(prices)
        _neutral = VolumeDivergenceResult(
            score=0.0, price_obv_correlation=0.0, correlation_score=0.0,
            volume_trend_ratio=1.0, volume_trend_score=0.0,
            buy_ratio=0.5, effective_ratio=0.5, pressure_score=0.0,
            volume_climax_zscore=0.0, climax_score=0.0,
            divergence_type="confirming", data_points_used=n,
            volume_data_quality="unavailable",
        )

        vol_sum = float(np.sum(np.abs(volumes))) if len(volumes) > 0 else 0.0
        if n < 36 or vol_sum < 1e-10:
            _neutral.volume_data_quality = "unavailable" if vol_sum < 1e-10 else "sparse"
            _neutral.data_points_used = n
            return _neutral

        quality = "good" if n >= 100 else "sparse"

        # ── Component A: Price-OBV Correlation (0-40) ──
        price_changes = np.diff(prices)
        signed_vol = np.where(
            price_changes > 0, volumes[1:],
            np.where(price_changes < 0, -volumes[1:], 0.0),
        )
        obv = np.concatenate([[0.0], np.cumsum(signed_vol)])

        corr_window = min(36, n)
        p_win = prices[-corr_window:]
        o_win = obv[-corr_window:]

        if np.std(p_win) < 1e-12 or np.std(o_win) < 1e-12:
            correlation = 0.0
        else:
            corr_matrix = np.corrcoef(p_win, o_win)
            correlation = float(corr_matrix[0, 1])
            if np.isnan(correlation):
                correlation = 0.0

        if correlation >= 0.7:
            corr_score = max(0.0, (0.9 - correlation) / 0.2 * 5)
        elif correlation >= 0.0:
            corr_score = 5.0 + (0.7 - correlation) / 0.7 * 30
        else:
            corr_score = 35.0 + min(5.0, abs(correlation) * 5)

        # ── Component B: Volume Trend Ratio (0-25) ──
        mid = n // 2
        vol_first = float(np.sum(np.abs(volumes[:mid])))
        vol_second = float(np.sum(np.abs(volumes[mid:])))

        vol_trend_ratio = vol_second / vol_first if vol_first > 1e-10 else 1.0

        if vol_trend_ratio >= 1.2:
            vol_trend_score = 0.0
        elif vol_trend_ratio >= 0.5:
            vol_trend_score = (1.2 - vol_trend_ratio) / 0.7 * 25.0
        else:
            vol_trend_score = 25.0

        # ── Component C: Buy/Sell Pressure Imbalance (0-20) ──
        recent_n = min(24, n)
        recent_buy = float(np.sum(buy_volumes[-recent_n:]))
        recent_sell = float(np.sum(sell_volumes[-recent_n:]))
        total_recent = recent_buy + recent_sell

        buy_ratio = recent_buy / total_recent if total_recent > 1e-10 else 0.5
        effective_ratio = buy_ratio if direction in ("Buy", "Long") else (1.0 - buy_ratio)

        if effective_ratio >= 0.6:
            pressure_score = 0.0
        elif effective_ratio >= 0.3:
            pressure_score = (0.6 - effective_ratio) / 0.3 * 20.0
        else:
            pressure_score = 20.0

        # ── Component D: Volume Climax Detection (0-15) ──
        abs_vols = np.abs(volumes)
        vol_mean = float(np.mean(abs_vols))
        vol_std_val = float(np.std(abs_vols))

        if vol_std_val > 1e-10:
            vol_zscore = (float(abs_vols[-1]) - vol_mean) / vol_std_val
        else:
            vol_zscore = 0.0

        climax_score = 0.0 if vol_zscore < 2.0 else min(15.0, (vol_zscore - 2.0) * 7.5)

        # ── Classification ──
        if correlation > 0.5 and vol_trend_ratio > 0.8:
            div_type = "confirming"
        elif correlation > 0.3 and vol_trend_ratio > 0.5:
            div_type = "weakening"
        elif correlation > 0.0:
            div_type = "diverging"
        else:
            div_type = "opposing"

        # ── Total ──
        total = max(0.0, min(100.0, corr_score + vol_trend_score + pressure_score + climax_score))

        return VolumeDivergenceResult(
            score=round(total, 1),
            price_obv_correlation=round(correlation, 3),
            correlation_score=round(corr_score, 1),
            volume_trend_ratio=round(vol_trend_ratio, 3),
            volume_trend_score=round(vol_trend_score, 1),
            buy_ratio=round(buy_ratio, 3),
            effective_ratio=round(effective_ratio, 3),
            pressure_score=round(pressure_score, 1),
            volume_climax_zscore=round(vol_zscore, 2),
            climax_score=round(climax_score, 1),
            divergence_type=div_type,
            data_points_used=n,
            volume_data_quality=quality,
        )

    # ─── Model 5b: Risk/Reward Shift (replaces Momentum Exhaustion in Phase 6) ──

    def compute_risk_reward(
        self,
        prices: np.ndarray,
        current_pnl_pct: float,
        peak_pnl_pct: float,
        hurst_value: float,
    ) -> RiskRewardResult:
        """Compute forward Expected Value of holding a position.

        Uses recent return distribution to estimate P(up/down) and magnitude,
        then computes whether holding or closing is mathematically correct.
        Incorporates Hurst (Phase 2) for mean-reversion adjustment and a
        profit amplifier for asymmetric loss aversion.

        Args:
            prices: Price array from buffer (100-720 points, 5s intervals).
            current_pnl_pct: Position's current PnL percentage.
            peak_pnl_pct: Highest PnL % this position has achieved.
            hurst_value: Hurst Exponent from Phase 2 (0-1). Cross-model dependency.

        Returns:
            RiskRewardResult with EV computation, probabilities, and amplified score.
        """
        n = len(prices)
        _default = RiskRewardResult(
            score=0.0, ev_hold=0.0, ev_ratio=0.0,
            p_up=0.5, p_down=0.5, p_up_empirical=0.5,
            avg_upside_per_tick=0.0, avg_downside_per_tick=0.0,
            expected_upside_5min=0.0, expected_downside_5min=0.0,
            mean_return=0.0, std_return=0.0, skewness=0.0,
            profit_amplifier=1.0, base_score=0.0,
            hurst_used=hurst_value, data_points_used=n,
        )

        if n < 30:
            return _default

        # ── Step 1: Return distribution ──
        analysis_prices = prices[-360:] if n > 360 else prices
        returns = np.diff(analysis_prices) / analysis_prices[:-1] * 100  # % returns
        nr = len(returns)
        if nr < 20:
            return _default

        # ── Step 2: Distribution statistics ──
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=1))

        # Manual skewness (no scipy)
        if std_ret > 1e-12 and nr >= 20:
            skew = float(np.sum(((returns - mean_ret) / std_ret) ** 3)) / nr
            skew = max(-3.0, min(3.0, skew))
        else:
            skew = 0.0

        # ── Step 3: Probabilities ──
        positive_count = int(np.sum(returns > 0))
        p_up_emp = positive_count / nr if nr > 0 else 0.5

        # Skewness adjustment
        skew_adj = 0.03 * skew
        p_up = float(np.clip(p_up_emp + skew_adj, 0.10, 0.90))

        # Hurst adjustment — mean reversion increases P(down)
        if hurst_value < 0.5:
            mr_penalty = (0.5 - hurst_value) * 0.5
            p_down_adj = min(0.90, (1.0 - p_up) + mr_penalty)
            p_up = 1.0 - p_down_adj

        p_down = 1.0 - p_up

        # ── Step 4: Average magnitudes ──
        pos_rets = returns[returns > 0]
        neg_rets = returns[returns < 0]

        avg_up = float(np.mean(pos_rets)) if len(pos_rets) >= 3 else (std_ret * 0.5 if std_ret > 0 else 0.0)
        avg_dn = float(abs(np.mean(neg_rets))) if len(neg_rets) >= 3 else (std_ret * 0.5 if std_ret > 0 else 0.0)

        # ── Step 5: Project to 5-min horizon (Hurst-aware scaling) ──
        horizon = 60  # 60 ticks = 5 minutes
        h_safe = max(0.1, min(0.9, hurst_value))
        scale = float(horizon ** h_safe)

        exp_up_5m = avg_up * scale * p_up
        exp_dn_5m = avg_dn * scale * p_down

        # ── Step 6: Expected Value ──
        ev_hold = exp_up_5m - exp_dn_5m

        if current_pnl_pct > 0.1:
            ev_ratio = ev_hold / current_pnl_pct
        elif current_pnl_pct < -0.1:
            ev_ratio = ev_hold / abs(current_pnl_pct)
        else:
            ev_ratio = ev_hold * 10

        # ── Step 7: Profit amplifier ──
        prof_amp = 1.0 + min(2.0, max(0.0, current_pnl_pct) * 0.1)

        # Drawdown amplifier
        drawdown_pct = peak_pnl_pct - current_pnl_pct
        if drawdown_pct > 0 and peak_pnl_pct > 1.0:
            dd_ratio = drawdown_pct / peak_pnl_pct
            prof_amp *= (1.0 + min(1.0, dd_ratio))

        prof_amp = min(4.0, prof_amp)

        # ── Step 8: Scoring ──
        if ev_ratio >= 0.5:
            base = max(0.0, (1.0 - ev_ratio) / 0.5 * 5)
        elif ev_ratio >= 0.0:
            base = 5.0 + (0.5 - ev_ratio) / 0.5 * 30
        elif ev_ratio >= -0.5:
            base = 35.0 + (-ev_ratio) / 0.5 * 30
        else:
            base = 65.0 + min(15.0, (-ev_ratio - 0.5) / 0.5 * 15)

        final = max(0.0, min(100.0, base * prof_amp))

        return RiskRewardResult(
            score=round(final, 1),
            ev_hold=round(ev_hold, 6),
            ev_ratio=round(ev_ratio, 4),
            p_up=round(p_up, 3),
            p_down=round(p_down, 3),
            p_up_empirical=round(p_up_emp, 3),
            avg_upside_per_tick=round(avg_up, 6),
            avg_downside_per_tick=round(avg_dn, 6),
            expected_upside_5min=round(exp_up_5m, 4),
            expected_downside_5min=round(exp_dn_5m, 4),
            mean_return=round(mean_ret, 6),
            std_return=round(std_ret, 6),
            skewness=round(skew, 3),
            profit_amplifier=round(prof_amp, 3),
            base_score=round(base, 1),
            hurst_used=round(hurst_value, 3),
            data_points_used=n,
        )

    # ─── OLD MODELS REMOVED (Phase 7 cleanup) ─────────────────────
    # calculate_velocity, calculate_volume_analysis, calculate_bollinger,
    # calculate_momentum — all replaced by compute_* methods above.
    # combine_raw_scores — replaced by ProfitSniper._compute_composite_score.
    # calculate_z_score — replaced by compute_hurst.
    # _interpolate helper still kept as module-level function (used nowhere now
    # but harmless and may be useful for future interpolation needs).

    _OLD_MODELS_REMOVED = True  # Phase 7 marker — grep verification
