"""Stage 2 phase 4 — priority-aware trim drops optional, never essential.

These tests replicate the trim block's logic inline so we can drive it
deterministically without spinning up the full _build_trade_prompt
pipeline. The production code path is exactly the same (read it next
to this file in src/brain/strategist.py).
"""

from src.brain.strategist import (
    _TRIM_PRIORITY_ESSENTIAL,
    _TRIM_PRIORITY_IMPORTANT,
    _TRIM_PRIORITY_OPTIONAL,
    _infer_section_priority,
)


def _priority_trim_inline(
    sections: list[str],
    section_cap: int = 80,
    char_cap: int = 14000,
) -> tuple[list[str], list[str], int, int]:
    """Replicates the priority-aware trim from _build_trade_prompt.

    Returns ``(sections_after, dropped_labels, dropped_optional,
    dropped_important)``.
    """
    sections = list(sections)
    if not (
        len(sections) > section_cap
        or sum(len(s) for s in sections) > char_cap
    ):
        return sections, [], 0, 0

    priorities = [
        _infer_section_priority(s, i) for i, s in enumerate(sections)
    ]
    dropped_labels: list[str] = []
    dropped_optional = 0
    dropped_important = 0
    for target_pri in (_TRIM_PRIORITY_OPTIONAL, _TRIM_PRIORITY_IMPORTANT):
        i = len(sections) - 1
        while i >= 0 and (
            len(sections) > section_cap
            or sum(len(s) for s in sections) > char_cap
        ):
            if priorities[i] == target_pri:
                lbl = (
                    sections[i].split("\n", 2)[1]
                    if "\n" in sections[i] else sections[i]
                )[:60].strip()
                dropped_labels.append(lbl)
                if target_pri == _TRIM_PRIORITY_OPTIONAL:
                    dropped_optional += 1
                else:
                    dropped_important += 1
                sections.pop(i)
                priorities.pop(i)
            i -= 1
    return sections, dropped_labels, dropped_optional, dropped_important


class TestPriorityTrim:
    def test_under_caps_is_noop(self) -> None:
        sections = [
            "coaching content small",
            "\n## MARKET DATA\nx" * 10,
        ]
        out, dropped, n_opt, n_imp = _priority_trim_inline(sections, char_cap=14000)
        assert out == sections
        assert dropped == []
        assert n_opt == 0
        assert n_imp == 0

    def test_drops_optional_first(self) -> None:
        # 1 essential, 1 important, 1 optional, all 6000 chars; cap=14000
        # → drops only the optional.
        essential = "coaching " + ("X" * 6000)  # index 0 forces essential
        important = "\n## STRATEGY HINTS (automated signals)\n" + ("Y" * 6000)
        optional = "\n## SENTIMENT\nFear & Greed = 42\n" + ("Z" * 6000)
        sections = [essential, important, optional]
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )
        # Essential + important survive; optional dropped.
        assert essential in out
        assert important in out
        assert optional not in out
        assert n_opt == 1
        assert n_imp == 0

    def test_drops_important_after_optional_exhausted(self) -> None:
        # 1 essential at 6000 chars, 1 optional at 6000, 1 important at
        # 6000. cap=10000 forces dropping optional (left at 12000),
        # then important (left at 6000). Essential never dropped.
        essential = "coaching " + ("X" * 6000)
        important = "\n## STRATEGY HINTS\n" + ("Y" * 6000)
        optional = "\n## SENTIMENT\n" + ("Z" * 6000)
        sections = [essential, optional, important]
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=10000,
        )
        # Both optional and important dropped; only essential remains.
        assert essential in out
        assert important not in out
        assert optional not in out
        assert n_opt == 1
        assert n_imp == 1

    def test_essentials_never_dropped(self) -> None:
        # 7 essentials totalling ~21k chars over the 14k cap; trim must
        # NOT drop any essential. The result is over-cap but consistent
        # with the design: essentials are inviolate.
        big = "X" * 3000
        sections = [
            "coaching " + big,                     # idx 0 → essential
            "\n## MARKET DATA\n" + big,           # essential by marker
            "\n## ACCOUNT\nEquity: $1k\n" + big,  # essential
            "TRADEABLE COINS THIS CYCLE\n" + big, # essential
            "\n## TRADE CANDIDATES\n" + big,      # essential
            "\n## OPEN POSITIONS\n" + big,        # essential
            "\n## MARKET REGIME (CONTEXT)\n" + big,  # essential
        ]
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )
        assert n_opt == 0
        assert n_imp == 0
        assert dropped == []
        assert len(out) == 7
        assert sum(len(s) for s in out) > 14000  # over-cap by design

    def test_unknown_label_treated_as_optional(self) -> None:
        # 1 essential + 1 unknown — should drop unknown first.
        essential = "coaching block " + ("X" * 5000)
        unknown = "garbage_no_marker_here\n" + ("Y" * 10000)
        sections = [essential, unknown]
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=10000,
        )
        assert essential in out
        assert unknown not in out
        assert n_opt == 1


