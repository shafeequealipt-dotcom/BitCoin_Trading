"""E17 structureless-high-score reject tests (Third Five, 2026-05-27).

Mirrors the new reject predicate in apex/gate.py (inserted AFTER the existing
all-low AND zero-conviction reject, which is left intact): reject ONLY when
X-RAY structural confidence <= conf_floor AND setup_score >= score_min. The
emphasis is the over-reject safeguard — a legitimate aggressive entry (which
always carries real confidence) must NEVER match. Pure-math mirror.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _structureless_reject(xray: float, setup: float,
                          conf_floor: float = 0.0, score_min: float = 999.0) -> bool:
    """Mirrors apex/gate.py E17: confidence at/below floor AND score at/above min."""
    return xray <= conf_floor and setup >= score_min


def test_structureless_high_score_rejected_at_live_values():
    """score 100 / confidence 0 with #7 bypassed -> rejected."""
    assert _structureless_reject(0.0, 100.0, 0.05, 65.0) is True


def test_sharp7_capped_none_not_rejected():
    """When #7 ran, a NONE setup is capped to score 49 -> below 65 -> NOT a
    reject (no double-jeopardy; #7 already demoted it)."""
    assert _structureless_reject(0.0, 49.0, 0.05, 65.0) is False


def test_legitimate_aggressive_not_culled():
    """The over-reject safeguard: a real aggressive entry (confidence 0.55,
    score 70) fails the confidence leg and is never a reject candidate."""
    assert _structureless_reject(0.55, 70.0, 0.05, 65.0) is False


def test_strong_a_plus_not_rejected():
    assert _structureless_reject(0.88, 88.0, 0.05, 65.0) is False


def test_defaults_are_inert():
    """At the dataclass defaults (conf_floor 0.0, score_min 999) even a
    score-100/conf-0 coin is NOT rejected -- the guard is opt-in."""
    assert _structureless_reject(0.0, 100.0, 0.0, 999.0) is False


def test_settings_defaults_inert():
    from src.config.settings import APEXSettings
    s = APEXSettings()
    assert s.gate_structureless_conf_floor == 0.0
    assert s.gate_structureless_score_min == 999.0


def test_config_toml_enables_live_values():
    """config.toml [apex] turns the guard on at 0.05 / 65."""
    from src.config.settings import Settings
    s = Settings._load_fresh()
    assert s.apex.gate_structureless_conf_floor == 0.05
    assert s.apex.gate_structureless_score_min == 65.0
