"""Aggressive-framing rewrite (2026-05-05) — preserves Path C softening.

The original Phase 3 Path C work softened the strict 5-criteria STRONG
rule (TradeScorer >=70 AND ensemble dominant >=3 voters AND XRAY conf
>=0.7 AND regime conf >=0.6 AND RR good). The aggressive-framing
rewrite replaces the philosophical contract with FIX Change 7
verbatim text but does NOT re-introduce any of those strict numeric
gates — the new prompt operates by opportunity-pattern catalog and
4-step decision process, not numeric thresholds.

This file confirms:
  Part 1 — the 5 strict rules from the pre-Path-C prompt are still
           absent (negative assertions stay valid)
  Part 2 — the operational machinery the parser depends on
           (DIRECTION BY REGIME, FEAR & GREED, JSON, RULES, [POS]
           gate, SL minimum, per-trade size limit reference) is
           retained
  Part 3 — the new aggressive framing is present (aim line, pattern
           catalog, 4-step process, closing imperative)
  Part 4 — zero-trades is reframed as "rare" rather than enumerated
           through specific failure-mode gates
"""

from __future__ import annotations

import re

import pytest

from src.brain.strategist import (
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
)


# ─────────────────────────────────────────────────────────────────────────
# Part 1 — Strict STRONG rule still removed
# ─────────────────────────────────────────────────────────────────────────


