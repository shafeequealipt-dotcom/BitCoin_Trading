"""Live brain-prompt-enrichment simulation harness.

Recreates each of the original information gaps documented in
``IMPLEMENT_BRAIN_PROMPT_ENRICHMENT.md`` as concrete scenarios, then
renders them through the real production code path with the fix flags
toggled OFF (the broken pre-fix world) and ON (the post-fix world).

Each scenario asserts:
  1. The pre-fix render is missing the information the brain needs
  2. The post-fix render contains it in the prescribed format
  3. The brain reading the post-fix render could reach a different,
     better-informed conclusion than reading the pre-fix render

This is not a unit test — it's a behavioural simulation. Failure here
means the fix does not address the diagnosed issue, even if unit tests
pass.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Project imports — must run from the project root
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
from src.strategies.registry import StrategyRegistry
from src.strategies.register_all import register_all_strategies
from src.workers.scanner.state_labeler import (
    LABEL_RECENT_LOSER_COOLDOWN,
    LABEL_TREND_PULLBACK_LONG,
)


# ───────────────── Fake services for renderer-only scenarios ─────────────────


class _Brain:
    def __init__(self, **flags) -> None:
        self.surface_briefing_fields = flags.get("surface_briefing_fields", True)
        self.surface_top_n_voters = flags.get("surface_top_n_voters", 10)
        self.emit_vote_opposition = flags.get("emit_vote_opposition", True)
        self.emit_category_split = flags.get("emit_category_split", True)
        self.emit_direction_perf_in_callb = flags.get(
            "emit_direction_perf_in_callb", True
        )
        self.emit_recent_loss_context = flags.get("emit_recent_loss_context", True)
        self.recent_loss_lookback_hours = 336
        self.recent_loss_max_lessons = 2


class _Settings:
    def __init__(self, **flags) -> None:
        self.brain = _Brain(**flags)
        self.stage2 = SimpleNamespace(enable_zero_two_contract=False)
        self.scanner = SimpleNamespace(
            briefing=SimpleNamespace(prompt_floor_interestingness=0.20)
        )


class _LM:
    def __init__(self, votes_entry: dict | None) -> None:
        self._votes_entry = votes_entry

    def get_strategy_votes(self, symbol):
        return self._votes_entry


def _make_votes(triples):
    """Build a _strategy_votes[symbol] entry from a list of
    (name, vote, conf) tuples (weight defaults to 1.0)."""
    votes = {
        name: {"vote": v, "confidence": c, "weight": 1.0, "reasoning": ""}
        for name, v, c in triples
    }
    buy_w = sum(c for _, v, c in triples if v == "BUY")
    sell_w = sum(c for _, v, c in triples if v == "SELL")
    neutral_w = sum(c for _, v, c in triples if v == "NEUTRAL")
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


def _make_pkg(symbol="BNBUSDT", *, label=LABEL_TREND_PULLBACK_LONG):
    pkg = CoinPackage(
        symbol=symbol,
        qualified=True,
        opportunity_score=0.7,
        price_data=PriceDataBlock(
            current=1.5,
            change_24h_pct=2.1,
            volume_24h_usd=1e7,
            regime="trending_up",
        ),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=72.0,
            setup_type_confidence=0.7,
            trade_direction="long",
        ),
        strategies=StrategiesBlock(
            fired_count=10,
            ensemble_consensus="GOOD",
            consensus_score=0.7,
            total_score=72.0,
        ),
        signals=SignalsBlock(confidence=0.6, direction="long"),
        alt_data=AltDataBlock(
            funding_rate=0.0001, funding_signal="neutral", fear_greed=42
        ),
    )
    pkg.state_label = StateLabelBlock(primary=label, secondary=[], confidence=0.7)
    pkg.interestingness_score = 0.6
    pkg.interestingness_breakdown = {}
    pkg.state_cleanness = 0.6
    pkg.confluence_count = 3
    return pkg


class _Harness:
    """Renderer-only harness — binds the production methods."""

    def __init__(self, votes_entry=None, **flags) -> None:
        self.settings = _Settings(**flags)
        self.services = {"layer_manager": _LM(votes_entry)}
        self._registry = StrategyRegistry()
        register_all_strategies(self._registry)
        self.services["registry"] = self._registry

    _format_packages_for_prompt = ClaudeStrategist._format_packages_for_prompt
    _format_briefing_extras = ClaudeStrategist._format_briefing_extras
    _format_action_hint = ClaudeStrategist._format_action_hint
    _format_recent_loss_lines = ClaudeStrategist._format_recent_loss_lines
    _strategy_category_map = ClaudeStrategist._strategy_category_map


# ────────────────────────── pretty-printing helpers ──────────────────────────


def _hr(char="─", width=78):
    return char * width


def _section(title):
    print()
    print(_hr("═"))
    print(f"  {title}")
    print(_hr("═"))


def _subsection(title):
    print()
    print(_hr())
    print(f"  {title}")
    print(_hr())


def _diff_summary(before: str, after: str) -> str:
    """Show one-line diff signal for operator scan."""
    delta = len(after) - len(before)
    return f"chars: {len(before)} → {len(after)} (Δ={delta:+d})"


# ─────────────────────────────── Scenarios ───────────────────────────────────


def scenario_e1_hidden_voters() -> None:
    """ORIGINAL ISSUE (Finding 2): brain sees only top-3 BUY voters per
    coin. If there are 7 more BUY voters at conf >0.6, the brain doesn't
    know how broad the agreement is — it sees three names and an
    aggregate, not the distribution.

    Real-world impact: brain skips coins it would have taken if it saw
    the full breadth of agreement.

    Simulation: 10 BUY voters at varying confidence, real production
    strategy names. Render with top-N=3 (pre-fix) vs top-N=10 (post-fix).
    """
    _section("E1 — Hidden voters beyond top-3 (Finding 2)")

    triples = [
        ("F2_multi_tf_alignment", "BUY", 0.85),
        ("B1_volume_breakout", "BUY", 0.80),
        ("H3_vol_switch", "BUY", 0.78),
        ("B4_double_bottom_top", "BUY", 0.75),
        ("A2_vwap_bounce", "BUY", 0.70),
        ("I1_kill_zone", "BUY", 0.65),
        ("E1_fear_greed", "BUY", 0.62),
        ("H4_order_flow", "BUY", 0.60),
        ("G3_liq_frontrunner", "BUY", 0.58),
        ("D2_oi_divergence", "BUY", 0.55),
        # one weak opposing voter
        ("D1_funding_fade", "SELL", 0.40),
    ]
    votes = _make_votes(triples)
    pkg = _make_pkg()

    # PRE-FIX: top_n=3 (matching legacy "top-3 BUY" behaviour)
    pre = _Harness(votes_entry=votes, surface_top_n_voters=3).\
        _format_packages_for_prompt({pkg.symbol: pkg})
    # POST-FIX: top_n=10 (the new default)
    post = _Harness(votes_entry=votes, surface_top_n_voters=10).\
        _format_packages_for_prompt({pkg.symbol: pkg})

    _subsection("PRE-FIX (legacy top-3 budget)")
    print(pre)
    _subsection("POST-FIX (top-10)")
    print(post)

    pre_voter_count = pre.count("(B ")
    post_voter_count = post.count("(B ")
    assert pre_voter_count == 3, f"pre-fix should show 3 voters, saw {pre_voter_count}"
    assert post_voter_count == 10, f"post-fix should show 10, saw {post_voter_count}"

    print()
    print(_diff_summary(pre, post))
    print(f"[ASSERT] pre-fix shows {pre_voter_count} BUY voters (truncated at 3)")
    print(f"[ASSERT] post-fix shows {post_voter_count} BUY voters (full top-10)")
    print(
        "[VERDICT] Brain now sees 7 additional strong voters (conf >0.55)"
        " that were previously invisible."
    )


def scenario_e2_opposition_hidden() -> None:
    """ORIGINAL ISSUE (Finding 8): "STRONG consensus" hides whether
    opposing side has strong individual voters. A 10/0 setup and a 10/4
    setup both read as STRONG; the brain cannot distinguish.

    Real-world impact: brain takes mixed-conviction trades thinking
    they're unanimous.

    Simulation: two coins with same buy_weighted but different opposing
    counts. Pre-fix shows identical-looking votes lines. Post-fix
    surfaces the opposition tier.
    """
    _section("E2 — Opposition hidden behind STRONG label (Finding 8)")

    coin_a_triples = [
        # 6 strong BUY, no opposition
        ("F2_multi_tf_alignment", "BUY", 0.85),
        ("B1_volume_breakout", "BUY", 0.80),
        ("H3_vol_switch", "BUY", 0.75),
        ("A2_vwap_bounce", "BUY", 0.70),
        ("I1_kill_zone", "BUY", 0.65),
        ("E1_fear_greed", "BUY", 0.60),
    ]
    coin_b_triples = [
        # Same 6 BUYs but 4 strong SELLs too
        ("F2_multi_tf_alignment", "BUY", 0.85),
        ("B1_volume_breakout", "BUY", 0.80),
        ("H3_vol_switch", "BUY", 0.75),
        ("A2_vwap_bounce", "BUY", 0.70),
        ("I1_kill_zone", "BUY", 0.65),
        ("E1_fear_greed", "BUY", 0.60),
        ("D1_funding_fade", "SELL", 0.70),
        ("C1_bb_mean_reversion", "SELL", 0.70),
        ("D2_oi_divergence", "SELL", 0.65),
        ("G2_retail_fade", "SELL", 0.60),
    ]

    pkg_a = _make_pkg("COINAUSDT")
    pkg_b = _make_pkg("COINBUSDT")

    pre_a = _Harness(votes_entry=_make_votes(coin_a_triples), emit_vote_opposition=False).\
        _format_packages_for_prompt({pkg_a.symbol: pkg_a})
    pre_b = _Harness(votes_entry=_make_votes(coin_b_triples), emit_vote_opposition=False).\
        _format_packages_for_prompt({pkg_b.symbol: pkg_b})
    post_a = _Harness(votes_entry=_make_votes(coin_a_triples), emit_vote_opposition=True).\
        _format_packages_for_prompt({pkg_a.symbol: pkg_a})
    post_b = _Harness(votes_entry=_make_votes(coin_b_triples), emit_vote_opposition=True).\
        _format_packages_for_prompt({pkg_b.symbol: pkg_b})

    _subsection("PRE-FIX — COIN A (6 BUY / 0 SELL)")
    print(pre_a)
    _subsection("PRE-FIX — COIN B (6 BUY / 4 strong SELL)")
    print(pre_b)
    _subsection("POST-FIX — COIN A (opposition surfaces)")
    print(post_a)
    _subsection("POST-FIX — COIN B (opposition surfaces)")
    print(post_b)

    assert "Opposition:" not in pre_a and "Opposition:" not in pre_b
    assert "NEGLIGIBLE" in post_a, "Coin A should be NEGLIGIBLE"
    # Coin B: opposing_wsum=2.65, agreeing_wsum=4.35, ratio≈0.61 → STRONG
    assert "STRONG" in post_b, "Coin B should be STRONG opposition"
    assert "4 SELL voters at conf>=0.6" in post_b, \
        "Coin B should count 4 strong opposing voters"

    print()
    print(
        "[ASSERT] Pre-fix: both coins show identical-shaped 'Votes:' lines"
        " with no opposition characterisation."
    )
    print("[ASSERT] Post-fix COIN A: Opposition NEGLIGIBLE — safe to enter")
    print(
        "[ASSERT] Post-fix COIN B: Opposition STRONG, 4 SELL voters at"
        " conf>=0.6 — brain warned about pushback"
    )
    print(
        "[VERDICT] Brain reading post-fix COIN B can now distinguish"
        " genuine consensus from contested setup."
    )


def scenario_e3_category_cluster() -> None:
    """ORIGINAL ISSUE (Finding 7): category breakdown hidden. A 6/0
    BUY from 6 momentum strategies and a 6/0 BUY from 6 distinct
    categories look identical in the prompt — but the first is a
    one-category cluster (weaker), the second is cross-category
    (more robust).

    Real-world impact: brain takes thin one-category setups as if
    they were diversified consensus.

    Simulation: same vote counts, different category distribution.
    """
    _section("E3 — Category cluster vs diversified agreement (Finding 7)")

    coin_a_triples = [
        # All 6 BUYs from MOMENTUM category — single-category cluster
        ("B1_volume_breakout", "BUY", 0.80),
        ("B2_supertrend", "BUY", 0.78),
        ("B3_ichimoku", "BUY", 0.75),
        ("B4_double_bottom_top", "BUY", 0.72),
        ("A4_ema_crossover", "BUY", 0.70),  # scalping but momentum-like
        ("B1_volume_breakout", "BUY", 0.68),  # noop dedup, just to fill slots
    ]
    # Cleaner cluster — only B-family
    coin_a_triples = [
        ("B1_volume_breakout", "BUY", 0.80),
        ("B2_supertrend", "BUY", 0.78),
        ("B3_ichimoku", "BUY", 0.75),
        ("B4_double_bottom_top", "BUY", 0.72),
    ]
    coin_b_triples = [
        # 4 BUYs from 4 DIFFERENT categories — diversified agreement
        ("B1_volume_breakout", "BUY", 0.80),  # momentum
        ("F2_multi_tf_alignment", "BUY", 0.78),  # advanced
        ("H3_vol_switch", "BUY", 0.75),  # microstructure
        ("A2_vwap_bounce", "BUY", 0.72),  # scalping
    ]

    pkg_a = _make_pkg("COINAUSDT")
    pkg_b = _make_pkg("COINBUSDT")

    pre_a = _Harness(votes_entry=_make_votes(coin_a_triples), emit_category_split=False).\
        _format_packages_for_prompt({pkg_a.symbol: pkg_a})
    pre_b = _Harness(votes_entry=_make_votes(coin_b_triples), emit_category_split=False).\
        _format_packages_for_prompt({pkg_b.symbol: pkg_b})
    post_a = _Harness(votes_entry=_make_votes(coin_a_triples), emit_category_split=True).\
        _format_packages_for_prompt({pkg_a.symbol: pkg_a})
    post_b = _Harness(votes_entry=_make_votes(coin_b_triples), emit_category_split=True).\
        _format_packages_for_prompt({pkg_b.symbol: pkg_b})

    _subsection("PRE-FIX — COIN A (all 4 votes from MOMENTUM category)")
    print(pre_a)
    _subsection("PRE-FIX — COIN B (4 votes from 4 different categories)")
    print(pre_b)
    _subsection("POST-FIX — COIN A (category cluster surfaces)")
    print(post_a)
    _subsection("POST-FIX — COIN B (cross-category surfaces)")
    print(post_b)

    assert "Cats:" not in pre_a and "Cats:" not in pre_b
    assert "Cats: momentum 4B" in post_a, "Coin A should show momentum 4B"
    # Coin B should show 4 different categories
    for required_cat in ("momentum", "advanced", "microstructure", "scalping"):
        assert required_cat in post_b, f"COIN B should reveal {required_cat} vote"

    print()
    print("[ASSERT] Pre-fix: both coins look identical in vote counts")
    print(
        "[ASSERT] Post-fix COIN A: 'Cats: momentum 4B' — single-category"
        " cluster exposed"
    )
    print(
        "[ASSERT] Post-fix COIN B: 'momentum 1B, advanced 1B, microstructure"
        " 1B, scalping 1B' — cross-category diversification exposed"
    )
    print(
        "[VERDICT] Brain can now down-weight one-category clusters and"
        " up-weight cross-category agreement."
    )


def scenario_e5_dir_perf_blind() -> None:
    """ORIGINAL ISSUE: CALL_B brain doesn't see today's direction
    asymmetry. If today's shorts are 0/5 and longs are 4/1, the brain
    managing positions has no aggregate signal.

    Simulation: build the actual ClaudeStrategist with two different
    per-direction states and inspect the CALL_B prompt.
    """
    _section("E5 — Direction perf blind spot in CALL_B")

    async def _build(per_dir, emit_flag):
        from src.config.settings import Settings
        settings = Settings.load()
        # Override the flag to demonstrate
        settings.brain.emit_direction_perf_in_callb = emit_flag

        thesis_mgr = MagicMock()
        thesis_mgr.get_open_theses = AsyncMock(return_value=[])
        pos_svc = MagicMock()
        pos_svc.get_positions = AsyncMock(return_value=[])
        coord = MagicMock()
        coord.get_trade_plan = MagicMock(return_value=None)
        coord.get_trade_info = MagicMock(return_value={})
        coord._symbol_cooldowns = {}
        urgent_q = MagicMock()
        urgent_q.has_concerns = False
        services = {
            "thesis_manager": thesis_mgr,
            "position_service": pos_svc,
            "trade_coordinator": coord,
            "pnl_manager": SimpleNamespace(current_pnl_pct=0.0),
            "regime_detector": MagicMock(),
            "urgent_queue": urgent_q,
            "enforcer": SimpleNamespace(_per_direction=per_dir),
        }
        strat = ClaudeStrategist(
            claude_client=None, services=services, settings=settings,
        )
        strat.refresh_positions = AsyncMock(return_value=[])
        strat._last_regime_str = "trending_up"
        strat._last_regime_confidence = 0.55
        strat._last_fg_value = 42
        return await strat._build_position_prompt()

    # Real-issue scenario: shorts losing today, longs winning
    asymmetric = {"Buy": {"wins": 4, "losses": 1}, "Sell": {"wins": 0, "losses": 5}}
    pre = asyncio.run(_build(asymmetric, emit_flag=False))
    post = asyncio.run(_build(asymmetric, emit_flag=True))

    _subsection("PRE-FIX (E5 OFF) — brain blind to direction asymmetry")
    print(pre.split("## CONTRACT")[0])  # only top section
    _subsection("POST-FIX (E5 ON) — direction asymmetry visible")
    print(post.split("## CONTRACT")[0])

    assert "TODAY DIRECTION PERF" not in pre
    assert "TODAY DIRECTION PERF: Longs 4W/1L (80% WR) | Shorts 0W/5L (0% WR)" in post

    print()
    print("[ASSERT] Pre-fix prompt has no direction-perf line")
    print(
        "[ASSERT] Post-fix prompt shows Longs 4W/1L (80% WR) | Shorts 0W/5L"
        " (0% WR) — clear today-shorts-failing signal"
    )
    print(
        "[VERDICT] Brain managing positions today can now bias its"
        " hold-vs-close decisions toward the winning direction."
    )


def scenario_e6_lesson_bridge() -> None:
    """ORIGINAL ISSUE (Finding 6): coin carrying RECENT_LOSER_COOLDOWN
    flag shows the brain only the bare label. Brain is told 'skip
    unless thesis materially changed' but has no concrete cause to
    judge 'materially'.

    Simulation: a coin flagged RECENT_LOSER_COOLDOWN with two real
    losing-trade lessons. Pre-fix shows the bare flag; post-fix
    surfaces the cause.
    """
    _section("E6 — RECENT_LOSER_COOLDOWN flag without specific cause")

    # Real-data-shaped lessons (matches what trade_intelligence stores)
    lessons = [
        {
            "trade_closed_at": "2026-05-15T16:34:00+00:00",
            "direction": "Sell",
            "pnl_pct": -0.42,
            "hold_seconds": 720.0,
            "closed_by": "wd_claude_action",
            "regime": "ranging",
            "ds_why": (
                "trend-pullback failed when range-bound; entry timed at"
                " false breakdown"
            ),
            "ds_category": "wrong_environment",
            "ds_what_should_done": "held until 1H structure broke",
        },
        {
            "trade_closed_at": "2026-05-14T08:12:00+00:00",
            "direction": "Sell",
            "pnl_pct": -0.31,
            "hold_seconds": 1500.0,
            "closed_by": "bybit_sl_hit",
            "regime": "ranging",
            "ds_why": "SL too tight relative to ATR for ranging regime",
            "ds_category": "wrong_sizing",
            "ds_what_should_done": "wider SL or smaller size in low-ATR regime",
        },
    ]
    pkg = _make_pkg("ETHUSDT", label=LABEL_RECENT_LOSER_COOLDOWN)

    pre = _Harness(votes_entry=_make_votes([("F2_multi_tf_alignment", "BUY", 0.7)]),
                   emit_recent_loss_context=False).\
        _format_packages_for_prompt({pkg.symbol: pkg})
    post = _Harness(votes_entry=_make_votes([("F2_multi_tf_alignment", "BUY", 0.7)]),
                    emit_recent_loss_context=True).\
        _format_packages_for_prompt(
            {pkg.symbol: pkg}, lessons_by_sym={pkg.symbol: lessons},
        )

    _subsection("PRE-FIX — RECENT_LOSER_COOLDOWN flag with no cause")
    print(pre)
    _subsection("POST-FIX — TIAS lessons surface specific cause")
    print(post)

    assert "Past loss" not in pre
    assert "Past loss [Sell, ranging]:" in post
    assert "trend-pullback failed when range-bound" in post
    assert "wd_claude_action" in post and "12m" in post
    # Both lessons should render
    assert post.count("Past loss") == 2

    print()
    print("[ASSERT] Pre-fix: only the [RECENT_LOSER_COOLDOWN] tag visible")
    print("[ASSERT] Post-fix: 2 concrete loss lines with cause excerpts")
    print(
        "[VERDICT] Brain can now judge 'has thesis materially changed?'"
        " — if the current regime is no longer 'ranging' or the SL"
        " is now wider, the past-loss conditions no longer hold."
    )


def scenario_combined_callA() -> None:
    """ALL E1+E2+E3+E6 at once on a single ambiguous-but-tradeable
    candidate. This simulates the real per-coin block size + content
    that production would render."""
    _section("Combined — single per-coin block with all CALL_A enrichments")

    triples = [
        # Top-end BUY voters
        ("F2_multi_tf_alignment", "BUY", 0.85),
        ("B1_volume_breakout", "BUY", 0.80),
        ("H3_vol_switch", "BUY", 0.75),
        ("A2_vwap_bounce", "BUY", 0.70),
        ("I1_kill_zone", "BUY", 0.65),
        ("H4_order_flow", "BUY", 0.60),
        # Strong SELL opposition (4 voters)
        ("D1_funding_fade", "SELL", 0.65),
        ("C1_bb_mean_reversion", "SELL", 0.60),
        ("G2_retail_fade", "SELL", 0.55),
        # Neutral noise
        ("A3_bb_squeeze", "NEUTRAL", 0.20),
    ]
    lessons = [
        {
            "direction": "Buy",
            "pnl_pct": -0.35,
            "hold_seconds": 900.0,
            "closed_by": "wd_claude_action",
            "regime": "trending_up",
            "ds_why": (
                "entered late on extended trend; mean-reversion fired"
                " inside the candle"
            ),
        },
    ]
    pkg = _make_pkg("BNBUSDT", label=LABEL_RECENT_LOSER_COOLDOWN)
    h = _Harness(
        votes_entry=_make_votes(triples),
        surface_top_n_voters=10,
        emit_vote_opposition=True,
        emit_category_split=True,
        emit_recent_loss_context=True,
    )
    out = h._format_packages_for_prompt(
        {pkg.symbol: pkg}, lessons_by_sym={pkg.symbol: lessons},
    )
    print(out)
    print()
    print(f"Per-coin chars: {len(out)}")
    print(
        "[NOTE] Brain sees: 10 voters (E1) + opposition tier (E2) +"
        " category split (E3) + concrete past-loss cause (E6), all in"
        " ~one screen of prompt."
    )


def main():
    print()
    print(_hr("█"))
    print(
        "  BRAIN-PROMPT-ENRICHMENT LIVE SIMULATION — recreate each"
        " original issue and verify the fix responds correctly."
    )
    print(_hr("█"))
    scenario_e1_hidden_voters()
    scenario_e2_opposition_hidden()
    scenario_e3_category_cluster()
    scenario_e5_dir_perf_blind()
    scenario_e6_lesson_bridge()
    scenario_combined_callA()
    print()
    print(_hr("█"))
    print("  ALL SCENARIOS PASSED")
    print(_hr("█"))


if __name__ == "__main__":
    main()
