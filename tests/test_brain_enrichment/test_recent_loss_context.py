"""Brain-prompt-enrichment Phase 3.5 — recent-loss TIAS context bridge.

These tests pin three contracts:

  1. ``recent_losses_for_setup`` returns the expected row shape and
     respects the safety-default sentinels (hours<=0, limit<=0, empty
     symbol/side).
  2. ``ClaudeStrategist._format_recent_loss_lines`` produces the
     "Past loss [...]: ..." format and truncates ``ds_why`` so the
     line stays within the per-coin char budget.
  3. The CALL_A package formatter consumes the precomputed
     ``lessons_by_sym`` dict and injects the lines right under the
     header for candidates flagged RECENT_LOSER_COOLDOWN.

CALL_B is intentionally OUT OF SCOPE (the Post-Execution Closure Fix
on 2026-05-05 removed TIAS lessons from CALL_B for closed-loop
reasons; re-adding would require operator approval).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from src.core.trade_recorder import recent_losses_for_setup
from src.workers.scanner.state_labeler import LABEL_RECENT_LOSER_COOLDOWN

# ─── recent_losses_for_setup contract ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_losses_returns_empty_on_safety_sentinels() -> None:
    """hours<=0, limit<=0, empty symbol or side → empty list without
    a DB hit (the caller is unsafe; the helper protects the cycle)."""
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(side_effect=AssertionError("db hit!"))
    assert (
        await recent_losses_for_setup(
            fake_db, symbol="BTC", side="Buy", hours=0,
        )
        == []
    )
    assert (
        await recent_losses_for_setup(
            fake_db, symbol="BTC", side="Buy", limit=0,
        )
        == []
    )
    assert (
        await recent_losses_for_setup(
            fake_db, symbol="", side="Buy",
        )
        == []
    )
    assert (
        await recent_losses_for_setup(
            fake_db, symbol="BTC", side="",
        )
        == []
    )


@pytest.mark.asyncio
async def test_recent_losses_query_includes_regime_filter_when_supplied() -> None:
    """When ``regime`` is provided the SQL includes the regime predicate;
    when None the helper falls back to the symbol+side-only query."""
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    await recent_losses_for_setup(
        fake_db,
        symbol="BTCUSDT",
        side="Sell",
        regime="trending_down",
        hours=72,
        limit=3,
    )
    sql, params = fake_db.fetch_all.call_args[0]
    assert "AND regime = ?" in sql
    assert params == ("BTCUSDT", "Sell", "trending_down", "-72 hours", 3)

    fake_db.fetch_all.reset_mock()
    await recent_losses_for_setup(
        fake_db, symbol="BTCUSDT", side="Sell", regime=None, hours=72, limit=3,
    )
    sql, params = fake_db.fetch_all.call_args[0]
    assert "AND regime = ?" not in sql
    assert params == ("BTCUSDT", "Sell", "-72 hours", 3)


@pytest.mark.asyncio
async def test_recent_losses_swallows_db_errors() -> None:
    """A query exception returns an empty list — the prompt build
    must never crash on a recorder hiccup."""
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(side_effect=RuntimeError("boom"))
    result = await recent_losses_for_setup(
        fake_db, symbol="BTC", side="Buy",
    )
    assert result == []


# ─── _format_recent_loss_lines renderer ───────────────────────────────────────


def _strat() -> ClaudeStrategist:
    """Bare ClaudeStrategist instance — used only for its bound
    renderer methods."""
    return ClaudeStrategist.__new__(ClaudeStrategist)


def _lesson(
    *,
    direction: str = "Sell",
    pnl_pct: float = -0.4,
    closed_by: str = "wd_claude_action",
    hold_seconds: float = 720.0,
    regime: str = "trending_down",
    ds_why: str = "trend-pullback failed when range-bound",
) -> dict:
    return {
        "direction": direction,
        "pnl_pct": pnl_pct,
        "closed_by": closed_by,
        "hold_seconds": hold_seconds,
        "regime": regime,
        "ds_why": ds_why,
        "trade_closed_at": "2026-05-15T16:34:00+00:00",
        "ds_category": "wrong_direction",
        "ds_what_should_done": "held until 1H structure broke",
    }


def test_lesson_line_format_includes_dir_regime_pnl_closereason_cause() -> None:
    """Default case: line includes all the brain-facing fields and
    stays within the char budget."""
    out = _strat()._format_recent_loss_lines([_lesson()])
    assert len(out) == 1
    line = out[0]
    # Issue 2.7 (2026-06-07): the lesson line is now a prominent, actionable
    # CAUTION line (was the terse "Past loss [...]").
    assert "CAUTION recent loss [Sell, trending_down]" in line
    assert "do NOT repeat" in line          # actionable instruction present
    assert "-0.40%" in line
    assert "wd_claude_action" in line
    assert "12m" in line  # 720 seconds → 12 minutes
    assert "Cause: trend-pullback failed when range-bound" in line
    # Per-coin char budget for the (now more prominent/actionable) lesson line.
    assert len(line) <= 270


def test_lesson_line_truncates_long_why_to_keep_budget() -> None:
    """Long ``ds_why`` strings are still truncated with an ellipsis so the
    line is bounded regardless of analyst verbosity. The F22 fix
    (2026-06-04) raised the cause budget from the old hard 57-char cut to
    ``brain.tias_cause_max_chars`` (default 120) so the failure pattern is no
    longer dropped mid-sentence; the bounded total stays well under 200."""
    long_why = "a" * 400
    out = _strat()._format_recent_loss_lines([_lesson(ds_why=long_why)])
    line = out[0]
    assert "..." in line
    assert "Cause:" in line
    # Bounded: the cause is capped at the default 120 chars + the fixed prefix
    # (Issue 2.7 raised the prefix with the prominent/actionable CAUTION text).
    assert len(line) <= 270
    # And it keeps materially MORE context than the old 57-char cut.
    assert line.count("a") >= 100


def test_lesson_lines_render_empty_list_for_empty_input() -> None:
    """No lessons → no lines — caller can extend unconditionally."""
    assert _strat()._format_recent_loss_lines(None) == []
    assert _strat()._format_recent_loss_lines([]) == []


def test_lesson_lines_skip_individual_malformed_rows() -> None:
    """A single bad row does not crash the renderer — other rows
    still emit. Each row's format error is logged at DEBUG."""

    class _BadRow:
        def get(self, *_a, **_kw):
            raise RuntimeError("simulated row corruption")

    out = _strat()._format_recent_loss_lines([_BadRow(), _lesson()])
    assert len(out) == 1
    assert "CAUTION recent loss" in out[0]


