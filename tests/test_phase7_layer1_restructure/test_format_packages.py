"""_format_packages_for_prompt — Layer 1 restructure Phase 7."""

from src.brain.strategist import ClaudeStrategist
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)


def _stub_strategist() -> ClaudeStrategist:
    return ClaudeStrategist.__new__(ClaudeStrategist)


def _btc_pkg(qualified: bool = True, score: float = 0.85) -> CoinPackage:
    return CoinPackage(
        symbol="BTCUSDT",
        qualified=qualified,
        opportunity_score=score,
        qualification_reasons=["xray_setup=bullish_fvg_ob", "consensus=STRONG"],
        price_data=PriceDataBlock(current=70000.0, change_24h_pct=2.5, regime="trending_up"),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=85.0,
            setup_type_confidence=0.78,
            structural_levels=StructuralLevels(
                current_price=70000.0, suggested_sl=68000.0,
                suggested_tp=74000.0, rr_ratio=2.0,
            ),
        ),
        strategies=StrategiesBlock(
            fired_count=5, ensemble_consensus="STRONG",
            consensus_score=0.92, total_score=88.0,
        ),
        signals=SignalsBlock(confidence=0.82, direction="long"),
        alt_data=AltDataBlock(funding_rate=0.0001, funding_signal="longs_paying"),
    )


class TestFormat:
    def test_empty_packages(self) -> None:
        s = _stub_strategist()
        assert s._format_packages_for_prompt({}) == ""

    def test_single_package(self) -> None:
        s = _stub_strategist()
        out = s._format_packages_for_prompt({"BTCUSDT": _btc_pkg()})
        assert "BTCUSDT" in out
        assert "TRADE CANDIDATES" in out
        assert "bullish_fvg_ob" in out
        assert "STRONG" in out
        assert "Suggested SL/TP" in out

    def test_sorted_by_score_desc(self) -> None:
        s = _stub_strategist()
        pkg_low = CoinPackage(symbol="LOW", qualified=True, opportunity_score=0.10)
        pkg_high = CoinPackage(symbol="HIGH", qualified=True, opportunity_score=0.95)
        out = s._format_packages_for_prompt({"LOW": pkg_low, "HIGH": pkg_high})
        # HIGH must appear before LOW.
        assert out.index("HIGH") < out.index("LOW")

    def test_force_included_marker(self) -> None:
        """An open-position-forced package (qualified=False but
        open_position is not None) is rendered with the management
        marker so Claude treats it as 'manage existing' rather than
        'new entry'.

        Q3b (2026-04-29) — the marker text changed from
        ``(force-included)`` to ``(open-position, manage)`` because
        the previous label conflated open-position force-includes
        with BTC/ETH ref-pair force-includes (now removed). The new
        label is precise: only open-position packages get it.
        """
        s = _stub_strategist()
        pkg = _btc_pkg(qualified=False)  # forced because of open position
        pkg.open_position = {"side": "long", "entry_price": 70000.0}
        out = s._format_packages_for_prompt({"BTCUSDT": pkg})
        assert "open-position" in out, (
            f"Q3b: expected 'open-position' marker. Output:\n{out}"
        )
        assert "OPEN POSITION" in out

    def test_renders_funding_signal(self) -> None:
        s = _stub_strategist()
        out = s._format_packages_for_prompt({"BTCUSDT": _btc_pkg()})
        assert "longs_paying" in out
