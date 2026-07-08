"""Phase 1 — contrarian-long label calibration self-verification (2026-06-08).

The signal-classifier F&G-neutral fix is confirm-only (no change). The residual:
the EXTREME_FEAR_CONTRARIAN_LONG label fired at a GLOBAL F&G-only confidence
(0.64 in extreme fear F&G=8), making it the uniform-confidence primary on ~87%
of candidates regardless of the coin's own structure.

The calibration (in src/workers/scanner/state_labeler.py) scales that confidence
by the coin's OWN structural conviction (setup_type_confidence) against a floor,
and broadens the counter-regime haircut to off-trend/dead regimes. The
directional anchor (the coin's own long read) is untouched — neutrality, not a
flip.

OFF (pre-calibration: floor=1.0, offtrend=False) reproduces the uniform 0.64
stamp; ON (floor=0.35, offtrend=True) spreads the confidence by per-coin
structure and regime, so a structure-blind coin floors low and a structured coin
keeps a high score. A coin whose own read is SHORT never fires the long label
(no flip).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.workers.scanner.state_labeler import _trigger_extreme_fear_long

FG = 8  # extreme fear -> fear_extremity = 0.40 + (20-8)/50 = 0.64
OFF = dict(conviction_floor=1.0, offtrend_haircut=False, regime_haircut=0.5)
ON = dict(conviction_floor=0.35, offtrend_haircut=True, regime_haircut=0.5)


def fear(regime: str, stc: float, mode: dict, direction: str = "long") -> float | None:
    return _trigger_extreme_fear_long(
        fear_greed=FG, regime=regime,
        consensus_direction=direction, trade_direction=direction,
        setup_type_confidence=stc, **mode,
    )


# Structure-blind coin in a ranging regime (the dominant live case).
blind_off = fear("ranging", 0.0, OFF)
blind_on = fear("ranging", 0.0, ON)
# Coin with genuine bullish structure, ranging regime.
strong_off = fear("ranging", 0.85, OFF)
strong_on = fear("ranging", 0.85, ON)
# Strong structure but an OFF-TREND (dead) regime where a contrarian-long has no edge.
dead_on = fear("dead", 0.85, ON)
# Neutrality: a coin whose own read is SHORT must NOT fire the long label.
short_off = fear("ranging", 0.85, OFF, direction="short")
short_on = fear("ranging", 0.85, ON, direction="short")

print("=== Phase 1 label calibration (real _trigger_extreme_fear_long, F&G=8) ===")
print(f"structure-blind ranging : OFF={blind_off:.3f}  ON={blind_on:.3f}")
print(f"strong-structure ranging: OFF={strong_off:.3f}  ON={strong_on:.3f}")
print(f"strong-structure DEAD    : OFF={fear('dead',0.85,OFF):.3f}  ON={dead_on:.3f}")
print(f"own-read SHORT (no flip) : OFF={short_off}  ON={short_on}")

# OFF is the uniform 0.64 stamp regardless of structure/regime (the bug).
assert abs(blind_off - 0.64) < 1e-6 and abs(strong_off - 0.64) < 1e-6, "OFF should be the uniform 0.64 stamp"
assert abs(fear("dead", 0.85, OFF) - 0.64) < 1e-6, "OFF gives full 0.64 even in a dead regime (the bug)"

# ON spreads by per-coin structure: a structure-blind coin floors LOW...
assert blind_on < blind_off - 0.3, "ON should floor a structure-blind coin well below 0.64"
assert abs(blind_on - 0.64 * 0.35) < 1e-6, "structure-blind ON = 0.64 * floor 0.35 = 0.224"
# ...a structured coin keeps a HIGH score...
assert strong_on > blind_on + 0.25, "ON: a structured coin must outrank a structure-blind one"
assert abs(strong_on - 0.64 * 0.85) < 1e-6, "structured ON = 0.64 * 0.85 = 0.544"
# ...and an off-trend regime is additionally haircut.
assert dead_on < strong_on, "ON: an off-trend (dead) regime is haircut below the same coin in ranging"
assert abs(dead_on - 0.64 * 0.85 * 0.5) < 1e-6, "off-trend ON = 0.64 * 0.85 * haircut 0.5 = 0.272"

# Neutrality: the long label never fires on a coin whose own read is short.
assert short_off is None and short_on is None, "the contrarian-LONG label must not fire on a short read (no flip)"

print(
    "\nPASS: OFF is the uniform 0.64 stamp; ON ranks by the coin's own structure "
    "(blind 0.224 < structured 0.544) and haircuts off-trend regimes (dead 0.272); "
    "the long-only anchor is preserved (short read -> no fire, no flip)."
)
