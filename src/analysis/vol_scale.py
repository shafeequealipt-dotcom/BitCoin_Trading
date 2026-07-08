"""Pure-function helpers for volatility-class-aware parameter scaling.

No IO, no state, no dependencies beyond typing. Single source of truth for
callers that need "given a vol_class, scale a value" or "given ATR + class,
compute an effective min-distance". Keeps the same formula in one place so
Profit Sniper's pre-screen and SL Gateway's R2 enforcement never drift.

Conventions:
    vol_class ∈ {"dead","low","medium","high","extreme"} or None.
    Unknown / None → treat as "medium" for defaulting.
    All percentages are in % (not fractions): 0.3 = 0.3 %, NOT 30 %.
"""

from __future__ import annotations

from collections.abc import Mapping

CLASS_ORDER = ("dead", "low", "medium", "high", "extreme")
_DEFAULT_CLASS = "medium"


def scale_by_class(
    value: float,
    vol_class: str | None,
    factors: Mapping[str, float],
    default_class: str = _DEFAULT_CLASS,
) -> float:
    """Return ``value * factors[vol_class]``, falling back to factors[default_class].

    Used for the TP cap multiplier (APEX), per-class grace/ATR multipliers
    (Time-Decay), or any other "baseline × class-factor" scaling.

    Args:
        value: Baseline scalar to scale.
        vol_class: "dead"/"low"/"medium"/"high"/"extreme" or None.
        factors: Mapping class → multiplier. Missing keys fall through to
            factors[default_class]; if default_class is also missing, we
            return the unscaled value (factor 1.0) — never crash.
        default_class: Class to use when vol_class is None or not in factors.

    Returns:
        Scaled value. Finite when inputs are finite; no rounding applied
        (callers round as appropriate for their output precision).
    """
    key = vol_class if (vol_class and vol_class in factors) else default_class
    mult = factors.get(key, factors.get(default_class, 1.0))
    return value * mult


def min_distance_for_class(
    atr_5m_pct: float,
    vol_class: str | None,
    sl_cfg,
) -> float:
    """Compute the ATR-scaled min_distance % for SL Gateway R2 / Sniper.

    Spec formula (user-approved for "exploit market for max profit"):

        min_distance = max(abs_floor_pct, atr_5m_pct * atr_multiplier)

    A per-class ceiling clamps pathological values (e.g. freak ATR spikes
    during flash crashes). When atr_5m_pct <= 0 (profiler unavailable or
    cold state), we return the legacy global min_distance_pct so callers
    degrade gracefully to pre-fix behaviour.

    Args:
        atr_5m_pct: The coin's 5-minute Normalized ATR as a percentage.
        vol_class: The coin's volatility class or None.
        sl_cfg: Settings object exposing:
            - min_distance_atr_multiplier: float (default 0.5)
            - min_distance_abs_floor_pct: float (default 0.05)
            - min_distance_class_ceiling: dict[str, float] (default {})
            - min_distance_pct: float (legacy global, used as cold-ATR fallback)

    Returns:
        Effective min_distance in %, to be compared against abs(new_sl - price) / price * 100.

    Edge cases (all safe):
        * atr_5m_pct <= 0 → legacy global (no scaling).
        * atr_5m_pct > 0 but tiny → absolute floor protects (e.g. 0.05 %).
        * atr_5m_pct huge (flash crash) → class ceiling clamps down.
        * vol_class unknown → medium-class ceiling used.
        * sl_cfg missing a field → getattr with sensible default.
    """
    if atr_5m_pct <= 0:
        return float(getattr(sl_cfg, "min_distance_pct", 0.3))

    mult = float(getattr(sl_cfg, "min_distance_atr_multiplier", 0.5))
    abs_floor = float(getattr(sl_cfg, "min_distance_abs_floor_pct", 0.05))
    base = max(abs_floor, atr_5m_pct * mult)

    ceiling_map = getattr(sl_cfg, "min_distance_class_ceiling", None) or {}
    key = vol_class if (vol_class and vol_class in ceiling_map) else _DEFAULT_CLASS
    # Ceiling 5.0 % is an emergency cap — anything above that is a bug or a
    # flash crash; the SL would be unacceptably far regardless.
    ceiling = float(ceiling_map.get(key, 5.0))
    return min(base, ceiling)