class TestLegacyTrimNotDisturbed:
    """Sanity check: the legacy trim path lives next to this and must be
    byte-identical when the flag is off. We don't simulate the legacy
    path here — the integration verification (Phase 0 baseline vs Phase 1
    post-deploy) already covers byte-for-byte equivalence in production.
    These tests only validate the new priority path."""

    def test_classifier_does_not_mutate_sections(self) -> None:
        sections = ["coaching", "\n## MARKET DATA\n[x]"]
        before = list(sections)
        _ = [_infer_section_priority(s, i) for i, s in enumerate(sections)]
        assert sections == before


class TestFundRulesSurvivesTrim:
    """XRAY phase-5 fix — FUND RULES (sizing-contract section emitted by
    tiered_capital.FundLimits.to_prompt_text) is now in
    _TRIM_ESSENTIAL_MARKERS so it survives even when the 14k cap fires.

    Pre-fix the section header lacked a ``##`` prefix and didn't match
    any marker, so it fell through to OPTIONAL and was the FIRST thing
    dropped — caught in the 2026-05-05 audit cycles at 05:41, 05:47,
    06:42 with FUND RULES in dropped_labels."""

    def test_fund_rules_survives_when_optional_filler_dominates(self) -> None:
        """50 OPTIONAL filler sections + 1 FUND RULES at the END
        totalling > 14k chars → trim drops the fillers, FUND RULES
        survives because it's now ESSENTIAL."""
        coaching = "coaching essential " + ("X" * 200)
        fund_rules_text = (
            "\nFUND RULES (non-negotiable):\n"
            "  Total equity: $6,008\n"
            "  Starting equity: $168,000\n"
            "  Max single trade: $451\n"
            "  Max positions: 6\n"
            "  Size your trades within available capital."
        )
        # 50 OPTIONAL filler sections. Use the SENTIMENT marker so the
        # classifier picks them up as OPTIONAL deterministically.
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, *fillers, fund_rules_text]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        # FUND RULES still present.
        assert fund_rules_text in out
        # Coaching (idx 0) still present.
        assert coaching in out
        # At least some fillers were dropped.
        assert n_opt > 0

    def test_fund_rules_survives_when_only_essentials_and_fund_rules(self) -> None:
        """Even when the only sections present are essentials + FUND
        RULES + filler, FUND RULES is preserved alongside the other
        essentials."""
        coaching = "coaching " + ("X" * 3000)
        market_data = "\n## MARKET DATA\n" + ("X" * 3000)
        regime = (
            "\n## MARKET REGIME (CONTEXT)\n"
            + ("X" * 3000)
        )
        fund_rules_text = (
            "\nFUND RULES (non-negotiable):\n  Total equity: $6,008\n"
            + ("Y" * 2000)
        )
        # Generous filler that should drop.
        filler = "\n## SENTIMENT\n" + ("Z" * 8000)
        sections = [coaching, market_data, regime, fund_rules_text, filler]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert fund_rules_text in out
        assert coaching in out
        assert market_data in out
        assert regime in out
        # The SENTIMENT filler dropped.
        assert filler not in out
        # 'FUND RULES' must NOT appear in dropped_labels.
        for lbl in dropped:
            assert "FUND RULES" not in lbl, (
                f"FUND RULES should not appear in dropped_labels: {dropped}"
            )


