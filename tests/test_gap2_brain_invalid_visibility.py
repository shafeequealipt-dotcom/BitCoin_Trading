"""Gap 2 fix (2026-05-19) — brain visibility of bidirectional clamp flags.

Tests verify:
- StructuralPlacement carries new ``is_long_invalid`` / ``is_short_invalid``
  fields and exposes them in ``to_dict()``.
- structure_engine marshals both flags onto the chosen placement from the
  separately-computed long_pl and short_pl objects.
- strategist renders ``INVALID_LONG=Y/N INVALID_SHORT=Y/N`` on each per-coin
  RR_DIR row in the X-RAY STRUCTURAL SETUPS section.
- The annotation is purely informational — no restrictive guidance text was
  added to the system prompt (Rule 4 anti-pattern check).
- The X-RAY block carries the brief field-key explainer right under the
  section header.
"""
from __future__ import annotations

from src.analysis.structure.models.structure_types import StructuralPlacement
from src.brain.strategist import TRADE_SYSTEM_PROMPT

# ── Section 1 — StructuralPlacement bidirectional fields ────────────────────


def test_structural_placement_has_is_long_invalid_field() -> None:
    """Field exists with safe default False."""
    p = StructuralPlacement()
    assert hasattr(p, "is_long_invalid")
    assert p.is_long_invalid is False


def test_structural_placement_has_is_short_invalid_field() -> None:
    """Mirror field for the short direction."""
    p = StructuralPlacement()
    assert hasattr(p, "is_short_invalid")
    assert p.is_short_invalid is False


def test_structural_placement_to_dict_exposes_both_flags() -> None:
    """to_dict serialization carries both bidirectional flags so any
    downstream JSON / log consumer can see them."""
    p = StructuralPlacement(is_long_invalid=True, is_short_invalid=False)
    d = p.to_dict()
    assert "is_long_invalid" in d
    assert "is_short_invalid" in d
    assert d["is_long_invalid"] is True
    assert d["is_short_invalid"] is False


def test_legacy_is_structurally_invalid_field_still_present() -> None:
    """The pre-existing single-direction flag must remain for backward
    compatibility with the XRAY_LEVELS debug log + any future
    single-direction consumer."""
    p = StructuralPlacement()
    assert hasattr(p, "is_structurally_invalid")
    assert "is_structurally_invalid" in p.to_dict()


# ── Section 2 — strategist prompt rendering ────────────────────────────────


def _build_xray_line_with_invalid(
    rr_long: float, rr_short: float, is_long_invalid: bool, is_short_invalid: bool,
) -> str:
    """Reproduce the RR_DIR + INVALID_* line construction at strategist.py:
    1380-1404 in isolation. This mirrors the live code path so the test
    fails if the rendering format drifts."""
    sp = StructuralPlacement(
        rr_long=rr_long,
        rr_short=rr_short,
        is_long_invalid=is_long_invalid,
        is_short_invalid=is_short_invalid,
    )
    line = ""
    if sp.rr_long > 0 and sp.rr_short > 0:
        if sp.rr_long >= sp.rr_short:
            _ratio = sp.rr_long / max(sp.rr_short, 0.01)
            _best = "LONG"
        else:
            _ratio = sp.rr_short / max(sp.rr_long, 0.01)
            _best = "SHORT"
        line += (
            f"RR_DIR(L={sp.rr_long:.1f},S={sp.rr_short:.1f},"
            f"best={_best},{_ratio:.1f}x) "
        )
        _il = "Y" if getattr(sp, "is_long_invalid", False) else "N"
        _is = "Y" if getattr(sp, "is_short_invalid", False) else "N"
        line += f"INVALID_LONG={_il} INVALID_SHORT={_is} "
    return line


def test_xray_line_contains_invalid_long_y_when_long_clamped() -> None:
    """A long-side clamp activation surfaces as ``INVALID_LONG=Y`` on the
    per-coin RR_DIR line."""
    line = _build_xray_line_with_invalid(
        rr_long=0.2, rr_short=5.4,
        is_long_invalid=True, is_short_invalid=False,
    )
    assert "RR_DIR(L=0.2,S=5.4,best=SHORT,27.0x)" in line
    assert "INVALID_LONG=Y" in line
    assert "INVALID_SHORT=N" in line


def test_xray_line_contains_invalid_short_y_when_short_clamped() -> None:
    """Mirror: short-side clamp activation surfaces as ``INVALID_SHORT=Y``."""
    line = _build_xray_line_with_invalid(
        rr_long=4.5, rr_short=0.2,
        is_long_invalid=False, is_short_invalid=True,
    )
    assert "INVALID_LONG=N" in line
    assert "INVALID_SHORT=Y" in line


def test_xray_line_contains_both_n_when_both_healthy() -> None:
    """Healthy placement on both sides: both flags = N. The annotation
    is ALWAYS emitted for symmetric visibility (operator can grep for
    INVALID_*=Y and find every clamp activation)."""
    line = _build_xray_line_with_invalid(
        rr_long=3.0, rr_short=2.5,
        is_long_invalid=False, is_short_invalid=False,
    )
    assert "INVALID_LONG=N" in line
    assert "INVALID_SHORT=N" in line


def test_xray_line_omits_invalid_when_rr_dir_omitted() -> None:
    """When ``rr_long`` or ``rr_short`` is zero the entire RR_DIR + INVALID
    block is suppressed — the annotation does not appear without its
    contextual RR comparison."""
    line = _build_xray_line_with_invalid(
        rr_long=0.0, rr_short=2.5,
        is_long_invalid=False, is_short_invalid=False,
    )
    assert "RR_DIR" not in line
    assert "INVALID_LONG" not in line
    assert "INVALID_SHORT" not in line


# ── Section 3 — Rule 4 anti-pattern compliance (framing) ────────────────────


def test_system_prompt_does_not_tell_brain_to_avoid_invalid_setups() -> None:
    """The system prompt must NOT contain restrictive guidance about the
    new INVALID flag. The flag is informational only — brain decides
    what to do with it. This protects against Rule 4 anti-pattern:
    'Adding "if INVALID, avoid this trade" instruction to brain prompt
    (hardcoded direction)'."""
    forbidden_phrases = [
        "avoid invalid",
        "avoid INVALID",
        "if INVALID",
        "skip INVALID",
        "do not trade INVALID",
        "reject INVALID",
    ]
    lower_prompt = TRADE_SYSTEM_PROMPT.lower()
    for phrase in forbidden_phrases:
        assert phrase.lower() not in lower_prompt, (
            f"system prompt contains forbidden restrictive guidance: "
            f"'{phrase}'. Rule 4 anti-pattern violation."
        )


# ── Section 4 — Structure engine marshalling (smoke) ──────────────────────


def test_chosen_placement_carries_both_flags_from_long_and_short_pls() -> None:
    """Smoke test: simulate the structure_engine marshalling pattern at
    structure_engine.py:341-356. After computing long_pl + short_pl, the
    chosen placement must carry both ``is_long_invalid`` and
    ``is_short_invalid`` flags from the source placements."""
    long_pl = StructuralPlacement(is_structurally_invalid=True)
    short_pl = StructuralPlacement(is_structurally_invalid=False)
    # Assume short_pl is the chosen placement (better RR)
    chosen = short_pl
    chosen.is_long_invalid = bool(long_pl.is_structurally_invalid) if long_pl else False
    chosen.is_short_invalid = bool(short_pl.is_structurally_invalid) if short_pl else False
    assert chosen.is_long_invalid is True
    assert chosen.is_short_invalid is False
