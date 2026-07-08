"""Aggressive-framing rewrite (2026-05-05) — system-prompt dispatch + parser.

Confirms:
1. ``TRADE_SYSTEM_PROMPT_ZERO_TWO`` opens with the FIX Change 7 verbatim
   aggressive-exploitation framing and retains the operational rules
   (DIRECTION BY REGIME, FEAR & GREED, FOR EACH NEW TRADE, POSITION
   GATE, JSON spec, RULES) needed by the parser.
2. Flag-off path serves the rewritten legacy ``TRADE_SYSTEM_PROMPT``.
3. Flag-on path swaps in ``TRADE_SYSTEM_PROMPT_ZERO_TWO``.
4. ``BRIEFING_SYSTEM_PROMPT_SUFFIX`` continues to apply on top either way.
5. ``_parse_trade_plan`` handles ``new_trades=[]`` cleanly.

The "STRICT 0-2 CONTRACT" / "Three or more is a HARD violation" /
"JUDGMENT — USE THE FULL PER-COIN DATA" / "DO NOT require unanimous
agreement" / "trust the structure" coaching from the previous Stage 2
phase-3 prompt is gone; the new framing reframes the contract as
"return 2-4, zero or one only when the entire candidate set is
genuinely flat" with an opportunity-pattern catalog (overbought =
fade, extended = exhaustion, range tops = reversal, etc.). Range
expanded from 1-2 to 2-4 on 2026-05-05 alongside top_n_to_brain
6 → 10 (see Stage2Settings docstring).
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.brain.strategist import (
    BRIEFING_SYSTEM_PROMPT_SUFFIX,
    TRADE_SYSTEM_PROMPT,
    TRADE_SYSTEM_PROMPT_ZERO_TWO,
    ClaudeStrategist,
)
from src.config.settings import Stage2Settings


class _CapturingClaude:
    """Minimal stub that captures the (prompt, system) tuple."""

    def __init__(self, response: str = '{"new_trades": []}') -> None:
        self.response = response
        self.captured_system: str | None = None
        self.captured_prompt: str | None = None

    async def send_message(self, prompt: str, system: str) -> str:
        self.captured_prompt = prompt
        self.captured_system = system
        return self.response

    def extract_json(self, raw: str) -> dict:
        import json as _json
        return _json.loads(raw)


def _stub_strategist(
    *, zero_two: bool, briefing: bool, claude=None
) -> ClaudeStrategist:
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.claude = claude or _CapturingClaude()
    s.services = {}
    s.settings = SimpleNamespace(
        stage2=Stage2Settings(enable_zero_two_contract=zero_two),
        brain=SimpleNamespace(
            use_packages=False,  # skip the package-read block in build
            surface_briefing_fields=briefing,
        ),
    )
    s._has_urgent_concerns = False
    s._last_regime_str = "ranging"
    s._last_regime_confidence = 0.5
    s._last_fg_value = 50
    return s


class TestZeroTwoConstantHasAggressiveFraming:
    """The rewritten ZERO_TWO constant opens with the FIX Change 7
    verbatim aggressive-exploitation framing, the opportunity-pattern
    catalog, and the 4-step decision process."""

    def test_aim_line_present(self) -> None:
        assert (
            "Your aim is to exploit the current market situation and "
            "aggressively fetch the maximum profitable trade"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_opportunity_pattern_catalog_present(self) -> None:
        """The pattern catalog is the heart of the new framing — these
        are the exploitation plays Claude should match per coin."""
        assert "Overbought conditions are fade setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Extended moves are exhaustion plays" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Range tops are reversal setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Range bottoms are breakout setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert (
            "Pullbacks in trends are continuation entries"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert "Liquidity sweeps are reclaim setups" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_four_step_process_present(self) -> None:
        """The 4-step decision process tells Claude how to reason
        through each candidate's exploitation play."""
        assert (
            "1. Read the FULL evidence: structural data, signals, regime, and ensemble votes"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "2. Identify what kind of opportunity this coin's current state represents"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "3. Determine the direction and entry that exploits that opportunity"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )
        assert (
            "4. Compare across candidates and pick the BEST GENUINE plays"
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_two_to_four_range_with_rare_zero_or_one(self) -> None:
        """Fix 6 (2026-06-10): QUALITY-over-quota — return the 2 to 5 BEST
        GENUINE plays, and returning fewer than 3 (down to 0) is CORRECT whenever
        fewer genuinely qualify. Never manufacture a trade to hit a count.
        Replaces the stale D2 'Return up to 4 trades' / 'Returning 0 or 1 is
        CORRECT' wording the 2026-06-09 min-3 mandate had already removed."""
        assert "2 to 5 BEST GENUINE plays" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "QUALITY OVER QUOTA" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "return fewer than 3" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_closing_imperative_present(self) -> None:
        assert (
            "Aggressive exploitation. Maximum profit. Find the play."
            in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_legacy_strict_contract_phrasing_removed(self) -> None:
        """The previous-iteration STRICT 0-2 / HARD violation /
        JUDGMENT / "trust the structure" coaching language is gone."""
        assert "STRICT 0-2 CONTRACT" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Three or more is a HARD violation" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "TWO trades is the cap" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "JUDGMENT — USE THE FULL PER-COIN DATA" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Weigh these together" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "DO NOT require unanimous agreement" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "trust the structure" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert (
            "Missing a genuine opportunity is as costly"
            not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        )

    def test_legacy_volume_mandate_still_absent(self) -> None:
        """The pre-phase-3 "ALWAYS find at least 2 trades" mandate
        was already absent from ZERO_TWO; that property still holds."""
        assert "ALWAYS find at least 2" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Target: 3-6" not in TRADE_SYSTEM_PROMPT_ZERO_TWO


class TestZeroTwoOperationalCapsPreserved:
    """Below the new aggressive framing, the operational machinery the
    parser depends on is retained verbatim: DIRECTION BY REGIME, FEAR
    & GREED, FOR EACH NEW TRADE, POSITION GATE, JSON spec, RULES."""

    def test_direction_by_regime_intact(self) -> None:
        assert "DIRECTION BY REGIME" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "ranging" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "volatile" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "dead" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_fear_greed_contrarian_intact(self) -> None:
        assert "FEAR & GREED" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "F&G < 20" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "F&G > 80" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_position_gate_intact(self) -> None:
        assert "[POS]" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "NEVER suggest a [POS] coin" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_json_response_schema_intact(self) -> None:
        """The literal JSON schema string Claude must echo — parser
        contract."""
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

    def test_size_limit_rule_points_at_per_trade_limit(self) -> None:
        """Rule 8 must reference the per-trade size limit shown in the user
        prompt. Fund-management rewrite (2026-05-31): the PROPER FUNDING rule
        references the per-trade size limit (per trade) AND the "Available for
        new trades" portfolio budget, not the legacy bare-respect wording."""
        assert "per-trade size limit" in TRADE_SYSTEM_PROMPT_ZERO_TWO.lower()
        assert "PROPER FUNDING" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "Available for new trades" in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_no_legacy_hardcoded_size_range(self) -> None:
        """Old prompt said size_usd: $500-$5000; STRONG = larger
        ($2000-$5000) — that range is contract-blind. New prompt
        defers to the per-trade size limit shown in the user prompt."""
        assert "$500-$5000" not in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "$2000-$5000" not in TRADE_SYSTEM_PROMPT_ZERO_TWO

    def test_rule_1_reframes_two_to_four_range(self) -> None:
        """RULES 1 — Fix 6 (2026-06-10): quality over quota; declining a
        skip-quality candidate is correct, and returning fewer than 3 when fewer
        genuinely qualify is correct (replaces the stale D2 'Return up to 4
        trades — quality over count' wording)."""
        assert "2 to 5 BEST GENUINE plays" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "QUALITY OVER QUOTA" in TRADE_SYSTEM_PROMPT_ZERO_TWO
        assert "return fewer than 3" in TRADE_SYSTEM_PROMPT_ZERO_TWO


class TestLegacyConstantHasAggressiveFraming:
    """The dormant legacy ``TRADE_SYSTEM_PROMPT`` was rewritten in
    lockstep with ZERO_TWO so a flag-flip back to
    ``enable_zero_two_contract=false`` doesn't re-introduce the
    "PAPER TRADING. We need VOLUME...ALWAYS find at least 2 trades"
    framing the operator wanted gone, or the avoidance-bias rule
    "Overbought RSI...prefer waiting for a pullback to RSI 40-60"
    that directly contradicts the new pattern catalog ("Overbought
    conditions are fade setups").
    """

    def test_legacy_aim_line_present(self) -> None:
        assert (
            "Your aim is to exploit the current market situation"
            in TRADE_SYSTEM_PROMPT
        )

    def test_legacy_pattern_catalog_present(self) -> None:
        assert "Overbought conditions are fade setups" in TRADE_SYSTEM_PROMPT
        assert "Extended moves are exhaustion plays" in TRADE_SYSTEM_PROMPT
        assert "Range tops are reversal setups" in TRADE_SYSTEM_PROMPT
        assert "Range bottoms are breakout setups" in TRADE_SYSTEM_PROMPT
        assert (
            "Pullbacks in trends are continuation entries" in TRADE_SYSTEM_PROMPT
        )
        assert "Liquidity sweeps are reclaim setups" in TRADE_SYSTEM_PROMPT

    def test_legacy_four_step_process_present(self) -> None:
        assert (
            "1. Read the FULL evidence: structural data, signals, regime, and ensemble votes"
            in TRADE_SYSTEM_PROMPT
        )
        assert (
            "4. Compare across candidates and pick the BEST GENUINE plays"
            in TRADE_SYSTEM_PROMPT
        )

    def test_legacy_two_to_four_range_with_rare_zero_or_one(self) -> None:
        # Fix 6 (2026-06-10): legacy prompt reworded in lockstep — quality over quota.
        assert "2 to 5 BEST GENUINE plays" in TRADE_SYSTEM_PROMPT
        assert "QUALITY OVER QUOTA" in TRADE_SYSTEM_PROMPT

    def test_legacy_volume_mandate_removed(self) -> None:
        assert "ALWAYS find at least 2 trades" not in TRADE_SYSTEM_PROMPT
        assert "Target: 3-6 trades per cycle" not in TRADE_SYSTEM_PROMPT
        assert "PAPER TRADING. We need VOLUME" not in TRADE_SYSTEM_PROMPT
        assert "Minimum: 2 trades per cycle" not in TRADE_SYSTEM_PROMPT
        assert "Maximum: 8 trades" not in TRADE_SYSTEM_PROMPT

    def test_legacy_pullback_avoidance_language_removed(self) -> None:
        """The "Overbought RSI...prefer waiting for a pullback to RSI
        40-60" language directly contradicts the new pattern catalog
        ("Overbought conditions are fade setups") — must be gone."""
        assert "prefer waiting for a pullback" not in TRADE_SYSTEM_PROMPT
        assert "RSI 40-60" not in TRADE_SYSTEM_PROMPT
        assert (
            "Oversold RSI in a downtrend means the trend is STRONG, NOT"
            not in TRADE_SYSTEM_PROMPT
        )
        assert (
            "extreme fear in a downtrend means the trend is accelerating"
            not in TRADE_SYSTEM_PROMPT
        )

    def test_legacy_setup_quality_thresholds_removed(self) -> None:
        """SETUP QUALITY block (STRONG/GOOD/NEUTRAL/WEAK score
        thresholds) was anchoring sizing on score gates — gone with
        the framing rewrite."""
        assert "STRONG consensus (score >= 70)" not in TRADE_SYSTEM_PROMPT
        assert "GOOD consensus (score 55-69)" not in TRADE_SYSTEM_PROMPT
        assert "WEAK consensus (score < 40): Skip" not in TRADE_SYSTEM_PROMPT

    def test_legacy_per_trade_size_limit_referenced(self) -> None:
        """size_usd guidance defers to the per-trade size limit shown in the
        user prompt rather than a legacy hardcoded range. Fund-management
        rewrite (2026-05-31): PROPER FUNDING references the per-trade size limit
        + "Available for new trades"; the legacy hardcoded ranges stay absent."""
        assert "per-trade size limit" in TRADE_SYSTEM_PROMPT.lower()
        assert "PROPER FUNDING" in TRADE_SYSTEM_PROMPT
        assert "$500-$5000" not in TRADE_SYSTEM_PROMPT
        assert "MINIMUM $500" not in TRADE_SYSTEM_PROMPT
        assert "TYPICAL $1000-$3000" not in TRADE_SYSTEM_PROMPT

    def test_legacy_direction_performance_rule_removed(self) -> None:
        """The legacy RULES 13 referenced the DIRECTION PERFORMANCE
        section which was removed in commit 5 — its rule is also gone."""
        assert "CHECK THE DIRECTION PERFORMANCE" not in TRADE_SYSTEM_PROMPT
        assert (
            "if one direction is failing badly, AVOID that direction"
            not in TRADE_SYSTEM_PROMPT
        )

    def test_legacy_closing_imperative_present(self) -> None:
        assert (
            "Aggressive exploitation. Maximum profit. Find the play."
            in TRADE_SYSTEM_PROMPT
        )


class TestDispatchInline:
    """Inline simulation of the dispatch logic — avoids running the full
    create_trade_plan body which depends on many services. We replicate
    the exact branch from strategist.py:create_trade_plan to confirm
    the flag behavior is correct."""

    def _resolve_system(self, *, zero_two: bool, briefing: bool) -> str:
        s = _stub_strategist(zero_two=zero_two, briefing=briefing)
        _stage2_cfg_a = getattr(s.settings, "stage2", None)
        _zero_two = bool(getattr(
            _stage2_cfg_a, "enable_zero_two_contract", False,
        )) if _stage2_cfg_a else False
        system = TRADE_SYSTEM_PROMPT_ZERO_TWO if _zero_two else TRADE_SYSTEM_PROMPT
        if bool(getattr(
            s.settings.brain, "surface_briefing_fields", False,
        )):
            system += BRIEFING_SYSTEM_PROMPT_SUFFIX
        return system

    def test_legacy_path_default(self) -> None:
        """Legacy path now serves the rewritten aggressive prompt so a
        flag-flip back to ``enable_zero_two_contract=false`` doesn't
        re-introduce defensive framing."""
        out = self._resolve_system(zero_two=False, briefing=False)
        assert "Your aim is to exploit the current market situation" in out
        assert (
            "Aggressive exploitation. Maximum profit. Find the play." in out
        )
        assert "ALWAYS find at least 2 trades" not in out
        assert "STRICT 0-2" not in out

    def test_zero_two_path(self) -> None:
        out = self._resolve_system(zero_two=True, briefing=False)
        assert "Your aim is to exploit the current market situation" in out
        assert (
            "Aggressive exploitation. Maximum profit. Find the play." in out
        )
        assert "STRICT 0-2 CONTRACT" not in out

    def test_briefing_suffix_appended_to_legacy(self) -> None:
        out = self._resolve_system(zero_two=False, briefing=True)
        assert "Your aim is to exploit the current market situation" in out
        assert "BRIEFING-MODE FIELDS" in out

    def test_briefing_suffix_appended_to_zero_two(self) -> None:
        out = self._resolve_system(zero_two=True, briefing=True)
        assert "Aggressive exploitation. Maximum profit. Find the play." in out
        assert "BRIEFING-MODE FIELDS" in out


class TestParserHandlesEmptyTrades:
    def test_parse_empty_new_trades(self) -> None:
        s = ClaudeStrategist.__new__(ClaudeStrategist)
        plan = s._parse_trade_plan({
            "new_trades": [],
            "market_view": "no edge — entire candidate set genuinely flat",
            "risk_level": "cautious",
        })
        assert plan.new_trades == []
        assert plan.market_view == "no edge — entire candidate set genuinely flat"
        assert plan.risk_level == "cautious"

    def test_parse_missing_new_trades_key(self) -> None:
        s = ClaudeStrategist.__new__(ClaudeStrategist)
        # Defensively: parser must handle missing key (Claude might omit
        # under the rare-zero framing if it had nothing to say).
        plan = s._parse_trade_plan({"market_view": "no edge"})
        assert plan.new_trades == []
