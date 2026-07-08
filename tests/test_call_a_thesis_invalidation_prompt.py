"""Phase 3.2 — Mid-Hold Trade Management Fix: CALL_A system prompt schema.

Verifies that both production system prompts (TRADE_SYSTEM_PROMPT and
TRADE_SYSTEM_PROMPT_ZERO_TWO) teach the brain to provide a
``thesis_invalidation`` field in the new_trades JSON, and that the new
guidance is information-only (no Rule 4 directive language).

Per IMPLEMENT_MIDHOLD doc Rule 16: Approach C is primary. The brain
states the invalidation criterion in the CALL_A response. The watchdog
later monitors whatever criterion the brain specified (or falls back to
Approach A heuristic on missing/invalid).
"""

from __future__ import annotations

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_THESIS_INVALIDATION_VERSION,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
)


# ════════════════════════════════════════════════════════════════════
# 1. Schema teaching present in both production prompts
# ════════════════════════════════════════════════════════════════════


def test_trade_system_prompt_has_thesis_invalidation_field_in_schema() -> None:
    """The JSON schema example must include thesis_invalidation."""
    assert '"thesis_invalidation"' in TRADE_SYSTEM_PROMPT


def test_zero_two_prompt_has_thesis_invalidation_field_in_schema() -> None:
    """The zero_two variant must also include thesis_invalidation."""
    assert '"thesis_invalidation"' in TRADE_SYSTEM_PROMPT_ZERO_TWO


def test_both_prompts_carry_thesis_invalidation_section() -> None:
    """Both prompts must explain the four type options."""
    for prompt_name, prompt in (
        ("TRADE_SYSTEM_PROMPT", TRADE_SYSTEM_PROMPT),
        ("TRADE_SYSTEM_PROMPT_ZERO_TWO", TRADE_SYSTEM_PROMPT_ZERO_TWO),
    ):
        assert "THESIS INVALIDATION" in prompt, (
            f"{prompt_name} missing THESIS INVALIDATION section header"
        )
        for keyword in (
            "price_close_above",
            "price_close_below",
            "signal",
        ):
            assert keyword in prompt, (
                f"{prompt_name} missing thesis_invalidation type keyword "
                f"{keyword!r}"
            )


def test_both_prompts_enumerate_signal_keywords() -> None:
    """The four signal-type keywords must be documented in both prompts."""
    expected_signals = {
        "ensemble_flip_to_strong_buy",
        "ensemble_flip_to_strong_sell",
        "regime_inverted",
        "mtf_alignment_broken",
    }
    for prompt_name, prompt in (
        ("TRADE_SYSTEM_PROMPT", TRADE_SYSTEM_PROMPT),
        ("TRADE_SYSTEM_PROMPT_ZERO_TWO", TRADE_SYSTEM_PROMPT_ZERO_TWO),
    ):
        for kw in expected_signals:
            assert kw in prompt, (
                f"{prompt_name} missing signal keyword {kw!r}"
            )


# ════════════════════════════════════════════════════════════════════
# 2. Rule 4 anti-pattern guard — information only, no directives
# ════════════════════════════════════════════════════════════════════


def test_prompt_does_not_directive_close_on_invalidation() -> None:
    """The brain must decide. The new section must not tell the brain
    to close, exit, or avoid when invalidation fires. Mirrors the
    existing forbidden-phrases test at
    tests/test_gap2_brain_invalid_visibility.py."""
    forbidden = [
        "close if invalidated",
        "close on invalidation",
        "exit if invalidated",
        "exit on invalidation",
        "avoid invalidated",
        "skip invalidated",
        "if invalidated, close",
        "if invalidated, exit",
        "must close",
        "must exit",
        "rule: close",
        "rule: exit",
    ]
    for prompt_name, prompt in (
        ("TRADE_SYSTEM_PROMPT", TRADE_SYSTEM_PROMPT),
        ("TRADE_SYSTEM_PROMPT_ZERO_TWO", TRADE_SYSTEM_PROMPT_ZERO_TWO),
    ):
        lower = prompt.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lower, (
                f"{prompt_name} contains forbidden directive phrase "
                f"{phrase!r} — Rule 4 anti-pattern violation"
            )


def test_prompt_explicitly_says_information_only() -> None:
    """The section must explicitly frame the watchdog surfacing as
    information supply, not directive — same framing as the
    bidirectional is_long_invalid/is_short_invalid flags (Phase C of
    direction-bias series)."""
    for prompt in (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO):
        lower = prompt.lower()
        # One of these phrasings should be present per Rule 4 framing.
        framing_present = (
            "information supply" in lower
            or "not a directive" in lower
            or "you decide what to do" in lower
            or "decide what to do with that information" in lower
        )
        assert framing_present, (
            "thesis_invalidation section must explicitly frame as "
            "information-only — Rule 4 requirement"
        )


# ════════════════════════════════════════════════════════════════════
# 3. Schema growth is bounded
# ════════════════════════════════════════════════════════════════════


