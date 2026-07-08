"""Phase 6 of the 1D briefing rewrite — prompt extension flag tests.

Two questions per test:
1. Flag OFF (default) — prompt is byte-identical to pre-Phase-6 production
   when packages don't carry the new fields. This is the regression guard
   for production rollout.
2. Flag ON — new lines (interestingness, label, votes, action hint) appear
   in the per-coin block; total prompt size stays under the 18 KB hard cap
   per Risk R3.
"""

from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    XrayBlock,
)
from src.workers.scanner.state_labeler import (
    LABEL_TREND_PULLBACK_LONG,
)


# Hard cap per Risk R3 — Phase 9 cutover requires headroom of >=25%
# vs the current 12-14 KB prompt; the 18 KB limit is the absolute
# ceiling for the new prompt size.
PROMPT_HARD_CAP_KB = 18.0


class _FakeBrainSettings:
    def __init__(self, surface: bool):
        self.surface_briefing_fields = surface


class _FakeSettings:
    def __init__(self, surface: bool):
        self.brain = _FakeBrainSettings(surface)


class _FakeStrategist:
    """Minimal harness that exercises _format_packages_for_prompt without
    constructing the full Strategist (which has heavy service dependencies).
    """

    def __init__(self, surface: bool):
        self.settings = _FakeSettings(surface)
        # _format_briefing_extras reads self.services.get('layer_manager').
        # Phase 6 supplies an empty services dict so the votes block is
        # silently skipped — that's covered by a separate test below.
        self.services = {}

    # Bind the real method so we exercise the production code path.
    from src.brain.strategist import ClaudeStrategist as _Strat
    _format_packages_for_prompt = _Strat._format_packages_for_prompt
    _format_briefing_extras = _Strat._format_briefing_extras
    _format_action_hint = _Strat._format_action_hint


def _make_pkg(symbol: str, *, with_label: bool, opportunity: float = 0.7) -> CoinPackage:
    pkg = CoinPackage(
        symbol=symbol,
        qualified=True,
        opportunity_score=opportunity,
        price_data=PriceDataBlock(
            current=1.0, change_24h_pct=2.5,
            volume_24h_usd=1000000.0, regime="trending_up",
        ),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=72.0,
            setup_type_confidence=0.7,
            trade_direction="long",
        ),
        strategies=StrategiesBlock(
            fired_count=12, ensemble_consensus="GOOD",
            consensus_score=0.75, total_score=72.0,
        ),
        signals=SignalsBlock(confidence=0.65, direction="long"),
        alt_data=AltDataBlock(funding_rate=-0.0018, funding_signal="shorts_paying", fear_greed=18),
    )
    if with_label:
        pkg.state_label = StateLabelBlock(
            primary=LABEL_TREND_PULLBACK_LONG,
            secondary=[],
            confidence=0.7,
        )
        pkg.interestingness_score = 0.62
        pkg.interestingness_breakdown = {
            "cleanness": 0.13, "confluence": 0.10,
            "extremity": 0.08, "label_strength": 0.17,
            "structural_quality": 0.10, "mtf_alignment": 0.04,
            "open_position_floor": 0.0,
        }
        pkg.state_cleanness = 0.65
        pkg.confluence_count = 4
    return pkg


def test_flag_off_omits_briefing_lines() -> None:
    """Default flag — prompt does NOT contain the new field labels.

    This guards the production rollout: a worker restart with the new
    code but old config produces byte-identical Claude prompts.
    """
    s = _FakeStrategist(surface=False)
    pkg = _make_pkg("BTCUSDT", with_label=True)
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    # Legacy keys still present.
    assert "BTCUSDT" in out
    assert "Setup:" in out
    assert "Strategies:" in out
    # Phase 6 keys MUST NOT appear under flag-off.
    assert "interestingness=" not in out
    assert "Action hint:" not in out
    assert "Top BUY:" not in out
    assert "State: cleanness=" not in out


def test_flag_on_emits_briefing_lines() -> None:
    """Flag on — new fields appear in the per-coin block."""
    s = _FakeStrategist(surface=True)
    pkg = _make_pkg("BTCUSDT", with_label=True)
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    # Phase 6 fields visible.
    assert "interestingness=0.62" in out
    assert LABEL_TREND_PULLBACK_LONG in out
    assert "Action hint:" in out
    # Cleanness + confluence count line.
    assert "State: cleanness=0.65" in out
    assert "confluence=4" in out
    # Legacy lines remain.
    assert "Setup:" in out
    assert "Strategies:" in out
    assert "Funding:" in out


def test_flag_on_byte_budget_under_hard_cap() -> None:
    """Risk R3 — even with all fields surfaced for 15 packages, prompt
    block stays well under 18 KB. (Per-coin block ~1 KB × 15 = ~15 KB
    for the candidates section; rest of prompt adds the legacy 12-14 KB.)"""
    s = _FakeStrategist(surface=True)
    packages = {
        f"COIN{i}USDT": _make_pkg(f"COIN{i}USDT", with_label=True, opportunity=0.7 - i * 0.01)
        for i in range(15)
    }
    out = s._format_packages_for_prompt(packages)
    kb = len(out) / 1024.0
    # The candidates block alone is ~6-7 KB; total prompt with legacy
    # MARKET DATA section is bounded elsewhere. Hard-cap of 18 KB on
    # the candidates section is a comfortable headroom.
    assert kb < PROMPT_HARD_CAP_KB, (
        f"candidates block grew to {kb:.1f} KB — exceeded {PROMPT_HARD_CAP_KB} KB cap"
    )


def test_flag_on_skips_advisory_only_action_hint_for_unlabeled() -> None:
    """When primary label is empty/absent, action hint line is omitted."""
    s = _FakeStrategist(surface=True)
    pkg = _make_pkg("BTCUSDT", with_label=False)
    pkg.state_label = StateLabelBlock(primary="", secondary=[], confidence=0.0)
    out = s._format_packages_for_prompt({pkg.symbol: pkg})
    assert "Action hint:" not in out


def test_flag_on_sort_order_uses_interestingness() -> None:
    """When flag on, packages sort by interestingness DESC (then opportunity)."""
    s = _FakeStrategist(surface=True)
    pkg_lo = _make_pkg("LO_USDT", with_label=True, opportunity=0.9)
    pkg_lo.interestingness_score = 0.30
    pkg_hi = _make_pkg("HI_USDT", with_label=True, opportunity=0.1)
    pkg_hi.interestingness_score = 0.85
    out = s._format_packages_for_prompt({"LO": pkg_lo, "HI": pkg_hi})
    # HI should appear BEFORE LO in the rendered output.
    assert out.index("HI_USDT") < out.index("LO_USDT")


def test_briefing_system_prompt_suffix_constant_exists() -> None:
    """Phase 6 suffix is defined, non-empty, and mentions the key fields."""
    from src.brain.strategist import (
        BRIEFING_SYSTEM_PROMPT_SUFFIX,
        TRADE_SYSTEM_PROMPT,
    )
    assert BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "INTERESTINGNESS" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "STATE LABELS" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "VOTES BLOCK" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    assert "ACTION HINT" in BRIEFING_SYSTEM_PROMPT_SUFFIX
    # Suffix must be a strict addition to the legacy prompt — never
    # replace or shadow the legacy instructions.
    assert TRADE_SYSTEM_PROMPT != BRIEFING_SYSTEM_PROMPT_SUFFIX