# ──────────────────────────────────────────────────────────────────────────
# Dynamic Adaptive Exit geometry (2026-06-15).
#
# Every exit threshold is a bounded multiple of R — the coin's ATR-as-percent,
# the movement unit — and every PROFIT threshold is floored at the trade's
# round-trip fee so a locked win is net-positive. These stay pure functions of
# (R, cfg): no state, no IO, so each open position computes its own geometry
# independently of every other (per-trade, parallel, non-colliding). The
# existing owner functions (the ladder, the trail, the graduation latch, the
# hard stop, the initial stop, the development guard) fetch R from the existing
# volatility profiler and the fee from config, call these, and feed the result
# into their UNCHANGED logic — the hierarchy that decides WHO writes the stop is
# not touched. Every coefficient lives in the [adaptive_exit] config section
# (``cfg`` below); nothing is hardcoded inline here. All percentages are in %
# (0.3 = 0.3 %), matching the rest of this module.
#
# The starting multiples were tuned on the replay against the real logged
# trades (simulate_adaptive_exit_replay.py); they remain bounded and centralized
# so they can be re-tuned without touching code.
# ──────────────────────────────────────────────────────────────────────────


def _bounded(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` to [lo, hi]. An ``hi`` of 0 (or negative) means no upper
    bound (the value is already naturally bounded by its inputs, e.g. a lock can
    never exceed the peak it trails)."""
    if value < lo:
        value = lo
    if hi > 0 and value > hi:
        value = hi
    return value


def fee_floor_pct(cfg) -> float:
    """The net-positive floor beneath every profit threshold: the round-trip fee
    times a small safety buffer. This is the spine of the design — the arm, the
    locks, and the take-profit are never set below it, so a locked win clears
    cost and is real net profit, not a gross figure fees erase."""
    fee = float(getattr(cfg, "round_trip_fee_pct", 0.11))
    buf = float(getattr(cfg, "fee_floor_buffer", 1.0))
    return max(0.0, fee * buf)


def arm_pct(R: float, cfg, fee: float | None = None) -> float:
    """Profit (in %) the trade must reach before the ladder/trail/graduation
    arm: the larger of ``arm_r`` × R and the fee floor, bounded. On a volatile
    coin R dominates (arm later); on a quiet coin the fee floor dominates (arm
    earlier, each at its own scale)."""
    if fee is None:
        fee = fee_floor_pct(cfg)
    arm_r = float(getattr(cfg, "arm_r", 0.5))
    val = max(arm_r * max(R, 0.0), fee)
    return _bounded(val, float(getattr(cfg, "arm_min_pct", 0.0)),
                    float(getattr(cfg, "arm_max_pct", 1.0)))


def effective_trail_r(peak_pct: float, R: float, cfg) -> float:
    """The profit-scaled trail coefficient (in R units) that sits behind the
    running peak — the single source of truth for how tightly the adaptive lock
    trails (2026-06-26 give-back fix).

    Below the knee the coefficient equals ``trail_r`` (the unchanged half-R
    trail), so a young or small mover is byte-identical to the pre-fix behaviour
    — this knee is the over-tighten guarantee that keeps the system from cutting
    unproven winners short on noise. Above the knee the coefficient decays
    smoothly toward ``trail_r_floor``, so a larger green trade locks progressively
    nearer its peak: protection tightens as profit grows, which is the whole point
    of the fix. The decay is::

        peak_r  = peak_pct / R                         # profit in R units
        excess  = max(0, peak_r - trail_tighten_knee_r)
        decay   = 1 / (1 + excess / trail_tighten_scale_r)   # 1 at the knee → 0 far above
        eff     = trail_r_floor + (trail_r - trail_r_floor) * decay

    It is bounded in ``[trail_r_floor, trail_r]`` by construction (decay ∈ (0, 1]),
    and it can only ever shrink the trail distance — never widen it — so the lock
    this feeds is always at or above today's lock (tighten-only is preserved).
    A positive ``trail_r_floor`` guarantees a ``trail_r_floor × R`` buffer below
    the peak, so the lock can never reach the peak itself.

    Behaviour-neutral default: ``trail_r_floor`` defaults to ``trail_r`` (0.5),
    which makes ``trail_r - trail_r_floor`` zero and the coefficient a constant
    ``trail_r`` regardless of the knee/scale — identical to the pre-fix geometry.
    Activation is a single config flip of ``trail_r_floor`` below ``trail_r``.
    """
    trail_r = float(getattr(cfg, "trail_r", 0.5))
    trail_r_floor = float(getattr(cfg, "trail_r_floor", trail_r))
    # Defensive clamp (the validator enforces the same on load): the floor must be
    # a positive fraction no larger than trail_r so the buffer below the peak is
    # real and the coefficient never widens the trail. A raw/legacy cfg that
    # violates this degrades to the unchanged half-R trail rather than misbehaving.
    if not (0.0 < trail_r_floor <= trail_r):
        trail_r_floor = trail_r
    if R <= 0 or trail_r_floor == trail_r:
        return trail_r
    knee = float(getattr(cfg, "trail_tighten_knee_r", 1.0))
    scale = float(getattr(cfg, "trail_tighten_scale_r", 1.0))
    if scale <= 0:
        return trail_r
    excess = max(0.0, peak_pct / R - knee)
    decay = 1.0 / (1.0 + excess / scale)
    return trail_r_floor + (trail_r - trail_r_floor) * decay


def profit_lock_pct(peak_pct: float, R: float, cfg, fee: float | None = None):
    """The guaranteed-profit lock (in % above entry) as a function of the running
    peak: the staged ladder capture combined with the R-fraction trail behind
    the peak, floored at the fee. Returns None until the peak reaches the arm.

    Staged capture (blueprint Part 5): at the first rung the stop becomes a free
    roll (break-even-plus = the fee floor); at the middle rung it secures real
    profit; above that the trail behind the peak runs. The trail sits an
    ``effective_trail_r`` fraction of R behind the running peak — a fraction that
    starts at ``trail_r`` for young/small movers and tightens toward
    ``trail_r_floor`` as the peak grows in R units (the 2026-06-26 give-back fix),
    so protection tightens as profit grows. Naturally bounded above by the peak.
    """
    if fee is None:
        fee = fee_floor_pct(cfg)
    if R <= 0:
        return None
    arm = arm_pct(R, cfg, fee)
    if peak_pct < arm:
        return None
    rungs = list(getattr(cfg, "rung_r", (1.5, 3.0, 5.0)))
    trail = peak_pct - effective_trail_r(peak_pct, R, cfg) * R
    staged = 0.0
    if len(rungs) >= 2 and peak_pct >= rungs[1] * R:        # middle rung: secure
        staged = float(getattr(cfg, "secure_at_3r_r", 1.5)) * R
    elif len(rungs) >= 1 and peak_pct >= rungs[0] * R:      # first rung: free roll
        staged = fee
    lock = max(fee, trail, staged)
    return _bounded(lock, fee, float(getattr(cfg, "lock_max_pct", 0.0)))


def hard_stop_pct(R: float, cfg) -> float:
    """The wide watchdog hard stop (in %): ``hard_stop_r`` × R, bounded. It is a
    vol-scaled backstop that sits between the trade and the sacred catastrophic
    cap; the cap is computed and enforced separately and always fires first, so
    this never weakens it. The upper bound keeps it sane on a volatile coin."""
    hs_r = float(getattr(cfg, "hard_stop_r", 9.0))
    return _bounded(hs_r * max(R, 0.0),
                    float(getattr(cfg, "hard_stop_min_pct", 2.5)),
                    float(getattr(cfg, "hard_stop_max_pct", 10.0)))