# ─── CALL_A package formatter injection ───────────────────────────────────────


class _FakeBrainSettings:
    surface_briefing_fields = True
    surface_top_n_voters = 0  # keep the Top-N renderer quiet for this test.
    emit_vote_opposition = False
    emit_category_split = False
    emit_recent_loss_context = True
    recent_loss_lookback_hours = 336
    recent_loss_max_lessons = 2


class _FakeSettings:
    brain = _FakeBrainSettings()
    scanner = SimpleNamespace(briefing=SimpleNamespace(prompt_floor_interestingness=0.20))


class _PackageHarness:
    """Same construction pattern as test_phase6_1d_briefing — binds
    the production formatter so the rendered output reflects live
    code, not a copy."""

    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.services = {}

    _format_packages_for_prompt = ClaudeStrategist._format_packages_for_prompt
    _format_briefing_extras = ClaudeStrategist._format_briefing_extras
    _format_action_hint = ClaudeStrategist._format_action_hint
    _format_recent_loss_lines = ClaudeStrategist._format_recent_loss_lines
    _strategy_category_map = ClaudeStrategist._strategy_category_map


def _flagged_pkg(symbol: str = "BNBUSDT") -> CoinPackage:
    pkg = CoinPackage(
        symbol=symbol,
        qualified=True,
        opportunity_score=0.7,
        price_data=PriceDataBlock(
            current=1.0, change_24h_pct=2.0,
            volume_24h_usd=1_000_000.0, regime="trending_down",
        ),
        xray=XrayBlock(
            setup_type="bearish_structural_break",
            setup_score=72.0,
            setup_type_confidence=0.7,
            trade_direction="short",
        ),
        strategies=StrategiesBlock(
            fired_count=10, ensemble_consensus="GOOD",
            consensus_score=0.7, total_score=70.0,
        ),
        signals=SignalsBlock(confidence=0.6, direction="short"),
        alt_data=AltDataBlock(
            funding_rate=0.0001, funding_signal="neutral", fear_greed=35,
        ),
    )
    pkg.state_label = StateLabelBlock(
        primary=LABEL_RECENT_LOSER_COOLDOWN,
        secondary=[],
        confidence=0.7,
    )
    pkg.interestingness_score = 0.55
    pkg.interestingness_breakdown = {}
    pkg.state_cleanness = 0.5
    pkg.confluence_count = 3
    return pkg


def test_formatter_injects_lesson_line_under_header_for_flagged_coin() -> None:
    """When ``lessons_by_sym`` carries an entry for a candidate, the
    lesson line appears between the header (``### SYM ...``) and the
    next sub-block (``Setup:``)."""
    h = _PackageHarness()
    pkg = _flagged_pkg("BNBUSDT")
    lessons = {"BNBUSDT": [_lesson()]}
    out = h._format_packages_for_prompt({pkg.symbol: pkg}, lessons_by_sym=lessons)
    header_idx = out.index("### BNBUSDT")
    # ``out`` is the rendered prompt STRING; str.index gives the character
    # offset of each section so the ordering assertion below still holds.
    lesson_idx = out.index("CAUTION recent loss [Sell, trending_down]")
    setup_idx = out.index("Setup:")
    assert header_idx < lesson_idx < setup_idx


def test_formatter_omits_lesson_line_when_no_dict_provided() -> None:
    """Default call signature (no ``lessons_by_sym``) keeps the
    legacy byte-shape — no Past-loss line."""
    h = _PackageHarness()
    pkg = _flagged_pkg("BNBUSDT")
    out = h._format_packages_for_prompt({pkg.symbol: pkg})
    assert "CAUTION recent loss" not in out


def test_formatter_omits_lesson_line_when_symbol_not_in_dict() -> None:
    """Lessons dict present but no entry for this coin → no line."""
    h = _PackageHarness()
    pkg = _flagged_pkg("BNBUSDT")
    out = h._format_packages_for_prompt(
        {pkg.symbol: pkg}, lessons_by_sym={"OTHERUSDT": [_lesson()]},
    )
    assert "CAUTION recent loss" not in out
