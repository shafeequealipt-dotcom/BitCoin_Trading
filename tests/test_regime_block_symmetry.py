"""Symmetry assertions for the CALL_A MARKET REGIME (CONTEXT) block.

Issue 4 of the 2026-05-19 direction-bias fix Phase A. The asymmetric
"DEFAULT SELL BIAS / BUY preferred" direction_hint dict and the
trending_down-only conf>0.60 NOTE (block_version=1) was replaced with a
symmetric scenario-driven version (block_version=2) at
``src/brain/strategist.py:3371-3400`` (live block) and its dead duplicate
at ``:1416-1445``.

These tests assert the symmetric block is loaded into memory and that the
asymmetric strings from version 1 are not present in the production
prompt builders.
"""

from __future__ import annotations

import re

from src.brain.strategist import STRAT_REGIME_BLOCK_VERSION


def _read_strategist_source() -> str:
    """Read the current strategist.py source for static-content assertions."""
    from pathlib import Path

    src_path = Path(__file__).resolve().parent.parent / "src" / "brain" / "strategist.py"
    return src_path.read_text(encoding="utf-8")


class TestRegimeBlockSymmetryConstants:
    """Sentinel constant assertions — proves the block_version bump happened."""

    def test_block_version_is_4(self) -> None:
        # Per-coin-authority Phase 6 (2026-05-29) bumped 2 -> 3: the default path
        # now emits NO global direction mandate (per-coin is authoritative); the
        # symmetric direction_hint + NOTE asserted by the rest of this file are
        # the ROLLBACK path (stage2.per_coin_direction_enabled=false), still
        # present in source, which is why the source-grep assertions below hold.
        # D1 of IMPLEMENT_NEUTRALITY_AND_EXIT_SYSTEM_FIX (2026-05-30) bumped
        # 3 -> 4: the Fear & Greed framing was reframed NEUTRAL on direction
        # (no contrarian-buy lean). D2 (2026-06-05) bumped 4 -> 5: the
        # "RR by direction" check was neutralized — demoted from a "take the
        # better-reward side" command to ONE input, the spent/mitigated-zone
        # artifact taught symmetrically, SKIP made first-class — removing the
        # prompt-level long-bias that pushed Buys in selloffs. Bump is the sentinel.
        assert STRAT_REGIME_BLOCK_VERSION == 5

    def test_boot_sentinel_string_is_present(self) -> None:
        source = _read_strategist_source()
        assert "STRAT_REGIME_INSTR_REFRAMED" in source

    def test_boot_sentinel_emits_block_version(self) -> None:
        source = _read_strategist_source()
        assert "block_version={STRAT_REGIME_BLOCK_VERSION}" in source

    def test_boot_sentinel_mode_is_symmetric_scenario(self) -> None:
        source = _read_strategist_source()
        assert "mode=symmetric_scenario" in source


class TestRegimeBlockDirectionHintSymmetry:
    """The direction_hint dict has symmetric strings for trending_down/up."""

    def test_trending_down_hint_is_symmetric_with_trending_up(self) -> None:
        source = _read_strategist_source()
        # Symmetric phrasing for both regimes; per-coin override called
        # out identically in both.
        assert (
            '"trending_down": "Bias for shorts when per-coin evidence agrees; '
            'per-coin tags override."' in source
        )
        assert (
            '"trending_up": "Bias for longs when per-coin evidence agrees; '
            'per-coin tags override."' in source
        )

    def test_asymmetric_version_1_strings_absent(self) -> None:
        """Negative assertion: legacy "DEFAULT SELL BIAS" / "BUY preferred"
        direction_hint values must not be present in the active code path.

        The strings may still appear in non-active comment blocks or in
        dev_notes — but they must not be in the direction_hint dict
        literal that gets emitted to Claude.
        """
        source = _read_strategist_source()
        # Look for the legacy direction_hint pattern as it appeared in
        # block_version=1. If present, the symmetric rewrite is incomplete.
        legacy_pattern = re.compile(
            r'"trending_down":\s*"DEFAULT SELL BIAS',
            re.MULTILINE,
        )
        assert (
            legacy_pattern.search(source) is None
        ), "Legacy 'DEFAULT SELL BIAS' direction_hint must be removed"

        legacy_buy_preferred = re.compile(
            r'"trending_up":\s*"BUY preferred"',
            re.MULTILINE,
        )
        assert (
            legacy_buy_preferred.search(source) is None
        ), "Legacy 'BUY preferred' direction_hint must be removed"


