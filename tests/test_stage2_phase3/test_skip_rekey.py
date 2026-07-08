"""Element 1 of the Four-Element Prompt Recalibration (2026-06-11) —
the quality-over-quota skip permission re-keyed from the proven-empty
labels (X-RAY SKIP grade / interestingness below 0.30) to the
proven-toxic patterns (the dead-thin-zero-fired cluster and the heavy
losing session), with the thresholds centralized in config and injected
via placeholder tokens.

Confirms:
1. Both system-prompt constants carry each placeholder token exactly
   twice (directive paragraph + RULES 1), so the centralized thresholds
   reach every site and none is hardcoded inline (Rule 9).
2. ``_resolve_prompt_calibration`` injects the configured values,
   leaves no token behind, and never disturbs the literal-JSON response
   schema (the reason str.format is not used).
3. The old keys are no longer the primary skip currency but survive as
   the explicit secondary-context sentence (spec Element 1: "may remain
   as secondary mentions").
4. The retained-verbatim phrases the broader contract tests pin
   ("DECLINING that candidate is correct trading", "return fewer than
   3") are still present, and the RR-conflict skip is kept.
5. The BRIEFING_SYSTEM_PROMPT_SUFFIX consistency note (Rule 6) is in
   place so the interestingness coaching no longer contradicts the
   re-keyed permission.
"""

from src.brain.strategist import (
    BRIEFING_SYSTEM_PROMPT_SUFFIX,
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_SKIP_KEYS_VERSION,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    _PROMPT_CALIBRATION_TOKENS,
    _resolve_prompt_calibration,
)

BOTH_PROMPTS = (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO)


def test_tokens_present_exactly_twice_per_constant():
    for prompt in BOTH_PROMPTS:
        assert prompt.count("__DEAD_THIN_VOL_RATIO__") == 2
        assert prompt.count("__HEAVY_ATTEMPTS_COUNT__") == 2


def test_new_cluster_language_at_both_sites_of_both_constants():
    for prompt in BOTH_PROMPTS:
        # Directive paragraph + RULES 1 name the cluster (Element 1);
        # step 4 of the analysis method names it as the
        # non-repetition example (Element 5). Pinned exactly so a new
        # site cannot appear without a conscious test update (Rule 6).
        assert prompt.count("dead-thin-zero-fired cluster") == 3
        assert "heavy losing session" in prompt
        assert "zero strategies fired" in prompt
        # The pinned-verbatim permission phrase survives the re-key.
        assert "DECLINING that candidate is correct trading" in prompt


def test_old_keys_demoted_to_secondary_not_removed():
    for prompt in BOTH_PROMPTS:
        # Old primary phrasings gone.
        assert "reads skip-quality (X-RAY quality SKIP" not in prompt
        assert (
            "declining a skip-quality / neither-side-tradeable / "
            "deep-sub-confidence candidate" not in prompt
        )
        # Demoted secondary mention retained (spec: "may remain as
        # secondary mentions but must no longer be the primary currency").
        assert "interestingness below 0.30" in prompt
        assert "secondary context only" in prompt
        # The anti-redemption clause the June-11 evidence demands
        # (IMX/MON carried the deck's HIGHEST interestingness, 0.82).
        assert "does NOT redeem a dead-thin-zero-fired candidate" in prompt


def test_resolver_injects_defaults_and_preserves_json_schema():
    for prompt in BOTH_PROMPTS:
        resolved = _resolve_prompt_calibration(
            prompt, thin_vol_ratio=0.25, heavy_attempts=6,
        )
        assert "at or below 0.25" in resolved
        assert "6 or more" in resolved
        for token in _PROMPT_CALIBRATION_TOKENS:
            assert token not in resolved
        # The literal JSON braces that rule out str.format stay intact.
        assert '{"new_trades":[{"symbol":"SYM"' in resolved
        assert '"thesis_invalidation":{"type"' in resolved


def test_resolver_honors_custom_config_values():
    resolved = _resolve_prompt_calibration(
        TRADE_SYSTEM_PROMPT_ZERO_TWO, thin_vol_ratio=0.1, heavy_attempts=8,
    )
    assert "at or below 0.10" in resolved
    assert "8 or more" in resolved
    assert "0.25" not in resolved.split("RULES:")[0].split("QUALITY OVER QUOTA")[1][:600]


def test_rr_conflict_skip_and_fewer_than_three_retained():
    for prompt in BOTH_PROMPTS:
        assert "return fewer than 3" in prompt
        assert "neither-side-tradeable RR conflict" in prompt
        assert "remains a valid skip as before" in prompt


def test_briefing_suffix_carries_consistency_note():
    # Rule 6 — the suffix's interestingness coaching must not contradict
    # the re-keyed permission.
    assert "NOT win odds" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "dead-thin-zero-fired cluster" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "see QUALITY OVER QUOTA" in BRIEFING_SYSTEM_PROMPT_SUFFIX


def test_skip_keys_version_bumped_for_log_tail_monitoring():
    assert TRADE_SYSTEM_PROMPT_SKIP_KEYS_VERSION == 2