class TestTodayPerformanceSurvivesTrim:
    """XRAY phase-5 follow-up — ``## TODAY'S PERFORMANCE`` and ``## TODAY:``
    are promoted to ESSENTIAL so the daily-trade count and PnL context
    Claude uses for sizing survive the 14k cap.

    The Phase 0 baseline (2026-05-05) showed three real cycles where
    ``CLAUDE_PROMPT_TRIMMED`` dropped both ``"Trades today: 0"`` and
    ``"Daily PnL: +0.00%"`` — both lines emit from this section. Without
    them Claude has no view on cumulative daily activity, which the
    FUND RULES tier definition consumes when sizing new trades.
    """

    def test_today_performance_survives_under_cap(self) -> None:
        """``## TODAY'S PERFORMANCE`` is preserved alongside FUND RULES
        when the trim has to drop OPTIONAL fillers to fit the cap."""
        coaching = "coaching essential " + ("X" * 200)
        today_text = (
            "\n## TODAY'S PERFORMANCE\n"
            "  Trades today: 0\n"
            "  Daily PnL: +0.00%\n"
        )
        fund_rules_text = (
            "\nFUND RULES (non-negotiable):\n"
            "  Total equity: $6,008\n"
        )
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, *fillers, today_text, fund_rules_text]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert today_text in out, (
            "TODAY'S PERFORMANCE must survive trim — it carries the "
            "Trades today / Daily PnL lines used for sizing context."
        )
        assert fund_rules_text in out
        assert coaching in out
        assert n_opt > 0  # at least some fillers dropped
        for lbl in dropped:
            assert "TODAY'S PERFORMANCE" not in lbl
            assert "Trades today" not in lbl
            assert "Daily PnL" not in lbl

    def test_today_short_marker_survives_under_cap(self) -> None:
        """The shorter ``## TODAY:`` summary header is also preserved."""
        coaching = "coaching essential " + ("X" * 200)
        today_short = "\n## TODAY: PnL=+1.2% trades=3"
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, *fillers, today_short]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert today_short in out
        assert n_opt > 0


class TestPerTradeSizeLimitSurvivesTrim:
    """Aggressive-framing rewrite (2026-05-05) — the FundLimits.to_prompt_text()
    block was replaced in _build_trade_prompt by two clean lines led by
    ``Per-trade size limit: $X``. The new marker is ESSENTIAL so the
    minimal sizing block stays protected from the priority-aware trim.

    Without the marker, the new lines fall through to OPTIONAL and the
    14k cap drops them first — leaving Claude without the numeric ceiling
    for size_usd. These tests simulate the trim algorithm directly to
    confirm the marker binds correctly.
    """

    def test_per_trade_size_limit_survives_when_optional_filler_dominates(
        self,
    ) -> None:
        """50 OPTIONAL filler sections + 1 minimal sizing block at the
        END totalling > 14k chars → trim drops fillers, sizing block
        survives because it's now ESSENTIAL."""
        coaching = "coaching essential " + ("X" * 200)
        sizing_text = (
            "\nPer-trade size limit: $1,500\n"
            "Maximum concurrent positions: 4"
        )
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, *fillers, sizing_text]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert sizing_text in out
        assert coaching in out
        assert n_opt > 0
        for lbl in dropped:
            assert "Per-trade size limit" not in lbl, (
                f"Per-trade size limit should never appear in dropped "
                f"labels: {dropped}"
            )

    def test_per_trade_size_limit_survives_alongside_essentials(self) -> None:
        """Even when only essentials + filler are present, the new sizing
        block is preserved alongside MARKET DATA and MARKET REGIME."""
        coaching = "coaching " + ("X" * 3000)
        market_data = "\n## MARKET DATA\n" + ("X" * 3000)
        regime = (
            "\n## MARKET REGIME (CONTEXT)\n"
            + ("X" * 3000)
        )
        sizing_text = (
            "\nPer-trade size limit: $1,500\n"
            "Maximum concurrent positions: 4"
            + ("Y" * 1500)
        )
        filler = "\n## SENTIMENT\n" + ("Z" * 8000)
        sections = [coaching, market_data, regime, sizing_text, filler]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert sizing_text in out
        assert coaching in out
        assert market_data in out
        assert regime in out
        assert filler not in out
        for lbl in dropped:
            assert "Per-trade size limit" not in lbl, (
                f"Per-trade size limit should not appear in dropped "
                f"labels: {dropped}"
            )


