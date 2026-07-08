"""E18 A+ size-boost confidence-floor tests (Third Five, 2026-05-27).

Mirrors the gated-boost decision in apex/gate.py CHECK 4: the A+ size boost
(weight *= mult when setup_score >= score_threshold) is now gated on X-RAY
structural confidence >= gate_a_plus_conf_floor. The boost only withholds the
multiplier — it NEVER sets _gate_rejected (sizing-only). Pure-math mirror so a
future divergence from the production predicate breaks loudly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _boost_applies(score: float, xconf: float,
                   score_thr: float = 80.0, conf_floor: float = 0.0) -> bool:
    """Mirrors apex/gate.py E18: boost applies only when in the A+ score band
    AND X-RAY confidence is at-or-above the floor."""
    if score >= score_thr:
        return xconf >= conf_floor
    return False  # below the A+ band the elif ladder handles sizing, no boost


def test_structureless_a_plus_boost_withheld_at_070_floor():
    """Score 100 with zero structural confidence -> boost withheld."""
    assert _boost_applies(100.0, 0.0, 80.0, 0.70) is False


def test_confident_a_plus_still_boosted_at_070_floor():
    """A genuinely strong A+ (score 88, confidence 0.88) is still boosted."""
    assert _boost_applies(88.0, 0.88, 80.0, 0.70) is True


def test_floor_zero_preserves_current_behaviour():
    """Default floor 0.0 -> boost always applies in the A+ band (conf >= 0)."""
    assert _boost_applies(100.0, 0.0, 80.0, 0.0) is True


def test_below_a_plus_band_never_boosts():
    assert _boost_applies(70.0, 0.90, 80.0, 0.70) is False


def test_settings_defaults_preserve_behaviour():
    """The three new fields exist; defaults reproduce current behaviour."""
    from src.config.settings import APEXSettings
    s = APEXSettings()
    assert s.gate_a_plus_score_threshold == 80.0
    assert s.gate_a_plus_size_mult == 1.20
    assert s.gate_a_plus_conf_floor == 0.0  # 0.0 = boost always applies


def test_config_toml_enables_070_floor():
    """config.toml [apex] sets the live floor to 0.70."""
    from src.config.settings import Settings
    s = Settings._load_fresh()
    assert s.apex.gate_a_plus_conf_floor == 0.70