class TestStrictRuleStillRemoved:
    """The pre-Path-C 'STRONG conviction means ALL of:' block must
    remain gone, along with each of the 5 hard thresholds it enforced.
    These negative assertions held before the aggressive-framing
    rewrite and continue to hold after."""

    def test_no_strong_conviction_means_all_of(self) -> None:
        assert "STRONG conviction means ALL of" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_tradescorer_70_threshold(self) -> None:
        """The 'TradeScorer total >= 70' hard gate must be gone — Claude
        sees the score as one signal among many, not a binary gate."""
        assert "TradeScorer total >= 70" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "total >= 70" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_xray_07_threshold(self) -> None:
        """The 'setup_type_confidence >= 0.7' hard gate must be gone."""
        assert "setup_type_confidence >= 0.7" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert not re.search(r"confidence\s*>=\s*0\.7", TRADE_SYSTEM_PROMPT_ZERO_TWO)

    def test_no_regime_06_threshold(self) -> None:
        """The 'regime confidence >= 0.6' hard gate must be gone."""
        assert "regime confidence >= 0.6" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert not re.search(
            r"regime\s+confidence\s*>=\s*0\.6", TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_no_voter_count_threshold(self) -> None:
        """The 'dominant side has >= 3 voters with confidence >= 0.65'
        ensemble-agreement gate must be gone."""
        assert ">= 3 voters" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "confidence >= 0.65" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_smc_70_threshold(self) -> None:
        """The pre-fix WHEN TO RETURN ZERO TRADES had 'smc_confluence >= 70'
        as a hard gate inside the F&G contrarian skip path; remove it."""
        assert "smc_confluence >= 70" not in TRADE_SYSTEM_PROMPT_ZERO_TWO


# ─────────────────────────────────────────────────────────────────────────
# Part 2 — Operational caps preserved
# ─────────────────────────────────────────────────────────────────────────


class TestOperationalCapsPreserved:
    """The position gate, JSON output schema, SL minimum, regime
    direction guidance, F&G contrarian rules, and per-trade size limit
    reference must all survive the framing rewrite."""

    def test_quality_conditioned_count_contract_present(self) -> None:
        """Fix 6 (2026-06-10): the count contract is quality-conditioned —
        'Return the 2 to 5 BEST GENUINE plays', quality over quota, and returning
        fewer when fewer genuinely qualify is correct. This replaces the stale
        D2-era 'Return up to 4 trades' / 'Returning 0 or 1 is CORRECT' wording
        the 2026-06-09 min-3 mandate had already clobbered (so this assertion was
        red before Fix 6 too)."""
        assert "2 to 5 BEST GENUINE plays" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "QUALITY OVER QUOTA" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "return fewer than 3" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_per_trade_size_limit_reference_present(self) -> None:
        """size_usd guidance must point at the per-trade size limit shown in
        the user prompt. Fund-management rewrite (2026-05-31) replaced the bare
        "must respect the per-trade size limit" wording with the PROPER FUNDING
        instruction, which still references the per-trade size limit (per trade)
        AND adds "Available for new trades" (the portfolio budget)."""
        assert "per-trade size limit" in TRADE_SYSTEM_PROMPT_ZERO_TWO.lower()
        assert "PROPER FUNDING" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Available for new trades" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_legacy_hardcoded_size_range(self) -> None:
        """Old prompt said size_usd: $500-$5000; STRONG = larger
        ($2000-$5000) — that range is contract-blind. New prompt
        defers to the per-trade size limit shown in the user prompt."""
        assert "$500-$5000" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "$2000-$5000" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_position_gate_intact(self) -> None:
        assert "[POS]" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "NEVER suggest a [POS] coin" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_direction_by_regime_intact(self) -> None:
        assert "DIRECTION BY REGIME" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "ranging" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "volatile" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "dead" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_fear_greed_contrarian_intact(self) -> None:
        assert "FEAR & GREED" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "F&G < 20" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "F&G > 80" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_json_response_schema_intact(self) -> None:
        # The literal JSON schema string Claude must echo.
        assert '"new_trades":[' in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert '"market_view"' in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert '"risk_level"' in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_sl_minimum_15pct_rule_intact(self) -> None:
        """Five-Fix Follow-Up Fix 3 (2026-06-10): the rule became
        volatility-aware (per-candidate Vol stop floor) but the 1.5 percent
        ABSOLUTE floor is preserved verbatim — this is what must stay intact."""
        assert "Absolute minimum 1.5% from entry" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "tighter is rejected" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Vol stop floor" in TRADE_SYSTEM_PROMPT_ZERO_TWO


# ─────────────────────────────────────────────────────────────────────────
# Part 3 — Aggressive-framing language present
# ─────────────────────────────────────────────────────────────────────────


class TestAggressiveFramingPresent:
    """The new aggressive-exploitation framing replaces the previous
    "characterize each coin's situation" / "JUDGMENT — USE THE FULL
    PER-COIN DATA" / "trust the structure" coaching."""

    def test_aggressive_aim_present(self) -> None:
        """Operator philosophy explicitly stated up front."""
        assert (
            "Your aim is to exploit the current market situation"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "aggressively fetch the maximum profitable trade"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_exploitation_pattern_catalog_present(self) -> None:
        """The opportunity-pattern catalog is the heart of the new
        framing — these are the exploitation plays Claude should
        match per coin instead of avoiding."""
        assert "Overbought conditions are fade setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Extended moves are exhaustion plays" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Range tops are reversal setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Range bottoms are breakout setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert (
            "Pullbacks in trends are continuation entries"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert "Liquidity sweeps are reclaim setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_four_step_decision_process_present(self) -> None:
        """The 4-step decision process tells Claude how to reason
        through each candidate's exploitation play."""
        assert (
            "1. Read the FULL evidence: structural data, signals, regime, and ensemble votes"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "2. Identify what kind of opportunity"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "3. Determine the direction and entry that exploits"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "4. Compare across candidates and pick the BEST GENUINE plays"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_closing_imperative_present(self) -> None:
        assert (
            "Aggressive exploitation. Maximum profit. Find the play."
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_previous_phase3_coaching_removed(self) -> None:
        """The previous-iteration 0-2 contract coaching language is
        gone — the new framing replaces it with the aggressive-
        exploitation aim line and pattern catalog."""
        assert "STRICT 0-2 CONTRACT" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Three or more is a HARD violation" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "TWO trades is the cap" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "JUDGMENT" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Weigh these together" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "DO NOT require unanimous agreement" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "trust the structure" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "exploit the best opportunities" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert (
            "Missing a genuine opportunity is as costly"
            not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )


# ─────────────────────────────────────────────────────────────────────────
# Part 4 — Zero-trades reframed as rare, not enumerated
# ─────────────────────────────────────────────────────────────────────────


class TestZeroTradesReframedAsRare:
    """D2 (2026-06-05) reframe: zero-trade outputs are no longer "rare" — they
    are CORRECT whenever no candidate offers a real edge (flat set, internally
    conflicted coins, or no side with both confirmation and reward room). The
    previous WHEN TO RETURN ZERO TRADES checklist is still gone; the framing
    trusts Claude to judge a genuine edge rather than forcing a count."""

    def test_rare_zero_framing_present(self) -> None:
        # Fix 6 (2026-06-10): quality over quota — declining a skip-quality
        # candidate is correct, and returning fewer than 3 when fewer genuinely
        # qualify is correct. Replaces the stale D2 "Returning 0 or 1 is CORRECT"
        # wording the 2026-06-09 min-3 mandate had already removed (so this was
        # red before Fix 6 too).
        assert "QUALITY OVER QUOTA" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "return fewer than 3" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert (
            "DECLINING that candidate is correct trading"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_no_when_to_return_zero_section(self) -> None:
        assert "WHEN TO RETURN ZERO TRADES" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_explicit_skip_reasons(self) -> None:
        """Pre-rewrite the prompt enumerated MANIPULATION_WINDOW and
        RECENT_LOSER_COOLDOWN as primary-state skip triggers — the
        new framing removes the enumeration so Claude doesn't anchor
        on those state labels as default skip reasons."""
        # The state labels still appear in BRIEFING_SYSTEM_PROMPT_SUFFIX
        # as descriptive content for the per-coin block, but they are
        # NOT framed as skip-reasons in the system prompt's primary
        # text any more.
        assert (
            "All candidates are flagged MANIPULATION_WINDOW"
            not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "RECENT_LOSER_COOLDOWN as the primary state"
            not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_no_numeric_gate_in_zero_reasons(self) -> None:
        """Pre-fix: 'All top-6 candidates have TradeScorer total < 60'
        was a hard numeric gate Claude had to honor. Path C dropped it
        and the aggressive-framing rewrite continues to omit it."""
        assert "TradeScorer total < 60" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "total < 60" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_ensemble_consensus_gate_in_zero_reasons(self) -> None:
        """Pre-fix: 'All top-6 have CONFLICT or WEAK ensemble consensus'
        was a hard gate. Stays gone."""
        assert "CONFLICT or WEAK ensemble consensus" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
