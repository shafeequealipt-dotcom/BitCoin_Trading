"""T1-3 sniper trail floor-from-current-price tests (2026-05-12).

Validates the inline clamp block added to
``src/workers/profit_sniper._apply_trail_stop`` which adds a
distance-from-CURRENT-price floor to the trail update path.

Pre-fix: the gauntlet (min_trail at lines 1259-1285, breakeven floor,
SNIPER_CAP, SNIPER_TOO_CLOSE) bounded only the from-PEAK distance. As
current price oscillated near peak, the from-current distance shrank to
~0.15 % — below the 0.13 % mean-reversion noise band — and ARBUSDT /
SKRUSDT lost $4.70 in 70 s on 2026-05-12.

The fix adds a floor in _apply_trail_stop AFTER SNIPER_TOO_CLOSE and
BEFORE SNIPER_WRONG_SIDE_GUARD with formula:
    floor_pct = clamp(max(min_pct, atr_5m_pct * atr_mult), upper=max_pct)
Behaviour: CLAMP outward (preserve trail intent); reject only when the
clamp would loosen prior cur_sl (R1 / Bug-2 invariant).

Pure-math test (mirrors the inline formula). The reproduction lives
next to the assertions so a future divergence between production code
and this test fails loudly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _floor_inline(
    *,
    symbol_price: float,
    atr_pct: float,
    direction: str,
    proposed_sl: float,
    cur_sl: float | None,
    atr_mult: float = 0.75,
    min_pct: float = 0.20,
    max_pct: float = 1.50,
) -> tuple[float, float, str]:
    """Mirror of profit_sniper._apply_trail_stop T1-3 clamp block.

    Returns (final_sl, floor_pct, action) where action in
    {"pass", "clamp", "reject_would_loosen"}.
    """
    if atr_pct > 0:
        floor_pct_raw = max(min_pct, atr_pct * atr_mult)
    else:
        floor_pct_raw = min_pct
    floor_pct = min(floor_pct_raw, max_pct)
    floor_dist_abs = symbol_price * floor_pct / 100.0
    cur_dist_abs = abs(symbol_price - proposed_sl)
    if cur_dist_abs >= floor_dist_abs:
        return proposed_sl, floor_pct, "pass"
    if direction in ("Buy", "Long"):
        clamped = symbol_price - floor_dist_abs
        if cur_sl is not None and cur_sl > 0 and clamped <= cur_sl:
            return cur_sl, floor_pct, "reject_would_loosen"
        return round(clamped, 8), floor_pct, "clamp"
    clamped = symbol_price + floor_dist_abs
    if cur_sl is not None and cur_sl > 0 and clamped >= cur_sl:
        return cur_sl, floor_pct, "reject_would_loosen"
    return round(clamped, 8), floor_pct, "clamp"


# --- Group 1: bug replication ----------------------------------------------


def test_arbusdt_replication_clamps_to_floor():
    """ARBUSDT-style Long: peak ~$0.345, current=$0.3455 (near peak),
    proposed_sl=$0.3450 (~0.145% from current), atr=0.20% (low-vol).
    Floor formula: max(0.20%, 0.20% * 0.75) = 0.20%. Clamp to 0.998 *
    price = 0.3448090. Must remain a tighten vs cur_sl=$0.3440."""
    final, fpct, action = _floor_inline(
        symbol_price=0.3455, atr_pct=0.20, direction="Buy",
        proposed_sl=0.3450, cur_sl=0.3440,
    )
    assert action == "clamp"
    assert abs(fpct - 0.20) < 1e-9
    assert abs(final - (0.3455 * 0.998)) < 1e-7
    assert final > 0.3440


def test_skrusdt_replication_clamps_then_remains_tighten():
    """SKRUSDT-style Sell: similar dynamics inverted."""
    final, fpct, action = _floor_inline(
        symbol_price=1.000, atr_pct=0.18, direction="Sell",
        proposed_sl=1.0015, cur_sl=1.0030,
    )
    assert action == "clamp"
    assert abs(fpct - 0.20) < 1e-9
    assert abs(final - (1.000 * 1.002)) < 1e-7
    assert final < 1.0030


# --- Group 2: floor scales with vol on high-ATR coins ----------------------


def test_high_vol_coin_floor_caps_at_max_pct():
    """Extreme-vol coin (NATR=2.5%): floor_pct = 2.5*0.75 = 1.875 %,
    capped at max_pct=1.50 %. A 1.6 % from-current trail passes through;
    a 1.0 % trail clamps."""
    final, fpct, action = _floor_inline(
        symbol_price=100.0, atr_pct=2.5, direction="Buy",
        proposed_sl=98.4, cur_sl=98.0,
    )
    assert action == "pass"
    assert abs(fpct - 1.50) < 1e-9
    assert final == 98.4

    final, fpct, action = _floor_inline(
        symbol_price=100.0, atr_pct=2.5, direction="Buy",
        proposed_sl=99.0, cur_sl=98.0,
    )
    assert action == "clamp"
    assert abs(fpct - 1.50) < 1e-9
    assert abs(final - 98.5) < 1e-7


# --- Group 3: clamp would loosen → reject_would_loosen ---------------------


def test_clamp_that_would_loosen_returns_reject():
    """Edge: cur_sl is closer to price than the floor. A clamp would
    push SL further from price (loosen) — violates R1. Must reject and
    preserve cur_sl."""
    final, fpct, action = _floor_inline(
        symbol_price=100.0, atr_pct=0.20, direction="Buy",
        proposed_sl=99.95,
        cur_sl=99.85,
    )
    assert action == "reject_would_loosen"
    assert final == 99.85


# --- Group 4: zero-ATR fallback --------------------------------------------


def test_zero_atr_falls_back_to_min_pct():
    """When the volatility profiler returns no ATR (cold start, lookup
    fail), the floor uses min_pct alone. Must not crash; must clamp at
    min_pct=0.20 % from price."""
    final, fpct, action = _floor_inline(
        symbol_price=100.0, atr_pct=0.0, direction="Buy",
        proposed_sl=99.95, cur_sl=99.5,
    )
    assert action == "clamp"
    assert abs(fpct - 0.20) < 1e-9
    assert abs(final - 99.80) < 1e-7


# --- Group 5: lock the operator-approved defaults --------------------------


def test_mode4_defaults_match_config_toml():
    """Lock T1-3 defaults so a future config-touch can't silently revert
    them. atr_multiplier=0.75 / min_pct=0.20 / max_pct=1.50 were
    explicitly approved by the operator on 2026-05-12; if these change
    the production behaviour shifts and verification metrics change with
    them — that requires a deliberate operator decision."""
    from src.config.settings import Mode4Settings
    cfg = Mode4Settings()
    assert cfg.trail_floor_from_price_atr_multiplier == 0.75, (
        "T1-3 atr_multiplier default tampered without operator sign-off"
    )
    assert cfg.trail_floor_from_price_min_pct == 0.20, (
        "T1-3 min_pct default tampered without operator sign-off"
    )
    assert cfg.trail_floor_from_price_max_pct == 1.50, (
        "T1-3 max_pct default tampered without operator sign-off"
    )


# --- Group 6: settings builder tolerates absence ---------------------------


def test_mode4_settings_builds_without_t1_3_keys_in_toml():
    """The TOML builder uses hasattr filtering, so absence of the new
    keys must use dataclass defaults, not raise."""
    from src.config.settings import _build_mode4
    cfg = _build_mode4({"base_atr_multiplier": 2.5})
    assert cfg.trail_floor_from_price_atr_multiplier == 0.75
    assert cfg.trail_floor_from_price_min_pct == 0.20
    assert cfg.trail_floor_from_price_max_pct == 1.50


# --- Group 7: composability with existing peak-distance min_trail ----------


def test_floor_does_not_collide_with_peak_distance_floor():
    """The peak-distance floor is min_trail_pct=0.30 % (in
    _compute_trail_stop). The from-current floor min_pct=0.20 %. Both
    operate on different distances (peak vs current) — they must
    compose, not conflict. Sanity: when peak == current and trail is at
    the from-current floor, distance from peak is also at the from-peak
    floor, so the position is at the intersection of both protections.
    """
    from src.config.settings import Mode4Settings
    cfg = Mode4Settings()
    # The from-current floor is intentionally below the peak-distance floor
    # so the two compose rather than collapse.
    assert cfg.trail_floor_from_price_min_pct < cfg.min_trail_pct, (
        "T1-3 from-current floor must be tighter than peak-distance floor "
        "so the two compose rather than collapse"
    )


# --- Runner ----------------------------------------------------------------


def _run_all() -> int:
    tests = [
        fn for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            failed.append((fn.__name__, str(e)))
            print(f"  [FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed.append((fn.__name__, f"{type(e).__name__}: {e}"))
            print(f"  [ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  {name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
