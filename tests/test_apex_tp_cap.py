"""Phase 5 of dir-block-fix (2026-05-05) — APEX TP cap recalibration.

Two smoke tests for the raised class multipliers + hard-ceiling cap +
new `was_reduced` semantics. The cap formula and emission are inside
``TradeOptimizer.optimize`` and require a full APEX run to exercise
end-to-end; these tests assert on the dataclass-default invariants
that drive the formula so a future config-touch can't silently revert
the recalibration.
"""

from __future__ import annotations

from src.config.settings import APEXSettings


def test_tp_cap_multipliers_raised() -> None:
    """Phase 5 raised every class multiplier so Qwen's TPs flow more
    often when structure supports them.
    """
    cfg = APEXSettings()
    m = cfg.tp_cap_multiplier_by_class
    assert m["dead"] == 1.4
    assert m["low"] == 1.5
    assert m["medium"] == 1.6
    assert m["high"] == 1.8
    assert m["extreme"] == 2.0


def test_hard_ceiling_default_is_5pct() -> None:
    """The hard upper-bound ceiling on the TP cap (independent of
    class multiplier) defaults to 5.0 %. Any ``recTP × mult`` above
    this value is clamped down by the ``min(...)`` in
    ``optimize()``.
    """
    cfg = APEXSettings()
    assert cfg.apex_tp_cap_hard_ceiling_pct == 5.0
    # Sanity: a high-class coin with recTP=4 % would compute
    # cap = min(4 × 1.8, 5.0) = 5.0 — ceiling kicks in.
    assert min(4.0 * 1.8, cfg.apex_tp_cap_hard_ceiling_pct) == 5.0
    # And a medium-class coin with recTP=1.1 % would compute
    # cap = min(1.1 × 1.6, 5.0) ≈ 1.76 % — multiplier wins. (Float-precision-safe.)
    assert abs(min(1.1 * 1.6, cfg.apex_tp_cap_hard_ceiling_pct) - 1.76) < 1e-6


# ── Layer 1 Defect 7 — TP-cap source reconciliation ──────────────────


def test_models_display_map_matches_settings_default() -> None:
    """Layer 1 Defect 7 regression guard: apex/models.py's
    _CAP_MULT_MAP_DISPLAY is the source of the TP_CAP value rendered
    into DeepSeek's prompt. It must equal the settings dataclass
    default at APEXSettings.tp_cap_multiplier_by_class. Historical
    drift left the display at {1.2,1.3,1.3,1.4,1.5} while the
    optimizer enforced {1.4,1.5,1.6,1.8,2.0} from settings, so
    DeepSeek self-limited against a tighter cap than the optimizer
    would have allowed.
    """
    from src.apex.models import _CAP_MULT_MAP_DISPLAY
    cfg = APEXSettings()
    for cls, mult in cfg.tp_cap_multiplier_by_class.items():
        assert cls in _CAP_MULT_MAP_DISPLAY, (
            f"models.py display map missing class {cls!r} present in "
            f"settings — DeepSeek would see a hardcoded fallback "
            f"instead of the configured multiplier."
        )
        assert abs(_CAP_MULT_MAP_DISPLAY[cls] - mult) < 1e-9, (
            f"Class {cls!r}: models.py display map "
            f"({_CAP_MULT_MAP_DISPLAY[cls]}) diverged from settings "
            f"({mult}). DeepSeek would self-limit to one cap while "
            f"the optimizer enforces another."
        )


def test_prompt_text_no_longer_hardcodes_one_three_x() -> None:
    """Layer 1 Defect 7: the APEX system prompt at prompts.py:66 used
    to hardcode '1.3x recTP%' which contradicted both the optimizer
    enforcement and the display map. The fix generalises the language
    to refer to the displayed TP_CAP without naming a specific
    multiplier."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src" / "apex" / "prompts.py").read_text()
    assert "1.3x recTP" not in src, (
        "prompts.py still hardcodes '1.3x recTP%' in the TP HARD CAP "
        "instruction. After Defect 7 this should be class-agnostic so "
        "the model trusts the per-class TP_CAP shown in coin data."
    )