# ─── Issue A fix (2026-05-08) — URGENT WATCHDOG ALERTS + bare-line ─────
# metadata + raised char cap. The 13:00–16:00 UTC window on 2026-05-08
# showed 21 priority-mode CLAUDE_PROMPT_TRIMMED events with raw prompts
# 16,910–19,919 chars vs the prior 14,000-char cap. 14 events dropped
# URGENT WATCHDOG ALERTS, all 21 dropped Equity / Available / Maximum
# concurrent positions, and 17 cascaded into IMPORTANT-tagged sections.
# Fix: marker tuple corrected (commit issueA/3a), char cap raised to
# 30,000 (commit issueA/3b). These tests cover both.


class TestUrgentWatchdogSurvivesTrim:
    """The live CALL_A urgent block emitted by
    ``src/core/urgent_queue.py:format_for_prompt`` has header
    ``"\\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\\n"``.
    The new marker ``"## URGENT WATCHDOG ALERTS"`` substring-matches the
    live header so the urgent block is now ESSENTIAL and survives trim.
    """

    URGENT_HEADER = (
        "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"
        "These positions need your attention. For each, decide: "
        "hold, close, tighten_stop, or set_exit.\n"
        "You MUST include position_actions for each alerted symbol "
        "in your response."
    )

    def test_urgent_block_survives_when_optional_filler_dominates(
        self,
    ) -> None:
        """50 OPTIONAL fillers + URGENT block at the end ≈ 18k chars at
        the legacy 14k cap → fillers drop, URGENT survives."""
        coaching = "coaching essential " + ("X" * 200)
        urgent = (
            self.URGENT_HEADER
            + "\n[CRITICAL] BTCUSDT [Buy] — PnL: -1.20%"
            + ("\n  warnings ..." * 30)
        )
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, *fillers, urgent]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert urgent in out, "URGENT block must survive trim"
        assert coaching in out
        assert n_opt > 0
        for lbl in dropped:
            assert "URGENT WATCHDOG" not in lbl, (
                f"URGENT WATCHDOG must never appear in dropped_labels: "
                f"{dropped}"
            )

    def test_urgent_block_survives_alongside_essentials(self) -> None:
        """Worst case — only essentials + URGENT + 1 large filler. URGENT
        is preserved with the other essentials; only the filler drops."""
        coaching = "coaching " + ("X" * 3000)
        market_data = "\n## MARKET DATA\n" + ("X" * 3000)
        regime = (
            "\n## MARKET REGIME (CONTEXT)\n"
            + ("X" * 3000)
        )
        urgent = self.URGENT_HEADER + ("Y" * 2000)
        filler = "\n## SENTIMENT\n" + ("Z" * 8000)
        sections = [coaching, market_data, regime, urgent, filler]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert urgent in out
        assert coaching in out
        assert market_data in out
        assert regime in out
        assert filler not in out
        for lbl in dropped:
            assert "URGENT WATCHDOG" not in lbl


