"""Brain-prompt-enrichment Phase 3.1 — Top-N mixed-direction voters per coin.

The legacy Phase-6 briefing renderer emitted two sub-blocks per candidate
("Top BUY" + "Top SELL", each truncated to 3 voters by confidence*weight).
The enrichment fix replaces those with one combined "Top-N" line ranked
across all directions, gated by ``[brain].surface_top_n_voters`` (default
10). These tests pin the new format and the backward-compatibility
contract.
"""

from src.brain.strategist import ClaudeStrategist
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    XrayBlock,
)
from src.workers.scanner.state_labeler import LABEL_TREND_PULLBACK_LONG


class _FakeBrainSettings:
    def __init__(
        self,
        *,
        surface: bool,
        top_n: int,
        emit_opp: bool = True,
        emit_cats: bool = True,
    ) -> None:
        self.surface_briefing_fields = surface
        self.surface_top_n_voters = top_n
        self.emit_vote_opposition = emit_opp
        self.emit_category_split = emit_cats
        # Candidate-Block Data Integrity Fix — Issue 1b (2026-06-09): mirror the
        # production default so the votes-line label and two-sided-poll rendering
        # reflect the live code path.
        self.emit_direction_disagreement_notes = True


class _FakeSettings:
    def __init__(
        self,
        *,
        surface: bool,
        top_n: int,
        emit_opp: bool = True,
        emit_cats: bool = True,
    ) -> None:
        self.brain = _FakeBrainSettings(
            surface=surface,
            top_n=top_n,
            emit_opp=emit_opp,
            emit_cats=emit_cats,
        )


class _FakeStrategy:
    """Mimics ``BaseStrategy`` for the registry-shape lookup —
    ``_strategy_category_map`` reads ``.name`` and ``.category``."""

    def __init__(self, name: str, category: str) -> None:
        self.name = name
        self.category = category