class TestRegimeBlockNoteSymmetry:
    """The conf > 0.60 NOTE block fires on both trending_down AND trending_up."""

    def test_trending_down_high_conf_note_present(self) -> None:
        source = _read_strategist_source()
        assert (
            "NOTE: High-confidence global downtrend. Use this as default bias"
            in source
        )

    def test_trending_up_high_conf_note_present(self) -> None:
        """Mirror NOTE — proves the asymmetry is fixed."""
        source = _read_strategist_source()
        assert (
            "NOTE: High-confidence global uptrend. Use this as default bias"
            in source
        )

    def test_legacy_asymmetric_note_absent(self) -> None:
        """The version 1 NOTE only fired for trending_down with mandate-
        flavoured wording. Confirm it's gone."""
        source = _read_strategist_source()
        legacy_note = (
            "NOTE: High-confidence global downtrend. DEFAULT to SELL for coins "
            "without per-coin regime data"
        )
        assert (
            legacy_note not in source
        ), "Legacy trending_down-only mandate NOTE must be removed"


class TestRegimeBlockHeaderSymmetry:
    """The canonical header is now "## MARKET REGIME (CONTEXT)"."""

    def test_canonical_header_is_present(self) -> None:
        source = _read_strategist_source()
        # Header is emitted from sections.append at two sites (live + dead
        # duplicate); both should now carry the new canonical text. Use a
        # raw-string match because the source contains the literal escape
        # sequence ``\n`` (backslash + n, 2 chars) inside the f-string,
        # not a real newline.
        assert source.count(r'"\n## MARKET REGIME (CONTEXT)"') >= 2

    def test_legacy_header_still_in_trim_marker_tuple(self) -> None:
        """The legacy header substring is retained in the
        _TRIM_ESSENTIAL_MARKERS tuple for transitional robustness, even
        though the active code paths emit the new canonical header.
        """
        source = _read_strategist_source()
        # Both substrings appear in _TRIM_ESSENTIAL_MARKERS — confirms
        # backward-compat trim classification works for any replay of
        # legacy prompts.
        assert '"## MARKET REGIME (CONTEXT)"' in source
        assert '"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"' in source


class TestStratAggressiveFramingSentinelTruth:
    """The STRAT_AGGRESSIVE_FRAMING boot sentinel now reflects symmetric state."""

    def test_regime_instr_field_is_symmetric(self) -> None:
        source = _read_strategist_source()
        # The pre-fix value was regime_instr=minimal (falsely claimed the
        # asymmetric block was suppressed when it was still emitted).
        # Post-fix the value is regime_instr=symmetric (truthful).
        assert "regime_instr=symmetric contract=aggressive_exploit" in source

    def test_regime_instr_minimal_no_longer_emitted_for_aggressive_framing(self) -> None:
        """Sanity check the misleading "minimal" string is not still in
        the STRAT_AGGRESSIVE_FRAMING f-string."""
        source = _read_strategist_source()
        # The substring "regime_instr=minimal" might still appear in
        # comments or dead code, but should not appear in the active
        # STRAT_AGGRESSIVE_FRAMING log line.
        aggressive_framing_block = re.search(
            r'STRAT_AGGRESSIVE_FRAMING \| .*?\}"',
            source,
            re.DOTALL,
        )
        assert aggressive_framing_block is not None
        block_text = aggressive_framing_block.group(0)
        assert "regime_instr=minimal" not in block_text
        assert "regime_instr=symmetric" in block_text
