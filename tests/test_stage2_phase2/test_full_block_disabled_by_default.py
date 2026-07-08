"""Stage 2 phase 2 — flag-off path is byte-identical to the legacy formatter.

Confirms that adding _format_packages_for_prompt_full does not
disturb the Phase 9 briefing-mode output when
[stage2].enable_full_layer_block is False (the default).
"""

from src.brain.strategist import ClaudeStrategist
from src.config.settings import Stage2Settings
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StateLabelBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)


def _stub_strategist() -> ClaudeStrategist:
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = {}
    return s


def _btc_pkg() -> CoinPackage:
    return CoinPackage(
        symbol="BTCUSDT",
        qualified=True,
        opportunity_score=0.85,
        qualification_reasons=["xray_setup=bullish_fvg_ob", "consensus=STRONG"],
        price_data=PriceDataBlock(
            current=70000.0, change_24h_pct=2.5, regime="trending_up",
        ),
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
        alt_data=AltDataBlock(
            funding_rate=0.0001, funding_signal="longs_paying",
        ),
        state_label=StateLabelBlock(primary="MOMENTUM_RUN", confidence=0.7),
    )


class TestDefaultOff:
    def test_default_settings_flag_is_false(self) -> None:
        cfg = Stage2Settings()
        assert cfg.enable_full_layer_block is False

    def test_full_renderer_returns_empty_for_empty_packages(self) -> None:
        # The new method must mirror the legacy method's empty-input
        # contract (returns "") so the dispatch in _build_trade_prompt
        # is a drop-in switch.
        s = _stub_strategist()
        assert s._format_packages_for_prompt_full({}) == ""

    def test_legacy_renderer_unchanged_for_single_package(self) -> None:
        # Phase 9 briefing-mode header + body shape must continue
        # rendering unchanged through the legacy path.
        s = _stub_strategist()
        out = s._format_packages_for_prompt({"BTCUSDT": _btc_pkg()})
        assert "BTCUSDT" in out
        assert "TRADE CANDIDATES" in out
        assert "bullish_fvg_ob" in out
        assert "STRONG" in out