class _FakeRegistry:
    """Mimics ``StrategyRegistry`` — exposes the public ``get_all``
    accessor used by ``_strategy_category_map``."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._all = [
            _FakeStrategy(name=name, category=cat)
            for name, cat in mapping.items()
        ]

    def get_all(self) -> list:
        return list(self._all)


class _FakeLayerManager:
    """Mimics ``LayerManager.get_strategy_votes`` return shape."""

    def __init__(self, votes_entry: dict | None) -> None:
        self._votes_entry = votes_entry

    def get_strategy_votes(self, symbol: str) -> dict | None:
        return self._votes_entry


class _FakeStrategist:
    """Minimal harness — same pattern as test_phase6_1d_briefing.

    Binds the production ``_format_briefing_extras`` method so the
    rendered output reflects the live code, not a copy.
    """

    def __init__(
        self,
        *,
        surface: bool = True,
        top_n: int = 10,
        emit_opp: bool = True,
        emit_cats: bool = True,
        votes_entry: dict | None = None,
        category_map: dict[str, str] | None = None,
    ) -> None:
        self.settings = _FakeSettings(
            surface=surface,
            top_n=top_n,
            emit_opp=emit_opp,
            emit_cats=emit_cats,
        )
        services = {"layer_manager": _FakeLayerManager(votes_entry)}
        if category_map is not None:
            services["registry"] = _FakeRegistry(category_map)
        self.services = services

    _format_briefing_extras = ClaudeStrategist._format_briefing_extras
    _format_action_hint = ClaudeStrategist._format_action_hint
    _format_packages_for_prompt = ClaudeStrategist._format_packages_for_prompt
    _strategy_category_map = ClaudeStrategist._strategy_category_map


def _make_pkg(symbol: str = "BTCUSDT") -> CoinPackage:
    """Minimal CoinPackage with briefing fields set (so flag-on path
    reaches the votes block render — the action_hint and label fields
    are unused by the votes block but ``_format_packages_for_prompt``
    expects them when surface_briefing_fields is True)."""
    pkg = CoinPackage(
        symbol=symbol,
        qualified=True,
        opportunity_score=0.7,
        price_data=PriceDataBlock(
            current=1.0,
            change_24h_pct=2.5,
            volume_24h_usd=1_000_000.0,
            regime="trending_up",
        ),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=72.0,
            setup_type_confidence=0.7,
            trade_direction="long",
        ),
        strategies=StrategiesBlock(
            fired_count=12,
            ensemble_consensus="GOOD",
            consensus_score=0.75,
            total_score=72.0,
        ),
        signals=SignalsBlock(confidence=0.65, direction="long"),
        alt_data=AltDataBlock(
            funding_rate=-0.0018,
            funding_signal="shorts_paying",
            fear_greed=18,
        ),
    )
    pkg.state_label = StateLabelBlock(
        primary=LABEL_TREND_PULLBACK_LONG,
        secondary=[],
        confidence=0.7,
    )
    pkg.interestingness_score = 0.62
    pkg.interestingness_breakdown = {"cleanness": 0.13}
    pkg.state_cleanness = 0.65
    pkg.confluence_count = 4
    return pkg


def _votes(entries: list[tuple[str, str, float, float]]) -> dict:
    """Build a ``_strategy_votes[symbol]`` entry from
    ``(name, vote, confidence, weight)`` tuples. Mirrors the shape
    returned by ``EnsembleResult.vote_distribution_dict`` and
    ``LayerManager.get_strategy_votes``.
    """
    votes = {
        name: {
            "vote": v,
            "confidence": c,
            "weight": w,
            "reasoning": "",
        }
        for (name, v, c, w) in entries
    }
    buy_w = sum(c * w for _n, v, c, w in entries if v == "BUY")
    sell_w = sum(c * w for _n, v, c, w in entries if v == "SELL")
    neutral_w = sum(c * w for _n, v, c, w in entries if v == "NEUTRAL")
    return {
        "votes": votes,
        "buy_weighted": buy_w,
        "sell_weighted": sell_w,
        "neutral_weighted": neutral_w,
        "consensus": "GOOD",
        "consensus_direction": "BUY" if buy_w > sell_w else "SELL",
        "size_multiplier": 1.0,
        "last_updated": 0.0,
    }


def test_top_n_renders_single_mixed_line() -> None:
    """The new Top-N line replaces the legacy two-line BUY/SELL block."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True,
        top_n=10,
        votes_entry=_votes(
            [
                ("F2_multi_tf", "BUY", 0.85, 1.00),
                ("B1_vol_brk", "BUY", 0.70, 1.00),
                ("D1_fund", "SELL", 0.45, 1.00),
                ("C1_bb_mean", "SELL", 0.40, 1.00),
                ("A2_vwap", "BUY", 0.60, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    # New format present.
    assert "Top-5:" in out  # 5 voters, all > 0 conf*weight
    assert "F2_multi_tf(B 0.85)" in out
    assert "D1_fund(S 0.45)" in out
    # Legacy lines must NOT appear.
    assert "Top BUY:" not in out
    assert "Top SELL:" not in out


def test_top_n_orders_by_conf_times_weight() -> None:
    """Voters ranked by confidence × weight, descending, mixed
    direction. Equal-side ranks defer to that product (no side-priority
    bias)."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True,
        top_n=3,
        votes_entry=_votes(
            [
                # F2 has highest conf × weight.
                ("F2_multi_tf", "BUY", 0.85, 1.00),
                # D1 second.
                ("D1_fund", "SELL", 0.80, 1.00),
                # B1 third.
                ("B1_vol_brk", "BUY", 0.70, 1.00),
                # Below cutoff.
                ("C1_bb", "SELL", 0.10, 1.00),
                ("E1_fg", "BUY", 0.05, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Top-3:" in out
    # Order is by conf * weight desc — F2 → D1 → B1.
    top_line = next(
        line for line in out.splitlines() if line.strip().startswith("Top-")
    )
    assert top_line.index("F2_multi_tf") < top_line.index("D1_fund")
    assert top_line.index("D1_fund") < top_line.index("B1_vol_brk")
    # Below-cutoff voters NOT in the line.
    assert "C1_bb" not in top_line
    assert "E1_fg" not in top_line


def test_top_n_zero_disables_line_without_breaking_votes_summary() -> None:
    """N=0 suppresses the Top-N line but keeps the aggregate Votes
    line — operator can drop the per-coin voter list without losing
    the buy_weighted / sell_weighted summary."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True,
        top_n=0,
        votes_entry=_votes(
            [
                ("F2_multi_tf", "BUY", 0.85, 1.00),
                ("D1_fund", "SELL", 0.45, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    # Aggregate stays. Issue 1b (2026-06-09): the one-sided tally is now
    # labeled "Votes (confirmed-direction tally)" so it is not mistaken for the
    # full contest.
    assert "Votes (confirmed-direction tally): BUY=" in out
    # Detail line gone.
    assert "Top-" not in out


def test_top_n_filters_zero_conf_weight_voters() -> None:
    """A NEUTRAL with conf=0.0 or weight=0.0 contributes no signal —
    it must not crowd the Top-N slot."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True,
        top_n=5,
        votes_entry=_votes(
            [
                ("F2_multi_tf", "BUY", 0.85, 1.00),
                ("K1_claude", "NEUTRAL", 0.0, 1.0),  # excluded.
                ("K3_ensemble", "NEUTRAL", 0.0, 1.0),  # excluded.
                ("D1_fund", "SELL", 0.45, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    top_line = next(
        line for line in out.splitlines() if line.strip().startswith("Top-")
    )
    assert "F2_multi_tf" in top_line
    assert "D1_fund" in top_line
    assert "K1_claude" not in top_line
    assert "K3_ensemble" not in top_line
    # Only 2 informative voters exist, so the line caps at Top-2.
    assert "Top-2:" in top_line


def test_top_n_renders_neutral_voters_when_informative() -> None:
    """A NEUTRAL with non-zero conf × weight (e.g. K2 pattern memory
    when wired) is a real signal — it should appear in the Top-N when
    it ranks high enough."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True,
        top_n=3,
        votes_entry=_votes(
            [
                ("F2_multi_tf", "BUY", 0.85, 1.00),
                ("K2_pattern", "NEUTRAL", 0.50, 1.00),  # informative.
                ("D1_fund", "SELL", 0.30, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "K2_pattern(N 0.50)" in out


def test_flag_off_omits_votes_block_entirely() -> None:
    """When surface_briefing_fields=False, the votes block must not
    render at all — this guards the operator-flip rollback path.
    """
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=False,
        top_n=10,
        votes_entry=_votes([("F2_multi_tf", "BUY", 0.85, 1.00)]),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Votes:" not in out
    assert "Top-" not in out


# ─── Brain-prompt-enrichment Phase 3.2 — Opposition tier ───


def _opp_line(out: str) -> str:
    """Pull the single Opposition line from a rendered candidate block."""
    for line in out.splitlines():
        if "Opposition:" in line:
            return line.strip()
    return ""


def test_opposition_negligible_when_one_side_dominates() -> None:
    """BUY heavily dominant, no opposing weight → NEGLIGIBLE tier with
    zero strong opposing voters."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("F2", "BUY", 0.85, 1.00),
                ("B1", "BUY", 0.70, 1.00),
                ("A2", "BUY", 0.60, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _opp_line(out)
    assert "NEGLIGIBLE" in line
    assert "0 SELL voters at conf>=0.6" in line
    assert "agree_wsum=2.15" in line
    assert "opp_wsum=0.00" in line


def test_opposition_strong_when_ratio_above_half() -> None:
    """opp_wsum / agree_wsum > 0.5 → STRONG tier."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("F2", "BUY", 0.85, 1.00),
                ("B1", "BUY", 0.80, 1.00),
                # Two strong SELL voters → ratio = 1.50/1.65 ~= 0.91 → STRONG.
                ("D1", "SELL", 0.80, 1.00),
                ("C1", "SELL", 0.70, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _opp_line(out)
    assert "STRONG" in line
    # Both opposing voters are at conf >= 0.6 → strong_opp_count = 2.
    assert "2 SELL voters at conf>=0.6" in line


def test_opposition_moderate_when_ratio_in_range() -> None:
    """opp_wsum / agree_wsum in [0.20, 0.50) → MODERATE."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("F2", "BUY", 0.85, 1.00),
                ("B1", "BUY", 0.80, 1.00),
                ("A2", "BUY", 0.70, 1.00),
                ("E1", "BUY", 0.60, 1.00),
                ("H3", "BUY", 0.60, 1.00),
                # SELL side aggregate 1.20 vs BUY's 3.55 → ratio 0.34 → MODERATE.
                ("D1", "SELL", 0.70, 1.00),
                ("C1", "SELL", 0.50, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _opp_line(out)
    assert "MODERATE" in line
    # Only one of the two opposing voters is at conf >= 0.6.
    assert "1 SELL voters at conf>=0.6" in line


def test_opposition_flips_direction_when_sell_dominates() -> None:
    """When SELL is the agreeing side, opposition tier reports BUY
    voters."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("D1", "SELL", 0.85, 1.00),
                ("C1", "SELL", 0.80, 1.00),
                ("D2", "SELL", 0.70, 1.00),
                ("F2", "BUY", 0.20, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _opp_line(out)
    # ratio = 0.20 / 2.35 = 0.085 → WEAK.
    assert "WEAK" in line
    assert "0 BUY voters at conf>=0.6" in line


def test_opposition_omitted_when_no_voters_fired() -> None:
    """Both weighted sums zero → no line (would be meaningless)."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes([("K1", "NEUTRAL", 0.0, 1.0)]),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Opposition:" not in out


def test_opposition_flag_off_suppresses_line() -> None:
    """emit_vote_opposition=False drops the line without affecting
    the Top-N renderer."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        emit_opp=False,
        votes_entry=_votes(
            [
                ("F2", "BUY", 0.85, 1.00),
                ("D1", "SELL", 0.45, 1.00),
            ]
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Opposition:" not in out
    # Top-N still renders.
    assert "Top-2:" in out


# ─── Brain-prompt-enrichment Phase 3.3 — Category split ───


_CAT_MAP_EXAMPLE = {
    "F2_multi_tf_alignment": "advanced",
    "B1_volume_breakout": "momentum",
    "B4_double_bottom_top": "momentum",
    "A2_vwap_bounce": "scalping",
    "A3_bb_squeeze_scalp": "scalping",
    "D1_funding_fade": "funding_arb",
    "C1_bb_mean_reversion": "mean_reversion",
    "K2_pattern_memory": "ai_enhanced",
    "H3_vol_switch": "microstructure",
    "I1_kill_zone": "time_based",
}


def _cat_line(out: str) -> str:
    for line in out.splitlines():
        if "Cats:" in line:
            return line.strip()
    return ""


def test_category_split_groups_buy_only_by_category() -> None:
    """When all firing voters agree on BUY, the Cats line lists each
    category with its buy count and no sell-count remainder."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("F2_multi_tf_alignment", "BUY", 0.85, 1.00),
                ("B1_volume_breakout", "BUY", 0.70, 1.00),
                ("B4_double_bottom_top", "BUY", 0.65, 1.00),
                ("A2_vwap_bounce", "BUY", 0.60, 1.00),
            ]
        ),
        category_map=_CAT_MAP_EXAMPLE,
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _cat_line(out)
    # Total directional count desc: momentum 2, then scalping 1, advanced 1.
    # Tied counts break alphabetically.
    assert "momentum 2B" in line
    assert "advanced 1B" in line
    assert "scalping 1B" in line
    # NEUTRAL categories absent (no K2 in this set, but rule sanity check).
    assert "ai_enhanced" not in line


def test_category_split_renders_both_sides_when_mixed() -> None:
    """When the same category casts BUY and SELL, format is ``NB+MS``."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("B1_volume_breakout", "BUY", 0.70, 1.00),
                ("B4_double_bottom_top", "SELL", 0.60, 1.00),
                ("F2_multi_tf_alignment", "BUY", 0.85, 1.00),
                ("D1_funding_fade", "SELL", 0.55, 1.00),
            ]
        ),
        category_map=_CAT_MAP_EXAMPLE,
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _cat_line(out)
    assert "momentum 1B+1S" in line
    assert "advanced 1B" in line
    assert "funding_arb 1S" in line


def test_category_split_excludes_neutral_votes() -> None:
    """NEUTRAL votes carry no directional signal — their categories
    must not appear in the Cats line."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("F2_multi_tf_alignment", "BUY", 0.85, 1.00),
                # K2 fires NEUTRAL with non-zero conf — counts for the
                # Top-N line but NOT for the category split.
                ("K2_pattern_memory", "NEUTRAL", 0.50, 1.00),
            ]
        ),
        category_map=_CAT_MAP_EXAMPLE,
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    line = _cat_line(out)
    assert "advanced 1B" in line
    # ai_enhanced category must NOT appear despite K2 having non-zero
    # conf — the rule is "directional vote only".
    assert "ai_enhanced" not in line


def test_category_split_omitted_when_only_neutral_votes() -> None:
    """No directional votes → no line (would be empty)."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [
                ("K2_pattern_memory", "NEUTRAL", 0.50, 1.00),
            ]
        ),
        category_map=_CAT_MAP_EXAMPLE,
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Cats:" not in out


def test_category_split_omitted_without_registry() -> None:
    """Without a registry service, the helper returns an empty map
    and the Cats line is suppressed — graceful degradation rather
    than a crash."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        votes_entry=_votes(
            [("F2_multi_tf_alignment", "BUY", 0.85, 1.00)]
        ),
        # No category_map → no registry service installed.
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Cats:" not in out
    # Top-N still renders (independent of registry).
    assert "Top-1:" in out


def test_category_split_flag_off_suppresses_line() -> None:
    """emit_category_split=False suppresses the line independently
    of the other two enrichments."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        emit_cats=False,
        votes_entry=_votes(
            [("F2_multi_tf_alignment", "BUY", 0.85, 1.00)]
        ),
        category_map=_CAT_MAP_EXAMPLE,
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Cats:" not in out
    # Top-N + Opposition still render.
    assert "Top-1:" in out
    assert "Opposition:" in out


def test_strategy_category_map_caches_per_instance() -> None:
    """Map is built once and cached — repeated calls reuse the result
    (no per-cycle registry iteration)."""
    s = _FakeStrategist(category_map=_CAT_MAP_EXAMPLE)
    m1 = s._strategy_category_map()
    m2 = s._strategy_category_map()
    assert m1 is m2  # same object reference → cached.
    assert m1["F2_multi_tf_alignment"] == "advanced"
    assert m1["K2_pattern_memory"] == "ai_enhanced"
    # All 10 entries present.
    assert len(m1) == 10


# --- Candidate-Block Data Integrity Fix — Issue 1b (2026-06-09) -------------
# Votes-line / two-sided-poll consistency. The one-sided "Votes" line is
# relabeled the confirmed-direction tally, and the two-sided poll renders
# whenever two-sided polling ran (not only when the opposing weight is
# non-zero) so the BSB asymmetry — poll line vanished while a one-sided tally
# read as clean conviction — is removed.

def _two_sided_votes(entries, opposing_weighted):
    ve = _votes(entries)
    ve["two_sided"] = True
    ve["opposing_weighted"] = opposing_weighted
    return ve


def test_issue1b_votes_line_labeled_confirmed_tally() -> None:
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True, top_n=10,
        votes_entry=_two_sided_votes(
            [("F2_multi_tf", "BUY", 0.85, 1.0), ("A4_ema", "BUY", 0.65, 1.0)],
            opposing_weighted=0.0,
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Votes (confirmed-direction tally): BUY=" in out


def test_issue1b_two_sided_poll_renders_when_opposing_zero() -> None:
    """BSB case: two-sided polling ran but the opposing side scored 0. The
    poll line must still render (no silent omission) and state plainly that the
    opposite side was polled and nobody backed it."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True, top_n=10,
        votes_entry=_two_sided_votes(
            [("F2_multi_tf", "BUY", 0.85, 1.0), ("A4_ema", "BUY", 0.65, 1.0)],
            opposing_weighted=0.0,
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Two-sided poll: BUY=" in out
    assert "the opposite side was polled and no" in out


def test_issue1b_two_sided_poll_shows_latent_opposition() -> None:
    """SKR/KAT case: a genuine latent opposite-side strength still renders with
    the original 'asked the OTHER side' wording."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True, top_n=10,
        votes_entry=_two_sided_votes(
            [("F2_multi_tf", "BUY", 0.85, 1.0)], opposing_weighted=0.99,
        ),
    )
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Two-sided poll: BUY=" in out
    assert "asked the OTHER" in out


def test_issue1b_flag_off_restores_prior_rendering() -> None:
    """Rollback: with the flag off the label reverts to plain 'Votes:' and a
    zero-opposition two-sided poll is omitted (pre-fix behavior)."""
    pkg = _make_pkg()
    s = _FakeStrategist(
        surface=True, top_n=10,
        votes_entry=_two_sided_votes(
            [("F2_multi_tf", "BUY", 0.85, 1.0)], opposing_weighted=0.0,
        ),
    )
    s.settings.brain.emit_direction_disagreement_notes = False
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Votes: BUY=" in out
    assert "Votes (confirmed-direction tally)" not in out
    assert "Two-sided poll" not in out