class TestBareLineMetadataSurvivesTrim:
    """Equity / Available / Maximum concurrent positions are appended at
    strategist.py:2861/2862/2904 as their own single-line entries in the
    ``sections`` list with no leading ``##`` header. The new bare-line
    markers (``Equity:``, ``Available:``, ``Maximum concurrent positions``)
    classify them ESSENTIAL so they survive the priority-aware trim."""

    def test_three_bare_lines_survive_when_filler_dominates(self) -> None:
        """Replicates the 13:00–16:00 trim pattern: a coaching block, the
        three bare-line metadata sections, and many OPTIONAL fillers
        pushing total chars over the legacy 14k cap. All three bare lines
        survive (pre-fix all three appeared in dropped_labels every cycle).
        """
        coaching = "coaching essential " + ("X" * 200)
        equity = "Equity: $182,520.28"
        available = "Available: $99,997.51"
        max_pos = "Maximum concurrent positions: 10"
        fillers = [
            f"\n## SENTIMENT block {i}\n" + ("Z" * 300)
            for i in range(50)
        ]
        sections = [coaching, equity, available, max_pos, *fillers]

        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=14000,
        )

        assert equity in out
        assert available in out
        assert max_pos in out
        for lbl in dropped:
            assert lbl != equity
            assert lbl != available
            assert lbl != max_pos
            assert "Equity:" not in lbl
            assert "Available:" not in lbl
            assert "Maximum concurrent positions" not in lbl


class TestRaisedCharCap:
    """Issue A Phase 3b — char cap raised from 14,000 to 30,000.

    Verifies the algorithm respects the new cap at the production value.
    The production constant lives at strategist.py:3018 inside
    ``_build_trade_prompt`` (function-local). These tests exercise the
    behaviour at the value the production code uses.
    """

    PRODUCTION_CHAR_CAP = 30000

    def test_prompt_at_25k_chars_is_not_trimmed_under_new_cap(self) -> None:
        """A 25k-char prompt would have been trimmed under the legacy
        14k cap but is well under the 30k cap and stays intact."""
        # 5 essential sections at 5,000 chars each = 25,000 chars total.
        sections = [
            "coaching " + ("X" * 5000),
            "\n## MARKET DATA\n" + ("X" * 5000),
            "\n## ACCOUNT\n" + ("X" * 5000),
            "\n## TRADE CANDIDATES\n" + ("X" * 5000),
            "\n## MARKET REGIME (CONTEXT)\n"
            + ("X" * 5000),
        ]
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=self.PRODUCTION_CHAR_CAP,
        )
        assert out == sections
        assert dropped == []
        assert n_opt == 0
        assert n_imp == 0

    def test_prompt_at_31k_chars_still_trims_optional_under_new_cap(
        self,
    ) -> None:
        """A 31k-char prompt with OPTIONAL filler at the end is trimmed
        under the new cap. Behaviour preserved: trim still works for
        runaway prompts; OPTIONAL drops first."""
        coaching = "coaching " + ("X" * 5000)
        essential = "\n## MARKET DATA\n" + ("X" * 10000)
        important = "\n## STRATEGY HINTS\n" + ("Y" * 8000)
        optional = "\n## SENTIMENT\n" + ("Z" * 8000)
        sections = [coaching, essential, important, optional]
        # Total ≈ 31,000 chars > 30k cap.
        out, dropped, n_opt, n_imp = _priority_trim_inline(
            sections, section_cap=80, char_cap=self.PRODUCTION_CHAR_CAP,
        )
        assert coaching in out
        assert essential in out
        assert important in out
        assert optional not in out
        assert n_opt == 1
        assert n_imp == 0

    def test_production_constant_is_30000(self) -> None:
        """Defends the cap value against accidental rollback. If the
        cap moves intentionally, this test moves with it — a deliberate
        guardrail, not a maintenance burden."""
        import re
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "src" / "brain" / "strategist.py"
        )
        text = path.read_text()
        # Match the exact ``_CHAR_CAP = N`` assignment inside
        # _build_trade_prompt. Anchored to the assignment with optional
        # surrounding whitespace; tolerant of spacing changes.
        match = re.search(r"_CHAR_CAP\s*=\s*(\d+)", text)
        assert match is not None, "_CHAR_CAP assignment not found"
        assert int(match.group(1)) == self.PRODUCTION_CHAR_CAP, (
            f"Production _CHAR_CAP is {match.group(1)}, expected "
            f"{self.PRODUCTION_CHAR_CAP}"
        )
