"""Entry-Quality Fix 6 self-verification (2026-06-10).

The Call-A minimum-trades mandate is reframed from an absolute "MINIMUM of 3"
floor to a quality-conditioned "2 to 5 BEST GENUINE plays — quality over quota",
with an explicit declining-is-correct clause. Every exploitation phrase is
preserved; the change is prompt-text only. This asserts both prompt variants
carry the new framing, no residual hard-floor language survives, the exploitation
language is intact, and the activity version bumped. Never rewrites data.
"""

from __future__ import annotations

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION,
)

PROMPTS = {
    "TRADE_SYSTEM_PROMPT": TRADE_SYSTEM_PROMPT,
    "TRADE_SYSTEM_PROMPT_ZERO_TWO": TRADE_SYSTEM_PROMPT_ZERO_TWO,
}

# Wording that MUST be gone (the absolute hard floor).
FORBIDDEN = [
    "MINIMUM of 3 trades",
    "Do not stop short of 3",
    "AT LEAST 3",
    "at least 3 exploitable",
    "at least 3 profitable",
    "best 2-4",
]

# The quality-conditioned reframe that MUST be present in BOTH prompts.
REQUIRED_QUALITY = [
    "2 to 5 BEST GENUINE plays",
    "QUALITY OVER QUOTA",
    "if the genuine plays number fewer than 3, return fewer than 3",
    "if fewer than 3 genuine plays exist this cycle, return fewer than 3",
]

# Exploitation language that MUST be preserved (Anti-Patterns: do not delete it).
REQUIRED_AGGRESSION = [
    "EXPLOIT and FETCH MAXIMUM PROFIT from EVERY situation",
    "WORK every one of the candidates",
    "reach especially for the smaller, shorter, both-direction plays",
    "rather than sitting out",
    "FLATLY contradicts",
    "long OR short",
]


def test_no_residual_hard_floor() -> None:
    for name, p in PROMPTS.items():
        for bad in FORBIDDEN:
            assert bad not in p, f"{name} still contains hard-floor wording: {bad!r}"
    print("PASS: no residual hard-floor language ('MINIMUM of 3', 'AT LEAST 3', etc.) in either prompt.")


def test_quality_framing_present_in_both() -> None:
    for name, p in PROMPTS.items():
        for need in REQUIRED_QUALITY:
            assert need in p, f"{name} missing quality framing: {need!r}"
    print("PASS: both prompts carry the quality-conditioned floor + declining-is-correct + fewer-than-3 clause.")


def test_exploitation_language_preserved() -> None:
    for name, p in PROMPTS.items():
        for need in REQUIRED_AGGRESSION:
            assert need in p, f"{name} dropped exploitation language: {need!r}"
    print("PASS: every exploitation phrase preserved in both prompts (aggression intact, conditioned on quality).")


def test_activity_version_bumped() -> None:
    assert TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION == 2, TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION
    print("PASS: TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION bumped to 2 (boot sentinel reflects the reworded prompt).")


def test_both_prompts_agree_on_floor_framing() -> None:
    # The directive-paragraph floor sentence must be identical between the two
    # prompts so behaviour does not depend on the enable_zero_two_contract flag.
    anchor = "Return the 2 to 5 BEST GENUINE plays — this system's entire aim"
    assert anchor in TRADE_SYSTEM_PROMPT and anchor in TRADE_SYSTEM_PROMPT_ZERO_TWO
    print("PASS: both prompt variants carry word-for-word identical floor framing.")


def main() -> None:
    print("=== Entry-Quality Fix 6 — minimum-trades mandate reframe verification ===")
    test_no_residual_hard_floor()
    test_quality_framing_present_in_both()
    test_exploitation_language_preserved()
    test_activity_version_bumped()
    test_both_prompts_agree_on_floor_framing()
    print("\nALL FIX-6 CHECKS PASSED.")


if __name__ == "__main__":
    main()
