"""Stage 2 phase 2 — missing services degrade gracefully sub-block by sub-block.

Each rich sub-block runs in its own try/except. When a service is
unavailable (cold start, configuration omission), the formatter must
still emit a coherent block: the missing sub-block is silently
omitted (DEBUG log only) and the rest render.
"""

from types import SimpleNamespace

from src.brain.strategist import ClaudeStrategist
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


def _btc_pkg() -> CoinPackage:
    return CoinPackage(
        symbol="BTCUSDT",
        qualified=True,
        opportunity_score=0.85,
        price_data=PriceDataBlock(current=70000.0),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=85,
            setup_type_confidence=0.78,
            trade_direction="long",
            structural_levels=StructuralLevels(
                suggested_sl=68000.0, suggested_tp=74000.0, rr_ratio=2.0,
            ),
        ),
        strategies=StrategiesBlock(
            fired_count=5, ensemble_consensus="STRONG", total_score=88.0,
        ),
        signals=SignalsBlock(confidence=0.82, direction="long"),
        alt_data=AltDataBlock(
            funding_rate=0.0001, funding_signal="longs_paying",
            oi_change_24h_pct=1.0, fear_greed=40,
        ),
        state_label=StateLabelBlock(primary="MOMENTUM_RUN", confidence=0.7),
    )


def _stub(services: dict) -> ClaudeStrategist:
    s = ClaudeStrategist.__new__(ClaudeStrategist)
    s.services = services
    s.settings = SimpleNamespace(
        brain=SimpleNamespace(surface_briefing_fields=False),
        scanner=SimpleNamespace(
            briefing=SimpleNamespace(prompt_floor_interestingness=0.20),
        ),
    )
    return s


class TestGracefulDegradation:
    def test_no_services_dict_at_all(self) -> None:
        # services empty: header + ensemble + funding lines render;
        # XRAY/signals/regime/scorer sub-blocks omit silently.
        s = _stub({})
        out = s._format_packages_for_prompt_full({"BTCUSDT": _btc_pkg()})
        assert "BTCUSDT" in out
        assert "Strategies: 5 fired" in out
        assert "Funding: 0.0001" in out
        # Sub-blocks dependent on services must NOT raise; their lines
        # are simply absent.
        assert "XRAY: setup=" not in out  # structure_cache missing
        assert "Signal: type=" not in out  # signal_worker missing
        assert "Score: total=" not in out  # scorer_components missing

    def test_only_layer_manager_with_scorer_cache(self) -> None:
        class _LM:
            _scorer_components = {
                "BTCUSDT": {
                    "base": 30.0, "confluence": 20.0, "context": 15.0,
                    "quality": 12.0, "total": 77.0, "grade": "A+",
                    "last_updated": 0.0,
                },
            }

            def get_scorer_components(self, sym):
                return self._scorer_components.get(sym)

        s = _stub({"layer_manager": _LM()})
        out = s._format_packages_for_prompt_full({"BTCUSDT": _btc_pkg()})
        assert "Score: total=77.0 grade=A+" in out
        assert "base=30.0/40" in out
        # Other sub-blocks gracefully omit.
        assert "XRAY: setup=" not in out
