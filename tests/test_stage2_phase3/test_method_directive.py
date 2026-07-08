"""Four-Element Prompt Recalibration, Element 5 (2026-06-11) — the
deep-analysis directive: the brain's instructed method anchored to the
facts the June-11 forensics proved decisive.

Confirms, in BOTH system-prompt constants:
1. Step 1 reads the FULL evidence including the session history
   (Element 2's line), the activity state, and the true range position
   (Element 3's marker semantics: below or above the range is a break
   in progress, not a floor or ceiling).
2. Step 4 defines the best play on the three reads — evidence strength,
   context liveness, non-repetition of today's proven failures — and
   keeps its pinned prefix.
3. The directive paragraph carries the same three-reads selection
   sentence, naming the Session liveness line (Element 4).
4. Rule 6 coherence: the method names exactly the facts Elements 1
   through 4 render, so the assembled prompt reads as ONE method.
5. The method version sentinel is bumped for log-tail monitoring.
"""

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_METHOD_VERSION,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
)

BOTH_PROMPTS = (TRADE_SYSTEM_PROMPT, TRADE_SYSTEM_PROMPT_ZERO_TWO)


def test_step_one_reads_the_full_evidence():
    for prompt in BOTH_PROMPTS:
        assert (
            "1. Read the FULL evidence: structural data, signals, regime, "
            "and ensemble votes — AND this coin's session history "
            "(attempts today and their net result), its activity state "
            "(regime word, volume ratio, strategies fired), and its true "
            "range position" in prompt
        )
        assert (
            "BELOW or ABOVE the range is a break in progress, not a floor "
            "or ceiling" in prompt
        )


def test_steps_two_and_three_unchanged():
    for prompt in BOTH_PROMPTS:
        assert (
            "2. Identify what kind of opportunity this coin's current "
            "state represents" in prompt
        )
        assert (
            "3. Determine the direction and entry that exploits that "
            "opportunity" in prompt
        )


def test_step_four_keeps_prefix_and_adds_the_three_reads():
    for prompt in BOTH_PROMPTS:
        # The pinned prefix other suites assert stays intact.
        assert (
            "4. Compare across candidates and pick the BEST GENUINE plays "
            "— usually 2 to 5; take fewer only when fewer genuinely "
            "qualify." in prompt
        )
        assert (
            "The best play is the one whose evidence is strong AND whose "
            "context is alive AND which does not repeat a pattern that has "
            "already failed today (a heavy losing session, the "
            "dead-thin-zero-fired cluster)" in prompt
        )


def test_directive_paragraph_carries_the_selection_sentence():
    for prompt in BOTH_PROMPTS:
        assert (
            "Selection runs on three reads together: evidence strength, "
            "context liveness (the Session liveness line and the coin's "
            "own volume ratio), and non-repetition of today's proven "
            "failures." in prompt
        )


def test_method_names_only_facts_the_prompt_renders():
    """Rule 6 — every fact the method references is rendered by an
    earlier element: the session attempts line (Element 2), the
    Session liveness line (Element 4), the range-truth read (Element
    3), and the dead-thin-zero-fired cluster vocabulary (Element 1)."""
    for prompt in BOTH_PROMPTS:
        assert "session attempts line" in prompt  # Element 1 wording
        assert "Session liveness line" in prompt  # Element 5 reference
        assert "dead-thin-zero-fired cluster" in prompt  # Element 1
        assert "attempts today" in prompt


def test_exploitation_framing_untouched():
    for prompt in BOTH_PROMPTS:
        assert "everything short of that, you exploit." in prompt
        assert "Aggressive exploitation. Maximum profit. Find the play." in prompt


def test_method_version_bumped():
    assert TRADE_SYSTEM_PROMPT_METHOD_VERSION == 2
