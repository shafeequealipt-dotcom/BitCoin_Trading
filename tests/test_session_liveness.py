"""Four-Element Prompt Recalibration, Element 4 (2026-06-11) — the
session-liveness context line and the corrected premise.

Covers:
1. The pure classifier ``_session_liveness`` boundaries (quantized
   fifths on the live 5-candidate deck; threshold equality counts as
   thin; empty input reads unknown so nothing renders — Rule 4).
2. The premise correction in BOTH system-prompt constants: the false
   "Markets always present opportunities." absolute and the
   "sitting out from laziness" framing are gone; the full play catalog,
   "FIND it and TRADE it", "quality over quota", and the exploitation
   phrases are preserved verbatim (Rule 3); the
   capital-preserved-is-ammunition correction is present.
3. Wiring contracts: the liveness line is inserted next to the market
   context, is ESSENTIAL-protected from the priority trim, and renders
   nothing when no ratios are measured.
"""

import inspect
from types import SimpleNamespace

import pytest

from src.brain.strategist import (
    _TRIM_ESSENTIAL_MARKERS,
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_PREMISE_VERSION,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    ClaudeStrategist,
    _candidate_vol_ratio,
    _session_liveness,
)

BOTH_PROMPTS = (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO)


class TestLivenessClassifier:
    def test_empty_reads_unknown(self):
        assert _session_liveness([]) == ("unknown", 0)

    def test_one_of_five_thin_reads_live(self):
        label, thin = _session_liveness([0.05, 0.9, 1.2, 0.8, 0.5])
        assert (label, thin) == ("live", 1)

    def test_two_of_five_thin_reads_mixed(self):
        label, thin = _session_liveness([0.05, 0.10, 1.2, 0.8, 0.5])
        assert (label, thin) == ("mixed", 2)

    def test_three_of_five_thin_reads_thin(self):
        label, thin = _session_liveness([0.05, 0.10, 0.20, 0.8, 0.5])
        assert (label, thin) == ("thin", 3)

    def test_all_thin_reads_thin(self):
        label, thin = _session_liveness([0.01, 0.02, 0.03, 0.04, 0.05])
        assert (label, thin) == ("thin", 5)

    def test_threshold_equality_counts_thin(self):
        label, thin = _session_liveness([0.25, 0.9], thin_vol_ratio=0.25)
        assert thin == 1

    def test_custom_boundaries_honored(self):
        # With live_max at 0.5, a 2-of-5 thin share (0.4) reads live.
        label, _ = _session_liveness(
            [0.1, 0.1, 0.9, 0.9, 0.9],
            live_max_thin_share=0.5, thin_min_thin_share=0.8,
        )
        assert label == "live"


class TestPremiseCorrection:
    def test_false_absolute_removed(self):
        for prompt in BOTH_PROMPTS:
            assert "Markets always present opportunities." not in prompt
            assert "sitting out from laziness" not in prompt

    def test_corrected_premise_present(self):
        for prompt in BOTH_PROMPTS:
            assert (
                "Most cycles present genuine opportunities; a dead, thin "
                "tape may present none." in prompt
            )
            assert (
                "returning fewer or zero trades IS correct exploitation"
                in prompt
            )
            assert "ammunition for the live ones" in prompt

    def test_laziness_clause_replaced_with_honest_decline(self):
        for prompt in BOTH_PROMPTS:
            assert "so FIND it and TRADE it" in prompt
            assert (
                "when a dead thin tape genuinely offers no profitable side, "
                "declining is the same exploitation" in prompt
            )

    @pytest.mark.parametrize("catalog_sentence", [
        "Overbought conditions are fade setups.",
        "Extended moves are exhaustion plays.",
        "Range tops are reversal setups.",
        "Range bottoms are breakout setups.",
        "Pullbacks in trends are continuation entries.",
        "Liquidity sweeps are reclaim setups.",
    ])
    def test_play_catalog_kept_verbatim(self, catalog_sentence):
        for prompt in BOTH_PROMPTS:
            assert catalog_sentence in prompt

    def test_exploitation_phrases_kept_verbatim(self):
        for prompt in BOTH_PROMPTS:
            assert (
                "EXPLOIT and FETCH MAXIMUM PROFIT from EVERY situation"
                in prompt
            )
            assert "Aggressive exploitation. Maximum profit." in prompt
            assert "quality over quota" in prompt

    def test_premise_version_bumped(self):
        assert TRADE_SYSTEM_PROMPT_PREMISE_VERSION == 2


class TestCandidateVolRatio:
    """Cross-check fix (2026-06-11) — the liveness gather must count a
    ratio as MEASURED only when the Regime line would render a number,
    via the same two-source contract (scored snapshot, else live regime
    cache). The adversarial audit caught the original gather treating an
    UNSCORED package's dataclass defaults (ratio 0.0, known True) as a
    measured-thin reading — a fabricated measurement (Rule 4)."""

    def _pkg(self, scoring_regime, ratio=0.0, known=True):
        return SimpleNamespace(
            symbol="AAAUSDT",
            strategies=SimpleNamespace(
                scoring_regime=scoring_regime,
                scoring_regime_volume_ratio=ratio,
                scoring_regime_volume_ratio_known=known,
            ),
        )

    def test_scored_coin_uses_the_scored_snapshot(self):
        assert _candidate_vol_ratio(
            self._pkg("dead", ratio=0.043, known=True), None,
        ) == (0.043, True)

    def test_unscored_default_never_counts_as_measured(self):
        # scoring_regime empty + no live cache: the 0.0/known=True pair
        # is a dataclass default, not a measurement — must be excluded.
        value, known = _candidate_vol_ratio(self._pkg(""), None)
        assert known is False

    def test_unscored_coin_falls_back_to_live_cache(self):
        detector = SimpleNamespace(
            get_coin_regime=lambda sym: SimpleNamespace(
                volume_ratio=0.8, volume_ratio_known=True,
            ),
        )
        assert _candidate_vol_ratio(self._pkg(""), detector) == (0.8, True)

    def test_scored_but_volume_missing_reads_unknown(self):
        value, known = _candidate_vol_ratio(
            self._pkg("dead", ratio=0.0, known=False), None,
        )
        assert known is False

    def test_live_cache_volume_missing_reads_unknown(self):
        detector = SimpleNamespace(
            get_coin_regime=lambda sym: SimpleNamespace(
                volume_ratio=0.0, volume_ratio_known=False,
            ),
        )
        value, known = _candidate_vol_ratio(self._pkg(""), detector)
        assert known is False

    def test_no_strategies_block_and_no_cache_excluded(self):
        value, known = _candidate_vol_ratio(
            SimpleNamespace(symbol="A", strategies=None), None,
        )
        assert known is False


class TestWiringContracts:
    def test_liveness_line_inserted_next_to_market_context(self):
        src = inspect.getsource(ClaudeStrategist._build_trade_prompt)
        assert "_mkt_ctx_idx = len(sections) - 1" in src
        assert "Session liveness: " in src
        assert "_session_liveness(" in src
        # Rule 4 honesty: unknown ratio set renders nothing, and each
        # coin's ratio goes through the Regime-line two-source contract.
        assert '_lv_label != "unknown"' in src
        assert "_candidate_vol_ratio(" in src

    def test_liveness_line_is_trim_essential(self):
        assert "Session liveness:" in _TRIM_ESSENTIAL_MARKERS

    def test_liveness_marker_does_not_collide_with_optional_session(self):
        # "## SESSION" is an OPTIONAL trim marker; the liveness line must
        # never start with it.
        assert not "Session liveness:".startswith("## SESSION")
