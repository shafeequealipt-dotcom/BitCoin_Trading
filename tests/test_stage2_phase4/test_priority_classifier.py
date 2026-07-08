"""Stage 2 phase 4 — section-priority classifier (_infer_section_priority).

The classifier matches leading-marker substrings against three priority
buckets. Index 0 is always essential (coaching is the first append).
Unknown content defaults to optional.
"""

from src.brain.strategist import (
    _TRIM_PRIORITY_ESSENTIAL,
    _TRIM_PRIORITY_IMPORTANT,
    _TRIM_PRIORITY_OPTIONAL,
    _detect_essential_drift,
    _infer_section_priority,
    _summarize_kept_protections,
)


class TestPriorityClassifier:
    def test_index_zero_is_essential(self) -> None:
        # The coaching block is the first append and has no header.
        out = _infer_section_priority("any coaching content...", 0)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_market_data_is_essential(self) -> None:
        out = _infer_section_priority("## MARKET DATA\n[content]", 5)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_account_is_essential(self) -> None:
        out = _infer_section_priority("\n## ACCOUNT\nEquity: $1000", 7)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_trade_candidates_is_essential(self) -> None:
        out = _infer_section_priority(
            "## TRADE CANDIDATES (passed ScannerWorker qualitative gate)", 3,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_tradeable_coins_is_essential(self) -> None:
        out = _infer_section_priority(
            "TRADEABLE COINS THIS CYCLE (15 coins):\nBTC, ETH...", 4,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_market_regime_is_essential(self) -> None:
        # Issue 4 of 2026-05-19 direction-bias fix: canonical header is
        # now "## MARKET REGIME (CONTEXT)". The legacy header
        # "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)" is retained
        # in the marker tuple as a transitional fallback (see strategist.py
        # _TRIM_ESSENTIAL_MARKERS) — covered by
        # test_market_regime_legacy_header_still_essential below.
        out = _infer_section_priority(
            "\n## MARKET REGIME (CONTEXT)", 6,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_market_regime_legacy_header_still_essential(self) -> None:
        out = _infer_section_priority(
            "\n## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)", 6,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_strategy_hints_is_important(self) -> None:
        out = _infer_section_priority(
            "\n## STRATEGY HINTS (automated signals)", 8,
        )
        assert out == _TRIM_PRIORITY_IMPORTANT

    def test_today_performance_is_essential(self) -> None:
        """Marker classification — defense-in-depth.

        After the aggressive-framing rewrite (2026-05-05) the live Call A
        prompt no longer emits a "## TODAY'S PERFORMANCE" section
        (recency bias was training avoidance). The marker stays in
        ``_TRIM_ESSENTIAL_MARKERS`` so that if any future code path
        re-emits the header — Call B's _build_position_prompt still
        emits "## TODAY:" at line 2963, and the dead
        _build_context_prompt also emits the legacy header — the
        priority-aware trim still protects it from being dropped first.
        Pure classifier test; no production-prompt coupling required.
        """
        out = _infer_section_priority(
            "\n## TODAY'S PERFORMANCE\nDaily PnL: +1.2%", 10,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_today_short_marker_is_essential(self) -> None:
        """The shorter ``## TODAY:`` header is still essential —
        Call B's _build_position_prompt:2963 emits this short form."""
        out = _infer_section_priority(
            "## TODAY: PnL=+1.2% trades=3", 11,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_direction_perf_is_important(self) -> None:
        out = _infer_section_priority(
            "## DIRECTION PERFORMANCE (last 20 trades — read carefully)", 2,
        )
        assert out == _TRIM_PRIORITY_IMPORTANT

    def test_sentiment_is_optional(self) -> None:
        out = _infer_section_priority(
            "\n## SENTIMENT\nFear & Greed = 42", 9,
        )
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_session_is_optional(self) -> None:
        out = _infer_section_priority(
            "\n## SESSION: NY (mid)", 11,
        )
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_xray_long_listing_is_optional(self) -> None:
        out = _infer_section_priority(
            "\n## X-RAY STRUCTURAL SETUPS (ranked by confluence)\n...", 12,
        )
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_recent_lessons_is_optional(self) -> None:
        out = _infer_section_priority("\n## RECENT LESSONS\n- ...", 13)
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_market_data_error_is_optional(self) -> None:
        out = _infer_section_priority("(market data error: timeout)", 14)
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_unknown_content_defaults_to_optional(self) -> None:
        out = _infer_section_priority(
            "garbage label nobody recognizes", 15,
        )
        assert out == _TRIM_PRIORITY_OPTIONAL

    def test_empty_content_defaults_to_optional(self) -> None:
        # Index > 0 + empty content → optional (safe default).
        out = _infer_section_priority("", 5)
        assert out == _TRIM_PRIORITY_OPTIONAL

    # ─── XRAY phase-5 fix — FUND RULES is essential ──────────────────
    # The tiered_capital section emits "FUND RULES (non-negotiable):"
    # without a "##" prefix, so pre-fix it fell through to OPTIONAL and
    # was dropped first when the 14k cap fired (caught in the
    # 2026-05-05 audit cycles at 05:41 / 05:47 / 06:42 with FUND RULES
    # in dropped_labels). Substring match is sufficient because
    # _infer_section_priority looks at the first 200 chars.

    def test_fund_rules_is_essential(self) -> None:
        out = _infer_section_priority(
            "\nFUND RULES (non-negotiable):\n"
            "  Total equity: $6,008\n"
            "  Starting equity: $168,000\n", 7,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_fund_rules_minimal_header_match(self) -> None:
        """Substring match — even a bare 'FUND RULES' line classifies."""
        out = _infer_section_priority("FUND RULES", 5)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    # ─── Aggressive-framing rewrite — Per-trade size limit marker ─────
    # The 2026-05-05 framing rewrite replaced the FUND RULES block with
    # two clean lines led by ``Per-trade size limit:``. A new marker was
    # added to ``_TRIM_ESSENTIAL_MARKERS`` so the new sizing block stays
    # protected from the priority-aware trim. Without the marker, the
    # block falls through to OPTIONAL and the trim drops it first when
    # the 14k char cap fires.

    def test_per_trade_size_limit_is_essential(self) -> None:
        """The new minimal sizing block must classify ESSENTIAL so the
        priority-aware trim never drops the per-trade size ceiling
        from the prompt."""
        out = _infer_section_priority(
            "\nPer-trade size limit: $1,500\n"
            "Maximum concurrent positions: 4",
            7,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_per_trade_size_limit_bare_marker_match(self) -> None:
        """Substring match — the bare leading phrase classifies even
        without the dollar amount on the same line."""
        out = _infer_section_priority("Per-trade size limit", 4)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    # ─── Issue A fix (2026-05-08) — URGENT WATCHDOG ALERTS protection ───
    # The live CALL_A urgent block is emitted by
    # ``src/core/urgent_queue.py:format_for_prompt`` with the header
    # ``"\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"``.
    # The pre-fix marker tuple had ``"OVERRIDE — URGENT WATCHDOG ALERTS"``
    # which never matched the live header (no shared substring), so the
    # urgent block was classified OPTIONAL and dropped first. Verified
    # bug: 14 ``URGENT WATCHDOG`` drops in dropped_labels across the
    # 13:00–16:00 2026-05-08 window. The new marker
    # ``"## URGENT WATCHDOG ALERTS"`` is a substring of the live header
    # and protects the urgent block from the priority-aware trim.

    def test_urgent_watchdog_alerts_full_header_is_essential(self) -> None:
        """The exact live urgent_queue header classifies as essential."""
        out = _infer_section_priority(
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"
            "These positions need your attention. For each, decide: "
            "hold, close, tighten_stop, or set_exit.\n",
            8,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_urgent_watchdog_alerts_with_critical_tag_is_essential(
        self,
    ) -> None:
        """Header followed by a [CRITICAL] tag — common live shape."""
        out = _infer_section_priority(
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"
            "[CRITICAL] BTCUSDT [Buy] — PnL: -1.20%",
            10,
        )
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_urgent_watchdog_alerts_bare_marker_is_essential(self) -> None:
        """The bare marker substring classifies even when the rest of
        the header is omitted (defends against future header tweaks)."""
        out = _infer_section_priority("## URGENT WATCHDOG ALERTS", 4)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    # ─── Issue A fix (2026-05-08) — bare-line metadata protection ───────
    # ``_build_trade_prompt`` appends three single-line sections with no
    # ``##`` header (Equity / Available / Maximum concurrent positions)
    # at lines 2861, 2862, 2904. Pre-fix they fell through to OPTIONAL
    # and were dropped on every priority-mode trim event in the
    # 13:00–16:00 2026-05-08 window (all 21/21). They now have explicit
    # markers in ``_TRIM_ESSENTIAL_MARKERS``, mirroring the protection
    # already in place for the sibling ``Per-trade size limit:`` line.

    def test_equity_bare_line_is_essential(self) -> None:
        """``Equity: $X`` is appended as its own section (strategist.py:2861)
        with no leading header. The bare-line marker protects it."""
        out = _infer_section_priority("Equity: $182,520.28", 7)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_available_bare_line_is_essential(self) -> None:
        """``Available: $X`` is appended as its own section
        (strategist.py:2862) with no leading header."""
        out = _infer_section_priority("Available: $99,997.51", 8)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_max_concurrent_positions_bare_line_is_essential(self) -> None:
        """``Maximum concurrent positions: N`` is appended as its own
        section (strategist.py:2904) with no leading header."""
        out = _infer_section_priority("Maximum concurrent positions: 10", 9)
        assert out == _TRIM_PRIORITY_ESSENTIAL

    def test_dead_override_marker_no_longer_required(self) -> None:
        """The pre-fix marker ``OVERRIDE — URGENT WATCHDOG ALERTS`` only
        ever matched a system-prompt fragment at strategist.py:694, not
        any user-prompt section. Removing it must not regress any other
        classification — a section with that text alone now defaults to
        OPTIONAL (the safe fallback for unknown content)."""
        out = _infer_section_priority(
            "OVERRIDE — URGENT WATCHDOG ALERTS:\n"
            "The data below contains URGENT position alerts...",
            6,
        )
        assert out == _TRIM_PRIORITY_OPTIONAL


class TestEssentialDriftDetection:
    """Issue A Phase 3c — ``_detect_essential_drift`` flags any dropped
    label whose text contains a substring listed in
    ``_TRIM_ESSENTIAL_MARKERS``. Used by ``_build_trade_prompt``'s trim
    block to emit ``STRAT_TRIM_ESSENTIAL_DROPPED`` when classifier-vs-
    marker drift causes an essential section to be dropped despite the
    contractual protection.

    Defends specifically against the failure mode that drove this fix:
    the marker tuple was missing the substring of the live URGENT block
    header, so the classifier returned OPTIONAL and the trim happily
    dropped it. Had the drift detector existed, the dropped label
    ``"## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED"`` would
    have matched the new marker ``"## URGENT WATCHDOG ALERTS"`` and
    fired the warning every cycle.
    """

    def test_no_drift_when_only_optional_dropped(self) -> None:
        """OPTIONAL labels containing no essential substring → no drift."""
        labels = [
            "## SENTIMENT",
            "## SESSION: NY (mid)",
            "## RECENT LESSONS",
            "(market data error: timeout)",
        ]
        assert _detect_essential_drift(labels) == []

    def test_drift_detected_when_urgent_label_in_dropped(self) -> None:
        """The dropped label exposes ``## URGENT WATCHDOG ALERTS`` —
        the marker tuple's substring — so drift fires."""
        labels = [
            "## SENTIMENT",
            "## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED",
        ]
        out = _detect_essential_drift(labels)
        assert len(out) == 1
        marker, label = out[0]
        assert marker == "## URGENT WATCHDOG ALERTS"
        assert "URGENT WATCHDOG" in label

    def test_drift_detected_for_bare_line_metadata(self) -> None:
        """Bare-line essentials (Equity / Available / Maximum concurrent
        positions) trigger drift detection if they ever land in
        dropped_labels."""
        labels = [
            "## SENTIMENT",
            "Equity: $182,520.28",
            "Available: $99,997.51",
            "Maximum concurrent positions: 10",
        ]
        out = _detect_essential_drift(labels)
        # All three bare lines drifted.
        assert len(out) == 3
        markers_seen = {m for m, _lbl in out}
        assert markers_seen == {
            "Equity:",
            "Available:",
            "Maximum concurrent positions",
        }

    def test_drift_returns_first_marker_only_per_label(self) -> None:
        """A label that contains multiple essential substrings reports
        the FIRST matching marker only — keeps output bounded and
        avoids quadratic explosion if marker tuple grows."""
        # Crafted label contains both "Equity:" and "Available:" markers.
        labels = ["Equity: $X | Available: $Y"]
        out = _detect_essential_drift(labels)
        assert len(out) == 1
        marker, _label = out[0]
        # Either marker is acceptable depending on tuple iteration order;
        # the contract is "exactly one entry per drifted label".
        assert marker in ("Equity:", "Available:")

    def test_drift_empty_input_returns_empty(self) -> None:
        assert _detect_essential_drift([]) == []


class TestSummarizeKeptProtections:
    """Issue A Phase 3d — ``_summarize_kept_protections`` walks the
    surviving sections after trim and returns ``(kept_count,
    kept_categories)``. Used by ``_build_trade_prompt``'s priority-
    aware trim block to enrich the ``CLAUDE_PROMPT_TRIMMED`` log line
    so operators can verify essential coverage without reading source.

    Contract reminders:
    - Categories accumulated by FIRST matching marker substring per
      section (mirrors ``_infer_section_priority``'s contract).
    - Index-0 coaching is intentionally NOT counted (no marker,
      forced-essential by classifier).
    - Output is sorted for deterministic log lines.
    """

    def test_empty_sections_zero_count(self) -> None:
        kept, cats = _summarize_kept_protections([])
        assert kept == 0
        assert cats == []

    def test_only_optional_sections_zero_count(self) -> None:
        sections = [
            "\n## SENTIMENT\nFear & Greed = 42",
            "\n## RECENT LESSONS\n- ...",
        ]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 0
        assert cats == []

    def test_single_essential_counted(self) -> None:
        sections = [
            "\n## MARKET DATA\nBTCUSDT $50000",
            "\n## SENTIMENT\nFear & Greed = 42",
        ]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 1
        assert cats == ["## MARKET DATA"]

    def test_multiple_essentials_each_counted(self) -> None:
        """Realistic CALL_A subset — five essentials present, one
        OPTIONAL filler ignored."""
        sections = [
            "\n## MARKET DATA\nBTCUSDT $50000",
            "\n## ACCOUNT\nbalance details",
            "\n## TRADE CANDIDATES\npackage list",
            "\n## MARKET REGIME (CONTEXT)\ntrending",
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n[CRITICAL]",
            "\n## SENTIMENT\noptional content",
        ]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 5
        assert cats == sorted([
            "## MARKET DATA",
            "## ACCOUNT",
            "## TRADE CANDIDATES",
            "## MARKET REGIME (CONTEXT)",
            "## URGENT WATCHDOG ALERTS",
        ])

    def test_bare_line_metadata_counted(self) -> None:
        """The new bare-line markers (Equity / Available / Maximum
        concurrent positions) all show up in kept_categories when the
        respective sections survive."""
        sections = [
            "Equity: $182,520.28",
            "Available: $99,997.51",
            "Maximum concurrent positions: 10",
            "\n## SENTIMENT\nfiller",
        ]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 3
        assert cats == sorted([
            "Equity:",
            "Available:",
            "Maximum concurrent positions",
        ])

    def test_index_zero_coaching_not_counted_when_no_marker(self) -> None:
        """Coaching has no marker and is forced essential by index-0
        in ``_infer_section_priority``. The summary excludes it from
        the category coverage — the contract is "marker-matched
        sections" not "essentials including coaching"."""
        sections = [
            "coaching block content with no header",
            "\n## MARKET DATA\nBTCUSDT $50000",
        ]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 1
        assert cats == ["## MARKET DATA"]

    def test_first_marker_match_only_per_section(self) -> None:
        """A section whose first 200 chars contain multiple essential
        markers counts once and reports the FIRST matching marker —
        keeps output deterministic regardless of marker tuple ordering
        changes."""
        # Crafted: head contains both ``Equity:`` and ``Available:``.
        sections = ["Equity: $X | Available: $Y"]
        kept, cats = _summarize_kept_protections(sections)
        assert kept == 1
        # Either marker is acceptable (depends on tuple order); the
        # contract is exactly one match per section.
        assert len(cats) == 1
        assert cats[0] in ("Equity:", "Available:")

    def test_marker_outside_first_200_chars_not_counted(self) -> None:
        """Mirrors ``_infer_section_priority``'s 200-char window. If a
        marker only appears beyond char 200 it is NOT counted — the
        classifier wouldn't have classified it essential either."""
        # 250 chars of filler then the marker — outside scan window.
        section = "X" * 250 + "\n## MARKET DATA\nbody"
        kept, cats = _summarize_kept_protections([section])
        assert kept == 0
        assert cats == []

    def test_sorted_output_is_deterministic(self) -> None:
        """Categories are returned sorted; same sections in different
        order produce identical output."""
        sections_a = [
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n",
            "\n## ACCOUNT\nx",
            "\n## MARKET DATA\ny",
        ]
        sections_b = [
            "\n## MARKET DATA\ny",
            "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n",
            "\n## ACCOUNT\nx",
        ]
        _, cats_a = _summarize_kept_protections(sections_a)
        _, cats_b = _summarize_kept_protections(sections_b)
        assert cats_a == cats_b
        assert cats_a == sorted(cats_a)