def test_prompt_growth_bounded() -> None:
    """Per IMPLEMENT doc Risk 6 — prompt growth must be bounded."""
    # Conservative bound: the additions for THESIS INVALIDATION + the
    # bullet line + the JSON example field add roughly 700-1200 chars.
    # The legacy TRADE_SYSTEM_PROMPT was ~5.5 KB pre-Phase 3.2; bound
    # this at 9 KB to allow headroom for future tuning passes (3.10).
    # Fund-management fix (2026-05-31) raised the bound 9 KB -> 9.5 KB: the
    # size_usd PROPER-FUNDING instruction (deploy real capital as a share of
    # "Available for new trades", buffer, conviction split) is direction-quality
    # content the brain sizes against, not bloat.
    # D2 (2026-06-05) added the RR-neutralization READ paragraph (landed on main
    # without its companion bound-raise) and Issue 5 (CALL_A exploit/fetch,
    # 2026-06-05) added the exploitation-breadth framing (work to surface ~3
    # genuine plays), the shorter-hold guidance, and the smaller-size clause —
    # all direction/quality content the brain reasons against, not bloat. Bound
    # raised to 12.5 KB to cover that approved growth while still guarding bloat.
    # Fix 6 (mandate reframe, 2026-06-10) reframed the hard "MINIMUM of 3" floor
    # to the quality-conditioned "2 to 5 BEST GENUINE plays" with the
    # declining-is-correct clause (and restored the count-neutral "across the
    # full candidate set" phrasing). That is direction-quality decision guidance,
    # not bloat; bound raised 12.5 KB -> 13 KB to cover it while still guarding.
    # Four-Element Prompt Recalibration (2026-06-11): Element 1 re-keyed the
    # quality-over-quota skip permission to the proven-toxic patterns (the
    # dead-thin-zero-fired cluster and the heavy losing session, ~+0.9 KB),
    # and Elements 4 and 5 correct the always-presents-opportunities premise
    # and anchor the analysis method to the new session/liveness/range-truth
    # facts (~+0.7 KB more). All of it is decision content the June-11
    # forensics proved the brain was missing, not bloat; bound raised
    # 13 KB -> 15.5 KB to cover the program while still guarding growth.
    assert len(TRADE_SYSTEM_PROMPT) < 15500, (
        f"TRADE_SYSTEM_PROMPT exceeds 15.5 KB ({len(TRADE_SYSTEM_PROMPT)} chars). "
        "Trim before adding more sections."
    )
    # Neutrality + exit-system fix (2026-05-30) raised the lean bound from
    # 6.5 KB to 7.5 KB. Fund-management fix (2026-05-31) raised it to 8.2 KB for
    # the size_usd PROPER-FUNDING instruction. D2 + Issue 5 (2026-06-05) raised
    # it to 11 KB for the RR-neutralization and exploitation-breadth framing.
    # Fix 6 (mandate reframe, 2026-06-10) added the identical quality-conditioned
    # floor + declining clause to this variant; bound raised 11 KB -> 11.5 KB.
    # Still a meaningful "stay lean" guard with headroom.
    # Four-Element Prompt Recalibration (2026-06-11): the same Element 1/4/5
    # decision content lands identically in this LIVE variant; bound raised
    # 11.5 KB -> 14 KB (same rationale as the legacy bound above).
    assert len(TRADE_SYSTEM_PROMPT_ZERO_TWO) < 14000, (
        f"TRADE_SYSTEM_PROMPT_ZERO_TWO exceeds 14 KB "
        f"({len(TRADE_SYSTEM_PROMPT_ZERO_TWO)} chars). Keep the contract lean."
    )


def test_thesis_invalidation_version_sentinel_present() -> None:
    """The sentinel constant exists for log-tail boot verification."""
    assert isinstance(TRADE_SYSTEM_PROMPT_THESIS_INVALIDATION_VERSION, int)
    assert TRADE_SYSTEM_PROMPT_THESIS_INVALIDATION_VERSION >= 1


# ════════════════════════════════════════════════════════════════════
# 4. JSON schema shape (best-effort structural check)
# ════════════════════════════════════════════════════════════════════


def test_schema_includes_thesis_invalidation_inside_new_trades_inner_object() -> None:
    """The thesis_invalidation field must be inside the new_trades inner
    object, NOT at the top level. Brain-stated criterion is per-trade,
    not per-batch."""
    for prompt_name, prompt in (
        ("TRADE_SYSTEM_PROMPT", TRADE_SYSTEM_PROMPT),
        ("TRADE_SYSTEM_PROMPT_ZERO_TWO", TRADE_SYSTEM_PROMPT_ZERO_TWO),
    ):
        # Locate the new_trades JSON sub-block and assert the field lives there.
        idx_new_trades = prompt.find('"new_trades"')
        idx_inv = prompt.find('"thesis_invalidation"')
        idx_market_view = prompt.find('"market_view"')
        assert idx_new_trades >= 0, f"{prompt_name} missing new_trades key"
        assert idx_inv >= 0, f"{prompt_name} missing thesis_invalidation key"
        assert idx_market_view >= 0, f"{prompt_name} missing market_view key"
        # thesis_invalidation must appear between new_trades and the
        # outer market_view (which marks the end of the inner object).
        assert idx_new_trades < idx_inv < idx_market_view, (
            f"{prompt_name}: thesis_invalidation must live inside the "
            "new_trades inner object, not at the top level"
        )
